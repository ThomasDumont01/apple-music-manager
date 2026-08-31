"""Music Manager entry point — minimal pre-Textual setup.

Platform check, config, data root, and folder creation happen here.
All other checks run inside Textual screens for visual feedback.
"""

import os
import sys
from typing import TextIO

from music_manager import __version__
from music_manager.core.checks import check_macos
from music_manager.core.config import Paths, load_config, save_config
from music_manager.core.logger import init_logger
from music_manager.core.setup import choose_data_root, create_data_folders
from music_manager.services.albums import Albums
from music_manager.services.apple import Apple
from music_manager.services.tracks import Tracks

# ── Entry point ──────────────────────────────────────────────────────────────


_CLI_COMMANDS = frozenset(
    {
        "search",
        "search-playlists",
        "playlist-tracks",
        "import-isrcs",
        "import-status",
        "import-failures",
        "play",
        "play-playlist",
        "shuffle",
        "home",
        "spotify-login",
        "spotify-auth-status",
        "spotify-logout",
        "spotify-playlists",
        "spotify-playlist-tracks",
        "spotify-set-client-id",
        "exportify-process-csv",
        "playlist-local-tracks",
        "import-cancel",
        "install-widget",
    }
)



def _print_usage(stream: TextIO | None = None) -> None:
    """Print the top-level usage, including every CLI sub-command."""
    out = stream if stream is not None else sys.stdout
    print(
        f"music-manager {__version__}\n"
        "\n"
        "Sans argument : lance l'application.\n"
        "\n"
        "Options :\n"
        "  -h, --help     affiche cette aide\n"
        "  -V, --version  affiche la version\n"
        "\n"
        "Sous-commandes (destinées au widget et aux scripts) :\n"
        "  " + "\n  ".join(sorted(_CLI_COMMANDS)),
        file=out,
    )


def main(argv: list[str] | None = None) -> None:
    """Launch Music Manager (or dispatch a CLI sub-command)."""
    args = sys.argv[1:] if argv is None else argv

    # ── CLI fast path (widget / scripts) ─────────────────
    # Sub-commands skip the Textual UI entirely. Zero overhead for normal
    # launches: the import below only runs when a sub-command was requested.
    if args and args[0] in _CLI_COMMANDS:
        from music_manager.cli import dispatch  # noqa: PLC0415

        sys.exit(dispatch(args))

    # ── Options, not sub-commands ────────────────────────
    # Anything starting with "-" is a request for information or a typo.
    # Falling through to the UI left the user in a full-screen app with no
    # idea why `--help` had not printed anything.
    if args and args[0].startswith("-"):
        if args[0] in ("-h", "--help"):
            _print_usage()
            sys.exit(0)
        if args[0] in ("-V", "--version"):
            print(f"music-manager {__version__}")
            sys.exit(0)
        print(f"Option inconnue : {args[0]}\n", file=sys.stderr)
        _print_usage(stream=sys.stderr)
        sys.exit(2)

    _run_ui()


def _run_ui() -> None:
    """Boot the Textual application."""
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

    if config.get("youtube_cookies"):
        from music_manager.services.youtube import set_use_cookies  # noqa: PLC0415

        set_use_cookies(True)

    # ── Session start log ────────────────────��───────────
    from music_manager.core.logger import log_event  # noqa: PLC0415

    _log_session_start(log_event, config, paths)

    # ── Log upload (every 2 weeks) ──────────────────────
    _try_upload_logs(config, paths)

    # ── Convert Exportify CSVs before UI ─────────────────
    _convert_all_exportify(paths.requests_path, paths.playlists_dir)

    # ── Stores (loaded if setup already done) ────────────
    apple = Apple()
    tracks = Tracks(paths.tracks_path) if config["setup_done"] else None
    albums = Albums(paths.albums_path) if config["setup_done"] else None

    # ── Single-instance guard ────────────────────────────
    # Two UIs writing to tracks.json in parallel
    # would corrupt the stores. The lock is acquired by ``MusicApp.on_mount``;
    # here we just refuse to start when a live PID already holds it.
    from music_manager.cli.lock import (  # noqa: PLC0415
        clear_stale_lock,
        is_locked,
        lock_owner_pid,
    )

    # A lock left behind by a crashed instance must be dropped now: kept on
    # disk it eventually matches a recycled PID and locks the user out.
    clear_stale_lock(paths.ui_lock_path)

    if is_locked(paths.ui_lock_path):
        other_pid = lock_owner_pid(paths.ui_lock_path)
        sys.exit(
            "Music Manager est déjà en cours d'exécution"
            f" (PID {other_pid}). Ferme l'autre instance avant de relancer."
        )

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


def _try_upload_logs(config: dict, paths: object) -> None:
    """Upload logs to analytics endpoint if interval elapsed."""
    try:
        from music_manager.services.log_uploader import upload_logs  # noqa: PLC0415

        upload_logs(getattr(paths, "logs_path", ""), config)
    except Exception:  # noqa: BLE001
        pass  # never block startup


def _log_session_start(
    log_fn: object,
    config: dict,
    paths: object,
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
