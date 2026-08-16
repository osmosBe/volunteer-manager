import base64
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.config.settings import get_settings
from app.database.base import Base
from app.database.session import create_database_engine, get_db
from app.main import app
from app.models import AuditLog
from app.services.permissions import PermissionRepository, bootstrap_permissions


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
def permission_app(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_MODE", "easyauth")
    monkeypatch.setenv("DEBUG", "false")
    get_settings.cache_clear()
    engine = create_database_engine(f"sqlite:///{tmp_path / 'routes.db'}")
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


def test_admin_can_view_add_update_and_remove_mappings_with_audit(permission_app):
    client, Session = permission_app
    headers = _role_header("Volunteer.Admin")

    page = client.get("/admin/permissions", headers=headers)
    assert page.status_code == 200
    assert "Berechtigungen" in page.text
    assert 'value.removeAttribute("pattern")' in page.text
    assert 'value.pattern = type.value === "group"' not in page.text

    additions = [
        ("role", "Volunteer.SecondAdmin"),
        ("group", "485747e8-9fb1-481a-9830-8d7a01c60fed"),
        ("email", "Backup.Admin@Example.Org"),
    ]
    for mapping_type, mapping_value in additions:
        response = client.post(
            "/admin/permissions/admin/mappings",
            headers=headers,
            data={
                "mapping_type": mapping_type,
                "mapping_value": mapping_value,
                "display_label": "Backup",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303

    with Session() as db:
        repository = PermissionRepository(db)
        email_mapping = next(
            item
            for item in repository.list_mappings("admin")
            if item.mapping_type == "email"
        )
        assert email_mapping.mapping_value == "backup.admin@example.org"
        mapping_id = email_mapping.id
        actions = list(db.scalars(select(AuditLog.action)))
        assert actions.count("permission.mapping.add") == 3

    response = client.post(
        f"/admin/permissions/mappings/{mapping_id}",
        headers=headers,
        data={"display_label": "Emergency admin"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    response = client.post(
        f"/admin/permissions/mappings/{mapping_id}/remove",
        headers=headers,
        follow_redirects=False,
    )
    assert response.status_code == 303

    with Session() as db:
        actions = list(db.scalars(select(AuditLog.action)))
        assert "permission.mapping.update" in actions
        assert "permission.mapping.remove" in actions


@pytest.mark.parametrize(
    ("mapping_type", "mapping_value"),
    [
        ("email", "not-an-email"),
        ("group", "not-a-uuid"),
        ("role", ""),
        ("unsupported", "value"),
    ],
)
def test_invalid_mapping_is_rejected_without_database_change(
    permission_app, mapping_type, mapping_value
):
    client, Session = permission_app
    headers = _role_header("Volunteer.Admin")
    with Session() as db:
        before = len(PermissionRepository(db).list_mappings("manager"))

    response = client.post(
        "/admin/permissions/manager/mappings",
        headers=headers,
        data={"mapping_type": mapping_type, "mapping_value": mapping_value},
    )

    assert response.status_code == 422
    assert "alert-danger" in response.text
    with Session() as db:
        assert len(PermissionRepository(db).list_mappings("manager")) == before


def test_duplicate_mapping_is_rejected_without_database_change(permission_app):
    client, Session = permission_app
    headers = _role_header("Volunteer.Admin")

    response = client.post(
        "/admin/permissions/admin/mappings",
        headers=headers,
        data={"mapping_type": "role", "mapping_value": "Volunteer.Admin"},
    )

    assert response.status_code == 422
    with Session() as db:
        assert len(PermissionRepository(db).list_mappings("admin")) == 1


@pytest.mark.parametrize(
    "role", ["Volunteer.Manager", "Volunteer.CheckIn", "Volunteer.Police"]
)
def test_non_admin_permissions_cannot_manage_permission_mappings(permission_app, role):
    client, _ = permission_app
    headers = _role_header(role)

    assert client.get("/admin/permissions", headers=headers).status_code == 403
    assert (
        client.post(
            "/admin/permissions/admin/mappings",
            headers=headers,
            data={"mapping_type": "role", "mapping_value": "Unauthorized"},
        ).status_code
        == 403
    )


def test_route_permission_matrix(permission_app):
    client, _ = permission_app
    admin = _role_header("Volunteer.Admin")
    manager = _role_header("Volunteer.Manager")
    checkin = _role_header("Volunteer.CheckIn")
    police = _role_header("Volunteer.Police")

    for path in (
        "/admin",
        "/admin/check-in",
        "/admin/einstellungen/smtp",
        "/admin/branding",
        "/admin/permissions",
        "/admin/db",
        "/admin/mail",
    ):
        assert client.get(path, headers=admin).status_code == 200, path
    admin_dashboard = client.get("/admin", headers=admin)
    assert 'href="/admin/permissions"' in admin_dashboard.text
    assert 'href="/admin/db"' in admin_dashboard.text
    assert 'href="/admin/branding"' in admin_dashboard.text

    manager_dashboard = client.get("/admin", headers=manager)
    assert manager_dashboard.status_code == 200
    assert "/admin/permissions" not in manager_dashboard.text
    assert "/admin/einstellungen/smtp" not in manager_dashboard.text
    assert "/admin/branding" not in manager_dashboard.text
    assert "/admin/db" not in manager_dashboard.text
    assert client.get("/admin/check-in", headers=manager).status_code == 200
    assert client.get("/admin/mail", headers=manager).status_code == 200
    for path in (
        "/admin/einstellungen/smtp",
        "/admin/branding",
        "/admin/permissions",
        "/admin/db",
    ):
        assert client.get(path, headers=manager).status_code == 403, path

    assert client.get("/admin/check-in", headers=checkin).status_code == 200
    for path in ("/admin", "/admin/mail", "/admin/permissions", "/admin/db"):
        assert client.get(path, headers=checkin).status_code == 403, path

    for path in ("/admin", "/admin/check-in", "/admin/mail", "/admin/db"):
        assert client.get(path, headers=police).status_code == 403, path


@pytest.mark.parametrize(
    ("role", "expected_location"),
    [
        ("Volunteer.Admin", "/admin"),
        ("Volunteer.Manager", "/admin"),
        ("Volunteer.CheckIn", "/admin/check-in"),
        ("Volunteer.Police", "/"),
    ],
)
def test_post_login_redirects_to_authorized_landing(
    permission_app, role, expected_location
):
    client, _ = permission_app

    response = client.get(
        "/auth/post-login",
        headers=_role_header(role),
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == expected_location
