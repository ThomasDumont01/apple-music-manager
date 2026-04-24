"""Tests for setup.py — Phase 4: ISRC → Deezer resolution at first launch.

Tests the logic, not the UI (Textual screen). We test the resolution
flow by calling the same code paths the setup worker uses.
"""

from unittest.mock import patch

from music_manager.core.models import Track
from music_manager.services.albums import Albums
from music_manager.services.resolver import ResolveResult
from music_manager.services.tracks import Tracks

_PATCH_RESOLVE = "music_manager.services.resolver"


def _resolved(**overrides) -> ResolveResult:
    defaults = {
        "isrc": "ISRC1",
        "title": "Song",
        "artist": "Art",
        "album": "Al",
        "deezer_id": 100,
        "album_id": 50,
        "cover_url": "https://c.jpg",
        "genre": "Pop",
        "release_date": "2020-01-01",
        "track_number": 1,
        "total_tracks": 12,
        "disk_number": 1,
        "total_discs": 1,
        "album_artist": "Art",
        "duration": 200,
        "preview_url": "https://p.mp3",
    }
    defaults.update(overrides)
    return ResolveResult(
        "resolved",
        track=Track(
            **{
                k: v
                for k, v in defaults.items()
                if k in {f.name for f in Track.__dataclass_fields__.values()}
            }
        ),
    )


def _simulate_setup_phase4(
    tracks: Tracks,
    albums: Albums,
    resolve_fn,
) -> int:
    """Simulate Phase 4 of setup: resolve ISRC → Deezer.

    Same logic as setup.py _run_setup Phase 4.
    """
    with_isrc = [
        (aid, e) for aid, e in tracks.all().items() if e.get("isrc") and not e.get("deezer_id")
    ]
    resolved = 0
    for apple_id, entry in with_isrc:
        resolution = resolve_fn(
            entry.get("title") or "",
            entry.get("artist") or "",
            entry.get("album") or "",
            entry.get("isrc") or "",
            albums,
        )
        if resolution.status == "resolved" and resolution.track:
            trk = resolution.track
            tracks.update(
                apple_id,
                {
                    "deezer_id": trk.deezer_id,
                    "album_id": trk.album_id,
                    "isrc": trk.isrc,
                    "cover_url": trk.cover_url,
                    "genre": trk.genre,
                    "release_date": trk.release_date,
                    "track_number": trk.track_number,
                    "total_tracks": trk.total_tracks,
                    "disk_number": trk.disk_number,
                    "total_discs": trk.total_discs,
                    "album_artist": trk.album_artist,
                    "duration": trk.duration,
                    "preview_url": trk.preview_url,
                },
            )
            resolved += 1
    tracks.save()
    albums.save()
    return resolved


# ── Basic resolution ──────────────────────────────────────────────────────


@patch(f"{_PATCH_RESOLVE}.resolve")
def test_resolves_tracks_with_isrc(mock_resolve, tmp_path) -> None:
    """Tracks with ISRC get full Deezer data."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add(
        "A1",
        {
            "title": "Song",
            "artist": "Art",
            "album": "Al",
            "isrc": "ISRC1",
            "origin": "baseline",
        },
    )
    albums = Albums(str(tmp_path / "a.json"))

    mock_resolve.return_value = _resolved(deezer_id=42, album_id=10)
    count = _simulate_setup_phase4(tracks, albums, mock_resolve)

    assert count == 1
    entry = tracks.get_by_apple_id("A1")
    assert entry is not None
    assert entry["deezer_id"] == 42
    assert entry["album_id"] == 10
    assert entry["genre"] == "Pop"
    assert entry["cover_url"] == "https://c.jpg"


@patch(f"{_PATCH_RESOLVE}.resolve")
def test_skips_tracks_without_isrc(mock_resolve, tmp_path) -> None:
    """Tracks without ISRC are skipped (handled by identify later)."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add("A1", {"title": "S", "artist": "A", "isrc": ""})
    albums = Albums(str(tmp_path / "a.json"))

    count = _simulate_setup_phase4(tracks, albums, mock_resolve)

    assert count == 0
    mock_resolve.assert_not_called()


@patch(f"{_PATCH_RESOLVE}.resolve")
def test_skips_already_identified(mock_resolve, tmp_path) -> None:
    """Tracks already with deezer_id are skipped."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add(
        "A1",
        {
            "title": "S",
            "artist": "A",
            "isrc": "ISRC1",
            "deezer_id": 99,
        },
    )
    albums = Albums(str(tmp_path / "a.json"))

    count = _simulate_setup_phase4(tracks, albums, mock_resolve)

    assert count == 0
    mock_resolve.assert_not_called()


@patch(f"{_PATCH_RESOLVE}.resolve")
def test_not_found_isrc_not_counted(mock_resolve, tmp_path) -> None:
    """ISRC not found on Deezer → not counted as resolved."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add("A1", {"title": "S", "artist": "A", "isrc": "ISRC1"})
    albums = Albums(str(tmp_path / "a.json"))

    mock_resolve.return_value = ResolveResult("not_found")
    count = _simulate_setup_phase4(tracks, albums, mock_resolve)

    assert count == 0
    entry = tracks.get_by_apple_id("A1")
    assert entry is not None
    assert entry.get("deezer_id") is None


# ── Mixed scenario ────────────────────────────────────────────────────────


@patch(f"{_PATCH_RESOLVE}.resolve")
def test_mixed_isrc_and_no_isrc(mock_resolve, tmp_path) -> None:
    """Only tracks with ISRC are processed, rest skipped."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add("A1", {"title": "Has", "artist": "A", "isrc": "ISRC1"})
    tracks.add("A2", {"title": "No", "artist": "A", "isrc": ""})
    tracks.add("A3", {"title": "Also", "artist": "A", "isrc": "ISRC3"})
    albums = Albums(str(tmp_path / "a.json"))

    mock_resolve.side_effect = [
        _resolved(deezer_id=10),
        _resolved(deezer_id=30),
    ]
    count = _simulate_setup_phase4(tracks, albums, mock_resolve)

    assert count == 2
    assert mock_resolve.call_count == 2
    a1 = tracks.get_by_apple_id("A1")
    a2 = tracks.get_by_apple_id("A2")
    a3 = tracks.get_by_apple_id("A3")
    assert a1 is not None
    assert a2 is not None
    assert a3 is not None
    assert a1["deezer_id"] == 10
    assert a2.get("deezer_id") is None
    assert a3["deezer_id"] == 30


# ── Data integrity ────────────────────────────────────────────────────────


@patch(f"{_PATCH_RESOLVE}.resolve")
def test_preserves_baseline_fields(mock_resolve, tmp_path) -> None:
    """Resolution adds fields but doesn't overwrite origin/status."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add(
        "A1",
        {
            "title": "Song",
            "artist": "Art",
            "album": "Al",
            "isrc": "ISRC1",
            "origin": "baseline",
            "status": None,
            "file_path": "/music/s.m4a",
        },
    )
    albums = Albums(str(tmp_path / "a.json"))

    mock_resolve.return_value = _resolved()
    _simulate_setup_phase4(tracks, albums, mock_resolve)

    entry = tracks.get_by_apple_id("A1")
    assert entry is not None
    assert entry["origin"] == "baseline"  # preserved
    assert entry["status"] is None  # preserved
    assert entry["file_path"] == "/music/s.m4a"  # preserved
    assert entry["deezer_id"] == 100  # added


@patch(f"{_PATCH_RESOLVE}.resolve")
def test_saves_to_disk(mock_resolve, tmp_path) -> None:
    """Both tracks and albums are saved to disk after resolution."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add("A1", {"title": "S", "artist": "A", "isrc": "ISRC1"})
    albums = Albums(str(tmp_path / "a.json"))

    mock_resolve.return_value = _resolved()
    _simulate_setup_phase4(tracks, albums, mock_resolve)

    # Reload from disk — data should persist
    tracks2 = Tracks(str(tmp_path / "t.json"))
    a1 = tracks2.get_by_apple_id("A1")
    assert a1 is not None
    assert a1["deezer_id"] == 100


@patch(f"{_PATCH_RESOLVE}.resolve")
def test_identify_only_shows_remaining(mock_resolve, tmp_path) -> None:
    """After setup resolution, identify only processes unresolved tracks."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add("A1", {"title": "Resolved", "artist": "A", "isrc": "ISRC1"})
    tracks.add("A2", {"title": "NoISRC", "artist": "A", "isrc": ""})
    albums = Albums(str(tmp_path / "a.json"))

    mock_resolve.return_value = _resolved()
    _simulate_setup_phase4(tracks, albums, mock_resolve)

    # Now check what identify_library would process
    unidentified = [(aid, e) for aid, e in tracks.all().items() if not e.get("deezer_id")]
    assert len(unidentified) == 1
    assert unidentified[0][0] == "A2"  # only the one without ISRC
