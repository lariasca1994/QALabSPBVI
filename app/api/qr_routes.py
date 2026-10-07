"""Pagos con código QR (perfil EMVCo de laboratorio, ver app/domains/qr/emv.py).

- POST /qr/static: QR estático (o híbrido con monto) de una llave confirmada.
- POST /qr/charges, GET y DELETE /qr/charges/{id}: cobro dinámico de un solo uso.
- POST /qr/decode: valida el QR y muestra qué se va a pagar, sin mover dinero.
- POST /qr/pay: paga el QR. Si la llave está en el DIFE del SPBVI de la cuenta origen es
  intra (sin consultar el DICE); si no, inter por DICE y MOL. Reutiliza las rutas de pago,
  con sus rechazos ISO, idempotencia y límite de UVB.
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse

from app.api.routes import (
    HTTP_422,
    DbSession,
    create_inter_spbvi_payment_route,
    create_intra_spbvi_payment,
)
from app.api.schemas import (
    PaymentCreateRequest,
    QrChargeRequest,
    QrChargeResponse,
    QrCodeResponse,
    QrDecodeRequest,
    QrDecodeResponse,
    QrPaymentResponse,
    QrPayRequest,
    QrStaticRequest,
)
from app.core.security import require_roles
from app.db.models import Account, User, UserRole
from app.domains.keys.dife import resolve_key
from app.domains.keys.key_types import InvalidKeyError
from app.domains.keys.persistence import DiceSession, DifeSession
from app.domains.payments.service import AmountLimitExceededError
from app.domains.qr import service
from app.domains.qr.emv import QrFormatError, build_payload, parse_payload
from app.domains.qr.persistence import QrCharge, QrSession

router = APIRouter(prefix="/qr", tags=["qr"])
AnyRole = Annotated[User, Depends(require_roles(UserRole.ADMIN, UserRole.ADMINISTRADOR, UserRole.USUARIO))]


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat().replace("+00:00", "Z")


def _charge_response(charge: QrCharge) -> QrChargeResponse:
    now = int(datetime.now(UTC).timestamp())
    return QrChargeResponse(
        charge_id=charge.charge_id,
        payload=charge.payload,
        status=charge.status,
        spbvi_id=charge.spbvi_id,
        key_type=charge.key_type,
        key_value=charge.key_value,
        amount_cents=charge.amount_cents,
        reference=charge.reference,
        merchant_name=charge.merchant_name,
        merchant_city=charge.merchant_city,
        created_at=_iso(charge.created_at_epoch),
        expires_at=_iso(charge.expires_at_epoch),
        seconds_left=max(0, charge.expires_at_epoch - now) if charge.status in ("pending", "reserved") else 0,
        operation_id=charge.operation_id,
        payer_account_id=charge.payer_account_id,
    )


def _receiver(dife_db, user: User, request) -> service.ReceiverKey:
    try:
        return service.resolve_receiver_key(
            dife_db, user=user, spbvi_id=request.spbvi_id, key_type=request.key_type, key_value=request.key_value
        )
    except InvalidKeyError as error:
        raise HTTPException(status_code=HTTP_422, detail=str(error)) from error
    except service.QrKeyNotFoundError as error:
        raise HTTPException(status_code=404, detail="No hay una llave confirmada con esos datos en ese SPBVI.") from error
    except service.QrKeyOwnershipError as error:
        raise HTTPException(status_code=403, detail="Solo puedes cobrar con llaves de las que eres titular.") from error


def _parse(payload: str):
    try:
        return parse_payload(payload)
    except QrFormatError as error:
        raise HTTPException(status_code=HTTP_422, detail=str(error)) from error


@router.post("/static", response_model=QrCodeResponse)
def create_static_qr(request: QrStaticRequest, dife_db: DifeSession, user: AnyRole) -> QrCodeResponse:
    key = _receiver(dife_db, user, request)
    try:
        data = service.static_payload(
            key, amount_cents=request.amount_cents, merchant_name=request.merchant_name, merchant_city=request.merchant_city
        )
        payload = build_payload(data)
    except (AmountLimitExceededError, QrFormatError) as error:
        raise HTTPException(status_code=HTTP_422, detail=str(error)) from error
    decoded = parse_payload(payload)
    return QrCodeResponse(
        payload=payload,
        qr_type=decoded.qr_type,
        key_type=decoded.key_type,
        key_value=decoded.key_value,
        amount_cents=decoded.amount_cents,
        merchant_name=decoded.merchant_name,
        merchant_city=decoded.merchant_city,
    )


@router.post("/charges", response_model=QrChargeResponse, status_code=status.HTTP_201_CREATED)
def create_qr_charge(request: QrChargeRequest, dife_db: DifeSession, qr_db: QrSession, user: AnyRole) -> QrChargeResponse:
    key = _receiver(dife_db, user, request)
    try:
        charge = service.create_charge(
            qr_db,
            user=user,
            key=key,
            amount_cents=request.amount_cents,
            reference=request.reference,
            merchant_name=request.merchant_name,
            merchant_city=request.merchant_city,
            expires_in_seconds=request.expires_in_seconds,
        )
    except (AmountLimitExceededError, QrFormatError) as error:
        raise HTTPException(status_code=HTTP_422, detail=str(error)) from error
    return _charge_response(charge)


@router.get("/charges/{charge_id}", response_model=QrChargeResponse)
def get_qr_charge(charge_id: str, db: DbSession, qr_db: QrSession, _: AnyRole) -> QrChargeResponse:
    try:
        return _charge_response(service.get_charge(qr_db, db, charge_id))
    except service.ChargeNotFoundError as error:
        raise HTTPException(status_code=404, detail="No existe un cobro con ese identificador.") from error


@router.delete("/charges/{charge_id}", response_model=QrChargeResponse)
def cancel_qr_charge(charge_id: str, db: DbSession, qr_db: QrSession, user: AnyRole) -> QrChargeResponse:
    try:
        return _charge_response(service.cancel_charge(qr_db, db, charge_id, user=user))
    except service.ChargeNotFoundError as error:
        raise HTTPException(status_code=404, detail="No existe un cobro con ese identificador.") from error
    except service.QrKeyOwnershipError as error:
        raise HTTPException(status_code=403, detail="Solo quien creó el cobro o un administrador puede anularlo.") from error
    except service.ChargeStateError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error)) from error


@router.post("/decode", response_model=QrDecodeResponse)
def decode_qr(request: QrDecodeRequest, db: DbSession, qr_db: QrSession, _: AnyRole) -> QrDecodeResponse:
    data = _parse(request.payload)
    charge_status = expires_at = None
    if data.charge_id:
        try:
            charge = service.get_charge(qr_db, db, data.charge_id)
        except service.ChargeNotFoundError as error:
            raise HTTPException(status_code=404, detail="El cobro de este QR no existe.") from error
        charge_status, expires_at = charge.status, _iso(charge.expires_at_epoch)
    return QrDecodeResponse(
        qr_type=data.qr_type,
        key_type=data.key_type,
        key_value=data.key_value,
        merchant_name=data.merchant_name,
        merchant_city=data.merchant_city,
        amount_cents=data.amount_cents,
        amount_editable=data.qr_type == "static",
        reference=data.reference,
        charge_id=data.charge_id,
        charge_status=charge_status,
        expires_at=expires_at,
    )


@router.post(
    "/pay",
    response_model=QrPaymentResponse,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"description": "Cobro ya pagado o en pago, o rechazo del pago"}, 410: {"description": "Cobro vencido o anulado"}},
)
def pay_qr(
    request: QrPayRequest,
    response: Response,
    db: DbSession,
    dife_db: DifeSession,
    dice_db: DiceSession,
    qr_db: QrSession,
    user: AnyRole,
) -> QrPaymentResponse | JSONResponse:
    data = _parse(request.payload)
    charge: QrCharge | None = None
    if data.qr_type == "static":
        if request.amount_cents is None:
            raise HTTPException(status_code=HTTP_422, detail="Este QR no trae monto: indica el monto a pagar.")
        amount = request.amount_cents
    else:
        amount = data.amount_cents or 0
        if request.amount_cents is not None and request.amount_cents != amount:
            raise HTTPException(status_code=HTTP_422, detail="El monto no coincide con el del QR; no se puede cambiar.")

    source = db.get(Account, request.source_account_id.strip())
    if source is None:
        raise HTTPException(status_code=404, detail="No se encontro la cuenta de origen.")

    if data.qr_type == "dynamic":
        try:
            charge = service.reserve(qr_db, db, data.charge_id or "", payer_account_id=source.id)
        except service.ChargeNotFoundError as error:
            raise HTTPException(status_code=404, detail="El cobro de este QR no existe.") from error
        except service.ChargeStateError as error:
            raise HTTPException(status_code=error.status_code, detail=str(error)) from error
        if (charge.key_type, charge.key_value, charge.amount_cents) != (data.key_type, data.key_value, amount):
            service.release(qr_db, charge)
            raise HTTPException(status_code=HTTP_422, detail="El QR no coincide con el cobro registrado.")
        operation_id = service.operation_id_for(charge.charge_id)
    else:
        if not request.operation_id:
            raise HTTPException(status_code=HTTP_422, detail="El pago con QR estático necesita un operation_id.")
        operation_id = request.operation_id

    payment_request = PaymentCreateRequest(
        operation_id=operation_id,
        source_account_id=source.id,
        destination_key_type=data.key_type,
        destination_key_value=data.key_value,
        amount_cents=amount,
    )
    # Intra si la llave está en el DIFE del SPBVI de origen (el DICE no se consulta).
    intra = resolve_key(dife_db, spbvi_id=source.spbvi_id, key_type=data.key_type, key_value=data.key_value) is not None
    try:
        if intra:
            result = create_intra_spbvi_payment(payment_request, db, dife_db, response, user)
        else:
            result = create_inter_spbvi_payment_route(payment_request, db, dice_db, response, user)
    except HTTPException:
        # Las rutas de pago levantan HTTPException solo cuando no quedó pago (cuenta, llave,
        # saldo, idempotencia o MOL revertido): liberar es seguro. Una caída de base no pasa
        # por aquí y deja la reserva para que la concilie la próxima consulta.
        if charge is not None:
            service.release(qr_db, charge)
        raise
    if isinstance(result, JSONResponse):
        # Rechazo de negocio con su mensaje ISO: el cobro vuelve a quedar disponible.
        if charge is not None:
            service.release(qr_db, charge)
        return result
    if charge is not None:
        service.mark_paid(qr_db, charge, operation_id=operation_id)
    return QrPaymentResponse(
        flow="intra" if intra else "inter",
        qr_type=data.qr_type,
        charge_id=charge.charge_id if charge else None,
        charge_status=charge.status if charge else None,
        payment=result.model_dump(),
    )

