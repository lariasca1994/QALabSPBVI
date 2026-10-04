import httpx
import pytest

from app.core import config
from app.core.mailer import BrevoMailer, MailDeliveryError


def test_brevo_mailer_sends_api_key_only_to_brevo_https(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BREVO_API_KEY", "local-test-api-key")
    monkeypatch.setenv("BREVO_SENDER_EMAIL", "qa@example.com")
    monkeypatch.setenv("BREVO_SENDER_NAME", "QA Lab")
    config.get_settings.cache_clear()
    captured: dict[str, object] = {}

    def fake_post(url: str, **kwargs) -> httpx.Response:
        captured["url"] = url
        captured["kwargs"] = kwargs
        return httpx.Response(
            201,
            json={"messageId": "test-message"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    try:
        BrevoMailer().send(
            recipient="tester@example.com",
            subject="Test notification",
            body="Notification body",
        )
    finally:
        config.get_settings.cache_clear()

    assert captured["url"] == "https://api.brevo.com/v3/smtp/email"
    kwargs = captured["kwargs"]
    assert kwargs["headers"]["api-key"] == "local-test-api-key"
    assert kwargs["json"]["sender"] == {
        "email": "qa@example.com",
        "name": "QA Lab",
    }
    assert kwargs["json"]["to"] == [{"email": "tester@example.com"}]
    assert kwargs["timeout"] == 10.0


def test_brevo_mailer_reports_provider_failure_without_leaking_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BREVO_API_KEY", "local-test-api-key")
    monkeypatch.setenv("BREVO_SENDER_EMAIL", "qa@example.com")
    config.get_settings.cache_clear()

    def rejected_post(url: str, **kwargs) -> httpx.Response:
        return httpx.Response(
            401,
            text="private provider response",
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", rejected_post)
    try:
        with pytest.raises(MailDeliveryError) as error:
            BrevoMailer().send(
                recipient="tester@example.com",
                subject="Test notification",
                body="Notification body",
            )
    finally:
        config.get_settings.cache_clear()

    assert str(error.value) == "Brevo no pudo aceptar el mensaje."
    assert "private provider response" not in str(error.value)
    assert "local-test-api-key" not in str(error.value)
