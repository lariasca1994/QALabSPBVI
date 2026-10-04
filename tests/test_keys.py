from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.security import current_user
from app.db.models import KeyStatus, User, UserRole
from app.domains.keys.persistence import (
    DiceBase,
    DiceKey,
    DifeBase,
    DifeKey,
    DifeKeyStatusEvent,
    KeyLifecycleEvent,
    ensure_dice_product_column,
    ensure_key_lifecycle_columns,
    get_dice_db,
    get_dife_db,
)
from app.domains.keys.service import register_key
from app.domains.keys.service import delete_key, suspend_key
from app.domains.keys.dice import resolve_inter_spbvi_key
from app.main import app

KEY_LIFECYCLE_FIXTURES = Path(__file__).parent / "fixtures" / "key_lifecycle"


def key_payload(name: str) -> dict[str, str]:
    return json.loads((KEY_LIFECYCLE_FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def authenticate_as(*, email: str, role: UserRole) -> None:
    app.dependency_overrides[current_user] = lambda: User(
        id=1,
        email=email,
        display_name="Test User",
        password_hash="test-hash",
        role=role,
        is_active=True,
    )


@pytest.fixture
def key_stores() -> Iterator[tuple[Engine, Engine]]:
    dife_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    dice_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    DifeBase.metadata.create_all(dife_engine)
    DiceBase.metadata.create_all(dice_engine)

    def override_dife_db():
        with Session(dife_engine) as session:
            yield session

    def override_dice_db():
        with Session(dice_engine) as session:
            yield session

    app.dependency_overrides[get_dife_db] = override_dife_db
    app.dependency_overrides[get_dice_db] = override_dice_db
    app.dependency_overrides[current_user] = lambda: User(
        id=1,
        email="admin@example.com",
        display_name="Admin",
        password_hash="test-hash",
        role=UserRole.ADMIN,
        is_active=True,
    )
    try:
        yield dife_engine, dice_engine
    finally:
        app.dependency_overrides.clear()
        DifeBase.metadata.drop_all(dife_engine)
        DiceBase.metadata.drop_all(dice_engine)
        dife_engine.dispose()
        dice_engine.dispose()


@pytest.fixture
def key_client(key_stores: tuple[Engine, Engine]) -> Iterator[TestClient]:
    _ = key_stores
    with TestClient(app) as client:
        client.cookies.set("qalab_csrf", "test-csrf")
        client.headers["x-csrf-token"] = "test-csrf"
        yield client


def register(client: TestClient, *, spbvi_id: str = "spbvi-a") -> object:
    return client.post(
        f"/difes/{spbvi_id}/keys",
        json=key_payload("create"),
    )


def test_register_key_reserves_in_dice_and_activates_in_dife(
    key_client: TestClient,
    key_stores: tuple[Engine, Engine],
) -> None:
    dife_engine, dice_engine = key_stores

    response = register(key_client)

    assert response.status_code == 201
    assert response.json() == {
        "id": 1,
        "key_type": "email",
        "key_value": "ana@example.test",
        "spbvi_id": "spbvi-a",
        "deposit_product_id": "account-123",
        "status": "confirmed",
    }
    with Session(dife_engine) as session:
        events = session.scalars(
            select(DifeKeyStatusEvent).order_by(DifeKeyStatusEvent.id)
        ).all()
        assert [(event.status, event.actor) for event in events] == [
            (KeyStatus.PENDING, "DICE"),
            (KeyStatus.ACTIVE, "DIFE"),
        ]
        assert session.scalar(select(func.count()).select_from(DifeKey)) == 1
    with Session(dice_engine) as session:
        reservation = session.scalar(select(DiceKey))
        assert reservation is not None
        assert reservation.status is KeyStatus.CONFIRMED
        assert reservation.owner_email == "admin@example.com"


def test_administrative_suspension_blocks_both_dife_and_dice_resolution(
    key_client: TestClient,
    key_stores: tuple[Engine, Engine],
) -> None:
    dife_engine, dice_engine = key_stores
    assert register(key_client).status_code == 201

    response = key_client.post(
        "/difes/spbvi-a/keys/suspend",
        json=key_payload("suspend_administrative"),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "suspended_administrative"
    with Session(dife_engine) as session:
        key = session.scalar(select(DifeKey))
        assert key is not None
        assert key.status is KeyStatus.SUSPENDED_ADMINISTRATIVE
    with Session(dice_engine) as session:
        key = session.scalar(select(DiceKey))
        assert key is not None
        assert key.status is KeyStatus.SUSPENDED_ADMINISTRATIVE
    assert key_client.get(
        "/difes/spbvi-a/keys/resolve",
        params={"key_type": "email", "key_value": "ana@example.test"},
    ).status_code == 404
    with Session(dice_engine) as dice_db:
        assert (
            resolve_inter_spbvi_key(
                dice_db,
                key_type="email",
                key_value="ana@example.test",
            )
            is None
        )


def test_administrative_reactivation_after_administrative_suspension(
    key_client: TestClient,
    key_stores: tuple[Engine, Engine],
) -> None:
    dife_engine, dice_engine = key_stores
    assert register(key_client).status_code == 201
    assert key_client.post(
        "/difes/spbvi-a/keys/suspend",
        json=key_payload("suspend_administrative"),
    ).status_code == 200

    response = key_client.post(
        "/difes/spbvi-a/keys/reactivate",
        json=key_payload("reactivate_administrative"),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "confirmed"
    assert key_client.get(
        "/difes/spbvi-a/keys/resolve",
        params={"key_type": "email", "key_value": "ana@example.test"},
    ).status_code == 200
    with Session(dife_engine) as session:
        assert session.scalar(select(DifeKey)).status is KeyStatus.ACTIVE
    with Session(dice_engine) as session:
        assert session.scalar(select(DiceKey)).status is KeyStatus.CONFIRMED


def test_create_does_not_reactivate_an_existing_suspended_key(
    key_client: TestClient,
    key_stores: tuple[Engine, Engine],
) -> None:
    assert register(key_client).status_code == 201
    assert key_client.post(
        "/difes/spbvi-a/keys/suspend",
        json=key_payload("suspend_administrative"),
    ).status_code == 200

    duplicate = register(key_client)

    assert duplicate.status_code == 409
    with Session(key_stores[0]) as session:
        assert session.scalar(select(DifeKey)).status is KeyStatus.SUSPENDED_ADMINISTRATIVE
    with Session(key_stores[1]) as session:
        assert session.scalar(select(DiceKey)).status is KeyStatus.SUSPENDED_ADMINISTRATIVE


def test_personal_suspension_and_reactivation_require_key_owner(
    key_client: TestClient,
    key_stores: tuple[Engine, Engine],
) -> None:
    dife_engine, dice_engine = key_stores
    assert register(key_client).status_code == 201
    authenticate_as(email="admin@example.com", role=UserRole.USUARIO)

    suspended = key_client.post(
        "/difes/spbvi-a/keys/suspend",
        json=key_payload("suspend_personal"),
    )
    assert suspended.status_code == 200
    assert suspended.json()["status"] == "suspended_personal"
    reactivated = key_client.post(
        "/difes/spbvi-a/keys/reactivate",
        json=key_payload("reactivate_personal"),
    )
    assert reactivated.status_code == 200
    assert reactivated.json()["status"] == "confirmed"
    with Session(dife_engine) as session:
        events = session.scalars(select(KeyLifecycleEvent)).all()
        assert [event.action for event in events] == [
            "created",
            "suspended_personal",
            "reactivated_personal",
        ]
        assert all(event.actor_email == "admin@example.com" for event in events)
    with Session(dice_engine) as session:
        assert session.scalar(select(DiceKey)).status is KeyStatus.CONFIRMED


def test_personal_key_lifecycle_rejects_non_owner_and_non_owner_cannot_reactivate_admin_suspension(
    key_client: TestClient,
) -> None:
    assert register(key_client).status_code == 201
    authenticate_as(email="another@example.test", role=UserRole.USUARIO)

    forbidden = key_client.post(
        "/difes/spbvi-a/keys/suspend",
        json=key_payload("suspend_personal"),
    )
    assert forbidden.status_code == 403

    authenticate_as(email="admin@example.com", role=UserRole.ADMIN)
    assert key_client.post(
        "/difes/spbvi-a/keys/suspend",
        json=key_payload("suspend_administrative"),
    ).status_code == 200
    authenticate_as(email="admin@example.com", role=UserRole.USUARIO)
    cannot_reactivate = key_client.post(
        "/difes/spbvi-a/keys/reactivate",
        json=key_payload("reactivate_personal"),
    )
    assert cannot_reactivate.status_code == 409


def test_admin_can_assign_owner_to_legacy_key_for_personal_actions(
    key_client: TestClient,
    key_stores: tuple[Engine, Engine],
) -> None:
    create_payload = key_payload("create")
    create_payload.pop("owner_email")
    assert key_client.post("/difes/spbvi-a/keys", json=create_payload).status_code == 201

    assigned = key_client.patch(
        "/difes/spbvi-a/keys/owner",
        json=key_payload("assign_owner"),
    )

    assert assigned.status_code == 200
    authenticate_as(email="titular@example.com", role=UserRole.USUARIO)
    personal_suspend = key_client.post(
        "/difes/spbvi-a/keys/suspend",
        json=key_payload("suspend_personal"),
    )
    assert personal_suspend.status_code == 200

    with Session(key_stores[0]) as session:
        key = session.scalar(select(DifeKey))
        assert key is not None
        assert key.owner_email == "titular@example.com"
    with Session(key_stores[1]) as session:
        key = session.scalar(select(DiceKey))
        assert key is not None
        assert key.owner_email == "titular@example.com"


def test_existing_key_store_schema_gets_lifecycle_columns_idempotently() -> None:
    dife_engine = create_engine("sqlite://")
    dice_engine = create_engine("sqlite://")
    try:
        with dife_engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE dife_keys (id INTEGER PRIMARY KEY, "
                    "status VARCHAR(16) NOT NULL)"
                )
            )
            connection.execute(
                text(
                    "CREATE TABLE dife_key_status_events (id INTEGER PRIMARY KEY, "
                    "status VARCHAR(16) NOT NULL)"
                )
            )
        with dice_engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE dice_keys (registration_id VARCHAR(36) PRIMARY KEY, "
                    "status VARCHAR(16) NOT NULL)"
                )
            )

        ensure_dice_product_column(dice_engine)
        ensure_key_lifecycle_columns(dife_engine, dice_engine)
        ensure_dice_product_column(dice_engine)
        ensure_key_lifecycle_columns(dife_engine, dice_engine)

        assert "owner_email" in {
            column["name"]
            for column in inspect(dife_engine).get_columns("dife_keys")
        }
        assert "owner_email" in {
            column["name"]
            for column in inspect(dice_engine).get_columns("dice_keys")
        }
        assert "deposit_product_id" in {
            column["name"]
            for column in inspect(dice_engine).get_columns("dice_keys")
        }
    finally:
        dife_engine.dispose()
        dice_engine.dispose()


def test_delete_releases_key_in_both_stores_and_records_audit(
    key_client: TestClient,
    key_stores: tuple[Engine, Engine],
) -> None:
    dife_engine, dice_engine = key_stores
    assert register(key_client).status_code == 201

    response = key_client.request(
        "DELETE",
        "/difes/spbvi-a/keys",
        json=key_payload("delete"),
    )

    assert response.status_code == 200
    assert response.json()["deleted"] is True
    with Session(dife_engine) as session:
        assert session.scalar(select(func.count()).select_from(DifeKey)) == 0
        deletion = session.scalar(
            select(KeyLifecycleEvent).where(KeyLifecycleEvent.action == "deleted")
        )
        assert deletion is not None
        assert deletion.reason == key_payload("delete")["reason"]
    with Session(dice_engine) as session:
        assert session.scalar(select(func.count()).select_from(DiceKey)) == 0
    assert register(key_client).status_code == 201


def test_suspension_retry_recovers_after_dife_commit_and_dice_failure(
    key_client: TestClient,
    key_stores: tuple[Engine, Engine],
) -> None:
    dife_engine, dice_engine = key_stores
    assert register(key_client).status_code == 201

    with Session(dife_engine) as dife_db, Session(dice_engine) as dice_db:
        original_commit = dice_db.commit
        commit_count = 0

        def fail_final_dice_commit() -> None:
            nonlocal commit_count
            commit_count += 1
            if commit_count == 2:
                raise RuntimeError("Falla simulada al confirmar suspension en DICE")
            original_commit()

        dice_db.commit = fail_final_dice_commit
        with pytest.raises(RuntimeError, match="DICE"):
            suspend_key(
                dife_db,
                dice_db,
                spbvi_id="spbvi-a",
                key_type="email",
                key_value="ana@example.test",
                suspension_type="administrative",
                reason="Prueba de recuperación",
                actor_email="admin@example.com",
                actor_role=UserRole.ADMIN.value,
            )

    with Session(dife_engine) as dife_db, Session(dice_engine) as dice_db:
        assert dife_db.scalar(select(DifeKey)).status is KeyStatus.SUSPENDED_ADMINISTRATIVE
        assert dice_db.scalar(select(DiceKey)).status is KeyStatus.PENDING
        recovered = suspend_key(
            dife_db,
            dice_db,
            spbvi_id="spbvi-a",
            key_type="email",
            key_value="ana@example.test",
            suspension_type="administrative",
            reason="Reintento tras error",
            actor_email="admin@example.com",
            actor_role=UserRole.ADMIN.value,
        )
        assert recovered.status is KeyStatus.SUSPENDED_ADMINISTRATIVE
    with Session(dife_engine) as session:
        assert session.scalar(
            select(func.count())
            .select_from(KeyLifecycleEvent)
            .where(KeyLifecycleEvent.action == "suspended_administrative")
        ) == 1
    with Session(dice_engine) as session:
        assert session.scalar(select(DiceKey)).status is KeyStatus.SUSPENDED_ADMINISTRATIVE


def test_delete_retry_finishes_after_dife_was_deleted(
    key_client: TestClient,
    key_stores: tuple[Engine, Engine],
) -> None:
    dife_engine, dice_engine = key_stores
    assert register(key_client).status_code == 201

    with Session(dife_engine) as dife_db, Session(dice_engine) as dice_db:
        original_commit = dice_db.commit
        commit_count = 0

        def fail_final_dice_commit() -> None:
            nonlocal commit_count
            commit_count += 1
            if commit_count == 2:
                raise RuntimeError("Falla simulada al eliminar de DICE")
            original_commit()

        dice_db.commit = fail_final_dice_commit
        with pytest.raises(RuntimeError, match="DICE"):
            delete_key(
                dife_db,
                dice_db,
                spbvi_id="spbvi-a",
                key_type="email",
                key_value="ana@example.test",
                reason="Prueba de recuperación",
                actor_email="admin@example.com",
                actor_role=UserRole.ADMIN.value,
            )

    with Session(dife_engine) as dife_db, Session(dice_engine) as dice_db:
        assert dife_db.scalar(select(DifeKey)) is None
        assert dice_db.scalar(select(DiceKey)).status is KeyStatus.PENDING
        delete_key(
            dife_db,
            dice_db,
            spbvi_id="spbvi-a",
            key_type="email",
            key_value="ana@example.test",
            reason="Reintento tras error",
            actor_email="admin@example.com",
            actor_role=UserRole.ADMIN.value,
        )
    with Session(dice_engine) as session:
        assert session.scalar(select(DiceKey)) is None
    with Session(dife_engine) as session:
        assert session.scalar(
            select(func.count())
            .select_from(KeyLifecycleEvent)
            .where(KeyLifecycleEvent.action == "deleted")
        ) == 1


def test_users_cannot_perform_administrative_key_lifecycle_actions(
    key_client: TestClient,
) -> None:
    assert register(key_client).status_code == 201
    authenticate_as(email="admin@example.com", role=UserRole.USUARIO)

    suspended = key_client.post(
        "/difes/spbvi-a/keys/suspend",
        json=key_payload("suspend_administrative"),
    )
    deleted = key_client.request(
        "DELETE",
        "/difes/spbvi-a/keys",
        json=key_payload("delete"),
    )

    assert suspended.status_code == 403
    assert deleted.status_code == 403


def test_dice_rejects_duplicate_key_globally_across_spbvis(
    key_client: TestClient,
    key_stores: tuple[Engine, Engine],
) -> None:
    first = register(key_client)
    duplicate = register(key_client, spbvi_id="spbvi-b")

    assert first.status_code == 201
    assert duplicate.status_code == 409
    with Session(key_stores[0]) as session:
        assert session.scalar(select(func.count()).select_from(DifeKey)) == 1
    with Session(key_stores[1]) as session:
        assert session.scalar(select(func.count()).select_from(DiceKey)) == 1


def test_dife_resolves_only_keys_registered_in_its_spbvi(
    key_client: TestClient,
) -> None:
    assert register(key_client).status_code == 201
    resolved = key_client.get(
        "/difes/spbvi-a/keys/resolve",
        params={"key_type": "email", "key_value": "ana@example.test"},
    )
    other_spbvi = key_client.get(
        "/difes/spbvi-b/keys/resolve",
        params={"key_type": "email", "key_value": "ana@example.test"},
    )

    assert resolved.status_code == 200
    assert resolved.json()["deposit_product_id"] == "account-123"
    assert other_spbvi.status_code == 404


def test_pending_dice_reservation_is_resumed_after_confirmation_failure(
    key_stores: tuple[Engine, Engine],
) -> None:
    dife_engine, dice_engine = key_stores
    with Session(dife_engine) as dife_db, Session(dice_engine) as dice_db:
        original_commit = dice_db.commit
        commit_count = 0

        def fail_confirmation_commit() -> None:
            nonlocal commit_count
            commit_count += 1
            if commit_count == 2:
                raise RuntimeError("Simulated DICE confirmation failure")
            original_commit()

        dice_db.commit = fail_confirmation_commit
        with pytest.raises(RuntimeError, match="Simulated"):
            register_key(
                dife_db,
                dice_db,
                key_type="alias",
                key_value="ana",
                spbvi_id="spbvi-a",
                deposit_product_id="account-123",
            )

    with Session(dice_engine) as session:
        reservation = session.scalar(select(DiceKey))
        assert reservation is not None
        assert reservation.status is KeyStatus.PENDING
    with Session(dife_engine) as session:
        local_key = session.scalar(select(DifeKey))
        assert local_key is not None
        assert local_key.status is KeyStatus.ACTIVE

    with Session(dife_engine) as dife_db, Session(dice_engine) as dice_db:
        resumed = register_key(
            dife_db,
            dice_db,
            key_type="alias",
            key_value="ana",
            spbvi_id="spbvi-a",
            deposit_product_id="account-123",
        )
        assert resumed.status is KeyStatus.ACTIVE
    with Session(dice_engine) as session:
        confirmed = session.scalar(select(DiceKey))
        assert confirmed is not None
        assert confirmed.status is KeyStatus.CONFIRMED
        assert confirmed.deposit_product_id == "account-123"


def test_dice_unique_constraint_rejects_concurrent_duplicate_reservations(
    tmp_path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'dice.db'}", pool_size=2)
    DiceBase.metadata.create_all(engine)
    barrier = Barrier(2)

    def reserve_concurrently() -> str:
        with Session(engine) as session:
            barrier.wait()
            session.add(
                DiceKey(
                    registration_id=str(uuid4()),
                    key_type="phone",
                    key_value="+573001234567",
                    spbvi_id="spbvi-a",
                    status=KeyStatus.PENDING,
                )
            )
            try:
                session.commit()
                return "created"
            except IntegrityError:
                session.rollback()
                return "duplicate"

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(lambda _: reserve_concurrently(), range(2)))

        assert sorted(outcomes) == ["created", "duplicate"]
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(DiceKey)) == 1
    finally:
        DiceBase.metadata.drop_all(engine)
        engine.dispose()
