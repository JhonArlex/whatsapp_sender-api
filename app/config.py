from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Protege los endpoints del servicio (obligatorio en producción)
    service_api_key: str = Field(default="", validation_alias="SERVICE_API_KEY")

    data_dir: Path = Field(default=Path("/app/data"), validation_alias="DATA_DIR")
    # Coincide con COPY en Dockerfile (Dokploy / volumen vacío cuando no hay msg en data/)
    default_data_dir: Path = Field(default=Path("/opt/default-data"), validation_alias="DEFAULT_DATA_DIR")
    csv_name: str = Field(default="grupos_chinatowm.csv", validation_alias="CSV_FILENAME")
    msg_dir: str | None = Field(default=None, validation_alias="MSG_DIR")

    evolution_api_url: str = Field(default="http://localhost:8080", validation_alias="EVOLUTION_API_URL")
    evolution_api_key: str = Field(default="", validation_alias="EVOLUTION_API_KEY")
    instance: str = Field(default="default", validation_alias="EVOLUTION_INSTANCE")
    # Debe coincidir con uno de los orígenes permitidos en Evolution (CORS_ORIGIN), p. ej.
    # https://whatsapp.tu-dominio.com — sin esto, algunas versiones de Evolution responden 500
    # "Not allowed by CORS" a peticiones servidor-servidor sin Origin permitido.
    evolution_request_origin: str = Field(default="", validation_alias="EVOLUTION_REQUEST_ORIGIN")

    delay_seg: int = Field(default=8, validation_alias="DELAY_SEG")
    extra_image_delay: float = Field(default=2.0, validation_alias="EXTRA_IMAGE_DELAY")

    scheduler_check_interval: int = Field(default=30, validation_alias="SCHEDULER_CHECK_INTERVAL")

    cors_origins: str = Field(default="", validation_alias="CORS_ORIGINS")

    @property
    def evolution_request_origins_list(self) -> list[str]:
        """Orígenes a probar contra CORS en Evolution (split por coma, trim incluido)."""
        raw = self.evolution_request_origin or ""
        return [p.strip().rstrip("/") for p in raw.split(",") if p.strip()]

    @property
    def csv_path(self) -> Path:
        return self.data_dir / self.csv_name


settings = Settings()
