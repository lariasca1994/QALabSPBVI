"""Lambda de entrega de avisos: llama a Brevo y devuelve los fallidos a SQS."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "services" / "notifier"))

import handler as notifier  # noqa: E402


class Response:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None


def record(message_id: str, recipient: str) -> dict:
    body = {"recipient": recipient, "subject": "Asunto", "text": "texto", "html": "<p>x</p>"}
    return {"messageId": message_id, "body": json.dumps(body)}


def test_delivers_each_message_and_reports_only_failures(monkeypatch) -> None:
    monkeypatch.setenv("BREVO_SENDER_EMAIL", "lab@example.com")
    sent = []

    def opener(request, timeout):
        payload = json.loads(request.data)
        if payload["to"][0]["email"] == "falla@example.com":
            raise OSError("Brevo caído")
        sent.append((request.headers["Api-key"], payload))
        return Response(201)

    result = notifier.handler(
        {"Records": [record("1", "ana@example.com"), record("2", "falla@example.com")]},
        None,
        api_key="clave",
        opener=opener,
    )
    assert result == {"batchItemFailures": [{"itemIdentifier": "2"}]}
    assert sent[0][0] == "clave"
    assert sent[0][1]["sender"] == {"email": "lab@example.com", "name": "Laboratorio de pagos inmediatos"}
    assert sent[0][1]["htmlContent"] == "<p>x</p>"


def test_malformed_message_is_returned_for_retry(monkeypatch) -> None:
    monkeypatch.setenv("BREVO_SENDER_EMAIL", "lab@example.com")
    result = notifier.handler({"Records": [{"messageId": "9", "body": "no-json"}]}, None, api_key="k")
    assert result == {"batchItemFailures": [{"itemIdentifier": "9"}]}


def test_legacy_or_empty_sender_name_becomes_the_portal_name(monkeypatch) -> None:
    monkeypatch.setenv("BREVO_SENDER_EMAIL", "lab@example.com")
    sent = []

    def opener(request, timeout):
        sent.append(json.loads(request.data))
        return Response(201)

    for value in ("QALabSPBVI", ""):
        monkeypatch.setenv("BREVO_SENDER_NAME", value)
        notifier.handler({"Records": [record("1", "ana@example.com")]}, None, api_key="k", opener=opener)
    monkeypatch.setenv("BREVO_SENDER_NAME", "Otro remitente")
    notifier.handler({"Records": [record("2", "ana@example.com")]}, None, api_key="k", opener=opener)
    assert [payload["sender"]["name"] for payload in sent] == [
        "Laboratorio de pagos inmediatos", "Laboratorio de pagos inmediatos", "Otro remitente",
    ]
