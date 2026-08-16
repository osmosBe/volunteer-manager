"""Shared HTML template engine and safe global context."""

from fastapi.templating import Jinja2Templates

from app.auth.navigation import navigation_context

templates = Jinja2Templates(
    directory="app/templates", context_processors=[navigation_context]
)
