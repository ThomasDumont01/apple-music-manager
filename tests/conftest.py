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
