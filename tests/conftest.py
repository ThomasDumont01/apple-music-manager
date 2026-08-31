"""Pytest configuration — auto-skip integration tests on non-macOS."""

import platform

import pytest


@pytest.fixture(autouse=True)
def _sandbox_config_dir(tmp_path_factory, monkeypatch):
    """Point CONFIG_DIR at a throwaway folder for every test.

    Why: ``Paths`` derives the lock, status and failure files from
    ``core.config.CONFIG_DIR``. A test that forgot to sandbox it therefore
    operated on the user's real ``~/.config/music_manager/`` — and the
    startup sweep that drops crash-orphaned locks happily deleted the real
    ``.ui.lock``. Tests that need their own directory still override this
    (their monkeypatch runs after ours and wins).
    """
    sandbox = tmp_path_factory.mktemp("config_dir")
    monkeypatch.setattr("music_manager.core.config.CONFIG_DIR", str(sandbox))
    monkeypatch.setattr("music_manager.core.config.CONFIG_PATH", str(sandbox / "config.json"))
    return sandbox


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

    Why: a test that mocks one helper while the code under test calls a
    neighbouring one lets real AppleScript through, and the suite then
    edits the user's actual library — creating playlists or importing
    files that outlive the run.

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
            "function (e.g. import_file) instead."
        )

    monkeypatch.setattr("music_manager.services.apple.run_applescript", _refuse)
    # Both entry points must be sealed: `run_applescript_result` is the one
    # that actually spawns osascript, and helpers built on it (playlist
    # artwork, apple_ids_exist_checked) would otherwise slip past the guard.
    monkeypatch.setattr("music_manager.services.apple.run_applescript_result", _refuse)

    # The library scan goes through PyObjC, not osascript, so it slipped past
    # the guard above: unit tests were reading the user's real Apple Music
    # library, which made their outcome depend on what happens to be in it.
    def _refuse_scan(*_args, **_kwargs):
        raise RuntimeError(
            "Real Apple Music library scan leaked from a test — patch the "
            "caller (e.g. options.import_tracks._live_apple_ids) instead."
        )

    monkeypatch.setattr("music_manager.services.apple._scan_itunes_library", _refuse_scan)
    yield
