"""Deezer error-envelope handling — quota vs genuinely absent.

Deezer answers HTTP 200 for both "no such track" (code 800) and "you are over
quota" (code 4). Treating them the same is how a transient rate limit turned
valid tracks into permanent "not on Deezer" results for the rest of a run.
"""

from unittest.mock import MagicMock, patch

import pytest

from music_manager.services import resolver

_PATCH = "music_manager.services.resolver"


@pytest.fixture(autouse=True)
def _clean_resolver_state():
    """Each test starts with an empty cache and a closed circuit breaker."""
    resolver._API_CACHE.clear()
    resolver._consecutive_failures = 0
    resolver._circuit_open_until = 0.0
    yield
    resolver._API_CACHE.clear()
    resolver._consecutive_failures = 0
    resolver._circuit_open_until = 0.0


def _response(payload: dict) -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = payload
    return response


# ── Genuine "not found" ────────────────────────────────────────────────────


def test_not_found_is_cached() -> None:
    """code 800 means the track really isn't there → cache it, don't refetch."""
    payload = {"error": {"code": 800, "message": "no data"}}
    with (
        patch(f"{_PATCH}.time.sleep"),
        patch(f"{_PATCH}._SESSION.get", return_value=_response(payload)) as mock_get,
    ):
        assert resolver.deezer_get("/track/isrc:AAAA00000000") is None
        assert resolver.deezer_get("/track/isrc:AAAA00000000") is None

    assert mock_get.call_count == 1
    assert resolver._consecutive_failures == 0


# ── Quota / transient ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "payload",
    [
        {"error": {"code": 4, "message": "Quota limit exceeded"}},
        {"error": {"code": 700, "message": "Service busy, try again later"}},
        {"error": {"type": "Exception", "message": "Quota limit exceeded"}},
    ],
)
def test_quota_error_is_not_cached(payload: dict) -> None:
    """A rate limit must stay retryable instead of poisoning the cache."""
    with (
        patch(f"{_PATCH}.time.sleep"),
        patch(f"{_PATCH}._SESSION.get", return_value=_response(payload)) as mock_get,
    ):
        assert resolver.deezer_get("/track/isrc:BBBB00000000") is None
        assert resolver.deezer_get("/track/isrc:BBBB00000000") is None

    # Each call burns its full retry budget before giving up — what matters
    # is that the second one went back to the network at all.
    assert mock_get.call_count == 2 * resolver._DEEZER_TRANSIENT_ATTEMPTS
    assert "/track/isrc:BBBB00000000" not in resolver._API_CACHE


def test_quota_error_feeds_the_circuit_breaker() -> None:
    """Quota errors used to reset the counter, so the breaker never fired."""
    payload = {"error": {"code": 4, "message": "Quota limit exceeded"}}
    with (
        patch(f"{_PATCH}.time.sleep"),
        patch(f"{_PATCH}._SESSION.get", return_value=_response(payload)),
    ):
        for index in range(resolver._CIRCUIT_BREAKER_THRESHOLD):
            resolver.deezer_get(f"/track/isrc:CCCC0000000{index}")

    assert resolver._consecutive_failures >= resolver._CIRCUIT_BREAKER_THRESHOLD


def test_success_after_quota_resets_the_counter() -> None:
    """A working call closes the breaker again."""
    quota = _response({"error": {"code": 4, "message": "Quota limit exceeded"}})
    ok = _response({"id": 1, "title": "Bad Guy"})
    # The first call exhausts its retry budget on quota answers, the next
    # one succeeds.
    responses = [quota] * resolver._DEEZER_TRANSIENT_ATTEMPTS + [ok]
    with (
        patch(f"{_PATCH}.time.sleep"),
        patch(f"{_PATCH}._SESSION.get", side_effect=responses),
    ):
        resolver.deezer_get("/track/isrc:DDDD00000000")
        assert resolver.deezer_get("/track/isrc:DDDD00000001") == {"id": 1, "title": "Bad Guy"}

    assert resolver._consecutive_failures == 0


# ── Transient retry ────────────────────────────────────────────────────────


def test_quota_error_is_retried_and_recovers() -> None:
    """A quota answer is a "come back in a moment", not a verdict.

    Regression: the rate limiter lives in memory, so two CLI processes
    launched back to back each believed they owned the whole Deezer budget.
    The second one burnt through its quota, every call came back with
    code 4, and the widget rendered an empty radio.
    """
    quota = _response({"error": {"code": 4, "message": "Quota limit exceeded"}})
    found = _response({"id": 7, "title": "Papaoutai"})

    with (
        patch(f"{_PATCH}._SESSION.get", side_effect=[quota, found]) as mock_get,
        patch(f"{_PATCH}.time.sleep") as mock_sleep,
    ):
        result = resolver.deezer_get("/track/7")

    assert result is not None and result["title"] == "Papaoutai"
    assert mock_get.call_count == 2
    assert mock_sleep.called, "the retry must back off before trying again"
    assert resolver._consecutive_failures == 0


def test_quota_error_gives_up_after_the_retry_budget() -> None:
    """A sustained quota still fails — and counts once, not once per attempt."""
    quota = _response({"error": {"code": 4, "message": "Quota limit exceeded"}})

    with (
        patch(f"{_PATCH}._SESSION.get", return_value=quota) as mock_get,
        patch(f"{_PATCH}.time.sleep"),
    ):
        result = resolver.deezer_get("/track/7")

    assert result is None
    assert mock_get.call_count == resolver._DEEZER_TRANSIENT_ATTEMPTS
    assert resolver._consecutive_failures == 1


def test_not_found_is_never_retried() -> None:
    """code 800 is a verdict — retrying it just burns quota."""
    missing = _response({"error": {"code": 800, "message": "no data"}})

    with patch(f"{_PATCH}._SESSION.get", return_value=missing) as mock_get:
        assert resolver.deezer_get("/track/404") is None

    assert mock_get.call_count == 1
