"""The banned-word matcher.

shiplock ships no word list: each repo declares its own in ``[style].banned``
and can exempt a word per repo with ``[style].allow``. A repo that declares no
words gets a skip notice from the ``banned-words`` check, never a silent pass.

Matching is word-boundary and case-insensitive, so a banned word inside a
longer identifier doesn't match, and a capitalised heading matches the same as
lowercase prose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class BannedHit:
    """One banned word found at a line in a file."""

    line: int
    word: str


def effective_words(banned: tuple[str, ...] = (), allow: tuple[str, ...] = ()) -> set[str]:
    """The lowercase word set enforced: the declared words minus the exempt ones."""
    allow_lower = {w.lower() for w in allow}
    return {w.lower() for w in banned} - allow_lower


def _compile(words: set[str]) -> re.Pattern[str] | None:
    if not words:
        return None
    alternation = "|".join(re.escape(w) for w in sorted(words))
    return re.compile(rf"\b({alternation})\b", re.IGNORECASE)


def find_banned(
    text: str,
    banned: tuple[str, ...] = (),
    allow: tuple[str, ...] = (),
) -> list[BannedHit]:
    """Return every banned-word hit in ``text``, one per occurrence, by line."""
    pattern = _compile(effective_words(banned, allow))
    if pattern is None:
        return []
    hits: list[BannedHit] = []
    for i, line in enumerate(text.splitlines(), start=1):
        for match in pattern.finditer(line):
            hits.append(BannedHit(line=i, word=match.group(1).lower()))
    return hits
