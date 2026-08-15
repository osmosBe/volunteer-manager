import csv
from datetime import datetime
from io import StringIO
from typing import Annotated

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.public import router as public_router
from app.auth.permissions import has_permission, permission_names, require_permission
from app.auth.provider import get_current_user
from app.config.settings import get_settings
from app.database.session import database_status, get_db
from app.models import (
    AgeGroup,
    AssignmentStatus,
    Briefing,
    Event,
    EventStatus,
    OutboxMessage,
    Role,
    Shift,
    ShiftAssignment,
    ShiftStatus,
    Team,
    Volunteer,
)
from app.services.admin import (
    AdminWorkflowError,
    anonymize_volunteer,
    change_assignment_status,
    promote_first_waitlisted,
    record_audit,
)
from app.services.checkins import (
    CheckInError,
    check_in_assignment,
    check_out_assignment,
)

admin_dependency = Depends(require_permission("admin"))
db_dependency = Depends(get_db)

settings = get_settings()
templates = Jinja2Templates(directory="app/templates")

app = FastAPI(title=settings.app_name, version=settings.app_version)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(public_router)


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
    return templates.TemplateResponse(
        "admin_dashboard.html",
        {
            "request": request,
            "admin_user": admin_user,
            "events": events,
            "event_stats": event_stats,
            "dashboard_error": dashboard_error,
        },
    )


@app.get("/admin/veranstaltungen/neu", tags=["admin"])
def new_event_form(request: Request, admin_user=admin_dependency):
    return templates.TemplateResponse(
        "admin_event_form.html", {"request": request, "event": None, "error": None}
    )


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
    status_value: Annotated[str, Form(alias="status")] = EventStatus.draft.value,
    is_public: Annotated[bool, Form()] = False,
):
    if not name.strip() or not slug.strip():
        return templates.TemplateResponse(
            "admin_event_form.html",
            {
                "request": request,
                "event": None,
                "error": "Titel und URL-Kürzel sind erforderlich.",
            },
            status_code=422,
        )
    if ends_at and starts_at and ends_at <= starts_at:
        return templates.TemplateResponse(
            "admin_event_form.html",
            {
                "request": request,
                "event": None,
                "error": "Das Ende muss nach dem Beginn liegen.",
            },
            status_code=422,
        )
    if db.scalar(select(Event).where(Event.slug == slug.strip())):
        return templates.TemplateResponse(
            "admin_event_form.html",
            {
                "request": request,
                "event": None,
                "error": "Dieses URL-Kürzel wird bereits verwendet.",
            },
            status_code=422,
        )
    try:
        event_status = EventStatus(status_value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail="Ungültiger Veranstaltungsstatus"
        ) from exc
    event = Event(
        name=name.strip(),
        slug=slug.strip(),
        starts_at=starts_at,
        ends_at=ends_at,
        venue=venue.strip() or None,
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
    return templates.TemplateResponse(
        "admin_event_form.html", {"request": request, "event": event, "error": None}
    )


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
    status_value: Annotated[str, Form(alias="status")] = EventStatus.draft.value,
    is_public: Annotated[bool, Form()] = False,
):
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404)
    error = None
    if not name.strip() or not slug.strip():
        error = "Titel und URL-Kürzel sind erforderlich."
    elif starts_at and ends_at and ends_at <= starts_at:
        error = "Das Ende muss nach dem Beginn liegen."
    elif db.scalar(
        select(Event).where(Event.slug == slug.strip(), Event.id != event.id)
    ):
        error = "Dieses URL-Kürzel wird bereits verwendet."
    try:
        event_status = EventStatus(status_value)
    except ValueError:
        event_status = EventStatus.draft
        error = "Ungültiger Veranstaltungsstatus."
    if error:
        return templates.TemplateResponse(
            "admin_event_form.html",
            {"request": request, "event": event, "error": error},
            status_code=422,
        )
    event.name = name.strip()
    event.slug = slug.strip()
    event.starts_at = starts_at
    event.ends_at = ends_at
    event.venue = venue.strip() or None
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
        promote_first_waitlisted(db, shift)
    except AdminWorkflowError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return RedirectResponse(
        url=f"/admin/veranstaltungen/{shift.event_id}", status_code=303
    )


@app.get("/admin/check-in", tags=["admin"])
def checkin_page(
    request: Request, db: Session = db_dependency, admin_user=admin_dependency
):
    assignments = list(
        db.scalars(
            select(ShiftAssignment).where(
                ShiftAssignment.assignment_status.in_(
                    [AssignmentStatus.confirmed, AssignmentStatus.checked_in]
                )
            )
        )
    )
    return templates.TemplateResponse(
        "admin_checkin.html",
        {"request": request, "assignments": assignments, "error": None},
    )


@app.get("/admin/ehrenamtliche", tags=["admin"])
def volunteer_list(
    request: Request,
    query: str = "",
    event_id: int | None = None,
    assignment_status: str = "",
    u18: bool = False,
    db: Session = db_dependency,
    admin_user=admin_dependency,
):
    statement = select(Volunteer).order_by(Volunteer.last_name, Volunteer.first_name)
    if query.strip():
        pattern = f"%{query.strip()}%"
        statement = statement.where(
            Volunteer.first_name.ilike(pattern)
            | Volunteer.last_name.ilike(pattern)
            | Volunteer.email.ilike(pattern)
            | Volunteer.phone.ilike(pattern)
        )
    if event_id is not None:
        statement = statement.where(Volunteer.event_id == event_id)
    if u18:
        statement = statement.where(Volunteer.age_group != AgeGroup.adult)
    if assignment_status:
        try:
            parsed_status = AssignmentStatus(assignment_status)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail="Ungültiger Statusfilter"
            ) from exc
        statement = statement.join(ShiftAssignment).where(
            ShiftAssignment.assignment_status == parsed_status
        )
    return templates.TemplateResponse(
        "admin_volunteer_list.html",
        {
            "request": request,
            "volunteers": list(db.scalars(statement).unique()),
            "query": query,
            "events": list(db.scalars(select(Event).order_by(Event.name))),
            "event_id": event_id,
            "assignment_status": assignment_status,
            "assignment_statuses": list(AssignmentStatus),
            "u18": u18,
        },
    )


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
        },
    )


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


@app.get("/admin/outbox", tags=["admin"])
def outbox_preview(
    request: Request, db: Session = db_dependency, admin_user=admin_dependency
):
    messages = list(
        db.scalars(select(OutboxMessage).order_by(OutboxMessage.created_at.desc()))
    )
    return templates.TemplateResponse(
        "admin_outbox.html", {"request": request, "messages": messages}
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
    admin_user=admin_dependency,
    lanyard: Annotated[bool, Form()] = False,
    wristband: Annotated[bool, Form()] = False,
    radio: Annotated[bool, Form()] = False,
    other: Annotated[str | None, Form()] = None,
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
        )
    except CheckInError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return RedirectResponse(url="/admin/check-in", status_code=303)


@app.post("/admin/check-in/{assignment_id}/check-out", tags=["admin"])
def checkout_submit(
    assignment_id: int, db: Session = db_dependency, admin_user=admin_dependency
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
    admin_user=admin_dependency,
    db: Session = db_dependency,
):
    inspector = inspect(db.bind)
    migration_revision = None
    if inspector.has_table("alembic_version"):
        migration_revision = db.execute(
            text("SELECT version_num FROM alembic_version LIMIT 1")
        ).scalar_one_or_none()

    return templates.TemplateResponse(
        "admin_db.html",
        {
            "request": request,
            "admin_user": admin_user,
            "status": database_status(db),
            "migration_revision": migration_revision,
            "event_count": db.scalar(select(func.count(Event.id))) or 0,
            "volunteer_count": db.scalar(select(func.count(Volunteer.id))) or 0,
            "shift_count": db.scalar(select(func.count(Shift.id))) or 0,
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


@app.get("/auth/login", tags=["auth"])
def login():
    settings = get_settings()
    if settings.auth_mode == "easyauth":
        return RedirectResponse(
            url="/.auth/login/aad?post_login_redirect_uri=/admin",
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        )
    return RedirectResponse(
        url="/admin", status_code=status.HTTP_307_TEMPORARY_REDIRECT
    )


@app.get("/auth/logout", tags=["auth"])
def logout():
    settings = get_settings()
    if settings.auth_mode == "easyauth":
        return RedirectResponse(
            url="/.auth/logout?post_logout_redirect_uri=/",
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        )
    return RedirectResponse(url="/", status_code=status.HTTP_307_TEMPORARY_REDIRECT)
