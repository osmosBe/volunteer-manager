from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.database.session import create_database_engine
from app.models import (
    AgeGroup,
    AssignmentStatus,
    AuditLog,
    Event,
    EventStatus,
    OutboxMessage,
    Shift,
    ShiftAssignment,
    ShiftStatus,
    Volunteer,
    VolunteerStatus,
)
from app.services.admin import (
    AdminWorkflowError,
    anonymize_volunteer,
    assign_volunteer,
    promote_first_waitlisted,
    reject_volunteer,
)
from app.services.volunteers import deterministic_email_hash, normalize_email


@pytest.fixture()
def db(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'admin-workflows.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as session:
        yield session


def build_shift(db, capacity=1):
    start = datetime.now(timezone.utc) + timedelta(days=1)
    event = Event(name="Test", slug="admin-test", status=EventStatus.registration_open)
    shift = Shift(
        event=event,
        title="Info",
        starts_at=start,
        ends_at=start + timedelta(hours=2),
        needed_count=capacity,
        status=ShiftStatus.open,
    )
    db.add_all([event, shift])
    db.commit()
    return event, shift


def volunteer_for(db, event, number):
    email = f"person{number}@example.invalid"
    volunteer = Volunteer(
        event=event,
        first_name="Demo",
        last_name=str(number),
        email=email,
        email_normalized=normalize_email(email),
        email_hash=deterministic_email_hash(email),
        age_group=AgeGroup.adult,
    )
    db.add(volunteer)
    db.flush()
    return volunteer


def test_promote_first_waitlisted_preserves_order_and_audits(db):
    event, shift = build_shift(db)
    first = volunteer_for(db, event, 1)
    second = volunteer_for(db, event, 2)
    assignment = ShiftAssignment(
        volunteer=first,
        shift=shift,
        assignment_status=AssignmentStatus.waitlisted,
        registered_at=datetime.now(timezone.utc),
    )
    later = ShiftAssignment(
        volunteer=second,
        shift=shift,
        assignment_status=AssignmentStatus.waitlisted,
        registered_at=datetime.now(timezone.utc) + timedelta(minutes=1),
    )
    db.add_all([assignment, later])
    db.commit()

    promoted = promote_first_waitlisted(db, shift)

    assert promoted.id == assignment.id
    assert promoted.assignment_status == AssignmentStatus.confirmed
    assert later.assignment_status == AssignmentStatus.waitlisted
    assert db.scalar(select(AuditLog).where(AuditLog.entity_id == str(assignment.id)))
    assert db.scalar(
        select(OutboxMessage).where(OutboxMessage.kind == "waitlist_promoted")
    )


def test_rejection_cancels_active_work_and_queues_explanation(db):
    event, shift = build_shift(db)
    volunteer = volunteer_for(db, event, 1)
    assignment = ShiftAssignment(
        volunteer=volunteer,
        shift=shift,
        assignment_status=AssignmentStatus.confirmed,
    )
    db.add(assignment)
    db.commit()

    message = reject_volunteer(db, volunteer, "Leider keine passende Aufgabe frei.")

    assert volunteer.status == VolunteerStatus.rejected
    assert volunteer.edit_token_revoked_at is not None
    assert assignment.assignment_status == AssignmentStatus.rejected
    assert message is not None
    assert message.delivery_mode == "automatic"
    assert "keine passende Aufgabe" in message.body
    assert db.scalar(select(AuditLog).where(AuditLog.action == "volunteer.rejected"))


def test_waitlist_cannot_be_promoted_over_capacity(db):
    event, shift = build_shift(db)
    confirmed = volunteer_for(db, event, 1)
    waiting = volunteer_for(db, event, 2)
    db.add_all(
        [
            ShiftAssignment(
                volunteer=confirmed,
                shift=shift,
                assignment_status=AssignmentStatus.confirmed,
            ),
            ShiftAssignment(
                volunteer=waiting,
                shift=shift,
                assignment_status=AssignmentStatus.waitlisted,
            ),
        ]
    )
    db.commit()

    with pytest.raises(AdminWorkflowError, match="keinen freien Platz"):
        promote_first_waitlisted(db, shift)


def test_anonymization_removes_personal_data_but_keeps_assignments(db):
    event, shift = build_shift(db)
    volunteer = volunteer_for(db, event, 1)
    volunteer.phone = "+43 123"
    volunteer.dietary_needs = "sensibel"
    assignment = ShiftAssignment(
        volunteer=volunteer,
        shift=shift,
        assignment_status=AssignmentStatus.confirmed,
    )
    db.add(assignment)
    db.commit()

    anonymize_volunteer(db, volunteer)

    assert volunteer.first_name == "Anonymisiert"
    assert volunteer.phone is None
    assert volunteer.dietary_needs is None
    assert volunteer.edit_token_hash is None
    assert volunteer.anonymized_at is not None
    assert db.get(ShiftAssignment, assignment.id) is not None
    assert db.scalar(select(AuditLog).where(AuditLog.action == "volunteer.anonymized"))


def test_admin_assignment_requires_explicit_override_for_time_conflict(db):
    event, first_shift = build_shift(db, capacity=2)
    volunteer = volunteer_for(db, event, 1)
    db.add(
        ShiftAssignment(
            volunteer=volunteer,
            shift=first_shift,
            assignment_status=AssignmentStatus.confirmed,
        )
    )
    overlapping = Shift(
        event=event,
        title="Parallel",
        starts_at=first_shift.starts_at + timedelta(minutes=30),
        ends_at=first_shift.ends_at + timedelta(minutes=30),
        needed_count=2,
        status=ShiftStatus.open,
    )
    db.add(overlapping)
    db.commit()

    with pytest.raises(AdminWorkflowError, match="zweite Bestätigung"):
        assign_volunteer(db, volunteer, overlapping)

    assignment = assign_volunteer(db, volunteer, overlapping, override_conflict=True)
    assert assignment.assignment_status == AssignmentStatus.confirmed


def test_admin_cannot_overbook_but_can_waitlist(db):
    event, shift = build_shift(db)
    first = volunteer_for(db, event, 1)
    second = volunteer_for(db, event, 2)
    assign_volunteer(db, first, shift)

    with pytest.raises(AdminWorkflowError, match="voll"):
        assign_volunteer(db, second, shift)

    assignment = assign_volunteer(db, second, shift, status=AssignmentStatus.waitlisted)
    assert assignment.assignment_status == AssignmentStatus.waitlisted
