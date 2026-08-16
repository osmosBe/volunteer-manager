"""Idempotent, strictly fictional data for local and DEV demonstrations."""

import json
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AgeGroup,
    AssignmentSource,
    AssignmentStatus,
    AuditLog,
    Briefing,
    BriefingConfirmation,
    CheckIn,
    CheckInMaterial,
    Event,
    EventStatus,
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
from app.services.mail_templates import ensure_mail_templates
from app.services.volunteers import deterministic_email_hash

DEFAULT_EVENT_NAME = "Demo-Veranstaltung 2026"
DEFAULT_EVENT_SLUG = "demo-2026"


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


def _assignment_status(index: int) -> AssignmentStatus:
    if index in {21, 22, 23}:
        return AssignmentStatus.waitlisted
    if index in {24, 25}:
        return AssignmentStatus.pending
    if index in {26, 27}:
        return AssignmentStatus.checked_in
    if index in {28, 29}:
        return AssignmentStatus.attended
    if index == 30:
        return AssignmentStatus.no_show
    if index == 31:
        return AssignmentStatus.cancelled
    if index == 32:
        return AssignmentStatus.rejected
    return AssignmentStatus.confirmed


def _volunteer_status(assignment_status: AssignmentStatus) -> VolunteerStatus:
    if assignment_status == AssignmentStatus.rejected:
        return VolunteerStatus.rejected
    if assignment_status == AssignmentStatus.cancelled:
        return VolunteerStatus.submitted
    if assignment_status == AssignmentStatus.pending:
        return VolunteerStatus.orga_review
    return VolunteerStatus.assigned


def ensure_demo_data(db: Session) -> Event:
    """Create a comprehensive and repeatable demonstration data set.

    Every contact uses the reserved ``example.invalid`` domain and every person
    is explicitly named as a demo record. The function may safely run after
    every non-persistent DEV revision start.
    """
    ensure_mail_templates(db)
    event = db.scalar(select(Event).where(Event.slug == DEFAULT_EVENT_SLUG))
    if event is not None and db.scalar(
        select(Team.id).where(Team.event_id == event.id).limit(1)
    ):
        db.commit()
        return event

    now = datetime.now(timezone.utc).replace(microsecond=0)
    event_start = (now + timedelta(days=14)).replace(
        hour=10, minute=0, second=0, microsecond=0
    )
    event_end = event_start + timedelta(hours=16)
    if event is None:
        event = Event(name=DEFAULT_EVENT_NAME, slug=DEFAULT_EVENT_SLUG)
        db.add(event)

    event.short_description = "Gemeinsam machen wir die Demo-Veranstaltung möglich."
    event.description = (
        "Vollständig fiktive Veranstaltung für den Volunteer Manager. Alle "
        "Personen, Kontakte und Vorgänge dienen ausschließlich der Erprobung "
        "des Prototyps."
    )
    event.starts_at = event_start
    event.ends_at = event_end
    event.start_time_is_set = True
    event.end_time_is_set = True
    event.venue = "Demo-Platz St. Pölten"
    event.address = "Demostraße 1, 3100 St. Pölten"
    event.public_meeting_point = "Demo-Infostand"
    event.registration_opens_at = now - timedelta(hours=1)
    event.registration_closes_at = event_start - timedelta(hours=2)
    event.registration_open_time_is_set = True
    event.registration_close_time_is_set = True
    event.status = EventStatus.registration_open
    event.is_public = True
    event.allows_minors = True
    event.contact_name = "Demo Veranstaltungskoordination"
    event.contact_email = "demo-kontakt@example.invalid"
    event.contact_phone = "+43 000 000 000"
    event.briefing = (
        "Check-in am Demo-Infostand. Bei Eskalationen zuerst Eigenschutz, "
        "danach Teamleitung oder Veranstaltungskoordination verständigen."
    )
    event.clothing_and_material = (
        "Bequeme, wetterfeste Kleidung und festes Schuhwerk. Benötigtes "
        "Team-Material wird beim Check-in ausgegeben."
    )
    event.catering_info = "Fiktive Getränke- und Jausenstation beim Demo-Infostand."
    event.accessibility_info = (
        "Demo-Angabe: stufenloser Check-in und ruhiger Rückzugsbereich vorhanden."
    )
    db.flush()

    role_specs = [
        ("Aufbau", "Aufbau Platz und Stände", 10, 2, -3, 4, True),
        ("Aufbau", "Aufbau Demowagen", 4, 1, -2, 4, False),
        ("Parade", "Besucher:innenzählung", 4, 2, 2, 3, True),
        ("Parade", "Ordner:in Parade", 40, 6, 2, 4, True),
        ("Platz", "Ordner:in Platz", 20, 4, 0, 5, True),
        ("Platz", "Solibandverteilung", 6, 3, 5, 4, True),
        ("Backstage", "Backstage- und Artist-Betreuung", 4, 2, 1, 7, False),
        ("Clubbing", "Clubbing-Aufbau", 3, 1, 8, 4, False),
        ("Abbau", "Abbau Demowagen", 4, 1, 12, 3, False),
        ("Abbau", "Abbau Platz und Stände", 8, 2, 13, 3, True),
        ("Abbau", "Abbau Bühne", 3, 1, 13, 3, False),
        ("Organisation", "Organisations-Team", 6, 2, 0, 10, False),
    ]
    team_details = {
        "Aufbau": ("Vorbereitung von Platz, Ständen und Demowagen.", "#6f42c1"),
        "Parade": ("Begleitung und Absicherung des Demo-Zugs.", "#d63384"),
        "Platz": ("Information, Verteilung und Platzorganisation.", "#0d6efd"),
        "Backstage": ("Betreuung von Acts und Backstage-Abläufen.", "#6610f2"),
        "Clubbing": ("Vorbereitung der fiktiven Abendveranstaltung.", "#fd7e14"),
        "Abbau": ("Geordneter Rückbau nach Veranstaltungsende.", "#198754"),
        "Organisation": ("Koordination, Kommunikation und Eskalationswege.", "#212529"),
    }
    teams: dict[str, Team] = {}
    for index, name in enumerate(dict.fromkeys(spec[0] for spec in role_specs)):
        description, color = team_details[name]
        team = Team(
            event=event,
            name=name,
            description=description,
            color=color,
            sort_order=index,
            lead_name=f"Demo-Teamleitung {name}",
            lead_contact=f"demo-{name.lower()}@example.invalid",
            meeting_point="Demo-Infostand",
            notes="Ausschließlich fiktiver Arbeitsbereich für die Demo.",
        )
        teams[name] = team
        db.add(team)
    db.flush()

    material_specs = {
        "Aufbau": [
            ("Warnwesten", 10, 12, "Stück", False),
            ("Arbeitshandschuhe", 20, 24, "Paar", True),
        ],
        "Parade": [
            ("Funkgeräte", 8, 10, "Stück", False),
            ("Lanyards", 44, 50, "Stück", False),
        ],
        "Platz": [
            ("Zutrittsbändchen", 30, 100, "Stück", True),
            ("Solibänder", 500, 750, "Stück", True),
        ],
        "Backstage": [("Funkgeräte", 4, 5, "Stück", False)],
        "Clubbing": [("Werkzeugkiste", 1, 1, "Kiste", False)],
        "Abbau": [("Arbeitshandschuhe", 16, 20, "Paar", True)],
        "Organisation": [
            ("Erste-Hilfe-Set", 2, 2, "Set", False),
            ("Funkgeräte", 5, 6, "Stück", False),
        ],
    }
    materials_by_team: dict[str, list[TeamMaterial]] = {}
    for team_name, materials in material_specs.items():
        materials_by_team[team_name] = []
        for material_name, required, available, unit, consumable in materials:
            material = TeamMaterial(
                team=teams[team_name],
                name=material_name,
                quantity_required=required,
                quantity_available=available,
                unit=unit,
                is_consumable=consumable,
                notes="Fiktiver Demo-Bestand; keine reale Inventarangabe.",
            )
            materials_by_team[team_name].append(material)
            db.add(material)

    shifts: list[Shift] = []
    roles: dict[str, Role] = {}
    for (
        team_name,
        role_name,
        capacity,
        waitlist,
        offset,
        duration,
        minors,
    ) in role_specs:
        role = Role(
            team=teams[team_name],
            name=role_name,
            short_description=f"Demo-Aufgabe: {role_name}",
            description=(
                "Fiktive Aufgabenbeschreibung mit klarer Übergabe an die "
                "zuständige Demo-Teamleitung."
            ),
            requirements="Zuverlässigkeit und Teilnahme am Briefing.",
            minimum_age=16 if minors else 18,
            training_required=team_name in {"Parade", "Backstage", "Organisation"},
            prefer_pair="Ordner:in" in role_name or role_name == "Solibandverteilung",
            physically_demanding=team_name in {"Aufbau", "Abbau"},
            sensitive_task=team_name in {"Backstage", "Organisation"},
            default_meeting_point="Demo-Infostand",
        )
        shift = Shift(
            event=event,
            role=role,
            title=role_name,
            description=f"Fiktive Demo-Schicht für {role_name}.",
            starts_at=event_start + timedelta(hours=offset),
            ends_at=event_start + timedelta(hours=offset + duration),
            location="Demo-Infostand",
            needed_count=capacity,
            waitlist_capacity=waitlist,
            lead_name=f"Demo-Teamleitung {team_name}",
            volunteer_notes=(
                "Bitte 15 Minuten vor Beginn am Demo-Infostand einfinden."
            ),
            admin_notes="Fiktive Notiz für die Administrationsdemo.",
            status=ShiftStatus.open,
            allows_minors=minors,
            requires_birth_date=True,
        )
        roles[role_name] = role
        shifts.append(shift)
        db.add_all([role, shift])
    db.flush()

    general_briefing = Briefing(
        event=event,
        title="Allgemeines Sicherheits- und Awareness-Briefing",
        content=(
            "Fiktives Demo-Briefing: Respekt, Eigenschutz, Awareness-Strukturen "
            "und Eskalationswege beachten."
        ),
        version="1.0-demo",
        visible_from=now - timedelta(days=1),
    )
    parade_briefing = Briefing(
        event=event,
        team_id=teams["Parade"].id,
        title="Parade und Ordner:innen",
        content="Fiktives Team-Briefing zu Route, Positionen und Funkdisziplin.",
        version="1.0-demo",
        visible_from=now,
    )
    backstage_briefing = Briefing(
        event=event,
        role_id=roles["Backstage- und Artist-Betreuung"].id,
        title="Backstage-Abläufe",
        content="Fiktives Rollen-Briefing zu Zutritt, Übergaben und Privatsphäre.",
        version="1.0-demo",
        visible_from=now,
    )
    db.add_all([general_briefing, parade_briefing, backstage_briefing])
    db.flush()

    demo_first_names = [
        "Alex",
        "Sam",
        "Robin",
        "Kim",
        "Toni",
        "Charlie",
        "Mika",
        "Noa",
        "Jules",
        "Lou",
        "Jamie",
        "Sascha",
    ]
    pronouns = ["keine Angabe", "sie/ihr", "er/ihm", "they/them"]
    volunteers: list[Volunteer] = []
    assignments: list[ShiftAssignment] = []
    minor_shifts = [shift for shift in shifts if shift.allows_minors]
    for index in range(36):
        assignment_status = _assignment_status(index)
        is_minor = index in {5, 17, 35}
        shift = (
            minor_shifts[index % len(minor_shifts)]
            if is_minor
            else shifts[index % len(shifts)]
        )
        email = f"demo.person.{index + 1:02d}@example.invalid"
        birth_date = (
            date(now.year - 17, (index % 12) + 1, min(index + 1, 28))
            if is_minor
            else date(1985 + (index % 15), (index % 12) + 1, min(index + 1, 28))
        )
        volunteer = Volunteer(
            event=event,
            first_name=f"Demo {demo_first_names[index % len(demo_first_names)]}",
            last_name=f"Beispiel {index + 1:02d}",
            pronouns=pronouns[index % len(pronouns)],
            email=email,
            email_normalized=email,
            email_hash=deterministic_email_hash(email),
            phone=f"+43 000 100 {index + 1:03d}",
            age_group=AgeGroup.age_16_17 if is_minor else AgeGroup.adult,
            birth_date=birth_date,
            birth_date_verified=index % 4 == 0,
            tshirt_size=["S", "M", "L", "XL"][index % 4],
            food_choice=["vegan", "vegetarisch", "keine Angabe"][index % 3],
            accessibility_needs=(
                "Fiktive Demo-Angabe: stufenloser Zugang gewünscht."
                if index in {7, 19}
                else None
            ),
            experience="Fiktive Vorerfahrung aus einer Demo-Veranstaltung.",
            notes="Ausschließlich fiktiver Demo-Datensatz.",
            contact_consent=True,
            future_contact_consent=index % 3 == 0,
            consented_at=now - timedelta(days=index % 5),
            privacy_version="demo-1",
            email_verified_at=(now - timedelta(days=1) if index % 3 != 0 else None),
            status=_volunteer_status(assignment_status),
        )
        assignment = ShiftAssignment(
            volunteer=volunteer,
            shift=shift,
            assignment_status=assignment_status,
            source=(
                AssignmentSource.public if index % 2 == 0 else AssignmentSource.admin
            ),
            registered_at=now - timedelta(days=(index % 7) + 1),
            confirmed_at=(
                now - timedelta(days=1)
                if assignment_status
                in {
                    AssignmentStatus.confirmed,
                    AssignmentStatus.checked_in,
                    AssignmentStatus.attended,
                    AssignmentStatus.no_show,
                }
                else None
            ),
            cancelled_at=(
                now - timedelta(hours=4)
                if assignment_status == AssignmentStatus.cancelled
                else None
            ),
            internal_note=(
                "Fiktiver Ablehnungs- oder Stornohinweis."
                if assignment_status
                in {AssignmentStatus.cancelled, AssignmentStatus.rejected}
                else None
            ),
        )
        volunteers.append(volunteer)
        assignments.append(assignment)
        db.add_all([volunteer, assignment])
    db.flush()

    for index in {26, 27, 28, 29}:
        assignment = assignments[index]
        checked_in_at = now - timedelta(hours=2, minutes=index)
        checked_out_at = (
            now - timedelta(minutes=20)
            if assignment.assignment_status == AssignmentStatus.attended
            else None
        )
        assignment.checked_in_at = checked_in_at
        checkin = CheckIn(
            assignment=assignment,
            checked_in_at=checked_in_at,
            checked_out_at=checked_out_at,
            lanyard_issued=True,
            wristband_issued=index % 2 == 0,
            radio_issued=index % 2 == 1,
            materials_returned_at=checked_out_at,
            note="Fiktiver Check-in für die Funktionsdemo.",
        )
        team_name = assignment.shift.role.team.name
        material = materials_by_team[team_name][0]
        db.add(
            CheckInMaterial(
                checkin=checkin,
                material=material,
                quantity_issued=1,
                return_required=not material.is_consumable,
                returned_at=(
                    checked_out_at
                    if checked_out_at and not material.is_consumable
                    else None
                ),
            )
        )

    db.add_all(
        BriefingConfirmation(
            briefing=general_briefing,
            volunteer_id=volunteer.id,
            confirmed_at=now - timedelta(hours=index + 1),
        )
        for index, volunteer in enumerate(volunteers[:14])
    )
    parade_volunteers = [
        assignment.volunteer
        for assignment in assignments
        if assignment.shift.role.team.name == "Parade"
    ][:5]
    db.add_all(
        BriefingConfirmation(
            briefing=parade_briefing,
            volunteer_id=volunteer.id,
            confirmed_at=now - timedelta(hours=index + 2),
        )
        for index, volunteer in enumerate(parade_volunteers)
    )

    outbox_specs = [
        (0, "email_verification", "Demo: E-Mail bestätigen", "automatic", None, None),
        (1, "registration_updated", "Demo: Anmeldung geändert", "manual", None, None),
        (
            2,
            "waitlist_promoted",
            "Demo: Von Warteliste nachgerückt",
            "automatic",
            now - timedelta(hours=3),
            None,
        ),
        (
            3,
            "registration_cancellation",
            "Demo: Schicht storniert",
            "manual",
            None,
            None,
        ),
        (
            4,
            "registration_rejected",
            "Demo: Rückmeldung zur Anmeldung",
            "automatic",
            None,
            "Fiktiver SMTP-Fehler für die Wiederholungsdemo",
        ),
        (
            5,
            "email_verification",
            "Demo: E-Mail bestätigen",
            "automatic",
            now - timedelta(days=1),
            None,
        ),
    ]
    for volunteer_index, kind, subject, mode, sent_at, error in outbox_specs:
        volunteer = volunteers[volunteer_index]
        db.add(
            OutboxMessage(
                volunteer_id=volunteer.id,
                kind=kind,
                subject=subject,
                body=(
                    "Dies ist eine ausschließlich fiktive Demo-Nachricht. "
                    "Es wird keine reale Person kontaktiert."
                ),
                recipient_email=volunteer.email,
                delivery_mode=mode,
                sent_at=sent_at,
                last_error=error,
                created_at=now - timedelta(hours=volunteer_index + 1),
            )
        )

    db.add(
        SMTPConfiguration(
            host="smtp.example.invalid",
            port=587,
            username="demo-smtp-user",
            from_email="demo-volunteer@example.invalid",
            from_name="Volunteer Manager Demo",
            use_starttls=True,
            use_ssl=False,
            enabled=False,
        )
    )
    for action, entity_type, entity_id, details in [
        ("demo.seeded", "event", str(event.id), {"fictional": True}),
        ("volunteer.bulk_status", "volunteer", str(volunteers[0].id), {"demo": True}),
        (
            "checkin.created",
            "shift_assignment",
            str(assignments[26].id),
            {"demo": True},
        ),
        ("briefing.confirmed", "briefing", str(general_briefing.id), {"demo": True}),
    ]:
        db.add(
            AuditLog(
                actor_user_id="demo-seed",
                actor_email="demo-admin@example.invalid",
                actor_name="Demo Administration",
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                metadata_json=json.dumps(details),
                created_at=now,
            )
        )

    db.commit()
    db.refresh(event)
    return event
