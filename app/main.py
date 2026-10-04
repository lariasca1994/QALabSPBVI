from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pymongo.errors import PyMongoError

from app.api.auth_routes import router as auth_router
from app.api.qa_routes import router as qa_router
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

    application.include_router(auth_router)
    application.include_router(router)
    application.include_router(qa_router)
    return application


app = create_app()
