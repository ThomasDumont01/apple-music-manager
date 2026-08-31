"""Maintenance mixin — reset failed imports, move data, delete everything."""

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

        if key == "reset_failed":
            from music_manager.options.maintenance import reset_failed  # noqa: PLC0415

            count = reset_failed(self._tracks_store) if self._tracks_store else 0
            self._show_maintenance_result(f"{count} import(s) en échec réinitialisé(s)")
            log_event("maintenance_done", op=key, count=count)

        elif key == "move_data":
            self._show_move_data_input()

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

        action, _count = self._maintenance_pending

        if action == "delete_all":
            from music_manager.options.maintenance import delete_all  # noqa: PLC0415

            if self._paths:
                delete_all(self._paths.root)
            log_event("maintenance_done", op=action)
            self.app.exit()

    def _show_move_data_input(self) -> None:
        """Open Finder folder picker and move data."""
        import os  # noqa: PLC0415

        from music_manager.core.logger import log_event  # noqa: PLC0415
        from music_manager.core.setup import choose_data_root  # noqa: PLC0415

        with self.app.suspend():
            new_root = choose_data_root()

        if not new_root or not self._paths:
            self._show_maintenance_result("Déplacement annulé")
            return

        from music_manager.options.maintenance import move_data  # noqa: PLC0415

        old_root = self._paths.root
        ok = move_data(old_root, new_root)
        if ok:
            log_event("maintenance_done", op="move_data", new_root=new_root)
            # Remove leftover .data/ recreated by log_event
            import shutil  # noqa: PLC0415

            leftover = os.path.join(old_root, ".data")
            if os.path.isdir(leftover):
                shutil.rmtree(leftover, ignore_errors=True)
            self._restart_app()
        else:
            self._show_maintenance_result("Déplacement impossible (même dossier ?)")

    def _restart_app(self) -> None:
        """Exit and re-exec the app process."""
        import os  # noqa: PLC0415
        import sys  # noqa: PLC0415

        self.app.exit()
        os.execvp(sys.executable, [sys.executable, "-m", "music_manager"])

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
