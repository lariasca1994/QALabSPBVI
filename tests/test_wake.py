"""Bases que duermen sin uso: se activan al entrar a la app y toleran la reanudación."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from app.db import wake


class FlakyDbapi:
    """DBAPI falsa: falla con el error de reanudación de Azure SQL N veces."""

    def __init__(self, failures: int, message: str = "[42000] Database is not currently available (40613)") -> None:
        self.failures = failures
        self.message = message
        self.calls = 0

    def __getattr__(self, name: str):
        import sqlite3

        return getattr(sqlite3, name)

    def connect(self, *args, **kwargs):
        import sqlite3

        self.calls += 1
        if self.calls <= self.failures:
            raise sqlite3.OperationalError(self.message)
        return sqlite3.connect(":memory:")


def engine_with(dbapi: FlakyDbapi, attempts: int = 4):
    engine = create_engine("sqlite://")
    engine.dialect.loaded_dbapi = dbapi
    return wake.retry_transient_connect(engine, attempts=attempts, backoff=0)


def test_connect_retries_while_the_database_resumes() -> None:
    dbapi = FlakyDbapi(failures=2)
    with engine_with(dbapi).connect() as connection:
        assert connection.execute(text("SELECT 1")).scalar() == 1
    assert dbapi.calls == 3


def test_connect_does_not_retry_real_errors() -> None:
    dbapi = FlakyDbapi(failures=5, message="Login failed for user")
    with pytest.raises(OperationalError):
        engine_with(dbapi).connect()
    assert dbapi.calls == 1


def test_connect_gives_up_after_the_last_attempt() -> None:
    dbapi = FlakyDbapi(failures=10)
    with pytest.raises(OperationalError):
        engine_with(dbapi, attempts=3).connect()
    assert dbapi.calls == 3


def test_wake_runs_at_most_every_five_minutes(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class Engine:
        def __init__(self, name: str) -> None:
            self.name = name

        def connect(self):
            calls.append(self.name)
            return create_engine("sqlite://").connect()

    monkeypatch.setattr("app.domains.keys.persistence.get_dife_engine", lambda: Engine("dife"))
    monkeypatch.setattr("app.domains.keys.persistence.get_dice_engine", lambda: Engine("dice"))
    monkeypatch.setattr(wake, "_last_wake", 0.0)
    wake.wake_key_stores()
    wake.wake_key_stores()
    assert calls == ["dife", "dice"]


def test_auth_me_wakes_key_stores_but_health_does_not(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import config
    from app.core.security import current_user
    from app.db.models import User, UserRole
    from app.main import app

    woke: list[bool] = []
    monkeypatch.setattr("app.api.auth_routes.wake_key_stores", lambda: woke.append(True))
    monkeypatch.setenv("DIFE_DATABASE_URL", "mssql+pyodbc://u:p@h/db")
    config.get_settings.cache_clear()
    app.dependency_overrides[current_user] = lambda: User(
        id=1, email="a@example.com", display_name="A", password_hash="x", role=UserRole.ADMIN, is_active=True
    )
    try:
        client = TestClient(app)
        assert client.get("/health").status_code == 200
        assert woke == []
        assert client.get("/auth/me").status_code == 200
        assert woke == [True]
    finally:
        app.dependency_overrides.clear()
        config.get_settings.cache_clear()


def test_unavailable_database_returns_a_readable_503() -> None:
    from fastapi import FastAPI

    from app.main import create_app

    application: FastAPI = create_app()

    @application.get("/falla-sql")
    def broken():
        raise OperationalError("SELECT 1", {}, Exception("40613"))

    response = TestClient(application, raise_server_exceptions=False).get("/falla-sql")
    assert response.status_code == 503
    assert "se está activando" in response.json()["detail"]
    assert response.headers["retry-after"] == "15"


def test_databases_endpoint_reports_waking_databases(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from app.core.security import current_user
    from app.db.models import User, UserRole
    from app.main import app

    app.dependency_overrides[current_user] = lambda: User(
        id=1, email="qa@example.test", display_name="QA", password_hash="x", role=UserRole.USUARIO, is_active=True
    )
    try:
        client = TestClient(app)
        monkeypatch.setattr("app.api.routes.check_databases", lambda: {"pagos": "lista", "dife": "activando", "dice": "lista"})
        waking = client.get("/health/databases")
        assert waking.status_code == 503 and waking.headers["retry-after"] == "10"
        assert waking.json() == {"ready": False, "databases": {"pagos": "lista", "dife": "activando", "dice": "lista"}}
        monkeypatch.setattr("app.api.routes.check_databases", lambda: {"pagos": "lista", "dife": "lista", "dice": "lista"})
        assert client.get("/health/databases").json()["ready"] is True
    finally:
        app.dependency_overrides.clear()
    # Sin sesión no responde: los monitores no pueden despertar las bases.
    assert TestClient(app).get("/health/databases").status_code == 401
