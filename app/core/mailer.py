import json
from functools import lru_cache
from typing import Annotated, Any, Protocol

import httpx
from fastapi import Depends

from app.core.config import get_settings
from app.core.email_templates import EmailMessage, render_email


class MailDeliveryError(RuntimeError):
    """Brevo no pudo aceptar el correo de la aplicacion."""


class Mailer(Protocol):
    def send(
        self, *, recipient: str, subject: str, body: str, html: str | None = None
    ) -> None: ...


class BrevoMailer:
    def send(
        self, *, recipient: str, subject: str, body: str, html: str | None = None
    ) -> None:
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
        if html:
            payload["htmlContent"] = html
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


def send_message(mailer: Mailer, *, recipient: str, message: EmailMessage) -> None:
    """Envía un correo con la plantilla común: HTML con CSS inline y texto plano."""
    content = render_email(message)
    mailer.send(
        recipient=recipient,
        subject=content.subject,
        body=content.text,
        html=content.html,
    )


def get_mailer() -> Mailer:
    return BrevoMailer()


@lru_cache
def _sqs_client(region: str) -> Any:
    import boto3

    return boto3.client("sqs", region_name=region)


class SqsMailer:
    """Encola el correo en AWS SQS; la Lambda `qalabspbvi-notifier` lo entrega por Brevo.

    Las credenciales de AWS llegan por las variables estándar (AWS_ACCESS_KEY_ID y
    AWS_SECRET_ACCESS_KEY) de un usuario IAM que solo puede enviar a esta cola.
    Que SQS acepte el mensaje cuenta como entregado: los reintentos y la cola de
    mensajes fallidos (DLQ) quedan del lado de AWS.
    """

    def __init__(self, queue_url: str, region: str, client: Any = None) -> None:
        self.queue_url = queue_url
        self.client = client or _sqs_client(region)

    def send(
        self, *, recipient: str, subject: str, body: str, html: str | None = None
    ) -> None:
        message = {"recipient": recipient, "subject": subject, "text": body, "html": html}
        try:
            self.client.send_message(
                QueueUrl=self.queue_url,
                MessageBody=json.dumps(message, ensure_ascii=False),
                MessageAttributes={
                    "source": {"DataType": "String", "StringValue": "qalabspbvi"}
                },
            )
        except Exception as error:  # botocore.exceptions.* y errores de red
            raise MailDeliveryError("SQS no pudo aceptar el aviso.") from error


def get_notification_mailer(mailer: Annotated[Mailer, Depends(get_mailer)]) -> Mailer:
    """Canal de los avisos QA: SQS + Lambda si hay cola configurada, si no Brevo directo.

    El código MFA y el correo de bienvenida no pasan por aquí: salen directo por Brevo
    para no agregar la latencia de la cola a un inicio de sesión.
    """
    settings = get_settings()
    if settings.notifications_queue_url:
        return SqsMailer(settings.notifications_queue_url, settings.aws_region)
    return mailer
