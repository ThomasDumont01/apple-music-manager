"""Persistent import-failure store used by the widget."""

import json
import time
from pathlib import Path

from music_manager.cli.failures import (
    COOLDOWN_SECONDS,
    clear_failures,
    load_failures,
    recent_permanent_failures,
    record_failures,
)


def _path(tmp_path: Path) -> str:
    return str(tmp_path / "widget_failures.json")


# ── Round-trip ─────────────────────────────────────────────────────────────


def test_record_then_load(tmp_path: Path) -> None:
    path = _path(tmp_path)
    record_failures(path, [{"isrc": "AAAA00000000", "reason": "youtube_failed"}])

    entries = load_failures(path)
    assert len(entries) == 1
    assert entries[0]["isrc"] == "AAAA00000000"
    assert entries[0]["at"] > 0


def test_load_missing_file_is_empty(tmp_path: Path) -> None:
    assert load_failures(_path(tmp_path)) == []


def test_load_corrupt_file_is_empty(tmp_path: Path) -> None:
    path = Path(_path(tmp_path))
    path.write_text("{not json", encoding="utf-8")
    assert load_failures(str(path)) == []


def test_same_isrc_is_not_duplicated(tmp_path: Path) -> None:
    """Re-failing a track updates its entry instead of stacking copies."""
    path = _path(tmp_path)
    record_failures(path, [{"isrc": "AAAA00000000", "detail": "youtube_blocked"}])
    record_failures(path, [{"isrc": "AAAA00000000", "detail": "youtube_not_found"}])

    entries = load_failures(path)
    assert len(entries) == 1
    assert entries[0]["detail"] == "youtube_not_found"


def test_entries_without_isrc_are_ignored(tmp_path: Path) -> None:
    path = _path(tmp_path)
    record_failures(path, [{"reason": "youtube_failed"}])
    assert load_failures(path) == []


# ── Clearing ───────────────────────────────────────────────────────────────


def test_clear_specific_isrcs(tmp_path: Path) -> None:
    path = _path(tmp_path)
    record_failures(path, [{"isrc": "AAAA00000000"}, {"isrc": "BBBB00000000"}])

    clear_failures(path, ["aaaa00000000"])  # case-insensitive

    assert [entry["isrc"] for entry in load_failures(path)] == ["BBBB00000000"]


def test_clear_everything(tmp_path: Path) -> None:
    path = _path(tmp_path)
    record_failures(path, [{"isrc": "AAAA00000000"}])
    clear_failures(path)
    assert load_failures(path) == []


# ── Cooldown ───────────────────────────────────────────────────────────────


def test_recent_permanent_failure_is_reported(tmp_path: Path) -> None:
    path = _path(tmp_path)
    record_failures(path, [{"isrc": "AAAA00000000", "detail": "youtube_blocked"}])

    assert "AAAA00000000" in recent_permanent_failures(path)


def test_transient_failure_is_never_skipped(tmp_path: Path) -> None:
    """A timeout may well succeed on the next click — don't suppress it."""
    path = _path(tmp_path)
    record_failures(path, [{"isrc": "AAAA00000000", "detail": "youtube_timeout"}])

    assert recent_permanent_failures(path) == {}


def test_expired_failure_is_retried(tmp_path: Path) -> None:
    path = _path(tmp_path)
    record_failures(
        path,
        [
            {
                "isrc": "AAAA00000000",
                "detail": "youtube_blocked",
                "at": time.time() - COOLDOWN_SECONDS - 1,
            }
        ],
    )

    assert recent_permanent_failures(path) == {}


def test_store_is_bounded(tmp_path: Path) -> None:
    """The store must not grow without limit."""
    path = _path(tmp_path)
    record_failures(
        path,
        [{"isrc": f"AA{index:010d}", "at": index} for index in range(500)],
    )

    stored = json.loads(Path(path).read_text(encoding="utf-8"))["entries"]
    assert len(stored) == 200
    # The newest ones are the ones kept.
    assert stored[-1]["isrc"] == "AA0000000499"
