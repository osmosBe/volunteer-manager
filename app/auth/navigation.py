"""Provider-independent, privacy-safe navigation state for HTML templates."""

from typing import Any

from fastapi import Request

from app.auth.provider import get_current_user

APPLICATION_PERMISSIONS = ("admin", "manager", "checkin", "police")


def remember_navigation_permissions(
    request: Request, user: Any, permissions: set[str]
) -> None:
    """Store only the minimum request-local state needed by the layout."""

    request.state.authenticated_user_present = user is not None
    request.state.navigation_permissions = frozenset(permissions)


def navigation_context(request: Request) -> dict[str, Any]:
    """Build template navigation without exposing identity claims or tokens."""

    authenticated = getattr(request.state, "authenticated_user_present", None)
    if authenticated is None:
        authenticated = get_current_user(request) is not None

    permissions = set(getattr(request.state, "navigation_permissions", frozenset()))
    can_use_dashboard = bool(permissions & {"admin", "manager"})
    can_use_checkin = bool(permissions & {"admin", "manager", "checkin"})
    can_manage_system = "admin" in permissions

    return {
        "navigation": {
            "authenticated": authenticated,
            # Authenticated identities use a server-side resolver because the
            # database, rather than provider claims, is the authorization source.
            "logo_href": "/auth/home" if authenticated else "/",
            "can_use_dashboard": can_use_dashboard,
            "can_use_checkin": can_use_checkin,
            "can_manage_system": can_manage_system,
            "has_application_menu": can_use_dashboard or can_use_checkin,
        }
    }
