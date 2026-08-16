import base64
import json
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from app.config.settings import get_settings
from app.database.base import Base
from app.database.session import create_database_engine, get_db
from app.main import app
from app.models import AuditLog, BrandingSettings
from app.services.branding import DEFAULT_LOGO_URL, MAX_LOGO_BYTES
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


def _png(width: int = 240, height: int = 120) -> bytes:
    output = BytesIO()
    Image.new("RGBA", (width, height), (136, 64, 255, 220)).save(output, "PNG")
    return output.getvalue()


def _jpeg(width: int = 320, height: int = 160) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), (136, 64, 255)).save(output, "JPEG")
    return output.getvalue()


@pytest.fixture
def branding_app(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_MODE", "easyauth")
    monkeypatch.setenv("DEBUG", "false")
    get_settings.cache_clear()
    engine = create_database_engine(f"sqlite:///{tmp_path / 'branding.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        bootstrap_permissions(db)
        db.commit()

    def override_get_db():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app), Session
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def test_default_logo_and_admin_branding_page(branding_app):
    client, _ = branding_app
    headers = _role_header("Volunteer.Admin")

    logo = client.get("/branding/logo", follow_redirects=False)
    page = client.get("/admin/branding", headers=headers)
    dashboard = client.get("/admin", headers=headers)

    assert logo.status_code == 307
    assert logo.headers["location"] == DEFAULT_LOGO_URL
    assert logo.headers["cache-control"] == "no-store"
    assert page.status_code == 200
    assert "Mitgeliefertes Standardlogo" in page.text
    assert 'src="/branding/logo"' in page.text
    assert DEFAULT_LOGO_URL in page.text
    assert 'href="/admin/branding"' in dashboard.text


def test_bundled_logo_uses_neutral_path_and_is_served(branding_app):
    client, _ = branding_app

    logo = client.get(DEFAULT_LOGO_URL)
    page = client.get("/")

    assert DEFAULT_LOGO_URL == "/static/images/logo.png"
    assert logo.status_code == 200
    assert logo.headers["content-type"] == "image/png"
    assert DEFAULT_LOGO_URL in page.text
    assert "Volunteer Manager" in page.text


def test_repository_contains_no_legacy_organization_branding():
    root = Path(__file__).resolve().parents[1]
    forbidden = (
        bytes((112, 114, 105, 100, 101)).decode("ascii"),
        bytes((115, 116, 112, 114, 105, 100, 101)).decode("ascii"),
        bytes((115, 116, 45, 112, 114, 105, 100, 101)).decode("ascii"),
    )
    suffixes = {
        ".example",
        ".html",
        ".json",
        ".md",
        ".ps1",
        ".py",
        ".toml",
        ".yaml",
        ".yml",
    }
    paths = [root / "LICENSE"]
    paths.extend(
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix in suffixes
        and ".git" not in path.parts
        and "__pycache__" not in path.parts
        and ".venv" not in path.parts
        and "venv" not in path.parts
        and "node_modules" not in path.parts
    )

    for path in paths:
        content = path.read_text(encoding="utf-8").casefold()
        for value in forbidden:
            assert value not in content, f"legacy branding remains in {path}"


def test_logo_delivery_falls_back_when_branding_database_query_fails(
    branding_app, monkeypatch
):
    client, _ = branding_app

    def fail_query(repository):
        del repository
        raise SQLAlchemyError("database unavailable")

    monkeypatch.setattr("app.api.admin_branding.BrandingRepository.get", fail_query)

    response = client.get("/branding/logo", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == DEFAULT_LOGO_URL


def test_admin_can_set_remote_logo_url_with_safe_audit(branding_app):
    client, Session = branding_app
    headers = _role_header("Volunteer.Admin")
    remote_url = "https://cdn.example.org/branding/event-logo.svg?version=2"

    response = client.post(
        "/admin/branding/url",
        headers=headers,
        data={"logo_url": remote_url},
        follow_redirects=False,
    )
    logo = client.get("/branding/logo", follow_redirects=False)

    assert response.status_code == 303
    assert logo.status_code == 307
    assert logo.headers["location"] == remote_url
    assert logo.headers["referrer-policy"] == "no-referrer"
    page = client.get("/admin/branding", headers=headers)
    assert "Logo-URL" in page.text
    assert "this.onerror=null" in page.text
    with Session() as db:
        settings = db.get(BrandingSettings, 1)
        assert settings.logo_url == remote_url
        assert settings.logo_data is None
        audit = db.scalar(
            select(AuditLog).where(AuditLog.action == "branding.logo.url_set")
        )
        assert audit is not None
        assert json.loads(audit.metadata_json) == {
            "source": "url",
            "host": "cdn.example.org",
        }
        assert "version=2" not in audit.metadata_json


@pytest.mark.parametrize(
    "invalid_url",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "http://localhost/logo.png",
        "http://127.0.0.1/logo.png",
        "https://user:secret@example.org/logo.png",
    ],
)
def test_invalid_logo_urls_are_rejected_without_database_change(
    branding_app, invalid_url
):
    client, Session = branding_app

    response = client.post(
        "/admin/branding/url",
        headers=_role_header("Volunteer.Admin"),
        data={"logo_url": invalid_url},
    )

    assert response.status_code == 422
    assert "alert-danger" in response.text
    with Session() as db:
        assert db.get(BrandingSettings, 1) is None


def test_admin_can_upload_resized_sanitized_png_and_reset(branding_app):
    client, Session = branding_app
    headers = _role_header("Volunteer.Admin")

    response = client.post(
        "/admin/branding/upload",
        headers=headers,
        files={"logo_file": ("large-logo.png", _png(2000, 1000), "image/png")},
        follow_redirects=False,
    )
    logo = client.get("/branding/logo")

    assert response.status_code == 303
    assert logo.status_code == 200
    assert logo.headers["content-type"] == "image/png"
    assert logo.headers["x-content-type-options"] == "nosniff"
    assert logo.headers["cache-control"] == "no-store"
    with Image.open(BytesIO(logo.content)) as image:
        assert image.size == (1600, 800)
        assert image.getexif() == {}
    with Session() as db:
        settings = db.get(BrandingSettings, 1)
        assert settings.logo_url is None
        assert settings.logo_filename == "large-logo.png"
        audit = db.scalar(
            select(AuditLog).where(AuditLog.action == "branding.logo.uploaded")
        )
        metadata = json.loads(audit.metadata_json)
        assert metadata["content_type"] == "image/png"
        assert metadata["size_bytes"] == len(settings.logo_data)

    reset = client.post(
        "/admin/branding/reset", headers=headers, follow_redirects=False
    )
    fallback = client.get("/branding/logo", follow_redirects=False)

    assert reset.status_code == 303
    assert fallback.status_code == 307
    assert fallback.headers["location"] == DEFAULT_LOGO_URL
    with Session() as db:
        settings = db.get(BrandingSettings, 1)
        assert settings.logo_data is None
        assert db.scalar(
            select(AuditLog).where(AuditLog.action == "branding.logo.reset")
        )


def test_safe_svg_upload_is_sanitized_and_served_with_restrictions(branding_app):
    client, _ = branding_app
    svg = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 200">
      <rect width="400" height="200" fill="#7c3aed"/>
    </svg>"""

    response = client.post(
        "/admin/branding/upload",
        headers=_role_header("Volunteer.Admin"),
        files={"logo_file": ("logo.svg", svg, "image/svg+xml")},
        follow_redirects=False,
    )
    logo = client.get("/branding/logo")

    assert response.status_code == 303
    assert logo.headers["content-type"] == "image/svg+xml"
    assert "default-src 'none'" in logo.headers["content-security-policy"]
    assert b"<script" not in logo.content.lower()
    assert b"<svg" in logo.content


def test_jpeg_upload_is_decoded_and_reencoded(branding_app):
    client, _ = branding_app

    response = client.post(
        "/admin/branding/upload",
        headers=_role_header("Volunteer.Admin"),
        files={"logo_file": ("logo.jpeg", _jpeg(), "image/jpeg")},
        follow_redirects=False,
    )
    logo = client.get("/branding/logo")

    assert response.status_code == 303
    assert logo.headers["content-type"] == "image/jpeg"
    with Image.open(BytesIO(logo.content)) as image:
        assert image.format == "JPEG"
        assert image.size == (320, 160)


@pytest.mark.parametrize(
    ("filename", "content", "content_type", "expected"),
    [
        ("logo.txt", b"not an image", "text/plain", "PNG-, JPEG- und SVG"),
        ("fake.png", b"not a png", "image/png", "ungültig"),
        (
            "active.svg",
            b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>',
            "image/svg+xml",
            "aktive Inhalte",
        ),
        (
            "external.svg",
            b'<svg xmlns="http://www.w3.org/2000/svg"><image href="https://example.org/x.png"/></svg>',
            "image/svg+xml",
            "Externe SVG-Ressourcen",
        ),
        (
            "external-fill.svg",
            b'<svg xmlns="http://www.w3.org/2000/svg"><rect fill="url(https://example.org/x.svg)"/></svg>',
            "image/svg+xml",
            "Externe SVG-Ressourcen",
        ),
        ("huge.png", b"x" * (MAX_LOGO_BYTES + 1), "image/png", "maximal 2 MB"),
    ],
)
def test_unsafe_or_invalid_uploads_are_rejected(
    branding_app, filename, content, content_type, expected
):
    client, Session = branding_app

    response = client.post(
        "/admin/branding/upload",
        headers=_role_header("Volunteer.Admin"),
        files={"logo_file": (filename, content, content_type)},
    )

    assert response.status_code == 422
    assert expected in response.text
    with Session() as db:
        assert db.get(BrandingSettings, 1) is None


def test_failed_upload_preserves_previous_remote_url(branding_app):
    client, Session = branding_app
    headers = _role_header("Volunteer.Admin")
    remote_url = "https://cdn.example.org/logo.png"
    assert (
        client.post(
            "/admin/branding/url", headers=headers, data={"logo_url": remote_url}
        ).status_code
        == 200
    )

    response = client.post(
        "/admin/branding/upload",
        headers=headers,
        files={"logo_file": ("bad.png", b"bad", "image/png")},
    )

    assert response.status_code == 422
    with Session() as db:
        settings = db.get(BrandingSettings, 1)
        assert settings.logo_url == remote_url
        assert settings.logo_data is None


@pytest.mark.parametrize(
    "role", ["Volunteer.Manager", "Volunteer.CheckIn", "Volunteer.Police"]
)
def test_non_admins_cannot_manage_branding(branding_app, role):
    client, _ = branding_app
    headers = _role_header(role)

    assert client.get("/admin/branding", headers=headers).status_code == 403
    assert (
        client.post(
            "/admin/branding/url",
            headers=headers,
            data={"logo_url": "https://cdn.example.org/logo.png"},
        ).status_code
        == 403
    )
    assert client.post("/admin/branding/reset", headers=headers).status_code == 403
