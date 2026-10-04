"""Login MFA automatizable para pruebas E2E (qa-evidencia), limitado a una lista cerrada.

El MFA por correo no se puede automatizar sin leer el buzón. Para las cuentas de
`QA_AUTOMATION_EMAILS` (cuentas de prueba con rol `usuario`), el código MFA no se envía
por correo: se registra en `qa_automation_codes` y se entrega por
`POST /auth/qa-automation/code` solo a quien presente:

- el token `QA_AUTOMATION_TOKEN` (cabecera `X-QA-Automation-Token`, mínimo 32 caracteres), y
- la cookie del inicio de sesión pendiente, que solo existe tras una contraseña válida.

Sin token configurado la función está apagada y el endpoint responde 404, igual que ante
cualquier dato inválido, para no revelar nada. El resto de cuentas sigue con MFA por correo.
"""

import hmac
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import digest_token
from app.db.models import PendingLogin, QaAutomationCode, User


def automation_emails() -> frozenset[str]:
    settings = get_settings()
    if len(settings.qa_automation_token) < 32:
        return frozenset()
    return frozenset(
        email.strip().casefold()
        for email in settings.qa_automation_emails.split(",")
        if email.strip()
    )


def is_automation_account(user: User) -> bool:
    return user.email.casefold() in automation_emails()


def record_code(db: Session, *, user: User, code: str, expires_at_epoch: int) -> None:
    existing = db.get(QaAutomationCode, user.id)
    if existing is None:
        db.add(QaAutomationCode(user_id=user.id, code=code, expires_at_epoch=expires_at_epoch))
    else:
        existing.code = code
        existing.expires_at_epoch = expires_at_epoch
    db.commit()


def code_for_pending_login(
    db: Session, *, token: str | None, pending_token: str | None
) -> str | None:
    """Código vigente del login pendiente, o None si algo no corresponde."""
    expected = get_settings().qa_automation_token
    if len(expected) < 32 or not token or not pending_token:
        return None
    if not hmac.compare_digest(token.encode(), expected.encode()):
        return None
    now = int(time.time())
    pending = db.scalar(
        select(PendingLogin).where(PendingLogin.token_digest == digest_token(pending_token))
    )
    if pending is None or pending.expires_at_epoch <= now:
        return None
    user = db.get(User, pending.user_id)
    if user is None or not user.is_active or not is_automation_account(user):
        return None
    record = db.get(QaAutomationCode, user.id)
    if record is None or record.expires_at_epoch <= now:
        return None
    return record.code
