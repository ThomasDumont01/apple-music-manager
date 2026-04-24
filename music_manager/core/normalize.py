"""String normalization and fuzzy matching for titles and artists.

Simple API:
- normalize(text) → canonical form for exact comparison (dedup, album matching)
- is_match(a, b, kind, threshold) → fuzzy matching for Resolver search/filter
- match_score(a, b, kind) → raw similarity score 0-100
- first_artist(text) → extract primary artist from multi-artist string
"""

import re
import unicodedata
from typing import Literal

from rapidfuzz import fuzz

# ── Constants ────────────────────────────────────────────────────────────────

Kind = Literal["title", "artist"]

_PAREN_SUFFIX_RE = re.compile(r"(?:\s*\([^)]*\))+\s*$")

_FEAT_RE = re.compile(
    r"\s+(?:feat\.?|ft\.?|featuring|vs\.?|with|x)\s+.*$",
    flags=re.IGNORECASE,
)

_ARTIST_SEP_RE = re.compile(
    r"\s*[,;&]\s*|\s+/\s+|\s+x\s+",
    flags=re.IGNORECASE,
)

_FILLER_TOKENS = frozenset(
    {
        "the",
        "le",
        "la",
        "les",
        "los",
        "las",
        "el",
        "il",
        "lo",
        "de",
        "du",
        "da",
        "dos",
        "des",
        "di",
        "y",
        "and",
    }
)

_DEFAULT_THRESHOLDS = {
    "title": 85.0,
    "artist": 90.0,
}


# ── Entry point ──────────────────────────────────────────────────────────────


def match_score(a: str, b: str, kind: Kind) -> float:
    """Return fuzzy similarity score (0-100) between two strings.

    Title uses token_sort_ratio (strict).
    Artist uses token_set_ratio with content-token guard (lenient on articles,
    strict on content words — prevents "Dave" matching "Dave Brubeck").
    """
    if kind == "title":
        return fuzz.token_sort_ratio(prepare_title(a), prepare_title(b))
    prepared_a = normalize(_strip_featuring(a))
    prepared_b = normalize(_strip_featuring(b))
    return _artist_score(prepared_a, prepared_b)


def is_match(a: str, b: str, kind: Kind, threshold: float | None = None) -> bool:
    """Return True if match_score(a, b, kind) >= threshold.

    Default thresholds: title=85, artist=90.
    """
    if threshold is None:
        threshold = _DEFAULT_THRESHOLDS[kind]
    return match_score(a, b, kind) >= threshold


def normalize(text: str) -> str:
    """Lowercase, strip accents, strip punctuation, normalize whitespace.

    The canonical form for exact comparison. Never use for display.
    Preserves CJK/non-latin characters when ASCII encoding would lose them.
    """
    nfd = unicodedata.normalize("NFD", text)
    ascii_attempt = nfd.encode("ascii", "ignore").decode()
    if ascii_attempt.strip() or not text.strip():
        # Latin text or empty — strip accents as before
        result = ascii_attempt.lower()
    else:
        # Non-latin (CJK, Arabic, Cyrillic) — keep original chars lowercased
        result = nfd.lower()
    result = result.replace("&", " and ")
    result = re.sub(
        r"[^a-z0-9\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af "
        r"\u0400-\u04ff\u0600-\u06ff]",
        " ",
        result,
    )
    return " ".join(result.split())


def first_artist(text: str) -> str:
    """Extract primary artist from multi-artist string.

    Splits on ',', ';', '&', ' / ', ' x '. Preserves band names without
    whitespace-separated slashes (e.g. 'AC/DC' stays intact).
    """
    parts = _ARTIST_SEP_RE.split(text, maxsplit=1)
    return parts[0].strip()


def prepare_title(text: str) -> str:
    """Strip trailing parenthetical groups then normalize.

    'Imagine (Remastered 2010)' → 'imagine'
    'Song (feat. X) (Live)'    → 'song'
    """
    stripped = _PAREN_SUFFIX_RE.sub("", text).strip()
    return normalize(stripped) if stripped else normalize(text)


# ── Private Functions ────────────────────────────────────────────────────────


def _strip_featuring(text: str) -> str:
    """Remove featuring suffix from artist string."""
    return _FEAT_RE.sub("", text).rstrip()


def _artist_score(a: str, b: str) -> float:
    """token_set_ratio with content-token guard against subset false positives.

    Falls back to strict token_sort_ratio when extra tokens are content words
    (not articles/fillers). Prevents "Dave" matching "Dave Brubeck" while
    preserving "The Beatles" matching "Beatles".
    """
    tokens_a = set(a.split())
    tokens_b = set(b.split())
    if not tokens_a or not tokens_b:
        return 0.0
    extras = (tokens_a | tokens_b) - (tokens_a & tokens_b)
    content_extras = extras - _FILLER_TOKENS
    if content_extras:
        return fuzz.token_sort_ratio(a, b)
    return fuzz.token_set_ratio(a, b)
