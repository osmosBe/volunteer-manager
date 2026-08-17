"""Persistent domain model for the Volunteer Manager.

The initial migration remains deliberately compatible with the earlier project
scaffold.  New workflow fields live alongside those columns so existing DEV
databases can be upgraded without losing the small amount of seed data.
"""

import enum
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StringEnum(str, enum.Enum):
    """String-backed enum compatible with local and production Python versions."""


class EventStatus(StringEnum):
    draft = "draft"
    active = "active"  # compatibility with the first DEV migration
    registration_open = "registration_open"
    registration_closed = "registration_closed"
    ongoing = "ongoing"
    completed = "completed"
    closed = "closed"  # compatibility with the first DEV migration
    archived = "archived"


class ShiftStatus(StringEnum):
    draft = "draft"
    open = "open"
    full = "full"
    closed = "closed"
    cancelled = "cancelled"
    completed = "completed"


class AssignmentStatus(StringEnum):
    proposed = "proposed"  # compatibility with the first DEV migration
    pending = "pending"
    confirmed = "confirmed"
    waitlisted = "waitlisted"
    cancelled = "cancelled"
    rejected = "rejected"
    checked_in = "checked_in"
    attended = "attended"
    no_show = "no_show"


class AssignmentSource(StringEnum):
    public = "public"
    admin = "admin"


class AgeGroup(StringEnum):
    under_16 = "under_16"
    age_16_17 = "age_16_17"
    adult = "adult"


class MinAgeGroup(StringEnum):
    age_16_17 = "age_16_17"
    adult = "adult"


class VolunteerStatus(StringEnum):
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


class EligibilityStatus(StringEnum):
    eligible = "eligible"
    missing_birth_date = "missing_birth_date"
    too_young = "too_young"
    blocked = "blocked"
    not_approved = "not_approved"
    police_clearance_missing = "police_clearance_missing"


class BlockCategory(StringEnum):
    security = "security"
    reliability = "reliability"
    boundary_violation = "boundary_violation"
    house_rules = "house_rules"
    other = "other"


class BlockStatus(StringEnum):
    active = "active"
    under_review = "under_review"
    expired = "expired"
    revoked = "revoked"


class FileType(StringEnum):
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
    short_description: Mapped[str | None] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    start_time_is_set: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    end_time_is_set: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    timezone: Mapped[str] = mapped_column(
        String(80), default="Europe/Vienna", nullable=False
    )
    venue: Mapped[str | None] = mapped_column(String(200))
    address: Mapped[str | None] = mapped_column(String(500))
    public_meeting_point: Mapped[str | None] = mapped_column(String(300))
    registration_opens_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    registration_closes_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    registration_open_time_is_set: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    registration_close_time_is_set: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    status: Mapped[EventStatus] = mapped_column(
        SAEnum(EventStatus, native_enum=False),
        default=EventStatus.draft,
        nullable=False,
    )
    contact_name: Mapped[str | None] = mapped_column(String(200))
    contact_email: Mapped[str | None] = mapped_column(String(255))
    contact_phone: Mapped[str | None] = mapped_column(String(50))
    briefing: Mapped[str | None] = mapped_column(Text)
    clothing_and_material: Mapped[str | None] = mapped_column(Text)
    catering_info: Mapped[str | None] = mapped_column(Text)
    accessibility_info: Mapped[str | None] = mapped_column(Text)
    allows_minors: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    goodiebag_offered: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    teams: Mapped[list["Team"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )
    volunteers: Mapped[list["Volunteer"]] = relationship(back_populates="event")
    shifts: Mapped[list["Shift"]] = relationship(back_populates="event")
    briefings: Mapped[list["Briefing"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )


class Team(TimestampMixin, Base):
    __tablename__ = "teams"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    color: Mapped[str | None] = mapped_column(String(20))
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lead_name: Mapped[str | None] = mapped_column(String(200))
    lead_contact: Mapped[str | None] = mapped_column(String(255))
    meeting_point: Mapped[str | None] = mapped_column(String(300))
    notes: Mapped[str | None] = mapped_column(Text)
    event: Mapped[Event] = relationship(back_populates="teams")
    roles: Mapped[list["Role"]] = relationship(
        back_populates="team", cascade="all, delete-orphan"
    )
    materials: Mapped[list["TeamMaterial"]] = relationship(
        back_populates="team", cascade="all, delete-orphan"
    )


class TeamMaterial(TimestampMixin, Base):
    __tablename__ = "team_materials"
    __table_args__ = (
        UniqueConstraint("team_id", "name", name="uq_team_material_name"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    quantity_required: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    quantity_available: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unit: Mapped[str] = mapped_column(String(40), default="Stück", nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    is_consumable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    team: Mapped[Team] = relationship(back_populates="materials")
    checkin_issues: Mapped[list["CheckInMaterial"]] = relationship(
        back_populates="material"
    )


class Role(TimestampMixin, Base):
    __tablename__ = "roles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    short_description: Mapped[str | None] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)
    requirements: Mapped[str | None] = mapped_column(Text)
    minimum_age: Mapped[int | None] = mapped_column(Integer)
    training_required: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    prefer_pair: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    physically_demanding: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    sensitive_task: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    default_meeting_point: Mapped[str | None] = mapped_column(String(300))
    materials: Mapped[str | None] = mapped_column(Text)
    team: Mapped[Team] = relationship(back_populates="roles")
    shifts: Mapped[list["Shift"]] = relationship(back_populates="role")


class Volunteer(TimestampMixin, Base):
    __tablename__ = "volunteers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    pronouns: Mapped[str | None] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    email_normalized: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True
    )
    email_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    phone: Mapped[str | None] = mapped_column(String(50))
    age_group: Mapped[AgeGroup] = mapped_column(
        SAEnum(AgeGroup, native_enum=False), default=AgeGroup.adult, nullable=False
    )
    birth_date: Mapped[date | None] = mapped_column(Date)
    birth_date_verified: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    emergency_contact_name: Mapped[str | None] = mapped_column(String(200))
    emergency_contact_phone: Mapped[str | None] = mapped_column(String(50))
    tshirt_size: Mapped[str | None] = mapped_column(String(40))
    food_choice: Mapped[str | None] = mapped_column(String(100))
    dietary_needs: Mapped[str | None] = mapped_column(Text)
    accessibility_needs: Mapped[str | None] = mapped_column(Text)
    experience: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    internal_note: Mapped[str | None] = mapped_column(Text)
    contact_consent: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    future_contact_consent: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    consented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    privacy_version: Mapped[str | None] = mapped_column(String(80))
    edit_token_hash: Mapped[str | None] = mapped_column(String(128), unique=True)
    edit_token_revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    email_verification_token_hash: Mapped[str | None] = mapped_column(
        String(128), unique=True, index=True
    )
    email_verification_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    anonymized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[VolunteerStatus] = mapped_column(
        SAEnum(VolunteerStatus, native_enum=False),
        default=VolunteerStatus.submitted,
        nullable=False,
    )
    event: Mapped[Event] = relationship(back_populates="volunteers")
    custom_fields: Mapped[list["VolunteerCustomField"]] = relationship(
        back_populates="volunteer", cascade="all, delete-orphan"
    )
    assignments: Mapped[list["ShiftAssignment"]] = relationship(
        back_populates="volunteer"
    )
    files: Mapped[list["FileRecord"]] = relationship(back_populates="volunteer")


class Shift(TimestampMixin, Base):
    __tablename__ = "shifts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role_id: Mapped[int | None] = mapped_column(
        ForeignKey("roles.id", ondelete="SET NULL"), index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(String(200))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    needed_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    waitlist_capacity: Mapped[int | None] = mapped_column(Integer)
    lead_name: Mapped[str | None] = mapped_column(String(200))
    admin_notes: Mapped[str | None] = mapped_column(Text)
    volunteer_notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ShiftStatus] = mapped_column(
        SAEnum(ShiftStatus, native_enum=False),
        default=ShiftStatus.draft,
        nullable=False,
    )
    min_age_group: Mapped[MinAgeGroup] = mapped_column(
        SAEnum(MinAgeGroup, native_enum=False),
        default=MinAgeGroup.age_16_17,
        nullable=False,
    )
    allows_minors: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    requires_birth_date: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    requires_police_export: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    goodiebag_override: Mapped[bool | None] = mapped_column(Boolean)
    event: Mapped[Event] = relationship(back_populates="shifts")
    role: Mapped[Role | None] = relationship(back_populates="shifts")
    assignments: Mapped[list["ShiftAssignment"]] = relationship(back_populates="shift")

    @property
    def goodiebag_is_offered(self) -> bool:
        """Return the effective Goodiebag setting for this shift."""

        if self.goodiebag_override is not None:
            return self.goodiebag_override
        return self.event.goodiebag_offered


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
        default=AssignmentStatus.pending,
        nullable=False,
    )
    source: Mapped[AssignmentSource] = mapped_column(
        SAEnum(AssignmentSource, native_enum=False),
        default=AssignmentSource.public,
        nullable=False,
    )
    eligibility_status: Mapped[EligibilityStatus] = mapped_column(
        SAEnum(EligibilityStatus, native_enum=False),
        default=EligibilityStatus.eligible,
        nullable=False,
    )
    registered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checked_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    internal_note: Mapped[str | None] = mapped_column(Text)
    volunteer: Mapped[Volunteer] = relationship(back_populates="assignments")
    shift: Mapped[Shift] = relationship(back_populates="assignments")
    checkin: Mapped["CheckIn | None"] = relationship(
        back_populates="assignment", uselist=False, cascade="all, delete-orphan"
    )


class CheckIn(TimestampMixin, Base):
    __tablename__ = "checkins"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assignment_id: Mapped[int] = mapped_column(
        ForeignKey("shift_assignments.id", ondelete="CASCADE"), unique=True, index=True
    )
    checked_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checked_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lanyard_issued: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    wristband_issued: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    radio_issued: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    goodiebag_received: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    other_issued: Mapped[str | None] = mapped_column(Text)
    materials_returned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    note: Mapped[str | None] = mapped_column(Text)
    assignment: Mapped[ShiftAssignment] = relationship(back_populates="checkin")
    material_issues: Mapped[list["CheckInMaterial"]] = relationship(
        back_populates="checkin", cascade="all, delete-orphan"
    )


class CheckInMaterial(Base):
    __tablename__ = "checkin_materials"
    __table_args__ = (
        UniqueConstraint("checkin_id", "material_id", name="uq_checkin_material"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    checkin_id: Mapped[int] = mapped_column(
        ForeignKey("checkins.id", ondelete="CASCADE"), nullable=False, index=True
    )
    material_id: Mapped[int] = mapped_column(
        ForeignKey("team_materials.id"), nullable=False, index=True
    )
    quantity_issued: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    return_required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    returned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checkin: Mapped[CheckIn] = relationship(back_populates="material_issues")
    material: Mapped[TeamMaterial] = relationship(back_populates="checkin_issues")


class Briefing(TimestampMixin, Base):
    __tablename__ = "briefings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    team_id: Mapped[int | None] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), index=True
    )
    role_id: Mapped[int | None] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(String(40), default="1.0", nullable=False)
    visible_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    event: Mapped[Event] = relationship(back_populates="briefings")
    confirmations: Mapped[list["BriefingConfirmation"]] = relationship(
        back_populates="briefing", cascade="all, delete-orphan"
    )


class BriefingConfirmation(Base):
    __tablename__ = "briefing_confirmations"
    __table_args__ = (
        UniqueConstraint("briefing_id", "volunteer_id", name="uq_briefing_volunteer"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    briefing_id: Mapped[int] = mapped_column(
        ForeignKey("briefings.id", ondelete="CASCADE"), index=True
    )
    volunteer_id: Mapped[int] = mapped_column(
        ForeignKey("volunteers.id", ondelete="CASCADE"), index=True
    )
    confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    briefing: Mapped[Briefing] = relationship(back_populates="confirmations")


class OutboxMessage(Base):
    __tablename__ = "outbox_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    volunteer_id: Mapped[int] = mapped_column(
        ForeignKey("volunteers.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(60), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    recipient_email: Mapped[str | None] = mapped_column(String(255))
    delivery_mode: Mapped[str] = mapped_column(
        String(20), default="manual", nullable=False
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class SMTPConfiguration(TimestampMixin, Base):
    __tablename__ = "smtp_configurations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, default=587, nullable=False)
    username: Mapped[str | None] = mapped_column(String(255))
    from_email: Mapped[str] = mapped_column(String(255), nullable=False)
    from_name: Mapped[str] = mapped_column(
        String(255), default="Volunteer Manager", nullable=False
    )
    use_starttls: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    use_ssl: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class MailTemplate(TimestampMixin, Base):
    __tablename__ = "mail_templates"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(
        String(60), nullable=False, unique=True, index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    trigger_description: Mapped[str] = mapped_column(String(500), nullable=False)
    subject_template: Mapped[str] = mapped_column(String(255), nullable=False)
    body_template: Mapped[str] = mapped_column(Text, nullable=False)
    delivery_mode: Mapped[str] = mapped_column(
        String(20), default="manual", nullable=False
    )


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
    retention_category: Mapped[str | None] = mapped_column(String(100))
    volunteer: Mapped[Volunteer] = relationship(back_populates="custom_fields")


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
    original_filename: Mapped[str | None] = mapped_column(String(255))
    mime_type: Mapped[str | None] = mapped_column(String(120))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    volunteer: Mapped[Volunteer] = relationship(back_populates="files")


class Block(Base):
    __tablename__ = "blocks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    first_name: Mapped[str | None] = mapped_column(String(100))
    last_name: Mapped[str | None] = mapped_column(String(100))
    birth_date: Mapped[date | None] = mapped_column(Date)
    email_hash: Mapped[str | None] = mapped_column(String(128), index=True)
    block_category: Mapped[BlockCategory] = mapped_column(
        SAEnum(BlockCategory, native_enum=False), nullable=False
    )
    block_status: Mapped[BlockStatus] = mapped_column(
        SAEnum(BlockStatus, native_enum=False),
        default=BlockStatus.active,
        nullable=False,
    )
    created_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_user_id: Mapped[str | None] = mapped_column(String(255))
    actor_email: Mapped[str | None] = mapped_column(String(255))
    actor_name: Mapped[str | None] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(120), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(120))
    metadata_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class BrandingSettings(TimestampMixin, Base):
    """Singleton, portable branding configuration stored in the database."""

    __tablename__ = "branding_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_branding_settings_singleton"),
        CheckConstraint(
            "NOT (logo_url IS NOT NULL AND logo_data IS NOT NULL)",
            name="ck_branding_logo_source",
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    logo_url: Mapped[str | None] = mapped_column(String(2048))
    logo_data: Mapped[bytes | None] = mapped_column(LargeBinary)
    logo_content_type: Mapped[str | None] = mapped_column(String(80))
    logo_filename: Mapped[str | None] = mapped_column(String(255))


class LegalSettings(TimestampMixin, Base):
    """Singleton legal-footer configuration stored in the application database."""

    __tablename__ = "legal_settings"
    __table_args__ = (CheckConstraint("id = 1", name="ck_legal_settings_singleton"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    privacy_url: Mapped[str | None] = mapped_column(String(2048))
    imprint_url: Mapped[str | None] = mapped_column(String(2048))


class Permission(TimestampMixin, Base):
    __tablename__ = "permissions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(
        String(80), nullable=False, unique=True, index=True
    )
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_system: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    mappings: Mapped[list["PermissionMapping"]] = relationship(
        back_populates="permission", cascade="all, delete-orphan"
    )


class PermissionMapping(TimestampMixin, Base):
    __tablename__ = "permission_mappings"
    __table_args__ = (
        CheckConstraint(
            "mapping_type IN ('role', 'group', 'email')",
            name="ck_permission_mapping_type",
        ),
        UniqueConstraint(
            "permission_id",
            "mapping_type",
            "mapping_value",
            name="uq_permission_mapping_value",
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    permission_id: Mapped[int] = mapped_column(
        ForeignKey("permissions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mapping_type: Mapped[str] = mapped_column(String(16), nullable=False)
    mapping_value: Mapped[str] = mapped_column(String(255), nullable=False)
    display_label: Mapped[str | None] = mapped_column(String(255))
    permission: Mapped[Permission] = relationship(back_populates="mappings")
