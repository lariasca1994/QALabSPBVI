"""Registro de solicitudes de la API en una base dedicada (Neon en la nube).

Cada solicitud deja una fila con método, ruta, plantilla de ruta, código, duración, usuario,
identificador de correlación (X-Request-ID) y el operation_id cuando la solicitud pertenece a
un pago: así se correlacionan pacs.008, pacs.002, devoluciones y cancelaciones de una misma
operación. La escritura es asíncrona y por lotes en un hilo propio: la API nunca espera a la
base de logs, y si esta no responde (por ejemplo, mientras Neon se activa) solo se pierde el
lote, nunca la solicitud. /health no se registra para no despertar la base con los chequeos.
"""

import logging
import queue
import threading
import time
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from sqlalchemy import BigInteger, DateTime, Float, Integer, String, create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

logger = logging.getLogger(__name__)
SKIPPED_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}
BATCH_SIZE = 50
FLUSH_SECONDS = 2.0
MAX_QUEUE = 5000


class LogsBase(DeclarativeBase):
    pass


class ApiLog(LogsBase):
    __tablename__ = "api_logs"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    environment: Mapped[str] = mapped_column(String(20), nullable=False)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    route: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    operation_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)


@lru_cache
def logs_engine() -> Engine | None:
    url = get_settings().logs_database_url
    if not url:
        return None
    options: dict[str, Any] = {"poolclass": NullPool}
    if url.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False}
    engine = create_engine(url, **options)
    return engine


class LogWriter:
    """Cola en memoria + hilo que escribe por lotes. Si la cola se llena, descarta."""

    def __init__(self) -> None:
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=MAX_QUEUE)
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._schema_ready = False

    def submit(self, entry: dict[str, Any]) -> None:
        if logs_engine() is None:
            return
        try:
            self._queue.put_nowait(entry)
        except queue.Full:
            return
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="api-log-writer", daemon=True)
                self._thread.start()

    def _run(self) -> None:
        while True:
            batch = self._take(timeout=FLUSH_SECONDS)
            if batch is None:
                return
            if batch:
                self._write(batch)

    def _take(self, timeout: float) -> list[dict[str, Any]] | None:
        try:
            first = self._queue.get(timeout=timeout)
        except queue.Empty:
            return None  # Sin tráfico: el hilo termina y se recrea con la próxima solicitud.
        batch = [first]
        while len(batch) < BATCH_SIZE:
            try:
                batch.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return batch

    def _write(self, batch: list[dict[str, Any]]) -> None:
        engine = logs_engine()
        if engine is None:
            return
        try:
            if not self._schema_ready:
                LogsBase.metadata.create_all(engine)
                self._schema_ready = True
            with Session(engine) as session:
                session.add_all(ApiLog(**entry) for entry in batch)
                session.commit()
        except Exception as error:  # noqa: BLE001 - los logs nunca rompen la API
            logger.warning("No se pudo escribir un lote de %s logs (%s).", len(batch), type(error).__name__)

    def flush(self) -> None:
        """Escribe lo pendiente (al apagar la API y en las pruebas)."""
        while True:
            batch = self._take(timeout=0.01)
            if not batch:
                return
            self._write(batch)


writer = LogWriter()


def record(
    *,
    request_id: str,
    method: str,
    path: str,
    route: str | None,
    status_code: int,
    duration_ms: float,
    user_id: int | None,
    operation_id: str | None,
) -> None:
    if path in SKIPPED_PATHS or method == "OPTIONS":
        return
    writer.submit(
        {
            "created_at": datetime.now(UTC),
            "environment": get_settings().app_env[:20],
            "request_id": request_id[:64],
            "method": method[:10],
            "path": path[:500],
            "route": route[:200] if route else None,
            "status_code": status_code,
            "duration_ms": round(duration_ms, 2),
            "user_id": user_id,
            "operation_id": operation_id[:100] if operation_id else None,
        }
    )


def query_logs(
    *,
    operation_id: str | None = None,
    request_id: str | None = None,
    route: str | None = None,
    status_code: int | None = None,
    limit: int = 200,
) -> list[ApiLog]:
    engine = logs_engine()
    if engine is None:
        raise LookupError("El registro de logs no está configurado.")
    writer.flush()
    LogsBase.metadata.create_all(engine)
    statement = select(ApiLog).order_by(ApiLog.id.desc()).limit(limit)
    if operation_id:
        statement = statement.where(ApiLog.operation_id == operation_id)
    if request_id:
        statement = statement.where(ApiLog.request_id == request_id)
    if route:
        statement = statement.where(ApiLog.route == route)
    if status_code is not None:
        statement = statement.where(ApiLog.status_code == status_code)
    with Session(engine) as session:
        return list(session.scalars(statement))


def elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000
