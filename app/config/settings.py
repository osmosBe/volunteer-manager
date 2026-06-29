from functools import lru_cache
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _parse_csv(value: str | list[str] | tuple[str, ...] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return [str(item).strip() for item in value if str(item).strip()]


CsvList = Annotated[list[str], BeforeValidator(_parse_csv)]
AuthMode = Literal["disabled", "easyauth"]


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    app_name: str = "ST. PRIDE Volunteer Management"
    app_version: str = "0.1.0"
    environment: str = "development"
    database_url: str = Field(default="sqlite:////data/app.db")

    app_base_url: str = "http://localhost:8000"
    auth_mode: AuthMode = "disabled"
    debug: bool = False
    admin_allowed_emails: CsvList = Field(default_factory=list)
    admin_allowed_group_ids: CsvList = Field(default_factory=list)

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()
