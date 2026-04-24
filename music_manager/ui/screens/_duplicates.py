"""Duplicates mixin — find and resolve duplicate tracks."""

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


class DuplicatesMixin(_MixinBase):
    """Duplicate detection and resolution methods for MenuScreen."""

    def _start_duplicates(self) -> None:
        """Launch find-duplicates: instant scan, per-group review."""
        from music_manager.options.find_duplicates import (  # noqa: PLC0415
            best_version,
            find_duplicates,
            group_key,
            load_ignored,
        )
        from music_manager.ui.text import (  # noqa: PLC0415
            DUP_NO_IDENTIFIED,
            DUP_NONE_FOUND,
            DUP_TITLE,
        )

        self._return_to = "tools"

        if not self._tracks_store:
            return

        has_identified = any(e.get("deezer_id") for e in self._tracks_store.all().values())
        if not has_identified:
            self._view = "summary"
            self._set_header(render_sub_header(DUP_TITLE))
            from rich.text import Text as RichText  # noqa: PLC0415

            body = RichText()
            body.append(f"\n  {CHECK}  ", style="green")
            body.append(f"{DUP_NO_IDENTIFIED}\n")
            self._set_body(body)
            self._set_help(HELP_BACK, with_newline=False)
            return

        groups = find_duplicates(self._tracks_store)

        prefs_path = self._paths.preferences_path if self._paths else ""
        if prefs_path:
            ignored = load_ignored(prefs_path)
            groups = [group for group in groups if group_key(group) not in ignored]

        if not groups:
            self._view = "summary"
            self._set_header(render_sub_header(DUP_TITLE))
            from rich.text import Text as RichText  # noqa: PLC0415

            body = RichText()
            body.append(f"\n  {CHECK}  ", style="green")
            body.append(f"{DUP_NONE_FOUND}\n")
            self._set_body(body)
            self._set_help(HELP_BACK, with_newline=False)
            return

        self._dup_groups = groups
        self._dup_best = [best_version(group) for group in groups]
        self._dup_idx = 0
        self._dup_cursor = 0
        self._dup_result = {"removed": 0, "skipped": 0, "ignored": 0}
        self._dup_render_group()

    def _dup_render_group(self) -> None:
        """Show current duplicate group for review."""
        from music_manager.ui.render import render_duplicate_group  # noqa: PLC0415
        from music_manager.ui.text import (  # noqa: PLC0415
            DUP_IGNORE,
            DUP_SKIP,
            DUP_TITLE,
            HELP_DUP,
        )

        if self._dup_idx >= len(self._dup_groups):
            self._dup_finish()
            return

        self._view = "duplicates"
        self._dup_cursor = self._dup_best[self._dup_idx]
        group = self._dup_groups[self._dup_idx]
        actions = [DUP_SKIP, DUP_IGNORE]

        self._dup_actions = actions
        self._set_header(render_sub_header(DUP_TITLE))
        self._set_body(
            render_duplicate_group(
                group,
                self._dup_best[self._dup_idx],
                self._dup_cursor,
                self._dup_idx + 1,
                len(self._dup_groups),
                actions,
            )
        )
        self._set_help(HELP_DUP)

    def _dup_move(self, direction: int) -> None:
        """Navigate within current duplicate group."""
        group = self._dup_groups[self._dup_idx]
        total = len(group) + len(self._dup_actions)
        self._dup_cursor = (self._dup_cursor + direction) % total
        self._dup_refresh()

    def _dup_refresh(self) -> None:
        """Re-render current group (cursor move, no scroll jump)."""
        from music_manager.ui.render import render_duplicate_group  # noqa: PLC0415

        group = self._dup_groups[self._dup_idx]
        self._set_body(
            render_duplicate_group(
                group,
                self._dup_best[self._dup_idx],
                self._dup_cursor,
                self._dup_idx + 1,
                len(self._dup_groups),
                self._dup_actions,
            ),
        )

    def _dup_select(self) -> None:
        """Enter on duplicates: keep version or execute action."""
        group = self._dup_groups[self._dup_idx]
        num_entries = len(group)

        if self._dup_cursor < num_entries:
            self._dup_keep_version(self._dup_cursor)
        else:
            action_idx = self._dup_cursor - num_entries
            if action_idx == 0:
                self._dup_result["skipped"] += 1
                self._dup_idx += 1
                self._dup_render_group()
            elif action_idx == 1:
                self._dup_ignore()

    def _dup_skip(self) -> None:
        """S key shortcut — skip current group."""
        from music_manager.core.logger import log_event  # noqa: PLC0415

        group = self._dup_groups[self._dup_idx]
        log_event("dup_skip", title=group[0].get("title", ""), artist=group[0].get("artist", ""))
        self._dup_result["skipped"] += 1
        self._dup_idx += 1
        self._dup_render_group()

    @work(thread=True)
    def _dup_keep_version(self, keep_idx: int) -> None:
        """Keep selected version, delete others."""
        from music_manager.options.find_duplicates import remove_duplicates  # noqa: PLC0415
        from music_manager.ui.text import DUP_REMOVING  # noqa: PLC0415

        self.app.call_from_thread(self._dup_set_working, DUP_REMOVING)

        group = self._dup_groups[self._dup_idx]
        kept = group[keep_idx]
        to_remove = [entry["_apple_id"] for i, entry in enumerate(group) if i != keep_idx]

        from music_manager.core.logger import log_event  # noqa: PLC0415

        log_event(
            "dup_keep",
            title=kept.get("title", ""),
            artist=kept.get("artist", ""),
            kept_apple_id=kept.get("_apple_id", ""),
            removed_count=len(to_remove),
        )

        removed = 0
        if to_remove and self._tracks_store:
            removed = remove_duplicates(to_remove, self._tracks_store)
            self._save_all()

        self._dup_result["removed"] += removed
        self.app.call_from_thread(self._dup_advance)

    def _dup_set_working(self, message: str) -> None:
        """Show working status."""
        from music_manager.ui.render import render_modify_status  # noqa: PLC0415

        self._view = "dup_removing"
        self._set_body(render_modify_status(message))
        self._set_help("")

    def _dup_advance(self) -> None:
        """Move to next group after keep/ignore."""
        self._dup_idx += 1
        self._dup_render_group()

    def _dup_preview(self) -> None:
        """Play preview of currently selected duplicate entry."""
        group = self._dup_groups[self._dup_idx]
        if self._dup_cursor >= len(group):
            return
        entry = group[self._dup_cursor]
        deezer_id = entry.get("deezer_id")
        if deezer_id:
            self._play_preview_fresh(deezer_id)

    def _dup_ignore(self) -> None:
        """Ignore current group permanently."""
        from music_manager.core.logger import log_event  # noqa: PLC0415

        group = self._dup_groups[self._dup_idx]
        log_event(
            "dup_ignore",
            title=group[0].get("title", ""),
            artist=group[0].get("artist", ""),
            group_size=len(group),
        )
        from music_manager.options.find_duplicates import ignore_group  # noqa: PLC0415

        prefs_path = self._paths.preferences_path if self._paths else ""
        if prefs_path:
            ignore_group(group, prefs_path)
        self._dup_result["ignored"] += 1
        self._dup_idx += 1
        self._dup_render_group()

    def _dup_finish(self) -> None:
        """Show duplicates summary."""
        from music_manager.core.logger import log_event  # noqa: PLC0415
        from music_manager.ui.render import render_duplicates_summary  # noqa: PLC0415
        from music_manager.ui.text import DUP_TITLE  # noqa: PLC0415

        log_event("duplicates_complete", **self._dup_result)
        self._view = "summary"
        self._set_header(render_sub_header(DUP_TITLE))
        self._set_body(
            render_duplicates_summary(
                self._dup_result["removed"],
                self._dup_result["skipped"],
                self._dup_result["ignored"],
            )
        )
        self._set_help(HELP_BACK, with_newline=False)
        self._save_all()
