"""Middleware ASGI de auditoría: X-Request-ID y registro de cada solicitud en la base de logs.

Es ASGI puro (no BaseHTTPMiddleware) para no envolver la respuesta: los cuerpos en streaming
pasan tal cual y un corte de conexión a mitad de respuesta (mock server) sigue siendo un corte.
"""

import json
import time
from uuid import uuid4

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.domains.audit import logs as audit_logs

# La orden de pago trae el operation_id en el cuerpo; el resto, en la ruta o la consulta.
PAYMENT_ORDERS = {"/payments", "/payments/inter-spbvi"}
MAX_INSPECTED_BODY = 64 * 1024


class AuditMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {key.decode("latin-1"): value.decode("latin-1") for key, value in scope.get("headers", [])}
        request_id = (headers.get("x-request-id") or uuid4().hex)[:64]
        started = time.perf_counter()
        method = scope["method"]
        path = scope["path"]
        operation_id = None

        if method == "POST" and path in PAYMENT_ORDERS:
            # Se lee el cuerpo una vez y se reentrega igual a la aplicación.
            messages: list[Message] = []
            body = b""
            while True:
                message = await receive()
                messages.append(message)
                if message["type"] != "http.request":
                    break
                body += message.get("body", b"")
                if not message.get("more_body") or len(body) > MAX_INSPECTED_BODY:
                    break
            try:
                operation_id = str(json.loads(body).get("operation_id") or "") or None
            except (ValueError, AttributeError):
                operation_id = None
            original_receive = receive

            async def replay() -> Message:
                return messages.pop(0) if messages else await original_receive()

            receive = replay

        status_code = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                message.setdefault("headers", [])
                message["headers"] = [*message["headers"], (b"x-request-id", request_id.encode("latin-1"))]
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            query = dict(
                part.split("=", 1) for part in scope.get("query_string", b"").decode("latin-1").split("&") if "=" in part
            )
            route = scope.get("route")
            audit_logs.record(
                request_id=request_id,
                method=method,
                path=path,
                route=getattr(route, "path", None),
                status_code=status_code,
                duration_ms=audit_logs.elapsed_ms(started),
                user_id=(scope.get("state") or {}).get("user_id"),
                operation_id=operation_id
                or scope.get("path_params", {}).get("operation_id")
                # Las consultas de auditoría también se registran, pero no se asocian a la operación.
                or (None if path.startswith("/audit") else query.get("operation_id")),
            )
