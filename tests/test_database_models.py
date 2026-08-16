import importlib
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import inspect
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.config.settings import get_settings
from app.database.base import Base
from app.database.diagnostics import inspect_database
from app.database.session import (
    create_database_engine,
    database_backend,
    get_db,
    get_engine,
    reset_database_engine,
)
from app.models import (
    AgeGroup,
    AssignmentSource,
    AssignmentStatus,
    AuditLog,
    BrandingSettings,
    Briefing,
    BriefingConfirmation,
    CheckIn,
    CheckInMaterial,
    Event,
    EventStatus,
    MailTemplate,
    OutboxMessage,
    ShiftAssignment,
    ShiftStatus,
    SMTPConfiguration,
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


def test_database_backend_normalizes_supported_urls():
    assert database_backend("sqlite:///./dev.db") == "sqlite"
    assert (
        database_backend(
            "postgresql+psycopg://volunteer:secret@db.example.test/volunteer"
        )
        == "postgresql"
    )


def test_postgresql_engine_is_lazy_and_uses_psycopg():
    engine = create_database_engine(
        "postgresql+psycopg://volunteer:secret@db.example.test/volunteer"
    )
    try:
        assert engine.dialect.name == "postgresql"
        assert engine.dialect.driver == "psycopg"
    finally:
        engine.dispose()


def test_database_diagnostics_handles_uninitialized_schema(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'empty.db'}")

    diagnostics = inspect_database(engine)

    assert diagnostics == {
        "database_type": "sqlite",
        "database_reachable": True,
        "schema_initialized": False,
        "current_revision": None,
        "expected_revision": "20260816_0012",
        "migration_pending": True,
        "tables": {"events": False, "volunteers": False, "shifts": False},
        "diagnostic_error": None,
    }


def test_database_diagnostics_never_exposes_connection_exception_secrets():
    class BrokenEngine:
        url = make_url(
            "postgresql+psycopg://volunteer:never-print-me@db.example.test/volunteer"
        )

        def connect(self):
            raise RuntimeError("connection failed for password never-print-me")

    diagnostics = inspect_database(BrokenEngine())

    assert diagnostics["database_type"] == "postgresql"
    assert diagnostics["database_reachable"] is False
    assert diagnostics["diagnostic_error"] == "database_unreachable"
    assert "never-print-me" not in str(diagnostics)


def test_web_container_start_does_not_run_migrations_or_seed():
    start_script = (
        Path(__file__).resolve().parents[1] / "scripts/start.sh"
    ).read_text()

    assert "alembic upgrade" not in start_script
    assert "seed_default_event" not in start_script
    assert "exec uvicorn" in start_script


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
        "checkin_materials",
        "smtp_configurations",
        "mail_templates",
        "roles",
        "checkins",
        "briefings",
        "briefing_confirmations",
        "outbox_messages",
        "permissions",
        "permission_mappings",
        "branding_settings",
        "alembic_version",
    }.issubset(table_names)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        branding = db.get(BrandingSettings, 1)
        assert branding is not None
        assert branding.logo_url is None
        assert branding.logo_data is None


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
        assert db.query(Volunteer).filter(Volunteer.event_id == first.id).count() == 36
        assert db.query(ShiftAssignment).count() == 36
        assert {
            item.assignment_status for item in db.query(ShiftAssignment).all()
        }.issuperset(
            {
                AssignmentStatus.confirmed,
                AssignmentStatus.waitlisted,
                AssignmentStatus.pending,
                AssignmentStatus.checked_in,
                AssignmentStatus.attended,
                AssignmentStatus.cancelled,
                AssignmentStatus.rejected,
            }
        )
        assert db.query(CheckIn).count() == 4
        assert db.query(CheckInMaterial).count() == 4
        assert db.query(Briefing).count() == 3
        assert db.query(BriefingConfirmation).count() >= 14
        assert db.query(OutboxMessage).count() == 6
        assert db.query(MailTemplate).count() == 6
        assert db.query(AuditLog).count() == 4
        smtp = db.query(SMTPConfiguration).one()
        assert smtp.host == "smtp.example.invalid"
        assert smtp.enabled is False
        assert first.is_public is True
        assert first.status == EventStatus.registration_open
        assert first.starts_at.date() > date.today()
        assert all(
            volunteer.email.endswith("@example.invalid")
            for volunteer in db.query(Volunteer).filter(Volunteer.event_id == first.id)
        )


def test_deployment_uses_fixed_database_job_entrypoint():
    workflow = (
        Path(__file__).parents[1] / ".github/workflows/deploy-dev.yml"
    ).read_text(encoding="utf-8")
    migration_job = (
        Path(__file__).parents[1] / "infra/modules/migration-job.bicep"
    ).read_text(encoding="utf-8")

    assert "--command ./scripts/deploy_database.py" in workflow
    assert "--args" not in workflow
    assert '--set-env-vars "SEED_DEMO_DATA=$seed_demo_data"' in workflow
    assert "scripts/deploy_database.py" in migration_job
    assert "scripts.seed_default_event" not in workflow


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
    assert "Anmeldung erforderlich" in response.text
    assert "data-history-back" in response.text
