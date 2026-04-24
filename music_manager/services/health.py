"""Network health checks — verify external API availability.

Separated from core/checks.py because these require ``requests`` (external lib).
Core modules must not import external dependencies.
"""

import requests

# ── Entry point ──────────────────────────────────────────────────────────────


def check_deezer() -> bool:
    """Return True if Deezer API is reachable."""
    try:
        response = requests.get(
            "https://api.deezer.com/search?q=test",
            timeout=5,
        )
        return response.status_code == 200
    except (requests.ConnectionError, requests.Timeout):
        return False


def check_youtube() -> bool:
    """Return True if YouTube is reachable."""
    try:
        response = requests.get("https://www.youtube.com", timeout=5)
        return response.status_code == 200
    except (requests.ConnectionError, requests.Timeout):
        return False


def check_itunes() -> bool:
    """Return True if iTunes Search API is reachable."""
    try:
        response = requests.get(
            "https://itunes.apple.com/search?term=test&limit=1",
            timeout=5,
        )
        return response.status_code == 200
    except (requests.ConnectionError, requests.Timeout):
        return False
