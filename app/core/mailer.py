from typing import Protocol

import httpx

from app.core.config import get_settings


class MailDeliveryError(RuntimeError):
    """Brevo no pudo aceptar el correo de la aplicacion."""


class Mailer(Protocol):
    def send(self, *, recipient: str, subject: str, body: str) -> None: ...


class BrevoMailer:
    def send(self, *, recipient: str, subject: str, body: str) -> None:
        settings = get_settings()
        if not settings.brevo_api_key or not settings.brevo_sender_email:
            raise MailDeliveryError("Brevo no esta configurado.")

        payload = {
            "sender": {
                "email": settings.brevo_sender_email,
                "name": settings.brevo_sender_name,
            },
            "to": [{"email": recipient}],
            "subject": subject,
            "textContent": body,
        }
        try:
            response = httpx.post(
                "https://api.brevo.com/v3/smtp/email",
                headers={
                    "accept": "application/json",
                    "api-key": settings.brevo_api_key,
                    "content-type": "application/json",
                },
                json=payload,
                timeout=10.0,
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise MailDeliveryError(
                "Brevo no pudo aceptar el mensaje."
            ) from error


def get_mailer() -> Mailer:
    return BrevoMailer()
