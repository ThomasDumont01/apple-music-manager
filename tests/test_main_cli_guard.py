"""Argument handling before the Textual UI boots."""

from unittest.mock import patch

import pytest

from music_manager import __main__ as main_mod


@pytest.mark.parametrize("flag", ["--help", "-h", "--version", "-V"])
def test_help_and_version_never_boot_the_ui(flag: str, capsys: pytest.CaptureFixture) -> None:
    """`music-manager --help` must print, not open a full-screen TUI.

    Anything that wasn't an exact sub-command name used to fall through to
    the UI, so a typo or a plain --help left the user staring at the app
    with no idea why.
    """
    with patch.object(main_mod, "_run_ui") as run_ui:
        with pytest.raises(SystemExit) as exc:
            main_mod.main([flag])

    run_ui.assert_not_called()
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip()


def test_unknown_flag_reports_instead_of_launching(capsys: pytest.CaptureFixture) -> None:
    """An unknown option is a mistake, not a request to open the app."""
    with patch.object(main_mod, "_run_ui") as run_ui:
        with pytest.raises(SystemExit) as exc:
            main_mod.main(["--nope"])

    run_ui.assert_not_called()
    assert exc.value.code == 2
    assert "nope" in capsys.readouterr().err


def test_bare_invocation_still_opens_the_app() -> None:
    """No arguments is how the app is normally launched — keep that."""
    with patch.object(main_mod, "_run_ui") as run_ui:
        main_mod.main([])
    run_ui.assert_called_once()


def test_subcommand_still_dispatches() -> None:
    """A real sub-command keeps skipping the UI boot path entirely."""
    with (
        patch.object(main_mod, "_run_ui") as run_ui,
        patch("music_manager.cli.dispatch", return_value=0) as dispatch,
    ):
        with pytest.raises(SystemExit) as exc:
            main_mod.main(["import-status"])

    run_ui.assert_not_called()
    dispatch.assert_called_once_with(["import-status"])
    assert exc.value.code == 0
