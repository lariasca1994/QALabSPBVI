"""Login MFA automatizable para pruebas E2E (qa-evidencia), limitado a una lista cerrada.

El MFA por correo no se puede automatizar sin leer el buzón. Para las cuentas de
`QA_AUTOMATION_EMAILS` (cuentas de prueba con rol `usuario`), el código MFA no se envía
por correo: se registra en `qa_automation_codes` y se entrega por
`POST /auth/qa-automation/code` solo a quien presente:

- el token `QA_AUTOMATION_TOKEN` (cabecera `X-QA-Automation-Token`, mínimo 32 caracteres), y
- la cookie del inicio de sesión pendiente, que solo existe tras una contraseña válida.

Sin token configurado la función está apagada y el endpoint responde 404, igual que ante
cualquier dato inválido, para no revelar nada. El resto de cuentas sigue con MFA por correo.

Al ingresar una cuenta de automatización se aseguran, si faltan, los datos fijos que usan
las pruebas (cuentas `qa-evidencia-origen` y `qa-evidencia-destino` y la llave `@qaevidencia`
en spbvi-a), así cada entorno queda listo sin cargarlos a mano.
"""

import hmac
import logging
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import digest_token
from app.db.models import Account, LedgerEntry, PendingLogin, QaAutomationCode, User

logger = logging.getLogger(__name__)

QA_SPBVI = "spbvi-a"
QA_SOURCE_ACCOUNT = "qa-evidencia-origen"
QA_DESTINATION_ACCOUNT = "qa-evidencia-destino"
QA_ALIAS = "@qaevidencia"
QA_OPENING_BALANCE_CENTS = 99_971_100


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


def ensure_fixtures(db: Session, *, owner_email: str) -> None:
    """Crea los datos fijos de las pruebas E2E que falten. Idempotente."""
    # Import local: el dominio de llaves carga sus bases (DIFE/DICE) solo cuando se usa.
    from app.domains.keys.persistence import (
        DifeKey,
        get_dice_session_factory,
        get_dife_session_factory,
    )
    from app.domains.keys.service import register_key

    created = False
    for account_id, balance in (
        (QA_SOURCE_ACCOUNT, QA_OPENING_BALANCE_CENTS),
        (QA_DESTINATION_ACCOUNT, 0),
    ):
        if db.get(Account, account_id) is None:
            db.add(Account(id=account_id, spbvi_id=QA_SPBVI, balance_cents=balance))
            if balance:
                db.add(LedgerEntry(account_id=account_id, amount_cents=balance, entry_type="opening"))
            created = True
    if created:
        db.commit()

    with get_dife_session_factory()() as dife_db, get_dice_session_factory()() as dice_db:
        key = dife_db.scalar(
            select(DifeKey).where(DifeKey.key_type == "alias", DifeKey.key_value == QA_ALIAS)
        )
        if key is None:
            register_key(
                dife_db,
                dice_db,
                key_type="alias",
                key_value=QA_ALIAS,
                spbvi_id=QA_SPBVI,
                deposit_product_id=QA_DESTINATION_ACCOUNT,
                owner_email=owner_email,
            )


def ensure_fixtures_safely(db: Session, *, owner_email: str) -> None:
    """Como ensure_fixtures, pero un fallo nunca impide el ingreso."""
    try:
        ensure_fixtures(db, owner_email=owner_email)
    except Exception:  # noqa: BLE001 - los datos de prueba no deben bloquear el login
        db.rollback()
        logger.warning("No se pudieron asegurar los datos de prueba de qa-evidencia.", exc_info=True)


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
