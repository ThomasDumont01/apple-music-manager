"""Upgrade path for the low-res covers cached by v1.4.0."""

from unittest.mock import MagicMock, patch

from music_manager.services import resolver

_DEEZER_ALBUM = {
    "title": "Test Album",
    "artist": {"name": "Test Artist"},
    "genres": {"data": [{"name": "Pop"}]},
    "cover_xl": "http://deezer.cover/xl.jpg",
    "release_date": "2024-01-15",
    "nb_tracks": 12,
    "nb_disk": 1,
}


def _store() -> MagicMock:
    store = MagicMock()
    store.get.return_value = None
    return store


def test_full_fetch_upgrades_a_preview_cache_entry() -> None:
    """A low-res cached entry is a miss for the HD path, not a hit."""
    store = _store()
    store.get.return_value = {"id": 77, "title": "Preview", "cover_hd": False}

    with (
        patch.object(resolver, "deezer_get", return_value=dict(_DEEZER_ALBUM)),
        patch.object(resolver, "_itunes_cover", return_value="http://itunes/3000.jpg"),
        patch.object(resolver, "_pick_best_cover", return_value="http://itunes/3000.jpg"),
    ):
        result = resolver.fetch_album_with_cover(77, store)

    assert result["cover_url"] == "http://itunes/3000.jpg"
    assert result["cover_hd"] is True


def test_full_fetch_trusts_legacy_entries_without_the_flag() -> None:
    """Entries written before the flag existed came from the HD path."""
    store = _store()
    legacy = {"id": 88, "title": "Legacy", "cover_url": "http://itunes/3000.jpg"}
    store.get.return_value = legacy

    with patch.object(resolver, "deezer_get") as mock_dg:
        result = resolver.fetch_album_with_cover(88, store)

    assert result == legacy
    mock_dg.assert_not_called()
