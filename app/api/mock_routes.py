"""Mock server de fallos de red para las HU de resiliencia.

Simula lo que un CP no puede provocar contra la API real: latencia, el timeout de un servicio
aguas arriba (respondido como 504 por un gateway), errores 4xx/5xx, cortes de conexión y un
escenario inestable que falla N veces antes de responder. El ejecutor de CP reintenta los
cortes y los 502/503/504 y registra cada intento, así se validan los reintentos automáticos.

SUPUESTO: vive dentro de la API para no sumar infraestructura; solo lo usan usuarios
autenticados y los tiempos están acotados para no retener conexiones.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import require_roles
from app.db.models import MockScenario, User, UserRole
from app.db.session import get_db

# 422 como número: Starlette renombró la constante (UNPROCESSABLE_ENTITY → UNPROCESSABLE_CONTENT).
HTTP_422 = 422
router = APIRouter(prefix="/mock", tags=["mock server de resiliencia"])
AnyRole = Annotated[
    User,
    Depends(require_roles(UserRole.ADMIN, UserRole.ADMINISTRADOR, UserRole.USUARIO)),
]
MAX_DELAY_MS = 10_000
SIMULATED_STATUS = {
    400: "Solicitud inválida (simulada).",
    401: "Sin autenticación (simulada).",
    403: "Sin permisos (simulada).",
    404: "Recurso no encontrado (simulado).",
    408: "Tiempo de espera de la solicitud agotado (simulado).",
    429: "Demasiadas solicitudes (simulado).",
    500: "Error interno (simulado).",
    502: "Respuesta inválida del servicio aguas arriba (simulada).",
    503: "Servicio no disponible (simulado).",
    504: "El servicio aguas arriba no respondió a tiempo (simulado).",
}


class MockResponse(BaseModel):
    simulated: bool = True
    scenario: str
    delay_ms: int = 0
    attempts: int | None = None


class MockError(BaseModel):
    detail: str
    simulated: bool = True
    scenario: str


def _error(code: int, scenario: str) -> JSONResponse:
    headers = {"Retry-After": "1"} if code in {429, 503} else None
    body = MockError(detail=SIMULATED_STATUS[code], scenario=scenario)
    return JSONResponse(status_code=code, content=body.model_dump(), headers=headers)


async def _dropped_connection() -> AsyncIterator[bytes]:
    """Envía parte del cuerpo y corta: el cliente ve una desconexión, no un código HTTP."""
    yield b'{"simulated": true, "scenario": "disconn'
    await asyncio.sleep(0)
    raise ConnectionAbortedError("Corte de conexión simulado por el mock server.")


@router.get("/latency", response_model=MockResponse)
async def mock_latency(
    _: AnyRole,
    delay_ms: Annotated[int, Query(ge=0, le=MAX_DELAY_MS)] = 1000,
) -> MockResponse:
    """Responde 200 después de la latencia indicada (máximo 10 s)."""
    await asyncio.sleep(delay_ms / 1000)
    return MockResponse(scenario="latency", delay_ms=delay_ms)


@router.get(
    "/upstream",
    response_model=MockResponse,
    responses={504: {"model": MockError}},
)
async def mock_upstream(
    _: AnyRole,
    latency_ms: Annotated[int, Query(ge=0, le=60_000)] = 0,
    timeout_ms: Annotated[int, Query(ge=100, le=MAX_DELAY_MS)] = 5000,
) -> MockResponse | JSONResponse:
    """Gateway hacia un servicio lento: si la latencia supera el timeout, responde 504 al vencerlo."""
    if latency_ms > timeout_ms:
        await asyncio.sleep(timeout_ms / 1000)
        return _error(504, "upstream-timeout")
    await asyncio.sleep(latency_ms / 1000)
    return MockResponse(scenario="upstream", delay_ms=latency_ms)


@router.get(
    "/status/{code}",
    responses={code: {"model": MockError} for code in SIMULATED_STATUS},
)
def mock_status(code: int, _: AnyRole) -> JSONResponse:
    """Responde el código de error pedido (4xx/5xx de la lista), con Retry-After en 429 y 503."""
    if code not in SIMULATED_STATUS:
        raise HTTPException(
            HTTP_422,
            f"Código no simulado. Usa uno de: {', '.join(map(str, SIMULATED_STATUS))}.",
        )
    return _error(code, "status")


@router.get("/disconnect")
def mock_disconnect(_: AnyRole) -> StreamingResponse:
    """Corta la conexión a mitad de la respuesta."""
    return StreamingResponse(_dropped_connection(), media_type="application/json")


def _count_call(db: Session, scenario_id: str) -> int:
    try:
        db.add(MockScenario(scenario_id=scenario_id, calls=1))
        db.commit()
        return 1
    except IntegrityError:
        db.rollback()
    db.execute(
        update(MockScenario)
        .where(MockScenario.scenario_id == scenario_id)
        .values(calls=MockScenario.calls + 1)
    )
    db.commit()
    return int(db.scalar(select(MockScenario.calls).where(MockScenario.scenario_id == scenario_id)))


@router.get(
    "/flaky/{scenario_id}",
    response_model=MockResponse,
    responses={502: {"model": MockError}, 503: {"model": MockError}, 504: {"model": MockError}},
)
def mock_flaky(
    _: AnyRole,
    db: Annotated[Session, Depends(get_db)],
    scenario_id: Annotated[str, Path(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")],
    failures: Annotated[int, Query(ge=0, le=5)] = 2,
    mode: Literal["503", "502", "504", "disconnect"] = "503",
) -> MockResponse | JSONResponse | StreamingResponse:
    """Las primeras `failures` llamadas del escenario fallan (código o corte); después responde 200."""
    calls = _count_call(db, scenario_id)
    if calls <= failures:
        if mode == "disconnect":
            return StreamingResponse(_dropped_connection(), media_type="application/json")
        return _error(int(mode), f"flaky:{scenario_id}")
    return MockResponse(scenario=f"flaky:{scenario_id}", attempts=calls)
