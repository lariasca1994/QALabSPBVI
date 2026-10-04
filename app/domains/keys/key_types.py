"""Catálogo de tipos de llave Bre-B del laboratorio.

Tipos confirmados en la página pública de Banrep ("¿Cuáles tipos de Llave puedo
tener?"): número de documento de identidad (NIT para comercios), celular nacional,
correo electrónico, llave alfanumérica que inicia con @ y código de comercio.

SUPUESTO DEL LABORATORIO: Banrep no publica longitudes ni formatos exactos. Las
reglas de abajo son aproximaciones propias y deben ajustarse cuando se tenga la
especificación (anexo 6 de la Circular DSP-465 o equivalente).

Los códigos internos (`email`, `phone`, `document`, `alias`) se conservan por
compatibilidad con llaves ya registradas; `alias` es la llave alfanumérica.
"""

import re
from dataclasses import asdict, dataclass

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SEPARATORS = re.compile(r"[\s.\-()]")


@dataclass(frozen=True)
class KeyType:
    code: str
    label: str
    example: str
    hint: str


KEY_TYPES: tuple[KeyType, ...] = (
    KeyType("document", "Documento de identidad", "1023456789", "Solo números, de 5 a 15 dígitos (CC, CE, NIT sin dígito de verificación separado)."),
    KeyType("phone", "Celular", "3001234567", "Celular colombiano de 10 dígitos que inicia en 3; se acepta el prefijo +57."),
    KeyType("email", "Correo electrónico", "nombre@dominio.com", "Correo registrado en la entidad financiera."),
    KeyType("alias", "Llave alfanumérica", "@ana2026", "Inicia con @ seguida de 3 a 20 letras o números."),
    KeyType("merchant_code", "Código de comercio", "0012345", "Código del comercio o punto de venta, de 4 a 10 dígitos."),
)
KEY_TYPE_CODES = frozenset(key_type.code for key_type in KEY_TYPES)


class InvalidKeyError(ValueError):
    pass


def key_type_catalog() -> list[dict[str, str]]:
    return [asdict(key_type) for key_type in KEY_TYPES]


def normalize_key_type(value: str) -> str:
    code = value.strip().lower()
    if code not in KEY_TYPE_CODES:
        allowed = ", ".join(sorted(KEY_TYPE_CODES))
        raise InvalidKeyError(f"Tipo de llave no soportado. Usa uno de: {allowed}.")
    return code


def normalize_key_value(key_type: str, value: str) -> str:
    """Valida el valor según su tipo y lo deja en forma canónica (la que se indexa)."""
    raw = value.strip()
    if key_type == "email":
        normalized = raw.lower()
        if len(normalized) > 254 or not _EMAIL.fullmatch(normalized):
            raise InvalidKeyError("El correo no tiene un formato válido.")
        return normalized
    if key_type == "phone":
        digits = _SEPARATORS.sub("", raw)
        if digits.startswith("+57"):
            digits = digits[3:]
        elif digits.startswith("57") and len(digits) == 12:
            digits = digits[2:]
        if not re.fullmatch(r"3\d{9}", digits):
            raise InvalidKeyError("El celular debe tener 10 dígitos e iniciar en 3.")
        return digits
    if key_type == "document":
        digits = _SEPARATORS.sub("", raw)
        if not re.fullmatch(r"\d{5,15}", digits):
            raise InvalidKeyError("El documento debe tener entre 5 y 15 dígitos.")
        return digits
    if key_type == "alias":
        normalized = raw.lower()
        if not re.fullmatch(r"@[a-z0-9]{3,20}", normalized):
            raise InvalidKeyError(
                "La llave alfanumérica inicia con @ y tiene de 3 a 20 letras o números."
            )
        return normalized
    if key_type == "merchant_code":
        if not re.fullmatch(r"\d{4,10}", raw):
            raise InvalidKeyError("El código de comercio debe tener entre 4 y 10 dígitos.")
        return raw
    raise InvalidKeyError("Tipo de llave no soportado.")
