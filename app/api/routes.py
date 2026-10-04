from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.schemas import (
    AccountCreateRequest,
    AccountResponse,
    AccountStatementResponse,
    KeyLifecycleRequest,
    KeyOwnerUpdateRequest,
    KeyRegistrationRequest,
    KeySuspendRequest,
    KeyReactivateRequest,
    KeyDeleteResponse,
    InterSpbviPaymentRejectionResponse,
    InterSpbviPaymentResponse,
    PaymentCreateRequest,
    PaymentKeyResponse,
    PaymentResponse,
    PaymentStatusResponse,
    StatementEntry,
)
from app.core.security import require_roles
from app.db.models import Account, KeyStatus, LedgerEntry, Payment, User, UserRole
from app.db.session import get_db
from app.domains.keys.dife import resolve_key
from app.domains.keys.key_types import (
    InvalidKeyError,
    key_type_catalog,
    normalize_key_type,
    normalize_key_value,
)
from app.domains.keys.persistence import DiceSession, DifeKey, DifeSession
from app.domains.keys.service import (
    DuplicateKeyError,
    KeyNotFoundError,
    KeyOwnershipError,
    KeyStateConflictError,
    KeyStoreConsistencyError,
    delete_key,
    assign_key_owner,
    reactivate_key,
    register_key,
    suspend_key,
)
from app.domains.payments.service import (
    AccountNotFoundError,
    AmountLimitExceededError,
    DestinationKeyNotFoundError,
    IdempotencyConflictError,
    InsufficientFundsError,
    SameSpbviPaymentError,
    create_payment,
    create_inter_spbvi_payment,
)
from app.domains.payments.mol import MolInsufficientFundsError, MolSettlementError
from app.domains.iso20022.gateway import pacs002_xml, pacs008_xml
from app.domains.iso20022.messages import (
    Pacs002Data,
    Pacs008Data,
    pacs002_group_status_for_payment,
)

router = APIRouter()
DbSession = Annotated[Session, Depends(get_db)]


def _key_response(key: DifeKey) -> PaymentKeyResponse:
    return PaymentKeyResponse(
        id=key.id,
        key_type=key.key_type,
        key_value=key.key_value,
        spbvi_id=key.spbvi_id,
        deposit_product_id=key.deposit_product_id,
        status=(
            KeyStatus.CONFIRMED
            if key.status is KeyStatus.ACTIVE
            else key.status
        ),
    )


@router.get("/health", tags=["salud"])
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@router.post(
    "/accounts",
    response_model=AccountResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["cuentas"],
)
def create_account(
    request: AccountCreateRequest,
    db: DbSession,
    _: Annotated[
        User,
        Depends(require_roles(UserRole.ADMIN, UserRole.ADMINISTRADOR)),
    ],
) -> AccountResponse:
    account = Account(
        id=request.account_id.strip(),
        spbvi_id=request.spbvi_id.strip(),
        balance_cents=request.balance_cents,
    )
    db.add(account)
    if account.balance_cents > 0:
        db.add(
            LedgerEntry(
                account_id=account.id,
                amount_cents=account.balance_cents,
                entry_type="opening",
            )
        )
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El identificador de cuenta ya existe.",
        ) from error
    except Exception:
        db.rollback()
        raise
    db.refresh(account)
    return account


@router.get(
    "/accounts/{account_id}/statement",
    response_model=AccountStatementResponse,
    tags=["cuentas"],
)
def get_account_statement(
    account_id: str,
    db: DbSession,
    _: Annotated[
        User,
        Depends(require_roles(UserRole.ADMIN, UserRole.ADMINISTRADOR, UserRole.USUARIO)),
    ],
) -> AccountStatementResponse:
    account = db.get(Account, account_id)
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No se encontro la cuenta.",
        )
    rows = db.execute(
        select(LedgerEntry, Payment.operation_id)
        .outerjoin(Payment, Payment.id == LedgerEntry.payment_id)
        .where(LedgerEntry.account_id == account_id)
        .order_by(LedgerEntry.id)
    ).all()
    entries = [
        StatementEntry(
            entry_id=entry.id,
            entry_type=entry.entry_type,
            amount_cents=entry.amount_cents,
            operation_id=operation_id,
            created_at=_iso_datetime(entry.created_at),
        )
        for entry, operation_id in rows
    ]
    credits = sum(entry.amount_cents for entry in entries if entry.amount_cents > 0)
    debits = -sum(entry.amount_cents for entry in entries if entry.amount_cents < 0)
    # El saldo inicial de la cuenta se registra como asiento "opening"; antes de él es 0.
    opening = 0
    closing = opening + credits - debits
    return AccountStatementResponse(
        account_id=account.id,
        spbvi_id=account.spbvi_id,
        opening_balance_cents=opening,
        entries=entries,
        total_credits_cents=credits,
        total_debits_cents=debits,
        closing_balance_cents=closing,
        reconciled=closing == account.balance_cents,
    )


def _iso_datetime(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


@router.post(
    "/difes/{spbvi_id}/keys",
    response_model=PaymentKeyResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["llaves"],
)
def create_key(
    spbvi_id: str,
    request: KeyRegistrationRequest,
    dife_db: DifeSession,
    dice_db: DiceSession,
    actor: Annotated[
        User,
        Depends(require_roles(UserRole.ADMIN, UserRole.ADMINISTRADOR)),
    ],
) -> PaymentKeyResponse:
    try:
        key = register_key(
            dife_db,
            dice_db,
            key_type=request.key_type,
            key_value=request.key_value,
            spbvi_id=spbvi_id,
            deposit_product_id=request.deposit_product_id,
            owner_email=str(request.owner_email) if request.owner_email else None,
            actor_email=actor.email,
            actor_role=actor.role.value,
        )
        return PaymentKeyResponse(
            id=key.id,
            key_type=key.key_type,
            key_value=key.key_value,
            spbvi_id=key.spbvi_id,
            deposit_product_id=key.deposit_product_id,
            status=KeyStatus.CONFIRMED,
        )
    except DuplicateKeyError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La llave ya esta registrada.",
        ) from error
    except (SQLAlchemyError, RuntimeError) as error:
        dife_db.rollback()
        dice_db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No se pudo completar el registro entre DIFE y DICE. Reintenta la misma solicitud.",
        ) from error


@router.post(
    "/difes/{spbvi_id}/keys/suspend",
    response_model=PaymentKeyResponse,
    tags=["llaves"],
)
def suspend_registered_key(
    spbvi_id: str,
    request: KeySuspendRequest,
    dife_db: DifeSession,
    dice_db: DiceSession,
    actor: Annotated[
        User,
        Depends(
            require_roles(
                UserRole.ADMIN,
                UserRole.ADMINISTRADOR,
                UserRole.USUARIO,
            )
        ),
    ],
) -> PaymentKeyResponse:
    if (
        request.suspension_type == "administrative"
        and actor.role not in {UserRole.ADMIN, UserRole.ADMINISTRADOR}
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La suspension administrativa requiere rol administrador.",
        )
    try:
        key = suspend_key(
            dife_db,
            dice_db,
            spbvi_id=spbvi_id,
            key_type=request.key_type,
            key_value=request.key_value,
            suspension_type=request.suspension_type,
            reason=request.reason,
            actor_email=actor.email,
            actor_role=actor.role.value,
        )
    except KeyNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No se encontro la llave registrada en ambos directorios.",
        ) from error
    except KeyOwnershipError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo el titular de la llave puede solicitar esta accion personal.",
        ) from error
    except KeyStateConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El estado actual de la llave no permite suspenderla.",
        ) from error
    except KeyStoreConsistencyError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="DIFE y DICE tienen datos inconsistentes para esta llave.",
        ) from error
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No se pudo completar la suspension en DIFE y DICE; reintenta la misma solicitud.",
        ) from error
    return _key_response(key)


@router.post(
    "/difes/{spbvi_id}/keys/reactivate",
    response_model=PaymentKeyResponse,
    tags=["llaves"],
)
def reactivate_registered_key(
    spbvi_id: str,
    request: KeyReactivateRequest,
    dife_db: DifeSession,
    dice_db: DiceSession,
    actor: Annotated[
        User,
        Depends(
            require_roles(
                UserRole.ADMIN,
                UserRole.ADMINISTRADOR,
                UserRole.USUARIO,
            )
        ),
    ],
) -> PaymentKeyResponse:
    if (
        request.reactivation_type == "administrative"
        and actor.role not in {UserRole.ADMIN, UserRole.ADMINISTRADOR}
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La reactivacion administrativa requiere rol administrador.",
        )
    try:
        key = reactivate_key(
            dife_db,
            dice_db,
            spbvi_id=spbvi_id,
            key_type=request.key_type,
            key_value=request.key_value,
            reactivation_type=request.reactivation_type,
            reason=request.reason,
            actor_email=actor.email,
            actor_role=actor.role.value,
        )
    except KeyNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No se encontro la llave registrada en ambos directorios.",
        ) from error
    except KeyOwnershipError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo el titular de la llave puede solicitar esta accion personal.",
        ) from error
    except KeyStateConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El estado actual de la llave no permite reactivarla.",
        ) from error
    except KeyStoreConsistencyError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="DIFE y DICE tienen datos inconsistentes para esta llave.",
        ) from error
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No se pudo completar la reactivacion en DIFE y DICE; reintenta la misma solicitud.",
        ) from error
    return _key_response(key)


@router.patch(
    "/difes/{spbvi_id}/keys/owner",
    response_model=PaymentKeyResponse,
    tags=["llaves"],
)
def assign_registered_key_owner(
    spbvi_id: str,
    request: KeyOwnerUpdateRequest,
    dife_db: DifeSession,
    dice_db: DiceSession,
    actor: Annotated[
        User,
        Depends(require_roles(UserRole.ADMIN, UserRole.ADMINISTRADOR)),
    ],
) -> PaymentKeyResponse:
    try:
        key = assign_key_owner(
            dife_db,
            dice_db,
            spbvi_id=spbvi_id,
            key_type=request.key_type,
            key_value=request.key_value,
            owner_email=str(request.owner_email),
            reason=request.reason,
            actor_email=actor.email,
            actor_role=actor.role.value,
        )
    except KeyNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No se encontro la llave registrada en ambos directorios.",
        ) from error
    except KeyStoreConsistencyError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="DIFE y DICE tienen datos inconsistentes para esta llave.",
        ) from error
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No se pudo asociar el titular en DIFE y DICE; reintenta la misma solicitud.",
        ) from error
    return _key_response(key)


@router.delete(
    "/difes/{spbvi_id}/keys",
    response_model=KeyDeleteResponse,
    tags=["llaves"],
)
def delete_registered_key(
    spbvi_id: str,
    request: KeyLifecycleRequest,
    dife_db: DifeSession,
    dice_db: DiceSession,
    actor: Annotated[
        User,
        Depends(require_roles(UserRole.ADMIN, UserRole.ADMINISTRADOR)),
    ],
) -> KeyDeleteResponse:
    try:
        delete_key(
            dife_db,
            dice_db,
            spbvi_id=spbvi_id,
            key_type=request.key_type,
            key_value=request.key_value,
            reason=request.reason,
            actor_email=actor.email,
            actor_role=actor.role.value,
        )
    except KeyNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No se encontro la llave registrada.",
        ) from error
    except KeyStoreConsistencyError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="DIFE y DICE tienen datos inconsistentes para esta llave.",
        ) from error
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No se pudo eliminar la llave de DIFE y DICE; reintenta la misma solicitud.",
        ) from error
    return KeyDeleteResponse(
        deleted=True,
        key_type=request.key_type,
        key_value=request.key_value,
        spbvi_id=spbvi_id,
    )


@router.get("/keys/types", tags=["llaves"])
def list_key_types() -> list[dict[str, str]]:
    """Catálogo de tipos de llave Bre-B con ejemplo y formato esperado."""
    return key_type_catalog()


@router.get(
    "/difes/{spbvi_id}/keys/resolve",
    response_model=PaymentKeyResponse,
    tags=["llaves"],
)
def resolve_local_key(
    spbvi_id: str,
    dife_db: DifeSession,
    key_type: str,
    key_value: str,
    _: Annotated[
        User,
        Depends(require_roles(UserRole.ADMIN, UserRole.ADMINISTRADOR, UserRole.USUARIO)),
    ],
) -> PaymentKeyResponse:
    try:
        key_type = normalize_key_type(key_type)
        key_value = normalize_key_value(key_type, key_value)
    except InvalidKeyError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error
    key = resolve_key(
        dife_db,
        spbvi_id=spbvi_id,
        key_type=key_type,
        key_value=key_value,
    )
    if key is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No se encontro la llave confirmada en este SPBVI.",
        )
    return key


@router.post(
    "/payments",
    response_model=PaymentResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["pagos"],
)
def create_intra_spbvi_payment(
    request: PaymentCreateRequest,
    db: DbSession,
    dife_db: DifeSession,
    response: Response,
    _: Annotated[
        User,
        Depends(require_roles(UserRole.ADMIN, UserRole.ADMINISTRADOR, UserRole.USUARIO)),
    ],
) -> PaymentResponse:
    try:
        payment, created = create_payment(
            db,
            dife_db,
            operation_id=request.operation_id,
            source_account_id=request.source_account_id,
            destination_key_type=request.destination_key_type,
            destination_key_value=request.destination_key_value,
            amount_cents=request.amount_cents,
        )
    except AmountLimitExceededError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error
    except AccountNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No se encontro la cuenta de origen.",
        ) from error
    except DestinationKeyNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No se encontro una llave destino confirmada en el DIFE.",
        ) from error
    except InsufficientFundsError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La cuenta de origen no tiene saldo suficiente.",
        ) from error
    except IdempotencyConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El identificador de operacion ya existe con otros datos.",
        ) from error

    if not created:
        response.status_code = status.HTTP_200_OK

    return PaymentResponse(
        id=payment.id,
        operation_id=payment.operation_id,
        source_account_id=payment.source_account_id,
        destination_account_id=payment.destination_account_id,
        amount_cents=payment.amount_cents,
        payment_type=payment.payment_type,
        status=payment.status,
        replayed=not created,
    )


@router.get(
    "/payments/{operation_id}",
    response_model=PaymentStatusResponse,
    tags=["pagos"],
)
def get_payment_status(
    operation_id: str,
    db: DbSession,
    _: Annotated[
        User,
        Depends(require_roles(UserRole.ADMIN, UserRole.ADMINISTRADOR, UserRole.USUARIO)),
    ],
) -> PaymentStatusResponse:
    payment = db.scalar(select(Payment).where(Payment.operation_id == operation_id))
    if payment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No se encontro el pago con ese identificador de operacion.",
        )
    return PaymentStatusResponse(
        operation_id=payment.operation_id,
        payment_type=payment.payment_type,
        status=payment.status,
        iso_status=pacs002_group_status_for_payment(payment.status),
        source_account_id=payment.source_account_id,
        destination_account_id=payment.destination_account_id,
        amount_cents=payment.amount_cents,
        created_at=_iso_datetime(payment.created_at),
    )


@router.post(
    "/payments/inter-spbvi",
    response_model=InterSpbviPaymentResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["pagos"],
)
def create_inter_spbvi_payment_route(
    request: PaymentCreateRequest,
    db: DbSession,
    dice_db: DiceSession,
    response: Response,
    _: Annotated[
        User,
        Depends(require_roles(UserRole.ADMIN, UserRole.ADMINISTRADOR, UserRole.USUARIO)),
    ],
) -> InterSpbviPaymentResponse | JSONResponse:
    try:
        payment, created = create_inter_spbvi_payment(
            db,
            dice_db,
            operation_id=request.operation_id,
            source_account_id=request.source_account_id,
            destination_key_type=request.destination_key_type,
            destination_key_value=request.destination_key_value,
            amount_cents=request.amount_cents,
        )
    except AccountNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No se encontro la cuenta de origen.",
        ) from error
    except DestinationKeyNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="DICE no encontro una llave confirmada o su cuenta receptora.",
        ) from error
    except SameSpbviPaymentError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="La llave pertenece al mismo SPBVI; usa el endpoint intra-SPBVI.",
        ) from error
    except InsufficientFundsError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La cuenta de origen no tiene saldo suficiente.",
        ) from error
    except IdempotencyConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El identificador de operacion ya existe con otros datos.",
        ) from error
    except AmountLimitExceededError as error:
        return _inter_rejection(request.operation_id, str(error), "AMOUNT_LIMIT_EXCEEDED", 422)
    except MolInsufficientFundsError as error:
        pacs002, iso_gateway = pacs002_xml(
            Pacs002Data(
                message_id=f"pacs002-{request.operation_id}",
                original_message_id=f"pacs008-{request.operation_id}",
                original_message_name="pacs.008.001.08",
                group_status=pacs002_group_status_for_payment("rejected"),
                created_at=datetime.now(UTC),
                status_reason="INSUFFICIENT_FUNDS",
            )
        )
        rejection = InterSpbviPaymentRejectionResponse(
            detail=str(error),
            operation_id=request.operation_id,
            status="rejected",
            pacs002_xml=pacs002,
            iso_gateway=iso_gateway,
        )
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=rejection.model_dump(),
        )
    except MolSettlementError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El MOL simulado no pudo completar la liquidacion.",
        ) from error
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No se pudo consultar DICE o persistir la liquidacion MOL.",
        ) from error

    if not created:
        response.status_code = status.HTTP_200_OK
    source = db.get(Account, payment.source_account_id)
    destination = db.get(Account, payment.destination_account_id)
    if source is None or destination is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No se pudieron recuperar las cuentas para generar los mensajes ISO 20022.",
        )
    created_at = payment.created_at
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        created_at = created_at.replace(tzinfo=UTC)
    pacs008, gateway008 = pacs008_xml(
        Pacs008Data(
            message_id=f"pacs008-{payment.operation_id}",
            operation_id=payment.operation_id,
            source_spbvi_id=source.spbvi_id,
            destination_spbvi_id=destination.spbvi_id,
            source_account_id=source.id,
            destination_account_id=destination.id,
            amount_cents=payment.amount_cents,
            created_at=created_at,
        )
    )
    pacs002, gateway002 = pacs002_xml(
        Pacs002Data(
            message_id=f"pacs002-{payment.operation_id}",
            original_message_id=f"pacs008-{payment.operation_id}",
            original_message_name="pacs.008.001.08",
            group_status=pacs002_group_status_for_payment(payment.status),
            created_at=created_at,
        )
    )
    # Solo se reporta render si ambos mensajes salieron del gateway.
    iso_gateway = gateway008 if gateway008 == gateway002 else "local"
    return InterSpbviPaymentResponse(
        id=payment.id,
        operation_id=payment.operation_id,
        source_account_id=payment.source_account_id,
        destination_account_id=payment.destination_account_id,
        amount_cents=payment.amount_cents,
        payment_type=payment.payment_type,
        status=payment.status,
        replayed=not created,
        pacs008_xml=pacs008,
        pacs002_xml=pacs002,
        iso_gateway=iso_gateway,
    )


def _inter_rejection(operation_id: str, detail: str, reason: str, status_code: int) -> JSONResponse:
    """Rechazo de negocio inter-SPBVI con pacs.002 RJCT y su razón propietaria."""
    pacs002, iso_gateway = pacs002_xml(
        Pacs002Data(
            message_id=f"pacs002-{operation_id}",
            original_message_id=f"pacs008-{operation_id}",
            original_message_name="pacs.008.001.08",
            group_status=pacs002_group_status_for_payment("rejected"),
            created_at=datetime.now(UTC),
            status_reason=reason,
        )
    )
    rejection = InterSpbviPaymentRejectionResponse(
        detail=detail,
        operation_id=operation_id,
        status="rejected",
        pacs002_xml=pacs002,
        iso_gateway=iso_gateway,
    )
    return JSONResponse(status_code=status_code, content=rejection.model_dump())
