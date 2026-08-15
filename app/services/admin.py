"""Administrative workflows with explicit history and privacy safeguards."""

import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AssignmentStatus,
    AuditLog,
    Shift,
    ShiftAssignment,
    Volunteer,
    VolunteerStatus,
)
from app.models.core import utcnow


class AdminWorkflowError(ValueError):
    pass


def record_audit(
    db: Session,
    *,
    action: str,
    entity_type: str,
    entity_id: int | str | None,
    changes: dict[str, object] | None = None,
    actor: str = "demo-admin",
) -> AuditLog:
    entry = AuditLog(
        actor_user_id=actor,
        actor_name=actor,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        metadata_json=json.dumps(changes or {}, ensure_ascii=False, default=str),
    )
    db.add(entry)
    return entry


def change_assignment_status(
    db: Session, assignment: ShiftAssignment, new_status: AssignmentStatus
) -> ShiftAssignment:
    old_status = assignment.assignment_status
    if new_status == AssignmentStatus.checked_in:
        raise AdminWorkflowError("Check-in muss über den Check-in-Modus erfolgen.")
    assignment.assignment_status = new_status
    if new_status == AssignmentStatus.confirmed:
        assignment.confirmed_at = utcnow()
        assignment.cancelled_at = None
    elif new_status == AssignmentStatus.cancelled:
        assignment.cancelled_at = utcnow()
    record_audit(
        db,
        action="assignment.status_changed",
        entity_type="shift_assignment",
        entity_id=assignment.id,
        changes={"from": old_status.value, "to": new_status.value},
    )
    db.commit()
    return assignment


def promote_first_waitlisted(db: Session, shift: Shift) -> ShiftAssignment:
    occupied = sum(
        item.assignment_status
        in {
            AssignmentStatus.confirmed,
            AssignmentStatus.checked_in,
            AssignmentStatus.attended,
        }
        for item in shift.assignments
    )
    if occupied >= shift.needed_count:
        raise AdminWorkflowError("Die Schicht hat derzeit keinen freien Platz.")
    assignment = db.scalar(
        select(ShiftAssignment)
        .where(
            ShiftAssignment.shift_id == shift.id,
            ShiftAssignment.assignment_status == AssignmentStatus.waitlisted,
        )
        .order_by(ShiftAssignment.registered_at, ShiftAssignment.id)
    )
    if assignment is None:
        raise AdminWorkflowError("Für diese Schicht gibt es keine Warteliste.")
    return change_assignment_status(db, assignment, AssignmentStatus.confirmed)


def anonymize_volunteer(db: Session, volunteer: Volunteer) -> Volunteer:
    """Irreversibly remove personal data while preserving deployment statistics."""
    if volunteer.anonymized_at is not None:
        return volunteer
    marker = f"anonym-{volunteer.id}@example.invalid"
    volunteer.first_name = "Anonymisiert"
    volunteer.last_name = f"#{volunteer.id}"
    volunteer.pronouns = None
    volunteer.email = marker
    volunteer.email_normalized = marker
    volunteer.email_hash = f"anonymized-{volunteer.id}"
    volunteer.phone = None
    volunteer.birth_date = None
    volunteer.birth_date_verified = False
    volunteer.emergency_contact_name = None
    volunteer.emergency_contact_phone = None
    volunteer.tshirt_size = None
    volunteer.food_choice = None
    volunteer.dietary_needs = None
    volunteer.accessibility_needs = None
    volunteer.experience = None
    volunteer.notes = None
    volunteer.internal_note = None
    volunteer.edit_token_hash = None
    volunteer.edit_token_revoked_at = utcnow()
    volunteer.anonymized_at = utcnow()
    volunteer.status = VolunteerStatus.deleted_operational_data
    volunteer.custom_fields.clear()
    for file_record in list(volunteer.files):
        db.delete(file_record)
    record_audit(
        db,
        action="volunteer.anonymized",
        entity_type="volunteer",
        entity_id=volunteer.id,
        changes={"anonymized_at": datetime.isoformat(volunteer.anonymized_at)},
    )
    db.commit()
    return volunteer
