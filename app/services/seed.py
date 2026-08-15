from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AssignmentSource,
    AssignmentStatus,
    Event,
    EventStatus,
    Role,
    Shift,
    ShiftAssignment,
    ShiftStatus,
    Team,
    Volunteer,
    VolunteerStatus,
)
from app.services.volunteers import deterministic_email_hash

DEFAULT_EVENT_NAME = "St. Pölten PRIDE 2026"
DEFAULT_EVENT_SLUG = "pride-2026"


def ensure_default_event(db: Session) -> Event:
    existing = db.scalar(select(Event).where(Event.slug == DEFAULT_EVENT_SLUG))
    if existing:
        return existing

    if db.scalar(select(Event).limit(1)):
        raise ValueError("Cannot seed default event because an event already exists")

    event = Event(
        name=DEFAULT_EVENT_NAME, slug=DEFAULT_EVENT_SLUG, status=EventStatus.draft
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def ensure_demo_data(db: Session) -> Event:
    """Create a repeatable, strictly fictional ST. PRIDE demonstration event."""
    event = db.scalar(select(Event).where(Event.slug == DEFAULT_EVENT_SLUG))
    if event is None:
        event = Event(
            name=DEFAULT_EVENT_NAME,
            slug=DEFAULT_EVENT_SLUG,
            short_description="Dein Einsatz für die St. Pölten PRIDE 2026.",
            description="Demo-Veranstaltung mit ausschließlich erfundenen Daten.",
            starts_at=datetime(2026, 7, 25, 13, tzinfo=timezone.utc),
            ends_at=datetime(2026, 7, 26, 2, tzinfo=timezone.utc),
            venue="St. Pölten Innenstadt",
            public_meeting_point="ST.-PRIDE-Infostand",
            status=EventStatus.registration_open,
            is_public=True,
            briefing=(
                "Check-in am Infostand. Briefing um 14:00. Bei Eskalationen: "
                "zuerst Eigenschutz, dann Teamleitung holen."
            ),
            clothing_and_material=(
                "Bequeme Kleidung, festes Schuhwerk und wetterfeste Schicht."
            ),
            accessibility_info="Bitte Unterstützungsbedarf bei der Anmeldung angeben.",
        )
        db.add(event)
        db.flush()

    if db.scalar(select(Team.id).where(Team.event_id == event.id)):
        return event

    start = datetime(2026, 7, 25, 13, tzinfo=timezone.utc)
    role_specs = [
        ("Aufbau", "Aufbau Platz/Stände", 10, 0),
        ("Aufbau", "Aufbau Demowagen", 4, 0),
        ("Parade", "Besucher:innenzählung", 4, 2),
        ("Parade", "Ordner:in Parade", 40, 4),
        ("Platz", "Ordner:in Platz", 20, 3),
        ("Platz", "Solibandverteilung", 6, 2),
        ("Backstage", "Backstage-/Artist-Betreuung", 4, 1),
        ("Clubbing", "Clubbing-Aufbau", 2, 0),
        ("Abbau", "Abbau Demowagen", 3, 0),
        ("Abbau", "Abbau Platz/Stände", 6, 0),
        ("Abbau", "Abbau Bühne", 2, 0),
        ("Organisation", "Organisations-Team", 5, 1),
    ]
    teams: dict[str, Team] = {}
    for index, name in enumerate(dict.fromkeys(spec[0] for spec in role_specs)):
        team = Team(
            event=event,
            name=name,
            sort_order=index,
            lead_name="Demo-Teamleitung",
            lead_contact="teamleitung@example.invalid",
            meeting_point="ST.-PRIDE-Infostand",
        )
        teams[name] = team
        db.add(team)
    db.flush()

    shifts: list[Shift] = []
    for _index, (team_name, role_name, capacity, waitlist_capacity) in enumerate(
        role_specs
    ):
        role = Role(
            team=teams[team_name],
            name=role_name,
            short_description=f"Demo-Rolle: {role_name}",
            prefer_pair="Ordner:in" in role_name or role_name == "Solibandverteilung",
            default_meeting_point="ST.-PRIDE-Infostand",
        )
        offset = 0 if team_name == "Aufbau" else 1 if team_name == "Abbau" else 2
        shift = Shift(
            event=event,
            role=role,
            title=role_name,
            starts_at=start + timedelta(hours=offset),
            ends_at=start + timedelta(hours=offset + 4),
            location="ST.-PRIDE-Infostand",
            needed_count=capacity,
            waitlist_capacity=waitlist_capacity or None,
            lead_name="Demo-Teamleitung",
            volunteer_notes=(
                "Treffen am Infostand; bei Bedarf Ausgabe von Lanyard und "
                "Zutrittsbändchen."
            ),
            status=ShiftStatus.open,
        )
        shifts.append(shift)
        db.add_all([role, shift])
    db.flush()

    for index in range(25):
        email = f"demo-{index + 1:02d}@example.invalid"
        volunteer = Volunteer(
            event=event,
            first_name=f"Demo{index + 1}",
            last_name="Ehrenamt",
            email=email,
            email_normalized=email,
            email_hash=deterministic_email_hash(email),
            contact_consent=True,
            status=VolunteerStatus.assigned,
        )
        status = (
            AssignmentStatus.waitlisted
            if index in {3, 14, 24}
            else AssignmentStatus.confirmed
        )
        db.add_all(
            [
                volunteer,
                ShiftAssignment(
                    volunteer=volunteer,
                    shift=shifts[index % len(shifts)],
                    assignment_status=status,
                    source=AssignmentSource.admin,
                ),
            ]
        )
    db.commit()
    db.refresh(event)
    return event
