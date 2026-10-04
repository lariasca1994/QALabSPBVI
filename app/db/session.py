from collections.abc import Generator

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import engine_options, get_settings

database_url = get_settings().database_url
engine = create_engine(database_url, **engine_options(database_url))
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def ensure_payment_type_column(database_engine: Engine = engine) -> None:
    if "payments" not in inspect(database_engine).get_table_names():
        return
    columns = {
        column["name"]
        for column in inspect(database_engine).get_columns("payments")
    }
    if "payment_type" in columns:
        return
    if database_engine.dialect.name not in {"postgresql", "sqlite"}:
        raise RuntimeError(
            "La migracion de payment_type solo esta habilitada para PostgreSQL y SQLite."
        )
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE payments ADD COLUMN payment_type "
                "VARCHAR(20) NOT NULL DEFAULT 'intra_spbvi'"
            )
        )


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
