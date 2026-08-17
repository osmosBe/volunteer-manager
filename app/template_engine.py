"""Shared HTML template engine and safe global context."""

from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.auth.navigation import navigation_context
from app.database.session import get_session_factory
from app.services.legal_settings import LegalSettingsRepository


def legal_links_context(request: Request) -> dict[str, Any]:
    """Hide legal links safely when configuration storage is unavailable."""

    del request
    try:
        with get_session_factory()() as db:
            settings = LegalSettingsRepository(db).get()
            privacy_url = settings.privacy_url if settings else None
            imprint_url = settings.imprint_url if settings else None
    except Exception:  # Rendering public/error pages must survive database failures.
        privacy_url = None
        imprint_url = None
    return {
        "legal_links": {
            "privacy_url": privacy_url,
            "imprint_url": imprint_url,
        }
    }


templates = Jinja2Templates(
    directory="app/templates",
    context_processors=[navigation_context, legal_links_context],
)
