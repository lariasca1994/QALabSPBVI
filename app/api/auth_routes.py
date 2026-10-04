import logging
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import (
    CsrfResponse,
    LoginRequest,
    UserCreateRequest,
    UserResponse,
    VerifyEmailCodeRequest,
)
from app.core.config import get_settings
from app.core.mailer import MailDeliveryError, Mailer, get_mailer, send_message
from app.core.security import (
    CSRF_COOKIE,
    SESSION_ABSOLUTE_SECONDS,
    SESSION_COOKIE,
    AuthenticatedUser,
    issue_csrf_cookie,
    require_roles,
    verify_csrf,
)
from app.db.models import User, UserRole
from app.db.session import get_db
from app.domains.auth.service import (
    InvalidChallengeError,
    LoginRateLimitError,
    PENDING_LOGIN_SECONDS,
    ResendCooldownError,
    complete_login,
    create_user,
    normalize_email,
    resend_code,
    revoke_session,
    start_login,
    welcome_message,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["autenticacion"])
PENDING_LOGIN_COOKIE = "qalab_pending_login"
DbSession = Annotated[Session, Depends(get_db)]
MailerDependency = Annotated[Mailer, Depends(get_mailer)]


def _secure_cookie() -> bool:
    return get_settings().app_env.casefold() not in {"local", "development", "test"}


@router.get("/csrf", response_model=CsrfResponse)
def get_csrf_token(request: Request, response: Response) -> CsrfResponse:
    token = issue_csrf_cookie(request)
    response.set_cookie(
        key=CSRF_COOKIE,
        value=token,
        max_age=SESSION_ABSOLUTE_SECONDS,
        secure=_secure_cookie(),
        httponly=False,
        samesite="strict",
        path="/",
    )
    return CsrfResponse(csrf_token=token)


def _set_pending_login_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=PENDING_LOGIN_COOKIE,
        value=token,
        max_age=PENDING_LOGIN_SECONDS,
        secure=_secure_cookie(),
        httponly=True,
        samesite="strict",
        path="/",
    )


@router.post("/login", status_code=status.HTTP_202_ACCEPTED)
def login(
    request: Request,
    response: Response,
    credentials: LoginRequest,
    db: DbSession,
    mailer: MailerDependency,
) -> dict[str, str]:
    verify_csrf(request)
    # Siempre se emite: su presencia no indica si la contraseña era válida.
    pending_token = secrets.token_urlsafe(32)
    _set_pending_login_cookie(response, pending_token)
    try:
        start_login(
            db,
            email=normalize_email(str(credentials.email)),
            password=credentials.password,
            client_ip=request.client.host if request.client else "unknown",
            mailer=mailer,
            pending_token=pending_token,
        )
    except LoginRateLimitError as error:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Demasiados intentos. Espera antes de volver a probar.",
            headers={"Retry-After": "900"},
        ) from error
    except MailDeliveryError as error:
        logger.exception("No se pudo entregar el codigo MFA por correo.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No se pudo enviar el codigo. Intenta nuevamente mas tarde.",
        ) from error

    return {
        "message": (
            "Si los datos son validos, enviaremos un codigo de acceso al correo "
            "asociado a la cuenta."
        )
    }


@router.post("/resend-code", status_code=status.HTTP_202_ACCEPTED)
def resend_email_code(
    request: Request,
    db: DbSession,
    mailer: MailerDependency,
) -> dict[str, str]:
    verify_csrf(request)
    try:
        resend_code(
            db,
            pending_token=request.cookies.get(PENDING_LOGIN_COOKIE),
            mailer=mailer,
        )
    except ResendCooldownError as error:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Espera {error.retry_after} segundos antes de pedir otro codigo.",
            headers={"Retry-After": str(error.retry_after)},
        ) from error
    except LoginRateLimitError as error:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Alcanzaste el maximo de codigos. Espera 15 minutos e inicia sesion de nuevo.",
            headers={"Retry-After": "900"},
        ) from error
    except MailDeliveryError as error:
        logger.exception("No se pudo reenviar el codigo MFA por correo.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No se pudo enviar el codigo. Intenta nuevamente mas tarde.",
        ) from error
    return {
        "message": (
            "Si hay un inicio de sesion pendiente, enviamos un codigo nuevo al correo "
            "asociado. El codigo anterior deja de funcionar."
        )
    }


@router.post("/verify-email-code")
def verify_email_code(
    request: Request,
    payload: VerifyEmailCodeRequest,
    response: Response,
    db: DbSession,
) -> dict[str, str]:
    verify_csrf(request)
    try:
        user, raw_token, max_age = complete_login(
            db,
            email=normalize_email(str(payload.email)),
            code=payload.code,
        )
    except InvalidChallengeError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="El codigo no es valido o vencio.",
        ) from error

    response.set_cookie(
        key=SESSION_COOKIE,
        value=raw_token,
        max_age=min(max_age, SESSION_ABSOLUTE_SECONDS),
        secure=_secure_cookie(),
        httponly=True,
        samesite="strict",
        path="/",
    )
    csrf_token = secrets.token_urlsafe(32)
    response.set_cookie(
        key=CSRF_COOKIE,
        value=csrf_token,
        max_age=SESSION_ABSOLUTE_SECONDS,
        secure=_secure_cookie(),
        httponly=False,
        samesite="strict",
        path="/",
    )
    return {"message": "Autenticacion completada.", "role": user.role.value}


@router.get("/me", response_model=UserResponse)
def me(user: AuthenticatedUser) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        is_active=user.is_active,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    db: DbSession,
) -> Response:
    verify_csrf(request)
    session_token = request.cookies.get(SESSION_COOKIE)
    if session_token:
        revoke_session(db, session_token)
    response.delete_cookie(
        SESSION_COOKIE,
        secure=_secure_cookie(),
        httponly=True,
        samesite="strict",
        path="/",
    )
    response.delete_cookie(
        CSRF_COOKIE,
        secure=_secure_cookie(),
        httponly=False,
        samesite="strict",
        path="/",
    )
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post(
    "/users",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_user_account(
    payload: UserCreateRequest,
    db: DbSession,
    actor: AuthenticatedUser,
    mailer: MailerDependency,
) -> UserResponse:
    if actor.role not in {UserRole.ADMIN, UserRole.ADMINISTRADOR}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tienes permisos para crear usuarios.",
        )
    if payload.role is UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="El rol admin solo puede asignarse durante el bootstrap seguro.",
        )
    if actor.role is UserRole.ADMINISTRADOR and payload.role is not UserRole.USUARIO:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo el admin puede crear administradores.",
        )
    try:
        user = create_user(
            db,
            email=str(payload.email),
            display_name=payload.display_name,
            password=payload.password,
            role=payload.role,
        )
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya existe un usuario con ese correo.",
        ) from error
    try:
        send_message(mailer, recipient=user.email, message=welcome_message(user, actor))
        notification_status = "sent"
    except MailDeliveryError:
        logger.exception("No se pudo enviar el correo de bienvenida.")
        notification_status = "failed"
    return UserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        is_active=user.is_active,
        notification_status=notification_status,
    )


@router.get(
    "/users",
    response_model=list[UserResponse],
)
def list_users(
    db: DbSession,
    _: Annotated[
        User,
        Depends(require_roles(UserRole.ADMIN, UserRole.ADMINISTRADOR)),
    ],
) -> list[UserResponse]:
    users = db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.email)).all()
    return [
        UserResponse(
            id=user.id,
            email=user.email,
            display_name=user.display_name,
            role=user.role,
            is_active=user.is_active,
        )
        for user in users
    ]
