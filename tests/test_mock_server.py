"""Mock server de resiliencia: latencia, timeout aguas arriba, errores y servicio inestable."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.security import current_user
from app.db.base import Base
from app.db.models import User, UserRole
from app.db.session import get_db
from app.main import app


@pytest.fixture
def client() -> Iterator[TestClient]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)

    def override_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[current_user] = lambda: User(
        id=7, email="qa@example.test", display_name="QA", password_hash="x", role=UserRole.USUARIO, is_active=True
    )
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    engine.dispose()


def test_mock_requires_an_authenticated_session() -> None:
    with TestClient(app) as anonymous:
        assert anonymous.get("/mock/latency", params={"delay_ms": 0}).status_code == 401


def test_latency_and_upstream_timeout(client: TestClient) -> None:
    response = client.get("/mock/latency", params={"delay_ms": 200})
    assert response.status_code == 200 and response.json()["delay_ms"] == 200
    assert response.elapsed.total_seconds() >= 0.2
    assert client.get("/mock/latency", params={"delay_ms": 60_000}).status_code == 422

    timeout = client.get("/mock/upstream", params={"latency_ms": 5000, "timeout_ms": 300})
    assert timeout.status_code == 504
    assert timeout.elapsed.total_seconds() < 2
    fast = client.get("/mock/upstream", params={"latency_ms": 100, "timeout_ms": 1000})
    assert fast.status_code == 200 and fast.json()["delay_ms"] == 100


def test_simulated_status_codes(client: TestClient) -> None:
    unavailable = client.get("/mock/status/503")
    assert unavailable.status_code == 503
    assert unavailable.headers["retry-after"] == "1"
    assert unavailable.json()["simulated"] is True
    assert client.get("/mock/status/418").status_code == 422


def test_flaky_scenario_fails_n_times_per_scenario(client: TestClient) -> None:
    codes = [client.get("/mock/flaky/esc-1", params={"failures": 2, "mode": "502"}).status_code for _ in range(3)]
    assert codes == [502, 502, 200]
    assert client.get("/mock/flaky/esc-1", params={"failures": 2}).json()["attempts"] == 4
    # Otro escenario lleva su propio contador.
    assert client.get("/mock/flaky/esc-2", params={"failures": 1}).status_code == 503
