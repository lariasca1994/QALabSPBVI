import getpass
import sys

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.exc import SQLAlchemyError

from app.db import models  # noqa: F401
from app.db.base import Base
from app.db.models import User, UserRole
from app.db.session import SessionLocal, engine
from app.domains.auth.service import create_user
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
    print(
        "Uso: python -m app.cli create-initial-admin | "
        "python -m app.cli create-admin | "
        "python -m app.cli init-key-stores"
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
