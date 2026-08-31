"""`python -m music_manager install-widget` — install the Übersicht widget.

The widget JSX ships inside the package so it always matches the installed
app version. This copies it next to the user's other Übersicht widgets,
which is also where the cover cache lives (WebKit refuses `file://` URLs
pointing outside the widget's own folder).
"""

import argparse
import os
import shutil
import sys
from pathlib import Path

from music_manager.cli.home import _widget_covers_dir

# ── Constants ────────────────────────────────────────────────────────────────

_WIDGET_FILENAME = "music-manager.jsx"


# ── Entry point ──────────────────────────────────────────────────────────────


def main(args: list[str]) -> int:
    """Copy the bundled widget into the Übersicht widgets folder."""
    parser = argparse.ArgumentParser(prog="music_manager install-widget")
    parser.parse_args(args)

    source = bundled_widget_path()
    if not source.is_file():
        print(f"✗  Widget introuvable dans le paquet : {source}", file=sys.stderr)
        return 1

    widgets_dir = _widget_dir()
    if not os.path.isdir(widgets_dir):
        print(
            "✗  Dossier de widgets Übersicht introuvable.\n"
            "   Installe Übersicht (https://tracesof.net/uebersicht/), lance-le une "
            "fois, puis relance cette commande.",
            file=sys.stderr,
        )
        return 1

    target = Path(widgets_dir) / _WIDGET_FILENAME
    new_content = source.read_text(encoding="utf-8")

    # Never discard a widget the user edited by hand — but don't litter the
    # folder with backups when the content is already identical.
    if target.is_file():
        current = target.read_text(encoding="utf-8", errors="replace")
        if current == new_content:
            print(f"✓  Widget déjà à jour : {target}")
            return 0
        backup = target.with_suffix(".jsx.bak")
        shutil.copy2(target, backup)
        print(f"·  Version précédente sauvegardée : {backup}")

    target.write_text(new_content, encoding="utf-8")
    print(f"✓  Widget installé : {target}")
    print("   Übersicht le charge automatiquement (sinon : menu Übersicht → Refresh).")
    return 0


def bundled_widget_path() -> Path:
    """Path to the JSX shipped inside the package."""
    return Path(__file__).resolve().parent.parent / "widget" / _WIDGET_FILENAME


# ── Private Functions ────────────────────────────────────────────────────────


def _widget_dir() -> str:
    """Übersicht's widgets folder — the parent of the cover cache."""
    return os.path.dirname(_widget_covers_dir())
