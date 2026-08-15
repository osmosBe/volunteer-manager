from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.database.session import create_database_engine, get_db
from app.main import app
from app.models import Event, EventStatus, Role, Shift, ShiftStatus, Team


def test_public_registration_and_cancellation_flow(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'public-flow.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    start = datetime.now(timezone.utc) + timedelta(days=1)
    with Session() as db:
        event = Event(
            name="ST. PRIDE Test",
            slug="stpride-test",
            is_public=True,
            status=EventStatus.registration_open,
        )
        shift = Shift(
            event=event,
            title="Infopoint",
            starts_at=start,
            ends_at=start + timedelta(hours=2),
            needed_count=2,
            status=ShiftStatus.open,
        )
        db.add_all([event, shift])
        db.commit()
        shift_id = shift.id

    def override_get_db():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        assert client.get("/").status_code == 200
        assert client.get("/veranstaltungen/stpride-test/anmeldung").status_code == 200
        assert (
            "Zusammenfassung vor dem Absenden"
            in client.get("/veranstaltungen/stpride-test/anmeldung").text
        )
        response = client.post(
            "/veranstaltungen/stpride-test/anmeldung",
            data={
                "first_name": "Alex",
                "last_name": "Muster",
                "email": "alex@example.org",
                "contact_consent": "true",
                "shift_ids": str(shift_id),
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        confirmation_url = response.headers["location"]
        confirmation = client.get(confirmation_url)
        assert confirmation.status_code == 200
        assert "Infopoint" in confirmation.text
        assert "/zuteilungen/1/qr.svg" in confirmation.text
        qr_response = client.get(
            f"/anmeldung/{confirmation_url.split('/')[2]}/zuteilungen/1/qr.svg"
        )
        assert qr_response.status_code == 200
        assert qr_response.headers["content-type"].startswith("image/svg+xml")
        assert b"alex@example.org" not in qr_response.content
        edit = client.get(f"/anmeldung/{confirmation_url.split('/')[2]}/bearbeiten")
        assert edit.status_code == 200
        assert "Alex" in edit.text
        assignment_id = 1
        token = confirmation_url.split("/")[2]
        cancelled = client.post(
            f"/anmeldung/{token}/zuteilungen/{assignment_id}/stornieren",
            follow_redirects=False,
        )
        assert cancelled.status_code == 303
        assert "Storniert" in client.get(confirmation_url).text
        assert client.get(f"/anmeldung/{token}/zuteilungen/1/qr.svg").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_public_event_content_and_shift_filters(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'filters.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    start = datetime.now(timezone.utc) + timedelta(days=2)
    with Session() as db:
        event = Event(
            name="Filterbare PRIDE",
            slug="filterbar",
            short_description="Gemeinsam helfen",
            description="Ausführliche Beschreibung",
            briefing="Briefing um 14 Uhr",
            accessibility_info="Stufenlos",
            is_public=True,
            status=EventStatus.registration_open,
        )
        info_team = Team(event=event, name="Info")
        parade_team = Team(event=event, name="Parade")
        info_role = Role(team=info_team, name="Infostand")
        parade_role = Role(team=parade_team, name="Ordner:in")
        db.add_all(
            [
                event,
                info_team,
                parade_team,
                Shift(
                    event=event,
                    role=info_role,
                    title="Info Früh",
                    starts_at=start,
                    ends_at=start + timedelta(hours=2),
                    needed_count=2,
                    status=ShiftStatus.open,
                ),
                Shift(
                    event=event,
                    role=parade_role,
                    title="Parade Spät",
                    starts_at=start + timedelta(hours=4),
                    ends_at=start + timedelta(hours=6),
                    needed_count=2,
                    status=ShiftStatus.open,
                ),
            ]
        )
        db.commit()
        info_team_id = info_team.id

    def override_get_db():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        page = client.get(f"/veranstaltungen/filterbar?team_id={info_team_id}")
        assert page.status_code == 200
        assert "Ausführliche Beschreibung" in page.text
        assert "Briefing um 14 Uhr" in page.text
        assert "Info Früh" in page.text
        assert "Parade Spät" not in page.text
        day = start.date().isoformat()
        registration = client.get(
            f"/veranstaltungen/filterbar/anmeldung?day={day}&available_only=true"
        )
        assert registration.status_code == 200
        assert "Info Früh" in registration.text
        assert "Parade Spät" in registration.text
    finally:
        app.dependency_overrides.clear()
