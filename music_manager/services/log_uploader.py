"""Log uploader — anonymize and send logs to Cloudflare Worker.

Checks every app launch whether 14 days have elapsed since the last
upload. If so, reads logs.jsonl, strips personal paths, and POSTs
the content to a Cloudflare Worker endpoint that stores it in R2.
"""

import os
import re
import uuid
from datetime import date, timedelta

import requests

from music_manager.core.config import save_config
from music_manager.core.logger import log_event

# ── Constants ────────────────────────────────────────────────────────────────

_WORKER_URL = "https://music-manager-logs.thomas-music.workers.dev/upload"
_UPLOAD_INTERVAL_DAYS = 14
_TIMEOUT_SECONDS = 15

# Regex: /Users/<any_username>/ → ~/
_HOME_PATTERN = re.compile(r"/Users/\w+/")


# ── Entry point ──────────────────────────────────────────────────────────────


def upload_logs(logs_path: str, config: dict) -> bool:
    """Upload logs to analytics endpoint if interval has elapsed.

    Returns True on successful upload, False otherwise.
    Never raises — all errors are caught and logged silently.
    """
    try:
        return _do_upload(logs_path, config)
    except Exception as exc:  # noqa: BLE001
        log_event("log_upload_failed", error=str(exc))
        return False


# ── Private Functions ────────────────────────────────────────────────────────


def _do_upload(logs_path: str, config: dict) -> bool:
    """Core upload logic — may raise on network/IO errors."""
    # Check consent
    if config.get("telemetry_consent") is not True:
        return False

    # Check interval
    last_upload = str(config.get("last_log_upload", ""))
    if last_upload:
        last_date = date.fromisoformat(last_upload)
        if date.today() - last_date < timedelta(days=_UPLOAD_INTERVAL_DAYS):
            return False

    # Read logs
    if not logs_path or not os.path.isfile(logs_path):
        return False
    with open(logs_path, encoding="utf-8") as file:
        content = file.read()
    if not content.strip():
        return False

    # Anonymize
    anonymized = _anonymize_logs(content)

    # Ensure install ID
    install_id = _ensure_install_id(config)

    # Upload
    response = requests.post(
        _WORKER_URL,
        data=anonymized,
        headers={
            "Content-Type": "application/jsonl",
            "X-User-Id": install_id,
        },
        timeout=_TIMEOUT_SECONDS,
    )
    response.raise_for_status()

    # Success → truncate logs + update config
    with open(logs_path, "w", encoding="utf-8") as file:
        file.truncate(0)

    save_config({"last_log_upload": date.today().isoformat()})
    log_event("log_upload_done", lines=anonymized.count("\n"))
    return True


def _anonymize_logs(content: str) -> str:
    """Replace absolute home paths with ~/ for privacy."""
    return _HOME_PATTERN.sub("~/", content)


def _ensure_install_id(config: dict) -> str:
    """Return existing install_id or generate + save a new UUID4."""
    existing = str(config.get("install_id", ""))
    if existing:
        return existing
    new_id = str(uuid.uuid4())
    save_config({"install_id": new_id})
    return new_id
