from fastapi import HTTPException, Request, status

from app.auth.models import AuthenticatedUser
from app.auth.provider import get_current_user
from app.config.settings import Settings, get_settings


def _normalized(values: list[str]) -> set[str]:
    return {value.strip().lower() for value in values if value.strip()}


def is_authorized_admin(
    user: AuthenticatedUser, settings: Settings | None = None
) -> bool:
    settings = settings or get_settings()
    if settings.auth_mode == "disabled":
        return True

    allowed_emails = _normalized(settings.admin_allowed_emails)
    allowed_groups = {
        value.strip() for value in settings.admin_allowed_group_ids if value.strip()
    }

    if not allowed_emails and not allowed_groups:
        return False

    email = str(user.email or "").strip().lower()
    if email and email in allowed_emails:
        return True

    return bool(allowed_groups.intersection({group.strip() for group in user.groups}))


def require_admin(request: Request) -> AuthenticatedUser:
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    if not is_authorized_admin(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return user
