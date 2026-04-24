"""Modify mixin — search library, change edition, cover, metadata."""

from typing import TYPE_CHECKING

from textual import work
from textual.widgets import Input

from music_manager.ui.render import render_help, render_sub_header
from music_manager.ui.text import HELP_BACK, HELP_SEARCH_INPUT

if TYPE_CHECKING:
    from music_manager.ui.screens._protocol import MenuScreenProto

    _MixinBase = MenuScreenProto
else:
    _MixinBase = object


class ModifyMixin(_MixinBase):
    """Modify track/album methods for MenuScreen."""

    # ── Modify track ───────────────────────────────────────────────────────

    def _start_modify(self) -> None:
        """Enter modify track mode: show search input."""
        from music_manager.ui.text import MODIFY_TITLE  # noqa: PLC0415

        self._return_to = "tools"
        self._modify_cursor = 0
        self._modify_selected_track = None
        self._modify_selected_album = None
        self._set_header(render_sub_header(MODIFY_TITLE))
        self._show_modify_search()

    def _show_modify_search(self) -> None:
        """Show modify search with Input widget for live filtering."""
        from music_manager.ui.text import HELP_MODIFY_SEARCH, MODIFY_TITLE  # noqa: PLC0415

        self._view = "modify_search"
        self._modify_cursor = 0
        self._modify_track_items = []
        self._modify_album_items = []
        self._modify_selectable = []
        self._set_header(render_sub_header(MODIFY_TITLE))
        self._set_body(render_help("", with_newline=False))
        self._set_help(HELP_MODIFY_SEARCH, with_newline=False)

        search_w = self.query_one("#menu-search-input", Input)
        search_w.value = ""
        search_w.display = True
        search_w.disabled = False
        search_w.focus()

    def _hide_modify_search(self) -> None:
        """Hide modify search input."""
        try:
            search_w = self.query_one("#menu-search-input", Input)
            search_w.display = False
            search_w.disabled = True
            search_w.value = ""
        except Exception:  # noqa: BLE001
            pass

    def on_input_changed(self, event: Input.Changed) -> None:
        """Live filtering for modify search."""
        if self._view not in ("modify_search", "modify_results"):
            return
        if event.input.id != "menu-search-input":
            return
        if not self._tracks_store:
            return

        from music_manager.options.modify_track import search_library  # noqa: PLC0415
        from music_manager.ui.render import render_modify_search  # noqa: PLC0415

        query = event.value.strip()
        tracks, albums = search_library(query, self._tracks_store)

        # Build display items
        self._modify_track_items = [
            (f"{t.artist} — {t.title}", f"[{t.album}]" if t.album else "", t.apple_id)
            for t in tracks
        ]
        self._modify_album_items = [
            (f"{a.album_title} — {a.artist}", a.artist, a.track_count) for a in albums
        ]

        # Build selectable indices (skip separators)
        self._modify_selectable = []
        idx = 0
        if self._modify_track_items:
            idx += 1  # separator
            for _ in self._modify_track_items:
                self._modify_selectable.append(idx)
                idx += 1
        if self._modify_album_items:
            idx += 1  # separator
            for _ in self._modify_album_items:
                self._modify_selectable.append(idx)
                idx += 1

        self._modify_cursor = 0

        # Store full objects for selection
        self._modify_tracks_data = tracks
        self._modify_albums_data = albums

        self._set_body(
            render_modify_search(
                query,
                self._modify_track_items,
                self._modify_album_items,
                self._modify_cursor,
                self._modify_selectable,
            ),
        )

        # Switch to results view so arrow keys work
        if self._modify_selectable:
            self._view = "modify_results"
        else:
            self._view = "modify_search"

    def _refresh_modify_search(self) -> None:
        """Re-render modify search results with updated cursor."""
        from music_manager.ui.render import render_modify_search  # noqa: PLC0415

        search_w = self.query_one("#menu-search-input", Input)
        query = search_w.value.strip()
        self._set_body(
            render_modify_search(
                query,
                self._modify_track_items,
                self._modify_album_items,
                self._modify_cursor,
                self._modify_selectable,
            ),
        )

    def _modify_select(self) -> None:
        """Handle Enter in modify views."""
        if self._view == "modify_results":
            self._modify_select_result()
        elif self._view == "modify_actions":
            self._modify_select_action()
        elif self._view == "modify_editions":
            self._modify_select_edition()
        elif self._view == "modify_metadata":
            self._modify_select_metadata()
        elif self._view == "modify_covers":
            self._modify_select_cover()

    def _modify_select_result(self) -> None:
        """Handle selection of a search result."""
        if not self._modify_selectable or self._modify_cursor >= len(self._modify_selectable):
            return

        # Determine if track or album based on cursor position
        track_count = len(self._modify_track_items)

        if self._modify_cursor < track_count:
            # Track selected
            self._modify_selected_track = self._modify_tracks_data[self._modify_cursor]
            self._modify_selected_album = None
            self._hide_modify_search()
            self._show_modify_actions()
        else:
            # Album selected
            album_idx = self._modify_cursor - track_count
            if album_idx < len(self._modify_albums_data):
                self._modify_selected_album = self._modify_albums_data[album_idx]
                self._modify_selected_track = None
                self._hide_modify_search()
                self._show_modify_actions()

    def _show_modify_actions(self) -> None:
        """Show action menu for selected track or album."""
        from music_manager.ui.render import render_modify_actions  # noqa: PLC0415
        from music_manager.ui.text import (  # noqa: PLC0415
            HELP_MODIFY_ACTIONS,
            MODIFY_ALBUM_ACTIONS,
            MODIFY_TRACK_ACTIONS,
        )

        self._view = "modify_actions"
        self._modify_cursor = 0

        if self._modify_selected_track:
            trk = self._modify_selected_track
            self._set_header(render_sub_header(f"{trk.artist} — {trk.title}"))
            self._modify_actions_items = list(MODIFY_TRACK_ACTIONS)
        elif self._modify_selected_album:
            alb = self._modify_selected_album
            self._set_header(
                render_sub_header(f"{alb.album_title} — {alb.artist} ({alb.track_count} pistes)")
            )
            self._modify_actions_items = list(MODIFY_ALBUM_ACTIONS)
        else:
            return

        self._modify_actions_selectable = [
            i for i, item in enumerate(self._modify_actions_items) if item is not None
        ]
        self._set_body(
            render_modify_actions(
                self._modify_actions_items, self._modify_cursor, self._modify_actions_selectable
            )
        )
        self._set_help(HELP_MODIFY_ACTIONS)

    def _refresh_modify_actions(self) -> None:
        """Re-render action menu."""
        from music_manager.ui.render import render_modify_actions  # noqa: PLC0415

        self._set_body(
            render_modify_actions(
                self._modify_actions_items, self._modify_cursor, self._modify_actions_selectable
            )
        )

    def _modify_select_action(self) -> None:
        """Handle action selection."""
        if not self._modify_actions_selectable:
            return
        idx = self._modify_actions_selectable[self._modify_cursor]
        item = self._modify_actions_items[idx]
        if item is None:
            return
        key, _ = item

        if key == "back":
            self._modify_cursor = 0
            self._show_modify_search()
            return

        if key == "edition":
            self._modify_show_editions()
        elif key == "redownload":
            self._modify_redownload()
        elif key == "replace_url":
            self._modify_replace_url()
        elif key == "cover":
            self._modify_show_covers()
        elif key == "metadata":
            self._modify_show_metadata()
        elif key == "album_edition":
            self._modify_show_album_editions()
        elif key == "album_cover":
            self._modify_show_album_covers()
        elif key == "album_metadata":
            self._modify_show_album_metadata()
        elif key == "delete":
            self._modify_show_delete_confirm()
        elif key == "album_delete":
            self._modify_show_delete_confirm()

    # ── Modify: editions ───────────────────────────────────────────────────

    def _modify_show_editions(self) -> None:
        """Search Deezer for alternative editions and show picker."""
        trk = self._modify_selected_track
        if not trk:
            return

        self._view = "modify_working"
        self._set_body(render_help("\n  Recherche des éditions...", with_newline=False))
        self._set_help("")
        self._search_editions_worker(trk.title, trk.artist, trk.isrc)

    @work(thread=True)
    def _search_editions_worker(self, title: str, artist: str, current_isrc: str) -> None:
        """Search editions in background."""
        from music_manager.services.resolver import search_editions  # noqa: PLC0415

        try:
            editions = search_editions(title, artist)
        except Exception as _exc:  # noqa: BLE001
            from music_manager.core.logger import log_event as _le  # noqa: PLC0415

            _le("worker_error", error=str(_exc))
            editions = []
        self.app.call_from_thread(self._on_editions_loaded, editions, current_isrc)

    def _on_editions_loaded(self, editions: list[dict], current_isrc: str) -> None:
        """Editions loaded — show picker."""
        from music_manager.ui.render import render_modify_editions  # noqa: PLC0415
        from music_manager.ui.text import HELP_MODIFY_EDITIONS  # noqa: PLC0415

        # Filter out current edition
        other = [e for e in editions if e.get("isrc", "").upper() != (current_isrc or "").upper()]

        if not other:
            self._set_body(render_help("\n  Aucune autre édition disponible.", with_newline=False))
            self._set_help(HELP_BACK)
            self._view = "modify_editions"
            self._modify_editions = []
            return

        self._modify_editions = other
        self._modify_cursor = 0
        self._view = "modify_editions"
        self._set_body(render_modify_editions(other, 0))
        self._set_help(HELP_MODIFY_EDITIONS)

    def _refresh_modify_editions(self) -> None:
        """Re-render editions picker."""
        from music_manager.ui.render import render_modify_editions  # noqa: PLC0415

        trk = self._modify_selected_track
        current_isrc = trk.isrc if trk else ""
        best = self._identify_best_edition if self._view == "identify_album_pick" else -1
        self._set_body(
            render_modify_editions(
                self._modify_editions,
                self._modify_cursor,
                current_isrc,
                best_idx=best,
            ),
        )

    @work(thread=True)
    def _run_change_edition(self, old_apple_id: str, deezer_id: int) -> None:
        """Run change edition in background."""
        from music_manager.options.modify_track import change_edition  # noqa: PLC0415

        if not (self._paths and self._tracks_store and self._albums_store):
            self.app.call_from_thread(self._on_modify_done, False, "no_config")
            return

        def on_status(status: str) -> None:
            from music_manager.ui.text import MODIFY_STATUS  # noqa: PLC0415

            msg = MODIFY_STATUS.get(status, status)
            self.app.call_from_thread(
                self._set_body, render_help(f"\n  {msg}", with_newline=False)
            )

        try:
            result = change_edition(
                old_apple_id,
                deezer_id,
                self._paths,
                self._tracks_store,
                self._albums_store,
                on_status=on_status,
            )
            self.app.call_from_thread(self._on_modify_done, result.success, result.error)
        except Exception as _exc:  # noqa: BLE001
            from music_manager.core.logger import log_event as _le  # noqa: PLC0415

            _le("worker_error", error=str(_exc))
            self.app.call_from_thread(self._on_modify_done, False, "unexpected_error")

    def _modify_preview_edition(self) -> None:
        """Preview audio of selected edition (track only, not album)."""
        if not self._modify_selected_track:
            return  # no preview for album editions
        if self._modify_cursor >= len(self._modify_editions):
            return
        url = self._modify_editions[self._modify_cursor].get("preview", "")
        if url:
            self._play_preview(url)

    # ── Modify: redownload ─────────────────────────────────────────────────

    def _modify_redownload(self) -> None:
        """Redownload audio for selected track."""
        trk = self._modify_selected_track
        if not trk:
            return

        self._view = "modify_working"
        self._set_body(render_help("\n  Retéléchargement...", with_newline=False))
        self._set_help("")
        self._run_redownload(trk.apple_id)

    @work(thread=True)
    def _run_redownload(self, apple_id: str) -> None:
        """Run redownload in background."""
        from music_manager.options.modify_track import redownload_audio  # noqa: PLC0415

        if not (self._paths and self._tracks_store and self._albums_store):
            self.app.call_from_thread(self._on_modify_done, False, "no_config")
            return

        try:
            result = redownload_audio(
                apple_id,
                self._tracks_store,
                self._albums_store,
                self._paths,
            )
            self.app.call_from_thread(self._on_modify_done, result.success, result.error)
        except Exception as _exc:  # noqa: BLE001
            from music_manager.core.logger import log_event as _le  # noqa: PLC0415

            _le("worker_error", error=str(_exc))
            self.app.call_from_thread(self._on_modify_done, False, "unexpected_error")

    # ── Modify: replace URL ────────────────────────────────────────────────

    def _modify_replace_url(self) -> None:
        """Show input for YouTube URL."""
        from urllib.parse import quote_plus  # noqa: PLC0415

        from music_manager.services.apple import open_url_over_music  # noqa: PLC0415

        trk = self._modify_selected_track
        if not trk:
            return

        # Open YouTube search
        query = f"{trk.title} {trk.artist}"
        open_url_over_music(f"https://www.youtube.com/results?search_query={quote_plus(query)}")

        self._view = "modify_meta_edit"
        self._modify_editing_field = "__youtube_url__"
        self._set_body(render_help("\n  Collez l'URL YouTube :", with_newline=False))
        self._set_help(HELP_SEARCH_INPUT)

        input_widget = self.query_one("#menu-input", Input)
        input_widget.value = ""
        input_widget.placeholder = "URL YouTube..."
        input_widget.display = True
        input_widget.disabled = False
        input_widget.focus()

    def _modify_run_replace_url(self, youtube_url: str) -> None:
        """Run replace audio URL."""
        trk = self._modify_selected_track
        if not trk:
            return
        self._view = "modify_working"
        self._set_body(render_help("\n  Téléchargement et import...", with_newline=False))
        self._set_help("")
        self._run_replace_audio(trk.apple_id, youtube_url)

    @work(thread=True)
    def _run_replace_audio(self, apple_id: str, youtube_url: str) -> None:
        """Run replace audio in background."""
        from music_manager.options.modify_track import replace_audio_url  # noqa: PLC0415

        if not (self._paths and self._tracks_store and self._albums_store):
            self.app.call_from_thread(self._on_modify_done, False, "no_config")
            return
        try:
            result = replace_audio_url(
                apple_id,
                youtube_url,
                self._tracks_store,
                self._albums_store,
                self._paths,
            )
            self.app.call_from_thread(self._on_modify_done, result.success, result.error)
        except Exception as _exc:  # noqa: BLE001
            from music_manager.core.logger import log_event as _le  # noqa: PLC0415

            _le("worker_error", error=str(_exc))
            self.app.call_from_thread(self._on_modify_done, False, "unexpected_error")

    # ── Modify: covers ─────────────────────────────────────────────────────

    def _modify_show_covers(self) -> None:
        """Search iTunes for covers and show picker."""
        trk = self._modify_selected_track
        if not trk:
            return
        self._view = "modify_working"
        self._set_body(render_help("\n  Recherche des pochettes...", with_newline=False))
        self._set_help("")
        album = trk.album or trk.title
        self._search_covers_worker(album, trk.artist)

    def _modify_show_album_covers(self) -> None:
        """Search iTunes for album covers."""
        alb = self._modify_selected_album
        if not alb:
            return
        self._view = "modify_working"
        self._set_body(render_help("\n  Recherche des pochettes...", with_newline=False))
        self._set_help("")
        self._search_covers_worker(alb.album_title, alb.artist)

    @work(thread=True)
    def _search_covers_worker(self, album: str, artist: str) -> None:
        """Search covers in background."""
        from music_manager.options.modify_track import search_covers  # noqa: PLC0415

        try:
            covers = search_covers(album, artist)
        except Exception as _exc:  # noqa: BLE001
            from music_manager.core.logger import log_event as _le  # noqa: PLC0415

            _le("worker_error", error=str(_exc))
            covers = []
        self.app.call_from_thread(self._on_covers_loaded, covers)

    def _on_covers_loaded(self, covers: list[dict]) -> None:
        """Covers loaded — show picker."""
        from music_manager.ui.render import render_modify_covers  # noqa: PLC0415
        from music_manager.ui.text import HELP_MODIFY_COVERS  # noqa: PLC0415

        # If no iTunes results, try cached cover URL from albums_store
        if not covers and self._albums_store:
            album_id = None
            if self._modify_selected_track:
                entry = (
                    self._tracks_store.all().get(
                        self._modify_selected_track.apple_id,
                        {},
                    )
                    if self._tracks_store
                    else {}
                )
                album_id = entry.get("album_id")
            elif self._modify_selected_album and self._modify_selected_album.tracks:
                entry = (
                    self._tracks_store.all().get(
                        self._modify_selected_album.tracks[0].apple_id,
                        {},
                    )
                    if self._tracks_store
                    else {}
                )
                album_id = entry.get("album_id")

            if album_id:
                album_data = self._albums_store.get(album_id)
                if album_data and album_data.get("cover_url"):
                    covers = [
                        {
                            "url": album_data["cover_url"],
                            "thumbnail": album_data["cover_url"],
                            "year": album_data.get("year", ""),
                            "track_count": album_data.get("total_tracks", 0),
                            "artist": album_data.get("artist", ""),
                            "album": album_data.get("title", ""),
                        }
                    ]

        if not covers:
            self._set_body(render_help("\n  Aucune pochette disponible.", with_newline=False))
            self._set_help(HELP_BACK)
            self._view = "modify_covers"
            self._modify_covers = []
            return

        # Single cover → show it (user can preview + apply or skip)
        # No auto-apply — let user see and decide

        self._modify_covers = covers
        self._modify_cursor = 0
        self._view = "modify_covers"
        self._set_body(render_modify_covers(covers, 0))
        self._set_help(HELP_MODIFY_COVERS)

    def _refresh_modify_covers(self) -> None:
        """Re-render covers picker."""
        from music_manager.ui.render import render_modify_covers  # noqa: PLC0415

        self._set_body(render_modify_covers(self._modify_covers, self._modify_cursor))

    def _modify_select_cover(self) -> None:
        """Handle cover selection."""
        if self._modify_cursor >= len(self._modify_covers):
            # Back
            self._modify_cursor = 0
            self._show_modify_actions()
            return

        cover = self._modify_covers[self._modify_cursor]
        cover_url = cover.get("url", "")
        if not cover_url:
            return

        self._view = "modify_working"
        self._set_body(render_help("\n  Application de la pochette...", with_newline=False))
        self._set_help("")

        if self._modify_selected_track:
            self._run_change_cover_track(self._modify_selected_track.apple_id, cover_url)
        elif self._modify_selected_album:
            self._run_change_cover_album(self._modify_selected_album.tracks, cover_url)

    @work(thread=True)
    def _run_change_cover_track(self, apple_id: str, cover_url: str) -> None:
        """Apply cover to track in background."""
        from music_manager.options.modify_track import change_cover_track  # noqa: PLC0415

        if not (self._paths and self._tracks_store):
            self.app.call_from_thread(self._on_modify_done, False, "no_config")
            return
        try:
            result = change_cover_track(apple_id, cover_url, self._tracks_store, self._paths)
            self.app.call_from_thread(self._on_modify_done, result.success, result.error)
        except Exception as _exc:  # noqa: BLE001
            from music_manager.core.logger import log_event as _le  # noqa: PLC0415

            _le("worker_error", error=str(_exc))
            self.app.call_from_thread(self._on_modify_done, False, "unexpected_error")

    @work(thread=True)
    def _run_change_cover_album(self, tracks, cover_url: str) -> None:
        """Apply cover to all album tracks in background."""
        from music_manager.options.modify_track import change_cover_album  # noqa: PLC0415

        if not self._paths:
            self.app.call_from_thread(self._on_modify_done, False, "no_config")
            return
        try:
            result = change_cover_album(tracks, cover_url, self._paths)
            self.app.call_from_thread(self._on_modify_done, result.success, result.error)
        except Exception as _exc:  # noqa: BLE001
            from music_manager.core.logger import log_event as _le  # noqa: PLC0415

            _le("worker_error", error=str(_exc))
            self.app.call_from_thread(self._on_modify_done, False, "unexpected_error")

    def _modify_preview_cover(self) -> None:
        """Open cover in browser for preview."""
        from music_manager.services.apple import open_url_over_music  # noqa: PLC0415

        if self._modify_cursor >= len(self._modify_covers):
            return
        cover = self._modify_covers[self._modify_cursor]
        url = cover.get("url", "") or cover.get("thumbnail", "")
        if url:
            open_url_over_music(url)

    # ── Modify: metadata ───────────────────────────────────────────────────

    def _modify_show_metadata(self) -> None:
        """Show metadata editor for selected track."""
        from music_manager.ui.text import (  # noqa: PLC0415
            HELP_MODIFY_METADATA,
            MODIFY_METADATA_FIELDS,
        )

        trk = self._modify_selected_track
        if not trk or not self._tracks_store:
            return

        entry = self._tracks_store.all().get(trk.apple_id, {})
        self._modify_meta_fields = []
        for key, label in MODIFY_METADATA_FIELDS:
            if key == "year":
                val = entry.get("year", "") or (entry.get("release_date") or "")[:4]
            else:
                val = entry.get(key, "")
            self._modify_meta_fields.append((label, key, str(val)))
        self._modify_meta_changes = {}
        self._modify_cursor = 0
        self._view = "modify_metadata"
        self._refresh_modify_metadata()
        self._set_help(HELP_MODIFY_METADATA)

    def _modify_show_album_metadata(self) -> None:
        """Show metadata editor for album fields."""
        from music_manager.ui.text import HELP_MODIFY_METADATA  # noqa: PLC0415

        alb = self._modify_selected_album
        if not alb or not alb.tracks or not self._tracks_store:
            return

        # Use first track as reference, fallback to album data
        entry = self._tracks_store.all().get(alb.tracks[0].apple_id, {})
        album_data: dict = {}
        album_id = entry.get("album_id")
        if album_id and self._albums_store:
            album_data = self._albums_store.get(album_id) or {}
        genre = entry.get("genre", "") or album_data.get("genre", "")
        year = entry.get("year", "") or (entry.get("release_date") or "")[:4]
        if not year:
            year = album_data.get("year", "")
        album_fields = [
            ("Artiste album", "album_artist", str(entry.get("album_artist", ""))),
            ("Genre", "genre", str(genre)),
            ("Année", "year", str(year)),
        ]
        self._modify_meta_fields = album_fields
        self._modify_meta_changes = {}
        self._modify_cursor = 0
        self._view = "modify_metadata"
        self._refresh_modify_metadata()
        self._set_help(HELP_MODIFY_METADATA)

    def _refresh_modify_metadata(self) -> None:
        """Re-render metadata editor."""
        from music_manager.ui.render import render_modify_metadata  # noqa: PLC0415

        # Update values with pending changes
        fields = [
            (label, key, str(self._modify_meta_changes.get(key, value)))
            for label, key, value in self._modify_meta_fields
        ]
        self._set_body(render_modify_metadata(fields, self._modify_cursor))

    def _modify_select_metadata(self) -> None:
        """Handle Enter in metadata editor."""
        num_fields = len(self._modify_meta_fields)
        if self._modify_cursor == num_fields:
            # Apply button
            self._apply_modify_metadata()
            return
        if self._modify_cursor == num_fields + 1:
            # Back button
            self._modify_cursor = 0
            self._show_modify_actions()
            return

        # Edit selected field
        label, key, value = self._modify_meta_fields[self._modify_cursor]
        current = self._modify_meta_changes.get(key, value)
        self._modify_editing_field = key
        self._view = "modify_meta_edit"

        from music_manager.ui.text import HELP_SEARCH_INPUT  # noqa: PLC0415

        self._set_help(HELP_SEARCH_INPUT)
        input_widget = self.query_one("#menu-input", Input)
        input_widget.value = current
        input_widget.placeholder = label
        input_widget.display = True
        input_widget.disabled = False
        input_widget.focus()

    def _hide_modify_input(self) -> None:
        """Hide modify input field."""
        try:
            input_widget = self.query_one("#menu-input", Input)
            input_widget.display = False
            input_widget.disabled = True
            input_widget.value = ""
        except Exception:  # noqa: BLE001
            pass

    def _apply_modify_metadata(self) -> None:
        """Apply metadata changes."""
        if not self._modify_meta_changes:
            self._modify_cursor = 0
            self._show_modify_actions()
            return

        self._view = "modify_working"
        self._set_body(render_help("\n  Application des modifications...", with_newline=False))
        self._set_help("")

        if self._modify_selected_track:
            self._run_edit_metadata_track(
                self._modify_selected_track.apple_id, dict(self._modify_meta_changes)
            )
        elif self._modify_selected_album:
            self._run_edit_metadata_album(
                self._modify_selected_album.tracks, dict(self._modify_meta_changes)
            )

    @work(thread=True)
    def _run_edit_metadata_track(self, apple_id: str, fields: dict) -> None:
        """Apply metadata in background."""
        from music_manager.options.modify_track import edit_metadata_track  # noqa: PLC0415

        if not self._tracks_store:
            self.app.call_from_thread(self._on_modify_done, False, "no_config")
            return
        try:
            result = edit_metadata_track(apple_id, fields, self._tracks_store)
            self.app.call_from_thread(self._on_modify_done, result.success, result.error)
        except Exception as _exc:  # noqa: BLE001
            from music_manager.core.logger import log_event as _le  # noqa: PLC0415

            _le("worker_error", error=str(_exc))
            self.app.call_from_thread(self._on_modify_done, False, "unexpected_error")

    @work(thread=True)
    def _run_edit_metadata_album(self, tracks, fields: dict) -> None:
        """Apply metadata to all album tracks in background."""
        from music_manager.options.modify_track import edit_metadata_album  # noqa: PLC0415

        if not self._tracks_store:
            self.app.call_from_thread(self._on_modify_done, False, "no_config")
            return
        try:
            result = edit_metadata_album(tracks, fields, self._tracks_store)
            self.app.call_from_thread(self._on_modify_done, result.success, result.error)
        except Exception as _exc:  # noqa: BLE001
            from music_manager.core.logger import log_event as _le  # noqa: PLC0415

            _le("worker_error", error=str(_exc))
            self.app.call_from_thread(self._on_modify_done, False, "unexpected_error")

    # ── Modify: album edition ───────────────────────────────────────��──────

    def _modify_show_album_editions(self) -> None:
        """Search Deezer for alternative album editions."""
        alb = self._modify_selected_album
        if not alb:
            return
        self._view = "modify_working"
        self._set_body(render_help("\n  Recherche des éditions...", with_newline=False))
        self._set_help("")
        self._search_album_editions_worker(alb.album_title, alb.artist)

    @work(thread=True)
    def _search_album_editions_worker(self, album: str, artist: str) -> None:
        """Search album editions in background."""
        from music_manager.services.resolver import search_album_editions  # noqa: PLC0415

        try:
            editions = search_album_editions(album, artist, self._albums_store)
        except Exception as _exc:  # noqa: BLE001
            from music_manager.core.logger import log_event as _le  # noqa: PLC0415

            _le("worker_error", error=str(_exc))
            editions = []
        self.app.call_from_thread(self._on_album_editions_loaded, editions)

    def _on_album_editions_loaded(self, editions: list[dict]) -> None:
        """Album editions loaded — show picker."""
        from music_manager.ui.render import render_modify_editions  # noqa: PLC0415
        from music_manager.ui.text import HELP_MODIFY_ACTIONS  # noqa: PLC0415

        if not editions:
            self._set_body(render_help("\n  Aucune édition trouvée.", with_newline=False))
            self._set_help(HELP_BACK)
            self._view = "modify_editions"
            self._modify_editions = []
            return

        self._modify_editions = [
            {
                "album": ed.get("title", ""),
                "nb_tracks": ed.get("nb_tracks", 0),
                "album_id": ed.get("album_id", 0),
                "year": ed.get("year", ""),
            }
            for ed in editions
        ]
        self._modify_cursor = 0
        self._view = "modify_editions"
        self._set_body(render_modify_editions(self._modify_editions, 0))
        self._set_help(HELP_MODIFY_ACTIONS)

    def _modify_select_edition(self) -> None:
        """Handle edition selection — track or album."""
        if self._modify_cursor >= len(self._modify_editions):
            # Back
            self._modify_cursor = 0
            self._show_modify_actions()
            return

        edition = self._modify_editions[self._modify_cursor]

        if self._modify_selected_track:
            # Track edition change
            trk = self._modify_selected_track
            if edition.get("isrc", "").upper() == (trk.isrc or "").upper():
                return  # same edition
            self._view = "modify_working"
            self._set_body(render_help("\n  Import en cours...", with_newline=False))
            self._set_help("")
            self._run_change_edition(trk.apple_id, edition["deezer_id"])

        elif self._modify_selected_album:
            # Album edition change
            album_id = edition.get("album_id", 0)
            if not album_id:
                return
            self._view = "modify_working"
            self._set_body(render_help("\n  Import en cours...", with_newline=False))
            self._set_help("")
            self._run_change_album_edition(self._modify_selected_album.tracks, album_id)

    @work(thread=True)
    def _run_change_album_edition(self, tracks, album_id: int) -> None:
        """Run album edition change in background."""
        from music_manager.options.modify_track import change_album_edition  # noqa: PLC0415

        if not (self._paths and self._tracks_store and self._albums_store):
            self.app.call_from_thread(self._on_modify_done, False, "no_config")
            return

        def on_progress(current: int, total: int, title: str) -> None:
            msg = f"Import {current}/{total}" + (f"  {title}" if title else "")
            self.app.call_from_thread(
                self._set_body, render_help(f"\n  {msg}", with_newline=False)
            )

        try:
            result = change_album_edition(
                tracks,
                album_id,
                self._paths,
                self._tracks_store,
                self._albums_store,
                on_progress=on_progress,
            )
            if result.unmatched:
                self.app.call_from_thread(
                    self._on_modify_done_with_unmatched,
                    result.success,
                    result.error,
                    result.unmatched,
                )
            else:
                self.app.call_from_thread(
                    self._on_modify_done,
                    result.success,
                    result.error,
                )
        except Exception as _exc:  # noqa: BLE001
            from music_manager.core.logger import log_event as _le  # noqa: PLC0415

            _le("worker_error", error=str(_exc))
            self.app.call_from_thread(self._on_modify_done, False, "unexpected_error")

    # ── Modify: common result ──────────────────────────────────────────────

    def _handle_unmatched_decision(self) -> None:
        """Handle Supprimer/Conserver for unmatched tracks."""
        if self._modify_cursor == 0:
            # Delete track
            from music_manager.services.apple import delete_tracks  # noqa: PLC0415

            apple_ids = [t.apple_id for t in self._modify_unmatched]
            if apple_ids:
                delete_tracks(apple_ids)
                if self._tracks_store:
                    for aid in apple_ids:
                        self._tracks_store.remove(aid)
            self._modify_unmatched = []
            self._on_modify_done(True)
        else:
            # Conserver
            self._modify_unmatched = []
            self._on_modify_done(True)

    # ── Modify: delete track / album ────────────────────────────────────

    def _modify_show_delete_confirm(self) -> None:
        """Show delete confirmation screen."""
        from rich.text import Text as RichText  # noqa: PLC0415

        from music_manager.ui.render import render_modify_actions  # noqa: PLC0415
        from music_manager.ui.styles import WARN as _WARN  # noqa: PLC0415
        from music_manager.ui.text import HELP_MODIFY_ACTIONS  # noqa: PLC0415

        body = RichText()
        if self._modify_selected_track:
            trk = self._modify_selected_track
            body.append(
                f"\n  {_WARN}  Supprimer cette piste ?\n\n", style="yellow"
            )
            body.append(f"     {trk.title} — {trk.artist}\n\n", style="dim")
        elif self._modify_selected_album:
            alb = self._modify_selected_album
            body.append(
                f"\n  {_WARN}  Supprimer cet album ({alb.track_count} piste(s)) ?\n\n",
                style="yellow",
            )
            body.append(f"     {alb.album_title} — {alb.artist}\n\n", style="dim")
        else:
            return

        self._modify_cursor = 0
        self._modify_actions_items = [
            ("confirm_delete", "Supprimer"),
            ("cancel_delete", "Annuler"),
        ]
        self._modify_actions_selectable = [0, 1]
        self._view = "modify_delete_confirm"

        actions_body = render_modify_actions(
            self._modify_actions_items,
            self._modify_cursor,
            self._modify_actions_selectable,
        )
        body.append_text(actions_body)
        self._set_body(body)
        self._set_help(HELP_MODIFY_ACTIONS)

    def _handle_delete_decision(self) -> None:
        """Handle Supprimer/Annuler for delete confirmation."""
        if self._modify_cursor == 0:
            # Delete
            from music_manager.services.apple import delete_tracks  # noqa: PLC0415

            apple_ids: list[str] = []
            if self._modify_selected_track:
                apple_ids = [self._modify_selected_track.apple_id]
            elif self._modify_selected_album:
                apple_ids = [t.apple_id for t in self._modify_selected_album.tracks]

            if apple_ids:
                delete_tracks(apple_ids)
                if self._tracks_store:
                    for aid in apple_ids:
                        self._tracks_store.remove(aid)
                    self._tracks_store.save()
            self._on_modify_done(True)
        else:
            # Cancel — back to actions
            self._modify_cursor = 0
            self._show_modify_actions()

    def _on_modify_done(self, success: bool, error: str = "") -> None:
        """Modify operation completed — show result."""
        from music_manager.core.logger import log_event  # noqa: PLC0415

        log_event("modify_result", success=success, error=error)
        from music_manager.ui.render import render_modify_result  # noqa: PLC0415

        self._set_body(render_modify_result(success, error))
        self._set_help(HELP_BACK)
        self._view = "modify_done"

    def _on_modify_done_with_unmatched(
        self,
        success: bool,
        error: str,
        unmatched: list,
    ) -> None:
        """Album edition change done with unmatched tracks to review."""
        from rich.text import Text as RichText  # noqa: PLC0415

        from music_manager.ui.styles import CHECK as _CHECK  # noqa: PLC0415
        from music_manager.ui.styles import WARN as _WARN  # noqa: PLC0415

        body = RichText()
        if success:
            body.append(f"\n  {_CHECK}  Modification effectuée\n\n", style="green")
        if unmatched:
            body.append(
                f"  {_WARN}  {len(unmatched)} piste(s) absente(s) de la nouvelle édition :\n",
                style="yellow",
            )
            for trk in unmatched:
                body.append(f"     {trk.title} — {trk.artist}\n", style="dim")
            body.append(
                "\n  Supprimer ces pistes d'Apple Music ?\n",
                style="dim",
            )

        self._set_body(body)
        self._modify_unmatched = unmatched
        self._modify_cursor = 0

        if unmatched:
            from music_manager.ui.render import render_modify_actions  # noqa: PLC0415
            from music_manager.ui.text import HELP_MODIFY_ACTIONS  # noqa: PLC0415

            # Build simple Yes/No menu
            self._modify_actions_items = [
                ("unmatched_delete", "Supprimer"),
                ("unmatched_keep", "Conserver"),
            ]
            self._modify_actions_selectable = [0, 1]
            self._view = "modify_unmatched"

            actions_body = render_modify_actions(
                self._modify_actions_items,
                self._modify_cursor,
                self._modify_actions_selectable,
            )
            body.append_text(actions_body)
            self._set_body(body)
            self._set_help(HELP_MODIFY_ACTIONS)
        else:
            self._set_help(HELP_BACK)
            self._view = "modify_done"
