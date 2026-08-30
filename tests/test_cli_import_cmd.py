"""Tests for music_manager/cli/import_cmd.py."""

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from music_manager.cli import dispatch, import_cmd
from music_manager.cli.failures import load_failures, record_failures
from music_manager.cli.lock import acquire_lock, release_lock
from music_manager.core.config import Paths
from music_manager.core.models import PendingTrack, Track
from music_manager.services.tracks import Tracks

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Configure a sandboxed data root + CONFIG_DIR."""
    data_root = tmp_path / "music"
    data_root.mkdir()
    (data_root / ".data").mkdir()

    config_dir = tmp_path / "config"
    config_dir.mkdir()

    monkeypatch.setattr("music_manager.core.config.CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(
        "music_manager.core.config.load_config",
        lambda: {"data_root": str(data_root)},
    )
    monkeypatch.setattr(
        "music_manager.cli.import_cmd.load_config",
        lambda: {"data_root": str(data_root)},
    )

    paths = Paths(str(data_root))
    return paths


def _track(isrc: str = "FRABC1234567") -> Track:
    return Track(isrc=isrc, title="Bad Guy", artist="Billie Eilish", album="WAFL")


# ── Argument parsing ───────────────────────────────────────────────────────


def test_parse_isrcs_filters_invalid_tokens() -> None:
    """Anti-injection: only canonical 12-char alphanumeric ISRCs survive."""
    result = import_cmd._parse_isrcs("FRABC1234567,usum71916175,; rm -rf /,SHORTONE,FRABC1234567")
    # `usum71916175` uppercased = USUM71916175 ✓ ; ";" / "SHORTONE" rejected ;
    # duplicate FRABC1234567 deduped.
    assert result == ["FRABC1234567", "USUM71916175"]


def test_parse_isrcs_returns_empty_for_no_valid_input() -> None:
    assert import_cmd._parse_isrcs("") == []
    assert import_cmd._parse_isrcs(",,;,;") == []


def test_main_rejects_invalid_isrc_list(env: Paths) -> None:
    """If nothing parses, exit with EXIT_INVALID."""
    code = import_cmd.main(["NOT-VALID-INPUT"])
    assert code == import_cmd.EXIT_INVALID


# ── UI lock check ──────────────────────────────────────────────────────────


def test_blocked_when_ui_lock_held(env: Paths) -> None:
    """If the Textual UI lock is held by a live PID, refuse + write status."""
    acquire_lock(env.ui_lock_path)
    try:
        code = import_cmd.main(["FRABC1234567"])
    finally:
        release_lock(env.ui_lock_path)
    assert code == import_cmd.EXIT_USAGE
    status = json.loads(Path(env.widget_status_path).read_text())
    assert status["status"] == "blocked"
    assert status["reason"] == "ui_running"


def test_ignores_stale_ui_lock(env: Paths) -> None:
    """A stale UI lock (dead PID) does not block — the import proceeds."""
    Path(env.ui_lock_path).parent.mkdir(parents=True, exist_ok=True)
    Path(env.ui_lock_path).write_text("0")  # PID 0 is never alive
    with (
        patch("music_manager.cli.import_cmd.resolve_by_isrc", return_value=None),
        patch("music_manager.cli.import_cmd.init_logger"),
        patch("music_manager.cli.import_cmd.configure_resolver"),
    ):
        code = import_cmd.main(["FRABC1234567"])
    assert code == import_cmd.EXIT_OK


# ── Detach ─────────────────────────────────────────────────────────────────


def test_detach_returns_immediately(env: Paths) -> None:
    """--detach spawns a detached worker and the foreground call returns OK."""
    with patch("music_manager.cli.import_cmd.subprocess.Popen") as mock_popen:
        code = import_cmd.main(["FRABC1234567", "--detach"])
    mock_popen.assert_called_once()
    args = mock_popen.call_args
    assert args.kwargs.get("start_new_session") is True
    assert code == import_cmd.EXIT_OK


# ── Run logic ──────────────────────────────────────────────────────────────


def test_run_writes_running_then_done_status(env: Paths) -> None:
    """A successful import transitions through running → done with completed list."""

    def fake_import(track: Track, _paths, _tracks, _albums):
        track.apple_id = "AP_" + track.isrc
        return None

    with (
        patch(
            "music_manager.cli.import_cmd.resolve_by_isrc",
            side_effect=lambda isrc, _albums: _track(isrc),
        ),
        patch(
            "music_manager.pipeline.importer.import_resolved_track",
            side_effect=fake_import,
        ),
        patch("music_manager.cli.import_cmd.init_logger"),
        patch("music_manager.cli.import_cmd.configure_resolver"),
    ):
        code = import_cmd.main(["FRABC1234567,USUM71916175"])

    assert code == import_cmd.EXIT_OK
    status = json.loads(Path(env.widget_status_path).read_text())
    assert status["status"] == "done"
    assert status["total"] == 2
    completed_isrcs = [c["isrc"] for c in status["completed"]]
    assert completed_isrcs == ["FRABC1234567", "USUM71916175"]


def test_run_records_unresolved_isrc_as_failed(env: Paths) -> None:
    """ISRC not on Deezer → registered in 'failed' but run completes."""
    with (
        patch("music_manager.cli.import_cmd.resolve_by_isrc", return_value=None),
        patch("music_manager.cli.import_cmd.init_logger"),
        patch("music_manager.cli.import_cmd.configure_resolver"),
    ):
        code = import_cmd.main(["FRABC1234567"])
    assert code == import_cmd.EXIT_OK
    status = json.loads(Path(env.widget_status_path).read_text())
    assert status["failed"] == [
        {
            "isrc": "FRABC1234567",
            "reason": "not_on_deezer",
            "detail": "not_on_deezer",
            "title": "",
            "artist": "",
        }
    ]
    assert status["completed"] == []


def test_run_continues_after_single_failure(env: Paths) -> None:
    """One import raising doesn't abort the whole batch."""

    def flaky_import(track: Track, *_args, **_kwargs):
        if track.isrc.startswith("BAD"):
            raise RuntimeError("yt-dlp boom")
        track.apple_id = "AP_" + track.isrc
        return None

    def fake_resolve(isrc: str, _albums):
        return _track(isrc)

    with (
        patch(
            "music_manager.cli.import_cmd.resolve_by_isrc",
            side_effect=fake_resolve,
        ),
        patch(
            "music_manager.pipeline.importer.import_resolved_track",
            side_effect=flaky_import,
        ),
        patch("music_manager.cli.import_cmd.init_logger"),
        patch("music_manager.cli.import_cmd.configure_resolver"),
    ):
        # Force BAD123456789 to look like a valid ISRC: 12 chars, alphanumeric.
        import_cmd.main(["FRABC1234567,BAD123456789"])

    status = json.loads(Path(env.widget_status_path).read_text())
    assert {c["isrc"] for c in status["completed"]} == {"FRABC1234567"}
    assert {f["isrc"] for f in status["failed"]} == {"BAD123456789"}


def test_run_widget_lock_released_on_exit(env: Paths) -> None:
    """The widget lock is released even when nothing succeeds."""
    with (
        patch("music_manager.cli.import_cmd.resolve_by_isrc", return_value=None),
        patch("music_manager.cli.import_cmd.init_logger"),
        patch("music_manager.cli.import_cmd.configure_resolver"),
    ):
        import_cmd.main(["FRABC1234567"])
    assert not Path(env.widget_lock_path).exists()


def test_widget_busy_when_another_run_holds_lock(env: Paths) -> None:
    """Two concurrent widget imports → second one exits with EXIT_BUSY."""
    acquire_lock(env.widget_lock_path)
    try:
        code = import_cmd.main(["FRABC1234567"])
    finally:
        release_lock(env.widget_lock_path)
    assert code == import_cmd.EXIT_BUSY
    status = json.loads(Path(env.widget_status_path).read_text())
    assert status["status"] == "blocked"
    assert status["reason"] == "widget_busy"


def test_missing_data_root_returns_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a configured data root, the CLI refuses cleanly."""
    monkeypatch.setattr(
        "music_manager.cli.import_cmd.load_config",
        lambda: {"data_root": ""},
    )
    code = import_cmd.main(["FRABC1234567"])
    assert code == import_cmd.EXIT_USAGE


# ── Dispatcher routing ─────────────────────────────────────────────────────


def test_dispatcher_routes_to_status() -> None:
    """The package-level dispatcher routes 'import-status' to the right module."""
    with patch("music_manager.cli.status.main", return_value=0) as mock_main:
        code = dispatch(["import-status"])
    mock_main.assert_called_once_with([])
    assert code == 0


def test_dispatcher_routes_to_search() -> None:
    with patch("music_manager.cli.search.main", return_value=0) as mock_main:
        code = dispatch(["search", "billie"])
    mock_main.assert_called_once_with(["billie"])
    assert code == 0


def test_dispatcher_unknown_command_returns_usage_error() -> None:
    assert dispatch(["nope"]) == 2


def test_dispatcher_empty_args_returns_usage_error() -> None:
    assert dispatch([]) == 2


def test_dispatcher_routes_to_import_isrcs() -> None:
    with patch("music_manager.cli.import_cmd.main", return_value=0) as mock_main:
        code = dispatch(["import-isrcs", "FRABC1234567"])
    mock_main.assert_called_once_with(["FRABC1234567"])
    assert code == 0


# ── Helpers checked separately ─────────────────────────────────────────────


def test_write_status_is_atomic(tmp_path: Path) -> None:
    """The .tmp + replace pattern leaves no half-written file behind."""
    target = tmp_path / "subdir" / "widget_status.json"
    import_cmd._write_status(str(target), {"status": "running"})
    assert json.loads(target.read_text()) == {"status": "running"}
    assert not (tmp_path / "subdir" / "widget_status.json.tmp").exists()


# ── --playlist-name option ─────────────────────────────────────────────────


def test_playlist_name_appends_apple_ids_to_playlist(env: Paths) -> None:
    """With --playlist-name, successful apple_ids are batched into add_to_playlist."""

    def fake_import(track: Track, _paths, _tracks, _albums):
        track.apple_id = "AP_" + track.isrc
        return None

    with (
        patch(
            "music_manager.cli.import_cmd.resolve_by_isrc",
            side_effect=lambda isrc, _albums: _track(isrc),
        ),
        patch(
            "music_manager.pipeline.importer.import_resolved_track",
            side_effect=fake_import,
        ),
        patch("music_manager.cli.import_cmd.init_logger"),
        patch("music_manager.cli.import_cmd.configure_resolver"),
        patch("music_manager.cli.import_cmd.add_to_playlist", return_value=2) as mock_add,
    ):
        code = import_cmd.main(
            [
                "FRABC1234567,USUM71916175",
                "--playlist-name",
                "Mes vibes",
            ]
        )

    assert code == import_cmd.EXIT_OK
    mock_add.assert_called_once_with("Mes vibes", ["AP_FRABC1234567", "AP_USUM71916175"])
    status = json.loads(Path(env.widget_status_path).read_text())
    assert status["playlist_name"] == "Mes vibes"
    assert status["playlist_added"] == 2


def test_playlist_skipped_when_all_failed(env: Paths) -> None:
    """All ISRCs fail → add_to_playlist is NOT called (nothing to add)."""
    with (
        patch("music_manager.cli.import_cmd.resolve_by_isrc", return_value=None),
        patch("music_manager.cli.import_cmd.init_logger"),
        patch("music_manager.cli.import_cmd.configure_resolver"),
        patch("music_manager.cli.import_cmd.add_to_playlist") as mock_add,
    ):
        code = import_cmd.main(["FRABC1234567", "--playlist-name", "Mes vibes"])

    assert code == import_cmd.EXIT_OK
    mock_add.assert_not_called()
    status = json.loads(Path(env.widget_status_path).read_text())
    # Field still recorded so the widget can show "playlist X — 0 ajoutées".
    assert status["playlist_name"] == "Mes vibes"
    assert status["playlist_added"] == 0


def test_playlist_only_collects_successful_apple_ids(env: Paths) -> None:
    """Failed tracks must not pollute the playlist add list."""

    def flaky_import(track: Track, *_args, **_kwargs):
        if track.isrc.startswith("BAD"):
            raise RuntimeError("boom")
        track.apple_id = "AP_" + track.isrc
        return None

    with (
        patch(
            "music_manager.cli.import_cmd.resolve_by_isrc",
            side_effect=lambda isrc, _albums: _track(isrc),
        ),
        patch(
            "music_manager.pipeline.importer.import_resolved_track",
            side_effect=flaky_import,
        ),
        patch("music_manager.cli.import_cmd.init_logger"),
        patch("music_manager.cli.import_cmd.configure_resolver"),
        patch("music_manager.cli.import_cmd.add_to_playlist", return_value=1) as mock_add,
    ):
        import_cmd.main(["FRABC1234567,BAD123456789", "--playlist-name", "Chill"])

    mock_add.assert_called_once_with("Chill", ["AP_FRABC1234567"])


def test_detach_forwards_playlist_name(env: Paths) -> None:
    """--detach must propagate --playlist-name to the spawned worker."""
    with patch("music_manager.cli.import_cmd.subprocess.Popen") as mock_popen:
        code = import_cmd.main(
            [
                "FRABC1234567",
                "--playlist-name",
                "Mes vibes",
                "--detach",
            ]
        )
    assert code == import_cmd.EXIT_OK
    args, kwargs = mock_popen.call_args
    cmd = args[0]
    assert "--playlist-name" in cmd
    assert "Mes vibes" in cmd
    assert kwargs.get("start_new_session") is True


def test_playlist_cover_url_triggers_artwork_set(env: Paths) -> None:
    """--playlist-cover-url downloads the image + sets Apple Music artwork."""

    def fake_import(track: Track, _paths, _tracks, _albums):
        track.apple_id = "AP_" + track.isrc
        return None

    with (
        patch(
            "music_manager.cli.import_cmd.resolve_by_isrc",
            side_effect=lambda isrc, _albums: _track(isrc),
        ),
        patch(
            "music_manager.pipeline.importer.import_resolved_track",
            side_effect=fake_import,
        ),
        patch("music_manager.cli.import_cmd.init_logger"),
        patch("music_manager.cli.import_cmd.configure_resolver"),
        patch("music_manager.cli.import_cmd.add_to_playlist", return_value=1),
        patch(
            "music_manager.services.resolver.download_cover_file",
            return_value="/tmp/playlist_cover.jpg",
        ) as mock_dl,
        patch("music_manager.cli.import_cmd.set_playlist_artwork", return_value=True) as mock_art,
    ):
        code = import_cmd.main(
            [
                "FRABC1234567",
                "--playlist-name",
                "Chill",
                "--playlist-cover-url",
                "https://e/cover.jpg",
            ]
        )

    assert code == import_cmd.EXIT_OK
    mock_dl.assert_called_once()
    assert mock_dl.call_args[0][0] == "https://e/cover.jpg"
    mock_art.assert_called_once_with("Chill", "/tmp/playlist_cover.jpg")


def test_playlist_cover_url_skipped_when_no_tracks_imported(env: Paths) -> None:
    """All ISRCs fail → no add_to_playlist, no artwork attempt."""
    with (
        patch("music_manager.cli.import_cmd.resolve_by_isrc", return_value=None),
        patch("music_manager.cli.import_cmd.init_logger"),
        patch("music_manager.cli.import_cmd.configure_resolver"),
        patch("music_manager.cli.import_cmd.add_to_playlist"),
        patch("music_manager.cli.import_cmd.set_playlist_artwork") as mock_art,
    ):
        code = import_cmd.main(
            [
                "FRABC1234567",
                "--playlist-name",
                "Chill",
                "--playlist-cover-url",
                "https://e/cover.jpg",
            ]
        )
    assert code == import_cmd.EXIT_OK
    mock_art.assert_not_called()


def test_detach_forwards_playlist_cover_url(env: Paths) -> None:
    """--detach must propagate --playlist-cover-url to the spawned worker."""
    with patch("music_manager.cli.import_cmd.subprocess.Popen") as mock_popen:
        code = import_cmd.main(
            [
                "FRABC1234567",
                "--playlist-name",
                "Mix",
                "--playlist-cover-url",
                "https://e/c.jpg",
                "--detach",
            ]
        )
    assert code == import_cmd.EXIT_OK
    cmd = mock_popen.call_args[0][0]
    assert "--playlist-cover-url" in cmd
    assert "https://e/c.jpg" in cmd


def test_already_imported_isrc_added_to_playlist_without_pipeline(env: Paths) -> None:
    """ISRC already in tracks.json → its apple_id goes to the playlist directly,
    no Deezer call, no yt-dlp invocation."""
    # Seed an existing track in the store.
    from music_manager.services.tracks import Tracks  # noqa: PLC0415

    tracks_store = Tracks(env.tracks_path)
    tracks_store.add(
        "AP_EXISTING",
        {
            "isrc": "FRABC1234567",
            "title": "Already Here",
            "artist": "Someone",
            "apple_id": "AP_EXISTING",
        },
    )
    tracks_store.save()

    with (
        patch(
            "music_manager.cli.import_cmd.apple_ids_exist_checked",
            return_value=(True, {"AP_EXISTING"}),
        ),
        patch(
            "music_manager.cli.import_cmd.resolve_by_isrc",
        ) as mock_resolve,
        patch(
            "music_manager.pipeline.importer.import_resolved_track",
        ) as mock_import,
        patch("music_manager.cli.import_cmd.init_logger"),
        patch("music_manager.cli.import_cmd.configure_resolver"),
        patch("music_manager.cli.import_cmd.add_to_playlist", return_value=1) as mock_add,
    ):
        code = import_cmd.main(["FRABC1234567", "--playlist-name", "Mix"])

    assert code == import_cmd.EXIT_OK
    # Pipeline must NOT have been invoked.
    mock_resolve.assert_not_called()
    mock_import.assert_not_called()
    # But the playlist call DID receive the existing apple_id.
    mock_add.assert_called_once_with("Mix", ["AP_EXISTING"])
    status = json.loads(Path(env.widget_status_path).read_text())
    assert status["completed"] == [
        {"isrc": "FRABC1234567", "apple_id": "AP_EXISTING", "title": "Already Here"}
    ]


def test_playlist_name_supports_special_chars(env: Paths) -> None:
    """Name with apostrophe, accents, &: passes through unchanged to add_to_playlist."""

    def fake_import(track: Track, _paths, _tracks, _albums):
        track.apple_id = "AP_" + track.isrc
        return None

    tricky = "L'été & Cosy ♥"
    with (
        patch(
            "music_manager.cli.import_cmd.resolve_by_isrc",
            side_effect=lambda isrc, _albums: _track(isrc),
        ),
        patch(
            "music_manager.pipeline.importer.import_resolved_track",
            side_effect=fake_import,
        ),
        patch("music_manager.cli.import_cmd.init_logger"),
        patch("music_manager.cli.import_cmd.configure_resolver"),
        patch("music_manager.cli.import_cmd.add_to_playlist", return_value=1) as mock_add,
    ):
        import_cmd.main(["FRABC1234567", "--playlist-name", tricky])

    mock_add.assert_called_once()
    assert mock_add.call_args[0][0] == tricky


# ── Cancel ────────────────────────────────────────────────────────────────


def test_stale_flag_cleared_before_loop_starts(env: Paths) -> None:
    """Un flag oublié d'un crash précédent ne doit PAS annuler le run suivant."""
    Path(env.widget_cancel_path).write_text("1")

    def fake_import(track: Track, _paths, _tracks, _albums):
        track.apple_id = "AP_" + track.isrc
        return None

    with (
        patch(
            "music_manager.cli.import_cmd.resolve_by_isrc",
            side_effect=lambda isrc, _albums: _track(isrc),
        ),
        patch(
            "music_manager.pipeline.importer.import_resolved_track",
            side_effect=fake_import,
        ),
        patch("music_manager.cli.import_cmd.init_logger"),
        patch("music_manager.cli.import_cmd.configure_resolver"),
    ):
        code = import_cmd.main(["FRABC1234567"])

    assert code == import_cmd.EXIT_OK
    status = json.loads(Path(env.widget_status_path).read_text())
    # Le flag stale a été nettoyé → l'import s'est terminé normalement.
    assert status["status"] == "done"
    assert not Path(env.widget_cancel_path).exists()


def test_cancel_flag_mid_run_stops_after_current_track(env: Paths) -> None:
    """Flag posé après la première track → la 2e track ne tourne pas."""
    seen: list[str] = []

    def resolve_and_set_flag(isrc: str, _albums):
        seen.append(isrc)
        # On pose le flag pendant la 1ʳᵉ track : la check au début du 2e tour
        # de boucle doit déclencher l'arrêt.
        Path(env.widget_cancel_path).write_text("1")
        return _track(isrc)

    def fake_import(track: Track, _paths, _tracks, _albums):
        track.apple_id = "AP_" + track.isrc
        return None

    with (
        patch(
            "music_manager.cli.import_cmd.resolve_by_isrc",
            side_effect=resolve_and_set_flag,
        ),
        patch(
            "music_manager.pipeline.importer.import_resolved_track",
            side_effect=fake_import,
        ),
        patch("music_manager.cli.import_cmd.init_logger"),
        patch("music_manager.cli.import_cmd.configure_resolver"),
    ):
        code = import_cmd.main(["FRABC1234567,USUM71916175"])

    assert code == import_cmd.EXIT_OK
    status = json.loads(Path(env.widget_status_path).read_text())
    assert status["status"] == "cancelled"
    # La 1ʳᵉ track a été importée avant que le flag soit checké au début du 2e tour.
    assert len(status["completed"]) == 1
    assert seen == ["FRABC1234567"]


def test_partial_playlist_added_on_cancel(env: Paths) -> None:
    """Sur annulation avec --playlist-name : la playlist garde les tracks déjà importées."""
    isrcs_seen: list[str] = []

    def resolve_and_cancel_after_one(isrc: str, _albums):
        isrcs_seen.append(isrc)
        if len(isrcs_seen) >= 1:
            Path(env.widget_cancel_path).write_text("1")
        return _track(isrc)

    def fake_import(track: Track, _paths, _tracks, _albums):
        track.apple_id = "AP_" + track.isrc
        return None

    with (
        patch(
            "music_manager.cli.import_cmd.resolve_by_isrc",
            side_effect=resolve_and_cancel_after_one,
        ),
        patch(
            "music_manager.pipeline.importer.import_resolved_track",
            side_effect=fake_import,
        ),
        patch("music_manager.cli.import_cmd.init_logger"),
        patch("music_manager.cli.import_cmd.configure_resolver"),
        patch("music_manager.cli.import_cmd.add_to_playlist", return_value=1) as mock_add,
    ):
        import_cmd.main(["FRABC1234567,USUM71916175", "--playlist-name", "Annulée"])

    status = json.loads(Path(env.widget_status_path).read_text())
    assert status["status"] == "cancelled"
    # La playlist doit avoir reçu les tracks déjà importées.
    mock_add.assert_called_once()
    assert mock_add.call_args[0][0] == "Annulée"
    assert mock_add.call_args[0][1] == ["AP_FRABC1234567"]


# ── Run identity (widget polling race) ─────────────────────────────────────


def test_status_carries_run_id(env: Paths) -> None:
    """The status file names the run it belongs to.

    Regression: the widget's first poll read the *previous* run's finished
    status, concluded the import was over and stopped polling while the
    detached worker was still booting.
    """
    with (
        patch("music_manager.cli.import_cmd.resolve_by_isrc", return_value=None),
        patch("music_manager.cli.import_cmd.init_logger"),
        patch("music_manager.cli.import_cmd.configure_resolver"),
    ):
        import_cmd.main(["FRABC1234567", "--run-id", "abc123"])

    status = json.loads(Path(env.widget_status_path).read_text())
    assert status["run_id"] == "abc123"


def test_detach_emits_run_id_on_stdout(env: Paths, capsys: pytest.CaptureFixture) -> None:
    """--detach hands the caller the run_id it will have to match against."""
    with patch("music_manager.cli.import_cmd.subprocess.Popen") as mock_popen:
        code = import_cmd.main(["FRABC1234567", "--detach"])

    assert code == import_cmd.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "started"
    assert payload["run_id"]
    assert payload["total"] == 1
    # The very same id must reach the worker, or the widget can never match it.
    spawned = mock_popen.call_args[0][0]
    assert "--run-id" in spawned
    assert spawned[spawned.index("--run-id") + 1] == payload["run_id"]


def test_busy_does_not_clobber_a_running_status(env: Paths) -> None:
    """A refused second import must leave the live one's status alone.

    Regression: the loser wrote {"status": "blocked"} into the very file the
    running import was using, so the widget showed "blocked" for a healthy run.
    """
    Path(env.widget_status_path).parent.mkdir(parents=True, exist_ok=True)
    Path(env.widget_status_path).write_text(
        json.dumps({"status": "running", "run_id": "live", "current": 3, "total": 10})
    )
    acquire_lock(env.widget_lock_path)
    try:
        code = import_cmd.main(["FRABC1234567"])
    finally:
        release_lock(env.widget_lock_path)

    assert code == import_cmd.EXIT_BUSY
    status = json.loads(Path(env.widget_status_path).read_text())
    assert status["status"] == "running"
    assert status["run_id"] == "live"


def test_blocked_status_written_when_nothing_is_running(env: Paths) -> None:
    """With no live run, the blocked state is still surfaced in the file."""
    acquire_lock(env.ui_lock_path)
    try:
        code = import_cmd.main(["FRABC1234567"])
    finally:
        release_lock(env.ui_lock_path)

    assert code == import_cmd.EXIT_USAGE
    status = json.loads(Path(env.widget_status_path).read_text())
    assert status["status"] == "blocked"
    assert status["reason"] == "ui_running"


# ── Failure persistence + cooldown ─────────────────────────────────────────


def test_failures_are_persisted_with_detail(env: Paths) -> None:
    """Failures outlive the detached worker so the widget can list and retry."""

    def fake_import(track: Track, _paths, _tracks, _albums):
        return PendingTrack(reason="youtube_failed", detail="youtube_blocked", track=track)

    with (
        patch(
            "music_manager.cli.import_cmd.resolve_by_isrc",
            side_effect=lambda isrc, _albums: _track(isrc),
        ),
        patch("music_manager.pipeline.importer.import_resolved_track", side_effect=fake_import),
        patch("music_manager.cli.import_cmd.init_logger"),
        patch("music_manager.cli.import_cmd.configure_resolver"),
    ):
        import_cmd.main(["FRABC1234567"])

    stored = json.loads(Path(env.widget_failures_path).read_text())["entries"]
    assert len(stored) == 1
    assert stored[0]["isrc"] == "FRABC1234567"
    assert stored[0]["detail"] == "youtube_blocked"
    assert stored[0]["title"] == "Bad Guy"


def test_recently_failed_isrc_is_skipped(env: Paths) -> None:
    """A hopeless track isn't re-attempted seconds later."""
    record_failures(
        env.widget_failures_path,
        [{"isrc": "FRABC1234567", "reason": "youtube_failed", "detail": "youtube_blocked"}],
    )

    with (
        patch("music_manager.cli.import_cmd.resolve_by_isrc") as mock_resolve,
        patch("music_manager.cli.import_cmd.init_logger"),
        patch("music_manager.cli.import_cmd.configure_resolver"),
    ):
        import_cmd.main(["FRABC1234567"])

    mock_resolve.assert_not_called()
    status = json.loads(Path(env.widget_status_path).read_text())
    assert status["skipped"][0]["reason"] == "recently_failed"


def test_force_bypasses_the_cooldown(env: Paths) -> None:
    """The widget's explicit retry must always actually retry."""
    record_failures(
        env.widget_failures_path,
        [{"isrc": "FRABC1234567", "reason": "youtube_failed", "detail": "youtube_blocked"}],
    )

    with (
        patch("music_manager.cli.import_cmd.resolve_by_isrc", return_value=None) as mock_resolve,
        patch("music_manager.cli.import_cmd.init_logger"),
        patch("music_manager.cli.import_cmd.configure_resolver"),
    ):
        import_cmd.main(["FRABC1234567", "--force"])

    mock_resolve.assert_called_once()


def test_success_clears_a_previous_failure(env: Paths) -> None:
    """Once a track finally imports, it must stop being listed as failed."""
    record_failures(
        env.widget_failures_path,
        [{"isrc": "FRABC1234567", "reason": "youtube_failed", "detail": "youtube_blocked"}],
    )

    def fake_import(track: Track, _paths, _tracks, _albums):
        track.apple_id = "AP_" + track.isrc
        return None

    with (
        patch(
            "music_manager.cli.import_cmd.resolve_by_isrc",
            side_effect=lambda isrc, _albums: _track(isrc),
        ),
        patch("music_manager.pipeline.importer.import_resolved_track", side_effect=fake_import),
        patch("music_manager.cli.import_cmd.init_logger"),
        patch("music_manager.cli.import_cmd.configure_resolver"),
    ):
        import_cmd.main(["FRABC1234567", "--force"])

    assert load_failures(env.widget_failures_path) == []


# ── Apple Music consistency ────────────────────────────────────────────────


def test_fast_path_reimports_when_apple_id_is_dead(env: Paths) -> None:
    """A track deleted from Apple Music must not count as already imported.

    Regression: the fast path trusted tracks.json, reported a success and
    pushed a dead persistent ID into the playlist.
    """
    store = Tracks(env.tracks_path)
    store.add("DEAD_ID", {"isrc": "FRABC1234567", "apple_id": "DEAD_ID", "title": "Bad Guy"})
    store.save()

    def fake_import(track: Track, _paths, _tracks, _albums):
        track.apple_id = "FRESH_ID"
        return None

    with (
        patch("music_manager.cli.import_cmd.apple_ids_exist_checked", return_value=(True, set())),
        patch(
            "music_manager.cli.import_cmd.resolve_by_isrc",
            side_effect=lambda isrc, _albums: _track(isrc),
        ),
        patch("music_manager.pipeline.importer.import_resolved_track", side_effect=fake_import),
        patch("music_manager.cli.import_cmd.init_logger"),
        patch("music_manager.cli.import_cmd.configure_resolver"),
    ):
        import_cmd.main(["FRABC1234567"])

    status = json.loads(Path(env.widget_status_path).read_text())
    assert status["completed"] == [
        {"isrc": "FRABC1234567", "apple_id": "FRESH_ID", "title": "Bad Guy"}
    ]


def test_fast_path_keeps_live_apple_id(env: Paths) -> None:
    """A track still present in Apple Music keeps the cheap path."""
    store = Tracks(env.tracks_path)
    store.add("LIVE_ID", {"isrc": "FRABC1234567", "apple_id": "LIVE_ID", "title": "Bad Guy"})
    store.save()

    with (
        patch(
            "music_manager.cli.import_cmd.apple_ids_exist_checked",
            return_value=(True, {"LIVE_ID"}),
        ),
        patch("music_manager.cli.import_cmd.resolve_by_isrc") as mock_resolve,
        patch("music_manager.cli.import_cmd.init_logger"),
        patch("music_manager.cli.import_cmd.configure_resolver"),
    ):
        import_cmd.main(["FRABC1234567"])

    mock_resolve.assert_not_called()
    status = json.loads(Path(env.widget_status_path).read_text())
    assert status["completed"][0]["apple_id"] == "LIVE_ID"


# ── Housekeeping ───────────────────────────────────────────────────────────


def test_worker_cleans_covers_and_pending_audio(env: Paths, tmp_path: Path) -> None:
    """The detached worker has no UI teardown → it must clean .tmp/ itself.

    Regression: 11 MB of orphan covers accumulated because cleanup_covers()
    was only called from the Textual paths.
    """
    tmp_dir = Path(env.tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    (tmp_dir / "cover_1.jpg").write_bytes(b"img")
    leftover = tmp_dir / "audio.m4a"
    leftover.write_bytes(b"audio")

    def fake_import(track: Track, _paths, _tracks, _albums):
        return PendingTrack(
            reason="duration_suspect",
            detail="duration_suspect",
            track=track,
            dl_path=str(leftover),
        )

    with (
        patch(
            "music_manager.cli.import_cmd.resolve_by_isrc",
            side_effect=lambda isrc, _albums: _track(isrc),
        ),
        patch("music_manager.pipeline.importer.import_resolved_track", side_effect=fake_import),
        patch("music_manager.cli.import_cmd.init_logger"),
        patch("music_manager.cli.import_cmd.configure_resolver"),
    ):
        import_cmd.main(["FRABC1234567"])

    assert not (tmp_dir / "cover_1.jpg").exists()
    assert not leftover.exists()


def test_status_reports_stale_yt_dlp(env: Paths) -> None:
    """A stale yt-dlp is surfaced so the user can act instead of guessing."""
    with (
        patch(
            "music_manager.cli.import_cmd.check_yt_dlp_fresh",
            return_value=("2026.03.17", 156, True),
        ),
        patch("music_manager.cli.import_cmd.resolve_by_isrc", return_value=None),
        patch("music_manager.cli.import_cmd.init_logger"),
        patch("music_manager.cli.import_cmd.configure_resolver"),
    ):
        import_cmd.main(["FRABC1234567"])

    status = json.loads(Path(env.widget_status_path).read_text())
    assert status["yt_dlp_stale"] is True
    assert status["yt_dlp_version"] == "2026.03.17"


def test_blocked_status_is_timestamped(env: Paths) -> None:
    """The status file persists — the widget needs an age to stop showing it."""
    acquire_lock(env.ui_lock_path)
    try:
        import_cmd.main(["FRABC1234567"])
    finally:
        release_lock(env.ui_lock_path)

    status = json.loads(Path(env.widget_status_path).read_text())
    assert status["at"]


def test_timestamps_are_timezone_aware(env: Paths) -> None:
    """Naive local timestamps were unreadable next to the UTC ones in logs."""
    with (
        patch("music_manager.cli.import_cmd.resolve_by_isrc", return_value=None),
        patch("music_manager.cli.import_cmd.init_logger"),
        patch("music_manager.cli.import_cmd.configure_resolver"),
    ):
        import_cmd.main(["FRABC1234567"])

    status = json.loads(Path(env.widget_status_path).read_text())
    for key in ("started_at", "finished_at"):
        assert datetime.fromisoformat(status[key]).tzinfo is not None
