from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request, status
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.auth.models import AuthenticatedUser
from app.auth.provider import get_current_user
from app.config.settings import Settings, get_settings


class Permission(BaseModel):
    roles: list[str] = Field(default_factory=list)
    groups: list[str] = Field(default_factory=list)
    emails: list[str] = Field(default_factory=list)

    @field_validator("roles", "groups", mode="before")
    @classmethod
    def _normalize_values(cls, value: Any) -> list[str]:
        return _normalize_list(value)

    @field_validator("emails", mode="before")
    @classmethod
    def _normalize_emails(cls, value: Any) -> list[str]:
        return [item.lower() for item in _normalize_list(value)]


class PermissionsConfig(BaseModel):
    permissions: dict[str, Permission] = Field(default_factory=dict)


def _normalize_list(value: Any) -> list[str]:
    if value is None:
        return []
    raw = value if isinstance(value, list | tuple | set) else [value]
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = str(item).strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _load_permissions_yaml(path: Path) -> dict[str, Any]:
    """Load the small permissions YAML shape without exposing a YAML dependency."""
    result: dict[str, Any] = {"permissions": {}}
    current_permission: str | None = None
    current_list: str | None = None
    saw_root = False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        indent = len(line) - len(line.lstrip(" "))
        if indent == 0 and stripped == "permissions:":
            saw_root = True
            continue
        if not saw_root:
            raise ValueError("permissions root missing")
        if indent == 2 and stripped.endswith(":"):
            current_permission = stripped[:-1].strip()
            if not current_permission:
                raise ValueError("empty permission name")
            result["permissions"][current_permission] = {}
            current_list = None
            continue
        if indent == 4 and current_permission and ":" in stripped:
            key, value = [part.strip() for part in stripped.split(":", 1)]
            if key not in {"roles", "groups", "emails"}:
                raise ValueError("unknown permission key")
            current_list = key
            result["permissions"][current_permission][key] = []
            if value and value != "[]":
                raise ValueError("inline values are not supported")
            continue
        if (
            indent == 6
            and current_permission
            and current_list
            and stripped.startswith("- ")
        ):
            result["permissions"][current_permission][current_list].append(
                stripped[2:].strip().strip('"\'')
            )
            continue
        raise ValueError("invalid permissions YAML")
    if not saw_root:
        raise ValueError("permissions root missing")
    return result


def load_permissions_config(path: str | Path) -> PermissionsConfig | None:
    try:
        data = _load_permissions_yaml(Path(path))
        return PermissionsConfig.model_validate(data)
    except (OSError, ValidationError, TypeError, ValueError):
        return None


def get_permissions_config(settings: Settings | None = None) -> PermissionsConfig | None:
    settings = settings or get_settings()
    return load_permissions_config(settings.permissions_config_path)


def permission_names(settings: Settings | None = None) -> list[str]:
    config = get_permissions_config(settings)
    if not config:
        return []
    return sorted(config.permissions)


def _permission_for(name: str, settings: Settings) -> Permission | None:
    config = get_permissions_config(settings)
    permission = config.permissions.get(name) if config else None
    if name == "admin":
        permission = _with_admin_environment_fallback(permission, settings)
    return permission


def _with_admin_environment_fallback(
    permission: Permission | None, settings: Settings
) -> Permission | None:
    emails = _normalize_list(settings.admin_allowed_emails)
    groups = _normalize_list(settings.admin_allowed_group_ids)
    if not emails and not groups:
        return permission
    base = permission or Permission()
    return Permission(
        roles=base.roles,
        groups=[*base.groups, *groups],
        emails=[*base.emails, *[email.lower() for email in emails]],
    )


def has_permission(
    user: AuthenticatedUser | None,
    permission_name: str,
    settings: Settings | None = None,
) -> bool:
    settings = settings or get_settings()
    if settings.auth_mode == "disabled":
        return True
    if not user:
        return False

    permission = _permission_for(permission_name, settings)
    if not permission:
        return False
    if not any([permission.roles, permission.groups, permission.emails]):
        return False

    user_roles = {role.strip() for role in user.roles if role.strip()}
    user_groups = {group.strip() for group in user.groups if group.strip()}
    user_email = str(user.email or "").strip().lower()
    return bool(
        user_roles.intersection(permission.roles)
        or user_groups.intersection(permission.groups)
        or (user_email and user_email in permission.emails)
    )


def require_permission(permission_name: str):
    def dependency(request: Request) -> AuthenticatedUser:
        user = get_current_user(request)
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
        if not has_permission(user, permission_name):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
        return user

    return dependency


def require_any_permission(permission_names: list[str]):
    def dependency(request: Request) -> AuthenticatedUser:
        user = get_current_user(request)
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
        if not any(has_permission(user, name) for name in permission_names):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
        return user

    return dependency
