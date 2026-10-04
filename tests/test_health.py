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
