"""Small process-local status snapshot; this is not a delivery queue/history."""

from threading import Lock

from app.models.core import utcnow

_lock = Lock()
_last_test: dict[str, object] | None = None


def record_test_status(*, success: bool, provider: str, error_code: str | None) -> None:
    global _last_test
    with _lock:
        _last_test = {
            "success": success,
            "provider": provider,
            "error_code": error_code,
            "timestamp": utcnow(),
        }


def get_last_test_status() -> dict[str, object] | None:
    with _lock:
        return dict(_last_test) if _last_test else None
