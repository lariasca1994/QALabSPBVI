"""Lambda `qalabspbvi-notifier`: entrega por Brevo los avisos QA que la API encola en SQS.

Cada mensaje SQS trae un correo ya renderizado ({recipient, subject, text, html}) con la
plantilla de la aplicación. La API key de Brevo se lee de SSM Parameter Store
(SecureString) al arrancar; el remitente llega por variables de entorno.

Los mensajes que fallan se devuelven en `batchItemFailures`: SQS los reintenta y, tras
varios intentos, los mueve a la cola de mensajes fallidos (DLQ). Solo usa la librería
estándar y boto3 (incluido en el runtime de Lambda).
"""

import json
import logging
import os
import urllib.error
import urllib.request

logger = logging.getLogger()
logger.setLevel(logging.INFO)

BREVO_URL = "https://api.brevo.com/v3/smtp/email"
_api_key: str | None = None


def _brevo_api_key() -> str:
    global _api_key
    if _api_key is None:
        import boto3

        parameter = boto3.client("ssm").get_parameter(
            Name=os.environ["BREVO_API_KEY_PARAMETER"], WithDecryption=True
        )
        _api_key = parameter["Parameter"]["Value"]
    return _api_key


def _masked(email: str) -> str:
    user, _, domain = email.partition("@")
    return f"{user[:2]}***@{domain}"


def send_email(message: dict, api_key: str, opener=urllib.request.urlopen) -> None:
    payload = {
        "sender": {
            "email": os.environ["BREVO_SENDER_EMAIL"],
            "name": os.environ.get("BREVO_SENDER_NAME", "QALabSPBVI"),
        },
        "to": [{"email": message["recipient"]}],
        "subject": message["subject"],
        "textContent": message["text"],
    }
    if message.get("html"):
        payload["htmlContent"] = message["html"]
    request = urllib.request.Request(
        BREVO_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "accept": "application/json",
            "api-key": api_key,
            "content-type": "application/json",
        },
        method="POST",
    )
    with opener(request, timeout=10) as response:
        if response.status >= 300:
            raise urllib.error.HTTPError(BREVO_URL, response.status, "Brevo", None, None)


def handler(event: dict, context: object, *, api_key: str | None = None, opener=urllib.request.urlopen) -> dict:
    failures = []
    for record in event.get("Records", []):
        try:
            message = json.loads(record["body"])
            send_email(message, api_key or _brevo_api_key(), opener)
            logger.info("Aviso entregado a %s", _masked(message["recipient"]))
        except Exception:  # cualquier fallo se reintenta desde SQS
            logger.exception("No se pudo entregar el mensaje %s", record.get("messageId"))
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}
