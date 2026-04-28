from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Protege los endpoints del servicio (obligatorio en producción)
    service_api_key: str = Field(default="", validation_alias="SERVICE_API_KEY")

    data_dir: Path = Field(default=Path("/app/data"), validation_alias="DATA_DIR")
    csv_name: str = Field(default="grupos_chinatowm.csv", validation_alias="CSV_FILENAME")
    msg_dir: str | None = Field(default=None, validation_alias="MSG_DIR")

    evolution_api_url: str = Field(default="http://localhost:8080", validation_alias="EVOLUTION_API_URL")
    evolution_api_key: str = Field(default="", validation_alias="EVOLUTION_API_KEY")
    instance: str = Field(default="default", validation_alias="EVOLUTION_INSTANCE")

    delay_seg: int = Field(default=8, validation_alias="DELAY_SEG")
    extra_image_delay: float = Field(default=2.0, validation_alias="EXTRA_IMAGE_DELAY")

    cors_origins: str = Field(default="", validation_alias="CORS_ORIGINS")

    @property
    def csv_path(self) -> Path:
        return self.data_dir / self.csv_name


settings = Settings()
