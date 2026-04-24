"""Tests for core/setup.py."""

import os
from pathlib import Path

from music_manager.core.setup import create_data_folders


def test_create_data_folders(tmp_path: Path) -> None:
    """Creates correct folder structure."""
    root = str(tmp_path)
    create_data_folders(root)

    assert os.path.isdir(os.path.join(root, ".data"))
    assert os.path.isdir(os.path.join(root, ".tmp"))
    assert os.path.isdir(os.path.join(root, "playlists"))
    assert os.path.isdir(os.path.join(root, "raccourcis"))
    assert os.path.isfile(os.path.join(root, "requetes.csv"))
    assert os.path.isfile(os.path.join(root, ".data", "tracks.json"))
