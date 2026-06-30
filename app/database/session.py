from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import get_settings


def _ensure_sqlite_parent_dir(database_url: str) -> None:
    if not database_url.startswith("sqlite:///") or database_url in {
        "sqlite://",
        "sqlite:///:memory:",
    }:
        return
    path = database_url.removeprefix("sqlite:///")
    if path and path != ":memory:":
        Path(
            "/" + path if database_url.startswith("sqlite:////") else path
        ).parent.mkdir(parents=True, exist_ok=True)


def _connect_args(database_url: str) -> dict[str, object]:
    if database_url.startswith("sqlite"):
        return {"check_same_thread": False}
    return {}


def create_database_engine(database_url: str | None = None) -> Engine:
    url = database_url or get_settings().database_url
    _ensure_sqlite_parent_dir(url)
    engine = create_engine(url, connect_args=_connect_args(url), future=True)

    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(
            dbapi_connection, connection_record
        ):  # noqa: ANN001, ARG001
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            # WAL is useful for the default local persistent database, but avoid it
            # for in-memory or explicitly shared-cache SQLite URLs.
            if ":memory:" not in url and "mode=memory" not in url:
                cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    return engine


engine = create_database_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)


def get_db() -> Generator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def database_status(db: Session) -> dict[str, str | bool]:
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - defensive status path
        return {"connected": False, "message": str(exc)}
    return {"connected": True, "message": "ok"}
