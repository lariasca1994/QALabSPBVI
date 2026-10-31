import logging
import secrets
import time

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.email_templates import EmailMessage
from app.core.mailer import MailDeliveryError, Mailer, send_message
from app.core.security import code_digest, digest_token, keyed_digest
from app.domains.auth.automation import (
    ensure_fixtures_in_background,
    is_automation_account,
    record_code,
)
from app.domains.auth import two_factor

logger = logging.getLogger(__name__)
from app.db.models import (
    AuthSession,
    EmailLoginChallenge,
    LoginAttempt,
    PendingLogin,
    User,
    UserRole,
)

PASSWORD_HASHER = PasswordHasher()
DUMMY_PASSWORD_HASH = PASSWORD_HASHER.hash("dummy-password-used-only-for-timing")
OTP_TTL_SECONDS = 5 * 60
OTP_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 15 * 60
# Mismo criterio en todo el portafolio: 5 contraseñas erradas por cuenta bloquean un tiempo;
# por conexión se admite más, porque varias personas pueden compartir la misma red.
LOGIN_MAX_FAILURES = 5
LOGIN_MAX_FAILURES_IP = 10
# Los intentos de ingreso (eventos de seguridad) se depuran a los 15 días.
LOGIN_ATTEMPTS_RETENTION_SECONDS = 15 * 24 * 3600
SESSION_IDLE_SECONDS = 30 * 60
SESSION_ABSOLUTE_SECONDS = 8 * 60 * 60
# Reenvío del código: ventana del inicio pendiente, espera entre envíos y tope de códigos.
PENDING_LOGIN_SECONDS = 15 * 60
RESEND_COOLDOWN_SECONDS = 60
MAX_CODES_PER_WINDOW = 3
MAX_CODES_PER_WINDOW_AUTOMATION = 20


class LoginRateLimitError(Exception):
    pass


class InvalidCredentialsError(Exception):
    """Correo o contraseña incorrectos (mismo error en ambos casos)."""


class InvalidChallengeError(Exception):
    pass


class ResendCooldownError(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__(retry_after)
        self.retry_after = retry_after


def normalize_email(email: str) -> str:
    return email.strip().casefold()


def hash_password(password: str) -> str:
    return PASSWORD_HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return PASSWORD_HASHER.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError, VerificationError):
        return False


def create_user(
    db: Session,
    *,
    email: str,
    display_name: str,
    password: str,
    role: UserRole,
) -> User:
    user = User(
        email=normalize_email(email),
        display_name=display_name.strip(),
        password_hash=hash_password(password),
        role=role,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def mfa_code_message(display_name: str, code: str) -> EmailMessage:
    return EmailMessage(
        subject="Código de acceso a QALabSPBVI",
        eyebrow="Acceso seguro",
        heading="Tu código de acceso",
        greeting=f"Hola {display_name},",
        paragraphs=["Usa este código para completar el inicio de sesión en QALabSPBVI."],
        code=code,
        code_caption=f"Vence en {OTP_TTL_SECONDS // 60} minutos y sirve una sola vez.",
        notice=(
            "No compartas este código con nadie: el equipo de QALabSPBVI nunca te lo va a "
            "pedir. Si no intentaste iniciar sesión, ignora este correo."
        ),
    )


ROLE_LABELS = {
    UserRole.ADMIN: "Admin",
    UserRole.ADMINISTRADOR: "Administrador",
    UserRole.USUARIO: "Usuario",
}


def welcome_message(user: User, created_by: User) -> EmailMessage:
    # Nunca incluir la contraseña: quien creó la cuenta la entrega por un canal seguro.
    return EmailMessage(
        subject="Te damos la bienvenida a QALabSPBVI",
        eyebrow="Nueva cuenta",
        heading="Tu cuenta está lista",
        greeting=f"Hola {user.display_name},",
        paragraphs=[
            "Se creó tu cuenta en QALabSPBVI, el laboratorio de pruebas de pagos inmediatos.",
            "Para ingresar usa tu correo y la contraseña que te compartió quien creó la cuenta. "
            "En cada inicio de sesión te enviaremos un código de un solo uso a este correo.",
        ],
        details=[
            ("Correo", user.email),
            ("Rol", ROLE_LABELS[user.role]),
            ("Creada por", created_by.email),
        ],
        notice=(
            "Si no esperabas esta cuenta, avísale al administrador de QALabSPBVI. "
            "Nunca te pediremos tu contraseña por correo."
        ),
    )


def _email_hash(email: str) -> str:
    return keyed_digest(normalize_email(email))


def _check_rate_limit(
    db: Session,
    *,
    email_hash: str,
    ip_hash: str,
    now: int,
) -> None:
    window_start = now - LOGIN_WINDOW_SECONDS
    email_failures = db.scalar(
        select(func.count())
        .select_from(LoginAttempt)
        .where(
            LoginAttempt.email_digest == email_hash,
            LoginAttempt.succeeded.is_(False),
            LoginAttempt.occurred_at_epoch > window_start,
        )
    )
    ip_failures = db.scalar(
        select(func.count())
        .select_from(LoginAttempt)
        .where(
            LoginAttempt.ip_digest == ip_hash,
            LoginAttempt.succeeded.is_(False),
            LoginAttempt.occurred_at_epoch > window_start,
        )
    )
    if email_failures >= LOGIN_MAX_FAILURES or ip_failures >= LOGIN_MAX_FAILURES_IP:
        raise LoginRateLimitError


def start_login(
    db: Session,
    *,
    email: str,
    password: str,
    client_ip: str,
    mailer: Mailer,
    pending_token: str,
) -> str:
    """Valida la contraseña y pide el segundo factor; devuelve el método ("email" o "totp").

    Con el correo se envía el código; con la app autenticadora no se envía nada.

    Con credenciales inválidas lanza InvalidCredentialsError, igual si el correo no
    existe o si la contraseña está mal: el mensaje y el tiempo de respuesta (se verifica
    contra un hash de relleno) no revelan qué cuentas existen.
    """
    normalized_email = normalize_email(email)
    email_hash = _email_hash(normalized_email)
    ip_hash = keyed_digest(client_ip)
    now = int(time.time())
    _check_rate_limit(db, email_hash=email_hash, ip_hash=ip_hash, now=now)

    user = db.scalar(select(User).where(User.email == normalized_email))
    hash_to_verify = (
        user.password_hash
        if user is not None and user.is_active
        else DUMMY_PASSWORD_HASH
    )
    password_valid = verify_password(hash_to_verify, password)
    succeeded = user is not None and user.is_active and password_valid

    db.execute(delete(LoginAttempt).where(
        LoginAttempt.occurred_at_epoch < now - LOGIN_ATTEMPTS_RETENTION_SECONDS
    ))
    db.add(
        LoginAttempt(
            email_digest=email_hash,
            ip_digest=ip_hash,
            occurred_at_epoch=now,
            succeeded=succeeded,
        )
    )
    db.commit()

    if not succeeded or user is None:
        raise InvalidCredentialsError

    db.add(
        PendingLogin(
            user_id=user.id,
            token_digest=digest_token(pending_token),
            created_at_epoch=now,
            expires_at_epoch=now + PENDING_LOGIN_SECONDS,
            last_sent_epoch=now,
        )
    )
    db.commit()
    if two_factor.login_method(db, user) == two_factor.METHOD_TOTP:
        pending = db.scalar(
            select(PendingLogin).where(PendingLogin.token_digest == digest_token(pending_token))
        )
        if pending is not None:
            # Sin correo enviado, "usar un código por correo" no tiene que esperar.
            pending.last_sent_epoch = now - RESEND_COOLDOWN_SECONDS
            db.commit()
        return two_factor.METHOD_TOTP
    _issue_code(db, user=user, now=now, mailer=mailer)
    return two_factor.METHOD_EMAIL


def _issue_code(db: Session, *, user: User, now: int, mailer: Mailer) -> None:
    """Genera un código nuevo, invalida los anteriores y lo envía por correo."""
    recent_challenges = db.scalar(
        select(func.count())
        .select_from(EmailLoginChallenge)
        .where(
            EmailLoginChallenge.user_id == user.id,
            EmailLoginChallenge.created_at_epoch > now - LOGIN_WINDOW_SECONDS,
        )
    )
    # Las cuentas de automatización E2E (lista cerrada, código protegido por token) ingresan
    # varias veces por corrida: tienen un tope mayor. El resto mantiene 3 cada 15 minutos.
    limit = (
        MAX_CODES_PER_WINDOW_AUTOMATION if is_automation_account(user) else MAX_CODES_PER_WINDOW
    )
    if recent_challenges >= limit:
        raise LoginRateLimitError

    previous = db.scalars(
        select(EmailLoginChallenge).where(
            EmailLoginChallenge.user_id == user.id,
            EmailLoginChallenge.used_at_epoch.is_(None),
        )
    ).all()
    for challenge in previous:
        challenge.used_at_epoch = now

    code = f"{secrets.randbelow(1_000_000):06d}"
    challenge = EmailLoginChallenge(
        user_id=user.id,
        code_digest=code_digest(code),
        created_at_epoch=now,
        expires_at_epoch=now + OTP_TTL_SECONDS,
    )
    db.add(challenge)
    db.commit()
    automation = is_automation_account(user)
    if automation:
        # Cuenta de automatización E2E: el código queda además en el registro protegido
        # por token (ver app/domains/auth/automation.py), así la prueba no depende del
        # correo y la persona dueña de la cuenta lo sigue recibiendo cuando hay envío.
        record_code(db, user=user, code=code, expires_at_epoch=challenge.expires_at_epoch)
        ensure_fixtures_in_background(owner_email=user.email)
    try:
        send_message(
            mailer,
            recipient=user.email,
            message=mfa_code_message(user.display_name, code),
        )
    except MailDeliveryError as error:
        if automation:
            logger.warning("No se envió el correo MFA de la cuenta de automatización; queda el registro.")
            return
        challenge.used_at_epoch = now
        db.commit()
        raise MailDeliveryError from error


def resend_code(db: Session, *, pending_token: str | None, mailer: Mailer) -> None:
    """Reenvía el código de un inicio pendiente. Un token desconocido no hace nada."""
    if not pending_token:
        return
    now = int(time.time())
    pending = db.scalar(
        select(PendingLogin).where(PendingLogin.token_digest == digest_token(pending_token))
    )
    if pending is None or pending.expires_at_epoch <= now:
        return
    user = db.get(User, pending.user_id)
    if user is None or not user.is_active or not two_factor.methods(db, user)["email"]:
        return
    wait = pending.last_sent_epoch + RESEND_COOLDOWN_SECONDS - now
    if wait > 0:
        raise ResendCooldownError(wait)
    pending.last_sent_epoch = now
    db.commit()
    _issue_code(db, user=user, now=now, mailer=mailer)


def complete_login(
    db: Session,
    *,
    email: str,
    code: str,
) -> tuple[User, str, int]:
    now = int(time.time())
    user = db.scalar(select(User).where(User.email == normalize_email(email)))
    if user is None or not user.is_active or not two_factor.methods(db, user)["email"]:
        raise InvalidChallengeError

    challenge = db.scalar(
        select(EmailLoginChallenge)
        .where(
            EmailLoginChallenge.user_id == user.id,
            EmailLoginChallenge.used_at_epoch.is_(None),
        )
        .order_by(EmailLoginChallenge.id.desc())
        .limit(1)
    )
    if (
        challenge is None
        or challenge.expires_at_epoch <= now
        or challenge.attempts >= OTP_MAX_ATTEMPTS
    ):
        raise InvalidChallengeError

    if not secrets.compare_digest(challenge.code_digest, code_digest(code)):
        challenge.attempts += 1
        if challenge.attempts >= OTP_MAX_ATTEMPTS:
            challenge.used_at_epoch = now
        db.commit()
        raise InvalidChallengeError

    challenge.used_at_epoch = now
    db.commit()
    two_factor.confirm_email_method(db, user)
    return _open_session(db, user, now)


def pending_user(db: Session, pending_token: str | None) -> User | None:
    """Cuenta del inicio de sesión pendiente (contraseña ya validada), o None."""
    if not pending_token:
        return None
    pending = db.scalar(
        select(PendingLogin).where(PendingLogin.token_digest == digest_token(pending_token))
    )
    if pending is None or pending.expires_at_epoch <= int(time.time()):
        return None
    user = db.get(User, pending.user_id)
    return user if user is not None and user.is_active else None


def issue_email_code(db: Session, *, user: User, mailer: Mailer) -> None:
    """Código por correo para activar ese método desde Seguridad (mismos topes del login)."""
    _issue_code(db, user=user, now=int(time.time()), mailer=mailer)


def confirm_email_code(db: Session, *, user: User, code: str) -> bool:
    """Valida el último código enviado al correo y deja el correo como método activo."""
    now = int(time.time())
    challenge = db.scalar(
        select(EmailLoginChallenge)
        .where(
            EmailLoginChallenge.user_id == user.id,
            EmailLoginChallenge.used_at_epoch.is_(None),
        )
        .order_by(EmailLoginChallenge.id.desc())
        .limit(1)
    )
    if (
        challenge is None
        or challenge.expires_at_epoch <= now
        or challenge.attempts >= OTP_MAX_ATTEMPTS
    ):
        return False
    if not secrets.compare_digest(challenge.code_digest, code_digest(code)):
        challenge.attempts += 1
        if challenge.attempts >= OTP_MAX_ATTEMPTS:
            challenge.used_at_epoch = now
        db.commit()
        return False
    challenge.used_at_epoch = now
    db.commit()
    two_factor.confirm_email_method(db, user)
    return True


def complete_totp_login(
    db: Session, *, pending_token: str | None, code: str
) -> tuple[User, str, int]:
    """Segundo paso con la app autenticadora (o su primera configuración)."""
    user = pending_user(db, pending_token)
    if user is None:
        raise InvalidChallengeError
    try:
        valid = two_factor.verify_totp(db, user, code)
    except two_factor.TotpNotConfiguredError as error:
        raise InvalidChallengeError from error
    if not valid:
        raise InvalidChallengeError
    return _open_session(db, user, int(time.time()))


def _open_session(db: Session, user: User, now: int) -> tuple[User, str, int]:
    for pending in db.scalars(select(PendingLogin).where(PendingLogin.user_id == user.id)):
        db.delete(pending)
    raw_token = secrets.token_urlsafe(48)
    session = AuthSession(
        user_id=user.id,
        token_digest=digest_token(raw_token),
        created_at_epoch=now,
        last_seen_epoch=now,
        expires_at_epoch=now + SESSION_ABSOLUTE_SECONDS,
    )
    db.add(session)
    db.commit()
    return user, raw_token, SESSION_ABSOLUTE_SECONDS


def revoke_session(db: Session, raw_token: str) -> None:
    session = db.scalar(
        select(AuthSession).where(
            AuthSession.token_digest == digest_token(raw_token),
            AuthSession.revoked_at_epoch.is_(None),
        )
    )
    if session is not None:
        session.revoked_at_epoch = int(time.time())
        db.commit()
