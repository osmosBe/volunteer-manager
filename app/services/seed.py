from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Event, EventStatus

DEFAULT_EVENT_NAME = "St. Pölten PRIDE 2026"
DEFAULT_EVENT_SLUG = "pride-2026"


def ensure_default_event(db: Session) -> Event:
    existing = db.scalar(select(Event).where(Event.slug == DEFAULT_EVENT_SLUG))
    if existing:
        return existing

    if db.scalar(select(Event).limit(1)):
        raise ValueError("Cannot seed default event because an event already exists")

    event = Event(
        name=DEFAULT_EVENT_NAME, slug=DEFAULT_EVENT_SLUG, status=EventStatus.draft
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event
