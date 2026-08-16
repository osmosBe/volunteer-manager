"""Check-in workflow with explicit material handover history."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AssignmentStatus,
    CheckIn,
    CheckInMaterial,
    Shift,
    ShiftAssignment,
)
from app.models.core import utcnow


class CheckInError(ValueError):
    pass


def goodiebag_is_offered(shift: Shift) -> bool:
    """Resolve the event default and optional shift-level override."""

    return shift.goodiebag_is_offered


def check_in_assignment(
    db: Session,
    assignment: ShiftAssignment,
    *,
    lanyard: bool,
    wristband: bool,
    radio: bool,
    other: str | None,
    material_ids: list[int] | None = None,
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
    db.add(checkin)
    selected_ids = set(material_ids or [])
    team = assignment.shift.role.team if assignment.shift.role else None
    available_materials = {
        material.id: material
        for material in (team.materials if team else [])
        if material.is_active
    }
    if selected_ids - available_materials.keys():
        raise CheckInError(
            "Mindestens ein Material gehört nicht zum Arbeitsbereich dieser Schicht."
        )
    existing_ids = {issue.material_id for issue in checkin.material_issues}
    for material_id in selected_ids - existing_ids:
        material = available_materials[material_id]
        with db.no_autoflush:
            used = db.scalar(
                select(
                    func.coalesce(func.sum(CheckInMaterial.quantity_issued), 0)
                ).where(
                    CheckInMaterial.material_id == material.id,
                    (
                        CheckInMaterial.return_required.is_(False)
                        | CheckInMaterial.returned_at.is_(None)
                    ),
                )
            )
        if used >= material.quantity_available:
            raise CheckInError(f"„{material.name}“ ist nicht mehr verfügbar.")
        checkin.material_issues.append(
            CheckInMaterial(
                material=material,
                quantity_issued=1,
                return_required=not material.is_consumable,
            )
        )
    assignment.assignment_status = AssignmentStatus.checked_in
    assignment.checked_in_at = checkin.checked_in_at
    db.commit()
    return checkin


def check_out_assignment(
    db: Session,
    assignment: ShiftAssignment,
    *,
    goodiebag_received: bool = False,
) -> CheckIn:
    if assignment.checkin is None or assignment.checkin.checked_in_at is None:
        raise CheckInError("Diese Zuteilung wurde noch nicht eingecheckt.")
    if goodiebag_received and not goodiebag_is_offered(assignment.shift):
        raise CheckInError("Für diese Schicht wird kein Goodiebag angeboten.")
    assignment.checkin.goodiebag_received = goodiebag_received
    assignment.checkin.checked_out_at = utcnow()
    assignment.checkin.materials_returned_at = utcnow()
    for issue in assignment.checkin.material_issues:
        if issue.return_required and issue.returned_at is None:
            issue.returned_at = utcnow()
    assignment.assignment_status = AssignmentStatus.attended
    db.commit()
    return assignment.checkin
