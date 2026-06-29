"""`python -m music_manager import-isrcs ISRC1,ISRC2,... [--detach]`.

Drives the Music Manager import pipeline from a list of ISRCs picked in
the Übersicht widget. Progress is persisted to ``widget_status.json`` so
the widget can poll without keeping a process handle.

Concurrency rules:
- If the Textual UI holds ``~/.config/music_manager/.ui.lock``, this CLI
  refuses to run (exit code 2). The user must close the UI first.
- Two widget imports can't overlap: the second one fails with exit 3.
- ``--detach`` re-spawns the worker in a new session so the widget's
  subprocess returns instantly while the import runs in background.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime

from music_manager.cli.lock import acquire_lock, is_locked, release_lock
from music_manager.core.config import Paths, load_config
from music_manager.core.logger import init_logger, log_event
from music_manager.services.albums import Albums
from music_manager.services.apple import add_to_playlist, set_playlist_artwork
from music_manager.services.resolver import configure as configure_resolver
from music_manager.services.resolver import resolve_by_isrc
from music_manager.services.tracks import Tracks

# ── Constants ────────────────────────────────────────────────────────────────

_ISRC_RE = re.compile(r"^[A-Z0-9]{12}$")

# Übersicht spawns widget commands with a minimal PATH (/usr/bin:/bin), so the
# Python subprocess that calls `yt-dlp` and `ffmpeg` can't find them. We
# prepend the standard macOS package locations to be safe — these are no-ops
# when already present (e.g. when launched from a normal shell).
_PATH_AUGMENT = ("/opt/homebrew/bin", "/opt/local/bin", "/usr/local/bin")

EXIT_OK = 0
EXIT_USAGE = 2  # also used for "blocked by UI lock" (visible in status file)
EXIT_BUSY = 3
EXIT_INVALID = 4


# ── Entry point ──────────────────────────────────────────────────────────────


def main(args: list[str]) -> int:
    _augment_path()
    parser = argparse.ArgumentParser(prog="music_manager import-isrcs")
    parser.add_argument(
        "isrcs",
        help="comma-separated ISRC list (e.g. FRABC1234567,USUM71916175)",
    )
    parser.add_argument(
        "--detach",
        action="store_true",
        help="spawn the import worker in background and return immediately",
    )
    parser.add_argument(
        "--playlist-name",
        default="",
        help="if set, batch all successfully imported tracks into this "
        "Apple Music playlist (creates it if missing)",
    )
    parser.add_argument(
        "--playlist-cover-url",
        default="",
        help="optional cover image URL — downloaded and applied as the "
        "Apple Music playlist artwork (best-effort)",
    )
    parsed = parser.parse_args(args)

    isrcs = _parse_isrcs(parsed.isrcs)
    if not isrcs:
        sys.stderr.write("No valid ISRC provided.\n")
        return EXIT_INVALID

    playlist_name = (parsed.playlist_name or "").strip()
    playlist_cover_url = (parsed.playlist_cover_url or "").strip()

    config = load_config()
    data_root = str(config.get("data_root") or "")
    if not data_root or not os.path.isdir(data_root):
        sys.stderr.write("Music Manager data root not configured.\n")
        return EXIT_USAGE
    paths = Paths(data_root)

    if is_locked(paths.ui_lock_path):
        _write_status(
            paths.widget_status_path,
            {"status": "blocked", "reason": "ui_running", "isrcs": isrcs},
        )
        sys.stderr.write("Music Manager UI is running — close it first.\n")
        return EXIT_USAGE

    if parsed.detach:
        _spawn_detached(parsed.isrcs, playlist_name, playlist_cover_url)
        return EXIT_OK

    if not acquire_lock(paths.widget_lock_path):
        _write_status(
            paths.widget_status_path,
            {"status": "blocked", "reason": "widget_busy"},
        )
        sys.stderr.write("Another widget import is already running.\n")
        return EXIT_BUSY

    try:
        init_logger(paths.logs_path)
        return _run_import(paths, isrcs, playlist_name, playlist_cover_url)
    finally:
        release_lock(paths.widget_lock_path)


# ── Worker ───────────────────────────────────────────────────────────────────


def _run_import(
    paths: Paths,
    isrcs: list[str],
    playlist_name: str = "",
    playlist_cover_url: str = "",
) -> int:
    """Sequential import of every ISRC, with crash-safe status updates."""
    configure_resolver(str(load_config().get("language") or "fr"))

    tracks_store = Tracks(paths.tracks_path)
    albums_store = Albums(paths.albums_path)

    # Clean any stale cancel flag from a previous aborted run.
    _clear_cancel_flag(paths)

    status: dict = {
        "status": "running",
        "started_at": _now_iso(),
        "current": 0,
        "total": len(isrcs),
        "completed": [],
        "failed": [],
        "current_title": "",
        "playlist_name": playlist_name,
        "playlist_added": 0,
        "cancellable": True,
    }
    _write_status(paths.widget_status_path, status)
    log_event("widget_import_start", total=len(isrcs), playlist=playlist_name or None)

    # Importer pipeline pulled in lazily — avoids loading yt-dlp setup unless
    # we actually run an import (keeps the CLI startup tight).
    from music_manager.pipeline.importer import import_resolved_track  # noqa: PLC0415

    cancelled = False
    for idx, isrc in enumerate(isrcs, start=1):
        if _check_cancel(paths):
            cancelled = True
            break
        status["current"] = idx
        status["current_title"] = ""
        _write_status(paths.widget_status_path, status)

        # Fast-path : ISRC déjà importé → on collecte son apple_id pour la
        # playlist sans repasser par Deezer/yt-dlp (gain : 1-3s/track + 0 net).
        existing = tracks_store.get_by_isrc(isrc)
        if existing and existing.get("apple_id"):
            apple_id = str(existing["apple_id"])
            title = str(existing.get("title", ""))
            status["completed"].append({"isrc": isrc, "apple_id": apple_id, "title": title})
            status["current_title"] = f"{existing.get('artist', '')} — {title}".strip(" —")
            log_event("widget_import_skip_existing", isrc=isrc, apple_id=apple_id)
            _write_status(paths.widget_status_path, status)
            continue

        track = resolve_by_isrc(isrc, albums_store)
        if track is None:
            status["failed"].append({"isrc": isrc, "reason": "not_on_deezer"})
            log_event("widget_import_failed", isrc=isrc, reason="not_on_deezer")
            _write_status(paths.widget_status_path, status)
            continue

        status["current_title"] = f"{track.artist} — {track.title}"
        _write_status(paths.widget_status_path, status)

        try:
            pending = import_resolved_track(track, paths, tracks_store, albums_store)
        except Exception as exc:  # noqa: BLE001
            status["failed"].append({"isrc": isrc, "reason": str(exc)[:120]})
            log_event("widget_import_failed", isrc=isrc, reason=str(exc)[:200])
            _write_status(paths.widget_status_path, status)
            continue

        if pending is None and track.apple_id:
            status["completed"].append(
                {"isrc": isrc, "apple_id": track.apple_id, "title": track.title}
            )
            log_event("widget_import_done", isrc=isrc, apple_id=track.apple_id)
        else:
            reason = pending.reason if pending else "no_apple_id"
            status["failed"].append({"isrc": isrc, "reason": reason})
            log_event("widget_import_failed", isrc=isrc, reason=reason)

        # Crash safety after each item — never lose progress mid-run.
        tracks_store.save()
        albums_store.save()
        _write_status(paths.widget_status_path, status)

    # Batch-add successful tracks into the requested Apple Music playlist.
    # add_to_playlist is idempotent (creates the playlist if missing, appends
    # otherwise) and runs a single AppleScript call for all IDs. On cancel, we
    # still create the playlist with whatever was already imported so the user
    # keeps partial progress.
    if playlist_name:
        success_ids = [entry["apple_id"] for entry in status["completed"] if entry.get("apple_id")]
        if success_ids:
            try:
                status["playlist_added"] = add_to_playlist(playlist_name, success_ids)
            except Exception as exc:  # noqa: BLE001
                log_event(
                    "widget_playlist_add_failed",
                    playlist=playlist_name,
                    reason=str(exc)[:200],
                )

            # Best-effort : pose la cover Deezer sur la playlist Apple Music.
            # On télécharge en local (tmp_dir nettoyé par le pipeline), puis
            # AppleScript pour set l'artwork. Échec silencieux par design.
            if playlist_cover_url:
                _try_set_playlist_cover(playlist_name, playlist_cover_url, paths.tmp_dir)

    status["status"] = "cancelled" if cancelled else "done"
    status["finished_at"] = _now_iso()
    status["current_title"] = ""
    _write_status(paths.widget_status_path, status)
    _clear_cancel_flag(paths)
    log_event(
        "widget_import_end",
        completed=len(status["completed"]),
        failed=len(status["failed"]),
        playlist_added=status["playlist_added"],
        cancelled=cancelled,
    )
    return EXIT_OK


# ── Private Functions ────────────────────────────────────────────────────────


def _parse_isrcs(raw: str) -> list[str]:
    """Validate + dedupe the comma-separated ISRC list. Anti-injection."""
    seen: set[str] = set()
    isrcs: list[str] = []
    for token in (raw or "").split(","):
        candidate = token.strip().upper()
        if not _ISRC_RE.match(candidate):
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        isrcs.append(candidate)
    return isrcs


def _spawn_detached(raw_arg: str, playlist_name: str = "", playlist_cover_url: str = "") -> None:
    """Re-spawn ourselves in a new session so the widget returns immediately."""
    cmd = [sys.executable, "-m", "music_manager", "import-isrcs", raw_arg]
    if playlist_name:
        cmd.extend(["--playlist-name", playlist_name])
    if playlist_cover_url:
        cmd.extend(["--playlist-cover-url", playlist_cover_url])
    subprocess.Popen(  # noqa: S603
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )


def _try_set_playlist_cover(playlist_name: str, cover_url: str, tmp_dir: str) -> None:
    """Best-effort: download a cover URL and apply it as playlist artwork.

    Logs the outcome for observability. Failures (Deezer down, AppleScript
    quirk, etc.) never escalate — the playlist content is already in place.
    """
    try:
        from music_manager.services.resolver import download_cover_file  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        log_event(
            "widget_playlist_cover_failed",
            playlist=playlist_name,
            stage="import",
            reason=str(exc)[:200],
        )
        return
    path = download_cover_file(cover_url, tmp_dir, name="playlist_cover")
    if not path:
        log_event(
            "widget_playlist_cover_failed",
            playlist=playlist_name,
            stage="download",
        )
        return
    try:
        ok = set_playlist_artwork(playlist_name, path)
    except Exception as exc:  # noqa: BLE001
        log_event(
            "widget_playlist_cover_failed",
            playlist=playlist_name,
            stage="applescript",
            reason=str(exc)[:200],
        )
        return
    log_event(
        "widget_playlist_cover_set" if ok else "widget_playlist_cover_failed",
        playlist=playlist_name,
        stage="applescript",
    )


def _write_status(path: str, payload: dict) -> None:
    """Atomic write of ``widget_status.json``."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False)
    os.replace(tmp, path)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _check_cancel(paths: Paths) -> bool:
    """Return True if the cancel flag has been set by ``import-cancel``."""
    return os.path.isfile(paths.widget_cancel_path)


def _clear_cancel_flag(paths: Paths) -> None:
    """Remove the cancel flag — called before run + after end (cleanup)."""
    try:
        os.remove(paths.widget_cancel_path)
    except OSError:
        pass


def _augment_path() -> None:
    """Prepend standard Homebrew/MacPorts/usr-local dirs to PATH if missing."""
    current = os.environ.get("PATH", "")
    parts = current.split(":") if current else []
    seen = set(parts)
    added = [p for p in _PATH_AUGMENT if p not in seen]
    if added:
        os.environ["PATH"] = ":".join([*added, *parts])
