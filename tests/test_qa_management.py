from collections.abc import Iterator

import httpx
import mongomock
import pytest
from fastapi import Header
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.mailer import get_mailer
from app.core.mailer import MailDeliveryError
from app.core.security import current_user
from app.db.base import Base
from app.db.models import User, UserRole
from app.db.session import get_db
from app.domains.qa.mongo import get_qa_database, get_qa_http_client
from app.main import app


class FakeMailer:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.messages: list[tuple[str, str, str]] = []
        self.requests: list[httpx.Request] = []

    def send(
        self, *, recipient: str, subject: str, body: str, html: str | None = None
    ) -> None:
        if self.fail:
            raise MailDeliveryError("Local test mailer unavailable.")
        self.messages.append((recipient, subject, body))


@pytest.fixture
def qa_environment() -> Iterator[tuple[TestClient, mongomock.database.Database, FakeMailer]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    mongo_client = mongomock.MongoClient()
    database = mongo_client["qa_test"]
    mailer = FakeMailer()
    users = {
        "admin": User(
            id=1,
            email="admin@example.com",
            display_name="Admin",
            password_hash="test",
            role=UserRole.ADMIN,
            is_active=True,
        ),
        "administrador": User(
            id=2,
            email="manager@example.com",
            display_name="Manager",
            password_hash="test",
            role=UserRole.ADMINISTRADOR,
            is_active=True,
        ),
        "usuario": User(
            id=3,
            email="tester@example.com",
            display_name="Tester",
            password_hash="test",
            role=UserRole.USUARIO,
            is_active=True,
        ),
        "externo": User(
            id=4,
            email="outside@example.com",
            display_name="Outside",
            password_hash="test",
            role=UserRole.USUARIO,
            is_active=True,
        ),
    }

    with Session(engine, expire_on_commit=False) as db:
        db.add_all(users.values())
        db.commit()

    def override_db():
        with Session(engine) as db:
            yield db

    def override_user(x_test_role: str = Header(default="admin")) -> User:
        return users[x_test_role]

    def override_http_client():
        def respond(request: httpx.Request) -> httpx.Response:
            mailer.requests.append(request)
            body = (
                {"accepted": True, "echo": "local-secret-token"}
                if (
                    b"local-secret-token" in request.content
                    or request.headers.get("Authorization") == "Bearer local-secret-token"
                )
                else {"accepted": True}
            )
            return httpx.Response(
                201,
                json={**body, "Authorization": "local-token"},
            )

        with httpx.Client(
            transport=httpx.MockTransport(respond),
            follow_redirects=False,
        ) as client:
            yield client

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[current_user] = override_user
    app.dependency_overrides[get_mailer] = lambda: mailer
    app.dependency_overrides[get_qa_database] = lambda: database
    app.dependency_overrides[get_qa_http_client] = override_http_client
    client = TestClient(app)
    client.cookies.set("qalab_csrf", "test-csrf-token")
    client.headers["x-csrf-token"] = "test-csrf-token"
    client.headers["x-test-role"] = "admin"
    try:
        yield client, database, mailer
    finally:
        client.close()
        app.dependency_overrides.clear()
        mongo_client.drop_database("qa_test")
        mongo_client.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def create_epic(client: TestClient) -> str:
    response = client.post(
        "/qa/epics",
        json={
            "title": "Pagos inmediatos",
            "description": "Asegurar el flujo de pagos.",
            "member_ids": [2, 3],
        },
    )
    assert response.status_code == 201
    return response.json()["key"]


def create_story_and_case(client: TestClient, epic_key: str) -> str:
    client.headers["x-test-role"] = "administrador"
    story_response = client.post(
        f"/qa/epics/{epic_key}/stories",
        json={
            "title": "Pago intra-SPBVI",
            "description": "Permitir un pago dentro del mismo SPBVI.",
            "priority": "high",
            "acceptance_criteria": ["El pago conserva su saldo."],
        },
    )
    assert story_response.status_code == 201

    case_response = client.post(
        f"/qa/stories/{story_response.json()['key']}/test-cases",
        json={
            "title": "Crear pago",
            "description": "Enviar un pago y validar su respuesta.",
            "priority": "high",
            "preconditions": ["Hay cuentas de prueba activas."],
            "steps": [{"action": "Enviar la solicitud", "expected": "Pago aceptado"}],
            "expected_result": "El endpoint acepta el pago.",
            "request_method": "POST",
            "request_path": "/api/payments",
            "request_query": {},
            "request_body": {"amount_cents": 125},
            "expected_status_codes": [201],
            "expected_response": {"accepted": True},
        },
    )
    assert case_response.status_code == 201
    assert case_response.json()["request"]["method"] == "POST"
    return case_response.json()["key"]


def test_only_admin_creates_epics_and_manager_creates_jira_story_and_case(
    qa_environment: tuple[TestClient, mongomock.database.Database, FakeMailer],
) -> None:
    client, database, mailer = qa_environment
    forbidden = client.post(
        "/qa/epics",
        headers={"x-test-role": "administrador"},
        json={"title": "Nope", "description": "Nope"},
    )
    assert forbidden.status_code == 403

    epic_key = create_epic(client)
    case_key = create_story_and_case(client, epic_key)

    assert case_key.startswith("CP-")
    assert database.epics.count_documents({"key": epic_key}) == 1
    assert database.work_items.count_documents({"kind": "story"}) == 1
    assert database.work_items.count_documents({"kind": "test_case"}) == 1
    assert {message[0] for message in mailer.messages} == {
        "admin@example.com",
        "manager@example.com",
        "tester@example.com",
    }


def test_selected_case_executes_saved_http_json_and_notifies_epic_members(
    qa_environment: tuple[TestClient, mongomock.database.Database, FakeMailer],
) -> None:
    client, database, mailer = qa_environment
    epic_key = create_epic(client)
    case_key = create_story_and_case(client, epic_key)
    client.headers["x-test-role"] = "usuario"

    response = client.post(f"/qa/cases/{case_key}/execute")

    assert response.status_code == 201
    execution = response.json()
    assert execution["request"]["method"] == "POST"
    assert execution["request"]["url"] == "http://127.0.0.1:8000/api/payments"
    assert execution["result"]["status_code"] == 201
    assert execution["result"]["body"] == {
        "accepted": True,
        "Authorization": "[REDACTADO]",
    }
    assert execution["passed"] is True
    assert execution["notification_status"] == "sent"
    assert database.executions.count_documents({"case_key": case_key}) == 1
    executions = client.get(f"/qa/cases/{case_key}/executions")
    assert executions.status_code == 200
    assert executions.json()[0]["key"] == execution["key"]
    assert len([message for message in mailer.messages if "ejecutado" in message[1]]) == 3


def test_case_definition_rejects_external_targets_and_secret_fields(
    qa_environment: tuple[TestClient, mongomock.database.Database, FakeMailer],
) -> None:
    client, database, _ = qa_environment
    epic_key = create_epic(client)
    client.headers["x-test-role"] = "administrador"
    story = client.post(
        f"/qa/epics/{epic_key}/stories",
        json={
            "title": "Story",
            "description": "Story",
            "acceptance_criteria": ["Works"],
        },
    ).json()
    base_payload = {
        "title": "Unsafe case",
        "description": "Must not send arbitrary requests.",
        "steps": [{"action": "Send", "expected": "Reject"}],
        "expected_result": "Rejected",
        "request_method": "GET",
        "request_path": "/health",
    }

    external = client.post(
        f"/qa/stories/{story['key']}/test-cases",
        json={**base_payload, "request_path": "//evil.example/steal"},
    )
    secret_field = client.post(
        f"/qa/stories/{story['key']}/test-cases",
        json={
            **base_payload,
            "request_body": {"password": "plaintext-secret"},
        },
    )

    assert external.status_code == 422
    assert secret_field.status_code == 422
    assert database.work_items.count_documents({"kind": "test_case"}) == 0


def test_case_secrets_are_resolved_at_runtime_and_redacted_from_execution(
    qa_environment: tuple[TestClient, mongomock.database.Database, FakeMailer],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, database, mailer = qa_environment
    epic_key = create_epic(client)
    client.headers["x-test-role"] = "administrador"
    story = client.post(
        f"/qa/epics/{epic_key}/stories",
        json={
            "title": "Credential test",
            "description": "Keep credentials out of stored executions.",
            "acceptance_criteria": ["The secret never persists in plaintext."],
        },
    ).json()
    monkeypatch.setenv("QA_SECRET_PAYMENTS_TOKEN", "local-secret-token")
    case = client.post(
        f"/qa/stories/{story['key']}/test-cases",
        json={
            "title": "Send token safely",
            "description": "Use a local secret reference.",
            "steps": [{"action": "Send", "expected": "Accepted"}],
            "expected_result": "The endpoint echoes the expected token.",
            "request_method": "POST",
            "request_path": "/api/secure",
            "request_headers": {
                "Authorization": "Bearer {{secret:PAYMENTS_TOKEN}}"
            },
            "request_body": {"payment": "ok"},
            "expected_status_codes": [201],
            "expected_response": {"echo": "{{secret:PAYMENTS_TOKEN}}"},
        },
    )
    assert case.status_code == 201

    client.headers["x-test-role"] = "usuario"
    execution = client.post(f"/qa/cases/{case.json()['key']}/execute")

    assert execution.status_code == 201
    assert execution.json()["passed"] is True
    assert mailer.requests[-1].headers["Authorization"] == (
        "Bearer local-secret-token"
    )
    persisted_case = database.work_items.find_one({"key": case.json()["key"]})
    persisted_execution = database.executions.find_one({"key": execution.json()["key"]})
    assert persisted_case is not None
    assert "local-secret-token" not in str(persisted_case)
    assert persisted_case["request"]["headers"]["Authorization"] == (
        "Bearer {{secret:PAYMENTS_TOKEN}}"
    )
    assert persisted_execution is not None
    assert "local-secret-token" not in str(persisted_execution)
    assert persisted_execution["request"]["headers"]["Authorization"] == (
        "[REDACTADO]"
    )
    assert persisted_execution["result"]["body"]["echo"] == "[REDACTADO]"


def test_failed_notification_is_persisted_and_can_be_retried(
    qa_environment: tuple[TestClient, mongomock.database.Database, FakeMailer],
) -> None:
    client, database, mailer = qa_environment
    mailer.fail = True

    response = client.post(
        "/qa/epics",
        json={"title": "Pending email", "description": "Retry this notification."},
    )

    assert response.status_code == 201
    assert response.json()["notification_status"] == "pending"
    epic = database.epics.find_one({"key": response.json()["key"]})
    assert epic is not None
    assert all(
        not recipient["delivered"]
        for event in epic["notifications"].values()
        for recipient in event["recipients"]
    )

    mailer.fail = False
    retried = client.post("/qa/notifications/retry")

    assert retried.status_code == 200
    assert retried.json() == {"delivered": 1, "pending": 0}
    epic = database.epics.find_one({"key": response.json()["key"]})
    assert epic is not None
    assert epic["notifications"] == {}


def test_bug_fix_retest_lifecycle_and_role_permissions(
    qa_environment: tuple[TestClient, mongomock.database.Database, FakeMailer],
) -> None:
    client, database, _ = qa_environment
    epic_key = create_epic(client)
    case_key = create_story_and_case(client, epic_key)
    client.headers["x-test-role"] = "usuario"
    execution = client.post(f"/qa/cases/{case_key}/execute").json()
    bug_response = client.post(
        "/qa/bugs",
        json={
            "case_key": case_key,
            "execution_key": execution["key"],
            "title": "El saldo no se actualiza",
            "description": "El saldo visible permanece igual.",
            "severity": "major",
        },
    )
    assert bug_response.status_code == 201
    bug_key = bug_response.json()["key"]
    listed_bugs = client.get(f"/qa/epics/{epic_key}/bugs")
    assert listed_bugs.status_code == 200
    assert [bug["key"] for bug in listed_bugs.json()] == [bug_key]

    admin_fix_forbidden = client.post(
        f"/qa/bugs/{bug_key}/fixes",
        headers={"x-test-role": "administrador"},
        json={"title": "Fix", "description": "Fix"},
    )
    assert admin_fix_forbidden.status_code == 403

    assigned = client.post(
        f"/qa/bugs/{bug_key}/transition",
        headers={"x-test-role": "administrador"},
        json={"status": "assigned", "assignee_id": 3},
    )
    assert assigned.status_code == 200

    fix = client.post(
        f"/qa/bugs/{bug_key}/fixes",
        json={"title": "Corregir saldo", "description": "Actualizar el saldo."},
    )
    assert fix.status_code == 201
    assert fix.json()["status"] == "in_fix"
    premature_retest = client.post(
        f"/qa/bugs/{bug_key}/transition",
        headers={"x-test-role": "administrador"},
        json={"status": "ready_for_retest"},
    )
    assert premature_retest.status_code == 409
    fix_key = fix.json()["fix"]["key"]
    fix_ready = client.post(
        f"/qa/bugs/{bug_key}/fixes/{fix_key}/transition",
        json={"status": "ready_for_retest"},
    )
    assert fix_ready.status_code == 200
    ready = client.post(
        f"/qa/bugs/{bug_key}/transition",
        headers={"x-test-role": "administrador"},
        json={"status": "ready_for_retest"},
    )
    assert ready.status_code == 200
    reopened = client.post(
        f"/qa/bugs/{bug_key}/transition",
        headers={"x-test-role": "administrador"},
        json={"status": "closed", "retest_passed": False},
    )
    assert reopened.status_code == 200
    assert reopened.json()["status"] == "reopened"

    reassigned = client.post(
        f"/qa/bugs/{bug_key}/transition",
        headers={"x-test-role": "administrador"},
        json={"status": "assigned", "assignee_id": 3},
    )
    assert reassigned.status_code == 200
    second_fix = client.post(
        f"/qa/bugs/{bug_key}/fixes",
        json={"title": "Ajustar retest", "description": "Corregir la regresion."},
    )
    assert second_fix.status_code == 201
    second_fix_ready = client.post(
        f"/qa/bugs/{bug_key}/fixes/{second_fix.json()['fix']['key']}/transition",
        json={"status": "ready_for_retest"},
    )
    assert second_fix_ready.status_code == 200
    ready_again = client.post(
        f"/qa/bugs/{bug_key}/transition",
        headers={"x-test-role": "administrador"},
        json={"status": "ready_for_retest"},
    )
    assert ready_again.status_code == 200
    closed = client.post(
        f"/qa/bugs/{bug_key}/transition",
        headers={"x-test-role": "administrador"},
        json={"status": "closed", "retest_passed": True},
    )
    assert closed.status_code == 200
    assert closed.json()["status"] == "closed"

    persisted_bug = database.issues.find_one({"key": bug_key})
    assert persisted_bug is not None
    assert len(persisted_bug["fixes"]) == 2
    assert persisted_bug["history"][-1]["to"] == "closed"


def test_only_epic_members_can_execute_cases(
    qa_environment: tuple[TestClient, mongomock.database.Database, FakeMailer],
) -> None:
    client, _, _ = qa_environment
    epic_key = create_epic(client)
    case_key = create_story_and_case(client, epic_key)
    client.headers["x-test-role"] = "externo"

    response = client.post(f"/qa/cases/{case_key}/execute")

    assert response.status_code == 403


def test_task_is_created_by_manager_and_only_assignee_can_execute(
    qa_environment: tuple[TestClient, mongomock.database.Database, FakeMailer],
) -> None:
    client, database, mailer = qa_environment
    epic_key = create_epic(client)
    client.headers["x-test-role"] = "administrador"
    created = client.post(
        f"/qa/epics/{epic_key}/tasks",
        json={
            "title": "Validar ledger",
            "description": "Confirmar que el movimiento este balanceado.",
            "assignee_id": 3,
        },
    )
    assert created.status_code == 201
    task_key = created.json()["key"]

    client.headers["x-test-role"] = "externo"
    forbidden = client.post(
        f"/qa/tasks/{task_key}/transition",
        json={"status": "in_progress"},
    )
    assert forbidden.status_code == 403

    client.headers["x-test-role"] = "usuario"
    started = client.post(
        f"/qa/tasks/{task_key}/transition",
        json={"status": "in_progress"},
    )
    completed = client.post(
        f"/qa/tasks/{task_key}/transition",
        json={"status": "done"},
    )

    assert started.status_code == 200
    assert completed.status_code == 200
    assert database.work_items.find_one({"key": task_key})["status"] == "done"
    assert any("TASK-" in message[1] for message in mailer.messages)


def test_admin_and_manager_assign_tasks_but_user_cannot(
    qa_environment: tuple[TestClient, mongomock.database.Database, FakeMailer],
) -> None:
    client, database, mailer = qa_environment
    epic_key = create_epic(client)
    created = client.post(
        f"/qa/epics/{epic_key}/tasks",
        json={"title": "Revisar llaves", "description": "Validar el ciclo.", "assignee_id": 3},
    )
    assert created.status_code == 201
    task_key = created.json()["key"]
    assert created.json()["assignee"]["user_id"] == 3

    user_assign = client.post(
        f"/qa/tasks/{task_key}/assign",
        headers={"x-test-role": "usuario"},
        json={"assignee_id": 2},
    )
    assert user_assign.status_code == 403

    outsider = client.post(
        f"/qa/tasks/{task_key}/assign",
        headers={"x-test-role": "administrador"},
        json={"assignee_id": 4},
    )
    assert outsider.status_code == 422

    mailer.messages.clear()
    reassigned = client.post(
        f"/qa/tasks/{task_key}/assign",
        headers={"x-test-role": "administrador"},
        json={"assignee_id": 2},
    )
    assert reassigned.status_code == 200
    assert database.work_items.find_one({"key": task_key})["assignee"]["user_id"] == 2
    assert {message[0] for message in mailer.messages} == {
        "admin@example.com",
        "manager@example.com",
        "tester@example.com",
    }
    assert any("Te asignaron una tarea" in message[2] or task_key in message[1] for message in mailer.messages)


def test_user_manages_bug_flow_but_only_managers_assign_it(
    qa_environment: tuple[TestClient, mongomock.database.Database, FakeMailer],
) -> None:
    client, _, _ = qa_environment
    epic_key = create_epic(client)
    case_key = create_story_and_case(client, epic_key)
    client.headers["x-test-role"] = "usuario"
    execution = client.post(f"/qa/cases/{case_key}/execute").json()
    bug_key = client.post(
        "/qa/bugs",
        json={
            "case_key": case_key,
            "execution_key": execution["key"],
            "title": "Saldo",
            "description": "No cambia.",
            "severity": "major",
        },
    ).json()["key"]

    assign_by_user = client.post(
        f"/qa/bugs/{bug_key}/transition", json={"status": "assigned", "assignee_id": 3}
    )
    assert assign_by_user.status_code == 403

    in_fix = client.post(f"/qa/bugs/{bug_key}/transition", json={"status": "in_fix"})
    assert in_fix.status_code == 200
    assert in_fix.json()["status"] == "in_fix"


def test_epic_member_updates_case_json_with_version_history(
    qa_environment: tuple[TestClient, mongomock.database.Database, FakeMailer],
) -> None:
    client, database, mailer = qa_environment
    epic_key = create_epic(client)
    case_key = create_story_and_case(client, epic_key)
    new_definition = {
        "request_method": "GET",
        "request_path": "/health",
        "request_query": {},
        "request_headers": {},
        "request_body": None,
        "expected_status_codes": [200],
        "expected_response": {"status": "ok"},
        "change_note": "Apuntar al chequeo de salud.",
    }

    outsider = client.put(
        f"/qa/cases/{case_key}", headers={"x-test-role": "externo"}, json=new_definition
    )
    assert outsider.status_code == 403

    external = client.put(
        f"/qa/cases/{case_key}",
        headers={"x-test-role": "usuario"},
        json={**new_definition, "request_path": "https://example.com/x"},
    )
    assert external.status_code == 422

    mailer.messages.clear()
    updated = client.put(
        f"/qa/cases/{case_key}", headers={"x-test-role": "usuario"}, json=new_definition
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["version"] == 2
    assert body["request"] == {
        "method": "GET",
        "path": "/health",
        "query": {},
        "headers": {},
        "body": None,
    }
    assert body["expected_response"] == {"status": "ok"}
    stored = database.work_items.find_one({"key": case_key})
    assert stored["versions"][0]["version"] == 1
    assert stored["versions"][0]["request"]["path"] == "/api/payments"
    assert stored["versions"][0]["replaced_by"]["email"] == "tester@example.com"
    assert stored["versions"][0]["change_note"] == "Apuntar al chequeo de salud."
    assert len(mailer.messages) == 3

    execution = client.post(
        f"/qa/cases/{case_key}/execute", headers={"x-test-role": "usuario"}
    ).json()
    assert execution["request"]["method"] == "GET"
    assert execution["request"]["url"].endswith("/health")


def sample_program() -> dict:
    return {
        "format": "qalabspbvi-qa-program/v1",
        "name": "Programa de prueba",
        "stories": [
            {
                "ref": "HU-001",
                "title": "Validar pago inmediato",
                "description": "Como analista de pruebas quiero validar el pago.",
                "priority": "high",
                "acceptance_criteria": ["Responde con el pago aceptado."],
                "labels": ["pain001", "json"],
                "story_points": 8,
                "test_cases": [
                    {
                        "ref": "TC-001",
                        "title": "Pago válido",
                        "description": "Envía un pago válido.",
                        "priority": "high",
                        "preconditions": ["Datos preparados."],
                        "steps": [{"action": "Enviar POST", "expected": "201"}],
                        "expected_result": "Pago aceptado.",
                        "labels": ["api-rest"],
                        "request_method": "POST",
                        "request_path": "/payments",
                        "request_body": {"amount_cents": 125},
                        "expected_status_codes": [200, 201],
                        "expected_response": {"status": "completed"},
                    }
                ],
            },
            {
                "ref": "HU-007",
                "title": "Validar devoluciones",
                "description": "Sin endpoint en el laboratorio.",
                "priority": "high",
                "acceptance_criteria": ["Pendiente de endpoint."],
            },
        ],
        "tasks": [
            {"ref": "TASK-001", "title": "Configurar validador", "description": "JSON Schema.", "labels": ["json-schema"]}
        ],
    }


def test_manager_imports_program_once_with_single_summary_notification(
    qa_environment: tuple[TestClient, mongomock.database.Database, FakeMailer],
) -> None:
    client, database, mailer = qa_environment
    epic_key = create_epic(client)
    program = sample_program()

    by_admin = client.post(f"/qa/epics/{epic_key}/import", json=program)
    assert by_admin.status_code == 403
    by_user = client.post(
        f"/qa/epics/{epic_key}/import", headers={"x-test-role": "usuario"}, json=program
    )
    assert by_user.status_code == 403

    mailer.messages.clear()
    client.headers["x-test-role"] = "administrador"
    imported = client.post(f"/qa/epics/{epic_key}/import", json=program)
    assert imported.status_code == 201
    assert imported.json()["created"] == {"stories": 2, "test_cases": 1, "tasks": 1}
    assert imported.json()["skipped"] == {"stories": 0, "test_cases": 0, "tasks": 0}
    assert len(mailer.messages) == 3
    assert all("importó" in message[1] or "import" in message[1].lower() for message in mailer.messages)

    case = database.work_items.find_one({"kind": "test_case", "external_key": "TC-001"})
    story = database.work_items.find_one({"kind": "story", "external_key": "HU-001"})
    assert case["story_key"] == story["key"]
    assert case["request"]["method"] == "POST"
    assert case["labels"] == ["api-rest"]
    assert story["story_points"] == 8

    again = client.post(f"/qa/epics/{epic_key}/import", json=program)
    assert again.status_code == 201
    assert again.json()["created"] == {"stories": 0, "test_cases": 0, "tasks": 0}
    assert again.json()["skipped"] == {"stories": 2, "test_cases": 1, "tasks": 1}
    assert database.work_items.count_documents({"epic_key": epic_key}) == 4


def test_program_with_invalid_case_is_rejected_without_partial_import(
    qa_environment: tuple[TestClient, mongomock.database.Database, FakeMailer],
) -> None:
    client, database, _ = qa_environment
    epic_key = create_epic(client)
    program = sample_program()
    program["stories"][0]["test_cases"][0]["request_path"] = "https://example.com/pagos"
    client.headers["x-test-role"] = "administrador"

    response = client.post(f"/qa/epics/{epic_key}/import", json=program)

    assert response.status_code == 422
    assert "TC-001" in response.json()["detail"]
    assert database.work_items.count_documents({"epic_key": epic_key}) == 0


def test_case_without_expected_response_only_checks_status_code(
    qa_environment: tuple[TestClient, mongomock.database.Database, FakeMailer],
) -> None:
    client, _, _ = qa_environment
    epic_key = create_epic(client)
    case_key = create_story_and_case(client, epic_key)
    client.put(
        f"/qa/cases/{case_key}",
        headers={"x-test-role": "usuario"},
        json={
            "request_method": "POST",
            "request_path": "/api/payments",
            "request_body": {"amount_cents": 125},
            "expected_status_codes": [201],
            "expected_response": None,
            "change_note": "Solo validar el código HTTP.",
        },
    )

    execution = client.post(
        f"/qa/cases/{case_key}/execute", headers={"x-test-role": "usuario"}
    ).json()

    assert execution["passed"] is True
