"""Tests for services/recommendations_store.py."""

import json
from pathlib import Path

import pytest

from music_manager.services.recommendations_store import RecommendationsStore


@pytest.fixture
def store_path(tmp_path: Path) -> str:
    return str(tmp_path / "recommendations.json")


# ── Queries ─────────────────────────────────────────────────────────────────


def test_empty_store_has_no_active_or_blacklist(store_path: str) -> None:
    """A fresh store reports empty queries on everything."""
    store = RecommendationsStore(store_path)
    assert store.all_active() == {}
    assert store.all_blacklist() == {}
    assert store.stats() == {}
    assert store.is_active("ANY") is False
    assert store.is_blacklisted("ANY") is False


def test_is_active_and_blacklisted_case_insensitive(store_path: str) -> None:
    """Lookups normalize to uppercase — matches CLAUDE.md ISRC convention."""
    store = RecommendationsStore(store_path)
    store.add_active(
        {"isrc": "frabc1234567", "apple_id": "A1", "title": "T", "artist": "A"}
    )
    assert store.is_active("frabc1234567")
    assert store.is_active("FRABC1234567")
    assert not store.is_blacklisted("frabc1234567")


def test_empty_isrc_returns_false(store_path: str) -> None:
    """Empty ISRC inputs short-circuit instead of polluting the store."""
    store = RecommendationsStore(store_path)
    assert store.is_active("") is False
    assert store.is_blacklisted("") is False


# ── Mutations ───────────────────────────────────────────────────────────────


def test_add_active_persists_required_fields(store_path: str) -> None:
    """add_active stores the recommendation and stamps added_at."""
    store = RecommendationsStore(store_path)
    store.add_active(
        {"isrc": "X1", "apple_id": "AP1", "title": "Song", "artist": "Artist"}
    )
    entry = store.all_active()["X1"]
    assert entry["apple_id"] == "AP1"
    assert entry["title"] == "Song"
    assert entry["added_at"]  # ISO timestamp populated


def test_add_active_duplicate_isrc_is_noop(store_path: str) -> None:
    """A second add_active for the same ISRC keeps the first record."""
    store = RecommendationsStore(store_path)
    store.add_active(
        {"isrc": "X1", "apple_id": "AP1", "title": "First", "artist": "A"}
    )
    store.add_active(
        {"isrc": "X1", "apple_id": "AP_OTHER", "title": "Second", "artist": "B"}
    )
    entry = store.all_active()["X1"]
    assert entry["title"] == "First"
    assert entry["apple_id"] == "AP1"


def test_add_active_skips_when_isrc_missing(store_path: str) -> None:
    """An entry without an ISRC cannot be deduped → must be rejected."""
    store = RecommendationsStore(store_path)
    store.add_active({"isrc": "", "apple_id": "AP", "title": "T", "artist": "A"})
    assert store.all_active() == {}


def test_blacklist_removes_from_active(store_path: str) -> None:
    """Blacklisting an active ISRC moves it across — it is no longer active."""
    store = RecommendationsStore(store_path)
    store.add_active(
        {"isrc": "X1", "apple_id": "AP1", "title": "T", "artist": "A"}
    )
    store.blacklist("X1")
    assert not store.is_active("X1")
    assert store.is_blacklisted("X1")
    assert store.all_blacklist()["X1"]["title"] == "T"


def test_blacklist_preserves_title_artist_when_not_active(store_path: str) -> None:
    """Caller can supply title/artist when the ISRC was never active."""
    store = RecommendationsStore(store_path)
    store.blacklist("X1", title="Manually", artist="Added")
    entry = store.all_blacklist()["X1"]
    assert entry["title"] == "Manually"
    assert entry["artist"] == "Added"


def test_move_to_blacklist_counts_actually_moved(store_path: str) -> None:
    """move_to_blacklist reports how many ISRCs changed state."""
    store = RecommendationsStore(store_path)
    store.add_active({"isrc": "X1", "apple_id": "A", "title": "1", "artist": "a"})
    store.add_active({"isrc": "X2", "apple_id": "B", "title": "2", "artist": "b"})
    moved = store.move_to_blacklist({"X1", "X2", "UNKNOWN"})
    # X1 + X2 + UNKNOWN (new blacklist insert) → 3 changes
    assert moved == 3
    assert store.all_active() == {}
    assert set(store.all_blacklist().keys()) == {"X1", "X2", "UNKNOWN"}


def test_move_to_blacklist_ignores_empty_isrcs(store_path: str) -> None:
    """Empty strings and None-ish entries are skipped, not blacklisted."""
    store = RecommendationsStore(store_path)
    moved = store.move_to_blacklist({"", "X1"})
    assert moved == 1
    assert "" not in store.all_blacklist()


def test_move_to_blacklist_does_not_re_insert_known_blacklist(store_path: str) -> None:
    """An already-blacklisted ISRC that is not active doesn't count again."""
    store = RecommendationsStore(store_path)
    store.blacklist("X1")
    moved = store.move_to_blacklist({"X1"})
    assert moved == 0


# ── Persistence ─────────────────────────────────────────────────────────────


def test_save_then_reload_roundtrip(store_path: str) -> None:
    """A saved store reloads identical."""
    store = RecommendationsStore(store_path)
    store.add_active(
        {"isrc": "X1", "apple_id": "AP1", "title": "T", "artist": "A", "score": 0.9}
    )
    store.blacklist("X2", title="Removed", artist="ByUser")
    store.record_generation()
    store.save()

    reloaded = RecommendationsStore(store_path)
    assert reloaded.is_active("X1")
    assert reloaded.all_active()["X1"]["score"] == 0.9
    assert reloaded.is_blacklisted("X2")
    assert reloaded.stats()["generations"] == 1
    assert reloaded.stats()["last_run"]


def test_save_is_atomic(store_path: str) -> None:
    """Saved file is valid JSON with the documented top-level keys."""
    store = RecommendationsStore(store_path)
    store.add_active(
        {"isrc": "X1", "apple_id": "AP1", "title": "T", "artist": "A"}
    )
    store.save()

    raw = json.loads(Path(store_path).read_text())
    assert set(raw.keys()) == {"active", "blacklist", "stats"}


def test_save_skipped_when_clean(store_path: str) -> None:
    """A store with no mutations does not touch disk."""
    store = RecommendationsStore(store_path)
    store.save()
    assert not Path(store_path).exists()


def test_corrupt_or_missing_file_returns_empty_store(store_path: str) -> None:
    """An absent file is fine; a corrupt file is handled by core.io.load_json."""
    Path(store_path).write_text("{ this is not json")
    store = RecommendationsStore(store_path)
    assert store.all_active() == {}
    assert store.all_blacklist() == {}


# ── seed_quality (negative reinforcement) ───────────────────────────────────


def test_seed_quality_requires_min_samples(store_path: str) -> None:
    """A seed with only 1-2 observations is too noisy to judge — skip it."""
    store = RecommendationsStore(store_path)
    store.add_active(
        {"isrc": "X1", "apple_id": "A", "title": "T", "artist": "a", "seed_isrc": "SEED1"}
    )
    store.blacklist("X1")  # one blacklisted, total 1 — below min_samples=3
    assert store.seed_quality() == {}


def test_seed_quality_computes_blacklist_ratio(store_path: str) -> None:
    """A seed with mostly blacklisted picks is flagged for downscoring."""
    store = RecommendationsStore(store_path)
    # SEED_BAD: 1 active, 3 blacklisted → ratio 0.75
    store.add_active(
        {
            "isrc": "B1", "apple_id": "A1", "title": "T",
            "artist": "a", "seed_isrc": "SEED_BAD",
        }
    )
    for i in range(3):
        isrc = f"B{i + 2}"
        store.add_active(
            {
                "isrc": isrc, "apple_id": f"A{i + 2}", "title": "T",
                "artist": "a", "seed_isrc": "SEED_BAD",
            }
        )
        store.blacklist(isrc)

    # SEED_GOOD: 3 active, 0 blacklisted → ratio 0.0
    for i in range(3):
        store.add_active(
            {
                "isrc": f"G{i}",
                "apple_id": f"AG{i}",
                "title": "T",
                "artist": "g",
                "seed_isrc": "SEED_GOOD",
            }
        )

    quality = store.seed_quality()
    assert quality["SEED_BAD"] == pytest.approx(0.75)
    assert quality["SEED_GOOD"] == pytest.approx(0.0)


def test_blacklist_preserves_seed_isrc(store_path: str) -> None:
    """Blacklisting an active entry keeps its seed for future reinforcement."""
    store = RecommendationsStore(store_path)
    store.add_active(
        {"isrc": "X1", "apple_id": "A", "title": "T", "artist": "a", "seed_isrc": "S1"}
    )
    store.blacklist("X1")
    assert store.all_blacklist()["X1"]["seed_isrc"] == "S1"
