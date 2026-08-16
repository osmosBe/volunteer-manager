"""Administrative editor for privacy and imprint footer links."""

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth.permissions import require_permission
from app.database.session import get_db
from app.services.admin import record_audit
from app.services.legal_settings import (
    LegalSettingsError,
    LegalSettingsRepository,
    legal_target_type,
)
from app.template_engine import templates

router = APIRouter()
strict_admin_dependency = Depends(require_permission("admin"))
db_dependency = Depends(get_db)
LEGAL_SETTINGS_PATH = "/admin/einstellungen/rechtliches"


def legal_settings_page(
    request: Request,
    repository: LegalSettingsRepository,
    *,
    form_values: dict[str, str] | None = None,
    field_errors: dict[str, str] | None = None,
    status_code: int = status.HTTP_200_OK,
):
    settings = repository.get()
    values = form_values or {
        "privacy_url": settings.privacy_url if settings else "",
        "imprint_url": settings.imprint_url if settings else "",
    }
    return templates.TemplateResponse(
        "admin_legal_settings.html",
        {
            "request": request,
            "form_values": values,
            "field_errors": field_errors or {},
        },
        status_code=status_code,
    )


@router.get(LEGAL_SETTINGS_PATH, tags=["admin"])
def get_legal_settings_page(
    request: Request,
    admin_user=strict_admin_dependency,
    db: Session = db_dependency,
):
    del admin_user
    return legal_settings_page(request, LegalSettingsRepository(db))


@router.post(LEGAL_SETTINGS_PATH, tags=["admin"])
def update_legal_settings(
    request: Request,
    privacy_url: str = Form(default=""),
    imprint_url: str = Form(default=""),
    admin_user=strict_admin_dependency,
    db: Session = db_dependency,
):
    repository = LegalSettingsRepository(db)
    form_values = {"privacy_url": privacy_url, "imprint_url": imprint_url}
    try:
        settings = repository.update(privacy_url, imprint_url)
    except LegalSettingsError as exc:
        db.rollback()
        return legal_settings_page(
            request,
            repository,
            form_values=form_values,
            field_errors=exc.field_errors,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    record_audit(
        db,
        action="legal.links.updated",
        entity_type="legal_settings",
        entity_id=settings.id,
        changes={
            "privacy_target_type": legal_target_type(settings.privacy_url),
            "imprint_target_type": legal_target_type(settings.imprint_url),
        },
        actor=admin_user.user_id,
    )
    db.commit()
    return RedirectResponse(LEGAL_SETTINGS_PATH, status_code=status.HTTP_303_SEE_OTHER)
