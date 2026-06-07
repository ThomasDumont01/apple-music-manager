"""File-based locks for CLI ↔ UI coordination.

Each lock file holds the PID of the holder. A lock is considered active
only if its PID is alive. Stale locks (PID dead) are treated as released —
this avoids permanent deadlocks after a crash.

Used by:
- ``ui/app.py`` to advertise that the Textual UI is running (so the widget
  CLI refuses to import in parallel and corrupt tracks.json).
- ``cli/import_cmd.py`` to prevent two widget imports from running at once.
"""

import os

# ── Entry point ──────────────────────────────────────────────────────────────


def acquire_lock(path: str) -> bool:
    """Try to acquire the lock at ``path``.

    Returns True if the caller now owns it, False if another live process
    already holds it. A stale lock (PID dead) is overwritten.
    """
    if is_locked(path):
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as file:
        file.write(str(os.getpid()))
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
    """Return True if ``path`` holds the PID of a live process."""
    pid = _read_pid(path)
    if pid is None:
        return False
    return _pid_alive(pid)


def lock_owner_pid(path: str) -> int | None:
    """Return the PID stored in ``path`` (alive or not), or None if absent."""
    return _read_pid(path)


# ── Private Functions ────────────────────────────────────────────────────────


def _read_pid(path: str) -> int | None:
    """Return the PID stored in the lock file, or None on any failure."""
    try:
        with open(path, encoding="utf-8") as file:
            content = file.read().strip()
    except (OSError, FileNotFoundError):
        return None
    try:
        return int(content)
    except ValueError:
        return None


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
