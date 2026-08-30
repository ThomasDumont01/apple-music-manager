"""Tests for music_manager/cli/lock.py."""

import json
import os
from pathlib import Path

import pytest

from music_manager.cli import lock


@pytest.fixture
def lock_path(tmp_path: Path) -> str:
    return str(tmp_path / "subdir" / ".test.lock")


# ── acquire / release ──────────────────────────────────────────────────────


def _payload(lock_path: str) -> dict:
    return json.loads(Path(lock_path).read_text())


def test_acquire_release_roundtrip(lock_path: str) -> None:
    """A lock acquired by the current PID can be released by the same."""
    assert lock.acquire_lock(lock_path)
    assert _payload(lock_path)["pid"] == os.getpid()
    lock.release_lock(lock_path)
    assert not Path(lock_path).exists()


def test_acquire_records_process_start_time(lock_path: str) -> None:
    """The holder's start time is stored so a recycled PID can be detected."""
    assert lock.acquire_lock(lock_path)
    assert _payload(lock_path)["started_at"]
    lock.release_lock(lock_path)


def test_acquire_creates_parent_directory(tmp_path: Path) -> None:
    """Parent directories are created on demand — the lock path lives wherever."""
    deep = str(tmp_path / "a" / "b" / "c" / ".lock")
    assert lock.acquire_lock(deep)
    assert Path(deep).exists()
    lock.release_lock(deep)


def test_release_no_op_when_missing(lock_path: str) -> None:
    """Releasing a non-existing lock must never raise."""
    lock.release_lock(lock_path)


def test_release_only_owns_pid(lock_path: str, tmp_path: Path) -> None:
    """A lock held by another PID is not removed by release()."""
    Path(lock_path).parent.mkdir(parents=True, exist_ok=True)
    Path(lock_path).write_text("999999")  # arbitrary foreign PID
    lock.release_lock(lock_path)
    # The file still exists because we don't own it.
    assert Path(lock_path).exists()


# ── is_locked ──────────────────────────────────────────────────────────────


def test_is_locked_false_when_missing(lock_path: str) -> None:
    """An absent lock file is not locked."""
    assert not lock.is_locked(lock_path)


def test_is_locked_true_for_live_pid(lock_path: str) -> None:
    """A lock holding the current PID counts as locked."""
    lock.acquire_lock(lock_path)
    assert lock.is_locked(lock_path)
    lock.release_lock(lock_path)


def test_is_locked_false_for_dead_pid(lock_path: str) -> None:
    """A stale lock (PID dead) is reported as unlocked → can be reclaimed."""
    Path(lock_path).parent.mkdir(parents=True, exist_ok=True)
    # PID 0 is never a valid process; large unallocated PIDs are also safe.
    Path(lock_path).write_text("0")
    assert not lock.is_locked(lock_path)


def test_is_locked_false_for_garbage_content(lock_path: str) -> None:
    """A lock file with non-integer content is treated as absent."""
    Path(lock_path).parent.mkdir(parents=True, exist_ok=True)
    Path(lock_path).write_text("not-a-pid")
    assert not lock.is_locked(lock_path)


def test_acquire_fails_when_held_by_live_pid(lock_path: str) -> None:
    """Two concurrent processes can't both hold the same lock."""
    Path(lock_path).parent.mkdir(parents=True, exist_ok=True)
    # Simulate another live process by reusing our own PID under the lock.
    Path(lock_path).write_text(str(os.getpid()))
    assert not lock.acquire_lock(lock_path)


def test_acquire_reclaims_stale_lock(lock_path: str) -> None:
    """A stale lock (PID dead) is reclaimed by the next acquirer."""
    Path(lock_path).parent.mkdir(parents=True, exist_ok=True)
    Path(lock_path).write_text("0")
    assert lock.acquire_lock(lock_path)
    assert _payload(lock_path)["pid"] == os.getpid()
    lock.release_lock(lock_path)


# ── PID reuse ──────────────────────────────────────────────────────────────


def test_recycled_pid_does_not_hold_the_lock(lock_path: str) -> None:
    """A live PID that isn't the original holder must not keep the lock.

    Regression: a .ui.lock orphaned by a killed UI sat on disk for weeks. The
    day macOS handed its PID to an unrelated process, every widget import
    would have been refused with "ui_running" forever.
    """
    Path(lock_path).parent.mkdir(parents=True, exist_ok=True)
    Path(lock_path).write_text(
        json.dumps({"pid": os.getpid(), "started_at": "Thu  1 Jan 00:00:00 1970"})
    )
    assert not lock.is_locked(lock_path)
    assert lock.acquire_lock(lock_path)
    lock.release_lock(lock_path)


def test_legacy_bare_pid_lock_is_still_honoured(lock_path: str) -> None:
    """Upgrading must not invalidate a lock held by a running instance."""
    Path(lock_path).parent.mkdir(parents=True, exist_ok=True)
    Path(lock_path).write_text(str(os.getpid()))
    assert lock.is_locked(lock_path)


# ── clear_stale_lock ───────────────────────────────────────────────────────


def test_clear_stale_lock_removes_dead_holder(lock_path: str) -> None:
    """A crash-orphaned lock is dropped at startup instead of lingering."""
    Path(lock_path).parent.mkdir(parents=True, exist_ok=True)
    Path(lock_path).write_text("0")
    assert lock.clear_stale_lock(lock_path)
    assert not Path(lock_path).exists()


def test_clear_stale_lock_keeps_live_holder(lock_path: str) -> None:
    """A genuinely held lock survives the startup sweep."""
    lock.acquire_lock(lock_path)
    try:
        assert not lock.clear_stale_lock(lock_path)
        assert Path(lock_path).exists()
    finally:
        lock.release_lock(lock_path)


def test_clear_stale_lock_no_op_when_missing(lock_path: str) -> None:
    assert not lock.clear_stale_lock(lock_path)


# ── lock_owner_pid ─────────────────────────────────────────────────────────


def test_lock_owner_pid_returns_pid(lock_path: str) -> None:
    lock.acquire_lock(lock_path)
    assert lock.lock_owner_pid(lock_path) == os.getpid()
    lock.release_lock(lock_path)


def test_lock_owner_pid_none_when_missing(lock_path: str) -> None:
    assert lock.lock_owner_pid(lock_path) is None
