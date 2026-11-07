"""Gestión de contraseñas y estado de las cuentas.

- Cambio de la propia contraseña con sesión: exige la actual y cierra las demás sesiones.
- Recuperación ("olvidé mi contraseña"): enlace de un solo uso por correo (token aleatorio
  guardado como hash, vence en 30 minutos, tope de envíos y respuesta siempre igual). Los
  enlaces inválidos se cuentan por conexión: 5 en 15 minutos bloquean 15 minutos. Al usarlo
  se invalidan los enlaces de la cuenta y se cierran todas las sesiones; la verificación en
  dos pasos no cambia.
- Activación y desactivación de cuentas: el admin gestiona administradores y usuarios, el
  administrador solo usuarios y nadie se desactiva a sí mismo. Desactivar cierra sus sesiones.
"""

import logging
import secrets
import time

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.core.email_templates import EmailMessage
from app.core.mailer import Mailer, send_message
from app.core.security import digest_token, keyed_digest
from app.db.models import (
    AuthSession,
    PasswordResetChallenge,
    PasswordResetFailure,
    PendingLogin,
    User,
    UserRole,
)
from app.domains.auth.service import hash_password, normalize_email, verify_password

logger = logging.getLogger(__name__)
RESET_TTL_SECONDS = 30 * 60
RESET_WINDOW_SECONDS = 15 * 60
RESET_MAX_CODES_PER_WINDOW = 3
# Estándar del portafolio: 5 intentos fallidos bloquean 15 minutos.
RESET_MAX_FAILURES_PER_IP = 5
RESET_LOCK_SECONDS = 15 * 60
RESET_LINK_PARAMETER = "restablecer"


class InvalidCurrentPasswordError(Exception):
    pass


class SamePasswordError(Exception):
    pass


class InvalidResetCodeError(Exception):
    """El enlace no existe, ya se usó o venció."""


class ResetRateLimitError(Exception):
    """Demasiados enlaces inválidos desde la misma conexión."""


class AccountChangeForbiddenError(Exception):
    pass


class AccountNotFoundError(Exception):
    pass


def reset_link_message(display_name: str, link: str) -> EmailMessage:
    return EmailMessage(
        subject="Restablece tu contraseña",
        eyebrow="Recuperación de acceso",
        heading="Restablece tu contraseña",
        greeting=f"Hola {display_name},",
        paragraphs=["Abre este enlace para definir una contraseña nueva en tu cuenta."],
        action_label="Definir contraseña nueva",
        action_url=link,
        action_caption=f"El enlace vence en {RESET_TTL_SECONDS // 60} minutos y sirve una sola vez.",
        notice=(
            "No compartas este enlace con nadie. Si no pediste cambiar tu contraseña, ignora "
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


def reset_link(base_url: str, token: str) -> str:
    """El token va en el fragmento (#): el navegador no lo envía a ningún servidor."""
    return f"{base_url.rstrip('/')}/#{RESET_LINK_PARAMETER}={token}"


def request_password_reset(db: Session, *, email: str, mailer: Mailer, base_url: str) -> None:
    """Envía un enlace si la cuenta existe, está activa y no superó el tope de envíos.

    En cualquier otro caso no hace nada: la ruta responde siempre igual para no revelar qué
    correos están registrados.
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
        logger.info("Recuperación de contraseña: tope de enlaces alcanzado (cuenta %s).", user.id)
        return
    # Un enlace nuevo invalida los anteriores.
    db.execute(
        update(PasswordResetChallenge)
        .where(PasswordResetChallenge.user_id == user.id, PasswordResetChallenge.used_at_epoch.is_(None))
        .values(used_at_epoch=now)
    )
    token = secrets.token_urlsafe(32)
    challenge = PasswordResetChallenge(
        user_id=user.id,
        code_digest=digest_token(token),
        created_at_epoch=now,
        expires_at_epoch=now + RESET_TTL_SECONDS,
    )
    db.add(challenge)
    db.commit()
    try:
        send_message(
            mailer,
            recipient=user.email,
            message=reset_link_message(user.display_name, reset_link(base_url, token)),
        )
    except Exception:
        challenge.used_at_epoch = now
        db.commit()
        raise
    logger.info("Recuperación de contraseña: enlace enviado (cuenta %s).", user.id)


def _check_reset_lock(db: Session, ip_digest: str, now: int) -> None:
    failures = db.scalar(
        select(func.count())
        .select_from(PasswordResetFailure)
        .where(
            PasswordResetFailure.ip_digest == ip_digest,
            PasswordResetFailure.occurred_at_epoch > now - RESET_LOCK_SECONDS,
        )
    )
    if failures >= RESET_MAX_FAILURES_PER_IP:
        raise ResetRateLimitError


def _valid_challenge(db: Session, *, token: str, client_ip: str) -> PasswordResetChallenge:
    """Enlace vigente o InvalidResetCodeError (registrando el fallo de la conexión)."""
    now = int(time.time())
    ip_digest = keyed_digest(client_ip)
    _check_reset_lock(db, ip_digest, now)
    challenge = db.scalar(
        select(PasswordResetChallenge).where(PasswordResetChallenge.code_digest == digest_token(token))
    )
    user = db.get(User, challenge.user_id) if challenge is not None else None
    if (
        challenge is None
        or challenge.used_at_epoch is not None
        or challenge.expires_at_epoch <= now
        or user is None
        or not user.is_active
    ):
        db.execute(
            delete(PasswordResetFailure).where(
                PasswordResetFailure.occurred_at_epoch < now - RESET_LOCK_SECONDS
            )
        )
        db.add(PasswordResetFailure(ip_digest=ip_digest, occurred_at_epoch=now))
        db.commit()
        logger.info("Recuperación de contraseña: enlace inválido o vencido.")
        raise InvalidResetCodeError
    return challenge


def check_password_reset(db: Session, *, token: str, client_ip: str) -> None:
    """Valida el enlace al abrirlo (sin usarlo) para mostrar el formulario o el aviso."""
    _valid_challenge(db, token=token, client_ip=client_ip)


def confirm_password_reset(db: Session, *, token: str, new_password: str, client_ip: str) -> None:
    """Usa el enlace: contraseña nueva, enlaces invalidados y todas las sesiones cerradas."""
    challenge = _valid_challenge(db, token=token, client_ip=client_ip)
    user = db.get(User, challenge.user_id)
    assert user is not None
    now = int(time.time())
    db.execute(
        update(PasswordResetChallenge)
        .where(PasswordResetChallenge.user_id == user.id, PasswordResetChallenge.used_at_epoch.is_(None))
        .values(used_at_epoch=now)
    )
    user.password_hash = hash_password(new_password)
    closed = _revoke_sessions(db, user.id)
    db.execute(update(PendingLogin).where(PendingLogin.user_id == user.id).values(expires_at_epoch=now))
    db.commit()
    logger.info("Recuperación de contraseña: contraseña restablecida (cuenta %s, %s sesiones cerradas).", user.id, closed)


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
