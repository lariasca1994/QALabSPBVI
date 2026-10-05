"""Rutas posteriores al pago: devoluciones, cancelaciones, investigaciones, notificaciones y
reporte de estado al cliente, cada una con su mensaje ISO 20022 de laboratorio."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import (
    AccountNotificationEntry,
    AccountNotificationsResponse,
    CancellationRequestCreate,
    InvestigationResolutionRequest,
    InvestigationResponse,
    PaymentReturnRequest,
    PaymentReturnResponse,
    PaymentStatusReportResponse,
)
from app.core.security import require_roles
from app.db.models import Account, CancellationRequest, LedgerEntry, Payment, PaymentReturn, User, UserRole
from app.db.session import get_db
from app.domains.iso20022.lifecycle import (
    CANCELLATION_REASONS,
    CANCELLATION_REJECTION_REASONS,
    RETURN_REASONS,
    Camt029Data,
    Camt054Data,
    Camt056Data,
    NotificationEntry,
    Pacs004Data,
    Pain002Data,
    build_camt029,
    build_camt054,
    build_camt056,
    build_pacs004,
    build_pain002,
    pain002_status_for_payment,
)
from app.domains.payments.lifecycle import (
    InvestigationNotFoundError,
    LifecycleConflictError,
    LifecycleValidationError,
    PaymentNotFoundError,
    ReturnInsufficientFundsError,
    create_cancellation_request,
    create_return,
    get_investigation,
    resolve_investigation,
    returned_cents,
)

# 422 como número: Starlette renombró la constante (UNPROCESSABLE_ENTITY → UNPROCESSABLE_CONTENT).
HTTP_422 = 422
router = APIRouter(tags=["ciclo de vida del pago"])
DbSession = Annotated[Session, Depends(get_db)]
AnyRole = Annotated[
    User,
    Depends(require_roles(UserRole.ADMIN, UserRole.ADMINISTRADOR, UserRole.USUARIO)),
]
PAYMENT_NOT_FOUND = "No se encontro el pago con ese identificador de operacion."


def _aware(value: datetime | None) -> datetime:
    value = value or datetime.now(UTC)
    return value if value.tzinfo is not None and value.utcoffset() is not None else value.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str:
    return _aware(value).isoformat(timespec="seconds").replace("+00:00", "Z")


def _spbvi(db: Session, account_id: str) -> str:
    account = db.get(Account, account_id)
    return account.spbvi_id if account is not None else "desconocido"


def _raise(error: Exception) -> None:
    if isinstance(error, PaymentNotFoundError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, PAYMENT_NOT_FOUND) from error
    if isinstance(error, InvestigationNotFoundError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No se encontro la investigacion.") from error
    if isinstance(error, LifecycleValidationError):
        raise HTTPException(HTTP_422, str(error)) from error
    if isinstance(error, ReturnInsufficientFundsError):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "La cuenta receptora no tiene saldo suficiente para la devolucion.",
        ) from error
    if isinstance(error, LifecycleConflictError):
        raise HTTPException(status.HTTP_409_CONFLICT, str(error) or "Conflicto de estado.") from error
    raise error


def _return_response(db: Session, item: PaymentReturn, payment: Payment, replayed: bool) -> PaymentReturnResponse:
    created_at = _aware(item.created_at)
    return PaymentReturnResponse(
        return_id=item.return_id,
        operation_id=payment.operation_id,
        original_amount_cents=payment.amount_cents,
        amount_cents=item.amount_cents,
        returned_total_cents=returned_cents(db, payment.id),
        reason_code=item.reason_code,
        reason=RETURN_REASONS[item.reason_code],
        cancellation_id=item.cancellation_id,
        replayed=replayed,
        pacs004_xml=build_pacs004(
            Pacs004Data(
                return_id=item.return_id,
                operation_id=payment.operation_id,
                original_amount_cents=payment.amount_cents,
                returned_amount_cents=item.amount_cents,
                reason_code=item.reason_code,
                created_at=created_at,
            )
        ),
        created_at=_iso(created_at),
    )


def _investigation_response(
    db: Session,
    item: CancellationRequest,
    payment: Payment,
    *,
    replayed: bool = False,
    payment_return: PaymentReturn | None = None,
) -> InvestigationResponse:
    requester = _spbvi(db, payment.source_account_id)
    responder = _spbvi(db, payment.destination_account_id)
    if payment_return is None and item.return_id:
        payment_return = db.scalar(select(PaymentReturn).where(PaymentReturn.return_id == item.return_id))
    return InvestigationResponse(
        cancellation_id=item.cancellation_id,
        operation_id=payment.operation_id,
        status=item.status,
        reason_code=item.reason_code,
        reason=CANCELLATION_REASONS[item.reason_code],
        rejection_reason=item.rejection_reason,
        rejection_detail=CANCELLATION_REJECTION_REASONS.get(item.rejection_reason or ""),
        replayed=replayed,
        camt056_xml=build_camt056(
            Camt056Data(
                cancellation_id=item.cancellation_id,
                operation_id=payment.operation_id,
                requester_spbvi_id=requester,
                responder_spbvi_id=responder,
                original_amount_cents=payment.amount_cents,
                reason_code=item.reason_code,
                created_at=_aware(item.created_at),
            )
        ),
        camt029_xml=build_camt029(
            Camt029Data(
                cancellation_id=item.cancellation_id,
                operation_id=payment.operation_id,
                responder_spbvi_id=responder,
                requester_spbvi_id=requester,
                status=item.status,
                rejection_reason=item.rejection_reason,
                created_at=_aware(item.resolved_at or item.created_at),
            )
        ),
        payment_return=(
            _return_response(db, payment_return, payment, replayed) if payment_return is not None else None
        ),
        created_at=_iso(item.created_at),
        resolved_at=_iso(item.resolved_at) if item.resolved_at else None,
    )


@router.post(
    "/payments/{operation_id}/returns",
    response_model=PaymentReturnResponse,
    status_code=status.HTTP_201_CREATED,
)
def post_payment_return(
    operation_id: str,
    request: PaymentReturnRequest,
    db: DbSession,
    response: Response,
    _: AnyRole,
) -> PaymentReturnResponse:
    """pacs.004: devuelve total o parcialmente un pago liquidado."""
    try:
        item, payment, created = create_return(
            db,
            operation_id=operation_id,
            return_id=request.return_id,
            amount_cents=request.amount_cents,
            reason_code=request.reason_code,
        )
    except Exception as error:  # noqa: BLE001 - _raise traduce los errores de dominio
        _raise(error)
    if not created:
        response.status_code = status.HTTP_200_OK
    return _return_response(db, item, payment, not created)


@router.get("/payments/{operation_id}/returns", response_model=list[PaymentReturnResponse])
def get_payment_returns(operation_id: str, db: DbSession, _: AnyRole) -> list[PaymentReturnResponse]:
    payment = db.scalar(select(Payment).where(Payment.operation_id == operation_id))
    if payment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, PAYMENT_NOT_FOUND)
    items = db.scalars(
        select(PaymentReturn).where(PaymentReturn.payment_id == payment.id).order_by(PaymentReturn.id)
    ).all()
    return [_return_response(db, item, payment, False) for item in items]


@router.post(
    "/payments/{operation_id}/cancellation-requests",
    response_model=InvestigationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def post_cancellation_request(
    operation_id: str,
    request: CancellationRequestCreate,
    db: DbSession,
    response: Response,
    _: AnyRole,
) -> InvestigationResponse:
    """camt.056: el SPBVI de origen pide cancelar; queda una investigación pendiente (camt.029 PDCR)."""
    try:
        item, payment, created = create_cancellation_request(
            db,
            operation_id=operation_id,
            cancellation_id=request.cancellation_id,
            reason_code=request.reason_code,
        )
    except Exception as error:  # noqa: BLE001
        _raise(error)
    if not created:
        response.status_code = status.HTTP_200_OK
    return _investigation_response(db, item, payment, replayed=not created)


@router.get("/investigations/{cancellation_id}", response_model=InvestigationResponse)
def get_investigation_route(cancellation_id: str, db: DbSession, _: AnyRole) -> InvestigationResponse:
    try:
        item, payment = get_investigation(db, cancellation_id)
    except Exception as error:  # noqa: BLE001
        _raise(error)
    return _investigation_response(db, item, payment)


@router.post("/investigations/{cancellation_id}/resolution", response_model=InvestigationResponse)
def post_investigation_resolution(
    cancellation_id: str,
    request: InvestigationResolutionRequest,
    db: DbSession,
    _: AnyRole,
) -> InvestigationResponse:
    """camt.029: el SPBVI receptor acepta (devolución pacs.004 FOCR) o rechaza con motivo."""
    try:
        item, payment, payment_return, created = resolve_investigation(
            db,
            cancellation_id=cancellation_id,
            accepted=request.accepted,
            rejection_reason=request.rejection_reason,
        )
    except Exception as error:  # noqa: BLE001
        _raise(error)
    return _investigation_response(db, item, payment, replayed=not created, payment_return=payment_return)


@router.get("/accounts/{account_id}/notifications", response_model=AccountNotificationsResponse)
def get_account_notifications(
    account_id: str,
    db: DbSession,
    _: AnyRole,
    operation_id: str | None = None,
) -> AccountNotificationsResponse:
    """camt.054: créditos y débitos de la cuenta asociados a pagos y devoluciones."""
    account = db.get(Account, account_id)
    if account is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No se encontro la cuenta.")
    query = (
        select(LedgerEntry, Payment.operation_id)
        .join(Payment, Payment.id == LedgerEntry.payment_id)
        .where(LedgerEntry.account_id == account_id)
        .order_by(LedgerEntry.id)
    )
    if operation_id:
        query = query.where(Payment.operation_id == operation_id)
    rows = db.execute(query).all()
    if operation_id and not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "La cuenta no tiene movimientos de esa operacion.")
    entries = [
        NotificationEntry(
            entry_id=entry.id,
            amount_cents=abs(entry.amount_cents),
            credit=entry.amount_cents > 0,
            booked_at=_aware(entry.created_at),
            entry_type=entry.entry_type,
            operation_id=operation,
        )
        for entry, operation in rows
    ]
    now = datetime.now(UTC)
    notification_id = f"{account_id}-{entries[-1].entry_id if entries else 0}"
    return AccountNotificationsResponse(
        account_id=account.id,
        spbvi_id=account.spbvi_id,
        entries=[
            AccountNotificationEntry(
                entry_id=entry.entry_id,
                notification_type="credit" if entry.credit else "debit",
                entry_type=entry.entry_type,
                amount_cents=entry.amount_cents,
                currency="COP",
                value_date=entry.booked_at.date().isoformat(),
                operation_id=entry.operation_id,
            )
            for entry in entries
        ],
        total_credits_cents=sum(entry.amount_cents for entry in entries if entry.credit),
        total_debits_cents=sum(entry.amount_cents for entry in entries if not entry.credit),
        camt054_xml=build_camt054(
            Camt054Data(
                notification_id=notification_id,
                account_id=account.id,
                entries=tuple(entries),
                created_at=now,
            )
        ),
    )


@router.get("/payments/{operation_id}/status-report", response_model=PaymentStatusReportResponse)
def get_payment_status_report(operation_id: str, db: DbSession, _: AnyRole) -> PaymentStatusReportResponse:
    """pain.002 independiente: estado del pago para el cliente que lo ordenó."""
    payment = db.scalar(select(Payment).where(Payment.operation_id == operation_id))
    if payment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, PAYMENT_NOT_FOUND)
    transaction_status = pain002_status_for_payment(payment.status)
    return PaymentStatusReportResponse(
        operation_id=payment.operation_id,
        payment_type=payment.payment_type,
        transaction_status=transaction_status,
        amount_cents=payment.amount_cents,
        pain002_xml=build_pain002(
            Pain002Data(
                operation_id=payment.operation_id,
                transaction_status=transaction_status,
                amount_cents=payment.amount_cents,
                created_at=_aware(payment.created_at),
            )
        ),
    )
