"""Last.fm API wrapper — similar tracks, similar artists, tag top tracks.

Last.fm is used exclusively as a recommendation source: candidates are
later resolved against Deezer (ISRC) so the existing import pipeline can
fetch the audio.

Authentication: a free API key is required.
- Lookup order: env var ``LASTFM_API_KEY`` → ``config["lastfm_api_key"]``.
- Without a key, all helpers return an empty list and log a warning. The
  UI prompts the user before the first call.

Resilience:
- Shared in-memory LRU cache (2000 entries) keyed by method+params.
- Circuit breaker after 5 consecutive failures (60 s cooldown).
- HTTP layer reuses ``resolver.http_get`` for connection pooling.
"""

import json
import os
import threading
import time
import urllib.parse

from music_manager.core.config import load_config
from music_manager.core.logger import log_event
from music_manager.services.resolver import http_get

# ── Constants ────────────────────────────────────────────────────────────────

LASTFM_BASE = "https://ws.audioscrobbler.com/2.0/"

_CACHE_MAX = 2000
_CIRCUIT_THRESHOLD = 5
_CIRCUIT_COOLDOWN = 60.0
_REQUEST_TIMEOUT = 10

_CACHE: dict[str, dict | None] = {}
_CACHE_LOCK = threading.Lock()
_STATE_LOCK = threading.Lock()
_consecutive_failures = 0
_circuit_open_until = 0.0


# ── Entry point ──────────────────────────────────────────────────────────────


def get_api_key() -> str | None:
    """Return the configured Last.fm API key, or None if unset.

    Lookup order: ``LASTFM_API_KEY`` env var, then ``lastfm_api_key`` in the
    user config file.
    """
    env_key = os.environ.get("LASTFM_API_KEY", "").strip()
    if env_key:
        return env_key
    cfg = load_config()
    key = str(cfg.get("lastfm_api_key") or "").strip()
    return key or None


def get_similar_tracks(artist: str, track: str, *, limit: int = 50) -> list[dict]:
    """Return tracks similar to ``artist`` / ``track``.

    Each item: ``{"name", "artist", "mbid", "match", "playcount"}``.
    Empty list on any failure (network, auth, missing seed). Never raises.
    """
    if not artist or not track:
        return []
    data = _lastfm_get(
        "track.getsimilar",
        {"artist": artist, "track": track, "autocorrect": "1", "limit": str(limit)},
    )
    if not data:
        return []
    raw = _extract_list(data, "similartracks", "track")
    return [_normalize_track(item) for item in raw if item]


def get_top_tracks_by_tag(tag: str, *, limit: int = 50) -> list[dict]:
    """Return top tracks for a Last.fm tag (e.g. "chill", "indie")."""
    if not tag:
        return []
    data = _lastfm_get("tag.gettoptracks", {"tag": tag, "limit": str(limit)})
    if not data:
        return []
    raw = _extract_list(data, "tracks", "track")
    return [_normalize_track(item) for item in raw if item]


def get_chart_top_tracks(*, limit: int = 200) -> list[dict]:
    """Return the global Last.fm chart top tracks.

    Used as a discovery cold-start fallback when the user's library has
    no profile seeds yet. No ``match`` field on chart items, so it
    surfaces as ``match=0.0`` (skipped by the standard quality filter
    but accepted in cold-start when no other source has produced
    candidates).
    """
    data = _lastfm_get("chart.gettoptracks", {"limit": str(limit)})
    if not data:
        return []
    raw = _extract_list(data, "tracks", "track")
    return [_normalize_track(item) for item in raw if item]


def get_similar_artists(artist: str, *, limit: int = 10) -> list[dict]:
    """Return artists similar to ``artist`` (fallback when seeds run dry)."""
    if not artist:
        return []
    data = _lastfm_get(
        "artist.getsimilar",
        {"artist": artist, "autocorrect": "1", "limit": str(limit)},
    )
    if not data:
        return []
    raw = _extract_list(data, "similarartists", "artist")
    return [
        {
            "name": str(item.get("name") or "").strip(),
            "mbid": str(item.get("mbid") or "").strip(),
            "match": _to_float(item.get("match")),
        }
        for item in raw
        if item and item.get("name")
    ]


# ── Private Functions ────────────────────────────────────────────────────────


def _lastfm_get(method: str, params: dict[str, str]) -> dict | None:
    """Execute a Last.fm API call. Returns the parsed JSON dict, or None.

    Honors the API key, in-process LRU cache, and the circuit breaker. All
    transport errors are swallowed and logged.
    """
    api_key = get_api_key()
    if not api_key:
        log_event("lastfm_no_api_key", method=method)
        return None

    if _circuit_is_open():
        return None

    cache_key = _build_cache_key(method, params)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    query = {**params, "method": method, "api_key": api_key, "format": "json"}
    url = f"{LASTFM_BASE}?{urllib.parse.urlencode(query)}"
    start = time.monotonic()
    try:
        response = http_get(url, timeout=_REQUEST_TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        _record_failure()
        log_event("lastfm_request_failed", method=method, error=str(exc))
        return None

    duration_ms = int((time.monotonic() - start) * 1000)
    if response.status_code != 200:
        _record_failure()
        log_event(
            "lastfm_http_error",
            method=method,
            status=response.status_code,
            duration_ms=duration_ms,
        )
        return None

    try:
        data = response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        _record_failure()
        log_event("lastfm_invalid_json", method=method, error=str(exc))
        return None

    if isinstance(data, dict) and "error" in data:
        _record_failure()
        log_event(
            "lastfm_api_error",
            method=method,
            code=data.get("error"),
            message=str(data.get("message", "")),
        )
        return None

    _record_success()
    _cache_put(cache_key, data)
    log_event("lastfm_request", method=method, duration_ms=duration_ms)
    return data


def _normalize_track(item: dict) -> dict:
    """Coerce a Last.fm track payload into our flat shape."""
    artist_field = item.get("artist")
    if isinstance(artist_field, dict):
        artist_name = str(artist_field.get("name") or "").strip()
    else:
        artist_name = str(artist_field or "").strip()
    return {
        "name": str(item.get("name") or "").strip(),
        "artist": artist_name,
        "mbid": str(item.get("mbid") or "").strip(),
        "match": _to_float(item.get("match")),
        "playcount": _to_int(item.get("playcount")),
    }


def _to_int(value: object) -> int:
    """Coerce a possibly-stringy integer (Last.fm returns playcounts as strings)."""
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def _extract_list(data: dict, container_key: str, item_key: str) -> list[dict]:
    """Return the inner list, tolerating Last.fm's quirky single-item shape."""
    container = data.get(container_key) or {}
    if not isinstance(container, dict):
        return []
    items = container.get(item_key)
    if items is None:
        return []
    if isinstance(items, dict):
        return [items]
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    return []


def _to_float(value: object) -> float:
    """Coerce a possibly-stringy similarity score into a float (0 on failure)."""
    if value is None:
        return 0.0
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


def _build_cache_key(method: str, params: dict[str, str]) -> str:
    serialized = "&".join(f"{k}={params[k]}" for k in sorted(params))
    return f"{method}?{serialized}"


def _cache_get(key: str) -> dict | None:
    with _CACHE_LOCK:
        return _CACHE.get(key)


def _cache_put(key: str, value: dict | None) -> None:
    with _CACHE_LOCK:
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = value


def _circuit_is_open() -> bool:
    with _STATE_LOCK:
        global _circuit_open_until  # noqa: PLW0602
        return time.monotonic() < _circuit_open_until


def _record_failure() -> None:
    global _consecutive_failures, _circuit_open_until  # noqa: PLW0603
    with _STATE_LOCK:
        _consecutive_failures += 1
        if _consecutive_failures >= _CIRCUIT_THRESHOLD:
            _circuit_open_until = time.monotonic() + _CIRCUIT_COOLDOWN


def _record_success() -> None:
    global _consecutive_failures, _circuit_open_until  # noqa: PLW0603
    with _STATE_LOCK:
        _consecutive_failures = 0
        _circuit_open_until = 0.0


def _reset_state_for_tests() -> None:
    """Test-only: clear cache and circuit breaker between tests."""
    global _consecutive_failures, _circuit_open_until  # noqa: PLW0603
    with _CACHE_LOCK:
        _CACHE.clear()
    with _STATE_LOCK:
        _consecutive_failures = 0
        _circuit_open_until = 0.0
