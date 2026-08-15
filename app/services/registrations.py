"""Public volunteer registration workflow.

The service deliberately owns all allocation decisions.  HTTP handlers only
collect input and render results, which keeps public and later admin workflows
on the same data-integrity rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
from secrets import token_urlsafe

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.config.settings import get_settings
from app.models import (
    AgeGroup,
    AssignmentSource,
    AssignmentStatus,
    Event,
    EventStatus,
    OutboxMessage,
    Shift,
    ShiftAssignment,
    ShiftStatus,
    Volunteer,
    VolunteerStatus,
)
from app.models.core import utcnow
from app.services.email_addresses import EmailAddressError, validate_email_address
from app.services.volunteers import deterministic_email_hash


class RegistrationError(ValueError):
    """Raised when a registration cannot safely be accepted."""


ACTIVE_ASSIGNMENT_STATUSES = (
    AssignmentStatus.pending,
    AssignmentStatus.confirmed,
    AssignmentStatus.checked_in,
    AssignmentStatus.attended,
)


@dataclass(frozen=True)
class RegistrationData:
    first_name: str
    last_name: str
    email: str
    contact_consent: bool
    phone: str | None = None
    pronouns: str | None = None
    age_group: AgeGroup = AgeGroup.adult
    birth_date: date | None = None
    emergency_contact_name: str | None = None
    emergency_contact_phone: str | None = None
    tshirt_size: str | None = None
    dietary_needs: str | None = None
    accessibility_needs: str | None = None
    experience: str | None = None
    notes: str | None = None
    future_contact_consent: bool = False
    privacy_version: str = "prototype-1"


@dataclass(frozen=True)
class RegistrationResult:
    volunteer: Volunteer
    assignments: tuple[ShiftAssignment, ...]
    edit_token: str


def hash_edit_token(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    """Normalize SQLite's timezone-naive timestamps for safe comparisons."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def available_places(db: Session, shift: Shift) -> int:
    assigned_count = (
        db.scalar(
            select(func.count(ShiftAssignment.id)).where(
                ShiftAssignment.shift_id == shift.id,
                ShiftAssignment.assignment_status.in_(ACTIVE_ASSIGNMENT_STATUSES),
            )
        )
        or 0
    )
    return max(shift.needed_count - assigned_count, 0)


def _validate_event_is_open(event: Event) -> None:
    now = utcnow()
    if not event.is_public or event.status != EventStatus.registration_open:
        raise RegistrationError(
            "Die Anmeldung für diese Veranstaltung ist nicht geöffnet."
        )
    if event.registration_opens_at and _as_utc(event.registration_opens_at) > now:
        raise RegistrationError(
            "Die Anmeldung für diese Veranstaltung hat noch nicht begonnen."
        )
    if event.registration_closes_at and _as_utc(event.registration_closes_at) < now:
        raise RegistrationError(
            "Die Anmeldung für diese Veranstaltung ist bereits geschlossen."
        )


def _selected_shifts(db: Session, event: Event, shift_ids: list[int]) -> list[Shift]:
    unique_ids = list(dict.fromkeys(shift_ids))
    if not unique_ids:
        raise RegistrationError("Bitte wähle mindestens eine Schicht aus.")
    if len(unique_ids) != len(shift_ids):
        raise RegistrationError("Eine Schicht wurde mehrfach ausgewählt.")

    shifts = list(
        db.scalars(
            select(Shift)
            .where(Shift.event_id == event.id, Shift.id.in_(unique_ids))
            .options(joinedload(Shift.role))
        )
    )
    if len(shifts) != len(unique_ids):
        raise RegistrationError("Mindestens eine ausgewählte Schicht ist ungültig.")
    if any(shift.status != ShiftStatus.open for shift in shifts):
        raise RegistrationError(
            "Mindestens eine ausgewählte Schicht ist nicht mehr offen."
        )

    ordered = sorted(shifts, key=lambda shift: shift.starts_at)
    for previous, current in zip(ordered, ordered[1:], strict=False):
        if _as_utc(previous.ends_at) > _as_utc(current.starts_at):
            raise RegistrationError(
                "Die ausgewählten Schichten überschneiden sich zeitlich."
            )
    return ordered


def _assignment_status(db: Session, shift: Shift) -> AssignmentStatus:
    if available_places(db, shift) > 0:
        return AssignmentStatus.confirmed

    if shift.waitlist_capacity is None:
        raise RegistrationError(f"Die Schicht „{shift.title}“ ist bereits voll.")

    waitlist_count = (
        db.scalar(
            select(func.count(ShiftAssignment.id)).where(
                ShiftAssignment.shift_id == shift.id,
                ShiftAssignment.assignment_status == AssignmentStatus.waitlisted,
            )
        )
        or 0
    )
    if waitlist_count >= shift.waitlist_capacity:
        raise RegistrationError(f"Die Warteliste für „{shift.title}“ ist voll.")
    return AssignmentStatus.waitlisted


def create_registration(
    db: Session, event: Event, data: RegistrationData, shift_ids: list[int]
) -> RegistrationResult:
    """Persist a public registration and return its one-time edit token.

    An email address is intentionally not used to look up existing people:
    knowing an address must never grant access to someone else's information.
    """
    if not data.contact_consent:
        raise RegistrationError(
            "Bitte stimme der Kontaktaufnahme für diese Veranstaltung zu."
        )
    if (
        not data.first_name.strip()
        or not data.last_name.strip()
        or not data.email.strip()
    ):
        raise RegistrationError(
            "Vorname, Nachname und E-Mail-Adresse sind erforderlich."
        )

    _validate_event_is_open(event)
    shifts = _selected_shifts(db, event, shift_ids)
    statuses = {shift.id: _assignment_status(db, shift) for shift in shifts}
    edit_token = token_urlsafe(32)
    try:
        email = validate_email_address(data.email)
    except EmailAddressError as exc:
        raise RegistrationError(str(exc)) from exc
    volunteer = Volunteer(
        event=event,
        first_name=data.first_name.strip(),
        last_name=data.last_name.strip(),
        email=email,
        email_normalized=email,
        email_hash=deterministic_email_hash(email),
        contact_consent=True,
        future_contact_consent=data.future_contact_consent,
        consented_at=utcnow(),
        privacy_version=data.privacy_version,
        edit_token_hash=hash_edit_token(edit_token),
        status=VolunteerStatus.submitted,
        phone=data.phone,
        pronouns=data.pronouns,
        age_group=data.age_group,
        birth_date=data.birth_date,
        emergency_contact_name=data.emergency_contact_name,
        emergency_contact_phone=data.emergency_contact_phone,
        tshirt_size=data.tshirt_size,
        dietary_needs=data.dietary_needs,
        accessibility_needs=data.accessibility_needs,
        experience=data.experience,
        notes=data.notes,
    )
    db.add(volunteer)
    db.flush()

    assignments = tuple(
        ShiftAssignment(
            volunteer=volunteer,
            shift=shift,
            assignment_status=statuses[shift.id],
            source=AssignmentSource.public,
            registered_at=utcnow(),
            confirmed_at=(
                utcnow() if statuses[shift.id] == AssignmentStatus.confirmed else None
            ),
        )
        for shift in shifts
    )
    db.add_all(assignments)
    db.add(
        OutboxMessage(
            volunteer_id=volunteer.id,
            kind="registration_confirmation",
            subject=f"Deine Anmeldung für {event.name}",
            body=(
                f"Hallo {volunteer.first_name},\n\n"
                f"deine Anmeldung für {event.name} wurde erfasst. "
                "Du kannst sie über diesen persönlichen Link ansehen und ändern:\n"
                f"{get_settings().app_base_url.rstrip('/')}/anmeldung/"
                f"{edit_token}/bestaetigung\n\nST. PRIDE"
            ),
            recipient_email=email,
        )
    )
    db.commit()
    db.refresh(volunteer)
    return RegistrationResult(volunteer, assignments, edit_token)


def get_volunteer_by_edit_token(db: Session, token: str) -> Volunteer | None:
    if not token:
        return None
    return db.scalar(
        select(Volunteer)
        .where(
            Volunteer.edit_token_hash == hash_edit_token(token),
            Volunteer.edit_token_revoked_at.is_(None),
        )
        .options(joinedload(Volunteer.assignments).joinedload(ShiftAssignment.shift))
    )


def update_registration(
    db: Session, volunteer: Volunteer, data: RegistrationData, shift_ids: list[int]
) -> tuple[ShiftAssignment, ...]:
    """Update contact details and replace the public shift selection safely."""
    if not data.contact_consent:
        raise RegistrationError(
            "Bitte stimme der Kontaktaufnahme für diese Veranstaltung zu."
        )
    if (
        not data.first_name.strip()
        or not data.last_name.strip()
        or not data.email.strip()
    ):
        raise RegistrationError(
            "Vorname, Nachname und E-Mail-Adresse sind erforderlich."
        )

    event = db.get(Event, volunteer.event_id)
    if event is None:
        raise RegistrationError("Die Veranstaltung wurde nicht gefunden.")
    _validate_event_is_open(event)
    shifts = _selected_shifts(db, event, shift_ids)
    selected_ids = {shift.id for shift in shifts}
    active_assignments = {
        assignment.shift_id: assignment
        for assignment in volunteer.assignments
        if assignment.assignment_status in ACTIVE_ASSIGNMENT_STATUSES
        or assignment.assignment_status == AssignmentStatus.waitlisted
    }
    assignments_by_shift = {
        assignment.shift_id: assignment for assignment in volunteer.assignments
    }

    # Free removed selections before calculating capacity for new selections.
    for shift_id, assignment in active_assignments.items():
        if shift_id not in selected_ids:
            assignment.assignment_status = AssignmentStatus.cancelled
            assignment.cancelled_at = utcnow()

    assignments: list[ShiftAssignment] = []
    for shift in shifts:
        existing = active_assignments.get(shift.id)
        if existing is not None:
            assignments.append(existing)
            continue
        cancelled_assignment = assignments_by_shift.get(shift.id)
        if cancelled_assignment is not None:
            status = _assignment_status(db, shift)
            cancelled_assignment.assignment_status = status
            cancelled_assignment.cancelled_at = None
            cancelled_assignment.confirmed_at = (
                utcnow() if status == AssignmentStatus.confirmed else None
            )
            assignments.append(cancelled_assignment)
            continue
        status = _assignment_status(db, shift)
        assignment = ShiftAssignment(
            volunteer=volunteer,
            shift=shift,
            assignment_status=status,
            source=AssignmentSource.public,
            registered_at=utcnow(),
            confirmed_at=utcnow() if status == AssignmentStatus.confirmed else None,
        )
        db.add(assignment)
        assignments.append(assignment)

    try:
        email = validate_email_address(data.email)
    except EmailAddressError as exc:
        raise RegistrationError(str(exc)) from exc
    volunteer.first_name = data.first_name.strip()
    volunteer.last_name = data.last_name.strip()
    volunteer.email = email
    volunteer.email_normalized = email
    volunteer.email_hash = deterministic_email_hash(email)
    volunteer.phone = data.phone
    volunteer.pronouns = data.pronouns
    volunteer.birth_date = data.birth_date
    volunteer.contact_consent = True
    volunteer.future_contact_consent = data.future_contact_consent
    db.add(
        OutboxMessage(
            volunteer_id=volunteer.id,
            kind="registration_updated",
            subject="Deine Anmeldung wurde aktualisiert",
            body="Deine Kontaktdaten oder Schichten wurden aktualisiert.",
            recipient_email=email,
        )
    )
    db.commit()
    db.refresh(volunteer)
    return tuple(assignments)


def cancel_assignment(db: Session, volunteer: Volunteer, assignment_id: int) -> None:
    assignment = db.scalar(
        select(ShiftAssignment).where(
            ShiftAssignment.id == assignment_id,
            ShiftAssignment.volunteer_id == volunteer.id,
        )
    )
    if assignment is None or assignment.assignment_status == AssignmentStatus.cancelled:
        raise RegistrationError("Die ausgewählte Anmeldung wurde nicht gefunden.")
    assignment.assignment_status = AssignmentStatus.cancelled
    assignment.cancelled_at = utcnow()
    db.add(
        OutboxMessage(
            volunteer_id=volunteer.id,
            kind="registration_cancellation",
            subject="Deine Schicht wurde storniert",
            body="Eine deiner Schichten wurde storniert.",
            recipient_email=volunteer.email,
        )
    )
    db.commit()
