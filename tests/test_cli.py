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


def test_seed_program_creates_epic_imports_program_and_is_idempotent(tmp_path, monkeypatch) -> None:
    import mongomock

    from app.cli import seed_program

    engine = create_engine(f"sqlite:///{tmp_path / 'seed.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([
            User(email="admin@example.com", display_name="Admin", password_hash="x", role=UserRole.ADMIN),
            User(email="manager@example.com", display_name="Manager", password_hash="x", role=UserRole.ADMINISTRADOR),
            User(email="tester@example.com", display_name="Tester", password_hash="x", role=UserRole.USUARIO),
        ])
        db.commit()
    database = mongomock.MongoClient()["seed_test"]
    sent: list[str] = []

    class Mailer:
        def send(self, *, recipient: str, subject: str, body: str, html: str | None = None) -> None:
            sent.append(recipient)

    monkeypatch.setattr("app.cli.SessionLocal", lambda: Session(engine))
    monkeypatch.setattr("app.cli.get_qa_database", lambda: database)
    monkeypatch.setattr("app.cli.get_mailer", Mailer)
    program = "qa_programs/iso20022-breb-rest-json.json"

    try:
        assert seed_program(program, "tester@example.com", "manager@example.com") == 1
        assert database.epics.count_documents({}) == 0

        assert seed_program(program, "admin@example.com", "manager@example.com") == 0
        epic = database.epics.find_one()
        assert len(epic["members"]) == 3
        assert database.work_items.count_documents({"kind": "story"}) == 19
        assert database.work_items.count_documents({"kind": "test_case"}) == 55
        assert database.work_items.count_documents({"kind": "task"}) == 22
        # Un aviso de épica y un resumen de importación, a los tres integrantes.
        assert len(sent) == 6

        assert seed_program(program, "admin@example.com", "manager@example.com") == 0
        assert database.epics.count_documents({}) == 1
        assert database.work_items.count_documents({}) == 96

        # Con --actualizar, un CP cuyo JSON cambió en el programa pasa a una versión nueva.
        database.work_items.update_one({"external_key": "TC-001"}, {"$set": {"request.path": "/viejo"}})
        assert seed_program(program, "admin@example.com", "manager@example.com", update_existing=True) == 0
        tc001 = database.work_items.find_one({"external_key": "TC-001"})
        assert tc001["request"]["path"] == "/payments"
        assert tc001["version"] == 2
        assert tc001["versions"][0]["request"]["path"] == "/viejo"
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
