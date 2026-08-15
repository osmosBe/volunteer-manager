import base64
import json

import pytest

from app.auth.easyauth import parse_easyauth_principal
from app.auth.models import AuthenticatedUser
from app.auth.permissions import has_permission, load_permissions_config
from app.config.settings import Settings

ROLE_CLAIM_URI = "http://schemas.microsoft.com/ws/2008/06/identity/claims/role"
GROUPSID_CLAIM_URI = "http://schemas.microsoft.com/ws/2008/06/identity/claims/groupsid"
EMAIL_CLAIM_URI = "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress"


def _principal(claims):
    return base64.b64encode(json.dumps({"claims": claims}).encode()).decode()


@pytest.mark.parametrize(
    ("claim_type", "expected"),
    [
        ("roles", ["Volunteer.Admin"]),
        ("role", ["Volunteer.Admin"]),
        (
            ROLE_CLAIM_URI,
            ["Volunteer.Admin"],
        ),
    ],
)
def test_easyauth_extracts_roles(claim_type, expected):
    user = parse_easyauth_principal(
        {
            "x-ms-client-principal": _principal(
                [{"typ": claim_type, "val": " Volunteer.Admin "}]
            )
        }
    )

    assert user.roles == expected


def test_easyauth_extracts_and_deduplicates_groups():
    user = parse_easyauth_principal(
        {
            "x-ms-client-principal": _principal(
                [
                    {"typ": "groups", "val": " group-a "},
                    {"typ": "group", "val": "group-a"},
                    {
                        "typ": GROUPSID_CLAIM_URI,
                        "val": "group-b",
                    },
                ]
            )
        }
    )

    assert user.groups == ["group-a", "group-b"]


def test_easyauth_extracts_schema_email_and_claim_names_only():
    user = parse_easyauth_principal(
        {
            "x-ms-client-principal": _principal(
                [
                    {
                        "typ": EMAIL_CLAIM_URI,
                        "val": "admin@example.org",
                    },
                    {"typ": "name", "val": "Admin User"},
                ]
            )
        }
    )

    assert user.email == "admin@example.org"
    assert user.claims == [
        EMAIL_CLAIM_URI,
        "name",
    ]
    assert "Admin User" not in user.claims


def test_easyauth_malformed_principal_does_not_crash():
    assert parse_easyauth_principal({"x-ms-client-principal": "not-base64"}) is None


def test_load_permissions_config_normalizes_values(tmp_path):
    config_file = tmp_path / "permissions.yaml"
    config_file.write_text(
        """
permissions:
  admin:
    roles:
      - Volunteer.Admin
      - ""
    groups:
      - group-a
      - group-a
    emails:
      - Admin@Example.Org
""",
        encoding="utf-8",
    )

    config = load_permissions_config(config_file)

    assert config.permissions["admin"].roles == ["Volunteer.Admin"]
    assert config.permissions["admin"].groups == ["group-a"]
    assert config.permissions["admin"].emails == ["admin@example.org"]


def test_missing_or_invalid_permissions_file_fails_closed(tmp_path):
    settings = Settings(
        auth_mode="easyauth",
        permissions_config_path=str(tmp_path / "missing.yaml"),
    )
    user = AuthenticatedUser(email="admin@example.org", roles=["Volunteer.Admin"])

    assert not has_permission(user, "admin", settings)

    invalid = tmp_path / "invalid.yaml"
    invalid.write_text("not permissions", encoding="utf-8")
    settings = Settings(auth_mode="easyauth", permissions_config_path=str(invalid))
    assert not has_permission(user, "admin", settings)


def test_role_group_and_email_grant_permission(tmp_path):
    config_file = tmp_path / "permissions.yaml"
    config_file.write_text(
        """
permissions:
  admin:
    roles:
      - Volunteer.Admin
    groups:
      - group-a
    emails:
      - admin@example.org
  empty:
    roles: []
    groups: []
    emails: []
""",
        encoding="utf-8",
    )
    settings = Settings(auth_mode="easyauth", permissions_config_path=str(config_file))

    assert has_permission(
        AuthenticatedUser(roles=["Volunteer.Admin"]), "admin", settings
    )
    assert has_permission(AuthenticatedUser(groups=["group-a"]), "admin", settings)
    assert has_permission(
        AuthenticatedUser(email="ADMIN@example.org"), "admin", settings
    )
    assert not has_permission(None, "admin", settings)
    assert not has_permission(
        AuthenticatedUser(email="user@example.org"), "admin", settings
    )
    assert not has_permission(
        AuthenticatedUser(roles=["Volunteer.Admin"]), "unknown", settings
    )
    assert not has_permission(
        AuthenticatedUser(roles=["Volunteer.Admin"]), "empty", settings
    )


def test_auth_mode_disabled_grants_all_permissions():
    settings = Settings(auth_mode="disabled", permissions_config_path="missing.yaml")

    assert has_permission(None, "anything", settings)


def test_legacy_disable_auth_mode_is_normalized(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "disable")

    settings = Settings(permissions_config_path="missing.yaml")

    assert settings.auth_mode == "disabled"
