"""`python -m music_manager import-isrcs ISRC1,ISRC2,... [--detach]`.

Drives the Music Manager import pipeline from a list of ISRCs picked in
the Übersicht widget. Progress is persisted to ``widget_status.json`` so
the widget can poll without keeping a process handle.

Every invocation carries a ``run_id``: the widget can then tell its own run
apart from the leftovers of the previous one. Without it, the first poll of a
new import read the *previous* run's finished status, concluded the import was
over and stopped polling while the worker was still starting up.

Concurrency rules:
- If the Textual UI holds ``~/.config/music_manager/.ui.lock``, this CLI
  refuses to run (exit code 2). The user must close the UI first.
- Two widget imports can't overlap: the second one fails with exit 3 and
  leaves the running import's status file untouched.
- ``--detach`` re-spawns the worker in a new session so the widget's
  subprocess returns instantly while the import runs in background. The
  parent prints ``{"status": "started", "run_id": ...}`` on stdout.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime

from music_manager.cli.failures import (
    clear_failures,
    recent_permanent_failures,
    record_failures,
)
from music_manager.cli.lock import acquire_lock, clear_stale_lock, is_locked, release_lock
from music_manager.cli.status import read_status
from music_manager.core.checks import check_yt_dlp_fresh, yt_dlp_update_hint
from music_manager.core.config import Paths, load_config
from music_manager.core.logger import init_logger, log_event
from music_manager.services.albums import Albums
from music_manager.services.apple import (
    add_to_playlist,
    apple_ids_exist_checked,
    set_playlist_artwork,
)
from music_manager.services.resolver import configure as configure_resolver
from music_manager.services.resolver import resolve_by_isrc
from music_manager.services.tracks import Tracks

# ── Constants ────────────────────────────────────────────────────────────────

_ISRC_RE = re.compile(r"^[A-Z0-9]{12}$")

# Übersicht spawns widget commands with a minimal PATH (/usr/bin:/bin), so the
# Python subprocess that calls `yt-dlp` and `ffmpeg` can't find them. We
# prepend the standard macOS package locations to be safe — these are no-ops
# when already present (e.g. when launched from a normal shell), so a user's
# own PATH ordering is never rewritten.
# ``~/.local/bin`` comes first: `uv tool` / `pipx` installs live there and
# track yt-dlp releases directly, while the Homebrew formula regularly lags
# upstream by weeks — long enough for YouTube to start answering 403.
_PATH_AUGMENT = (
    os.path.join(os.path.expanduser("~"), ".local", "bin"),
    "/opt/homebrew/bin",
    "/opt/local/bin",
    "/usr/local/bin",
)

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
    parser.add_argument(
        "--run-id",
        default="",
        help="identifier echoed in widget_status.json so the caller can tell "
        "its own run apart from a previous one (generated when omitted)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="retry ISRCs that failed recently instead of skipping them",
    )
    parsed = parser.parse_args(args)

    isrcs = _parse_isrcs(parsed.isrcs)
    if not isrcs:
        _emit({"status": "error", "reason": "no_valid_isrc"})
        sys.stderr.write("No valid ISRC provided.\n")
        return EXIT_INVALID

    playlist_name = (parsed.playlist_name or "").strip()
    playlist_cover_url = (parsed.playlist_cover_url or "").strip()
    run_id = (parsed.run_id or "").strip() or uuid.uuid4().hex[:12]

    config = load_config()
    data_root = str(config.get("data_root") or "")
    if not data_root or not os.path.isdir(data_root):
        _emit({"status": "error", "reason": "no_data_root"})
        sys.stderr.write("Music Manager data root not configured.\n")
        return EXIT_USAGE
    paths = Paths(data_root)

    # A lock orphaned by a crash must not outlive the crash: left on disk it
    # eventually matches a recycled PID and blocks every future import.
    clear_stale_lock(paths.ui_lock_path)
    clear_stale_lock(paths.widget_lock_path)

    if is_locked(paths.ui_lock_path):
        _report_blocked(paths, "ui_running", isrcs)
        sys.stderr.write("Music Manager UI is running — close it first.\n")
        return EXIT_USAGE

    if is_locked(paths.widget_lock_path):
        _report_blocked(paths, "widget_busy", isrcs)
        sys.stderr.write("Another widget import is already running.\n")
        return EXIT_BUSY

    if parsed.detach:
        _spawn_detached(parsed.isrcs, playlist_name, playlist_cover_url, run_id, parsed.force)
        _emit({"status": "started", "run_id": run_id, "total": len(isrcs)})
        return EXIT_OK

    if not acquire_lock(paths.widget_lock_path):
        _report_blocked(paths, "widget_busy", isrcs)
        sys.stderr.write("Another widget import is already running.\n")
        return EXIT_BUSY

    try:
        init_logger(paths.logs_path)
        code = _run_import(
            paths, isrcs, playlist_name, playlist_cover_url, run_id, force=parsed.force
        )
    finally:
        release_lock(paths.widget_lock_path)
    _emit({"status": "finished", "run_id": run_id})
    return code


# ── Worker ───────────────────────────────────────────────────────────────────


def _run_import(
    paths: Paths,
    isrcs: list[str],
    playlist_name: str = "",
    playlist_cover_url: str = "",
    run_id: str = "",
    force: bool = False,
) -> int:
    """Sequential import of every ISRC, with crash-safe status updates."""
    config = load_config()
    configure_resolver(str(config.get("language") or "fr"))
    _configure_youtube(paths, config)

    tracks_store = Tracks(paths.tracks_path)
    albums_store = Albums(paths.albums_path)

    # Clean any stale cancel flag from a previous aborted run.
    _clear_cancel_flag(paths)

    version, age_days, stale = check_yt_dlp_fresh()
    status: dict = {
        "status": "running",
        "run_id": run_id,
        "started_at": _now_iso(),
        "current": 0,
        "total": len(isrcs),
        "completed": [],
        "failed": [],
        "skipped": [],
        "current_title": "",
        "playlist_name": playlist_name,
        "playlist_added": 0,
        "cancellable": True,
        # Surfaced by the widget: a stale yt-dlp is the single most common
        # cause of a batch of "youtube_blocked" failures, and the user can
        # fix it in one command.
        "yt_dlp_version": version,
        "yt_dlp_stale": stale,
        "yt_dlp_update_cmd": yt_dlp_update_hint() if stale else "",
        "waiting_seconds": 0,
        "waiting_reason": "",
    }
    _write_status(paths.widget_status_path, status)
    log_event(
        "widget_import_start",
        run_id=run_id,
        total=len(isrcs),
        playlist=playlist_name or None,
        yt_dlp_version=version,
        yt_dlp_age_days=age_days,
    )
    if stale:
        log_event("yt_dlp_stale", version=version, age_days=age_days)

    _register_callbacks(paths, status)

    skip_index = {} if force else recent_permanent_failures(paths.widget_failures_path)

    # Importer pipeline pulled in lazily — avoids loading yt-dlp setup unless
    # we actually run an import (keeps the CLI startup tight).
    from music_manager.pipeline.importer import (  # noqa: PLC0415
        cleanup_covers,
        discard_pending,
        import_resolved_track,
    )

    new_failures: list[dict] = []
    imported_isrcs: list[str] = []
    cancelled = False
    try:
        for idx, isrc in enumerate(isrcs, start=1):
            if _check_cancel(paths):
                cancelled = True
                break
            status["current"] = idx
            status["current_title"] = ""
            _write_status(paths.widget_status_path, status)

            recent = skip_index.get(isrc)
            if recent:
                status["skipped"].append(
                    {
                        "isrc": isrc,
                        "reason": "recently_failed",
                        "detail": str(recent.get("detail") or recent.get("reason") or ""),
                        "title": str(recent.get("title") or ""),
                        "artist": str(recent.get("artist") or ""),
                    }
                )
                log_event("widget_import_skip_recent", isrc=isrc, run_id=run_id)
                _write_status(paths.widget_status_path, status)
                continue

            # Fast-path : ISRC déjà importé → on collecte son apple_id pour la
            # playlist sans repasser par Deezer/yt-dlp (gain : 1-3s/track + 0 net).
            existing = tracks_store.get_by_isrc(isrc)
            if existing and existing.get("apple_id"):
                apple_id = str(existing["apple_id"])
                # tracks.json can be ahead of reality: the user may have
                # deleted the track from Apple Music. Counting it as imported
                # would report a success and add a dead ID to the playlist.
                if _apple_id_alive(apple_id):
                    title = str(existing.get("title", ""))
                    status["completed"].append(
                        {"isrc": isrc, "apple_id": apple_id, "title": title}
                    )
                    status["current_title"] = f"{existing.get('artist', '')} — {title}".strip(" —")
                    log_event("widget_import_skip_existing", isrc=isrc, apple_id=apple_id)
                    _write_status(paths.widget_status_path, status)
                    continue
                log_event("widget_import_stale_apple_id", isrc=isrc, apple_id=apple_id)

            track = resolve_by_isrc(isrc, albums_store)
            if track is None:
                _record_failure(status, new_failures, isrc, "not_on_deezer", "not_on_deezer")
                log_event("widget_import_failed", isrc=isrc, reason="not_on_deezer")
                _write_status(paths.widget_status_path, status)
                continue

            status["current_title"] = f"{track.artist} — {track.title}"
            _write_status(paths.widget_status_path, status)

            try:
                pending = import_resolved_track(track, paths, tracks_store, albums_store)
            except Exception as exc:  # noqa: BLE001
                _record_failure(
                    status,
                    new_failures,
                    isrc,
                    "import_error",
                    str(exc)[:120],
                    title=track.title,
                    artist=track.artist,
                )
                log_event("widget_import_failed", isrc=isrc, reason=str(exc)[:200])
                _write_status(paths.widget_status_path, status)
                continue

            if pending is None and track.apple_id:
                status["completed"].append(
                    {"isrc": isrc, "apple_id": track.apple_id, "title": track.title}
                )
                imported_isrcs.append(isrc)
                log_event("widget_import_done", isrc=isrc, apple_id=track.apple_id)
            else:
                reason = pending.reason if pending else "no_apple_id"
                detail = (pending.detail if pending else "") or reason
                # No review screen here — the audio the pending track was
                # holding on to would leak into .tmp/ forever.
                discard_pending(pending)
                _record_failure(
                    status,
                    new_failures,
                    isrc,
                    reason,
                    detail,
                    title=track.title,
                    artist=track.artist,
                )
                log_event("widget_import_failed", isrc=isrc, reason=reason, detail=detail)

            # Crash safety after each item — never lose progress mid-run.
            tracks_store.save()
            albums_store.save()
            _write_status(paths.widget_status_path, status)
    finally:
        _unregister_callbacks()
        # The Textual UI wipes .tmp/ when it exits; the detached worker has to
        # clean up after itself or covers pile up indefinitely.
        cleanup_covers(paths.tmp_dir)

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

    record_failures(paths.widget_failures_path, new_failures)
    if imported_isrcs:
        clear_failures(paths.widget_failures_path, imported_isrcs)

    status["status"] = "cancelled" if cancelled else "done"
    status["finished_at"] = _now_iso()
    status["current_title"] = ""
    status["waiting_seconds"] = 0
    status["waiting_reason"] = ""
    _write_status(paths.widget_status_path, status)
    _clear_cancel_flag(paths)
    log_event(
        "widget_import_end",
        run_id=run_id,
        completed=len(status["completed"]),
        failed=len(status["failed"]),
        skipped=len(status["skipped"]),
        playlist_added=status["playlist_added"],
        cancelled=cancelled,
    )
    return EXIT_OK


# ── Private Functions ────────────────────────────────────────────────────────


def _configure_youtube(paths: Paths, config: dict) -> None:
    """Apply the YouTube settings the Textual UI applies at launch.

    ``__main__`` dispatches CLI sub-commands *before* it configures services,
    so the widget worker used to run with cookies disabled no matter what
    ``config.json`` said — every age-gated track failed here while the same
    track imported fine from the UI.
    """
    from music_manager.services.youtube import (  # noqa: PLC0415
        set_state_path,
        set_use_cookies,
    )

    set_state_path(paths.youtube_state_path)
    if config.get("youtube_cookies"):
        set_use_cookies(True)


def _register_callbacks(paths: Paths, status: dict) -> None:
    """Wire the throttle callbacks the Textual UI wires for its own imports.

    Without them a rate-limit backoff blocked the worker for up to 30 minutes
    with a frozen progress bar and an unresponsive cancel button.
    """
    from music_manager.services.youtube import (  # noqa: PLC0415
        set_cancel_check,
        set_rate_limit_callback,
    )

    def _on_rate_limit(seconds: int, reason: str) -> None:
        status["waiting_seconds"] = seconds
        status["waiting_reason"] = reason[:200]
        status["waiting_until"] = time.time() + seconds
        _write_status(paths.widget_status_path, status)

    set_rate_limit_callback(_on_rate_limit)
    set_cancel_check(lambda: _check_cancel(paths))


def _unregister_callbacks() -> None:
    """Drop the callbacks so nothing keeps writing to a finished run."""
    from music_manager.services.youtube import (  # noqa: PLC0415
        set_cancel_check,
        set_rate_limit_callback,
    )

    set_rate_limit_callback(None)
    set_cancel_check(None)


def _record_failure(
    status: dict,
    sink: list[dict],
    isrc: str,
    reason: str,
    detail: str,
    title: str = "",
    artist: str = "",
) -> None:
    """Append a failure to both the live status and the persistent store."""
    entry = {
        "isrc": isrc,
        "reason": reason,
        "detail": detail,
        "title": title,
        "artist": artist,
    }
    status["failed"].append(entry)
    sink.append({**entry, "at": time.time()})


def _apple_id_alive(apple_id: str) -> bool:
    """Return True if ``apple_id`` still exists in Apple Music.

    AppleScript being unavailable (Music not running, automation denied) must
    not turn every already-imported track into a re-download, so an
    unanswerable query is treated as "assume alive".
    """
    try:
        answered, alive = apple_ids_exist_checked([apple_id])
    except Exception:  # noqa: BLE001
        return True
    return apple_id in alive if answered else True


def _report_blocked(paths: Paths, reason: str, isrcs: list[str]) -> None:
    """Tell the caller it was refused without disturbing a running import.

    Writing ``{"status": "blocked"}`` unconditionally used to overwrite the
    status file of the import that was still in progress, so the widget showed
    "blocked" for a run that was working fine.
    """
    _emit({"status": "blocked", "reason": reason})
    current = read_status(paths.widget_status_path)
    if current.get("status") == "running":
        return
    # Timestamped: the file persists, and without an age the widget would keep
    # showing "blocked" long after the blocker was gone.
    _write_status(
        paths.widget_status_path,
        {"status": "blocked", "reason": reason, "isrcs": isrcs, "at": _now_iso()},
    )


def _emit(payload: dict) -> None:
    """Write a single JSON line on stdout for the widget to parse."""
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))


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


def _spawn_detached(
    raw_arg: str,
    playlist_name: str = "",
    playlist_cover_url: str = "",
    run_id: str = "",
    force: bool = False,
) -> None:
    """Re-spawn ourselves in a new session so the widget returns immediately."""
    cmd = [sys.executable, "-m", "music_manager", "import-isrcs", raw_arg]
    if playlist_name:
        cmd.extend(["--playlist-name", playlist_name])
    if playlist_cover_url:
        cmd.extend(["--playlist-cover-url", playlist_cover_url])
    if run_id:
        cmd.extend(["--run-id", run_id])
    if force:
        cmd.append("--force")
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
        ok, error = set_playlist_artwork(playlist_name, path)
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
        **({} if ok else {"reason": error[:200]}),
    )


def _write_status(path: str, payload: dict) -> None:
    """Atomic write of ``widget_status.json``."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False)
    os.replace(tmp, path)


def _now_iso() -> str:
    """Local timestamp with offset — same clock as logs.jsonl, unambiguous in JS."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


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
