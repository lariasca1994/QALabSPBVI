from datetime import UTC, datetime
import json
from pathlib import Path

import pytest
from lxml import etree

from app.domains.iso20022.messages import (
    Iso20022ValidationError,
    Pacs002Data,
    Pacs008Data,
    build_pacs002,
    build_pacs008,
    pacs002_group_status_for_payment,
    validate_message,
)

NS_PACS_008 = "urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08"
NS_PACS_002 = "urn:iso:std:iso:20022:tech:xsd:pacs.002.001.10"
CREATED_AT = datetime(2026, 10, 3, 12, 30, tzinfo=UTC)
ISO_FIXTURES = Path(__file__).parent / "fixtures" / "iso20022"


def pacs002_payload(name: str) -> dict[str, str]:
    return json.loads(
        (ISO_FIXTURES / f"pacs002_{name}.json").read_text(encoding="utf-8")
    )


def test_builds_and_validates_pacs008_without_float_amounts() -> None:
    message = build_pacs008(
        Pacs008Data(
            message_id="message-001",
            operation_id="operation-001",
            source_spbvi_id="spbvi-a",
            destination_spbvi_id="spbvi-b",
            source_account_id="account-a",
            destination_account_id="account-b",
            amount_cents=1250,
            created_at=CREATED_AT,
        )
    )

    validate_message(message)
    root = etree.fromstring(message.encode("utf-8"))
    amount = root.findtext(
        f".//{{{NS_PACS_008}}}IntrBkSttlmAmt"
    )
    assert amount == "12.50"
    assert root.findtext(f".//{{{NS_PACS_008}}}InstrId") == "operation-001"
    assert root.findtext(f".//{{{NS_PACS_008}}}Dbtr/{{{NS_PACS_008}}}Nm") == "spbvi-a"
    assert root.findtext(f".//{{{NS_PACS_008}}}Cdtr/{{{NS_PACS_008}}}Nm") == "spbvi-b"


def test_builds_and_validates_pacs002_status_report() -> None:
    payload = pacs002_payload("accepted")
    message = build_pacs002(
        Pacs002Data(
            **payload,
            created_at=CREATED_AT,
        )
    )

    validate_message(message)
    root = etree.fromstring(message.encode("utf-8"))
    assert root.findtext(f".//{{{NS_PACS_002}}}OrgnlMsgId") == payload[
        "original_message_id"
    ]
    assert root.findtext(f".//{{{NS_PACS_002}}}GrpSts") == "ACCP"


@pytest.mark.parametrize(
    ("fixture_name", "payment_status", "group_status", "reason"),
    [
        ("accepted", "completed", "ACCP", None),
        (
            "rejected_insufficient_funds",
            "rejected",
            "RJCT",
            "INSUFFICIENT_FUNDS",
        ),
        (
            "pending",
            "pending",
            "PDNG",
            "AWAITING_SIMULATED_MOL_CONFIRMATION",
        ),
    ],
)
def test_pacs002_status_fixtures_validate_and_match_payment_outcomes(
    fixture_name: str,
    payment_status: str,
    group_status: str,
    reason: str | None,
) -> None:
    payload = pacs002_payload(fixture_name)
    data = Pacs002Data(**payload, created_at=CREATED_AT)

    message = build_pacs002(data)
    validate_message(message)

    root = etree.fromstring(message.encode("utf-8"))
    assert pacs002_group_status_for_payment(payment_status) == group_status
    assert root.findtext(f".//{{{NS_PACS_002}}}GrpSts") == group_status
    assert (
        root.findtext(
            f".//{{{NS_PACS_002}}}StsRsnInf/"
            f"{{{NS_PACS_002}}}Rsn/{{{NS_PACS_002}}}Prtry"
        )
        == reason
    )


@pytest.mark.parametrize(
    ("payment_status", "expected_group_status"),
    [
        ("completed", "ACCP"),
        ("pending", "PDNG"),
        ("rejected", "RJCT"),
    ],
)
def test_maps_payment_status_to_pacs002_group_status(
    payment_status: str,
    expected_group_status: str,
) -> None:
    assert pacs002_group_status_for_payment(payment_status) == expected_group_status


def test_rejects_unknown_payment_status_for_pacs002() -> None:
    with pytest.raises(ValueError, match="sin correspondencia"):
        pacs002_group_status_for_payment("cancelled")


def test_builder_escapes_untrusted_xml_text() -> None:
    message = build_pacs008(
        Pacs008Data(
            message_id="message<&>",
            operation_id="operation<&>",
            source_spbvi_id="spbvi<&>",
            destination_spbvi_id="spbvi-b",
            source_account_id="account-a",
            destination_account_id="account-b",
            amount_cents=1,
            created_at=CREATED_AT,
        )
    )

    root = etree.fromstring(message.encode("utf-8"))
    assert root.findtext(f".//{{{NS_PACS_008}}}InstrId") == "operation<&>"
    assert "&lt;" in message


@pytest.mark.parametrize("amount_cents", [0, -1, 1.5, True])
def test_pacs008_rejects_invalid_cent_amounts(amount_cents: int) -> None:
    with pytest.raises(ValueError):
        Pacs008Data(
            message_id="message-001",
            operation_id="operation-001",
            source_spbvi_id="spbvi-a",
            destination_spbvi_id="spbvi-b",
            source_account_id="account-a",
            destination_account_id="account-b",
            amount_cents=amount_cents,
            created_at=CREATED_AT,
        )


def test_validator_rejects_unknown_or_invalid_xml() -> None:
    with pytest.raises(Iso20022ValidationError):
        validate_message("<NotAnIsoMessage/>")

    with pytest.raises(Iso20022ValidationError):
        validate_message(
            f'<Document xmlns="{NS_PACS_008}"><FIToFICstmrCdtTrf/></Document>'
        )
