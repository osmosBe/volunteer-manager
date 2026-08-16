"""Safe administrative mail diagnostics and explicit test delivery."""

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.auth.permissions import require_any_permission
from app.config.settings import get_settings
from app.database.session import get_db
from app.mail.models import MailMessage, MailRecipient
from app.mail.service import (
    MailConfigurationError,
    MailService,
    get_mail_service,
    mail_diagnostics,
)
from app.mail.status import get_last_test_status, record_test_status
from app.services.admin import record_audit

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
email_templates = Environment(
    loader=FileSystemLoader("app/templates/email"),
    autoescape=select_autoescape(["html", "xml"]),
)
admin_dependency = Depends(require_any_permission(["admin", "manager"]))
db_dependency = Depends(get_db)


def require_mail_service() -> MailService:
    try:
        return get_mail_service()
    except MailConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Der Mail-Provider ist nicht vollständig konfiguriert.",
        ) from exc


mail_service_dependency = Depends(require_mail_service)


@router.get("/admin/mail", tags=["admin"])
def admin_mail_status(
    request: Request,
    admin_user=admin_dependency,
):
    return templates.TemplateResponse(
        "admin_mail.html",
        {
            "request": request,
            "admin_user": admin_user,
            "diagnostics": mail_diagnostics(),
            "last_test": get_last_test_status(),
            "test_result": None,
        },
    )


@router.post("/admin/mail/test", tags=["admin"])
def admin_mail_test(
    request: Request,
    recipient: Annotated[str, Form()],
    admin_user=admin_dependency,
    db: Session = db_dependency,
    mail_service: MailService = mail_service_dependency,
):
    settings = get_settings()
    try:
        mail_recipient = MailRecipient(email=recipient.strip())
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Die Test-E-Mail-Adresse ist ungültig.",
        ) from exc

    timestamp = datetime.now(timezone.utc)
    html_body = email_templates.get_template("test.html").render(
        app_name=settings.app_name,
        environment=settings.environment,
        timestamp=timestamp.isoformat(),
    )
    message = MailMessage(
        to=[mail_recipient],
        subject=f"{settings.app_name} – Test ({settings.environment})",
        text_body=(
            f"{settings.app_name}\n"
            f"Environment: {settings.environment}\n"
            f"Timestamp: {timestamp.isoformat()}"
        ),
        html_body=html_body,
    )
    result = mail_service.send(message)
    record_test_status(
        success=result.success,
        provider=result.provider,
        error_code=result.error_code,
    )
    audit = record_audit(
        db,
        actor=admin_user.user_id or admin_user.email or "unknown-admin",
        action="mail.test",
        entity_type="mail",
        entity_id=None,
        changes={
            "provider": result.provider,
            "recipient_count": 1,
            "success": result.success,
            "error_code": result.error_code,
        },
    )
    audit.actor_email = admin_user.email
    audit.actor_name = admin_user.name
    db.commit()
    return templates.TemplateResponse(
        "admin_mail.html",
        {
            "request": request,
            "admin_user": admin_user,
            "diagnostics": mail_diagnostics(),
            "last_test": get_last_test_status(),
            "test_result": result,
        },
        status_code=200 if result.success else 502,
    )


@router.get("/debug/mail", tags=["debug"])
def debug_mail():
    if not get_settings().debug:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return JSONResponse(mail_diagnostics())
