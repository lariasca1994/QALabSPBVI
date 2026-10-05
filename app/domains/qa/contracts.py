"""Validación de contrato: la respuesta de cada CP se compara con el JSON Schema de OpenAPI.

FastAPI publica el contrato de cada endpoint (OpenAPI 3.1, que usa JSON Schema 2020-12). El
ejecutor de CP busca la operación por método y ruta, toma el esquema del código HTTP recibido
y valida el cuerpo. Así un CP no solo revisa los campos que espera, sino toda la forma de la
respuesta (tipos, obligatorios, enumeraciones). Si el código no está documentado (por ejemplo,
un 404 genérico), no hay contrato que validar y se informa como "sin esquema".
"""

import re
from functools import lru_cache
from typing import Any

from jsonschema import Draft202012Validator

MAX_ERRORS = 5


@lru_cache(maxsize=1)
def _openapi() -> dict[str, Any]:
    from app.main import app  # Importación diferida: app.main importa las rutas QA.

    return app.openapi()


@lru_cache(maxsize=1)
def _routes() -> list[tuple[re.Pattern[str], str, int]]:
    routes = []
    for template in _openapi().get("paths", {}):
        parameters = template.count("{")
        pattern = re.compile("^" + re.sub(r"\{[^/]+\}", "[^/]+", re.escape(template).replace(r"\{", "{").replace(r"\}", "}")) + "$")
        routes.append((pattern, template, parameters))
    # Las rutas literales (/payments/inter-spbvi) ganan sobre las parametrizadas.
    return sorted(routes, key=lambda item: item[2])


def _operation(method: str, path: str) -> tuple[str, dict[str, Any]] | None:
    paths = _openapi().get("paths", {})
    for pattern, template, _ in _routes():
        if pattern.match(path) and method.lower() in paths[template]:
            return template, paths[template][method.lower()]
    return None


def validate_response_contract(method: str, path: str, status_code: int | None, body: Any) -> dict[str, Any]:
    """Resultado: validated (había esquema), valid y hasta cinco errores legibles."""
    result: dict[str, Any] = {"validated": False, "valid": None, "errors": [], "schema": None}
    if status_code is None:
        return result
    found = _operation(method, path.split("?", 1)[0])
    if found is None:
        result["errors"] = ["La ruta no está en el contrato OpenAPI de la API."]
        return result
    template, operation = found
    response = operation.get("responses", {}).get(str(status_code))
    schema = (response or {}).get("content", {}).get("application/json", {}).get("schema")
    result["schema"] = f"{method.upper()} {template} → {status_code}"
    if not schema:
        return result
    # Los $ref (#/components/schemas/…) se resuelven contra el mismo documento OpenAPI.
    document = {**schema, "components": _openapi().get("components", {})}
    errors = sorted(Draft202012Validator(document).iter_errors(body), key=lambda error: list(error.absolute_path))
    result["validated"] = True
    result["valid"] = not errors
    result["errors"] = [
        f"{'/'.join(str(part) for part in error.absolute_path) or '(raíz)'}: {error.message}"
        for error in errors[:MAX_ERRORS]
    ]
    return result
