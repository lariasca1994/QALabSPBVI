from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
import json
import logging
import time
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from pymongo.errors import PyMongoError
from sqlalchemy.exc import DBAPIError

from app.api.auth_routes import router as auth_router
from app.api.qa_routes import router as qa_router
from app.api.lifecycle_routes import router as lifecycle_router
from app.api.mock_routes import router as mock_router
from app.api.audit_routes import router as audit_router
from app.domains.audit import logs as audit_logs
from app.api.routes import router
from app.core.config import get_settings
from app.db.base import Base
from app.db import models  # noqa: F401
from app.db.session import engine, ensure_payment_type_column

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        Base.metadata.create_all(bind=engine)
        ensure_payment_type_column()
        yield
        audit_logs.writer.flush()

    application = FastAPI(title=settings.app_name, lifespan=lifespan)

    @application.exception_handler(PyMongoError)
    async def mongo_unavailable(_, error: PyMongoError) -> JSONResponse:
        logger.error(
            "No se pudo completar una operacion MongoDB (%s).",
            type(error).__name__,
        )
        return JSONResponse(
            status_code=503,
            content={"detail": "El almacenamiento QA no esta disponible."},
        )

    @application.exception_handler(DBAPIError)
    async def sql_unavailable(_, error: DBAPIError) -> JSONResponse:
        # Las bases gratuitas se pausan sin uso; al reanudarse pueden rechazar conexiones.
        logger.error("Base relacional no disponible (%s).", type(error.orig).__name__)
        return JSONResponse(
            status_code=503,
            content={
                "detail": "La base de datos se está activando. Reintenta en unos segundos."
            },
            headers={"Retry-After": "15"},
        )

    application.include_router(auth_router)
    application.include_router(router)
    application.include_router(qa_router)
    application.include_router(lifecycle_router)
    application.include_router(mock_router)
    application.include_router(audit_router)

    @application.middleware("http")
    async def audit_requests(request: Request, call_next):
        """Correlación (X-Request-ID) y registro asíncrono de cada solicitud en la base de logs."""
        request_id = (request.headers.get("x-request-id") or uuid4().hex)[:64]
        started = time.perf_counter()
        operation_id = None
        # La orden de pago trae el operation_id en el cuerpo; el resto, en la ruta o la consulta.
        if request.method == "POST" and request.url.path in {"/payments", "/payments/inter-spbvi"}:
            try:
                operation_id = str(json.loads(await request.body()).get("operation_id") or "") or None
            except (ValueError, AttributeError):
                operation_id = None
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        route = request.scope.get("route")
        audit_logs.record(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            route=getattr(route, "path", None),
            status_code=response.status_code,
            duration_ms=audit_logs.elapsed_ms(started),
            user_id=getattr(request.state, "user_id", None),
            operation_id=operation_id
            or request.scope.get("path_params", {}).get("operation_id")
            # Las consultas de auditoría también se registran, pero no se asocian a la operación.
            or (None if request.url.path.startswith("/audit") else request.query_params.get("operation_id")),
        )
        return response
    application.openapi = lambda: _openapi_with_business_errors(application)
    return application


def _openapi_with_business_errors(application: FastAPI) -> dict:
    """El 422 de la API tiene dos formas: validación de FastAPI (detail es una lista) y
    rechazo de negocio (detail es un texto, a veces con el mensaje ISO del rechazo). El
    contrato publicado documenta ambas para que la validación JSON Schema de los CP sea fiel."""
    if application.openapi_schema:
        return application.openapi_schema
    schema = get_openapi(title=application.title, version=application.version, routes=application.routes)
    schemas = schema.setdefault("components", {}).setdefault("schemas", {})
    schemas["BusinessError"] = {
        "title": "BusinessError",
        "type": "object",
        "required": ["detail"],
        "properties": {"detail": {"type": "string"}},
        "additionalProperties": True,
    }
    for operations in schema.get("paths", {}).values():
        for operation in operations.values():
            content = operation.get("responses", {}).get("422", {}).get("content", {}).get("application/json")
            if content and content.get("schema", {}).get("$ref", "").endswith("/HTTPValidationError"):
                content["schema"] = {
                    "anyOf": [
                        {"$ref": "#/components/schemas/HTTPValidationError"},
                        {"$ref": "#/components/schemas/BusinessError"},
                    ]
                }
    application.openapi_schema = schema
    return schema


app = create_app()
