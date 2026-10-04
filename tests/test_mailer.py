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


def test_email_template_escapes_content_and_has_no_external_resources() -> None:
    from app.core.email_templates import EmailMessage, render_email

    content = render_email(
        EmailMessage(
            subject="Prueba",
            eyebrow="Gestión QA",
            heading="<script>alert(1)</script>",
            greeting="Hola <b>Ana</b>,",
            paragraphs=["Párrafo con & y <i>etiquetas</i>."],
            details=[("Épica", "EP-1 <img src=x>")],
        )
    )

    assert "<script>" not in content.html
    assert "&lt;script&gt;" in content.html
    assert "<img" not in content.html
    assert "<b>Ana</b>" not in content.html
    for external in ("<link", "@import", "src=\"http", "url(", "<img"):
        assert external not in content.html
    assert "style=" in content.html
    assert "Hola <b>Ana</b>," in content.text
    assert "Épica: EP-1 <img src=x>" in content.text


def test_mfa_email_shows_only_code_expiry_and_do_not_share_notice() -> None:
    from app.core.email_templates import render_email
    from app.domains.auth.service import mfa_code_message

    content = render_email(mfa_code_message("Ana", "123456"))

    assert "Código: 123456" in content.text
    assert "123456" in content.html
    assert "5 minutos" in content.text
    assert "No compartas este código" in content.text


def test_brevo_mailer_sends_html_and_text_alternative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BREVO_API_KEY", "local-test-api-key")
    monkeypatch.setenv("BREVO_SENDER_EMAIL", "qa@example.com")
    config.get_settings.cache_clear()
    captured: dict[str, object] = {}

    def fake_post(url: str, **kwargs) -> httpx.Response:
        captured["json"] = kwargs["json"]
        return httpx.Response(201, json={}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    try:
        BrevoMailer().send(
            recipient="tester@example.com",
            subject="Asunto",
            body="Texto plano",
            html="<p style=\"color:#183448\">HTML</p>",
        )
    finally:
        config.get_settings.cache_clear()

    assert captured["json"]["textContent"] == "Texto plano"
    assert captured["json"]["htmlContent"].startswith("<p style=")
