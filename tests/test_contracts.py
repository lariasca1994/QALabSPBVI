"""Validación JSON Schema de las respuestas de los CP contra el contrato OpenAPI."""

from app.domains.qa.contracts import validate_response_contract
from app.main import app


def test_valid_response_matches_documented_schema() -> None:
    result = validate_response_contract("GET", "/health", 200, {"status": "ok"})
    assert result == {"validated": True, "valid": True, "errors": [], "schema": "GET /health → 200"}


def test_missing_and_mistyped_fields_are_reported() -> None:
    result = validate_response_contract(
        "GET", "/payments/op-1/status-report", 200, {"operation_id": 1, "payment_type": "intra_spbvi"}
    )
    assert result["validated"] is True and result["valid"] is False
    assert any("operation_id" in error and "string" in error for error in result["errors"])
    assert any("pain002_xml" in error for error in result["errors"])


def test_literal_routes_win_over_parameters_and_unknown_codes_are_skipped() -> None:
    literal = validate_response_contract("POST", "/payments/inter-spbvi", 201, {})
    assert literal["schema"] == "POST /payments/inter-spbvi → 201"
    undocumented = validate_response_contract("GET", "/payments/op-1", 404, {"detail": "x"})
    assert undocumented["validated"] is False and undocumented["valid"] is None
    unknown = validate_response_contract("GET", "/no-existe", 200, {})
    assert unknown["validated"] is False and unknown["errors"]


def test_422_accepts_framework_validation_and_business_rejections() -> None:
    business = {"detail": "El monto supera el limite.", "status": "rejected", "pain002_xml": "<x/>"}
    framework = {"detail": [{"loc": ["body", "amount_cents"], "msg": "Field required", "type": "missing"}]}
    assert validate_response_contract("POST", "/payments", 422, business)["valid"] is True
    assert validate_response_contract("POST", "/payments", 422, framework)["valid"] is True
    assert validate_response_contract("POST", "/payments", 422, {"detail": 3})["valid"] is False
    assert "BusinessError" in app.openapi()["components"]["schemas"]
