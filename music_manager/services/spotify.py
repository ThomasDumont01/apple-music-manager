"""Spotify Web API client — OAuth PKCE + read-only user playlists / liked tracks.

Used by the Übersicht widget to import a user's Spotify playlists and liked
tracks directly into Apple Music (via the existing ISRC pipeline). Read-only:
the module never modifies Spotify state.

Auth model: OAuth 2.0 with PKCE (no client secret). Tokens persisted in
``config.json`` (chmod 600 after write). Single-user "Development mode" app —
the client_id is public and may be overridden via env var ``MM_SPOTIFY_CLIENT_ID``
or a ``spotify_client_id`` entry in config.json.

All HTTP traffic goes through ``resolver.http_get`` to reuse the shared
``requests.Session`` connection pool.
"""

import base64
import hashlib
import os
import secrets
import threading
import time
import urllib.parse
from typing import Any

import requests

from music_manager.core.config import load_config, save_config
from music_manager.services.resolver import http_get

# ── Constants ────────────────────────────────────────────────────────────────

_SPOTIFY_AUTH_URL = "https://accounts.spotify.com/authorize"
_SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"  # noqa: S105
_SPOTIFY_API_BASE = "https://api.spotify.com/v1"

# Spotify Developer app — Dev mode, single user (Thomas). Public client_id is
# safe with PKCE flow. Override at runtime via env var or config.
_SPOTIFY_CLIENT_ID = ""  # set by Thomas after creating the dev app
_SPOTIFY_REDIRECT_URI = "http://127.0.0.1:8765/callback"
_SPOTIFY_SCOPES = (
    "playlist-read-private playlist-read-collaborative user-library-read"
)

_REQUEST_TIMEOUT = 15
_HEADERS = {"Accept": "application/json"}

# Circuit breaker mirrors resolver.py's Deezer breaker shape.
_CIRCUIT_BREAKER_THRESHOLD = 5
_CIRCUIT_BREAKER_COOLDOWN = 60
_consecutive_failures_sp = 0
_circuit_open_until_sp = 0.0

# LRU-bounded, thread-safe response cache (per process).
_SPOTIFY_CACHE: dict[str, dict | None] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_MAX_SIZE = 2000

# Special id used by the widget for the virtual "liked tracks" entry.
LIKED_TRACKS_ID = "liked"
LIKED_TRACKS_TITLE = "♥ Titres likés"


# ── Entry point — OAuth helpers ──────────────────────────────────────────────


def get_client_id() -> str:
    """Return the configured Spotify Client ID.

    Resolution order: env var ``MM_SPOTIFY_CLIENT_ID`` → ``spotify_client_id``
    in config.json → module-level ``_SPOTIFY_CLIENT_ID`` constant.
    """
    env_value = os.environ.get("MM_SPOTIFY_CLIENT_ID", "").strip()
    if env_value:
        return env_value
    cfg_value = str(load_config().get("spotify_client_id") or "").strip()
    return cfg_value or _SPOTIFY_CLIENT_ID


def pkce_verifier_challenge() -> tuple[str, str]:
    """Return ``(verifier, challenge)`` per RFC 7636.

    Verifier: 64-byte URL-safe random string (high entropy).
    Challenge: SHA-256 of verifier, base64url-encoded without padding.
    """
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_auth_url(state: str, code_challenge: str) -> str:
    """Build the Spotify authorize URL for the PKCE flow."""
    params = {
        "client_id": get_client_id(),
        "response_type": "code",
        "redirect_uri": _SPOTIFY_REDIRECT_URI,
        "scope": _SPOTIFY_SCOPES,
        "state": state,
        "code_challenge_method": "S256",
        "code_challenge": code_challenge,
    }
    return f"{_SPOTIFY_AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_code(code: str, code_verifier: str) -> dict:
    """Exchange an authorization code for access + refresh tokens.

    Raises ``RuntimeError`` on Spotify error (network bubbles up as ``requests``).
    """
    response = requests.post(
        _SPOTIFY_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": _SPOTIFY_REDIRECT_URI,
            "client_id": get_client_id(),
            "code_verifier": code_verifier,
        },
        timeout=_REQUEST_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"spotify_token_exchange_failed:{response.status_code}")
    return response.json()


def refresh_access_token(refresh_token: str) -> dict:
    """Refresh the access token. May return a rotated refresh_token."""
    response = requests.post(
        _SPOTIFY_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": get_client_id(),
        },
        timeout=_REQUEST_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"spotify_refresh_failed:{response.status_code}")
    return response.json()


# ── Token persistence ────────────────────────────────────────────────────────


def load_tokens() -> dict:
    """Return current persisted tokens. Empty strings if not authenticated."""
    config = load_config()
    return {
        "access_token": str(config.get("spotify_access_token") or ""),
        "refresh_token": str(config.get("spotify_refresh_token") or ""),
        "expiry": _coerce_float(config.get("spotify_token_expiry")),
    }


def _coerce_float(value: object) -> float:
    """Best-effort conversion to float. Returns 0.0 when value is unusable."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


def save_tokens(access_token: str, refresh_token: str, expires_in: int) -> None:
    """Persist tokens + computed expiry to config.json, then chmod 600."""
    from music_manager.core.config import CONFIG_PATH  # noqa: PLC0415

    expiry = time.time() + max(0, int(expires_in))
    save_config(
        {
            "spotify_access_token": access_token,
            "spotify_refresh_token": refresh_token,
            "spotify_token_expiry": expiry,
        }
    )
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass


def clear_tokens() -> None:
    """Remove all Spotify tokens from config (logout)."""
    save_config(
        {
            "spotify_access_token": "",
            "spotify_refresh_token": "",
            "spotify_token_expiry": 0.0,
        }
    )


def is_authenticated() -> bool:
    """True iff a refresh_token is persisted (access may be expired)."""
    return bool(load_tokens()["refresh_token"])


# ── API wrapper — auto-refresh + cache + circuit breaker ─────────────────────


def spotify_get(endpoint: str) -> dict | None:
    """Authenticated GET against ``api.spotify.com/v1``.

    - Auto-refreshes the access_token when it's about to expire (<60s).
    - On 401, retries once with a forced refresh (only if a refresh_token exists).
    - Circuit breaker: after 5 consecutive failures, skip calls for 60s.
    - Caches successful responses (LRU-bounded, thread-safe).
    """
    global _consecutive_failures_sp, _circuit_open_until_sp  # noqa: PLW0603

    with _CACHE_LOCK:
        if endpoint in _SPOTIFY_CACHE:
            return _SPOTIFY_CACHE[endpoint]
        if _consecutive_failures_sp >= _CIRCUIT_BREAKER_THRESHOLD:
            if time.time() < _circuit_open_until_sp:
                return None
            _consecutive_failures_sp = 0

    access = _ensure_fresh_access_token()
    if not access:
        return None

    url = f"{_SPOTIFY_API_BASE}{endpoint}"
    data = _do_request(url, access)
    if data is None and is_authenticated():
        access = _ensure_fresh_access_token(force_refresh=True)
        if access:
            data = _do_request(url, access)

    with _CACHE_LOCK:
        if data is None:
            _consecutive_failures_sp += 1
            _circuit_open_until_sp = time.time() + _CIRCUIT_BREAKER_COOLDOWN
        else:
            _consecutive_failures_sp = 0
            if len(_SPOTIFY_CACHE) >= _CACHE_MAX_SIZE:
                oldest = next(iter(_SPOTIFY_CACHE))
                del _SPOTIFY_CACHE[oldest]
            _SPOTIFY_CACHE[endpoint] = data
    return data


def clear_api_cache() -> None:
    """Clear the in-memory Spotify response cache."""
    with _CACHE_LOCK:
        _SPOTIFY_CACHE.clear()


# ── Endpoints métier ─────────────────────────────────────────────────────────


def fetch_user_playlists(max_playlists: int = 200) -> list[dict]:
    """Return playlists owned or followed by the user.

    Stable shape (mirrors ``search_deezer_playlists``)::

        [{"spotify_id": "...", "title": "...", "nb_tracks": N,
          "picture_url": "...", "creator": "..."}, ...]
    """
    playlists: list[dict] = []
    offset = 0
    page_size = 50
    while len(playlists) < max_playlists:
        page = spotify_get(f"/me/playlists?limit={page_size}&offset={offset}")
        if not page:
            break
        items = page.get("items") or []
        if not isinstance(items, list) or not items:
            break
        for item in items:
            if len(playlists) >= max_playlists:
                break
            if not isinstance(item, dict):
                continue
            playlists.append(_format_playlist(item))
        if not page.get("next"):
            break
        offset += page_size
    return playlists


def count_liked_tracks() -> int:
    """Return the total number of liked tracks for the user."""
    data = spotify_get("/me/tracks?limit=1")
    if not data:
        return 0
    return int(data.get("total") or 0)


def fetch_spotify_playlist_preview(
    playlist_id: str, max_tracks: int = 500
) -> dict:
    """Resolve a Spotify playlist into preview-ready track metadata.

    Output shape strictly matches Deezer's ``fetch_playlist_preview`` so the
    widget can render both via the same ``PlaylistPreview`` component.
    """
    if not playlist_id:
        return _empty_preview()

    encoded = urllib.parse.quote(playlist_id, safe="")
    meta = spotify_get(f"/playlists/{encoded}")
    if not meta:
        return _empty_preview()

    name = str(meta.get("name") or "")
    creator = str((meta.get("owner") or {}).get("display_name") or "")
    nb_tracks = int((meta.get("tracks") or {}).get("total") or 0)
    cover_url = _extract_image(meta.get("images") or [])

    tracks: list[dict] = []
    seen: set[str] = set()
    skipped = 0
    offset = 0
    page_size = 100
    fields = (
        "items(track(name,is_local,external_ids,preview_url,"
        "artists(name),album(images))),next"
    )
    encoded_fields = urllib.parse.quote(fields, safe="")

    while len(tracks) < max_tracks:
        page = spotify_get(
            f"/playlists/{encoded}/tracks"
            f"?limit={page_size}&offset={offset}&fields={encoded_fields}"
        )
        if not page:
            break
        items = page.get("items") or []
        if not isinstance(items, list) or not items:
            break
        for wrapper in items:
            if len(tracks) >= max_tracks:
                break
            entry, has_isrc = _build_spotify_track_entry(wrapper)
            if not has_isrc:
                skipped += 1
                continue
            if entry["isrc"] in seen:
                continue
            seen.add(entry["isrc"])
            tracks.append(entry)
        if not page.get("next"):
            break
        offset += page_size

    return {
        "name": name,
        "creator": creator,
        "nb_tracks": nb_tracks,
        "cover_url": cover_url,
        "tracks": tracks,
        "skipped_no_isrc": skipped,
    }


def fetch_liked_tracks(max_tracks: int = 500) -> dict:
    """Return the user's liked tracks in the same shape as a playlist preview.

    ``name`` is set to ``LIKED_TRACKS_TITLE`` so the widget renders it like
    any other entry. Paginated via ``/me/tracks?limit=50&offset=N``.
    """
    tracks: list[dict] = []
    seen: set[str] = set()
    skipped = 0
    offset = 0
    page_size = 50
    total = 0
    while len(tracks) < max_tracks:
        page = spotify_get(f"/me/tracks?limit={page_size}&offset={offset}")
        if not page:
            break
        total = max(total, int(page.get("total") or 0))
        items = page.get("items") or []
        if not isinstance(items, list) or not items:
            break
        for wrapper in items:
            if len(tracks) >= max_tracks:
                break
            entry, has_isrc = _build_spotify_track_entry(wrapper)
            if not has_isrc:
                skipped += 1
                continue
            if entry["isrc"] in seen:
                continue
            seen.add(entry["isrc"])
            tracks.append(entry)
        if not page.get("next"):
            break
        offset += page_size

    return {
        "name": LIKED_TRACKS_TITLE,
        "creator": "",
        "nb_tracks": total,
        "cover_url": "",
        "tracks": tracks,
        "skipped_no_isrc": skipped,
    }


# ── Private Functions ────────────────────────────────────────────────────────


def _ensure_fresh_access_token(force_refresh: bool = False) -> str:
    """Return a valid access token, refreshing transparently when needed."""
    tokens = load_tokens()
    refresh = tokens["refresh_token"]
    if not refresh:
        return ""
    access = tokens["access_token"]
    expiry = tokens["expiry"]
    if access and not force_refresh and time.time() < expiry - 60:
        return access
    try:
        payload = refresh_access_token(refresh)
    except (requests.ConnectionError, requests.Timeout, RuntimeError):
        _log("spotify_token_refresh", success=False)
        return ""
    new_access = str(payload.get("access_token") or "")
    new_refresh = str(payload.get("refresh_token") or refresh)
    expires_in = int(payload.get("expires_in") or 3600)
    if not new_access:
        return ""
    save_tokens(new_access, new_refresh, expires_in)
    _log("spotify_token_refresh", success=True)
    return new_access


def _do_request(url: str, access_token: str) -> dict | None:
    """Single HTTP GET with bearer token. Returns None on any failure."""
    try:
        response = http_get(
            url,
            headers={**_HEADERS, "Authorization": f"Bearer {access_token}"},
        )
    except (requests.ConnectionError, requests.Timeout):
        return None
    if response.status_code != 200:
        return None
    try:
        return response.json()
    except ValueError:
        return None


def _format_playlist(item: dict) -> dict:
    """Project a ``/me/playlists`` item onto the stable widget schema."""
    owner = item.get("owner") or {}
    images = item.get("images") or []
    tracks = item.get("tracks") or {}
    return {
        "spotify_id": str(item.get("id") or ""),
        "title": str(item.get("name") or ""),
        "nb_tracks": int(tracks.get("total") or 0),
        "picture_url": _extract_image(images),
        "creator": str(owner.get("display_name") or ""),
    }


def _extract_image(images: list[Any]) -> str:
    """Pick the first non-empty cover URL from a Spotify ``images`` list."""
    if not images or not isinstance(images, list):
        return ""
    for image in images:
        if isinstance(image, dict) and image.get("url"):
            return str(image["url"])
    return ""


def _build_spotify_track_entry(wrapper: Any) -> tuple[dict, bool]:
    """Project a ``items[].track`` item onto the preview track schema.

    Returns ``(entry, has_isrc)``. ``has_isrc`` is False for unavailable or
    local-file tracks so the caller can count them as skipped.
    """
    if not isinstance(wrapper, dict):
        return _empty_track(), False
    track = wrapper.get("track")
    if not isinstance(track, dict):
        return _empty_track(), False
    if track.get("is_local"):
        return _empty_track(), False
    isrc = str((track.get("external_ids") or {}).get("isrc") or "").strip().upper()
    if not isrc:
        return _empty_track(), False
    artists = track.get("artists") or []
    artist_name = ""
    if isinstance(artists, list) and artists:
        first = artists[0]
        if isinstance(first, dict):
            artist_name = str(first.get("name") or "")
    album = track.get("album") or {}
    cover_url = _extract_image(album.get("images") or [])
    entry = {
        "isrc": isrc,
        "title": str(track.get("name") or ""),
        "artist": artist_name,
        "cover_url": cover_url,
        "preview_url": str(track.get("preview_url") or ""),
    }
    return entry, True


def _empty_track() -> dict:
    return {
        "isrc": "",
        "title": "",
        "artist": "",
        "cover_url": "",
        "preview_url": "",
    }


def _empty_preview() -> dict:
    return {
        "name": "",
        "creator": "",
        "nb_tracks": 0,
        "cover_url": "",
        "tracks": [],
        "skipped_no_isrc": 0,
    }


def _log(action: str, **data: object) -> None:
    """Best-effort log via the central logger."""
    try:
        from music_manager.core.logger import log_event  # noqa: PLC0415

        log_event(action, **data)
    except Exception:  # noqa: BLE001
        pass
