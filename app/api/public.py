"""Public, unauthenticated volunteer-registration routes."""

from datetime import date, time
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database.session import get_db
from app.forms.query_filters import (
    QueryFilterError,
    parse_checkbox,
    parse_optional_date,
    parse_optional_id,
    parse_optional_time,
)
from app.models import Event, EventStatus, Shift, ShiftStatus
from app.services.mail_delivery import (
    send_pending_automatic_messages,
)
from app.services.qr_codes import qr_svg
from app.services.registrations import (
    RegistrationData,
    RegistrationError,
    available_places,
    cancel_assignment,
    create_registration,
    get_volunteer_by_edit_token,
    update_registration,
    verify_email_token,
)

router = APIRouter(tags=["public"])
templates = Jinja2Templates(directory="app/templates")
DatabaseSession = Annotated[Session, Depends(get_db)]


def _parse_birth_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise RegistrationError(
            "Bitte gib ein gültiges Geburtsdatum an.", "birth_date"
        ) from exc
    return parsed


def _parse_shift_ids(values: list[str] | None) -> list[int]:
    try:
        return [int(value) for value in values or []]
    except (TypeError, ValueError) as exc:
        raise RegistrationError(
            "Mindestens eine ausgewählte Schicht ist ungültig.", "shift_ids"
        ) from exc


def _registration_form_context(
    db: Session,
    event: Event,
    *,
    team_id: int | None = None,
    day: date | None = None,
    time_from: time | None = None,
    time_to: time | None = None,
    available_only: bool = False,
    selected_shift_ids: set[int] | None = None,
    form_values: dict[str, str | bool] | None = None,
    error: str | None = None,
    field_errors: dict[str, str] | None = None,
) -> dict:
    shifts, places = filtered_open_shifts(
        db,
        event,
        team_id=team_id,
        day=day,
        time_from=time_from,
        time_to=time_to,
        available_only=available_only,
    )
    return {
        "event": event,
        "shifts": shifts,
        "places": places,
        "filters": {
            "team_id": team_id,
            "day": day,
            "time_from": time_from,
            "time_to": time_to,
            "available_only": available_only,
        },
        "selected_shift_ids": selected_shift_ids or set(),
        "form_values": form_values or {},
        "error": error,
        "field_errors": field_errors or {},
    }


def public_events(db: Session) -> list[Event]:
    return list(
        db.scalars(
            select(Event)
            .where(
                Event.is_public.is_(True), Event.status == EventStatus.registration_open
            )
            .order_by(Event.starts_at)
        )
    )


def get_public_event(db: Session, slug: str) -> Event:
    event = db.scalar(
        select(Event)
        .where(Event.slug == slug, Event.is_public.is_(True))
        .options(
            selectinload(Event.shifts).selectinload(Shift.role),
            selectinload(Event.teams),
        )
    )
    if event is None:
        raise HTTPException(status_code=404, detail="Veranstaltung nicht gefunden.")
    return event


def filtered_open_shifts(
    db: Session,
    event: Event,
    *,
    team_id: int | None = None,
    day: date | None = None,
    time_from: time | None = None,
    time_to: time | None = None,
    available_only: bool = False,
) -> tuple[list[Shift], dict[int, int]]:
    open_shifts = [shift for shift in event.shifts if shift.status == ShiftStatus.open]
    places = {shift.id: available_places(db, shift) for shift in open_shifts}
    shifts = [
        shift
        for shift in open_shifts
        if (team_id is None or (shift.role and shift.role.team_id == team_id))
        and (day is None or shift.starts_at.date() == day)
        and (time_from is None or shift.starts_at.time() >= time_from)
        and (time_to is None or shift.ends_at.time() <= time_to)
        and (not available_only or places[shift.id] > 0)
    ]
    return sorted(shifts, key=lambda shift: shift.starts_at), places


def _parse_shift_filters(
    event: Event,
    *,
    team_id: str | None,
    day: str | None,
    time_from: str | None,
    time_to: str | None,
    available_only: str | None,
) -> dict[str, int | date | time | bool | None]:
    parsed_team_id = parse_optional_id(
        team_id, field_name="team_id", label="Der Arbeitsbereich"
    )
    if parsed_team_id is not None and not any(
        team.id == parsed_team_id for team in event.teams
    ):
        raise QueryFilterError(
            "team_id", "Der Arbeitsbereich gehört nicht zu dieser Veranstaltung."
        )
    return {
        "team_id": parsed_team_id,
        "day": parse_optional_date(day, field_name="day", label="Der Tag"),
        "time_from": parse_optional_time(
            time_from, field_name="time_from", label="Die Startzeit"
        ),
        "time_to": parse_optional_time(
            time_to, field_name="time_to", label="Die Endzeit"
        ),
        "available_only": parse_checkbox(
            available_only,
            field_name="available_only",
            label="Der Verfügbarkeitsfilter",
        ),
    }


def _empty_shift_filters() -> dict[str, int | date | time | bool | None]:
    return {
        "team_id": None,
        "day": None,
        "time_from": None,
        "time_to": None,
        "available_only": False,
    }


@router.get("/")
def landing_page(request: Request, db: DatabaseSession):
    return templates.TemplateResponse(
        request, "landing.html", {"events": public_events(db)}
    )


@router.get("/veranstaltungen/{slug}")
def event_detail(
    slug: str,
    request: Request,
    db: DatabaseSession,
    team_id: str | None = None,
    day: str | None = None,
    time_from: str | None = None,
    time_to: str | None = None,
    available_only: str | None = None,
):
    event = get_public_event(db, slug)
    filter_error = None
    status_code = 200
    try:
        filters = _parse_shift_filters(
            event,
            team_id=team_id,
            day=day,
            time_from=time_from,
            time_to=time_to,
            available_only=available_only,
        )
    except QueryFilterError as exc:
        filters = _empty_shift_filters()
        filter_error = str(exc)
        status_code = 422
    shifts, places = filtered_open_shifts(
        db,
        event,
        team_id=filters["team_id"],
        day=filters["day"],
        time_from=filters["time_from"],
        time_to=filters["time_to"],
        available_only=bool(filters["available_only"]),
    )
    return templates.TemplateResponse(
        request,
        "event_detail.html",
        {
            "event": event,
            "shifts": shifts,
            "places": places,
            "filters": filters,
            "filter_error": filter_error,
            "filter_query": request.url.query if not filter_error else "",
        },
        status_code=status_code,
    )


@router.get("/veranstaltungen/{slug}/anmeldung")
def registration_form(
    slug: str,
    request: Request,
    db: DatabaseSession,
    team_id: str | None = None,
    day: str | None = None,
    time_from: str | None = None,
    time_to: str | None = None,
    available_only: str | None = None,
    shift_id: str | None = None,
):
    event = get_public_event(db, slug)
    try:
        filters = _parse_shift_filters(
            event,
            team_id=team_id,
            day=day,
            time_from=time_from,
            time_to=time_to,
            available_only=available_only,
        )
        parsed_shift_id = parse_optional_id(
            shift_id, field_name="shift_id", label="Die ausgewählte Schicht"
        )
        if parsed_shift_id is not None and not any(
            shift.id == parsed_shift_id for shift in event.shifts
        ):
            raise QueryFilterError(
                "shift_id", "Die ausgewählte Schicht gehört nicht zur Veranstaltung."
            )
        filter_error = None
        status_code = 200
    except QueryFilterError as exc:
        filters = _empty_shift_filters()
        parsed_shift_id = None
        filter_error = str(exc)
        status_code = 422
    selected_shift_ids = {
        shift.id
        for shift in event.shifts
        if shift.id == parsed_shift_id
        and shift.status == ShiftStatus.open
        and (available_places(db, shift) > 0 or shift.waitlist_capacity is not None)
    }
    return templates.TemplateResponse(
        request,
        "registration_form.html",
        _registration_form_context(
            db,
            event,
            team_id=filters["team_id"],
            day=filters["day"],
            time_from=filters["time_from"],
            time_to=filters["time_to"],
            available_only=bool(filters["available_only"]),
            selected_shift_ids=selected_shift_ids,
            error=filter_error,
        ),
        status_code=status_code,
    )


@router.post("/veranstaltungen/{slug}/anmeldung")
def submit_registration(
    slug: str,
    request: Request,
    db: DatabaseSession,
    first_name: Annotated[str, Form()] = "",
    last_name: Annotated[str, Form()] = "",
    email: Annotated[str, Form()] = "",
    contact_consent: Annotated[bool, Form()] = False,
    shift_ids: Annotated[list[str] | None, Form()] = None,
    phone: Annotated[str | None, Form()] = None,
    pronouns: Annotated[str | None, Form()] = None,
    birth_date: Annotated[str, Form()] = "",
    future_contact_consent: Annotated[bool, Form()] = False,
):
    event = get_public_event(db, slug)
    form_values: dict[str, str | bool] = {
        "first_name": first_name,
        "last_name": last_name,
        "email": email,
        "birth_date": birth_date,
        "phone": phone or "",
        "pronouns": pronouns or "",
        "contact_consent": contact_consent,
        "future_contact_consent": future_contact_consent,
    }
    selected_shift_ids = {
        int(value) for value in shift_ids or [] if str(value).isdigit()
    }
    try:
        parsed_shift_ids = _parse_shift_ids(shift_ids)
        parsed_birth_date = _parse_birth_date(birth_date)
        result = create_registration(
            db,
            event,
            RegistrationData(
                first_name=first_name,
                last_name=last_name,
                email=email,
                phone=phone,
                pronouns=pronouns,
                birth_date=parsed_birth_date,
                contact_consent=contact_consent,
                future_contact_consent=future_contact_consent,
            ),
            parsed_shift_ids,
        )
    except RegistrationError as exc:
        field_errors = {exc.field_name: str(exc)} if exc.field_name else {}
        return templates.TemplateResponse(
            request,
            "registration_form.html",
            _registration_form_context(
                db,
                event,
                selected_shift_ids=selected_shift_ids,
                form_values=form_values,
                error=None if field_errors else str(exc),
                field_errors=field_errors,
            ),
            status_code=422,
        )
    send_pending_automatic_messages(db, result.volunteer.id)
    return RedirectResponse(
        url=f"/anmeldung/{result.edit_token}/bestaetigung", status_code=303
    )


@router.get("/anmeldung/email-bestaetigen/{token}")
def email_verification(token: str, request: Request, db: DatabaseSession):
    volunteer = verify_email_token(db, token)
    if volunteer is None:
        raise HTTPException(status_code=404, detail="Bestätigungslink ungültig.")
    return templates.TemplateResponse(
        request,
        "email_verified.html",
        {"volunteer": volunteer},
    )


@router.get("/anmeldung/{token}/bestaetigung")
def registration_confirmation(token: str, request: Request, db: DatabaseSession):
    volunteer = get_volunteer_by_edit_token(db, token)
    if volunteer is None:
        raise HTTPException(status_code=404, detail="Bearbeitungslink ungültig.")
    return templates.TemplateResponse(
        request,
        "registration_confirmation.html",
        {"volunteer": volunteer, "token": token},
    )


@router.get("/anmeldung/{token}/zuteilungen/{assignment_id}/qr.svg")
def public_assignment_qr(
    token: str, assignment_id: int, request: Request, db: DatabaseSession
):
    volunteer = get_volunteer_by_edit_token(db, token)
    if volunteer is None:
        raise HTTPException(status_code=404, detail="Bearbeitungslink ungültig.")
    assignment = next(
        (
            item
            for item in volunteer.assignments
            if item.id == assignment_id
            and item.assignment_status.value in {"confirmed", "checked_in"}
        ),
        None,
    )
    if assignment is None:
        raise HTTPException(status_code=404, detail="Zuteilung nicht gefunden.")
    scan_url = str(request.url_for("qr_checkin_scan", assignment_id=assignment.id))
    return Response(
        content=qr_svg(scan_url),
        media_type="image/svg+xml",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'inline; filename="checkin-{assignment.id}.svg"',
        },
    )


@router.get("/anmeldung/{token}/bearbeiten")
def edit_registration(token: str, request: Request, db: DatabaseSession):
    volunteer = get_volunteer_by_edit_token(db, token)
    if volunteer is None:
        raise HTTPException(status_code=404, detail="Bearbeitungslink ungültig.")
    event = get_public_event(db, volunteer.event.slug)
    shifts = [shift for shift in event.shifts if shift.status == ShiftStatus.open]
    selected_shift_ids = {
        assignment.shift_id
        for assignment in volunteer.assignments
        if assignment.assignment_status.value != "cancelled"
    }
    return templates.TemplateResponse(
        request,
        "registration_edit.html",
        {
            "event": event,
            "volunteer": volunteer,
            "token": token,
            "shifts": shifts,
            "selected_shift_ids": selected_shift_ids,
            "error": None,
            "field_errors": {},
            "form_values": {},
        },
    )


@router.post("/anmeldung/{token}/bearbeiten")
def submit_registration_edit(
    token: str,
    request: Request,
    db: DatabaseSession,
    first_name: Annotated[str, Form()] = "",
    last_name: Annotated[str, Form()] = "",
    email: Annotated[str, Form()] = "",
    contact_consent: Annotated[bool, Form()] = False,
    shift_ids: Annotated[list[str] | None, Form()] = None,
    phone: Annotated[str | None, Form()] = None,
    pronouns: Annotated[str | None, Form()] = None,
    birth_date: Annotated[str, Form()] = "",
    future_contact_consent: Annotated[bool, Form()] = False,
):
    volunteer = get_volunteer_by_edit_token(db, token)
    if volunteer is None:
        raise HTTPException(status_code=404, detail="Bearbeitungslink ungültig.")
    event = get_public_event(db, volunteer.event.slug)
    form_values: dict[str, str | bool] = {
        "first_name": first_name,
        "last_name": last_name,
        "email": email,
        "birth_date": birth_date,
        "phone": phone or "",
        "pronouns": pronouns or "",
        "contact_consent": contact_consent,
        "future_contact_consent": future_contact_consent,
    }
    selected_shift_ids = {
        int(value) for value in shift_ids or [] if str(value).isdigit()
    }
    try:
        parsed_shift_ids = _parse_shift_ids(shift_ids)
        parsed_birth_date = _parse_birth_date(birth_date)
        update_registration(
            db,
            volunteer,
            RegistrationData(
                first_name=first_name,
                last_name=last_name,
                email=email,
                phone=phone,
                pronouns=pronouns,
                birth_date=parsed_birth_date,
                contact_consent=contact_consent,
                future_contact_consent=future_contact_consent,
            ),
            parsed_shift_ids,
        )
    except RegistrationError as exc:
        shifts = [shift for shift in event.shifts if shift.status == ShiftStatus.open]
        field_errors = {exc.field_name: str(exc)} if exc.field_name else {}
        return templates.TemplateResponse(
            request,
            "registration_edit.html",
            {
                "event": event,
                "volunteer": volunteer,
                "token": token,
                "shifts": shifts,
                "selected_shift_ids": selected_shift_ids,
                "error": None if field_errors else str(exc),
                "field_errors": field_errors,
                "form_values": form_values,
            },
            status_code=422,
        )
    send_pending_automatic_messages(db, volunteer.id)
    return RedirectResponse(url=f"/anmeldung/{token}/bestaetigung", status_code=303)


@router.post("/anmeldung/{token}/zuteilungen/{assignment_id}/stornieren")
def cancel_registration_assignment(token: str, assignment_id: int, db: DatabaseSession):
    volunteer = get_volunteer_by_edit_token(db, token)
    if volunteer is None:
        raise HTTPException(status_code=404, detail="Bearbeitungslink ungültig.")
    try:
        cancel_assignment(db, volunteer, assignment_id)
    except RegistrationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    send_pending_automatic_messages(db, volunteer.id)
    return RedirectResponse(url=f"/anmeldung/{token}/bestaetigung", status_code=303)
