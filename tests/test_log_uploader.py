"""Tests for services/log_uploader.py — anonymization + Cloudflare Worker upload."""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from music_manager.services.log_uploader import (
    _anonymize_logs,
    _ensure_install_id,
    upload_logs,
)

_P_UPLOADER = "music_manager.services.log_uploader"


# ── Anonymization ───────────────────────────────────────────────────────────


def test_anonymize_strips_home_path() -> None:
    """Absolute home paths replaced with ~/."""
    line = json.dumps({"file": "/Users/thomas/Music/song.m4a"})
    result = _anonymize_logs(line)
    parsed = json.loads(result)
    assert parsed["file"] == "~/Music/song.m4a"
    assert "/Users/" not in result


def test_anonymize_strips_various_usernames() -> None:
    """Works for any macOS username, not just 'thomas'."""
    line = json.dumps({"path": "/Users/alice/Documents/test.mp3"})
    result = _anonymize_logs(line)
    assert "~/Documents/test.mp3" in result
    assert "/Users/alice" not in result


def test_anonymize_preserves_metadata() -> None:
    """Music metadata (title, artist, ISRC) stays intact."""
    line = json.dumps({
        "action": "import_done",
        "title": "Bohemian Rhapsody",
        "artist": "Queen",
        "isrc": "GBAYE7500101",
        "duration_ms": 354000,
    })
    result = _anonymize_logs(line)
    parsed = json.loads(result)
    assert parsed["title"] == "Bohemian Rhapsody"
    assert parsed["artist"] == "Queen"
    assert parsed["isrc"] == "GBAYE7500101"
    assert parsed["duration_ms"] == 354000


def test_anonymize_multiline() -> None:
    """Handles multiple JSONL lines."""
    lines = (
        json.dumps({"file": "/Users/bob/Music/a.m4a"}) + "\n"
        + json.dumps({"file": "/Users/bob/Music/b.m4a"}) + "\n"
    )
    result = _anonymize_logs(lines)
    assert result.count("~/Music/") == 2
    assert "/Users/" not in result


# ── Install ID ──────────────────────────────────────────────────────────────


def test_ensure_install_id_creates_new(tmp_path: Path) -> None:
    """Generates UUID4 and saves to config when missing."""
    config: dict[str, object] = {"install_id": ""}
    with patch(f"{_P_UPLOADER}.save_config") as mock_save:
        result = _ensure_install_id(config)
    assert len(result) == 36  # UUID4 format: 8-4-4-4-12
    assert "-" in result
    mock_save.assert_called_once()


def test_ensure_install_id_reuses_existing() -> None:
    """Existing install_id returned without saving."""
    config: dict[str, object] = {"install_id": "existing-uuid-1234"}
    with patch(f"{_P_UPLOADER}.save_config") as mock_save:
        result = _ensure_install_id(config)
    assert result == "existing-uuid-1234"
    mock_save.assert_not_called()


# ── Upload logic ────────────────────────────────────────────────────────────


def test_upload_skip_consent_false(tmp_path: Path) -> None:
    """telemetry_consent=False → skip upload."""
    logs_path = str(tmp_path / "logs.jsonl")
    _write_log(logs_path, {"action": "test"})

    config: dict[str, object] = {
        "telemetry_consent": False,
        "last_log_upload": "",
        "install_id": "abc",
    }
    result = upload_logs(logs_path, config)
    assert result is False


def test_upload_skip_recent(tmp_path: Path) -> None:
    """Upload within last 14 days → skip."""
    from datetime import date  # noqa: PLC0415

    logs_path = str(tmp_path / "logs.jsonl")
    _write_log(logs_path, {"action": "test"})

    config: dict[str, object] = {
        "telemetry_consent": True,
        "last_log_upload": date.today().isoformat(),
        "install_id": "abc",
    }
    result = upload_logs(logs_path, config)
    assert result is False


def test_upload_skip_empty_logs(tmp_path: Path) -> None:
    """Empty log file → skip upload."""
    logs_path = str(tmp_path / "logs.jsonl")
    Path(logs_path).touch()

    config: dict[str, object] = {
        "telemetry_consent": True,
        "last_log_upload": "",
        "install_id": "abc",
    }
    result = upload_logs(logs_path, config)
    assert result is False


def test_upload_success_clears_logs(tmp_path: Path) -> None:
    """Successful upload → logs truncated + config updated."""
    logs_path = str(tmp_path / "logs.jsonl")
    _write_log(logs_path, {"action": "test", "file": "/Users/x/Music/a.m4a"})

    config: dict[str, object] = {
        "telemetry_consent": True,
        "last_log_upload": "",
        "install_id": "test-uuid",
    }

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"ok": True}

    with (
        patch("requests.post", return_value=mock_response) as mock_post,
        patch(f"{_P_UPLOADER}.save_config") as mock_save,
    ):
        result = upload_logs(logs_path, config)

    assert result is True
    # Logs file should be truncated
    assert os.path.getsize(logs_path) == 0
    # Config updated with upload date
    mock_save.assert_called_once()
    saved = mock_save.call_args[0][0]
    assert "last_log_upload" in saved
    # POST was called with correct headers
    call_kwargs = mock_post.call_args
    assert call_kwargs.kwargs["headers"]["X-User-Id"] == "test-uuid"
    # Body should be anonymized (no /Users/)
    assert "/Users/" not in call_kwargs.kwargs["data"]


def test_upload_failure_keeps_logs(tmp_path: Path) -> None:
    """HTTP error → logs preserved, returns False."""
    logs_path = str(tmp_path / "logs.jsonl")
    _write_log(logs_path, {"action": "test"})
    original_size = os.path.getsize(logs_path)

    config: dict[str, object] = {
        "telemetry_consent": True,
        "last_log_upload": "",
        "install_id": "test-uuid",
    }

    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.raise_for_status.side_effect = Exception("Server error")

    with (
        patch("requests.post", return_value=mock_response) as mock_post,
        patch(f"{_P_UPLOADER}.save_config") as mock_save,
    ):
        mock_post.return_value = mock_response
        result = upload_logs(logs_path, config)

    assert result is False
    # Logs should NOT be truncated
    assert os.path.getsize(logs_path) == original_size
    # Config should NOT be updated
    mock_save.assert_not_called()


def test_upload_network_error_never_crashes(tmp_path: Path) -> None:
    """Network exception → returns False, no crash."""
    logs_path = str(tmp_path / "logs.jsonl")
    _write_log(logs_path, {"action": "test"})

    config: dict[str, object] = {
        "telemetry_consent": True,
        "last_log_upload": "",
        "install_id": "test-uuid",
    }

    with patch("requests.post", side_effect=ConnectionError("DNS failed")):
        result = upload_logs(logs_path, config)

    assert result is False
    # Logs preserved
    assert os.path.getsize(logs_path) > 0


# ── Helpers ─────────────────────────────────────────────────────────────────


def _write_log(path: str, entry: dict) -> None:
    """Write a single log entry to a JSONL file."""
    with open(path, "w", encoding="utf-8") as file:
        file.write(json.dumps(entry, ensure_ascii=False) + "\n")
