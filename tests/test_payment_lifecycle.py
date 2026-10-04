"""Devoluciones (pacs.004), cancelaciones (camt.056 → camt.029), notificaciones (camt.054) y
reporte de estado al cliente (pain.002)."""

from lxml import etree
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Account, LedgerEntry
from app.domains.iso20022.lifecycle import (
    CAMT_029_NAMESPACE,
    CAMT_054_NAMESPACE,
    CAMT_056_NAMESPACE,
    PACS_004_NAMESPACE,
    PAIN_002_NAMESPACE,
)
from app.domains.iso20022.messages import validate_message
from tests.test_payments import (  # noqa: F401 - fixtures compartidas
    inter_payment_payload,
    key_stores,
    payment_client,
    payment_payload,
    seed_inter_spbvi_scenario,
    seed_payment_scenario,
)


def _text(xml: str, namespace: str, path: str) -> str:
    root = etree.fromstring(xml.encode("utf-8"))
    return root.findtext(path, namespaces={"d": namespace})


def _balances(engine) -> dict[str, int]:
    with Session(engine) as db:
        return {account.id: account.balance_cents for account in db.scalars(select(Account))}


def _ledger_total(engine) -> int:
    with Session(engine) as db:
        return db.scalar(
            select(func.sum(LedgerEntry.amount_cents)).where(LedgerEntry.payment_id.is_not(None))
        )


def _paid(client, operation_id="op-1", amount_cents=1250):
    seed_payment_scenario(client)
    response = client.post("/payments", json=payment_payload(operation_id, amount_cents))
    assert response.status_code == 201
    return response.json()


def test_total_return_restores_balances_and_is_idempotent(payment_client) -> None:
    client, _, engine = payment_client
    _paid(client)
    assert _balances(engine) == {"source": 3750, "@recipient": 1500}

    response = client.post("/payments/op-1/returns", json={"return_id": "ret-1", "reason_code": "MD06"})
    assert response.status_code == 201
    body = response.json()
    assert body["amount_cents"] == 1250
    assert body["returned_total_cents"] == 1250
    assert body["reason"] == "Reembolso solicitado por el cliente"
    validate_message(body["pacs004_xml"])
    assert _text(body["pacs004_xml"], PACS_004_NAMESPACE, "d:PmtRtr/d:TxInf/d:RtrRsnInf/d:Rsn/d:Cd") == "MD06"
    assert _text(body["pacs004_xml"], PACS_004_NAMESPACE, "d:PmtRtr/d:TxInf/d:RtrdIntrBkSttlmAmt") == "12.50"
    assert _balances(engine) == {"source": 5000, "@recipient": 250}
    assert _ledger_total(engine) == 0

    replay = client.post("/payments/op-1/returns", json={"return_id": "ret-1", "reason_code": "MD06"})
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert _balances(engine) == {"source": 5000, "@recipient": 250}

    again = client.post("/payments/op-1/returns", json={"return_id": "ret-2", "reason_code": "MD06"})
    assert again.status_code == 409


def test_partial_returns_cannot_exceed_original_amount(payment_client) -> None:
    client, _, engine = payment_client
    _paid(client)

    first = client.post(
        "/payments/op-1/returns", json={"return_id": "ret-p1", "amount_cents": 500, "reason_code": "AM05"}
    )
    assert first.status_code == 201
    over = client.post(
        "/payments/op-1/returns", json={"return_id": "ret-p2", "amount_cents": 800, "reason_code": "AM05"}
    )
    assert over.status_code == 422
    rest = client.post("/payments/op-1/returns", json={"return_id": "ret-p3", "reason_code": "AM05"})
    assert rest.status_code == 201
    assert rest.json()["amount_cents"] == 750
    assert rest.json()["returned_total_cents"] == 1250
    listed = client.get("/payments/op-1/returns")
    assert [item["return_id"] for item in listed.json()] == ["ret-p1", "ret-p3"]
    assert _ledger_total(engine) == 0


def test_return_validates_reason_payment_and_receiver_funds(payment_client) -> None:
    client, db, engine = payment_client
    _paid(client)

    invalid = client.post("/payments/op-1/returns", json={"return_id": "r", "reason_code": "ZZ99"})
    assert invalid.status_code == 422
    missing = client.post("/payments/nada/returns", json={"return_id": "r", "reason_code": "MD06"})
    assert missing.status_code == 404

    # El receptor gastó el dinero: la devolución no deja saldos a medias.
    with Session(engine) as session:
        session.get(Account, "@recipient").balance_cents = 100
        session.commit()
    before = _balances(engine)
    poor = client.post("/payments/op-1/returns", json={"return_id": "r-poor", "reason_code": "MD06"})
    assert poor.status_code == 409
    assert _balances(engine) == before
    assert client.get("/payments/op-1/returns").json() == []


def test_cancellation_accepted_returns_funds_with_focr(payment_client) -> None:
    client, _, engine = payment_client
    _paid(client)

    request = client.post(
        "/payments/op-1/cancellation-requests", json={"cancellation_id": "cxl-1", "reason_code": "DUPL"}
    )
    assert request.status_code == 202
    body = request.json()
    assert body["status"] == "pending"
    validate_message(body["camt056_xml"])
    assert _text(body["camt056_xml"], CAMT_056_NAMESPACE, "d:FIToFIPmtCxlReq/d:Undrlyg/d:TxInf/d:CxlRsnInf/d:Rsn/d:Cd") == "DUPL"
    assert _text(body["camt029_xml"], CAMT_029_NAMESPACE, "d:RsltnOfInvstgtn/d:Sts/d:Conf") == "PECR"

    duplicate = client.post(
        "/payments/op-1/cancellation-requests", json={"cancellation_id": "cxl-2", "reason_code": "CUST"}
    )
    assert duplicate.status_code == 409

    resolved = client.post("/investigations/cxl-1/resolution", json={"accepted": True})
    assert resolved.status_code == 200
    result = resolved.json()
    assert result["status"] == "accepted"
    assert _text(result["camt029_xml"], CAMT_029_NAMESPACE, "d:RsltnOfInvstgtn/d:CxlDtls/d:TxInfAndSts/d:TxCxlSts") == "ACCR"
    assert result["payment_return"]["reason_code"] == "FOCR"
    assert result["payment_return"]["amount_cents"] == 1250
    assert _balances(engine) == {"source": 5000, "@recipient": 250}

    replay = client.post("/investigations/cxl-1/resolution", json={"accepted": True})
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert _balances(engine) == {"source": 5000, "@recipient": 250}
    conflicting = client.post(
        "/investigations/cxl-1/resolution", json={"accepted": False, "rejection_reason": "CUST"}
    )
    assert conflicting.status_code == 409
    assert client.get("/investigations/cxl-1").json()["status"] == "accepted"

    after_return = client.post(
        "/payments/op-1/cancellation-requests", json={"cancellation_id": "cxl-3", "reason_code": "CUST"}
    )
    assert after_return.status_code == 409


def test_cancellation_rejected_keeps_funds_and_requires_reason(payment_client) -> None:
    client, _, engine = payment_client
    _paid(client)
    client.post("/payments/op-1/cancellation-requests", json={"cancellation_id": "cxl-r", "reason_code": "CUST"})

    without_reason = client.post("/investigations/cxl-r/resolution", json={"accepted": False})
    assert without_reason.status_code == 422
    rejected = client.post(
        "/investigations/cxl-r/resolution", json={"accepted": False, "rejection_reason": "NOAS"}
    )
    assert rejected.status_code == 200
    body = rejected.json()
    assert body["status"] == "rejected"
    assert body["payment_return"] is None
    assert _text(body["camt029_xml"], CAMT_029_NAMESPACE, "d:RsltnOfInvstgtn/d:Sts/d:Conf") == "RJCR"
    assert _text(
        body["camt029_xml"], CAMT_029_NAMESPACE, "d:RsltnOfInvstgtn/d:CxlDtls/d:TxInfAndSts/d:CxlStsRsnInf/d:Rsn/d:Cd"
    ) == "NOAS"
    assert _balances(engine) == {"source": 3750, "@recipient": 1500}

    assert client.get("/investigations/no-existe").status_code == 404
    assert client.post("/investigations/no-existe/resolution", json={"accepted": True}).status_code == 404
    invalid = client.post(
        "/payments/op-1/cancellation-requests", json={"cancellation_id": "cxl-x", "reason_code": "ABCD"}
    )
    assert invalid.status_code == 422


def test_account_notifications_report_credits_and_debits(payment_client) -> None:
    client, _, _ = payment_client
    _paid(client)
    client.post("/payments/op-1/returns", json={"return_id": "ret-n", "amount_cents": 250, "reason_code": "MD06"})

    response = client.get("/accounts/@recipient/notifications")
    assert response.status_code == 200
    body = response.json()
    assert [(entry["notification_type"], entry["amount_cents"]) for entry in body["entries"]] == [
        ("credit", 1250),
        ("debit", 250),
    ]
    assert body["total_credits_cents"] == 1250 and body["total_debits_cents"] == 250
    validate_message(body["camt054_xml"])
    root = etree.fromstring(body["camt054_xml"].encode("utf-8"))
    indicators = root.findall(".//d:Ntry/d:CdtDbtInd", namespaces={"d": CAMT_054_NAMESPACE})
    assert [item.text for item in indicators] == ["CRDT", "DBIT"]

    filtered = client.get("/accounts/source/notifications", params={"operation_id": "op-1"})
    assert [entry["notification_type"] for entry in filtered.json()["entries"]] == ["debit", "credit"]
    assert client.get("/accounts/no-existe/notifications").status_code == 404
    assert client.get("/accounts/source/notifications", params={"operation_id": "otra"}).status_code == 404


def test_intra_payment_reports_pain002_on_success_and_rejection(payment_client) -> None:
    client, _, _ = payment_client
    body = _paid(client)
    validate_message(body["pain002_xml"])
    assert _text(body["pain002_xml"], PAIN_002_NAMESPACE, "d:CstmrPmtStsRpt/d:OrgnlPmtInfAndSts/d:TxInfAndSts/d:TxSts") == "ACSC"

    poor = client.post("/payments", json=payment_payload("op-poor", 999_999))
    assert poor.status_code == 409
    rejection = poor.json()
    assert rejection["status"] == "rejected" and rejection["reason_code"] == "AM04"
    assert _text(rejection["pain002_xml"], PAIN_002_NAMESPACE, ".//d:TxSts") == "RJCT"
    assert _text(rejection["pain002_xml"], PAIN_002_NAMESPACE, ".//d:StsRsnInf/d:Rsn/d:Cd") == "AM04"

    missing_key = payment_payload("op-key")
    missing_key["destination_key_value"] = "@nadie"
    unknown = client.post("/payments", json=missing_key)
    assert unknown.status_code == 404 and unknown.json()["reason_code"] == "AC03"

    report = client.get("/payments/op-1/status-report")
    assert report.status_code == 200
    assert report.json()["transaction_status"] == "ACSC"
    validate_message(report.json()["pain002_xml"])
    assert client.get("/payments/no-existe/status-report").status_code == 404


def test_inter_spbvi_payment_can_be_returned(payment_client, key_stores) -> None:
    client, _, engine = payment_client
    seed_inter_spbvi_scenario(client, key_stores)
    assert client.post("/payments/inter-spbvi", json=inter_payment_payload()).status_code == 201

    response = client.post(
        "/payments/inter-op-1/returns", json={"return_id": "ret-inter", "reason_code": "AC04"}
    )
    assert response.status_code == 201
    assert _balances(engine) == {"inter-source": 5000, "inter-recipient": 250}
    report = client.get("/payments/inter-op-1/status-report").json()
    assert report["payment_type"] == "inter_spbvi"
