"""Avisos QA: contenido del correo y canal SQS (AWS) para la Lambda de entrega."""

import json

import pytest

from app.core import config
from app.core.email_templates import render_email
from app.core.mailer import MailDeliveryError, SqsMailer, get_notification_mailer
from app.domains.qa.service import qa_notification_message


def notification(event_type: str) -> dict:
    return {
        "event_type": event_type,
        "epic_key": "EPIC-00001",
        "item_key": "RUN-00010",
        "title": "CP CP-00009 ejecutado: APROBADO",
        "actor_email": "ana@example.com",
        "actor_name": "Ana Pérez",
    }


def test_execution_email_says_who_executed_it() -> None:
    content = render_email(qa_notification_message(notification("test_case_executed")))
    assert "Ejecutado por Ana Pérez (ana@example.com)." in content.text
    assert "Ejecutado por" in content.html
    assert "integrante de la épica" not in content.text


def test_management_email_says_who_did_it() -> None:
    content = render_email(qa_notification_message(notification("task_created")))
    assert "Realizado por Ana Pérez (ana@example.com)." in content.text


class FakeSqs:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.messages: list[dict] = []

    def send_message(self, **kwargs) -> dict:
        if self.fail:
            raise RuntimeError("sin red")
        self.messages.append(kwargs)
        return {"MessageId": "1"}


def test_sqs_mailer_enqueues_the_rendered_email() -> None:
    sqs = FakeSqs()
    SqsMailer("https://sqs.example/queue", "us-east-1", client=sqs).send(
        recipient="ana@example.com", subject="Asunto", body="texto", html="<p>html</p>"
    )
    message = sqs.messages[0]
    assert message["QueueUrl"] == "https://sqs.example/queue"
    assert json.loads(message["MessageBody"]) == {
        "recipient": "ana@example.com", "subject": "Asunto", "text": "texto", "html": "<p>html</p>"
    }


def test_sqs_failure_becomes_a_retryable_delivery_error() -> None:
    with pytest.raises(MailDeliveryError):
        SqsMailer("https://sqs.example/queue", "us-east-1", client=FakeSqs(fail=True)).send(
            recipient="ana@example.com", subject="Asunto", body="texto"
        )


def test_notification_channel_uses_sqs_only_when_a_queue_is_configured(monkeypatch) -> None:
    direct = object()
    monkeypatch.setenv("NOTIFICATIONS_QUEUE_URL", "")
    config.get_settings.cache_clear()
    assert get_notification_mailer(direct) is direct

    monkeypatch.setenv("NOTIFICATIONS_QUEUE_URL", "https://sqs.example/queue")
    monkeypatch.setattr("app.core.mailer._sqs_client", lambda region: FakeSqs())
    config.get_settings.cache_clear()
    try:
        assert isinstance(get_notification_mailer(direct), SqsMailer)
    finally:
        config.get_settings.cache_clear()
