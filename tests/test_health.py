import base64
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.auth.easyauth import parse_easyauth_principal
from app.auth.models import AuthenticatedUser
from app.config.settings import get_settings, parse_csv_env
from app.database.base import Base
from app.database.session import create_database_engine, get_db
from app.main import app


def _set_auth_env(monkeypatch, auth_mode: str, debug: bool = False) -> None:
    monkeypatch.setenv("AUTH_MODE", auth_mode)
    monkeypatch.setenv("DEBUG", "true" if debug else "false")
    monkeypatch.delenv("ADMIN_ALLOWED_EMAILS", raising=False)
    monkeypatch.delenv("ADMIN_ALLOWED_GROUP_IDS", raising=False)
    get_settings.cache_clear()


def _principal_header(payload: dict) -> str:
    return base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")


def test_healthz_remains_public(monkeypatch) -> None:
    _set_auth_env(monkeypatch, "easyauth")
    client = TestClient(app)

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.1.0"}


def test_landing_page_is_public(monkeypatch, tmp_path) -> None:
    _set_auth_env(monkeypatch, "easyauth")
    engine = create_database_engine(f"sqlite:///{tmp_path / 'public.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    def override_get_db():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    try:
        response = client.get("/")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "Gemeinsam machen wir PRIDE möglich" in response.text
    assert 'href="/auth/login"' in response.text
    assert "Derzeit sind keine Anmeldungen geöffnet" in response.text


def test_admin_denies_access_in_easyauth_mode_without_headers(monkeypatch) -> None:
    _set_auth_env(monkeypatch, "easyauth")
    client = TestClient(app)

    response = client.get("/admin")

    assert response.status_code == 401
    assert "Anmeldung erforderlich" in response.text
    assert "data-history-back" in response.text


def test_admin_allows_access_when_auth_mode_disabled(monkeypatch, tmp_path) -> None:
    _set_auth_env(monkeypatch, "disabled")
    engine = create_database_engine(f"sqlite:///{tmp_path / 'admin.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    def override_get_db():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        response = client.get("/admin")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "ST. PRIDE Volunteer Management – Admin Dashboard" in response.text
    assert "Local Developer" in response.text
    assert 'href="/admin/permissions"' in response.text
    assert 'href="/admin/db"' in response.text
    assert 'href="/admin/einstellungen/smtp"' in response.text
    assert 'href="/admin/branding"' in response.text


def test_easyauth_principal_parser_handles_valid_client_principal() -> None:
    header = _principal_header(
        {
            "auth_typ": "aad",
            "claims": [
                {"typ": "name", "val": "Admin User"},
                {"typ": "preferred_username", "val": "admin@example.org"},
                {"typ": "oid", "val": "user-123"},
                {"typ": "groups", "val": "group-123"},
            ],
        }
    )

    user = parse_easyauth_principal(
        {
            "x-ms-client-principal": header,
            "x-ms-client-principal-name": "admin@example.org",
            "x-ms-client-principal-id": "user-123",
        }
    )

    assert user == AuthenticatedUser(
        user_id="user-123",
        name="Admin User",
        email="admin@example.org",
        roles=[],
        groups=["group-123"],
        claims=["name", "preferred_username", "oid", "groups"],
    )


def test_easyauth_principal_parser_handles_invalid_base64_safely() -> None:
    user = parse_easyauth_principal({"x-ms-client-principal": "not-valid-base64!!!"})

    assert user is None


def test_easyauth_principal_parser_uses_fallback_headers() -> None:
    user = parse_easyauth_principal(
        {
            "x-ms-client-principal-name": "fallback@example.org",
            "x-ms-client-principal-id": "fallback-user-id",
        }
    )

    assert user == AuthenticatedUser(
        user_id="fallback-user-id",
        name="fallback@example.org",
        email="fallback@example.org",
        roles=[],
        groups=[],
        claims=[],
    )


def test_debug_easyauth_returns_404_when_debug_false(monkeypatch) -> None:
    _set_auth_env(monkeypatch, "disabled", debug=False)
    client = TestClient(app)

    response = client.get("/debug/easyauth")

    assert response.status_code == 404


def test_debug_db_returns_404_without_touching_database(monkeypatch) -> None:
    _set_auth_env(monkeypatch, "disabled", debug=False)

    def fail_if_called():
        raise AssertionError("database diagnostics must not run when DEBUG=false")

    monkeypatch.setattr("app.main.safe_database_diagnostics", fail_if_called)

    response = TestClient(app).get("/debug/db")

    assert response.status_code == 404


def test_debug_db_returns_safe_schema_diagnostics(monkeypatch, tmp_path) -> None:
    _set_auth_env(monkeypatch, "disabled", debug=True)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'empty.db'}")
    get_settings.cache_clear()

    response = TestClient(app).get("/debug/db")

    assert response.status_code == 200
    assert response.json() == {
        "database_type": "sqlite",
        "database_reachable": True,
        "schema_initialized": False,
        "current_revision": None,
        "expected_revision": "20260816_0013",
        "migration_pending": True,
        "tables": {"events": False, "volunteers": False, "shifts": False},
        "diagnostic_error": None,
    }
    assert "DATABASE_URL" not in response.text


def test_admin_db_returns_200_when_database_is_unavailable(monkeypatch) -> None:
    _set_auth_env(monkeypatch, "disabled")
    monkeypatch.setattr(
        "app.main.safe_database_diagnostics",
        lambda: {
            "database_type": "postgresql",
            "database_reachable": False,
            "schema_initialized": False,
            "current_revision": None,
            "expected_revision": "20260816_0012",
            "migration_pending": None,
            "tables": {"events": False, "volunteers": False, "shifts": False},
            "diagnostic_error": "database_unreachable",
        },
    )

    response = TestClient(app).get("/admin/db")

    assert response.status_code == 200
    assert "postgresql" in response.text
    assert "database_unreachable" in response.text


def test_admin_db_checks_permission_before_diagnostics(monkeypatch) -> None:
    _set_auth_env(monkeypatch, "easyauth")

    def fail_if_called():
        raise AssertionError("unauthorized requests must not initialize the database")

    monkeypatch.setattr("app.main.safe_database_diagnostics", fail_if_called)

    response = TestClient(app).get("/admin/db")

    assert response.status_code == 401


def test_debug_easyauth_returns_sanitized_data_when_debug_true(monkeypatch) -> None:
    _set_auth_env(monkeypatch, "easyauth", debug=True)
    client = TestClient(app)
    header = _principal_header(
        {
            "claims": [
                {"typ": "name", "val": "Admin User"},
                {"typ": "preferred_username", "val": "secret-admin@example.org"},
                {"typ": "access_token", "val": "super-secret-token"},
            ],
        }
    )

    response = client.get("/debug/easyauth", headers={"x-ms-client-principal": header})

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "authenticated": True,
        "name": "Admin User",
        "email": "secret-admin@example.org",
        "user_id": "",
        "roles": [],
        "groups": [],
        "claims": ["name", "preferred_username", "access_token"],
        "auth_mode": "easyauth",
        "permissions_summary": {},
    }
    assert "super-secret-token" not in response.text
    assert header not in response.text


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, []),
        ("", []),
        ("   ", []),
        (",", []),
        (",,,", []),
        ("admin@example.org", ["admin@example.org"]),
        (
            "admin@example.org,second@example.org",
            ["admin@example.org", "second@example.org"],
        ),
        (
            " admin@example.org , second@example.org ",
            ["admin@example.org", "second@example.org"],
        ),
        ("Admin@Example.Org", ["admin@example.org"]),
    ],
)
def test_parse_csv_env_handles_optional_email_allowlist_values(value, expected) -> None:
    assert parse_csv_env(value, lowercase=True) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, []),
        ("", []),
        ("   ", []),
        (",", []),
        ("group1", ["group1"]),
        (" group1 , Group2 ", ["group1", "Group2"]),
    ],
)
def test_parse_csv_env_handles_optional_group_allowlist_values(value, expected) -> None:
    assert parse_csv_env(value) == expected


def test_admin_route_returns_403_without_database_mapping_match(
    monkeypatch,
) -> None:
    _set_auth_env(monkeypatch, "easyauth")
    client = TestClient(app)
    header = _principal_header(
        {
            "auth_typ": "aad",
            "claims": [
                {"typ": "preferred_username", "val": "user@example.org"},
                {"typ": "oid", "val": "user-123"},
            ],
        }
    )

    response = client.get("/admin", headers={"x-ms-client-principal": header})

    assert response.status_code == 403


def test_legacy_allowlist_environment_values_are_ignored(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "easyauth")
    monkeypatch.setenv("ADMIN_ALLOWED_EMAILS", "legacy@example.org")
    monkeypatch.setenv("ADMIN_ALLOWED_GROUP_IDS", "legacy-group")
    get_settings.cache_clear()

    client = TestClient(app)
    response = client.get("/healthz")

    assert response.status_code == 200
