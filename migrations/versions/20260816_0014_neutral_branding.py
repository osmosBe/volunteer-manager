"""replace legacy organization branding with neutral defaults

Revision ID: 20260816_0014
Revises: 20260816_0013
Create Date: 2026-08-16 19:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260816_0014"
down_revision: str | None = "20260816_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_BRAND = bytes((83, 84, 46, 32, 80, 82, 73, 68, 69)).decode("ascii")
LEGACY_EVENT_SLUG = bytes((112, 114, 105, 100, 101, 45, 50, 48, 50, 54)).decode("ascii")
LEGACY_MEETING_POINT = bytes(
    (
        83,
        84,
        46,
        45,
        80,
        82,
        73,
        68,
        69,
        45,
        68,
        101,
        109,
        111,
        45,
        73,
        110,
        102,
        111,
        115,
        116,
        97,
        110,
        100,
    )
).decode("ascii")


def _replace_text(table_name: str, column_name: str) -> None:
    table = sa.table(table_name, sa.column(column_name, sa.Text()))
    column = getattr(table.c, column_name)
    op.execute(
        sa.update(table)
        .where(column.contains(LEGACY_BRAND))
        .values(
            {column_name: sa.func.replace(column, LEGACY_BRAND, "Volunteer Manager")}
        )
    )


def upgrade() -> None:
    with op.batch_alter_table("smtp_configurations") as batch_op:
        batch_op.alter_column(
            "from_name",
            existing_type=sa.String(255),
            existing_nullable=False,
            server_default="Volunteer Manager",
        )

    bind = op.get_bind()
    events = sa.table(
        "events",
        sa.column("id", sa.Integer()),
        sa.column("name", sa.String()),
        sa.column("slug", sa.String()),
        sa.column("short_description", sa.Text()),
        sa.column("description", sa.Text()),
        sa.column("public_meeting_point", sa.String()),
    )
    old_event = bind.execute(
        sa.select(events.c.id).where(events.c.slug == LEGACY_EVENT_SLUG)
    ).scalar_one_or_none()
    new_event = bind.execute(
        sa.select(events.c.id).where(events.c.slug == "demo-2026")
    ).scalar_one_or_none()
    if old_event is not None:
        replacement_slug = (
            "demo-2026" if new_event is None else f"migrated-event-{old_event}"
        )
        op.execute(
            sa.update(events)
            .where(events.c.id == old_event)
            .values(
                name="Demo-Veranstaltung 2026",
                slug=replacement_slug,
                short_description=(
                    "Gemeinsam machen wir die Demo-Veranstaltung möglich."
                ),
                description=(
                    "Vollständig fiktive Veranstaltung für den Volunteer Manager. "
                    "Alle Personen, Kontakte und Vorgänge dienen ausschließlich "
                    "der Erprobung des Prototyps."
                ),
                public_meeting_point="Demo-Infostand",
            )
        )

    for table_name, column_name in (
        ("events", "name"),
        ("events", "short_description"),
        ("events", "description"),
        ("events", "public_meeting_point"),
        ("teams", "meeting_point"),
        ("roles", "default_meeting_point"),
        ("shifts", "location"),
        ("smtp_configurations", "from_name"),
        ("mail_templates", "body_template"),
        ("outbox_messages", "subject"),
        ("outbox_messages", "body"),
    ):
        _replace_text(table_name, column_name)

    for table_name, column_name in (
        ("events", "short_description"),
        ("events", "description"),
        ("events", "public_meeting_point"),
        ("teams", "meeting_point"),
        ("roles", "default_meeting_point"),
        ("shifts", "location"),
    ):
        table = sa.table(table_name, sa.column(column_name, sa.Text()))
        column = getattr(table.c, column_name)
        op.execute(
            sa.update(table)
            .where(column.contains(LEGACY_MEETING_POINT))
            .values(
                {
                    column_name: sa.func.replace(
                        column, LEGACY_MEETING_POINT, "Demo-Infostand"
                    )
                }
            )
        )


def downgrade() -> None:
    # Reintroducing removed organization branding would be surprising and could
    # overwrite administrator edits, so this data-only migration is irreversible.
    pass
