"""Persistent record of tracks the widget failed to import.

``PendingTrack`` lives in memory only, and the widget's import worker is a
detached process that exits as soon as the batch ends. Everything it learned
about a failure used to die with it: the user saw "youtube_failed" once and
had no way to find, understand or retry the tracks afterwards.

This module keeps a small JSON file next to the widget status so failures
outlive the worker, feed a short "don't hammer a hopeless track" cooldown, and
can be replayed on demand.
"""

import json
import os
import time

# ── Constants ────────────────────────────────────────────────────────────────

# Failures with these codes answer identically on an immediate retry, so a
# re-import inside the cooldown is skipped instead of burning a search plus
# three downloads against YouTube's rate limiter.
PERMANENT_CODES = frozenset(
    {
        "youtube_not_found",
        "youtube_unavailable",
        "youtube_blocked",
        "not_on_deezer",
    }
)

COOLDOWN_SECONDS = 600  # 10 minutes

_MAX_ENTRIES = 200


# ── Entry point ──────────────────────────────────────────────────────────────


def load_failures(path: str) -> list[dict]:
    """Return the recorded failures, newest last. Empty list on any problem."""
    try:
        with open(path, encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    entries = data.get("entries")
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def record_failures(path: str, failures: list[dict]) -> None:
    """Merge ``failures`` into the store (one entry per ISRC, newest wins)."""
    if not failures:
        return
    by_isrc = {str(entry.get("isrc") or ""): entry for entry in load_failures(path)}
    now = time.time()
    for failure in failures:
        isrc = str(failure.get("isrc") or "")
        if not isrc:
            continue
        # `or` would treat a legitimate epoch 0 as "missing" and re-stamp it.
        recorded_at = failure.get("at")
        by_isrc[isrc] = {
            **failure,
            "isrc": isrc,
            "at": now if recorded_at is None else recorded_at,
        }

    entries = sorted(by_isrc.values(), key=lambda entry: float(entry.get("at") or 0))
    _write(path, entries[-_MAX_ENTRIES:])


def clear_failures(path: str, isrcs: list[str] | None = None) -> None:
    """Drop ``isrcs`` from the store (all of them when ``isrcs`` is None)."""
    if isrcs is None:
        _write(path, [])
        return
    targets = {isrc.strip().upper() for isrc in isrcs if isrc.strip()}
    if not targets:
        return
    kept = [
        entry
        for entry in load_failures(path)
        if str(entry.get("isrc") or "").upper() not in targets
    ]
    _write(path, kept)


def recent_permanent_failures(
    path: str, cooldown: int = COOLDOWN_SECONDS, now: float | None = None
) -> dict[str, dict]:
    """Return ``{isrc: entry}`` for hopeless failures still inside the cooldown."""
    reference = time.time() if now is None else now
    fresh: dict[str, dict] = {}
    for entry in load_failures(path):
        if str(entry.get("detail") or entry.get("reason") or "") not in PERMANENT_CODES:
            continue
        try:
            age = reference - float(entry.get("at") or 0)
        except (TypeError, ValueError):
            continue
        if 0 <= age < cooldown:
            fresh[str(entry.get("isrc") or "").upper()] = entry
    return fresh


# ── Private Functions ────────────────────────────────────────────────────────


def _write(path: str, entries: list[dict]) -> None:
    """Atomically replace the store. Never raises."""
    payload = {"updated_at": time.time(), "entries": entries}
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        pass
