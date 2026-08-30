"""Tests for fetch_album_preview() — the cover-cheap album fetch."""

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


def test_preview_never_calls_itunes() -> None:
    """The preview path must not hit the iTunes cover search.

    Regression: building one recommendation feed fired 194 iTunes searches
    purely for artwork, and iTunes answered 107 of them with HTTP 429. The
    widget only needs a thumbnail, and Deezer already ships a 1000x1000.
    """
    store = _store()
    with (
        patch.object(resolver, "deezer_get", return_value=dict(_DEEZER_ALBUM)),
        patch.object(resolver, "_itunes_cover") as mock_itunes,
    ):
        result = resolver.fetch_album_preview(456, store)

    mock_itunes.assert_not_called()
    assert result["cover_url"] == "http://deezer.cover/xl.jpg"
    assert result["genre"] == "Pop"
    assert result["release_date"] == "2024-01-15"


def test_preview_marks_what_it_caches_as_low_res() -> None:
    """A preview is cached, but flagged so the import path can upgrade it.

    albums.json is what the import path reads to tag the real file. An
    unflagged Deezer-only 1000x1000 would silently downgrade the artwork of
    every track later imported from that album.
    """
    store = _store()
    with (
        patch.object(resolver, "deezer_get", return_value=dict(_DEEZER_ALBUM)),
        patch.object(resolver, "_itunes_cover", return_value=""),
    ):
        resolver.fetch_album_preview(456, store)

    store.put.assert_called_once()
    stored = store.put.call_args[0][1]
    assert stored["cover_hd"] is False


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


def test_preview_reuses_a_full_cache_entry() -> None:
    """A warm album (HD cover already fetched) is returned as-is, no call."""
    store = _store()
    cached = {"id": 123, "title": "Cached", "cover_url": "http://itunes/3000.jpg"}
    store.get.return_value = cached

    with patch.object(resolver, "deezer_get") as mock_dg:
        result = resolver.fetch_album_preview(123, store)

    assert result == cached
    mock_dg.assert_not_called()


def test_preview_returns_empty_for_zero_id() -> None:
    """album_id=0 short-circuits without touching the store."""
    store = _store()
    assert resolver.fetch_album_preview(0, store) == {}
    store.get.assert_not_called()
