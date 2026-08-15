from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.database.session import create_database_engine
from app.models import (
    AssignmentStatus,
    Event,
    EventStatus,
    OutboxMessage,
    Shift,
    ShiftAssignment,
    ShiftStatus,
)
from app.services.registrations import (
    RegistrationData,
    RegistrationError,
    cancel_assignment,
    create_registration,
    get_volunteer_by_edit_token,
)


@pytest.fixture()
def db(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'registrations.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as session:
        yield session


def make_event_and_shifts(db, *, needed_count=2, waitlist_capacity=1):
    start = datetime.now(timezone.utc) + timedelta(days=3)
    event = Event(
        name="ST. PRIDE Test",
        slug="stpride-test",
        is_public=True,
        status=EventStatus.registration_open,
        registration_opens_at=start - timedelta(days=10),
        registration_closes_at=start + timedelta(days=1),
    )
    first = Shift(
        event=event,
        title="Info",
        starts_at=start,
        ends_at=start + timedelta(hours=2),
        needed_count=needed_count,
        waitlist_capacity=waitlist_capacity,
        status=ShiftStatus.open,
    )
    second = Shift(
        event=event,
        title="Abbau",
        starts_at=start + timedelta(hours=3),
        ends_at=start + timedelta(hours=5),
        needed_count=needed_count,
        waitlist_capacity=waitlist_capacity,
        status=ShiftStatus.open,
    )
    db.add_all([event, first, second])
    db.commit()
    return event, first, second


def registration_data(email="person@example.org"):
    return RegistrationData(
        first_name="Alex",
        last_name="Test",
        email=email,
        contact_consent=True,
    )


def test_registration_confirms_available_shifts_and_stores_only_token_hash(db):
    event, first, second = make_event_and_shifts(db)

    result = create_registration(db, event, registration_data(), [first.id, second.id])

    assert result.edit_token
    assert result.edit_token != result.volunteer.edit_token_hash
    assert [item.assignment_status for item in result.assignments] == [
        AssignmentStatus.confirmed,
        AssignmentStatus.confirmed,
    ]
    assert db.scalar(
        select(OutboxMessage).where(OutboxMessage.volunteer_id == result.volunteer.id)
    )
    assert get_volunteer_by_edit_token(db, result.edit_token).id == result.volunteer.id
    assert get_volunteer_by_edit_token(db, "not-a-real-token") is None


def test_registration_uses_waitlist_when_shift_is_full(db):
    event, first, _ = make_event_and_shifts(db, needed_count=1, waitlist_capacity=1)
    create_registration(db, event, registration_data("one@example.org"), [first.id])

    waitlisted = create_registration(
        db, event, registration_data("two@example.org"), [first.id]
    )

    assert waitlisted.assignments[0].assignment_status == AssignmentStatus.waitlisted
    with pytest.raises(RegistrationError, match="Warteliste"):
        create_registration(
            db, event, registration_data("three@example.org"), [first.id]
        )


def test_registration_rejects_overlapping_shifts(db):
    event, first, second = make_event_and_shifts(db)
    second.starts_at = first.starts_at + timedelta(hours=1)
    second.ends_at = first.ends_at + timedelta(hours=1)
    db.commit()

    with pytest.raises(RegistrationError, match="überschneiden"):
        create_registration(db, event, registration_data(), [first.id, second.id])


def test_cancelling_assignment_keeps_history_and_frees_capacity(db):
    event, first, _ = make_event_and_shifts(db, needed_count=1)
    result = create_registration(db, event, registration_data(), [first.id])

    cancel_assignment(db, result.volunteer, result.assignments[0].id)

    assignment = db.get(ShiftAssignment, result.assignments[0].id)
    assert assignment.assignment_status == AssignmentStatus.cancelled
    assert assignment.cancelled_at is not None
    replacement = create_registration(
        db, event, registration_data("new@example.org"), [first.id]
    )
    assert replacement.assignments[0].assignment_status == AssignmentStatus.confirmed
