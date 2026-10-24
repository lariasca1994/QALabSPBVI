"""Verificación en dos pasos con app autenticadora (TOTP) junto al código por correo."""

import time

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.domains.auth import two_factor
from tests.test_auth import FakeMailer, auth_environment, prepare_csrf, sign_in  # noqa: F401

EMAIL = "admin@example.com"
PASSWORD = "correct horse battery staple"


def _login(client: TestClient) -> dict:
    prepare_csrf(client)
    response = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert response.status_code == 202
    return response.json()


def _code(secret: str, offset: int = 0) -> str:
    return two_factor.totp_code(secret, int(time.time() // two_factor.TOTP_PERIOD) + offset)


def test_totp_matches_rfc_6238_reference() -> None:
    # Vector de prueba del RFC 6238 (SHA-1, T = 59 s): 94287082 → los 6 últimos dígitos.
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert two_factor.totp_code(secret, 59 // 30) == "287082"
    assert two_factor.matching_step(secret, "287082", 0, now=59) == 1
    assert two_factor.matching_step(secret, "287082", 1, now=59) is None  # ya usado


def test_secret_is_stored_encrypted(auth_environment) -> None:  # noqa: F811
    encrypted = two_factor.encrypt_secret("JBSWY3DPEHPK3PXP")
    assert "JBSWY3DPEHPK3PXP" not in encrypted
    assert two_factor.decrypt_secret(encrypted) == "JBSWY3DPEHPK3PXP"


def test_first_login_can_choose_the_app_and_then_it_is_asked_by_default(
    auth_environment: tuple[TestClient, FakeMailer, Session],  # noqa: F811
) -> None:
    client, mailer, _ = auth_environment
    first = _login(client)
    assert first["method"] == "email"
    assert first["methods"]["chosen"] is False

    setup = client.post("/auth/mfa/totp/setup")
    assert setup.status_code == 200
    secret = setup.json()["secret"]
    assert setup.json()["uri"].startswith("otpauth://totp/")
    assert client.post("/auth/verify-totp", json={"code": _code(secret)}).status_code == 200
    client.headers["x-csrf-token"] = client.cookies.get("qalab_csrf")
    assert client.get("/auth/mfa").json() == {
        "chosen": True, "totp": True, "email": False, "preferred": "totp",
    }

    # Siguiente ingreso: se pide la app y no se envía correo; el correo no está activo.
    client.post("/auth/logout")
    sent = len(mailer.messages)
    second = _login(client)
    assert second["method"] == "totp"
    assert len(mailer.messages) == sent
    assert client.post("/auth/resend-code").status_code == 202
    assert len(mailer.messages) == sent
    # El mismo código no se acepta dos veces; el del paso siguiente sí.
    assert client.post("/auth/verify-totp", json={"code": _code(secret)}).status_code == 401
    assert client.post("/auth/verify-totp", json={"code": _code(secret, 1)}).status_code == 200


def test_five_wrong_app_codes_lock_the_app_for_fifteen_minutes(
    auth_environment: tuple[TestClient, FakeMailer, Session],  # noqa: F811
) -> None:
    client, _, _ = auth_environment
    _login(client)
    secret = client.post("/auth/mfa/totp/setup").json()["secret"]
    wrong = "000000" if _code(secret) != "000000" else "111111"
    for _ in range(4):
        assert client.post("/auth/verify-totp", json={"code": wrong}).status_code == 401
    assert client.post("/auth/verify-totp", json={"code": wrong}).status_code == 423
    assert client.post("/auth/verify-totp", json={"code": _code(secret)}).status_code == 423


def test_email_user_adds_the_app_from_security_and_chooses_the_default(
    auth_environment: tuple[TestClient, FakeMailer, Session],  # noqa: F811
) -> None:
    client, mailer, _ = auth_environment
    sign_in(client, mailer)
    assert client.get("/auth/mfa").json()["preferred"] == "email"

    secret = client.post("/auth/mfa/totp/start").json()["secret"]
    assert client.post("/auth/mfa/totp/activate", json={"code": "123"}).status_code == 422
    activated = client.post("/auth/mfa/totp/activate", json={"code": _code(secret)})
    assert activated.status_code == 200
    # Ya usaba el correo: sigue siendo el preferido hasta que la persona lo cambie.
    assert activated.json() == {"chosen": True, "totp": True, "email": True, "preferred": "email"}
    assert client.post("/auth/mfa/totp/start").status_code == 409

    preferred = client.post("/auth/mfa/preferred", json={"method": "totp"})
    assert preferred.json()["preferred"] == "totp"


def test_login_without_password_cannot_set_up_the_app(
    auth_environment: tuple[TestClient, FakeMailer, Session],  # noqa: F811
) -> None:
    client, _, _ = auth_environment
    prepare_csrf(client)
    assert client.post("/auth/mfa/totp/setup").status_code == 401
    assert client.post("/auth/verify-totp", json={"code": "123456"}).status_code == 401
