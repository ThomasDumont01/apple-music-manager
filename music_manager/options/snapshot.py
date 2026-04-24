"""Snapshot — promote imported tracks to baseline (§12)."""

from music_manager.services.tracks import Tracks

# ── Entry point ──────────────────────────────────────────────────────────────


def snapshot(tracks_store: Tracks) -> int:
    """Promote all imported+done tracks to baseline. Returns count promoted."""
    count = 0
    for apple_id, entry in list(tracks_store.all().items()):
        if entry.get("origin") == "imported" and entry.get("status") == "done":
            tracks_store.update(apple_id, {"origin": "baseline"})
            count += 1

    if count > 0:
        tracks_store.save()

    return count
