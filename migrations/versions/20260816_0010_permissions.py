"""database backed permission mappings

Revision ID: 20260816_0010
Revises: 20260815_0009
"""

from collections.abc import Sequence
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision: str = "20260816_0010"
down_revision: str | None = "20260815_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULTS = [
    ("admin", "Administration", "Full application administration", "Volunteer.Admin"),
    (
        "manager",
        "Volunteer management",
        "Operational volunteer management",
        "Volunteer.Manager",
    ),
    (
        "checkin",
        "Check-in",
        "Restricted event-day check-in access",
        "Volunteer.CheckIn",
    ),
    (
        "police",
        "Police workflow",
        "Restricted police-clearance workflow",
        "Volunteer.Police",
    ),
]


def upgrade() -> None:
    op.create_table(
        "permissions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=80), nullable=False, unique=True),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_permissions_name", "permissions", ["name"])
    op.create_table(
        "permission_mappings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("permission_id", sa.Integer(), nullable=False),
        sa.Column("mapping_type", sa.String(length=16), nullable=False),
        sa.Column("mapping_value", sa.String(length=255), nullable=False),
        sa.Column("display_label", sa.String(length=255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["permission_id"], ["permissions.id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            "mapping_type IN ('role', 'group', 'email')",
            name="ck_permission_mapping_type",
        ),
        sa.UniqueConstraint(
            "permission_id",
            "mapping_type",
            "mapping_value",
            name="uq_permission_mapping_value",
        ),
    )
    op.create_index(
        "ix_permission_mappings_permission_id", "permission_mappings", ["permission_id"]
    )
    bind = op.get_bind()
    now = datetime.now(timezone.utc)
    for name, display_name, description, role in DEFAULTS:
        result = bind.execute(
            sa.text("SELECT id FROM permissions WHERE name = :name"), {"name": name}
        ).first()
        if result is None:
            bind.execute(
                sa.text(
                    "INSERT INTO permissions "
                    "(name, display_name, description, is_system, "
                    "created_at, updated_at) "
                    "VALUES (:name, :display_name, :description, :is_system, "
                    ":created_at, :updated_at)"
                ),
                {
                    "name": name,
                    "display_name": display_name,
                    "description": description,
                    "is_system": True,
                    "created_at": now,
                    "updated_at": now,
                },
            )
            # Query through the unique logical key instead of relying on
            # cursor.lastrowid, which is not available with psycopg 3.
            permission_id = bind.execute(
                sa.text("SELECT id FROM permissions WHERE name = :name"),
                {"name": name},
            ).scalar_one()
            bind.execute(
                sa.text(
                    "INSERT INTO permission_mappings "
                    "(permission_id, mapping_type, mapping_value, "
                    "created_at, updated_at) "
                    "VALUES (:permission_id, 'role', :role, :created_at, :updated_at)"
                ),
                {
                    "permission_id": permission_id,
                    "role": role,
                    "created_at": now,
                    "updated_at": now,
                },
            )


def downgrade() -> None:
    op.drop_index(
        "ix_permission_mappings_permission_id", table_name="permission_mappings"
    )
    op.drop_table("permission_mappings")
    op.drop_index("ix_permissions_name", table_name="permissions")
    op.drop_table("permissions")
