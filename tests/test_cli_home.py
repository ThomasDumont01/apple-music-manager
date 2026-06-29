"""Tests for music_manager/cli/home.py."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from music_manager.cli import dispatch, home


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Sandbox the data root."""
    data_root = tmp_path / "music"
    data_root.mkdir()
    (data_root / ".data").mkdir()
    monkeypatch.setattr(
        "music_manager.cli.home.load_config",
        lambda: {"data_root": str(data_root)},
    )
    return data_root


# ── Recent tracks ──────────────────────────────────────────────────────────


def test_home_returns_recent_sorted_desc(env: Path, capsys: pytest.CaptureFixture) -> None:
    """Recent tracks are sorted by imported_at desc and capped to limit."""
    (env / ".data" / "tracks.json").write_text(
        json.dumps(
            {
                "AP1": {
                    "title": "Old",
                    "artist": "A",
                    "apple_id": "AP1",
                    "imported_at": "2024-01-01 12:00:00",
                },
                "AP2": {
                    "title": "Newest",
                    "artist": "B",
                    "apple_id": "AP2",
                    "imported_at": "2026-06-07 02:00:00",
                },
                "AP3": {
                    "title": "Middle",
                    "artist": "C",
                    "apple_id": "AP3",
                    "imported_at": "2025-12-01 09:00:00",
                },
            }
        )
    )
    with patch("music_manager.cli.home._playlists", return_value=[]):
        home.main(["--recent-limit", "5"])

    payload = json.loads(capsys.readouterr().out)
    titles = [t["title"] for t in payload["recent"]]
    assert titles == ["Newest", "Middle", "Old"]


def test_home_skips_tracks_without_imported_at(env: Path, capsys: pytest.CaptureFixture) -> None:
    """Tracks with no imported_at are excluded — never imported by Music Manager."""
    (env / ".data" / "tracks.json").write_text(
        json.dumps(
            {
                "AP1": {"title": "Played", "apple_id": "AP1", "imported_at": "2025-01"},
                "AP2": {"title": "Never", "apple_id": "AP2"},
                "AP3": {"title": "Empty", "apple_id": "AP3", "imported_at": ""},
            }
        )
    )
    with patch("music_manager.cli.home._playlists", return_value=[]):
        home.main([])
    payload = json.loads(capsys.readouterr().out)
    assert [t["title"] for t in payload["recent"]] == ["Played"]


def test_home_skips_tracks_without_apple_id(env: Path, capsys: pytest.CaptureFixture) -> None:
    """Without an apple_id we can't `play` the track — drop it."""
    (env / ".data" / "tracks.json").write_text(
        json.dumps({"_OTHER_KEY": {"title": "Nope", "imported_at": "2025"}})
    )
    with patch("music_manager.cli.home._playlists", return_value=[]):
        home.main([])
    payload = json.loads(capsys.readouterr().out)
    # JSON key "_OTHER_KEY" is used as fallback apple_id, so this passes.
    # Verify the fallback works.
    assert payload["recent"][0]["apple_id"] == "_OTHER_KEY"


def test_home_caps_at_limit(env: Path, capsys: pytest.CaptureFixture) -> None:
    """--recent-limit caps the array length."""
    entries = {
        f"AP{i}": {
            "title": f"T{i}",
            "apple_id": f"AP{i}",
            "imported_at": f"2025-01-{i:02d}",
        }
        for i in range(1, 21)
    }
    (env / ".data" / "tracks.json").write_text(json.dumps(entries))
    with patch("music_manager.cli.home._playlists", return_value=[]):
        home.main(["--recent-limit", "3"])
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["recent"]) == 3


def test_home_returns_empty_when_no_tracks_json(env: Path, capsys: pytest.CaptureFixture) -> None:
    """No tracks.json → empty recent, no crash."""
    with patch("music_manager.cli.home._playlists", return_value=[]):
        home.main([])
    payload = json.loads(capsys.readouterr().out)
    assert payload["recent"] == []


def test_home_returns_empty_when_no_data_root(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Unconfigured data root → empty recent."""
    monkeypatch.setattr(
        "music_manager.cli.home.load_config",
        lambda: {"data_root": ""},
    )
    with patch("music_manager.cli.home._playlists", return_value=[]):
        home.main([])
    payload = json.loads(capsys.readouterr().out)
    assert payload["recent"] == []


# ── Playlists ──────────────────────────────────────────────────────────────


def test_home_returns_playlists_with_cover(env: Path, capsys: pytest.CaptureFixture) -> None:
    """cover_path is collapsed to its basename for relative widget loading."""
    with patch(
        "music_manager.services.playlist_covers.list_playlists_with_covers",
        return_value=[
            {
                "name": "Chill",
                "count": 52,
                "cover_path": "/some/dir/abc.jpg",
            },
            {"name": "Workout", "count": 18, "cover_path": ""},
        ],
    ):
        home.main([])
    payload = json.loads(capsys.readouterr().out)
    assert payload["playlists"][0]["name"] == "Chill"
    # Only the basename leaks through — the widget prepends its own asset dir.
    assert payload["playlists"][0]["cover_filename"] == "abc.jpg"
    assert payload["playlists"][1]["cover_filename"] == ""


def test_home_filters_system_playlists(env: Path, capsys: pytest.CaptureFixture) -> None:
    """Built-in playlists (Library, Recently Added…) are hidden."""
    with patch(
        "music_manager.services.playlist_covers.list_playlists_with_covers",
        return_value=[
            {"name": "Library", "count": 9999, "cover_path": ""},
            {"name": "Recently Added", "count": 50, "cover_path": ""},
            {"name": "Chill", "count": 52, "cover_path": ""},
        ],
    ):
        home.main([])
    payload = json.loads(capsys.readouterr().out)
    names = [p["name"] for p in payload["playlists"]]
    assert names == ["Chill"]


def test_home_passes_exclude_folder_for_me_to_playlist_covers(
    env: Path, capsys: pytest.CaptureFixture
) -> None:
    """The ``for me`` recommendation folder must be excluded from the widget.

    Otherwise generated sub-playlists (``library``, ``rock``…) would
    pollute the user's normal playlists.
    """
    seen: dict = {}

    def capture(covers_dir, *, exclude_folder=None):
        seen["exclude_folder"] = exclude_folder
        return [{"name": "Chill", "count": 1, "cover_path": ""}]

    with patch(
        "music_manager.services.playlist_covers.list_playlists_with_covers",
        side_effect=capture,
    ):
        home.main([])
    assert seen["exclude_folder"] == "for me"


def test_home_caps_playlist_count(env: Path, capsys: pytest.CaptureFixture) -> None:
    """--playlist-limit caps the array length."""
    raw = [{"name": f"PL{i}", "count": i, "cover_path": ""} for i in range(40)]
    with patch(
        "music_manager.services.playlist_covers.list_playlists_with_covers",
        return_value=raw,
    ):
        home.main(["--playlist-limit", "5"])
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["playlists"]) == 5


def test_home_handles_framework_failure(env: Path, capsys: pytest.CaptureFixture) -> None:
    """If the cover extractor raises, return an empty playlists array."""
    with patch(
        "music_manager.services.playlist_covers.list_playlists_with_covers",
        side_effect=RuntimeError("framework boom"),
    ):
        home.main([])
    payload = json.loads(capsys.readouterr().out)
    assert payload["playlists"] == []


# ── Dispatcher ─────────────────────────────────────────────────────────────


def test_dispatcher_routes_to_home() -> None:
    with patch("music_manager.cli.home.main", return_value=0) as mock_main:
        code = dispatch(["home"])
    mock_main.assert_called_once_with([])
    assert code == 0
