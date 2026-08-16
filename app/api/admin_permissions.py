"""Administrative permission mapping UI."""

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth.permissions import require_permission
from app.database.session import get_db
from app.services.admin import record_audit
from app.services.permissions import PermissionRepository, PermissionServiceError

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
admin_dependency = Depends(require_permission("admin"))
db_dependency = Depends(get_db)


def permission_page(
    request: Request,
    repository: PermissionRepository,
    *,
    error: str | None = None,
    status_code: int = status.HTTP_200_OK,
):
    return templates.TemplateResponse(
        "admin_permissions.html",
        {
            "request": request,
            "permissions": repository.list_permissions(),
            "error": error,
        },
        status_code=status_code,
    )


@router.get("/admin/permissions", tags=["admin"])
def list_permissions(
    request: Request, admin_user=admin_dependency, db: Session = db_dependency
):
    return permission_page(request, PermissionRepository(db))


@router.post("/admin/permissions/{permission_name}/mappings", tags=["admin"])
def add_mapping(
    permission_name: str,
    request: Request,
    mapping_type: str = Form(),
    mapping_value: str = Form(),
    display_label: str = Form(""),
    admin_user=admin_dependency,
    db: Session = db_dependency,
):
    repository = PermissionRepository(db)
    try:
        mapping = repository.add_mapping(
            permission_name, mapping_type, mapping_value, display_label
        )
    except PermissionServiceError as exc:
        return permission_page(
            request,
            repository,
            error=str(exc),
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    record_audit(
        db,
        action="permission.mapping.add",
        entity_type="permission",
        entity_id=permission_name,
        changes={
            "mapping_type": mapping.mapping_type,
            "mapping_value": mapping.mapping_value,
        },
        actor=admin_user.user_id,
    )
    db.commit()
    return RedirectResponse("/admin/permissions", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/admin/permissions/mappings/{mapping_id}", tags=["admin"])
def update_mapping_label(
    mapping_id: int,
    request: Request,
    display_label: str = Form(""),
    admin_user=admin_dependency,
    db: Session = db_dependency,
):
    repository = PermissionRepository(db)
    try:
        mapping = repository.update_mapping_label(mapping_id, display_label)
    except PermissionServiceError as exc:
        return permission_page(
            request,
            repository,
            error=str(exc),
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    record_audit(
        db,
        action="permission.mapping.update",
        entity_type="permission",
        entity_id=mapping.permission.name,
        changes={
            "mapping_type": mapping.mapping_type,
            "mapping_value": mapping.mapping_value,
            "display_label": mapping.display_label,
        },
        actor=admin_user.user_id,
    )
    db.commit()
    return RedirectResponse("/admin/permissions", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/admin/permissions/mappings/{mapping_id}/remove", tags=["admin"])
def remove_mapping(
    mapping_id: int,
    request: Request,
    admin_user=admin_dependency,
    db: Session = db_dependency,
):
    repository = PermissionRepository(db)
    try:
        mapping = repository.remove_mapping(mapping_id)
        name, kind, value = (
            mapping.permission.name,
            mapping.mapping_type,
            mapping.mapping_value,
        )
    except PermissionServiceError as exc:
        return permission_page(
            request,
            repository,
            error=str(exc),
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    record_audit(
        db,
        action="permission.mapping.remove",
        entity_type="permission",
        entity_id=name,
        changes={"mapping_type": kind, "mapping_value": value},
        actor=admin_user.user_id,
    )
    db.commit()
    return RedirectResponse("/admin/permissions", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/admin/permissions/{permission_name}", tags=["admin"])
def update_metadata(
    permission_name: str,
    request: Request,
    display_name: str = Form(),
    description: str = Form(""),
    admin_user=admin_dependency,
    db: Session = db_dependency,
):
    repository = PermissionRepository(db)
    try:
        repository.update_permission_metadata(
            permission_name, display_name, description
        )
    except PermissionServiceError as exc:
        return permission_page(
            request,
            repository,
            error=str(exc),
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    record_audit(
        db,
        action="permission.metadata.update",
        entity_type="permission",
        entity_id=permission_name,
        changes={"display_name": display_name.strip()},
        actor=admin_user.user_id,
    )
    db.commit()
    return RedirectResponse("/admin/permissions", status_code=status.HTTP_303_SEE_OTHER)
