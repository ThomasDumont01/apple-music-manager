"""Menu screen core — state management, rendering, navigation dispatch."""

import os
import subprocess
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer, Vertical
from textual.screen import Screen
from textual.widgets import Input, Static

if TYPE_CHECKING:
    from music_manager.ui.screens._protocol import MenuScreenProto

    # Let the type checker see mixin methods on self
    class _Base(Screen, MenuScreenProto): ...  # type: ignore[misc]
else:
    _Base = Screen

from music_manager.core.config import Paths
from music_manager.core.io import load_csv
from music_manager.core.models import PendingTrack, Track
from music_manager.options.import_tracks import ImportResult
from music_manager.options.modify_track import AlbumMatch, TrackMatch
from music_manager.services.albums import Albums
from music_manager.services.tracks import Tracks
from music_manager.ui.render import (
    render_batch_decision,
    render_help,
    render_main_header,
    render_menu_options,
    render_sub_header,
)
from music_manager.ui.styles import CHECK
from music_manager.ui.text import (
    HELP_HELP,
    HELP_MAIN,
    HELP_SUB,
    HELP_TEXT,
    MAINTENANCE_ITEMS,
    TOOLS_ITEMS,
)

# ── Exceptions ─────────────────────────────────────────────────────────────


class _ImportCancelled(Exception):
    """Raised when user presses Esc during import."""


# ── Screen ──────────────────────────────────────────────────────────────────


class MenuScreenCore(_Base):
    """Base class: state, compose, rendering, navigation dispatch."""

    DEFAULT_CSS = """
    MenuScreen {
        layout: vertical;
        overflow-y: auto;
    }
    #menu-scroll {
        height: auto;
    }
    #menu-input {
        margin: 0 0 0 2;
        padding: 0;
        height: 1;
        border: none;
        background: transparent;
    }
    #menu-search-input {
        margin: 1 0 0 2;
        padding: 0;
        height: 1;
        border: none;
        background: transparent;
    }
    """

    BINDINGS = [
        Binding("up,k", "move(-1)", "Up", show=False),
        Binding("down,j", "move(1)", "Down", show=False),
        Binding("enter", "select", "Select", show=False),
        Binding("escape", "back", "Back", show=False),
        Binding("space", "toggle", "Toggle", show=False),
        Binding("s", "skip", "Skip", show=False),
        Binding("p", "preview", "Preview", show=False),
    ]

    def __init__(
        self,
        tracks_count: int = 0,
        albums_count: int = 0,
        identified_count: int = 0,
        tracks_store: Tracks | None = None,
        albums_store: Albums | None = None,
        paths: Paths | None = None,
        requests_path: str = "",
        playlists_dir: str = "",
    ) -> None:
        super().__init__()
        self._tracks_count = tracks_count
        self._albums_count = albums_count
        self._identified_count = identified_count
        self._tracks_store = tracks_store
        self._albums_store = albums_store
        self._paths = paths
        self._requests_path = requests_path
        self._playlists_dir = playlists_dir
        self._view = "main"
        self._cursor = 0
        self._items: list[tuple[str, str] | None] = []
        self._selectable: list[int] = []

        # Import state
        self._import_queue: list[str] = []
        self._import_queue_idx = 0
        self._cancel_requested = False
        self._return_to = "main"  # where Esc returns from summary
        self._import_lines: list = []
        self._import_csv = ""
        self._import_total = 0
        self._import_result: ImportResult | None = None

        # Review state
        self._pending: list[PendingTrack] = []
        self._pending_idx = 0
        self._review_cursor = 0
        self._review_options: list[str] = []
        self._review_skipped = 0
        self._review_deleted = 0
        self._review_ignored = 0
        # Decisions collected during review, executed at the end
        # (pending, action_type, data)
        self._accepted: list[tuple[PendingTrack, str, Track | dict | None]] = []
        self._to_delete: list[PendingTrack] = []
        self._batch_cursor = 0
        self._batch_import_idx = 0
        self._batch_import_done = 0
        self._search_type = ""  # "deezer" or "youtube"

        # Preview state
        self._preview_proc: subprocess.Popen | None = None

        # Fix-metadata state
        self._fix_albums: list = []
        self._fix_album_idx = 0
        self._fix_result: dict = {"corrected": 0, "up_to_date": 0, "skipped": 0}
        self._fix_checks: list[bool] = []
        self._fix_cursor = 0
        self._fix_actions: list[str] = []
        self._fix_unique_indices: list[int] = []
        self._fix_explicit_queue: list[tuple[str, bool]] = []

        # Maintenance state
        self._maintenance_pending: tuple[str, int] = ("", 0)

        # Export playlist state
        self._export_playlists: list[tuple[str, int]] = []
        self._export_checks: list[bool] = []
        self._export_cursor = 0
        self._export_actions: list[str] = []

        # Complete albums state
        self._complete_albums: list[dict] = []
        self._complete_checks: list[bool] = []
        self._complete_cursor = 0
        self._complete_actions: list[str] = []

        # Duplicates state
        self._dup_groups: list[list[dict]] = []
        self._dup_best: list[int] = []
        self._dup_idx = 0
        self._dup_cursor = 0
        self._dup_actions: list[str] = []
        self._dup_result: dict = {"removed": 0, "skipped": 0, "ignored": 0}

        # Unmatched tracks (album edition change)
        self._modify_unmatched: list = []

        # Identify state
        self._identify_result = None
        self._identify_albums_to_review: list[dict] = []
        self._identify_singles_no_album: list[str] = []
        self._identify_album_idx = 0
        self._identify_apple_ids: list[str] = []
        self._identify_best_edition: int = 0

        # Modify track state
        self._modify_track_items: list[tuple[str, str, str]] = []  # (label, suffix, apple_id)
        self._modify_album_items: list[tuple[str, str, int]] = []  # (label, artist, count)
        self._modify_selectable: list[int] = []
        self._modify_cursor = 0
        self._modify_selected_track: TrackMatch | None = None
        self._modify_selected_album: AlbumMatch | None = None
        self._modify_actions_items: list[tuple[str, str] | None] = []
        self._modify_actions_selectable: list[int] = []
        self._modify_editions: list[dict] = []
        self._modify_covers: list[dict] = []
        self._modify_meta_fields: list[tuple[str, str, str]] = []  # (label, key, value)
        self._modify_meta_changes: dict = {}
        self._modify_editing_field: str = ""  # field key being edited
        self._modify_tracks_data: list = []  # TrackMatch objects
        self._modify_albums_data: list = []  # AlbumMatch objects

    def compose(self) -> ComposeResult:
        with Vertical(id="menu-content"):
            yield Static("", id="menu-header")
            yield Input(placeholder="recherche...", id="menu-search-input")
            with ScrollableContainer(id="menu-scroll"):
                yield Static("", id="menu-body")
            yield Input(placeholder="collez le lien ici...", id="menu-input")
            yield Static("", id="menu-help")

    def on_mount(self) -> None:
        """Show main view."""
        # Prevent scroll container from capturing keyboard (up/down)
        self.query_one("#menu-scroll", ScrollableContainer).can_focus = False

        input_w = self.query_one("#menu-input", Input)
        input_w.display = False
        input_w.disabled = True
        search_w = self.query_one("#menu-search-input", Input)
        search_w.display = False
        search_w.disabled = True
        self._switch_view("main")

    # ── Widget updaters ────────────────────────────────────────────────────

    def _set_header(self, content) -> None:
        """Update header."""
        self.query_one("#menu-header", Static).update(content)

    def _set_body(self, content, check_scroll: bool = True) -> None:
        """Update body. Auto-enables scroll if content overflows terminal."""
        body = self.query_one("#menu-body", Static)
        body.display = True
        body.update(content)
        if check_scroll:
            line_count = str(content).count("\n") + 1 if content else 0
            if line_count > self.app.size.height - 7:
                self._enable_scroll()
                # Scroll to keep cursor (❯) visible
                self._scroll_to_cursor(content)
            else:
                self._disable_scroll()

    def _scroll_to_cursor(self, content) -> None:
        """Scroll to keep the cursor marker visible."""
        try:
            scroll = self.query_one("#menu-scroll", ScrollableContainer)
            text = str(content)
            lines = text.split("\n")
            cursor_line = 0
            for i, line in enumerate(lines):
                if "❯" in line:
                    cursor_line = i
                    break
            # Scroll so cursor line is roughly centered
            visible_height = self.app.size.height - 7
            target_y = max(0, cursor_line - visible_height // 2)
            scroll.scroll_to(0, target_y, animate=False)
        except Exception:  # noqa: BLE001
            pass

    def _set_help(self, text: str, with_newline: bool = True) -> None:
        """Update help."""
        self.query_one("#menu-help", Static).update(render_help(text, with_newline))

    def _enable_scroll(self) -> None:
        """Enable scroll on body (for import view with many lines)."""
        scroll = self.query_one("#menu-scroll", ScrollableContainer)
        scroll.styles.height = "1fr"

    def _disable_scroll(self) -> None:
        """Disable scroll — normal layout (menu, review, summary)."""
        scroll = self.query_one("#menu-scroll", ScrollableContainer)
        scroll.styles.height = "auto"
        scroll.scroll_home(animate=False)

    # ── View switching ──────────────────────────────────────────────────────

    def _switch_view(self, view: str) -> None:
        """Switch between main, tools, maintenance, help."""
        # Flush all stores to disk on navigation back to menu
        self._save_all()

        self._view = view
        self._cursor = 0
        # Set return_to based on view hierarchy
        if view in ("tools", "maintenance", "help"):
            self._return_to = "main"
        elif view == "main":
            self._return_to = "main"

        if view == "help":
            self._items = []
            self._selectable = []
            from rich.text import Text  # noqa: PLC0415

            self._set_header(Text.from_markup(HELP_TEXT))
            self.query_one("#menu-body", Static).display = False
            self._set_help(HELP_HELP, with_newline=False)
            return

        if view == "main":
            self._refresh_stats()
            self._items = self._build_main()
            self._set_header(render_main_header(self._tracks_count, self._albums_count))
        elif view == "tools":
            self._refresh_stats()  # Update counts after operations
            self._items = list(TOOLS_ITEMS)
            self._set_header(render_sub_header("Outils"))
        elif view == "maintenance":
            self._items = list(MAINTENANCE_ITEMS)
            self._set_header(render_sub_header("Maintenance"))
        else:
            return

        self._selectable = [
            i
            for i, item in enumerate(self._items)
            if item is not None and item[0] != "__sep__"
        ]
        self.query_one("#menu-body", Static).display = True
        self._refresh_menu()
        self._set_help(HELP_MAIN if view == "main" else HELP_SUB)

    def _save_all(self) -> None:
        """Flush tracks + albums stores to disk if dirty."""
        if self._tracks_store:
            self._tracks_store.save()
        if self._albums_store:
            self._albums_store.save()

    def _refresh_stats(self) -> None:
        """Recompute stats from live tracks_store."""
        if not self._tracks_store:
            return
        all_tracks = self._tracks_store.all()
        self._tracks_count = len(all_tracks)
        self._albums_count = len(
            {e.get("album", "") for e in all_tracks.values() if e.get("album")}
        )
        # Count identified — exclude permanently ignored tracks
        from music_manager.core.io import load_json  # noqa: PLC0415

        ignored: set[str] = set()
        if self._paths:
            prefs = load_json(self._paths.preferences_path)
            raw = prefs.get("ignored_tracks", [])
            if isinstance(raw, list):
                ignored = set(raw)

        self._identified_count = sum(
            1
            for e in all_tracks.values()
            if e.get("deezer_id")
            or f"{(e.get('title') or '').lower()}::{(e.get('artist') or '').lower()}" in ignored
        )

    def _refresh_menu(self) -> None:
        """Re-render menu body."""
        self._set_body(
            render_menu_options(self._items, self._selectable, self._cursor, self._view)
        )

    # ── Menu building ──────────────────────────────────────────────────────

    def _build_main(self) -> list[tuple[str, str] | None]:
        """Build main menu items."""
        items: list[tuple[str, str] | None] = []

        unidentified = self._tracks_count - self._identified_count
        if unidentified > 0:
            items.append(("identify", f"▶ Identifier la bibliothèque|{unidentified}|red"))

        from music_manager.ui.text import SECTION_PISTES, SECTION_PLAYLISTS  # noqa: PLC0415

        data_csvs = self._scan_csvs(os.path.dirname(self._requests_path))
        if data_csvs:
            items.append(("__sep__", SECTION_PISTES))
            items.extend(data_csvs)

        playlist_csvs = self._scan_csvs(self._playlists_dir)
        if playlist_csvs:
            items.append(("__sep__", SECTION_PLAYLISTS))
            items.extend(playlist_csvs)

        # "Tout traiter" if more than 1 CSV
        all_csvs = data_csvs + playlist_csvs
        if len(all_csvs) > 1:
            items.append(None)
            items.append(("import_all", "Tout traiter"))

        items.append(None)
        items.append(("tools", "Outils"))
        items.append(("maintenance", "Maintenance"))
        items.append(("help", "Aide"))
        return items

    def _scan_csvs(self, directory: str) -> list[tuple[str, str]]:
        """Scan directory for CSV files (converts Exportify format if needed)."""
        if not directory or not os.path.isdir(directory):
            return []
        from music_manager.core.io import convert_exportify  # noqa: PLC0415

        result = []
        for name in sorted(os.listdir(directory)):
            if not name.endswith(".csv"):
                continue
            path = os.path.join(directory, name)
            convert_exportify(path)
            rows = load_csv(path)
            total = len(rows)
            done = self._count_done(rows)
            label = os.path.splitext(name)[0]
            if not total:
                badge = CHECK
            elif done >= total:
                badge = CHECK
            else:
                badge = f"{done}/{total}"
            result.append((f"csv:{path}", f"{label}|{badge}|csv"))
        return result

    def _count_done(self, rows: list[dict]) -> int:
        """Count CSV rows already present in the library (any status)."""
        if not self._tracks_store:
            return 0
        return sum(1 for row in rows if self._is_in_library(row))

    def _is_in_library(self, row: dict) -> bool:
        """Check if a CSV row matches any entry in tracks.json (any status except failed)."""
        from music_manager.pipeline.dedup import is_duplicate  # noqa: PLC0415

        if not self._tracks_store:
            return False

        return is_duplicate(
            row.get("isrc", ""),
            row.get("title", ""),
            row.get("artist", ""),
            self._tracks_store,
        )

    # ── Actions ─────────────────────────────────────────────────────────────

    def action_move(self, direction: int) -> None:
        """Move cursor."""
        if self._view in ("search_input", "modify_search", "modify_meta_edit"):
            return
        if self._view == "duplicates":
            self._dup_move(direction)
            return
        if self._view == "maintenance_confirm":
            self._modify_cursor = (self._modify_cursor + direction) % 2
            self._refresh_maintenance_confirm()
            return
        if self._view == "exporting":
            total = len(self._export_playlists) + len(self._export_actions)
            self._export_cursor = (self._export_cursor + direction) % total
            self._refresh_export_body()
            return
        if self._view == "completing":
            total = len(self._complete_albums) + len(self._complete_actions)
            self._complete_cursor = (self._complete_cursor + direction) % total
            self._refresh_complete_body()
            return
        if self._view == "fixing":
            self._fix_move(direction)
        elif self._view == "reviewing":
            self._review_move(direction)
        elif self._view == "batch_decision":
            self._batch_cursor = (self._batch_cursor + direction) % 3
            self._set_body(render_batch_decision(self._batch_cursor))
        elif self._view == "modify_results":
            if self._modify_selectable:
                new = (self._modify_cursor + direction) % len(self._modify_selectable)
                self._modify_cursor = new
                self._refresh_modify_search()
        elif self._view == "modify_actions":
            if self._modify_actions_selectable:
                total_act = len(self._modify_actions_selectable)
                self._modify_cursor = (self._modify_cursor + direction) % total_act
                self._refresh_modify_actions()
        elif self._view == "modify_unmatched":
            self._modify_cursor = (self._modify_cursor + direction) % 2
            self._on_modify_done_with_unmatched(True, "", self._modify_unmatched)
        elif self._view == "modify_delete_confirm":
            self._modify_cursor = (self._modify_cursor + direction) % 2
            self._modify_show_delete_confirm()
        elif self._view == "identify_album_pick":
            total = len(self._modify_editions) + 1  # +1 for skip
            self._modify_cursor = (self._modify_cursor + direction) % total
            self._refresh_modify_editions()
        elif self._view == "modify_editions":
            total = len(self._modify_editions) + 1  # +1 for back
            self._modify_cursor = (self._modify_cursor + direction) % total
            self._refresh_modify_editions()
        elif self._view == "modify_covers":
            total = len(self._modify_covers) + 1  # +1 for back
            self._modify_cursor = (self._modify_cursor + direction) % total
            self._refresh_modify_covers()
        elif self._view == "modify_metadata":
            total = len(self._modify_meta_fields) + 2  # +2 for apply + back
            self._modify_cursor = (self._modify_cursor + direction) % total
            self._refresh_modify_metadata()
        elif self._selectable:
            self._cursor = (self._cursor + direction) % len(self._selectable)
            self._refresh_menu()

    def action_select(self) -> None:
        """Enter key."""
        # Stop preview if playing
        if self._preview_proc is not None:
            try:
                self._preview_proc.kill()
            except OSError:
                pass
            self._preview_proc = None

        if self._view == "duplicates":
            self._dup_select()
            return
        if self._view == "maintenance_confirm":
            if self._modify_cursor == 0:
                self._confirm_maintenance()
            else:
                self._switch_view("maintenance")
            return
        if self._view == "exporting":
            self._export_select()
            return
        if self._view == "completing":
            self._complete_select()
            return
        if self._view == "fixing":
            self._fix_select()
            return
        if self._view == "reviewing":
            self._review_select()
            return
        if self._view == "search_failed":
            self._render_review()
            return
        if self._view == "search_input":
            return  # Input widget handles Enter via on_input_submitted
        if self._view == "modify_meta_edit":
            return  # Input widget handles Enter
        if self._view == "modify_unmatched":
            self._handle_unmatched_decision()
            return
        if self._view == "modify_delete_confirm":
            self._handle_delete_decision()
            return
        if self._view == "identify_album_pick":
            self._identify_album_select()
            return
        if self._view in (
            "modify_results",
            "modify_actions",
            "modify_editions",
            "modify_metadata",
            "modify_covers",
        ):
            self._modify_select()
            return
        if self._view == "queue_next":
            self._start_import(self._import_queue[self._import_queue_idx])
            return
        if self._view == "batch_decision":
            self._handle_batch_decision()
            return
        if self._view == "import_done_pending":
            self._pre_review()
            return
        if self._view == "identify_done":
            self._identify_start_review()
            return
        if not self._selectable:
            return
        idx = self._selectable[self._cursor]
        item = self._items[idx]
        if item is None:
            return
        key, _ = item
        if key in ("fix", "duplicates", "modify", "identify", "import_all", "complete", "export"):
            from music_manager.core.logger import log_event  # noqa: PLC0415

            log_event("option_selected", option=key)
        if key == "back":
            self._switch_view(self._return_to)
        elif key in ("tools", "maintenance", "help"):
            self._return_to = self._view
            self._switch_view(key)
        elif key == "import_all":
            self._return_to = "main"
            self._start_import_all()
        elif key.startswith("csv:"):
            self._return_to = "main"
            self._start_import(key[4:])
        elif key == "fix":
            self._start_fix_metadata()
        elif key == "duplicates":
            self._start_duplicates()
        elif key == "complete":
            self._start_complete()
        elif key == "export":
            self._start_export()
        elif key == "modify":
            self._start_modify()
        elif key == "identify":
            self._start_identify()
        elif key in (
            "snapshot",
            "reset_failed",
            "clear_prefs",
            "revert",
            "delete_all",
            "move_data",
        ):
            from music_manager.core.logger import log_event  # noqa: PLC0415

            log_event("maintenance_action", op=key)
            self._run_maintenance(key)
        else:
            self._return_to = self._view
            self.app.action_open_option(key)  # type: ignore

    def action_toggle(self) -> None:  # type: ignore[override]
        """Space key — toggle checkbox."""
        if self._view == "exporting":
            if self._export_cursor < len(self._export_playlists):
                self._export_checks[self._export_cursor] = not self._export_checks[
                    self._export_cursor
                ]
                self._refresh_export_body()
        elif self._view == "completing":
            if self._complete_cursor < len(self._complete_albums):
                self._complete_checks[self._complete_cursor] = not self._complete_checks[
                    self._complete_cursor
                ]
                self._refresh_complete_body()
        elif self._view == "fixing":
            if self._fix_cursor < len(self._fix_unique_indices):
                self._fix_checks[self._fix_cursor] = not self._fix_checks[self._fix_cursor]
                self._refresh_fix_body()

    def action_skip(self) -> None:
        """S key."""
        if self._view == "search_input":
            return
        if self._view == "duplicates":
            self._dup_skip()
            return
        if self._view == "identify_album_pick":
            # Skip album → send tracks to individual review
            from music_manager.core.logger import log_event  # noqa: PLC0415

            group = self._identify_albums_to_review[self._identify_album_idx]
            log_event("identify_album_skip", album=group.get("album_name", ""))
            for aid in group.get("apple_ids", []):
                self._identify_singles_no_album.append(aid)
            self._identify_album_idx += 1
            self._identify_next_album()
            return
        if self._view == "reviewing":
            self._review_skip()

    def action_preview(self) -> None:
        """P key — preview audio (review), cover (fix-metadata), or edition preview."""
        if self._view == "duplicates":
            self._dup_preview()
            return
        if self._view == "fixing":
            self._preview_cover()
            return
        if self._view == "modify_editions":
            self._modify_preview_edition()
            return
        if self._view == "modify_covers":
            self._modify_preview_cover()
            return
        if self._view == "identify_album_pick":
            self._identify_preview_album()
            return
        if self._view != "reviewing":
            return
        if self._pending_idx >= len(self._pending):
            return
        pending = self._pending[self._pending_idx]

        # Get preview: from candidate (direct URL) or track (fetch fresh)
        if pending.reason == "ambiguous" and pending.candidates:
            opts = self._review_options
            key = opts[self._review_cursor] if self._review_cursor < len(opts) else ""
            if key.startswith("candidate:"):
                cidx = int(key.split(":")[1])
                url = pending.candidates[cidx].get("preview", "")
                if url:
                    self._play_preview(url)
        elif pending.track and pending.track.deezer_id:
            self._play_preview_fresh(pending.track.deezer_id)

    def _play_preview_fresh(self, deezer_id: int) -> None:
        """Fetch fresh preview URL from Deezer and play."""
        import threading  # noqa: PLC0415

        def _fetch_and_play() -> None:
            try:
                from music_manager.services.resolver import http_get  # noqa: PLC0415

                resp = http_get(
                    f"https://api.deezer.com/track/{deezer_id}",
                    timeout=5,
                )
                if resp.status_code == 200:
                    url = resp.json().get("preview", "")
                    if url:
                        self._play_preview(url)
            except Exception:  # noqa: BLE001
                pass

        threading.Thread(target=_fetch_and_play, daemon=True).start()

    def action_back(self) -> None:
        """Escape key."""
        # Stop preview if playing
        if self._preview_proc is not None:
            try:
                self._preview_proc.kill()
            except OSError:
                pass
            self._preview_proc = None

        if self._view == "main":
            self.app.exit()
        elif self._view == "importing":
            self._cancel_requested = True
            self._import_queue = []
            return
        elif self._view == "identifying":
            self._cancel_requested = True
            return
        elif self._view in ("identify_album_pick", "identify_done", "identify_summary"):
            self._switch_view("main")
            return
        elif self._view == "search_input":
            self._hide_search_input()
            self._view = "reviewing"
            self._render_review()
        elif self._view == "search_failed":
            self._render_review()
        elif self._view == "reviewing":
            if self._identify_apple_ids:
                # Identify mode: save accepted confirmations, then return
                if self._accepted:
                    self._execute_identify_confirmations()
                    return
                self._switch_view("main")
                return
            self._review_skip()
        elif self._view == "modify_unmatched":
            # Esc = keep (do not delete)
            self._modify_cursor = 0
            self._show_modify_actions()
        elif self._view == "modify_delete_confirm":
            # Esc = cancel delete
            self._modify_cursor = 0
            self._show_modify_actions()
        elif self._view in ("duplicates", "dup_removing"):
            self._switch_view("tools")
            return
        elif self._view == "maintenance_confirm":
            self._switch_view("maintenance")
            return
        elif self._view == "exporting":
            self._switch_view("tools")
            return
        elif self._view == "completing":
            self._switch_view("tools")
            return
        elif self._view == "completing_progress":
            self._cancel_requested = True
            return
        elif self._view == "modify_search":
            self._hide_modify_search()
            self._switch_view("tools")
        elif self._view == "modify_results":
            self._modify_cursor = 0
            self._show_modify_search()
        elif self._view == "modify_actions":
            self._modify_cursor = 0
            self._show_modify_search()
        elif self._view in ("modify_editions", "modify_covers"):
            self._modify_cursor = 0
            self._show_modify_actions()
        elif self._view == "modify_metadata":
            self._modify_cursor = 0
            self._show_modify_actions()
        elif self._view == "modify_meta_edit":
            self._hide_modify_input()
            if self._modify_editing_field == "__youtube_url__":
                self._modify_cursor = 0
                self._show_modify_actions()
            else:
                self._view = "modify_metadata"
                self._refresh_modify_metadata()
        elif self._view == "modify_working":
            return  # can't cancel during operation
        elif self._view == "modify_done":
            self._modify_cursor = 0
            self._show_modify_actions()
        else:
            self._import_queue = []
            self._switch_view(self._return_to)
