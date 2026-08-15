"""Check-in workflow with explicit material handover history."""

from sqlalchemy.orm import Session

from app.models import AssignmentStatus, CheckIn, ShiftAssignment
from app.models.core import utcnow


class CheckInError(ValueError):
    pass


def check_in_assignment(
    db: Session,
    assignment: ShiftAssignment,
    *,
    lanyard: bool,
    wristband: bool,
    radio: bool,
    other: str | None,
) -> CheckIn:
    if assignment.assignment_status not in {
        AssignmentStatus.confirmed,
        AssignmentStatus.checked_in,
    }:
        raise CheckInError("Diese Zuteilung kann nicht eingecheckt werden.")
    checkin = assignment.checkin or CheckIn(assignment=assignment)
    checkin.checked_in_at = checkin.checked_in_at or utcnow()
    checkin.lanyard_issued = lanyard
    checkin.wristband_issued = wristband
    checkin.radio_issued = radio
    checkin.other_issued = other
    assignment.assignment_status = AssignmentStatus.checked_in
    assignment.checked_in_at = checkin.checked_in_at
    db.add(checkin)
    db.commit()
    return checkin


def check_out_assignment(db: Session, assignment: ShiftAssignment) -> CheckIn:
    if assignment.checkin is None or assignment.checkin.checked_in_at is None:
        raise CheckInError("Diese Zuteilung wurde noch nicht eingecheckt.")
    assignment.checkin.checked_out_at = utcnow()
    assignment.checkin.materials_returned_at = utcnow()
    db.commit()
    return assignment.checkin
