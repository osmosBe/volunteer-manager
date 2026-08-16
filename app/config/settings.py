from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import BeforeValidator, Field, SecretStr
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def parse_csv_env(value: Any, lowercase: bool = False) -> list[str]:
    """Parse an optional comma-separated environment value into a clean list."""
    if value is None:
        return []

    if isinstance(value, str):
        raw_items = value.split(",")
    elif isinstance(value, list | tuple | set):
        raw_items = value
    else:
        raw_items = [value]

    items = [str(item).strip() for item in raw_items]
    if lowercase:
        items = [item.lower() for item in items]
    return [item for item in items if item]


def _parse_csv(value: Any) -> list[str]:
    return parse_csv_env(value)


def _parse_email_csv(value: Any) -> list[str]:
    return parse_csv_env(value, lowercase=True)


def _parse_auth_mode(value: Any) -> Any:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "disable":
            return "disabled"
        return normalized
    return value


CsvList = Annotated[list[str], NoDecode, BeforeValidator(_parse_csv)]
EmailCsvList = Annotated[list[str], NoDecode, BeforeValidator(_parse_email_csv)]
AuthMode = Annotated[Literal["disabled", "easyauth"], BeforeValidator(_parse_auth_mode)]


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    app_name: str = "ST. PRIDE Volunteer Management"
    app_version: str = "0.1.0"
    environment: str = "development"
    database_url: str = Field(default="sqlite:///./volunteer.db")
    seed_demo_data: bool = False

    app_base_url: str = "http://localhost:8000"
    auth_mode: AuthMode = "disabled"
    debug: bool = False
    permissions_config_path: str = "config/permissions.yaml"
    admin_allowed_emails: EmailCsvList = Field(default_factory=list)
    admin_allowed_group_ids: CsvList = Field(default_factory=list)
    smtp_password: SecretStr | None = None

    # Transactional mail foundation. ``console`` is deliberately safe for new
    # and local installations; selecting Graph requires complete credentials.
    mail_provider: Literal["console", "graph"] = "console"
    mail_from_address: str | None = None
    mail_from_name: str = "ST. PRIDE Volunteer Manager"
    mail_reply_to: str | None = None
    m365_tenant_id: str | None = None
    m365_client_id: str | None = None
    m365_client_secret: SecretStr | None = None
    m365_auth_mode: Literal["client_secret", "managed_identity"] = "client_secret"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()
