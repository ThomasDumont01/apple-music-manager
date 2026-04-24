"""Tests for tagger.py — get_cover_dimensions."""

import struct
import subprocess
from pathlib import Path

import pytest
from mutagen.id3 import APIC  # type: ignore[attr-defined]
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover

from music_manager.services.tagger import get_cover_dimensions


def _make_jpeg(width: int, height: int) -> bytes:
    """Create minimal JPEG bytes with given dimensions."""
    sof = struct.pack(">BBHBHH", 0xFF, 0xC0, 11, 8, height, width)
    return b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + sof


def _make_png(width: int, height: int) -> bytes:
    """Create minimal PNG bytes with given dimensions."""
    header = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">II", width, height) + b"\x08\x02\x00\x00\x00"
    return header + b"\x00\x00\x00\rIHDR" + ihdr_data


@pytest.fixture()
def m4a_path(tmp_path: Path) -> str:
    """Create a minimal valid M4A file via ffmpeg."""
    fp = str(tmp_path / "test.m4a")
    subprocess.run(
        [
            "ffmpeg",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=mono",
            "-t",
            "0.1",
            "-c:a",
            "aac",
            fp,
            "-y",
        ],
        capture_output=True,
        timeout=10,
        check=True,
    )
    return fp


@pytest.fixture()
def mp3_path(tmp_path: Path) -> str:
    """Create a minimal valid MP3 file via ffmpeg."""
    fp = str(tmp_path / "test.mp3")
    subprocess.run(
        [
            "ffmpeg",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=mono",
            "-t",
            "0.1",
            "-c:a",
            "libmp3lame",
            fp,
            "-y",
        ],
        capture_output=True,
        timeout=10,
        check=True,
    )
    return fp


# ── M4A tests ──────────────────────────────────────────────────────────────


def test_dimensions_m4a_jpeg(m4a_path: str) -> None:
    """Read JPEG cover dimensions from M4A file."""
    audio = MP4(m4a_path)
    audio["covr"] = [MP4Cover(_make_jpeg(1400, 1400), imageformat=MP4Cover.FORMAT_JPEG)]
    audio.save()

    assert get_cover_dimensions(m4a_path) == (1400, 1400)


def test_dimensions_m4a_png(m4a_path: str) -> None:
    """Read PNG cover dimensions from M4A file."""
    audio = MP4(m4a_path)
    audio["covr"] = [MP4Cover(_make_png(3000, 3000), imageformat=MP4Cover.FORMAT_PNG)]
    audio.save()

    assert get_cover_dimensions(m4a_path) == (3000, 3000)


# ── MP3 tests ──────────────────────────────────────────────────────────────


def test_dimensions_mp3(mp3_path: str) -> None:
    """Read cover dimensions from MP3 APIC tag."""
    audio = MP3(mp3_path)
    if audio.tags is None:
        audio.add_tags()
    assert audio.tags is not None
    audio.tags.add(APIC(encoding=3, mime="image/jpeg", type=3, data=_make_jpeg(600, 600)))
    audio.save()

    assert get_cover_dimensions(mp3_path) == (600, 600)


# ── Edge cases ─────────────────────────────────────────────────────────────


def test_dimensions_no_cover(m4a_path: str) -> None:
    """File without cover returns (0, 0)."""
    assert get_cover_dimensions(m4a_path) == (0, 0)


def test_dimensions_nonexistent_file() -> None:
    """Non-existent file returns (0, 0)."""
    assert get_cover_dimensions("/nonexistent/file.m4a") == (0, 0)


def test_dimensions_small_cover(m4a_path: str) -> None:
    """Detect small cover < 1000."""
    audio = MP4(m4a_path)
    audio["covr"] = [MP4Cover(_make_jpeg(500, 500), imageformat=MP4Cover.FORMAT_JPEG)]
    audio.save()

    w, h = get_cover_dimensions(m4a_path)
    assert w == 500 and h == 500 and w < 1000


def test_dimensions_non_square(m4a_path: str) -> None:
    """Detect non-square cover."""
    audio = MP4(m4a_path)
    audio["covr"] = [MP4Cover(_make_jpeg(299, 300), imageformat=MP4Cover.FORMAT_JPEG)]
    audio.save()

    w, h = get_cover_dimensions(m4a_path)
    assert w != h
