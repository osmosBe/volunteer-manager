from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.database.session import create_database_engine, get_db
from app.main import app
from app.models import (
    AgeGroup,
    AssignmentStatus,
    CheckIn,
    Event,
    Shift,
    ShiftAssignment,
    ShiftStatus,
    Volunteer,
)
from app.services.checkins import CheckInError, check_out_assignment
from app.services.volunteers import deterministic_email_hash, normalize_email


@pytest.mark.parametrize(
    ("event_setting", "shift_override", "expected"),
    [
        (False, None, False),
        (True, None, True),
        (False, True, True),
        (True, False, False),
    ],
)
def test_shift_goodiebag_setting_uses_override_before_event(
    event_setting, shift_override, expected
):
    event = Event(name="Test", slug="test", goodiebag_offered=event_setting)
    shift = Shift(
        event=event,
        title="Schicht",
        starts_at=datetime.now(timezone.utc),
        ends_at=datetime.now(timezone.utc) + timedelta(hours=1),
        status=ShiftStatus.open,
        goodiebag_override=shift_override,
    )

    assert shift.goodiebag_is_offered is expected


def _checked_in_assignment(
    db, *, offered: bool, suffix: str = "default", override: bool | None = None
) -> ShiftAssignment:
    event = Event(name="Test", slug=f"test-{suffix}", goodiebag_offered=offered)
    start = datetime.now(timezone.utc)
    shift = Shift(
        event=event,
        title="Schicht",
        starts_at=start,
        ends_at=start + timedelta(hours=1),
        status=ShiftStatus.open,
        goodiebag_override=override,
    )
    email = f"goodiebag-{suffix}@example.invalid"
    volunteer = Volunteer(
        event=event,
        first_name="Demo",
        last_name="Person",
        email=email,
        email_normalized=normalize_email(email),
        email_hash=deterministic_email_hash(email),
        age_group=AgeGroup.adult,
    )
    assignment = ShiftAssignment(
        volunteer=volunteer,
        shift=shift,
        assignment_status=AssignmentStatus.checked_in,
        checked_in_at=start,
    )
    assignment.checkin = CheckIn(assignment=assignment, checked_in_at=start)
    db.add(assignment)
    db.commit()
    return assignment


def test_checkout_records_received_goodiebag(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'goodiebag.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    with Session() as db:
        assignment = _checked_in_assignment(db, offered=True, suffix="received")
        checkin = check_out_assignment(db, assignment, goodiebag_received=True)

        assert checkin.goodiebag_received is True
        assert checkin.checked_out_at is not None
        assert assignment.assignment_status == AssignmentStatus.attended


def test_checkout_rejects_goodiebag_when_not_offered_without_mutation(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'no-goodiebag.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    with Session() as db:
        assignment = _checked_in_assignment(db, offered=False, suffix="rejected")

        with pytest.raises(CheckInError, match="kein Goodiebag"):
            check_out_assignment(db, assignment, goodiebag_received=True)

        assert assignment.checkin.goodiebag_received is False
        assert assignment.checkin.checked_out_at is None
        assert assignment.assignment_status == AssignmentStatus.checked_in


def test_checkout_checkbox_uses_effective_shift_setting(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'visibility.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        hidden = _checked_in_assignment(
            db, offered=True, override=False, suffix="hidden"
        )
        visible = _checked_in_assignment(
            db, offered=False, override=True, suffix="visible"
        )
        hidden_id = hidden.id
        visible_id = visible.id

    def override_get_db():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = TestClient(app).get("/admin/check-in")
        assert response.status_code == 200
        assert f"goodiebag_received_{visible_id}" in response.text
        assert f"goodiebag_received_{hidden_id}" not in response.text
        tampered = TestClient(app).post(
            f"/admin/check-in/{hidden_id}/check-out",
            data={"goodiebag_received": "true"},
        )
        assert tampered.status_code == 422
        with Session() as db:
            hidden_assignment = db.get(ShiftAssignment, hidden_id)
            assert hidden_assignment.assignment_status == AssignmentStatus.checked_in
            assert hidden_assignment.checkin.checked_out_at is None
    finally:
        app.dependency_overrides.clear()
