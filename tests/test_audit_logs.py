"""Registro de solicitudes en la base de logs: correlación por operación, permisos y exportación."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core import config
from app.core.security import current_user
from app.db.models import User, UserRole
from app.domains.audit import logs
from tests.test_payments import key_stores, payment_client, payment_payload, seed_payment_scenario  # noqa: F401


@pytest.fixture
def logs_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("LOGS_DATABASE_URL", f"sqlite:///{tmp_path / 'logs.db'}")
    config.get_settings.cache_clear()
    logs.logs_engine.cache_clear()
    logs.writer = logs.LogWriter()
    yield
    logs.writer.flush()
    engine = logs.logs_engine()
    if engine is not None:
        engine.dispose()
    logs.logs_engine.cache_clear()
    config.get_settings.cache_clear()


def test_requests_are_logged_and_correlated_by_operation(logs_database, payment_client) -> None:
    client, _, _ = payment_client
    from fastapi import Request

    from app.main import app

    def authenticated(request: Request) -> User:
        # Igual que current_user: deja el usuario de la sesión en request.state.
        request.state.user_id = 1
        return User(id=1, email="admin@example.test", display_name="Admin", password_hash="x", role=UserRole.ADMIN, is_active=True)

    app.dependency_overrides[current_user] = authenticated
    seed_payment_scenario(client)
    paid = client.post("/payments", json=payment_payload("op-audit"), headers={"x-request-id": "req-123"})
    assert paid.headers["x-request-id"] == "req-123"
    client.post("/payments/op-audit/returns", json={"return_id": "r-audit", "reason_code": "MD06"})
    client.get("/payments/op-audit/status-report")
    client.get("/health")

    response = client.get("/audit/logs", params={"operation_id": "op-audit"})
    assert response.status_code == 200
    routes = [(item["method"], item["route"], item["status_code"]) for item in response.json()]
    assert routes == [
        ("GET", "/payments/{operation_id}/status-report", 200),
        ("POST", "/payments/{operation_id}/returns", 201),
        ("POST", "/payments", 201),
    ]
    assert response.json()[-1]["request_id"] == "req-123"
    assert response.json()[-1]["user_id"] == 1
    everything = client.get("/audit/logs", params={"limit": 1000}).json()
    assert all(item["path"] != "/health" for item in everything)

    export = client.get("/audit/logs/export", params={"operation_id": "op-audit"})
    assert export.status_code == 200
    assert export.headers["content-type"].startswith("text/csv")
    lines = export.text.strip().splitlines()
    assert lines[0].startswith("created_at,environment,request_id") and len(lines) == 4


def test_only_managers_read_logs(logs_database, payment_client) -> None:
    client, _, _ = payment_client
    from app.main import app

    app.dependency_overrides[current_user] = lambda: User(
        id=9, email="u@example.test", display_name="U", password_hash="x", role=UserRole.USUARIO, is_active=True
    )
    assert client.get("/audit/logs").status_code == 403


def test_logs_endpoint_reports_missing_configuration(payment_client, monkeypatch) -> None:
    client, _, _ = payment_client
    monkeypatch.setenv("LOGS_DATABASE_URL", "")
    config.get_settings.cache_clear()
    logs.logs_engine.cache_clear()
    assert client.get("/audit/logs").status_code == 503
    config.get_settings.cache_clear()
