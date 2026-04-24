"""Rendering functions — build Rich Text objects for display.

Change these to adjust how things look without touching logic.
"""

from rich.cells import cell_len
from rich.text import Text

from music_manager.core.models import PendingTrack
from music_manager.ui.styles import BLUE, CHECK, COL, CROSS, MARKER, MARKER_EMPTY, SEP, SKIP, WARN
from music_manager.ui.text import (
    ACTION_LABELS,
    REASON_LABELS,
    REVIEW_EDITION,
    STATUS_LABELS,
    SUMMARY_DELETED,
    SUMMARY_EXISTING,
    SUMMARY_FAILED,
    SUMMARY_IGNORED,
    SUMMARY_IMPORTED,
)

# ── Helpers ─────────────────────────────────────────────────────────────────


def _fmt_duration(seconds: int) -> str:
    """Format seconds as m:ss (e.g. 185 → '3:05')."""
    return f"{seconds // 60}:{seconds % 60:02d}"


def pad_to(left: str, badge: str) -> str:
    """Spacing to right-align badge at COL."""
    return " " * max(1, COL - cell_len(left) - cell_len(badge))


def render_help(text: str, with_newline: bool = True) -> Text:
    """Render a dim help bar."""
    txt = Text()
    if with_newline:
        txt.append("\n")
    txt.append(text, style="dim")
    return txt


# ── Header ──────────────────────────────────────────────────────────────────


def render_main_header(tracks_count: int, albums_count: int) -> Text:
    """Music Manager title + stats."""
    title = "Music Manager"
    stats = f"{tracks_count} pistes · {albums_count} albums"
    spacing = pad_to(title, stats)
    txt = Text()
    txt.append(title, style=f"bold {BLUE}")
    txt.append(spacing + stats, style="dim")
    return txt


def render_sub_header(title: str) -> Text:
    """Submenu header."""
    return Text(title, style=f"bold {BLUE}")


def render_import_header(csv_name: str, total: int) -> Text:
    """Import header: Traitement : name    N ♪."""
    title = f"Traitement : {csv_name}"
    stats = f"{total} pistes"
    spacing = pad_to(title, stats)
    txt = Text()
    txt.append(title, style=f"bold {BLUE}")
    txt.append(spacing + stats, style="dim")
    return txt


def render_review_header(pending_count: int) -> Text:
    """Review header: Revue    N en attente."""
    title = "Revue"
    stats = f"{pending_count} en attente"
    spacing = pad_to(title, stats)
    txt = Text()
    txt.append(title, style=f"bold {BLUE}")
    txt.append(spacing + stats, style="dim")
    return txt


# ── Menu body ───────────────────────────────────────────────────────────────


def render_menu_options(
    items: list[tuple[str, str] | None],
    selectable: list[int],
    cursor: int,
    view: str,
) -> Text:
    """Render a list of menu items with cursor, badges, sections."""
    body = Text()

    for i, item in enumerate(items):
        if i > 0:
            body.append("\n")

        if item is None:
            body.append(f"\n  {SEP * 3}", style="dim")
            body.append("\n")
            continue

        key, raw = item

        # Named separator: ("__sep__", "Section Label")
        if key == "__sep__":
            sep_len = max(1, COL - cell_len(raw) - 7)
            body.append(f"\n  {SEP * 3} {raw} {SEP * sep_len}", style="dim")
            body.append("\n")
            continue

        parts = raw.split("|")
        label = parts[0]
        badge = parts[1] if len(parts) > 1 else ""
        style = parts[2] if len(parts) > 2 else ""

        is_active = i in selectable and i == selectable[cursor]
        marker = MARKER if is_active else MARKER_EMPTY
        plain_left = f"{marker}{label}"
        spacing = pad_to(plain_left, badge) if badge else ""

        if is_active:
            body.append(marker, style=f"bold {BLUE}")
        else:
            body.append(marker)

        if style == "red":
            body.append(label, style="bold red" if is_active else "red")
        elif is_active:
            body.append(label, style=f"bold {BLUE}")
        else:
            body.append(label)

        if badge:
            body.append(spacing)
            if style == "red":
                body.append(badge, style="red")
            elif style == "csv":
                body.append(badge, style="green")
            else:
                body.append(badge)

    return body


# ── Import progress ─────────────────────────────────────────────────────────


def render_import_line(idx: int, total: int, title: str, artist: str, status: str) -> Text:
    """Single import progress line: ✓  [1/5] Title — Artist  (status)."""
    line = Text()

    # Symbol
    if status == "done":
        line.append(f"  {CHECK}  ", style="green")
    elif status == "skipped":
        line.append(f"  {SKIP}  ", style="dim")
    elif status in ("not_found", "youtube_failed"):
        line.append(f"  {CROSS}  ", style="red")
    else:
        line.append(f"  {WARN}  ", style="yellow")

    # Counter
    line.append(f"[{idx + 1}/{total}] ", style="dim")

    # Title — Artist
    line.append(f"{title} ", style="bold" if status == "done" else "")
    line.append(f"— {artist}", style="dim")

    # Status suffix
    suffix = STATUS_LABELS.get(status, status)
    if suffix:
        line.append(f"  ({suffix})", style="dim")

    return line


def render_import_body(lines: list[Text]) -> Text:
    """Full import progress body: all lines stacked."""
    body = Text()
    for line in lines:
        body.append_text(line)
        body.append("\n")
    return body


def render_summary_line(
    imported: int, skipped: int, pending: int, deleted: int = 0, ignored: int = 0
) -> Text:
    """Summary: ✓  N importée(s) · N existante(s) · N passée(s) · N supprimée(s) · N ignorée(s)."""
    txt = Text()
    parts = []
    if imported:
        parts.append(f"{imported} {SUMMARY_IMPORTED}")
    if skipped:
        parts.append(f"{skipped} {SUMMARY_EXISTING}")
    if pending:
        parts.append(f"{pending} {SUMMARY_FAILED}")
    if deleted:
        parts.append(f"{deleted} {SUMMARY_DELETED}")
    if ignored:
        parts.append(f"{ignored} {SUMMARY_IGNORED}")

    if parts:
        txt.append(f"  {CHECK}  ", style="green")
        txt.append(" · ".join(parts))
    else:
        txt.append(f"  {CHECK}  Aucune action", style="green")
    return txt


def render_final_summary(
    lines: list[Text],
    imported: int,
    skipped: int,
    pending_left: int,
    deleted: int = 0,
    ignored: int = 0,
) -> Text:
    """Full summary: import lines + summary counts."""
    body = Text()
    for line in lines:
        body.append_text(line)
        body.append("\n")
    body.append("\n")
    body.append_text(render_summary_line(imported, skipped, pending_left, deleted, ignored))
    body.append("\n")
    return body


def render_playlist_result(name: str, added: int, already: int) -> Text:
    """Playlist result: ✓ Playlist 'name' : N ajoutée(s), N déjà présente(s)."""
    txt = Text()
    txt.append(f"\n  {CHECK}  ", style="green")
    txt.append(f"Playlist '{name}'", style="bold")
    parts = []
    if added:
        parts.append(f"{added} ajoutée(s)")
    if already:
        parts.append(f"{already} déjà présente(s)")
    if parts:
        txt.append(f" : {', '.join(parts)}\n", style="dim")
    else:
        txt.append(" : à jour\n", style="dim")
    return txt


# ── Review ──────────────────────────────────────────────────────────────────


def render_review_separator(idx: int, total: int) -> Text:
    """Numbered separator: ──── 1/3 ────."""
    label = f" {idx}/{total} "
    sep_side = max(1, (COL - cell_len(label)) // 2 - 2)
    txt = Text()
    txt.append(f"  {SEP * sep_side}{label}{SEP * sep_side}\n\n", style="dim")
    return txt


def render_review_body(
    pending: PendingTrack, options: list[str], cursor: int, idx: int, total: int
) -> Text:
    """Render a pending track for review: info + details + action menu."""
    body = Text()

    # Numbered separator
    body.append_text(render_review_separator(idx, total))

    # Symbol + track info
    if pending.reason in ("not_found", "youtube_failed"):
        body.append(f"  {CROSS}  ", style="red")
    else:
        body.append(f"  {WARN}  ", style="yellow")

    body.append(f"{pending.csv_title}", style="bold")
    body.append(f" — {pending.csv_artist}", style="dim")
    if pending.csv_album:
        body.append(f"  [{pending.csv_album}]", style="dim")
    body.append("\n")

    # Reason
    reason_text = REASON_LABELS.get(pending.reason, pending.reason)
    body.append(f"     {reason_text}\n", style="dim")

    # Details (reason-specific)
    if pending.reason == "mismatch":
        # Mismatch: track is None, details are in candidates[0]
        if pending.candidates:
            c = pending.candidates[0]
            dz_title = c.get("title", "")
            dz_album = c.get("album", {}).get("title", "")
            dz_artist = c.get("artist", {}).get("name", "")
            body.append(f"\n     Deezer : {dz_title}", style="bold")
            body.append(f" · {dz_album}", style="dim")
            body.append(f" · {dz_artist}\n", style="dim")
        elif pending.track:
            body.append(f"\n     Deezer : {pending.track.title}", style="bold")
            body.append(f" · {pending.track.album}", style="dim")
            body.append(f" · {pending.track.artist}\n", style="dim")

    elif pending.reason == "duration_suspect" and pending.track:
        expected = pending.track.duration
        actual = pending.actual_duration
        pct = round(abs(actual - expected) / expected * 100) if expected else 0
        body.append(
            f"\n     Durée : Deezer {_fmt_duration(expected)}"
            f" → YouTube {_fmt_duration(actual)} ({pct}%)\n",
            style="dim",
        )

    elif pending.reason == "ambiguous" and pending.candidates:
        body.append(f"\n  {REVIEW_EDITION}\n")

    body.append("\n")

    # Action menu
    body.append_text(_render_action_menu(pending, options, cursor))

    return body


def _render_action_menu(pending: PendingTrack, options: list[str], cursor: int) -> Text:
    """Render the navigable action menu for a review item."""
    from music_manager.core.normalize import normalize  # noqa: PLC0415

    body = Text()

    # Find best candidate: album match → title exact match → first non-live/remix
    best_candidate_idx = -1
    if pending.reason == "ambiguous" and pending.candidates:
        norm_csv = normalize(pending.csv_album) if pending.csv_album else ""
        norm_title = normalize(pending.csv_title) if pending.csv_title else ""
        # Pass 1: album name matches csv_album
        if norm_csv:
            for ci, cand in enumerate(pending.candidates):
                if normalize(cand.get("album", {}).get("title", "")) == norm_csv:
                    best_candidate_idx = ci
                    break
        # Pass 2: exact title match on non-live/remix/demo variant
        if best_candidate_idx == -1 and norm_title:
            _skip = {"live", "remix", "demo", "acoustic", "instrumental"}
            for ci, cand in enumerate(pending.candidates):
                ct = cand.get("title", "").lower()
                if normalize(ct) == norm_title and not any(s in ct for s in _skip):
                    best_candidate_idx = ci
                    break
        # Pass 3: first candidate (fallback)
        if best_candidate_idx == -1:
            best_candidate_idx = 0

    for i, key in enumerate(options):
        is_active = i == cursor
        marker = MARKER if is_active else MARKER_EMPTY

        # Separator before non-candidate actions in ambiguous
        if key == "search_deezer" and pending.reason == "ambiguous" and i > 0:
            body.append(f"  {SEP * 3}\n", style="dim")

        if key.startswith("candidate:"):
            # Ambiguous candidate
            cidx = int(key.split(":")[1])
            candidate = pending.candidates[cidx]
            title = candidate.get("title", "")
            album = candidate.get("album", {}).get("title", "")
            nb_tracks = candidate.get("album", {}).get("nb_tracks", 0)

            if is_active:
                body.append(f"  {marker}", style=f"bold {BLUE}")
                body.append(title, style=f"bold {BLUE}")
            else:
                body.append(f"  {marker}{title}")

            # Album + track count after title
            if album:
                body.append(f"  {album}", style="dim")
            if nb_tracks:
                pl = "s" if nb_tracks != 1 else ""
                body.append(f" ({nb_tracks} piste{pl})", style="dim")
            if cidx == best_candidate_idx:
                body.append("  (recommandé)", style="green")
            body.append("\n")
        else:
            # Standard action
            label = ACTION_LABELS.get(key, key)
            if is_active:
                body.append(f"  {marker}", style=f"bold {BLUE}")
                body.append(label, style=f"bold {BLUE}")
            else:
                body.append(f"  {marker}{label}")
            body.append("\n")

    return body


def render_batch_decision(cursor: int) -> Text:
    """Render batch decision: Tout accepter / Tout rejeter / Un par un."""
    from music_manager.ui.text import (  # noqa: PLC0415
        REVIEW_BATCH_ALL,
        REVIEW_BATCH_ONE,
        REVIEW_BATCH_REJECT,
    )

    options = [
        (REVIEW_BATCH_ALL, "Accepte la première version Deezer pour chaque piste"),
        (REVIEW_BATCH_REJECT, "Conserve les pistes en attente dans le CSV"),
        (REVIEW_BATCH_ONE, "Choisir manuellement pour chaque piste"),
    ]
    body = Text()
    body.append("\n")
    for i, (option, hint) in enumerate(options):
        is_active = i == cursor
        marker = MARKER if is_active else MARKER_EMPTY
        if is_active:
            body.append(f"  {marker}", style=f"bold {BLUE}")
            body.append(option, style=f"bold {BLUE}")
        else:
            body.append(f"  {marker}{option}")
        body.append(f"\n     {hint}\n\n", style="dim")
    return body


# ── Fix metadata ───────────────────────────────────────────────────────────


def render_fix_header(
    album_title: str, artist: str, track_count: int, idx: int = 0, total: int = 0
) -> Text:
    """Fix metadata header: Artist — Album (N pistes) (idx/total)."""
    txt = Text()
    txt.append(f"{artist}", style=f"bold {BLUE}")
    txt.append(f" — {album_title}", style="bold")
    pl = "s" if track_count != 1 else ""
    txt.append(f"  ({track_count} piste{pl})", style="dim")
    if total > 1:
        txt.append(f"  ({idx}/{total})", style="dim")
    return txt


def render_fix_body(
    divergences_labels: list[tuple[str, str, str, str, bool]],
    actions: list[str],
    cursor: int,
    num_divs: int,
) -> Text:
    """Render fix-metadata body: checkboxes + actions.

    divergences_labels: [(field_name, field_label, local_val, deezer_val, checked), ...]
    actions: ["apply", "skip", "ignore"]
    cursor: current position (0..len(divs)+len(actions)-1)
    num_divs: len(divergences_labels)
    """
    from music_manager.ui.text import (  # noqa: PLC0415
        FIX_APPLY,
        FIX_IGNORE,
        FIX_SKIP,
    )

    body = Text()

    # Checkboxes
    for i, (field_name, field_label, local_val, deezer_val, checked) in enumerate(
        divergences_labels
    ):
        is_active = i == cursor
        checkbox = f"[{'x' if checked else ' '}]"
        marker = MARKER if is_active else MARKER_EMPTY

        if is_active:
            body.append(f"  {marker}", style=f"bold {BLUE}")
            body.append(f"{checkbox} ", style=f"bold {BLUE}")
            body.append(f"{field_label}", style=f"bold {BLUE}")
        else:
            body.append(f"  {marker}{checkbox} {field_label}")

        if field_name == "cover":
            if local_val:
                body.append(f" : {local_val} → 3000×3000\n", style="dim")
            else:
                body.append(" : meilleure qualité disponible\n", style="dim")
        elif field_name == "explicit":
            display_local = "oui" if local_val == "True" else "non"
            display_deezer = "oui" if deezer_val == "True" else "non"
            body.append(f" : {display_local} → {display_deezer}\n", style="dim")
        else:
            body.append(f" : « {local_val} » → « {deezer_val} »\n", style="dim")

    # Separator
    body.append(f"\n  {SEP * 3}\n", style="dim")

    # Actions
    action_labels = {"apply": FIX_APPLY, "skip": FIX_SKIP, "ignore": FIX_IGNORE}
    for j, action in enumerate(actions):
        idx = num_divs + j
        is_active = idx == cursor
        marker = MARKER if is_active else MARKER_EMPTY
        label = action_labels.get(action, action)

        if is_active:
            body.append(f"  {marker}", style=f"bold {BLUE}")
            body.append(label, style=f"bold {BLUE}")
        else:
            body.append(f"  {marker}{label}")
        body.append("\n")

    return body


def render_fix_summary(corrected: int, up_to_date: int, skipped: int) -> Text:
    """Fix metadata summary."""
    txt = Text()
    parts = []
    if corrected:
        parts.append(f"{corrected} album(s) corrigé(s)")
    if up_to_date:
        parts.append(f"{up_to_date} à jour")
    if skipped:
        parts.append(f"{skipped} passé(s)")

    if parts:
        txt.append(f"  {CHECK}  ", style="green")
        txt.append(" · ".join(parts) + "\n")
    else:
        txt.append(f"  {CHECK}  Aucune correction\n", style="green")
    return txt


# ── Modify track ───────────────────────────────────────────────────────────


def render_modify_search(
    query: str,
    track_items: list[tuple[str, str, str]],
    album_items: list[tuple[str, str, int]],
    cursor: int,
    selectable: list[int],
) -> Text:
    """Render search results: tracks section + albums section.

    track_items: [(label, suffix, apple_id), ...]
    album_items: [(label, artist, track_count), ...]
    """
    from music_manager.ui.text import (  # noqa: PLC0415
        MODIFY_NO_RESULTS,
        MODIFY_SECTION_ALBUMS,
        MODIFY_SECTION_TRACKS,
    )

    body = Text()

    if not track_items and not album_items:
        if query and len(query) >= 2:
            body.append(f"\n  {MODIFY_NO_RESULTS}\n", style="dim")
        return body

    item_idx = 0

    # Tracks section
    if track_items:
        sep_len = max(1, COL - cell_len(MODIFY_SECTION_TRACKS) - 7)
        body.append(f"\n  {SEP * 3} {MODIFY_SECTION_TRACKS} {SEP * sep_len}\n\n", style="dim")
        item_idx += 1  # separator

        for label, suffix, _ in track_items:
            is_active = item_idx in selectable and selectable.index(item_idx) == cursor
            marker = MARKER if is_active else MARKER_EMPTY
            plain_left = f"  {marker}{label}"
            spacing = pad_to(plain_left, suffix) if suffix else ""

            if is_active:
                body.append(f"  {marker}", style=f"bold {BLUE}")
                body.append(label, style=f"bold {BLUE}")
            else:
                body.append(f"  {marker}{label}")
            if suffix:
                body.append(f"{spacing}{suffix}\n", style="dim")
            else:
                body.append("\n")
            item_idx += 1

    # Albums section
    if album_items:
        sep_len = max(1, COL - cell_len(MODIFY_SECTION_ALBUMS) - 7)
        body.append(f"\n  {SEP * 3} {MODIFY_SECTION_ALBUMS} {SEP * sep_len}\n\n", style="dim")
        item_idx += 1  # separator

        for label, _artist, count in album_items:
            is_active = item_idx in selectable and selectable.index(item_idx) == cursor
            marker = MARKER if is_active else MARKER_EMPTY
            suffix = f"({count} piste{'s' if count != 1 else ''})"
            plain_left = f"  {marker}{label}"
            spacing = pad_to(plain_left, suffix)

            if is_active:
                body.append(f"  {marker}", style=f"bold {BLUE}")
                body.append(label, style=f"bold {BLUE}")
            else:
                body.append(f"  {marker}{label}")
            body.append(f"{spacing}{suffix}\n", style="dim")
            item_idx += 1

    return body


def render_modify_actions(
    items: list[tuple[str, str] | None],
    cursor: int,
    selectable: list[int],
) -> Text:
    """Render action menu for modify track/album."""
    body = Text()
    body.append("\n")

    for i, item in enumerate(items):
        if item is None:
            body.append(f"\n  {SEP * 3}\n", style="dim")
            continue

        _key, label = item
        is_active = i in selectable and selectable.index(i) == cursor
        marker = MARKER if is_active else MARKER_EMPTY

        if is_active:
            body.append(f"  {marker}", style=f"bold {BLUE}")
            body.append(label, style=f"bold {BLUE}")
        else:
            body.append(f"  {marker}{label}")
        body.append("\n")

    return body


def render_modify_editions(
    editions: list[dict],
    cursor: int,
    current_isrc: str = "",
    best_idx: int = -1,
) -> Text:
    """Render edition picker: album name · year · N pistes per edition."""
    body = Text()
    body.append("\n")

    for i, ed in enumerate(editions):
        is_active = i == cursor
        marker = MARKER if is_active else MARKER_EMPTY
        album = ed.get("album", ed.get("title", ""))
        nb = ed.get("total_tracks", ed.get("nb_tracks", 0))
        year = ed.get("year", "")
        is_current = bool(current_isrc) and ed.get("isrc", "").upper() == current_isrc.upper()

        if is_active:
            body.append(f"  {marker}", style=f"bold {BLUE}")
            body.append(album, style=f"bold {BLUE}")
        else:
            body.append(f"  {marker}{album}")

        # Metadata suffix: (year) (N piste(s))
        suffix = ""
        if year:
            suffix += f" ({year})"
        if nb:
            suffix += f" ({nb} piste{'s' if nb != 1 else ''})"
        if suffix:
            body.append(suffix, style="dim")
        if is_current:
            body.append(f"  {CHECK}", style="green")
        if i == best_idx:
            body.append("  (recommandé)", style="green")
        body.append("\n")

    # Back option
    body.append(f"\n  {SEP * 3}\n", style="dim")
    is_back = len(editions) == cursor
    marker = MARKER if is_back else MARKER_EMPTY
    if is_back:
        body.append(f"  {marker}", style=f"bold {BLUE}")
        body.append("Retour", style=f"bold {BLUE}")
    else:
        body.append(f"  {marker}Retour")
    body.append("\n")

    return body


def render_complete_albums(
    albums: list[dict],
    checks: list[bool],
    cursor: int,
    actions: list[str],
) -> Text:
    """Render incomplete albums with checkboxes.

    albums: [{album_id, title, artist, local, total}, ...]
    """
    body = Text()
    body.append("\n")
    num_albums = len(albums)

    for i, album in enumerate(albums):
        is_active = i == cursor
        marker = MARKER if is_active else MARKER_EMPTY
        checkbox = f"[{'x' if checks[i] else ' '}]"
        title = album.get("title", "")
        artist = album.get("artist", "")
        local = album.get("local", 0)
        total = album.get("total", 0)
        label = f"{title} — {artist}" if artist else title
        badge = f"{local}/{total} pistes"

        plain_left = f"  {marker}{checkbox} {label}"
        spacing = pad_to(plain_left, badge)

        if is_active:
            body.append(f"  {marker}", style=f"bold {BLUE}")
            body.append(f"{checkbox} ", style=f"bold {BLUE}")
            body.append(title, style=f"bold {BLUE}")
            if artist:
                body.append(f" — {artist}", style="dim")
        else:
            body.append(f"  {marker}{checkbox} {title}")
            if artist:
                body.append(f" — {artist}", style="dim")

        body.append(f"{spacing}{badge}", style="dim")
        body.append("\n")

    # Actions
    body.append(f"\n  {SEP * 3}\n", style="dim")

    for j, action in enumerate(actions):
        idx = num_albums + j
        is_active = idx == cursor
        marker = MARKER if is_active else MARKER_EMPTY
        if is_active:
            body.append(f"  {marker}", style=f"bold {BLUE}")
            body.append(action, style=f"bold {BLUE}")
        else:
            body.append(f"  {marker}{action}")
        body.append("\n")

    return body


def render_complete_summary(imported: int, failed: int) -> Text:
    """Complete albums summary."""
    txt = Text()
    parts = []
    if imported:
        parts.append(f"{imported} piste(s) importée(s)")
    if failed:
        parts.append(f"{failed} échouée(s)")
    if parts:
        txt.append(f"\n  {CHECK}  ", style="green")
        txt.append(" · ".join(parts) + "\n")
    else:
        txt.append(f"\n  {CHECK}  Aucune piste à importer\n", style="green")
    return txt


def render_modify_metadata(
    fields: list[tuple[str, str, str]],
    cursor: int,
) -> Text:
    """Render metadata editor: field name + current value.

    fields: [(label, field_key, current_value), ...]
    """
    body = Text()
    body.append("\n")

    for i, (label, _key, value) in enumerate(fields):
        is_active = i == cursor
        marker = MARKER if is_active else MARKER_EMPTY

        if is_active:
            body.append(f"  {marker}", style=f"bold {BLUE}")
            body.append(f"{label}", style=f"bold {BLUE}")
            body.append(f" : {value}\n", style="dim")
        else:
            body.append(f"  {marker}{label}")
            body.append(f" : {value}\n", style="dim")

    # Actions
    body.append(f"\n  {SEP * 3}\n", style="dim")

    actions = ["Appliquer", "Retour"]
    for j, action in enumerate(actions):
        idx = len(fields) + j
        is_active = idx == cursor
        marker = MARKER if is_active else MARKER_EMPTY
        if is_active:
            body.append(f"  {marker}", style=f"bold {BLUE}")
            body.append(action, style=f"bold {BLUE}")
        else:
            body.append(f"  {marker}{action}")
        body.append("\n")

    return body


def render_modify_covers(
    covers: list[dict],
    cursor: int,
) -> Text:
    """Render cover picker: year + track count per cover."""
    body = Text()
    body.append("\n")

    for i, cover in enumerate(covers):
        is_active = i == cursor
        marker = MARKER if is_active else MARKER_EMPTY
        year = cover.get("year", "")
        count = cover.get("track_count", 0)
        album = cover.get("album", "")
        label = f"{album} ({year})" if year else album
        badge = f"({count} piste{'s' if count != 1 else ''})" if count else ""

        plain_left = f"  {marker}{label}"
        spacing = pad_to(plain_left, badge) if badge else ""

        if is_active:
            body.append(f"  {marker}", style=f"bold {BLUE}")
            body.append(label, style=f"bold {BLUE}")
        else:
            body.append(f"  {marker}{label}")
        if badge:
            body.append(f"{spacing}{badge}", style="dim")
        body.append("\n")

    # Back option
    body.append(f"\n  {SEP * 3}\n", style="dim")
    is_back = len(covers) == cursor
    marker = MARKER if is_back else MARKER_EMPTY
    if is_back:
        body.append(f"  {marker}", style=f"bold {BLUE}")
        body.append("Retour", style=f"bold {BLUE}")
    else:
        body.append(f"  {marker}Retour")
    body.append("\n")

    return body


# ── Duplicates ─────────────────────────────────────────────────────────────


def render_duplicate_group(
    group: list[dict],
    best_idx: int,
    cursor: int,
    idx: int,
    total: int,
    actions: list[str],
) -> Text:
    """Render a single duplicate group for review.

    cursor: position across entries (0..len-1) + actions (len..len+N-1).
    best_idx: index of best_version (shown with ★ hint).
    idx/total: group number for separator.
    """
    body = Text()
    num_entries = len(group)

    # Numbered separator
    body.append_text(render_review_separator(idx, total))

    for ei, entry in enumerate(group):
        is_active = ei == cursor
        marker = MARKER if is_active else MARKER_EMPTY

        entry_title = entry.get("title", "")
        entry_artist = entry.get("artist", "")
        album = entry.get("album", "")
        duration = entry.get("duration") or 0
        dur_str = f"{duration // 60}:{duration % 60:02d}" if duration else ""
        badge_parts = []
        if album:
            badge_parts.append(album)
        if dur_str:
            badge_parts.append(dur_str)
        badge = "  ".join(badge_parts)

        label_full = f"{entry_title} — {entry_artist}" if entry_artist else entry_title
        plain_left = f"  {marker}{label_full}"
        reco = "  (recommandé)" if ei == best_idx else ""
        spacing = pad_to(plain_left, badge + reco) if badge else ""

        if is_active:
            body.append(f"  {marker}", style=f"bold {BLUE}")
            body.append(entry_title, style=f"bold {BLUE}")
            if entry_artist:
                body.append(f" — {entry_artist}", style="dim")
        else:
            body.append(f"  {marker}")
            body.append(entry_title)
            if entry_artist:
                body.append(f" — {entry_artist}", style="dim")

        if badge:
            body.append(f"{spacing}{badge}", style="dim")
        if reco:
            body.append(reco, style="green")
        body.append("\n")

    # Actions
    body.append(f"\n  {SEP * 3}\n", style="dim")

    for j, action in enumerate(actions):
        action_idx = num_entries + j
        is_active = action_idx == cursor
        marker = MARKER if is_active else MARKER_EMPTY

        if is_active:
            body.append(f"  {marker}", style=f"bold {BLUE}")
            body.append(action, style=f"bold {BLUE}")
        else:
            body.append(f"  {marker}{action}")
        body.append("\n")

    return body


def render_duplicates_summary(removed: int, skipped: int, ignored: int) -> Text:
    """Duplicates removal summary."""
    txt = Text()
    parts = []
    if removed:
        parts.append(f"{removed} doublon(s) supprimé(s)")
    if skipped:
        parts.append(f"{skipped} passé(s)")
    if ignored:
        parts.append(f"{ignored} ignoré(s)")

    if parts:
        txt.append(f"\n  {CHECK}  ", style="green")
        txt.append(" · ".join(parts) + "\n")
    else:
        txt.append(f"\n  {CHECK}  Aucun doublon\n", style="green")
    return txt


def render_modify_status(message: str) -> Text:
    """Render a status message during modify operations."""
    txt = Text()
    txt.append(f"\n  {message}\n", style="dim")
    return txt


def render_modify_result(success: bool, error: str = "") -> Text:
    """Render modify result: success or error."""
    txt = Text()
    if success:
        txt.append(f"\n  {CHECK}  Modification effectuée\n", style="green")
    else:
        error_msg = {
            "deezer_resolve_failed": "Introuvable sur Deezer — vérifiez l'orthographe",
            "youtube_failed": "Échec YouTube — vérifiez votre connexion",
            "youtube_download_failed": "Échec YouTube — vérifiez votre connexion",
            "cover_download_failed": "Échec pochette — réessayez plus tard",
            "no_deezer_id": "Piste non identifiée — identifiez la bibliothèque d'abord",
            "track_not_found": "Piste introuvable dans la bibliothèque",
            "no_fields": "Aucun champ à modifier",
            "album_tracklist_empty": "Album vide sur Deezer",
            "all_skipped": "Toutes les pistes déjà à jour",
            "import_failed": "Échec import — vérifiez qu'Apple Music est ouvert",
            "unexpected_error": "Erreur inattendue — voir logs pour détails",
        }.get(error, error or "Erreur inconnue")
        txt.append(f"\n  {CROSS}  {error_msg}\n", style="red")
    return txt
