"""Welcome screen — first-launch feature tour.

Shown once before checks on the very first launch.
Explains what the app does and how it works.
"""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Static

from music_manager.ui.styles import BLUE, CHECK, CROSS


class WelcomeScreen(Screen):
    """One-time welcome screen with feature tour."""

    DEFAULT_CSS = """
    WelcomeScreen { layout: vertical; overflow-y: auto; }
    """

    BINDINGS = [
        Binding("enter", "continue", "Commencer"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="menu-content"):
            yield Static("", id="menu-header")
            yield Static("", id="menu-body")
            yield Static("", id="menu-help")

    def on_mount(self) -> None:
        """Render the welcome content."""
        from rich.text import Text  # noqa: PLC0415

        # Header — same style as all other screens
        self.query_one("#menu-header", Static).update(Text("Music Manager", style=f"bold {BLUE}"))

        # Body
        body = Text()
        body.append("\nImporte ta musique dans Apple Music avec\n")
        body.append("pochettes HD et audio en qualité officielle.\n\n")

        features = [
            ("♪", "Importer depuis un CSV Spotify/Deezer"),
            ("♫", "Pochettes HD 3000×3000 automatiques"),
            (CHECK, "Correction des métadonnées en un clic"),
            (CROSS, "Détection et suppression des doublons"),
        ]
        for symbol, desc in features:
            body.append(f"  {symbol}  ", style=f"bold {BLUE}")
            body.append(f"{desc}\n")

        body.append("\n")
        body.append("Comment ça marche :\n", style="bold")
        steps = [
            "On vérifie que tout est prêt",
            "On scanne ta bibliothèque Apple Music",
            "Tu déposes tes CSV dans le dossier dédié",
            "Music Manager fait le reste",
        ]
        for i, step in enumerate(steps, 1):
            body.append(f"  {i}. {step}\n", style="dim")

        self.query_one("#menu-body", Static).update(body)

        # Help bar — same position as all other screens
        self.query_one("#menu-help", Static).update(Text("\n⏎  Commencer", style=f"bold {BLUE}"))

    def action_continue(self) -> None:
        """Proceed to checks screen."""
        from music_manager.ui.screens.checks import ChecksScreen  # noqa: PLC0415

        self.app.switch_screen(ChecksScreen(first_launch=True))
