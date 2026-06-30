from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import get_settings

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_engine_url: str | None = None


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
    created_engine = create_engine(url, connect_args=_connect_args(url), future=True)

    if url.startswith("sqlite"):

        @event.listens_for(created_engine, "connect")
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

    return created_engine


def get_engine() -> Engine:
    global _engine, _engine_url

    url = get_settings().database_url
    if _engine is None or _engine_url != url:
        if _engine is not None:
            _engine.dispose()
        _engine = create_database_engine(url)
        _engine_url = url
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory

    current_engine = get_engine()
    if (
        _session_factory is None
        or _session_factory.kw.get("bind") is not current_engine
    ):
        _session_factory = sessionmaker(
            autocommit=False, autoflush=False, bind=current_engine, future=True
        )
    return _session_factory


def reset_database_engine() -> None:
    """Dispose lazy database globals so tests can change DATABASE_URL safely."""
    global _engine, _engine_url, _session_factory

    if _engine is not None:
        _engine.dispose()
    _engine = None
    _engine_url = None
    _session_factory = None


def get_db() -> Generator[Session]:
    db = get_session_factory()()
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
