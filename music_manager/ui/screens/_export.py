"""Export mixin — export playlists to CSV."""

from typing import TYPE_CHECKING

from textual import work

from music_manager.ui.render import render_sub_header
from music_manager.ui.styles import CHECK
from music_manager.ui.text import HELP_BACK

if TYPE_CHECKING:
    from music_manager.ui.screens._protocol import MenuScreenProto

    _MixinBase = MenuScreenProto
else:
    _MixinBase = object


class ExportMixin(_MixinBase):
    """Export playlist methods for MenuScreen."""

    def _start_export(self) -> None:
        """Launch export: list playlists with checkboxes."""
        from music_manager.services.apple import list_playlists  # noqa: PLC0415
        from music_manager.ui.text import (  # noqa: PLC0415
            EXPORT_APPLY,
            EXPORT_BACK,
            EXPORT_NO_PLAYLISTS,
            EXPORT_TITLE,
            HELP_EXPORT,
        )

        self._return_to = "tools"

        playlists = list_playlists()

        if not playlists:
            self._view = "summary"
            self._set_header(render_sub_header(EXPORT_TITLE))
            from rich.text import Text as RichText  # noqa: PLC0415

            body = RichText()
            body.append(f"\n  {CHECK}  ", style="green")
            body.append(f"{EXPORT_NO_PLAYLISTS}\n")
            self._set_body(body)
            self._set_help(HELP_BACK, with_newline=False)
            return

        self._export_playlists = playlists
        self._export_checks = [False] * len(playlists)
        self._export_cursor = 0
        self._export_actions = [EXPORT_APPLY, EXPORT_BACK]
        self._view = "exporting"

        self._set_header(render_sub_header(EXPORT_TITLE))
        self._refresh_export_body()
        self._set_help(HELP_EXPORT)

    def _refresh_export_body(self) -> None:
        """Re-render export playlist checkboxes."""
        from music_manager.ui.render import render_complete_albums  # noqa: PLC0415

        items = [
            {"title": name, "artist": "", "local": count, "total": count}
            for name, count in self._export_playlists
        ]
        self._set_body(
            render_complete_albums(
                items,
                self._export_checks,
                self._export_cursor,
                self._export_actions,
            ),
        )

    def _export_select(self) -> None:
        """Enter on export: toggle checkbox or execute action."""
        num = len(self._export_playlists)
        if self._export_cursor < num:
            # Toggle checkbox (same as Space)
            self._export_checks[self._export_cursor] = not self._export_checks[
                self._export_cursor
            ]
            self._refresh_export_body()
            return
        action_idx = self._export_cursor - num
        if action_idx == 0:
            selected = [
                self._export_playlists[i]
                for i, checked in enumerate(self._export_checks)
                if checked
            ]
            if selected:
                self._run_export(selected)
        else:
            self._switch_view("tools")

    @work(thread=True)
    def _run_export(self, playlists: list[tuple[str, int]]) -> None:
        """Export selected playlists in background."""
        import os  # noqa: PLC0415

        from music_manager.core.logger import log_event  # noqa: PLC0415
        from music_manager.options.export import export_playlist  # noqa: PLC0415
        from music_manager.services.apple import get_playlist_tracks  # noqa: PLC0415

        total_exported = 0
        total_playlists = 0

        for name, _ in playlists:
            try:
                apple_ids = get_playlist_tracks(name)
                tracks = []
                for aid in apple_ids:
                    entry = self._tracks_store.get_by_apple_id(aid) if self._tracks_store else None
                    if entry:
                        tracks.append(entry)
                    elif self.app.apple:  # type: ignore[attr-defined]
                        lib = self.app.apple.get_all()  # type: ignore[attr-defined]
                        lib_entry = lib.get(aid)
                        if lib_entry:
                            tracks.append(
                                {
                                    "title": lib_entry.title,
                                    "artist": lib_entry.artist,
                                    "album": lib_entry.album,
                                    "genre": lib_entry.genre,
                                    "year": lib_entry.year,
                                    "track_number": lib_entry.track_number or "",
                                    "disk_number": lib_entry.disk_number or "",
                                    "album_artist": lib_entry.album_artist,
                                    "isrc": lib_entry.isrc,
                                }
                            )

                if tracks and self._paths:
                    safe_name = name.replace("/", "_").replace(":", "_")
                    os.makedirs(self._paths.playlists_dir, exist_ok=True)
                    filepath = os.path.join(self._paths.playlists_dir, f"{safe_name}.csv")
                    count = export_playlist(tracks, filepath)
                    total_exported += count
                    total_playlists += 1
                    log_event("export_playlist", name=name, tracks=count, path=filepath)
            except Exception as exc:  # noqa: BLE001
                from music_manager.core.logger import log_event as log_err  # noqa: PLC0415

                log_err("worker_error", error=str(exc))

        self.app.call_from_thread(self._export_done, total_playlists, total_exported)

    def _export_done(self, playlists: int, tracks: int) -> None:
        """Show export summary."""
        from music_manager.core.logger import log_event  # noqa: PLC0415
        from music_manager.ui.text import EXPORT_TITLE  # noqa: PLC0415

        log_event("export_done", playlists=playlists, tracks=tracks)
        self._view = "summary"
        self._set_header(render_sub_header(EXPORT_TITLE))

        from rich.text import Text as RichText  # noqa: PLC0415

        body = RichText()
        if playlists:
            body.append(f"\n  {CHECK}  ", style="green")
            body.append(f"{playlists} playlist(s) exportée(s), {tracks} pistes\n")
        else:
            body.append(f"\n  {CHECK}  Aucune playlist exportée\n", style="green")
        self._set_body(body)
        self._set_help(HELP_BACK, with_newline=False)
