"""El programa ISO 20022 / Bre-B importable se carga y todos sus CP pasan contra la API.

Importa qa_programs/iso20022-breb-rest-json.json en una épica y ejecuta cada CP con el
mismo runner de la plataforma, dos veces seguidas, para comprobar que es repetible.
"""

import json
import re
from collections.abc import Iterator
from pathlib import Path

import mongomock
import pytest
from fastapi import Header
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core import config
from app.core.mailer import get_mailer
from app.core.security import current_user
from app.db.base import Base
from app.db.models import User, UserRole
from app.db.session import get_db
from app.domains.keys.persistence import DiceBase, DifeBase, get_dice_db, get_dife_db
from app.domains.qa.mongo import get_qa_database, get_qa_http_client
from app.main import app

PROGRAM = Path(__file__).parents[1] / "qa_programs" / "iso20022-breb-rest-json.json"


class SilentMailer:
    def __init__(self) -> None:
        self.sent = 0

    def send(self, *, recipient: str, subject: str, body: str, html: str | None = None) -> None:
        self.sent += 1


def memory_engine():
    return create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )


@pytest.fixture
def platform(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, mongomock.database.Database]]:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("QA_TARGET_BASE_URL", "http://127.0.0.1:8000")
    config.get_settings.cache_clear()
    engines = {"sql": memory_engine(), "dife": memory_engine(), "dice": memory_engine()}
    Base.metadata.create_all(engines["sql"])
    DifeBase.metadata.create_all(engines["dife"])
    DiceBase.metadata.create_all(engines["dice"])
    users = {
        "admin": User(id=1, email="admin@example.com", display_name="Admin", password_hash="x", role=UserRole.ADMIN, is_active=True),
        "administrador": User(id=2, email="manager@example.com", display_name="Manager", password_hash="x", role=UserRole.ADMINISTRADOR, is_active=True),
    }
    with Session(engines["sql"], expire_on_commit=False) as db:
        db.add_all(users.values())
        db.commit()
    mongo = mongomock.MongoClient()
    database = mongo["qa_program_test"]

    def session_for(name: str):
        def dependency():
            with Session(engines[name]) as session:
                yield session

        return dependency

    # Las solicitudes internas del runner llegan sin cabecera: actúan como el administrador.
    def override_user(x_test_role: str = Header(default="administrador")) -> User:
        return users[x_test_role]

    def runner_client():
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client

    app.dependency_overrides[get_db] = session_for("sql")
    app.dependency_overrides[get_dife_db] = session_for("dife")
    app.dependency_overrides[get_dice_db] = session_for("dice")
    app.dependency_overrides[current_user] = override_user
    app.dependency_overrides[get_mailer] = SilentMailer
    app.dependency_overrides[get_qa_database] = lambda: database
    app.dependency_overrides[get_qa_http_client] = runner_client
    client = TestClient(app, raise_server_exceptions=False)
    client.cookies.set("qalab_csrf", "test-csrf")
    client.headers["x-csrf-token"] = "test-csrf"
    try:
        yield client, database
    finally:
        client.close()
        app.dependency_overrides.clear()
        mongo.close()
        for engine in engines.values():
            engine.dispose()
        config.get_settings.cache_clear()


def case_number(key: str) -> int:
    return int(re.sub(r"\D", "", key))


def test_iso20022_breb_program_imports_and_every_case_passes_twice(
    platform: tuple[TestClient, mongomock.database.Database],
) -> None:
    client, database = platform
    program = json.loads(PROGRAM.read_text(encoding="utf-8"))
    epic = client.post(
        "/qa/epics",
        headers={"x-test-role": "admin"},
        json={"title": program["epic"]["title"], "description": program["epic"]["description"], "member_ids": [2]},
    )
    assert epic.status_code == 201
    epic_key = epic.json()["key"]

    imported = client.post(f"/qa/epics/{epic_key}/import", json=program)
    assert imported.status_code == 201, imported.text
    assert imported.json()["created"] == {"stories": 19, "test_cases": 35, "tasks": 22}

    cases = sorted(
        database.work_items.find({"epic_key": epic_key, "kind": "test_case"}),
        key=lambda item: case_number(item["key"]),
    )
    for round_number in (1, 2):
        failures = []
        for test_case in cases:
            response = client.post(f"/qa/cases/{test_case['key']}/execute")
            assert response.status_code == 201, response.text
            execution = response.json()
            if not execution["passed"]:
                failures.append(
                    f"{test_case['external_key']} → {execution['result'].get('status_code')} "
                    f"{json.dumps(execution['result'].get('body'), ensure_ascii=False)[:300]}"
                )
        assert failures == [], f"Ronda {round_number}:\n" + "\n".join(failures)

    # Cada ronda generó llaves nuevas y válidas de cada tipo Bre-B, sin repetir valores.
    from app.domains.keys.key_types import normalize_key_value

    pool = client.get(f"/qa/epics/{epic_key}/keys").json()
    assert len(pool) == 14
    assert len({(key["key_type"], key["key_value"]) for key in pool}) == 14
    assert {key["key_type"] for key in pool if key["spbvi_id"] == "spbvi-a"} == {
        "phone", "email", "document", "alias", "merchant_code"
    }
    for key in pool:
        assert normalize_key_value(key["key_type"], key["key_value"]) == key["key_value"]
        assert key["status"] == "confirmed"
    assert len(client.get(f"/qa/epics/{epic_key}/keys", params={"key_type": "phone", "spbvi_id": "spbvi-b"}).json()) == 2

    # Quien ejecuta puede elegir una llave de la lista según el tipo del CP.
    tc001 = next(case for case in cases if case["external_key"] == "TC-001")
    oldest_phone = [key for key in pool if key["key_type"] == "phone" and key["spbvi_id"] == "spbvi-a"][-1]
    chosen = client.post(
        f"/qa/cases/{tc001['key']}/execute",
        json={"selected_keys": {"phone:spbvi-a": oldest_phone["key_value"]}},
    )
    assert chosen.status_code == 201 and chosen.json()["passed"], chosen.text
    assert chosen.json()["request"]["body"]["destination_key_value"] == oldest_phone["key_value"]
    assert chosen.json()["placeholders"]["{{key:phone:spbvi-a}}"] == oldest_phone["key_value"]

    outside = client.post(
        f"/qa/cases/{tc001['key']}/execute",
        json={"selected_keys": {"phone:spbvi-a": "3009999999"}},
    )
    assert outside.status_code == 422
    assert "lista de llaves" in outside.json()["detail"]


TEMPLATES = PROGRAM.parent / "plantillas"


def test_templates_match_the_api_schemas() -> None:
    from app.api.schemas import (
        BugCreateRequest,
        EpicCreateRequest,
        FixCreateRequest,
        ProgramImportRequest,
        StoryCreateRequest,
        TaskCreateRequest,
        TestCaseCreateRequest,
    )

    schemas = {
        "epica.json": EpicCreateRequest,
        "hu.json": StoryCreateRequest,
        "cp.json": TestCaseCreateRequest,
        "tarea.json": TaskCreateRequest,
        "bug.json": BugCreateRequest,
        "fix.json": FixCreateRequest,
        "programa.json": ProgramImportRequest,
    }
    assert {path.name for path in TEMPLATES.glob("*.json")} == set(schemas)
    for name, schema in schemas.items():
        schema.model_validate(json.loads((TEMPLATES / name).read_text(encoding="utf-8")))


def test_program_template_imports_and_its_cases_pass(
    platform: tuple[TestClient, mongomock.database.Database],
) -> None:
    client, database = platform
    program = json.loads((TEMPLATES / "programa.json").read_text(encoding="utf-8"))
    epic = client.post(
        "/qa/epics",
        headers={"x-test-role": "admin"},
        json={"title": program["epic"]["title"], "description": program["epic"]["description"], "member_ids": [2]},
    ).json()
    imported = client.post(f"/qa/epics/{epic['key']}/import", json=program)
    assert imported.status_code == 201, imported.text

    cases = sorted(
        database.work_items.find({"epic_key": epic["key"], "kind": "test_case"}),
        key=lambda item: case_number(item["key"]),
    )
    results = {case["external_key"]: client.post(f"/qa/cases/{case['key']}/execute").json() for case in cases}
    assert all(result["passed"] for result in results.values()), results
    assert results["PLT-TC-004"]["placeholders"]["{{key:phone:spbvi-a}}"] == results["PLT-TC-003"]["placeholders"]["{{key:new:phone}}"]
