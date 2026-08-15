"""Public, unauthenticated volunteer-registration routes."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database.session import get_db
from app.models import Event, EventStatus, Shift, ShiftStatus
from app.services.registrations import (
    RegistrationData,
    RegistrationError,
    cancel_assignment,
    create_registration,
    get_volunteer_by_edit_token,
    update_registration,
)

router = APIRouter(tags=["public"])
templates = Jinja2Templates(directory="app/templates")
DatabaseSession = Annotated[Session, Depends(get_db)]


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
        .options(selectinload(Event.shifts).selectinload(Shift.role))
    )
    if event is None:
        raise HTTPException(status_code=404, detail="Veranstaltung nicht gefunden.")
    return event


@router.get("/")
def landing_page(request: Request, db: DatabaseSession):
    return templates.TemplateResponse(
        request, "landing.html", {"events": public_events(db)}
    )


@router.get("/veranstaltungen/{slug}")
def event_detail(slug: str, request: Request, db: DatabaseSession):
    event = get_public_event(db, slug)
    shifts = [shift for shift in event.shifts if shift.status == ShiftStatus.open]
    return templates.TemplateResponse(
        request, "event_detail.html", {"event": event, "shifts": shifts}
    )


@router.get("/veranstaltungen/{slug}/anmeldung")
def registration_form(slug: str, request: Request, db: DatabaseSession):
    event = get_public_event(db, slug)
    shifts = [shift for shift in event.shifts if shift.status == ShiftStatus.open]
    return templates.TemplateResponse(
        request,
        "registration_form.html",
        {"event": event, "shifts": shifts, "error": None},
    )


@router.post("/veranstaltungen/{slug}/anmeldung")
def submit_registration(
    slug: str,
    request: Request,
    db: DatabaseSession,
    first_name: Annotated[str, Form()],
    last_name: Annotated[str, Form()],
    email: Annotated[str, Form()],
    contact_consent: Annotated[bool, Form()] = False,
    shift_ids: Annotated[list[int] | None, Form()] = None,
    phone: Annotated[str | None, Form()] = None,
    pronouns: Annotated[str | None, Form()] = None,
    birth_date: Annotated[date | None, Form()] = None,
    future_contact_consent: Annotated[bool, Form()] = False,
):
    event = get_public_event(db, slug)
    try:
        result = create_registration(
            db,
            event,
            RegistrationData(
                first_name=first_name,
                last_name=last_name,
                email=email,
                phone=phone,
                pronouns=pronouns,
                birth_date=birth_date,
                contact_consent=contact_consent,
                future_contact_consent=future_contact_consent,
            ),
            shift_ids or [],
        )
    except RegistrationError as exc:
        shifts = [shift for shift in event.shifts if shift.status == ShiftStatus.open]
        return templates.TemplateResponse(
            request,
            "registration_form.html",
            {"event": event, "shifts": shifts, "error": str(exc)},
            status_code=422,
        )
    return RedirectResponse(
        url=f"/anmeldung/{result.edit_token}/bestaetigung", status_code=303
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
        },
    )


@router.post("/anmeldung/{token}/bearbeiten")
def submit_registration_edit(
    token: str,
    request: Request,
    db: DatabaseSession,
    first_name: Annotated[str, Form()],
    last_name: Annotated[str, Form()],
    email: Annotated[str, Form()],
    contact_consent: Annotated[bool, Form()] = False,
    shift_ids: Annotated[list[int] | None, Form()] = None,
    phone: Annotated[str | None, Form()] = None,
    pronouns: Annotated[str | None, Form()] = None,
    birth_date: Annotated[date | None, Form()] = None,
    future_contact_consent: Annotated[bool, Form()] = False,
):
    volunteer = get_volunteer_by_edit_token(db, token)
    if volunteer is None:
        raise HTTPException(status_code=404, detail="Bearbeitungslink ungültig.")
    event = get_public_event(db, volunteer.event.slug)
    try:
        update_registration(
            db,
            volunteer,
            RegistrationData(
                first_name=first_name,
                last_name=last_name,
                email=email,
                phone=phone,
                pronouns=pronouns,
                birth_date=birth_date,
                contact_consent=contact_consent,
                future_contact_consent=future_contact_consent,
            ),
            shift_ids or [],
        )
    except RegistrationError as exc:
        shifts = [shift for shift in event.shifts if shift.status == ShiftStatus.open]
        return templates.TemplateResponse(
            request,
            "registration_edit.html",
            {
                "event": event,
                "volunteer": volunteer,
                "token": token,
                "shifts": shifts,
                "selected_shift_ids": set(shift_ids or []),
                "error": str(exc),
            },
            status_code=422,
        )
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
    return RedirectResponse(url=f"/anmeldung/{token}/bestaetigung", status_code=303)
