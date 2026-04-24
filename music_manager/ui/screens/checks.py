"""Checks screen — verify dependencies and services at startup."""

import subprocess

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Static

from music_manager.core.checks import (
    check_apple_music,
    check_brew,
    check_dependencies,
)
from music_manager.services.health import check_deezer, check_itunes, check_youtube
from music_manager.ui.styles import BLUE, CHECK, CROSS, WARN
from music_manager.ui.text import (
    CHECKS_APPLE_LABEL,
    CHECKS_BREW_INSTALL,
    CHECKS_BREW_PROMPT,
    CHECKS_DEEZER_LABEL,
    CHECKS_DEPS_LABEL,
    CHECKS_ERROR_APPLE,
    CHECKS_ERROR_NO_BREW,
    CHECKS_ITUNES_LABEL,
    CHECKS_TITLE,
    CHECKS_YOUTUBE_LABEL,
    HELP_CHECKS,
    HELP_CHECKS_BREW,
    HELP_CHECKS_ERROR,
)

# ── Screen ──────────────────────────────────────────────────────────────────


class ChecksScreen(Screen):
    """Display startup checks one by one."""

    DEFAULT_CSS = """
    ChecksScreen { layout: vertical; overflow-y: auto; }
    """

    BINDINGS = [
        Binding("enter", "continue", "Continue", show=False),
        Binding("escape", "quit", "Quit", show=False),
    ]

    def __init__(self, first_launch: bool = False) -> None:
        super().__init__()
        self._first_launch = first_launch
        self._lines: list[Text] = []
        self._state = "running"  # running, done, brew_prompt, error, update_prompt
        self._update_dmg_url = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="menu-content"):
            yield Static("", id="menu-header")
            yield Static("", id="menu-body")
            yield Static("", id="menu-help")

    def on_mount(self) -> None:
        """Start checks in background."""
        self._set_header(Text(CHECKS_TITLE, style=f"bold {BLUE}"))
        self._set_help("")
        self._run_checks()

    # ── Widget updaters ────────────────────────────────────────────────────

    def _set_header(self, content) -> None:
        self.query_one("#menu-header", Static).update(content)

    def _set_body(self, content) -> None:
        self.query_one("#menu-body", Static).update(content)

    def _set_help(self, text: str) -> None:
        from music_manager.ui.render import render_help  # noqa: PLC0415

        self.query_one("#menu-help", Static).update(render_help(text, with_newline=False))

    def _refresh_body(self) -> None:
        body = Text()
        for line in self._lines:
            body.append_text(line)
            body.append("\n")
        self._set_body(body)

    def _add_ok(self, label: str) -> None:
        line = Text()
        line.append(f"  {CHECK}  ", style="green")
        line.append(label)
        self._lines.append(line)
        self._refresh_body()

    def _add_warn(self, label: str, detail: str = "") -> None:
        line = Text()
        line.append(f"  {WARN}  ", style="yellow")
        line.append(label)
        if detail:
            line.append(f"  ({detail})", style="dim")
        self._lines.append(line)
        self._refresh_body()

    def _add_fail(self, label: str, detail: str = "") -> None:
        line = Text()
        line.append(f"  {CROSS}  ", style="red")
        line.append(label)
        if detail:
            line.append(f"  ({detail})", style="dim")
        self._lines.append(line)
        self._refresh_body()

    # ── Checks worker ──────────────────────────────────────────────────────

    @work(thread=True)
    def _run_checks(self) -> None:
        """Run all checks sequentially in background thread."""
        # Dependencies
        missing = check_dependencies()
        if missing:
            self.app.call_from_thread(self._on_deps_missing, missing)
            return
        self.app.call_from_thread(self._add_ok, CHECKS_DEPS_LABEL)

        # Apple Music
        if not check_apple_music():
            self.app.call_from_thread(self._on_apple_fail)
            return
        self.app.call_from_thread(self._add_ok, CHECKS_APPLE_LABEL)

        # APIs + update check in parallel
        from concurrent.futures import Future, ThreadPoolExecutor  # noqa: PLC0415

        from music_manager.services.version import check_for_update  # noqa: PLC0415

        with ThreadPoolExecutor(max_workers=4) as pool:
            f_deezer: Future[bool] = pool.submit(check_deezer)
            f_youtube: Future[bool] = pool.submit(check_youtube)
            f_itunes: Future[bool] = pool.submit(check_itunes)
            f_update: Future[tuple] = pool.submit(check_for_update)

            deezer_ok = f_deezer.result()
            if deezer_ok:
                self.app.call_from_thread(self._add_ok, CHECKS_DEEZER_LABEL)
            else:
                self.app.call_from_thread(self._add_warn, CHECKS_DEEZER_LABEL, "indisponible")

            youtube_ok = f_youtube.result()
            if youtube_ok:
                self.app.call_from_thread(self._add_ok, CHECKS_YOUTUBE_LABEL)
            else:
                self.app.call_from_thread(self._add_warn, CHECKS_YOUTUBE_LABEL, "indisponible")

            itunes_ok = f_itunes.result()
            if itunes_ok:
                self.app.call_from_thread(self._add_ok, CHECKS_ITUNES_LABEL)
            else:
                self.app.call_from_thread(self._add_warn, CHECKS_ITUNES_LABEL, "indisponible")

            has_update, latest, dmg_url = f_update.result()

        # Store results on app
        self.app.deezer_ok = deezer_ok  # type: ignore[attr-defined]
        self.app.youtube_ok = youtube_ok  # type: ignore[attr-defined]

        if has_update and dmg_url:
            self.app.call_from_thread(self._on_update_available, latest, dmg_url)
        else:
            self.app.call_from_thread(self._on_checks_done)

    def _on_deps_missing(self, missing: list[str]) -> None:
        """Handle missing dependencies."""
        for dep in missing:
            self._add_fail(dep, "manquant")

        if check_brew():
            self._state = "brew_prompt"
            self._missing_deps = missing
            line = Text()
            line.append(f"\n  {CHECKS_BREW_PROMPT}", style=f"bold {BLUE}")
            self._lines.append(line)
            self._refresh_body()
            self._set_help(HELP_CHECKS_BREW)
        else:
            self._state = "error"
            line = Text()
            line.append(f"\n  {CHECKS_ERROR_NO_BREW}", style="red")
            self._lines.append(line)
            self._refresh_body()
            self._set_help(HELP_CHECKS_ERROR)

    def _on_apple_fail(self) -> None:
        """Apple Music not responding."""
        self._add_fail(CHECKS_APPLE_LABEL)
        self._state = "error"
        line = Text()
        line.append(f"\n  {CHECKS_ERROR_APPLE}", style="red")
        self._lines.append(line)
        self._refresh_body()
        self._set_help(HELP_CHECKS_ERROR)

    def _on_checks_done(self) -> None:
        """All checks passed."""
        self._state = "done"
        self._set_help(HELP_CHECKS)

    def _on_update_available(self, version: str, dmg_url: str) -> None:
        """Update available — prompt user to download."""
        self._update_dmg_url = dmg_url
        self._state = "update_prompt"
        self._add_warn("Mise à jour", f"v{version} disponible")
        self._set_help("⏎  télécharger la mise à jour    esc  ignorer")

    @work(thread=True)
    def _download_update(self) -> None:
        """Download and open DMG in background."""
        from music_manager.services.version import download_and_install  # noqa: PLC0415

        self.app.call_from_thread(self._set_help, "  téléchargement en cours...")
        success = download_and_install(self._update_dmg_url)
        if success:
            self.app.call_from_thread(self._on_update_downloaded)
        else:
            self.app.call_from_thread(self._on_update_failed)

    def _on_update_downloaded(self) -> None:
        """DMG downloaded and opened — tell user to install."""
        self._add_ok("Mise à jour téléchargée")
        line = Text()
        line.append(
            "\n  L'installeur s'est ouvert."
            "\n  Double-cliquez sur « Installer Music Manager » pour mettre à jour."
            "\n  L'app va se fermer.",
            style="dim",
        )
        self._lines.append(line)
        self._refresh_body()
        self._state = "update_done"
        self._set_help("⏎  fermer l'app")

    def _on_update_failed(self) -> None:
        """Update download failed — continue normally."""
        self._add_warn("Mise à jour", "échec du téléchargement")
        self._state = "done"
        self._set_help(HELP_CHECKS)

    # ── Actions ────────────────────────────────────────────────────────────

    def action_continue(self) -> None:
        """Enter key."""
        if self._state == "done":
            self.app.on_checks_done(self._first_launch)  # type: ignore[attr-defined]
        elif self._state == "brew_prompt":
            self._brew_install()
        elif self._state == "update_prompt":
            self._download_update()
        elif self._state == "update_done":
            self.app.exit()

    def action_quit(self) -> None:
        """Escape key."""
        if self._state in ("error", "brew_prompt"):
            self.app.exit()
        elif self._state == "update_prompt":
            # User ignores update — continue normally
            self._state = "done"
            self._set_help(HELP_CHECKS)
            self._on_checks_done()

    @work(thread=True)
    def _brew_install(self) -> None:
        """Install missing deps via brew."""
        self.app.call_from_thread(self._set_help, "")
        line = Text()
        line.append(f"  {CHECKS_BREW_INSTALL}", style="dim")
        self._lines.append(line)
        self.app.call_from_thread(self._refresh_body)

        try:
            subprocess.run(
                ["brew", "install", *self._missing_deps],
                timeout=600,
                check=True,
                capture_output=True,
            )
            # Blank line then show installed deps (keep original ✗ visible)
            self._lines.append(Text())
            for dep in self._missing_deps:
                self.app.call_from_thread(self._add_ok, dep)
            # Continue checks (Apple Music, APIs)
            self._run_remaining_checks()
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            self.app.call_from_thread(self._on_brew_fail)

    def _run_remaining_checks(self) -> None:
        """Continue with Apple Music and API checks after brew install."""
        from concurrent.futures import Future, ThreadPoolExecutor  # noqa: PLC0415

        if not check_apple_music():
            self.app.call_from_thread(self._on_apple_fail)
            return
        self.app.call_from_thread(self._add_ok, CHECKS_APPLE_LABEL)

        with ThreadPoolExecutor(max_workers=3) as pool:
            f_deezer: Future[bool] = pool.submit(check_deezer)
            f_youtube: Future[bool] = pool.submit(check_youtube)
            f_itunes: Future[bool] = pool.submit(check_itunes)

            deezer_ok = f_deezer.result()
            if deezer_ok:
                self.app.call_from_thread(self._add_ok, CHECKS_DEEZER_LABEL)
            else:
                self.app.call_from_thread(self._add_warn, CHECKS_DEEZER_LABEL, "indisponible")

            youtube_ok = f_youtube.result()
            if youtube_ok:
                self.app.call_from_thread(self._add_ok, CHECKS_YOUTUBE_LABEL)
            else:
                self.app.call_from_thread(self._add_warn, CHECKS_YOUTUBE_LABEL, "indisponible")

            itunes_ok = f_itunes.result()
            if itunes_ok:
                self.app.call_from_thread(self._add_ok, CHECKS_ITUNES_LABEL)
            else:
                self.app.call_from_thread(self._add_warn, CHECKS_ITUNES_LABEL, "indisponible")

        self.app.deezer_ok = deezer_ok  # type: ignore[attr-defined]
        self.app.youtube_ok = youtube_ok  # type: ignore[attr-defined]
        self.app.call_from_thread(self._on_checks_done)

    def _on_brew_fail(self) -> None:
        """Brew install failed."""
        self._state = "error"
        line = Text()
        line.append("\n  Installation échouée.", style="red")
        self._lines.append(line)
        self._refresh_body()
        self._set_help(HELP_CHECKS_ERROR)
