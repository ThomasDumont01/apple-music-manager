"""Tests for music_manager.cli.import_cancel."""

import json

import pytest

from music_manager.cli import import_cancel


def test_writes_cancel_flag(
    tmp_path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "music"
    data_root.mkdir()
    monkeypatch.setattr(
        import_cancel, "load_config", lambda: {"data_root": str(data_root)}
    )
    # CONFIG_DIR is used by Paths for widget_cancel_path → patch via env-style.
    config_dir = tmp_path / "config"
    monkeypatch.setattr("music_manager.core.config.CONFIG_DIR", str(config_dir))
    exit_code = import_cancel.main([])
    assert exit_code == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"status": "ok"}
    flag = config_dir / ".widget_cancel"
    assert flag.exists()
    assert flag.read_text() == "1"


def test_silent_when_no_data_root(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(import_cancel, "load_config", lambda: {"data_root": ""})
    exit_code = import_cancel.main([])
    assert exit_code == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"status": "ok"}


def test_idempotent_multiple_calls(
    tmp_path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "music"
    data_root.mkdir()
    config_dir = tmp_path / "config"
    monkeypatch.setattr(
        import_cancel, "load_config", lambda: {"data_root": str(data_root)}
    )
    monkeypatch.setattr("music_manager.core.config.CONFIG_DIR", str(config_dir))
    import_cancel.main([])
    capsys.readouterr()
    import_cancel.main([])
    out = json.loads(capsys.readouterr().out)
    assert out == {"status": "ok"}
