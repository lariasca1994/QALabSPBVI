from collections.abc import Iterator

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.cli import create_admin_account
from app.db.base import Base
from app.db.models import User, UserRole
from app.domains.auth.service import verify_password


def test_create_admin_account_prompts_for_fresh_password_and_persists_hash(
    tmp_path,
    monkeypatch,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'admins.db'}")
    Base.metadata.create_all(engine)
    inputs: Iterator[str] = iter(("admin@example.com", "Admin QA"))
    passwords: Iterator[str] = iter(("fresh-local-password-2026", "fresh-local-password-2026"))
    monkeypatch.setattr("app.cli.engine", engine)
    monkeypatch.setattr("app.cli.SessionLocal", lambda: Session(engine))
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    monkeypatch.setattr("app.cli.getpass.getpass", lambda _: next(passwords))

    try:
        assert create_admin_account() == 0
        with Session(engine) as db:
            user = db.scalar(select(User).where(User.email == "admin@example.com"))
            assert user is not None
            assert user.role is UserRole.ADMIN
            assert user.is_active is True
            assert verify_password(user.password_hash, "fresh-local-password-2026")
            assert user.password_hash != "fresh-local-password-2026"
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_create_admin_account_rejects_password_mismatch_without_creating_user(
    tmp_path,
    monkeypatch,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'admins.db'}")
    Base.metadata.create_all(engine)
    inputs: Iterator[str] = iter(("admin@example.com", "Admin QA"))
    passwords: Iterator[str] = iter(("fresh-local-password-2026", "different-password-2026"))
    monkeypatch.setattr("app.cli.engine", engine)
    monkeypatch.setattr("app.cli.SessionLocal", lambda: Session(engine))
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    monkeypatch.setattr("app.cli.getpass.getpass", lambda _: next(passwords))

    try:
        assert create_admin_account() == 1
        with Session(engine) as db:
            assert db.scalar(select(User)) is None
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
