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
from app.core.mailer import MailDeliveryError, Mailer, get_mailer
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
    complete_login,
    create_user,
    normalize_email,
    revoke_session,
    start_login,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["autenticacion"])
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


@router.post("/login", status_code=status.HTTP_202_ACCEPTED)
def login(
    request: Request,
    credentials: LoginRequest,
    db: DbSession,
    mailer: MailerDependency,
) -> dict[str, str]:
    verify_csrf(request)
    try:
        start_login(
            db,
            email=normalize_email(str(credentials.email)),
            password=credentials.password,
            client_ip=request.client.host if request.client else "unknown",
            mailer=mailer,
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
) -> UserResponse:
    if actor.role not in {UserRole.ADMIN, UserRole.ADMINISTRADOR}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tenes permisos para crear usuarios.",
        )
    if payload.role is UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="El rol admin solo puede asignarse durante el bootstrap seguro.",
        )
    if actor.role is UserRole.ADMIN and payload.role is not UserRole.ADMINISTRADOR:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="El admin inicial solo puede crear administradores.",
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
    return UserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        is_active=user.is_active,
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
