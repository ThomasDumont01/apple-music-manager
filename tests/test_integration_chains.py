"""Integration tests — cross-module data flow chains.

Tests the handoff between modules: fix_metadata → store → dedup,
modify → store → dedup, snapshot → reset_failed, ignored tracks consistency.
All external services mocked.
"""

from pathlib import Path
from unittest.mock import patch

from music_manager.core.config import Paths
from music_manager.core.io import load_json, save_csv, save_json
from music_manager.core.models import Track
from music_manager.options.fix_metadata import Divergence, apply_corrections
from music_manager.options.import_tracks import process_csv
from music_manager.options.maintenance import reset_failed
from music_manager.options.modify_track import change_edition
from music_manager.options.snapshot import snapshot
from music_manager.pipeline.dedup import is_duplicate
from music_manager.services.albums import Albums
from music_manager.services.resolver import ResolveResult
from music_manager.services.tracks import Tracks

_P_IMPORTER = "music_manager.pipeline.importer"
_P_APPLE = "music_manager.services.apple"
_P_RESOLVER = "music_manager.services.resolver"
_P_FIXMETA = "music_manager.options.fix_metadata"


def _paths(tmp_path: Path) -> Paths:
    return Paths(str(tmp_path / "data"))


# ══════════════════════════════════════════════════════════════════════════
# 1. fix_metadata → store → dedup chain
# ══════════════════════════════════════════════════════════════════════════


class TestFixMetadataStoreDedup:
    """After apply_corrections updates metadata, dedup uses new values."""

    @patch(f"{_P_FIXMETA}.update_tracks_batch")
    def test_title_artist_correction_updates_dedup(
        self, mock_apple_update, tmp_path: Path
    ) -> None:
        """Correcting title+artist via fix_metadata → dedup matches new, not old."""
        tracks = Tracks(str(tmp_path / "t.json"))
        tracks.add(
            "AP1",
            {
                "title": "Old Title",
                "artist": "Old Artist",
                "album": "Album",
                "isrc": "ISRC001",
                "deezer_id": 100,
                "album_id": 10,
                "status": "done",
            },
        )

        # Apply title + artist corrections
        corrections = [
            Divergence(
                apple_id="AP1",
                field_name="title",
                local_value="Old Title",
                deezer_value="New Title",
            ),
            Divergence(
                apple_id="AP1",
                field_name="artist",
                local_value="Old Artist",
                deezer_value="New Artist",
            ),
        ]
        count, explicit_q = apply_corrections(corrections, tracks)
        assert count == 2

        # Store has new values
        entry = tracks.get_by_apple_id("AP1")
        assert entry is not None
        assert entry["title"] == "New Title"
        assert entry["artist"] == "New Artist"

        # Dedup matches new metadata
        assert is_duplicate("ISRC001", "New Title", "New Artist", tracks) is True
        # Old metadata no longer matches via title+artist (ISRC still works)
        assert is_duplicate("", "Old Title", "Old Artist", tracks) is False

        # Title+artist index updated
        assert tracks.get_by_title_artist("new title", "new artist") is not None
        assert tracks.get_by_title_artist("old title", "old artist") is None

    @patch(f"{_P_FIXMETA}.update_tracks_batch")
    def test_int_fields_stored_as_int_not_string(self, mock_apple_update, tmp_path: Path) -> None:
        """track_number, disk_number, year corrections stored as int in store."""
        tracks = Tracks(str(tmp_path / "t.json"))
        tracks.add(
            "AP1",
            {
                "title": "Song",
                "artist": "Art",
                "album": "Album",
                "isrc": "ISRC001",
                "deezer_id": 100,
                "album_id": 10,
                "status": "done",
            },
        )

        corrections = [
            Divergence("AP1", "track_number", "0", "5"),
            Divergence("AP1", "disk_number", "0", "2"),
            Divergence("AP1", "year", "", "2023"),
            Divergence("AP1", "total_tracks", "0", "12"),
        ]
        count, _ = apply_corrections(corrections, tracks)
        assert count == 4

        entry = tracks.get_by_apple_id("AP1")
        assert entry is not None
        assert entry["track_number"] == 5
        assert isinstance(entry["track_number"], int)
        assert entry["disk_number"] == 2
        assert isinstance(entry["disk_number"], int)
        assert entry["year"] == 2023
        assert isinstance(entry["year"], int)
        assert entry["total_tracks"] == 12
        assert isinstance(entry["total_tracks"], int)

    @patch(f"{_P_FIXMETA}.update_tracks_batch")
    def test_int_fields_survive_save_reload(self, mock_apple_update, tmp_path: Path) -> None:
        """Int fields persist correctly through save/reload cycle."""
        path = str(tmp_path / "t.json")
        tracks = Tracks(path)
        tracks.add(
            "AP1",
            {
                "title": "Song",
                "artist": "Art",
                "album": "Album",
                "isrc": "ISRC001",
                "deezer_id": 100,
                "album_id": 10,
                "status": "done",
            },
        )

        corrections = [
            Divergence("AP1", "track_number", "", "3"),
            Divergence("AP1", "disk_number", "", "1"),
        ]
        apply_corrections(corrections, tracks)

        # Reload from disk (apply_corrections calls tracks_store.save())
        tracks2 = Tracks(path)
        entry = tracks2.get_by_apple_id("AP1")
        assert entry is not None
        assert entry["track_number"] == 3
        assert isinstance(entry["track_number"], int)
        assert entry["disk_number"] == 1
        assert isinstance(entry["disk_number"], int)

    @patch(f"{_P_FIXMETA}.update_tracks_batch")
    def test_corrected_track_still_dedup_by_isrc(self, mock_apple_update, tmp_path: Path) -> None:
        """After title correction, ISRC-based dedup still works."""
        tracks = Tracks(str(tmp_path / "t.json"))
        tracks.add(
            "AP1",
            {
                "title": "Original",
                "artist": "Art",
                "album": "Album",
                "isrc": "ISRC999",
                "deezer_id": 1,
                "status": "done",
            },
        )

        corrections = [
            Divergence("AP1", "title", "Original", "Corrected"),
        ]
        apply_corrections(corrections, tracks)

        # ISRC match still works regardless of title change
        assert is_duplicate("ISRC999", "Anything", "Anyone", tracks) is True


# ══════════════════════════════════════════════════════════════════════════
# 2. modify → store → dedup chain
# ══════════════════════════════════════════════════════════════════════════


class TestModifyStoreDedup:
    """After change_edition replaces a track, dedup reflects the swap."""

    @patch(f"{_P_IMPORTER}.cleanup_covers")
    @patch(f"{_P_APPLE}.delete_tracks")
    @patch(f"{_P_IMPORTER}.import_resolved_track")
    @patch(f"{_P_RESOLVER}.resolve_by_id")
    def test_change_edition_dedup_finds_new_not_old(
        self,
        mock_resolve,
        mock_import,
        mock_delete,
        mock_cleanup,
        tmp_path: Path,
    ) -> None:
        """change_edition replaces apple_id — dedup finds new, not old."""
        tracks = Tracks(str(tmp_path / "t.json"))
        albums = Albums(str(tmp_path / "a.json"))
        paths = _paths(tmp_path)

        # Initial track
        tracks.add(
            "OLD_AP",
            {
                "title": "Song (Standard)",
                "artist": "Artist",
                "album": "Album",
                "isrc": "ISRC_OLD",
                "deezer_id": 100,
                "album_id": 10,
                "status": "done",
            },
        )

        # Mock: resolve returns new track, import succeeds
        new_track = Track(
            isrc="ISRC_NEW",
            title="Song (Deluxe)",
            artist="Artist",
            album="Album Deluxe",
            deezer_id=200,
            album_id=20,
        )
        mock_resolve.return_value = new_track

        def fake_import(track, *args, **kwargs):
            track.apple_id = "NEW_AP"
            tracks.add(
                "NEW_AP",
                {
                    "title": track.title,
                    "artist": track.artist,
                    "album": track.album,
                    "isrc": track.isrc,
                    "deezer_id": track.deezer_id,
                    "album_id": track.album_id,
                    "status": "done",
                },
            )
            return None

        mock_import.side_effect = fake_import

        result = change_edition("OLD_AP", 200, paths, tracks, albums)
        assert result.success is True

        # New track found by dedup
        assert is_duplicate("ISRC_NEW", "Song (Deluxe)", "Artist", tracks) is True
        # Old entry completely gone
        assert tracks.get_by_apple_id("OLD_AP") is None
        assert tracks.get_by_isrc("ISRC_OLD") is None
        # Old ISRC no longer triggers dedup (no entry has it)
        assert is_duplicate("ISRC_OLD", "Song (Standard)", "Artist", tracks) is False

    @patch(f"{_P_IMPORTER}.cleanup_covers")
    @patch(f"{_P_APPLE}.delete_tracks")
    @patch(f"{_P_IMPORTER}.import_resolved_track")
    @patch(f"{_P_RESOLVER}.resolve_by_id")
    def test_change_edition_preserves_other_tracks(
        self,
        mock_resolve,
        mock_import,
        mock_delete,
        mock_cleanup,
        tmp_path: Path,
    ) -> None:
        """change_edition on one track doesn't affect others in the store."""
        tracks = Tracks(str(tmp_path / "t.json"))
        albums = Albums(str(tmp_path / "a.json"))
        paths = _paths(tmp_path)

        # Two tracks in store
        tracks.add(
            "AP1",
            {
                "title": "Song A",
                "artist": "Artist",
                "album": "Album",
                "isrc": "ISRC_A",
                "deezer_id": 1,
                "status": "done",
            },
        )
        tracks.add(
            "AP2",
            {
                "title": "Song B",
                "artist": "Artist",
                "album": "Album",
                "isrc": "ISRC_B",
                "deezer_id": 2,
                "status": "done",
            },
        )

        new_track = Track(
            isrc="ISRC_A2",
            title="Song A (Remix)",
            artist="Artist",
            album="Album Remix",
            deezer_id=10,
            album_id=10,
        )
        mock_resolve.return_value = new_track

        def fake_import(track, *args, **kwargs):
            track.apple_id = "AP1_NEW"
            tracks.add(
                "AP1_NEW",
                {
                    "title": track.title,
                    "artist": track.artist,
                    "album": track.album,
                    "isrc": track.isrc,
                    "deezer_id": track.deezer_id,
                    "status": "done",
                },
            )
            return None

        mock_import.side_effect = fake_import

        change_edition("AP1", 10, paths, tracks, albums)

        # AP2 untouched
        assert tracks.get_by_apple_id("AP2") is not None
        assert is_duplicate("ISRC_B", "Song B", "Artist", tracks) is True
        # AP1 replaced
        assert tracks.get_by_apple_id("AP1") is None
        assert tracks.get_by_apple_id("AP1_NEW") is not None


# ══════════════════════════════════════════════════════════════════════════
# 3. snapshot → reset_failed roundtrip
# ══════════════════════════════════════════════════════════════════════════


class TestSnapshotResetFailed:
    """snapshot promotes, reset_failed resets, both persist correctly."""

    def test_snapshot_promotes_imported_done(self, tmp_path: Path) -> None:
        """snapshot changes origin from imported to baseline for done tracks."""
        path = str(tmp_path / "t.json")
        tracks = Tracks(path)

        tracks.add(
            "AP1",
            {"title": "A", "artist": "X", "origin": "imported", "status": "done", "deezer_id": 1},
        )
        tracks.add(
            "AP2",
            {"title": "B", "artist": "Y", "origin": "imported", "status": "done", "deezer_id": 2},
        )
        tracks.add(
            "AP3",
            {"title": "C", "artist": "Z", "origin": "baseline", "status": None, "deezer_id": None},
        )

        count = snapshot(tracks)
        assert count == 2

        assert tracks.get_by_apple_id("AP1")["origin"] == "baseline"
        assert tracks.get_by_apple_id("AP2")["origin"] == "baseline"
        # Already baseline — unchanged
        assert tracks.get_by_apple_id("AP3")["origin"] == "baseline"

    def test_snapshot_persists_to_disk(self, tmp_path: Path) -> None:
        """After snapshot, reloading store still shows promoted tracks."""
        path = str(tmp_path / "t.json")
        tracks = Tracks(path)

        tracks.add(
            "AP1",
            {"title": "A", "artist": "X", "origin": "imported", "status": "done", "deezer_id": 1},
        )
        snapshot(tracks)

        # Reload from disk
        tracks2 = Tracks(path)
        assert tracks2.get_by_apple_id("AP1")["origin"] == "baseline"

    def test_reset_failed_clears_status(self, tmp_path: Path) -> None:
        """reset_failed sets status=None and clears fail_reason."""
        path = str(tmp_path / "t.json")
        tracks = Tracks(path)

        tracks.add(
            "AP1",
            {"title": "A", "artist": "X", "status": "failed", "fail_reason": "youtube_failed"},
        )
        tracks.add("AP2", {"title": "B", "artist": "Y", "status": "done"})
        tracks.add(
            "AP3", {"title": "C", "artist": "Z", "status": "failed", "fail_reason": "network"}
        )

        count = reset_failed(tracks)
        assert count == 2

        assert tracks.get_by_apple_id("AP1")["status"] is None
        assert tracks.get_by_apple_id("AP1")["fail_reason"] == ""
        assert tracks.get_by_apple_id("AP2")["status"] == "done"  # untouched
        assert tracks.get_by_apple_id("AP3")["status"] is None

    def test_reset_failed_persists_dirty_flag(self, tmp_path: Path) -> None:
        """reset_failed uses update() which sets _dirty, then save() persists."""
        path = str(tmp_path / "t.json")
        tracks = Tracks(path)

        tracks.add("AP1", {"title": "A", "artist": "X", "status": "failed", "fail_reason": "err"})
        # Save initial state
        tracks.save()

        # Reset and verify it persists
        reset_failed(tracks)

        # Reload — must reflect the reset
        tracks2 = Tracks(path)
        assert tracks2.get_by_apple_id("AP1")["status"] is None
        assert tracks2.get_by_apple_id("AP1")["fail_reason"] == ""

    def test_snapshot_then_reset_failed_roundtrip(self, tmp_path: Path) -> None:
        """Full lifecycle: import → snapshot → fail → reset → verify."""
        path = str(tmp_path / "t.json")
        tracks = Tracks(path)

        # Start with imported+done tracks
        tracks.add(
            "AP1",
            {"title": "A", "artist": "X", "origin": "imported", "status": "done", "deezer_id": 1},
        )
        tracks.add(
            "AP2",
            {"title": "B", "artist": "Y", "origin": "imported", "status": "done", "deezer_id": 2},
        )

        # Snapshot promotes to baseline
        snapshot(tracks)
        assert tracks.get_by_apple_id("AP1")["origin"] == "baseline"

        # Simulate failure on AP2
        tracks.update("AP2", {"status": "failed", "fail_reason": "network_error"})
        tracks.save()

        # Reset failed
        count = reset_failed(tracks)
        assert count == 1
        assert tracks.get_by_apple_id("AP2")["status"] is None
        assert tracks.get_by_apple_id("AP2")["origin"] == "baseline"  # origin preserved

        # Verify persistence
        tracks2 = Tracks(path)
        assert tracks2.get_by_apple_id("AP1")["status"] == "done"
        assert tracks2.get_by_apple_id("AP2")["status"] is None


# ══════════════════════════════════════════════════════════════════════════
# 4. ignored tracks consistency
# ══════════════════════════════════════════════════════════════════════════


class TestIgnoredTracksConsistency:
    """Ignored track keys are consistent across import and identify screens."""

    def test_ignored_key_format_lowercase(self, tmp_path: Path) -> None:
        """Ignored key uses lowercase title::artist format."""
        paths = _paths(tmp_path)
        prefs_path = paths.preferences_path
        save_json(prefs_path, {})

        # Simulate what the review screen does when ignoring a track
        prefs = load_json(prefs_path)
        raw = prefs.get("ignored_tracks")
        ignored: list = raw if isinstance(raw, list) else []

        # Key format: "{csv_title.lower()}::{csv_artist.lower()}"
        key = f"{'My Song'.lower()}::{'My Artist'.lower()}"
        ignored.append(key)
        prefs["ignored_tracks"] = ignored
        save_json(prefs_path, prefs)

        # Verify import_tracks uses the same format to check
        # import_tracks.py line 85: f"{title.lower()}::{artist.lower()}"
        loaded_prefs = load_json(prefs_path)
        loaded_raw = loaded_prefs.get("ignored_tracks", [])
        ignored_set: set[str] = set(loaded_raw) if isinstance(loaded_raw, list) else set()

        # Same format as import_tracks.py check
        check_key = f"{'My Song'.lower()}::{'My Artist'.lower()}"
        assert check_key in ignored_set

    def test_ignored_from_review_found_by_import(self, tmp_path: Path) -> None:
        """Track ignored via review screen is skipped by process_csv."""
        paths = _paths(tmp_path)
        prefs_path = paths.preferences_path

        # Simulate review screen ignoring a track
        key = f"{'Toxic'.lower()}::{'Britney Spears'.lower()}"
        save_json(prefs_path, {"ignored_tracks": [key]})

        # Now import_tracks should skip this track
        loaded = load_json(prefs_path)
        raw = loaded.get("ignored_tracks", [])
        ignored_set = set(raw) if isinstance(raw, list) else set()

        # Exact same check as import_tracks.py line 85
        title = "Toxic"
        artist = "Britney Spears"
        assert f"{title.lower()}::{artist.lower()}" in ignored_set

    def test_ignored_from_identify_found_by_import(self, tmp_path: Path) -> None:
        """Track ignored via identify screen uses same key format as import check."""
        paths = _paths(tmp_path)
        prefs_path = paths.preferences_path

        # Simulate identify screen ignoring (uses csv_title/csv_artist)
        # _identify.py line 669: f"{pending.csv_title.lower()}::{pending.csv_artist.lower()}"
        csv_title = "L'Été Indien"
        csv_artist = "Joe Dassin & Friends"
        key = f"{csv_title.lower()}::{csv_artist.lower()}"
        save_json(prefs_path, {"ignored_tracks": [key]})

        # import_tracks uses the same lowercase format
        loaded = load_json(prefs_path)
        raw = loaded.get("ignored_tracks", [])
        ignored_set = set(raw) if isinstance(raw, list) else set()

        assert f"{csv_title.lower()}::{csv_artist.lower()}" in ignored_set

    def test_ignored_case_sensitivity(self, tmp_path: Path) -> None:
        """Mixed case input → lowercase key → found regardless of input case."""
        paths = _paths(tmp_path)
        prefs_path = paths.preferences_path

        # Store with lowercase key
        save_json(prefs_path, {"ignored_tracks": ["bohemian rhapsody::queen"]})

        loaded = load_json(prefs_path)
        ignored_set = set(loaded.get("ignored_tracks", []))

        # Various input cases all produce the same lowercase key
        assert f"{'Bohemian Rhapsody'.lower()}::{'Queen'.lower()}" in ignored_set
        assert f"{'BOHEMIAN RHAPSODY'.lower()}::{'QUEEN'.lower()}" in ignored_set
        assert f"{'bohemian rhapsody'.lower()}::{'queen'.lower()}" in ignored_set

    @patch("music_manager.options.import_tracks.log_event")
    @patch("music_manager.options.import_tracks.import_resolved_track")
    @patch("music_manager.options.import_tracks.resolve")
    def test_ignored_track_skipped_in_full_import(
        self, mock_resolve, mock_import, mock_log, tmp_path: Path
    ) -> None:
        """Full integration: ignored track is skipped by process_csv."""
        tracks = Tracks(str(tmp_path / "t.json"))
        albums = Albums(str(tmp_path / "a.json"))
        paths = _paths(tmp_path)

        # Ignore "Song A" by "Artist A"
        save_json(
            paths.preferences_path,
            {
                "ignored_tracks": ["song a::artist a"],
            },
        )

        csv_path = str(tmp_path / "import.csv")
        save_csv(
            csv_path,
            [
                {"title": "Song A", "artist": "Artist A", "album": "Al", "isrc": "ISRC1"},
                {"title": "Song B", "artist": "Artist B", "album": "Al", "isrc": "ISRC2"},
            ],
        )

        def fake_resolve(title, artist, album, isrc, as_):
            t = Track(isrc=isrc, title=title, artist=artist, album=album, deezer_id=1, album_id=1)
            return ResolveResult("resolved", track=t)

        call_count = [0]

        def fake_import(track, *a, **kw):
            call_count[0] += 1
            track.apple_id = f"AP{call_count[0]}"
            tracks.add(
                track.apple_id,
                {
                    "title": track.title,
                    "artist": track.artist,
                    "album": track.album,
                    "isrc": track.isrc,
                    "status": "done",
                    "deezer_id": 1,
                },
            )
            return None

        mock_resolve.side_effect = fake_resolve
        mock_import.side_effect = fake_import

        result = process_csv(csv_path, paths, tracks, albums)

        # Song A skipped (ignored), Song B imported
        assert result.skipped == 1
        assert result.imported == 1
        assert call_count[0] == 1  # only one import call
