from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Protege los endpoints del servicio (obligatorio en producción)
    service_api_key: str = Field(default="", validation_alias="SERVICE_API_KEY")

    database_url: str = Field(
        default="postgresql://jhonocampo:yMSHGswzRNiu7DmzJ8zN@164.68.109.125:5434/WhatsappSender",
        validation_alias="DATABASE_URL",
    )

    data_dir: Path = Field(default=Path("/app/data"), validation_alias="DATA_DIR")
    default_data_dir: Path = Field(default=Path("/opt/default-data"), validation_alias="DEFAULT_DATA_DIR")
    csv_name: str = Field(default="grupos_chinatowm.csv", validation_alias="CSV_FILENAME")
    msg_dir: str | None = Field(default=None, validation_alias="MSG_DIR")

    evolution_api_url: str = Field(default="http://localhost:8080", validation_alias="EVOLUTION_API_URL")
    evolution_api_key: str = Field(default="", validation_alias="EVOLUTION_API_KEY")
    instance: str = Field(default="default", validation_alias="EVOLUTION_INSTANCE")
    evolution_request_origin: str = Field(default="", validation_alias="EVOLUTION_REQUEST_ORIGIN")

    delay_seg: int = Field(default=8, validation_alias="DELAY_SEG")
    extra_image_delay: float = Field(default=2.0, validation_alias="EXTRA_IMAGE_DELAY")

    scheduler_check_interval: int = Field(default=30, validation_alias="SCHEDULER_CHECK_INTERVAL")
    scheduler_history_poll: int = Field(default=15, validation_alias="SCHEDULER_HISTORY_POLL")

    timezone: str = Field(default="America/Santiago", validation_alias="TIMEZONE")

    cors_origins: str = Field(
        default="https://sender.jhonocampo.com",
        validation_alias="CORS_ORIGINS",
    )

    # JWT Config
    jwt_secret: str = Field(default="super-secret-key-change-in-production", validation_alias="JWT_SECRET")
    jwt_algorithm: str = Field(default="HS256", validation_alias="JWT_ALGORITHM")
    access_token_expire_minutes: int = Field(default=30, validation_alias="ACCESS_TOKEN_EXPIRE_MINUTES")
    refresh_token_expire_days: int = Field(default=7, validation_alias="REFRESH_TOKEN_EXPIRE_DAYS")

    # Crypto para API Keys
    crypto_key: str = Field(
        default="dGVzdC1jcnlwdG8ta2V5LWZvci1kZXZlbG9wbWVudC1vbmx5LTEyMzQ1Ng==",
        validation_alias="CRYPTO_KEY",
    )

    @property
    def evolution_request_origins_list(self) -> list[str]:
        raw = self.evolution_request_origin or ""
        return [p.strip().rstrip("/") for p in raw.split(",") if p.strip()]

    @property
    def csv_path(self) -> Path:
        return self.data_dir / self.csv_name


settings = Settings()
