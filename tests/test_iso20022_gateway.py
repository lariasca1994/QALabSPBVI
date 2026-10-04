"""Gateway ISO 20022 (Cloud Run) y su cliente con respaldo local en la API."""

import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core import config
from app.domains.iso20022 import gateway
from app.domains.iso20022.messages import Pacs002Data, Pacs008Data, validate_message

sys.path.insert(0, str(Path(__file__).parents[1] / "services" / "iso20022_gateway"))
import main as gateway_service  # noqa: E402

TOKEN = "t" * 40
PACS008 = Pacs008Data(
    message_id="pacs008-op-1",
    operation_id="op-1",
    source_spbvi_id="spbvi-a",
    destination_spbvi_id="spbvi-b",
    source_account_id="origen",
    destination_account_id="destino",
    amount_cents=125_000,
    created_at=datetime(2026, 10, 4, 12, 0, tzinfo=UTC),
)
PACS002 = Pacs002Data(
    message_id="pacs002-op-1",
    original_message_id="pacs008-op-1",
    original_message_name="pacs.008.001.08",
    group_status="ACCP",
    created_at=datetime(2026, 10, 4, 12, 0, tzinfo=UTC),
)


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("ISO_GATEWAY_TOKEN", TOKEN)
    return TestClient(gateway_service.app)


@pytest.fixture
def remote(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ISO_GATEWAY_URL", "http://testserver")
    monkeypatch.setenv("ISO_GATEWAY_TOKEN", TOKEN)
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


def test_service_requires_its_token(service: TestClient) -> None:
    assert service.get("/health").json()["status"] == "ok"
    assert service.post("/pacs008", json=gateway._payload(PACS008)).status_code == 401
    wrong = service.post("/pacs008", json=gateway._payload(PACS008), headers={"authorization": "Bearer otro"})
    assert wrong.status_code == 401


def test_service_builds_valid_messages(service: TestClient) -> None:
    auth = {"authorization": f"Bearer {TOKEN}"}
    pacs008 = service.post("/pacs008", json=gateway._payload(PACS008), headers=auth)
    assert pacs008.status_code == 200
    validate_message(pacs008.json()["xml"])
    pacs002 = service.post("/pacs002", json=gateway._payload(PACS002), headers=auth)
    validate_message(pacs002.json()["xml"])
    assert service.post("/validate", json={"xml": pacs002.json()["xml"]}, headers=auth).json() == {"valid": True}
    assert service.post("/validate", json={"xml": "<x/>"}, headers=auth).json()["valid"] is False


def test_client_uses_the_remote_gateway(service: TestClient, remote) -> None:
    xml, source = gateway.pacs008_xml(PACS008, client=service)
    assert source == gateway.REMOTE
    validate_message(xml)
    assert gateway.pacs002_xml(PACS002, client=service)[1] == gateway.REMOTE


def test_client_falls_back_to_local_when_the_gateway_fails(remote) -> None:
    down = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503)))
    xml, source = gateway.pacs008_xml(PACS008, client=down)
    assert source == gateway.LOCAL
    validate_message(xml)

    invalid = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"xml": "<x/>"})))
    assert gateway.pacs002_xml(PACS002, client=invalid)[1] == gateway.LOCAL


def test_without_gateway_url_messages_are_built_in_process() -> None:
    config.get_settings.cache_clear()
    assert gateway.pacs008_xml(PACS008)[1] == gateway.LOCAL
