"""Exhaustive importer tests — duration boundaries, cover handling, retry."""

import os
from pathlib import Path
from unittest.mock import patch

from music_manager.core.config import Paths
from music_manager.core.models import PendingTrack, Track
from music_manager.pipeline.importer import (
    _download_first_usable,
    _download_with_retry,
    cleanup_covers,
    discard_pending,
    download_cover,
    import_resolved_track,
)
from music_manager.services.albums import Albums
from music_manager.services.tracks import Tracks
from music_manager.services.youtube import (
    ERROR_BLOCKED,
    ERROR_OTHER,
    ERROR_UNAVAILABLE,
    DownloadError,
)

_PATCH = "music_manager.pipeline.importer"


def _track(**overrides) -> Track:
    defaults = {
        "isrc": "ISRC123",
        "title": "Song",
        "artist": "Artist",
        "album": "Album",
        "deezer_id": 1,
        "album_id": 1,
        "duration": 200,
        "cover_url": "https://cover.jpg",
    }
    defaults.update(overrides)
    return Track(**defaults)


def _paths(tmp_path: Path) -> Paths:
    return Paths(str(tmp_path / "data"))


# ── Duration boundary tests ───────────────────────────────────────────────


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}._cleanup")
@patch(f"{_PATCH}.import_file", return_value="AP1")
@patch(f"{_PATCH}.tag_audio_file")
@patch(f"{_PATCH}.download_track", return_value=("/tmp/x.m4a", 186))
@patch(f"{_PATCH}.search_by_isrc_detailed", return_value=([{"url": "u"}], ""))
def test_duration_exactly_093(m1, m2, m3, m4, m5, m6, tmp_path) -> None:
    """Ratio 0.93 exactly → passes (< not <=)."""
    t = Tracks(str(tmp_path / "t.json"))
    a = Albums(str(tmp_path / "a.json"))
    a.put(1, {"cover_url": ""})
    result = import_resolved_track(_track(duration=200), _paths(tmp_path), t, a)
    assert result is None  # 186/200 = 0.93, not < 0.93


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}._cleanup")
@patch(f"{_PATCH}.import_file", return_value="AP1")
@patch(f"{_PATCH}.tag_audio_file")
@patch(f"{_PATCH}.download_track", return_value=("/tmp/x.m4a", 214))
@patch(f"{_PATCH}.search_by_isrc_detailed", return_value=([{"url": "u"}], ""))
def test_duration_exactly_107(m1, m2, m3, m4, m5, m6, tmp_path) -> None:
    """Ratio 1.07 exactly → passes."""
    t = Tracks(str(tmp_path / "t.json"))
    a = Albums(str(tmp_path / "a.json"))
    a.put(1, {"cover_url": ""})
    result = import_resolved_track(_track(duration=200), _paths(tmp_path), t, a)
    assert result is None  # 214/200 = 1.07, not > 1.07


@patch(f"{_PATCH}.download_track", return_value=("/tmp/x.m4a", 185))
@patch(f"{_PATCH}.search_by_isrc_detailed", return_value=([{"url": "u"}], ""))
def test_duration_below_093(m1, m2, tmp_path) -> None:
    """Ratio 0.925 → duration_suspect."""
    t = Tracks(str(tmp_path / "t.json"))
    a = Albums(str(tmp_path / "a.json"))
    a.put(1, {"cover_url": ""})
    result = import_resolved_track(_track(duration=200), _paths(tmp_path), t, a)
    assert result is not None
    assert result.reason == "duration_suspect"


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}._cleanup")
@patch(f"{_PATCH}.import_file", return_value="AP1")
@patch(f"{_PATCH}.tag_audio_file")
@patch(f"{_PATCH}.download_track", return_value=("/tmp/x.m4a", 300))
@patch(f"{_PATCH}.search_by_isrc_detailed", return_value=([{"url": "u"}], ""))
def test_duration_zero_skips_check(m1, m2, m3, m4, m5, m6, tmp_path) -> None:
    """track.duration=0 → duration check skipped."""
    t = Tracks(str(tmp_path / "t.json"))
    a = Albums(str(tmp_path / "a.json"))
    a.put(1, {"cover_url": ""})
    result = import_resolved_track(_track(duration=0), _paths(tmp_path), t, a)
    assert result is None


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}._cleanup")
@patch(f"{_PATCH}.import_file", return_value="AP1")
@patch(f"{_PATCH}.tag_audio_file")
@patch(f"{_PATCH}.download_track", return_value=("/tmp/x.m4a", None))
@patch(f"{_PATCH}.search_by_isrc_detailed", return_value=([{"url": "u"}], ""))
def test_duration_none_skips_check(m1, m2, m3, m4, m5, m6, tmp_path) -> None:
    """actual_duration=None → duration check skipped."""
    t = Tracks(str(tmp_path / "t.json"))
    a = Albums(str(tmp_path / "a.json"))
    a.put(1, {"cover_url": ""})
    result = import_resolved_track(_track(duration=200), _paths(tmp_path), t, a)
    assert result is None


# ── CSV label fallback ────────────────────────────────────────────────────


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}._cleanup")
@patch(f"{_PATCH}.import_file", return_value="AP1")
@patch(f"{_PATCH}.tag_audio_file")
@patch(f"{_PATCH}.download_track", return_value=("/tmp/x.m4a", 200))
@patch(f"{_PATCH}.search_by_isrc_detailed", return_value=([{"url": "u"}], ""))
def test_csv_labels_fallback_to_track(m1, m2, m3, m4, m5, m6, tmp_path) -> None:
    """Empty csv_title/artist/album → falls back to track fields."""
    t = Tracks(str(tmp_path / "t.json"))
    a = Albums(str(tmp_path / "a.json"))
    a.put(1, {"cover_url": ""})
    track = _track(title="Deezer Title", artist="Deezer Artist", album="Deezer Album")
    import_resolved_track(track, _paths(tmp_path), t, a, csv_title="", csv_artist="", csv_album="")
    entry = t.get_by_apple_id("AP1")
    assert entry is not None
    assert entry["csv_title"] == "Deezer Title"
    assert entry["csv_artist"] == "Deezer Artist"
    assert entry["csv_album"] == "Deezer Album"


# ── Cover handling ────────────────────────────────────────────────────────


def test_cover_reuse_existing_file(tmp_path: Path) -> None:
    """Existing cover file is reused, not re-downloaded."""
    paths = _paths(tmp_path)
    os.makedirs(paths.tmp_dir, exist_ok=True)
    cover_file = os.path.join(paths.tmp_dir, "cover_1.jpg")
    with open(cover_file, "wb") as f:
        f.write(b"fake image")

    albums = Albums(str(tmp_path / "a.json"))
    albums.put(1, {"cover_url": "https://should-not-be-called.jpg"})

    result = download_cover(_track(album_id=1), paths, albums)
    assert result == cover_file  # reused, no HTTP


def test_cover_download_falls_back_to_itunes_search(tmp_path: Path) -> None:
    """If the cached cover URL fails, try a fresh iTunes artwork result."""
    paths = _paths(tmp_path)
    albums = Albums(str(tmp_path / "a.json"))
    albums.put(1, {"cover_url": "https://stale-cover.jpg"})

    with (
        patch(
            "music_manager.services.resolver.download_cover_file",
            side_effect=["", "/tmp/fallback.jpg"],
        ) as mock_download,
        patch(
            "music_manager.services.resolver.search_itunes_covers",
            return_value=[{"url": "https://fresh-cover.jpg"}],
        ) as mock_search,
    ):
        result = download_cover(_track(album_id=1), paths, albums)

    assert result == "/tmp/fallback.jpg"
    assert mock_download.call_count == 2
    mock_search.assert_called_once_with("Album", "Artist")


def test_cleanup_covers_only_removes_cover_files(tmp_path: Path) -> None:
    """cleanup_covers removes cover_* files but keeps others."""
    os.makedirs(str(tmp_path / "tmp"), exist_ok=True)
    for name in ("cover_1.jpg", "cover_2.png", "song.m4a", "data.json"):
        with open(str(tmp_path / "tmp" / name), "w") as f:
            f.write("x")

    cleanup_covers(str(tmp_path / "tmp"))

    remaining = os.listdir(str(tmp_path / "tmp"))
    assert "song.m4a" in remaining
    assert "data.json" in remaining
    assert "cover_1.jpg" not in remaining
    assert "cover_2.png" not in remaining


# ── Retry logic ───────────────────────────────────────────────────────────


@patch(f"{_PATCH}.time.sleep")
@patch(f"{_PATCH}.download_track")
def test_download_with_retry_succeeds_second_try(mock_dl, mock_sleep) -> None:
    """First attempt fails, second succeeds."""
    mock_dl.side_effect = [RuntimeError("fail"), ("/tmp/x.m4a", 200)]
    path, dur, error = _download_with_retry("url", "/tmp")
    assert path == "/tmp/x.m4a"
    assert error == ""
    assert mock_dl.call_count == 2


@patch(f"{_PATCH}.time.sleep")
@patch(f"{_PATCH}.download_track", side_effect=RuntimeError("fail"))
def test_download_with_retry_all_fail(mock_dl, mock_sleep) -> None:
    """All 3 attempts fail → returns None."""
    path, dur, error = _download_with_retry("url", "/tmp")
    assert path is None
    assert dur is None
    assert error == ERROR_OTHER
    assert mock_dl.call_count == 3


@patch(f"{_PATCH}.time.sleep")
@patch(f"{_PATCH}.download_track", side_effect=DownloadError("403", ERROR_BLOCKED))
def test_download_blocked_is_not_retried(mock_dl, mock_sleep) -> None:
    """A 403 answers identically every time → fail fast, don't burn 3 requests.

    Regression: the old retry loop re-requested the same blocked URL three
    times with 3s/9s sleeps, guaranteeing failure and wasting rate budget.
    """
    path, _, error = _download_with_retry("url", "/tmp")
    assert path is None
    assert error == ERROR_BLOCKED
    assert mock_dl.call_count == 1
    mock_sleep.assert_not_called()


@patch(f"{_PATCH}.time.sleep")
@patch(f"{_PATCH}.download_track")
def test_download_falls_back_to_next_candidate(mock_dl, mock_sleep) -> None:
    """The best match being blocked must not fail the whole track."""
    mock_dl.side_effect = [
        DownloadError("403", ERROR_BLOCKED),
        ("/tmp/second.m4a", 200),
    ]
    candidates = [{"url": "first"}, {"url": "second"}]
    path, dur, index, error = _download_first_usable(candidates, "/tmp")
    assert path == "/tmp/second.m4a"
    assert index == 1
    assert error == ""


@patch(f"{_PATCH}.time.sleep")
@patch(f"{_PATCH}.download_track", side_effect=DownloadError("gone", ERROR_UNAVAILABLE))
def test_download_first_usable_reports_last_error(mock_dl, mock_sleep) -> None:
    """Every candidate exhausted → the last failure code reaches the caller."""
    path, _, index, error = _download_first_usable([{"url": "a"}, {"url": "b"}], "/tmp")
    assert path is None
    assert index == -1
    assert error == ERROR_UNAVAILABLE
    assert mock_dl.call_count == 2


def test_discard_pending_removes_leftover_audio(tmp_path: Path) -> None:
    """The widget worker has no review step → it must delete the .m4a itself.

    Regression: duration-suspect downloads leaked into .tmp/ forever.
    """
    leftover = tmp_path / "x.m4a"
    leftover.write_bytes(b"audio")
    pending = PendingTrack(reason="duration_suspect", dl_path=str(leftover))

    discard_pending(pending)

    assert not leftover.exists()
    assert pending.dl_path == ""


def test_cleanup_covers_also_removes_playlist_artwork(tmp_path: Path) -> None:
    """playlist_cover.jpg had no owner and survived every run."""
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    (tmp_dir / "cover_1.jpg").write_bytes(b"a")
    (tmp_dir / "playlist_cover.jpg").write_bytes(b"b")
    (tmp_dir / "keep.json").write_bytes(b"c")

    cleanup_covers(str(tmp_dir))

    assert {p.name for p in tmp_dir.iterdir()} == {"keep.json"}
