from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Account, LedgerEntry, Payment
from app.domains.keys.dice import resolve_inter_spbvi_key
from app.domains.keys.dife import resolve_key
from app.domains.keys.persistence import DiceKey
from app.domains.payments.mol import MolSettlementError, settle_with_mol


class AccountNotFoundError(Exception):
    pass


class DestinationKeyNotFoundError(Exception):
    pass


class InsufficientFundsError(Exception):
    pass


class IdempotencyConflictError(Exception):
    pass


class SameSpbviPaymentError(Exception):
    pass


class AmountLimitExceededError(Exception):
    def __init__(self, limit_uvb: int) -> None:
        super().__init__(f"El monto supera el limite de {limit_uvb} UVB por operacion.")
        self.limit_uvb = limit_uvb


def check_amount_limit(amount_cents: int) -> None:
    """Regla Bre-B: ninguna operación supera PAYMENT_LIMIT_UVB (en centavos, sin floats)."""
    settings = get_settings()
    if amount_cents > settings.payment_limit_uvb * settings.uvb_value_cents:
        raise AmountLimitExceededError(settings.payment_limit_uvb)


def _same_request(
    payment: Payment,
    *,
    source_account_id: str,
    destination_key_type: str,
    destination_key_value: str,
    amount_cents: int,
    payment_type: str,
) -> bool:
    return (
        payment.source_account_id == source_account_id
        and payment.destination_key_type == destination_key_type
        and payment.destination_key_value == destination_key_value
        and payment.amount_cents == amount_cents
        and payment.payment_type == payment_type
    )


def _existing_payment(
    db: Session,
    *,
    operation_id: str,
    source_account_id: str,
    destination_key_type: str,
    destination_key_value: str,
    amount_cents: int,
    payment_type: str,
) -> Payment | None:
    payment = db.scalar(
        select(Payment).where(Payment.operation_id == operation_id)
    )
    if payment is not None and not _same_request(
        payment,
        source_account_id=source_account_id,
        destination_key_type=destination_key_type,
        destination_key_value=destination_key_value,
        amount_cents=amount_cents,
        payment_type=payment_type,
    ):
        raise IdempotencyConflictError
    return payment


def _is_operation_id_conflict(error: IntegrityError) -> bool:
    constraint_name = getattr(
        getattr(error.orig, "diag", None),
        "constraint_name",
        None,
    )
    sqlite_unique_violation = (
        "UNIQUE constraint failed: payments.operation_id" in str(error.orig)
    )
    return (
        constraint_name == "uq_payment_operation_id"
        or sqlite_unique_violation
    )


def create_payment(
    db: Session,
    dife_db: Session,
    *,
    operation_id: str,
    source_account_id: str,
    destination_key_type: str,
    destination_key_value: str,
    amount_cents: int,
) -> tuple[Payment, bool]:
    check_amount_limit(amount_cents)
    operation_id = operation_id.strip()
    source_account_id = source_account_id.strip()
    destination_key_type = destination_key_type.strip().lower()
    destination_key_value = destination_key_value.strip()

    try:
        existing = _existing_payment(
            db,
            operation_id=operation_id,
            source_account_id=source_account_id,
            destination_key_type=destination_key_type,
            destination_key_value=destination_key_value,
            amount_cents=amount_cents,
            payment_type="intra_spbvi",
        )
        if existing is not None:
            return existing, False

        source = db.get(Account, source_account_id)
        if source is None:
            raise AccountNotFoundError

        key = resolve_key(
            dife_db,
            spbvi_id=source.spbvi_id,
            key_type=destination_key_type,
            key_value=destination_key_value,
        )
        if key is None:
            raise DestinationKeyNotFoundError

        recipient = db.get(Account, key.deposit_product_id)
        if recipient is None or recipient.spbvi_id != source.spbvi_id:
            raise DestinationKeyNotFoundError

        payment = Payment(
            operation_id=operation_id,
            source_account_id=source.id,
            destination_account_id=recipient.id,
            destination_key_type=destination_key_type,
            destination_key_value=destination_key_value,
            amount_cents=amount_cents,
            payment_type="intra_spbvi",
            status="completed",
        )
        db.add(payment)
        try:
            db.flush()
        except IntegrityError as error:
            db.rollback()
            if not _is_operation_id_conflict(error):
                raise
            existing = _existing_payment(
                db,
                operation_id=operation_id,
                source_account_id=source_account_id,
                destination_key_type=destination_key_type,
                destination_key_value=destination_key_value,
                amount_cents=amount_cents,
                payment_type="intra_spbvi",
            )
            if existing is None:
                raise
            return existing, False

        debit = db.execute(
            update(Account)
            .where(
                Account.id == source.id,
                Account.balance_cents >= amount_cents,
            )
            .values(balance_cents=Account.balance_cents - amount_cents)
        )
        if debit.rowcount != 1:
            raise InsufficientFundsError

        db.add(
            LedgerEntry(
                payment_id=payment.id,
                account_id=source.id,
                amount_cents=-amount_cents,
                entry_type="debit",
            )
        )
        db.flush()

        credit = db.execute(
            update(Account)
            .where(Account.id == recipient.id)
            .values(balance_cents=Account.balance_cents + amount_cents)
        )
        if credit.rowcount != 1:
            raise DestinationKeyNotFoundError

        db.add(
            LedgerEntry(
                payment_id=payment.id,
                account_id=recipient.id,
                amount_cents=amount_cents,
                entry_type="credit",
            )
        )
        db.commit()
        db.refresh(payment)
        return payment, True
    except Exception:
        db.rollback()
        raise


def create_inter_spbvi_payment(
    db: Session,
    dice_db: Session,
    *,
    operation_id: str,
    source_account_id: str,
    destination_key_type: str,
    destination_key_value: str,
    amount_cents: int,
) -> tuple[Payment, bool]:
    check_amount_limit(amount_cents)
    operation_id = operation_id.strip()
    source_account_id = source_account_id.strip()
    destination_key_type = destination_key_type.strip().lower()
    destination_key_value = destination_key_value.strip()

    try:
        existing = _existing_payment(
            db,
            operation_id=operation_id,
            source_account_id=source_account_id,
            destination_key_type=destination_key_type,
            destination_key_value=destination_key_value,
            amount_cents=amount_cents,
            payment_type="inter_spbvi",
        )
        if existing is not None:
            return existing, False

        source = db.get(Account, source_account_id)
        if source is None:
            raise AccountNotFoundError

        dice_key: DiceKey | None = resolve_inter_spbvi_key(
            dice_db,
            key_type=destination_key_type,
            key_value=destination_key_value,
        )
        if dice_key is None:
            raise DestinationKeyNotFoundError
        if dice_key.spbvi_id == source.spbvi_id:
            raise SameSpbviPaymentError

        destination = db.get(Account, dice_key.deposit_product_id)
        if destination is None or destination.spbvi_id != dice_key.spbvi_id:
            raise DestinationKeyNotFoundError

        payment = Payment(
            operation_id=operation_id,
            source_account_id=source.id,
            destination_account_id=destination.id,
            destination_key_type=destination_key_type,
            destination_key_value=destination_key_value,
            amount_cents=amount_cents,
            payment_type="inter_spbvi",
            status="pending",
        )
        db.add(payment)
        try:
            db.flush()
        except IntegrityError as error:
            db.rollback()
            if not _is_operation_id_conflict(error):
                raise
            existing = _existing_payment(
                db,
                operation_id=operation_id,
                source_account_id=source_account_id,
                destination_key_type=destination_key_type,
                destination_key_value=destination_key_value,
                amount_cents=amount_cents,
                payment_type="inter_spbvi",
            )
            if existing is None:
                raise
            return existing, False

        try:
            settle_with_mol(
                db,
                payment=payment,
                source=source,
                destination=destination,
            )
        except RuntimeError as error:
            raise MolSettlementError(
                "El MOL simulado no pudo completar la liquidacion."
            ) from error
        db.commit()
        db.refresh(payment)
        return payment, True
    except Exception:
        db.rollback()
        raise
