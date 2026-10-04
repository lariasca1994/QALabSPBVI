"""Operaciones posteriores al pago: devoluciones (pacs.004) y cancelaciones (camt.056 → camt.029).

El MOL simulado liquida de forma síncrona, así que todo pago registrado ya está liquidado.
Por eso una cancelación no anula el pago: abre una investigación que el SPBVI receptor
acepta (y entonces se devuelve el saldo pendiente con motivo FOCR) o rechaza con un motivo.
La devolución mueve el dinero de la cuenta receptora a la de origen en una sola transacción
local: o se registran ambos asientos o ninguno, y los asientos de cada devolución suman cero.
"""

from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Account, CancellationRequest, LedgerEntry, Payment, PaymentReturn
from app.domains.iso20022.lifecycle import (
    CANCELLATION_REASONS,
    CANCELLATION_REJECTION_REASONS,
    RETURN_REASONS,
)


class PaymentNotFoundError(Exception):
    pass


class InvestigationNotFoundError(Exception):
    pass


class LifecycleValidationError(ValueError):
    pass


class LifecycleConflictError(Exception):
    pass


class ReturnInsufficientFundsError(Exception):
    pass


def _payment(db: Session, operation_id: str) -> Payment:
    payment = db.scalar(select(Payment).where(Payment.operation_id == operation_id.strip()))
    if payment is None:
        raise PaymentNotFoundError
    return payment


def returned_cents(db: Session, payment_id: int) -> int:
    return int(
        db.scalar(
            select(func.coalesce(func.sum(PaymentReturn.amount_cents), 0)).where(
                PaymentReturn.payment_id == payment_id
            )
        )
    )


def _apply_return(
    db: Session,
    *,
    payment: Payment,
    return_id: str,
    amount_cents: int,
    reason_code: str,
    cancellation_id: str | None = None,
) -> PaymentReturn:
    """Debita al receptor y acredita al ordenante sin hacer commit."""
    item = PaymentReturn(
        return_id=return_id,
        payment_id=payment.id,
        amount_cents=amount_cents,
        reason_code=reason_code,
        cancellation_id=cancellation_id,
    )
    db.add(item)
    db.flush()
    debit = db.execute(
        update(Account)
        .where(
            Account.id == payment.destination_account_id,
            Account.balance_cents >= amount_cents,
        )
        .values(balance_cents=Account.balance_cents - amount_cents)
    )
    if debit.rowcount != 1:
        raise ReturnInsufficientFundsError
    db.add(
        LedgerEntry(
            payment_id=payment.id,
            account_id=payment.destination_account_id,
            amount_cents=-amount_cents,
            entry_type="return_debit",
        )
    )
    db.execute(
        update(Account)
        .where(Account.id == payment.source_account_id)
        .values(balance_cents=Account.balance_cents + amount_cents)
    )
    db.add(
        LedgerEntry(
            payment_id=payment.id,
            account_id=payment.source_account_id,
            amount_cents=amount_cents,
            entry_type="return_credit",
        )
    )
    return item


def create_return(
    db: Session,
    *,
    operation_id: str,
    return_id: str,
    amount_cents: int | None,
    reason_code: str,
) -> tuple[PaymentReturn, Payment, bool]:
    """Devuelve total (sin monto) o parcialmente un pago. Reenviar el mismo return_id no duplica."""
    return_id = return_id.strip()
    reason_code = reason_code.strip().upper()
    if reason_code not in RETURN_REASONS:
        raise LifecycleValidationError(
            f"Motivo de devolución inválido. Usa uno de: {', '.join(sorted(RETURN_REASONS))}."
        )
    try:
        payment = _payment(db, operation_id)
        existing = db.scalar(select(PaymentReturn).where(PaymentReturn.return_id == return_id))
        if existing is not None:
            if existing.payment_id != payment.id or existing.reason_code != reason_code or (
                amount_cents is not None and existing.amount_cents != amount_cents
            ):
                raise LifecycleConflictError("El identificador de devolución ya existe con otros datos.")
            return existing, payment, False
        if payment.status != "completed":
            raise LifecycleConflictError("Solo se devuelven pagos liquidados.")
        available = payment.amount_cents - returned_cents(db, payment.id)
        if available <= 0:
            raise LifecycleConflictError("El pago ya fue devuelto por completo.")
        amount = available if amount_cents is None else amount_cents
        if amount > available:
            raise LifecycleValidationError(
                f"La devolución supera el saldo devolvible del pago ({available} centavos)."
            )
        item = _apply_return(db, payment=payment, return_id=return_id, amount_cents=amount, reason_code=reason_code)
        db.commit()
        db.refresh(item)
        return item, payment, True
    except IntegrityError as error:
        db.rollback()
        raise LifecycleConflictError("El identificador de devolución ya existe.") from error
    except Exception:
        db.rollback()
        raise


def create_cancellation_request(
    db: Session,
    *,
    operation_id: str,
    cancellation_id: str,
    reason_code: str,
) -> tuple[CancellationRequest, Payment, bool]:
    cancellation_id = cancellation_id.strip()
    reason_code = reason_code.strip().upper()
    if reason_code not in CANCELLATION_REASONS:
        raise LifecycleValidationError(
            f"Motivo de cancelación inválido. Usa uno de: {', '.join(sorted(CANCELLATION_REASONS))}."
        )
    try:
        payment = _payment(db, operation_id)
        existing = db.scalar(
            select(CancellationRequest).where(CancellationRequest.cancellation_id == cancellation_id)
        )
        if existing is not None:
            if existing.payment_id != payment.id or existing.reason_code != reason_code:
                raise LifecycleConflictError("El identificador de cancelación ya existe con otros datos.")
            return existing, payment, False
        if returned_cents(db, payment.id) >= payment.amount_cents:
            raise LifecycleConflictError("El pago ya fue devuelto por completo; no hay nada que cancelar.")
        open_request = db.scalar(
            select(CancellationRequest).where(
                CancellationRequest.payment_id == payment.id,
                CancellationRequest.status == "pending",
            )
        )
        if open_request is not None:
            raise LifecycleConflictError(
                f"El pago ya tiene una investigación pendiente: {open_request.cancellation_id}."
            )
        item = CancellationRequest(
            cancellation_id=cancellation_id,
            payment_id=payment.id,
            reason_code=reason_code,
            status="pending",
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        return item, payment, True
    except IntegrityError as error:
        db.rollback()
        raise LifecycleConflictError("El identificador de cancelación ya existe.") from error
    except Exception:
        db.rollback()
        raise


def get_investigation(db: Session, cancellation_id: str) -> tuple[CancellationRequest, Payment]:
    item = db.scalar(
        select(CancellationRequest).where(CancellationRequest.cancellation_id == cancellation_id.strip())
    )
    if item is None:
        raise InvestigationNotFoundError
    payment = db.get(Payment, item.payment_id)
    if payment is None:
        raise InvestigationNotFoundError
    return item, payment


def resolve_investigation(
    db: Session,
    *,
    cancellation_id: str,
    accepted: bool,
    rejection_reason: str | None,
) -> tuple[CancellationRequest, Payment, PaymentReturn | None, bool]:
    """El SPBVI receptor acepta (devuelve lo pendiente con FOCR) o rechaza con motivo."""
    if accepted and rejection_reason is not None:
        raise LifecycleValidationError("Una resolución aceptada no lleva motivo de rechazo.")
    if not accepted:
        if rejection_reason is None:
            raise LifecycleValidationError("El rechazo de la cancelación exige un motivo.")
        rejection_reason = rejection_reason.strip().upper()
        if rejection_reason not in CANCELLATION_REJECTION_REASONS:
            raise LifecycleValidationError(
                "Motivo de rechazo inválido. Usa uno de: "
                f"{', '.join(sorted(CANCELLATION_REJECTION_REASONS))}."
            )
    try:
        item, payment = get_investigation(db, cancellation_id)
        target = "accepted" if accepted else "rejected"
        if item.status != "pending":
            if item.status == target and item.rejection_reason == rejection_reason:
                existing_return = (
                    db.scalar(select(PaymentReturn).where(PaymentReturn.return_id == item.return_id))
                    if item.return_id
                    else None
                )
                return item, payment, existing_return, False
            raise LifecycleConflictError("La investigación ya fue resuelta con otra decisión.")
        payment_return = None
        if accepted:
            available = payment.amount_cents - returned_cents(db, payment.id)
            if available <= 0:
                raise LifecycleConflictError(
                    "El pago ya fue devuelto por completo; rechaza la cancelación con ARDT."
                )
            payment_return = _apply_return(
                db,
                payment=payment,
                return_id=f"ret-{item.cancellation_id}",
                amount_cents=available,
                reason_code="FOCR",
                cancellation_id=item.cancellation_id,
            )
            item.return_id = payment_return.return_id
        item.status = target
        item.rejection_reason = rejection_reason
        item.resolved_at = datetime.now(UTC)
        db.commit()
        db.refresh(item)
        if payment_return is not None:
            db.refresh(payment_return)
        return item, payment, payment_return, True
    except Exception:
        db.rollback()
        raise
