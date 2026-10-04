from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.mailer import get_mailer
from app.core.security import current_user
from app.db import models  # noqa: F401
from app.db.base import Base
from app.db.models import User, UserRole
from app.db.session import get_db
from app.domains.auth.service import create_user
from app.main import app


class FakeMailer:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str]] = []

    def send(self, *, recipient: str, subject: str, body: str) -> None:
        self.messages.append((recipient, subject, body))


@pytest.fixture
def auth_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, FakeMailer, Session]]:
    from app.core import config

    monkeypatch.setenv("AUTH_SECRET_KEY", "test-only-secret-key-with-at-least-32-bytes")
    config.get_settings.cache_clear()
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    mailer = FakeMailer()

    def override_get_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_mailer] = lambda: mailer
    with Session(engine) as db:
        create_user(
            db,
            email="Admin@Example.com",
            display_name="Admin",
            password="correct horse battery staple",
            role=UserRole.ADMIN,
        )
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield client, mailer, Session(engine)
    finally:
        client.close()
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        engine.dispose()
        config.get_settings.cache_clear()


def prepare_csrf(client: TestClient) -> str:
    response = client.get("/auth/csrf")
    assert response.status_code == 200
    token = response.json()["csrf_token"]
    client.headers["x-csrf-token"] = token
    return token


def sign_in(
    client: TestClient,
    mailer: FakeMailer,
    *,
    email: str = "admin@example.com",
    password: str = "correct horse battery staple",
) -> dict[str, str]:
    prepare_csrf(client)
    message_count = len(mailer.messages)
    login = client.post(
        "/auth/login",
        json={"email": email, "password": password},
    )
    assert login.status_code == 202
    assert len(mailer.messages) == message_count + 1
    code = mailer.messages[-1][2].split("Tu codigo de acceso es: ")[1].splitlines()[0]
    verified = client.post(
        "/auth/verify-email-code",
        json={"email": email, "code": code},
    )
    assert verified.status_code == 200
    client.headers["x-csrf-token"] = client.cookies.get("qalab_csrf")
    return verified.json()


def test_login_requires_password_then_single_use_email_code(
    auth_environment: tuple[TestClient, FakeMailer, Session],
) -> None:
    client, mailer, db = auth_environment
    prepare_csrf(client)

    login = client.post(
        "/auth/login",
        json={
            "email": "admin@example.com",
            "password": "correct horse battery staple",
        },
    )

    assert login.status_code == 202
    assert len(mailer.messages) == 1
    recipient, subject, body = mailer.messages[0]
    assert recipient == "admin@example.com"
    assert "codigo de acceso" in subject.lower()
    code = body.split("Tu codigo de acceso es: ")[1].splitlines()[0]
    assert len(code) == 6
    assert not client.cookies.get("qalab_session")

    verified = client.post(
        "/auth/verify-email-code",
        json={"email": recipient, "code": code},
    )
    assert verified.status_code == 200
    assert client.cookies.get("qalab_session")
    session_cookie = next(
        cookie
        for cookie in client.cookies.jar
        if cookie.name == "qalab_session"
    )
    assert session_cookie.has_nonstandard_attr("HttpOnly")
    client.headers["x-csrf-token"] = client.cookies.get("qalab_csrf")
    assert client.get("/auth/me").json()["role"] == "admin"

    replay = client.post(
        "/auth/verify-email-code",
        json={"email": recipient, "code": code},
    )
    assert replay.status_code == 401
    assert client.post("/auth/logout").status_code == 204
    assert client.get("/auth/me").status_code == 401
    db.expire_all()
    assert db.scalar(select(User).where(User.email == recipient)) is not None


def test_login_rejects_wrong_password_without_sending_a_code(
    auth_environment: tuple[TestClient, FakeMailer, Session],
) -> None:
    client, mailer, _ = auth_environment
    prepare_csrf(client)

    response = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "wrong password"},
    )

    assert response.status_code == 202
    assert "Si los datos son validos" in response.json()["message"]
    assert mailer.messages == []


def test_missing_csrf_token_is_rejected(
    auth_environment: tuple[TestClient, FakeMailer, Session],
) -> None:
    client, _, _ = auth_environment

    response = client.post(
        "/auth/login",
        json={
            "email": "admin@example.com",
            "password": "correct horse battery staple",
        },
    )

    assert response.status_code == 403


def test_protected_payment_endpoints_reject_anonymous_requests(
    auth_environment: tuple[TestClient, FakeMailer, Session],
) -> None:
    client, _, _ = auth_environment
    prepare_csrf(client)

    response = client.post(
        "/accounts",
        json={"account_id": "account", "spbvi_id": "spbvi-a", "balance_cents": 0},
    )

    assert response.status_code == 401


def test_bad_email_code_is_limited_and_session_requires_authentication(
    auth_environment: tuple[TestClient, FakeMailer, Session],
) -> None:
    client, mailer, _ = auth_environment
    prepare_csrf(client)
    client.post(
        "/auth/login",
        json={
            "email": "admin@example.com",
            "password": "correct horse battery staple",
        },
    )
    actual_code = (
        mailer.messages[0][2]
        .split("Tu codigo de acceso es: ")[1]
        .splitlines()[0]
    )
    wrong_code = "000000" if actual_code != "000000" else "000001"

    for _ in range(5):
        response = client.post(
            "/auth/verify-email-code",
            json={"email": "admin@example.com", "code": wrong_code},
        )
        assert response.status_code == 401

    assert len(mailer.messages) == 1
    client.cookies.delete("qalab_session")
    client.headers["x-csrf-token"] = client.cookies.get("qalab_csrf")
    assert client.get("/auth/me").status_code == 401


def test_password_failures_are_rate_limited(
    auth_environment: tuple[TestClient, FakeMailer, Session],
) -> None:
    client, mailer, _ = auth_environment
    prepare_csrf(client)

    for _ in range(10):
        response = client.post(
            "/auth/login",
            json={"email": "admin@example.com", "password": "incorrect password"},
        )
        assert response.status_code == 202

    blocked = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "incorrect password"},
    )
    assert blocked.status_code == 429
    assert mailer.messages == []


def test_passwords_are_stored_as_argon2id_hashes(
    auth_environment: tuple[TestClient, FakeMailer, Session],
) -> None:
    _, _, db = auth_environment

    admin = db.scalar(select(User).where(User.email == "admin@example.com"))

    assert admin is not None
    assert admin.password_hash.startswith("$argon2id$")


def test_administrator_can_create_non_admin_users_but_admin_cannot(
    auth_environment: tuple[TestClient, FakeMailer, Session],
) -> None:
    client, mailer, db = auth_environment
    sign_in(client, mailer)

    forbidden_admin = client.post(
        "/auth/users",
        json={
            "email": "other-admin@example.com",
            "display_name": "Another Admin",
            "password": "a different secure password",
            "role": "admin",
        },
    )
    assert forbidden_admin.status_code == 403

    administrator = client.post(
        "/auth/users",
        json={
            "email": "manager@example.com",
            "display_name": "Manager",
            "password": "a different secure password",
            "role": "administrador",
        },
    )
    assert administrator.status_code == 201

    client.post("/auth/logout")
    client.cookies.delete("qalab_session")

    sign_in(
        client,
        mailer,
        email="manager@example.com",
        password="a different secure password",
    )
    created = client.post(
        "/auth/users",
        json={
            "email": "tester@example.com",
            "display_name": "Tester",
            "password": "another secure password",
            "role": "usuario",
        },
    )
    assert created.status_code == 201
    assert created.json()["role"] == "usuario"

    listed = client.get("/auth/users")
    assert listed.status_code == 200
    assert len(listed.json()) == 3
    assert all("password_hash" not in user for user in listed.json())

    client.post("/auth/logout")
    client.cookies.delete("qalab_session")
    sign_in(
        client,
        mailer,
        email="tester@example.com",
        password="another secure password",
    )
    assert client.get("/auth/users").status_code == 403
