"""Activación bajo demanda de las bases que se pausan solas (capas gratuitas).

Azure SQL (oferta gratuita, DIFE) se pausa tras 60 minutos sin sesiones y tarda cerca de
un minuto en reanudarse; mientras tanto rechaza conexiones con el error 40613. Para que
la base se active cuando alguien entra a la aplicación (y no antes, ni por monitores):

- `/auth/me` (lo llama la interfaz al cargar, solo con sesión válida) agenda
  `wake_key_stores` en segundo plano: abre y cierra una conexión a DIFE y a DICE.
- `retry_transient_connect` reintenta la conexión mientras Azure SQL se reanuda.

Nada de esto corre en `/health` ni en procesos de fondo: sin uso, las bases duermen.
"""

import logging
import threading
import time

from sqlalchemy import Engine, event, text

logger = logging.getLogger(__name__)

# Errores de Azure SQL mientras la base se reanuda o no está disponible un momento.
TRANSIENT_MARKERS = ("40613", "40197", "40501", "49918", "HYT00", "08S01", "Login timeout")
CONNECT_ATTEMPTS = 4
CONNECT_BACKOFF_SECONDS = 5.0
WAKE_INTERVAL_SECONDS = 300

_last_wake = 0.0
_wake_lock = threading.Lock()


def _is_transient(error: Exception) -> bool:
    message = str(error)
    return any(marker in message for marker in TRANSIENT_MARKERS)


def retry_transient_connect(
    engine: Engine,
    attempts: int = CONNECT_ATTEMPTS,
    backoff: float = CONNECT_BACKOFF_SECONDS,
) -> Engine:
    """Reintenta la conexión DBAPI ante errores transitorios (base reanudándose)."""

    @event.listens_for(engine, "do_connect")
    def connect_with_retry(dialect, conn_rec, cargs, cparams):
        for attempt in range(1, attempts + 1):
            try:
                return dialect.loaded_dbapi.connect(*cargs, **cparams)
            except Exception as error:
                if attempt == attempts or not _is_transient(error):
                    raise
                logger.info("Base reanudándose; reintento %s de %s.", attempt + 1, attempts)
                time.sleep(backoff)
        return None  # inalcanzable

    return engine


def wake_key_stores() -> None:
    """Abre y cierra una conexión a DIFE y DICE para que se reanuden (como mucho cada 5 min)."""
    global _last_wake
    with _wake_lock:
        if time.monotonic() - _last_wake < WAKE_INTERVAL_SECONDS and _last_wake:
            return
        _last_wake = time.monotonic()
    from app.domains.keys.persistence import get_dice_engine, get_dife_engine

    for name, factory in (("DIFE", get_dife_engine), ("DICE", get_dice_engine)):
        try:
            with factory().connect() as connection:
                connection.execute(text("SELECT 1" if name == "DIFE" else "SELECT 1 FROM DUAL"))
        except Exception as error:  # la activación es de mejor esfuerzo
            logger.info("No se pudo activar %s todavía (%s).", name, type(error).__name__)


READY_PROBE_SECONDS = 8.0


def check_databases(timeout: float = READY_PROBE_SECONDS) -> dict[str, str]:
    """Estado de pagos, DIFE, DICE y cobros QR: "lista" o "activando".

    Cada prueba corre en su propio hilo; si no termina a tiempo (Azure SQL en australiaeast
    puede tardar cerca de un minuto en reanudarse), la base se informa "activando" y la
    prueba sigue en segundo plano, así la próxima consulta la encuentra despierta.
    """
    from concurrent.futures import ThreadPoolExecutor, wait

    from app.db.session import engine as payments_engine
    from app.domains.keys.persistence import get_dice_engine, get_dife_engine
    from app.domains.qr.persistence import get_qr_engine

    probes = {
        "pagos": (lambda: payments_engine, "SELECT 1"),
        "dife": (get_dife_engine, "SELECT 1"),
        "dice": (get_dice_engine, "SELECT 1 FROM DUAL"),
        "qr": (get_qr_engine, "SELECT 1"),
    }

    def probe(factory, statement: str) -> None:
        engine = factory()
        sql = "SELECT 1" if engine.dialect.name != "oracle" else statement
        with engine.connect() as connection:
            connection.execute(text(sql))

    pool = ThreadPoolExecutor(max_workers=len(probes), thread_name_prefix="db-ready")
    futures = {name: pool.submit(probe, *spec) for name, spec in probes.items()}
    wait(futures.values(), timeout=timeout)
    pool.shutdown(wait=False)
    status = {}
    for name, future in futures.items():
        if not future.done():
            status[name] = "activando"
        elif future.exception() is not None:
            logger.info("%s todavía no responde (%s).", name, type(future.exception()).__name__)
            status[name] = "activando"
        else:
            status[name] = "lista"
    return status
