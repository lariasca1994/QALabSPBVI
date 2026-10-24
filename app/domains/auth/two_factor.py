"""Verificación en dos pasos: app autenticadora (TOTP) además del código por correo.

Cada cuenta elige su método. Las cuentas sin elección guardada reciben el código por correo
(como siempre) y en esa misma pantalla pueden configurar la app autenticadora. Al confirmar un
método queda registrado en `user_mfa`; desde Seguridad se agrega el otro y se elige cuál se pide
por defecto.

Reglas: la clave de la app se guarda cifrada (AES-GCM con una llave derivada de
AUTH_SECRET_KEY); un código de la app no se acepta dos veces; 5 códigos errados de la app la
bloquean 15 minutos.
"""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import User, UserMfa

METHOD_EMAIL = "email"
METHOD_TOTP = "totp"
ISSUER = "QALabSPBVI"
TOTP_PERIOD = 30
TOTP_MAX_FAILURES = 5
TOTP_LOCK_SECONDS = 15 * 60


class TotpLockedError(Exception):
    """Demasiados códigos errados de la app: bloqueo temporal."""


class TotpNotConfiguredError(Exception):
    pass


def _key() -> bytes:
    secret = get_settings().auth_secret_key
    if len(secret) < 32:
        raise RuntimeError("AUTH_SECRET_KEY no está configurada.")
    return hashlib.sha256(f"{secret}|qalab-totp".encode()).digest()


def encrypt_secret(value: str) -> str:
    nonce = secrets.token_bytes(12)
    data = AESGCM(_key()).encrypt(nonce, value.encode(), b"qalab-totp")
    return base64.urlsafe_b64encode(nonce + data).decode()


def decrypt_secret(value: str) -> str:
    raw = base64.urlsafe_b64decode(value.encode())
    return AESGCM(_key()).decrypt(raw[:12], raw[12:], b"qalab-totp").decode()


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def provisioning_uri(secret: str, email: str) -> str:
    label = quote(f"{ISSUER}:{email}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={ISSUER}&digits=6&period={TOTP_PERIOD}"


def totp_code(secret: str, step: int) -> str:
    padded = secret + "=" * (-len(secret) % 8)
    digest = hmac.new(base64.b32decode(padded), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{value % 1_000_000:06d}"


def matching_step(secret: str, code: str, last_step: int, now: float | None = None) -> int | None:
    """Paso del código (±1 paso de tolerancia), o None si no coincide o ya se usó."""
    code = "".join(ch for ch in code if ch.isdigit())
    if len(code) != 6:
        return None
    current = int((now if now is not None else time.time()) // TOTP_PERIOD)
    for step in (current - 1, current, current + 1):
        if step > last_step and hmac.compare_digest(totp_code(secret, step), code):
            return step
    return None


def mfa_row(db: Session, user: User) -> UserMfa | None:
    return db.get(UserMfa, user.id)


def _ensure_row(db: Session, user: User) -> UserMfa:
    row = mfa_row(db, user)
    if row is None:
        row = UserMfa(user_id=user.id)
        db.add(row)
    return row


def methods(db: Session, user: User) -> dict[str, object]:
    """Métodos activos y el preferido. Sin elección guardada, el correo es el método vigente."""
    row = mfa_row(db, user)
    if row is None:
        return {"chosen": False, "totp": False, "email": True, "preferred": METHOD_EMAIL}
    totp = bool(row.totp_active)
    email = bool(row.email_active)
    preferred = row.preferred
    if (preferred == METHOD_TOTP and not totp) or (preferred == METHOD_EMAIL and not email):
        preferred = METHOD_TOTP if totp else METHOD_EMAIL
    return {"chosen": True, "totp": totp, "email": email, "preferred": preferred}


def login_method(db: Session, user: User) -> str:
    """Método que se pide al ingresar: la app solo si está activa y es la preferida."""
    state = methods(db, user)
    return METHOD_TOTP if state["totp"] and state["preferred"] == METHOD_TOTP else METHOD_EMAIL


def start_totp_setup(db: Session, user: User) -> tuple[str, str]:
    """Genera una clave pendiente (no reemplaza una app activa) y devuelve (clave, otpauth://)."""
    row = _ensure_row(db, user)
    if row.totp_active:
        raise TotpNotConfiguredError("La app autenticadora ya está activa.")
    secret = new_secret()
    row.totp_pending = encrypt_secret(secret)
    db.commit()
    return secret, provisioning_uri(secret, user.email)


def _check_lock(row: UserMfa, now: int) -> None:
    if row.locked_until_epoch and row.locked_until_epoch > now:
        raise TotpLockedError


def _register_failure(db: Session, row: UserMfa, now: int) -> None:
    row.failures = (row.failures or 0) + 1
    if row.failures >= TOTP_MAX_FAILURES:
        row.failures = 0
        row.locked_until_epoch = now + TOTP_LOCK_SECONDS
        db.commit()
        raise TotpLockedError
    db.commit()


def verify_totp(db: Session, user: User, code: str) -> bool:
    """Código de la app activa, o de la clave pendiente (la activa al primer acierto)."""
    now = int(time.time())
    row = mfa_row(db, user)
    if row is None or (not row.totp_active and not row.totp_pending):
        raise TotpNotConfiguredError
    _check_lock(row, now)
    encrypted = row.totp_secret if row.totp_active else row.totp_pending
    step = matching_step(decrypt_secret(encrypted), code, row.totp_last_step or 0, now)
    if step is None:
        _register_failure(db, row, now)
        return False
    row.totp_last_step = step
    row.failures = 0
    row.locked_until_epoch = 0
    if not row.totp_active:
        first_method = not row.email_active
        row.totp_secret = row.totp_pending
        row.totp_pending = None
        row.totp_active = True
        if first_method or row.preferred is None:
            row.preferred = METHOD_TOTP
    db.commit()
    return True


def confirm_email_method(db: Session, user: User) -> None:
    """El correo quedó confirmado (se usó un código del correo): queda como método activo."""
    row = _ensure_row(db, user)
    if not row.email_active:
        row.email_active = True
        if row.preferred is None:
            row.preferred = METHOD_EMAIL
    db.commit()


def set_preferred(db: Session, user: User, method: str) -> bool:
    state = methods(db, user)
    if (method == METHOD_TOTP and not state["totp"]) or (method == METHOD_EMAIL and not state["email"]):
        return False
    row = _ensure_row(db, user)
    if not state["chosen"]:
        row.email_active = True
    row.preferred = method
    db.commit()
    return True
