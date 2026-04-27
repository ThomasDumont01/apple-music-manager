"""Import mixin — CSV import flow and queue management."""

import os
from typing import TYPE_CHECKING

from rich.text import Text
from textual import work
from textual.containers import ScrollableContainer

from music_manager.core.io import load_csv
from music_manager.options.import_tracks import ImportResult
from music_manager.ui.render import (
    render_final_summary,
    render_import_body,
    render_import_header,
    render_import_line,
    render_playlist_result,
)
from music_manager.ui.screens._core import _ImportCancelled
from music_manager.ui.styles import CHECK
from music_manager.ui.text import HELP_BACK, HELP_IMPORT, HELP_REVIEW_START

if TYPE_CHECKING:
    from music_manager.ui.screens._protocol import MenuScreenProto

    _MixinBase = MenuScreenProto
else:
    _MixinBase = object


class ImportMixin(_MixinBase):
    """Import CSV flow methods for MenuScreen."""

    # ── Import ──────────────────────────────────────────────────────────────

    def _start_import_all(self) -> None:
        """Collect all CSV paths and import them sequentially."""
        csv_paths = []
        for directory in (os.path.dirname(self._requests_path), self._playlists_dir):
            if not directory or not os.path.isdir(directory):
                continue
            for name in sorted(os.listdir(directory)):
                if name.endswith(".csv"):
                    csv_paths.append(os.path.join(directory, name))

        if not csv_paths:
            return

        self._import_queue = csv_paths
        self._import_queue_idx = 0
        self._start_import(self._import_queue[0])

    @work(thread=True)
    def _sync_playlist_all_skipped(self, csv_path: str, csv_name: str, rows: list) -> None:
        """All tracks skipped — sync playlist in background, show result."""
        from music_manager.options.import_tracks import find_apple_id  # noqa: PLC0415
        from music_manager.services.apple import add_to_playlist  # noqa: PLC0415

        if not self._tracks_store:
            self.app.call_from_thread(self._on_playlist_synced, csv_name, 0, len(rows))
            return

        try:
            playlist_ids = []
            for row in rows:
                apple_id = find_apple_id(
                    row.get("isrc", ""),
                    row.get("title", ""),
                    row.get("artist", ""),
                    self._tracks_store,
                )
                if apple_id:
                    playlist_ids.append(apple_id)

            pl_added = 0
            if playlist_ids:
                pl_added = add_to_playlist(csv_name, playlist_ids)
            pl_already = len(playlist_ids) - pl_added

            self.app.call_from_thread(self._on_playlist_synced, csv_name, pl_added, pl_already)
        except Exception as _exc:  # noqa: BLE001
            from music_manager.core.logger import log_worker_error  # noqa: PLC0415

            log_worker_error(_exc)
            self.app.call_from_thread(self._on_playlist_synced, csv_name, 0, len(rows))

    def _on_playlist_synced(self, csv_name: str, pl_added: int, pl_already: int) -> None:
        """Playlist sync done — show summary."""
        from music_manager.core.logger import log_event  # noqa: PLC0415

        log_event("playlist_sync", playlist=csv_name, added=pl_added, already=pl_already)
        from rich.text import Text as RichText  # noqa: PLC0415

        body = RichText()
        body.append_text(render_playlist_result(csv_name, pl_added, pl_already))
        self._set_body(body)
        self._view = "summary"

        if self._import_queue and self._import_queue_idx + 1 < len(self._import_queue):
            self._import_queue_idx += 1
            self._set_help("⏎  CSV suivant    esc  retour au menu", with_newline=False)
            self._view = "queue_next"
        else:
            self._import_queue = []
            self._set_help(HELP_BACK, with_newline=False)

    def _skip_to_next_in_queue(self) -> None:
        """Skip current CSV in queue and move to next (or return to menu)."""
        if self._import_queue_idx + 1 < len(self._import_queue):
            self._import_queue_idx += 1
            self._start_import(self._import_queue[self._import_queue_idx])
        else:
            self._import_queue = []
            self._switch_view("main")

    def _start_import(self, csv_path: str) -> None:
        """Launch CSV import — or show message if all already present."""
        rows = load_csv(csv_path)
        csv_name = os.path.splitext(os.path.basename(csv_path))[0]

        if not rows:
            if self._import_queue:
                self._skip_to_next_in_queue()
                return
            from rich.text import Text as RichText  # noqa: PLC0415

            self._set_header(render_import_header(csv_name, 0))
            body = RichText()
            body.append(f"\n  {CHECK}  ", style="green")
            body.append("CSV vide.\n")
            self._set_body(body)
            self._view = "summary"
            self._set_help(HELP_BACK, with_newline=False)
            return

        is_playlist = os.path.dirname(os.path.abspath(csv_path)) == (
            os.path.abspath(self._playlists_dir)
        )
        done = sum(1 for row in rows if self._is_in_library(row))
        if done >= len(rows):
            from rich.text import Text as RichText  # noqa: PLC0415

            self._set_header(render_import_header(csv_name, len(rows)))
            self._import_csv = csv_path
            self._import_lines = []

            if is_playlist:
                # Sync playlist in background, show summary when done
                self._sync_playlist_all_skipped(csv_path, csv_name, rows)
            else:
                body = RichText()
                body.append(f"\n  {CHECK}  ", style="green")
                body.append(f"{done} existante(s)\n")
                self._set_body(body)
                self._view = "summary"
                self._set_help(HELP_BACK, with_newline=False)
            return

        self._view = "importing"
        self._items = []
        self._selectable = []
        self._import_lines = []
        self._import_csv = csv_path
        self._import_result = None
        self._import_total = len(rows)

        self._set_header(render_import_header(csv_name, self._import_total))
        self._set_body(render_import_body(self._import_lines))
        self._set_help(HELP_IMPORT, with_newline=False)
        from music_manager.core.logger import log_event  # noqa: PLC0415

        log_event(
            "import_start",
            csv_name=os.path.basename(csv_path),
            total_rows=self._import_total,
        )
        self._run_import(csv_path)

    @work(thread=True)
    def _run_import(self, csv_path: str) -> None:
        """Run import in background thread."""
        from music_manager.options.import_tracks import process_csv  # noqa: PLC0415
        from music_manager.services.youtube import (  # noqa: PLC0415
            reset_throttle,
            set_rate_limit_callback,
        )

        if not (self._paths and self._tracks_store and self._albums_store):
            self.app.call_from_thread(self._on_import_done, ImportResult())
            return

        reset_throttle()

        def _on_rate_limit(seconds: int, reason: str) -> None:
            self.app.call_from_thread(self._on_import_rate_limit, seconds, reason)

        set_rate_limit_callback(_on_rate_limit)

        try:
            paths = self._paths
            tracks_store = self._tracks_store
            albums_store = self._albums_store
            self._cancel_requested = False

            def on_row(idx: int, total: int, title: str, artist: str, status: str) -> None:
                if self._cancel_requested:
                    raise _ImportCancelled
                self.app.call_from_thread(self._on_import_row, idx, total, title, artist, status)

            result = process_csv(csv_path, paths, tracks_store, albums_store, on_row=on_row)
        except _ImportCancelled:
            result = ImportResult()
            self.app.call_from_thread(self._switch_view, "main")
            return
        except Exception as exc:  # noqa: BLE001
            from music_manager.core.logger import log_event  # noqa: PLC0415

            log_event("import_error", error=str(exc))
            result = ImportResult()
        finally:
            set_rate_limit_callback(None)
        self.app.call_from_thread(self._on_import_done, result)

    def _on_import_row(self, idx: int, total: int, title: str, artist: str, status: str) -> None:
        """Update import progress."""
        self._import_lines.append(render_import_line(idx, total, title, artist, status))
        self._set_body(render_import_body(self._import_lines))
        # Scroll to bottom to show latest import line
        try:
            self.query_one("#menu-scroll", ScrollableContainer).scroll_end(animate=False)
        except Exception:  # noqa: BLE001
            pass

    def _on_import_rate_limit(self, seconds: int, reason: str = "") -> None:
        """Show rate limit warning in import progress."""
        from music_manager.ui.text import (  # noqa: PLC0415
            RATE_LIMIT_REASON,
            RATE_LIMIT_WAIT,
            format_wait,
        )

        wait = format_wait(seconds)
        if reason:
            msg = RATE_LIMIT_REASON.format(reason=reason, wait=wait)
        else:
            msg = RATE_LIMIT_WAIT.format(wait=wait)
        self._import_lines.append(Text(f"  ⏳ {msg}", style="yellow"))
        self._set_body(render_import_body(self._import_lines))

    def _on_import_done(self, result) -> None:
        """Import finished — show summary, then review if pending."""
        self._import_result = result

        body = render_final_summary(
            self._import_lines, result.imported, result.skipped, len(result.pending)
        )
        self._set_body(body)

        if result.pending:
            self._set_help(HELP_REVIEW_START, with_newline=False)
            self._view = "import_done_pending"
        else:
            self._finish_import()

    # ── Finish ──────────────────────────────────────────────────────────────

    def _finish_import(self) -> None:
        """Show final summary + handle playlist + clean CSV."""
        self._view = "summary"
        result = self._import_result

        review_imported = self._batch_import_done
        imported = (result.imported if result else 0) + review_imported
        skipped = result.skipped if result else 0
        pending_left = self._review_skipped
        deleted = self._review_deleted
        ignored = self._review_ignored

        # Playlist: batch add tracks imported during review
        csv_name = os.path.splitext(os.path.basename(self._import_csv))[0]
        is_playlist = os.path.dirname(os.path.abspath(self._import_csv)) == (
            os.path.abspath(self._playlists_dir)
        )

        if is_playlist:
            review_added = 0
            review_ids = []
            for pending, _, _ in self._accepted:
                if pending.track and pending.track.apple_id:
                    review_ids.append(pending.track.apple_id)
            if review_ids:
                from music_manager.services.apple import add_to_playlist  # noqa: PLC0415

                review_added = add_to_playlist(csv_name, review_ids)

            pl_added = (result.playlist_added if result else 0) + review_added
            pl_already = result.playlist_already if result else 0

            # Playlist: lines + playlist summary only (no import summary)
            body = render_import_body(self._import_lines)
            body.append_text(render_playlist_result(csv_name, pl_added, pl_already))
        else:
            body = render_final_summary(
                self._import_lines, imported, skipped, pending_left, deleted, ignored
            )

        # Clean CSV — remove rows now in library (after review imports)
        self._clean_csv_after_review()

        self._set_body(body)

        # Queue: if more CSVs to process, auto-advance after brief display
        if self._import_queue and self._import_queue_idx + 1 < len(self._import_queue):
            self._import_queue_idx += 1
            self._set_help("⏎  CSV suivant    esc  retour au menu", with_newline=False)
            self._view = "queue_next"
        else:
            self._import_queue = []
            self._set_help(HELP_BACK, with_newline=False)

    def _clean_csv_after_review(self) -> None:
        """Re-save CSV: remove rows imported/deleted during review."""
        from music_manager.core.io import save_csv  # noqa: PLC0415

        csv_path = self._import_csv
        if not csv_path:
            return

        # Playlists: CSV is the canonical playlist definition, never auto-remove rows
        is_playlist = os.path.dirname(os.path.abspath(csv_path)) == (
            os.path.abspath(self._playlists_dir)
        )
        if is_playlist:
            return

        # Build set of resolved CSV keys (imported during review)
        # _to_delete rows already removed by _execute_review_decisions
        resolved_keys: set[tuple[str, str]] = set()
        for pending, _, _ in self._accepted:
            resolved_keys.add((pending.csv_title.lower(), pending.csv_artist.lower()))

        if not resolved_keys:
            return

        rows = load_csv(csv_path)
        remaining = [
            row
            for row in rows
            if (row.get("title", "").lower(), row.get("artist", "").lower()) not in resolved_keys
        ]
        if len(remaining) < len(rows):
            save_csv(csv_path, remaining)
