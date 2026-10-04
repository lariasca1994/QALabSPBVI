from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.schemas import (
    AccountCreateRequest,
    AccountResponse,
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
)
from app.core.security import require_roles
from app.db.models import Account, KeyStatus, LedgerEntry, User, UserRole
from app.db.session import get_db
from app.domains.keys.dife import resolve_key
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
    DestinationKeyNotFoundError,
    IdempotencyConflictError,
    InsufficientFundsError,
    SameSpbviPaymentError,
    create_payment,
    create_inter_spbvi_payment,
)
from app.domains.payments.mol import MolInsufficientFundsError, MolSettlementError
from app.domains.iso20022.messages import (
    Pacs002Data,
    Pacs008Data,
    build_pacs002,
    build_pacs008,
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
    except MolInsufficientFundsError as error:
        pacs002 = build_pacs002(
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
    pacs008 = build_pacs008(
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
    pacs002 = build_pacs002(
        Pacs002Data(
            message_id=f"pacs002-{payment.operation_id}",
            original_message_id=f"pacs008-{payment.operation_id}",
            original_message_name="pacs.008.001.08",
            group_status=pacs002_group_status_for_payment(payment.status),
            created_at=created_at,
        )
    )
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
    )
