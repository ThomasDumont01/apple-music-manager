"""Maintenance mixin — reset, revert, delete operations."""

from typing import TYPE_CHECKING

from music_manager.ui.render import render_sub_header
from music_manager.ui.styles import CHECK
from music_manager.ui.text import HELP_BACK

if TYPE_CHECKING:
    from music_manager.ui.screens._protocol import MenuScreenProto

    _MixinBase = MenuScreenProto
else:
    _MixinBase = object


class MaintenanceMixin(_MixinBase):
    """Maintenance feature methods for MenuScreen."""

    def _run_maintenance(self, key: str) -> None:
        """Execute a maintenance action with confirmation for destructive ops."""
        from music_manager.core.logger import log_event  # noqa: PLC0415

        if key == "snapshot":
            from music_manager.options.snapshot import snapshot  # noqa: PLC0415

            count = snapshot(self._tracks_store) if self._tracks_store else 0
            self._show_maintenance_result(f"{count} piste(s) promue(s) en existantes")
            log_event("maintenance_done", op=key, count=count)

        elif key == "reset_failed":
            from music_manager.options.maintenance import reset_failed  # noqa: PLC0415

            count = reset_failed(self._tracks_store) if self._tracks_store else 0
            self._show_maintenance_result(f"{count} import(s) en échec réinitialisé(s)")
            log_event("maintenance_done", op=key, count=count)

        elif key == "clear_prefs":
            from music_manager.options.maintenance import clear_preferences  # noqa: PLC0415

            if self._paths:
                clear_preferences(self._paths.preferences_path)
            self._show_maintenance_result("Préférences vidées")
            log_event("maintenance_done", op=key)

        elif key == "revert":
            count = 0
            if self._tracks_store:
                count = sum(
                    1
                    for e in self._tracks_store.all().values()
                    if e.get("origin") == "imported" and e.get("status") == "done"
                )
            if count == 0:
                self._show_maintenance_result("Aucun import à annuler")
                return
            self._maintenance_pending = ("revert", count)
            self._show_maintenance_confirm(
                f"Supprimer {count} import(s) d'Apple Music ?",
            )

        elif key == "delete_all":
            self._maintenance_pending = ("delete_all", 0)
            self._show_maintenance_confirm(
                "Supprimer toutes les données Music Manager ?",
            )

    def _show_maintenance_confirm(self, message: str) -> None:
        """Show confirmation dialog for destructive operations."""
        from rich.text import Text as RichText  # noqa: PLC0415

        from music_manager.ui.styles import BLUE  # noqa: PLC0415

        self._view = "maintenance_confirm"
        self._modify_cursor = 0  # reuse cursor: 0=confirm, 1=cancel

        body = RichText()
        body.append(f"\n  {message}\n\n", style="bold red")
        body.append("  ❯ Confirmer\n", style=f"bold {BLUE}")
        body.append("    Annuler\n")
        self._set_body(body)
        self._set_help("↑↓  naviguer    ⏎  sélectionner    esc  annuler")

    def _refresh_maintenance_confirm(self) -> None:
        """Re-render confirmation with cursor."""
        from rich.text import Text as RichText  # noqa: PLC0415

        from music_manager.ui.styles import BLUE, MARKER, MARKER_EMPTY  # noqa: PLC0415

        action, count = self._maintenance_pending
        if action == "revert":
            msg = f"Supprimer {count} import(s) d'Apple Music ?"
        else:
            msg = "Supprimer toutes les données Music Manager ?"

        body = RichText()
        body.append(f"\n  {msg}\n\n", style="bold red")
        options = ["Confirmer", "Annuler"]
        for i, opt in enumerate(options):
            is_active = i == self._modify_cursor
            marker = MARKER if is_active else MARKER_EMPTY
            if is_active:
                body.append(f"  {marker}", style=f"bold {BLUE}")
                body.append(opt, style=f"bold {BLUE}")
            else:
                body.append(f"  {marker}{opt}")
            body.append("\n")
        self._set_body(body)

    def _confirm_maintenance(self) -> None:
        """Execute confirmed destructive action."""
        from music_manager.core.logger import log_event  # noqa: PLC0415

        action, count = self._maintenance_pending

        if action == "revert":
            from music_manager.options.maintenance import revert_imports  # noqa: PLC0415

            reverted = revert_imports(self._tracks_store) if self._tracks_store else 0
            self._refresh_stats()
            self._show_maintenance_result(f"{reverted} import(s) supprimé(s)")
            log_event("maintenance_done", op=action, count=reverted)

        elif action == "delete_all":
            from music_manager.options.maintenance import delete_all  # noqa: PLC0415

            if self._paths:
                delete_all(self._paths.root)
            log_event("maintenance_done", op=action)
            self.app.exit()

    def _show_maintenance_result(self, message: str) -> None:
        """Show maintenance result summary."""
        from rich.text import Text as RichText  # noqa: PLC0415

        self._view = "summary"
        self._return_to = "maintenance"
        self._set_header(render_sub_header("Maintenance"))
        body = RichText()
        body.append(f"\n  {CHECK}  ", style="green")
        body.append(f"{message}\n")
        self._set_body(body)
        self._set_help(HELP_BACK, with_newline=False)
