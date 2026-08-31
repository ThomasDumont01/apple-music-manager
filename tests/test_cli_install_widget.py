"""`music-manager install-widget` — ship the Übersicht widget to its folder."""

from pathlib import Path
from unittest.mock import patch

from music_manager.cli import install_widget

_PATCH = "music_manager.cli.install_widget"


def test_bundled_widget_ships_with_the_package() -> None:
    """The JSX must be inside the package, or the wheel won't carry it."""
    source = install_widget.bundled_widget_path()
    assert source.is_file()
    assert source.read_text(encoding="utf-8").startswith("// music-manager.jsx")


def test_installs_into_the_ubersicht_folder(tmp_path: Path) -> None:
    """Copies the widget next to the other Übersicht widgets."""
    widgets = tmp_path / "widgets"
    widgets.mkdir()
    with patch(f"{_PATCH}._widget_dir", return_value=str(widgets)):
        assert install_widget.main([]) == 0

    installed = widgets / "music-manager.jsx"
    assert installed.is_file()
    assert installed.read_text(encoding="utf-8") == (
        install_widget.bundled_widget_path().read_text(encoding="utf-8")
    )


def test_missing_ubersicht_folder_is_not_an_error_crash(tmp_path: Path) -> None:
    """Übersicht not installed → clear failure, no traceback."""
    with patch(f"{_PATCH}._widget_dir", return_value=str(tmp_path / "nope")):
        assert install_widget.main([]) == 1


def test_existing_different_widget_is_backed_up(tmp_path: Path) -> None:
    """Never silently discard a widget the user edited by hand."""
    widgets = tmp_path / "widgets"
    widgets.mkdir()
    target = widgets / "music-manager.jsx"
    target.write_text("// mes modifs perso\n", encoding="utf-8")

    with patch(f"{_PATCH}._widget_dir", return_value=str(widgets)):
        assert install_widget.main([]) == 0

    backup = widgets / "music-manager.jsx.bak"
    assert backup.read_text(encoding="utf-8") == "// mes modifs perso\n"
    assert target.read_text(encoding="utf-8").startswith("// music-manager.jsx")


def test_reinstalling_the_same_content_writes_no_backup(tmp_path: Path) -> None:
    """An unchanged reinstall must not litter the folder with .bak files."""
    widgets = tmp_path / "widgets"
    widgets.mkdir()
    (widgets / "music-manager.jsx").write_text(
        install_widget.bundled_widget_path().read_text(encoding="utf-8"), encoding="utf-8"
    )

    with patch(f"{_PATCH}._widget_dir", return_value=str(widgets)):
        assert install_widget.main([]) == 0

    assert not (widgets / "music-manager.jsx.bak").exists()
