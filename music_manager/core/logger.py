"""Event logger — append-only JSONL audit trail.

Every operation (search, import, error, user choice) is logged as a
single JSON line in logs.jsonl. Used for debugging and traceability.
"""

import json
import os
import threading
import traceback
from datetime import datetime

# ── Module state ─────────────────────────────────────────────────────────────

_log_path: str = ""
_log_lock = threading.Lock()


# ── Entry point ──────────────────────────────────────────────────────────────


def init_logger(logs_path: str) -> None:
    """Set the log file path. Call once at startup."""
    global _log_path  # noqa: PLW0603
    _log_path = logs_path


def log_event(action: str, **data: object) -> None:
    """Append an event to the log file. Silent if logger not initialized.

    Args:
        action: event type (e.g. "deezer_search", "import_done", "scan_library").
        **data: arbitrary key-value pairs attached to the event.
    """
    if not _log_path:
        return
    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "action": action,
        **data,
    }
    try:
        with _log_lock:
            os.makedirs(os.path.dirname(_log_path), exist_ok=True)
            with open(_log_path, "a", encoding="utf-8") as file:
                file.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def log_worker_error(exc: BaseException) -> None:
    """Log a worker_error with exception type and traceback."""
    log_event(
        "worker_error",
        error=f"{type(exc).__name__}: {exc}",
        traceback=traceback.format_exc(),
    )
