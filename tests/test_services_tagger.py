"""Integration tests for ISRC tag scanning."""

import time

import pytest

from music_manager.services.apple import Apple
from music_manager.services.tagger import scan_isrc

pytestmark = pytest.mark.integration


@pytest.fixture()
def library_entries() -> dict:
    """Real Apple Music library entries."""
    apple = Apple()
    return apple.scan()


def test_scan_isrc_finds_tags(library_entries: dict) -> None:
    """scan_isrc finds ISRC tags in real audio files."""
    if not library_entries:
        pytest.skip("Apple Music library is empty")
    found = scan_isrc(library_entries)

    # Some tracks may not have ISRC tags — skip if none found
    if found == 0:
        pytest.skip("No ISRC tags found in library files")
    with_isrc = [e for e in library_entries.values() if e.isrc]
    assert len(with_isrc) == found

    for entry in with_isrc:
        assert len(entry.isrc) == 12, f"Invalid ISRC length: {entry.isrc}"


def test_scan_isrc_performance(library_entries: dict) -> None:
    """scan_isrc completes in reasonable time."""
    start = time.perf_counter()
    scan_isrc(library_entries)
    elapsed = time.perf_counter() - start

    max_time = max(5.0, len(library_entries) * 0.01)
    assert elapsed < max_time, f"scan_isrc took {elapsed:.2f}s for {len(library_entries)} entries"


def test_scan_isrc_progress_callback(library_entries: dict) -> None:
    """Progress callback is called correctly."""
    calls: list[tuple[int, int]] = []
    scan_isrc(library_entries, on_progress=lambda c, t: calls.append((c, t)))

    assert len(calls) > 0
    last_current, last_total = calls[-1]
    assert last_current == last_total
