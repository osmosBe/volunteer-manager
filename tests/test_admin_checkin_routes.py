from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.database.session import create_database_engine, get_db
from app.main import app
from app.models import (
    AgeGroup,
    AssignmentStatus,
    CheckInMaterial,
    Event,
    EventStatus,
    Role,
    Shift,
    ShiftAssignment,
    ShiftStatus,
    Team,
    TeamMaterial,
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
        team = Team(event=event, name="Infobereich")
        role = Role(team=team, name="Infopoint")
        radio_material = TeamMaterial(
            team=team,
            name="Team-Funkgerät",
            quantity_required=2,
            quantity_available=2,
            unit="Stück",
        )
        flyers = TeamMaterial(
            team=team,
            name="Flyerpaket",
            quantity_required=10,
            quantity_available=10,
            unit="Paket",
            is_consumable=True,
        )
        shift = Shift(
            event=event,
            role=role,
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
        db.add_all(
            [event, team, role, radio_material, flyers, shift, volunteer, assignment]
        )
        db.commit()
        assignment_id = assignment.id
        radio_material_id = radio_material.id
        flyers_id = flyers.id

    def override_get_db():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        page = client.get("/admin/check-in")
        assert page.status_code == 200
        assert "Demo Checkin" in page.text
        scan_page = client.get(f"/admin/check-in/scan/{assignment_id}")
        assert scan_page.status_code == 200
        assert "QR-Check-in" in scan_page.text
        assert "Team-Funkgerät" in scan_page.text
        assert "Flyerpaket" in scan_page.text
        qr_response = client.get(f"/admin/zuteilungen/{assignment_id}/qr.svg")
        assert qr_response.status_code == 200
        assert qr_response.headers["content-type"].startswith("image/svg+xml")
        assert b"checkin@example.invalid" not in qr_response.content
        response = client.post(
            f"/admin/check-in/{assignment_id}",
            data={
                "lanyard": "true",
                "radio": "true",
                "material_ids": [str(radio_material_id), str(flyers_id)],
            },
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
            issues = db.query(CheckInMaterial).order_by(CheckInMaterial.id).all()
            assert len(issues) == 2
            radio_issue = next(
                issue for issue in issues if issue.material_id == radio_material_id
            )
            flyer_issue = next(
                issue for issue in issues if issue.material_id == flyers_id
            )
            assert radio_issue.returned_at is not None
            assert flyer_issue.return_required is False
            assert flyer_issue.returned_at is None
    finally:
        app.dependency_overrides.clear()
