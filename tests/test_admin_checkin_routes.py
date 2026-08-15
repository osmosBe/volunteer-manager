from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.database.session import create_database_engine, get_db
from app.main import app
from app.models import (
    AgeGroup,
    AssignmentStatus,
    Event,
    EventStatus,
    Shift,
    ShiftAssignment,
    ShiftStatus,
    Volunteer,
)
from app.services.volunteers import deterministic_email_hash, normalize_email


def test_admin_checkin_and_material_return_smoke_flow(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'checkin-route.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    start = datetime.now(timezone.utc) + timedelta(hours=1)
    with Session() as db:
        event = Event(name="Check-in Test", slug="checkin", status=EventStatus.ongoing)
        shift = Shift(
            event=event,
            title="Infostand",
            starts_at=start,
            ends_at=start + timedelta(hours=2),
            needed_count=2,
            status=ShiftStatus.open,
        )
        email = "checkin@example.invalid"
        volunteer = Volunteer(
            event=event,
            first_name="Demo",
            last_name="Checkin",
            email=email,
            email_normalized=normalize_email(email),
            email_hash=deterministic_email_hash(email),
            age_group=AgeGroup.adult,
        )
        assignment = ShiftAssignment(
            volunteer=volunteer,
            shift=shift,
            assignment_status=AssignmentStatus.confirmed,
        )
        db.add_all([event, shift, volunteer, assignment])
        db.commit()
        assignment_id = assignment.id

    def override_get_db():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        page = client.get("/admin/check-in")
        assert page.status_code == 200
        assert "Demo Checkin" in page.text
        response = client.post(
            f"/admin/check-in/{assignment_id}",
            data={"lanyard": "true", "radio": "true"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        response = client.post(
            f"/admin/check-in/{assignment_id}/check-out", follow_redirects=False
        )
        assert response.status_code == 303
        with Session() as db:
            checked = db.get(ShiftAssignment, assignment_id)
            assert checked.assignment_status == AssignmentStatus.attended
            assert checked.checkin.checked_in_at is not None
            assert checked.checkin.lanyard_issued is True
            assert checked.checkin.radio_issued is True
            assert checked.checkin.materials_returned_at is not None
    finally:
        app.dependency_overrides.clear()
