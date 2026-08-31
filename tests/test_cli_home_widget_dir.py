"""Übersicht widget-folder discovery for the cover cache."""

import plistlib
from unittest.mock import MagicMock, patch

from music_manager.cli import home

_PATCH = "music_manager.cli.home"


def _prefs(url: str) -> bytes:
    """Build Übersicht's prefs the way macOS stores them.

    ``widgetDirectory`` is an NSKeyedArchiver-encoded NSURL nested inside the
    domain plist, not a plain path string.
    """
    archived = plistlib.dumps(
        {
            "$version": 100000,
            "$archiver": "NSKeyedArchiver",
            "$top": {"root": plistlib.UID(1)},
            "$objects": ["$null", {"NS.relative": plistlib.UID(2)}, url],
        },
        fmt=plistlib.FMT_BINARY,
    )
    return plistlib.dumps({"widgetDirectory": archived}, fmt=plistlib.FMT_BINARY)


def _run(stdout: bytes) -> MagicMock:
    result = MagicMock()
    result.stdout = stdout
    return result


def test_discovers_a_relocated_widget_folder() -> None:
    """A user who moved their widgets folder still gets covers.

    Covers must sit next to the widget JSX: WebKit blocks file:// URLs
    pointing anywhere else, so a wrong folder means artwork silently missing.
    """
    prefs = _prefs("file:///Users/someone/Dropbox/Ubersicht/widgets/")
    with patch(f"{_PATCH}.subprocess.run", return_value=_run(prefs)):
        home._ubersicht_widget_dir.cache_clear()
        assert home._ubersicht_widget_dir() == "/Users/someone/Dropbox/Ubersicht/widgets"
    home._ubersicht_widget_dir.cache_clear()


def test_percent_escapes_are_decoded() -> None:
    """A folder with a space arrives percent-encoded in the URL."""
    prefs = _prefs("file:///Users/someone/Application%20Support/widgets/")
    with patch(f"{_PATCH}.subprocess.run", return_value=_run(prefs)):
        home._ubersicht_widget_dir.cache_clear()
        assert home._ubersicht_widget_dir() == "/Users/someone/Application Support/widgets"
    home._ubersicht_widget_dir.cache_clear()


def test_missing_prefs_fall_back_to_empty() -> None:
    """Übersicht not installed → no discovery, and no crash."""
    with patch(f"{_PATCH}.subprocess.run", return_value=_run(b"")):
        home._ubersicht_widget_dir.cache_clear()
        assert home._ubersicht_widget_dir() == ""
    home._ubersicht_widget_dir.cache_clear()


def test_defaults_failure_is_swallowed() -> None:
    """`defaults` missing or timing out must never break the home command."""
    with patch(f"{_PATCH}.subprocess.run", side_effect=OSError("boom")):
        home._ubersicht_widget_dir.cache_clear()
        assert home._ubersicht_widget_dir() == ""
    home._ubersicht_widget_dir.cache_clear()


def test_env_override_wins_over_discovery() -> None:
    """An explicit env var is the escape hatch for unusual setups."""
    with patch.dict("os.environ", {"MUSIC_MANAGER_WIDGET_ASSETS_DIR": "/tmp/custom"}):
        with patch(f"{_PATCH}._ubersicht_widget_dir", return_value="/ignored"):
            assert home._widget_covers_dir() == "/tmp/custom"


def test_falls_back_to_the_standard_ubersicht_folder() -> None:
    """No env var and no discovery → the folder Übersicht ships with."""
    with patch.dict("os.environ", {}, clear=True):
        with patch(f"{_PATCH}._ubersicht_widget_dir", return_value=""):
            assert home._widget_covers_dir().endswith("Übersicht/widgets/music-manager.assets")
