import getpass
import json
import sys
from pathlib import Path

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.exc import SQLAlchemyError

from app.db import models  # noqa: F401
from app.db.base import Base
from app.db.models import User, UserRole
from app.db.session import SessionLocal, engine
from app.api.schemas import ProgramImportRequest
from app.core.mailer import get_mailer
from app.domains.auth.service import create_user, normalize_email
from app.domains.qa.mongo import get_qa_database
from app.domains.qa.service import (
    QaForbiddenError,
    QaValidationError,
    create_epic,
    import_program,
)
from app.domains.keys.persistence import create_key_store_tables


def create_initial_admin() -> int:
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        user_count = db.scalar(select(func.count()).select_from(User))
        if user_count:
            print("Ya hay usuarios. El bootstrap solo se permite en una base vacia.")
            return 1

        email = input("Correo del admin inicial: ").strip()
        try:
            TypeAdapter(EmailStr).validate_python(email)
        except ValidationError:
            print("El correo no tiene un formato valido.")
            return 1

        password = getpass.getpass("Contrasena inicial (minimo 12 caracteres): ")
        confirmation = getpass.getpass("Repite la contrasena: ")
        if len(password) < 12 or len(password) > 128:
            print("La contrasena debe tener entre 12 y 128 caracteres.")
            return 1
        if password != confirmation:
            print("Las contrasenas no coinciden.")
            return 1

        try:
            create_user(
                db,
                email=email,
                display_name="Administrador inicial",
                password=password,
                role=UserRole.ADMIN,
            )
        except IntegrityError:
            db.rollback()
            print("No se pudo crear el admin inicial por conflicto en la base.")
            return 1

    print("Admin inicial creado. Configura Brevo para habilitar el MFA por correo.")
    return 0


def create_admin_account() -> int:
    Base.metadata.create_all(bind=engine)
    email = input("Correo del nuevo admin visible: ").strip()
    try:
        TypeAdapter(EmailStr).validate_python(email)
    except ValidationError:
        print("El correo no tiene un formato valido.")
        return 1

    display_name = input("Nombre visible del admin: ").strip()
    if not display_name or len(display_name) > 120:
        print("El nombre debe tener entre 1 y 120 caracteres.")
        return 1

    password = getpass.getpass("Contrasena nueva (minimo 12 caracteres): ")
    confirmation = getpass.getpass("Repite la contrasena: ")
    if len(password) < 12 or len(password) > 128:
        print("La contrasena debe tener entre 12 y 128 caracteres.")
        return 1
    if password != confirmation:
        print("Las contrasenas no coinciden.")
        return 1

    with SessionLocal() as db:
        existing_user = db.scalar(select(User).where(User.email == email.casefold()))
        if existing_user is not None:
            print("Ya existe una cuenta con ese correo; no se hicieron cambios.")
            return 1
        try:
            create_user(
                db,
                email=email,
                display_name=display_name,
                password=password,
                role=UserRole.ADMIN,
            )
        except IntegrityError:
            db.rollback()
            print("No se pudo crear la cuenta por un conflicto en la base.")
            return 1

    print("Cuenta admin creada. Es visible para el equipo y queda registrada en la base.")
    return 0


def seed_program(program_path: str, admin_email: str, manager_email: str) -> int:
    """Crea la épica del programa (si no existe) y carga sus HU, CP y tareas.

    Usa las mismas reglas de la plataforma: la épica la crea un admin y la importación
    la hace un administrador integrante. La épica asocia a todos los usuarios activos.
    Es idempotente: reejecutarlo reutiliza la épica por título y omite lo ya cargado.
    """
    try:
        raw_program = json.loads(Path(program_path).read_text(encoding="utf-8"))
        program = ProgramImportRequest.model_validate(raw_program).model_dump()
    except (OSError, ValueError) as error:
        print(f"No se pudo leer el programa: {error}")
        return 1
    epic_info = raw_program.get("epic") or {}
    if not epic_info.get("title") or not epic_info.get("description"):
        print("El programa no trae el bloque 'epic' con título y descripción.")
        return 1

    database = get_qa_database()
    mailer = get_mailer()
    with SessionLocal() as db:
        users = db.scalars(select(User).where(User.is_active.is_(True))).all()
        by_email = {user.email: user for user in users}
        admin = by_email.get(normalize_email(admin_email))
        manager = by_email.get(normalize_email(manager_email))
        if admin is None or admin.role is not UserRole.ADMIN:
            print("El primer correo debe ser de un admin activo.")
            return 1
        if manager is None or manager.role is not UserRole.ADMINISTRADOR:
            print("El segundo correo debe ser de un administrador activo.")
            return 1

        epic = database.epics.find_one({"title": epic_info["title"].strip()})
        if epic is None:
            epic = create_epic(
                database,
                db,
                title=epic_info["title"],
                description=epic_info["description"],
                member_ids=[user.id for user in users],
                actor=admin,
                mailer=mailer,
            )
            print(f"Épica {epic['key']} creada con {len(epic['members'])} integrantes.")
        else:
            print(f"Épica {epic['key']} ya existía; se reutiliza.")

        try:
            summary = import_program(
                database,
                epic_key=epic["key"],
                actor=manager,
                mailer=mailer,
                program=program,
            )
        except QaForbiddenError:
            print("El administrador no es integrante de la épica; asócialo y reintenta.")
            return 1
        except QaValidationError as error:
            print(f"El programa no es válido: {error}")
            return 1

    created, skipped = summary["created"], summary["skipped"]
    print(
        f"Creados: {created['stories']} HU, {created['test_cases']} CP, {created['tasks']} tareas. "
        f"Omitidos por existir: {skipped['stories']} HU, {skipped['test_cases']} CP, "
        f"{skipped['tasks']} tareas. Aviso por correo: {summary['notification_status']}."
    )
    return 0


def main() -> int:
    arguments = sys.argv[1:]
    if arguments == ["create-initial-admin"]:
        return create_initial_admin()
    if arguments == ["create-admin"]:
        return create_admin_account()
    if arguments == ["init-key-stores"]:
        try:
            create_key_store_tables()
        except (SQLAlchemyError, RuntimeError) as error:
            print(
                "No se pudieron inicializar las tablas dedicadas de DIFE/DICE "
                f"({type(error).__name__}). Verifica las conexiones configuradas."
            )
            return 1
        print("Tablas dedicadas de DIFE (SQL Server) y DICE (Oracle) listas.")
        return 0
    if len(arguments) == 4 and arguments[0] == "seed-program":
        return seed_program(*arguments[1:])
    print(
        "Uso: python -m app.cli create-initial-admin | "
        "python -m app.cli create-admin | "
        "python -m app.cli init-key-stores | "
        "python -m app.cli seed-program RUTA CORREO_ADMIN CORREO_ADMINISTRADOR"
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
