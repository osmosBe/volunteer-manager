from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.database.session import create_database_engine, get_db
from app.main import app
from app.models import Event, EventStatus, Shift, TeamMaterial, Volunteer


def test_admin_can_create_plan_edit_and_duplicate_event(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'admin-routes.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    def override_get_db():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    start = datetime.now(timezone.utc) + timedelta(days=10)
    try:
        client = TestClient(app)
        response = client.post(
            "/admin/veranstaltungen/neu",
            data={
                "name": "Planungstest",
                "slug": "planungstest",
                "starts_at": start.isoformat(),
                "ends_at": (start + timedelta(hours=8)).isoformat(),
                "status": "registration_open",
                "is_public": "true",
                "short_description": "Kurz und klar",
                "description": "Ausführliche öffentliche Beschreibung",
                "address": "Rathausplatz 1",
                "public_meeting_point": "Infostand",
                "contact_name": "Demo Kontakt",
                "contact_email": "kontakt@example.org",
                "briefing": "Sicherheitsbriefing",
                "accessibility_info": "Stufenlos erreichbar",
                "allows_minors": "true",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        event_url = response.headers["location"]
        event_id = int(event_url.rsplit("/", 1)[1])
        assert client.get(event_url).status_code == 200
        assert client.get("/admin/briefings").status_code == 200
        volunteer_response = client.post(
            "/admin/ehrenamtliche/neu",
            data={
                "event_id": event_id,
                "first_name": "Demo",
                "last_name": "Admin",
                "email": "admin-created@example.invalid",
                "age_group": "adult",
            },
            follow_redirects=False,
        )
        assert volunteer_response.status_code == 303
        assert volunteer_response.headers["location"].startswith(
            "/admin/ehrenamtliche/"
        )

        assert (
            client.post(
                f"/admin/veranstaltungen/{event_id}/bereiche",
                data={"name": "Awareness"},
                follow_redirects=False,
            ).status_code
            == 303
        )
        with Session() as db:
            event = db.get(Event, event_id)
            team_id = event.teams[0].id
        material_response = client.post(
            f"/admin/bereiche/{team_id}/materialien",
            data={
                "name": "Funkgerät",
                "quantity_required": 4,
                "quantity_available": 3,
                "unit": "Stück",
                "notes": "Ausgabe beim Infostand",
            },
            follow_redirects=False,
        )
        assert material_response.status_code == 303
        with Session() as db:
            material = db.query(TeamMaterial).one()
            material_id = material.id
            assert material.quantity_required == 4
            assert material.quantity_available == 3
        assert (
            client.post(
                f"/admin/materialien/{material_id}",
                data={
                    "quantity_required": 4,
                    "quantity_available": 4,
                    "notes": "vollständig",
                    "is_active": "true",
                },
                follow_redirects=False,
            ).status_code
            == 303
        )
        assert (
            client.post(
                f"/admin/bereiche/{team_id}/aufgaben",
                data={"name": "Infopoint"},
                follow_redirects=False,
            ).status_code
            == 303
        )
        with Session() as db:
            role_id = db.get(Event, event_id).teams[0].roles[0].id
        assert (
            client.post(
                f"/admin/bereiche/{team_id}",
                data={
                    "name": "Awareness & Info",
                    "description": "Hilft vor Ort",
                    "meeting_point": "Infostand",
                },
                follow_redirects=False,
            ).status_code
            == 303
        )
        assert (
            client.post(
                f"/admin/aufgaben/{role_id}",
                data={
                    "name": "Infopoint Betreuung",
                    "description": "Information",
                    "requirements": "Briefing",
                },
                follow_redirects=False,
            ).status_code
            == 303
        )
        shift_response = client.post(
            f"/admin/veranstaltungen/{event_id}/schichten",
            data={
                "title": "Info Früh",
                "role_id": role_id,
                "starts_at": start.isoformat(),
                "ends_at": (start + timedelta(hours=2)).isoformat(),
                "needed_count": 3,
                "waitlist_capacity": 1,
            },
            follow_redirects=False,
        )
        assert shift_response.status_code == 303
        with Session() as db:
            shift_id = db.query(Shift).one().id
        assert (
            client.post(
                f"/admin/schichten/{shift_id}/bearbeiten",
                data={
                    "title": "Info Früh aktualisiert",
                    "role_id": role_id,
                    "starts_at": start.isoformat(),
                    "ends_at": (start + timedelta(hours=3)).isoformat(),
                    "needed_count": 4,
                    "waitlist_capacity": 2,
                    "status": "open",
                    "location": "Hauptinfo",
                },
                follow_redirects=False,
            ).status_code
            == 303
        )
        assert (
            client.get(
                f"/admin/veranstaltungen/{event_id}/druck/schichtplan"
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"/admin/veranstaltungen/{event_id}/duplizieren",
                follow_redirects=False,
            ).status_code
            == 303
        )
        with Session() as db:
            assert db.query(Event).count() == 2
            assert db.query(Shift).count() == 2
            assert db.query(TeamMaterial).one().quantity_available == 4
            assert db.query(Volunteer).count() == 1
            assert db.get(Event, event_id).status == EventStatus.registration_open
            assert db.get(Event, event_id).description == (
                "Ausführliche öffentliche Beschreibung"
            )
            assert db.get(Event, event_id).contact_email == "kontakt@example.org"
            assert db.get(Event, event_id).allows_minors is True
            assert db.get(Shift, shift_id).title == "Info Früh aktualisiert"
            assert db.get(Shift, shift_id).needed_count == 4
    finally:
        app.dependency_overrides.clear()
