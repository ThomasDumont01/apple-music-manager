"""`python -m music_manager spotify-login [--detach]` — OAuth PKCE flow.

Drives the Spotify OAuth handshake for the Übersicht widget. Opens the
system browser to Spotify's authorize page, then catches the redirect on a
local HTTP server (``127.0.0.1:8765/callback``). On success, persists the
``access`` + ``refresh`` tokens in ``config.json`` (chmod 600).

JSON stdout: ``{"status": "ok"}`` on success, ``{"status": "running"}`` for
``--detach``, or ``{"error": "..."}`` otherwise. Exit code 0 on success, 1
on failure.

With ``--detach``, the worker is spawned in a new session so the widget's
subprocess returns instantly. The widget then polls ``spotify-auth-status``.
"""

import argparse
import json
import secrets
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from music_manager.services.spotify import (
    build_auth_url,
    exchange_code,
    get_client_id,
    pkce_verifier_challenge,
    save_tokens,
)

# ── Constants ────────────────────────────────────────────────────────────────

_CALLBACK_HOST = "127.0.0.1"
_CALLBACK_PORT = 8765
_CALLBACK_PATH = "/callback"
_DEFAULT_TIMEOUT = 300  # 5 minutes


# ── Entry point ──────────────────────────────────────────────────────────────


def main(args: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="music_manager spotify-login")
    parser.add_argument(
        "--detach",
        action="store_true",
        help="Spawn the OAuth worker in background and return immediately.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=_DEFAULT_TIMEOUT,
        help="Max seconds to wait for the OAuth callback (default 300).",
    )
    parsed = parser.parse_args(args)

    if not get_client_id():
        sys.stdout.write(json.dumps({"error": "missing_client_id"}))
        return 1

    if parsed.detach:
        _spawn_detached()
        sys.stdout.write(json.dumps({"status": "running"}))
        return 0

    try:
        result = _run_oauth_flow(parsed.timeout)
    except Exception as exc:  # noqa: BLE001
        sys.stdout.write(json.dumps({"error": str(exc)[:200]}))
        return 1

    if "error" in result:
        sys.stdout.write(json.dumps(result))
        return 1
    sys.stdout.write(json.dumps({"status": "ok"}))
    return 0


# ── Private Functions ────────────────────────────────────────────────────────


def _run_oauth_flow(timeout: int) -> dict:
    """Drive the OAuth PKCE handshake. Returns ``{}`` on success or ``{"error": ...}``."""
    verifier, challenge = pkce_verifier_challenge()
    state = secrets.token_urlsafe(16)
    url = build_auth_url(state=state, code_challenge=challenge)

    received: dict[str, str] = {}
    server = _build_callback_server(received, state)

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        webbrowser.open(url)
        deadline = time.time() + timeout
        while time.time() < deadline and "code" not in received and "error" not in received:
            time.sleep(0.2)
    finally:
        server.shutdown()
        server.server_close()

    if "error" in received:
        return {"error": received["error"]}
    if "code" not in received:
        return {"error": "timeout"}

    try:
        payload = exchange_code(received["code"], verifier)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"exchange_failed:{str(exc)[:100]}"}
    access = str(payload.get("access_token") or "")
    refresh = str(payload.get("refresh_token") or "")
    expires_in = int(payload.get("expires_in") or 3600)
    if not access or not refresh:
        return {"error": "incomplete_token_response"}
    save_tokens(access, refresh, expires_in)
    _log("spotify_login_ok")
    return {}


def _build_callback_server(received: dict, expected_state: str) -> HTTPServer:
    """Build a single-shot HTTP server that captures ``code`` from the callback."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — stdlib API
            parsed = urlparse(self.path)
            if parsed.path != _CALLBACK_PATH:
                self.send_response(404)
                self.end_headers()
                return
            params = parse_qs(parsed.query)
            if "error" in params:
                received["error"] = params["error"][0]
                self._respond_html("Erreur lors de la connexion Spotify.")
                return
            state = (params.get("state") or [""])[0]
            if state != expected_state:
                received["error"] = "state_mismatch"
                self._respond_html("Erreur — état OAuth invalide.")
                return
            code = (params.get("code") or [""])[0]
            if not code:
                received["error"] = "missing_code"
                self._respond_html("Erreur — code manquant.")
                return
            received["code"] = code
            self._respond_html(
                "<h2>✓ Connexion réussie</h2><p>Vous pouvez fermer cette fenêtre.</p>"
            )

        def _respond_html(self, body: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            page = (
                "<!doctype html><html><head><meta charset='utf-8'>"
                "<title>Music Manager</title></head>"
                "<body style='font-family:system-ui;text-align:center;"
                f"padding:40px;color:#1c1c1e'>{body}</body></html>"
            )
            self.wfile.write(page.encode("utf-8"))

        def log_message(self, *_args: Any, **_kwargs: Any) -> None:
            """Silence the default access log."""
            return

    return HTTPServer((_CALLBACK_HOST, _CALLBACK_PORT), Handler)


def _spawn_detached() -> None:
    """Re-spawn ourselves in a new session so the widget returns immediately."""
    cmd = [sys.executable, "-m", "music_manager", "spotify-login"]
    subprocess.Popen(  # noqa: S603
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )


def _log(action: str, **data: object) -> None:
    try:
        from music_manager.core.logger import log_event  # noqa: PLC0415

        log_event(action, **data)
    except Exception:  # noqa: BLE001
        pass
