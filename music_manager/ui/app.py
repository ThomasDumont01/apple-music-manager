"""Music Manager — Textual application."""

from pathlib import Path

from textual.app import App

from music_manager.core.config import Paths
from music_manager.services.albums import Albums
from music_manager.services.apple import Apple
from music_manager.services.tracks import Tracks

# ── Constants ───────────────────────────────────────────────────────────────

_CSS_PATH = Path(__file__).parent / "theme.css"


# ── App ─────────────────────────────────────────────────────────────────────


class MusicApp(App):
    """Main application — manages screens and shared state."""

    TITLE = "Music Manager"
    CSS_PATH = str(_CSS_PATH)
    ansi_color = True

    def __init__(
        self,
        setup_done: bool = False,
        tracks_store: Tracks | None = None,
        albums_store: Albums | None = None,
        paths: Paths | None = None,
        apple: Apple | None = None,
        requests_path: str = "",
        playlists_dir: str = "",
    ) -> None:
        super().__init__()
        self.theme = "textual-ansi"
        self.setup_done = setup_done
        self.tracks_store = tracks_store
        self.albums_store = albums_store
        self.paths = paths
        self.apple = apple
        self.requests_path = requests_path
        self.playlists_dir = playlists_dir
        self.deezer_ok = True
        self.youtube_ok = True

    def on_mount(self) -> None:
        """Start with welcome (first launch) or checks (subsequent)."""
        if not self.setup_done:
            from music_manager.ui.screens.welcome import WelcomeScreen  # noqa: PLC0415

            self.push_screen(WelcomeScreen())
        else:
            from music_manager.ui.screens.checks import ChecksScreen  # noqa: PLC0415

            self.push_screen(ChecksScreen(first_launch=False))

    def on_checks_done(self, first_launch: bool) -> None:
        """Checks passed — go to setup or menu."""
        if first_launch:
            from music_manager.ui.screens.setup import SetupScreen  # noqa: PLC0415

            self.switch_screen(SetupScreen())
        else:
            self._launch_menu_with_background_scan()

    def on_setup_done(self, tracks_total: int, isrc_count: int) -> None:
        """First launch setup complete — go to menu."""
        # Reload tracks store (created during setup)
        if self.paths:
            self.tracks_store = Tracks(self.paths.tracks_path)
            self.albums_store = Albums(self.paths.albums_path)
        self._launch_menu(tracks_total, isrc_count)

    def _launch_menu_with_background_scan(self) -> None:
        """Subsequent launches: scan → sync → menu (data always fresh)."""
        # Scan Apple Music library (blocking, ~2-4s)
        if self.apple and self.tracks_store:
            self.apple.scan()
            self._auto_sync(self.apple, self.tracks_store)

        all_tracks = self.tracks_store.all() if self.tracks_store else {}
        tracks_count = len(all_tracks)
        albums_count = len(
            {entry.get("album", "") for entry in all_tracks.values() if entry.get("album")}
        )
        identified_count = sum(1 for entry in all_tracks.values() if entry.get("deezer_id"))

        self._push_menu(tracks_count, albums_count, identified_count)

    def _launch_menu(self, tracks_total: int, isrc_count: int) -> None:
        """First launch: menu with known stats."""
        self._push_menu(tracks_total, 0, isrc_count)

    def _push_menu(self, tracks_count: int, albums_count: int, identified_count: int) -> None:
        """Push MenuScreen with current stats."""
        from music_manager.ui.screens.menu import MenuScreen  # noqa: PLC0415

        self.switch_screen(
            MenuScreen(
                tracks_count=tracks_count,
                albums_count=albums_count,
                identified_count=identified_count,
                tracks_store=self.tracks_store,
                albums_store=self.albums_store,
                paths=self.paths,
                requests_path=self.requests_path,
                playlists_dir=self.playlists_dir,
            )
        )

    def _auto_sync(self, apple: Apple, tracks: Tracks) -> None:
        """Sync store Tracks with Apple Music library + cleanup orphan albums."""
        from music_manager.core.logger import log_event  # noqa: PLC0415

        library = apple.get_all()
        library_ids = set(library.keys())
        tracked_ids = set(tracks.all().keys())

        for apple_id in library_ids - tracked_ids:
            entry = library[apple_id]
            entry_dict = entry.to_dict()
            entry_dict["origin"] = "baseline"
            entry_dict["status"] = None
            tracks.add(apple_id, entry_dict)

        for apple_id in tracked_ids - library_ids:
            tracks.remove(apple_id)

        # Sync file_path from Apple scan (Apple renames files on metadata changes)
        for apple_id in tracked_ids & library_ids:
            lib_entry = library[apple_id]
            stored = tracks.all().get(apple_id)
            if stored and lib_entry.file_path and stored.get("file_path") != lib_entry.file_path:
                stored["file_path"] = lib_entry.file_path
                tracks.mark_dirty()

        tracks.save()

        # Cleanup orphan albums (no tracks reference them anymore)
        if self.albums_store:
            self._cleanup_orphan_albums(tracks)

        added = len(library_ids - tracked_ids)
        removed = len(tracked_ids - library_ids)
        if added or removed:
            log_event("auto_sync", added=added, removed=removed)

    def _cleanup_orphan_albums(self, tracks: Tracks) -> None:
        """Remove albums from cache if no track references them."""
        if not self.albums_store:
            return

        # Collect album_ids still referenced by tracks
        used_album_ids = {
            str(entry.get("album_id", 0))
            for entry in tracks.all().values()
            if entry.get("album_id")
        }

        # Remove orphans from albums store
        all_albums = dict(self.albums_store.all())
        removed = 0
        for album_id in list(all_albums.keys()):
            if album_id not in used_album_ids:
                self.albums_store.remove(album_id)
                removed += 1

        if removed:
            self.albums_store.save()

    def action_open_option(self, key: str) -> None:
        """Open an option screen. Placeholder — screens added incrementally."""
        self.notify(f"{key}", timeout=2)
