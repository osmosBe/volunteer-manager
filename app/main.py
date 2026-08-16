from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config.settings import get_settings


settings = get_settings()
templates = Jinja2Templates(directory="app/templates")

app = FastAPI(title=settings.app_name, version=settings.app_version)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/", tags=["pages"])
def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="base.html",
        context={"demo_mode": settings.demo_mode},
    )


@app.get("/healthz", tags=["health"])
def healthz() -> dict[str, str]:
    return {"status": "ok", "version": settings.app_version}
