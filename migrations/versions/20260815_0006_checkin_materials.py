"""track structured materials issued during check-in

Revision ID: 20260815_0006
Revises: 20260815_0005
Create Date: 2026-08-15 15:25:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260815_0006"
down_revision: str | None = "20260815_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "checkin_materials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "checkin_id",
            sa.Integer(),
            sa.ForeignKey("checkins.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "material_id",
            sa.Integer(),
            sa.ForeignKey("team_materials.id"),
            nullable=False,
        ),
        sa.Column("quantity_issued", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("return_required", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("returned_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("checkin_id", "material_id", name="uq_checkin_material"),
    )
    op.create_index(
        "ix_checkin_materials_checkin_id", "checkin_materials", ["checkin_id"]
    )
    op.create_index(
        "ix_checkin_materials_material_id", "checkin_materials", ["material_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_checkin_materials_material_id", table_name="checkin_materials")
    op.drop_index("ix_checkin_materials_checkin_id", table_name="checkin_materials")
    op.drop_table("checkin_materials")
