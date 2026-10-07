"""Códigos QR de pago con el formato EMVCo (Merchant-Presented Mode), perfil de laboratorio.

Base pública: "Campos QR Code EMVCo – Estándar de industria EASPBV colombianas" v1.5 (2026),
que adopta EMV® QRCPS como lo exige la regulación de Bre-B. Cada campo es TLV: ID de 2
dígitos, largo de 2 dígitos y valor. Del estándar se toman:

- 00 indicador de formato ("01"); 01 tipo de QR: "11" estático, "12" dinámico. Un "11"
  con monto (tag 54) es estático híbrido: el monto no se puede editar al pagar.
- 26 llave Bre-B: subcampo 00 GUI y uno de 01 identificación, 02 celular, 03 correo,
  04 alfanumérica, 05 código de comercio.
- 49 red, 52 MCC, 53 moneda (170 = COP), 54 monto con punto y dos decimales, 58 país,
  59 nombre, 60 ciudad, 62 datos adicionales (05 referencia, 07 terminal, 08 propósito),
  80 canal, 90 identificador de la transacción del QR dinámico (1 a 35 caracteres),
  91 hash de seguridad SHA-256 y 63 CRC (ISO/IEC 13239, CRC-16/CCITT-FALSE).

SUPUESTOS DEL LABORATORIO (no son conformidad con el estándar ni con Bre-B):
- GUI propios "CO.COM.LAB.*" en lugar de los de las redes reales (RBM, CRB, ACH...).
- El tag 91 es un HMAC-SHA256 con una clave del servidor: detecta QR alterados, a
  diferencia del CRC, que solo detecta errores de lectura.
- Sin campos de impuestos (81 a 85): los pagos son entre personas o comercios del lab.
- El vencimiento del QR dinámico vive en la base de cobros, no en el QR.
"""

import hashlib
import hmac
import re
import unicodedata
from dataclasses import dataclass

from app.core.config import get_settings

GUI_KEY = "CO.COM.LAB.LLA"
GUI_NETWORK = "CO.COM.LAB.RED"
GUI_CHANNEL = "CO.COM.LAB.CANAL"
GUI_TRANSACTION = "CO.COM.LAB.TRXID"
GUI_SECURITY = "CO.COM.LAB.SEC"
NETWORK_ID = "LAB"
MCC = "0000"
CURRENCY_COP = "170"
COUNTRY = "CO"
TERMINAL = "QALAB"
PURPOSE_TRANSFER = "03"
CHANNEL = "APP"
STATIC = "11"
DYNAMIC = "12"

KEY_SUBTAGS = {"document": "01", "phone": "02", "email": "03", "alias": "04", "merchant_code": "05"}
SUBTAG_KEYS = {subtag: key_type for key_type, subtag in KEY_SUBTAGS.items()}
# El template 26 no puede pasar de 99: GUI (4 + 14) + subcampo (4 + valor).
MAX_KEY_LENGTH = 99 - (4 + len(GUI_KEY)) - 4
AMOUNT_PATTERN = re.compile(r"^(\d{1,10})\.(\d{2})$")
MAX_AMOUNT_CENTS = 99_999_999_99


class QrFormatError(ValueError):
    """El contenido no es un QR válido del laboratorio; el mensaje explica por qué."""


@dataclass(frozen=True)
class QrData:
    qr_type: str  # "static", "static_hybrid" o "dynamic"
    key_type: str
    key_value: str
    merchant_name: str
    merchant_city: str
    amount_cents: int | None = None
    reference: str | None = None
    charge_id: str | None = None


def crc16_ccitt(data: str) -> str:
    """CRC-16/CCITT-FALSE (polinomio 0x1021, inicio 0xFFFF), en 4 hexadecimales mayúsculas."""
    crc = 0xFFFF
    for byte in data.encode("utf-8"):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return f"{crc:04X}"


def _tlv(tag: str, value: str) -> str:
    if len(value) > 99:
        raise QrFormatError(f"El campo {tag} supera 99 caracteres.")
    return f"{tag}{len(value):02d}{value}"


def _ascii(text: str, limit: int) -> str:
    """EMVCo usa caracteres alfanuméricos ASCII: se quitan tildes y se recorta."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return " ".join(plain.split())[:limit]


def format_amount(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def parse_amount(text: str) -> int:
    match = AMOUNT_PATTERN.match(text)
    if not match:
        raise QrFormatError("El monto del QR debe tener punto y dos decimales, por ejemplo 1245.00.")
    cents = int(match.group(1)) * 100 + int(match.group(2))
    if cents <= 0:
        raise QrFormatError("El monto del QR debe ser mayor que cero.")
    return cents


def _signature(body: str) -> str:
    secret = get_settings().auth_secret_key or "qalab-local-qr"
    key = hmac.new(secret.encode("utf-8"), b"qalabspbvi-qr-signing", hashlib.sha256).digest()
    return hmac.new(key, body.encode("utf-8"), hashlib.sha256).hexdigest()


def build_payload(data: QrData) -> str:
    """Arma el contenido del QR con su firma (91) y su CRC (63)."""
    if data.key_type not in KEY_SUBTAGS:
        raise QrFormatError("Tipo de llave no soportado en QR.")
    if len(data.key_value) > MAX_KEY_LENGTH:
        raise QrFormatError(f"La llave supera {MAX_KEY_LENGTH} caracteres y no cabe en el QR.")
    dynamic = data.qr_type == "dynamic"
    if dynamic and not data.charge_id:
        raise QrFormatError("El QR dinámico necesita el identificador del cobro.")
    if data.amount_cents is not None and not 0 < data.amount_cents <= MAX_AMOUNT_CENTS:
        raise QrFormatError("Monto fuera de rango para el QR.")

    parts = [
        _tlv("00", "01"),
        _tlv("01", DYNAMIC if dynamic else STATIC),
        _tlv("26", _tlv("00", GUI_KEY) + _tlv(KEY_SUBTAGS[data.key_type], data.key_value)),
        _tlv("49", _tlv("00", GUI_NETWORK) + _tlv("01", NETWORK_ID)),
        _tlv("52", MCC),
        _tlv("53", CURRENCY_COP),
    ]
    if data.amount_cents is not None:
        parts.append(_tlv("54", format_amount(data.amount_cents)))
    parts += [
        _tlv("58", COUNTRY),
        _tlv("59", _ascii(data.merchant_name, 25) or "QALabSPBVI"),
        _tlv("60", _ascii(data.merchant_city, 15) or "Bogota"),
    ]
    additional = ""
    if data.reference:
        additional += _tlv("05", _ascii(data.reference, 25))
    additional += _tlv("07", TERMINAL) + _tlv("08", PURPOSE_TRANSFER)
    parts.append(_tlv("62", additional))
    parts.append(_tlv("80", _tlv("00", GUI_CHANNEL) + _tlv("01", CHANNEL)))
    if dynamic:
        parts.append(_tlv("90", _tlv("00", GUI_TRANSACTION) + _tlv("01", data.charge_id or "")))
    body = "".join(parts)
    body += _tlv("91", _tlv("00", GUI_SECURITY) + _tlv("01", _signature(body)))
    body += "6304"
    return body + crc16_ccitt(body)


def _read_tlv(text: str, where: str) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []
    position = 0
    while position < len(text):
        header = text[position:position + 4]
        if len(header) < 4 or not header.isdigit():
            raise QrFormatError(f"Estructura inválida en {where}: se esperaba ID y largo numéricos.")
        tag, length = header[:2], int(header[2:])
        value = text[position + 4:position + 4 + length]
        if len(value) != length:
            raise QrFormatError(f"El campo {tag} de {where} está truncado.")
        fields.append((tag, value))
        position += 4 + length
    return fields


def _template(value: str, tag: str) -> dict[str, str]:
    fields = _read_tlv(value, f"el campo {tag}")
    return dict(fields)


def parse_payload(payload: str) -> QrData:
    """Valida CRC, estructura, perfil y firma; devuelve los datos del QR."""
    payload = payload.strip()
    if len(payload) < 12 or payload[-8:-4] != "6304":
        raise QrFormatError("El QR no termina con el CRC (campo 63).")
    if crc16_ccitt(payload[:-4]) != payload[-4:].upper():
        raise QrFormatError("El CRC del QR no coincide: el contenido está dañado o fue modificado.")
    fields = _read_tlv(payload[:-8], "el QR")
    tags = [tag for tag, _ in fields]
    if len(tags) != len(set(tags)):
        raise QrFormatError("El QR repite campos.")
    values = dict(fields)
    if not tags or tags[0] != "00" or values["00"] != "01":
        raise QrFormatError("El QR no inicia con el indicador de formato EMVCo (00 = 01).")
    point = values.get("01")
    if point not in (STATIC, DYNAMIC):
        raise QrFormatError("El tipo de QR (campo 01) debe ser 11 (estático) o 12 (dinámico).")
    if values.get("53") != CURRENCY_COP or values.get("58") != COUNTRY:
        raise QrFormatError("El QR debe ser en pesos colombianos (53 = 170) y del país CO (58).")
    if "26" not in values:
        raise QrFormatError("El QR no trae la llave Bre-B (campo 26).")
    key_template = _template(values["26"], "26")
    if key_template.get("00") != GUI_KEY:
        raise QrFormatError("El QR es de otra red: el laboratorio solo lee QR propios (CO.COM.LAB).")
    key_fields = [(subtag, value) for subtag, value in key_template.items() if subtag in SUBTAG_KEYS]
    if len(key_fields) != 1 or not key_fields[0][1]:
        raise QrFormatError("El campo 26 debe traer exactamente una llave.")

    security = _template(values.get("91", ""), "91") if "91" in values else {}
    if security.get("00") != GUI_SECURITY or not security.get("01") or tags[-1] != "91":
        raise QrFormatError("El QR no trae el hash de seguridad (campo 91) antes del CRC.")
    signed_body = payload[: payload.index(_tlv("91", values["91"]))]
    if not hmac.compare_digest(security["01"], _signature(signed_body)):
        raise QrFormatError("La firma del QR no es válida: el QR fue alterado o no lo generó este laboratorio.")

    amount = parse_amount(values["54"]) if "54" in values else None
    charge_id = None
    if point == DYNAMIC:
        transaction = _template(values.get("90", ""), "90") if "90" in values else {}
        charge_id = transaction.get("01")
        if transaction.get("00") != GUI_TRANSACTION or not charge_id or len(charge_id) > 35:
            raise QrFormatError("El QR dinámico no trae un identificador de transacción válido (campo 90).")
        if amount is None:
            raise QrFormatError("El QR dinámico debe traer el monto (campo 54).")
    additional = _template(values["62"], "62") if "62" in values else {}
    subtag, key_value = key_fields[0]
    return QrData(
        qr_type="dynamic" if point == DYNAMIC else ("static_hybrid" if amount is not None else "static"),
        key_type=SUBTAG_KEYS[subtag],
        key_value=key_value,
        merchant_name=values.get("59", ""),
        merchant_city=values.get("60", ""),
        amount_cents=amount,
        reference=additional.get("05"),
        charge_id=charge_id,
    )
