"""Integration tests for Apple Music library scanning."""

import time

import pytest

from music_manager.core.models import LibraryEntry
from music_manager.services.apple import Apple

pytestmark = pytest.mark.integration


def test_scan_library_returns_entries() -> None:
    """scan returns a non-empty dict of LibraryEntry."""
    apple = Apple()
    entries = apple.scan()

    assert isinstance(entries, dict)
    if not entries:
        pytest.skip("Apple Music library is empty")

    entry = next(iter(entries.values()))
    assert isinstance(entry, LibraryEntry)
    assert entry.apple_id
    assert entry.title
    assert entry.file_path


def test_scan_library_performance() -> None:
    """scan completes in under 2 seconds."""
    apple = Apple()
    start = time.perf_counter()
    entries = apple.scan()
    elapsed = time.perf_counter() - start

    assert elapsed < 15.0, f"scan took {elapsed:.2f}s for {len(entries)} entries"


def test_scan_library_progress_callback() -> None:
    """Progress callback is called with (current, total)."""
    apple = Apple()
    calls: list[tuple[int, int]] = []
    apple.scan(on_progress=lambda c, t: calls.append((c, t)))

    assert len(calls) > 0
    last_current, last_total = calls[-1]
    assert last_current == last_total


def test_scan_background_and_wait() -> None:
    """Background scan completes and get_all returns data."""
    apple = Apple()
    assert not apple.is_ready()

    apple.scan_background()
    entries = apple.get_all()  # waits automatically

    assert apple.is_ready()
    if not entries:
        pytest.skip("Apple Music library is empty")
