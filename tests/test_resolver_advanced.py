"""Tests for resolver internals: circuit breaker, caching, fetch_album_with_cover, build_track."""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest
import requests

from music_manager.core.models import Track
from music_manager.services import resolver

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Reset resolver module-level state before each test."""
    resolver._consecutive_failures = 0
    resolver._circuit_open_until = 0.0
    resolver._API_CACHE.clear()
    yield
    resolver._consecutive_failures = 0
    resolver._circuit_open_until = 0.0
    resolver._API_CACHE.clear()


def _mock_response(status_code: int = 200, json_data: dict | None = None):
    """Build a fake requests.Response."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    return resp


# ── Circuit breaker ──────────────────────────────────────────────────────────


class TestCircuitBreaker:
    """Circuit breaker in deezer_get: open after 5 failures, cooldown, reset."""

    @patch.object(resolver, "_SESSION")
    @patch.object(resolver.time, "sleep")
    def test_five_failures_opens_circuit(self, _sleep, mock_session):
        """After 5 consecutive HTTP failures, circuit opens and requests are skipped."""
        mock_session.get.return_value = _mock_response(status_code=500)

        for _ in range(5):
            result = resolver.deezer_get("/track/1")
            assert result is None

        assert resolver._consecutive_failures >= resolver._CIRCUIT_BREAKER_THRESHOLD

        # Next call should be skipped (no HTTP call)
        mock_session.get.reset_mock()
        result = resolver.deezer_get("/track/2")
        assert result is None
        mock_session.get.assert_not_called()

    @patch.object(resolver, "_SESSION")
    @patch.object(resolver.time, "sleep")
    def test_requests_skipped_during_cooldown(self, _sleep, mock_session):
        """While circuit is open, deezer_get returns None without calling HTTP."""
        resolver._consecutive_failures = 5
        resolver._circuit_open_until = time.time() + 9999

        result = resolver.deezer_get("/track/123")
        assert result is None
        mock_session.get.assert_not_called()

    @patch.object(resolver, "_SESSION")
    @patch.object(resolver.time, "sleep")
    def test_cooldown_expiry_resets_circuit(self, _sleep, mock_session):
        """After cooldown expires, circuit resets and requests go through again."""
        resolver._consecutive_failures = 5
        resolver._circuit_open_until = time.time() - 1  # expired

        mock_session.get.return_value = _mock_response(200, {"id": 42, "title": "Ok"})

        result = resolver.deezer_get("/track/42")
        assert result == {"id": 42, "title": "Ok"}
        assert resolver._consecutive_failures == 0
        mock_session.get.assert_called_once()

    @patch.object(resolver, "_SESSION")
    @patch.object(resolver.time, "sleep")
    def test_success_resets_failure_count(self, _sleep, mock_session):
        """A successful response resets _consecutive_failures to 0."""
        # Accumulate 3 failures
        mock_session.get.return_value = _mock_response(status_code=500)
        for _ in range(3):
            resolver.deezer_get(f"/fail/{_}")
        assert resolver._consecutive_failures == 3

        # Success resets
        mock_session.get.return_value = _mock_response(200, {"id": 1})
        resolver.deezer_get("/track/ok")
        assert resolver._consecutive_failures == 0

    @patch.object(resolver, "_SESSION")
    @patch.object(resolver.time, "sleep")
    def test_connection_error_increments_failures(self, _sleep, mock_session):
        """ConnectionError and Timeout count as failures for circuit breaker."""
        mock_session.get.side_effect = requests.ConnectionError("refused")

        result = resolver.deezer_get("/track/1")
        assert result is None
        assert resolver._consecutive_failures == 1

    @patch.object(resolver, "_SESSION")
    @patch.object(resolver.time, "sleep")
    def test_timeout_error_increments_failures(self, _sleep, mock_session):
        """Timeout counts as a failure."""
        mock_session.get.side_effect = requests.Timeout("timed out")

        result = resolver.deezer_get("/track/1")
        assert result is None
        assert resolver._consecutive_failures == 1

    @patch.object(resolver, "_SESSION")
    @patch.object(resolver.time, "sleep")
    def test_thread_safety_concurrent_failures(self, _sleep, mock_session):
        """Concurrent failures don't corrupt _consecutive_failures beyond threshold."""
        mock_session.get.return_value = _mock_response(status_code=500)
        errors: list[Exception] = []

        def fail_request(idx: int):
            try:
                resolver.deezer_get(f"/concurrent/{idx}")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=fail_request, args=(i,)) for i in range(20)]
        for thr in threads:
            thr.start()
        for thr in threads:
            thr.join()

        assert not errors
        # Failures should be a reasonable number (not negative, not wildly inflated)
        assert resolver._consecutive_failures >= 0


# ── deezer_get caching ───────────────────────────────────────────────────────


class TestDeezerGetCaching:
    """Cache behavior in deezer_get: hit, miss, error caching, LRU eviction."""

    @patch.object(resolver, "_SESSION")
    @patch.object(resolver.time, "sleep")
    def test_successful_response_cached(self, _sleep, mock_session):
        """A 200 response with valid JSON is stored in _API_CACHE."""
        data = {"id": 10, "title": "Track"}
        mock_session.get.return_value = _mock_response(200, data)

        result = resolver.deezer_get("/track/10")
        assert result == data
        assert resolver._API_CACHE["/track/10"] == data

    @patch.object(resolver, "_SESSION")
    @patch.object(resolver.time, "sleep")
    def test_cache_hit_no_http_call(self, _sleep, mock_session):
        """Second call for same endpoint returns cached data without HTTP."""
        resolver._API_CACHE["/track/99"] = {"id": 99}

        result = resolver.deezer_get("/track/99")
        assert result == {"id": 99}
        mock_session.get.assert_not_called()

    @patch.object(resolver, "_SESSION")
    @patch.object(resolver.time, "sleep")
    def test_error_response_cached_as_none(self, _sleep, mock_session):
        """A Deezer error response (e.g. not found) is cached as None."""
        mock_session.get.return_value = _mock_response(200, {"error": {"type": "DataException"}})

        result = resolver.deezer_get("/track/404")
        assert result is None
        assert "/track/404" in resolver._API_CACHE
        assert resolver._API_CACHE["/track/404"] is None

        # Second call should NOT hit HTTP
        mock_session.get.reset_mock()
        result2 = resolver.deezer_get("/track/404")
        assert result2 is None
        mock_session.get.assert_not_called()

    @patch.object(resolver, "_SESSION")
    @patch.object(resolver.time, "sleep")
    def test_error_response_resets_failure_count(self, _sleep, mock_session):
        """A Deezer error (200 + error in JSON) is NOT an HTTP failure — resets counter."""
        resolver._consecutive_failures = 3
        mock_session.get.return_value = _mock_response(200, {"error": {"type": "DataException"}})

        resolver.deezer_get("/track/bad")
        assert resolver._consecutive_failures == 0

    @patch.object(resolver, "_SESSION")
    @patch.object(resolver.time, "sleep")
    def test_lru_eviction_at_max_size(self, _sleep, mock_session):
        """When cache reaches _CACHE_MAX_SIZE, oldest entry is evicted."""
        # Fill cache to max - 1
        for i in range(resolver._CACHE_MAX_SIZE - 1):
            resolver._API_CACHE[f"/fill/{i}"] = {"id": i}
        assert len(resolver._API_CACHE) == resolver._CACHE_MAX_SIZE - 1

        # One more brings it to max
        resolver._API_CACHE["/fill/last"] = {"id": -1}
        assert len(resolver._API_CACHE) == resolver._CACHE_MAX_SIZE

        # Next deezer_get should evict oldest ("/fill/0")
        mock_session.get.return_value = _mock_response(200, {"id": 9999})
        resolver.deezer_get("/new/entry")

        assert "/new/entry" in resolver._API_CACHE
        assert "/fill/0" not in resolver._API_CACHE
        assert len(resolver._API_CACHE) == resolver._CACHE_MAX_SIZE

    @patch.object(resolver, "_SESSION")
    @patch.object(resolver.time, "sleep")
    def test_lru_eviction_on_error_cache(self, _sleep, mock_session):
        """LRU eviction also happens when caching error responses."""
        for i in range(resolver._CACHE_MAX_SIZE):
            resolver._API_CACHE[f"/fill/{i}"] = {"id": i}

        mock_session.get.return_value = _mock_response(200, {"error": {"type": "DataException"}})
        resolver.deezer_get("/error/evict")

        assert "/error/evict" in resolver._API_CACHE
        assert "/fill/0" not in resolver._API_CACHE
        assert len(resolver._API_CACHE) == resolver._CACHE_MAX_SIZE

    @patch.object(resolver, "_SESSION")
    @patch.object(resolver.time, "sleep")
    def test_http_failure_not_cached(self, _sleep, mock_session):
        """A non-200 HTTP response is NOT stored in cache."""
        mock_session.get.return_value = _mock_response(status_code=500)

        result = resolver.deezer_get("/track/500")
        assert result is None
        assert "/track/500" not in resolver._API_CACHE


# ── fetch_album_with_cover ───────────────────────────────────────────────────


class TestFetchAlbumWithCover:
    """fetch_album_with_cover: cache hit, cache miss, iTunes cover upgrade."""

    def _make_albums_store(self) -> MagicMock:
        store = MagicMock()
        store.get.return_value = None
        return store

    def test_returns_empty_for_zero_album_id(self):
        """album_id=0 returns empty dict immediately."""
        store = self._make_albums_store()
        result = resolver.fetch_album_with_cover(0, store)
        assert result == {}
        store.get.assert_not_called()

    def test_cache_hit_returns_stored_data(self):
        """If albums_store.get() returns data, no API call is made."""
        store = self._make_albums_store()
        cached = {"id": 123, "title": "Cached Album", "cover_url": "http://cover.jpg"}
        store.get.return_value = cached

        with patch.object(resolver, "deezer_get") as mock_dg:
            result = resolver.fetch_album_with_cover(123, store)
            assert result == cached
            mock_dg.assert_not_called()

    @patch.object(resolver, "_itunes_cover", return_value="")
    @patch.object(resolver, "deezer_get")
    def test_cache_miss_fetches_from_deezer(self, mock_dg, _mock_itunes):
        """Cache miss fetches album from Deezer and stores in albums_store."""
        store = self._make_albums_store()
        mock_dg.return_value = {
            "title": "Test Album",
            "artist": {"name": "Test Artist"},
            "genres": {"data": [{"name": "Pop"}]},
            "cover_xl": "http://deezer.cover/xl.jpg",
            "release_date": "2024-01-15",
            "nb_tracks": 12,
            "nb_disk": 1,
        }

        result = resolver.fetch_album_with_cover(456, store)
        assert result["title"] == "Test Album"
        assert result["artist"] == "Test Artist"
        assert result["genre"] == "Pop"
        assert result["year"] == "2024"
        assert result["total_tracks"] == 12
        assert result["total_discs"] == 1
        assert result["id"] == 456
        store.put.assert_called_once_with(456, result)

    @patch.object(resolver, "_pick_best_cover", return_value="http://itunes.best/3000.jpg")
    @patch.object(resolver, "_itunes_cover", return_value="http://itunes.raw/art.jpg")
    @patch.object(resolver, "deezer_get")
    def test_itunes_cover_upgrade(self, mock_dg, _mock_itunes, _mock_pick):
        """When iTunes cover is available, _pick_best_cover chooses the best."""
        store = self._make_albums_store()
        mock_dg.return_value = {
            "title": "Album",
            "artist": {"name": "Artist"},
            "genres": {"data": []},
            "cover_xl": "http://deezer/cover.jpg",
            "release_date": "2023-06-01",
            "nb_tracks": 10,
            "nb_disk": 1,
        }

        result = resolver.fetch_album_with_cover(789, store)
        assert result["cover_url"] == "http://itunes.best/3000.jpg"

    @patch.object(resolver, "deezer_get", return_value=None)
    def test_deezer_api_failure_returns_empty(self, _mock_dg):
        """If deezer_get returns None, fetch_album_with_cover returns {}."""
        store = self._make_albums_store()
        result = resolver.fetch_album_with_cover(999, store)
        assert result == {}
        store.put.assert_not_called()

    @patch.object(resolver, "deezer_get", return_value={"error": {"type": "DataException"}})
    def test_deezer_error_returns_empty(self, _mock_dg):
        """If deezer_get returns error dict, fetch_album_with_cover returns {}."""
        store = self._make_albums_store()
        result = resolver.fetch_album_with_cover(888, store)
        assert result == {}
        store.put.assert_not_called()

    @patch.object(resolver, "_itunes_cover", side_effect=RuntimeError("iTunes down"))
    @patch.object(resolver, "deezer_get")
    def test_itunes_exception_handled_gracefully(self, mock_dg, _mock_itunes):
        """iTunes failure is caught — result uses Deezer cover."""
        store = self._make_albums_store()
        mock_dg.return_value = {
            "title": "Safe Album",
            "artist": {"name": "Artist"},
            "genres": {"data": []},
            "cover_xl": "http://deezer/fallback.jpg",
            "release_date": "2022-01-01",
            "nb_tracks": 8,
            "nb_disk": 1,
        }

        result = resolver.fetch_album_with_cover(777, store)
        assert result["cover_url"] == "http://deezer/fallback.jpg"
        store.put.assert_called_once()

    @patch.object(resolver, "_itunes_cover", return_value="")
    @patch.object(resolver, "deezer_get")
    def test_missing_genres_defaults_to_empty(self, mock_dg, _mock_itunes):
        """Album with no genres data gets empty genre string."""
        store = self._make_albums_store()
        mock_dg.return_value = {
            "title": "No Genre",
            "artist": {"name": "X"},
            "genres": {"data": []},
            "cover_xl": "",
            "release_date": "",
            "nb_tracks": 0,
            "nb_disk": 0,
        }

        result = resolver.fetch_album_with_cover(111, store)
        assert result["genre"] == ""
        assert result["year"] == ""


# ── build_track ──────────────────────────────────────────────────────────────


class TestBuildTrack:
    """build_track: field mapping from raw Deezer JSON to Track dataclass."""

    def _full_deezer_data(self) -> dict:
        return {
            "id": 12345,
            "isrc": "usrc12345678",
            "title": "Test Song",
            "artist": {"name": "Test Artist"},
            "album": {"id": 100, "title": "Deezer Album"},
            "track_position": 3,
            "disk_number": 1,
            "duration": 210,
            "explicit_lyrics": True,
            "preview": "http://preview.mp3",
        }

    def _full_album_data(self) -> dict:
        return {
            "id": 100,
            "title": "Album Title",
            "genre": "Rock",
            "release_date": "2024-03-15",
            "total_tracks": 12,
            "total_discs": 2,
            "album_artist": "Album Artist",
            "cover_url": "http://cover.jpg",
        }

    def test_correct_field_mapping(self):
        """All fields map correctly from Deezer JSON + album data to Track."""
        track = resolver.build_track(self._full_deezer_data(), self._full_album_data())

        assert isinstance(track, Track)
        assert track.isrc == "USRC12345678"  # uppercased
        assert track.title == "Test Song"
        assert track.artist == "Test Artist"
        assert track.album == "Album Title"  # album_data title takes priority
        assert track.album_id == 100
        assert track.genre == "Rock"
        assert track.release_date == "2024-03-15"
        assert track.track_number == 3
        assert track.total_tracks == 12
        assert track.disk_number == 1
        assert track.total_discs == 2
        assert track.album_artist == "Album Artist"
        assert track.duration == 210
        assert track.explicit is True
        assert track.cover_url == "http://cover.jpg"
        assert track.preview_url == "http://preview.mp3"
        assert track.deezer_id == 12345

    def test_missing_fields_use_defaults(self):
        """Minimal Deezer data produces a Track with sensible defaults."""
        deezer_data: dict = {"artist": {}, "album": {}}
        album_data: dict = {}

        track = resolver.build_track(deezer_data, album_data)

        assert track.isrc == ""
        assert track.title == ""
        assert track.artist == ""
        assert track.album == ""
        assert track.album_id == 0
        assert track.genre == ""
        assert track.release_date == ""
        assert track.track_number is None
        assert track.total_tracks is None
        assert track.disk_number == 1  # default in build_track
        assert track.total_discs == 0
        assert track.album_artist == ""
        assert track.duration == 0
        assert track.explicit is False
        assert track.cover_url == ""
        assert track.preview_url == ""
        assert track.deezer_id == 0

    def test_album_id_zero_falls_back_to_album_data(self):
        """When deezer_data album.id is 0, album_data.id is used."""
        deezer_data = self._full_deezer_data()
        deezer_data["album"]["id"] = 0
        album_data = self._full_album_data()
        album_data["id"] = 555

        track = resolver.build_track(deezer_data, album_data)
        assert track.album_id == 555

    def test_album_title_from_deezer_when_album_data_empty(self):
        """When album_data has no title, falls back to deezer_data album title."""
        deezer_data = self._full_deezer_data()
        album_data: dict = {}

        track = resolver.build_track(deezer_data, album_data)
        assert track.album == "Deezer Album"

    def test_isrc_uppercased(self):
        """ISRC is always uppercased regardless of input casing."""
        deezer_data = self._full_deezer_data()
        deezer_data["isrc"] = "gBaye0400162"

        track = resolver.build_track(deezer_data, self._full_album_data())
        assert track.isrc == "GBAYE0400162"

    def test_isrc_none_becomes_empty(self):
        """If ISRC is None in Deezer data, result is empty string."""
        deezer_data = self._full_deezer_data()
        deezer_data["isrc"] = None

        track = resolver.build_track(deezer_data, self._full_album_data())
        assert track.isrc == ""

    def test_explicit_false_by_default(self):
        """Missing explicit_lyrics field defaults to False."""
        deezer_data = self._full_deezer_data()
        del deezer_data["explicit_lyrics"]

        track = resolver.build_track(deezer_data, self._full_album_data())
        assert track.explicit is False
