"""Review mixin — review pending tracks, batch decisions, search."""

from typing import TYPE_CHECKING

from textual import work
from textual.widgets import Input

from music_manager.core.io import load_csv
from music_manager.core.models import PendingTrack, Track
from music_manager.ui.render import (
    render_batch_decision,
    render_help,
    render_review_body,
    render_review_header,
)
from music_manager.ui.text import (
    HELP_IMPORT,
    HELP_REVIEW,
    HELP_REVIEW_BATCH,
    HELP_SEARCH_INPUT,
    REVIEW_OPTIONS,
)

if TYPE_CHECKING:
    from music_manager.ui.screens._protocol import MenuScreenProto

    _MixinBase = MenuScreenProto
else:
    _MixinBase = object


class ReviewMixin(_MixinBase):
    """Review and search methods for MenuScreen."""

    # ── Review ──────────────────────────────────────────────────────────────

    def _pre_review(self) -> None:
        """Before review: batch decision if 3+ pending, else direct."""
        if not self._import_result:
            return
        pending = self._import_result.pending
        if len(pending) >= 3:
            self._view = "batch_decision"
            self._batch_cursor = 2  # default: one by one
            self._set_header(render_review_header(len(pending)))
            self._set_body(render_batch_decision(self._batch_cursor))
            self._set_help(HELP_REVIEW_BATCH)
        else:
            self._start_review(pending)

    def _handle_batch_decision(self) -> None:
        """Handle batch decision choice."""
        from music_manager.core.logger import log_event  # noqa: PLC0415

        choices = {0: "accept_all", 1: "reject_all", 2: "one_by_one"}
        log_event("review_batch_decision", choice=choices.get(self._batch_cursor, "unknown"))
        if not self._import_result:
            return
        pending = self._import_result.pending
        if self._batch_cursor == 0:
            # Tout accepter — auto-accept first candidate for ambiguous,
            # accept track directly for mismatch/duration_suspect
            self._batch_accept_all(pending)
        elif self._batch_cursor == 1:
            # Tout rejeter
            self._review_skipped = len(pending)
            self._pending = pending
            self._pending_idx = len(pending)
            self._finish_import()
        else:
            # One by one
            self._start_review(pending)

    def _batch_accept_all(self, pending: list[PendingTrack]) -> None:
        """Collect accept decisions for all pending, then execute."""
        self._pending = pending
        self._accepted = []
        self._to_delete = []
        self._review_skipped = 0
        self._review_deleted = 0

        for p in pending:
            if p.reason == "ambiguous" and p.candidates:
                self._accepted.append((p, "candidate", p.candidates[0]))
            elif p.reason in ("mismatch", "duration_suspect") and p.track:
                self._accepted.append((p, "track" if p.reason == "mismatch" else "audio", p.track))
            elif p.reason == "youtube_failed" and p.track:
                self._accepted.append((p, "track", p.track))
            else:
                self._review_skipped += 1

        self._execute_review_decisions()

    def _start_review(self, pending: list[PendingTrack]) -> None:
        """Start reviewing pending tracks (decision phase — no imports yet)."""
        self._view = "reviewing"
        self._pending = pending
        self._pending_idx = 0
        self._review_cursor = 0
        self._review_skipped = 0
        self._review_deleted = 0
        self._accepted = []
        self._to_delete = []
        self._render_review()

    def _build_review_options(self, pending: PendingTrack) -> list[str]:
        """Build the list of option keys for the current pending track."""
        if pending.reason == "ambiguous":
            opts = [f"candidate:{i}" for i in range(len(pending.candidates))]
            opts.extend(["search_deezer", "skip"])
        else:
            opts = list(REVIEW_OPTIONS.get(pending.reason, ["skip"]))
        # Identify mode: no CSV, add ignore option
        if self._identify_apple_ids:
            opts = [o for o in opts if o != "delete_csv"]
            if "ignore_identify" not in opts:
                opts.append("ignore_identify")
        # Playlist mode: rows never removed, hide "Delete from CSV"
        elif self._import_csv and self._playlists_dir:
            import os  # noqa: PLC0415

            if os.path.dirname(os.path.abspath(self._import_csv)) == os.path.abspath(
                self._playlists_dir
            ):
                opts = [o for o in opts if o != "delete_csv"]
        return opts

    def _render_review(self) -> None:
        """Show current pending track with navigable action menu."""
        # Clean up any leftover Input from search
        self._hide_search_input()

        if self._pending_idx >= len(self._pending):
            # All decisions made — execute imports
            self._execute_review_decisions()
            return

        self._view = "reviewing"
        pending = self._pending[self._pending_idx]
        self._review_options = self._build_review_options(pending)
        total = len(self._pending)
        idx = self._pending_idx + 1

        self._set_header(render_review_header(total))
        self._set_body(
            render_review_body(pending, self._review_options, self._review_cursor, idx, total)
        )
        # Show "p écouter" only if there are candidates/track to preview
        has_preview = pending.track or pending.candidates
        if has_preview:
            self._set_help(HELP_REVIEW)
        else:
            self._set_help("↑↓  naviguer    ⏎  sélectionner")

        self.focus()

    def _review_move(self, direction: int) -> None:
        """Navigate action menu with wrap-around."""
        if not self._review_options:
            return
        self._review_cursor = (self._review_cursor + direction) % len(self._review_options)
        # Re-render without touching scroll (cursor move only)
        pending = self._pending[self._pending_idx]
        total = len(self._pending)
        idx = self._pending_idx + 1
        self._set_body(
            render_review_body(pending, self._review_options, self._review_cursor, idx, total),
        )

    def _review_select(self) -> None:
        """Collect decision and advance to next pending (no import yet)."""
        if self._pending_idx >= len(self._pending):
            return
        if not self._review_options:
            return

        key = self._review_options[self._review_cursor]
        pending = self._pending[self._pending_idx]

        if key == "skip":
            self._review_skip()
        elif key == "delete_csv":
            self._review_delete_csv()
        elif key == "accept":
            from music_manager.core.logger import log_event  # noqa: PLC0415

            if pending.track:
                self._accepted.append((pending, "track", pending.track))
                log_event(
                    "review_accept",
                    title=pending.csv_title,
                    artist=pending.csv_artist,
                    deezer_id=pending.track.deezer_id,
                )
            elif pending.candidates:
                self._accepted.append((pending, "candidate", pending.candidates[0]))
                log_event(
                    "review_accept",
                    title=pending.csv_title,
                    artist=pending.csv_artist,
                    deezer_id=pending.candidates[0].get("id", 0),
                )
            self._advance_review()
        elif key == "accept_audio" and pending.track:
            from music_manager.core.logger import log_event  # noqa: PLC0415

            log_event("review_accept_audio", title=pending.csv_title, artist=pending.csv_artist)
            self._accepted.append((pending, "audio", None))
            self._advance_review()
        elif key == "retry" and pending.track:
            self._set_body(render_help("  Réessai en cours...", with_newline=False))
            self._retry_import(pending)
        elif key.startswith("candidate:"):
            cidx = int(key.split(":")[1])
            from music_manager.core.logger import log_event  # noqa: PLC0415

            candidate = pending.candidates[cidx]
            log_event(
                "review_accept",
                title=pending.csv_title,
                artist=pending.csv_artist,
                deezer_id=candidate.get("id", 0),
                edition=candidate.get("album", {}).get("title", ""),
            )
            self._accepted.append((pending, "candidate", candidate))
            self._advance_review()
        elif key == "ignore_identify":
            from music_manager.core.logger import log_event  # noqa: PLC0415

            log_event("identify_track_ignore", title=pending.csv_title, artist=pending.csv_artist)
            self._identify_ignore_track(pending)
            self._advance_review()
        elif key == "search_deezer":
            self._open_search("deezer")
        elif key == "search_youtube":
            self._open_search("youtube")
        else:
            self._review_skip()

    def _advance_review(self) -> None:
        """Move to next pending track. In identify mode, auto-resolve same album."""
        if self._identify_apple_ids and self._accepted:
            self._identify_resolve_same_album()

        self._pending_idx += 1
        self._review_cursor = 0
        self._render_review()

    def _identify_resolve_same_album(self) -> None:
        """Auto-resolve remaining tracks from same local album as accepted."""
        from music_manager.core.normalize import normalize as _norm  # noqa: PLC0415
        from music_manager.core.normalize import prepare_title  # noqa: PLC0415
        from music_manager.options.identify import confirm_track  # noqa: PLC0415
        from music_manager.services.resolver import get_album_tracklist  # noqa: PLC0415

        last_pending, action_type, data = self._accepted[-1]

        # Get album_id from accepted candidate
        album_id = 0
        if action_type == "candidate" and isinstance(data, dict):
            alb = data.get("album", {})
            album_id = alb.get("id", 0) if isinstance(alb, dict) else data.get("album_id", 0)
        elif action_type == "track" and isinstance(data, Track):
            album_id = data.album_id

        if not album_id or not self._tracks_store or not self._albums_store:
            return

        # Get tracklist
        tracklist = get_album_tracklist(album_id, self._albums_store)
        if not tracklist:
            return

        local_album = _norm(last_pending.csv_album)
        if not local_album:
            return

        # Build soft title lookup
        dz_by_prep: dict[str, dict] = {}
        dz_by_norm: dict[str, dict] = {}
        for dz in tracklist:
            t = dz.get("title", "")
            dz_by_norm[_norm(t)] = dz
            dz_by_prep[prepare_title(t)] = dz

        # Check remaining pending tracks
        resolved_indices: set[int] = set()
        for idx in range(self._pending_idx + 1, len(self._pending)):
            p = self._pending[idx]
            if _norm(p.csv_album) != local_album:
                continue

            # Match by exact normalize or soft prepare_title
            match = dz_by_norm.get(_norm(p.csv_title))
            if not match:
                match = dz_by_prep.get(prepare_title(p.csv_title))
            if not match:
                continue

            # Confirm this track
            if idx < len(self._identify_apple_ids):
                aid = self._identify_apple_ids[idx]
                entry = self._tracks_store.all().get(aid, {})
                candidate = dict(match)
                candidate["album_id"] = album_id
                confirm_track(
                    aid,
                    candidate,
                    self._tracks_store,
                    albums_store=self._albums_store,
                    file_path=entry.get("file_path") or "",
                )
                resolved_indices.add(idx)

        # Remove resolved from pending (reverse to keep indices valid)
        if resolved_indices:
            new_pending = []
            new_ids = []
            for idx in range(len(self._pending)):
                if idx not in resolved_indices:
                    new_pending.append(self._pending[idx])
                    if idx < len(self._identify_apple_ids):
                        new_ids.append(self._identify_apple_ids[idx])
            self._pending = new_pending
            self._identify_apple_ids = new_ids

    @work(thread=True)
    def _retry_import(self, pending: PendingTrack) -> None:
        """Retry YouTube download+import immediately. Re-show menu on failure."""
        from music_manager.pipeline.importer import import_resolved_track  # noqa: PLC0415

        if not (pending.track and self._paths and self._tracks_store and self._albums_store):
            self.app.call_from_thread(self._on_retry_failed)
            return

        try:
            result = import_resolved_track(
                pending.track,
                self._paths,
                self._tracks_store,
                self._albums_store,
                csv_title=pending.csv_title,
                csv_artist=pending.csv_artist,
                csv_album=pending.csv_album,
            )
            if result is None:
                # Success — collect and advance
                self.app.call_from_thread(self._on_retry_success, pending)
            else:
                # Failed again — re-show review with same pending
                self.app.call_from_thread(self._on_retry_failed)
        except Exception as _exc:  # noqa: BLE001
            from music_manager.core.logger import log_event as _le  # noqa: PLC0415

            _le("worker_error", error=str(_exc))
            self.app.call_from_thread(self._on_retry_failed)

    def _on_retry_success(self, pending: PendingTrack) -> None:
        """Retry succeeded — collect decision and advance."""
        self._accepted.append((pending, "track", pending.track))
        self._advance_review()

    def _on_retry_failed(self) -> None:
        """Retry failed — re-show review menu (user can try YouTube search)."""
        self._render_review()

    def _review_skip(self) -> None:
        """Skip current pending — re-tried at next import."""
        from music_manager.core.logger import log_event  # noqa: PLC0415

        if self._pending_idx < len(self._pending):
            p = self._pending[self._pending_idx]
            log_event("review_skip", title=p.csv_title, artist=p.csv_artist, reason=p.reason)
        self._review_skipped += 1
        self._advance_review()

    def _review_delete_csv(self) -> None:
        """Mark current pending for CSV deletion and advance."""
        from music_manager.core.logger import log_event  # noqa: PLC0415

        pending = self._pending[self._pending_idx]
        log_event("review_delete", title=pending.csv_title, artist=pending.csv_artist)
        self._to_delete.append(pending)
        self._review_deleted += 1
        self._advance_review()

    # ── Review: search input ───────────────────────────────────────────────

    # ── Review: execute decisions (batch import at end) ──────────────────

    def _execute_review_decisions(self) -> None:
        """Execute all collected decisions: confirm (identify) or import (CSV)."""
        # Identify mode: confirm tracks, no import
        if self._identify_apple_ids:
            self._execute_identify_confirmations()
            return

        # 1. Delete CSV rows (single read+write, not N separate calls)
        if self._to_delete:
            from music_manager.core.io import save_csv  # noqa: PLC0415

            delete_keys = {(p.csv_title.lower(), p.csv_artist.lower()) for p in self._to_delete}
            rows = load_csv(self._import_csv)
            remaining = [
                r
                for r in rows
                if (r.get("title", "").lower(), r.get("artist", "").lower()) not in delete_keys
            ]
            if len(remaining) < len(rows):
                save_csv(self._import_csv, remaining)

        # 2. Batch import accepted tracks
        if self._accepted:
            self._view = "importing"
            self._batch_import_idx = 0
            self._batch_import_done = 0
            self._set_header(render_review_header(len(self._accepted)))
            self._set_help(HELP_IMPORT, with_newline=False)
            self._execute_next_import()
        else:
            self._finish_import()

    def _execute_next_import(self) -> None:
        """Import the next accepted track in the batch."""
        if self._cancel_requested:
            self._finish_import()
            return
        if self._batch_import_idx >= len(self._accepted):
            self._finish_import()
            return

        pending, action_type, data = self._accepted[self._batch_import_idx]
        idx = self._batch_import_idx + 1
        total = len(self._accepted)
        self._set_body(
            render_help(
                f"  Import {idx}/{total} : {pending.csv_title} — {pending.csv_artist}...",
                with_newline=False,
            )
        )

        if action_type == "track":
            self._import_track(data, pending)
        elif action_type == "candidate":
            self._import_candidate(data, pending)
        elif action_type == "audio":
            self._finalize_existing_audio(pending)

    # ── Review: search input ───────────────────────────────────────────────

    def _open_search(self, search_type: str) -> None:
        """Open browser and show URL input below the current review body."""
        import webbrowser  # noqa: PLC0415
        from urllib.parse import quote_plus  # noqa: PLC0415

        pending = self._pending[self._pending_idx]
        query = f"{pending.csv_title} {pending.csv_artist}"

        if search_type == "deezer":
            webbrowser.open(f"https://www.deezer.com/search/{quote_plus(query)}")
        else:
            webbrowser.open(f"https://www.youtube.com/results?search_query={quote_plus(query)}")

        self._search_type = search_type
        self._view = "search_input"
        self._set_help(HELP_SEARCH_INPUT)

        # Show, enable, and focus the Input
        input_widget = self.query_one("#menu-input", Input)
        input_widget.value = ""
        input_widget.display = True
        input_widget.disabled = False
        input_widget.focus()

    def _hide_search_input(self) -> None:
        """Hide and disable the URL input field."""
        try:
            input_widget = self.query_one("#menu-input", Input)
            input_widget.display = False
            input_widget.disabled = True
            input_widget.value = ""
        except Exception:  # noqa: BLE001
            pass

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle submissions from Input widget."""
        value = event.value.strip()

        # Modify: metadata field edit or YouTube URL
        if self._view == "modify_meta_edit":
            if self._modify_editing_field == "__youtube_url__":
                self._hide_modify_input()
                if value:
                    self._modify_run_replace_url(value)
                else:
                    self._view = "modify_actions"
                    self._modify_cursor = 0
                    self._show_modify_actions()
            else:
                # Metadata field edit
                self._hide_modify_input()
                if value != "":
                    key = self._modify_editing_field
                    # Convert to int for numeric fields
                    if key in ("year", "track_number"):
                        try:
                            self._modify_meta_changes[key] = int(value)
                        except ValueError:
                            self._modify_meta_changes[key] = value
                    else:
                        self._modify_meta_changes[key] = value
                self._view = "modify_metadata"
                self._refresh_modify_metadata()
            return

        # Modify: search (Enter selects, handled by action_select → _modify_select)
        if self._view in ("modify_search", "modify_results"):
            # Trigger selection
            if self._view == "modify_results":
                self._modify_select_result()
            return

        if not value:
            return

        if self._search_type == "deezer":
            self._process_deezer_url(value)
        elif self._search_type == "youtube":
            self._hide_search_input()
            self._process_youtube_url(value)

    def _process_deezer_url(self, url: str) -> None:
        """Parse Deezer URL and import the track."""
        import re  # noqa: PLC0415

        # Parse URL: deezer.com/[lang/]track|album/ID or numeric ID
        match = re.search(r"deezer\.com/(?:\w+/)?(track|album)/(\d+)", url)
        if match:
            url_type = match.group(1)
            deezer_id = int(match.group(2))
        elif url.isdigit():
            url_type = "track"
            deezer_id = int(url)
        else:
            from music_manager.ui.text import SEARCH_ERROR_INVALID  # noqa: PLC0415

            self._set_body(render_help(f"\n  {SEARCH_ERROR_INVALID}\n", with_newline=False))
            # Input is still visible — clear it for retry
            input_widget = self.query_one("#menu-input", Input)
            input_widget.value = ""
            input_widget.focus()
            return

        self._hide_search_input()
        self._set_body(render_help("  Import en cours...", with_newline=False))
        self._fetch_deezer_and_import(url_type, deezer_id)

    @work(thread=True)
    def _fetch_deezer_and_import(self, url_type: str, deezer_id: int) -> None:
        """Fetch from Deezer API and import. Always fetches full /track/{id} for metadata."""
        from music_manager.services.resolver import (  # noqa: PLC0415
            build_track,
            deezer_get,
            fetch_album_with_cover,
        )

        if not self._albums_store:
            self.app.call_from_thread(self._on_search_failed)
            return

        try:
            pending = self._pending[self._pending_idx]

            if url_type == "track":
                # Fetch full track metadata via /track/{id}
                data = deezer_get(f"/track/{deezer_id}")
                if not data or "error" in data:
                    self.app.call_from_thread(self._on_search_failed)
                    return
                album_id = data.get("album", {}).get("id", 0)
                album_data = fetch_album_with_cover(album_id, self._albums_store)
                track = build_track(data, album_data)
                self.app.call_from_thread(self._on_search_resolved, track)

            elif url_type == "album":
                from music_manager.core.normalize import is_match  # noqa: PLC0415

                data = deezer_get(f"/album/{deezer_id}/tracks")
                if not data or "error" in data:
                    self.app.call_from_thread(self._on_search_failed)
                    return

                tracks_list = data.get("data", [])
                album_data = fetch_album_with_cover(deezer_id, self._albums_store)

                # Find track matching CSV title
                matched = [
                    t
                    for t in tracks_list
                    if is_match(pending.csv_title, t.get("title", ""), "title")
                ]

                if len(matched) == 1:
                    full_data = deezer_get(f"/track/{matched[0]['id']}")
                    if full_data and "error" not in full_data:
                        track = build_track(full_data, album_data)
                        self.app.call_from_thread(self._on_search_resolved, track)
                    else:
                        self.app.call_from_thread(self._on_search_failed)
                else:
                    # 0 or multiple matches → show as ambiguous for user to choose
                    candidates = matched if matched else tracks_list
                    if candidates:
                        self.app.call_from_thread(
                            self._show_album_candidates, candidates, album_data
                        )
                    else:
                        self.app.call_from_thread(self._on_search_failed)
        except Exception as _exc:  # noqa: BLE001
            from music_manager.core.logger import log_event as _le  # noqa: PLC0415

            _le("worker_error", error=str(_exc))
            self.app.call_from_thread(self._on_search_failed)

    def _on_search_resolved(self, track) -> None:
        """Deezer search found a track — collect decision and advance."""
        pending = self._pending[self._pending_idx]
        self._accepted.append((pending, "track", track))
        self._advance_review()

    def _on_search_failed(self) -> None:
        """Deezer/YouTube search failed — return to review options."""
        self._render_review()

    def _show_album_candidates(self, candidates: list[dict], album_data: dict) -> None:
        """Show album tracks as ambiguous candidates for user to choose."""
        pending = self._pending[self._pending_idx]
        pending.candidates = candidates
        pending.reason = "ambiguous"
        # Store album_data for when user selects a candidate
        self._review_cursor = 0
        self._render_review()

    def _process_youtube_url(self, url: str) -> None:
        """Download from YouTube URL and import."""
        self._set_body(render_help("  Téléchargement en cours...", with_newline=False))
        self._download_youtube_and_import(url)

    @work(thread=True)
    def _download_youtube_and_import(self, url: str) -> None:
        """Download YouTube URL and store as accept_audio decision."""
        from music_manager.services.youtube import download_track  # noqa: PLC0415

        try:
            pending = self._pending[self._pending_idx]
            if not pending.track or not self._paths:
                self.app.call_from_thread(self._on_search_failed)
                return

            # Download now (URL won't be available later)
            dl_path, actual_duration = download_track(url, self._paths.tmp_dir)

            # Store dl_path on pending so batch import can use it
            pending.dl_path = dl_path
            if actual_duration:
                pending.actual_duration = actual_duration

            self.app.call_from_thread(self._on_youtube_downloaded, pending)
        except Exception as _exc:  # noqa: BLE001
            from music_manager.core.logger import log_event as _le  # noqa: PLC0415

            _le("worker_error", error=str(_exc))
            self.app.call_from_thread(self._on_search_failed)

    def _on_youtube_downloaded(self, pending: PendingTrack) -> None:
        """YouTube downloaded — collect as audio decision and advance."""
        self._accepted.append((pending, "audio", None))
        self._advance_review()

    @work(thread=True)
    def _finalize_existing_audio(self, pending: PendingTrack) -> None:
        """Tag and import an already-downloaded file (duration_suspect accept)."""
        from datetime import datetime  # noqa: PLC0415

        from music_manager.core.logger import log_event  # noqa: PLC0415
        from music_manager.pipeline.importer import download_cover  # noqa: PLC0415
        from music_manager.services.apple import import_file  # noqa: PLC0415
        from music_manager.services.tagger import tag_audio_file  # noqa: PLC0415

        try:
            track = pending.track
            dl_path = pending.dl_path

            if not (
                track and dl_path and self._paths and self._albums_store and self._tracks_store
            ):
                self.app.call_from_thread(self._on_review_failed)
                return

            # Cover
            cover_path = download_cover(track, self._paths, self._albums_store)

            # Tag existing file
            tag_audio_file(dl_path, track, cover_path=cover_path)

            # Import into Apple Music
            apple_id = import_file(dl_path)

            # Update store
            track.apple_id = apple_id
            track.status = "done"
            track.origin = "imported"
            track.imported_at = datetime.now().isoformat(timespec="seconds")
            track.csv_title = pending.csv_title or track.title
            track.csv_artist = pending.csv_artist or track.artist
            track.csv_album = pending.csv_album or track.album

            self._tracks_store.add(apple_id, track.to_dict())
            self._tracks_store.save()

            log_event(
                "import_accept_audio",
                title=track.title,
                artist=track.artist,
                apple_id=apple_id,
            )

            # Cleanup audio
            import os as _os  # noqa: PLC0415

            try:
                _os.remove(dl_path)
            except OSError:
                pass

            self.app.call_from_thread(self._on_review_imported)
        except Exception as _exc:  # noqa: BLE001
            from music_manager.core.logger import log_event as _le  # noqa: PLC0415

            _le("worker_error", error=str(_exc))
            self.app.call_from_thread(self._on_review_failed)

    # ── Review: import workers ─────────────────────────────────────────────

    @work(thread=True)
    def _import_candidate(self, candidate: dict, pending: PendingTrack) -> None:
        """Resolve and import a candidate. Fetches full /track/{id} for metadata."""
        from music_manager.services.resolver import (  # noqa: PLC0415
            build_track,
            deezer_get,
            fetch_album_with_cover,
        )

        if not self._albums_store:
            self.app.call_from_thread(self._on_review_failed)
            return

        try:
            track_id = candidate.get("id", 0)
            full_data = deezer_get(f"/track/{track_id}") if track_id else None
            source = full_data if (full_data and "error" not in full_data) else candidate

            album_id = source.get("album", {}).get("id", 0)
            album_data = fetch_album_with_cover(album_id, self._albums_store)
            track = build_track(source, album_data)
            self.app.call_from_thread(self._do_batch_import, track, pending)
        except Exception as _exc:  # noqa: BLE001
            from music_manager.core.logger import log_event as _le  # noqa: PLC0415

            _le("worker_error", error=str(_exc))
            self.app.call_from_thread(self._on_review_failed)

    def _do_batch_import(self, track, pending: PendingTrack) -> None:
        """Bridge: start batch track import from main thread."""
        self._import_track(track, pending)

    @work(thread=True)
    def _import_track(self, track, pending: PendingTrack) -> None:
        """Import a single track with explicit pending (no index lookup)."""
        from music_manager.pipeline.importer import import_resolved_track  # noqa: PLC0415

        if not (self._paths and self._tracks_store and self._albums_store):
            self.app.call_from_thread(self._on_review_failed)
            return

        try:
            result = import_resolved_track(
                track,
                self._paths,
                self._tracks_store,
                self._albums_store,
                csv_title=pending.csv_title,
                csv_artist=pending.csv_artist,
                csv_album=pending.csv_album,
            )

            if result is None:
                self.app.call_from_thread(self._on_review_imported)
            else:
                self.app.call_from_thread(self._on_review_failed)
        except Exception as _exc:  # noqa: BLE001
            from music_manager.core.logger import log_event as _le  # noqa: PLC0415

            _le("worker_error", error=str(_exc))
            self.app.call_from_thread(self._on_review_failed)

    def _on_review_imported(self) -> None:
        """Batch import: track succeeded — add to playlist + advance."""
        pending, _, _ = self._accepted[self._batch_import_idx]

        self._batch_import_done += 1
        self._batch_import_idx += 1
        self._execute_next_import()

    def _on_review_failed(self) -> None:
        """Batch import: track failed — skip and advance."""
        self._review_skipped += 1
        self._batch_import_idx += 1
        self._execute_next_import()
