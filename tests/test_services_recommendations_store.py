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
    result = store.add_active({"isrc": "", "apple_id": "AP", "title": "T", "artist": "A"})
    assert store.all_active() == {}
    assert result == {"added": False, "reason": "no_isrc"}


def test_add_active_returns_added_true_on_success(store_path: str) -> None:
    store = RecommendationsStore(store_path)
    result = store.add_active(
        {"isrc": "X1", "apple_id": "AP1", "title": "T", "artist": "A"}
    )
    assert result == {"added": True}


def test_add_active_duplicate_returns_current_playlist(store_path: str) -> None:
    """Re-adding an ISRC reports the playlist the original was filed under.

    Lets the caller log a cross-playlist conflict (importing the same
    track into ``for me/library`` then ``for me/rock``).
    """
    store = RecommendationsStore(store_path)
    store.add_active(
        {
            "isrc": "X1",
            "apple_id": "AP1",
            "title": "T",
            "artist": "A",
            "playlist": "library",
        }
    )
    result = store.add_active(
        {
            "isrc": "X1",
            "apple_id": "AP1",
            "title": "T",
            "artist": "A",
            "playlist": "rock",
        }
    )
    assert result == {
        "added": False,
        "reason": "duplicate",
        "current_playlist": "library",
    }
    assert store.all_active()["X1"]["playlist"] == "library"  # unchanged


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
    assert set(raw.keys()) == {"active", "outcomes", "stats"}


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


# ── Outcomes 3-states ───────────────────────────────────────────────────────


def test_record_outcome_adopted_playlist_moves_from_active(store_path: str) -> None:
    """record_outcome with state=adopted_playlist removes from active."""
    store = RecommendationsStore(store_path)
    store.add_active(
        {"isrc": "X1", "apple_id": "AP1", "title": "T", "artist": "A", "genre": "Rock"}
    )
    store.record_outcome(
        "X1",
        state="adopted_playlist",
        from_playlist="for me / library",
        to_playlists=["My Favs", "Workout"],
    )
    assert not store.is_active("X1")
    assert store.is_outcome("X1")
    assert store.is_adopted("X1")
    entry = store.all_outcomes()["X1"]
    assert entry["state"] == "adopted_playlist"
    assert entry["from_playlist"] == "for me / library"
    assert entry["to_playlists"] == ["My Favs", "Workout"]
    assert entry["outcome_at"]
    assert entry["title"] == "T"
    assert entry["artist"] == "A"
    assert entry["genre"] == "Rock"


def test_record_outcome_kept_library(store_path: str) -> None:
    store = RecommendationsStore(store_path)
    store.add_active({"isrc": "X1", "apple_id": "A", "title": "T", "artist": "A"})
    store.record_outcome("X1", state="kept_library", from_playlist="for me / library")
    assert store.is_kept("X1")
    assert not store.is_adopted("X1")
    assert not store.is_rejected("X1")


def test_record_outcome_rejected(store_path: str) -> None:
    store = RecommendationsStore(store_path)
    store.add_active({"isrc": "X1", "apple_id": "A", "title": "T", "artist": "A"})
    store.record_outcome("X1", state="rejected", from_playlist="for me / library")
    assert store.is_rejected("X1")
    assert store.is_blacklisted("X1")  # backward-compat alias


def test_record_outcome_invalid_state_raises(store_path: str) -> None:
    store = RecommendationsStore(store_path)
    with pytest.raises(ValueError):
        store.record_outcome("X1", state="banana")


def test_record_outcome_empty_isrc_noop(store_path: str) -> None:
    store = RecommendationsStore(store_path)
    store.record_outcome("", state="rejected")
    assert store.all_outcomes() == {}


def test_record_outcome_overwrites_previous(store_path: str) -> None:
    """If state changes (e.g. rejected → adopted_playlist later), overwrite."""
    store = RecommendationsStore(store_path)
    store.record_outcome("X1", state="rejected", title="T", artist="A")
    store.record_outcome(
        "X1", state="adopted_playlist", to_playlists=["My Favs"], title="T", artist="A"
    )
    assert store.is_adopted("X1")
    assert not store.is_rejected("X1")


# ── Snapshots last_seen_loved / last_seen_playcount ────────────────────────


def test_add_active_stamps_snapshots_with_defaults(store_path: str) -> None:
    store = RecommendationsStore(store_path)
    store.add_active({"isrc": "X1", "apple_id": "A", "title": "T", "artist": "A"})
    entry = store.all_active()["X1"]
    assert entry["last_seen_loved"] is False
    assert entry["last_seen_playcount"] == 0


def test_add_active_honors_explicit_snapshots(store_path: str) -> None:
    store = RecommendationsStore(store_path)
    store.add_active(
        {
            "isrc": "X1",
            "apple_id": "A",
            "title": "T",
            "artist": "A",
            "last_seen_loved": True,
            "last_seen_playcount": 5,
        }
    )
    entry = store.all_active()["X1"]
    assert entry["last_seen_loved"] is True
    assert entry["last_seen_playcount"] == 5


def test_add_active_persists_playlist_target(store_path: str) -> None:
    store = RecommendationsStore(store_path)
    store.add_active(
        {
            "isrc": "X1",
            "apple_id": "A",
            "title": "T",
            "artist": "A",
            "playlist": "for me / rock",
        }
    )
    assert store.all_active()["X1"]["playlist"] == "for me / rock"


def test_update_snapshot_modifies_active_entry(store_path: str) -> None:
    store = RecommendationsStore(store_path)
    store.add_active({"isrc": "X1", "apple_id": "A", "title": "T", "artist": "A"})
    store.update_snapshot("X1", loved=True, playcount=4)
    entry = store.all_active()["X1"]
    assert entry["last_seen_loved"] is True
    assert entry["last_seen_playcount"] == 4


def test_update_snapshot_on_unknown_isrc_is_noop(store_path: str) -> None:
    store = RecommendationsStore(store_path)
    store.update_snapshot("UNKNOWN", loved=True, playcount=10)
    assert store.all_active() == {}


# ── Migration douce : ancien blacklist → outcomes ───────────────────────────


def test_legacy_blacklist_migrates_to_outcomes_rejected(tmp_path: Path) -> None:
    """An old recommendations.json with a 'blacklist' key migrates on load."""
    path = tmp_path / "recs.json"
    path.write_text(
        json.dumps(
            {
                "active": {},
                "blacklist": {
                    "OLD1": {
                        "removed_at": "2025-01-01T00:00:00+00:00",
                        "title": "Old",
                        "artist": "Legacy",
                        "seed_isrc": "S1",
                    }
                },
                "stats": {"generations": 4},
            }
        )
    )
    store = RecommendationsStore(str(path))
    assert store.is_rejected("OLD1")
    assert store.is_blacklisted("OLD1")  # backward-compat alias
    entry = store.all_outcomes()["OLD1"]
    assert entry["state"] == "rejected"
    assert entry["title"] == "Old"
    assert entry["artist"] == "Legacy"
    assert entry["seed_isrc"] == "S1"
    assert entry["outcome_at"] == "2025-01-01T00:00:00+00:00"


def test_legacy_active_without_playlist_field_migrates_to_library(
    tmp_path: Path,
) -> None:
    """Active entries from before the per-playlist tracking get ``library``."""
    path = tmp_path / "recs.json"
    path.write_text(
        json.dumps(
            {
                "active": {
                    "OLD1": {
                        "isrc": "OLD1",
                        "apple_id": "AP1",
                        "title": "T",
                        "artist": "A",
                        "play_count": 3,
                        "loved": True,
                    }
                },
                "outcomes": {},
                "stats": {},
            }
        )
    )
    store = RecommendationsStore(str(path))
    entry = store.all_active()["OLD1"]
    assert entry["playlist"] == "library"
    assert entry["last_seen_loved"] is True
    assert entry["last_seen_playcount"] == 3


def test_legacy_active_migration_persists_on_resave(tmp_path: Path) -> None:
    path = tmp_path / "recs.json"
    path.write_text(
        json.dumps(
            {
                "active": {
                    "OLD1": {
                        "isrc": "OLD1", "apple_id": "AP1", "title": "T", "artist": "A",
                    }
                }
            }
        )
    )
    store = RecommendationsStore(str(path))
    store.save()
    raw = json.loads(path.read_text())
    assert raw["active"]["OLD1"]["playlist"] == "library"
    assert raw["active"]["OLD1"]["last_seen_loved"] is False
    assert raw["active"]["OLD1"]["last_seen_playcount"] == 0


def test_legacy_blacklist_migration_does_not_lose_data_on_resave(tmp_path: Path) -> None:
    path = tmp_path / "recs.json"
    path.write_text(
        json.dumps({"active": {}, "blacklist": {"OLD1": {"title": "T", "artist": "A"}}})
    )
    store = RecommendationsStore(str(path))
    store.mark_dirty()
    store.save()
    raw = json.loads(path.read_text())
    assert "outcomes" in raw
    assert "blacklist" not in raw
    assert raw["outcomes"]["OLD1"]["state"] == "rejected"


# ── seed_quality uses outcomes (kept + adopted DO NOT count as negative) ────


def test_seed_quality_only_rejected_counts_as_negative(store_path: str) -> None:
    """A seed with adoptions+kept (no rejects) has ratio 0 — not flagged."""
    store = RecommendationsStore(store_path)
    for i in range(2):
        store.add_active(
            {"isrc": f"A{i}", "apple_id": f"AP{i}", "title": "T", "artist": "x", "seed_isrc": "S"}
        )
        store.record_outcome(f"A{i}", state="adopted_playlist")
    store.add_active(
        {"isrc": "K1", "apple_id": "AK", "title": "T", "artist": "x", "seed_isrc": "S"}
    )
    store.record_outcome("K1", state="kept_library")
    quality = store.seed_quality()
    assert quality["S"] == pytest.approx(0.0)
