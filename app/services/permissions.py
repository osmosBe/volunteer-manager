"""Database-backed permission mappings and fail-closed authorization."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.models import Permission, PermissionMapping
from app.models.core import utcnow
from app.services.email_addresses import EmailAddressError, validate_email_address

MAPPING_TYPES = {"role", "group", "email"}
DEFAULT_PERMISSIONS = {
    "admin": ("Administration", "Full application administration", "Volunteer.Admin"),
    "manager": (
        "Volunteer management",
        "Operational volunteer management",
        "Volunteer.Manager",
    ),
    "checkin": (
        "Check-in",
        "Restricted event-day check-in access",
        "Volunteer.CheckIn",
    ),
    "police": (
        "Police workflow",
        "Restricted police-clearance workflow",
        "Volunteer.Police",
    ),
}


class PermissionServiceError(ValueError):
    pass


class PermissionRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_permission(self, name: str) -> Permission | None:
        return self.db.scalar(
            select(Permission)
            .options(selectinload(Permission.mappings))
            .where(Permission.name == name)
        )

    def list_permissions(self) -> list[Permission]:
        return list(
            self.db.scalars(
                select(Permission)
                .options(selectinload(Permission.mappings))
                .order_by(Permission.name)
            )
        )

    def list_mappings(self, permission_name: str) -> list[PermissionMapping]:
        permission = self.get_permission(permission_name)
        return list(permission.mappings) if permission else []

    def add_mapping(
        self,
        permission_name: str,
        mapping_type: str,
        value: str,
        label: str | None = None,
    ) -> PermissionMapping:
        permission = self.get_permission(permission_name)
        if not permission:
            raise PermissionServiceError("Permission does not exist.")
        mapping_type, value = self._validate_mapping(mapping_type, value)
        mapping = PermissionMapping(
            permission=permission,
            mapping_type=mapping_type,
            mapping_value=value,
            display_label=label.strip() or None if label else None,
        )
        self.db.add(mapping)
        try:
            self.db.flush()
        except IntegrityError as exc:
            self.db.rollback()
            raise PermissionServiceError("This mapping already exists.") from exc
        return mapping

    def remove_mapping(self, mapping_id: int) -> PermissionMapping:
        mapping = self.db.get(PermissionMapping, mapping_id)
        if not mapping:
            raise PermissionServiceError("Mapping does not exist.")
        if mapping.permission.name == "admin":
            # PostgreSQL serializes concurrent removals on the permission row.
            # SQLite ignores FOR UPDATE but is documented/supported as one app
            # instance only, and this count still protects ordinary requests.
            self.db.scalar(
                select(Permission.id)
                .where(Permission.id == mapping.permission_id)
                .with_for_update()
            )
            mapping_count = self.db.scalar(
                select(func.count(PermissionMapping.id)).where(
                    PermissionMapping.permission_id == mapping.permission_id
                )
            )
            if not mapping_count or mapping_count <= 1:
                raise PermissionServiceError("At least one admin mapping must remain.")
        self.db.delete(mapping)
        self.db.flush()
        return mapping

    def update_mapping_label(
        self, mapping_id: int, display_label: str | None
    ) -> PermissionMapping:
        mapping = self.db.get(PermissionMapping, mapping_id)
        if not mapping:
            raise PermissionServiceError("Mapping does not exist.")
        mapping.display_label = display_label.strip() or None if display_label else None
        mapping.updated_at = utcnow()
        self.db.flush()
        return mapping

    def delete_permission(self, permission_name: str) -> None:
        permission = self.get_permission(permission_name)
        if not permission:
            raise PermissionServiceError("Permission does not exist.")
        if permission.is_system or permission.name == "admin":
            raise PermissionServiceError("System permissions cannot be deleted.")
        self.db.delete(permission)
        self.db.flush()

    def update_permission_metadata(
        self, permission_name: str, display_name: str, description: str | None
    ) -> Permission:
        permission = self.get_permission(permission_name)
        if not permission:
            raise PermissionServiceError("Permission does not exist.")
        if not display_name.strip():
            raise PermissionServiceError("Display name is required.")
        permission.display_name = display_name.strip()
        permission.description = description.strip() or None if description else None
        permission.updated_at = utcnow()
        self.db.flush()
        return permission

    @staticmethod
    def _validate_mapping(mapping_type: str, value: str) -> tuple[str, str]:
        kind = mapping_type.strip().lower()
        text = value.strip()
        if kind not in MAPPING_TYPES or not text:
            raise PermissionServiceError("Mapping type and value are required.")
        if kind == "email":
            try:
                text = validate_email_address(text)
            except EmailAddressError as exc:
                raise PermissionServiceError(str(exc)) from exc
        if kind == "group":
            try:
                text = str(UUID(text))
            except ValueError as exc:
                raise PermissionServiceError("Group ID must be a UUID.") from exc
        return kind, text


def bootstrap_permissions(db: Session) -> None:
    """Create missing defaults only; never overwrite administrator changes."""
    for name, (display_name, description, role) in DEFAULT_PERMISSIONS.items():
        if db.scalar(select(Permission.id).where(Permission.name == name)) is None:
            permission = Permission(
                name=name,
                display_name=display_name,
                description=description,
                is_system=True,
            )
            permission.mappings.append(
                PermissionMapping(mapping_type="role", mapping_value=role)
            )
            db.add(permission)
    db.flush()


def user_has_permission(db: Session, user, permission_name: str) -> bool:
    """Fail closed if permission data is missing or database access fails."""
    try:
        permission = PermissionRepository(db).get_permission(permission_name)
        if not permission or not permission.mappings or not user:
            return False
        roles = {value.strip() for value in user.roles if value.strip()}
        groups = {value.strip() for value in user.groups if value.strip()}
        email = str(user.email or "").strip().lower()
        return any(
            (mapping.mapping_type == "role" and mapping.mapping_value in roles)
            or (mapping.mapping_type == "group" and mapping.mapping_value in groups)
            or (mapping.mapping_type == "email" and mapping.mapping_value == email)
            for mapping in permission.mappings
        )
    except Exception:
        return False
