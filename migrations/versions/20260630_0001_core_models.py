"""create core volunteer management tables

Revision ID: 20260630_0001
Revises:
Create Date: 2026-06-30 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260630_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True)),
        sa.Column("ends_at", sa.DateTime(timezone=True)),
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "active",
                "closed",
                "archived",
                name="eventstatus",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("slug"),
    )
    op.create_index(op.f("ix_events_slug"), "events", ["slug"])

    op.create_table(
        "blocks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("first_name", sa.String(length=100)),
        sa.Column("last_name", sa.String(length=100)),
        sa.Column("birth_date", sa.Date()),
        sa.Column("email_hash", sa.String(length=128)),
        sa.Column(
            "block_category",
            sa.Enum(
                "security",
                "reliability",
                "boundary_violation",
                "house_rules",
                "other",
                name="blockcategory",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "block_status",
            sa.Enum(
                "active",
                "under_review",
                "expired",
                "revoked",
                name="blockstatus",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("review_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
    )
    op.create_index(op.f("ix_blocks_email_hash"), "blocks", ["email_hash"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("actor_user_id", sa.String(length=255)),
        sa.Column("actor_email", sa.String(length=255)),
        sa.Column("actor_name", sa.String(length=255)),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column("entity_type", sa.String(length=120), nullable=False),
        sa.Column("entity_id", sa.String(length=120)),
        sa.Column("metadata_json", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "volunteers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("first_name", sa.String(length=100), nullable=False),
        sa.Column("last_name", sa.String(length=100), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("email_normalized", sa.String(length=255), nullable=False),
        sa.Column("email_hash", sa.String(length=128), nullable=False),
        sa.Column("phone", sa.String(length=50)),
        sa.Column(
            "age_group",
            sa.Enum(
                "under_16", "age_16_17", "adult", name="agegroup", native_enum=False
            ),
            nullable=False,
        ),
        sa.Column("birth_date", sa.Date()),
        sa.Column("birth_date_verified", sa.Boolean(), nullable=False),
        sa.Column("food_choice", sa.String(length=100)),
        sa.Column(
            "status",
            sa.Enum(
                "submitted",
                "needs_more_info",
                "orga_review",
                "orga_approved",
                "police_export_required",
                "police_exported",
                "police_cleared",
                "assigned",
                "rejected",
                "blocked",
                "archived",
                "deleted_operational_data",
                name="volunteerstatus",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(op.f("ix_volunteers_event_id"), "volunteers", ["event_id"])
    op.create_index(op.f("ix_volunteers_email_hash"), "volunteers", ["email_hash"])
    op.create_index(
        op.f("ix_volunteers_email_normalized"), "volunteers", ["email_normalized"]
    )

    op.create_table(
        "shifts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("location", sa.String(length=200)),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("needed_count", sa.Integer(), nullable=False),
        sa.Column(
            "min_age_group",
            sa.Enum("age_16_17", "adult", name="minagegroup", native_enum=False),
            nullable=False,
        ),
        sa.Column("allows_minors", sa.Boolean(), nullable=False),
        sa.Column("requires_birth_date", sa.Boolean(), nullable=False),
        sa.Column("requires_police_export", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(op.f("ix_shifts_event_id"), "shifts", ["event_id"])

    op.create_table(
        "volunteer_custom_fields",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "volunteer_id",
            sa.Integer(),
            sa.ForeignKey("volunteers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("field_id", sa.String(length=120), nullable=False),
        sa.Column("value_json", sa.Text(), nullable=False),
        sa.Column("retention_category", sa.String(length=100)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "volunteer_id", "field_id", name="uq_volunteer_custom_field"
        ),
    )
    op.create_index(
        op.f("ix_volunteer_custom_fields_volunteer_id"),
        "volunteer_custom_fields",
        ["volunteer_id"],
    )
    op.create_table(
        "file_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "volunteer_id",
            sa.Integer(),
            sa.ForeignKey("volunteers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "file_type",
            sa.Enum("photo", "document", "other", name="filetype", native_enum=False),
            nullable=False,
        ),
        sa.Column("storage_path", sa.String(length=500), nullable=False),
        sa.Column("original_filename", sa.String(length=255)),
        sa.Column("mime_type", sa.String(length=120)),
        sa.Column("size_bytes", sa.Integer()),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        op.f("ix_file_records_volunteer_id"), "file_records", ["volunteer_id"]
    )
    op.create_table(
        "shift_assignments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "volunteer_id",
            sa.Integer(),
            sa.ForeignKey("volunteers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "shift_id",
            sa.Integer(),
            sa.ForeignKey("shifts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "assignment_status",
            sa.Enum(
                "proposed",
                "confirmed",
                "cancelled",
                "checked_in",
                "no_show",
                name="assignmentstatus",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "eligibility_status",
            sa.Enum(
                "eligible",
                "missing_birth_date",
                "too_young",
                "blocked",
                "not_approved",
                "police_clearance_missing",
                name="eligibilitystatus",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "volunteer_id", "shift_id", name="uq_volunteer_shift_assignment"
        ),
    )
    op.create_index(
        op.f("ix_shift_assignments_shift_id"), "shift_assignments", ["shift_id"]
    )
    op.create_index(
        op.f("ix_shift_assignments_volunteer_id"), "shift_assignments", ["volunteer_id"]
    )


def downgrade() -> None:
    op.drop_table("shift_assignments")
    op.drop_table("file_records")
    op.drop_table("volunteer_custom_fields")
    op.drop_table("shifts")
    op.drop_table("volunteers")
    op.drop_table("audit_logs")
    op.drop_table("blocks")
    op.drop_table("events")
