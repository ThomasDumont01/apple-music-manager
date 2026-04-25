"""Music Manager entry point — minimal pre-Textual setup.

Platform check, config, data root, and folder creation happen here.
All other checks run inside Textual screens for visual feedback.
"""

import os
import sys

from music_manager.core.checks import check_macos
from music_manager.core.config import Paths, load_config, save_config
from music_manager.core.logger import init_logger
from music_manager.core.setup import choose_data_root, create_data_folders
from music_manager.services.albums import Albums
from music_manager.services.apple import Apple
from music_manager.services.tracks import Tracks

# ── Entry point ──────────────────────────────────────────────────────────────


def main() -> None:
    """Launch Music Manager."""
    # ── Platform ─────────────────────────────────────────
    if not check_macos():
        sys.exit("Music Manager nécessite macOS.")

    config = load_config()

    # ── Data root ────────────────────────────────────────
    if not config["data_root"] or not os.path.isdir(str(config["data_root"])):
        data_root = choose_data_root()
        if not data_root:
            sys.exit(0)
        config["data_root"] = data_root
        save_config({"data_root": data_root})

    data_root = str(config["data_root"])

    # ── Folders + Paths + Logger ─────────────────────────
    try:
        create_data_folders(data_root)
    except OSError as exc:
        sys.exit(f"Impossible de créer les dossiers de données : {exc}")

    paths = Paths(data_root)
    init_logger(paths.logs_path)

    # ── Configure services ────────────────────────────────
    from music_manager.services.resolver import configure as configure_resolver  # noqa: PLC0415

    configure_resolver("fr")

    # ── Session start log ────────────────────��───────────
    from music_manager.core.logger import log_event  # noqa: PLC0415

    _log_session_start(log_event, config, paths)

    # ── Convert Exportify CSVs before UI ─────────────────
    _convert_all_exportify(paths.requests_path, paths.playlists_dir)

    # ── Stores (loaded if setup already done) ────────────
    apple = Apple()
    tracks = Tracks(paths.tracks_path) if config["setup_done"] else None
    albums = Albums(paths.albums_path) if config["setup_done"] else None

    # ── Launch Textual UI ────────────────────────────────
    from music_manager.ui.app import MusicApp  # noqa: PLC0415

    app = MusicApp(
        setup_done=bool(config["setup_done"]),
        tracks_store=tracks,
        albums_store=albums,
        paths=paths,
        apple=apple,
        requests_path=paths.requests_path,
        playlists_dir=paths.playlists_dir,
    )

    import time as _time  # noqa: PLC0415

    _session_t0 = _time.monotonic()

    try:
        app.run()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        log_event("crash", error=str(exc))
        sys.exit(f"Erreur fatale : {exc}")
    finally:
        session_ms = int((_time.monotonic() - _session_t0) * 1000)
        log_event("session_end", duration_ms=session_ms)
        # Cleanup temp files left by interrupted operations
        if hasattr(paths, "tmp_dir") and os.path.isdir(paths.tmp_dir):
            import shutil  # noqa: PLC0415

            try:
                shutil.rmtree(paths.tmp_dir, ignore_errors=True)
            except OSError:
                pass


# ── Private Functions ────────────────────────────────────────────────────────


def _log_session_start(
    log_fn: object, config: dict, paths: object,
) -> None:
    """Log session_start with app version + store sizes."""
    from collections.abc import Callable  # noqa: PLC0415
    from typing import Any  # noqa: PLC0415

    log: Callable[..., Any] = log_fn  # type: ignore[assignment]

    try:
        from music_manager import __version__  # noqa: PLC0415

        version = __version__
    except ImportError:
        version = "unknown"

    track_count = 0
    album_count = 0
    if config.get("setup_done"):
        try:
            # Count lines in JSON stores without loading full objects
            import json  # noqa: PLC0415

            tracks_path = getattr(paths, "tracks_path", "")
            albums_path = getattr(paths, "albums_path", "")
            if tracks_path and os.path.isfile(tracks_path):
                with open(tracks_path) as fh:
                    data = json.load(fh)
                    track_count = len(data) if isinstance(data, dict) else 0
            if albums_path and os.path.isfile(albums_path):
                with open(albums_path) as fh:
                    data = json.load(fh)
                    album_count = len(data) if isinstance(data, dict) else 0
        except Exception:  # noqa: BLE001
            pass

    log("session_start", version=version, track_count=track_count, album_count=album_count)


def _convert_all_exportify(requests_path: str, playlists_dir: str) -> None:
    """Convert any Exportify CSVs to standard format before menu display."""
    from music_manager.core.io import convert_exportify  # noqa: PLC0415

    if requests_path and os.path.isfile(requests_path):
        convert_exportify(requests_path)
    if playlists_dir and os.path.isdir(playlists_dir):
        for name in os.listdir(playlists_dir):
            if name.endswith(".csv"):
                convert_exportify(os.path.join(playlists_dir, name))


# ── Run script ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    main()
