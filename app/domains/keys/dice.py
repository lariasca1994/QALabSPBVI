from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import KeyStatus
from app.domains.keys.persistence import DiceKey


class DiceKeyNotFoundError(Exception):
    pass


def resolve_inter_spbvi_key(
    dice_db: Session, *, key_type: str, key_value: str
) -> DiceKey | None:
    statement = select(DiceKey).where(
        DiceKey.key_type == key_type.strip().lower(),
        DiceKey.key_value == key_value.strip(),
        DiceKey.status == KeyStatus.CONFIRMED,
        DiceKey.deposit_product_id.is_not(None),
    )
    return dice_db.scalar(statement)
