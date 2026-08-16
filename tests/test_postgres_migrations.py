import os
import uuid

import pytest
from sqlalchemy import inspect
from sqlalchemy.orm import sessionmaker

from app.database.session import create_database_engine
from app.models import (
    AgeGroup,
    BrandingSettings,
    Event,
    EventStatus,
    Volunteer,
    VolunteerStatus,
)
from app.services.volunteers import deterministic_email_hash, normalize_email

POSTGRES_TEST_URL = os.getenv("POSTGRES_TEST_URL")
pytestmark = pytest.mark.skipif(
    not POSTGRES_TEST_URL, reason="POSTGRES_TEST_URL is only set in integration CI"
)


def test_postgresql_schema_preserves_nullable_business_fields():
    engine = create_database_engine(POSTGRES_TEST_URL)
    inspector = inspect(engine)

    event_columns = {item["name"]: item for item in inspector.get_columns("events")}
    volunteer_columns = {
        item["name"]: item for item in inspector.get_columns("volunteers")
    }

    assert event_columns["starts_at"]["nullable"] is True
    assert event_columns["ends_at"]["nullable"] is True
    assert event_columns["start_time_is_set"]["nullable"] is False
    assert event_columns["end_time_is_set"]["nullable"] is False
    assert volunteer_columns["birth_date"]["nullable"] is True


def test_postgresql_models_accept_nullable_event_and_birth_date():
    engine = create_database_engine(POSTGRES_TEST_URL)
    Session = sessionmaker(bind=engine)
    suffix = uuid.uuid4().hex

    with Session() as db:
        event = Event(
            name="PostgreSQL integration test",
            slug=f"postgres-{suffix}",
            starts_at=None,
            ends_at=None,
            status=EventStatus.registration_open,
        )
        email = f"postgres-{suffix}@example.invalid"
        volunteer = Volunteer(
            event=event,
            first_name="PostgreSQL",
            last_name="Test",
            email=email,
            email_normalized=normalize_email(email),
            email_hash=deterministic_email_hash(email),
            birth_date=None,
            age_group=AgeGroup.adult,
            status=VolunteerStatus.submitted,
        )
        db.add(volunteer)
        db.flush()

        assert event.id is not None
        assert volunteer.id is not None
        assert event.starts_at is None
        assert event.ends_at is None
        assert event.status == EventStatus.registration_open
        assert volunteer.birth_date is None

        db.rollback()


def test_postgresql_branding_singleton_accepts_binary_logo_data():
    engine = create_database_engine(POSTGRES_TEST_URL)
    Session = sessionmaker(bind=engine)

    with Session() as db:
        branding = db.get(BrandingSettings, 1)
        assert branding is not None
        branding.logo_url = None
        branding.logo_data = b"postgres-logo"
        branding.logo_content_type = "image/png"
        branding.logo_filename = "logo.png"
        db.flush()

        assert branding.logo_data == b"postgres-logo"
        db.rollback()
