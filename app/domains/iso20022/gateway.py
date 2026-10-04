"""Cliente del gateway ISO 20022 desplegado en Google Cloud Run.

Con `ISO_GATEWAY_URL` configurada, los mensajes pacs.008 y pacs.002 los genera el
servicio `services/iso20022_gateway` (otra nube, otro proceso). La API vuelve a validar
el XML recibido contra los mismos XSD de laboratorio antes de devolverlo.

Si el gateway no responde a tiempo o devuelve algo inválido, el mensaje se genera en
proceso con el mismo adaptador: un pago ya liquidado nunca queda sin su XML. La
respuesta indica quién lo generó (`cloud-run` o `local`).
"""

import logging
from dataclasses import asdict
from typing import Any

import httpx

from app.core.config import get_settings
from app.domains.iso20022.messages import (
    Iso20022ValidationError,
    Pacs002Data,
    Pacs008Data,
    build_pacs002,
    build_pacs008,
    validate_message,
)

logger = logging.getLogger(__name__)
GATEWAY_TIMEOUT_SECONDS = 5.0
REMOTE = "cloud-run"
LOCAL = "local"


def _payload(data: Pacs008Data | Pacs002Data) -> dict[str, Any]:
    values = asdict(data)
    values["created_at"] = data.created_at.isoformat()
    return values


def _remote(path: str, data: Pacs008Data | Pacs002Data, client: httpx.Client | None) -> str | None:
    settings = get_settings()
    if not settings.iso_gateway_url:
        return None
    url = f"{settings.iso_gateway_url.rstrip('/')}{path}"
    try:
        owned = client is None
        http = client or httpx.Client(timeout=GATEWAY_TIMEOUT_SECONDS)
        try:
            response = http.post(
                url,
                json=_payload(data),
                headers={"authorization": f"Bearer {settings.iso_gateway_token}"},
            )
        finally:
            if owned:
                http.close()
        response.raise_for_status()
        xml = response.json()["xml"]
        validate_message(xml)
        return xml
    except (httpx.HTTPError, KeyError, TypeError, ValueError, Iso20022ValidationError):
        logger.warning("Gateway ISO 20022 no disponible en %s; se genera en proceso.", path)
        return None


def pacs008_xml(data: Pacs008Data, client: httpx.Client | None = None) -> tuple[str, str]:
    xml = _remote("/pacs008", data, client)
    return (xml, REMOTE) if xml is not None else (build_pacs008(data), LOCAL)


def pacs002_xml(data: Pacs002Data, client: httpx.Client | None = None) -> tuple[str, str]:
    xml = _remote("/pacs002", data, client)
    return (xml, REMOTE) if xml is not None else (build_pacs002(data), LOCAL)
