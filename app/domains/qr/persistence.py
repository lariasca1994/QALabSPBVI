"""Base de cobros QR: MySQL (TiDB Cloud en la nube), dedicada y aislada de las demás.

Guarda los cobros dinámicos (QR de un solo uso). Los QR estáticos no se guardan: su
contenido firmado basta. Fechas como epoch en segundos para no depender de zonas
horarias entre MySQL y SQLite (pruebas).
"""

from collections.abc import Generator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from sqlalchemy import BigInteger, Integer, String, Text, create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app.core.config import engine_options, get_settings


class QrBase(DeclarativeBase):
    pass


class QrCharge(QrBase):
    """Cobro dinámico: pending → reserved → paid, o expired / cancelled."""

    __tablename__ = "qr_charges"

    charge_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    spbvi_id: Mapped[str] = mapped_column(String(100), nullable=False)
    key_type: Mapped[str] = mapped_column(String(32), nullable=False)
    key_value: Mapped[str] = mapped_column(String(255), nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reference: Mapped[str | None] = mapped_column(String(25), nullable=True)
    merchant_name: Mapped[str] = mapped_column(String(25), nullable=False)
    merchant_city: Mapped[str] = mapped_column(String(15), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    created_by: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    # Pago en curso o hecho: cuenta que paga, cuándo se reservó y operation_id del pago.
    payer_account_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reserved_at_epoch: Mapped[int | None] = mapped_column(Integer, nullable=True)
    operation_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    paid_at_epoch: Mapped[int | None] = mapped_column(Integer, nullable=True)


def _connect_args(url: str) -> dict:
    """TiDB Cloud exige TLS; un MySQL local (Docker) no lo usa."""
    parsed = make_url(url)
    if not parsed.drivername.startswith("mysql") or parsed.host in (None, "localhost", "127.0.0.1", "mysql"):
        return {}
    import certifi

    return {"ssl": {"ca": certifi.where()}}


@lru_cache
def get_qr_engine() -> Engine:
    url = get_settings().qr_database_url
    settings = get_settings()
    if not url:
        raise RuntimeError("La conexion de la base de cobros QR no esta configurada.")
    # En la nube los cobros no pueden vivir en SQLite: el contenedor escala a cero y se perderían.
    if url.startswith("sqlite") and settings.app_env.casefold() not in {"local", "development", "test"}:
        raise RuntimeError("En la nube QR_DATABASE_URL debe apuntar a la base MySQL de cobros.")
    options = engine_options(url)
    extra = _connect_args(url)
    if extra:
        options["connect_args"] = {**options.get("connect_args", {}), **extra}
    engine = create_engine(url, **options)
    QrBase.metadata.create_all(bind=engine)
    return engine


@lru_cache
def get_qr_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_qr_engine(), autoflush=False, expire_on_commit=False)


def get_qr_db() -> Generator[Session, None, None]:
    with get_qr_session_factory()() as session:
        yield session


QrSession = Annotated[Session, Depends(get_qr_db)]
