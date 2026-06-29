from fastapi import Request

from app.auth.easyauth import parse_easyauth_principal
from app.auth.local import get_local_user
from app.auth.models import AuthenticatedUser
from app.config.settings import get_settings


def get_current_user(request: Request) -> AuthenticatedUser | None:
    """Return the authenticated user for the configured auth provider."""

    settings = get_settings()
    if settings.auth_mode == "disabled":
        return get_local_user()

    # EasyAuth headers must only be trusted behind Azure Container Apps
    # Authentication / Authorization, and only when AUTH_MODE=easyauth.
    if settings.auth_mode == "easyauth":
        return parse_easyauth_principal(request.headers)

    return None
