from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pymongo.errors import PyMongoError
from sqlalchemy.exc import DBAPIError

from app.api.auth_routes import router as auth_router
from app.api.qa_routes import router as qa_router
from app.api.lifecycle_routes import router as lifecycle_router
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
    return application


app = create_app()
