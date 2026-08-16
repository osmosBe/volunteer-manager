import csv
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from io import StringIO
from typing import Annotated

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import (
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from app.api.admin_branding import router as admin_branding_router
from app.api.admin_mail import router as admin_mail_router
from app.api.admin_permissions import router as admin_permissions_router
from app.api.public import router as public_router
from app.auth.oidc import begin_login, clear_login, complete_login, oidc_diagnostics
from app.auth.permissions import (
    has_permission,
    permission_names,
    require_any_permission,
    require_permission,
)
from app.auth.provider import get_current_user
from app.config.settings import get_settings
from app.database.diagnostics import safe_database_diagnostics
from app.database.session import database_status, get_db
from app.forms.query_filters import (
    QueryFilterError,
    parse_checkbox,
    parse_optional_id,
)
from app.models import (
    AgeGroup,
    AssignmentStatus,
    Briefing,
    BriefingConfirmation,
    Event,
    EventStatus,
    MailTemplate,
    OutboxMessage,
    Role,
    Shift,
    ShiftAssignment,
    ShiftStatus,
    SMTPConfiguration,
    Team,
    TeamMaterial,
    Volunteer,
    VolunteerStatus,
)
from app.services.admin import (
    AdminWorkflowError,
    anonymize_volunteer,
    assign_volunteer,
    change_assignment_status,
    promote_first_waitlisted,
    record_audit,
    reject_volunteer,
)
from app.services.checkins import (
    CheckInError,
    check_in_assignment,
    check_out_assignment,
)
from app.services.email_addresses import EmailAddressError, validate_email_address
from app.services.mail_delivery import (
    MailDeliveryError,
    get_smtp_configuration,
    send_outbox_message,
    send_pending_automatic_messages,
    smtp_password_configured,
)
from app.services.mail_templates import (
    DELIVERY_MODES,
    ensure_mail_templates,
    unknown_placeholders,
)
from app.services.permissions import user_has_permission
from app.services.qr_codes import qr_svg
from app.services.volunteers import deterministic_email_hash

admin_dependency = Depends(require_any_permission(["admin", "manager"]))
checkin_dependency = Depends(require_any_permission(["admin", "manager", "checkin"]))
strict_admin_dependency = Depends(require_permission("admin"))
db_dependency = Depends(get_db)

settings = get_settings()
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI):
    del application
    if get_settings().auth_mode == "disabled":
        logger.warning(
            "AUTH_MODE=disabled is enabled; never expose this mode "
            "to an untrusted network."
        )
    yield


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
)
app.add_middleware(
    SessionMiddleware,
    secret_key=(
        settings.session_secret.get_secret_value()
        if settings.session_secret
        else "local-development-session-secret"
    ),
    session_cookie="volunteer_manager_session",
    same_site="lax",
    https_only=settings.app_base_url.lower().startswith("https://"),
    max_age=8 * 60 * 60,
)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(public_router)
app.include_router(admin_branding_router)
app.include_router(admin_mail_router)
app.include_router(admin_permissions_router)


@app.exception_handler(status.HTTP_401_UNAUTHORIZED)
async def unauthenticated_handler(request: Request, exc: HTTPException):
    return templates.TemplateResponse(
        "unauthenticated.html",
        {"request": request},
        status_code=status.HTTP_401_UNAUTHORIZED,
    )


@app.exception_handler(status.HTTP_403_FORBIDDEN)
async def forbidden_handler(request: Request, exc: HTTPException):
    return templates.TemplateResponse(
        "forbidden.html", {"request": request}, status_code=status.HTTP_403_FORBIDDEN
    )


@app.exception_handler(status.HTTP_404_NOT_FOUND)
async def not_found_handler(request: Request, exc: HTTPException):
    return templates.TemplateResponse(
        "error.html",
        {
            "request": request,
            "title": "Nicht gefunden",
            "message": "Die angeforderte Seite oder der Link ist nicht verfügbar.",
        },
        status_code=status.HTTP_404_NOT_FOUND,
    )


@app.exception_handler(status.HTTP_422_UNPROCESSABLE_ENTITY)
async def unprocessable_handler(request: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, str) else "Eingabe nicht gültig."
    return templates.TemplateResponse(
        "error.html",
        {"request": request, "title": "Eingabe prüfen", "message": detail},
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    return templates.TemplateResponse(
        "error.html",
        {
            "request": request,
            "title": "Eingabe prüfen",
            "message": "Mindestens ein Feld fehlt oder enthält einen ungültigen Wert.",
        },
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


@app.get("/healthz", tags=["health"])
def healthz() -> dict[str, str]:
    return {"status": "ok", "version": settings.app_version}


@app.get("/health/live", tags=["health"])
def health_live() -> dict[str, str]:
    """Liveness deliberately avoids a database dependency."""
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
def health_ready(db: Session = db_dependency):
    state = database_status(db)
    if not state["connected"]:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    return {"status": "ok", "database": "connected"}


@app.get("/admin", tags=["admin"])
def admin_dashboard(
    request: Request, admin_user=admin_dependency, db: Session = db_dependency
):
    try:
        events = list(
            db.scalars(select(Event).order_by(Event.starts_at.desc(), Event.name))
        )
        event_stats = {}
        for event in events:
            assignments = [item for shift in event.shifts for item in shift.assignments]
            event_stats[event.id] = {
                "needed": sum(shift.needed_count for shift in event.shifts),
                "confirmed": sum(
                    item.assignment_status
                    in {
                        AssignmentStatus.confirmed,
                        AssignmentStatus.checked_in,
                        AssignmentStatus.attended,
                    }
                    for item in assignments
                ),
                "waitlisted": sum(
                    item.assignment_status == AssignmentStatus.waitlisted
                    for item in assignments
                ),
                "volunteers": len({item.volunteer_id for item in assignments}),
                "checked_in": sum(
                    item.assignment_status == AssignmentStatus.checked_in
                    for item in assignments
                ),
                "materials_open": sum(
                    bool(item.checkin)
                    and item.checkin.checked_in_at is not None
                    and item.checkin.materials_returned_at is None
                    for item in assignments
                ),
            }
        dashboard_error = None
    except SQLAlchemyError:
        events = []
        event_stats = {}
        dashboard_error = "Die Datenbank ist derzeit nicht erreichbar."
    # Use the public authorization wrapper so AUTH_MODE=disabled keeps its
    # documented local-developer access instead of hiding strict-admin links.
    can_manage_permissions = has_permission(admin_user, "admin", db=db)
    return templates.TemplateResponse(
        "admin_dashboard.html",
        {
            "request": request,
            "admin_user": admin_user,
            "events": events,
            "event_stats": event_stats,
            "dashboard_error": dashboard_error,
            "auth_provider": {
                "disabled": "Local Development",
                "easyauth": "EasyAuth",
                "oidc": "Generic OIDC",
            }[get_settings().auth_mode],
            "database_provider": (
                safe_database_diagnostics()["database_type"]
                if can_manage_permissions
                else None
            ),
            "can_manage_permissions": can_manage_permissions,
        },
    )


_EVENT_FORM_FIELDS = (
    "name",
    "slug",
    "starts_at",
    "ends_at",
    "venue",
    "address",
    "short_description",
    "description",
    "public_meeting_point",
    "registration_opens_at",
    "registration_closes_at",
    "contact_name",
    "contact_email",
    "contact_phone",
    "briefing",
    "clothing_and_material",
    "catering_info",
    "accessibility_info",
    "allows_minors",
    "status",
    "is_public",
)


def _event_form_values(**values) -> dict[str, object]:
    result: dict[str, object] = {field: "" for field in _EVENT_FORM_FIELDS}
    result.update(
        {"allows_minors": False, "status": EventStatus.draft.value, "is_public": False}
    )
    for field in _EVENT_FORM_FIELDS:
        if field not in values:
            continue
        value = values[field]
        if isinstance(value, datetime):
            value = value.strftime("%Y-%m-%dT%H:%M")
        elif isinstance(value, EventStatus):
            value = value.value
        result[field] = value if value is not None else ""
    return result


def _event_model_form_values(event: Event | None) -> dict[str, object]:
    if event is None:
        return _event_form_values()
    return _event_form_values(
        **{
            field: (event.status if field == "status" else getattr(event, field))
            for field in _EVENT_FORM_FIELDS
        }
    )


def _render_event_form(
    request: Request,
    event: Event | None,
    *,
    error: str | None = None,
    field_errors: dict[str, str] | None = None,
    form_values: dict[str, object] | None = None,
    status_code: int = 200,
):
    return templates.TemplateResponse(
        "admin_event_form.html",
        {
            "request": request,
            "event": event,
            "error": error,
            "field_errors": field_errors or {},
            "form_values": form_values or _event_model_form_values(event),
        },
        status_code=status_code,
    )


@app.get("/admin/veranstaltungen/neu", tags=["admin"])
def new_event_form(request: Request, admin_user=admin_dependency):
    return _render_event_form(request, None)


@app.post("/admin/veranstaltungen/neu", tags=["admin"])
def create_event(
    request: Request,
    admin_user=admin_dependency,
    db: Session = db_dependency,
    name: Annotated[str, Form()] = "",
    slug: Annotated[str, Form()] = "",
    starts_at: Annotated[datetime | None, Form()] = None,
    ends_at: Annotated[datetime | None, Form()] = None,
    venue: Annotated[str, Form()] = "",
    address: Annotated[str, Form()] = "",
    short_description: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
    public_meeting_point: Annotated[str, Form()] = "",
    registration_opens_at: Annotated[datetime | None, Form()] = None,
    registration_closes_at: Annotated[datetime | None, Form()] = None,
    contact_name: Annotated[str, Form()] = "",
    contact_email: Annotated[str, Form()] = "",
    contact_phone: Annotated[str, Form()] = "",
    briefing: Annotated[str, Form()] = "",
    clothing_and_material: Annotated[str, Form()] = "",
    catering_info: Annotated[str, Form()] = "",
    accessibility_info: Annotated[str, Form()] = "",
    allows_minors: Annotated[bool, Form()] = False,
    status_value: Annotated[str, Form(alias="status")] = EventStatus.draft.value,
    is_public: Annotated[bool, Form()] = False,
):
    form_values = _event_form_values(
        name=name,
        slug=slug,
        starts_at=starts_at,
        ends_at=ends_at,
        venue=venue,
        address=address,
        short_description=short_description,
        description=description,
        public_meeting_point=public_meeting_point,
        registration_opens_at=registration_opens_at,
        registration_closes_at=registration_closes_at,
        contact_name=contact_name,
        contact_email=contact_email,
        contact_phone=contact_phone,
        briefing=briefing,
        clothing_and_material=clothing_and_material,
        catering_info=catering_info,
        accessibility_info=accessibility_info,
        allows_minors=allows_minors,
        status=status_value,
        is_public=is_public,
    )
    field_errors = {}
    if not name.strip():
        field_errors["name"] = "Bitte gib einen Titel ein."
    if not slug.strip():
        field_errors["slug"] = "Bitte gib ein URL-Kürzel ein."
    if field_errors:
        return _render_event_form(
            request,
            None,
            field_errors=field_errors,
            form_values=form_values,
            status_code=422,
        )
    if ends_at and starts_at and ends_at <= starts_at:
        return _render_event_form(
            request,
            None,
            error="Das Ende muss nach dem Beginn liegen.",
            form_values=form_values,
            status_code=422,
        )
    if (
        registration_opens_at
        and registration_closes_at
        and registration_closes_at <= registration_opens_at
    ):
        return _render_event_form(
            request,
            None,
            error="Das Ende der Anmeldung muss nach deren Beginn liegen.",
            form_values=form_values,
            status_code=422,
        )
    if db.scalar(select(Event).where(Event.slug == slug.strip())):
        return _render_event_form(
            request,
            None,
            error="Dieses URL-Kürzel wird bereits verwendet.",
            form_values=form_values,
            status_code=422,
        )
    try:
        event_status = EventStatus(status_value)
    except ValueError:
        return _render_event_form(
            request,
            None,
            error="Ungültiger Veranstaltungsstatus.",
            form_values=form_values,
            status_code=422,
        )
    normalized_contact_email = None
    if contact_email.strip():
        try:
            normalized_contact_email = validate_email_address(contact_email)
        except EmailAddressError as exc:
            return _render_event_form(
                request,
                None,
                field_errors={"contact_email": str(exc)},
                form_values=form_values,
                status_code=422,
            )
    event = Event(
        name=name.strip(),
        slug=slug.strip(),
        starts_at=starts_at,
        ends_at=ends_at,
        venue=venue.strip() or None,
        address=address.strip() or None,
        short_description=short_description.strip() or None,
        description=description.strip() or None,
        public_meeting_point=public_meeting_point.strip() or None,
        registration_opens_at=registration_opens_at,
        registration_closes_at=registration_closes_at,
        contact_name=contact_name.strip() or None,
        contact_email=normalized_contact_email,
        contact_phone=contact_phone.strip() or None,
        briefing=briefing.strip() or None,
        clothing_and_material=clothing_and_material.strip() or None,
        catering_info=catering_info.strip() or None,
        accessibility_info=accessibility_info.strip() or None,
        allows_minors=allows_minors,
        status=event_status,
        is_public=is_public,
    )
    db.add(event)
    db.flush()
    record_audit(
        db,
        action="event.created",
        entity_type="event",
        entity_id=event.id,
        changes={"name": event.name, "status": event.status.value},
    )
    db.commit()
    return RedirectResponse(url=f"/admin/veranstaltungen/{event.id}", status_code=303)


@app.get("/admin/veranstaltungen/{event_id}", tags=["admin"])
def event_admin_detail(
    event_id: int,
    request: Request,
    db: Session = db_dependency,
    admin_user=admin_dependency,
):
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        "admin_event_detail.html",
        {
            "request": request,
            "event": event,
            "assignment_statuses": list(AssignmentStatus),
        },
    )


@app.get("/admin/veranstaltungen/{event_id}/bearbeiten", tags=["admin"])
def edit_event_form(
    event_id: int,
    request: Request,
    db: Session = db_dependency,
    admin_user=admin_dependency,
):
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404)
    return _render_event_form(request, event)


@app.post("/admin/veranstaltungen/{event_id}/bearbeiten", tags=["admin"])
def edit_event_submit(
    event_id: int,
    request: Request,
    db: Session = db_dependency,
    admin_user=admin_dependency,
    name: Annotated[str, Form()] = "",
    slug: Annotated[str, Form()] = "",
    starts_at: Annotated[datetime | None, Form()] = None,
    ends_at: Annotated[datetime | None, Form()] = None,
    venue: Annotated[str, Form()] = "",
    address: Annotated[str, Form()] = "",
    short_description: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
    public_meeting_point: Annotated[str, Form()] = "",
    registration_opens_at: Annotated[datetime | None, Form()] = None,
    registration_closes_at: Annotated[datetime | None, Form()] = None,
    contact_name: Annotated[str, Form()] = "",
    contact_email: Annotated[str, Form()] = "",
    contact_phone: Annotated[str, Form()] = "",
    briefing: Annotated[str, Form()] = "",
    clothing_and_material: Annotated[str, Form()] = "",
    catering_info: Annotated[str, Form()] = "",
    accessibility_info: Annotated[str, Form()] = "",
    allows_minors: Annotated[bool, Form()] = False,
    status_value: Annotated[str, Form(alias="status")] = EventStatus.draft.value,
    is_public: Annotated[bool, Form()] = False,
):
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404)
    form_values = _event_form_values(
        name=name,
        slug=slug,
        starts_at=starts_at,
        ends_at=ends_at,
        venue=venue,
        address=address,
        short_description=short_description,
        description=description,
        public_meeting_point=public_meeting_point,
        registration_opens_at=registration_opens_at,
        registration_closes_at=registration_closes_at,
        contact_name=contact_name,
        contact_email=contact_email,
        contact_phone=contact_phone,
        briefing=briefing,
        clothing_and_material=clothing_and_material,
        catering_info=catering_info,
        accessibility_info=accessibility_info,
        allows_minors=allows_minors,
        status=status_value,
        is_public=is_public,
    )
    error = None
    field_errors = {}
    if not name.strip():
        field_errors["name"] = "Bitte gib einen Titel ein."
    if not slug.strip():
        field_errors["slug"] = "Bitte gib ein URL-Kürzel ein."
    if starts_at and ends_at and ends_at <= starts_at:
        error = "Das Ende muss nach dem Beginn liegen."
    elif (
        registration_opens_at
        and registration_closes_at
        and registration_closes_at <= registration_opens_at
    ):
        error = "Das Ende der Anmeldung muss nach deren Beginn liegen."
    elif db.scalar(
        select(Event).where(Event.slug == slug.strip(), Event.id != event.id)
    ):
        error = "Dieses URL-Kürzel wird bereits verwendet."
    try:
        event_status = EventStatus(status_value)
    except ValueError:
        event_status = EventStatus.draft
        error = "Ungültiger Veranstaltungsstatus."
    normalized_contact_email = None
    if contact_email.strip():
        try:
            normalized_contact_email = validate_email_address(contact_email)
        except EmailAddressError as exc:
            field_errors["contact_email"] = str(exc)
    if error or field_errors:
        return _render_event_form(
            request,
            event,
            error=error,
            field_errors=field_errors,
            form_values=form_values,
            status_code=422,
        )
    event.name = name.strip()
    event.slug = slug.strip()
    event.starts_at = starts_at
    event.ends_at = ends_at
    event.venue = venue.strip() or None
    event.address = address.strip() or None
    event.short_description = short_description.strip() or None
    event.description = description.strip() or None
    event.public_meeting_point = public_meeting_point.strip() or None
    event.registration_opens_at = registration_opens_at
    event.registration_closes_at = registration_closes_at
    event.contact_name = contact_name.strip() or None
    event.contact_email = normalized_contact_email
    event.contact_phone = contact_phone.strip() or None
    event.briefing = briefing.strip() or None
    event.clothing_and_material = clothing_and_material.strip() or None
    event.catering_info = catering_info.strip() or None
    event.accessibility_info = accessibility_info.strip() or None
    event.allows_minors = allows_minors
    event.status = event_status
    event.is_public = is_public
    record_audit(
        db,
        action="event.updated",
        entity_type="event",
        entity_id=event.id,
        changes={"name": event.name, "status": event.status.value},
    )
    db.commit()
    return RedirectResponse(url=f"/admin/veranstaltungen/{event.id}", status_code=303)


@app.post("/admin/veranstaltungen/{event_id}/duplizieren", tags=["admin"])
def duplicate_event(
    event_id: int, db: Session = db_dependency, admin_user=admin_dependency
):
    source = db.get(Event, event_id)
    if source is None:
        raise HTTPException(status_code=404)
    suffix = 2
    slug = f"{source.slug}-kopie"
    while db.scalar(select(Event).where(Event.slug == slug)):
        slug = f"{source.slug}-kopie-{suffix}"
        suffix += 1
    event = Event(
        name=f"{source.name} (Kopie)",
        slug=slug,
        short_description=source.short_description,
        description=source.description,
        starts_at=source.starts_at,
        ends_at=source.ends_at,
        venue=source.venue,
        address=source.address,
        public_meeting_point=source.public_meeting_point,
        contact_name=source.contact_name,
        contact_email=source.contact_email,
        contact_phone=source.contact_phone,
        briefing=source.briefing,
        clothing_and_material=source.clothing_and_material,
        catering_info=source.catering_info,
        accessibility_info=source.accessibility_info,
        allows_minors=source.allows_minors,
        status=EventStatus.draft,
        is_public=False,
    )
    db.add(event)
    db.flush()
    for source_shift in source.shifts:
        db.add(
            Shift(
                event=event,
                title=source_shift.title,
                description=source_shift.description,
                location=source_shift.location,
                starts_at=source_shift.starts_at,
                ends_at=source_shift.ends_at,
                needed_count=source_shift.needed_count,
                waitlist_capacity=source_shift.waitlist_capacity,
                status=ShiftStatus.draft,
            )
        )
    record_audit(
        db,
        action="event.duplicated",
        entity_type="event",
        entity_id=event.id,
        changes={"source_id": source.id},
    )
    db.commit()
    return RedirectResponse(url=f"/admin/veranstaltungen/{event.id}", status_code=303)


@app.post("/admin/veranstaltungen/{event_id}/archivieren", tags=["admin"])
def archive_event(
    event_id: int, db: Session = db_dependency, admin_user=admin_dependency
):
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404)
    event.status = EventStatus.archived
    event.is_public = False
    record_audit(db, action="event.archived", entity_type="event", entity_id=event.id)
    db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/veranstaltungen/{event_id}/bereiche", tags=["admin"])
def create_team(
    event_id: int,
    db: Session = db_dependency,
    admin_user=admin_dependency,
    name: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
):
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404)
    if not name.strip():
        raise HTTPException(status_code=422, detail="Bereichsname ist erforderlich")
    team = Team(event=event, name=name.strip(), description=description.strip() or None)
    db.add(team)
    db.flush()
    record_audit(db, action="team.created", entity_type="team", entity_id=team.id)
    db.commit()
    return RedirectResponse(url=f"/admin/veranstaltungen/{event_id}", status_code=303)


@app.post("/admin/bereiche/{team_id}", tags=["admin"])
def update_team(
    team_id: int,
    db: Session = db_dependency,
    admin_user=admin_dependency,
    name: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
    meeting_point: Annotated[str, Form()] = "",
):
    team = db.get(Team, team_id)
    if team is None:
        raise HTTPException(status_code=404)
    if not name.strip():
        raise HTTPException(status_code=422, detail="Bereichsname ist erforderlich")
    team.name = name.strip()
    team.description = description.strip() or None
    team.meeting_point = meeting_point.strip() or None
    record_audit(
        db,
        action="team.updated",
        entity_type="team",
        entity_id=team.id,
        changes={"name": team.name},
    )
    db.commit()
    return RedirectResponse(
        url=f"/admin/veranstaltungen/{team.event_id}", status_code=303
    )


@app.post("/admin/bereiche/{team_id}/loeschen", tags=["admin"])
def delete_empty_team(
    team_id: int, db: Session = db_dependency, admin_user=admin_dependency
):
    team = db.get(Team, team_id)
    if team is None:
        raise HTTPException(status_code=404)
    if any(role.shifts for role in team.roles):
        raise HTTPException(
            status_code=422,
            detail="Bereiche mit verwendeten Aufgaben können nicht gelöscht werden.",
        )
    event_id = team.event_id
    record_audit(db, action="team.deleted", entity_type="team", entity_id=team.id)
    db.delete(team)
    db.commit()
    return RedirectResponse(url=f"/admin/veranstaltungen/{event_id}", status_code=303)


@app.post("/admin/bereiche/{team_id}/aufgaben", tags=["admin"])
def create_role(
    team_id: int,
    db: Session = db_dependency,
    admin_user=admin_dependency,
    name: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
):
    team = db.get(Team, team_id)
    if team is None:
        raise HTTPException(status_code=404)
    if not name.strip():
        raise HTTPException(status_code=422, detail="Aufgabenname ist erforderlich")
    role = Role(team=team, name=name.strip(), description=description.strip() or None)
    db.add(role)
    db.flush()
    record_audit(db, action="role.created", entity_type="role", entity_id=role.id)
    db.commit()
    return RedirectResponse(
        url=f"/admin/veranstaltungen/{team.event_id}", status_code=303
    )


@app.post("/admin/aufgaben/{role_id}", tags=["admin"])
def update_role(
    role_id: int,
    db: Session = db_dependency,
    admin_user=admin_dependency,
    name: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
    requirements: Annotated[str, Form()] = "",
):
    role = db.get(Role, role_id)
    if role is None:
        raise HTTPException(status_code=404)
    if not name.strip():
        raise HTTPException(status_code=422, detail="Aufgabenname ist erforderlich")
    role.name = name.strip()
    role.description = description.strip() or None
    role.requirements = requirements.strip() or None
    record_audit(
        db,
        action="role.updated",
        entity_type="role",
        entity_id=role.id,
        changes={"name": role.name},
    )
    db.commit()
    return RedirectResponse(
        url=f"/admin/veranstaltungen/{role.team.event_id}", status_code=303
    )


@app.post("/admin/aufgaben/{role_id}/loeschen", tags=["admin"])
def delete_unused_role(
    role_id: int, db: Session = db_dependency, admin_user=admin_dependency
):
    role = db.get(Role, role_id)
    if role is None:
        raise HTTPException(status_code=404)
    if role.shifts:
        raise HTTPException(
            status_code=422,
            detail="Verwendete Aufgaben können nicht gelöscht werden.",
        )
    event_id = role.team.event_id
    record_audit(db, action="role.deleted", entity_type="role", entity_id=role.id)
    db.delete(role)
    db.commit()
    return RedirectResponse(url=f"/admin/veranstaltungen/{event_id}", status_code=303)


@app.post("/admin/bereiche/{team_id}/materialien", tags=["admin"])
def create_team_material(
    team_id: int,
    db: Session = db_dependency,
    admin_user=admin_dependency,
    name: Annotated[str, Form()] = "",
    quantity_required: Annotated[int, Form()] = 1,
    quantity_available: Annotated[int, Form()] = 0,
    unit: Annotated[str, Form()] = "Stück",
    notes: Annotated[str, Form()] = "",
    is_consumable: Annotated[bool, Form()] = False,
):
    team = db.get(Team, team_id)
    if team is None:
        raise HTTPException(status_code=404)
    if not name.strip():
        raise HTTPException(status_code=422, detail="Materialname ist erforderlich")
    if quantity_required < 0 or quantity_available < 0:
        raise HTTPException(status_code=422, detail="Mengen dürfen nicht negativ sein")
    if db.scalar(
        select(TeamMaterial).where(
            TeamMaterial.team_id == team.id, TeamMaterial.name == name.strip()
        )
    ):
        raise HTTPException(
            status_code=422, detail="Dieses Material ist im Bereich bereits vorhanden"
        )
    material = TeamMaterial(
        team=team,
        name=name.strip(),
        quantity_required=quantity_required,
        quantity_available=quantity_available,
        unit=unit.strip() or "Stück",
        notes=notes.strip() or None,
        is_consumable=is_consumable,
    )
    db.add(material)
    db.flush()
    record_audit(
        db,
        action="team_material.created",
        entity_type="team_material",
        entity_id=material.id,
        changes={"team_id": team.id, "name": material.name},
    )
    db.commit()
    return RedirectResponse(
        url=f"/admin/veranstaltungen/{team.event_id}", status_code=303
    )


@app.post("/admin/materialien/{material_id}", tags=["admin"])
def update_team_material(
    material_id: int,
    db: Session = db_dependency,
    admin_user=admin_dependency,
    quantity_required: Annotated[int, Form()] = 1,
    quantity_available: Annotated[int, Form()] = 0,
    notes: Annotated[str, Form()] = "",
    is_active: Annotated[bool, Form()] = False,
):
    material = db.get(TeamMaterial, material_id)
    if material is None:
        raise HTTPException(status_code=404)
    if quantity_required < 0 or quantity_available < 0:
        raise HTTPException(status_code=422, detail="Mengen dürfen nicht negativ sein")
    material.quantity_required = quantity_required
    material.quantity_available = quantity_available
    material.notes = notes.strip() or None
    material.is_active = is_active
    record_audit(
        db,
        action="team_material.updated",
        entity_type="team_material",
        entity_id=material.id,
        changes={
            "required": quantity_required,
            "available": quantity_available,
            "active": is_active,
        },
    )
    db.commit()
    return RedirectResponse(
        url=f"/admin/veranstaltungen/{material.team.event_id}", status_code=303
    )


@app.post("/admin/materialien/{material_id}/loeschen", tags=["admin"])
def delete_unused_team_material(
    material_id: int,
    db: Session = db_dependency,
    admin_user=admin_dependency,
):
    material = db.get(TeamMaterial, material_id)
    if material is None:
        raise HTTPException(status_code=404, detail="Material nicht gefunden.")
    if material.checkin_issues:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Bereits ausgegebenes Material kann aus Gründen der "
                "Nachvollziehbarkeit nicht gelöscht werden. Deaktiviere es stattdessen."
            ),
        )

    event_id = material.team.event_id
    material_name = material.name
    team_id = material.team_id
    record_audit(
        db,
        action="team_material.deleted",
        entity_type="team_material",
        entity_id=material.id,
        changes={"team_id": team_id, "name": material_name},
        actor=admin_user.user_id,
    )
    db.delete(material)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Das Material wurde zwischenzeitlich verwendet und kann nicht "
                "gelöscht werden. Deaktiviere es stattdessen."
            ),
        ) from exc
    return RedirectResponse(url=f"/admin/veranstaltungen/{event_id}", status_code=303)


@app.post("/admin/veranstaltungen/{event_id}/schichten", tags=["admin"])
def create_shift(
    event_id: int,
    db: Session = db_dependency,
    admin_user=admin_dependency,
    title: Annotated[str, Form()] = "",
    starts_at: Annotated[datetime | None, Form()] = None,
    ends_at: Annotated[datetime | None, Form()] = None,
    needed_count: Annotated[int, Form()] = 1,
    waitlist_capacity: Annotated[int | None, Form()] = None,
    role_id: Annotated[int | None, Form()] = None,
    location: Annotated[str, Form()] = "",
):
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404)
    if not title.strip() or starts_at is None or ends_at is None:
        raise HTTPException(
            status_code=422, detail="Titel, Beginn und Ende sind erforderlich"
        )
    if ends_at <= starts_at:
        raise HTTPException(
            status_code=422, detail="Das Ende muss nach dem Beginn liegen"
        )
    if needed_count < 0 or (waitlist_capacity is not None and waitlist_capacity < 0):
        raise HTTPException(
            status_code=422, detail="Kapazitäten dürfen nicht negativ sein"
        )
    role = db.get(Role, role_id) if role_id else None
    if role is not None and role.team.event_id != event.id:
        raise HTTPException(
            status_code=422, detail="Aufgabe gehört zu einer anderen Veranstaltung"
        )
    shift = Shift(
        event=event,
        role=role,
        title=title.strip(),
        starts_at=starts_at,
        ends_at=ends_at,
        needed_count=needed_count,
        waitlist_capacity=waitlist_capacity,
        location=location.strip() or None,
        status=ShiftStatus.open,
    )
    db.add(shift)
    db.flush()
    record_audit(db, action="shift.created", entity_type="shift", entity_id=shift.id)
    db.commit()
    return RedirectResponse(url=f"/admin/veranstaltungen/{event_id}", status_code=303)


@app.get("/admin/schichten/{shift_id}/bearbeiten", tags=["admin"])
def edit_shift_form(
    shift_id: int,
    request: Request,
    db: Session = db_dependency,
    admin_user=admin_dependency,
):
    shift = db.get(Shift, shift_id)
    if shift is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        "admin_shift_form.html",
        {"request": request, "shift": shift, "event": shift.event, "error": None},
    )


@app.post("/admin/schichten/{shift_id}/bearbeiten", tags=["admin"])
def edit_shift_submit(
    shift_id: int,
    request: Request,
    db: Session = db_dependency,
    admin_user=admin_dependency,
    title: Annotated[str, Form()] = "",
    starts_at: Annotated[datetime | None, Form()] = None,
    ends_at: Annotated[datetime | None, Form()] = None,
    needed_count: Annotated[int, Form()] = 1,
    waitlist_capacity: Annotated[int | None, Form()] = None,
    role_id: Annotated[int | None, Form()] = None,
    location: Annotated[str, Form()] = "",
    status_value: Annotated[str, Form(alias="status")] = ShiftStatus.open.value,
):
    shift = db.get(Shift, shift_id)
    if shift is None:
        raise HTTPException(status_code=404)
    error = None
    if not title.strip() or starts_at is None or ends_at is None:
        error = "Titel, Beginn und Ende sind erforderlich."
    elif ends_at <= starts_at:
        error = "Das Ende muss nach dem Beginn liegen."
    elif needed_count < 0 or (waitlist_capacity is not None and waitlist_capacity < 0):
        error = "Kapazitäten dürfen nicht negativ sein."
    role = db.get(Role, role_id) if role_id else None
    if role is not None and role.team.event_id != shift.event_id:
        error = "Aufgabe gehört zu einer anderen Veranstaltung."
    try:
        parsed_status = ShiftStatus(status_value)
    except ValueError:
        parsed_status = shift.status
        error = "Ungültiger Schichtstatus."
    if error:
        return templates.TemplateResponse(
            "admin_shift_form.html",
            {"request": request, "shift": shift, "event": shift.event, "error": error},
            status_code=422,
        )
    shift.title = title.strip()
    shift.starts_at = starts_at
    shift.ends_at = ends_at
    shift.needed_count = needed_count
    shift.waitlist_capacity = waitlist_capacity
    shift.role = role
    shift.location = location.strip() or None
    shift.status = parsed_status
    record_audit(
        db,
        action="shift.updated",
        entity_type="shift",
        entity_id=shift.id,
        changes={"title": shift.title, "status": shift.status.value},
    )
    db.commit()
    return RedirectResponse(
        url=f"/admin/veranstaltungen/{shift.event_id}", status_code=303
    )


@app.post("/admin/schichten/{shift_id}/loeschen", tags=["admin"])
def delete_or_cancel_shift(
    shift_id: int, db: Session = db_dependency, admin_user=admin_dependency
):
    shift = db.get(Shift, shift_id)
    if shift is None:
        raise HTTPException(status_code=404)
    event_id = shift.event_id
    if shift.assignments:
        shift.status = ShiftStatus.cancelled
        action = "shift.cancelled_with_history"
    else:
        db.delete(shift)
        action = "shift.deleted"
    record_audit(db, action=action, entity_type="shift", entity_id=shift.id)
    db.commit()
    return RedirectResponse(url=f"/admin/veranstaltungen/{event_id}", status_code=303)


@app.post("/admin/schichten/{shift_id}/status", tags=["admin"])
def update_shift_status(
    shift_id: int,
    db: Session = db_dependency,
    admin_user=admin_dependency,
    status_value: Annotated[str, Form(alias="status")] = ShiftStatus.open.value,
    needed_count: Annotated[int, Form()] = 1,
):
    shift = db.get(Shift, shift_id)
    if shift is None:
        raise HTTPException(status_code=404)
    try:
        new_status = ShiftStatus(status_value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Ungültiger Schichtstatus") from exc
    if needed_count < 0:
        raise HTTPException(status_code=422, detail="Kapazität darf nicht negativ sein")
    old_status = shift.status
    shift.status = new_status
    shift.needed_count = needed_count
    record_audit(
        db,
        action="shift.updated",
        entity_type="shift",
        entity_id=shift.id,
        changes={"status_from": old_status.value, "status_to": new_status.value},
    )
    db.commit()
    return RedirectResponse(
        url=f"/admin/veranstaltungen/{shift.event_id}", status_code=303
    )


@app.post("/admin/schichten/{shift_id}/warteliste/nachruecken", tags=["admin"])
def promote_waitlist(
    shift_id: int, db: Session = db_dependency, admin_user=admin_dependency
):
    shift = db.get(Shift, shift_id)
    if shift is None:
        raise HTTPException(status_code=404)
    try:
        assignment = promote_first_waitlisted(db, shift)
    except AdminWorkflowError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    send_pending_automatic_messages(db, assignment.volunteer_id)
    return RedirectResponse(
        url=f"/admin/veranstaltungen/{shift.event_id}", status_code=303
    )


@app.get("/admin/check-in", tags=["admin"])
def checkin_page(
    request: Request,
    query: str = "",
    event_id: str | None = None,
    db: Session = db_dependency,
    admin_user=checkin_dependency,
):
    events = list(db.scalars(select(Event).order_by(Event.name)))
    filter_error = None
    status_code = 200
    try:
        parsed_event_id = parse_optional_id(
            event_id, field_name="event_id", label="Die Veranstaltung"
        )
        if parsed_event_id is not None and not any(
            event.id == parsed_event_id for event in events
        ):
            raise QueryFilterError(
                "event_id", "Die ausgewählte Veranstaltung wurde nicht gefunden."
            )
    except QueryFilterError as exc:
        parsed_event_id = None
        filter_error = str(exc)
        status_code = 422
    statement = (
        select(ShiftAssignment)
        .join(Volunteer)
        .join(Shift)
        .where(
            ShiftAssignment.assignment_status.in_(
                [AssignmentStatus.confirmed, AssignmentStatus.checked_in]
            )
        )
        .order_by(Shift.starts_at, Volunteer.last_name, Volunteer.first_name)
    )
    if parsed_event_id is not None:
        statement = statement.where(Shift.event_id == parsed_event_id)
    if query.strip() and not filter_error:
        pattern = f"%{query.strip()}%"
        statement = statement.where(
            Volunteer.first_name.ilike(pattern)
            | Volunteer.last_name.ilike(pattern)
            | Volunteer.email.ilike(pattern)
            | Volunteer.phone.ilike(pattern)
        )
    assignments = [] if filter_error else list(db.scalars(statement))
    return templates.TemplateResponse(
        "admin_checkin.html",
        {
            "request": request,
            "assignments": assignments,
            "events": events,
            "query": query,
            "event_id": parsed_event_id,
            "filter_error": filter_error,
        },
        status_code=status_code,
    )


@app.get("/admin/check-in/scan/{assignment_id}", tags=["admin"])
def qr_checkin_scan(
    assignment_id: int,
    request: Request,
    db: Session = db_dependency,
    admin_user=checkin_dependency,
):
    assignment = db.get(ShiftAssignment, assignment_id)
    if assignment is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        "admin_checkin_scan.html", {"request": request, "assignment": assignment}
    )


@app.get("/admin/zuteilungen/{assignment_id}/qr.svg", tags=["admin"])
def admin_assignment_qr(
    assignment_id: int,
    request: Request,
    db: Session = db_dependency,
    admin_user=checkin_dependency,
):
    assignment = db.get(ShiftAssignment, assignment_id)
    if assignment is None:
        raise HTTPException(status_code=404)
    scan_url = str(request.url_for("qr_checkin_scan", assignment_id=assignment.id))
    return Response(
        content=qr_svg(scan_url),
        media_type="image/svg+xml",
        headers={"Cache-Control": "private, no-store"},
    )


@app.get("/admin/ehrenamtliche", tags=["admin"])
def volunteer_list(
    request: Request,
    query: str = "",
    event_id: str | None = None,
    assignment_status: str = "",
    email_verified: str = "",
    u18: str | None = None,
    db: Session = db_dependency,
    admin_user=admin_dependency,
):
    events = list(db.scalars(select(Event).order_by(Event.name)))
    filter_error = None
    status_code = 200
    try:
        parsed_event_id = parse_optional_id(
            event_id, field_name="event_id", label="Die Veranstaltung"
        )
        if parsed_event_id is not None and not any(
            event.id == parsed_event_id for event in events
        ):
            raise QueryFilterError(
                "event_id", "Die ausgewählte Veranstaltung wurde nicht gefunden."
            )
        parsed_u18 = parse_checkbox(u18, field_name="u18", label="Der U18-Filter")
        parsed_assignment_status = (
            AssignmentStatus(assignment_status) if assignment_status else None
        )
        if email_verified not in {"", "verified", "pending"}:
            raise QueryFilterError(
                "email_verified", "Der E-Mail-Statusfilter ist ungültig."
            )
    except QueryFilterError as exc:
        parsed_event_id = None
        parsed_u18 = False
        parsed_assignment_status = None
        filter_error = str(exc)
        status_code = 422
    except ValueError:
        parsed_event_id = None
        parsed_u18 = False
        parsed_assignment_status = None
        filter_error = "Der Zuteilungsstatusfilter ist ungültig."
        status_code = 422
    statement = select(Volunteer).order_by(Volunteer.last_name, Volunteer.first_name)
    if query.strip() and not filter_error:
        pattern = f"%{query.strip()}%"
        statement = statement.where(
            Volunteer.first_name.ilike(pattern)
            | Volunteer.last_name.ilike(pattern)
            | Volunteer.email.ilike(pattern)
            | Volunteer.phone.ilike(pattern)
        )
    if parsed_event_id is not None:
        statement = statement.where(Volunteer.event_id == parsed_event_id)
    if parsed_u18:
        statement = statement.where(Volunteer.age_group != AgeGroup.adult)
    if parsed_assignment_status is not None:
        statement = statement.join(ShiftAssignment).where(
            ShiftAssignment.assignment_status == parsed_assignment_status
        )
    if email_verified == "verified":
        statement = statement.where(Volunteer.email_verified_at.is_not(None))
    elif email_verified == "pending":
        statement = statement.where(Volunteer.email_verified_at.is_(None))
    return templates.TemplateResponse(
        "admin_volunteer_list.html",
        {
            "request": request,
            "volunteers": (
                [] if filter_error else list(db.scalars(statement).unique())
            ),
            "query": query,
            "events": events,
            "event_id": parsed_event_id,
            "assignment_status": assignment_status,
            "email_verified": email_verified,
            "assignment_statuses": list(AssignmentStatus),
            "volunteer_statuses": list(VolunteerStatus),
            "briefings": list(db.scalars(select(Briefing).order_by(Briefing.title))),
            "u18": parsed_u18,
            "filter_error": filter_error,
        },
        status_code=status_code,
    )


@app.post("/admin/ehrenamtliche/bulk", tags=["admin"])
def volunteer_bulk_action(
    db: Session = db_dependency,
    admin_user=admin_dependency,
    volunteer_ids: Annotated[list[int] | None, Form()] = None,
    bulk_choice: Annotated[str, Form()] = "",
):
    selected_ids = list(dict.fromkeys(volunteer_ids or []))
    if not selected_ids:
        raise HTTPException(status_code=422, detail="Bitte Personen auswählen.")
    volunteers = list(
        db.scalars(select(Volunteer).where(Volunteer.id.in_(selected_ids)))
    )
    if len(volunteers) != len(selected_ids):
        raise HTTPException(
            status_code=422, detail="Auswahl enthält ungültige Personen."
        )
    if "|" not in bulk_choice:
        raise HTTPException(status_code=422, detail="Bitte eine Bulk-Aktion auswählen.")
    action, value = bulk_choice.split("|", 1)
    if action == "volunteer_status":
        try:
            new_status = VolunteerStatus(value)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail="Ungültiger Personenstatus."
            ) from exc
        if new_status == VolunteerStatus.rejected:
            for volunteer in volunteers:
                reject_volunteer(db, volunteer, "Entscheidung des Organisationsteams")
                send_pending_automatic_messages(db, volunteer.id)
        else:
            for volunteer in volunteers:
                volunteer.status = new_status
                record_audit(
                    db,
                    action="volunteer.bulk_status_changed",
                    entity_type="volunteer",
                    entity_id=volunteer.id,
                    changes={"status": new_status.value},
                )
    elif action == "assignment_status":
        try:
            new_status = AssignmentStatus(value)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail="Ungültiger Zuteilungsstatus."
            ) from exc
        if new_status == AssignmentStatus.checked_in:
            raise HTTPException(
                status_code=422, detail="Check-in ist nicht als Bulk-Aktion zulässig."
            )
        for volunteer in volunteers:
            for assignment in volunteer.assignments:
                if assignment.assignment_status != AssignmentStatus.cancelled:
                    assignment.assignment_status = new_status
                    record_audit(
                        db,
                        action="assignment.bulk_status_changed",
                        entity_type="shift_assignment",
                        entity_id=assignment.id,
                        changes={"status": new_status.value},
                    )
    elif action == "briefing_confirm":
        try:
            briefing_id = int(value)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Ungültiges Briefing.") from exc
        briefing = db.get(Briefing, briefing_id)
        if briefing is None:
            raise HTTPException(status_code=404)
        for volunteer in volunteers:
            if volunteer.event_id != briefing.event_id:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Alle Personen müssen zur Veranstaltung des Briefings gehören."
                    ),
                )
            exists = db.scalar(
                select(BriefingConfirmation).where(
                    BriefingConfirmation.briefing_id == briefing.id,
                    BriefingConfirmation.volunteer_id == volunteer.id,
                )
            )
            if not exists:
                db.add(
                    BriefingConfirmation(
                        briefing_id=briefing.id, volunteer_id=volunteer.id
                    )
                )
                record_audit(
                    db,
                    action="briefing.bulk_confirmed",
                    entity_type="volunteer",
                    entity_id=volunteer.id,
                    changes={"briefing_id": briefing.id},
                )
    else:
        raise HTTPException(status_code=422, detail="Unbekannte Bulk-Aktion.")
    db.commit()
    return RedirectResponse(url="/admin/ehrenamtliche", status_code=303)


@app.get("/admin/ehrenamtliche/neu", tags=["admin"])
def new_volunteer_form(
    request: Request, db: Session = db_dependency, admin_user=admin_dependency
):
    return templates.TemplateResponse(
        "admin_volunteer_form.html",
        {
            "request": request,
            "events": list(db.scalars(select(Event).order_by(Event.name))),
            "error": None,
        },
    )


@app.post("/admin/ehrenamtliche/neu", tags=["admin"])
def create_volunteer_admin(
    request: Request,
    db: Session = db_dependency,
    admin_user=admin_dependency,
    event_id: Annotated[int, Form()] = 0,
    first_name: Annotated[str, Form()] = "",
    last_name: Annotated[str, Form()] = "",
    email: Annotated[str, Form()] = "",
    phone: Annotated[str, Form()] = "",
    age_group: Annotated[str, Form()] = AgeGroup.adult.value,
):
    event = db.get(Event, event_id)
    error = None
    if event is None:
        error = "Bitte eine Veranstaltung auswählen."
    elif not first_name.strip() or not last_name.strip():
        error = "Vorname, Nachname und eine gültige E-Mail-Adresse sind erforderlich."
    try:
        normalized_email = validate_email_address(email)
    except EmailAddressError as exc:
        normalized_email = email.strip().lower()
        error = str(exc)
    try:
        parsed_age_group = AgeGroup(age_group)
    except ValueError:
        parsed_age_group = AgeGroup.adult
        error = "Ungültige Altersgruppe."
    if event and db.scalar(
        select(Volunteer).where(
            Volunteer.event_id == event.id,
            Volunteer.email_normalized == normalized_email,
        )
    ):
        error = "Diese E-Mail-Adresse ist für die Veranstaltung bereits vorhanden."
    if error:
        return templates.TemplateResponse(
            "admin_volunteer_form.html",
            {
                "request": request,
                "events": list(db.scalars(select(Event).order_by(Event.name))),
                "error": error,
            },
            status_code=422,
        )
    volunteer = Volunteer(
        event=event,
        first_name=first_name.strip(),
        last_name=last_name.strip(),
        email=normalized_email,
        email_normalized=normalized_email,
        email_hash=deterministic_email_hash(normalized_email),
        phone=phone.strip() or None,
        age_group=parsed_age_group,
    )
    db.add(volunteer)
    db.flush()
    record_audit(
        db,
        action="volunteer.created_by_admin",
        entity_type="volunteer",
        entity_id=volunteer.id,
    )
    db.commit()
    return RedirectResponse(url=f"/admin/ehrenamtliche/{volunteer.id}", status_code=303)


@app.get("/admin/ehrenamtliche/{volunteer_id}", tags=["admin"])
def volunteer_detail(
    volunteer_id: int,
    request: Request,
    db: Session = db_dependency,
    admin_user=admin_dependency,
):
    volunteer = db.get(Volunteer, volunteer_id)
    if volunteer is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        "admin_volunteer_detail.html",
        {
            "request": request,
            "volunteer": volunteer,
            "assignment_statuses": [
                AssignmentStatus.confirmed,
                AssignmentStatus.waitlisted,
                AssignmentStatus.cancelled,
                AssignmentStatus.no_show,
                AssignmentStatus.attended,
            ],
            "available_shifts": list(
                db.scalars(
                    select(Shift)
                    .where(Shift.event_id == volunteer.event_id)
                    .order_by(Shift.starts_at, Shift.title)
                )
            ),
        },
    )


@app.post("/admin/ehrenamtliche/{volunteer_id}/zuteilungen", tags=["admin"])
def create_assignment_admin(
    volunteer_id: int,
    db: Session = db_dependency,
    admin_user=admin_dependency,
    shift_id: Annotated[int, Form()] = 0,
    status_value: Annotated[
        str, Form(alias="status")
    ] = AssignmentStatus.confirmed.value,
    override_conflict: Annotated[bool, Form()] = False,
):
    volunteer = db.get(Volunteer, volunteer_id)
    shift = db.get(Shift, shift_id)
    if volunteer is None or shift is None:
        raise HTTPException(status_code=404)
    try:
        parsed_status = AssignmentStatus(status_value)
        if parsed_status not in {
            AssignmentStatus.confirmed,
            AssignmentStatus.waitlisted,
            AssignmentStatus.pending,
        }:
            raise AdminWorkflowError(
                "Dieser Status ist für neue Zuteilungen nicht zulässig."
            )
        assign_volunteer(
            db,
            volunteer,
            shift,
            status=parsed_status,
            override_conflict=override_conflict,
        )
    except (ValueError, AdminWorkflowError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return RedirectResponse(url=f"/admin/ehrenamtliche/{volunteer.id}", status_code=303)


@app.post("/admin/zuteilungen/{assignment_id}/status", tags=["admin"])
def assignment_status_submit(
    assignment_id: int,
    db: Session = db_dependency,
    admin_user=admin_dependency,
    status_value: Annotated[
        str, Form(alias="status")
    ] = AssignmentStatus.confirmed.value,
):
    assignment = db.get(ShiftAssignment, assignment_id)
    if assignment is None:
        raise HTTPException(status_code=404)
    try:
        new_status = AssignmentStatus(status_value)
        change_assignment_status(db, assignment, new_status)
    except (ValueError, AdminWorkflowError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return RedirectResponse(
        url=f"/admin/ehrenamtliche/{assignment.volunteer_id}", status_code=303
    )


@app.post("/admin/ehrenamtliche/{volunteer_id}/anonymisieren", tags=["admin"])
def volunteer_anonymize_submit(
    volunteer_id: int,
    db: Session = db_dependency,
    admin_user=admin_dependency,
    confirm: Annotated[str, Form()] = "",
):
    volunteer = db.get(Volunteer, volunteer_id)
    if volunteer is None:
        raise HTTPException(status_code=404)
    if confirm != "ANONYMISIEREN":
        raise HTTPException(status_code=422, detail="Bestätigung fehlt")
    anonymize_volunteer(db, volunteer)
    return RedirectResponse(url=f"/admin/ehrenamtliche/{volunteer.id}", status_code=303)


@app.post("/admin/ehrenamtliche/{volunteer_id}/ablehnen", tags=["admin"])
def volunteer_reject_submit(
    volunteer_id: int,
    db: Session = db_dependency,
    admin_user=admin_dependency,
    reason: Annotated[str, Form()] = "",
    confirm: Annotated[str, Form()] = "",
):
    volunteer = db.get(Volunteer, volunteer_id)
    if volunteer is None:
        raise HTTPException(status_code=404)
    if confirm != "ABLEHNEN":
        raise HTTPException(status_code=422, detail="Bestätigung fehlt")
    try:
        reject_volunteer(db, volunteer, reason)
    except AdminWorkflowError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    send_pending_automatic_messages(db, volunteer.id)
    return RedirectResponse(url=f"/admin/ehrenamtliche/{volunteer.id}", status_code=303)


@app.get("/admin/outbox", tags=["admin"])
def outbox_preview(
    request: Request, db: Session = db_dependency, admin_user=admin_dependency
):
    messages = list(
        db.scalars(select(OutboxMessage).order_by(OutboxMessage.created_at.desc()))
    )
    return templates.TemplateResponse(
        "admin_outbox.html",
        {
            "request": request,
            "messages": messages,
            "smtp_configuration": get_smtp_configuration(db),
            "smtp_password_configured": smtp_password_configured(),
            "mail_templates": ensure_mail_templates(db),
        },
    )


@app.post("/admin/outbox/{message_id}/senden", tags=["admin"])
def send_outbox_message_admin(
    message_id: int, db: Session = db_dependency, admin_user=admin_dependency
):
    message = db.get(OutboxMessage, message_id)
    if message is None:
        raise HTTPException(status_code=404)
    if message.sent_at is not None:
        raise HTTPException(
            status_code=422, detail="Nachricht wurde bereits versendet."
        )
    if message.delivery_mode == "disabled":
        raise HTTPException(status_code=422, detail="Nachricht ist deaktiviert.")
    try:
        send_outbox_message(db, message)
    except MailDeliveryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    record_audit(
        db,
        action="outbox.sent",
        entity_type="outbox_message",
        entity_id=message.id,
    )
    db.commit()
    return RedirectResponse(url="/admin/outbox", status_code=303)


@app.get("/admin/einstellungen/smtp", tags=["admin"])
def smtp_configuration_form(
    request: Request, db: Session = db_dependency, admin_user=strict_admin_dependency
):
    return templates.TemplateResponse(
        "admin_smtp.html",
        {
            "request": request,
            "configuration": get_smtp_configuration(db),
            "password_configured": smtp_password_configured(),
            "error": None,
        },
    )


@app.post("/admin/einstellungen/smtp", tags=["admin"])
def smtp_configuration_submit(
    request: Request,
    db: Session = db_dependency,
    admin_user=strict_admin_dependency,
    host: Annotated[str, Form()] = "",
    port: Annotated[int, Form()] = 587,
    username: Annotated[str, Form()] = "",
    from_email: Annotated[str, Form()] = "",
    from_name: Annotated[str, Form()] = "ST. PRIDE Volunteer Management",
    use_starttls: Annotated[bool, Form()] = False,
    use_ssl: Annotated[bool, Form()] = False,
    enabled: Annotated[bool, Form()] = False,
):
    configuration = get_smtp_configuration(db)
    error = None
    try:
        normalized_from = validate_email_address(from_email)
    except EmailAddressError as exc:
        normalized_from = from_email.strip().lower()
        error = str(exc)
    if not host.strip():
        error = "SMTP-Host ist erforderlich."
    elif not 1 <= port <= 65535:
        error = "SMTP-Port muss zwischen 1 und 65535 liegen."
    elif use_starttls and use_ssl:
        error = "SSL und STARTTLS dürfen nicht gleichzeitig aktiv sein."
    elif enabled and username.strip() and not smtp_password_configured():
        error = "Vor dem Aktivieren muss SMTP_PASSWORD als Secret gesetzt sein."
    if error:
        return templates.TemplateResponse(
            "admin_smtp.html",
            {
                "request": request,
                "configuration": configuration,
                "password_configured": smtp_password_configured(),
                "error": error,
            },
            status_code=422,
        )
    if configuration is None:
        configuration = SMTPConfiguration(host=host.strip(), from_email=normalized_from)
        db.add(configuration)
    configuration.host = host.strip()
    configuration.port = port
    configuration.username = username.strip() or None
    configuration.from_email = normalized_from
    configuration.from_name = from_name.strip() or "ST. PRIDE Volunteer Management"
    configuration.use_starttls = use_starttls
    configuration.use_ssl = use_ssl
    configuration.enabled = enabled
    db.flush()
    record_audit(
        db,
        action="smtp_configuration.updated",
        entity_type="smtp_configuration",
        entity_id=configuration.id,
        changes={
            "host": configuration.host,
            "port": configuration.port,
            "enabled": configuration.enabled,
            "password_configured": smtp_password_configured(),
        },
    )
    db.commit()
    return RedirectResponse(url="/admin/einstellungen/smtp", status_code=303)


@app.get("/admin/einstellungen/mail-templates", tags=["admin"])
def mail_template_settings(
    request: Request,
    db: Session = db_dependency,
    admin_user=admin_dependency,
):
    return templates.TemplateResponse(
        "admin_mail_templates.html",
        {
            "request": request,
            "mail_templates": ensure_mail_templates(db),
            "delivery_modes": ["automatic", "manual", "disabled"],
        },
    )


@app.post("/admin/einstellungen/mail-templates/{template_key}", tags=["admin"])
def mail_template_update(
    template_key: str,
    db: Session = db_dependency,
    admin_user=admin_dependency,
    subject_template: Annotated[str, Form()] = "",
    body_template: Annotated[str, Form()] = "",
    delivery_mode: Annotated[str, Form()] = "manual",
):
    ensure_mail_templates(db)
    template = db.scalar(select(MailTemplate).where(MailTemplate.key == template_key))
    if template is None:
        raise HTTPException(status_code=404)
    clean_subject = " ".join(subject_template.splitlines()).strip()
    if not clean_subject or not body_template.strip():
        raise HTTPException(
            status_code=422, detail="Betreff und Text sind erforderlich"
        )
    if len(clean_subject) > 255:
        raise HTTPException(status_code=422, detail="Betreff ist zu lang")
    unknown = unknown_placeholders(clean_subject + "\n" + body_template)
    if unknown:
        raise HTTPException(
            status_code=422,
            detail="Unbekannte Platzhalter: " + ", ".join(sorted(unknown)),
        )
    if delivery_mode not in DELIVERY_MODES:
        raise HTTPException(status_code=422, detail="Ungültiger Versandmodus")
    template.subject_template = clean_subject
    template.body_template = body_template.strip()
    template.delivery_mode = delivery_mode
    record_audit(
        db,
        action="mail_template.updated",
        entity_type="mail_template",
        entity_id=template.id,
        changes={"key": template.key, "delivery_mode": delivery_mode},
    )
    db.commit()
    return RedirectResponse(url="/admin/einstellungen/mail-templates", status_code=303)


@app.get("/admin/briefings", tags=["admin"])
def briefing_overview(
    request: Request,
    db: Session = db_dependency,
    admin_user=admin_dependency,
):
    return templates.TemplateResponse(
        "admin_briefing_overview.html",
        {
            "request": request,
            "events": list(db.scalars(select(Event).order_by(Event.name))),
        },
    )


@app.get("/admin/veranstaltungen/{event_id}/briefings", tags=["admin"])
def briefing_admin(
    event_id: int,
    request: Request,
    db: Session = db_dependency,
    admin_user=admin_dependency,
):
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        "admin_briefings.html", {"request": request, "event": event}
    )


@app.post("/admin/veranstaltungen/{event_id}/briefings", tags=["admin"])
def create_briefing(
    event_id: int,
    db: Session = db_dependency,
    admin_user=admin_dependency,
    title: Annotated[str, Form()] = "",
    content: Annotated[str, Form()] = "",
):
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404)
    if not title.strip() or not content.strip():
        raise HTTPException(
            status_code=422, detail="Titel und Inhalt sind erforderlich"
        )
    briefing = Briefing(event=event, title=title.strip(), content=content.strip())
    db.add(briefing)
    db.flush()
    record_audit(
        db, action="briefing.created", entity_type="briefing", entity_id=briefing.id
    )
    db.commit()
    return RedirectResponse(
        url=f"/admin/veranstaltungen/{event_id}/briefings", status_code=303
    )


@app.get("/admin/veranstaltungen/{event_id}/druck/schichtplan", tags=["admin"])
def print_shift_plan(
    event_id: int,
    request: Request,
    db: Session = db_dependency,
    admin_user=admin_dependency,
):
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404)
    shifts = sorted(
        event.shifts,
        key=lambda item: (
            item.role.team.name if item.role else "",
            item.starts_at,
            item.title,
        ),
    )
    return templates.TemplateResponse(
        "print_shift_plan.html", {"request": request, "event": event, "shifts": shifts}
    )


@app.get("/admin/veranstaltungen/{event_id}/druck/check-in", tags=["admin"])
def print_checkin_list(
    event_id: int,
    request: Request,
    db: Session = db_dependency,
    admin_user=admin_dependency,
):
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404)
    assignments = list(
        db.scalars(
            select(ShiftAssignment)
            .join(Shift)
            .where(
                Shift.event_id == event_id,
                ShiftAssignment.assignment_status.in_(
                    [AssignmentStatus.confirmed, AssignmentStatus.checked_in]
                ),
            )
            .order_by(Shift.starts_at, ShiftAssignment.id)
        )
    )
    return templates.TemplateResponse(
        "print_checkin_list.html",
        {"request": request, "event": event, "assignments": assignments},
    )


@app.get("/admin/veranstaltungen/{event_id}/druck/qr-check-in", tags=["admin"])
def print_qr_checkin_cards(
    event_id: int,
    request: Request,
    db: Session = db_dependency,
    admin_user=admin_dependency,
):
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404)
    assignments = list(
        db.scalars(
            select(ShiftAssignment)
            .join(Shift)
            .where(
                Shift.event_id == event_id,
                ShiftAssignment.assignment_status == AssignmentStatus.confirmed,
            )
            .order_by(Shift.starts_at, ShiftAssignment.id)
        )
    )
    return templates.TemplateResponse(
        "print_qr_checkin_cards.html",
        {"request": request, "event": event, "assignments": assignments},
    )


def csv_download(
    filename: str, header: list[str], rows: list[list[str]]
) -> StreamingResponse:
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(header)
    writer.writerows(rows)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/admin/export/veranstaltungen/{event_id}/kontakte.csv", tags=["admin"])
def export_event_contacts(
    event_id: int, db: Session = db_dependency, admin_user=admin_dependency
):
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404)
    volunteers = list(
        db.scalars(
            select(Volunteer)
            .where(Volunteer.event_id == event_id)
            .order_by(Volunteer.last_name, Volunteer.first_name)
        )
    )
    return csv_download(
        f"{event.slug}-kontakte.csv",
        ["Vorname", "Nachname", "E-Mail", "Telefonnummer"],
        [[v.first_name, v.last_name, v.email, v.phone or ""] for v in volunteers],
    )


@app.get("/admin/export/veranstaltungen/{event_id}/schichten.csv", tags=["admin"])
def export_event_assignments(
    event_id: int, db: Session = db_dependency, admin_user=admin_dependency
):
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404)
    assignments = list(
        db.scalars(
            select(ShiftAssignment)
            .join(Shift)
            .where(Shift.event_id == event_id)
            .order_by(ShiftAssignment.shift_id)
        )
    )
    return csv_download(
        f"{event.slug}-schichten.csv",
        ["Schicht", "Beginn", "Vorname", "Nachname", "Status"],
        [
            [
                a.shift.title,
                a.shift.starts_at.isoformat(),
                a.volunteer.first_name,
                a.volunteer.last_name,
                a.assignment_status.value,
            ]
            for a in assignments
        ],
    )


@app.post("/admin/check-in/{assignment_id}", tags=["admin"])
def checkin_submit(
    assignment_id: int,
    db: Session = db_dependency,
    admin_user=checkin_dependency,
    lanyard: Annotated[bool, Form()] = False,
    wristband: Annotated[bool, Form()] = False,
    radio: Annotated[bool, Form()] = False,
    other: Annotated[str | None, Form()] = None,
    material_ids: Annotated[list[int] | None, Form()] = None,
):
    assignment = db.get(ShiftAssignment, assignment_id)
    if assignment is None:
        raise HTTPException(status_code=404)
    try:
        check_in_assignment(
            db,
            assignment,
            lanyard=lanyard,
            wristband=wristband,
            radio=radio,
            other=other,
            material_ids=material_ids,
        )
    except CheckInError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return RedirectResponse(url="/admin/check-in", status_code=303)


@app.post("/admin/check-in/{assignment_id}/check-out", tags=["admin"])
def checkout_submit(
    assignment_id: int, db: Session = db_dependency, admin_user=checkin_dependency
):
    assignment = db.get(ShiftAssignment, assignment_id)
    if assignment is None:
        raise HTTPException(status_code=404)
    try:
        check_out_assignment(db, assignment)
    except CheckInError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return RedirectResponse(url="/admin/check-in", status_code=303)


@app.get("/admin/db", tags=["admin"])
def admin_database_status(
    request: Request,
    admin_user=strict_admin_dependency,
):
    diagnostics = safe_database_diagnostics()
    return templates.TemplateResponse(
        "admin_db.html",
        {
            "request": request,
            "admin_user": admin_user,
            "diagnostics": diagnostics,
        },
    )


@app.get("/debug/easyauth", tags=["debug"])
def debug_easyauth(request: Request):
    settings = get_settings()
    if not settings.debug:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    user = get_current_user(request)
    return JSONResponse(
        {
            "authenticated": user is not None,
            "name": user.name if user else None,
            "email": user.email if user else None,
            "user_id": user.user_id if user else None,
            "roles": user.roles if user else [],
            "groups": user.groups if user else [],
            "claims": user.claims if user else [],
            "auth_mode": settings.auth_mode,
            "permissions_summary": {
                name: has_permission(user, name, settings)
                for name in permission_names(settings)
            },
        }
    )


@app.get("/debug/oidc", tags=["debug"])
def debug_oidc(request: Request):
    if not get_settings().debug:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return JSONResponse(oidc_diagnostics(request))


@app.get("/debug/config", tags=["debug"])
def debug_config():
    settings = get_settings()
    if not settings.debug:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return JSONResponse(
        {
            "auth_mode": settings.auth_mode,
            "database_provider": safe_database_diagnostics()["database_type"],
            "app_base_url_configured": bool(settings.app_base_url),
        }
    )


@app.get("/debug/db", tags=["debug"])
def debug_database():
    if not get_settings().debug:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return JSONResponse(safe_database_diagnostics())


@app.get("/auth/login", tags=["auth"])
async def login(request: Request):
    settings = get_settings()
    if settings.auth_mode == "easyauth":
        return RedirectResponse(
            url="/.auth/login/aad?post_login_redirect_uri=/auth/post-login",
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        )
    if settings.auth_mode == "oidc":
        return await begin_login(request)
    return RedirectResponse(
        url="/admin", status_code=status.HTTP_307_TEMPORARY_REDIRECT
    )


def authenticated_landing(user, db: Session) -> str:
    if any(user_has_permission(db, user, name) for name in ("admin", "manager")):
        return "/admin"
    if user_has_permission(db, user, "checkin"):
        return "/admin/check-in"
    return "/"


@app.get("/auth/post-login", tags=["auth"])
def post_login(request: Request, db: Session = db_dependency):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return RedirectResponse(
        url=authenticated_landing(user, db),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.get("/auth/callback", tags=["auth"])
async def oidc_callback(request: Request, db: Session = db_dependency):
    user = await complete_login(request)
    return RedirectResponse(
        url=authenticated_landing(user, db),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.get("/auth/logout", tags=["auth"])
def logout(request: Request):
    settings = get_settings()
    if settings.auth_mode == "easyauth":
        return RedirectResponse(
            url="/.auth/logout?post_logout_redirect_uri=/",
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        )
    if settings.auth_mode == "oidc":
        clear_login(request)
    return RedirectResponse(url="/", status_code=status.HTTP_307_TEMPORARY_REDIRECT)
