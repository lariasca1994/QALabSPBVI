from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import KeyStatus
from app.domains.keys.persistence import (
    DiceKey,
    DifeKey,
    DifeKeyStatusEvent,
    KeyLifecycleEvent,
)


class DuplicateKeyError(Exception):
    """La llave ya esta registrada en el indice global del DICE."""


class KeyNotFoundError(Exception):
    pass


class KeyOwnershipError(Exception):
    pass


class KeyStateConflictError(Exception):
    pass


class KeyStoreConsistencyError(Exception):
    pass


def register_key(
    dife_db: Session,
    dice_db: Session,
    *,
    key_type: str,
    key_value: str,
    spbvi_id: str,
    deposit_product_id: str,
    owner_email: str | None = None,
    actor_email: str = "system@qalabspbvi.local",
    actor_role: str = "system",
) -> DifeKey:
    normalized_type = key_type.strip().lower()
    normalized_value = key_value.strip()
    normalized_spbvi = spbvi_id.strip()
    normalized_product = deposit_product_id.strip()
    normalized_owner = owner_email.strip().lower() if owner_email else None
    reservation = dice_db.scalar(
        select(DiceKey).where(
            DiceKey.key_type == normalized_type,
            DiceKey.key_value == normalized_value,
        )
    )

    if reservation is not None:
        if reservation.status is not KeyStatus.PENDING:
            raise DuplicateKeyError
        if reservation.spbvi_id != normalized_spbvi:
            raise DuplicateKeyError
        if reservation.deposit_product_id not in (None, normalized_product):
            raise DuplicateKeyError
        if reservation.owner_email not in (None, normalized_owner):
            raise DuplicateKeyError
        reservation.deposit_product_id = normalized_product
        reservation.owner_email = normalized_owner
    else:
        reservation = DiceKey(
            registration_id=str(uuid4()),
            key_type=normalized_type,
            key_value=normalized_value,
            spbvi_id=normalized_spbvi,
            deposit_product_id=normalized_product,
            owner_email=normalized_owner,
            status=KeyStatus.PENDING,
        )
        dice_db.add(reservation)
        try:
            dice_db.commit()
        except IntegrityError as error:
            dice_db.rollback()
            reservation = dice_db.scalar(
                select(DiceKey).where(
                    DiceKey.key_type == normalized_type,
                    DiceKey.key_value == normalized_value,
                )
            )
            if (
                reservation is None
                or reservation.status is not KeyStatus.PENDING
                or reservation.spbvi_id != normalized_spbvi
                or reservation.deposit_product_id not in (None, normalized_product)
                or reservation.owner_email not in (None, normalized_owner)
            ):
                raise DuplicateKeyError from error
            reservation.deposit_product_id = normalized_product
            reservation.owner_email = normalized_owner
            dice_db.commit()

    key = dife_db.scalar(
        select(DifeKey).where(
            DifeKey.key_type == normalized_type,
            DifeKey.key_value == normalized_value,
        )
    )
    if key is not None:
        if (
            key.spbvi_id != normalized_spbvi
            or key.deposit_product_id != normalized_product
            or key.owner_email not in (None, normalized_owner)
            or key.status not in {KeyStatus.PENDING, KeyStatus.ACTIVE}
        ):
            raise DuplicateKeyError
        key.owner_email = normalized_owner
    else:
        key = DifeKey(
            key_type=normalized_type,
            key_value=normalized_value,
            spbvi_id=normalized_spbvi,
            deposit_product_id=normalized_product,
            owner_email=normalized_owner,
            status=KeyStatus.PENDING,
        )
        dife_db.add(key)
        try:
            dife_db.flush()
        except IntegrityError as error:
            dife_db.rollback()
            key = dife_db.scalar(
                select(DifeKey).where(
                    DifeKey.key_type == normalized_type,
                    DifeKey.key_value == normalized_value,
                )
            )
            if (
                key is None
                or key.spbvi_id != normalized_spbvi
                or key.deposit_product_id != normalized_product
                or key.owner_email not in (None, normalized_owner)
            ):
                raise DuplicateKeyError from error

    try:
        if key.status is KeyStatus.PENDING:
            dife_db.add(
                DifeKeyStatusEvent(
                    key_id=key.id,
                    status=KeyStatus.PENDING,
                    actor="DICE",
                )
            )
            key.status = KeyStatus.ACTIVE
            dife_db.add(
                DifeKeyStatusEvent(
                    key_id=key.id,
                    status=KeyStatus.ACTIVE,
                    actor="DIFE",
                )
            )
            dife_db.add(
                KeyLifecycleEvent(
                    key_type=normalized_type,
                    key_value=normalized_value,
                    spbvi_id=normalized_spbvi,
                    deposit_product_id=normalized_product,
                    owner_email=normalized_owner,
                    action="created",
                    status=KeyStatus.ACTIVE.value,
                    reason="",
                    actor_email=actor_email,
                    actor_role=actor_role,
                )
            )
        dife_db.commit()
    except Exception:
        dife_db.rollback()
        raise

    try:
        reservation.status = KeyStatus.CONFIRMED
        dice_db.commit()
    except Exception:
        dice_db.rollback()
        raise

    dife_db.refresh(key)
    return key


def _get_key_pair(
    dife_db: Session,
    dice_db: Session,
    *,
    spbvi_id: str,
    key_type: str,
    key_value: str,
) -> tuple[DifeKey, DiceKey]:
    normalized_type = key_type.strip().lower()
    normalized_value = key_value.strip()
    dife_key = dife_db.scalar(
        select(DifeKey).where(
            DifeKey.spbvi_id == spbvi_id.strip(),
            DifeKey.key_type == normalized_type,
            DifeKey.key_value == normalized_value,
        )
    )
    dice_key = dice_db.scalar(
        select(DiceKey).where(
            DiceKey.key_type == normalized_type,
            DiceKey.key_value == normalized_value,
        )
    )
    if dife_key is None or dice_key is None:
        raise KeyNotFoundError
    if (
        dice_key.spbvi_id != dife_key.spbvi_id
        or dice_key.deposit_product_id != dife_key.deposit_product_id
    ):
        raise KeyStoreConsistencyError
    return dife_key, dice_key


def _record_lifecycle_event(
    dife_db: Session,
    *,
    key: DifeKey,
    action: str,
    status_value: str,
    reason: str,
    actor_email: str,
    actor_role: str,
) -> None:
    dife_db.add(
        KeyLifecycleEvent(
            key_type=key.key_type,
            key_value=key.key_value,
            spbvi_id=key.spbvi_id,
            deposit_product_id=key.deposit_product_id,
            owner_email=key.owner_email,
            action=action,
            status=status_value,
            reason=reason,
            actor_email=actor_email,
            actor_role=actor_role,
        )
    )


def _ensure_personal_owner(key: DifeKey, actor_email: str) -> None:
    if (
        key.owner_email is None
        or key.owner_email.casefold() != actor_email.strip().casefold()
    ):
        raise KeyOwnershipError


def _set_dice_pending(dice_db: Session, key: DiceKey) -> None:
    if key.status is not KeyStatus.PENDING:
        key.status = KeyStatus.PENDING
        dice_db.commit()


def suspend_key(
    dife_db: Session,
    dice_db: Session,
    *,
    spbvi_id: str,
    key_type: str,
    key_value: str,
    suspension_type: str,
    reason: str,
    actor_email: str,
    actor_role: str,
) -> DifeKey:
    target_status = {
        "administrative": KeyStatus.SUSPENDED_ADMINISTRATIVE,
        "personal": KeyStatus.SUSPENDED_PERSONAL,
    }[suspension_type]
    action = f"suspended_{suspension_type}"
    try:
        dife_key, dice_key = _get_key_pair(
            dife_db,
            dice_db,
            spbvi_id=spbvi_id,
            key_type=key_type,
            key_value=key_value,
        )
        if suspension_type == "personal":
            _ensure_personal_owner(dife_key, actor_email)

        if (
            dife_key.status is target_status
            and dice_key.status is target_status
        ):
            return dife_key
        allowed_dife = {KeyStatus.ACTIVE, target_status}
        if suspension_type == "administrative":
            allowed_dife.add(KeyStatus.SUSPENDED_PERSONAL)
        allowed_dice = {KeyStatus.CONFIRMED, KeyStatus.PENDING, target_status}
        if suspension_type == "administrative":
            allowed_dice.add(KeyStatus.SUSPENDED_PERSONAL)
        if dife_key.status not in allowed_dife or dice_key.status not in allowed_dice:
            raise KeyStateConflictError

        _set_dice_pending(dice_db, dice_key)
        if dife_key.status is not target_status:
            dife_key.status = target_status
            dife_db.add(
                DifeKeyStatusEvent(
                    key_id=dife_key.id,
                    status=target_status,
                    actor=suspension_type.upper(),
                )
            )
            _record_lifecycle_event(
                dife_db,
                key=dife_key,
                action=action,
                status_value=target_status.value,
                reason=reason,
                actor_email=actor_email,
                actor_role=actor_role,
            )
            dife_db.commit()
        dice_key.status = target_status
        dice_db.commit()
        dife_db.refresh(dife_key)
        return dife_key
    except Exception:
        dife_db.rollback()
        dice_db.rollback()
        raise


def reactivate_key(
    dife_db: Session,
    dice_db: Session,
    *,
    spbvi_id: str,
    key_type: str,
    key_value: str,
    reactivation_type: str,
    reason: str,
    actor_email: str,
    actor_role: str,
) -> DifeKey:
    try:
        dife_key, dice_key = _get_key_pair(
            dife_db,
            dice_db,
            spbvi_id=spbvi_id,
            key_type=key_type,
            key_value=key_value,
        )
        if reactivation_type == "personal":
            _ensure_personal_owner(dife_key, actor_email)

        if dife_key.status is KeyStatus.ACTIVE and dice_key.status is KeyStatus.CONFIRMED:
            return dife_key
        allowed_dife = {
            KeyStatus.SUSPENDED_ADMINISTRATIVE,
            KeyStatus.SUSPENDED_PERSONAL,
            KeyStatus.ACTIVE,
        }
        allowed_dice = {
            KeyStatus.SUSPENDED_ADMINISTRATIVE,
            KeyStatus.SUSPENDED_PERSONAL,
            KeyStatus.PENDING,
            KeyStatus.CONFIRMED,
        }
        if reactivation_type == "personal":
            allowed_dife = {KeyStatus.SUSPENDED_PERSONAL, KeyStatus.ACTIVE}
            allowed_dice = {KeyStatus.SUSPENDED_PERSONAL, KeyStatus.PENDING, KeyStatus.CONFIRMED}
        if dife_key.status not in allowed_dife or dice_key.status not in allowed_dice:
            raise KeyStateConflictError
        if (
            reactivation_type == "personal"
            and dife_key.status is KeyStatus.SUSPENDED_ADMINISTRATIVE
        ):
            raise KeyStateConflictError

        _set_dice_pending(dice_db, dice_key)
        if dife_key.status is not KeyStatus.ACTIVE:
            dife_key.status = KeyStatus.ACTIVE
            dife_db.add(
                DifeKeyStatusEvent(
                    key_id=dife_key.id,
                    status=KeyStatus.ACTIVE,
                    actor=reactivation_type.upper(),
                )
            )
            _record_lifecycle_event(
                dife_db,
                key=dife_key,
                action=f"reactivated_{reactivation_type}",
                status_value=KeyStatus.ACTIVE.value,
                reason=reason,
                actor_email=actor_email,
                actor_role=actor_role,
            )
            dife_db.commit()
        dice_key.status = KeyStatus.CONFIRMED
        dice_db.commit()
        dife_db.refresh(dife_key)
        return dife_key
    except Exception:
        dife_db.rollback()
        dice_db.rollback()
        raise


def assign_key_owner(
    dife_db: Session,
    dice_db: Session,
    *,
    spbvi_id: str,
    key_type: str,
    key_value: str,
    owner_email: str,
    reason: str,
    actor_email: str,
    actor_role: str,
) -> DifeKey:
    normalized_owner = owner_email.strip().lower()
    try:
        dife_key, dice_key = _get_key_pair(
            dife_db,
            dice_db,
            spbvi_id=spbvi_id,
            key_type=key_type,
            key_value=key_value,
        )
        if dife_key.owner_email == normalized_owner and dice_key.owner_email == normalized_owner:
            return dife_key

        _set_dice_pending(dice_db, dice_key)
        if dife_key.owner_email != normalized_owner:
            dife_key.owner_email = normalized_owner
            _record_lifecycle_event(
                dife_db,
                key=dife_key,
                action="owner_assigned",
                status_value=dife_key.status.value,
                reason=reason,
                actor_email=actor_email,
                actor_role=actor_role,
            )
            dife_db.commit()
        dice_key.owner_email = normalized_owner
        dice_key.status = (
            KeyStatus.CONFIRMED
            if dife_key.status is KeyStatus.ACTIVE
            else dife_key.status
        )
        dice_db.commit()
        dife_db.refresh(dife_key)
        return dife_key
    except Exception:
        dife_db.rollback()
        dice_db.rollback()
        raise


def delete_key(
    dife_db: Session,
    dice_db: Session,
    *,
    spbvi_id: str,
    key_type: str,
    key_value: str,
    reason: str,
    actor_email: str,
    actor_role: str,
) -> None:
    normalized_type = key_type.strip().lower()
    normalized_value = key_value.strip()
    normalized_spbvi = spbvi_id.strip()
    try:
        dife_key = dife_db.scalar(
            select(DifeKey).where(
                DifeKey.spbvi_id == normalized_spbvi,
                DifeKey.key_type == normalized_type,
                DifeKey.key_value == normalized_value,
            )
        )
        dice_key = dice_db.scalar(
            select(DiceKey).where(
                DiceKey.key_type == normalized_type,
                DiceKey.key_value == normalized_value,
            )
        )
        if dife_key is None and dice_key is None:
            already_deleted = dife_db.scalar(
                select(KeyLifecycleEvent).where(
                    KeyLifecycleEvent.spbvi_id == normalized_spbvi,
                    KeyLifecycleEvent.key_type == normalized_type,
                    KeyLifecycleEvent.key_value == normalized_value,
                    KeyLifecycleEvent.action == "deleted",
                )
            )
            if already_deleted is not None:
                return
            raise KeyNotFoundError
        if dice_key is not None and dice_key.spbvi_id != normalized_spbvi:
            raise KeyNotFoundError
        if (
            dife_key is not None
            and dice_key is not None
            and (
                dice_key.deposit_product_id != dife_key.deposit_product_id
                or dice_key.spbvi_id != dife_key.spbvi_id
            )
        ):
            raise KeyStoreConsistencyError

        if dice_key is not None:
            _set_dice_pending(dice_db, dice_key)

        if dife_key is not None:
            _record_lifecycle_event(
                dife_db,
                key=dife_key,
                action="deleted",
                status_value="deleted",
                reason=reason,
                actor_email=actor_email,
                actor_role=actor_role,
            )
            dife_db.execute(
                delete(DifeKeyStatusEvent).where(
                    DifeKeyStatusEvent.key_id == dife_key.id
                )
            )
            dife_db.delete(dife_key)
            dife_db.commit()
        if dice_key is not None:
            dice_db.delete(dice_key)
            dice_db.commit()
    except Exception:
        dife_db.rollback()
        dice_db.rollback()
        raise
