"""Cambio y recuperación de contraseña, y activación de cuentas. Sin correos reales: FakeMailer."""

from fastapi.testclient import TestClient

from app.db.models import UserRole
from app.domains.auth.service import create_user
from app.main import app
from tests.test_auth import FakeMailer, auth_environment, extract_code, prepare_csrf, sign_in  # noqa: F401

NEW_PASSWORD = "otra clave larga y segura 2026"


def test_change_password_requires_current_and_closes_other_sessions(auth_environment) -> None:
    client, mailer, _ = auth_environment
    sign_in(client, mailer)
    other = TestClient(app, raise_server_exceptions=False)
    sign_in(other, mailer)
    assert other.get("/auth/me").status_code == 200

    wrong = client.post("/auth/password", json={"current_password": "no es esta", "new_password": NEW_PASSWORD})
    assert wrong.status_code == 400 and wrong.json()["detail"] == "La contraseña actual no es correcta."
    same = client.post(
        "/auth/password",
        json={"current_password": "correct horse battery staple", "new_password": "correct horse battery staple"},
    )
    assert same.status_code == 400
    short = client.post("/auth/password", json={"current_password": "correct horse battery staple", "new_password": "corta"})
    assert short.status_code == 422

    changed = client.post(
        "/auth/password", json={"current_password": "correct horse battery staple", "new_password": NEW_PASSWORD}
    )
    assert changed.status_code == 200 and changed.json()["closed_sessions"] == 1
    assert client.get("/auth/me").status_code == 200
    assert other.get("/auth/me").status_code == 401
    other.close()

    client.post("/auth/logout")
    prepare_csrf(client)
    old = client.post("/auth/login", json={"email": "admin@example.com", "password": "correct horse battery staple"})
    assert old.status_code == 401
    sign_in(client, mailer, password=NEW_PASSWORD)


def test_password_reset_with_emailed_code(auth_environment) -> None:
    client, mailer, _ = auth_environment
    sign_in(client, mailer)
    prepare_csrf(client)

    requested = client.post("/auth/password-reset/request", json={"email": "admin@example.com"})
    assert requested.status_code == 202
    assert mailer.messages[-1][1] == "Código para restablecer tu contraseña de QALabSPBVI"
    code = extract_code(mailer.messages[-1][2])

    bad = client.post(
        "/auth/password-reset/confirm",
        json={"email": "admin@example.com", "code": "000000" if code != "000000" else "111111", "new_password": NEW_PASSWORD},
    )
    assert bad.status_code == 400 and bad.json()["detail"] == "El código no es válido o ya venció."
    ok = client.post(
        "/auth/password-reset/confirm", json={"email": "admin@example.com", "code": code, "new_password": NEW_PASSWORD}
    )
    assert ok.status_code == 200
    # Todas las sesiones se cierran y el código no se reutiliza.
    assert client.get("/auth/me").status_code == 401
    prepare_csrf(client)
    again = client.post(
        "/auth/password-reset/confirm", json={"email": "admin@example.com", "code": code, "new_password": "una tercera clave segura"}
    )
    assert again.status_code == 400
    sign_in(client, mailer, password=NEW_PASSWORD)


def test_password_reset_reveals_nothing_and_is_rate_limited(auth_environment) -> None:
    client, mailer, _ = auth_environment
    prepare_csrf(client)
    unknown = client.post("/auth/password-reset/request", json={"email": "nadie@example.com"})
    known = client.post("/auth/password-reset/request", json={"email": "admin@example.com"})
    assert unknown.status_code == known.status_code == 202
    assert unknown.json() == known.json()
    assert [message[0] for message in mailer.messages] == ["admin@example.com"]

    for _ in range(2):
        assert client.post("/auth/password-reset/request", json={"email": "admin@example.com"}).status_code == 202
    assert client.post("/auth/password-reset/request", json={"email": "admin@example.com"}).status_code == 429
    # Solo el último código sirve.
    first, last = extract_code(mailer.messages[0][2]), extract_code(mailer.messages[-1][2])
    if first != last:
        stale = client.post(
            "/auth/password-reset/confirm", json={"email": "admin@example.com", "code": first, "new_password": NEW_PASSWORD}
        )
        assert stale.status_code == 400


def test_reset_code_locks_after_five_wrong_attempts(auth_environment) -> None:
    client, mailer, _ = auth_environment
    prepare_csrf(client)
    client.post("/auth/password-reset/request", json={"email": "admin@example.com"})
    code = extract_code(mailer.messages[-1][2])
    wrong = "123456" if code != "123456" else "654321"
    for _ in range(5):
        client.post("/auth/password-reset/confirm", json={"email": "admin@example.com", "code": wrong, "new_password": NEW_PASSWORD})
    locked = client.post("/auth/password-reset/confirm", json={"email": "admin@example.com", "code": code, "new_password": NEW_PASSWORD})
    assert locked.status_code == 400


def test_account_activation_rules(auth_environment) -> None:
    client, mailer, db = auth_environment
    manager = create_user(db, email="manager@example.com", display_name="Manager", password="manager password 2026", role=UserRole.ADMINISTRADOR)
    tester = create_user(db, email="tester@example.com", display_name="Tester", password="tester password 2026", role=UserRole.USUARIO)
    other_manager = create_user(db, email="other@example.com", display_name="Other", password="other password 2026", role=UserRole.ADMINISTRADOR)

    tester_client = TestClient(app, raise_server_exceptions=False)
    sign_in(tester_client, mailer, email="tester@example.com", password="tester password 2026")
    assert tester_client.patch(f"/auth/users/{manager.id}", json={"is_active": False}).status_code == 403

    manager_client = TestClient(app, raise_server_exceptions=False)
    sign_in(manager_client, mailer, email="manager@example.com", password="manager password 2026")
    assert manager_client.patch(f"/auth/users/{other_manager.id}", json={"is_active": False}).status_code == 403
    assert manager_client.patch(f"/auth/users/{manager.id}", json={"is_active": False}).status_code == 403
    deactivated = manager_client.patch(f"/auth/users/{tester.id}", json={"is_active": False})
    assert deactivated.status_code == 200 and deactivated.json()["is_active"] is False
    # La sesión del usuario desactivado se cierra y ya no puede iniciar sesión.
    assert tester_client.get("/auth/me").status_code == 401
    prepare_csrf(tester_client)
    assert tester_client.post("/auth/login", json={"email": "tester@example.com", "password": "tester password 2026"}).status_code == 401
    listed = {user["email"]: user["is_active"] for user in manager_client.get("/auth/users").json()}
    assert listed["tester@example.com"] is False

    sign_in(client, mailer)
    assert client.patch(f"/auth/users/{other_manager.id}", json={"is_active": False}).json()["is_active"] is False
    assert client.patch(f"/auth/users/{tester.id}", json={"is_active": True}).json()["is_active"] is True
    assert client.patch("/auth/users/9999", json={"is_active": True}).status_code == 404
    admin_id = next(user["id"] for user in client.get("/auth/users").json() if user["role"] == "admin")
    assert client.patch(f"/auth/users/{admin_id}", json={"is_active": False}).status_code == 403
    sign_in(tester_client, mailer, email="tester@example.com", password="tester password 2026")
    tester_client.close()
    manager_client.close()


def test_public_signup_creates_usuario_without_epic_and_without_email(auth_environment) -> None:
    client, mailer, _ = auth_environment
    prepare_csrf(client)
    created = client.post(
        "/auth/register",
        json={"email": "Nueva@Example.com", "display_name": "  Persona Demo ", "password": "clave de la demo 2026"},
    )
    assert created.status_code == 201
    assert "asignarte a una épica" in created.json()["message"]
    assert mailer.messages == []
    duplicate = client.post(
        "/auth/register", json={"email": "nueva@example.com", "display_name": "Otra", "password": "clave de la demo 2026"}
    )
    assert duplicate.status_code == 409 and duplicate.json()["detail"] == "Ya existe una cuenta con ese correo."
    assert client.post("/auth/register", json={"email": "x@example.com", "display_name": "X", "password": "corta"}).status_code == 422

    # Nunca crea admins ni administradores, aunque se pida.
    forged = client.post(
        "/auth/register",
        json={"email": "rol@example.com", "display_name": "Rol", "password": "clave de la demo 2026", "role": "admin"},
    )
    assert forged.status_code == 201
    sign_in(client, mailer)
    roles = {user["email"]: user["role"] for user in client.get("/auth/users").json()}
    assert roles["nueva@example.com"] == "usuario" and roles["rol@example.com"] == "usuario"

    # La cuenta nueva inicia sesión con MFA.
    other = TestClient(app, raise_server_exceptions=False)
    sign_in(other, mailer, email="nueva@example.com", password="clave de la demo 2026")
    assert other.get("/auth/me").json()["role"] == "usuario"
    other.close()


def test_public_signup_is_rate_limited_per_connection(auth_environment) -> None:
    client, _, _ = auth_environment
    prepare_csrf(client)
    codes = [
        client.post("/auth/register", json={"email": f"demo{n}@example.com", "display_name": "Demo", "password": "clave de la demo 2026"}).status_code
        for n in range(6)
    ]
    assert codes == [201] * 5 + [429]


def test_epic_counts_show_who_is_pending_assignment(auth_environment) -> None:
    import mongomock

    from app.domains.qa.mongo import get_qa_database

    client, mailer, _ = auth_environment
    database = mongomock.MongoClient()["epic_counts"]
    database.epics.insert_many([
        {"key": "EP-1", "members": [{"user_id": 1}, {"user_id": 2}]},
        {"key": "EP-2", "members": [{"user_id": 1}]},
    ])
    app.dependency_overrides[get_qa_database] = lambda: database
    sign_in(client, mailer)
    assert client.get("/qa/members/epic-counts").json() == {"1": 2, "2": 1}
