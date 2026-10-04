from sqlalchemy import update
from sqlalchemy.orm import Session

from app.db.models import Account, LedgerEntry, Payment


class MolSettlementError(Exception):
    pass


class MolInsufficientFundsError(MolSettlementError):
    pass


def settle_with_mol(
    db: Session,
    *,
    payment: Payment,
    source: Account,
    destination: Account,
) -> None:
    debit = db.execute(
        update(Account)
        .where(
            Account.id == source.id,
            Account.balance_cents >= payment.amount_cents,
        )
        .values(balance_cents=Account.balance_cents - payment.amount_cents)
    )
    if debit.rowcount != 1:
        raise MolInsufficientFundsError("La cuenta de origen no tiene saldo suficiente.")

    db.add(
        LedgerEntry(
            payment_id=payment.id,
            account_id=source.id,
            amount_cents=-payment.amount_cents,
            entry_type="debit",
        )
    )
    db.flush()

    credit = db.execute(
        update(Account)
        .where(Account.id == destination.id)
        .values(balance_cents=Account.balance_cents + payment.amount_cents)
    )
    if credit.rowcount != 1:
        raise MolSettlementError("La cuenta receptora no esta disponible.")

    db.add(
        LedgerEntry(
            payment_id=payment.id,
            account_id=destination.id,
            amount_cents=payment.amount_cents,
            entry_type="credit",
        )
    )
    payment.status = "completed"
