from datetime import datetime
from typing import Annotated

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.public import router as public_router
from app.auth.permissions import has_permission, permission_names, require_permission
from app.auth.provider import get_current_user
from app.config.settings import get_settings
from app.database.session import database_status, get_db
from app.models import AssignmentStatus, Event, Shift, ShiftAssignment, Volunteer
from app.services.checkins import (
    CheckInError,
    check_in_assignment,
    check_out_assignment,
)

admin_dependency = Depends(require_permission("admin"))
db_dependency = Depends(get_db)

settings = get_settings()
templates = Jinja2Templates(directory="app/templates")

app = FastAPI(title=settings.app_name, version=settings.app_version)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(public_router)


@app.exception_handler(status.HTTP_401_UNAUTHORIZED)
async def unauthenticated_handler(request: Request, exc: HTTPException):
    return templates.TemplateResponse(
        "unauthenticated.html",
        {"request": request},
        status_code=status.HTTP_401_UNAUTHORIZED,
    )


@app.exception_handler(status.HTTP_403_FORBIDDEN)
async def forbidden_handler(request: Request, exc: HTTPException):
    return templates.TemplateResponse(
        "forbidden.html", {"request": request}, status_code=status.HTTP_403_FORBIDDEN
    )


@app.get("/healthz", tags=["health"])
def healthz() -> dict[str, str]:
    return {"status": "ok", "version": settings.app_version}


@app.get("/health/live", tags=["health"])
def health_live() -> dict[str, str]:
    """Liveness deliberately avoids a database dependency."""
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
def health_ready(db: Session = db_dependency):
    state = database_status(db)
    if not state["connected"]:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    return {"status": "ok", "database": "connected"}


@app.get("/admin", tags=["admin"])
def admin_dashboard(
    request: Request, admin_user=admin_dependency, db: Session = db_dependency
):
    try:
        events = list(
            db.scalars(select(Event).order_by(Event.starts_at.desc(), Event.name))
        )
        dashboard_error = None
    except SQLAlchemyError:
        events = []
        dashboard_error = "Die Datenbank ist derzeit nicht erreichbar."
    return templates.TemplateResponse(
        "admin_dashboard.html",
        {
            "request": request,
            "admin_user": admin_user,
            "events": events,
            "dashboard_error": dashboard_error,
        },
    )


@app.get("/admin/veranstaltungen/neu", tags=["admin"])
def new_event_form(request: Request, admin_user=admin_dependency):
    return templates.TemplateResponse(
        "admin_event_form.html", {"request": request, "event": None, "error": None}
    )


@app.post("/admin/veranstaltungen/neu", tags=["admin"])
def create_event(
    request: Request,
    admin_user=admin_dependency,
    db: Session = db_dependency,
    name: Annotated[str, Form()] = "",
    slug: Annotated[str, Form()] = "",
    starts_at: Annotated[datetime | None, Form()] = None,
    ends_at: Annotated[datetime | None, Form()] = None,
):
    if not name.strip() or not slug.strip():
        return templates.TemplateResponse(
            "admin_event_form.html",
            {
                "request": request,
                "event": None,
                "error": "Titel und URL-Kürzel sind erforderlich.",
            },
            status_code=422,
        )
    if ends_at and starts_at and ends_at <= starts_at:
        return templates.TemplateResponse(
            "admin_event_form.html",
            {
                "request": request,
                "event": None,
                "error": "Das Ende muss nach dem Beginn liegen.",
            },
            status_code=422,
        )
    if db.scalar(select(Event).where(Event.slug == slug.strip())):
        return templates.TemplateResponse(
            "admin_event_form.html",
            {
                "request": request,
                "event": None,
                "error": "Dieses URL-Kürzel wird bereits verwendet.",
            },
            status_code=422,
        )
    event = Event(
        name=name.strip(), slug=slug.strip(), starts_at=starts_at, ends_at=ends_at
    )
    db.add(event)
    db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.get("/admin/check-in", tags=["admin"])
def checkin_page(
    request: Request, db: Session = db_dependency, admin_user=admin_dependency
):
    assignments = list(
        db.scalars(
            select(ShiftAssignment).where(
                ShiftAssignment.assignment_status.in_(
                    [AssignmentStatus.confirmed, AssignmentStatus.checked_in]
                )
            )
        )
    )
    return templates.TemplateResponse(
        "admin_checkin.html",
        {"request": request, "assignments": assignments, "error": None},
    )


@app.get("/admin/ehrenamtliche", tags=["admin"])
def volunteer_list(
    request: Request,
    query: str = "",
    db: Session = db_dependency,
    admin_user=admin_dependency,
):
    statement = select(Volunteer).order_by(Volunteer.last_name, Volunteer.first_name)
    if query.strip():
        pattern = f"%{query.strip()}%"
        statement = statement.where(
            Volunteer.first_name.ilike(pattern)
            | Volunteer.last_name.ilike(pattern)
            | Volunteer.email.ilike(pattern)
            | Volunteer.phone.ilike(pattern)
        )
    return templates.TemplateResponse(
        "admin_volunteer_list.html",
        {"request": request, "volunteers": list(db.scalars(statement)), "query": query},
    )


@app.post("/admin/check-in/{assignment_id}", tags=["admin"])
def checkin_submit(
    assignment_id: int,
    db: Session = db_dependency,
    admin_user=admin_dependency,
    lanyard: Annotated[bool, Form()] = False,
    wristband: Annotated[bool, Form()] = False,
    radio: Annotated[bool, Form()] = False,
    other: Annotated[str | None, Form()] = None,
):
    assignment = db.get(ShiftAssignment, assignment_id)
    if assignment is None:
        raise HTTPException(status_code=404)
    try:
        check_in_assignment(
            db,
            assignment,
            lanyard=lanyard,
            wristband=wristband,
            radio=radio,
            other=other,
        )
    except CheckInError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return RedirectResponse(url="/admin/check-in", status_code=303)


@app.post("/admin/check-in/{assignment_id}/check-out", tags=["admin"])
def checkout_submit(
    assignment_id: int, db: Session = db_dependency, admin_user=admin_dependency
):
    assignment = db.get(ShiftAssignment, assignment_id)
    if assignment is None:
        raise HTTPException(status_code=404)
    try:
        check_out_assignment(db, assignment)
    except CheckInError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return RedirectResponse(url="/admin/check-in", status_code=303)


@app.get("/admin/db", tags=["admin"])
def admin_database_status(
    request: Request,
    admin_user=admin_dependency,
    db: Session = db_dependency,
):
    inspector = inspect(db.bind)
    migration_revision = None
    if inspector.has_table("alembic_version"):
        migration_revision = db.execute(
            text("SELECT version_num FROM alembic_version LIMIT 1")
        ).scalar_one_or_none()

    return templates.TemplateResponse(
        "admin_db.html",
        {
            "request": request,
            "admin_user": admin_user,
            "status": database_status(db),
            "migration_revision": migration_revision,
            "event_count": db.scalar(select(func.count(Event.id))) or 0,
            "volunteer_count": db.scalar(select(func.count(Volunteer.id))) or 0,
            "shift_count": db.scalar(select(func.count(Shift.id))) or 0,
        },
    )


@app.get("/debug/easyauth", tags=["debug"])
def debug_easyauth(request: Request):
    settings = get_settings()
    if not settings.debug:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    user = get_current_user(request)
    return JSONResponse(
        {
            "authenticated": user is not None,
            "name": user.name if user else None,
            "email": user.email if user else None,
            "user_id": user.user_id if user else None,
            "roles": user.roles if user else [],
            "groups": user.groups if user else [],
            "claims": user.claims if user else [],
            "auth_mode": settings.auth_mode,
            "permissions_summary": {
                name: has_permission(user, name, settings)
                for name in permission_names(settings)
            },
        }
    )


@app.get("/auth/login", tags=["auth"])
def login():
    settings = get_settings()
    if settings.auth_mode == "easyauth":
        return RedirectResponse(
            url="/.auth/login/aad?post_login_redirect_uri=/admin",
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        )
    return RedirectResponse(
        url="/admin", status_code=status.HTTP_307_TEMPORARY_REDIRECT
    )


@app.get("/auth/logout", tags=["auth"])
def logout():
    settings = get_settings()
    if settings.auth_mode == "easyauth":
        return RedirectResponse(
            url="/.auth/logout?post_logout_redirect_uri=/",
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        )
    return RedirectResponse(url="/", status_code=status.HTTP_307_TEMPORARY_REDIRECT)
