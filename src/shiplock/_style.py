"""The house banned-word list and the matcher over it.

This module is the one carve-out from shiplock's own banned-word sweep: it has
to name the forbidden words in order to check for them, so a repo checking
itself excludes this file via ``[style].exclude``. Every repo's config can
extend the list (``extra_banned``) or exempt a word (``allow``).

Matching is word-boundary and case-insensitive, so an identifier like
``realtime`` never trips on ``real`` and a header like ``Gaps`` is caught the
same as ``gaps``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# CLAUDE.md's list plus the inflections seen in live sweeps. Word-boundary
# matching keeps substrings inside larger identifiers from false-positiving.
BANNED_WORDS = frozenset(
    {
        "travel",
        "matters",
        "serious",
        "intersection",
        "quietly",
        "exactly",
        "genuine",
        "genuinely",
        "consistently",
        "straightforward",
        "worth",
        "earn",
        "earns",
        "sits",
        "sat",
        "lands",
        "landed",
        "shapes",
        "actually",
        "land",
        "shifts",
        "gap",
        "gaps",
        "sharper",
        "cleanly",
        "real",
    }
)


@dataclass(frozen=True)
class BannedHit:
    """One banned word found at a line in a file."""

    line: int
    word: str


def effective_words(
    extra_banned: tuple[str, ...] = (), allow: tuple[str, ...] = ()
) -> set[str]:
    """The lowercase word set actually enforced, after extend and exempt."""
    allow_lower = {w.lower() for w in allow}
    words = {w.lower() for w in BANNED_WORDS} | {w.lower() for w in extra_banned}
    return words - allow_lower


def _compile(words: set[str]) -> re.Pattern[str] | None:
    if not words:
        return None
    alternation = "|".join(re.escape(w) for w in sorted(words))
    return re.compile(rf"\b({alternation})\b", re.IGNORECASE)


def find_banned(
    text: str,
    extra_banned: tuple[str, ...] = (),
    allow: tuple[str, ...] = (),
) -> list[BannedHit]:
    """Return every banned-word hit in ``text``, one per occurrence, by line."""
    pattern = _compile(effective_words(extra_banned, allow))
    if pattern is None:
        return []
    hits: list[BannedHit] = []
    for i, line in enumerate(text.splitlines(), start=1):
        for match in pattern.finditer(line):
            hits.append(BannedHit(line=i, word=match.group(1).lower()))
    return hits
