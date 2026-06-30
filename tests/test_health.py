import base64
import json

import pytest
from fastapi.testclient import TestClient

from app.auth.admin import is_authorized_admin
from app.auth.easyauth import parse_easyauth_principal
from app.auth.models import AuthenticatedUser
from app.config.settings import Settings, get_settings, parse_csv_env
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


def test_landing_page_is_public(monkeypatch) -> None:
    _set_auth_env(monkeypatch, "easyauth")
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "ST. PRIDE Volunteer Management" in response.text
    assert "Admin sign in" in response.text


def test_admin_denies_access_in_easyauth_mode_without_headers(monkeypatch) -> None:
    _set_auth_env(monkeypatch, "easyauth")
    client = TestClient(app)

    response = client.get("/admin")

    assert response.status_code == 401
    assert "Sign in required" in response.text


def test_admin_allows_access_when_auth_mode_disabled(monkeypatch) -> None:
    _set_auth_env(monkeypatch, "disabled")
    client = TestClient(app)

    response = client.get("/admin")

    assert response.status_code == 200
    assert "ST. PRIDE Volunteer Management – Admin Dashboard" in response.text
    assert "Local Developer" in response.text


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


def test_authorization_helper_allows_configured_email() -> None:
    settings = Settings(auth_mode="easyauth", admin_allowed_emails="admin@example.org")

    user = AuthenticatedUser(email="Admin@Example.Org")

    assert is_authorized_admin(user, settings)


def test_authorization_helper_allows_configured_group_if_present() -> None:
    settings = Settings(auth_mode="easyauth", admin_allowed_group_ids="group-a,group-b")

    user = AuthenticatedUser(groups=["group-b"])

    assert is_authorized_admin(user, settings)


def test_authorization_helper_denies_access_when_no_allowlist_configured() -> None:
    settings = Settings(
        auth_mode="easyauth", admin_allowed_emails="", admin_allowed_group_ids=""
    )
    user = AuthenticatedUser(email="admin@example.org", groups=["group-b"])

    assert not is_authorized_admin(user, settings)


def test_debug_easyauth_returns_404_when_debug_false(monkeypatch) -> None:
    _set_auth_env(monkeypatch, "disabled", debug=False)
    client = TestClient(app)

    response = client.get("/debug/easyauth")

    assert response.status_code == 404


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
        "permissions_summary": {
            "admin": False,
            "checkin": False,
            "manager": False,
            "police": False,
        },
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


def test_settings_parses_comma_separated_admin_allowed_emails() -> None:
    settings = Settings(admin_allowed_emails="admin@example.org, ops@example.org, ")

    assert settings.admin_allowed_emails == ["admin@example.org", "ops@example.org"]


def test_settings_parses_single_admin_allowed_email() -> None:
    settings = Settings(admin_allowed_emails="admin@example.org")

    assert settings.admin_allowed_emails == ["admin@example.org"]


def test_settings_normalizes_admin_allowed_emails_to_lowercase() -> None:
    settings = Settings(admin_allowed_emails="Admin@Example.Org")

    assert settings.admin_allowed_emails == ["admin@example.org"]


def test_settings_parses_comma_separated_admin_allowed_group_ids() -> None:
    settings = Settings(admin_allowed_group_ids="group-a, group-b, ")

    assert settings.admin_allowed_group_ids == ["group-a", "group-b"]


def test_settings_parses_single_admin_allowed_group_id_without_lowercasing() -> None:
    settings = Settings(admin_allowed_group_ids="GroupA")

    assert settings.admin_allowed_group_ids == ["GroupA"]


@pytest.mark.parametrize(
    ("env_name", "value", "expected_emails", "expected_groups"),
    [
        ("ADMIN_ALLOWED_EMAILS", None, [], []),
        ("ADMIN_ALLOWED_EMAILS", "", [], []),
        ("ADMIN_ALLOWED_EMAILS", "   ", [], []),
        ("ADMIN_ALLOWED_EMAILS", ",", [], []),
        (
            "ADMIN_ALLOWED_EMAILS",
            "admin@example.org",
            ["admin@example.org"],
            [],
        ),
        (
            "ADMIN_ALLOWED_EMAILS",
            "admin@example.org,second@example.org",
            ["admin@example.org", "second@example.org"],
            [],
        ),
        (
            "ADMIN_ALLOWED_EMAILS",
            " admin@example.org , second@example.org ",
            ["admin@example.org", "second@example.org"],
            [],
        ),
        ("ADMIN_ALLOWED_GROUP_IDS", None, [], []),
        ("ADMIN_ALLOWED_GROUP_IDS", "", [], []),
        ("ADMIN_ALLOWED_GROUP_IDS", "group1", [], ["group1"]),
    ],
)
def test_settings_loads_supported_allowlist_env_values(
    monkeypatch, env_name, value, expected_emails, expected_groups
) -> None:
    monkeypatch.delenv("ADMIN_ALLOWED_EMAILS", raising=False)
    monkeypatch.delenv("ADMIN_ALLOWED_GROUP_IDS", raising=False)
    if value is not None:
        monkeypatch.setenv(env_name, value)

    settings = Settings()

    assert settings.admin_allowed_emails == expected_emails
    assert settings.admin_allowed_group_ids == expected_groups


def test_admin_route_returns_403_when_easyauth_allowlists_are_empty(
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


@pytest.mark.parametrize(
    ("email_value", "group_value"),
    [
        (None, None),
        ("", ""),
        ("   ", "   "),
        (",", ","),
        (",,,", ",,,"),
        ("admin@example.org", "group1"),
        ("admin@example.org,second@example.org", "group1,group2"),
        (" admin@example.org , second@example.org ", " group1 , Group2 "),
    ],
)
def test_application_startup_succeeds_with_supported_allowlist_configurations(
    monkeypatch, email_value, group_value
) -> None:
    monkeypatch.setenv("AUTH_MODE", "easyauth")
    for env_name, value in (
        ("ADMIN_ALLOWED_EMAILS", email_value),
        ("ADMIN_ALLOWED_GROUP_IDS", group_value),
    ):
        if value is None:
            monkeypatch.delenv(env_name, raising=False)
        else:
            monkeypatch.setenv(env_name, value)
    get_settings.cache_clear()

    client = TestClient(app)
    response = client.get("/healthz")

    assert response.status_code == 200
