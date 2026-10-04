from collections.abc import Iterator
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from lxml import etree
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.core.security import current_user
from app.db.models import Account, KeyStatus, LedgerEntry, Payment, User, UserRole
from app.db.session import ensure_payment_type_column, get_db
from app.domains.keys import dice
from app.domains.keys.persistence import (
    DiceBase,
    backfill_dice_product_ids,
    DiceKey,
    DifeBase,
    DifeKey,
    ensure_dice_product_column,
    get_dice_db,
    get_dife_db,
)
from app.domains.iso20022.messages import (
    PACS_002_NAMESPACE,
    PACS_008_NAMESPACE,
    validate_message,
)
from app.main import app

PAYMENT_FIXTURES = Path(__file__).parent / "fixtures" / "payments"


@pytest.fixture
def key_stores() -> Iterator[tuple[object, object]]:
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
    try:
        yield dife_engine, dice_engine
    finally:
        DifeBase.metadata.drop_all(dife_engine)
        DiceBase.metadata.drop_all(dice_engine)
        dife_engine.dispose()
        dice_engine.dispose()


@pytest.fixture
def payment_client(
    key_stores: tuple[object, object],
) -> Iterator[tuple[TestClient, Session, object]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    dife_engine, dice_engine = key_stores

    def override_get_db():
        with Session(engine) as session:
            yield session

    def override_dife_db():
        with Session(dife_engine) as session:
            yield session

    def override_dice_db():
        with Session(dice_engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_dife_db] = override_dife_db
    app.dependency_overrides[get_dice_db] = override_dice_db
    app.dependency_overrides[current_user] = lambda: User(
        id=1,
        email="admin@example.test",
        display_name="Admin",
        password_hash="test-hash",
        role=UserRole.ADMIN,
        is_active=True,
    )
    client = TestClient(app, raise_server_exceptions=False)
    client.cookies.set("qalab_csrf", "test-csrf")
    client.headers["x-csrf-token"] = "test-csrf"
    try:
        yield client, Session(engine), engine
    finally:
        client.close()
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        engine.dispose()


def seed_payment_scenario(client: TestClient) -> None:
    source = client.post(
        "/accounts",
        json={"account_id": "source", "spbvi_id": "spbvi-a", "balance_cents": 5000},
    )
    recipient = client.post(
        "/accounts",
        json={"account_id": "@recipient", "spbvi_id": "spbvi-a", "balance_cents": 250},
    )
    key = client.post(
        "/difes/spbvi-a/keys",
        json={
            "key_type": "alias",
            "key_value": "@recipient",
            "deposit_product_id": "@recipient",
        },
    )

    assert source.status_code == 201
    assert recipient.status_code == 201
    assert key.status_code == 201


def payment_payload(
    operation_id: str = "op-1",
    amount_cents: int = 1250,
) -> dict[str, str | int]:
    return {
        "operation_id": operation_id,
        "source_account_id": "source",
        "destination_key_type": "alias",
        "destination_key_value": "@recipient",
        "amount_cents": amount_cents,
    }


def seed_inter_spbvi_scenario(
    client: TestClient,
    key_stores: tuple[object, object],
) -> None:
    source = client.post(
        "/accounts",
        json={"account_id": "inter-source", "spbvi_id": "spbvi-a", "balance_cents": 5000},
    )
    recipient = client.post(
        "/accounts",
        json={"account_id": "inter-recipient", "spbvi_id": "spbvi-b", "balance_cents": 250},
    )
    assert source.status_code == 201
    assert recipient.status_code == 201

    with Session(key_stores[1]) as dice_db:
        dice_db.add(
            DiceKey(
                registration_id="inter-registration",
                key_type="alias",
                key_value="@remoterecipient",
                spbvi_id="spbvi-b",
                deposit_product_id="inter-recipient",
                status=KeyStatus.CONFIRMED,
            )
        )
        dice_db.commit()


def inter_payment_payload(
    operation_id: str = "inter-op-1",
    amount_cents: int = 1250,
) -> dict[str, str | int]:
    payload = json.loads(
        (PAYMENT_FIXTURES / "inter_spbvi.json").read_text(encoding="utf-8")
    )
    payload["operation_id"] = operation_id
    payload["amount_cents"] = amount_cents
    return payload


def test_payment_type_migration_preserves_legacy_payments() -> None:
    migration_engine = create_engine("sqlite://")
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE payments "
                    "(id INTEGER PRIMARY KEY, operation_id VARCHAR(100))"
                )
            )
            connection.execute(
                text("INSERT INTO payments (id, operation_id) VALUES (1, 'legacy')")
            )

        ensure_payment_type_column(migration_engine)

        with migration_engine.connect() as connection:
            payment_type = connection.scalar(
                text("SELECT payment_type FROM payments WHERE id = 1")
            )
        assert payment_type == "intra_spbvi"
    finally:
        migration_engine.dispose()


def test_dice_product_migration_adds_column_to_existing_table() -> None:
    migration_engine = create_engine("sqlite://")
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE dice_keys "
                    "(registration_id VARCHAR(36) PRIMARY KEY)"
                )
            )

        ensure_dice_product_column(migration_engine)

        assert "deposit_product_id" in {
            column["name"]
            for column in inspect(migration_engine).get_columns("dice_keys")
        }
    finally:
        migration_engine.dispose()


def test_dice_migration_backfills_legacy_mappings_from_active_dife_keys() -> None:
    dife_engine = create_engine("sqlite://")
    dice_engine = create_engine("sqlite://")
    DifeBase.metadata.create_all(dife_engine)
    DiceBase.metadata.create_all(dice_engine)
    try:
        with Session(dife_engine) as dife_db:
            dife_db.add(
                DifeKey(
                    key_type="alias",
                    key_value="@legacykey",
                    spbvi_id="spbvi-b",
                    deposit_product_id="legacy-account",
                    status=KeyStatus.ACTIVE,
                )
            )
            dife_db.commit()
        with Session(dice_engine) as dice_db:
            dice_db.add(
                DiceKey(
                    registration_id="legacy-registration",
                    key_type="alias",
                    key_value="@legacykey",
                    spbvi_id="spbvi-b",
                    deposit_product_id=None,
                    status=KeyStatus.CONFIRMED,
                )
            )
            dice_db.commit()

        backfill_dice_product_ids(dife_engine, dice_engine)

        with Session(dice_engine) as dice_db:
            legacy_key = dice_db.get(DiceKey, "legacy-registration")
            assert legacy_key is not None
            assert legacy_key.deposit_product_id == "legacy-account"
    finally:
        DifeBase.metadata.drop_all(dife_engine)
        DiceBase.metadata.drop_all(dice_engine)
        dife_engine.dispose()
        dice_engine.dispose()


def test_inter_spbvi_payment_resolves_dice_and_settles_through_mol(
    payment_client: tuple[TestClient, Session, object],
    key_stores: tuple[object, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db, _ = payment_client
    seed_inter_spbvi_scenario(client, key_stores)

    def fail_if_dife_is_consulted(*args, **kwargs):
        raise AssertionError("Un pago inter-SPBVI debe resolver la llave en DICE.")

    monkeypatch.setattr(
        "app.domains.payments.service.resolve_key",
        fail_if_dife_is_consulted,
    )
    response = client.post(
        "/payments/inter-spbvi",
        json=inter_payment_payload(),
    )

    assert response.status_code == 201
    assert response.json()["payment_type"] == "inter_spbvi"
    assert response.json()["status"] == "completed"
    assert response.json()["destination_account_id"] == "inter-recipient"
    assert response.json()["pacs008_xml"].startswith(
        f'<Document xmlns="{PACS_008_NAMESPACE}">'
    )
    assert response.json()["pacs002_xml"].startswith(
        f'<Document xmlns="{PACS_002_NAMESPACE}">'
    )
    validate_message(response.json()["pacs008_xml"])
    validate_message(response.json()["pacs002_xml"])
    db.expire_all()
    assert db.get(Account, "inter-source").balance_cents == 3750
    assert db.get(Account, "inter-recipient").balance_cents == 1500
    entries = db.scalars(select(LedgerEntry).where(LedgerEntry.payment_id.is_not(None))).all()
    assert len(entries) == 2
    assert sum(entry.amount_cents for entry in entries) == 0


def test_inter_spbvi_missing_dice_key_rejects_without_moving_funds(
    payment_client: tuple[TestClient, Session, object],
) -> None:
    client, db, _ = payment_client
    client.post(
        "/accounts",
        json={"account_id": "inter-source", "spbvi_id": "spbvi-a", "balance_cents": 5000},
    )

    response = client.post(
        "/payments/inter-spbvi",
        json=inter_payment_payload(),
    )

    assert response.status_code == 404
    db.expire_all()
    assert db.get(Account, "inter-source").balance_cents == 5000
    assert db.scalar(select(func.count()).select_from(Payment)) == 0


def test_inter_spbvi_replay_is_idempotent(
    payment_client: tuple[TestClient, Session, object],
    key_stores: tuple[object, object],
) -> None:
    client, db, _ = payment_client
    seed_inter_spbvi_scenario(client, key_stores)
    payload = inter_payment_payload()

    first = client.post("/payments/inter-spbvi", json=payload)
    replay = client.post("/payments/inter-spbvi", json=payload)

    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.json()["id"] == first.json()["id"]
    assert replay.json()["pacs008_xml"] == first.json()["pacs008_xml"]
    assert replay.json()["pacs002_xml"] == first.json()["pacs002_xml"]
    db.expire_all()
    assert db.get(Account, "inter-source").balance_cents == 3750
    assert db.get(Account, "inter-recipient").balance_cents == 1500
    assert db.scalar(select(func.count()).select_from(Payment)) == 1


def test_intra_payment_response_does_not_emit_inter_spbvi_iso_messages(
    payment_client: tuple[TestClient, Session, object],
) -> None:
    client, _, _ = payment_client
    seed_payment_scenario(client)

    response = client.post("/payments", json=payment_payload())

    assert response.status_code == 201
    assert "pacs008_xml" not in response.json()
    assert "pacs002_xml" not in response.json()


def test_operation_id_cannot_be_reused_between_payment_flows(
    payment_client: tuple[TestClient, Session, object],
    key_stores: tuple[object, object],
) -> None:
    client, db, _ = payment_client
    seed_payment_scenario(client)
    seed_inter_spbvi_scenario(client, key_stores)
    intra_payload = payment_payload(operation_id="shared-operation")
    inter_payload = inter_payment_payload(operation_id="shared-operation")

    first = client.post("/payments", json=intra_payload)
    conflict = client.post("/payments/inter-spbvi", json=inter_payload)

    assert first.status_code == 201
    assert conflict.status_code == 409
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Payment)) == 1
    assert db.get(Account, "inter-source").balance_cents == 5000


def test_inter_spbvi_payment_rejects_insufficient_balance_without_partial_ledger(
    payment_client: tuple[TestClient, Session, object],
    key_stores: tuple[object, object],
) -> None:
    client, db, _ = payment_client
    seed_inter_spbvi_scenario(client, key_stores)

    response = client.post(
        "/payments/inter-spbvi",
        json=inter_payment_payload(amount_cents=6000),
    )

    assert response.status_code == 409
    rejection = response.json()
    assert rejection["operation_id"] == inter_payment_payload(amount_cents=6000)[
        "operation_id"
    ]
    assert rejection["status"] == "rejected"
    validate_message(rejection["pacs002_xml"])
    rejection_xml = etree.fromstring(rejection["pacs002_xml"].encode("utf-8"))
    assert rejection_xml.findtext(f".//{{{PACS_002_NAMESPACE}}}GrpSts") == "RJCT"
    assert (
        rejection_xml.findtext(
            f".//{{{PACS_002_NAMESPACE}}}StsRsnInf/"
            f"{{{PACS_002_NAMESPACE}}}Rsn/{{{PACS_002_NAMESPACE}}}Prtry"
        )
        == "INSUFFICIENT_FUNDS"
    )
    db.expire_all()
    assert db.get(Account, "inter-source").balance_cents == 5000
    assert db.get(Account, "inter-recipient").balance_cents == 250
    assert db.scalar(select(func.count()).select_from(Payment)) == 0
    assert db.scalar(
        select(func.count())
        .select_from(LedgerEntry)
        .where(LedgerEntry.payment_id.is_not(None))
    ) == 0


def test_inter_spbvi_payment_rejects_same_participant_target(
    payment_client: tuple[TestClient, Session, object],
    key_stores: tuple[object, object],
) -> None:
    client, db, _ = payment_client
    client.post(
        "/accounts",
        json={"account_id": "inter-source", "spbvi_id": "spbvi-a", "balance_cents": 5000},
    )
    with Session(key_stores[1]) as dice_db:
        dice_db.add(
            DiceKey(
                registration_id="same-spbvi-registration",
                key_type="alias",
                key_value="@samespbvi",
                spbvi_id="spbvi-a",
                deposit_product_id="inter-source",
                status=KeyStatus.CONFIRMED,
            )
        )
        dice_db.commit()

    response = client.post(
        "/payments/inter-spbvi",
        json={
            **inter_payment_payload(),
            "destination_key_value": "@samespbvi",
        },
    )

    assert response.status_code == 422
    db.expire_all()
    assert db.get(Account, "inter-source").balance_cents == 5000
    assert db.scalar(select(func.count()).select_from(Payment)) == 0


def test_inter_spbvi_mol_failure_rolls_back_payment_and_both_ledger_sides(
    payment_client: tuple[TestClient, Session, object],
    key_stores: tuple[object, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db, _ = payment_client
    seed_inter_spbvi_scenario(client, key_stores)

    from app.domains.payments.mol import settle_with_mol as real_settle_with_mol

    def fail_after_settlement_writes(*args, **kwargs):
        real_settle_with_mol(*args, **kwargs)
        raise RuntimeError("Simulated MOL interruption")

    monkeypatch.setattr(
        "app.domains.payments.service.settle_with_mol",
        fail_after_settlement_writes,
    )
    response = client.post(
        "/payments/inter-spbvi",
        json=inter_payment_payload(),
    )

    assert response.status_code == 503
    db.expire_all()
    assert db.get(Account, "inter-source").balance_cents == 5000
    assert db.get(Account, "inter-recipient").balance_cents == 250
    assert db.scalar(select(func.count()).select_from(Payment)) == 0
    assert db.scalar(
        select(func.count())
        .select_from(LedgerEntry)
        .where(LedgerEntry.payment_id.is_not(None))
    ) == 0


def test_successful_intra_payment_moves_cents_and_balances_ledger(
    payment_client: tuple[TestClient, Session, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db, _ = payment_client
    seed_payment_scenario(client)

    def fail_if_dice_is_consulted(*args, **kwargs):
        raise AssertionError("Un pago intra-SPBVI no debe consultar al DICE.")

    monkeypatch.setattr(dice, "resolve_inter_spbvi_key", fail_if_dice_is_consulted)
    response = client.post("/payments", json=payment_payload())

    assert response.status_code == 201
    assert response.json()["status"] == "completed"
    assert response.json()["amount_cents"] == 1250
    db.expire_all()
    assert db.get(Account, "source").balance_cents == 3750
    assert db.get(Account, "@recipient").balance_cents == 1500
    entries = db.scalars(select(LedgerEntry)).all()
    payment_entries = [entry for entry in entries if entry.payment_id is not None]
    assert len(payment_entries) == 2
    assert sum(entry.amount_cents for entry in payment_entries) == 0
    assert {entry.amount_cents for entry in payment_entries} == {-1250, 1250}
    assert sum(entry.amount_cents for entry in entries) == 5250


def test_missing_destination_key_rejects_payment_without_changing_balances(
    payment_client: tuple[TestClient, Session, object],
) -> None:
    client, db, _ = payment_client
    client.post(
        "/accounts",
        json={"account_id": "source", "spbvi_id": "spbvi-a", "balance_cents": 5000},
    )

    response = client.post("/payments", json=payment_payload())

    assert response.status_code == 404
    db.expire_all()
    assert db.get(Account, "source").balance_cents == 5000
    assert db.scalar(select(func.count()).select_from(Payment)) == 0
    assert db.scalar(
        select(func.count())
        .select_from(LedgerEntry)
        .where(LedgerEntry.payment_id.is_not(None))
    ) == 0


def test_insufficient_funds_reject_payment_without_changing_balances(
    payment_client: tuple[TestClient, Session, object],
) -> None:
    client, db, _ = payment_client
    seed_payment_scenario(client)

    response = client.post(
        "/payments",
        json=payment_payload(amount_cents=6000),
    )

    assert response.status_code == 409
    db.expire_all()
    assert db.get(Account, "source").balance_cents == 5000
    assert db.get(Account, "@recipient").balance_cents == 250
    assert db.scalar(select(func.count()).select_from(Payment)) == 0
    assert db.scalar(
        select(func.count())
        .select_from(LedgerEntry)
        .where(LedgerEntry.payment_id.is_not(None))
    ) == 0


def test_repeated_operation_id_returns_original_payment_without_double_credit(
    payment_client: tuple[TestClient, Session, object],
) -> None:
    client, db, _ = payment_client
    seed_payment_scenario(client)

    first = client.post("/payments", json=payment_payload())
    replay = client.post("/payments", json=payment_payload())

    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.json()["id"] == first.json()["id"]
    db.expire_all()
    assert db.get(Account, "source").balance_cents == 3750
    assert db.get(Account, "@recipient").balance_cents == 1500
    assert db.scalar(select(func.count()).select_from(Payment)) == 1
    assert db.scalar(
        select(func.count())
        .select_from(LedgerEntry)
        .where(LedgerEntry.payment_id.is_not(None))
    ) == 2


def test_reusing_operation_id_with_different_payload_conflicts(
    payment_client: tuple[TestClient, Session, object],
) -> None:
    client, db, _ = payment_client
    seed_payment_scenario(client)
    client.post("/payments", json=payment_payload())

    response = client.post(
        "/payments",
        json=payment_payload(amount_cents=1000),
    )

    assert response.status_code == 409
    db.expire_all()
    assert db.get(Account, "source").balance_cents == 3750
    assert db.get(Account, "@recipient").balance_cents == 1500
    assert db.scalar(select(func.count()).select_from(Payment)) == 1


@pytest.mark.parametrize("amount_cents", [0, -1, 1.5])
def test_amount_must_be_a_positive_json_integer(
    payment_client: tuple[TestClient, Session, object],
    amount_cents: int | float,
) -> None:
    client, _, _ = payment_client

    invalid_payment = client.post(
        "/payments",
        json={**payment_payload(operation_id="invalid"), "amount_cents": amount_cents},
    )

    assert invalid_payment.status_code == 422


def test_failure_while_writing_credit_rolls_back_both_sides(
    payment_client: tuple[TestClient, Session, object],
) -> None:
    client, db, engine = payment_client
    seed_payment_scenario(client)
    inserted_entries = 0

    def fail_on_credit_entry(mapper, connection, target) -> None:
        nonlocal inserted_entries
        inserted_entries += 1
        if inserted_entries == 2:
            raise RuntimeError("Simulated credit ledger failure")

    sqlalchemy_event.listen(LedgerEntry, "before_insert", fail_on_credit_entry)
    try:
        response = client.post("/payments", json=payment_payload())
    finally:
        sqlalchemy_event.remove(LedgerEntry, "before_insert", fail_on_credit_entry)

    assert response.status_code == 500
    db.expire_all()
    assert db.get(Account, "source").balance_cents == 5000
    assert db.get(Account, "@recipient").balance_cents == 250
    assert db.scalar(select(func.count()).select_from(Payment)) == 0
    assert db.scalar(
        select(func.count())
        .select_from(LedgerEntry)
        .where(LedgerEntry.payment_id.is_not(None))
    ) == 0
