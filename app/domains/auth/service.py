import secrets
import time

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.mailer import MailDeliveryError, Mailer
from app.core.security import code_digest, digest_token, keyed_digest
from app.db.models import AuthSession, EmailLoginChallenge, LoginAttempt, User, UserRole

PASSWORD_HASHER = PasswordHasher()
DUMMY_PASSWORD_HASH = PASSWORD_HASHER.hash("dummy-password-used-only-for-timing")
OTP_TTL_SECONDS = 5 * 60
OTP_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_MAX_FAILURES = 10
SESSION_IDLE_SECONDS = 30 * 60
SESSION_ABSOLUTE_SECONDS = 8 * 60 * 60


class LoginRateLimitError(Exception):
    pass


class InvalidChallengeError(Exception):
    pass


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
    if email_failures >= LOGIN_MAX_FAILURES or ip_failures >= LOGIN_MAX_FAILURES:
        raise LoginRateLimitError


def start_login(
    db: Session,
    *,
    email: str,
    password: str,
    client_ip: str,
    mailer: Mailer,
) -> None:
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
        return

    recent_challenges = db.scalar(
        select(func.count())
        .select_from(EmailLoginChallenge)
        .where(
            EmailLoginChallenge.user_id == user.id,
            EmailLoginChallenge.created_at_epoch > now - LOGIN_WINDOW_SECONDS,
        )
    )
    if recent_challenges >= 3:
        raise LoginRateLimitError

    code = f"{secrets.randbelow(1_000_000):06d}"
    challenge = EmailLoginChallenge(
        user_id=user.id,
        code_digest=code_digest(code),
        created_at_epoch=now,
        expires_at_epoch=now + OTP_TTL_SECONDS,
    )
    db.add(challenge)
    db.commit()
    try:
        mailer.send(
            recipient=user.email,
            subject="Codigo de acceso a QALabSPBVI",
            body=(
                f"Hola {user.display_name},\n\n"
                f"Tu codigo de acceso es: {code}\n"
                "Vence en 5 minutos y solo puede usarse una vez. "
                "Si no solicitaste este acceso, ignora este correo."
            ),
        )
    except MailDeliveryError as error:
        challenge.used_at_epoch = now
        db.commit()
        raise MailDeliveryError from error


def complete_login(
    db: Session,
    *,
    email: str,
    code: str,
) -> tuple[User, str, int]:
    now = int(time.time())
    user = db.scalar(select(User).where(User.email == normalize_email(email)))
    if user is None or not user.is_active:
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
