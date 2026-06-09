"""Pytest configuration — auto-skip integration tests on non-macOS."""

import platform

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip integration tests when not running on macOS."""
    if platform.system() == "Darwin":
        return

    skip_marker = pytest.mark.skip(reason="Integration tests require macOS + Apple Music")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_marker)


@pytest.fixture(autouse=True)
def _block_real_osascript(request, monkeypatch):
    """Raise on any real osascript call — forgotten mocks pollute the user's Apple Music.

    Why: previously, tests covering `generate_recommendations` mocked
    `add_to_playlist` while the pipeline actually calls
    `add_to_playlist_in_folder` → real AppleScript ran → leftover
    "for me" folder + "library" playlist after each suite run.

    Tests that drive osascript verbs explicitly patch `run_applescript`
    themselves; that per-test patch overrides this guard. Integration
    tests (PyObjC `Apple().scan`, not osascript) are unaffected.
    """
    if "integration" in request.keywords:
        yield
        return

    def _refuse(_script: str) -> str | None:
        raise RuntimeError(
            "Real osascript call leaked from a test — mock the apple service "
            "function (e.g. add_to_playlist_in_folder) instead."
        )

    monkeypatch.setattr("music_manager.services.apple.run_applescript", _refuse)
    yield
