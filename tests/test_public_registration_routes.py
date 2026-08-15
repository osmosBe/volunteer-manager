from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.database.session import create_database_engine, get_db
from app.main import app
from app.models import Event, EventStatus, Shift, ShiftStatus


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
        assignment_id = 1
        token = confirmation_url.split("/")[2]
        cancelled = client.post(
            f"/anmeldung/{token}/zuteilungen/{assignment_id}/stornieren",
            follow_redirects=False,
        )
        assert cancelled.status_code == 303
        assert "Storniert" in client.get(confirmation_url).text
    finally:
        app.dependency_overrides.clear()
