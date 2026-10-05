"""Gestión de contraseñas y estado de las cuentas.

- Cambio de la propia contraseña con sesión: exige la actual y cierra las demás sesiones.
- Recuperación ("olvidé mi contraseña"): código de un solo uso por correo, con vencimiento,
  intentos limitados y tope de envíos; al usarlo se cierran todas las sesiones.
- Activación y desactivación de cuentas: el admin gestiona administradores y usuarios, el
  administrador solo usuarios y nadie se desactiva a sí mismo. Desactivar cierra sus sesiones.
"""

import logging
import secrets
import time

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.email_templates import EmailMessage
from app.core.mailer import Mailer, send_message
from app.core.security import code_digest, constant_time_equal, digest_token
from app.db.models import AuthSession, PasswordResetChallenge, PendingLogin, User, UserRole
from app.domains.auth.service import hash_password, normalize_email, verify_password

logger = logging.getLogger(__name__)
RESET_TTL_SECONDS = 10 * 60
RESET_MAX_ATTEMPTS = 5
RESET_WINDOW_SECONDS = 15 * 60
RESET_MAX_CODES_PER_WINDOW = 3


class InvalidCurrentPasswordError(Exception):
    pass


class SamePasswordError(Exception):
    pass


class InvalidResetCodeError(Exception):
    pass


class ResetRateLimitError(Exception):
    pass


class AccountChangeForbiddenError(Exception):
    pass


class AccountNotFoundError(Exception):
    pass


def reset_code_message(display_name: str, code: str) -> EmailMessage:
    return EmailMessage(
        subject="Código para restablecer tu contraseña de QALabSPBVI",
        eyebrow="Recuperación de acceso",
        heading="Restablece tu contraseña",
        greeting=f"Hola {display_name},",
        paragraphs=["Usa este código para definir una contraseña nueva en QALabSPBVI."],
        code=code,
        code_caption=f"Vence en {RESET_TTL_SECONDS // 60} minutos y sirve una sola vez.",
        notice=(
            "No compartas este código con nadie. Si no pediste cambiar tu contraseña, ignora "
            "este correo: tu contraseña actual sigue funcionando."
        ),
    )


def _revoke_sessions(db: Session, user_id: int, *, keep_digest: str | None = None) -> int:
    statement = (
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at_epoch.is_(None))
        .values(revoked_at_epoch=int(time.time()))
    )
    if keep_digest is not None:
        statement = statement.where(AuthSession.token_digest != keep_digest)
    return db.execute(statement).rowcount or 0


def change_password(
    db: Session,
    *,
    user: User,
    current_password: str,
    new_password: str,
    current_session_token: str | None,
) -> int:
    """Cambia la contraseña y cierra las otras sesiones. Devuelve cuántas cerró."""
    stored = db.get(User, user.id)
    if stored is None or not verify_password(stored.password_hash, current_password):
        raise InvalidCurrentPasswordError
    if verify_password(stored.password_hash, new_password):
        raise SamePasswordError
    stored.password_hash = hash_password(new_password)
    closed = _revoke_sessions(
        db,
        stored.id,
        keep_digest=digest_token(current_session_token) if current_session_token else None,
    )
    db.commit()
    return closed


def request_password_reset(db: Session, *, email: str, mailer: Mailer) -> None:
    """Envía un código si la cuenta existe y está activa; si no, no hace nada.

    La ruta responde igual en ambos casos para no revelar qué correos están registrados.
    """
    user = db.scalar(select(User).where(User.email == normalize_email(email)))
    if user is None or not user.is_active:
        return
    now = int(time.time())
    recent = db.scalar(
        select(func.count())
        .select_from(PasswordResetChallenge)
        .where(
            PasswordResetChallenge.user_id == user.id,
            PasswordResetChallenge.created_at_epoch > now - RESET_WINDOW_SECONDS,
        )
    )
    if recent >= RESET_MAX_CODES_PER_WINDOW:
        raise ResetRateLimitError
    db.execute(
        update(PasswordResetChallenge)
        .where(PasswordResetChallenge.user_id == user.id, PasswordResetChallenge.used_at_epoch.is_(None))
        .values(used_at_epoch=now)
    )
    code = f"{secrets.randbelow(1_000_000):06d}"
    challenge = PasswordResetChallenge(
        user_id=user.id,
        code_digest=code_digest(code),
        created_at_epoch=now,
        expires_at_epoch=now + RESET_TTL_SECONDS,
    )
    db.add(challenge)
    db.commit()
    try:
        send_message(mailer, recipient=user.email, message=reset_code_message(user.display_name, code))
    except Exception:
        challenge.used_at_epoch = now
        db.commit()
        raise


def confirm_password_reset(db: Session, *, email: str, code: str, new_password: str) -> None:
    """Valida el código, define la contraseña nueva y cierra todas las sesiones."""
    user = db.scalar(select(User).where(User.email == normalize_email(email)))
    if user is None or not user.is_active:
        raise InvalidResetCodeError
    now = int(time.time())
    challenge = db.scalar(
        select(PasswordResetChallenge)
        .where(PasswordResetChallenge.user_id == user.id, PasswordResetChallenge.used_at_epoch.is_(None))
        .order_by(PasswordResetChallenge.id.desc())
    )
    if challenge is None or challenge.expires_at_epoch <= now or challenge.attempts >= RESET_MAX_ATTEMPTS:
        raise InvalidResetCodeError
    if not constant_time_equal(challenge.code_digest, code_digest(code)):
        challenge.attempts += 1
        if challenge.attempts >= RESET_MAX_ATTEMPTS:
            challenge.used_at_epoch = now
        db.commit()
        raise InvalidResetCodeError
    challenge.used_at_epoch = now
    user.password_hash = hash_password(new_password)
    _revoke_sessions(db, user.id)
    db.execute(update(PendingLogin).where(PendingLogin.user_id == user.id).values(expires_at_epoch=now))
    db.commit()


def set_account_active(db: Session, *, actor: User, user_id: int, active: bool) -> User:
    target = db.get(User, user_id)
    if target is None:
        raise AccountNotFoundError
    if target.id == actor.id:
        raise AccountChangeForbiddenError("No puedes desactivar ni reactivar tu propia cuenta.")
    if target.role is UserRole.ADMIN:
        raise AccountChangeForbiddenError("La cuenta admin no se puede desactivar.")
    if actor.role is UserRole.ADMINISTRADOR and target.role is not UserRole.USUARIO:
        raise AccountChangeForbiddenError("Solo el admin gestiona cuentas de administrador.")
    if actor.role not in {UserRole.ADMIN, UserRole.ADMINISTRADOR}:
        raise AccountChangeForbiddenError("No tienes permisos para gestionar cuentas.")
    target.is_active = active
    if not active:
        _revoke_sessions(db, target.id)
    db.commit()
    db.refresh(target)
    return target


SIGNUP_WINDOW_SECONDS = 60 * 60
SIGNUP_MAX_PER_IP = 5


class SignupRateLimitError(Exception):
    pass


def register_user(db: Session, *, email: str, display_name: str, password: str, client_ip: str) -> User:
    """Registro público de la demo: crea una cuenta usuario, sin épica y sin enviar correos.

    El correo queda verificado en el primer inicio de sesión (código MFA). Un admin o
    administrador la asocia a una épica para que empiece a trabajar.
    """
    from app.core.security import keyed_digest
    from app.db.models import SignupAttempt
    from app.domains.auth.service import create_user

    now = int(time.time())
    ip_digest = keyed_digest(client_ip)
    recent = db.scalar(
        select(func.count())
        .select_from(SignupAttempt)
        .where(SignupAttempt.ip_digest == ip_digest, SignupAttempt.occurred_at_epoch > now - SIGNUP_WINDOW_SECONDS)
    )
    if recent >= SIGNUP_MAX_PER_IP:
        raise SignupRateLimitError
    db.add(SignupAttempt(ip_digest=ip_digest, occurred_at_epoch=now))
    db.commit()
    return create_user(db, email=email, display_name=display_name, password=password, role=UserRole.USUARIO)
