from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_check_returns_ok() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_payments_engine_discards_connections_closed_by_server() -> None:
    # Neon suspende el cómputo por inactividad y corta las conexiones del pool;
    # sin pre_ping la siguiente solicitud (p. ej. el login) termina en 500.
    from app.db.session import engine

    assert engine.pool._pre_ping is True
    assert 0 < engine.pool._recycle <= 300


def test_cloud_mode_uses_no_connection_pool_so_databases_can_pause(monkeypatch) -> None:
    """Sin pool no quedan sesiones ociosas: Azure SQL gratuito y Neon pueden pausarse."""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool, QueuePool

    from app.core import config

    monkeypatch.setenv("DATABASE_POOL", "null")
    config.get_settings.cache_clear()
    try:
        options = config.engine_options("postgresql+psycopg://u:p@h/db")
        assert options == {"poolclass": NullPool}
        engine = create_engine("postgresql+psycopg://u:p@h/db", **options)
        assert isinstance(engine.pool, NullPool)
    finally:
        config.get_settings.cache_clear()
    monkeypatch.setenv("DATABASE_POOL", "queue")
    config.get_settings.cache_clear()
    assert config.engine_options("postgresql+psycopg://u:p@h/db")["pool_pre_ping"] is True
    assert isinstance(create_engine("postgresql+psycopg://u:p@h/db", **config.engine_options("postgresql+psycopg://u:p@h/db")).pool, QueuePool)
    config.get_settings.cache_clear()


def test_health_does_not_touch_any_database() -> None:
    """Un monitor que consulta /health no debe despertar ninguna base."""
    import inspect

    from app.api import routes

    source = inspect.getsource(routes.health_check)
    assert "db" not in source and "Session" not in source
