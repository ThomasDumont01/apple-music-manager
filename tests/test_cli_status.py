"""Tests for music_manager/cli/status.py."""

import json
from pathlib import Path

from music_manager.cli.status import read_status


def test_read_status_returns_idle_when_missing(tmp_path: Path) -> None:
    """An absent status file yields the idle sentinel."""
    assert read_status(str(tmp_path / "missing.json")) == {"status": "idle"}


def test_read_status_returns_payload_when_present(tmp_path: Path) -> None:
    """A valid status file is parsed as-is."""
    payload = {"status": "running", "current": 1, "total": 3}
    path = tmp_path / "widget_status.json"
    path.write_text(json.dumps(payload))
    assert read_status(str(path)) == payload


def test_read_status_returns_idle_on_corruption(tmp_path: Path) -> None:
    """A non-JSON status file is treated as idle (rather than crashing)."""
    path = tmp_path / "widget_status.json"
    path.write_text("{ not json")
    assert read_status(str(path)) == {"status": "idle"}


def test_read_status_returns_idle_on_non_dict_payload(tmp_path: Path) -> None:
    """JSON arrays or scalars are also treated as idle."""
    path = tmp_path / "widget_status.json"
    path.write_text("[1, 2, 3]")
    assert read_status(str(path)) == {"status": "idle"}
