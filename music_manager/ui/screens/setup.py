"""Setup screen — first launch: scan library + ISRC + baseline snapshot."""

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Static

from music_manager.ui.styles import BLUE, CHECK
from music_manager.ui.text import (
    HELP_SETUP_DONE,
    SETUP_RESOLVE_ISRC,
    SETUP_SCAN_ISRC,
    SETUP_SCAN_LIBRARY,
    SETUP_TITLE,
)

# ── Screen ──────────────────────────────────────────────────────────────────


class SetupScreen(Screen):
    """First launch: scan iTunes library, read ISRCs, save baseline."""

    DEFAULT_CSS = """
    SetupScreen { layout: vertical; overflow-y: auto; }
    """

    BINDINGS = [
        Binding("enter", "continue", "Continue", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._state = "running"
        self._tracks_total = 0
        self._isrc_count = 0
        self._resolved_count = 0
        self._done_lines: list[Text] = []  # completed progress bars (frozen)

    def compose(self) -> ComposeResult:
        with Vertical(id="menu-content"):
            yield Static("", id="menu-header")
            yield Static("", id="menu-body")
            yield Static("", id="menu-help")

    def on_mount(self) -> None:
        """Start setup in background."""
        self._set_header(Text(SETUP_TITLE, style=f"bold {BLUE}"))
        self._set_help("")
        self._set_body(Text(""))
        self._run_setup()

    # ── Widget updaters ────────────────────────────────────────────────────

    def _set_header(self, content) -> None:
        self.query_one("#menu-header", Static).update(content)

    def _set_body(self, content) -> None:
        self.query_one("#menu-body", Static).update(content)

    def _set_help(self, text: str) -> None:
        from music_manager.ui.render import render_help  # noqa: PLC0415

        self.query_one("#menu-help", Static).update(render_help(text, with_newline=False))

    def _render_progress(self, label: str, current: int, total: int) -> None:
        """Render: frozen done lines + current live progress bar."""
        body = Text()

        # Frozen completed bars
        for line in self._done_lines:
            body.append_text(line)
            body.append("\n")

        # Current live bar
        pct = current / total if total else 0
        bar_width = 30
        filled = int(bar_width * pct)
        bar = "█" * filled + "░" * (bar_width - filled)

        padded = label.ljust(24)
        body.append(f"  {padded}  ", style="dim")
        body.append(f"[{bar}]", style=f"bold {BLUE}")
        body.append(f"  {current}/{total}", style="dim")

        self._set_body(body)

    def _freeze_progress(self, label: str, total: int) -> None:
        """Freeze a completed progress bar (full bar at 100%)."""
        bar = "█" * 30
        line = Text()
        padded = label.ljust(24)
        line.append(f"  {padded}  ", style="dim")
        line.append(f"[{bar}]", style=f"bold {BLUE}")
        line.append(f"  {total}/{total}", style="dim")
        self._done_lines.append(line)

    # ── Setup worker ───────────────────────────────────────────────────────

    @work(thread=True)
    def _run_setup(self) -> None:
        """Run first-launch setup: scan library → scan ISRC → save baseline."""
        from music_manager.core.config import save_config  # noqa: PLC0415
        from music_manager.core.logger import log_event  # noqa: PLC0415
        from music_manager.services.tagger import scan_isrc  # noqa: PLC0415

        apple = self.app.apple  # type: ignore[attr-defined]
        paths = self.app.paths  # type: ignore[attr-defined]

        # Phase 1: Scan iTunes library
        def on_library_progress(current: int, total: int) -> None:
            self.app.call_from_thread(self._render_progress, SETUP_SCAN_LIBRARY, current, total)

        entries = apple.scan(on_progress=on_library_progress)
        self._tracks_total = len(entries)
        self.app.call_from_thread(self._freeze_progress, SETUP_SCAN_LIBRARY, self._tracks_total)

        # Phase 2: Scan ISRCs from audio files
        def on_isrc_progress(current: int, total: int) -> None:
            self.app.call_from_thread(self._render_progress, SETUP_SCAN_ISRC, current, total)

        isrc_count = scan_isrc(entries, on_progress=on_isrc_progress)
        self._isrc_count = isrc_count
        self.app.call_from_thread(self._freeze_progress, SETUP_SCAN_ISRC, isrc_count)

        # Phase 3: Save baseline to tracks.json
        from music_manager.services.tracks import Tracks  # noqa: PLC0415

        tracks = Tracks(paths.tracks_path)
        for entry in entries.values():
            entry_dict = entry.to_dict()
            entry_dict["origin"] = "baseline"
            entry_dict["status"] = None
            tracks.add(entry.apple_id, entry_dict)
        tracks.save()

        # Phase 4: Resolve ISRC → Deezer (auto-identify tracks with ISRC)
        from concurrent.futures import ThreadPoolExecutor, as_completed  # noqa: PLC0415

        from music_manager.core.models import Track  # noqa: PLC0415
        from music_manager.services.albums import Albums  # noqa: PLC0415
        from music_manager.services.resolver import resolve  # noqa: PLC0415

        albums = Albums(paths.albums_path)
        with_isrc = [
            (aid, e) for aid, e in tracks.all().items() if e.get("isrc") and not e.get("deezer_id")
        ]
        resolved_count = 0

        if with_isrc:
            total_isrc = len(with_isrc)

            def _resolve_one(item: tuple[str, dict]) -> tuple[str, Track | None]:
                apple_id, entry = item
                isrc_val = entry.get("isrc") or ""
                title = entry.get("title") or ""
                artist = entry.get("artist") or ""
                album_name = entry.get("album") or ""
                resolution = resolve(title, artist, album_name, isrc_val, albums)
                if resolution.status == "resolved" and resolution.track:
                    return (apple_id, resolution.track)
                return (apple_id, None)

            done = 0
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = {pool.submit(_resolve_one, item): item for item in with_isrc}
                for future in as_completed(futures):
                    done += 1
                    self.app.call_from_thread(
                        self._render_progress,
                        SETUP_RESOLVE_ISRC,
                        done,
                        total_isrc,
                    )
                    apple_id, trk = future.result()
                    if trk is not None:
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
                        resolved_count += 1

            tracks.save()
            albums.save()
            self.app.call_from_thread(
                self._freeze_progress,
                SETUP_RESOLVE_ISRC,
                resolved_count,
            )

        self._resolved_count = resolved_count
        save_config({"setup_done": True})

        log_event(
            "first_launch",
            total_tracks=len(entries),
            tracks_with_isrc=isrc_count,
            resolved_deezer=resolved_count,
        )

        self.app.tracks_store = tracks  # type: ignore[attr-defined]
        self.app.albums_store = albums  # type: ignore[attr-defined]
        self.app.call_from_thread(self._on_setup_done)

    def _on_setup_done(self) -> None:
        """Setup complete — append summary below frozen progress bars."""
        self._state = "done"

        body = Text()

        # Frozen progress bars
        for line in self._done_lines:
            body.append_text(line)
            body.append("\n")

        # Summary below
        body.append("\n")
        body.append(f"  {CHECK}  ", style="green")
        body.append(f"{self._tracks_total} pistes détectées\n")
        if self._resolved_count:
            body.append(f"  {CHECK}  ", style="green")
            body.append(f"{self._resolved_count} identifiée(s) sur Deezer\n")
        unresolved = self._tracks_total - self._resolved_count
        if unresolved:
            body.append(f"  {CHECK}  ", style="green")
            body.append(f"{unresolved} à identifier\n")

        self._set_body(body)
        self._set_help(HELP_SETUP_DONE)

    # ── Actions ────────────────────────────────────────────────────────────

    def action_continue(self) -> None:
        """Enter key."""
        if self._state == "done":
            self.app.on_setup_done(  # type: ignore[attr-defined]
                self._tracks_total, self._isrc_count
            )
