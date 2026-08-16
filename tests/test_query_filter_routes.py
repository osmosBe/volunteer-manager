from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.config.settings import get_settings
from app.database.base import Base
from app.database.session import create_database_engine, get_db
from app.forms.query_filters import (
    QueryFilterError,
    parse_checkbox,
    parse_optional_date,
    parse_optional_id,
    parse_optional_time,
)
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


def _volunteer(event: Event, first_name: str, age_group: AgeGroup) -> Volunteer:
    email = f"{first_name.lower()}@example.invalid"
    return Volunteer(
        event=event,
        first_name=first_name,
        last_name="Filtertest",
        email=email,
        email_normalized=normalize_email(email),
        email_hash=deterministic_email_hash(email),
        age_group=age_group,
    )


def test_query_filter_parsers_accept_blank_and_reject_malformed_values():
    assert parse_optional_id("", field_name="event_id", label="ID") is None
    assert parse_optional_id(" 42 ", field_name="event_id", label="ID") == 42
    assert parse_optional_date("", field_name="day", label="Tag") is None
    assert parse_optional_date("2026-08-16", field_name="day", label="Tag").day == 16
    assert parse_optional_time("", field_name="time", label="Zeit") is None
    assert parse_optional_time("09:30", field_name="time", label="Zeit").minute == 30
    assert parse_checkbox(None, field_name="flag", label="Flag") is False
    assert parse_checkbox("true", field_name="flag", label="Flag") is True

    for parser, value, kwargs in [
        (parse_optional_id, "invalid", {"field_name": "id", "label": "ID"}),
        (parse_optional_id, "0", {"field_name": "id", "label": "ID"}),
        (
            parse_optional_date,
            "2026-99-99",
            {"field_name": "day", "label": "Tag"},
        ),
        (
            parse_optional_time,
            "29:90",
            {"field_name": "time", "label": "Zeit"},
        ),
        (
            parse_optional_time,
            "09:30+02:00",
            {"field_name": "time", "label": "Zeit"},
        ),
        (
            parse_checkbox,
            "perhaps",
            {"field_name": "flag", "label": "Flag"},
        ),
    ]:
        with pytest.raises(QueryFilterError):
            parser(value, **kwargs)


def test_admin_checkin_and_volunteer_filters_handle_empty_and_invalid_values(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AUTH_MODE", "disabled")
    get_settings.cache_clear()
    engine = create_database_engine(f"sqlite:///{tmp_path / 'query-filters.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    start = datetime.now(timezone.utc) + timedelta(hours=1)
    with Session() as db:
        alpha_event = Event(
            name="Alpha Event", slug="alpha-filter", status=EventStatus.ongoing
        )
        beta_event = Event(
            name="Beta Event", slug="beta-filter", status=EventStatus.ongoing
        )
        alpha_shift = Shift(
            event=alpha_event,
            title="Alpha Shift",
            starts_at=start,
            ends_at=start + timedelta(hours=2),
            needed_count=1,
            status=ShiftStatus.open,
        )
        beta_shift = Shift(
            event=beta_event,
            title="Beta Shift",
            starts_at=start,
            ends_at=start + timedelta(hours=2),
            needed_count=1,
            status=ShiftStatus.open,
        )
        alpha = _volunteer(alpha_event, "Alpha", AgeGroup.adult)
        beta = _volunteer(beta_event, "Beta", AgeGroup.age_16_17)
        db.add_all(
            [
                alpha_event,
                beta_event,
                alpha_shift,
                beta_shift,
                alpha,
                beta,
                ShiftAssignment(
                    volunteer=alpha,
                    shift=alpha_shift,
                    assignment_status=AssignmentStatus.confirmed,
                ),
                ShiftAssignment(
                    volunteer=beta,
                    shift=beta_shift,
                    assignment_status=AssignmentStatus.confirmed,
                ),
            ]
        )
        db.commit()
        alpha_event_id = alpha_event.id

    def override_get_db():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)

        unfiltered_checkins = client.get(
            "/admin/check-in", params={"event_id": "", "query": ""}
        )
        assert unfiltered_checkins.status_code == 200
        assert "Alpha Filtertest" in unfiltered_checkins.text
        assert "Beta Filtertest" in unfiltered_checkins.text
        assert (
            'href="/admin/check-in">Filter zurücksetzen</a>' in unfiltered_checkins.text
        )

        all_checkins = client.get(
            "/admin/check-in", params={"event_id": "", "query": "Filtertest"}
        )
        assert all_checkins.status_code == 200
        assert "Alpha Filtertest" in all_checkins.text
        assert "Beta Filtertest" in all_checkins.text

        one_event = client.get(
            "/admin/check-in", params={"event_id": str(alpha_event_id), "query": ""}
        )
        assert one_event.status_code == 200
        assert "Alpha Filtertest" in one_event.text
        assert "Beta Filtertest" not in one_event.text
        assert f'value="{alpha_event_id}" selected' in one_event.text

        invalid_checkin = client.get(
            "/admin/check-in", params={"event_id": "not-an-id", "query": "Beta"}
        )
        assert invalid_checkin.status_code == 422
        assert "Die Veranstaltung ist ungültig." in invalid_checkin.text
        assert "Bitte überprüfe deine Eingaben." in invalid_checkin.text
        assert 'name="query"' in invalid_checkin.text
        assert 'value="Beta"' in invalid_checkin.text

        all_volunteers = client.get(
            "/admin/ehrenamtliche",
            params={
                "event_id": "",
                "assignment_status": "",
                "email_verified": "",
            },
        )
        assert all_volunteers.status_code == 200
        assert "Alpha Filtertest" in all_volunteers.text
        assert "Beta Filtertest" in all_volunteers.text

        combined = client.get(
            "/admin/ehrenamtliche",
            params={
                "event_id": "",
                "assignment_status": "confirmed",
                "email_verified": "pending",
                "u18": "true",
            },
        )
        assert combined.status_code == 200
        assert "Alpha Filtertest" not in combined.text
        assert "Beta Filtertest" in combined.text
        assert 'value="confirmed" selected' in combined.text
        assert 'value="pending" selected' in combined.text
        assert 'name="u18" value="true" id="u18" checked' in combined.text
        assert 'href="/admin/ehrenamtliche">Filter zurücksetzen</a>' in combined.text

        for params, message in [
            ({"event_id": "invalid"}, "Die Veranstaltung ist ungültig."),
            (
                {"assignment_status": "invalid"},
                "Der Zuteilungsstatusfilter ist ungültig.",
            ),
            (
                {"email_verified": "invalid"},
                "Der E-Mail-Statusfilter ist ungültig.",
            ),
            ({"u18": "invalid"}, "Der U18-Filter ist ungültig."),
        ]:
            response = client.get("/admin/ehrenamtliche", params=params)
            assert response.status_code == 422
            assert message in response.text
            assert "Bitte überprüfe deine Eingaben." in response.text
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()
