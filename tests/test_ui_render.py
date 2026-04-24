"""Tests for ui/render.py — all rendering functions."""

from music_manager.core.models import PendingTrack
from music_manager.ui.render import (
    pad_to,
    render_batch_decision,
    render_complete_albums,
    render_complete_summary,
    render_duplicate_group,
    render_duplicates_summary,
    render_fix_body,
    render_fix_header,
    render_fix_summary,
    render_help,
    render_import_body,
    render_import_header,
    render_import_line,
    render_main_header,
    render_modify_actions,
    render_modify_covers,
    render_modify_editions,
    render_modify_metadata,
    render_modify_result,
    render_modify_search,
    render_modify_status,
    render_playlist_result,
    render_review_body,
    render_review_header,
    render_review_separator,
    render_sub_header,
    render_summary_line,
)

# ── Helpers ──────────────────────────────────────────────────────────────


def test_pad_to_positive_spacing() -> None:
    """pad_to returns spaces to align badge at COL."""
    result = pad_to("short", "badge")
    assert " " in result
    assert len(result) >= 1


def test_render_help_with_newline() -> None:
    """render_help with newline prepends newline."""
    txt = render_help("help text", with_newline=True)
    assert str(txt).startswith("\n")
    assert "help text" in str(txt)


def test_render_help_without_newline() -> None:
    """render_help without newline."""
    txt = render_help("help", with_newline=False)
    assert not str(txt).startswith("\n")


# ── Headers ──────────────────────────────────────────────────────────────


def test_render_main_header() -> None:
    """Main header shows title + stats."""
    txt = render_main_header(42, 10)
    plain = str(txt)
    assert "Music Manager" in plain
    assert "42" in plain
    assert "10" in plain


def test_render_sub_header() -> None:
    """Sub header shows title."""
    txt = render_sub_header("Outils")
    assert "Outils" in str(txt)


def test_render_import_header() -> None:
    """Import header shows CSV name + count."""
    txt = render_import_header("playlist.csv", 15)
    plain = str(txt)
    assert "playlist.csv" in plain
    assert "15" in plain


def test_render_review_header() -> None:
    """Review header shows pending count."""
    txt = render_review_header(5)
    assert "5" in str(txt)


# ── Import progress ─────────────────────────────────────────────────────


def test_render_import_line_done() -> None:
    """Done import line shows check mark."""
    txt = render_import_line(0, 5, "Song", "Artist", "done")
    plain = str(txt)
    assert "Song" in plain
    assert "Artist" in plain
    assert "1/5" in plain


def test_render_import_line_failed() -> None:
    """Failed import line shows status label."""
    txt = render_import_line(2, 10, "Track", "Art", "not_found")
    assert "introuvable" in str(txt)


def test_render_import_body() -> None:
    """Import body stacks lines."""
    lines = [
        render_import_line(0, 2, "A", "B", "done"),
        render_import_line(1, 2, "C", "D", "skipped"),
    ]
    txt = render_import_body(lines)
    plain = str(txt)
    assert "A" in plain
    assert "C" in plain


def test_render_summary_line() -> None:
    """Summary shows counts."""
    txt = render_summary_line(3, 1, 2, 0)
    plain = str(txt)
    assert "3" in plain
    assert "importée" in plain


def test_render_summary_line_no_actions() -> None:
    """Summary with all zeros."""
    txt = render_summary_line(0, 0, 0, 0)
    assert "Aucune action" in str(txt)


def test_render_playlist_result() -> None:
    """Playlist result shows name + counts."""
    txt = render_playlist_result("MyPlaylist", 5, 3)
    plain = str(txt)
    assert "MyPlaylist" in plain
    assert "5" in plain


# ── Review ───────────────────────────────────────────────────────────────


def test_render_review_separator() -> None:
    """Review separator shows idx/total."""
    txt = render_review_separator(3, 10)
    assert "3/10" in str(txt)


def test_render_review_body() -> None:
    """Review body renders pending track with actions."""
    pending = PendingTrack(
        reason="not_found",
        csv_title="Song",
        csv_artist="Artist",
        csv_album="Album",
    )
    txt = render_review_body(pending, ["skip", "search_deezer"], 0, 1, 5)
    plain = str(txt)
    assert "Song" in plain
    assert "Artist" in plain
    assert "Passer" in plain


def test_render_batch_decision() -> None:
    """Batch decision renders 3 options."""
    txt = render_batch_decision(0)
    plain = str(txt)
    assert "Tout accepter" in plain
    assert "Tout rejeter" in plain


# ── Fix metadata ─────────────────────────────────────────────────────────


def test_render_fix_header() -> None:
    """Fix header shows artist — album (count)."""
    txt = render_fix_header("Album", "Artist", 10)
    plain = str(txt)
    assert "Artist" in plain
    assert "Album" in plain
    assert "10" in plain


def test_render_fix_body() -> None:
    """Fix body renders checkboxes + actions."""
    divs = [("genre", "Genre", "Pop", "Rock", True), ("year", "Année", "2000", "1999", False)]
    actions = ["skip", "ignore"]
    txt = render_fix_body(divs, actions, 0, 2)
    plain = str(txt)
    assert "Genre" in plain
    assert "Pop" in plain
    assert "Rock" in plain
    assert "[x]" in plain
    assert "[ ]" in plain


def test_render_fix_body_cover_label() -> None:
    """Cover divergence shows quality info."""
    divs = [("cover", "Pochette", "600x600", "url", True)]
    txt = render_fix_body(divs, ["skip"], 0, 1)
    assert "600x600" in str(txt)
    assert "3000" in str(txt)


def test_render_fix_body_cover_missing() -> None:
    """Missing cover shows generic label."""
    divs = [("cover", "Pochette", "", "url", True)]
    txt = render_fix_body(divs, ["skip"], 0, 1)
    assert "meilleure qualité" in str(txt)


def test_render_fix_body_explicit() -> None:
    """Explicit divergence shows oui/non."""
    divs = [("explicit", "Explicit", "False", "True", True)]
    txt = render_fix_body(divs, ["skip"], 0, 1)
    plain = str(txt)
    assert "non" in plain
    assert "oui" in plain


def test_render_fix_summary() -> None:
    """Fix summary shows counts."""
    txt = render_fix_summary(5, 2, 1)
    plain = str(txt)
    assert "5" in plain
    assert "corrigé" in plain


def test_render_fix_summary_empty() -> None:
    """Fix summary with all zeros."""
    txt = render_fix_summary(0, 0, 0)
    assert "Aucune correction" in str(txt)


# ── Modify track ─────────────────────────────────────────────────────────


def test_render_modify_search_no_results() -> None:
    """Empty search results shows message."""
    txt = render_modify_search("test", [], [], 0, [])
    assert "Aucun résultat" in str(txt)


def test_render_modify_search_with_tracks() -> None:
    """Search results with tracks."""
    tracks = [("Song — Artist", "Album", "A1")]
    txt = render_modify_search("test", tracks, [], 0, [1])
    plain = str(txt)
    assert "Song" in plain
    assert "Pistes" in plain


def test_render_modify_actions() -> None:
    """Modify actions renders menu items."""
    items = [("edit", "Modifier"), None, ("back", "Retour")]
    txt = render_modify_actions(items, 0, [0, 2])
    plain = str(txt)
    assert "Modifier" in plain
    assert "Retour" in plain


def test_render_modify_editions() -> None:
    """Edition picker renders albums."""
    editions = [{"album": "Album A", "nb_tracks": 10, "year": "2020"}]
    txt = render_modify_editions(editions, 0)
    plain = str(txt)
    assert "Album A" in plain
    assert "2020" in plain
    assert "Retour" in plain


def test_render_modify_metadata() -> None:
    """Metadata editor renders fields + actions."""
    fields = [("Titre", "title", "Song"), ("Genre", "genre", "Rock")]
    txt = render_modify_metadata(fields, 0)
    plain = str(txt)
    assert "Titre" in plain
    assert "Song" in plain
    assert "Appliquer" in plain
    assert "Retour" in plain


def test_render_modify_covers() -> None:
    """Cover picker renders covers."""
    covers = [{"album": "Album", "year": "2020", "track_count": 10}]
    txt = render_modify_covers(covers, 0)
    plain = str(txt)
    assert "Album" in plain
    assert "Retour" in plain


def test_render_modify_status() -> None:
    """Status message renders dimmed."""
    txt = render_modify_status("Processing...")
    assert "Processing..." in str(txt)


def test_render_modify_result_success() -> None:
    """Success result shows check mark."""
    txt = render_modify_result(True)
    assert "effectuée" in str(txt)


def test_render_modify_result_error() -> None:
    """Error result shows error message."""
    txt = render_modify_result(False, "deezer_resolve_failed")
    assert "Deezer" in str(txt)


# ── Duplicates ───────────────────────────────────────────────────────────


def test_render_duplicate_group() -> None:
    """Duplicate group renders entries + actions."""
    group = [
        {"title": "Song", "artist": "Art", "album": "Al1", "duration": 200},
        {"title": "Song", "artist": "Art", "album": "Al2", "duration": 195},
    ]
    txt = render_duplicate_group(group, 0, 0, 1, 5, ["Passer", "Ignorer"])
    plain = str(txt)
    assert "Song" in plain
    assert "Art" in plain
    assert "recommandé" in plain
    assert "Passer" in plain


def test_render_duplicates_summary() -> None:
    """Duplicates summary shows counts."""
    txt = render_duplicates_summary(3, 1, 2)
    plain = str(txt)
    assert "3" in plain
    assert "supprimé" in plain


def test_render_duplicates_summary_empty() -> None:
    """Duplicates summary with all zeros."""
    txt = render_duplicates_summary(0, 0, 0)
    assert "Aucun doublon" in str(txt)


# ── Complete albums ──────────────────────────────────────────────────────


def test_render_complete_albums() -> None:
    """Complete albums renders checkboxes with counts."""

    albums = [
        {"title": "Album A", "artist": "Art1", "local": 5, "total": 12},
        {"title": "Album B", "artist": "Art2", "local": 8, "total": 10},
    ]
    txt = render_complete_albums(albums, [True, False], 0, ["Compléter", "Retour"])
    plain = str(txt)
    assert "Album A" in plain
    assert "[x]" in plain
    assert "[ ]" in plain
    assert "5/12" in plain
    assert "Compléter" in plain


def test_render_complete_summary_imported() -> None:
    """Complete summary shows imported count."""

    txt = render_complete_summary(5, 1)
    plain = str(txt)
    assert "5" in plain
    assert "importée" in plain
    assert "1" in plain
    assert "échouée" in plain


def test_render_complete_summary_none() -> None:
    """Complete summary with nothing to import."""

    txt = render_complete_summary(0, 0)
    assert "Aucune piste" in str(txt)
