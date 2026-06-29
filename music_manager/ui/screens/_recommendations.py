"""Recommendations mixin — Last.fm picks added to the "Recommandations" playlist."""

from collections import Counter
from typing import TYPE_CHECKING, Any

from textual import work
from textual.widgets import Input

from music_manager.ui.render import render_sub_header
from music_manager.ui.text import (
    HELP_BACK,
    HELP_RECOMMEND,
    HELP_RECOMMEND_API_KEY,
    HELP_RECOMMEND_DONE,
    HELP_RECOMMEND_RUNNING,
    RECOMMEND_API_KEY_PROMPT,
    RECOMMEND_COUNTS,
    RECOMMEND_DONE_SUMMARY,
    RECOMMEND_DONE_TITLE,
    RECOMMEND_ERROR_EMPTY,
    RECOMMEND_ERROR_GENERIC,
    RECOMMEND_ERROR_NO_KEY,
    RECOMMEND_GENERATING,
    RECOMMEND_MODE_DISCOVERY,
    RECOMMEND_MODE_GENRE,
    RECOMMEND_MODE_LIBRARY,
    RECOMMEND_MODE_MOOD,
    RECOMMEND_MODE_PLAYLIST,
    RECOMMEND_MOODS,
    RECOMMEND_NO_GENRES,
    RECOMMEND_NO_USER_PLAYLISTS,
    RECOMMEND_SCAN_RUNNING,
    RECOMMEND_TITLE,
)

if TYPE_CHECKING:
    from music_manager.ui.screens._protocol import MenuScreenProto

    _MixinBase = MenuScreenProto
else:
    _MixinBase = object


_TOP_GENRE_LIMIT = 8


class RecommendationsMixin(_MixinBase):
    """Recommendations feature methods for MenuScreen."""

    # ── Entry point ─────────────────────────────────────────────────────────

    def _start_recommendations(self) -> None:
        """Open the recommendations flow."""
        from music_manager.services.lastfm import get_api_key  # noqa: PLC0415

        self._return_to = "tools"
        self._recommend_mode = "library"
        self._recommend_count = 20
        self._recommend_running = False
        self._recommend_result = None

        if not get_api_key():
            self._show_api_key_prompt()
            return
        self._show_mode_selector()

    # ── API key prompt ──────────────────────────────────────────────────────

    def _show_api_key_prompt(self) -> None:
        """Ask the user for a Last.fm API key."""
        self._view = "recommend_api_key"
        self._set_header(render_sub_header(RECOMMEND_TITLE))
        self._set_body(RECOMMEND_API_KEY_PROMPT)
        self._set_help(HELP_RECOMMEND_API_KEY)

        input_w = self.query_one("#menu-input", Input)
        input_w.value = ""
        input_w.placeholder = "votre clé Last.fm"
        input_w.display = True
        input_w.disabled = False
        input_w.focus()

    def _on_recommend_api_key_submitted(self, key: str) -> None:
        """Persist the submitted key and continue to mode selection."""
        from music_manager.core.config import save_config  # noqa: PLC0415

        clean = key.strip()
        input_w = self.query_one("#menu-input", Input)
        input_w.display = False
        input_w.disabled = True
        input_w.value = ""

        if not clean:
            self._show_recommend_error(RECOMMEND_ERROR_NO_KEY)
            return

        save_config({"lastfm_api_key": clean})
        self._show_mode_selector()

    # ── Mode selector ───────────────────────────────────────────────────────

    def _show_mode_selector(self) -> None:
        """Display the five mode options."""
        self._view = "recommend_select_mode"
        self._items = [
            ("recommend_library", RECOMMEND_MODE_LIBRARY),
            ("recommend_playlist", RECOMMEND_MODE_PLAYLIST),
            ("recommend_genre", RECOMMEND_MODE_GENRE),
            ("recommend_mood", RECOMMEND_MODE_MOOD),
            ("recommend_discovery", RECOMMEND_MODE_DISCOVERY),
            None,
            ("back", "Retour"),
        ]
        self._selectable = [i for i, item in enumerate(self._items) if item is not None]
        self._cursor = 0
        self._set_header(render_sub_header(RECOMMEND_TITLE))
        self._refresh_menu()
        self._set_help(HELP_RECOMMEND)

    def _show_genre_selector(self) -> None:
        """List the top genres of the user's library."""
        genres = self._top_library_genres()
        if not genres:
            self._show_recommend_error(RECOMMEND_NO_GENRES)
            return

        self._view = "recommend_select_genre"
        self._items = [(f"recommend_genre:{name}", name) for name in genres]
        self._items.append(None)
        self._items.append(("back", "Retour"))
        self._selectable = [i for i, item in enumerate(self._items) if item is not None]
        self._cursor = 0
        self._set_header(render_sub_header(f"{RECOMMEND_TITLE} — Genres"))
        self._refresh_menu()
        self._set_help(HELP_RECOMMEND)

    def _show_mood_selector(self) -> None:
        """List the predefined Last.fm mood tags."""
        self._view = "recommend_select_mood"
        self._items = [(f"recommend_mood:{tag}", label) for tag, label in RECOMMEND_MOODS]
        self._items.append(None)
        self._items.append(("back", "Retour"))
        self._selectable = [i for i, item in enumerate(self._items) if item is not None]
        self._cursor = 0
        self._set_header(render_sub_header(f"{RECOMMEND_TITLE} — Ambiance"))
        self._refresh_menu()
        self._set_help(HELP_RECOMMEND)

    def _show_playlist_selector(self) -> None:
        """List Apple Music user playlists outside the ``for me`` folder."""
        from music_manager.services import apple  # noqa: PLC0415

        try:
            playlists = apple.list_playlists(exclude_folder=apple.RECO_FOLDER_NAME)
        except Exception:  # noqa: BLE001
            playlists = []
        # Defensive: also drop anything that still bears the legacy name.
        playlists = [(name, count) for name, count in playlists if name != "for me"]
        if not playlists:
            self._show_recommend_error(RECOMMEND_NO_USER_PLAYLISTS)
            return

        self._view = "recommend_select_playlist"
        self._items = [
            (f"recommend_playlist:{name}", f"{name} ({count})") for name, count in playlists
        ]
        self._items.append(None)
        self._items.append(("back", "Retour"))
        self._selectable = [i for i, item in enumerate(self._items) if item is not None]
        self._cursor = 0
        self._set_header(render_sub_header(f"{RECOMMEND_TITLE} — Playlist"))
        self._refresh_menu()
        self._set_help(HELP_RECOMMEND)

    def _on_recommend_mode_selected(self, mode: str) -> None:
        """Receive the picked mode and ask for the volume."""
        self._recommend_mode = mode
        self._show_count_selector()

    def _show_count_selector(self) -> None:
        """Ask the user how many recommendations to generate."""
        self._view = "recommend_select_count"
        self._items = [(f"recommend_count:{count}", label) for count, label in RECOMMEND_COUNTS]
        self._items.append(None)
        self._items.append(("back", "Retour"))
        self._selectable = [i for i, item in enumerate(self._items) if item is not None]
        # Default cursor to the "20" row (current default).
        default_idx = next(
            (
                idx
                for idx, sel in enumerate(self._selectable)
                if self._items[sel] and self._items[sel][0] == "recommend_count:20"  # type: ignore[index]
            ),
            0,
        )
        self._cursor = default_idx
        self._set_header(render_sub_header(f"{RECOMMEND_TITLE} — Quantité"))
        self._refresh_menu()
        self._set_help(HELP_RECOMMEND)

    def _on_recommend_count_selected(self, count: int) -> None:
        """Receive the picked count and start the worker."""
        self._recommend_count = max(1, int(count))
        self._start_recommend_worker()

    # ── Worker + progress ───────────────────────────────────────────────────

    def _start_recommend_worker(self) -> None:
        """Hand off to a background thread to keep the UI responsive."""
        self._recommend_running = True
        self._view = "recommend_scanning"
        self._set_header(render_sub_header(RECOMMEND_TITLE))
        self._set_body(RECOMMEND_SCAN_RUNNING)
        self._set_help(HELP_RECOMMEND_RUNNING, with_newline=False)
        self._run_recommend(self._recommend_mode)

    @work(thread=True)
    def _run_recommend(self, mode: str) -> None:
        """Background thread: scan + generate + import."""
        from music_manager.pipeline.recommend import generate_recommendations  # noqa: PLC0415

        if (
            self._tracks_store is None
            or self._albums_store is None
            or self._recs_store is None
            or self._paths is None
        ):
            return

        def progress(phase: str, current: int, total: int) -> None:
            self.app.call_from_thread(self._on_recommend_progress, phase, current, total)

        try:
            result = generate_recommendations(
                mode=mode,
                paths=self._paths,
                tracks_store=self._tracks_store,
                albums_store=self._albums_store,
                recs_store=self._recs_store,
                target_count=self._recommend_count,
                on_progress=progress,
            )
        except Exception as exc:  # noqa: BLE001
            self.app.call_from_thread(self._on_recommend_done, _ErrorResult(message=str(exc)))
            return

        self.app.call_from_thread(self._on_recommend_done, result)

    def _on_recommend_progress(self, phase: str, current: int, total: int) -> None:
        """Forward progress events to the body."""
        if phase == "scan":
            self._view = "recommend_scanning"
            self._set_body(RECOMMEND_SCAN_RUNNING)
        elif phase == "candidates":
            self._view = "recommend_generating"
            self._set_body(f"{RECOMMEND_GENERATING}  ({current}/{total})")
        elif phase == "resolve":
            self._view = "recommend_generating"
            self._set_body(f"Résolution Deezer  ({current}/{total})")
        elif phase == "import":
            self._view = "recommend_importing"
            self._set_body(f"Import {current}/{total}...")

    def _on_recommend_done(self, result: Any) -> None:
        """Render the final summary."""
        self._recommend_running = False
        self._recommend_result = result
        self._view = "recommend_done"
        self._set_header(render_sub_header(RECOMMEND_DONE_TITLE))

        if isinstance(result, _ErrorResult):
            self._show_recommend_error(RECOMMEND_ERROR_GENERIC.format(message=result.message))
            return

        error = getattr(result, "error", "")
        if error == "lastfm_no_api_key":
            self._show_recommend_error(RECOMMEND_ERROR_NO_KEY)
            return
        if error == "lastfm_empty":
            self._show_recommend_error(RECOMMEND_ERROR_EMPTY)
            return

        from music_manager.pipeline.recommend import (  # noqa: PLC0415
            playlist_name_for_mode,
        )

        try:
            playlist_label = playlist_name_for_mode(self._recommend_mode)
        except ValueError:
            playlist_label = "library"
        adopted = getattr(result, "adopted_playlist", 0)
        kept = getattr(result, "kept_library", 0)
        rejected = getattr(result, "rejected", 0)
        summary = RECOMMEND_DONE_SUMMARY.format(
            imported=result.imported,
            failed=result.failed,
            playlist=playlist_label,
            adopted=adopted,
            kept=kept,
            rejected=rejected,
        )
        self._set_body(summary)
        self._set_help(HELP_RECOMMEND_DONE, with_newline=False)

    def _show_recommend_error(self, message: str) -> None:
        """Render a terminal error state."""
        self._view = "recommend_error"
        self._set_header(render_sub_header(RECOMMEND_TITLE))
        self._set_body(message)
        self._set_help(HELP_BACK, with_newline=False)

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _top_library_genres(self) -> list[str]:
        """Return the top genres of the live library, ordered by frequency."""
        if not self._tracks_store:
            return []
        counter: Counter[str] = Counter()
        for entry in self._tracks_store.all().values():
            genre = str(entry.get("genre") or "").strip()
            if genre:
                counter[genre] += 1
        return [name for name, _count in counter.most_common(_TOP_GENRE_LIMIT)]


class _ErrorResult:
    """Lightweight error sentinel returned to the UI when the worker explodes."""

    def __init__(self, *, message: str) -> None:
        self.message = message
