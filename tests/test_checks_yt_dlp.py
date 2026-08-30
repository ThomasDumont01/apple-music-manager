"""yt-dlp freshness detection.

A yt-dlp older than ~6 weeks starts getting "HTTP Error 403: Forbidden" from
YouTube's download endpoints. That was the root cause of a whole batch of
import failures which surfaced only as an opaque "youtube_failed", so the
version is now checked and reported.
"""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from music_manager.core import checks
from music_manager.core.checks import (
    YT_DLP_MAX_AGE_DAYS,
    check_yt_dlp_fresh,
    yt_dlp_age_days,
    yt_dlp_update_hint,
    yt_dlp_version,
)

_PATCH = "music_manager.core.checks"


# ── Version parsing ────────────────────────────────────────────────────────


def test_age_days_from_release_date() -> None:
    assert yt_dlp_age_days("2026.07.04", today=date(2026, 8, 20)) == 47


def test_age_days_ignores_nightly_suffix() -> None:
    assert yt_dlp_age_days("2026.07.04.232319", today=date(2026, 8, 20)) == -1
    assert yt_dlp_age_days("2026.07.04-nightly", today=date(2026, 8, 20)) == 47


def test_age_days_unknown_for_unparseable_version() -> None:
    """A source checkout must read as "unknown", never as "stale"."""
    for version in ("", "dev", "not.a.date"):
        assert yt_dlp_age_days(version) == -1


def test_future_version_is_not_negative() -> None:
    assert yt_dlp_age_days("2026.09.01", today=date(2026, 8, 20)) == 0


# ── check_yt_dlp_fresh ─────────────────────────────────────────────────────


def test_fresh_version_is_not_stale() -> None:
    with patch(f"{_PATCH}.yt_dlp_version", return_value="2026.08.19"):
        version, age, stale = check_yt_dlp_fresh(max_age_days=YT_DLP_MAX_AGE_DAYS)
    assert version == "2026.08.19"
    assert age >= 0
    assert stale is False


def test_old_version_is_flagged_stale() -> None:
    with patch(f"{_PATCH}.yt_dlp_version", return_value="2020.01.01"):
        _, age, stale = check_yt_dlp_fresh()
    assert age > YT_DLP_MAX_AGE_DAYS
    assert stale is True


def test_unknown_version_never_blocks() -> None:
    """No yt-dlp version → don't claim it's stale, the import may work fine."""
    with patch(f"{_PATCH}.yt_dlp_version", return_value=""):
        version, age, stale = check_yt_dlp_fresh()
    assert version == ""
    assert age == -1
    assert stale is False


# ── yt_dlp_version ─────────────────────────────────────────────────────────


def test_version_reads_stdout() -> None:
    result = MagicMock(returncode=0, stdout="2026.07.04\n")
    with (
        patch(f"{_PATCH}.shutil.which", return_value="/opt/homebrew/bin/yt-dlp"),
        patch(f"{_PATCH}.subprocess.run", return_value=result),
    ):
        assert yt_dlp_version() == "2026.07.04"


def test_version_empty_when_binary_missing() -> None:
    with (
        patch(f"{_PATCH}.shutil.which", return_value=None),
        patch(f"{_PATCH}._YT_DLP_CANDIDATE_DIRS", ()),
    ):
        assert yt_dlp_version() == ""


def test_version_empty_on_failure() -> None:
    result = MagicMock(returncode=1, stdout="")
    with (
        patch(f"{_PATCH}.shutil.which", return_value="/usr/bin/yt-dlp"),
        patch(f"{_PATCH}.subprocess.run", return_value=result),
    ):
        assert yt_dlp_version() == ""


# ── Update hint ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("resolved", "expected"),
    [
        # The shim in ~/.local/bin says nothing — the symlink target does.
        ("/Users/x/.local/share/uv/tools/yt-dlp/bin/yt-dlp", "uv tool upgrade yt-dlp"),
        ("/Users/x/.local/pipx/venvs/yt-dlp/bin/yt-dlp", "pipx upgrade yt-dlp"),
        # `yt-dlp -U` refuses to run on a brew install — don't send users there.
        ("/opt/homebrew/Cellar/yt-dlp/2026.7.4/bin/yt-dlp", "brew upgrade yt-dlp"),
        ("/srv/app/.venv/bin/yt-dlp", "pip install -U yt-dlp"),
        ("/usr/bin/yt-dlp", "yt-dlp -U"),
    ],
)
def test_update_hint_matches_the_install_method(resolved: str, expected: str) -> None:
    with (
        patch(f"{_PATCH}.yt_dlp_path", return_value=resolved),
        patch(f"{_PATCH}.os.path.realpath", side_effect=lambda p: p),
    ):
        assert yt_dlp_update_hint() == expected


# ── Binary resolution ──────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_resolved_path():
    """The resolved path is cached per process — clear it between tests."""
    checks._yt_dlp_path = None
    yield
    checks._yt_dlp_path = None


def test_newest_wins_over_path_order(tmp_path) -> None:
    """A current yt-dlp must beat an outdated one that sits earlier in PATH.

    Regression: the user's PATH had ~/.local/bin (yt-dlp 2026.08.19) *after*
    /opt/homebrew/bin (2026.07.04), so every import silently ran the build
    YouTube answers 403 to.
    """
    old = tmp_path / "brew" / "yt-dlp"
    new = tmp_path / "local" / "yt-dlp"
    for path in (old, new):
        path.parent.mkdir(parents=True)
        path.write_text("#!/bin/sh\n")

    versions = {str(old): "2026.07.04", str(new): "2026.08.19"}
    with (
        patch(f"{_PATCH}.shutil.which", return_value=str(old)),
        patch(f"{_PATCH}._YT_DLP_CANDIDATE_DIRS", (str(new.parent), str(old.parent))),
        patch(f"{_PATCH}._probe_version", side_effect=lambda exe: versions.get(exe, "")),
    ):
        assert checks.yt_dlp_path() == str(new)


def test_resolution_is_cached(tmp_path) -> None:
    """Probing costs a subprocess per candidate — do it once per process."""
    binary = tmp_path / "yt-dlp"
    binary.write_text("#!/bin/sh\n")
    probe = MagicMock(return_value="2026.08.19")
    with (
        patch(f"{_PATCH}.shutil.which", return_value=str(binary)),
        patch(f"{_PATCH}._YT_DLP_CANDIDATE_DIRS", ()),
        patch(f"{_PATCH}._probe_version", probe),
    ):
        checks.yt_dlp_path()
        checks.yt_dlp_path()
    assert probe.call_count == 1


def test_falls_back_to_path_when_no_version_parses(tmp_path) -> None:
    """A source checkout reports no date — still usable, don't return nothing."""
    binary = tmp_path / "yt-dlp"
    binary.write_text("#!/bin/sh\n")
    with (
        patch(f"{_PATCH}.shutil.which", return_value=str(binary)),
        patch(f"{_PATCH}._YT_DLP_CANDIDATE_DIRS", ()),
        patch(f"{_PATCH}._probe_version", return_value="dev"),
    ):
        assert checks.yt_dlp_path() == str(binary)


def test_no_yt_dlp_anywhere() -> None:
    with (
        patch(f"{_PATCH}.shutil.which", return_value=None),
        patch(f"{_PATCH}._YT_DLP_CANDIDATE_DIRS", ()),
    ):
        assert checks.yt_dlp_path() == ""


def test_version_reports_the_binary_that_will_run(tmp_path) -> None:
    """The reported version must be the one actually used, not PATH's."""
    binary = tmp_path / "yt-dlp"
    binary.write_text("#!/bin/sh\n")
    with (
        patch(f"{_PATCH}.shutil.which", return_value=str(binary)),
        patch(f"{_PATCH}._YT_DLP_CANDIDATE_DIRS", ()),
        patch(f"{_PATCH}._probe_version", return_value="2026.08.19"),
    ):
        assert yt_dlp_version() == "2026.08.19"
