"""Fix metadata mixin — correct identified tracks against Deezer data."""

from typing import TYPE_CHECKING

from textual import work

from music_manager.ui.render import render_help, render_sub_header
from music_manager.ui.styles import CHECK
from music_manager.ui.text import HELP_BACK

if TYPE_CHECKING:
    from music_manager.ui.screens._protocol import MenuScreenProto

    _MixinBase = MenuScreenProto
else:
    _MixinBase = object


class FixMetadataMixin(_MixinBase):
    """Fix-metadata feature methods for MenuScreen."""

    def _start_fix_metadata(self) -> None:
        """Launch fix-metadata: scan divergences in background."""
        from music_manager.ui.text import FIX_SCANNING, FIX_TITLE  # noqa: PLC0415

        self._return_to = "tools"
        self._view = "fixing_scan"
        self._fix_explicit_queue = []
        self._set_header(render_sub_header(FIX_TITLE))
        self._set_body(render_help(f"\n  {FIX_SCANNING}", with_newline=False))
        self._set_help("")
        self._scan_divergences()

    def _fix_render_fetch(self, current: int, total: int) -> None:
        """Show progress for uncached album fetches."""
        from rich.text import Text as RichText  # noqa: PLC0415

        from music_manager.ui.styles import BLUE  # noqa: PLC0415
        from music_manager.ui.text import FIX_SCANNING  # noqa: PLC0415

        pct = current / total if total else 0
        bar_width = 30
        filled = int(bar_width * pct)
        bar = "█" * filled + "░" * (bar_width - filled)

        body = RichText()
        body.append(f"\n  {FIX_SCANNING}  ", style="dim")
        body.append(f"[{bar}]", style=f"bold {BLUE}")
        body.append(f"  {current}/{total}", style="dim")
        self._set_body(body)

    @work(thread=True)
    def _scan_divergences(self) -> None:
        """Scan all divergences in background thread."""
        from music_manager.options.fix_metadata import find_all_divergences  # noqa: PLC0415

        if not (self._tracks_store and self._albums_store and self._paths):
            self.app.call_from_thread(self._on_divergences_found, [])
            return

        try:
            apple = self.app.apple  # type: ignore[attr-defined]

            def on_fetch(current: int, total: int) -> None:
                self.app.call_from_thread(self._fix_render_fetch, current, total)

            divs = find_all_divergences(
                self._tracks_store,
                self._albums_store,
                apple,
                self._paths.preferences_path,
                on_fetch=on_fetch,
            )
            self.app.call_from_thread(self._on_divergences_found, divs)
        except Exception as exc:  # noqa: BLE001
            from music_manager.core.logger import log_worker_error  # noqa: PLC0415

            log_worker_error(exc)
            self.app.call_from_thread(self._on_divergences_found, [])

    def _on_divergences_found(self, album_divs: list) -> None:
        """Divergences scan complete."""
        from music_manager.ui.text import FIX_NO_IDENTIFIED, FIX_UP_TO_DATE  # noqa: PLC0415

        if not album_divs:
            from rich.text import Text as RichText  # noqa: PLC0415

            has_identified = (
                any(e.get("deezer_id") for e in self._tracks_store.all().values())
                if self._tracks_store
                else False
            )
            msg = FIX_UP_TO_DATE if has_identified else FIX_NO_IDENTIFIED
            body = RichText()
            body.append(f"\n  {CHECK}  ", style="green")
            body.append(f"{msg}\n")
            self._set_body(body)
            self._view = "summary"
            self._set_help(HELP_BACK, with_newline=False)
            return

        self._fix_albums = album_divs
        self._fix_album_idx = 0
        self._fix_result = {"corrected": 0, "up_to_date": 0, "skipped": 0}
        self._render_fix_album()

    def _render_fix_album(self) -> None:
        """Show current album's divergences with multi-select checkboxes."""
        from music_manager.ui.render import render_fix_body, render_fix_header  # noqa: PLC0415
        from music_manager.ui.text import FIELD_LABELS  # noqa: PLC0415

        if self._fix_album_idx >= len(self._fix_albums):
            self._finish_fix()
            return

        self._view = "fixing"
        album_div = self._fix_albums[self._fix_album_idx]

        seen: dict[str, int] = {}
        unique_divs: list[int] = []
        for i, div in enumerate(album_div.divergences):
            key = f"{div.field_name}:{div.deezer_value}"
            if key not in seen:
                seen[key] = i
                unique_divs.append(i)
        self._fix_unique_indices = unique_divs

        self._fix_checks = [True] * len(unique_divs)
        self._fix_cursor = 0
        self._fix_actions = ["skip", "ignore"]

        divs_labels = []
        for idx in unique_divs:
            div = album_div.divergences[idx]
            label = FIELD_LABELS.get(div.field_name, div.field_name)
            divs_labels.append((div.field_name, label, div.local_value, div.deezer_value, True))

        self._fix_divs_labels = divs_labels
        num_divs = len(divs_labels)

        self._set_header(
            render_fix_header(
                album_div.album_title,
                album_div.artist,
                album_div.track_count,
                idx=self._fix_album_idx + 1,
                total=len(self._fix_albums),
            )
        )
        self._set_body(render_fix_body(divs_labels, self._fix_actions, self._fix_cursor, num_divs))
        has_cover = any(d.field_name == "cover" for d in album_div.divergences)
        help_text = "↑↓  naviguer    espace  cocher/décocher    ⏎  appliquer"
        if has_cover:
            help_text += "    p  pochette"
        self._set_help(help_text)
        self.focus()

    def _fix_move(self, direction: int) -> None:
        """Navigate fix-metadata checkboxes and actions."""
        total = len(self._fix_unique_indices) + len(self._fix_actions)
        self._fix_cursor = (self._fix_cursor + direction) % total
        self._refresh_fix_body()

    def _fix_select(self) -> None:
        """Enter on fix-metadata: apply corrections or execute action."""
        num_divs = len(self._fix_unique_indices)

        if self._fix_cursor < num_divs:
            self._apply_fix_corrections()
        else:
            action_idx = self._fix_cursor - num_divs
            action = self._fix_actions[action_idx]

            if action == "skip":
                from music_manager.core.logger import log_event  # noqa: PLC0415

                album_div = self._fix_albums[self._fix_album_idx]
                log_event("fix_album_skip", album=album_div.album_title)
                self._fix_result["skipped"] += 1
                self._fix_album_idx += 1
                self._render_fix_album()
            elif action == "ignore":
                self._ignore_fix_album()

    def _refresh_fix_body(self) -> None:
        """Re-render fix body with current checkbox states."""
        from music_manager.ui.render import render_fix_body  # noqa: PLC0415
        from music_manager.ui.text import FIELD_LABELS  # noqa: PLC0415

        album_div = self._fix_albums[self._fix_album_idx]

        divs_labels = []
        for i, idx in enumerate(self._fix_unique_indices):
            div = album_div.divergences[idx]
            label = FIELD_LABELS.get(div.field_name, div.field_name)
            divs_labels.append(
                (div.field_name, label, div.local_value, div.deezer_value, self._fix_checks[i])
            )

        self._set_body(
            render_fix_body(divs_labels, self._fix_actions, self._fix_cursor, len(divs_labels)),
        )

    @work(thread=True)
    def _apply_fix_corrections(self) -> None:
        """Apply metadata+cover now, queue explicit for batch at end."""
        from music_manager.options.fix_metadata import (  # noqa: PLC0415
            apply_corrections,
            save_refusals,
        )

        try:
            album_div = self._fix_albums[self._fix_album_idx]

            checked_keys = set()
            refused_keys = set()
            for i, idx in enumerate(self._fix_unique_indices):
                div = album_div.divergences[idx]
                key = f"{div.field_name}:{div.deezer_value}"
                if self._fix_checks[i]:
                    checked_keys.add(key)
                else:
                    refused_keys.add(key)

            selected = [
                d
                for d in album_div.divergences
                if f"{d.field_name}:{d.deezer_value}" in checked_keys
            ]
            refused = [
                d
                for d in album_div.divergences
                if f"{d.field_name}:{d.deezer_value}" in refused_keys
            ]

            explicit_queue: list = []
            if selected and self._tracks_store:
                album_id = None
                for d in album_div.divergences:
                    entry = self._tracks_store.get_by_apple_id(d.apple_id)
                    if entry and entry.get("album_id"):
                        album_id = entry["album_id"]
                        break
                if album_id:
                    cover_entries = [
                        aid
                        for aid, entry in self._tracks_store.all().items()
                        if entry.get("album_id") == album_id
                    ]
                else:
                    cover_entries = list({d.apple_id for d in album_div.divergences})

                _, explicit_queue = apply_corrections(
                    selected,
                    self._tracks_store,
                    apple_store=self.app.apple,  # type: ignore[attr-defined]
                    cover_url=album_div.cover_url,
                    cover_entries=cover_entries,
                )
                self._fix_explicit_queue.extend(explicit_queue)

            from music_manager.core.logger import log_event  # noqa: PLC0415

            fields = list({d.field_name for d in selected})
            log_event(
                "fix_album_apply",
                album=album_div.album_title,
                fields=fields,
                explicit_queued=len(explicit_queue),
            )

            cover_applied = [d for d in selected if d.field_name == "cover"]
            to_save = list(refused) + cover_applied
            if to_save and self._paths:
                save_refusals(to_save, self._paths.preferences_path)
        except Exception as exc:  # noqa: BLE001
            from music_manager.core.logger import log_event  # noqa: PLC0415

            log_event("fix_apply_error", error=str(exc))

        self.app.call_from_thread(self._on_fix_applied)

    def _fix_render_explicit_progress(self, current: int, total: int) -> None:
        """Show progress bar during explicit tag updates."""
        from rich.text import Text as RichText  # noqa: PLC0415

        from music_manager.ui.styles import BLUE  # noqa: PLC0415

        pct = current / total if total else 0
        bar_width = 30
        filled = int(bar_width * pct)
        bar = "█" * filled + "░" * (bar_width - filled)

        self._set_header(render_sub_header("Corriger les métadonnées"))
        body = RichText()
        body.append("\n  Conversion explicit...  ", style="dim")
        body.append(f"[{bar}]", style=f"bold {BLUE}")
        body.append(f"  {current}/{total}", style="dim")
        self._set_body(body)
        self._set_help("")

    def _on_fix_applied(self) -> None:
        """Fix corrections applied — advance to next album."""
        self._fix_result["corrected"] += 1
        self._fix_album_idx += 1
        self._render_fix_album()

    def _ignore_fix_album(self) -> None:
        """Ignore album permanently."""
        from music_manager.core.logger import log_event  # noqa: PLC0415
        from music_manager.options.fix_metadata import ignore_album  # noqa: PLC0415

        album_div = self._fix_albums[self._fix_album_idx]
        log_event("fix_album_ignore", album=album_div.album_title)
        if self._paths:
            ignore_album(album_div.album_title, self._paths.preferences_path)
        self._fix_result["skipped"] += 1
        self._fix_album_idx += 1
        self._render_fix_album()

    def _finish_fix(self) -> None:
        """Apply queued explicit corrections, then show summary."""
        if self._fix_explicit_queue:
            self._apply_explicit_batch()
        else:
            self._show_fix_summary()

    @work(thread=True)
    def _apply_explicit_batch(self) -> None:
        """Apply all queued explicit corrections with progress bar."""
        from music_manager.options.fix_metadata import (  # noqa: PLC0415
            apply_explicit_batch,
            save_refusals,
        )

        if not self._tracks_store:
            self.app.call_from_thread(self._show_fix_summary)
            return

        try:

            def on_progress(current: int, total: int) -> None:
                self.app.call_from_thread(
                    self._fix_render_explicit_progress,
                    current,
                    total,
                )

            applied = apply_explicit_batch(
                self._fix_explicit_queue,
                self._tracks_store,
                apple_store=self.app.apple,  # type: ignore[attr-defined]
                on_progress=on_progress,
            )

            if applied and self._paths:
                save_refusals(applied, self._paths.preferences_path)

            self._save_all()
        except Exception as exc:  # noqa: BLE001
            from music_manager.core.logger import log_event  # noqa: PLC0415

            log_event("explicit_batch_error", error=str(exc))

        self._fix_explicit_queue = []
        self.app.call_from_thread(self._show_fix_summary)

    def _show_fix_summary(self) -> None:
        """Show fix-metadata summary."""
        from music_manager.core.logger import log_event  # noqa: PLC0415
        from music_manager.ui.render import render_fix_summary  # noqa: PLC0415
        from music_manager.ui.text import FIX_TITLE  # noqa: PLC0415

        log_event("fix_metadata_complete", **self._fix_result)
        self._view = "summary"
        self._set_header(render_sub_header(FIX_TITLE))
        self._set_body(
            render_fix_summary(
                self._fix_result["corrected"],
                self._fix_result["up_to_date"],
                self._fix_result["skipped"],
            )
        )
        self._set_help(HELP_BACK, with_newline=False)
