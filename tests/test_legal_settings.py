import base64
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config.settings import get_settings
from app.database.base import Base
from app.database.session import (
    get_engine,
    get_session_factory,
    reset_database_engine,
)
from app.main import app
from app.models import AuditLog, LegalSettings
from app.services.legal_settings import (
    LegalSettingsError,
    LegalSettingsRepository,
    validate_legal_links,
    validate_legal_url,
)
from app.services.permissions import bootstrap_permissions


def _role_header(role: str) -> dict[str, str]:
    principal = base64.b64encode(
        json.dumps(
            {
                "claims": [
                    {"typ": "oid", "val": f"user-{role}"},
                    {"typ": "name", "val": role},
                    {"typ": "roles", "val": role},
                ]
            }
        ).encode()
    ).decode()
    return {"x-ms-client-principal": principal}


@pytest.fixture
def legal_app(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_MODE", "easyauth")
    monkeypatch.setenv("DEBUG", "false")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'legal.db'}")
    get_settings.cache_clear()
    reset_database_engine()
    engine = get_engine()
    Base.metadata.create_all(engine)
    Session = get_session_factory()
    with Session() as db:
        bootstrap_permissions(db)
        db.commit()
    try:
        yield TestClient(app), Session
    finally:
        get_settings.cache_clear()
        reset_database_engine()


def test_new_installation_hides_unconfigured_legal_footer(legal_app):
    client, _ = legal_app

    public_page = client.get("/")
    admin_page = client.get(
        "/admin/einstellungen/rechtliches",
        headers=_role_header("Volunteer.Admin"),
    )

    assert public_page.status_code == 200
    assert 'aria-label="Rechtliche Hinweise"' not in public_page.text
    assert admin_page.status_code == 200
    assert "Rechtliche Links" in admin_page.text
    assert 'name="privacy_url"' in admin_page.text
    assert 'name="imprint_url"' in admin_page.text


def test_admin_can_persist_and_render_https_and_relative_links_with_safe_audit(
    legal_app,
):
    client, Session = legal_app
    privacy_url = "https://legal.example.org/privacy?tenant=public"
    imprint_url = "/impressum"

    response = client.post(
        "/admin/einstellungen/rechtliches",
        headers=_role_header("Volunteer.Admin"),
        data={"privacy_url": privacy_url, "imprint_url": imprint_url},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/einstellungen/rechtliches"
    for path in ("/", "/admin", "/missing-page"):
        page = client.get(path, headers=_role_header("Volunteer.Admin"))
        assert f'href="{privacy_url.replace("&", "&amp;")}"' in page.text
        assert f'href="{imprint_url}"' in page.text
        assert page.text.count(">Datenschutz</a>") == 1
        assert page.text.count(">Impressum</a>") == 1

    with Session() as db:
        settings = db.get(LegalSettings, 1)
        assert settings.privacy_url == privacy_url
        assert settings.imprint_url == imprint_url
        audit = db.scalar(
            select(AuditLog).where(AuditLog.action == "legal.links.updated")
        )
        assert audit is not None
        assert json.loads(audit.metadata_json) == {
            "privacy_target_type": "https",
            "imprint_target_type": "relative",
        }
        assert privacy_url not in audit.metadata_json


def test_admin_can_clear_one_or_both_legal_links(legal_app):
    client, Session = legal_app
    headers = _role_header("Volunteer.Admin")
    repository_session = Session()
    try:
        LegalSettingsRepository(repository_session).update(
            "https://example.org/privacy", "/impressum"
        )
        repository_session.commit()
    finally:
        repository_session.close()

    response = client.post(
        "/admin/einstellungen/rechtliches",
        headers=headers,
        data={"privacy_url": "", "imprint_url": "/impressum"},
        follow_redirects=False,
    )
    page = client.get("/")

    assert response.status_code == 303
    assert ">Datenschutz</a>" not in page.text
    assert ">Impressum</a>" in page.text

    response = client.post(
        "/admin/einstellungen/rechtliches",
        headers=headers,
        data={"privacy_url": "", "imprint_url": ""},
        follow_redirects=False,
    )
    page = client.get("/")

    assert response.status_code == 303
    assert 'aria-label="Rechtliche Hinweise"' not in page.text


@pytest.mark.parametrize(
    "role", ["Volunteer.Manager", "Volunteer.CheckIn", "Volunteer.Police"]
)
def test_non_admin_permissions_cannot_view_or_change_legal_links(legal_app, role):
    client, Session = legal_app
    headers = _role_header(role)

    assert (
        client.get("/admin/einstellungen/rechtliches", headers=headers).status_code
        == 403
    )
    assert (
        client.post(
            "/admin/einstellungen/rechtliches",
            headers=headers,
            data={"privacy_url": "https://example.org/privacy"},
        ).status_code
        == 403
    )
    with Session() as db:
        assert db.get(LegalSettings, 1) is None


@pytest.mark.parametrize(
    "invalid_url",
    [
        "http://example.org/privacy",
        "javascript:alert(1)",
        "//example.org/imprint",
        "relative/privacy",
        "https://user:secret@example.org/privacy",
        "https://example.org:invalid/privacy",
        "https://[::1",
        "https://example.org/privacy%",
    ],
)
def test_unsafe_or_malformed_links_are_rejected_without_database_change(
    legal_app, invalid_url
):
    client, Session = legal_app
    with Session() as db:
        LegalSettingsRepository(db).update("/existing-privacy", "/existing-imprint")
        db.commit()

    response = client.post(
        "/admin/einstellungen/rechtliches",
        headers=_role_header("Volunteer.Admin"),
        data={"privacy_url": invalid_url, "imprint_url": "/changed-imprint"},
    )

    assert response.status_code == 422
    assert "Bitte überprüfe deine Eingaben" in response.text
    assert 'data-error-for="privacy_url"' in response.text
    with Session() as db:
        settings = db.get(LegalSettings, 1)
        assert settings.privacy_url == "/existing-privacy"
        assert settings.imprint_url == "/existing-imprint"
        assert (
            db.scalar(select(AuditLog).where(AuditLog.action == "legal.links.updated"))
            is None
        )


def test_validation_reports_both_invalid_fields_and_normalizes_whitespace():
    with pytest.raises(LegalSettingsError) as error:
        validate_legal_links("http://example.org", "mailto:hello@example.org")
    assert set(error.value.field_errors) == {"privacy_url", "imprint_url"}
    assert validate_legal_url("  /privacy?lang=de  ") == "/privacy?lang=de"
    assert validate_legal_url("   ") is None


@pytest.mark.parametrize("value", ["/privacy\\evil", "/privacy%0d%0aheader"])
def test_encoded_or_backslash_control_paths_are_rejected(value):
    with pytest.raises(ValueError):
        validate_legal_url(value)


def test_footer_fails_safe_when_database_query_fails(legal_app, monkeypatch):
    client, _ = legal_app

    def fail_query(repository):
        del repository
        raise RuntimeError("database unavailable")

    monkeypatch.setattr("app.template_engine.LegalSettingsRepository.get", fail_query)

    response = client.get("/")

    assert response.status_code == 200
    assert 'aria-label="Rechtliche Hinweise"' not in response.text
