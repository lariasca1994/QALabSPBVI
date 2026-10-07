"""Cobros con QR: generación, ciclo del cobro dinámico y conciliación con la base de pagos.

El cobro (base QR, MySQL) y el pago (base de pagos, PostgreSQL) no comparten transacción.
La consistencia sale de dos reglas, igual que DIFE/DICE:

1. El pago de un cobro siempre usa operation_id = "qr-{charge_id}". La restricción única
   de pagos garantiza un solo pago por cobro aunque dos personas lo escaneen a la vez.
2. Orden: reservar el cobro (UPDATE condicional) → pagar (idempotente) → marcarlo pagado.
   Si el pago se rechaza, la reserva se libera. Si algo se cae a mitad de camino, la
   próxima consulta concilia: con el pago hecho, el cobro pasa a pagado; sin pago y con
   la reserva vencida, vuelve a pendiente.
"""

import time
import uuid
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.models import Payment, User, UserRole
from app.domains.keys.dife import resolve_key
from app.domains.keys.key_types import normalize_key_type, normalize_key_value
from app.domains.payments.service import check_amount_limit
from app.domains.qr.emv import QrData, build_payload
from app.domains.qr.persistence import QrCharge

DEFAULT_EXPIRY_SECONDS = 600
# Tiempo tras el cual una reserva sin pago se considera abandonada (pago caído a mitad).
RESERVATION_TIMEOUT_SECONDS = 60


class QrKeyNotFoundError(Exception):
    pass


class QrKeyOwnershipError(Exception):
    pass


class ChargeNotFoundError(Exception):
    pass


class ChargeStateError(Exception):
    """El cobro no admite la acción por su estado; status_code indica la respuesta HTTP."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class ReceiverKey:
    spbvi_id: str
    key_type: str
    key_value: str


def operation_id_for(charge_id: str) -> str:
    return f"qr-{charge_id}"


def resolve_receiver_key(dife_db: Session, *, user: User, spbvi_id: str, key_type: str, key_value: str) -> ReceiverKey:
    """La llave debe estar confirmada; un usuario solo cobra con llaves de las que es titular."""
    key_type = normalize_key_type(key_type)
    key_value = normalize_key_value(key_type, key_value)
    key = resolve_key(dife_db, spbvi_id=spbvi_id, key_type=key_type, key_value=key_value)
    if key is None:
        raise QrKeyNotFoundError
    if user.role is UserRole.USUARIO and (key.owner_email or "").lower() != user.email.lower():
        raise QrKeyOwnershipError
    return ReceiverKey(spbvi_id=spbvi_id, key_type=key.key_type, key_value=key.key_value)


def static_payload(key: ReceiverKey, *, amount_cents: int | None, merchant_name: str, merchant_city: str) -> QrData:
    if amount_cents is not None:
        check_amount_limit(amount_cents)
    data = QrData(
        qr_type="static_hybrid" if amount_cents is not None else "static",
        key_type=key.key_type,
        key_value=key.key_value,
        merchant_name=merchant_name,
        merchant_city=merchant_city,
        amount_cents=amount_cents,
    )
    return data


def create_charge(
    qr_db: Session,
    *,
    user: User,
    key: ReceiverKey,
    amount_cents: int,
    reference: str | None,
    merchant_name: str,
    merchant_city: str,
    expires_in_seconds: int = DEFAULT_EXPIRY_SECONDS,
) -> QrCharge:
    check_amount_limit(amount_cents)
    now = int(time.time())
    charge_id = uuid.uuid4().hex
    data = QrData(
        qr_type="dynamic",
        key_type=key.key_type,
        key_value=key.key_value,
        merchant_name=merchant_name,
        merchant_city=merchant_city,
        amount_cents=amount_cents,
        reference=reference,
        charge_id=charge_id,
    )
    charge = QrCharge(
        charge_id=charge_id,
        spbvi_id=key.spbvi_id,
        key_type=key.key_type,
        key_value=key.key_value,
        amount_cents=amount_cents,
        reference=reference,
        merchant_name=merchant_name[:25],
        merchant_city=merchant_city[:15],
        payload=build_payload(data),
        status="pending",
        created_by=user.email,
        created_at_epoch=now,
        expires_at_epoch=now + expires_in_seconds,
    )
    qr_db.add(charge)
    qr_db.commit()
    return charge


def get_charge(qr_db: Session, payments_db: Session, charge_id: str) -> QrCharge:
    """Devuelve el cobro ya conciliado (vencimiento y reservas abandonadas)."""
    charge = qr_db.get(QrCharge, charge_id)
    if charge is None:
        raise ChargeNotFoundError
    return reconcile(qr_db, payments_db, charge)


def reconcile(qr_db: Session, payments_db: Session, charge: QrCharge) -> QrCharge:
    now = int(time.time())
    changed = False
    if charge.status == "reserved":
        payment = payments_db.scalar(
            select(Payment).where(Payment.operation_id == operation_id_for(charge.charge_id))
        )
        if payment is not None:
            charge.status, charge.operation_id = "paid", payment.operation_id
            charge.payer_account_id = payment.source_account_id
            charge.paid_at_epoch = charge.paid_at_epoch or now
            changed = True
        elif (charge.reserved_at_epoch or 0) + RESERVATION_TIMEOUT_SECONDS <= now:
            _clear_reservation(charge)
            changed = True
    if charge.status == "pending" and charge.expires_at_epoch <= now:
        charge.status = "expired"
        changed = True
    if changed:
        qr_db.commit()
    return charge


def _clear_reservation(charge: QrCharge) -> None:
    charge.status = "pending"
    charge.payer_account_id = None
    charge.reserved_at_epoch = None


def reserve(qr_db: Session, payments_db: Session, charge_id: str, *, payer_account_id: str) -> QrCharge:
    """Reserva el cobro para un pago. Reintentar con la misma cuenta es idempotente."""
    charge = get_charge(qr_db, payments_db, charge_id)
    now = int(time.time())
    reserved = qr_db.execute(
        update(QrCharge)
        .where(
            QrCharge.charge_id == charge_id,
            QrCharge.status == "pending",
            QrCharge.expires_at_epoch > now,
        )
        .values(status="reserved", payer_account_id=payer_account_id, reserved_at_epoch=now)
    ).rowcount
    qr_db.commit()
    qr_db.refresh(charge)
    if reserved == 1:
        return charge
    if charge.status == "reserved" and charge.payer_account_id == payer_account_id:
        return charge
    if charge.status == "paid":
        raise ChargeStateError("El cobro ya fue pagado.", 409)
    if charge.status == "reserved":
        raise ChargeStateError("El cobro se está pagando en otra operación. Intenta de nuevo en un minuto.", 409)
    if charge.status == "cancelled":
        raise ChargeStateError("El cobro fue anulado.", 410)
    raise ChargeStateError("El cobro venció.", 410)


def mark_paid(qr_db: Session, charge: QrCharge, *, operation_id: str) -> None:
    qr_db.execute(
        update(QrCharge)
        .where(QrCharge.charge_id == charge.charge_id, QrCharge.status == "reserved")
        .values(status="paid", operation_id=operation_id, paid_at_epoch=int(time.time()))
    )
    qr_db.commit()
    qr_db.refresh(charge)


def release(qr_db: Session, charge: QrCharge) -> None:
    """Rechazo de negocio del pago: el cobro vuelve a quedar disponible (o vencido)."""
    qr_db.execute(
        update(QrCharge)
        .where(QrCharge.charge_id == charge.charge_id, QrCharge.status == "reserved")
        .values(status="pending", payer_account_id=None, reserved_at_epoch=None)
    )
    qr_db.commit()
    qr_db.refresh(charge)


def cancel_charge(qr_db: Session, payments_db: Session, charge_id: str, *, user: User) -> QrCharge:
    charge = get_charge(qr_db, payments_db, charge_id)
    if user.role is UserRole.USUARIO and charge.created_by.lower() != user.email.lower():
        raise QrKeyOwnershipError
    cancelled = qr_db.execute(
        update(QrCharge)
        .where(QrCharge.charge_id == charge_id, QrCharge.status == "pending")
        .values(status="cancelled")
    ).rowcount
    qr_db.commit()
    qr_db.refresh(charge)
    if cancelled != 1 and charge.status != "cancelled":
        raise ChargeStateError(f"No se puede anular un cobro en estado {charge.status}.", 409)
    return charge
