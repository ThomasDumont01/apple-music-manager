"""Identify mixin — link Apple Music tracks to Deezer."""

from typing import TYPE_CHECKING

from textual import work

from music_manager.core.models import PendingTrack, Track
from music_manager.ui.render import render_help, render_sub_header
from music_manager.ui.text import HELP_BACK

if TYPE_CHECKING:
    from music_manager.ui.screens._protocol import MenuScreenProto

    _MixinBase = MenuScreenProto
else:
    _MixinBase = object


class IdentifyMixin(_MixinBase):
    """Identify library methods for MenuScreen."""

    # ── Identify ───────────────────────────────────────────────────────────

    def _start_identify(self) -> None:
        """Launch library identification (album-based review)."""
        from music_manager.ui.text import (  # noqa: PLC0415
            IDENTIFY_SCANNING,
            IDENTIFY_TITLE,
        )

        self._return_to = "main"
        self._cancel_requested = False
        self._view = "identifying"
        self._set_header(render_sub_header(IDENTIFY_TITLE))
        self._set_body(render_help(f"\n\n  {IDENTIFY_SCANNING}", with_newline=False))
        self._set_help("")
        self._run_identify()

    @work(thread=True)
    def _run_identify(self) -> None:
        """Run identification in background thread."""
        from music_manager.options.identify import identify_library  # noqa: PLC0415

        if not (self._tracks_store and self._albums_store):
            self.app.call_from_thread(self._on_identify_done, None)
            return

        try:

            def on_progress(current: int, total: int) -> None:
                if self._cancel_requested:
                    return
                self.app.call_from_thread(
                    self._identify_render_progress,
                    current,
                    total,
                    "Scan bibliothèque...",
                )

            prefs_path = self._paths.preferences_path if self._paths else ""
            result = identify_library(
                self._tracks_store,
                self._albums_store,
                on_progress=on_progress,
                preferences_path=prefs_path,
            )
            # Save after Phase 2 (may have cached album data)
            self._save_all()
            self.app.call_from_thread(self._on_identify_done, result)
        except Exception as _exc:  # noqa: BLE001
            from music_manager.core.logger import log_worker_error  # noqa: PLC0415

            log_worker_error(_exc)
            self.app.call_from_thread(self._on_identify_done, None)

    def _identify_render_progress(
        self,
        current: int,
        total: int,
        label: str = "Recherche sur Deezer...",
    ) -> None:
        """Update progress bar during identification."""
        from rich.text import Text as RichText  # noqa: PLC0415

        from music_manager.ui.styles import BLUE as _BLUE  # noqa: PLC0415

        pct = current / total if total else 0
        bar_width = 30
        filled = int(bar_width * pct)
        bar = "█" * filled + "░" * (bar_width - filled)

        body = RichText()
        body.append(f"\n  {label}  ", style="dim")
        body.append(f"[{bar}]", style=f"bold {_BLUE}")
        body.append(f"  {current}/{total}", style="dim")
        self._set_body(body)

    def _on_identify_done(self, result) -> None:
        """Identification finished — show summary + start album review."""
        from music_manager.ui.text import HELP_BACK  # noqa: PLC0415

        if result is None:
            self._set_body(
                render_help(
                    "\n  Erreur lors de l'identification.",
                    with_newline=False,
                )
            )
            self._set_help(HELP_BACK)
            self._view = "identify_summary"
            return

        self._identify_result = result
        self._identify_albums_to_review = result.albums_to_review
        self._identify_album_idx = 0

        # Build summary
        from rich.text import Text as RichText  # noqa: PLC0415

        from music_manager.ui.styles import CHECK as _CHECK  # noqa: PLC0415

        body = RichText()
        body.append("\n")
        parts = []
        if result.auto_validated:
            parts.append(f"{result.auto_validated} identifiée(s)")
        if result.albums_to_review:
            album_tracks = sum(len(a.get("apple_ids", [])) for a in result.albums_to_review)
            parts.append(f"{album_tracks} en attente ({len(result.albums_to_review)} albums)")

        if parts:
            body.append(f"  {_CHECK}  ", style="green")
            body.append(" · ".join(parts))
        else:
            body.append(f"  {_CHECK}  Bibliothèque à jour", style="green")

        body.append("\n")
        self._set_body(body)

        if result.albums_to_review:
            from music_manager.ui.text import HELP_IDENTIFY_DONE  # noqa: PLC0415

            self._set_help(HELP_IDENTIFY_DONE)
            self._view = "identify_done"
        else:
            self._set_help(HELP_BACK)
            self._view = "identify_summary"

    def _identify_start_review(self) -> None:
        """Batch auto-confirm single-edition albums, then picker for rest."""
        if not self._identify_albums_to_review:
            self._identify_show_summary()
            return

        from music_manager.ui.text import IDENTIFY_TITLE  # noqa: PLC0415

        self._set_header(render_sub_header(IDENTIFY_TITLE))
        self._view = "modify_working"
        self._set_body(
            render_help(
                "\n  Recherche des albums sur Deezer...",
                with_newline=False,
            )
        )
        self._set_help("")
        self._identify_batch_resolve()

    @work(thread=True)
    def _identify_batch_resolve(self) -> None:
        """Batch: search all albums, auto-confirm singles, collect ambiguous."""
        from concurrent.futures import ThreadPoolExecutor, as_completed  # noqa: PLC0415

        from music_manager.core.normalize import first_artist, normalize  # noqa: PLC0415
        from music_manager.options.identify import confirm_album  # noqa: PLC0415
        from music_manager.services.resolver import search_album_editions  # noqa: PLC0415

        try:
            # Build cache index: norm(title)||norm(first_artist) → editions
            cache_index: dict[str, list[dict]] = {}
            if self._albums_store:
                for album_id_str, album_data in self._albums_store.all().items():
                    title = album_data.get("title", "")
                    artist_name = album_data.get("artist", "")
                    if not title:
                        continue
                    key = f"{normalize(title)}||{normalize(first_artist(artist_name))}"
                    cache_index.setdefault(key, []).append(
                        {
                            "album_id": int(album_id_str),
                            "title": title,
                            "artist": artist_name,
                            "nb_tracks": album_data.get("total_tracks", 0),
                            "year": album_data.get("year", ""),
                        }
                    )

            # Split groups into cached (instant) and uncached (need API)
            cached_results: list[tuple[int, dict, list[dict]]] = []
            to_search: list[tuple[int, dict]] = []

            for idx, group in enumerate(self._identify_albums_to_review):
                album_name = group.get("album_name", "")
                artist = group.get("artist", "")
                cache_key = f"{normalize(album_name)}||{normalize(first_artist(artist))}"
                editions = cache_index.get(cache_key, [])
                if editions:
                    cached_results.append((idx, group, editions))
                else:
                    to_search.append((idx, group))

            needs_picker: list[tuple[dict, list[dict]]] = []
            no_match_ids: list[str] = []
            total = len(self._identify_albums_to_review)
            done = 0

            # Process cached results first (instant)
            for idx, group, editions in cached_results:
                done += 1
                self.app.call_from_thread(
                    self._identify_render_progress,
                    done,
                    total,
                )
                apple_ids = group.get("apple_ids", [])
                if len(editions) == 1:
                    album_id = editions[0].get("album_id", 0)
                    if album_id and self._tracks_store and self._albums_store:
                        _, unmatched = confirm_album(
                            album_id,
                            apple_ids,
                            self._tracks_store,
                            self._albums_store,
                        )
                        no_match_ids.extend(unmatched)
                else:
                    needs_picker.append((group, editions))

            # Search uncached albums in parallel (4 workers, safe for Deezer rate limit)
            if to_search:

                def _search(item: tuple[int, dict]) -> tuple[int, dict, list[dict]]:
                    idx, group = item
                    return (
                        idx,
                        group,
                        search_album_editions(
                            group.get("album_name", ""),
                            group.get("artist", ""),
                            self._albums_store,
                        ),
                    )

                with ThreadPoolExecutor(max_workers=4) as pool:
                    futures = {pool.submit(_search, item): item for item in to_search}
                    for future in as_completed(futures):
                        done += 1
                        self.app.call_from_thread(
                            self._identify_render_progress,
                            done,
                            total,
                        )
                        idx, group, editions = future.result()
                        apple_ids = group.get("apple_ids", [])

                        if not editions:
                            no_match_ids.extend(apple_ids)
                        elif len(editions) == 1:
                            album_id = editions[0].get("album_id", 0)
                            if album_id and self._tracks_store and self._albums_store:
                                _, unmatched = confirm_album(
                                    album_id,
                                    apple_ids,
                                    self._tracks_store,
                                    self._albums_store,
                                )
                                no_match_ids.extend(unmatched)
                        else:
                            needs_picker.append((group, editions))

                # Save after all API calls (crash safety)
                self._save_all()

            self.app.call_from_thread(
                self._on_batch_resolved,
                needs_picker,
                no_match_ids,
            )
        except Exception as _exc:  # noqa: BLE001
            from music_manager.core.logger import log_worker_error  # noqa: PLC0415

            log_worker_error(_exc)
            self.app.call_from_thread(self._identify_show_summary)

    def _on_batch_resolved(
        self,
        needs_picker: list[tuple[dict, list[dict]]],
        no_match_ids: list[str],
    ) -> None:
        """Batch done — set up picker for ambiguous albums."""
        from music_manager.core.logger import log_event  # noqa: PLC0415

        log_event(
            "identify_batch_complete",
            needs_picker=len(needs_picker),
            no_match=len(no_match_ids),
        )
        self._identify_singles_no_album = no_match_ids
        self._identify_albums_to_review = [
            {**group, "_editions": editions} for group, editions in needs_picker
        ]
        self._identify_album_idx = 0

        if self._identify_albums_to_review:
            self._identify_next_album()
        elif no_match_ids:
            self._identify_individual_review(no_match_ids)
        else:
            self._identify_show_summary()

    def _identify_next_album(self) -> None:
        """Show picker for next ambiguous album (editions pre-fetched)."""
        if self._identify_album_idx >= len(self._identify_albums_to_review):
            unresolved = list(self._identify_singles_no_album)
            self._identify_singles_no_album = []
            if unresolved:
                self._identify_individual_review(unresolved)
            else:
                self._identify_show_summary()
            return

        group = self._identify_albums_to_review[self._identify_album_idx]
        editions = group.get("_editions", [])

        if editions:
            # Editions already fetched by batch → show picker directly
            self._on_identify_album_found(editions)
        else:
            # Fallback: search (shouldn't happen after batch)
            album_name = group.get("album_name", "")
            artist = group.get("artist", "")
            self._view = "modify_working"
            self._set_body(
                render_help(
                    f"\n  Recherche : {album_name}...",
                    with_newline=False,
                )
            )
            self._set_help("")
            self._identify_search_album(album_name, artist)

    @work(thread=True)
    def _identify_search_album(self, album: str, artist: str) -> None:
        """Search Deezer for album in background."""
        from music_manager.services.resolver import search_album_editions  # noqa: PLC0415

        try:
            editions = search_album_editions(
                album,
                artist,
                self._albums_store,
            )
        except Exception as _exc:  # noqa: BLE001
            from music_manager.core.logger import log_worker_error  # noqa: PLC0415

            log_worker_error(_exc)
            editions = []
        self.app.call_from_thread(self._on_identify_album_found, editions)

    def _on_identify_album_found(self, editions: list[dict]) -> None:
        """Album editions found — show picker."""
        from music_manager.ui.render import render_modify_editions  # noqa: PLC0415

        group = self._identify_albums_to_review[self._identify_album_idx]
        album_name = group.get("album_name", "")
        count = len(group.get("apple_ids", []))

        if not editions:
            # No album found → send tracks to individual
            for aid in group.get("apple_ids", []):
                self._identify_singles_no_album.append(aid)
            self._identify_album_idx += 1
            self._identify_next_album()
            return

        # Single edition → auto-confirm
        if len(editions) == 1:
            album_id = editions[0].get("album_id", 0)
            if album_id:
                apple_ids = group.get("apple_ids", [])
                self._view = "modify_working"
                self._set_body(
                    render_help(
                        "\n  Correspondance des pistes...",
                        with_newline=False,
                    )
                )
                self._identify_confirm_album(album_id, apple_ids)
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
        # Find recommended edition (match Apple Music year/track count)
        best_idx = 0
        apple_year = ""
        apple_tracks = 0
        if group.get("apple_ids") and self._tracks_store:
            first_entry = self._tracks_store.all().get(group["apple_ids"][0], {})
            apple_year = str(first_entry.get("year", ""))
            apple_tracks = len(group["apple_ids"])
        for i, ed in enumerate(self._modify_editions):
            if ed.get("year") == apple_year and ed.get("nb_tracks", 0) >= apple_tracks:
                best_idx = i
                break

        self._modify_cursor = best_idx
        self._identify_best_edition = best_idx
        self._view = "identify_album_pick"

        artist = group.get("artist", "")
        idx = self._identify_album_idx + 1
        total_albums = len(self._identify_albums_to_review)
        from rich.text import Text as RichText  # noqa: PLC0415

        from music_manager.ui.styles import BLUE as _BLUE  # noqa: PLC0415

        header_title = f"{album_name} — {artist}" if artist else album_name
        header = RichText()
        header.append(header_title, style=f"bold {_BLUE}")
        header.append(f"  ({count} piste{'s' if count != 1 else ''})", style="dim")
        if total_albums > 1:
            header.append(f"  ({idx}/{total_albums})", style="dim")
        self._set_header(header)
        self._set_body(
            render_modify_editions(
                self._modify_editions,
                best_idx,
                best_idx=best_idx,
            )
        )
        self._set_help("↑↓  naviguer    ⏎  sélectionner    p  voir    s  passer    esc  retour")

    def _identify_preview_album(self) -> None:
        """Open selected album on Deezer in browser."""
        from music_manager.services.apple import open_url_over_music  # noqa: PLC0415

        if self._modify_cursor >= len(self._modify_editions):
            return
        album_id = self._modify_editions[self._modify_cursor].get("album_id", 0)
        if album_id:
            open_url_over_music(f"https://www.deezer.com/album/{album_id}")

    def _identify_album_select(self) -> None:
        """User selected a Deezer album — match all tracks."""
        if self._modify_cursor >= len(self._modify_editions):
            # Back/skip → send to individual
            group = self._identify_albums_to_review[self._identify_album_idx]
            for aid in group.get("apple_ids", []):
                self._identify_singles_no_album.append(aid)
            self._identify_album_idx += 1
            self._identify_next_album()
            return

        edition = self._modify_editions[self._modify_cursor]
        album_id = edition.get("album_id", 0)
        if not album_id:
            return

        from music_manager.core.logger import log_event  # noqa: PLC0415

        group = self._identify_albums_to_review[self._identify_album_idx]
        log_event(
            "identify_album_pick",
            album=group.get("album_name", ""),
            artist=group.get("artist", ""),
            edition=edition.get("album", ""),
            album_id=album_id,
            year=edition.get("year", ""),
        )
        apple_ids = group.get("apple_ids", [])

        self._view = "modify_working"
        self._set_body(
            render_help(
                "\n  Correspondance des pistes...",
                with_newline=False,
            )
        )
        self._identify_confirm_album(album_id, apple_ids)

    @work(thread=True)
    def _identify_confirm_album(
        self,
        album_id: int,
        apple_ids: list[str],
    ) -> None:
        """Confirm album identification in background."""
        from music_manager.options.identify import confirm_album  # noqa: PLC0415

        try:
            matched = 0
            unmatched_ids: list[str] = []
            if self._tracks_store and self._albums_store:
                matched, unmatched_ids = confirm_album(
                    album_id,
                    apple_ids,
                    self._tracks_store,
                    self._albums_store,
                )
                # Save after confirm (crash safety)
                self._save_all()
            self.app.call_from_thread(
                self._on_identify_album_confirmed,
                matched,
                len(apple_ids),
                unmatched_ids,
            )
        except Exception as _exc:  # noqa: BLE001
            from music_manager.core.logger import log_worker_error  # noqa: PLC0415

            log_worker_error(_exc)
            self.app.call_from_thread(
                self._on_identify_album_confirmed,
                0,
                len(apple_ids),
                list(apple_ids),
            )

    def _on_identify_album_confirmed(
        self,
        matched: int,
        total: int,
        unmatched_ids: list[str],
    ) -> None:
        """Album confirmed — unmatched go to individual review."""
        self._identify_singles_no_album.extend(unmatched_ids)
        self._identify_album_idx += 1
        self._identify_next_album()

    def _identify_individual_review(self, apple_ids: list[str]) -> None:
        """Resolve remaining tracks individually, then review."""
        if not apple_ids or not self._tracks_store:
            self._identify_show_summary()
            return

        from music_manager.ui.text import IDENTIFY_TITLE  # noqa: PLC0415

        self._set_header(render_sub_header(IDENTIFY_TITLE))
        self._view = "modify_working"
        self._set_body(
            render_help(
                f"\n  Recherche Deezer... ({len(apple_ids)} pistes)",
                with_newline=False,
            )
        )
        self._set_help("")
        self._resolve_individual_worker(apple_ids)

    @work(thread=True)
    def _resolve_individual_worker(self, apple_ids: list[str]) -> None:
        """Resolve tracks individually in background."""
        from music_manager.options.identify import store_track_data  # noqa: PLC0415
        from music_manager.services.resolver import resolve  # noqa: PLC0415

        try:
            pending_list: list[PendingTrack] = []
            remaining_ids: list[str] = []

            total = len(apple_ids)
            for idx, aid in enumerate(apple_ids):
                if not self._tracks_store or not self._albums_store:
                    continue
                self.app.call_from_thread(
                    self._identify_render_progress,
                    idx + 1,
                    total,
                )
                entry = self._tracks_store.all().get(aid, {})
                title = entry.get("title") or ""
                artist = entry.get("artist") or ""
                album = entry.get("album") or ""
                isrc = entry.get("isrc") or ""

                resolution = resolve(
                    title,
                    artist,
                    album,
                    isrc,
                    self._albums_store,
                )

                if resolution.status == "resolved" and resolution.track:
                    store_track_data(
                        aid,
                        resolution.track,
                        entry,
                        self._tracks_store,
                    )
                    # Save after each resolve (crash safety)
                    self._save_all()
                else:
                    pending_list.append(
                        PendingTrack(
                            reason=resolution.status,
                            csv_title=title,
                            csv_artist=artist,
                            csv_album=album,
                            track=resolution.track,
                            candidates=resolution.candidates,
                        )
                    )
                    remaining_ids.append(aid)

            self.app.call_from_thread(
                self._on_individual_resolved,
                pending_list,
                remaining_ids,
            )
        except Exception as _exc:  # noqa: BLE001
            from music_manager.core.logger import log_worker_error  # noqa: PLC0415

            log_worker_error(_exc)
            self.app.call_from_thread(self._identify_show_summary)

    def _on_individual_resolved(
        self,
        pending_list: list[PendingTrack],
        apple_ids: list[str],
    ) -> None:
        """Individual resolve done — review remaining."""
        if not pending_list:
            self._identify_show_summary()
            return

        self._identify_apple_ids = apple_ids
        self._start_review(pending_list)
        self._return_to = "main"

    def _identify_show_summary(self) -> None:
        """Show final summary."""
        from rich.text import Text as RichText  # noqa: PLC0415

        from music_manager.ui.styles import CHECK as _CHECK  # noqa: PLC0415

        body = RichText()
        body.append(f"\n  {_CHECK}  Identification termin\u00e9e\n", style="green")
        self._set_body(body)
        self._set_help(HELP_BACK)
        self._view = "identify_summary"

    def _identify_ignore_track(self, pending: PendingTrack) -> None:
        """Ignore track permanently — save to preferences."""
        from music_manager.core.io import load_json, save_json  # noqa: PLC0415

        if not self._paths:
            return

        prefs_path = self._paths.preferences_path
        prefs = load_json(prefs_path)
        raw = prefs.get("ignored_tracks")
        ignored: list = raw if isinstance(raw, list) else []

        key = f"{pending.csv_title.lower()}::{pending.csv_artist.lower()}"
        if key not in ignored:
            ignored.append(key)
            prefs["ignored_tracks"] = ignored
            save_json(prefs_path, prefs)

    def _execute_identify_confirmations(self) -> None:
        """Confirm accepted tracks from individual review (no import)."""
        from music_manager.options.identify import confirm_track  # noqa: PLC0415

        confirmed = 0
        for pending, action_type, data in self._accepted:
            # Find the original index of this pending in _pending
            try:
                idx = self._pending.index(pending)
            except ValueError:
                continue
            if idx >= len(self._identify_apple_ids):
                continue

            apple_id = self._identify_apple_ids[idx]
            entry = self._tracks_store.all().get(apple_id, {}) if self._tracks_store else {}
            file_path = entry.get("file_path") or ""

            candidate = None
            if action_type == "candidate" and isinstance(data, dict):
                candidate = data
            elif action_type == "track" and isinstance(data, Track):
                candidate = {
                    "deezer_id": data.deezer_id,
                    "album_id": data.album_id,
                    "isrc": data.isrc,
                    "cover_url": data.cover_url,
                }

            if candidate and self._tracks_store:
                confirm_track(
                    apple_id,
                    candidate,
                    self._tracks_store,
                    albums_store=self._albums_store,
                    file_path=file_path,
                )
                confirmed += 1

        # Show summary
        from rich.text import Text as RichText  # noqa: PLC0415

        from music_manager.ui.styles import CHECK as _CHECK  # noqa: PLC0415

        body = RichText()
        body.append("\n")
        parts = []
        if confirmed:
            parts.append(f"{confirmed} identifiée(s)")
        if self._review_skipped:
            parts.append(f"{self._review_skipped} passée(s)")

        if parts:
            body.append(f"  {_CHECK}  ", style="green")
            body.append(" · ".join(parts))
        else:
            body.append(f"  {_CHECK}  Aucune action", style="green")
        body.append("\n")

        self._set_body(body)
        self._set_help(HELP_BACK)
        self._view = "identify_summary"
        self._identify_apple_ids = []
