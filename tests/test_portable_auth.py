import asyncio
import json

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import sessionmaker

from app.auth.models import AuthenticatedUser
from app.auth.oidc import (
    _oauth_client,
    begin_login,
    claim_values,
    clear_login,
    complete_login,
    get_oidc_user,
    oidc_diagnostics,
    user_from_claims,
)
from app.auth.provider import get_current_user
from app.config.settings import Settings, get_settings
from app.database.base import Base
from app.database.session import create_database_engine
from app.main import app
from app.services.permissions import (
    PermissionRepository,
    bootstrap_permissions,
    user_has_permission,
)


def _configure_oidc(monkeypatch, debug: bool = False) -> None:
    values = {
        "AUTH_MODE": "oidc",
        "DEBUG": "true" if debug else "false",
        "OIDC_ISSUER_URL": "https://identity.example.org/realms/volunteers",
        "OIDC_CLIENT_ID": "volunteer-manager",
        "OIDC_CLIENT_SECRET": "test-client-secret",
        "SESSION_SECRET": "test-session-secret",
        "OIDC_ROLE_CLAIMS": "roles,realm_access.roles",
        "OIDC_GROUP_CLAIMS": "groups",
        "APP_BASE_URL": "https://volunteer.example.org",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


def _request(session: dict | None = None) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/auth/callback",
            "raw_path": b"/auth/callback",
            "query_string": b"code=code&state=state",
            "headers": [],
            "scheme": "https",
            "server": ("volunteer.example.org", 443),
            "client": ("127.0.0.1", 1234),
            "session": session if session is not None else {},
        }
    )


def test_oidc_claim_mapping_supports_nested_paths_and_normalizes_values():
    settings = Settings(
        auth_mode="oidc",
        oidc_issuer_url="https://identity.example.org/issuer",
        oidc_client_id="client",
        oidc_client_secret="secret",
        session_secret="session-secret",
        oidc_role_claims="roles,realm_access.roles",
        oidc_group_claims="groups",
    )
    user = user_from_claims(
        {
            "sub": "user-1",
            "name": "Admin User",
            "email": "admin@example.org",
            "preferred_username": "fallback@example.org",
            "roles": [" Volunteer.Admin ", ""],
            "realm_access": {"roles": ["Volunteer.Admin", "Event.Manager"]},
            "groups": [" group-b ", "group-a", None],
        },
        settings,
    )

    assert user.user_id == "user-1"
    assert user.email == "admin@example.org"
    assert user.roles == ["Event.Manager", "Volunteer.Admin"]
    assert user.groups == ["group-a", "group-b"]
    assert claim_values({"bad": {"roles": {"nested": True}}}, ["bad.roles"]) == []


def test_oidc_discovery_and_pkce_configuration(monkeypatch):
    registered = {}
    marker = object()

    class FakeOAuth:
        def register(self, **kwargs):
            registered.update(kwargs)

        def create_client(self, name):
            assert name == "oidc"
            return marker

    monkeypatch.setattr("app.auth.oidc.OAuth", FakeOAuth)
    settings = Settings(
        auth_mode="oidc",
        oidc_issuer_url="https://identity.example.org/issuer/",
        oidc_client_id="client",
        oidc_client_secret="secret",
        session_secret="session-secret",
    )

    assert _oauth_client(settings) is marker
    assert registered["server_metadata_url"] == (
        "https://identity.example.org/issuer/.well-known/openid-configuration"
    )
    assert registered["client_kwargs"]["code_challenge_method"] == "S256"


def test_oidc_login_uses_app_base_url_nonce_and_pkce(monkeypatch):
    _configure_oidc(monkeypatch)
    captured = {}

    class FakeClient:
        async def authorize_redirect(self, request, callback_url, **kwargs):
            captured.update(callback_url=callback_url, **kwargs)
            return "redirect"

    monkeypatch.setattr("app.auth.oidc._oauth_client", lambda settings: FakeClient())
    request = _request({})

    assert asyncio.run(begin_login(request)) == "redirect"
    assert captured["callback_url"] == "https://volunteer.example.org/auth/callback"
    assert captured["nonce"]
    assert captured["code_verifier"]
    assert list(request.session) == ["oidc_transaction_id"]
    assert captured["nonce"] not in json.dumps(request.session)
    assert captured["code_verifier"] not in json.dumps(request.session)


def test_oidc_callback_validates_nonce_and_stores_only_opaque_session(monkeypatch):
    _configure_oidc(monkeypatch)
    captured = {}

    class FakeClient:
        async def authorize_redirect(self, request, callback_url, **kwargs):
            captured["login_nonce"] = kwargs["nonce"]
            captured["login_verifier"] = kwargs["code_verifier"]
            request.session["_state_oidc_state"] = {"data": "state"}
            return "redirect"

        async def authorize_access_token(self, request, **kwargs):
            captured["code_verifier"] = kwargs["code_verifier"]
            return {"id_token": "secret-id-token", "access_token": "secret-token"}

        async def parse_id_token(self, token, nonce):
            captured["nonce"] = nonce
            return {
                "sub": "oidc-user",
                "name": "OIDC User",
                "roles": ["Volunteer.Admin"],
            }

    monkeypatch.setattr("app.auth.oidc._oauth_client", lambda settings: FakeClient())
    request = _request({})
    asyncio.run(begin_login(request))

    user = asyncio.run(complete_login(request))

    assert user.user_id == "oidc-user"
    assert captured["code_verifier"] == captured["login_verifier"]
    assert captured["nonce"] == captured["login_nonce"]
    assert list(request.session) == ["oidc_session_id"]
    assert "secret" not in json.dumps(request.session)
    assert get_oidc_user(request) == user


@pytest.mark.parametrize("failure_stage", ["state", "nonce"])
def test_oidc_invalid_state_or_nonce_fails_closed(monkeypatch, failure_stage):
    _configure_oidc(monkeypatch)

    class FakeClient:
        async def authorize_redirect(self, request, callback_url, **kwargs):
            request.session["_state_oidc_state"] = {"data": "state"}
            return "redirect"

        async def authorize_access_token(self, request, **kwargs):
            if failure_stage == "state":
                raise ValueError("mismatching state")
            return {"id_token": "token"}

        async def parse_id_token(self, token, nonce):
            raise ValueError("invalid nonce")

    monkeypatch.setattr("app.auth.oidc._oauth_client", lambda settings: FakeClient())
    request = _request({})
    asyncio.run(begin_login(request))

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(complete_login(request))

    assert exc_info.value.status_code == 401
    assert request.session == {}


def test_oidc_callback_without_transaction_fails_closed(monkeypatch):
    _configure_oidc(monkeypatch)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(complete_login(_request({})))

    assert exc_info.value.status_code == 401


def test_oidc_logout_removes_server_and_cookie_session(monkeypatch):
    _configure_oidc(monkeypatch)
    request = _request({})

    class FakeClient:
        async def authorize_redirect(self, request, callback_url, **kwargs):
            request.session["_state_oidc_state"] = {"data": "state"}
            return "redirect"

        async def authorize_access_token(self, request, **kwargs):
            return {"id_token": "token"}

        async def parse_id_token(self, token, nonce):
            return {"sub": "oidc-user"}

    monkeypatch.setattr("app.auth.oidc._oauth_client", lambda settings: FakeClient())
    asyncio.run(begin_login(request))
    asyncio.run(complete_login(request))
    assert get_oidc_user(request) is not None

    clear_login(request)

    assert request.session == {}
    assert get_oidc_user(request) is None


def test_oidc_requires_complete_configuration_and_invalid_mode_fails():
    with pytest.raises(ValidationError, match="OIDC configuration is incomplete"):
        Settings(auth_mode="oidc")
    with pytest.raises(ValidationError):
        Settings(auth_mode="unknown")


def test_disabled_auth_is_rejected_in_production_without_explicit_opt_in():
    with pytest.raises(ValidationError, match="ALLOW_INSECURE_AUTH"):
        Settings(auth_mode="disabled", environment="production")


def test_same_roles_and_groups_have_identical_permission_semantics(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'permissions.db'}")
    Base.metadata.create_all(engine)
    group_id = "485747e8-9fb1-481a-9830-8d7a01c60fed"
    with sessionmaker(bind=engine)() as db:
        bootstrap_permissions(db)
        PermissionRepository(db).add_mapping("admin", "group", group_id)
        db.commit()
        for user in (
            AuthenticatedUser(roles=["Volunteer.Admin"]),
            user_from_claims(
                {"sub": "oidc", "roles": ["Volunteer.Admin"]},
                Settings(
                    auth_mode="oidc",
                    oidc_issuer_url="https://identity.example.org",
                    oidc_client_id="client",
                    oidc_client_secret="secret",
                    session_secret="session",
                ),
            ),
        ):
            assert user_has_permission(db, user, "admin")
        for user in (
            AuthenticatedUser(groups=[group_id]),
            user_from_claims(
                {"sub": "oidc", "groups": [group_id]},
                Settings(
                    auth_mode="oidc",
                    oidc_issuer_url="https://identity.example.org",
                    oidc_client_id="client",
                    oidc_client_secret="secret",
                    session_secret="session",
                ),
            ),
        ):
            assert user_has_permission(db, user, "admin")


def test_oidc_debug_never_exposes_secret_or_tokens(monkeypatch):
    _configure_oidc(monkeypatch, debug=True)
    response = TestClient(app).get("/debug/oidc")

    assert response.status_code == 200
    body = json.dumps(response.json())
    assert "test-client-secret" not in body
    assert "access_token" not in body
    assert response.json()["configured"] is True


def test_debug_oidc_is_404_when_disabled(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "disabled")
    monkeypatch.setenv("DEBUG", "false")
    get_settings.cache_clear()

    assert TestClient(app).get("/debug/oidc").status_code == 404


def test_provider_selects_disabled_easyauth_and_oidc(monkeypatch):
    request = _request({})

    monkeypatch.setenv("AUTH_MODE", "disabled")
    get_settings.cache_clear()
    assert get_current_user(request).name == "Local Developer"

    monkeypatch.setenv("AUTH_MODE", "easyauth")
    get_settings.cache_clear()
    assert get_current_user(request) is None

    _configure_oidc(monkeypatch)
    user = AuthenticatedUser(user_id="oidc-user", name="OIDC User")
    monkeypatch.setattr("app.auth.provider.get_oidc_user", lambda request: user)
    assert get_current_user(request) == user


def test_oidc_diagnostics_marks_other_auth_modes_not_applicable(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "easyauth")
    get_settings.cache_clear()

    diagnostics = oidc_diagnostics(_request({}))

    assert diagnostics["configured"] is False
    assert diagnostics["issuer"] is None
