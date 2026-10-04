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
    assert imported.json()["created"] == {"stories": 19, "test_cases": 30, "tasks": 22}

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
