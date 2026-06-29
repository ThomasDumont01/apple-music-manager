"""End-to-end tests for music_manager.cli.spotify_login (mocked)."""

import json
import threading
import time
from urllib.parse import parse_qs, urlparse

import pytest

from music_manager.cli import spotify_login


def test_missing_client_id_returns_error(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(spotify_login, "get_client_id", lambda: "")
    exit_code = spotify_login.main([])
    assert exit_code == 1
    out = json.loads(capsys.readouterr().out)
    assert out == {"error": "missing_client_id"}


def test_detach_spawns_and_returns_immediately(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(spotify_login, "get_client_id", lambda: "CLIENT_ID")
    spawned: list[bool] = []
    monkeypatch.setattr(spotify_login, "_spawn_detached", lambda: spawned.append(True))
    exit_code = spotify_login.main(["--detach"])
    assert exit_code == 0
    assert spawned == [True]
    out = json.loads(capsys.readouterr().out)
    assert out == {"status": "running"}


def _post_callback_after_delay(url_callback: str, delay: float = 0.05) -> None:
    """Deliver a callback request to the local server in a background thread."""

    def deliver() -> None:
        time.sleep(delay)
        try:
            import requests  # noqa: PLC0415

            requests.get(url_callback, timeout=2)
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=deliver, daemon=True).start()


def test_full_oauth_flow_success(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(spotify_login, "get_client_id", lambda: "CLIENT_ID")
    monkeypatch.setattr(
        spotify_login, "pkce_verifier_challenge", lambda: ("VERIFIER", "CHALLENGE")
    )

    def fake_browser_open(url: str) -> None:
        state = parse_qs(urlparse(url).query)["state"][0]
        _post_callback_after_delay(f"http://127.0.0.1:8765/callback?code=AUTH_CODE&state={state}")

    monkeypatch.setattr(spotify_login.webbrowser, "open", fake_browser_open)
    monkeypatch.setattr(
        spotify_login,
        "exchange_code",
        lambda code, v: {
            "access_token": "AT",
            "refresh_token": "RT",
            "expires_in": 3600,
        },
    )
    saved: dict[str, object] = {}
    monkeypatch.setattr(
        spotify_login,
        "save_tokens",
        lambda a, r, e: saved.update(access=a, refresh=r, expires_in=e),
    )
    exit_code = spotify_login.main(["--timeout", "5"])
    assert exit_code == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"status": "ok"}
    assert saved == {"access": "AT", "refresh": "RT", "expires_in": 3600}


def test_state_mismatch_returns_error(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(spotify_login, "get_client_id", lambda: "CLIENT_ID")
    monkeypatch.setattr(spotify_login, "pkce_verifier_challenge", lambda: ("V", "C"))

    def fake_open(url: str) -> None:
        _post_callback_after_delay("http://127.0.0.1:8765/callback?code=X&state=WRONG_STATE")

    monkeypatch.setattr(spotify_login.webbrowser, "open", fake_open)
    exit_code = spotify_login.main(["--timeout", "5"])
    assert exit_code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["error"] == "state_mismatch"


def test_timeout_returns_error(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(spotify_login, "get_client_id", lambda: "CLIENT_ID")
    monkeypatch.setattr(spotify_login, "pkce_verifier_challenge", lambda: ("V", "C"))
    monkeypatch.setattr(spotify_login.webbrowser, "open", lambda url: None)
    exit_code = spotify_login.main(["--timeout", "1"])
    assert exit_code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["error"] == "timeout"


def test_spotify_error_in_callback(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(spotify_login, "get_client_id", lambda: "CLIENT_ID")
    monkeypatch.setattr(spotify_login, "pkce_verifier_challenge", lambda: ("V", "C"))

    def fake_open(url: str) -> None:
        _post_callback_after_delay("http://127.0.0.1:8765/callback?error=access_denied")

    monkeypatch.setattr(spotify_login.webbrowser, "open", fake_open)
    exit_code = spotify_login.main(["--timeout", "5"])
    assert exit_code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["error"] == "access_denied"


def test_exchange_failure_returns_error(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(spotify_login, "get_client_id", lambda: "CLIENT_ID")
    monkeypatch.setattr(spotify_login, "pkce_verifier_challenge", lambda: ("V", "C"))

    def fake_open(url: str) -> None:
        state = parse_qs(urlparse(url).query)["state"][0]
        _post_callback_after_delay(f"http://127.0.0.1:8765/callback?code=X&state={state}")

    monkeypatch.setattr(spotify_login.webbrowser, "open", fake_open)

    def raise_err(_code: str, _v: str) -> dict:
        raise RuntimeError("bad")

    monkeypatch.setattr(spotify_login, "exchange_code", raise_err)
    exit_code = spotify_login.main(["--timeout", "5"])
    assert exit_code == 1
    out = json.loads(capsys.readouterr().out)
    assert "exchange_failed" in out["error"]


def test_incomplete_token_response(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spotify can hypothetically return without one of the tokens."""
    monkeypatch.setattr(spotify_login, "get_client_id", lambda: "CLIENT_ID")
    monkeypatch.setattr(spotify_login, "pkce_verifier_challenge", lambda: ("V", "C"))

    def fake_open(url: str) -> None:
        state = parse_qs(urlparse(url).query)["state"][0]
        _post_callback_after_delay(f"http://127.0.0.1:8765/callback?code=X&state={state}")

    monkeypatch.setattr(spotify_login.webbrowser, "open", fake_open)
    monkeypatch.setattr(
        spotify_login,
        "exchange_code",
        lambda c, v: {"access_token": "AT"},  # no refresh_token
    )
    exit_code = spotify_login.main(["--timeout", "5"])
    assert exit_code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["error"] == "incomplete_token_response"
