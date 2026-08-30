"""File-based locks for CLI ↔ UI coordination.

Each lock file holds the PID of the holder **and** that process's start time.
A lock is active only if the PID is alive *and* still the same process that
took it — macOS recycles PIDs, and a lock left behind by a killed UI would
otherwise start blocking every import the day its PID got reused.

Used by:
- ``ui/app.py`` to advertise that the Textual UI is running (so the widget
  CLI refuses to import in parallel and corrupt tracks.json).
- ``cli/import_cmd.py`` to prevent two widget imports from running at once.
"""

import json
import os
import subprocess

# ── Constants ────────────────────────────────────────────────────────────────

_PS_TIMEOUT = 5


# ── Entry point ──────────────────────────────────────────────────────────────


def acquire_lock(path: str) -> bool:
    """Try to acquire the lock at ``path``.

    Returns True if the caller now owns it, False if another live process
    already holds it. A stale lock (dead PID, or PID reused by an unrelated
    process) is overwritten.
    """
    if is_locked(path):
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pid = os.getpid()
    payload = {"pid": pid, "started_at": _process_start_time(pid)}
    tmp = f"{path}.{pid}.tmp"
    with open(tmp, "w", encoding="utf-8") as file:
        json.dump(payload, file)
    os.replace(tmp, path)
    return True


def release_lock(path: str) -> None:
    """Release the lock at ``path`` if it belongs to the current process.

    No-op if the file is missing or owned by another PID — never raises.
    """
    pid = _read_pid(path)
    if pid is None or pid != os.getpid():
        return
    try:
        os.remove(path)
    except OSError:
        pass


def is_locked(path: str) -> bool:
    """Return True if ``path`` is held by the live process that took it."""
    record = _read_record(path)
    if record is None:
        return False
    pid = record.get("pid")
    if not isinstance(pid, int) or not _pid_alive(pid):
        return False

    # No recorded start time: a lock written by an older version. Fall back to
    # PID liveness alone rather than treating it as free.
    recorded_start = record.get("started_at")
    if not recorded_start:
        return True

    current_start = _process_start_time(pid)
    if not current_start:
        return True  # couldn't verify — assume the holder is genuine
    return current_start == recorded_start


def lock_owner_pid(path: str) -> int | None:
    """Return the PID stored in ``path`` (alive or not), or None if absent."""
    return _read_pid(path)


def clear_stale_lock(path: str) -> bool:
    """Delete ``path`` if it is not held by a live process. Returns True if removed.

    Called at startup so a lock orphaned by a crash never accumulates: leaving
    it on disk is what turns a past crash into a future PID-reuse deadlock.
    """
    if not os.path.isfile(path) or is_locked(path):
        return False
    try:
        os.remove(path)
    except OSError:
        return False
    return True


# ── Private Functions ────────────────────────────────────────────────────────


def _read_record(path: str) -> dict | None:
    """Return the lock payload, or None if absent/unreadable.

    Accepts the legacy format (a bare PID as text) so an upgrade doesn't
    invalidate a lock held by a running instance.
    """
    try:
        with open(path, encoding="utf-8") as file:
            content = file.read().strip()
    except (OSError, FileNotFoundError):
        return None
    if not content:
        return None
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict):
        return data
    # Legacy format: the file held a bare PID. Note that `json.loads` parses
    # that into an int rather than failing, so both paths must be handled.
    try:
        return {"pid": int(content), "started_at": ""}
    except ValueError:
        return None


def _read_pid(path: str) -> int | None:
    """Return the PID stored in the lock file, or None on any failure."""
    record = _read_record(path)
    if record is None:
        return None
    pid = record.get("pid")
    return pid if isinstance(pid, int) else None


def _pid_alive(pid: int) -> bool:
    """Check if ``pid`` corresponds to a running process on this machine."""
    if pid <= 0:
        return False
    try:
        # Signal 0 doesn't send anything — it just probes for existence.
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but belongs to another user — still "alive".
        return True
    return True


def _process_start_time(pid: int) -> str:
    """Return the start time of ``pid`` as reported by ps ("" if unknown)."""
    if pid <= 0:
        return ""
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart="],
            capture_output=True,
            text=True,
            timeout=_PS_TIMEOUT,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()
