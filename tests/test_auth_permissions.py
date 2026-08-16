import base64
import json

import pytest

from app.auth.easyauth import parse_easyauth_principal
from app.auth.permissions import has_permission
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


def test_auth_mode_disabled_grants_all_permissions():
    settings = Settings(auth_mode="disabled")

    assert has_permission(None, "anything", settings)


def test_legacy_disable_auth_mode_is_normalized(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "disable")

    settings = Settings()

    assert settings.auth_mode == "disabled"
