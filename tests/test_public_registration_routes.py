import re
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.database.session import create_database_engine, get_db
from app.main import app
from app.models import Event, EventStatus, OutboxMessage, Role, Shift, ShiftStatus, Team


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
        event_page = client.get("/veranstaltungen/stpride-test")
        assert event_page.status_code == 200
        assert (
            f"/veranstaltungen/stpride-test/anmeldung?shift_id={shift_id}#registration-form"
            in event_page.text
        )
        direct_registration = client.get(
            f"/veranstaltungen/stpride-test/anmeldung?shift_id={shift_id}"
        )
        assert direct_registration.status_code == 200
        assert re.search(
            rf'id="shift-{shift_id}"[^>]*checked', direct_registration.text
        )
        assert client.get("/veranstaltungen/stpride-test/anmeldung").status_code == 200
        assert (
            "Zusammenfassung vor dem Absenden"
            in client.get("/veranstaltungen/stpride-test/anmeldung").text
        )
        valid_form = client.get("/veranstaltungen/stpride-test/anmeldung")
        assert "data-form-error-summary" not in valid_form.text
        assert "/static/js/forms.js" in valid_form.text
        missing_required = client.post(
            "/veranstaltungen/stpride-test/anmeldung",
            data={
                "first_name": "",
                "last_name": "Muster",
                "email": "alex@example.org",
                "birth_date": "1990-01-01",
                "contact_consent": "true",
                "shift_ids": str(shift_id),
            },
        )
        assert missing_required.status_code == 422
        assert "Bitte überprüfe deine Eingaben." in missing_required.text
        assert 'data-error-for="first_name"' in missing_required.text
        assert 'href="#first_name"' in missing_required.text
        assert 'id="first_name"' in missing_required.text
        assert 'aria-invalid="true"' in missing_required.text
        assert 'aria-describedby="first_name-error"' in missing_required.text
        assert 'data-field-error-for="first_name"' in missing_required.text
        invalid_email = client.post(
            "/veranstaltungen/stpride-test/anmeldung",
            data={
                "first_name": "<script>alert(1)</script>",
                "last_name": "Muster",
                "email": "keine-adresse",
                "birth_date": "1990-01-01",
                "contact_consent": "true",
                "shift_ids": str(shift_id),
            },
        )
        assert invalid_email.status_code == 422
        assert 'data-error-for="email"' in invalid_email.text
        assert 'href="#email"' in invalid_email.text
        assert 'aria-describedby="email-help email-error"' in invalid_email.text
        assert 'value="&lt;script&gt;alert(1)&lt;/script&gt;"' in invalid_email.text
        assert 'value="<script>alert(1)</script>"' not in invalid_email.text
        invalid = client.post(
            "/veranstaltungen/stpride-test/anmeldung",
            data={
                "first_name": "Alex",
                "last_name": "Muster",
                "email": "alex@example.org",
                "birth_date": "kein-datum",
                "contact_consent": "true",
                "shift_ids": str(shift_id),
            },
        )
        assert invalid.status_code == 422
        assert "Bitte gib ein gültiges Geburtsdatum an." in invalid.text
        assert "data-server-error" in invalid.text
        assert "Bitte überprüfe deine Eingaben." in invalid.text
        assert 'data-error-for="birth_date"' in invalid.text
        assert 'name="first_name" value="Alex"' in invalid.text
        assert 'name="last_name" value="Muster"' in invalid.text
        assert 'name="email" value="alex@example.org"' in invalid.text
        assert 'name="birth_date" value="kein-datum"' in invalid.text
        assert re.search(rf'id="shift-{shift_id}"[^>]*checked', invalid.text)
        assert 'id="contact_consent"' in invalid.text
        assert re.search(r'id="contact_consent"[^>]*checked', invalid.text)
        response = client.post(
            "/veranstaltungen/stpride-test/anmeldung",
            data={
                "first_name": "Alex",
                "last_name": "Muster",
                "email": "alex@example.org",
                "birth_date": "1990-01-01",
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
        assert "Bitte bestätige deine E-Mail-Adresse" in confirmation.text
        assert "/zuteilungen/1/qr.svg" in confirmation.text
        qr_response = client.get(
            f"/anmeldung/{confirmation_url.split('/')[2]}/zuteilungen/1/qr.svg"
        )
        assert qr_response.status_code == 200
        assert qr_response.headers["content-type"].startswith("image/svg+xml")
        assert b"alex@example.org" not in qr_response.content
        with Session() as db:
            verification_message = db.scalar(
                select(OutboxMessage).where(OutboxMessage.kind == "email_verification")
            )
            token_match = re.search(
                r"email-bestaetigen/([A-Za-z0-9_-]+)", verification_message.body
            )
            assert token_match is not None
        verified = client.get(f"/anmeldung/email-bestaetigen/{token_match.group(1)}")
        assert verified.status_code == 200
        assert "E-Mail-Adresse bestätigt" in verified.text
        assert "Deine E-Mail-Adresse ist bestätigt" in client.get(confirmation_url).text
        assert (
            client.get(
                f"/anmeldung/email-bestaetigen/{token_match.group(1)}"
            ).status_code
            == 404
        )
        edit = client.get(f"/anmeldung/{confirmation_url.split('/')[2]}/bearbeiten")
        assert edit.status_code == 200
        assert "Alex" in edit.text
        assignment_id = 1
        token = confirmation_url.split("/")[2]
        invalid_edit = client.post(
            f"/anmeldung/{token}/bearbeiten",
            data={
                "first_name": "Geändert",
                "last_name": "Muster",
                "email": "weiterhin-keine-adresse",
                "birth_date": "1990-01-01",
                "contact_consent": "true",
                "shift_ids": str(shift_id),
            },
        )
        assert invalid_edit.status_code == 422
        assert "Bitte überprüfe deine Eingaben." in invalid_edit.text
        assert 'data-error-for="email"' in invalid_edit.text
        assert 'name="first_name" value="Geändert"' in invalid_edit.text
        assert 'name="email" value="weiterhin-keine-adresse"' in invalid_edit.text
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
    start = (datetime.now(timezone.utc) + timedelta(days=2)).replace(
        hour=10, minute=0, second=0, microsecond=0
    )
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
