from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.database.session import create_database_engine, get_db
from app.main import app
from app.models import Event, EventStatus, Shift


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
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        event_url = response.headers["location"]
        event_id = int(event_url.rsplit("/", 1)[1])
        assert client.get(event_url).status_code == 200

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
            assert db.get(Event, event_id).status == EventStatus.registration_open
    finally:
        app.dependency_overrides.clear()
