import hashlib
import hmac
import secrets
import time
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import AuthSession, User, UserRole
from app.db.session import get_db

SESSION_COOKIE = "qalab_session"
CSRF_COOKIE = "qalab_csrf"
CSRF_HEADER = "x-csrf-token"
SESSION_IDLE_SECONDS = 30 * 60
SESSION_ABSOLUTE_SECONDS = 8 * 60 * 60


def digest_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def keyed_digest(value: str) -> str:
    secret = get_settings().auth_secret_key
    if len(secret) < 32:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="La autenticacion no esta configurada.",
        )
    return hmac.new(
        secret.encode("utf-8"),
        value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def code_digest(code: str) -> str:
    return keyed_digest(code)


def constant_time_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left, right)


def issue_csrf_cookie(request: Request) -> str:
    token = secrets.token_urlsafe(32)
    request.state.csrf_token = token
    return token


def verify_csrf(request: Request) -> None:
    cookie = request.cookies.get(CSRF_COOKIE)
    header = request.headers.get(CSRF_HEADER)
    if not cookie or not header or not constant_time_equal(cookie, header):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token CSRF invalido o ausente.",
        )


def current_user(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> User:
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        verify_csrf(request)
    token = request.cookies.get(SESSION_COOKIE)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Autenticacion requerida.",
            headers={"WWW-Authenticate": "Cookie"},
        )

    now = int(time.time())
    session = db.scalar(
        select(AuthSession).where(AuthSession.token_digest == digest_token(token))
    )
    if (
        session is None
        or session.revoked_at_epoch is not None
        or session.expires_at_epoch <= now
        or session.last_seen_epoch <= now - SESSION_IDLE_SECONDS
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="La sesion no es valida o expiro.",
            headers={"WWW-Authenticate": "Cookie"},
        )

    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="La sesion no es valida o expiro.",
        )

    session.last_seen_epoch = now
    db.commit()
    # Para el registro de auditoría: quién hizo la solicitud.
    request.state.user_id = user.id
    return user


def require_roles(*roles: UserRole):
    allowed = frozenset(roles)

    def dependency(user: Annotated[User, Depends(current_user)]) -> User:
        if user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes permisos para esta accion.",
            )
        return user

    return dependency


AuthenticatedUser = Annotated[User, Depends(current_user)]
