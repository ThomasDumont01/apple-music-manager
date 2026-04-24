"""Version check + auto-update — compare local version against GitHub Releases.

Non-blocking check at startup. Download + install on user confirmation.
"""

import os
import subprocess

import requests

import music_manager

# ── Config ──────────────────────────────────────────────────────────────────

GITHUB_OWNER = "ThomasDumont01"
GITHUB_REPO = "apple-music-manager"

_CHECK_TIMEOUT = 3  # seconds — fast, non-blocking
_DOWNLOAD_TIMEOUT = 120  # seconds — DMG download


# ── Entry point ──────────────────────────────────────────────────────────────


def check_for_update() -> tuple[bool, str, str]:
    """Check GitHub for a newer release.

    Returns (has_update, latest_version, dmg_url).
    Silent on any failure — returns (False, "", "") if unreachable.
    """
    try:
        response = requests.get(
            f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest",
            timeout=_CHECK_TIMEOUT,
            headers={"Accept": "application/vnd.github.v3+json"},
        )
        if response.status_code != 200:
            return False, "", ""

        data = response.json()
        tag = data.get("tag_name", "")
        latest = tag.lstrip("v")  # "v1.0.1" → "1.0.1"

        if not latest or not _is_newer(latest, music_manager.__version__):
            return False, "", ""

        # Find the .dmg asset in the release
        dmg_url = ""
        for asset in data.get("assets", []):
            if asset.get("name", "").endswith(".dmg"):
                dmg_url = asset.get("browser_download_url", "")
                break

        return True, latest, dmg_url
    except (requests.ConnectionError, requests.Timeout, ValueError, KeyError):
        return False, "", ""


def download_and_install(dmg_url: str) -> bool:
    """Download DMG from GitHub and open it for the user.

    Downloads to /tmp/, opens the DMG (macOS mounts it automatically),
    then the user double-clicks the installer inside — same flow as first install.
    Returns True if DMG opened successfully.
    """
    if not dmg_url:
        return False

    import tempfile  # noqa: PLC0415

    dmg_path = os.path.join(tempfile.gettempdir(), "MusicManager-update.dmg")
    try:
        # Download DMG
        response = requests.get(dmg_url, timeout=_DOWNLOAD_TIMEOUT, stream=True)
        response.raise_for_status()
        with open(dmg_path, "wb") as file:
            for chunk in response.iter_content(chunk_size=8192):
                file.write(chunk)

        # Open DMG (macOS mounts and shows Finder window)
        subprocess.run(["open", dmg_path], check=True, timeout=10)
        return True
    except (requests.ConnectionError, requests.Timeout, requests.HTTPError, OSError):
        # Cleanup on failure
        try:
            os.remove(dmg_path)
        except OSError:
            pass
        return False


# ── Private Functions ────────────────────────────────────────────────────────


def _is_newer(remote: str, local: str) -> bool:
    """Compare semver strings. Returns True if remote > local."""
    try:
        remote_parts = [int(x) for x in remote.split(".")]
        local_parts = [int(x) for x in local.split(".")]
        return remote_parts > local_parts
    except (ValueError, AttributeError):
        return False
