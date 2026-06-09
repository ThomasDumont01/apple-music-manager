"""Tests for music_manager/services/signals.py — append-only event log + affinity."""

import json
import os
import threading
from datetime import UTC, datetime, timedelta

import pytest

from music_manager.services.signals import SignalsLog

# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def signals_path(tmp_path) -> str:
    return str(tmp_path / "signals.jsonl")


@pytest.fixture
def signals(signals_path: str) -> SignalsLog:
    return SignalsLog(signals_path)


# ── log() — append ───────────────────────────────────────────────────────────


def test_log_creates_file_and_writes_record(signals_path: str, signals: SignalsLog) -> None:
    signals.log("recommend_imported", isrc="FRX001", artist="A", genre="Rock")
    assert os.path.isfile(signals_path)
    with open(signals_path, encoding="utf-8") as file:
        lines = file.readlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["type"] == "recommend_imported"
    assert record["isrc"] == "FRX001"
    assert record["artist"] == "A"
    assert record["genre"] == "Rock"
    assert "ts" in record


def test_log_creates_parent_directory_when_missing(tmp_path) -> None:
    nested = tmp_path / "deep" / "nested" / "signals.jsonl"
    log = SignalsLog(str(nested))
    log.log("recommend_imported", isrc="X")
    assert nested.is_file()


def test_log_appends_multiple_events(signals_path: str, signals: SignalsLog) -> None:
    signals.log("recommend_imported", isrc="A")
    signals.log("recommend_imported", isrc="B")
    signals.log("recommend_imported", isrc="C")
    with open(signals_path, encoding="utf-8") as file:
        assert len(file.readlines()) == 3


def test_log_skips_empty_event_type(signals_path: str, signals: SignalsLog) -> None:
    signals.log("", isrc="A")
    assert not os.path.isfile(signals_path)


def test_log_serializes_non_ascii(signals_path: str, signals: SignalsLog) -> None:
    signals.log("recommend_imported", isrc="X", artist="Édith Piaf", genre="Chanson française")
    with open(signals_path, encoding="utf-8") as file:
        record = json.loads(file.readline())
    assert record["artist"] == "Édith Piaf"
    assert record["genre"] == "Chanson française"


# ── iter_events ──────────────────────────────────────────────────────────────


def test_iter_events_empty_file_yields_nothing(signals: SignalsLog) -> None:
    assert list(signals.iter_events()) == []


def test_iter_events_yields_all_in_order(signals: SignalsLog) -> None:
    signals.log("a", isrc="1")
    signals.log("b", isrc="2")
    signals.log("c", isrc="3")
    assert [event["type"] for event in signals.iter_events()] == ["a", "b", "c"]


def test_iter_events_skips_corrupt_lines(signals_path: str, signals: SignalsLog) -> None:
    signals.log("good1", isrc="1")
    with open(signals_path, "a", encoding="utf-8") as file:
        file.write("{not json\n")
        file.write("\n")
        file.write("partial line without newline at EOF")
    signals.log("good2", isrc="2")
    types = [event["type"] for event in signals.iter_events()]
    assert types == ["good1", "good2"]


def test_iter_events_skips_non_dict_payloads(signals_path: str, signals: SignalsLog) -> None:
    signals.log("good", isrc="1")
    with open(signals_path, "a", encoding="utf-8") as file:
        file.write("[1, 2, 3]\n")
        file.write('"a string"\n')
        file.write("42\n")
    assert [event["type"] for event in signals.iter_events()] == ["good"]


def test_iter_events_since_filters_older(signals_path: str, signals: SignalsLog) -> None:
    with open(signals_path, "w", encoding="utf-8") as file:
        file.write(json.dumps({"ts": "2025-01-01T00:00:00+00:00", "type": "old"}) + "\n")
        file.write(json.dumps({"ts": "2026-01-01T00:00:00+00:00", "type": "new"}) + "\n")
    events = list(signals.iter_events(since="2025-06-01T00:00:00+00:00"))
    assert [event["type"] for event in events] == ["new"]


# ── events_for_isrc ──────────────────────────────────────────────────────────


def test_events_for_isrc_filters_and_uppercases(signals: SignalsLog) -> None:
    signals.log("a", isrc="frx001", artist="A")
    signals.log("b", isrc="frx002", artist="B")
    signals.log("c", isrc="FRX001", artist="A")
    events = signals.events_for_isrc("FRX001")
    assert {event["type"] for event in events} == {"a", "c"}


def test_events_for_isrc_empty_returns_empty(signals: SignalsLog) -> None:
    assert signals.events_for_isrc("") == []
    assert signals.events_for_isrc("UNKNOWN") == []


# ── count ────────────────────────────────────────────────────────────────────


def test_count_returns_event_total(signals: SignalsLog) -> None:
    assert signals.count() == 0
    signals.log("a")
    signals.log("b")
    assert signals.count() == 2


# ── artist_affinity ──────────────────────────────────────────────────────────


def test_artist_affinity_requires_min_samples(signals: SignalsLog) -> None:
    signals.log("recommend_adopted_playlist", isrc="A", artist="ArtistX")
    signals.log("recommend_adopted_playlist", isrc="B", artist="ArtistX")
    assert signals.artist_affinity() == {}
    signals.log("recommend_adopted_playlist", isrc="C", artist="ArtistX")
    affinity = signals.artist_affinity()
    assert affinity["artistx"] == pytest.approx(1.0)


def test_artist_affinity_rejected_lowers_score(signals: SignalsLog) -> None:
    for i in range(3):
        signals.log("recommend_rejected", isrc=f"X{i}", artist="Bad")
    assert signals.artist_affinity()["bad"] == pytest.approx(-1.0)


def test_artist_affinity_mixed_adopted_rejected(signals: SignalsLog) -> None:
    signals.log("recommend_adopted_playlist", isrc="1", artist="Mix")
    signals.log("recommend_adopted_playlist", isrc="2", artist="Mix")
    signals.log("recommend_rejected", isrc="3", artist="Mix")
    assert signals.artist_affinity()["mix"] == pytest.approx(1.0 / 3.0, abs=0.001)


def test_artist_affinity_kept_library_is_half_weight(signals: SignalsLog) -> None:
    for i in range(3):
        signals.log("recommend_kept_library", isrc=f"X{i}", artist="Kept")
    assert signals.artist_affinity()["kept"] == pytest.approx(0.5)


def test_artist_affinity_loved_delta_counts_only_to_loved_true(signals: SignalsLog) -> None:
    signals.log("loved_delta", isrc="1", artist="A", to_loved=True)
    signals.log("loved_delta", isrc="2", artist="A", to_loved=True)
    signals.log("loved_delta", isrc="3", artist="A", to_loved=False)
    assert signals.artist_affinity() == {}
    signals.log("loved_delta", isrc="4", artist="A", to_loved=True)
    assert signals.artist_affinity()["a"] == pytest.approx(0.7)


def test_artist_affinity_playcount_delta_counts_only_positive(signals: SignalsLog) -> None:
    signals.log("playcount_delta", isrc="1", artist="P", delta=2)
    signals.log("playcount_delta", isrc="2", artist="P", delta=5)
    signals.log("playcount_delta", isrc="3", artist="P", delta=0)
    assert signals.artist_affinity() == {}
    signals.log("playcount_delta", isrc="4", artist="P", delta=1)
    assert signals.artist_affinity()["p"] == pytest.approx(0.3)


def test_artist_affinity_window_excludes_old_events(
    signals_path: str, signals: SignalsLog
) -> None:
    old_ts = (datetime.now(UTC) - timedelta(days=400)).isoformat(timespec="seconds")
    new_ts = (datetime.now(UTC) - timedelta(days=10)).isoformat(timespec="seconds")
    with open(signals_path, "w", encoding="utf-8") as file:
        for i in range(3):
            event = {
                "ts": old_ts,
                "type": "recommend_adopted_playlist",
                "isrc": f"OLD{i}",
                "artist": "Stale",
            }
            file.write(json.dumps(event) + "\n")
        for i in range(3):
            event = {
                "ts": new_ts,
                "type": "recommend_rejected",
                "isrc": f"NEW{i}",
                "artist": "Fresh",
            }
            file.write(json.dumps(event) + "\n")
    affinity = signals.artist_affinity(window_days=180)
    assert "stale" not in affinity
    assert affinity["fresh"] == pytest.approx(-1.0)


def test_artist_affinity_ignores_missing_or_non_string_artist(signals: SignalsLog) -> None:
    signals.log("recommend_adopted_playlist", isrc="1", artist=None)
    signals.log("recommend_adopted_playlist", isrc="2", artist="")
    signals.log("recommend_adopted_playlist", isrc="3", artist=123)
    assert signals.artist_affinity() == {}


def test_artist_affinity_normalizes_case(signals: SignalsLog) -> None:
    signals.log("recommend_adopted_playlist", isrc="1", artist="Foo")
    signals.log("recommend_adopted_playlist", isrc="2", artist="foo")
    signals.log("recommend_adopted_playlist", isrc="3", artist="FOO")
    assert signals.artist_affinity() == {"foo": pytest.approx(1.0)}


def test_artist_affinity_clipped_to_unit_range(signals: SignalsLog) -> None:
    for i in range(10):
        signals.log("recommend_adopted_playlist", isrc=f"X{i}", artist="Top")
    score = signals.artist_affinity()["top"]
    assert -1.0 <= score <= 1.0


# ── genre_affinity ───────────────────────────────────────────────────────────


def test_genre_affinity_uses_genre_field(signals: SignalsLog) -> None:
    for i in range(3):
        signals.log("recommend_adopted_playlist", isrc=f"X{i}", genre="Rock")
    assert signals.genre_affinity()["rock"] == pytest.approx(1.0)


def test_genre_affinity_independent_from_artist(signals: SignalsLog) -> None:
    for i in range(3):
        signals.log("recommend_rejected", isrc=f"X{i}", artist="Anyone", genre="Metal")
    assert signals.genre_affinity()["metal"] == pytest.approx(-1.0)


# ── generation_run events ────────────────────────────────────────────────────


def test_generation_run_events_excluded_from_affinity(signals: SignalsLog) -> None:
    for _ in range(5):
        signals.log("generation_run", mode="library", imported=10, artist="X", genre="Y")
    assert signals.artist_affinity() == {}
    assert signals.genre_affinity() == {}


def test_recommend_imported_excluded_from_affinity(signals: SignalsLog) -> None:
    for i in range(5):
        signals.log("recommend_imported", isrc=f"X{i}", artist="A", genre="G")
    assert signals.artist_affinity() == {}
    assert signals.genre_affinity() == {}


# ── concurrent ───────────────────────────────────────────────────────────────


def test_concurrent_appends_preserve_all_events(signals: SignalsLog) -> None:
    def worker(prefix: str) -> None:
        for i in range(20):
            signals.log("test", isrc=f"{prefix}{i}")

    threads = [threading.Thread(target=worker, args=(prefix,)) for prefix in ("A", "B", "C", "D")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    events = list(signals.iter_events())
    assert len(events) == 80
    assert all(event["type"] == "test" for event in events)
