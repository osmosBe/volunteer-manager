from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    app_name: str = "ST. PRIDE Volunteer Management"
    app_version: str = "0.1.0"
    environment: str = "development"
    database_url: str = Field(default="sqlite:////data/app.db")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()
