import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.schemas import KeyRegistrationRequest, PaymentCreateRequest
from app.domains.keys.key_types import InvalidKeyError, normalize_key_value
from app.main import app


@pytest.mark.parametrize(
    ("key_type", "raw", "expected"),
    [
        ("phone", "+57 300 123 4567", "3001234567"),
        ("phone", "573001234567", "3001234567"),
        ("document", "1.023.456.789", "1023456789"),
        ("email", "  Ana@Example.COM ", "ana@example.com"),
        ("alias", "@Ana2026", "@ana2026"),
        ("merchant_code", "0012345", "0012345"),
    ],
)
def test_key_values_are_normalized_to_canonical_form(key_type: str, raw: str, expected: str) -> None:
    assert normalize_key_value(key_type, raw) == expected


@pytest.mark.parametrize(
    ("key_type", "raw"),
    [
        ("phone", "6011234567"),
        ("phone", "300123"),
        ("document", "12AB"),
        ("email", "sin-arroba"),
        ("alias", "ana2026"),
        ("alias", "@a"),
        ("merchant_code", "12"),
    ],
)
def test_invalid_key_values_are_rejected(key_type: str, raw: str) -> None:
    with pytest.raises(InvalidKeyError):
        normalize_key_value(key_type, raw)


def test_registration_and_payment_requests_validate_key_type_and_value() -> None:
    request = KeyRegistrationRequest(
        key_type="PHONE", key_value="+57 300 123 4567", deposit_product_id="acc-1"
    )
    assert (request.key_type, request.key_value) == ("phone", "3001234567")

    with pytest.raises(ValidationError):
        KeyRegistrationRequest(key_type="iban", key_value="x", deposit_product_id="acc-1")

    with pytest.raises(ValidationError):
        PaymentCreateRequest(
            operation_id="op-1",
            source_account_id="acc-1",
            destination_key_type="alias",
            destination_key_value="sin-arroba",
            amount_cents=100,
        )


def test_key_type_catalog_lists_bre_b_types() -> None:
    response = TestClient(app).get("/keys/types")

    assert response.status_code == 200
    assert [item["code"] for item in response.json()] == [
        "document",
        "phone",
        "email",
        "alias",
        "merchant_code",
    ]
    assert all(item["label"] and item["example"] and item["hint"] for item in response.json())
