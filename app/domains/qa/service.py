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
from app.core.email_templates import EmailMessage
from app.core.mailer import MailDeliveryError, Mailer, send_message
from app.db.models import User, UserRole
from app.domains.qa.contracts import validate_response_contract
from app.domains.qa.mongo import EXECUTION_TIMEOUT_SECONDS
from app.domains.qa.placeholders import (
    PlaceholderError,
    Resolver,
    list_epic_keys,
    record_key_result,
    record_qr_result,
    validate_placeholders,
)

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
# "code" solo cuenta como sensible si es el código MFA (campo code u otp), no un código de
# negocio como reason_code o merchant_code.
SENSITIVE_FIELD = re.compile(
    r"(password|secret|token|authorization|cookie|api.?key|access.?key|credential|private.?key"
    r"|mfa|otp|^code$|verification.?code|auth.?code)",
    re.IGNORECASE,
)
MAX_EXECUTION_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 0.5
RETRYABLE_STATUS = {502, 503, 504}
# Errores de red que justifican reintentar; los timeouts no se reintentan (ya esperaron el timeout completo).
RETRYABLE_ERRORS = {
    "ConnectError",
    "ReadError",
    "WriteError",
    "RemoteProtocolError",
    "ConnectionAbortedError",
}
# Antes de ejecutar, el ejecutor espera a que las bases del destino estén listas (DIFE en
# Azure SQL australiaeast tarda cerca de un minuto en reanudarse). Tope de 20 s por ejecución
# para no exceder el proxy de la interfaz; la interfaz ya las activa al iniciar sesión.
READY_WAIT_SECONDS = 20.0
READY_CACHE_SECONDS = 300.0
# Si no quedaron listas dentro del tope, no se vuelve a esperar enseguida: el CP decide.
NOT_READY_BACKOFF_SECONDS = 60.0
_target_ready_until = 0.0
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
        "actor_name": actor.display_name,
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


# Texto visible de cada evento QA en los correos: (etiqueta superior, encabezado).
QA_EVENT_LABELS: dict[str, tuple[str, str]] = {
    "epic_created": ("Nueva épica", "Se creó una épica"),
    "epic_members_updated": ("Integrantes de la épica", "Te asociaron a una épica"),
    "story_created": ("Nueva historia de usuario", "Se creó una HU"),
    "test_case_created": ("Nuevo caso de prueba", "Se creó un CP"),
    "task_created": ("Nueva tarea", "Se creó una tarea"),
    "test_case_executed": ("Ejecución", "Se ejecutó un caso de prueba"),
    "bug_created": ("Nuevo bug", "Se registró un bug"),
    "fix_created": ("Nuevo fix", "Se registró un fix"),
    "fix_ready_for_retest": ("Fix listo", "Un fix quedó listo para retest"),
    "bug_status_changed": ("Estado de bug", "Cambió el estado de un bug"),
    "task_status_changed": ("Estado de tarea", "Cambió el estado de una tarea"),
    "task_assigned": ("Asignación de tarea", "Te asignaron una tarea"),
    "test_case_updated": ("CP actualizado", "Se actualizó el JSON de un CP"),
    "program_imported": ("Importación", "Se importó un programa de pruebas"),
}


def qa_notification_message(notification: dict[str, Any]) -> EmailMessage:
    eyebrow, heading = QA_EVENT_LABELS.get(
        notification["event_type"], ("Gestión QA", "Hay una novedad en tu épica")
    )
    verb = "Ejecutado por" if notification["event_type"] == "test_case_executed" else "Realizado por"
    actor_name = notification.get("actor_name")
    actor = (
        f"{actor_name} ({notification['actor_email']})"
        if actor_name and actor_name != notification["actor_email"]
        else notification["actor_email"]
    )
    return EmailMessage(
        subject=f"[{notification['epic_key']}] {notification['title']}",
        eyebrow=eyebrow,
        heading=heading,
        greeting="Hola,",
        paragraphs=[f"{notification['title']}.", f"{verb} {actor}."],
        details=[
            ("Épica", notification["epic_key"]),
            ("Elemento", notification["item_key"]),
            (verb, actor),
        ],
        notice="Ingresa a QALabSPBVI para ver el detalle completo.",
    )


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
            send_message(
                mailer,
                recipient=recipient["email"],
                message=qa_notification_message(notification),
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


def _validate_case_request(
    *,
    request_method: str,
    request_path: str,
    request_query: dict[str, Any],
    request_headers: dict[str, str],
    request_body: dict[str, Any] | list[Any] | None,
    expected_status_codes: list[int],
    expected_response: dict[str, Any] | list[Any] | None,
) -> str:
    """Valida el contrato HTTP de un CP (alta y edición) y devuelve el método normalizado."""
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
    try:
        validate_placeholders(
            request_path, request_query, request_headers, request_body, expected_response
        )
    except PlaceholderError as error:
        raise QaValidationError(str(error)) from error
    return method


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
    method = _validate_case_request(
        request_method=request_method,
        request_path=request_path,
        request_query=request_query,
        request_headers=request_headers,
        request_body=request_body,
        expected_status_codes=expected_status_codes,
        expected_response=expected_response,
    )

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
    if actor.role not in {UserRole.ADMIN, UserRole.ADMINISTRADOR}:
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


def assign_task(
    database: Database,
    *,
    task_key: str,
    assignee_id: int,
    actor: User,
    mailer: Mailer,
) -> dict[str, Any]:
    if actor.role not in {UserRole.ADMIN, UserRole.ADMINISTRADOR}:
        raise QaForbiddenError
    task = _required_document(database, "work_items", task_key, "task")
    epic = _required_document(database, "epics", task["epic_key"], "epic")
    if actor.id not in {member["user_id"] for member in epic["members"]}:
        raise QaForbiddenError
    assignee = next(
        (member for member in epic["members"] if member["user_id"] == assignee_id), None
    )
    if assignee is None:
        raise QaValidationError("La persona asignada debe pertenecer a la epica.")
    notification = _new_notification(
        event_type="task_assigned",
        epic=epic,
        actor=actor,
        recipients=epic["members"],
        item_key=task_key,
        title=f"{task_key} asignada a {assignee['display_name']}",
    )
    database.work_items.update_one(
        {"_id": task["_id"]},
        {
            "$set": {
                "assignee": assignee,
                f"notifications.{notification['id']}": notification,
            },
            "$push": {
                "assignment_history": {
                    "assignee": assignee["email"],
                    "by": actor.email,
                    "at_epoch": int(time.time()),
                }
            },
        },
    )
    task = database.work_items.find_one({"_id": task["_id"]})
    task["notification_status"] = _deliver_event(
        database, "work_items", task, notification, mailer
    )
    return _response_document(task)


def update_test_case_definition(
    database: Database,
    *,
    case_key: str,
    actor: User,
    mailer: Mailer,
    request_method: str,
    request_path: str,
    request_query: dict[str, Any],
    request_headers: dict[str, str],
    request_body: dict[str, Any] | list[Any] | None,
    expected_status_codes: list[int],
    expected_response: dict[str, Any] | list[Any] | None,
    change_note: str,
) -> dict[str, Any]:
    """Reemplaza el JSON ejecutable del CP y guarda la versión anterior en el historial."""
    case = _required_document(database, "work_items", case_key, "test_case")
    epic = _required_document(database, "epics", case["epic_key"], "epic")
    if actor.id not in {member["user_id"] for member in epic["members"]}:
        raise QaForbiddenError
    method = _validate_case_request(
        request_method=request_method,
        request_path=request_path,
        request_query=request_query,
        request_headers=request_headers,
        request_body=request_body,
        expected_status_codes=expected_status_codes,
        expected_response=expected_response,
    )
    current_version = case.get("version", 1)
    previous = {
        "version": current_version,
        "request": case["request"],
        "expected_status_codes": case["expected_status_codes"],
        "expected_response": case.get("expected_response"),
        "replaced_by": _actor(actor),
        "replaced_at_epoch": int(time.time()),
        "change_note": change_note.strip(),
    }
    notification = _new_notification(
        event_type="test_case_updated",
        epic=epic,
        actor=actor,
        recipients=epic["members"],
        item_key=case_key,
        title=f"{case_key} actualizado a la versión {current_version + 1}",
    )
    updated = database.work_items.update_one(
        # Concurrencia optimista: si otra edición se guardó primero, no se pisa.
        {"_id": case["_id"], "version": case.get("version")},
        {
            "$set": {
                "version": current_version + 1,
                "request": {
                    "method": method,
                    "path": request_path,
                    "query": request_query,
                    "headers": request_headers,
                    "body": request_body,
                },
                "expected_status_codes": expected_status_codes,
                "expected_response": expected_response,
                "updated_by": _actor(actor),
                "updated_at_epoch": int(time.time()),
                f"notifications.{notification['id']}": notification,
            },
            "$push": {"versions": previous},
        },
    )
    if updated.modified_count != 1:
        raise QaConflictError
    case = database.work_items.find_one({"_id": case["_id"]})
    case["notification_status"] = _deliver_event(
        database, "work_items", case, notification, mailer
    )
    return _response_document(case)


def import_program(
    database: Database,
    *,
    epic_key: str,
    actor: User,
    mailer: Mailer,
    program: dict[str, Any],
    update_existing: bool = False,
) -> dict[str, Any]:
    """Carga HU, CP y tareas de un programa en una épica existente.

    Valida todo antes de escribir (un CP inválido rechaza el programa completo) y es
    idempotente por `ref`: lo que ya existe en la épica con esa referencia se omite. Con
    `update_existing`, los CP existentes cuyo JSON cambió se actualizan a una versión nueva
    (la anterior queda en el historial, igual que al editar un CP).
    Envía un único aviso resumen en lugar de uno por elemento.
    """
    if actor.role is not UserRole.ADMINISTRADOR:
        raise QaForbiddenError
    epic = _required_document(database, "epics", epic_key, "epic")
    if actor.id not in {member["user_id"] for member in epic["members"]}:
        raise QaForbiddenError

    refs: set[str] = set()
    for item in [
        *program["stories"],
        *(case for story in program["stories"] for case in story["test_cases"]),
        *program["tasks"],
    ]:
        if item["ref"] in refs:
            raise QaValidationError(f"La referencia {item['ref']} está repetida en el programa.")
        refs.add(item["ref"])

    validated_methods: dict[str, str] = {}
    for story in program["stories"]:
        for case in story["test_cases"]:
            try:
                validated_methods[case["ref"]] = _validate_case_request(
                    request_method=case["request_method"],
                    request_path=case["request_path"],
                    request_query=case["request_query"],
                    request_headers=case["request_headers"],
                    request_body=case["request_body"],
                    expected_status_codes=case["expected_status_codes"],
                    expected_response=case["expected_response"],
                )
            except QaValidationError as error:
                raise QaValidationError(f"{case['ref']}: {error}") from error

    existing = {
        document["external_key"]: document
        for document in database.work_items.find(
            {"epic_key": epic_key, "external_key": {"$exists": True}}
        )
    }
    created = {"stories": 0, "test_cases": 0, "tasks": 0}
    skipped = {"stories": 0, "test_cases": 0, "tasks": 0}
    updated = 0
    now = int(time.time())
    base = {
        "epic_key": epic_key,
        "created_by": _actor(actor),
        "created_at_epoch": now,
        "notifications": {},
        "imported_from": program["name"],
    }

    for story in program["stories"]:
        story_document = existing.get(story["ref"])
        if story_document is None:
            story_document = {
                **base,
                "key": _next_key(database, STORY_SEQUENCE, "HU"),
                "kind": "story",
                "external_key": story["ref"],
                "title": story["title"].strip(),
                "description": story["description"],
                "priority": story["priority"],
                "acceptance_criteria": story["acceptance_criteria"],
                "labels": story["labels"],
                "story_points": story["story_points"],
            }
            database.work_items.insert_one(story_document)
            created["stories"] += 1
        else:
            skipped["stories"] += 1
        for case in story["test_cases"]:
            if case["ref"] in existing:
                current = existing[case["ref"]]
                definition = {
                    "request": {
                        "method": validated_methods[case["ref"]],
                        "path": case["request_path"],
                        "query": case["request_query"],
                        "headers": case["request_headers"],
                        "body": case["request_body"],
                    },
                    "expected_status_codes": case["expected_status_codes"],
                    "expected_response": case["expected_response"],
                }
                changed = any(
                    current.get(field) != value for field, value in definition.items()
                )
                if not (update_existing and changed):
                    skipped["test_cases"] += 1
                    continue
                version = current.get("version", 1)
                result = database.work_items.update_one(
                    {"_id": current["_id"], "version": current.get("version")},
                    {
                        "$set": {
                            **definition,
                            "version": version + 1,
                            "title": case["title"].strip(),
                            "description": case["description"],
                            "preconditions": case["preconditions"],
                            "steps": case["steps"],
                            "expected_result": case["expected_result"],
                            "labels": case["labels"],
                            "updated_by": _actor(actor),
                            "updated_at_epoch": now,
                        },
                        "$push": {
                            "versions": {
                                "version": version,
                                "request": current["request"],
                                "expected_status_codes": current["expected_status_codes"],
                                "expected_response": current.get("expected_response"),
                                "replaced_by": _actor(actor),
                                "replaced_at_epoch": now,
                                "change_note": f"Actualizado al importar {program['name']}.",
                            }
                        },
                    },
                )
                if result.modified_count != 1:
                    raise QaConflictError
                updated += 1
                continue
            database.work_items.insert_one(
                {
                    **base,
                    "key": _next_key(database, CASE_SEQUENCE, "CP"),
                    "kind": "test_case",
                    "external_key": case["ref"],
                    "story_key": story_document["key"],
                    "title": case["title"].strip(),
                    "description": case["description"],
                    "priority": case["priority"],
                    "preconditions": case["preconditions"],
                    "steps": case["steps"],
                    "expected_result": case["expected_result"],
                    "labels": case["labels"],
                    "expected_status_codes": case["expected_status_codes"],
                    "expected_response": case["expected_response"],
                    "request": {
                        "method": validated_methods[case["ref"]],
                        "path": case["request_path"],
                        "query": case["request_query"],
                        "headers": case["request_headers"],
                        "body": case["request_body"],
                    },
                }
            )
            created["test_cases"] += 1

    for task in program["tasks"]:
        if task["ref"] in existing:
            skipped["tasks"] += 1
            continue
        database.work_items.insert_one(
            {
                **base,
                "key": _next_key(database, TASK_SEQUENCE, "TASK"),
                "kind": "task",
                "external_key": task["ref"],
                "title": task["title"].strip(),
                "description": task["description"],
                "labels": task["labels"],
                "story_points": task["story_points"],
                "status": "open",
                "assignee": None,
            }
        )
        created["tasks"] += 1

    notification = _new_notification(
        event_type="program_imported",
        epic=epic,
        actor=actor,
        recipients=epic["members"],
        item_key=epic_key,
        title=(
            f"Se importó {program['name']}: {created['stories']} HU, "
            f"{created['test_cases']} CP y {created['tasks']} tareas nuevas"
            + (f"; {updated} CP actualizados" if updated else "")
        ),
    )
    database.epics.update_one(
        {"_id": epic["_id"]},
        {"$set": {f"notifications.{notification['id']}": notification}},
    )
    epic = database.epics.find_one({"_id": epic["_id"]})
    notification_status = _deliver_event(database, "epics", epic, notification, mailer)
    return {
        "epic_key": epic_key,
        "program": program["name"],
        "created": created,
        "skipped": skipped,
        "updated_test_cases": updated,
        "notification_status": notification_status,
    }


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
    selected_keys: dict[str, str] | None = None,
) -> dict[str, Any]:
    case = _required_document(database, "work_items", case_key, "test_case")
    epic = _required_document(database, "epics", case["epic_key"], "epic")
    if actor.id not in {member["user_id"] for member in epic["members"]}:
        raise QaForbiddenError

    # Primero los marcadores dinámicos (llaves y operaciones), luego los secretos.
    resolver = Resolver(database, epic["key"], selected_keys)
    try:
        request_spec = resolver.resolve(case["request"])
        resolved_expected = resolver.resolve(case.get("expected_response"))
    except PlaceholderError as error:
        raise QaValidationError(str(error)) from error
    secret_values: set[str] = set()
    request_body = _resolve_secrets(request_spec["body"], secret_values)
    request_headers = _resolve_secrets(request_spec["headers"], secret_values)
    expected_response = (
        _resolve_secrets(resolved_expected, secret_values)
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
    http_client = client or httpx.Client(timeout=EXECUTION_TIMEOUT_SECONDS, follow_redirects=False)
    expected_codes = set(case.get("expected_status_codes", [200]))
    attempts: list[dict[str, Any]] = []
    total_started = time.perf_counter()

    def send_once() -> tuple[dict[str, Any], dict[str, Any], Any, float | None]:
        started = time.perf_counter()
        response_body_unredacted: Any = None
        retry_after: float | None = None
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
                header_retry = response.headers.get("retry-after", "")
                retry_after = float(header_retry) if header_retry.isdigit() else None
                if response_too_large:
                    result_record["error"] = "ResponseTooLarge"
        # ConnectionError: el corte de conexión cuando el transporte es en proceso.
        except (httpx.RequestError, ConnectionError) as error:
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
        return request_record, result_record, response_body_unredacted, retry_after

    _await_target_ready(http_client, headers.get("cookie"))
    try:
        # Reintentos automáticos (resiliencia): cortes de conexión y 502/503/504, salvo que el
        # CP espere ese código. Los POST de la API son idempotentes por su identificador.
        for attempt in range(1, MAX_EXECUTION_ATTEMPTS + 1):
            request_record, result_record, response_body_unredacted, retry_after = send_once()
            attempts.append(
                {
                    "attempt": attempt,
                    "status_code": result_record.get("status_code"),
                    "error": result_record.get("error"),
                    "duration_ms": result_record["duration_ms"],
                }
            )
            retryable = (
                result_record.get("error") in RETRYABLE_ERRORS
                or (
                    result_record.get("status_code") in RETRYABLE_STATUS
                    and result_record.get("status_code") not in expected_codes
                )
            )
            if not retryable or attempt == MAX_EXECUTION_ATTEMPTS:
                break
            time.sleep(min(retry_after if retry_after is not None else RETRY_BACKOFF_SECONDS * attempt, 15.0))
    finally:
        if owned_client:
            http_client.close()
    result_record["attempts"] = attempts
    result_record["total_duration_ms"] = round((time.perf_counter() - total_started) * 1000, 2)

    execution_key = _next_key(database, "execution", "RUN")
    contract = (
        {"validated": False, "valid": None, "errors": [], "schema": None}
        if "error" in result_record
        else validate_response_contract(
            request_spec["method"],
            request_spec["path"],
            result_record.get("status_code"),
            response_body_unredacted,
        )
    )
    passed = (
        result_record.get("status_code") in case.get("expected_status_codes", [200])
        and "error" not in result_record
        # Sin respuesta esperada (null) el CP valida solo el código HTTP.
        and (
            expected_response is None
            or _json_matches(response_body_unredacted, expected_response)
        )
        # Contrato OpenAPI / JSON Schema: si el código está documentado, la forma debe cumplirlo.
        and contract["valid"] is not False
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
        "contract": contract,
        "placeholders": resolver.values,
        "created_at_epoch": int(time.time()),
        "notifications": {},
    }
    resolver.commit()
    record_key_result(
        database,
        epic_key=epic["key"],
        method=request_spec["method"],
        path=request_spec["path"],
        status_code=result_record.get("status_code"),
        response_body=response_body_unredacted,
        case_key=case_key,
        execution_key=execution_key,
    )
    record_qr_result(
        database,
        epic_key=epic["key"],
        method=request_spec["method"],
        path=request_spec["path"],
        status_code=result_record.get("status_code"),
        response_body=response_body_unredacted,
    )
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


def _await_target_ready(http_client: httpx.Client, cookie: str | None) -> None:
    """Consulta /health/databases hasta que responda 200 (o venza el tope); nunca falla el CP."""
    global _target_ready_until
    if time.monotonic() < _target_ready_until:
        return
    deadline = time.monotonic() + READY_WAIT_SECONDS
    url = _target_url("/health/databases")
    request_headers = {"accept": "application/json", **({"cookie": cookie} if cookie else {})}
    while True:
        try:
            response = http_client.get(url, headers=request_headers, timeout=12.0)
        except httpx.HTTPError:
            _target_ready_until = time.monotonic() + NOT_READY_BACKOFF_SECONDS
            return
        if response.status_code == 200:
            _target_ready_until = time.monotonic() + READY_CACHE_SECONDS
            return
        if response.status_code != 503 or time.monotonic() >= deadline:
            _target_ready_until = time.monotonic() + NOT_READY_BACKOFF_SECONDS
            return
        time.sleep(3.0)


def list_epic_key_pool(
    database: Database,
    *,
    epic_key: str,
    actor: User,
    key_type: str | None = None,
    spbvi_id: str | None = None,
) -> list[dict[str, Any]]:
    """Lista de llaves creadas por los CP de la épica (para elegir en las transacciones)."""
    epic = _required_document(database, "epics", epic_key, "epic")
    if actor.id not in {member["user_id"] for member in epic["members"]}:
        raise QaForbiddenError
    return list_epic_keys(database, epic_key, key_type=key_type, spbvi_id=spbvi_id)


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
    bug = _required_document(database, "issues", bug_key, "bug")
    if actor.role is UserRole.USUARIO:
        # El usuario avanza el flujo (en fix, retest); asignar responsables es de gestión.
        if target_status == "assigned":
            raise QaForbiddenError
        bug_epic = _required_document(database, "epics", bug["epic_key"], "epic")
        if actor.id not in {member["user_id"] for member in bug_epic["members"]}:
            raise QaForbiddenError
    elif actor.role not in {UserRole.ADMIN, UserRole.ADMINISTRADOR}:
        raise QaForbiddenError
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
