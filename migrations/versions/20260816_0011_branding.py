"""database backed application branding

Revision ID: 20260816_0011
Revises: 20260816_0010
"""

from collections.abc import Sequence
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision: str = "20260816_0011"
down_revision: str | None = "20260816_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "branding_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("logo_url", sa.String(length=2048)),
        sa.Column("logo_data", sa.LargeBinary()),
        sa.Column("logo_content_type", sa.String(length=80)),
        sa.Column("logo_filename", sa.String(length=255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_branding_settings_singleton"),
        sa.CheckConstraint(
            "NOT (logo_url IS NOT NULL AND logo_data IS NOT NULL)",
            name="ck_branding_logo_source",
        ),
    )
    now = datetime.now(timezone.utc)
    op.get_bind().execute(
        sa.text(
            "INSERT INTO branding_settings (id, created_at, updated_at) "
            "VALUES (:id, :created_at, :updated_at)"
        ),
        {"id": 1, "created_at": now, "updated_at": now},
    )


def downgrade() -> None:
    op.drop_table("branding_settings")
