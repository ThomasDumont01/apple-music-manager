"""Unit tests for tagger.py — tag_audio_file, write_isrc, write_cover,
strip_youtube_tags, get_cover_dimensions (mocked mutagen)."""

from __future__ import annotations

import struct
from unittest.mock import MagicMock, mock_open, patch

from music_manager.core.models import Track
from music_manager.services.tagger import (
    get_cover_dimensions,
    parse_image_dimensions,
    strip_youtube_tags,
    tag_audio_file,
    write_cover,
    write_isrc,
)

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_track(**overrides: object) -> Track:
    """Build a Track with sensible defaults, overridable."""
    defaults: dict[str, object] = {
        "isrc": "USRC17607839",
        "title": "Bohemian Rhapsody",
        "artist": "Queen",
        "album": "A Night at the Opera",
        "genre": "Rock",
        "release_date": "1975-10-31",
        "track_number": 11,
        "total_tracks": 12,
        "disk_number": 1,
        "total_discs": 1,
        "album_artist": "Queen",
        "explicit": False,
    }
    defaults.update(overrides)
    return Track(**defaults)  # type: ignore[arg-type]


def _make_jpeg_bytes(width: int, height: int) -> bytes:
    """Minimal JPEG bytes with SOF0 marker encoding dimensions."""
    sof = struct.pack(">BBHBHH", 0xFF, 0xC0, 11, 8, height, width)
    return b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + sof


def _make_png_bytes(width: int, height: int) -> bytes:
    """Minimal PNG bytes with IHDR chunk encoding dimensions."""
    header = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">II", width, height) + b"\x08\x02\x00\x00\x00"
    return header + b"\x00\x00\x00\rIHDR" + ihdr_data


# ══════════════════════════════════════════════════════════════════════════════
# tag_audio_file
# ══════════════════════════════════════════════════════════════════════════════


class TestTagAudioFile:
    """Tests for tag_audio_file()."""

    @patch("mutagen.mp4.MP4")
    def test_sets_all_metadata_tags(self, mock_mp4_cls: MagicMock) -> None:
        """All expected MP4 tags are written from Track fields."""
        audio = MagicMock()
        audio.__setitem__ = MagicMock()
        audio.__getitem__ = MagicMock()
        audio.save = MagicMock()
        mock_mp4_cls.return_value = audio

        track = _make_track()
        result = tag_audio_file("/fake/song.m4a", track)

        assert result is True
        calls = {c[0][0]: c[0][1] for c in audio.__setitem__.call_args_list}
        assert calls["\xa9nam"] == ["Bohemian Rhapsody"]
        assert calls["\xa9ART"] == ["Queen"]
        assert calls["\xa9alb"] == ["A Night at the Opera"]
        assert calls["\xa9gen"] == ["Rock"]
        assert calls["\xa9day"] == ["1975"]
        assert calls["aART"] == ["Queen"]
        assert calls["trkn"] == [(11, 12)]
        assert calls["disk"] == [(1, 1)]
        assert calls["----:com.apple.iTunes:ISRC"] == [b"USRC17607839"]
        assert calls["rtng"] == [0]  # explicit=False → 0

    @patch("mutagen.mp4.MP4")
    def test_explicit_true_sets_rtng_1(self, mock_mp4_cls: MagicMock) -> None:
        """explicit=True writes rtng=[1]."""
        audio = MagicMock()
        audio.__setitem__ = MagicMock()
        audio.save = MagicMock()
        mock_mp4_cls.return_value = audio

        track = _make_track(explicit=True)
        result = tag_audio_file("/fake/song.m4a", track)

        assert result is True
        calls = {c[0][0]: c[0][1] for c in audio.__setitem__.call_args_list}
        assert calls["rtng"] == [1]

    @patch("mutagen.mp4.MP4")
    def test_explicit_false_sets_rtng_0(self, mock_mp4_cls: MagicMock) -> None:
        """explicit=False writes rtng=[0]."""
        audio = MagicMock()
        audio.__setitem__ = MagicMock()
        audio.save = MagicMock()
        mock_mp4_cls.return_value = audio

        track = _make_track(explicit=False)
        result = tag_audio_file("/fake/song.m4a", track)

        assert result is True
        calls = {c[0][0]: c[0][1] for c in audio.__setitem__.call_args_list}
        assert calls["rtng"] == [0]

    @patch("music_manager.services.tagger._embed_cover")
    @patch("mutagen.mp4.MP4")
    def test_cover_embedding(self, mock_mp4_cls: MagicMock, mock_embed: MagicMock) -> None:
        """Cover is embedded when cover_path is provided."""
        audio = MagicMock()
        audio.__setitem__ = MagicMock()
        audio.save = MagicMock()
        mock_mp4_cls.return_value = audio

        track = _make_track()
        result = tag_audio_file("/fake/song.m4a", track, cover_path="/fake/cover.jpg")

        assert result is True
        mock_embed.assert_called_once_with(audio, "/fake/cover.jpg")

    @patch("music_manager.services.tagger._embed_cover")
    @patch("mutagen.mp4.MP4")
    def test_no_cover_when_empty_path(
        self, mock_mp4_cls: MagicMock, mock_embed: MagicMock
    ) -> None:
        """Cover is NOT embedded when cover_path is empty."""
        audio = MagicMock()
        audio.__setitem__ = MagicMock()
        audio.save = MagicMock()
        mock_mp4_cls.return_value = audio

        track = _make_track()
        tag_audio_file("/fake/song.m4a", track, cover_path="")

        mock_embed.assert_not_called()

    @patch("mutagen.mp4.MP4")
    def test_save_failure_returns_false(self, mock_mp4_cls: MagicMock) -> None:
        """When audio.save() raises, tag_audio_file returns False."""
        audio = MagicMock()
        audio.__setitem__ = MagicMock()
        audio.save.side_effect = OSError("disk full")
        mock_mp4_cls.return_value = audio

        track = _make_track()
        result = tag_audio_file("/fake/song.m4a", track)

        assert result is False

    @patch("mutagen.mp4.MP4")
    def test_mp4_open_failure_returns_false(self, mock_mp4_cls: MagicMock) -> None:
        """When MP4() constructor raises, returns False."""
        mock_mp4_cls.side_effect = Exception("corrupt file")

        track = _make_track()
        result = tag_audio_file("/fake/song.m4a", track)

        assert result is False

    @patch("mutagen.mp4.MP4")
    def test_optional_fields_skipped_when_empty(self, mock_mp4_cls: MagicMock) -> None:
        """Optional fields (genre, release_date, album_artist) are not written when empty."""
        audio = MagicMock()
        audio.__setitem__ = MagicMock()
        audio.save = MagicMock()
        mock_mp4_cls.return_value = audio

        track = _make_track(
            genre="",
            release_date="",
            album_artist="",
            track_number=None,
            disk_number=0,
            isrc="",
        )
        tag_audio_file("/fake/song.m4a", track)

        keys_set = {c[0][0] for c in audio.__setitem__.call_args_list}
        assert "\xa9gen" not in keys_set
        assert "\xa9day" not in keys_set
        assert "aART" not in keys_set
        assert "trkn" not in keys_set
        assert "disk" not in keys_set
        assert "----:com.apple.iTunes:ISRC" not in keys_set
        # rtng is always written
        assert "rtng" in keys_set


# ══════════════════════════════════════════════════════════════════════════════
# write_isrc
# ══════════════════════════════════════════════════════════════════════════════


class TestWriteIsrc:
    """Tests for write_isrc()."""

    @patch("mutagen.File")
    def test_m4a_writes_correct_tag(self, mock_file: MagicMock) -> None:
        """M4A files get ISRC written as ----:com.apple.iTunes:ISRC."""
        tags = MagicMock()
        tags.__setitem__ = MagicMock()
        tags.__contains__ = MagicMock(return_value=False)
        mock_file.return_value = tags

        result = write_isrc("/music/track.m4a", "USRC17607839")

        assert result is True
        tags.__setitem__.assert_called_once_with("----:com.apple.iTunes:ISRC", [b"USRC17607839"])
        tags.save.assert_called_once()

    @patch("mutagen.File")
    def test_mp3_writes_tsrc_frame(self, mock_file: MagicMock) -> None:
        """MP3 files get ISRC written as TSRC ID3 frame."""
        tags = MagicMock()
        tags.tags = MagicMock()
        mock_file.return_value = tags

        result = write_isrc("/music/track.mp3", "GBUM71029604")

        assert result is True
        tags.tags.add.assert_called_once()
        frame = tags.tags.add.call_args[0][0]
        assert frame.text == ["GBUM71029604"]
        tags.save.assert_called_once()

    @patch("mutagen.File")
    def test_returns_false_on_none_tags(self, mock_file: MagicMock) -> None:
        """Returns False when mutagen.File returns None."""
        mock_file.return_value = None

        result = write_isrc("/missing/file.m4a", "USRC17607839")

        assert result is False

    @patch("mutagen.File")
    def test_returns_false_on_exception(self, mock_file: MagicMock) -> None:
        """Returns False when save raises an exception."""
        tags = MagicMock()
        tags.__setitem__ = MagicMock()
        tags.save.side_effect = OSError("permission denied")
        mock_file.return_value = tags

        result = write_isrc("/music/track.m4a", "USRC17607839")

        assert result is False

    @patch("mutagen.File")
    def test_empty_isrc_still_writes(self, mock_file: MagicMock) -> None:
        """Empty ISRC string is written (caller is responsible for validation)."""
        tags = MagicMock()
        tags.__setitem__ = MagicMock()
        mock_file.return_value = tags

        result = write_isrc("/music/track.m4a", "")

        assert result is True
        tags.__setitem__.assert_called_once_with("----:com.apple.iTunes:ISRC", [b""])


# ══════════════════════════════════════════════════════════════════════════════
# write_cover
# ══════════════════════════════════════════════════════════════════════════════


class TestWriteCover:
    """Tests for write_cover()."""

    @patch("music_manager.services.tagger._embed_cover")
    @patch("mutagen.mp4.MP4")
    def test_m4a_cover_writing(self, mock_mp4_cls: MagicMock, mock_embed: MagicMock) -> None:
        """M4A cover uses _embed_cover + save."""
        audio = MagicMock()
        mock_mp4_cls.return_value = audio

        result = write_cover("/music/track.m4a", "/covers/art.jpg")

        assert result is True
        mock_embed.assert_called_once_with(audio, "/covers/art.jpg")
        audio.save.assert_called_once()

    @patch("builtins.open", mock_open(read_data=b"\xff\xd8fake_jpeg"))
    @patch("mutagen.mp3.MP3")
    def test_mp3_cover_writing_jpeg(self, mock_mp3_cls: MagicMock) -> None:
        """MP3 cover with JPEG file uses APIC frame with image/jpeg mime."""
        audio = MagicMock()
        audio.tags = MagicMock()
        mock_mp3_cls.return_value = audio

        result = write_cover("/music/track.mp3", "/covers/art.jpg")

        assert result is True
        audio.tags.delall.assert_called_once_with("APIC")
        audio.tags.add.assert_called_once()
        apic_frame = audio.tags.add.call_args[0][0]
        assert apic_frame.mime == "image/jpeg"
        assert apic_frame.data == b"\xff\xd8fake_jpeg"
        audio.save.assert_called_once()

    @patch("builtins.open", mock_open(read_data=b"\x89PNGfake_png"))
    @patch("mutagen.mp3.MP3")
    def test_mp3_cover_writing_png(self, mock_mp3_cls: MagicMock) -> None:
        """MP3 cover with PNG file uses APIC frame with image/png mime."""
        audio = MagicMock()
        audio.tags = MagicMock()
        mock_mp3_cls.return_value = audio

        result = write_cover("/music/track.mp3", "/covers/art.png")

        assert result is True
        apic_frame = audio.tags.add.call_args[0][0]
        assert apic_frame.mime == "image/png"

    @patch("mutagen.mp3.MP3")
    def test_mp3_creates_tags_if_none(self, mock_mp3_cls: MagicMock) -> None:
        """MP3 with no existing tags calls add_tags()."""
        audio = MagicMock()
        audio.tags = None

        def set_tags() -> None:
            audio.tags = MagicMock()

        audio.add_tags = MagicMock(side_effect=set_tags)
        mock_mp3_cls.return_value = audio

        with patch("builtins.open", mock_open(read_data=b"imgdata")):
            result = write_cover("/music/track.mp3", "/covers/art.jpg")

        assert result is True
        audio.add_tags.assert_called_once()

    @patch("mutagen.mp4.MP4")
    def test_returns_false_on_exception(self, mock_mp4_cls: MagicMock) -> None:
        """Returns False on exception during cover writing."""
        mock_mp4_cls.side_effect = Exception("corrupt file")

        result = write_cover("/music/track.m4a", "/covers/art.jpg")

        assert result is False


# ══════════════════════════════════════════════════════════════════════════════
# strip_youtube_tags
# ══════════════════════════════════════════════════════════════════════════════


class TestStripYoutubeTags:
    """Tests for strip_youtube_tags()."""

    @patch("mutagen.File")
    def test_removes_txxx_and_tsse_tags(self, mock_file: MagicMock) -> None:
        """TXXX:* and TSSE tags are removed, file is saved."""
        tags_dict = {
            "TXXX:comment": "youtube comment",
            "TXXX:description": "video desc",
            "TXXX:purl": "https://youtube.com/...",
            "TSSE": "Lavf58.29.100",
            "TIT2": "My Song",  # Should be kept
        }
        tags_mock = MagicMock()
        tags_mock.keys.return_value = list(tags_dict.keys())
        tags_mock.__delitem__ = MagicMock()
        tags_mock.__contains__ = MagicMock(side_effect=lambda k: k in tags_dict)

        audio = MagicMock()
        audio.tags = tags_mock
        mock_file.return_value = audio

        strip_youtube_tags("/music/track.mp3")

        deleted_keys = [c[0][0] for c in tags_mock.__delitem__.call_args_list]
        assert "TXXX:comment" in deleted_keys
        assert "TXXX:description" in deleted_keys
        assert "TXXX:purl" in deleted_keys
        assert "TSSE" in deleted_keys
        assert "TIT2" not in deleted_keys
        audio.save.assert_called_once()

    @patch("mutagen.File")
    def test_no_save_when_nothing_to_remove(self, mock_file: MagicMock) -> None:
        """File is not saved when no YouTube tags are present."""
        tags_mock = MagicMock()
        tags_mock.keys.return_value = ["TIT2", "TPE1"]

        audio = MagicMock()
        audio.tags = tags_mock
        mock_file.return_value = audio

        strip_youtube_tags("/music/track.mp3")

        audio.save.assert_not_called()

    @patch("mutagen.File")
    def test_handles_none_tags(self, mock_file: MagicMock) -> None:
        """Does not crash when tags is None."""
        audio = MagicMock()
        audio.tags = None
        mock_file.return_value = audio

        strip_youtube_tags("/music/track.mp3")  # Should not raise

    @patch("mutagen.File")
    def test_handles_none_file(self, mock_file: MagicMock) -> None:
        """Does not crash when mutagen.File returns None."""
        mock_file.return_value = None

        strip_youtube_tags("/music/track.mp3")  # Should not raise

    @patch("mutagen.File")
    def test_handles_exception_silently(self, mock_file: MagicMock) -> None:
        """Exceptions are swallowed (best-effort)."""
        mock_file.side_effect = Exception("cannot read file")

        strip_youtube_tags("/music/track.mp3")  # Should not raise


# ══════════════════════════════════════════════════════════════════════════════
# get_cover_dimensions (mocked)
# ══════════════════════════════════════════════════════════════════════════════


class TestGetCoverDimensions:
    """Tests for get_cover_dimensions() with mocked mutagen."""

    @patch("mutagen.File")
    def test_m4a_cover_dimensions(self, mock_file: MagicMock) -> None:
        """Reads dimensions from M4A covr atom."""
        jpeg_data = _make_jpeg_bytes(1400, 1400)
        audio = MagicMock()
        audio.__contains__ = MagicMock(side_effect=lambda k: k == "covr")
        audio.__getitem__ = MagicMock(return_value=[jpeg_data])
        mock_file.return_value = audio

        width, height = get_cover_dimensions("/music/track.m4a")

        assert width == 1400
        assert height == 1400

    @patch("mutagen.File")
    def test_mp3_cover_dimensions(self, mock_file: MagicMock) -> None:
        """Reads dimensions from MP3 APIC frame."""
        jpeg_data = _make_jpeg_bytes(600, 600)
        apic_mock = MagicMock()
        apic_mock.data = jpeg_data

        audio = MagicMock()
        audio.__contains__ = MagicMock(return_value=False)  # No covr key
        tags = MagicMock()
        tags.__iter__ = MagicMock(return_value=iter(["APIC:"]))
        tags.__getitem__ = MagicMock(return_value=apic_mock)
        audio.tags = tags
        mock_file.return_value = audio

        width, height = get_cover_dimensions("/music/track.mp3")

        assert width == 600
        assert height == 600

    @patch("mutagen.File")
    def test_no_cover_returns_zero(self, mock_file: MagicMock) -> None:
        """File without cover returns (0, 0)."""
        audio = MagicMock()
        audio.__contains__ = MagicMock(return_value=False)
        audio.tags = MagicMock()
        audio.tags.__iter__ = MagicMock(return_value=iter([]))
        mock_file.return_value = audio

        width, height = get_cover_dimensions("/music/track.m4a")

        assert width == 0
        assert height == 0

    @patch("mutagen.File")
    def test_nonexistent_file_returns_zero(self, mock_file: MagicMock) -> None:
        """Non-existent file (mutagen returns None) gives (0, 0)."""
        mock_file.return_value = None

        width, height = get_cover_dimensions("/nonexistent/file.m4a")

        assert width == 0
        assert height == 0

    @patch("mutagen.File")
    def test_exception_returns_zero(self, mock_file: MagicMock) -> None:
        """Exception during reading returns (0, 0)."""
        mock_file.side_effect = Exception("corrupt")

        width, height = get_cover_dimensions("/music/track.m4a")

        assert width == 0
        assert height == 0


# ══════════════════════════════════════════════════════════════════════════════
# parse_image_dimensions
# ══════════════════════════════════════════════════════════════════════════════


class TestParseImageDimensions:
    """Tests for parse_image_dimensions()."""

    def test_jpeg_dimensions(self) -> None:
        """Parses width/height from JPEG SOF0 marker."""
        data = _make_jpeg_bytes(1920, 1080)
        assert parse_image_dimensions(data) == (1920, 1080)

    def test_png_dimensions(self) -> None:
        """Parses width/height from PNG IHDR chunk."""
        data = _make_png_bytes(3000, 3000)
        assert parse_image_dimensions(data) == (3000, 3000)

    def test_unknown_format_returns_zero(self) -> None:
        """Unknown image format returns (0, 0)."""
        assert parse_image_dimensions(b"NOT_AN_IMAGE_FORMAT") == (0, 0)

    def test_empty_data_returns_zero(self) -> None:
        """Empty bytes returns (0, 0)."""
        assert parse_image_dimensions(b"") == (0, 0)

    def test_truncated_jpeg_returns_zero(self) -> None:
        """Truncated JPEG (just magic bytes) returns (0, 0)."""
        assert parse_image_dimensions(b"\xff\xd8") == (0, 0)

    def test_truncated_png_returns_zero(self) -> None:
        """Truncated PNG (just header, no IHDR) returns (0, 0)."""
        assert parse_image_dimensions(b"\x89PNG\r\n\x1a\n") == (0, 0)
