import importlib
import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import inspect
from sqlalchemy.orm import sessionmaker

from app.config.settings import get_settings
from app.database.base import Base
from app.database.session import (
    create_database_engine,
    get_db,
    get_engine,
    reset_database_engine,
)
from app.models import (
    AgeGroup,
    AssignmentSource,
    AssignmentStatus,
    CheckIn,
    Event,
    EventStatus,
    ShiftStatus,
    Team,
    TeamMaterial,
    Volunteer,
    VolunteerStatus,
)
from app.services.seed import ensure_default_event, ensure_demo_data
from app.services.volunteers import deterministic_email_hash, normalize_email


def _use_database_url(monkeypatch, database_url: str) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    reset_database_engine()


def test_models_can_be_imported():
    import app.models as models

    assert models.Event.__tablename__ == "events"
    assert models.Volunteer.__tablename__ == "volunteers"


def test_importing_app_main_does_not_create_data_directory(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:////data/app.db")
    get_settings.cache_clear()
    reset_database_engine()

    original_mkdir = Path.mkdir

    def fail_for_data_path(self, *args, **kwargs):
        if str(self) in {"/data", "//data"}:
            raise AssertionError("import should not create /data")
        return original_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fail_for_data_path)

    import app.main as main_module

    importlib.reload(main_module)

    reset_database_engine()


def test_database_url_can_be_overridden_before_lazy_engine_creation(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "override.db"
    _use_database_url(monkeypatch, f"sqlite:///{db_path}")

    engine = get_engine()

    assert str(engine.url) == f"sqlite:///{db_path}"
    assert db_path.parent.exists()


def test_database_session_can_connect_to_temp_sqlite(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'app.db'}")
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT 1").scalar_one() == 1
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1


def test_initial_migration_creates_tables(tmp_path):
    db_path = tmp_path / "migrated.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_path}"}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=True,
        env=env,
        cwd=os.getcwd(),
    )

    engine = create_database_engine(f"sqlite:///{db_path}")
    table_names = set(inspect(engine).get_table_names())
    assert {
        "events",
        "volunteers",
        "volunteer_custom_fields",
        "shifts",
        "shift_assignments",
        "blocks",
        "file_records",
        "audit_logs",
        "teams",
        "team_materials",
        "roles",
        "checkins",
        "briefings",
        "briefing_confirmations",
        "outbox_messages",
        "alembic_version",
    }.issubset(table_names)


def test_default_event_seed_is_idempotent(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'seed.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    with Session() as db:
        first = ensure_default_event(db)
        second = ensure_default_event(db)
        assert first.id == second.id
        assert db.query(Event).count() == 1
        assert first.slug == "pride-2026"


def test_demo_seed_is_idempotent_and_uses_fictional_contacts(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'demo-seed.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    with Session() as db:
        first = ensure_demo_data(db)
        second = ensure_demo_data(db)

        assert first.id == second.id
        assert db.query(Team).filter(Team.event_id == first.id).count() == 7
        assert db.query(TeamMaterial).count() == 11
        assert db.query(Volunteer).filter(Volunteer.event_id == first.id).count() == 25
        assert all(
            volunteer.email.endswith("@example.invalid")
            for volunteer in db.query(Volunteer).filter(Volunteer.event_id == first.id)
        )


def test_volunteer_can_be_created_without_birth_date(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'volunteer.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    with Session() as db:
        event = Event(name="Test Event", slug="test", status=EventStatus.draft)
        email = "Person@Example.Org"
        volunteer = Volunteer(
            event=event,
            first_name="Test",
            last_name="Person",
            email=email,
            email_normalized=normalize_email(email),
            email_hash=deterministic_email_hash(email),
            age_group=AgeGroup.adult,
            status=VolunteerStatus.submitted,
        )
        db.add(volunteer)
        db.commit()

        assert volunteer.id is not None
        assert volunteer.birth_date is None


def test_age_group_validation_values():
    assert [item.value for item in AgeGroup] == ["under_16", "age_16_17", "adult"]


def test_extended_workflow_model_defaults(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'workflow.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    with Session() as db:
        event = Event(name="Test", slug="workflow", status=EventStatus.draft)
        team = Team(event=event, name="Aufbau")
        db.add_all([event, team])
        db.commit()

        assert team.id is not None
        assert ShiftStatus.open.value == "open"
        assert AssignmentStatus.waitlisted.value == "waitlisted"
        assert AssignmentSource.public.value == "public"
        assert CheckIn.__tablename__ == "checkins"


def test_admin_db_requires_admin_permission(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_MODE", "easyauth")
    get_settings.cache_clear()
    engine = create_database_engine(f"sqlite:///{tmp_path / 'admin.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    def override_get_db():
        with Session() as db:
            yield db

    from app.main import app

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = TestClient(app).get("/admin/db")
    finally:
        app.dependency_overrides.clear()
        monkeypatch.setenv("AUTH_MODE", "disabled")
        get_settings.cache_clear()

    assert response.status_code == 401
    assert "Sign in required" in response.text
