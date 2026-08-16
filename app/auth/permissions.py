"""Provider-independent, database-backed authorization dependencies."""

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth.models import AuthenticatedUser
from app.auth.provider import get_current_user
from app.config.settings import Settings, get_settings
from app.database.session import get_db, get_session_factory
from app.services.permissions import PermissionRepository, user_has_permission

db_dependency = Depends(get_db)


def permission_names(db: Session | None = None) -> list[str]:
    """Return known permissions, or an empty list when storage is unavailable."""
    try:
        if db is not None:
            return [item.name for item in PermissionRepository(db).list_permissions()]
        with get_session_factory()() as owned_db:
            return [
                item.name for item in PermissionRepository(owned_db).list_permissions()
            ]
    except Exception:
        return []


def has_permission(
    user: AuthenticatedUser | None,
    permission_name: str,
    settings: Settings | None = None,
    db: Session | None = None,
) -> bool:
    """Check a permission and fail closed on missing or broken DB state."""
    settings = settings or get_settings()
    if settings.auth_mode == "disabled":
        return True
    if not user:
        return False
    try:
        if db is not None:
            return user_has_permission(db, user, permission_name)
        with get_session_factory()() as owned_db:
            return user_has_permission(owned_db, user, permission_name)
    except Exception:
        return False


def require_permission(permission_name: str):
    def dependency(request: Request, db: Session = db_dependency) -> AuthenticatedUser:
        user = get_current_user(request)
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
        if not has_permission(user, permission_name, db=db):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
        return user

    return dependency


def require_any_permission(permission_names: list[str]):
    required = tuple(dict.fromkeys(permission_names))

    def dependency(request: Request, db: Session = db_dependency) -> AuthenticatedUser:
        user = get_current_user(request)
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
        if not any(has_permission(user, name, db=db) for name in required):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
        return user

    return dependency
