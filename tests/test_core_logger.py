"""Tests for core/logger.py."""

import json
from pathlib import Path

from music_manager.core.logger import init_logger, log_event


def test_log_event_writes_jsonl(tmp_path: Path) -> None:
    """log_event appends a JSON line to the log file."""
    log_path = str(tmp_path / "logs.jsonl")
    init_logger(log_path)

    log_event("test_action", key="value")

    with open(log_path) as file:
        line = json.loads(file.readline())
    assert line["action"] == "test_action"
    assert line["key"] == "value"
    assert "ts" in line


def test_log_event_silent_when_not_initialized() -> None:
    """No crash if logger not initialized."""
    init_logger("")
    log_event("should_not_crash")
