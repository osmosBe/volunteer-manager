# ruff: noqa: UP006,UP035
import enum
from datetime import UTC, date, datetime
from typing import List

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class EventStatus(enum.StrEnum):
    draft = "draft"
    active = "active"
    closed = "closed"
    archived = "archived"


class AgeGroup(enum.StrEnum):
    under_16 = "under_16"
    age_16_17 = "age_16_17"
    adult = "adult"


class VolunteerStatus(enum.StrEnum):
    submitted = "submitted"
    needs_more_info = "needs_more_info"
    orga_review = "orga_review"
    orga_approved = "orga_approved"
    police_export_required = "police_export_required"
    police_exported = "police_exported"
    police_cleared = "police_cleared"
    assigned = "assigned"
    rejected = "rejected"
    blocked = "blocked"
    archived = "archived"
    deleted_operational_data = "deleted_operational_data"


class MinAgeGroup(enum.StrEnum):
    age_16_17 = "age_16_17"
    adult = "adult"


class AssignmentStatus(enum.StrEnum):
    proposed = "proposed"
    confirmed = "confirmed"
    cancelled = "cancelled"
    checked_in = "checked_in"
    no_show = "no_show"


class EligibilityStatus(enum.StrEnum):
    eligible = "eligible"
    missing_birth_date = "missing_birth_date"
    too_young = "too_young"
    blocked = "blocked"
    not_approved = "not_approved"
    police_clearance_missing = "police_clearance_missing"


class BlockCategory(enum.StrEnum):
    security = "security"
    reliability = "reliability"
    boundary_violation = "boundary_violation"
    house_rules = "house_rules"
    other = "other"


class BlockStatus(enum.StrEnum):
    active = "active"
    under_review = "under_review"
    expired = "expired"
    revoked = "revoked"


class FileType(enum.StrEnum):
    photo = "photo"
    document = "document"
    other = "other"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class Event(TimestampMixin, Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(
        String(120), nullable=False, unique=True, index=True
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[EventStatus] = mapped_column(
        SAEnum(EventStatus, native_enum=False),
        default=EventStatus.draft,
        nullable=False,
    )

    volunteers: Mapped[List["Volunteer"]] = relationship(back_populates="event")
    shifts: Mapped[List["Shift"]] = relationship(back_populates="event")


class Volunteer(TimestampMixin, Base):
    __tablename__ = "volunteers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    email_normalized: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True
    )
    email_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    phone: Mapped[str] = mapped_column(String(50))
    age_group: Mapped[AgeGroup] = mapped_column(
        SAEnum(AgeGroup, native_enum=False), nullable=False
    )
    birth_date: Mapped[date] = mapped_column(Date)
    birth_date_verified: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    food_choice: Mapped[str] = mapped_column(String(100))
    status: Mapped[VolunteerStatus] = mapped_column(
        SAEnum(VolunteerStatus, native_enum=False),
        default=VolunteerStatus.submitted,
        nullable=False,
    )

    event: Mapped[Event] = relationship(back_populates="volunteers")
    custom_fields: Mapped[List["VolunteerCustomField"]] = relationship(
        back_populates="volunteer", cascade="all, delete-orphan"
    )
    assignments: Mapped[List["ShiftAssignment"]] = relationship(
        back_populates="volunteer"
    )
    files: Mapped[List["FileRecord"]] = relationship(back_populates="volunteer")


class VolunteerCustomField(TimestampMixin, Base):
    __tablename__ = "volunteer_custom_fields"
    __table_args__ = (
        UniqueConstraint("volunteer_id", "field_id", name="uq_volunteer_custom_field"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    volunteer_id: Mapped[int] = mapped_column(
        ForeignKey("volunteers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    field_id: Mapped[str] = mapped_column(String(120), nullable=False)
    value_json: Mapped[str] = mapped_column(Text, nullable=False)
    retention_category: Mapped[str] = mapped_column(String(100))

    volunteer: Mapped[Volunteer] = relationship(back_populates="custom_fields")


class Shift(TimestampMixin, Base):
    __tablename__ = "shifts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text)
    location: Mapped[str] = mapped_column(String(200))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    needed_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    min_age_group: Mapped[MinAgeGroup] = mapped_column(
        SAEnum(MinAgeGroup, native_enum=False), nullable=False
    )
    allows_minors: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    requires_birth_date: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    requires_police_export: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    event: Mapped[Event] = relationship(back_populates="shifts")
    assignments: Mapped[List["ShiftAssignment"]] = relationship(back_populates="shift")


class ShiftAssignment(TimestampMixin, Base):
    __tablename__ = "shift_assignments"
    __table_args__ = (
        UniqueConstraint(
            "volunteer_id", "shift_id", name="uq_volunteer_shift_assignment"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    volunteer_id: Mapped[int] = mapped_column(
        ForeignKey("volunteers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    shift_id: Mapped[int] = mapped_column(
        ForeignKey("shifts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assignment_status: Mapped[AssignmentStatus] = mapped_column(
        SAEnum(AssignmentStatus, native_enum=False),
        default=AssignmentStatus.proposed,
        nullable=False,
    )
    eligibility_status: Mapped[EligibilityStatus] = mapped_column(
        SAEnum(EligibilityStatus, native_enum=False),
        default=EligibilityStatus.eligible,
        nullable=False,
    )

    volunteer: Mapped[Volunteer] = relationship(back_populates="assignments")
    shift: Mapped[Shift] = relationship(back_populates="assignments")


class Block(Base):
    __tablename__ = "blocks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    birth_date: Mapped[date] = mapped_column(Date)
    email_hash: Mapped[str] = mapped_column(String(128), index=True)
    block_category: Mapped[BlockCategory] = mapped_column(
        SAEnum(BlockCategory, native_enum=False), nullable=False
    )
    block_status: Mapped[BlockStatus] = mapped_column(
        SAEnum(BlockStatus, native_enum=False),
        default=BlockStatus.active,
        nullable=False,
    )
    created_by: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    review_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FileRecord(Base):
    __tablename__ = "file_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    volunteer_id: Mapped[int] = mapped_column(
        ForeignKey("volunteers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    file_type: Mapped[FileType] = mapped_column(
        SAEnum(FileType, native_enum=False), nullable=False
    )
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(Integer)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    volunteer: Mapped[Volunteer] = relationship(back_populates="files")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_user_id: Mapped[str] = mapped_column(String(255))
    actor_email: Mapped[str] = mapped_column(String(255))
    actor_name: Mapped[str] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(120), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(120))
    metadata_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
