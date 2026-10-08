"""Marcadores dinámicos de los CP: llaves Bre-B generadas, llaves elegidas e identificadores.

Se resuelven al ejecutar un CP, antes de enviar la solicitud:

- ``{{key:new:TIPO}}``: genera un valor nuevo y válido para el tipo de llave Bre-B
  (``document``, ``phone``, ``email``, ``alias``, ``merchant_code``) según el catálogo de
  ``app.domains.keys.key_types``.
- ``{{key:TIPO}}`` o ``{{key:TIPO:SPBVI}}``: valor de una llave ya creada en la épica (la
  lista de llaves), filtrada por tipo y, si se indica, por SPBVI. Por defecto se usa la más
  reciente activa; quien ejecuta puede elegir otra de la lista.
- ``{{key:last:TIPO:SPBVI}}``: la llave más reciente de ese tipo en cualquier estado
  (suspendida o eliminada incluidas), para seguir el ciclo de vida de una misma llave.
- ``{{op:NOMBRE:new}}``: genera un identificador de operación nuevo y lo guarda en la épica.
- ``{{op:NOMBRE}}``: reutiliza el último identificador generado con ese nombre (reenvíos
  idempotentes y consultas de estado).
- ``{{qr:static}}``, ``{{qr:charge}}`` y ``{{qr:charge-id}}``: contenido del último QR
  estático o dinámico generado por un CP de la épica, y el identificador de ese cobro.
- ``{{me:email}}``: correo de quien ejecuta el CP (p. ej. para registrar una llave de la
  que es titular y luego hacer acciones personales sobre ella).
- ``{{epic:key}}``: clave de la épica del CP (p. ej. EPIC-00004), para CP que operan
  sobre su propia épica sin depender de su número.

Dentro de una misma ejecución, el mismo marcador siempre resuelve al mismo valor, así que
puede aparecer en el cuerpo, la ruta y la respuesta esperada.
"""

import re
import secrets
import string
import time
import uuid
from typing import Any

from pymongo.database import Database

from app.domains.keys.key_types import KEY_TYPE_CODES, normalize_key_value

KEY_PLACEHOLDER = re.compile(r"\{\{key:(?:(new|last):)?([a-z_]+)(?::([a-z0-9-]+))?\}\}")
OP_PLACEHOLDER = re.compile(r"\{\{op:([a-z0-9-]{1,40})(:new)?\}\}")
QR_PLACEHOLDER = re.compile(r"\{\{qr:(static|charge|charge-id)\}\}")
CONTEXT_PLACEHOLDER = re.compile(r"\{\{(me:email|epic:key)\}\}")
QR_PATHS = {"/qr/static": "static", "/qr/charges": "charge"}
KEYS_PATH = re.compile(r"^/difes/([a-z0-9-]+)/keys(?:/(suspend|reactivate|owner))?$")
USABLE_KEY_STATUSES = ("confirmed", "active")


class PlaceholderError(ValueError):
    pass


def generate_key_value(key_type: str) -> str:
    """Valor aleatorio que cumple las reglas del tipo (SUPUESTO: reglas del laboratorio)."""
    digits = string.digits
    if key_type == "document":
        value = secrets.choice("123456789") + "".join(secrets.choice(digits) for _ in range(9))
    elif key_type == "phone":
        value = "3" + "".join(secrets.choice(digits) for _ in range(9))
    elif key_type == "email":
        value = f"qa.{secrets.token_hex(5)}@qalabspbvi.test"
    elif key_type == "alias":
        alphabet = string.ascii_lowercase + digits
        value = "@qa" + "".join(secrets.choice(alphabet) for _ in range(10))
    elif key_type == "merchant_code":
        value = secrets.choice("123456789") + "".join(secrets.choice(digits) for _ in range(7))
    else:
        raise PlaceholderError(f"Tipo de llave no soportado: {key_type}.")
    return normalize_key_value(key_type, value)


def _strings(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from _strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _strings(child)
    elif isinstance(value, str):
        yield value


def validate_placeholders(*values: Any) -> None:
    for text in (text for value in values for text in _strings(value)):
        for match in KEY_PLACEHOLDER.finditer(text):
            if match.group(2) not in KEY_TYPE_CODES:
                allowed = ", ".join(sorted(KEY_TYPE_CODES))
                raise PlaceholderError(
                    f"Tipo de llave desconocido en {match.group(0)}. Usa uno de: {allowed}."
                )
            if match.group(1) == "new" and match.group(3):
                raise PlaceholderError(
                    f"{match.group(0)}: una llave nueva no lleva SPBVI; va en la ruta del registro."
                )
        if (
            "{{key:" in KEY_PLACEHOLDER.sub("", text)
            or "{{op:" in OP_PLACEHOLDER.sub("", text)
            or "{{qr:" in QR_PLACEHOLDER.sub("", text)
            or any(f"{{{{{prefix}:" in CONTEXT_PLACEHOLDER.sub("", text) for prefix in ("me", "epic"))
        ):
            raise PlaceholderError(
                "Hay un marcador {{key:…}}, {{op:…}}, {{qr:…}}, {{me:…}} o {{epic:…}} mal formado en el CP."
            )


def key_requirements(*values: Any) -> list[str]:
    """Llaves de la lista que necesita el CP, como 'TIPO' o 'TIPO:SPBVI' (sin repetir)."""
    found: dict[str, None] = {}
    for text in (text for value in values for text in _strings(value)):
        for match in KEY_PLACEHOLDER.finditer(text):
            if not match.group(1):
                spec = match.group(2) + (f":{match.group(3)}" if match.group(3) else "")
                found[spec] = None
    return list(found)


def list_epic_keys(
    database: Database,
    epic_key: str,
    *,
    key_type: str | None = None,
    spbvi_id: str | None = None,
) -> list[dict[str, Any]]:
    # Las llaves eliminadas quedan en la lista (para {{key:last:…}}) pero no se ofrecen.
    query: dict[str, Any] = {"epic_key": epic_key, "status": {"$ne": "deleted"}}
    if key_type:
        query["key_type"] = key_type
    if spbvi_id:
        query["spbvi_id"] = spbvi_id
    return [
        {key: value for key, value in document.items() if key != "_id"}
        for document in database.qa_keys.find(query).sort("created_at_epoch", -1)
    ]


class Resolver:
    """Resuelve los marcadores de una ejecución con valores consistentes."""

    def __init__(
        self,
        database: Database,
        epic_key: str,
        selected_keys: dict[str, str] | None = None,
        actor_email: str | None = None,
    ) -> None:
        self.database = database
        self.epic_key = epic_key
        self.selected_keys = selected_keys or {}
        self.actor_email = actor_email
        self.values: dict[str, str] = {}
        self.new_operations: dict[str, str] = {}

    def _key(self, match: re.Match[str]) -> str:
        new, key_type, spbvi_id = match.groups()
        if new == "new":
            return generate_key_value(key_type)
        if new == "last":
            query = {"epic_key": self.epic_key, "key_type": key_type}
            if spbvi_id:
                query["spbvi_id"] = spbvi_id
            document = self.database.qa_keys.find_one(query, sort=[("touched_ns", -1), ("updated_at_epoch", -1), ("_id", -1)])
            if document is None:
                raise PlaceholderError(
                    f"No hay llaves de tipo {key_type}{':' + spbvi_id if spbvi_id else ''} en la lista de la épica."
                )
            return document["key_value"]
        spec = key_type + (f":{spbvi_id}" if spbvi_id else "")
        query: dict[str, Any] = {"epic_key": self.epic_key, "key_type": key_type}
        if spbvi_id:
            query["spbvi_id"] = spbvi_id
        chosen = self.selected_keys.get(spec)
        if chosen is not None:
            document = self.database.qa_keys.find_one({**query, "key_value": chosen})
            if document is None:
                raise PlaceholderError(
                    f"La llave elegida para {spec} no está en la lista de llaves de la épica."
                )
            return document["key_value"]
        document = self.database.qa_keys.find_one(
            {**query, "status": {"$in": list(USABLE_KEY_STATUSES)}},
            sort=[("created_at_epoch", -1), ("_id", -1)],
        )
        if document is None:
            raise PlaceholderError(
                f"No hay llaves de tipo {spec} en la lista de la épica. "
                "Ejecuta primero un CP que registre una llave de ese tipo."
            )
        return document["key_value"]

    def _operation(self, match: re.Match[str]) -> str:
        name, new = match.groups()
        if new:
            value = f"qa-{name}-{uuid.uuid4().hex[:12]}"
            self.new_operations[name] = value
            return value
        if name in self.new_operations:
            return self.new_operations[name]
        document = self.database.qa_context.find_one(
            {"epic_key": self.epic_key, "kind": "operation", "name": name}
        )
        if document is None:
            raise PlaceholderError(
                f"No hay un identificador de operación '{name}' generado todavía. "
                "Ejecuta primero el CP que lo crea."
            )
        return document["value"]

    def _context(self, match: re.Match[str]) -> str:
        if match.group(1) == "epic:key":
            return self.epic_key
        if not self.actor_email:
            raise PlaceholderError("{{me:email}} solo se resuelve al ejecutar el CP con una sesión.")
        return self.actor_email

    def _qr(self, match: re.Match[str]) -> str:
        name = match.group(1)
        kind = "static" if name == "static" else "charge"
        document = self.database.qa_context.find_one(
            {"epic_key": self.epic_key, "kind": "qr", "name": kind}
        )
        if document is None:
            raise PlaceholderError(
                f"No hay un QR {'estático' if kind == 'static' else 'dinámico'} generado todavía. "
                "Ejecuta primero el CP que lo crea."
            )
        return document["charge_id"] if name == "charge-id" else document["payload"]

    def _replace(self, pattern: re.Pattern[str], handler, text: str) -> str:
        def substitute(match: re.Match[str]) -> str:
            token = match.group(0)
            if token not in self.values:
                self.values[token] = handler(match)
            return self.values[token]

        return pattern.sub(substitute, text)

    def resolve(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: self.resolve(child) for key, child in value.items()}
        if isinstance(value, list):
            return [self.resolve(child) for child in value]
        if isinstance(value, str):
            text = self._replace(OP_PLACEHOLDER, self._operation, value)
            text = self._replace(QR_PLACEHOLDER, self._qr, text)
            text = self._replace(CONTEXT_PLACEHOLDER, self._context, text)
            return self._replace(KEY_PLACEHOLDER, self._key, text)
        return value

    def commit(self) -> None:
        """Guarda los identificadores nuevos para que otros CP los reutilicen."""
        now = int(time.time())
        for name, value in self.new_operations.items():
            self.database.qa_context.update_one(
                {"epic_key": self.epic_key, "kind": "operation", "name": name},
                {"$set": {"value": value, "updated_at_epoch": now}},
                upsert=True,
            )


def record_key_result(
    database: Database,
    *,
    epic_key: str,
    method: str,
    path: str,
    status_code: int | None,
    response_body: Any,
    case_key: str,
    execution_key: str,
) -> None:
    """Mantiene la lista de llaves de la épica según el resultado de los CP de llaves."""
    match = KEYS_PATH.fullmatch(path)
    if (
        match is None
        or status_code is None
        or not 200 <= status_code < 300
        or not isinstance(response_body, dict)
        or not {"key_type", "key_value"} <= response_body.keys()
    ):
        return
    spbvi_id = response_body.get("spbvi_id") or match.group(1)
    identity = {
        "epic_key": epic_key,
        "key_type": response_body["key_type"],
        "key_value": response_body["key_value"],
        "spbvi_id": spbvi_id,
    }
    now = int(time.time())
    # Orden exacto de los cambios (dos CP pueden caer en el mismo segundo): {{key:last:…}}.
    touched = time.time_ns()
    if method == "DELETE":
        if response_body.get("deleted"):
            database.qa_keys.update_one(identity, {"$set": {"status": "deleted", "updated_at_epoch": now, "touched_ns": touched}})
        return
    if method == "POST" and match.group(2) is None:
        database.qa_keys.update_one(
            identity,
            {
                "$set": {
                    "status": response_body.get("status", "confirmed"),
                    "deposit_product_id": response_body.get("deposit_product_id"),
                    "updated_at_epoch": now,
                    "touched_ns": touched,
                },
                "$setOnInsert": {
                    "created_at_epoch": now,
                    "case_key": case_key,
                    "execution_key": execution_key,
                },
            },
            upsert=True,
        )
        return
    if "status" in response_body:
        database.qa_keys.update_one(
            identity,
            {"$set": {"status": response_body["status"], "updated_at_epoch": now, "touched_ns": touched}},
        )


def record_qr_result(
    database: Database,
    *,
    epic_key: str,
    method: str,
    path: str,
    status_code: int | None,
    response_body: Any,
) -> None:
    """Guarda el último QR generado por un CP para que otro CP lo pague ({{qr:…}})."""
    kind = QR_PATHS.get(path)
    if (
        method != "POST"
        or kind is None
        or status_code is None
        or not 200 <= status_code < 300
        or not isinstance(response_body, dict)
        or not isinstance(response_body.get("payload"), str)
    ):
        return
    database.qa_context.update_one(
        {"epic_key": epic_key, "kind": "qr", "name": kind},
        {
            "$set": {
                "payload": response_body["payload"],
                "charge_id": response_body.get("charge_id"),
                "updated_at_epoch": int(time.time()),
            }
        },
        upsert=True,
    )
