from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.auth.admin import require_admin
from app.auth.provider import get_current_user
from app.config.settings import get_settings

admin_dependency = Depends(require_admin)

settings = get_settings()
templates = Jinja2Templates(directory="app/templates")

app = FastAPI(title=settings.app_name, version=settings.app_version)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


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


@app.get("/", tags=["public"])
def landing_page(request: Request):
    return templates.TemplateResponse("landing.html", {"request": request})


@app.get("/admin", tags=["admin"])
def admin_dashboard(request: Request, admin_user=admin_dependency):
    return templates.TemplateResponse(
        "admin_dashboard.html", {"request": request, "admin_user": admin_user}
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
            "groups": user.groups if user else [],
            "claims": user.claims if user else [],
            "auth_mode": settings.auth_mode,
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
