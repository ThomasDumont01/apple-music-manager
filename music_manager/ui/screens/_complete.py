"""Complete albums mixin — import missing tracks from identified albums."""

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


class CompleteMixin(_MixinBase):
    """Complete-albums feature methods for MenuScreen."""

    def _start_complete(self) -> None:
        """Launch complete-albums: find incomplete, show checkboxes."""
        from music_manager.options.complete_albums import find_incomplete_albums  # noqa: PLC0415
        from music_manager.ui.text import (  # noqa: PLC0415
            COMPLETE_APPLY,
            COMPLETE_BACK,
            COMPLETE_NO_IDENTIFIED,
            COMPLETE_NONE_FOUND,
            COMPLETE_TITLE,
            HELP_COMPLETE,
        )

        self._return_to = "tools"

        if not self._tracks_store or not self._albums_store:
            return

        has_identified = any(e.get("deezer_id") for e in self._tracks_store.all().values())
        if not has_identified:
            self._view = "summary"
            self._set_header(render_sub_header(COMPLETE_TITLE))
            from rich.text import Text as RichText  # noqa: PLC0415

            body = RichText()
            body.append(f"\n  {CHECK}  ", style="green")
            body.append(f"{COMPLETE_NO_IDENTIFIED}\n")
            self._set_body(body)
            self._set_help(HELP_BACK, with_newline=False)
            return

        albums = find_incomplete_albums(self._tracks_store, self._albums_store)

        if not albums:
            self._view = "summary"
            self._set_header(render_sub_header(COMPLETE_TITLE))
            from rich.text import Text as RichText  # noqa: PLC0415

            body = RichText()
            body.append(f"\n  {CHECK}  ", style="green")
            body.append(f"{COMPLETE_NONE_FOUND}\n")
            self._set_body(body)
            self._set_help(HELP_BACK, with_newline=False)
            return

        self._complete_albums = albums
        self._complete_checks = [False] * len(albums)
        self._complete_cursor = 0
        self._complete_actions = [COMPLETE_APPLY, COMPLETE_BACK]
        self._view = "completing"

        total_missing = sum(a["total"] - a["local"] for a in albums)
        title = f"{COMPLETE_TITLE}  ({len(albums)} albums, {total_missing} pistes manquantes)"
        self._set_header(render_sub_header(title))
        self._refresh_complete_body()
        self._set_help(HELP_COMPLETE)

    def _refresh_complete_body(self) -> None:
        """Re-render complete albums checkboxes."""
        from music_manager.ui.render import render_complete_albums  # noqa: PLC0415

        self._set_body(
            render_complete_albums(
                self._complete_albums,
                self._complete_checks,
                self._complete_cursor,
                self._complete_actions,
            ),
        )

    def _complete_select(self) -> None:
        """Enter on complete: launch batch for selected albums."""
        num_albums = len(self._complete_albums)
        if self._complete_cursor < num_albums:
            selected = [
                a for a, checked in zip(self._complete_albums, self._complete_checks) if checked
            ]
            if selected:
                self._run_complete_batch(selected)
            return
        action_idx = self._complete_cursor - num_albums
        if action_idx == 0:
            selected = [
                a for a, checked in zip(self._complete_albums, self._complete_checks) if checked
            ]
            if selected:
                self._run_complete_batch(selected)
        else:
            self._switch_view("tools")

    @work(thread=True)
    def _run_complete_batch(self, albums: list[dict]) -> None:
        """Complete selected albums in background."""
        from music_manager.core.logger import log_event  # noqa: PLC0415
        from music_manager.options.complete_albums import complete_album  # noqa: PLC0415

        if not (self._paths and self._tracks_store and self._albums_store):
            return

        total_imported = 0
        total_failed = 0

        self._cancel_requested = False

        for album_idx, album in enumerate(albums):
            if self._cancel_requested:
                break
            album_id = album["album_id"]
            album_title = album.get("title", "")

            def on_progress(current: int, total: int) -> None:
                self.app.call_from_thread(
                    self._complete_render_progress,
                    album_title,
                    current,
                    total,
                    album_idx + 1,
                    len(albums),
                )

            try:
                prefs_path = self._paths.preferences_path if self._paths else ""
                result = complete_album(
                    album_id,
                    self._paths,
                    self._tracks_store,
                    self._albums_store,
                    on_progress=on_progress,
                    preferences_path=prefs_path,
                    should_cancel=lambda: self._cancel_requested,
                )
                total_imported += result.tracks_imported
                total_failed += len(result.pending)
                log_event(
                    "complete_album",
                    album=album_title,
                    imported=result.tracks_imported,
                    failed=len(result.pending),
                )
                self._save_all()
            except Exception as exc:  # noqa: BLE001
                from music_manager.core.logger import log_event as log_err  # noqa: PLC0415

                log_err("worker_error", error=str(exc))

        self.app.call_from_thread(self._complete_done, total_imported, total_failed)

    def _complete_render_progress(
        self,
        album_title: str,
        current: int,
        total: int,
        album_idx: int,
        album_total: int,
    ) -> None:
        """Show progress during album completion."""
        from rich.text import Text as RichText  # noqa: PLC0415

        from music_manager.ui.styles import BLUE  # noqa: PLC0415
        from music_manager.ui.text import COMPLETE_TITLE  # noqa: PLC0415

        pct = current / total if total else 0
        bar_width = 30
        filled = int(bar_width * pct)
        bar = "█" * filled + "░" * (bar_width - filled)

        self._view = "completing_progress"
        self._set_header(
            render_sub_header(
                f"{COMPLETE_TITLE}  ({album_idx}/{album_total})",
            )
        )
        body = RichText()
        body.append(f"\n  {album_title}  ", style="bold")
        body.append(f"[{bar}]", style=f"bold {BLUE}")
        body.append(f"  {current}/{total}", style="dim")
        self._set_body(body)
        self._set_help("")

    def _complete_done(self, imported: int, failed: int) -> None:
        """Show completion summary."""
        from music_manager.core.logger import log_event  # noqa: PLC0415
        from music_manager.ui.render import render_complete_summary  # noqa: PLC0415
        from music_manager.ui.text import COMPLETE_TITLE  # noqa: PLC0415

        log_event(
            "complete_done",
            imported=imported,
            failed=failed,
            total_tracks=len(self._tracks_store.all()) if self._tracks_store else 0,
        )
        self._save_all()
        self._refresh_stats()
        self._view = "summary"
        self._set_header(render_sub_header(COMPLETE_TITLE))
        self._set_body(render_complete_summary(imported, failed))
        self._set_help(HELP_BACK, with_newline=False)
