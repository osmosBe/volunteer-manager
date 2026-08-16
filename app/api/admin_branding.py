"""Administrative application-branding UI and public logo delivery."""

from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth.permissions import require_permission
from app.database.session import get_db
from app.services.admin import record_audit
from app.services.branding import (
    DEFAULT_LOGO_URL,
    MAX_LOGO_BYTES,
    BrandingError,
    BrandingRepository,
    branding_source,
    process_logo_upload,
)
from app.template_engine import templates

router = APIRouter()
strict_admin_dependency = Depends(require_permission("admin"))
db_dependency = Depends(get_db)


def branding_page(
    request: Request,
    repository: BrandingRepository,
    *,
    error: str | None = None,
    logo_url_value: str | None = None,
    status_code: int = status.HTTP_200_OK,
):
    settings = repository.get()
    return templates.TemplateResponse(
        "admin_branding.html",
        {
            "request": request,
            "branding": settings,
            "branding_source": branding_source(settings),
            "default_logo_url": DEFAULT_LOGO_URL,
            "error": error,
            "logo_url_value": (
                logo_url_value
                if logo_url_value is not None
                else (settings.logo_url if settings else "")
            ),
            "max_logo_megabytes": MAX_LOGO_BYTES // (1024 * 1024),
        },
        status_code=status_code,
    )


def _default_logo_response() -> RedirectResponse:
    return RedirectResponse(
        DEFAULT_LOGO_URL,
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


@router.get("/branding/logo", tags=["branding"])
def current_logo(db: Session = db_dependency):
    """Serve the configured logo while always retaining a bundled fallback."""

    try:
        settings = BrandingRepository(db).get()
    except SQLAlchemyError:
        return _default_logo_response()
    if settings and settings.logo_url:
        return RedirectResponse(
            settings.logo_url,
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )
    if settings and settings.logo_data and settings.logo_content_type:
        headers = {
            "Cache-Control": "no-store",
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
        }
        if settings.logo_content_type == "image/svg+xml":
            headers["Content-Security-Policy"] = (
                "default-src 'none'; style-src 'unsafe-inline'; sandbox"
            )
        return Response(
            content=settings.logo_data,
            media_type=settings.logo_content_type,
            headers=headers,
        )
    return _default_logo_response()


@router.get("/admin/branding", tags=["admin"])
def get_branding_page(
    request: Request,
    admin_user=strict_admin_dependency,
    db: Session = db_dependency,
):
    return branding_page(request, BrandingRepository(db))


@router.post("/admin/branding/url", tags=["admin"])
def set_branding_url(
    request: Request,
    logo_url: str = Form(),
    admin_user=strict_admin_dependency,
    db: Session = db_dependency,
):
    repository = BrandingRepository(db)
    try:
        settings = repository.set_logo_url(logo_url)
    except BrandingError as exc:
        return branding_page(
            request,
            repository,
            error=str(exc),
            logo_url_value=logo_url,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    record_audit(
        db,
        action="branding.logo.url_set",
        entity_type="branding",
        entity_id=settings.id,
        changes={"source": "url", "host": urlsplit(settings.logo_url).hostname},
        actor=admin_user.user_id,
    )
    db.commit()
    return RedirectResponse("/admin/branding", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/admin/branding/upload", tags=["admin"])
async def upload_branding_logo(
    request: Request,
    logo_file: Annotated[UploadFile, File()],
    admin_user=strict_admin_dependency,
    db: Session = db_dependency,
):
    repository = BrandingRepository(db)
    data = await logo_file.read(MAX_LOGO_BYTES + 1)
    try:
        logo = process_logo_upload(data, logo_file.content_type, logo_file.filename)
        settings = repository.set_uploaded_logo(logo)
    except BrandingError as exc:
        return branding_page(
            request,
            repository,
            error=str(exc),
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    record_audit(
        db,
        action="branding.logo.uploaded",
        entity_type="branding",
        entity_id=settings.id,
        changes={
            "source": "upload",
            "content_type": logo.content_type,
            "size_bytes": len(logo.data),
        },
        actor=admin_user.user_id,
    )
    db.commit()
    return RedirectResponse("/admin/branding", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/admin/branding/reset", tags=["admin"])
def reset_branding_logo(
    admin_user=strict_admin_dependency,
    db: Session = db_dependency,
):
    settings = BrandingRepository(db).reset()
    record_audit(
        db,
        action="branding.logo.reset",
        entity_type="branding",
        entity_id=settings.id,
        changes={"source": "default"},
        actor=admin_user.user_id,
    )
    db.commit()
    return RedirectResponse("/admin/branding", status_code=status.HTTP_303_SEE_OTHER)
