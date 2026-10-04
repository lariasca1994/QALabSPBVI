import json
import ipaddress
import logging
import os
import re
import time
import uuid
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from dotenv import dotenv_values
from pymongo import ReturnDocument
from pymongo.database import Database
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.mailer import MailDeliveryError, Mailer
from app.db.models import User, UserRole

logger = logging.getLogger(__name__)
MAX_RESPONSE_BYTES = 1_000_000
EPIC_SEQUENCE = "epic"
STORY_SEQUENCE = "story"
CASE_SEQUENCE = "case"
TASK_SEQUENCE = "task"
ISSUE_SEQUENCE = "issue"
ALLOWED_CASE_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
SECRET_PLACEHOLDER = re.compile(r"\{\{secret:([A-Z0-9_]+)\}\}")
HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
SECRET_AUTH_HEADER = re.compile(
    r"^(?:[A-Za-z][A-Za-z0-9+.-]*\s+)?\{\{secret:[A-Z0-9_]+\}\}$"
)
SENSITIVE_FIELD = re.compile(
    r"(password|secret|token|authorization|cookie|api.?key|access.?key|credential|private.?key|mfa|code)",
    re.IGNORECASE,
)
BUG_TRANSITIONS = {
    "open": {"assigned", "in_fix"},
    "assigned": {"in_fix", "reopened"},
    "in_fix": {"ready_for_retest", "reopened"},
    "ready_for_retest": {"closed", "reopened"},
    "reopened": {"assigned", "in_fix"},
    "closed": set(),
}
TASK_TRANSITIONS = {
    "open": {"in_progress"},
    "in_progress": {"done"},
    "done": set(),
}


class QaNotFoundError(Exception):
    pass


class QaConflictError(Exception):
    pass


class QaForbiddenError(Exception):
    pass


class QaValidationError(Exception):
    pass


def _next_key(database: Database, sequence: str, prefix: str) -> str:
    counter = database.counters.find_one_and_update(
        {"_id": sequence},
        {"$inc": {"value": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return f"{prefix}-{counter['value']:05d}"


def _actor(actor: User) -> dict[str, Any]:
    return {
        "id": actor.id,
        "email": actor.email,
        "display_name": actor.display_name,
        "role": actor.role.value,
    }


def _required_document(
    database: Database, collection: str, key: str, kind: str | None = None
) -> dict[str, Any]:
    query: dict[str, Any] = {"key": key}
    if kind is not None:
        query["kind"] = kind
    document = database[collection].find_one(query)
    if document is None:
        raise QaNotFoundError
    return document


def _epic_members(
    db: Session,
    member_ids: list[int],
    actor: User,
) -> list[dict[str, Any]]:
    unique_ids = list(dict.fromkeys([*member_ids, actor.id]))
    users = db.scalars(
        select(User).where(User.id.in_(unique_ids), User.is_active.is_(True))
    ).all()
    user_by_id = {user.id: user for user in users}
    if len(user_by_id) != len(unique_ids):
        raise QaValidationError("Todos los integrantes deben ser usuarios activos.")
    return [
        {"user_id": user.id, "email": user.email, "display_name": user.display_name}
        for user in (user_by_id[user_id] for user_id in unique_ids)
    ]


def _new_notification(
    *,
    event_type: str,
    epic: dict[str, Any],
    actor: User,
    recipients: list[dict[str, Any]],
    item_key: str,
    title: str,
) -> dict[str, Any]:
    now = int(time.time())
    recipient_by_id = {recipient["user_id"]: recipient for recipient in recipients}
    recipient_by_id.setdefault(
        actor.id,
        {
            "user_id": actor.id,
            "email": actor.email,
            "display_name": actor.display_name,
        },
    )
    return {
        "id": uuid.uuid4().hex,
        "event_type": event_type,
        "epic_key": epic["key"],
        "item_key": item_key,
        "title": title,
        "actor_email": actor.email,
        "created_at_epoch": now,
        "recipients": [
            {
                "email": recipient["email"],
                "delivered": False,
                "attempts": 0,
                "last_attempt_epoch": None,
            }
            for recipient in recipient_by_id.values()
        ],
    }


def _notification_result(notification: dict[str, Any]) -> str:
    return (
        "sent"
        if all(recipient["delivered"] for recipient in notification["recipients"])
        else "pending"
    )


def _deliver_event(
    database: Database,
    collection_name: str,
    document: dict[str, Any],
    notification: dict[str, Any],
    mailer: Mailer,
) -> str:
    collection = database[collection_name]
    notification_id = notification["id"]
    for index, recipient in enumerate(notification["recipients"]):
        if recipient["delivered"]:
            continue

        now = int(time.time())
        recipient_path = f"notifications.{notification_id}.recipients.{index}"
        result = collection.update_one(
            {
                "_id": document["_id"],
                f"{recipient_path}.delivered": False,
                "$or": [
                    {f"{recipient_path}.delivery_state": {"$ne": "sending"}},
                    {
                        f"{recipient_path}.lease_expires_epoch": {
                            "$lte": now
                        }
                    },
                ],
            },
            {
                "$inc": {
                    f"{recipient_path}.attempts": 1
                },
                "$set": {
                    f"{recipient_path}.last_attempt_epoch": now,
                    f"{recipient_path}.delivery_state": "sending",
                    f"{recipient_path}.lease_expires_epoch": now + 60,
                },
            },
        )
        if result.matched_count != 1:
            continue

        try:
            mailer.send(
                recipient=recipient["email"],
                subject=f"[{notification['epic_key']}] {notification['title']}",
                body=(
                    f"Evento: {notification['event_type']}\n"
                    f"Epica: {notification['epic_key']}\n"
                    f"Elemento: {notification['item_key']}\n"
                    f"Accion realizada por: {notification['actor_email']}\n"
                    f"Detalle: {notification['title']}\n"
                ),
            )
        except MailDeliveryError:
            logger.exception(
                "Fallo el envio de notificacion QA %s a un destinatario.",
                notification_id,
            )
            collection.update_one(
                {"_id": document["_id"]},
                {
                    "$set": {
                        f"{recipient_path}.delivery_state": "pending",
                        f"{recipient_path}.lease_expires_epoch": now,
                    }
                },
            )
            continue

        collection.update_one(
            {"_id": document["_id"]},
            {
                "$set": {
                    f"{recipient_path}.delivered": True,
                    f"{recipient_path}.delivery_state": "delivered",
                    f"{recipient_path}.delivered_at_epoch": int(time.time()),
                }
            },
        )

    refreshed = collection.find_one({"_id": document["_id"]})
    notification = refreshed.get("notifications", {}).get(notification_id)
    if notification is None:
        return "sent"
    delivery_status = _notification_result(notification)
    if delivery_status == "sent":
        collection.update_one(
            {"_id": document["_id"]},
            {"$unset": {f"notifications.{notification_id}": ""}},
        )
    return delivery_status


def _response_document(document: dict[str, Any]) -> dict[str, Any]:
    result = {
        key: value
        for key, value in document.items()
        if key not in {"_id", "notifications"}
    }
    return result


def create_epic(
    database: Database,
    db: Session,
    *,
    title: str,
    description: str,
    member_ids: list[int],
    actor: User,
    mailer: Mailer,
) -> dict[str, Any]:
    if actor.role is not UserRole.ADMIN:
        raise QaForbiddenError
    members = _epic_members(db, member_ids, actor)
    key = _next_key(database, EPIC_SEQUENCE, "EPIC")
    epic = {
        "key": key,
        "kind": "epic",
        "title": title.strip(),
        "description": description.strip(),
        "members": members,
        "created_by": _actor(actor),
        "created_at_epoch": int(time.time()),
        "notifications": {},
    }
    notification = _new_notification(
        event_type="epic_created",
        epic=epic,
        actor=actor,
        recipients=members,
        item_key=key,
        title=f"Epica creada: {epic['title']}",
    )
    epic["notifications"][notification["id"]] = notification
    database.epics.insert_one(epic)
    epic["notification_status"] = _deliver_event(
        database, "epics", epic, notification, mailer
    )
    return _response_document(epic)


def update_epic_members(
    database: Database,
    db: Session,
    *,
    epic_key: str,
    member_ids: list[int],
    actor: User,
    mailer: Mailer,
) -> dict[str, Any]:
    if actor.role not in {UserRole.ADMIN, UserRole.ADMINISTRADOR}:
        raise QaForbiddenError
    epic = _required_document(database, "epics", epic_key, "epic")
    members = _epic_members(db, member_ids, actor)
    notification = _new_notification(
        event_type="epic_members_updated",
        epic=epic,
        actor=actor,
        recipients=members,
        item_key=epic_key,
        title=f"Integrantes actualizados en {epic['title']}",
    )
    updated = database.epics.update_one(
        {"_id": epic["_id"]},
        {
            "$set": {
                "members": members,
                f"notifications.{notification['id']}": notification,
            }
        },
    )
    if updated.modified_count != 1:
        raise QaConflictError
    epic["members"] = members
    epic["notifications"][notification["id"]] = notification
    epic["notification_status"] = _deliver_event(
        database, "epics", epic, notification, mailer
    )
    return _response_document(epic)


def _create_work_item(
    database: Database,
    *,
    collection_name: str,
    sequence: str,
    prefix: str,
    kind: str,
    epic: dict[str, Any],
    actor: User,
    mailer: Mailer,
    event_type: str,
    title: str,
    fields: dict[str, Any],
) -> dict[str, Any]:
    if actor.id not in {member["user_id"] for member in epic["members"]}:
        raise QaForbiddenError
    key = _next_key(database, sequence, prefix)
    item = {
        "key": key,
        "kind": kind,
        "epic_key": epic["key"],
        "title": title.strip(),
        "created_by": _actor(actor),
        "created_at_epoch": int(time.time()),
        "notifications": {},
        **fields,
    }
    notification = _new_notification(
        event_type=event_type,
        epic=epic,
        actor=actor,
        recipients=epic["members"],
        item_key=key,
        title=title.strip(),
    )
    item["notifications"][notification["id"]] = notification
    database[collection_name].insert_one(item)
    item["notification_status"] = _deliver_event(
        database, collection_name, item, notification, mailer
    )
    return _response_document(item)


def create_story(
    database: Database,
    *,
    epic_key: str,
    actor: User,
    mailer: Mailer,
    title: str,
    description: str,
    priority: str,
    acceptance_criteria: list[str],
) -> dict[str, Any]:
    if actor.role is not UserRole.ADMINISTRADOR:
        raise QaForbiddenError
    epic = _required_document(database, "epics", epic_key, "epic")
    return _create_work_item(
        database,
        collection_name="work_items",
        sequence=STORY_SEQUENCE,
        prefix="HU",
        kind="story",
        epic=epic,
        actor=actor,
        mailer=mailer,
        event_type="story_created",
        title=title,
        fields={
            "description": description,
            "priority": priority,
            "acceptance_criteria": acceptance_criteria,
        },
    )


def create_test_case(
    database: Database,
    *,
    story_key: str,
    actor: User,
    mailer: Mailer,
    title: str,
    description: str,
    priority: str,
    preconditions: list[str],
    steps: list[dict[str, str]],
    expected_result: str,
    request_method: str,
    request_path: str,
    request_query: dict[str, Any],
    request_headers: dict[str, str],
    request_body: dict[str, Any] | list[Any] | None,
    expected_status_codes: list[int],
    expected_response: dict[str, Any] | list[Any] | None,
) -> dict[str, Any]:
    if actor.role is not UserRole.ADMINISTRADOR:
        raise QaForbiddenError
    story = _required_document(database, "work_items", story_key, "story")
    epic = _required_document(database, "epics", story["epic_key"], "epic")
    method = request_method.strip().upper()
    if method not in ALLOWED_CASE_METHODS:
        raise QaValidationError("El metodo HTTP no esta permitido.")
    parsed_path = urlsplit(request_path)
    if (
        not request_path.startswith("/")
        or request_path.startswith("//")
        or "\\" in request_path
        or parsed_path.scheme
        or parsed_path.netloc
        or parsed_path.query
        or parsed_path.fragment
    ):
        raise QaValidationError("La ruta del caso debe ser relativa al servicio local.")
    if request_path.startswith("/auth/") and method not in {"GET", "HEAD", "OPTIONS"}:
        raise QaValidationError("Los casos no pueden alterar directamente la autenticacion.")
    if _contains_sensitive_field(request_body):
        raise QaValidationError(
            "El JSON del caso no puede incluir credenciales en campos; usa referencias seguras."
        )
    if _contains_sensitive_field(request_query):
        raise QaValidationError(
            "Los parametros de consulta no pueden contener credenciales o tokens."
        )
    forbidden_headers = {
        "connection",
        "content-length",
        "cookie",
        "host",
        "origin",
        "proxy-authorization",
        "transfer-encoding",
    }
    for name, value in request_headers.items():
        normalized_name = name.casefold()
        if (
            not HEADER_NAME.fullmatch(name)
            or normalized_name in forbidden_headers
            or normalized_name.startswith("proxy-")
            or "\r" in value
            or "\n" in value
            or len(value) > 4096
        ):
            raise QaValidationError("El caso contiene una cabecera HTTP no permitida.")
        if SENSITIVE_FIELD.search(name) and not SECRET_AUTH_HEADER.fullmatch(value):
            raise QaValidationError(
                "Las cabeceras sensibles solo admiten referencias secretas locales."
            )
    if _contains_sensitive_field(expected_response):
        raise QaValidationError(
            "La respuesta esperada no puede incluir credenciales en campos."
        )
    if any(code < 100 or code > 599 for code in expected_status_codes):
        raise QaValidationError("Los codigos HTTP esperados deben estar entre 100 y 599.")

    return _create_work_item(
        database,
        collection_name="work_items",
        sequence=CASE_SEQUENCE,
        prefix="CP",
        kind="test_case",
        epic=epic,
        actor=actor,
        mailer=mailer,
        event_type="test_case_created",
        title=title,
        fields={
            "story_key": story_key,
            "description": description,
            "priority": priority,
            "preconditions": preconditions,
            "steps": steps,
            "expected_result": expected_result,
            "expected_status_codes": expected_status_codes,
            "expected_response": expected_response,
            "request": {
                "method": method,
                "path": request_path,
                "query": request_query,
                "headers": request_headers,
                "body": request_body,
            },
        },
    )


def create_task(
    database: Database,
    *,
    epic_key: str,
    actor: User,
    mailer: Mailer,
    title: str,
    description: str,
    assignee_id: int | None,
) -> dict[str, Any]:
    if actor.role is not UserRole.ADMINISTRADOR:
        raise QaForbiddenError
    epic = _required_document(database, "epics", epic_key, "epic")
    assignee = next(
        (
            member
            for member in epic["members"]
            if member["user_id"] == assignee_id
        ),
        None,
    )
    if assignee_id is not None and assignee is None:
        raise QaValidationError("La persona asignada debe pertenecer a la epica.")
    return _create_work_item(
        database,
        collection_name="work_items",
        sequence=TASK_SEQUENCE,
        prefix="TASK",
        kind="task",
        epic=epic,
        actor=actor,
        mailer=mailer,
        event_type="task_created",
        title=title,
        fields={
            "description": description,
            "status": "open",
            "assignee": assignee,
        },
    )


def _contains_sensitive_field(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            (
                bool(SENSITIVE_FIELD.search(str(key)))
                and not (
                    isinstance(child, str)
                    and SECRET_PLACEHOLDER.fullmatch(child) is not None
                )
            )
            or _contains_sensitive_field(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_sensitive_field(child) for child in value)
    return False


def _resolve_secrets(value: Any, values: set[str]) -> Any:
    if isinstance(value, dict):
        return {key: _resolve_secrets(child, values) for key, child in value.items()}
    if isinstance(value, list):
        return [_resolve_secrets(child, values) for child in value]
    if isinstance(value, str):
        def replace_secret(match: re.Match[str]) -> str:
            variable_name = f"QA_SECRET_{match.group(1)}"
            secret_value = os.environ.get(variable_name)
            if not secret_value:
                secret_value = dotenv_values(".env").get(variable_name)
            if not secret_value:
                raise QaValidationError(
                    f"El secreto configurado {variable_name} no esta disponible."
                )
            values.add(secret_value)
            return secret_value
        return SECRET_PLACEHOLDER.sub(replace_secret, value)
    return value


def _redact(value: Any, secret_values: set[str] | None = None) -> Any:
    secret_values = secret_values or set()
    if isinstance(value, dict):
        return {
            key: "[REDACTADO]"
            if SENSITIVE_FIELD.search(str(key))
            else _redact(child, secret_values)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_redact(child, secret_values) for child in value]
    if isinstance(value, str):
        for secret_value in secret_values:
            if secret_value:
                value = value.replace(secret_value, "[REDACTADO]")
    return value


def _json_matches(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        return (
            isinstance(actual, dict)
            and all(
                key in actual and _json_matches(actual[key], value)
                for key, value in expected.items()
            )
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) >= len(expected)
            and all(_json_matches(actual[index], value) for index, value in enumerate(expected))
        )
    return actual == expected


def _target_url(path: str) -> str:
    settings = get_settings()
    configured_base = settings.qa_target_base_url.rstrip("/")
    parsed = urlsplit(configured_base)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise QaValidationError("QA_TARGET_BASE_URL no es una URL HTTP segura.")
    if settings.app_env.casefold() in {"local", "development", "test"}:
        try:
            loopback = ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            loopback = parsed.hostname.casefold() == "localhost"
        if not loopback:
            raise QaValidationError(
                "En desarrollo local QA_TARGET_BASE_URL debe apuntar a localhost."
            )
    return f"{configured_base}{path}"


def execute_test_case(
    database: Database,
    *,
    case_key: str,
    actor: User,
    mailer: Mailer,
    client: httpx.Client | None = None,
    cookies: dict[str, str] | None = None,
    csrf_token: str | None = None,
    incoming_host: str | None = None,
) -> dict[str, Any]:
    case = _required_document(database, "work_items", case_key, "test_case")
    epic = _required_document(database, "epics", case["epic_key"], "epic")
    if actor.id not in {member["user_id"] for member in epic["members"]}:
        raise QaForbiddenError

    request_spec = case["request"]
    secret_values: set[str] = set()
    request_body = _resolve_secrets(request_spec["body"], secret_values)
    request_headers = _resolve_secrets(request_spec["headers"], secret_values)
    expected_response = (
        _resolve_secrets(case["expected_response"], secret_values)
        if "expected_response" in case
        else None
    )
    target_url = _target_url(request_spec["path"])
    headers = {"accept": "application/json"}
    headers.update(request_headers)
    configured_host = urlsplit(get_settings().qa_target_base_url).netloc.casefold()
    if cookies and incoming_host and incoming_host.casefold() == configured_host:
        headers["cookie"] = "; ".join(f"{name}={value}" for name, value in cookies.items())
    if csrf_token and incoming_host and incoming_host.casefold() == configured_host:
        headers["x-csrf-token"] = csrf_token
    owned_client = client is None
    http_client = client or httpx.Client(timeout=15.0, follow_redirects=False)
    started = time.perf_counter()
    response_body_unredacted: Any = None
    try:
        with http_client.stream(
            request_spec["method"],
            target_url,
            params=request_spec["query"],
            json=request_body,
            headers=headers,
        ) as response:
            body_buffer = bytearray()
            response_too_large = False
            for chunk in response.iter_bytes():
                body_buffer.extend(chunk)
                if len(body_buffer) > MAX_RESPONSE_BYTES:
                    response_too_large = True
                    break

            if not response_too_large:
                try:
                    response_body_unredacted = json.loads(body_buffer)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    response_body_unredacted = body_buffer.decode(
                        "utf-8",
                        errors="replace",
                    )
            request_record = {
                "method": request_spec["method"],
                "url": urlunsplit(
                    (*urlsplit(str(response.request.url))[:3], "", "")
                ),
                "query": _redact(request_spec["query"], secret_values),
                "headers": _redact(request_spec["headers"], secret_values),
                "body": _redact(request_spec["body"], secret_values),
            }
            result_record = {
                "status_code": response.status_code,
                "body": (
                    "[RESPUESTA MAYOR A 1 MB OMITIDA]"
                    if response_too_large
                    else _redact(response_body_unredacted, secret_values)
                ),
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            }
            if response_too_large:
                result_record["error"] = "ResponseTooLarge"
    except httpx.RequestError as error:
        request_record = {
            "method": request_spec["method"],
            "url": target_url,
            "query": _redact(request_spec["query"], secret_values),
            "headers": _redact(request_spec["headers"], secret_values),
            "body": _redact(request_spec["body"], secret_values),
        }
        result_record = {
            "error": type(error).__name__,
            "status_code": None,
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        }
    finally:
        if owned_client:
            http_client.close()

    execution_key = _next_key(database, "execution", "RUN")
    passed = (
        result_record.get("status_code") in case.get("expected_status_codes", [200])
        and "error" not in result_record
        and (
            "expected_response" not in case
            or _json_matches(response_body_unredacted, expected_response)
        )
    )
    execution = {
        "key": execution_key,
        "kind": "execution",
        "epic_key": epic["key"],
        "case_key": case_key,
        "actor": _actor(actor),
        "request": request_record,
        "result": result_record,
        "passed": passed,
        "created_at_epoch": int(time.time()),
        "notifications": {},
    }
    notification = _new_notification(
        event_type="test_case_executed",
        epic=epic,
        actor=actor,
        recipients=epic["members"],
        item_key=execution_key,
        title=f"CP {case_key} ejecutado: {'APROBADO' if execution['passed'] else 'FALLIDO'}",
    )
    execution["notifications"][notification["id"]] = notification
    database.executions.insert_one(execution)
    execution["notification_status"] = _deliver_event(
        database, "executions", execution, notification, mailer
    )
    return _response_document(execution)


def list_case_executions(
    database: Database, *, case_key: str, actor: User
) -> list[dict[str, Any]]:
    case = _required_document(database, "work_items", case_key, "test_case")
    epic = _required_document(database, "epics", case["epic_key"], "epic")
    if actor.id not in {member["user_id"] for member in epic["members"]}:
        raise QaForbiddenError
    return [
        _response_document(execution)
        for execution in database.executions.find({"case_key": case_key}).sort(
            "created_at_epoch", -1
        )
    ]


def list_epic_bugs(
    database: Database,
    *,
    epic_key: str,
    actor: User,
) -> list[dict[str, Any]]:
    epic = _required_document(database, "epics", epic_key, "epic")
    if actor.id not in {member["user_id"] for member in epic["members"]}:
        raise QaForbiddenError
    return [
        _response_document(issue)
        for issue in database.issues.find({"epic_key": epic_key}).sort(
            "created_at_epoch", -1
        )
    ]


def create_bug(
    database: Database,
    *,
    case_key: str,
    execution_key: str | None,
    actor: User,
    mailer: Mailer,
    title: str,
    description: str,
    severity: str,
) -> dict[str, Any]:
    if actor.role is not UserRole.USUARIO:
        raise QaForbiddenError
    case = _required_document(database, "work_items", case_key, "test_case")
    epic = _required_document(database, "epics", case["epic_key"], "epic")
    if actor.id not in {member["user_id"] for member in epic["members"]}:
        raise QaForbiddenError
    if execution_key is not None and database.executions.find_one(
        {"key": execution_key, "case_key": case_key}
    ) is None:
        raise QaValidationError("La ejecucion debe pertenecer al caso indicado.")
    return _create_work_item(
        database,
        collection_name="issues",
        sequence=ISSUE_SEQUENCE,
        prefix="BUG",
        kind="bug",
        epic=epic,
        actor=actor,
        mailer=mailer,
        event_type="bug_created",
        title=title,
        fields={
            "case_key": case_key,
            "execution_key": execution_key,
            "description": description,
            "severity": severity,
            "status": "open",
            "fixes": [],
            "history": [
                {
                    "from": None,
                    "to": "open",
                    "actor": _actor(actor),
                    "at_epoch": int(time.time()),
                }
            ],
        },
    )


def create_fix(
    database: Database,
    *,
    bug_key: str,
    actor: User,
    mailer: Mailer,
    title: str,
    description: str,
) -> dict[str, Any]:
    if actor.role is not UserRole.USUARIO:
        raise QaForbiddenError
    bug = _required_document(database, "issues", bug_key, "bug")
    epic = _required_document(database, "epics", bug["epic_key"], "epic")
    if actor.id not in {member["user_id"] for member in epic["members"]}:
        raise QaForbiddenError
    if bug["status"] not in {"assigned", "in_fix", "reopened"}:
        raise QaConflictError
    fix_key = _next_key(database, "fix", "FIX")
    fix = {
        "key": fix_key,
        "title": title.strip(),
        "description": description.strip(),
        "status": "in_progress",
        "created_by": _actor(actor),
        "created_at_epoch": int(time.time()),
    }
    notification = _new_notification(
        event_type="fix_created",
        epic=epic,
        actor=actor,
        recipients=epic["members"],
        item_key=fix_key,
        title=f"Fix creado para {bug_key}: {fix['title']}",
    )
    bug["notifications"][notification["id"]] = notification
    bug["fixes"].append(fix)
    bug["history"].append(
        {
            "from": bug["status"],
            "to": "in_fix",
            "actor": _actor(actor),
            "at_epoch": int(time.time()),
            "fix_key": fix_key,
        }
    )
    bug["status"] = "in_fix"
    updated = database.issues.update_one(
        {"_id": bug["_id"], "status": bug["history"][-1]["from"]},
        {
            "$set": {
                "fixes": bug["fixes"],
                "status": bug["status"],
                "history": bug["history"],
                f"notifications.{notification['id']}": notification,
            }
        },
    )
    if updated.modified_count != 1:
        raise QaConflictError
    notification_status = _deliver_event(
        database, "issues", bug, notification, mailer
    )
    result = _response_document(bug)
    result["fix"] = fix
    result["notification_status"] = notification_status
    return result


def transition_fix(
    database: Database,
    *,
    bug_key: str,
    fix_key: str,
    actor: User,
    mailer: Mailer,
) -> dict[str, Any]:
    if actor.role is not UserRole.USUARIO:
        raise QaForbiddenError
    bug = _required_document(database, "issues", bug_key, "bug")
    epic = _required_document(database, "epics", bug["epic_key"], "epic")
    if actor.id not in {member["user_id"] for member in epic["members"]}:
        raise QaForbiddenError
    fix = next((item for item in bug["fixes"] if item["key"] == fix_key), None)
    if fix is None:
        raise QaNotFoundError
    if fix["status"] != "in_progress":
        raise QaConflictError

    notification = _new_notification(
        event_type="fix_ready_for_retest",
        epic=epic,
        actor=actor,
        recipients=epic["members"],
        item_key=fix_key,
        title=f"Fix listo para retest: {fix_key}",
    )
    history_event = {
        "from": fix["status"],
        "to": "ready_for_retest",
        "actor": _actor(actor),
        "at_epoch": int(time.time()),
        "fix_key": fix_key,
    }
    updated = database.issues.update_one(
        {
            "_id": bug["_id"],
            "fixes": {"$elemMatch": {"key": fix_key, "status": "in_progress"}},
        },
        {
            "$set": {
                "fixes.$.status": "ready_for_retest",
                f"notifications.{notification['id']}": notification,
            },
            "$push": {"history": history_event},
        },
    )
    if updated.modified_count != 1:
        raise QaConflictError
    fix["status"] = "ready_for_retest"
    bug["notifications"][notification["id"]] = notification
    bug["history"].append(history_event)
    bug["notification_status"] = _deliver_event(
        database, "issues", bug, notification, mailer
    )
    result = _response_document(bug)
    result["fix"] = fix
    return result


def transition_bug(
    database: Database,
    *,
    bug_key: str,
    target_status: str,
    retest_passed: bool | None,
    assignee_id: int | None,
    actor: User,
    mailer: Mailer,
) -> dict[str, Any]:
    if actor.role not in {UserRole.ADMIN, UserRole.ADMINISTRADOR}:
        raise QaForbiddenError
    bug = _required_document(database, "issues", bug_key, "bug")
    if target_status not in BUG_TRANSITIONS.get(bug["status"], set()):
        raise QaConflictError
    if target_status == "assigned":
        if assignee_id is None:
            raise QaValidationError("Debes asignar el bug a un integrante de la epica.")
        epic_for_assignment = _required_document(
            database, "epics", bug["epic_key"], "epic"
        )
        assignee = next(
            (
                member
                for member in epic_for_assignment["members"]
                if member["user_id"] == assignee_id
            ),
            None,
        )
        if assignee is None:
            raise QaValidationError("La persona asignada debe pertenecer a la epica.")
        bug["assignee"] = assignee
    elif assignee_id is not None:
        raise QaValidationError("Solo se indica responsable al asignar el bug.")
    if target_status == "ready_for_retest" and (
        not bug["fixes"]
        or any(fix["status"] != "ready_for_retest" for fix in bug["fixes"])
    ):
        raise QaConflictError
    if bug["status"] == "ready_for_retest":
        if retest_passed is None:
            raise QaValidationError("El resultado del retest es obligatorio.")
        target_status = "closed" if retest_passed else "reopened"
    elif retest_passed is not None:
        raise QaValidationError("Solo se acepta el resultado al cerrar el retest.")

    epic = _required_document(database, "epics", bug["epic_key"], "epic")
    notification = _new_notification(
        event_type="bug_status_changed",
        epic=epic,
        actor=actor,
        recipients=epic["members"],
        item_key=bug_key,
        title=f"{bug_key} cambio a {target_status}",
    )
    previous_status = bug["status"]
    history_event = {
        "from": previous_status,
        "to": target_status,
        "actor": _actor(actor),
        "at_epoch": int(time.time()),
        "retest_passed": retest_passed,
    }
    result = database.issues.update_one(
        {"_id": bug["_id"], "status": previous_status},
        {
            "$set": {
                "status": target_status,
                **({"assignee": bug["assignee"]} if target_status == "assigned" else {}),
                f"notifications.{notification['id']}": notification,
            },
            "$push": {"history": history_event},
        },
    )
    if result.modified_count != 1:
        raise QaConflictError

    bug["status"] = target_status
    bug["notifications"][notification["id"]] = notification
    bug["history"].append(history_event)
    bug["notification_status"] = _deliver_event(
        database, "issues", bug, notification, mailer
    )
    return _response_document(bug)


def transition_task(
    database: Database,
    *,
    task_key: str,
    target_status: str,
    actor: User,
    mailer: Mailer,
) -> dict[str, Any]:
    if actor.role not in {
        UserRole.ADMIN,
        UserRole.ADMINISTRADOR,
        UserRole.USUARIO,
    }:
        raise QaForbiddenError
    task = _required_document(database, "work_items", task_key, "task")
    epic = _required_document(database, "epics", task["epic_key"], "epic")
    if actor.id not in {member["user_id"] for member in epic["members"]}:
        raise QaForbiddenError
    if task.get("assignee") and task["assignee"]["user_id"] != actor.id:
        if actor.role is UserRole.USUARIO:
            raise QaForbiddenError
    previous_status = task["status"]
    if target_status not in TASK_TRANSITIONS.get(previous_status, set()):
        raise QaConflictError
    notification = _new_notification(
        event_type="task_status_changed",
        epic=epic,
        actor=actor,
        recipients=epic["members"],
        item_key=task_key,
        title=f"{task_key} cambio a {target_status}",
    )
    history_event = {
        "from": previous_status,
        "to": target_status,
        "actor": _actor(actor),
        "at_epoch": int(time.time()),
    }
    result = database.work_items.update_one(
        {"_id": task["_id"], "status": previous_status},
        {
            "$set": {
                "status": target_status,
                f"notifications.{notification['id']}": notification,
            },
            "$push": {"history": history_event},
        },
    )
    if result.modified_count != 1:
        raise QaConflictError
    task["status"] = target_status
    task.setdefault("history", []).append(history_event)
    task["notifications"][notification["id"]] = notification
    task["notification_status"] = _deliver_event(
        database, "work_items", task, notification, mailer
    )
    return _response_document(task)


def retry_pending_notifications(
    database: Database,
    *,
    actor: User,
    mailer: Mailer,
) -> dict[str, int]:
    if actor.role not in {UserRole.ADMIN, UserRole.ADMINISTRADOR}:
        raise QaForbiddenError
    results = {"delivered": 0, "pending": 0}
    for collection_name in ("epics", "work_items", "executions", "issues"):
        collection = database[collection_name]
        documents = collection.find({"notifications": {"$exists": True}})
        for document in documents:
            for notification in document.get("notifications", {}).values():
                if all(item["delivered"] for item in notification["recipients"]):
                    continue
                result = _deliver_event(
                    database,
                    collection_name,
                    document,
                    notification,
                    mailer,
                )
                results["delivered" if result == "sent" else "pending"] += 1
    return results
