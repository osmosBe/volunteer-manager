"""Deprecated admin helpers kept for backward compatibility.

New route authorization uses ``app.auth.permissions.require_permission`` and
database-backed mappings. The legacy YAML/allowlist configuration is not an
authorization source.
"""

from fastapi import HTTPException, Request, status

from app.auth.models import AuthenticatedUser
from app.auth.permissions import has_permission
from app.auth.provider import get_current_user
from app.config.settings import Settings


def is_authorized_admin(
    user: AuthenticatedUser, settings: Settings | None = None
) -> bool:
    return has_permission(user, "admin", settings)


def require_admin(request: Request) -> AuthenticatedUser:
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    if not has_permission(user, "admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return user
