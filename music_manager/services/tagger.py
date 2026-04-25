"""Audio file tagging service — read and write metadata via mutagen."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mutagen.mp4 import MP4

    from music_manager.core.models import Track

import os
from collections.abc import Callable

import mutagen  # type: ignore[import-untyped]

from music_manager.core.models import LibraryEntry

# ── Constants ───────────────────────────────────────────────────────────────

_ISRC_KEYS = (
    "----:com.apple.iTunes:ISRC",  # M4A (MP4)
    "TSRC",  # MP3 (ID3)
    "isrc",  # FLAC, OGG (Vorbis comments)
)

# ── Entry point ──────────────────────────────────────────────────────────────


def scan_isrc(
    entries: dict[str, LibraryEntry],
    on_progress: Callable[[int, int], None] | None = None,
) -> int:
    """Read ISRC tags from local audio files via mutagen.

    Mutates entries in-place, filling the isrc field.
    Returns the number of entries where an ISRC was found.
    """
    total = len(entries)
    found = 0

    for index, entry in enumerate(entries.values()):
        if entry.isrc or not entry.file_path:
            if on_progress and index % 50 == 0:
                on_progress(index + 1, total)
            continue

        try:
            tags = mutagen.File(entry.file_path)  # type: ignore[attr-defined]
            if tags:
                isrc = _extract_isrc(tags)
                if isrc:
                    entry.isrc = isrc
                    found += 1
        except Exception as exc:
            from music_manager.core.logger import log_event  # noqa: PLC0415

            log_event("tagger_error", func="scan_isrc", file=entry.file_path, error=str(exc))

        if on_progress and index % 50 == 0:
            on_progress(index + 1, total)

    if on_progress:
        on_progress(total, total)

    return found


def tag_audio_file(
    filepath: str,
    track: Track,
    cover_path: str = "",
) -> bool:
    """Write all metadata to an M4A file via mutagen.

    Returns True if successful, False on error (best-effort).
    """
    from mutagen.mp4 import MP4  # noqa: PLC0415

    try:
        audio = MP4(filepath)
    except Exception as exc:
        from music_manager.core.logger import log_event  # noqa: PLC0415

        log_event("tagger_error", func="tag_audio_file", file=filepath, error=str(exc))
        return False

    audio["\xa9nam"] = [track.title]
    audio["\xa9ART"] = [track.artist]
    audio["\xa9alb"] = [track.album]

    if track.genre:
        audio["\xa9gen"] = [track.genre]
    if track.release_date:
        audio["\xa9day"] = [track.release_date[:4]]
    if track.album_artist:
        audio["aART"] = [track.album_artist]
    if track.track_number is not None:
        audio["trkn"] = [(int(track.track_number), int(track.total_tracks or 0))]
    if track.disk_number:
        audio["disk"] = [(int(track.disk_number), int(track.total_discs or 0))]
    if track.isrc:
        audio["----:com.apple.iTunes:ISRC"] = [track.isrc.encode("utf-8")]
    audio["rtng"] = [1 if track.explicit else 0]  # 1 = explicit, 0 = none

    if cover_path:
        _embed_cover(audio, cover_path)

    try:
        audio.save()
        return True
    except Exception as exc:
        from music_manager.core.logger import log_event  # noqa: PLC0415

        log_event("tagger_error", func="tag_audio_file_save", file=filepath, error=str(exc))
        return False


def strip_youtube_tags(filepath: str) -> None:
    """Remove YouTube metadata tags that cause Apple Music album splits.

    yt-dlp embeds TXXX:comment, TXXX:description, TXXX:purl, TSSE
    with per-track YouTube descriptions. Apple Music uses these to
    group albums — different descriptions = separate albums.
    """
    try:
        tags = mutagen.File(filepath)  # type: ignore[attr-defined]
        if tags is None or not hasattr(tags, "tags") or tags.tags is None:
            return
        removed = False
        for key in list(tags.tags.keys()):
            if key.startswith("TXXX:") or key == "TSSE":
                del tags.tags[key]
                removed = True
        if removed:
            tags.save()
    except Exception:  # noqa: BLE001
        pass  # Best-effort, don't block import


def get_cover_dimensions(filepath: str) -> tuple[int, int]:
    """Read cover dimensions from audio file (M4A or MP3).

    Returns (width, height) or (0, 0) if no cover, missing file, or error.
    """
    if not filepath or not os.path.isfile(filepath):
        return (0, 0)
    try:
        audio = mutagen.File(filepath)  # type: ignore[attr-defined]
        if audio is None:
            return (0, 0)

        cover_data: bytes | None = None

        # M4A: covr atom
        if "covr" in audio:
            cover_data = bytes(audio["covr"][0])

        # MP3: APIC frame
        if cover_data is None and hasattr(audio, "tags") and audio.tags:
            for key in audio.tags:
                if str(key).startswith("APIC"):
                    cover_data = audio.tags[key].data
                    break

        if not cover_data:
            return (0, 0)

        return parse_image_dimensions(cover_data)
    except Exception as exc:
        from music_manager.core.logger import log_event  # noqa: PLC0415

        log_event("tagger_error", func="get_cover_dimensions", file=filepath, error=str(exc))
        return (0, 0)


def write_isrc(filepath: str, isrc: str) -> bool:
    """Write ISRC tag to an audio file. Returns True if successful."""
    if not filepath or not os.path.isfile(filepath):
        return False
    try:
        tags = mutagen.File(filepath)  # type: ignore[attr-defined]
        if tags is None:
            return False

        # Detect format and use the correct tag key
        is_mp4 = filepath.lower().endswith((".m4a", ".mp4", ".m4b"))
        target_key = _ISRC_KEYS[0] if is_mp4 else _ISRC_KEYS[1]  # M4A or MP3

        if target_key.startswith("----"):
            tags[target_key] = [isrc.encode("utf-8")]
        else:
            # MP3 ID3: must use Frame object, not raw string
            from mutagen.id3._frames import TSRC  # type: ignore[import-not-found]  # noqa: PLC0415

            tags.tags.add(TSRC(encoding=3, text=[isrc]))
        tags.save()
        return True
    except Exception as exc:
        from music_manager.core.logger import log_event  # noqa: PLC0415

        log_event("tagger_error", func="write_isrc", file=filepath, error=str(exc))
    return False


def write_cover(filepath: str, cover_path: str) -> bool:
    """Embed a cover image into an audio file (M4A or MP3). Returns True if successful."""
    if not filepath or not os.path.isfile(filepath):
        return False
    try:
        if filepath.endswith(".m4a"):
            from mutagen.mp4 import MP4  # noqa: PLC0415

            audio = MP4(filepath)
            _embed_cover(audio, cover_path)
            audio.save()
        else:
            # MP3: use APIC frame
            from mutagen.id3 import APIC  # noqa: PLC0415, I001  # pyright: ignore[reportPrivateImportUsage]
            from mutagen.mp3 import MP3  # noqa: PLC0415

            audio_mp3 = MP3(filepath)
            if audio_mp3.tags is None:
                audio_mp3.add_tags()
            tags = audio_mp3.tags
            assert tags is not None
            with open(cover_path, "rb") as img:
                image_data = img.read()
            mime = "image/png" if cover_path.endswith(".png") else "image/jpeg"
            # Remove existing covers
            tags.delall("APIC")
            tags.add(APIC(encoding=3, mime=mime, type=3, data=image_data))
            audio_mp3.save()
        return True
    except Exception as exc:
        from music_manager.core.logger import log_event  # noqa: PLC0415

        log_event("tagger_error", func="write_cover", file=filepath, error=str(exc))
        return False


# ── Public helpers ──────────────────────────────────────────────────────────


def parse_image_dimensions(data: bytes) -> tuple[int, int]:
    """Extract width, height from JPEG or PNG raw bytes."""
    import struct  # noqa: PLC0415

    # PNG: magic + IHDR chunk
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        width = struct.unpack(">I", data[16:20])[0]
        height = struct.unpack(">I", data[20:24])[0]
        return width, height

    # JPEG: scan for SOF0/SOF2 marker
    if data[:2] == b"\xff\xd8":
        i = 2
        while i < len(data) - 8:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC2):  # SOF0 or SOF2 (progressive)
                height = struct.unpack(">H", data[i + 5 : i + 7])[0]
                width = struct.unpack(">H", data[i + 7 : i + 9])[0]
                return width, height
            if marker == 0xD9:  # EOI
                break
            length = struct.unpack(">H", data[i + 2 : i + 4])[0]
            i += 2 + length
    return (0, 0)


# ── Private Functions ────────────────────────────────────────────────────────


def _extract_isrc(tags: mutagen.FileType) -> str:  # type: ignore[name-defined]
    """Extract ISRC from any supported audio format."""
    for key in _ISRC_KEYS:
        if key in tags:
            raw = tags[key]
            if isinstance(raw, list) and raw:
                value = (
                    bytes(raw[0]).decode("utf-8").strip()
                    if hasattr(raw[0], "__bytes__")
                    else str(raw[0]).strip()
                )
            else:
                value = str(raw).strip()
            if value and _is_valid_isrc(value):
                return value.upper()
    return ""


def _is_valid_isrc(value: str) -> bool:
    """ISRC = exactly 12 alphanumeric characters (e.g. GBUM71029604)."""
    return len(value) == 12 and value.isalnum()


def _embed_cover(audio: MP4, cover_path: str) -> None:
    """Embed a cover image into an MP4 audio object (in-place, does not save)."""
    from mutagen.mp4 import MP4Cover  # noqa: PLC0415

    try:
        with open(cover_path, "rb") as file:
            image_data = file.read()
        image_format = MP4Cover.FORMAT_PNG if cover_path.endswith(".png") else MP4Cover.FORMAT_JPEG
        audio["covr"] = [MP4Cover(image_data, imageformat=image_format)]
    except Exception as exc:
        from music_manager.core.logger import log_event  # noqa: PLC0415

        log_event("tagger_error", func="_embed_cover", file=cover_path, error=str(exc))
