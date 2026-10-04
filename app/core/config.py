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
    auth_secret_key: str = ""
    brevo_api_key: str = ""
    brevo_sender_email: str = ""
    brevo_sender_name: str = "QALabSPBVI"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
