"""Deprecated admin helpers kept for backward compatibility.

Use app.auth.permissions.require_permission("admin") and permissions.yaml for new
route authorization. ADMIN_ALLOWED_EMAILS and ADMIN_ALLOWED_GROUP_IDS are only a
DEV/emergency fallback for the admin permission.
"""

from fastapi import Request

from app.auth.models import AuthenticatedUser
from app.auth.permissions import has_permission, require_permission
from app.config.settings import Settings


def is_authorized_admin(
    user: AuthenticatedUser, settings: Settings | None = None
) -> bool:
    return has_permission(user, "admin", settings)


def require_admin(request: Request) -> AuthenticatedUser:
    return require_permission("admin")(request)
