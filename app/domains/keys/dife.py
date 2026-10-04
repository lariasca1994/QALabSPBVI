from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import KeyStatus
from app.domains.keys.persistence import DifeKey


def resolve_key(
    dife_db: Session, *, spbvi_id: str, key_type: str, key_value: str
) -> DifeKey | None:
    statement = select(DifeKey).where(
        DifeKey.spbvi_id == spbvi_id,
        DifeKey.key_type == key_type.strip().lower(),
        DifeKey.key_value == key_value.strip(),
        DifeKey.status == KeyStatus.ACTIVE,
    )
    return dife_db.scalar(statement)
