"""Servidor de apoyo de Playwright con persistencias efimeras y aisladas."""

from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import re
import secrets
import time

data_dir = Path(os.environ["QALAB_E2E_DATA_DIR"]).resolve()
data_dir.mkdir(parents=True, exist_ok=True)
os.environ.update(
    {
        "APP_ENV": "local",
        "AUTH_SECRET_KEY": secrets.token_urlsafe(48),
        "DATABASE_URL": f"sqlite:///{(data_dir / 'payments.sqlite').as_posix()}",
        "DIFE_DATABASE_URL": f"sqlite:///{(data_dir / 'dife.sqlite').as_posix()}",
        "DICE_DATABASE_URL": f"sqlite:///{(data_dir / 'dice.sqlite').as_posix()}",
        "MONGODB_URL": "mongodb://127.0.0.1:1",
        "MONGODB_DATABASE": "qalabspbvi_e2e_isolated",
        "QA_TARGET_BASE_URL": "http://127.0.0.1:8010",
    }
)

from fastapi import FastAPI  # noqa: E402
import mongomock  # noqa: E402

from app.core.mailer import get_mailer  # noqa: E402
from app.db.models import UserRole  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.domains.auth.service import create_user  # noqa: E402
from app.domains.keys.persistence import (  # noqa: E402
    DiceBase,
    DifeBase,
    get_dice_engine,
    get_dife_engine,
)
from app.domains.qa.mongo import (  # noqa: E402
    ensure_qa_indexes,
    get_qa_database,
)
from app.main import app  # noqa: E402

mfa_code_path = data_dir / "mfa-code.txt"
credentials_path = data_dir / "credentials.json"
test_accounts: dict[str, dict[str, str]] = {}


class E2EMailer:
    def send(
        self, *, recipient: str, subject: str, body: str, html: str | None = None
    ) -> None:
        del recipient, subject
        match = re.search(r"Código:\s*(\d{6})", body)
        if match:
            temporary_path = mfa_code_path.with_suffix(".tmp")
            temporary_path.write_text(match.group(1), encoding="utf-8")
            temporary_path.replace(mfa_code_path)


mailer = E2EMailer()
mongo_client = mongomock.MongoClient()
qa_database = mongo_client["qalabspbvi_e2e_isolated"]
ensure_qa_indexes(qa_database)
original_lifespan = app.router.lifespan_context


@asynccontextmanager
async def e2e_lifespan(application: FastAPI):
    async with original_lifespan(application):
        DifeBase.metadata.create_all(bind=get_dife_engine())
        DiceBase.metadata.create_all(bind=get_dice_engine())
        with SessionLocal() as db:
            # "bugs" es un integrante con rol usuario: reporta bugs, registra fixes y hace retest.
            for account_name in ("auth", "keys", "intra", "inter", "bugs"):
                email = f"qa-e2e-{account_name}@example.com"
                password = secrets.token_urlsafe(24)
                role = UserRole.USUARIO if account_name == "bugs" else UserRole.ADMIN
                user = create_user(
                    db,
                    email=email,
                    display_name=f"{'Usuario' if role is UserRole.USUARIO else 'Admin'} E2E {account_name}",
                    password=password,
                    role=role,
                )
                test_accounts[account_name] = {
                    "email": email,
                    "password": password,
                    "user_id": str(user.id),
                }
        credentials_path.write_text(
            json.dumps(test_accounts),
            encoding="utf-8",
        )
        qa_database.epics.insert_one(
            {
                "key": "E2E-EPIC",
                "kind": "epic",
                "title": "Validacion de interfaz local",
                "description": "Epic temporal para Playwright.",
                "members": [
                    {
                        "user_id": int(account["user_id"]),
                        "email": account["email"],
                    }
                    for account in test_accounts.values()
                ],
                "created_at_epoch": int(time.time()),
            }
        )
        qa_database.work_items.insert_one(
            {
                "key": "E2E-HU-001",
                "kind": "story",
                "epic_key": "E2E-EPIC",
                "title": "Salud de la API",
                "description": "Como operador quiero confirmar que la API responde.",
                "priority": "high",
                "acceptance_criteria": ["El endpoint de salud responde 200."],
                "created_at_epoch": int(time.time()),
            }
        )
        qa_database.work_items.insert_one(
            {
                "key": "E2E-CP-001",
                "kind": "test_case",
                "story_key": "E2E-HU-001",
                "epic_key": "E2E-EPIC",
                "title": "Consultar salud local",
                "description": "Confirma el endpoint de salud del servidor aislado.",
                "priority": "high",
                "status": "open",
                "created_at_epoch": int(time.time()),
                "request": {
                    "method": "GET",
                    "path": "/health",
                    "query": {},
                    "headers": {},
                    "body": None,
                },
                "expected_status_codes": [200],
                "expected_response": {"status": "ok"},
            }
        )
        yield


app.router.lifespan_context = e2e_lifespan
app.dependency_overrides[get_mailer] = lambda: mailer
app.dependency_overrides[get_qa_database] = lambda: qa_database
