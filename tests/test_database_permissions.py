import pytest
from sqlalchemy import delete
from sqlalchemy.orm import sessionmaker

from app.auth.models import AuthenticatedUser
from app.database.base import Base
from app.database.session import create_database_engine
from app.models import PermissionMapping
from app.services.permissions import (
    DEFAULT_PERMISSIONS,
    PermissionRepository,
    PermissionServiceError,
    bootstrap_permissions,
    user_has_permission,
)


@pytest.fixture
def db(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'permissions.db'}")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session


def test_permission_bootstrap_is_idempotent_and_has_default_roles(db):
    bootstrap_permissions(db)
    bootstrap_permissions(db)
    db.commit()

    permissions = PermissionRepository(db).list_permissions()
    assert [item.name for item in permissions] == [
        "admin",
        "checkin",
        "manager",
        "police",
    ]
    assert {
        item.name: [
            (mapping.mapping_type, mapping.mapping_value) for mapping in item.mappings
        ]
        for item in permissions
    } == {name: [("role", values[2])] for name, values in DEFAULT_PERMISSIONS.items()}


def test_role_group_and_email_mappings_grant_and_normalize(db):
    bootstrap_permissions(db)
    repository = PermissionRepository(db)
    group_id = "485747E8-9FB1-481A-9830-8D7A01C60FED"
    repository.add_mapping("manager", "group", group_id, "Managers")
    email_mapping = repository.add_mapping("manager", "email", "Manager@Example.Org")
    db.commit()

    assert email_mapping.mapping_value == "manager@example.org"
    assert user_has_permission(
        db, AuthenticatedUser(roles=["Volunteer.Manager"]), "manager"
    )
    assert user_has_permission(
        db,
        AuthenticatedUser(groups=[group_id.lower()]),
        "manager",
    )
    assert user_has_permission(
        db, AuthenticatedUser(email="MANAGER@example.org"), "manager"
    )


def test_duplicate_mapping_is_rejected_without_persisting(db):
    bootstrap_permissions(db)
    repository = PermissionRepository(db)
    repository.add_mapping("manager", "email", "manager@example.org")
    db.commit()

    with pytest.raises(PermissionServiceError, match="already exists"):
        repository.add_mapping("manager", "email", "Manager@Example.Org")

    assert len(repository.list_mappings("manager")) == 2


def test_missing_empty_unmatched_and_database_failure_fail_closed(db, monkeypatch):
    bootstrap_permissions(db)
    db.execute(
        delete(PermissionMapping).where(
            PermissionMapping.permission_id
            == PermissionRepository(db).get_permission("manager").id
        )
    )
    db.commit()
    user = AuthenticatedUser(roles=["Volunteer.Manager"])

    assert not user_has_permission(db, user, "manager")
    assert not user_has_permission(db, user, "missing")
    assert not user_has_permission(db, AuthenticatedUser(), "admin")

    monkeypatch.setattr(
        PermissionRepository,
        "get_permission",
        lambda self, name: (_ for _ in ()).throw(RuntimeError("database down")),
    )
    assert not user_has_permission(db, user, "admin")


def test_admin_last_mapping_is_protected_but_one_of_two_can_be_removed(db):
    bootstrap_permissions(db)
    repository = PermissionRepository(db)
    admin = repository.get_permission("admin")

    with pytest.raises(PermissionServiceError, match="must remain"):
        repository.remove_mapping(admin.mappings[0].id)

    extra = repository.add_mapping("admin", "email", "backup@example.org")
    db.commit()
    repository.remove_mapping(extra.id)
    db.commit()
    assert len(repository.list_mappings("admin")) == 1


def test_system_permissions_cannot_be_deleted(db):
    bootstrap_permissions(db)

    with pytest.raises(PermissionServiceError, match="cannot be deleted"):
        PermissionRepository(db).delete_permission("admin")
    with pytest.raises(PermissionServiceError, match="cannot be deleted"):
        PermissionRepository(db).delete_permission("manager")
