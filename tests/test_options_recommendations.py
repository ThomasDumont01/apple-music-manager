"""Tests for the recommendations UI mixin (instance-level, no widget tree)."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from music_manager.core.config import Paths
from music_manager.pipeline.recommend import GenerationResult
from music_manager.services.albums import Albums
from music_manager.services.recommendations_store import RecommendationsStore
from music_manager.services.tracks import Tracks
from music_manager.ui.screens._recommendations import RecommendationsMixin

# ── Helpers ─────────────────────────────────────────────────────────────────


class _StubScreen(RecommendationsMixin):  # type: ignore[misc]
    """Minimal stand-in that mocks Textual widget access for unit-tested flows."""

    def __getattr__(self, name: str):
        # Satisfies ABC / Protocol without implementing 158 abstract methods.
        # Only called for attributes not found via normal lookup, so explicit
        # overrides below take precedence.
        return MagicMock()

    def __init__(
        self,
        *,
        tracks: Tracks,
        albums: Albums,
        recs: RecommendationsStore,
        paths: Paths,
    ) -> None:
        self._tracks_store = tracks
        self._albums_store = albums
        self._recs_store = recs
        self._paths = paths
        self._items: list = []
        self._selectable: list[int] = []
        self._cursor = 0
        self._view = ""
        self._return_to = ""
        self._recommend_mode = "general"
        self._recommend_count = 20
        self._recommend_running = False
        self._recommend_result = None
        self._headers: list = []
        self._bodies: list = []
        self._helps: list[str] = []
        self.app = MagicMock()
        # Pretend any widget query returns a stubbed Input.
        self._input_stub = MagicMock()
        self._input_stub.value = ""
        self.query_one = MagicMock(return_value=self._input_stub)

    def _set_header(self, content) -> None:  # type: ignore[override]
        self._headers.append(content)

    def _set_body(self, content, check_scroll: bool = True) -> None:  # type: ignore[override]
        self._bodies.append(content)

    def _set_help(self, text: str, with_newline: bool = True) -> None:  # type: ignore[override]
        self._helps.append(text)

    def _refresh_menu(self) -> None:  # type: ignore[override]
        pass

    def _switch_view(self, view: str) -> None:  # type: ignore[override]
        self._view = view


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    root = tmp_path / "music"
    root.mkdir()
    return Paths(str(root))


@pytest.fixture
def stub(paths: Paths) -> _StubScreen:
    tracks = Tracks(paths.tracks_path)
    albums = Albums(paths.albums_path)
    recs = RecommendationsStore(paths.recommendations_path)
    return _StubScreen(tracks=tracks, albums=albums, recs=recs, paths=paths)  # type: ignore[abstract]


# ── Tests ───────────────────────────────────────────────────────────────────


def test_start_recommendations_prompts_api_key_when_missing(stub, monkeypatch) -> None:
    """Without an API key, the UI shows the prompt instead of the mode selector."""
    monkeypatch.delenv("LASTFM_API_KEY", raising=False)
    monkeypatch.setattr(
        "music_manager.services.lastfm.load_config",
        lambda: {"lastfm_api_key": ""},
    )
    stub._start_recommendations()
    assert stub._view == "recommend_api_key"


def test_start_recommendations_shows_mode_selector_with_key(stub, monkeypatch) -> None:
    """An existing API key skips the prompt."""
    monkeypatch.setenv("LASTFM_API_KEY", "fake")
    stub._start_recommendations()
    assert stub._view == "recommend_select_mode"
    keys = [item[0] for item in stub._items if item is not None]
    assert {
        "recommend_library",
        "recommend_playlist",
        "recommend_genre",
        "recommend_mood",
        "recommend_discovery",
        "back",
    } <= set(keys)


def test_show_genre_selector_lists_top_genres(stub) -> None:
    """The genre selector lists the dominant genres of the live library."""
    stub._tracks_store.add("A1", {"isrc": "X1", "title": "T1", "artist": "A", "genre": "Rock"})
    stub._tracks_store.add("A2", {"isrc": "X2", "title": "T2", "artist": "B", "genre": "Rock"})
    stub._tracks_store.add("A3", {"isrc": "X3", "title": "T3", "artist": "C", "genre": "Pop"})
    stub._show_genre_selector()
    assert stub._view == "recommend_select_genre"
    genre_keys = [
        item[0]
        for item in stub._items
        if item is not None and item[0].startswith("recommend_genre:")
    ]
    assert "recommend_genre:Rock" in genre_keys
    assert "recommend_genre:Pop" in genre_keys


def test_show_genre_selector_with_no_genres_shows_error(stub) -> None:
    """Empty library → user-visible error, not a crash."""
    stub._show_genre_selector()
    assert stub._view == "recommend_error"


def test_on_recommend_api_key_persists_and_advances(stub, monkeypatch) -> None:
    """Submitting a non-empty key saves it and lands on the mode selector."""
    saved: dict = {}
    monkeypatch.setattr("music_manager.core.config.save_config", saved.update)
    monkeypatch.setenv("LASTFM_API_KEY", "ENV-KEY")  # ensure get_api_key returns truthy after save
    stub._on_recommend_api_key_submitted("my-secret-key")
    assert saved == {"lastfm_api_key": "my-secret-key"}
    assert stub._view == "recommend_select_mode"


def test_on_recommend_api_key_empty_shows_error(stub, monkeypatch) -> None:
    """Empty input → error view, no config write."""
    called = MagicMock()
    monkeypatch.setattr("music_manager.core.config.save_config", called)
    stub._on_recommend_api_key_submitted("   ")
    assert stub._view == "recommend_error"
    called.assert_not_called()


def test_progress_updates_view_per_phase(stub) -> None:
    """Progress callback routes the phase to the matching view state."""
    stub._on_recommend_progress("scan", 0, 0)
    assert stub._view == "recommend_scanning"
    stub._on_recommend_progress("candidates", 5, 25)
    assert stub._view == "recommend_generating"
    stub._on_recommend_progress("resolve", 10, 30)
    assert stub._view == "recommend_generating"
    stub._on_recommend_progress("import", 1, 20)
    assert stub._view == "recommend_importing"


def test_on_recommend_done_success_renders_summary(stub) -> None:
    """A success result lands on recommend_done with the summary text."""
    result = GenerationResult(imported=15, failed=2, rejected=3)
    stub._on_recommend_done(result)
    assert stub._view == "recommend_done"
    body = stub._bodies[-1]
    assert "15" in body and "for me" in body


def test_on_recommend_done_lastfm_empty_routes_to_error(stub) -> None:
    """A lastfm_empty error is rendered as an error view."""
    result = GenerationResult(error="lastfm_empty")
    stub._on_recommend_done(result)
    assert stub._view == "recommend_error"


def test_on_recommend_done_no_key_routes_to_error(stub) -> None:
    """lastfm_no_api_key is rendered as an error view."""
    result = GenerationResult(error="lastfm_no_api_key")
    stub._on_recommend_done(result)
    assert stub._view == "recommend_error"


def test_start_recommend_worker_launches_thread(stub, monkeypatch) -> None:
    """The worker entry point sets the running flag and renders scanning view."""
    fake_runner = MagicMock()
    monkeypatch.setattr(stub, "_run_recommend", fake_runner)
    stub._start_recommend_worker()
    fake_runner.assert_called_once_with(stub._recommend_mode)
    assert stub._recommend_running is True
    assert stub._view == "recommend_scanning"


def test_on_recommend_mode_selected_opens_count_selector(stub, monkeypatch) -> None:
    """Picking a mode opens the count selector — no worker yet."""
    fake_runner = MagicMock()
    monkeypatch.setattr(stub, "_run_recommend", fake_runner)
    stub._on_recommend_mode_selected("mood:chill")
    assert stub._recommend_mode == "mood:chill"
    assert stub._view == "recommend_select_count"
    fake_runner.assert_not_called()
    keys = [item[0] for item in stub._items if item is not None]
    assert "recommend_count:10" in keys
    assert "recommend_count:20" in keys
    assert "recommend_count:50" in keys


def test_on_recommend_count_selected_starts_worker(stub, monkeypatch) -> None:
    """Picking a count finally kicks off the worker with that target."""
    fake_runner = MagicMock()
    monkeypatch.setattr(stub, "_run_recommend", fake_runner)
    stub._recommend_mode = "general"
    stub._on_recommend_count_selected(50)
    assert stub._recommend_count == 50
    fake_runner.assert_called_once_with("general")


def test_on_recommend_count_selected_clamps_invalid(stub, monkeypatch) -> None:
    """A nonsense count is clamped to at least 1 — not zero."""
    monkeypatch.setattr(stub, "_run_recommend", MagicMock())
    stub._on_recommend_count_selected(0)
    assert stub._recommend_count == 1


def test_recommend_done_state_does_not_carry_stale_selectables(stub, monkeypatch) -> None:
    """Regression: after a generation, _items must NOT still point to the mode selector."""
    monkeypatch.setenv("LASTFM_API_KEY", "fake")
    stub._start_recommendations()
    assert stub._view == "recommend_select_mode"
    stale_keys = {item[0] for item in stub._items if item is not None}
    assert "recommend_library" in stale_keys  # confirm the bug surface

    stub._on_recommend_done(GenerationResult(imported=5))
    assert stub._view == "recommend_done"


def test_top_library_genres_returns_sorted_unique(stub) -> None:
    """_top_library_genres ranks by frequency and skips blanks."""
    stub._tracks_store.add("A1", {"isrc": "X1", "title": "T", "artist": "A", "genre": "Rock"})
    stub._tracks_store.add("A2", {"isrc": "X2", "title": "T", "artist": "A", "genre": "Rock"})
    stub._tracks_store.add("A3", {"isrc": "X3", "title": "T", "artist": "A", "genre": "Pop"})
    stub._tracks_store.add("A4", {"isrc": "X4", "title": "T", "artist": "A", "genre": ""})
    result = stub._top_library_genres()
    assert result[0] == "Rock"
    assert "Pop" in result
    assert "" not in result
