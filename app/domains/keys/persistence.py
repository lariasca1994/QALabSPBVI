from collections.abc import Generator
from datetime import datetime
from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from sqlalchemy import (
    DateTime,
    Enum as SqlEnum,
    Engine,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    func,
    inspect,
    select,
    text,
    update,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    sessionmaker,
)

from app.core.config import engine_options, get_settings
from app.db.wake import retry_transient_connect
from app.db.models import KeyStatus

KeyValueType = String(255, collation="Latin1_General_100_BIN2").with_variant(
    String(255),
    "sqlite",
)


class DifeBase(DeclarativeBase):
    pass


class DiceBase(DeclarativeBase):
    pass


class DifeKey(DifeBase):
    __tablename__ = "dife_keys"
    __table_args__ = (
        UniqueConstraint("key_type", "key_value", name="uq_dife_key_type_value"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    key_type: Mapped[str] = mapped_column(String(32), nullable=False)
    key_value: Mapped[str] = mapped_column(KeyValueType, nullable=False)
    spbvi_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    deposit_product_id: Mapped[str] = mapped_column(String(100), nullable=False)
    owner_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    status: Mapped[KeyStatus] = mapped_column(
        SqlEnum(
            KeyStatus,
            values_callable=lambda values: [value.value for value in values],
            native_enum=False,
            length=32,
        ),
        nullable=False,
    )


class DifeKeyStatusEvent(DifeBase):
    __tablename__ = "dife_key_status_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    key_id: Mapped[int] = mapped_column(
        ForeignKey("dife_keys.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[KeyStatus] = mapped_column(
        SqlEnum(
            KeyStatus,
            values_callable=lambda values: [value.value for value in values],
            native_enum=False,
            length=32,
        ),
        nullable=False,
    )
    actor: Mapped[str] = mapped_column(String(16), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
    )


class KeyLifecycleEvent(DifeBase):
    __tablename__ = "key_lifecycle_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key_type: Mapped[str] = mapped_column(String(32), nullable=False)
    key_value: Mapped[str] = mapped_column(KeyValueType, nullable=False)
    spbvi_id: Mapped[str] = mapped_column(String(100), nullable=False)
    deposit_product_id: Mapped[str] = mapped_column(String(100), nullable=False)
    owner_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    actor_email: Mapped[str] = mapped_column(String(320), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(20), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
    )


class DiceKey(DiceBase):
    __tablename__ = "dice_keys"
    __table_args__ = (
        UniqueConstraint("key_type", "key_value", name="uq_dice_key_type_value"),
    )

    registration_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    key_type: Mapped[str] = mapped_column(String(32), nullable=False)
    key_value: Mapped[str] = mapped_column(String(255), nullable=False)
    spbvi_id: Mapped[str] = mapped_column(String(100), nullable=False)
    deposit_product_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    owner_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    status: Mapped[KeyStatus] = mapped_column(
        SqlEnum(
            KeyStatus,
            values_callable=lambda values: [value.value for value in values],
            native_enum=False,
            length=32,
        ),
        nullable=False,
    )


def _required_url(value: str, store_name: str) -> str:
    if not value:
        raise RuntimeError(f"La conexion de {store_name} no esta configurada.")
    return value


@lru_cache
def get_dife_engine():
    url = _required_url(get_settings().dife_database_url, "DIFE")
    # Azure SQL gratuito se pausa sin uso: la primera conexión reintenta mientras se reanuda.
    return retry_transient_connect(create_engine(url, **engine_options(url)))


@lru_cache
def get_dice_engine():
    url = _required_url(get_settings().dice_database_url, "DICE")
    return create_engine(url, **engine_options(url))


@lru_cache
def get_dife_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_dife_engine(), autoflush=False, expire_on_commit=False)


@lru_cache
def get_dice_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_dice_engine(), autoflush=False, expire_on_commit=False)


def get_dife_db() -> Generator[Session, None, None]:
    with get_dife_session_factory()() as session:
        yield session


def get_dice_db() -> Generator[Session, None, None]:
    with get_dice_session_factory()() as session:
        yield session


def ensure_dice_product_column(dice_engine: Engine) -> None:
    inspector = inspect(dice_engine)
    table_name = next(
        (
            name
            for name in inspector.get_table_names()
            if name.lower() == "dice_keys"
        ),
        None,
    )
    if table_name is None:
        return
    columns = {
        column["name"].lower()
        for column in inspector.get_columns(table_name)
    }
    if "deposit_product_id" in columns:
        return
    if dice_engine.dialect.name == "oracle":
        statement = (
            "ALTER TABLE dice_keys ADD (deposit_product_id VARCHAR2(100))"
        )
    elif dice_engine.dialect.name == "sqlite":
        statement = "ALTER TABLE dice_keys ADD COLUMN deposit_product_id VARCHAR(100)"
    else:
        raise RuntimeError(
            "La migracion de deposit_product_id solo esta habilitada para Oracle y SQLite."
        )
    with dice_engine.begin() as connection:
        connection.execute(text(statement))


def _table_columns(engine: Engine, table_name: str) -> tuple[str | None, set[str]]:
    inspector = inspect(engine)
    actual_name = next(
        (name for name in inspector.get_table_names() if name.lower() == table_name),
        None,
    )
    if actual_name is None:
        return None, set()
    columns = {
        column["name"].lower()
        for column in inspector.get_columns(actual_name)
    }
    return actual_name, columns


def ensure_key_lifecycle_columns(dife_engine: Engine, dice_engine: Engine) -> None:
    dife_table, dife_columns = _table_columns(dife_engine, "dife_keys")
    if dife_table is not None and "owner_email" not in dife_columns:
        with dife_engine.begin() as connection:
            connection.execute(
                text(f"ALTER TABLE {dife_table} ADD owner_email VARCHAR(320) NULL")
            )

    if dife_engine.dialect.name == "mssql":
        for table_name in ("dife_keys", "dife_key_status_events"):
            actual_table, columns = _table_columns(dife_engine, table_name)
            if actual_table is not None and "status" in columns:
                with dife_engine.begin() as connection:
                    connection.execute(
                        text(
                            f"ALTER TABLE {actual_table} "
                            "ALTER COLUMN status VARCHAR(32) NOT NULL"
                        )
                    )

    dice_table, dice_columns = _table_columns(dice_engine, "dice_keys")
    if dice_table is None:
        return
    if "owner_email" not in dice_columns:
        if dice_engine.dialect.name == "oracle":
            statement = f"ALTER TABLE {dice_table} ADD (owner_email VARCHAR2(320))"
        elif dice_engine.dialect.name == "sqlite":
            statement = (
                f"ALTER TABLE {dice_table} ADD COLUMN owner_email VARCHAR(320)"
            )
        else:
            raise RuntimeError(
                "La migracion de owner_email en DICE solo admite Oracle o SQLite."
            )
        with dice_engine.begin() as connection:
            connection.execute(text(statement))

    if dice_engine.dialect.name == "oracle":
        with dice_engine.begin() as connection:
            connection.execute(
                text(f"ALTER TABLE {dice_table} MODIFY (status VARCHAR2(32))")
            )


def backfill_dice_product_ids(dife_engine: Engine, dice_engine: Engine) -> None:
    with Session(dife_engine) as dife_db, Session(dice_engine) as dice_db:
        legacy_keys = dice_db.scalars(
            select(DiceKey).where(
                DiceKey.status == KeyStatus.CONFIRMED,
                DiceKey.deposit_product_id.is_(None),
            )
        ).all()
        for dice_key in legacy_keys:
            dife_key = dife_db.scalar(
                select(DifeKey).where(
                    DifeKey.key_type == dice_key.key_type,
                    DifeKey.key_value == dice_key.key_value,
                    DifeKey.spbvi_id == dice_key.spbvi_id,
                    DifeKey.status == KeyStatus.ACTIVE,
                )
            )
            if dife_key is not None:
                dice_db.execute(
                    update(DiceKey)
                    .where(DiceKey.registration_id == dice_key.registration_id)
                    .values(deposit_product_id=dife_key.deposit_product_id)
                )
        dice_db.commit()


def create_key_store_tables() -> None:
    dife_engine = get_dife_engine()
    dice_engine = get_dice_engine()
    DifeBase.metadata.create_all(bind=dife_engine)
    DiceBase.metadata.create_all(bind=dice_engine)
    ensure_dice_product_column(dice_engine)
    ensure_key_lifecycle_columns(dife_engine, dice_engine)
    DifeBase.metadata.create_all(bind=dife_engine)
    backfill_dice_product_ids(dife_engine, dice_engine)


def close_key_store_engines() -> None:
    for engine_factory in (get_dife_engine, get_dice_engine):
        if engine_factory.cache_info().currsize:
            engine_factory().dispose()
            engine_factory.cache_clear()
    get_dife_session_factory.cache_clear()
    get_dice_session_factory.cache_clear()


DifeSession = Annotated[Session, Depends(get_dife_db)]
DiceSession = Annotated[Session, Depends(get_dice_db)]
