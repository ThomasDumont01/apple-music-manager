"""Tests for the shared Deezer rate limiter in services/resolver.py."""

import threading
import time

from music_manager.services.resolver import _RateLimiter


def test_limiter_allows_a_full_burst_without_waiting() -> None:
    """Up to max_calls in one window must go through immediately."""
    limiter = _RateLimiter(max_calls=10, period=5.0)
    start = time.monotonic()
    for _ in range(10):
        limiter.acquire()
    assert time.monotonic() - start < 0.5


def test_limiter_blocks_the_call_over_budget() -> None:
    """The (max_calls + 1)th call waits for the window to slide."""
    limiter = _RateLimiter(max_calls=3, period=0.6)
    for _ in range(3):
        limiter.acquire()
    start = time.monotonic()
    limiter.acquire()
    assert time.monotonic() - start >= 0.4


def test_limiter_caps_the_rate_across_threads() -> None:
    """The budget is global, not per thread.

    Regression: the recommendation feed fans out over nested thread pools
    and fired ~420 Deezer calls in a 5 s window against a ~50 call quota.
    Deezer answered HTTP 200 with {"code": 4, "Quota limit exceeded"}, the
    circuit breaker opened, and every remaining section of the feed came
    back empty with no error reported to the user.
    """
    limiter = _RateLimiter(max_calls=8, period=1.0)
    stamps: list[float] = []
    lock = threading.Lock()
    barrier = threading.Barrier(16)

    def worker() -> None:
        barrier.wait()
        limiter.acquire()
        with lock:
            stamps.append(time.monotonic())

    threads = [threading.Thread(target=worker) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(stamps) == 16
    # No 1 s window may hold more than the 8-call budget.
    worst = max(sum(1 for other in stamps if now <= other < now + 1.0) for now in stamps)
    assert worst <= 8, f"{worst} calls in one window, budget is 8"
