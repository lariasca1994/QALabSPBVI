"""Pagos con QR: formato EMVCo de laboratorio, cobro dinámico de un solo uso y conciliación."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.security import current_user
from app.db.models import Account, LedgerEntry, Payment, User, UserRole
from app.domains.keys import dice
from app.domains.qr import service
from app.domains.qr.emv import QrData, QrFormatError, build_payload, crc16_ccitt, parse_payload
from app.domains.qr.persistence import QrBase, QrCharge, get_qr_db
from app.main import app
from tests.test_payments import key_stores, payment_client, seed_payment_scenario  # noqa: F401

RECEIVER = {"spbvi_id": "spbvi-a", "key_type": "alias", "key_value": "@recipient", "merchant_name": "Tienda Ñandú"}


# --- Formato -------------------------------------------------------------------------


def test_crc_matches_the_ccitt_false_check_value() -> None:
    assert crc16_ccitt("123456789") == "29B1"


@pytest.mark.parametrize(
    ("data", "qr_type"),
    [
        (QrData("static", "phone", "3001234567", "Ana", "Bogota"), "static"),
        (QrData("static_hybrid", "email", "ana@example.com", "Ana", "Cali", amount_cents=124500), "static_hybrid"),
        (QrData("dynamic", "alias", "@ana", "Café Ana", "Medellín", amount_cents=99, reference="F-1", charge_id="a" * 32), "dynamic"),
    ],
)
def test_payload_round_trip_keeps_the_data(data: QrData, qr_type: str) -> None:
    payload = build_payload(data)
    assert payload.startswith("000201010211" if qr_type != "dynamic" else "000201010212")
    assert "5303170" in payload and "5802CO" in payload
    parsed = parse_payload(payload)
    assert parsed.qr_type == qr_type
    assert (parsed.key_type, parsed.key_value, parsed.amount_cents) == (data.key_type, data.key_value, data.amount_cents)
    assert parsed.charge_id == data.charge_id
    # Tildes fuera: EMVCo usa ASCII.
    assert parsed.merchant_city in ("Bogota", "Cali", "Medellin")


def test_amount_is_written_with_two_decimals_and_read_as_integer_cents() -> None:
    payload = build_payload(QrData("static_hybrid", "alias", "@ana", "Ana", "Bogota", amount_cents=124505))
    assert "54071245.05" in payload
    assert parse_payload(payload).amount_cents == 124505


def _with_crc(body_without_crc: str) -> str:
    body = body_without_crc + "6304"
    return body + crc16_ccitt(body)


def test_tampering_is_detected_by_crc_and_by_signature() -> None:
    payload = build_payload(QrData("static_hybrid", "alias", "@ana", "Ana", "Bogota", amount_cents=1000))
    altered = payload.replace("540510.00", "540590.00")
    with pytest.raises(QrFormatError, match="CRC"):
        parse_payload(altered)
    # Con el CRC recalculado, la firma (campo 91) sigue delatando el cambio.
    with pytest.raises(QrFormatError, match="firma"):
        parse_payload(_with_crc(altered[:-8]))


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("000201", "CRC"),
        (_with_crc("0002010102115303170"), "CO"),
        (_with_crc("000201010213"), "tipo de QR"),
        (_with_crc("00020101021126180014CO.COM.RBM.LLA5303170"), "CO"),
        (_with_crc("00020101021126300014CO.COM.RBM.LLA0408@ana12345303170" + "5802CO"), "otra red"),
        (_with_crc("00020101021126300014CO.COM.LAB.LLA0408@ana12345303170" + "5802CO"), "hash de seguridad"),
        (_with_crc("0002010102112699"), "truncado"),
    ],
)
def test_invalid_payloads_are_rejected_with_a_clear_reason(payload: str, message: str) -> None:
    with pytest.raises(QrFormatError, match=message):
        parse_payload(payload)


# --- API -----------------------------------------------------------------------------


@pytest.fixture
def qr_client(payment_client) -> Iterator[tuple[TestClient, Session, Session]]:  # noqa: F811
    client, db, _ = payment_client
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    QrBase.metadata.create_all(engine)

    def override_qr_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_qr_db] = override_qr_db
    seed_payment_scenario(client)
    with Session(engine) as qr_db:
        yield client, db, qr_db
    engine.dispose()


def _charge(client: TestClient, amount: int = 1000, **extra) -> dict:
    response = client.post("/qr/charges", json={**RECEIVER, "amount_cents": amount, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def _balance(db: Session, account_id: str) -> int:
    db.expire_all()
    return db.get(Account, account_id).balance_cents


def test_static_qr_payment_is_intra_and_never_asks_dice(qr_client, monkeypatch) -> None:
    client, db, _ = qr_client
    static = client.post("/qr/static", json=RECEIVER)
    assert static.status_code == 200 and static.json()["qr_type"] == "static"

    preview = client.post("/qr/decode", json={"payload": static.json()["payload"]}).json()
    assert preview["amount_editable"] is True and preview["key_value"] == "@recipient"

    monkeypatch.setattr(dice, "resolve_inter_spbvi_key", lambda *a, **k: pytest.fail("intra no consulta el DICE"))
    body = {"payload": static.json()["payload"], "source_account_id": "source"}
    assert client.post("/qr/pay", json=body).status_code == 422  # falta el monto
    assert client.post("/qr/pay", json={**body, "amount_cents": 700}).status_code == 422  # falta operation_id
    paid = client.post("/qr/pay", json={**body, "amount_cents": 700, "operation_id": "qr-static-1"})
    assert paid.status_code == 201, paid.text
    assert paid.json()["flow"] == "intra" and paid.json()["payment"]["pain002_xml"]
    replay = client.post("/qr/pay", json={**body, "amount_cents": 700, "operation_id": "qr-static-1"})
    assert replay.status_code == 200 and replay.json()["payment"]["replayed"] is True
    assert _balance(db, "source") == 4300 and _balance(db, "@recipient") == 950


def test_hybrid_static_qr_does_not_allow_another_amount(qr_client) -> None:
    client, _, _ = qr_client
    payload = client.post("/qr/static", json={**RECEIVER, "amount_cents": 500}).json()["payload"]
    body = {"payload": payload, "source_account_id": "source", "operation_id": "qr-hybrid-1"}
    assert client.post("/qr/pay", json={**body, "amount_cents": 501}).status_code == 422
    assert client.post("/qr/pay", json=body).json()["payment"]["amount_cents"] == 500


def test_dynamic_charge_is_paid_once_and_ledger_balances(qr_client) -> None:
    client, db, _ = qr_client
    charge = _charge(client, 1250, reference="Factura 7")
    assert charge["status"] == "pending" and charge["seconds_left"] > 500
    preview = client.post("/qr/decode", json={"payload": charge["payload"]}).json()
    assert preview["qr_type"] == "dynamic" and preview["charge_status"] == "pending"
    assert preview["amount_editable"] is False and preview["reference"] == "Factura 7"

    paid = client.post("/qr/pay", json={"payload": charge["payload"], "source_account_id": "source"})
    assert paid.status_code == 201, paid.text
    assert paid.json()["charge_status"] == "paid"
    assert paid.json()["payment"]["operation_id"] == f"qr-{charge['charge_id']}"

    again = client.post("/qr/pay", json={"payload": charge["payload"], "source_account_id": "source"})
    assert again.status_code == 409 and again.json()["detail"] == "El cobro ya fue pagado."
    stored = client.get(f"/qr/charges/{charge['charge_id']}").json()
    assert stored["status"] == "paid" and stored["payer_account_id"] == "source"
    assert _balance(db, "source") == 3750
    entries = db.scalars(select(LedgerEntry).where(LedgerEntry.payment_id.is_not(None))).all()
    assert sum(entry.amount_cents for entry in entries) == 0


def test_dynamic_charge_rejects_a_different_amount(qr_client) -> None:
    client, _, _ = qr_client
    charge = _charge(client, 1000)
    response = client.post("/qr/pay", json={"payload": charge["payload"], "source_account_id": "source", "amount_cents": 1})
    assert response.status_code == 422
    assert client.get(f"/qr/charges/{charge['charge_id']}").json()["status"] == "pending"


def test_rejected_payment_releases_the_charge_for_another_payer(qr_client) -> None:
    client, db, _ = qr_client
    assert client.post("/accounts", json={"account_id": "poor", "spbvi_id": "spbvi-a", "balance_cents": 10}).status_code == 201
    charge = _charge(client, 1000)
    rejected = client.post("/qr/pay", json={"payload": charge["payload"], "source_account_id": "poor"})
    assert rejected.status_code == 409 and "pain002_xml" in rejected.json()
    assert client.get(f"/qr/charges/{charge['charge_id']}").json()["status"] == "pending"
    assert client.post("/qr/pay", json={"payload": charge["payload"], "source_account_id": "source"}).status_code == 201
    assert _balance(db, "poor") == 10


def test_expired_and_cancelled_charges_cannot_be_paid(qr_client, monkeypatch) -> None:
    client, _, _ = qr_client
    cancelled = _charge(client)
    assert client.delete(f"/qr/charges/{cancelled['charge_id']}").json()["status"] == "cancelled"
    response = client.post("/qr/pay", json={"payload": cancelled["payload"], "source_account_id": "source"})
    assert response.status_code == 410 and response.json()["detail"] == "El cobro fue anulado."

    expiring = _charge(client, expires_in_seconds=60)
    real_time = service.time.time
    monkeypatch.setattr(service.time, "time", lambda: real_time() + 61)
    response = client.post("/qr/pay", json={"payload": expiring["payload"], "source_account_id": "source"})
    assert response.status_code == 410 and response.json()["detail"] == "El cobro venció."
    assert client.get(f"/qr/charges/{expiring['charge_id']}").json()["status"] == "expired"
    assert client.delete(f"/qr/charges/{expiring['charge_id']}").status_code == 409


def test_charge_reserved_by_another_payer_is_blocked_and_same_payer_can_retry(qr_client) -> None:
    client, db, qr_db = qr_client
    assert client.post("/accounts", json={"account_id": "other", "spbvi_id": "spbvi-a", "balance_cents": 5000}).status_code == 201
    charge = _charge(client, 300)
    # Una reserva de "source" quedó a medias (por ejemplo, se cayó la conexión).
    service.reserve(qr_db, db, charge["charge_id"], payer_account_id="source")
    blocked = client.post("/qr/pay", json={"payload": charge["payload"], "source_account_id": "other"})
    assert blocked.status_code == 409 and "otra operación" in blocked.json()["detail"]
    retried = client.post("/qr/pay", json={"payload": charge["payload"], "source_account_id": "source"})
    assert retried.status_code == 201 and retried.json()["charge_status"] == "paid"


def test_reconciliation_finishes_or_frees_interrupted_payments(qr_client, monkeypatch) -> None:
    client, db, qr_db = qr_client
    # 1) El pago se hizo pero el cobro quedó "reserved" (caída antes de marcarlo pagado).
    paid = _charge(client, 400)
    service.reserve(qr_db, db, paid["charge_id"], payer_account_id="source")
    db.add(Payment(
        operation_id=f"qr-{paid['charge_id']}", source_account_id="source", destination_account_id="@recipient",
        destination_key_type="alias", destination_key_value="@recipient", amount_cents=400, status="completed",
    ))
    db.commit()
    assert client.get(f"/qr/charges/{paid['charge_id']}").json()["status"] == "paid"

    # 2) La reserva quedó sin pago: tras el tiempo de espera vuelve a estar disponible.
    stuck = _charge(client, 400)
    service.reserve(qr_db, db, stuck["charge_id"], payer_account_id="source")
    assert client.get(f"/qr/charges/{stuck['charge_id']}").json()["status"] == "reserved"
    real_time = service.time.time
    monkeypatch.setattr(service.time, "time", lambda: real_time() + service.RESERVATION_TIMEOUT_SECONDS + 1)
    assert client.get(f"/qr/charges/{stuck['charge_id']}").json()["status"] == "pending"
    assert qr_db.scalar(select(QrCharge.payer_account_id).where(QrCharge.charge_id == stuck["charge_id"])) is None


def test_inter_spbvi_qr_payment_goes_through_dice_and_mol(qr_client) -> None:
    client, db, _ = qr_client
    assert client.post("/accounts", json={"account_id": "b-shop", "spbvi_id": "spbvi-b", "balance_cents": 0}).status_code == 201
    key = client.post("/difes/spbvi-b/keys", json={"key_type": "merchant_code", "key_value": "0012345", "deposit_product_id": "b-shop"})
    assert key.status_code == 201
    charge = client.post("/qr/charges", json={
        "spbvi_id": "spbvi-b", "key_type": "merchant_code", "key_value": "0012345", "merchant_name": "Comercio B", "amount_cents": 2000,
    }).json()
    paid = client.post("/qr/pay", json={"payload": charge["payload"], "source_account_id": "source"})
    assert paid.status_code == 201, paid.text
    assert paid.json()["flow"] == "inter" and "pacs.008.001.08" in paid.json()["payment"]["pacs008_xml"]
    assert _balance(db, "b-shop") == 2000


def test_usuario_only_charges_with_own_keys_and_tampered_qr_is_refused(qr_client) -> None:
    client, _, _ = qr_client
    app.dependency_overrides[current_user] = lambda: User(
        id=7, email="persona@example.test", display_name="Persona", password_hash="x", role=UserRole.USUARIO, is_active=True,
    )
    assert client.post("/qr/charges", json={**RECEIVER, "amount_cents": 100}).status_code == 403
    assert client.post("/qr/static", json={**RECEIVER, "key_value": "@noexiste"}).status_code == 404
    payload = build_payload(QrData("static_hybrid", "alias", "@recipient", "X", "Bogota", amount_cents=100))
    forged = _with_crc(payload[:-8].replace("@recipient", "@recipiens"))
    response = client.post("/qr/decode", json={"payload": forged})
    assert response.status_code == 422 and "firma" in response.json()["detail"]


def test_qr_placeholders_reuse_the_last_qr_generated_by_a_case() -> None:
    import mongomock

    from app.domains.qa.placeholders import PlaceholderError, Resolver, record_qr_result, validate_placeholders

    database = mongomock.MongoClient()["qr_placeholders"]
    with pytest.raises(PlaceholderError, match="QR dinámico"):
        Resolver(database, "EP-1").resolve({"payload": "{{qr:charge}}"})
    record_qr_result(database, epic_key="EP-1", method="POST", path="/qr/charges", status_code=201,
                     response_body={"payload": "000201...", "charge_id": "abc"})
    record_qr_result(database, epic_key="EP-1", method="POST", path="/qr/charges", status_code=422,
                     response_body={"payload": "no-se-guarda"})
    resolved = Resolver(database, "EP-1").resolve({"payload": "{{qr:charge}}", "path": "/qr/charges/{{qr:charge-id}}"})
    assert resolved == {"payload": "000201...", "path": "/qr/charges/abc"}
    with pytest.raises(PlaceholderError, match="mal formado"):
        validate_placeholders({"payload": "{{qr:otro}}"})
