"""Las épicas de qa_programs/epicas/ se importan y todos sus CP pasan contra la API.

Mismo runner de la plataforma (uvicorn real), con el rol que indica cada CP en sus
precondiciones: en la plataforma real es el rol de quien lo ejecuta. Todas las épicas
comparten las bases, como en un entorno real, y se ejecutan en orden.
"""

import json
from pathlib import Path

import pytest
from fastapi import Header
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.security import current_user
from app.db.models import User, UserRole
from app.domains.audit import logs as audit_logs
from app.domains.qr.persistence import QrBase, get_qr_db
from app.main import app
from tests.test_qa_program import case_number, platform  # noqa: F401

EPICS = sorted((Path(__file__).parents[1] / "qa_programs" / "epicas").glob("EPIC-*.json"))


def role_for(case: dict) -> str:
    text = " ".join(case.get("preconditions", [])).lower()
    if "rol usuario" in text:
        return "usuario"
    if "rol admin." in text or "rol admin:" in text:
        return "admin"
    return "administrador"


def test_epic_programs_import_and_every_case_passes(platform, monkeypatch, tmp_path) -> None:  # noqa: F811
    client, database = platform
    # Bases propias de cobros QR y de logs (en la nube: TiDB y Neon).
    qr_engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    QrBase.metadata.create_all(qr_engine)

    def qr_db():
        with Session(qr_engine) as session:
            yield session

    app.dependency_overrides[get_qr_db] = qr_db
    monkeypatch.setenv("LOGS_DATABASE_URL", f"sqlite:///{(tmp_path / 'logs.sqlite').as_posix()}")
    from app.core import config

    config.get_settings.cache_clear()
    audit_logs.logs_engine.cache_clear()

    created = client.post(
        "/auth/users",
        headers={"x-test-role": "admin"},
        json={"email": "persona@example.com", "display_name": "Persona", "password": "persona password 2026", "role": "usuario"},
    )
    assert created.status_code == 201, created.text
    people = {
        "admin": User(id=1, email="admin@example.com", display_name="Admin", password_hash="x", role=UserRole.ADMIN, is_active=True),
        "administrador": User(id=2, email="manager@example.com", display_name="Manager", password_hash="x", role=UserRole.ADMINISTRADOR, is_active=True),
        "usuario": User(id=created.json()["id"], email="persona@example.com", display_name="Persona", password_hash="x", role=UserRole.USUARIO, is_active=True),
    }
    app.dependency_overrides[current_user] = lambda x_test_role=Header(default="administrador"): people[x_test_role]

    assert len(EPICS) == 17
    failures = []
    for path in EPICS:
        program = json.loads(path.read_text(encoding="utf-8"))
        epic = client.post(
            "/qa/epics",
            headers={"x-test-role": "admin"},
            json={"title": program["epic"]["title"], "description": program["epic"]["description"], "member_ids": [1, 2, people["usuario"].id]},
        )
        assert epic.status_code == 201, epic.text
        key = epic.json()["key"]
        imported = client.post(f"/qa/epics/{key}/import", json={k: program[k] for k in ("format", "name", "stories", "tasks")})
        assert imported.status_code == 201, f"{path.name}: {imported.text}"
        cases = sorted(database.work_items.find({"epic_key": key, "kind": "test_case"}), key=lambda c: case_number(c["key"]))
        for case in cases:
            role = role_for(case)
            # El runner llama a la API sin cookie: la cabecera indica el rol de esa llamada.
            database.work_items.update_one({"key": case["key"]}, {"$set": {"request.headers.x-test-role": role}})
            response = client.post(f"/qa/cases/{case['key']}/execute", headers={"x-test-role": role})
            if response.status_code != 201 or not response.json()["passed"]:
                result = response.json().get("result", {}) if response.status_code == 201 else {}
                failures.append(f"{path.stem} {case['external_key']} ({role}) → {result.get('status_code')} "
                                f"{json.dumps(result.get('body'), ensure_ascii=False)[:200] if result else response.text[:200]}")
    assert failures == [], "\n".join(failures)


@pytest.mark.parametrize("path", EPICS, ids=lambda p: p.stem)
def test_epic_programs_keep_the_standard(path: Path) -> None:
    from app.api.schemas import ProgramImportRequest

    program = json.loads(path.read_text(encoding="utf-8"))
    ProgramImportRequest.model_validate(program)
    assert program["epic"]["ref"] == path.stem[:8] and program["epic"]["title"] and program["epic"]["description"]
    for story in program["stories"]:
        for case in story["test_cases"]:
            # Nunca POST/PUT/PATCH/DELETE sobre /auth/* (la plataforma lo rechaza) ni credenciales.
            assert not (case["request_path"].startswith("/auth/") and case["request_method"] != "GET"), case["ref"]
            assert "{{secret:" not in json.dumps(case), case["ref"]
