from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.database.session import create_database_engine, get_db
from app.main import app
from app.models import Event, EventStatus


def test_shared_form_validation_asset_has_accessible_error_behavior():
    response = TestClient(app).get("/static/js/forms.js")

    assert response.status_code == 200
    source = response.text
    assert "Bitte überprüfe deine Eingaben." in source
    assert 'setAttribute("aria-invalid", "true")' in source
    assert 'setAttribute("aria-describedby"' in source
    assert "this.summary.focus()" in source
    assert "requiredGroup" in source
    assert "maxFileSize" in source
    assert "data-server-error" in source
    assert "window.confirm" in source
    assert "decorateRequirements(form)" in source
    assert "dataset.fieldRequirement" in source
    assert "document.querySelectorAll('form:not([data-validation=\"off\"])')" in source
    assert "control.dataset.after" in source
    assert "control.dataset.rangeStartDate" in source
    assert 'endTime?.value || "23:59:59.999"' in source


def test_error_pages_offer_history_back_with_home_fallback():
    client = TestClient(app)

    response = client.get("/does-not-exist")
    navigation = client.get("/static/js/navigation.js")

    assert response.status_code == 404
    assert 'href="/" data-history-back' in response.text
    assert "Zurück" in response.text
    assert "Zur Startseite" in response.text
    assert navigation.status_code == 200
    assert "window.history.length <= 1" in navigation.text
    assert "window.history.back()" in navigation.text


def test_event_form_keeps_values_and_shows_server_errors_in_form(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'event-form.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        db.add(Event(name="Bestehend", slug="bestehend", status=EventStatus.draft))
        db.commit()

    def override_get_db():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        missing = client.post(
            "/admin/veranstaltungen/neu",
            data={"name": "", "slug": "", "short_description": "Bleibt erhalten"},
        )
        duplicate = client.post(
            "/admin/veranstaltungen/neu",
            data={
                "name": "Neuer Titel",
                "slug": "bestehend",
                "start_date": "2030-05-10",
                "end_date": "2030-05-11",
                "registration_open_date": "2030-05-01",
                "registration_close_date": "2030-05-09",
                "contact_name": "Kontakt bleibt erhalten",
            },
        )
        page = client.get("/admin/veranstaltungen/neu")
    finally:
        app.dependency_overrides.clear()

    assert missing.status_code == 422
    assert "data-form-error-summary" in missing.text
    assert 'data-error-for="name"' in missing.text
    assert 'data-error-for="slug"' in missing.text
    assert 'data-error-for="start_date"' in missing.text
    assert 'data-error-for="end_date"' in missing.text
    assert 'value="Bleibt erhalten"' in missing.text
    assert duplicate.status_code == 422
    assert "Dieses URL-Kürzel wird bereits verwendet." in duplicate.text
    assert 'value="Neuer Titel"' in duplicate.text
    assert 'value="Kontakt bleibt erhalten"' in duplicate.text
    assert page.status_code == 200
    assert 'name="name"' in page.text and "required" in page.text
    assert 'name="start_date"' in page.text and "required" in page.text
    assert 'name="start_time"' in page.text
    assert 'data-range-start-date="start_date"' in page.text
    assert 'name="registration_open_date"' in page.text
    assert 'name="registration_open_date"' in page.text and "required" in page.text
    assert 'name="registration_open_time"' in page.text
    assert 'data-range-start-date="registration_open_date"' in page.text
