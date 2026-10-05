"""Consulta y exportación del registro de solicitudes (auditoría)."""

import csv
import io
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel

from app.core.security import require_roles
from app.db.models import User, UserRole
from app.domains.audit.logs import ApiLog, query_logs

router = APIRouter(prefix="/audit", tags=["auditoría"])
Manager = Annotated[User, Depends(require_roles(UserRole.ADMIN, UserRole.ADMINISTRADOR))]
EXPORT_FIELDS = (
    "created_at",
    "environment",
    "request_id",
    "method",
    "path",
    "route",
    "status_code",
    "duration_ms",
    "user_id",
    "operation_id",
)


class ApiLogResponse(BaseModel):
    id: int
    created_at: str
    environment: str
    request_id: str
    method: str
    path: str
    route: str | None
    status_code: int
    duration_ms: float
    user_id: int | None
    operation_id: str | None


def _logs(**filters) -> list[ApiLog]:
    try:
        return query_logs(**filters)
    except LookupError as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from error


def _response(item: ApiLog) -> ApiLogResponse:
    return ApiLogResponse(
        id=item.id,
        created_at=item.created_at.isoformat(timespec="seconds"),
        environment=item.environment,
        request_id=item.request_id,
        method=item.method,
        path=item.path,
        route=item.route,
        status_code=item.status_code,
        duration_ms=item.duration_ms,
        user_id=item.user_id,
        operation_id=item.operation_id,
    )


@router.get("/logs", response_model=list[ApiLogResponse])
def get_logs(
    _: Manager,
    operation_id: str | None = None,
    request_id: str | None = None,
    route: str | None = None,
    status_code: Annotated[int | None, Query(ge=100, le=599)] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> list[ApiLogResponse]:
    """Últimas solicitudes, filtrables por operación (correlación de mensajes), ruta o código."""
    return [
        _response(item)
        for item in _logs(
            operation_id=operation_id,
            request_id=request_id,
            route=route,
            status_code=status_code,
            limit=limit,
        )
    ]


@router.get("/logs/export", response_class=Response)
def export_logs(
    _: Manager,
    operation_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 1000,
) -> Response:
    """CSV para análisis externo."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(EXPORT_FIELDS)
    for item in _logs(operation_id=operation_id, limit=limit):
        row = _response(item).model_dump()
        writer.writerow([row[field] for field in EXPORT_FIELDS])
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="qalabspbvi-logs.csv"'},
    )
