from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "QALabSPBVI"
    app_env: str = "local"
    database_url: str = "sqlite:///./qalab.db"
    dife_database_url: str = ""
    dice_database_url: str = ""
    mongodb_url: str = "mongodb://127.0.0.1:27017"
    mongodb_database: str = "qalabspbvi_qa"
    qa_target_base_url: str = "http://127.0.0.1:8000"
    # Límite Bre-B de 1.000 UVB por operación (parámetro configurable del laboratorio).
    # SUPUESTO: valor de la UVB en centavos; actualizarlo con el valor oficial vigente.
    payment_limit_uvb: int = 1000
    uvb_value_cents: int = 1_155_200
    # "null": sin pool, cada solicitud abre y cierra su conexión. En la nube evita sesiones
    # ociosas que impiden la pausa automática de Azure SQL (oferta gratuita) y la
    # suspensión de Neon: las bases solo se activan cuando alguien usa la aplicación.
    database_pool: str = "queue"
    auth_secret_key: str = ""
    brevo_api_key: str = ""
    brevo_sender_email: str = ""
    brevo_sender_name: str = "QALabSPBVI"
    # Avisos QA por AWS SQS + Lambda (vacío: se envían directo por Brevo).
    notifications_queue_url: str = ""
    aws_region: str = "us-east-1"
    # Gateway ISO 20022 en Render (vacío: el adaptador corre en proceso).
    iso_gateway_url: str = ""
    iso_gateway_token: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def engine_options(url: str) -> dict:
    """Opciones comunes de create_engine según DATABASE_POOL."""
    from sqlalchemy.pool import NullPool

    options: dict = {}
    if url.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False}
    if get_settings().database_pool == "null":
        options["poolclass"] = NullPool
        return options
    # pool_pre_ping descarta conexiones cortadas por el servidor (p. ej. Neon suspende el
    # cómputo por inactividad); pool_recycle las renueva antes.
    options.update(pool_pre_ping=True, pool_recycle=240)
    return options
