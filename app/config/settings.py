from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import BeforeValidator, Field, SecretStr, model_validator
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


def _parse_auth_mode(value: Any) -> Any:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "disable":
            return "disabled"
        return normalized
    return value


CsvList = Annotated[list[str], NoDecode, BeforeValidator(_parse_csv)]
AuthMode = Annotated[
    Literal["disabled", "easyauth", "oidc"], BeforeValidator(_parse_auth_mode)
]


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    app_name: str = "ST. PRIDE Volunteer Management"
    app_version: str = "0.1.0"
    environment: str = "development"
    database_url: str = Field(default="sqlite:///./volunteer.db")
    seed_demo_data: bool = False

    app_base_url: str = "http://localhost:8000"
    auth_mode: AuthMode = "disabled"
    allow_insecure_auth: bool = False
    debug: bool = False
    session_secret: SecretStr | None = None
    oidc_issuer_url: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: SecretStr | None = None
    oidc_scopes: str = "openid profile email"
    oidc_role_claims: CsvList = Field(default_factory=lambda: ["roles"])
    oidc_group_claims: CsvList = Field(default_factory=lambda: ["groups"])
    oidc_user_id_claim: str = "sub"
    oidc_name_claim: str = "name"
    oidc_email_claims: CsvList = Field(
        default_factory=lambda: ["email", "preferred_username"]
    )
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

    @model_validator(mode="after")
    def validate_auth_configuration(self) -> "Settings":
        if self.auth_mode == "oidc":
            missing = [
                name
                for name, value in {
                    "OIDC_ISSUER_URL": self.oidc_issuer_url,
                    "OIDC_CLIENT_ID": self.oidc_client_id,
                    "OIDC_CLIENT_SECRET": self.oidc_client_secret,
                    "SESSION_SECRET": self.session_secret,
                }.items()
                if not value
            ]
            if missing:
                raise ValueError(
                    "OIDC configuration is incomplete: " + ", ".join(missing)
                )
        if (
            self.auth_mode == "disabled"
            and self.environment.lower() not in {"development", "test", "testing"}
            and not self.allow_insecure_auth
        ):
            raise ValueError(
                "AUTH_MODE=disabled requires ALLOW_INSECURE_AUTH=true "
                "outside development/test"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
