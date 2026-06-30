from app.database.session import get_session_factory
from app.services.seed import ensure_default_event


def main() -> None:
    with get_session_factory()() as db:
        event = ensure_default_event(db)
        print(f"Default event available: {event.slug} ({event.name})")


if __name__ == "__main__":
    main()
