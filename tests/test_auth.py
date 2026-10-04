import re
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


def extract_code(body: str) -> str:
    match = re.search(r"Código:\s*(\d{6})", body)
    assert match, "El correo MFA no contiene el código"
    return match.group(1)


class FakeMailer:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str]] = []

    def send(
        self, *, recipient: str, subject: str, body: str, html: str | None = None
    ) -> None:
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
    code = extract_code(mailer.messages[-1][2])
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
    assert "código de acceso" in subject.lower()
    code = extract_code(body)
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
        extract_code(mailer.messages[0][2])
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


def test_only_admin_creates_administrators_and_new_users_get_welcome_email(
    auth_environment: tuple[TestClient, FakeMailer, Session],
) -> None:
    client, mailer, _ = auth_environment
    sign_in(client, mailer)
    created_by_admin = client.post(
        "/auth/users",
        json={
            "email": "manager@example.com",
            "display_name": "Manager",
            "password": "a different secure password",
            "role": "administrador",
        },
    )
    assert created_by_admin.status_code == 201
    assert created_by_admin.json()["notification_status"] == "sent"
    recipient, subject, body = mailer.messages[-1]
    assert recipient == "manager@example.com"
    assert "bienvenida" in subject.lower()
    assert "Administrador" in body
    assert "admin@example.com" in body
    assert "a different secure password" not in body

    user_by_admin = client.post(
        "/auth/users",
        json={
            "email": "analyst@example.com",
            "display_name": "Analyst",
            "password": "analyst secure password",
            "role": "usuario",
        },
    )
    assert user_by_admin.status_code == 201

    client.post("/auth/logout")
    client.cookies.delete("qalab_session")
    sign_in(client, mailer, email="manager@example.com", password="a different secure password")

    second_administrator = client.post(
        "/auth/users",
        json={
            "email": "manager2@example.com",
            "display_name": "Manager 2",
            "password": "yet another secure password",
            "role": "administrador",
        },
    )
    assert second_administrator.status_code == 403

    tester = client.post(
        "/auth/users",
        json={
            "email": "tester@example.com",
            "display_name": "Tester",
            "password": "another secure password",
            "role": "usuario",
        },
    )
    assert tester.status_code == 201
    assert mailer.messages[-1][0] == "tester@example.com"
    assert "another secure password" not in mailer.messages[-1][2]


def test_user_is_created_even_if_welcome_email_fails(
    auth_environment: tuple[TestClient, FakeMailer, Session],
) -> None:
    from app.core.mailer import MailDeliveryError

    client, mailer, _ = auth_environment
    sign_in(client, mailer)

    def failing_send(**_: object) -> None:
        raise MailDeliveryError("Brevo no disponible en la prueba.")

    mailer.send = failing_send  # type: ignore[method-assign]
    created = client.post(
        "/auth/users",
        json={
            "email": "manager@example.com",
            "display_name": "Manager",
            "password": "a different secure password",
            "role": "administrador",
        },
    )
    assert created.status_code == 201
    assert created.json()["notification_status"] == "failed"


class FakeClock:
    def __init__(self, start: float = 1_800_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


def start_pending_login(client: TestClient, password: str = "correct horse battery staple"):
    prepare_csrf(client)
    return client.post(
        "/auth/login", json={"email": "admin@example.com", "password": password}
    )


def test_resend_code_replaces_previous_code_after_cooldown(
    auth_environment: tuple[TestClient, FakeMailer, Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domains.auth import service

    clock = FakeClock()
    monkeypatch.setattr(service.time, "time", clock)
    client, mailer, _ = auth_environment
    assert start_pending_login(client).status_code == 202
    first_code = extract_code(mailer.messages[-1][2])

    too_soon = client.post("/auth/resend-code")
    assert too_soon.status_code == 429
    assert int(too_soon.headers["Retry-After"]) > 0
    assert len(mailer.messages) == 1

    clock.now += 61
    resent = client.post("/auth/resend-code")
    assert resent.status_code == 202
    assert len(mailer.messages) == 2
    second_code = extract_code(mailer.messages[-1][2])

    old_code = client.post(
        "/auth/verify-email-code", json={"email": "admin@example.com", "code": first_code}
    )
    if first_code != second_code:
        assert old_code.status_code == 401

    verified = client.post(
        "/auth/verify-email-code", json={"email": "admin@example.com", "code": second_code}
    )
    assert verified.status_code == 200
    # Tras verificar, el inicio pendiente se elimina: reenviar ya no envía nada.
    client.headers["x-csrf-token"] = client.cookies["qalab_csrf"]
    clock.now += 61
    assert client.post("/auth/resend-code").status_code == 202
    assert len(mailer.messages) == 2


def test_resend_code_is_limited_to_three_codes_per_window(
    auth_environment: tuple[TestClient, FakeMailer, Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domains.auth import service

    clock = FakeClock()
    monkeypatch.setattr(service.time, "time", clock)
    client, mailer, _ = auth_environment
    start_pending_login(client)
    for _ in range(2):
        clock.now += 61
        assert client.post("/auth/resend-code").status_code == 202
    clock.now += 61
    limited = client.post("/auth/resend-code")
    assert limited.status_code == 429
    assert len(mailer.messages) == 3


def test_resend_after_wrong_password_reveals_nothing_and_sends_nothing(
    auth_environment: tuple[TestClient, FakeMailer, Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domains.auth import service

    clock = FakeClock()
    monkeypatch.setattr(service.time, "time", clock)
    client, mailer, _ = auth_environment
    failed = start_pending_login(client, password="wrong password value")
    assert failed.status_code == 202
    assert "qalab_pending_login" in client.cookies

    clock.now += 61
    resent = client.post("/auth/resend-code")
    assert resent.status_code == 202
    assert resent.json() == client.post("/auth/resend-code").json()
    assert mailer.messages == []


def test_resend_requires_csrf(
    auth_environment: tuple[TestClient, FakeMailer, Session],
) -> None:
    client, _, _ = auth_environment
    start_pending_login(client)
    del client.headers["x-csrf-token"]
    assert client.post("/auth/resend-code").status_code == 403
