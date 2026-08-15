from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.database.session import create_database_engine, get_db
from app.main import app
from app.models import (
    AgeGroup,
    AssignmentStatus,
    Briefing,
    BriefingConfirmation,
    Event,
    EventStatus,
    Shift,
    ShiftAssignment,
    ShiftStatus,
    Volunteer,
    VolunteerStatus,
)
from app.services.volunteers import deterministic_email_hash


def test_bulk_status_and_briefing_confirmation(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'bulk.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    start = datetime.now(timezone.utc) + timedelta(days=1)
    with Session() as db:
        event = Event(name="Bulk", slug="bulk", status=EventStatus.draft)
        shift = Shift(
            event=event,
            title="Info",
            starts_at=start,
            ends_at=start + timedelta(hours=2),
            status=ShiftStatus.open,
        )
        briefing = Briefing(event=event, title="Sicherheit", content="Inhalt")
        volunteers = []
        for number in range(2):
            email = f"bulk-{number}@example.invalid"
            volunteer = Volunteer(
                event=event,
                first_name="Bulk",
                last_name=str(number),
                email=email,
                email_normalized=email,
                email_hash=deterministic_email_hash(email),
                age_group=AgeGroup.adult,
            )
            volunteers.append(volunteer)
            db.add(
                ShiftAssignment(
                    volunteer=volunteer,
                    shift=shift,
                    assignment_status=AssignmentStatus.pending,
                )
            )
        db.add_all([event, shift, briefing, *volunteers])
        db.commit()
        ids = [volunteer.id for volunteer in volunteers]
        briefing_id = briefing.id

    def override_get_db():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post(
            "/admin/ehrenamtliche/bulk",
            data={
                "volunteer_ids": ids,
                "bulk_choice": "volunteer_status|assigned",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        response = client.post(
            "/admin/ehrenamtliche/bulk",
            data={
                "volunteer_ids": ids,
                "bulk_choice": f"briefing_confirm|{briefing_id}",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        with Session() as db:
            assert all(
                volunteer.status == VolunteerStatus.assigned
                for volunteer in db.query(Volunteer).all()
            )
            assert db.query(BriefingConfirmation).count() == 2
        detail = client.get(f"/admin/ehrenamtliche/{ids[0]}")
        assert "Anmeldung ablehnen" in detail.text
        rejected = client.post(
            f"/admin/ehrenamtliche/{ids[0]}/ablehnen",
            data={
                "reason": "Leider keine passende Aufgabe verfügbar.",
                "confirm": "ABLEHNEN",
            },
            follow_redirects=False,
        )
        assert rejected.status_code == 303
        with Session() as db:
            volunteer = db.get(Volunteer, ids[0])
            assert volunteer.status == VolunteerStatus.rejected
            assert (
                volunteer.assignments[0].assignment_status == AssignmentStatus.rejected
            )
    finally:
        app.dependency_overrides.clear()
