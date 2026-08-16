from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import URL, Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import get_settings

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_engine_url: str | None = None


def database_backend(database_url: str | URL) -> str:
    """Return a normalized backend name without connecting to the database."""
    backend = make_url(database_url).get_backend_name()
    return "postgresql" if backend.startswith("postgresql") else backend


def _ensure_sqlite_parent_dir(database_url: str | URL) -> None:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database:
        return
    if url.database != ":memory:":
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)


def _connect_args(database_url: str | URL) -> dict[str, object]:
    if database_backend(database_url) == "sqlite":
        return {"check_same_thread": False}
    return {}


def create_database_engine(database_url: str | None = None) -> Engine:
    url = database_url or get_settings().database_url
    _ensure_sqlite_parent_dir(url)
    backend = database_backend(url)
    engine_options: dict[str, object] = {
        "connect_args": _connect_args(url),
        "future": True,
    }
    if backend == "postgresql":
        engine_options.update(pool_pre_ping=True, pool_recycle=300)
    created_engine = create_engine(url, **engine_options)

    if backend == "sqlite":

        @event.listens_for(created_engine, "connect")
        def _set_sqlite_pragmas(
            dbapi_connection, connection_record
        ):  # noqa: ANN001, ARG001
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            # WAL is useful for the default local persistent database, but avoid it
            # for in-memory or explicitly shared-cache SQLite URLs.
            url_text = str(url)
            if ":memory:" not in url_text and "mode=memory" not in url_text:
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
