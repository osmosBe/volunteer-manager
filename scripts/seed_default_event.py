from app.database.session import get_session_factory
from app.services.seed import ensure_demo_data


def main() -> None:
    with get_session_factory()() as db:
        event = ensure_demo_data(db)
        print(f"Demo event available: {event.slug} ({event.name})")


if __name__ == "__main__":
    main()
