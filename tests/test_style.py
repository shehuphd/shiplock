"""Banned-word matcher: edge cases first, then the ordinary hit.

shiplock ships no word list, so every test declares the words it holds the
text against. An edge test run with no words would pass for the wrong reason.
"""

from __future__ import annotations

from shiplock._style import find_banned

WORDS = ("real", "gaps")


# --- adversarial edges: things that must NOT trip -------------------------


def test_substring_inside_identifier_is_not_a_hit():
    # "realtime" contains "real" but has no word boundary; it must not trip.
    assert find_banned("a realtime pipeline", banned=WORDS) == []


def test_plural_boundary_is_respected():
    # "gaps_analysis" is an identifier; "gaps" is only a hit as a whole word.
    assert find_banned("the gaps_analysis module", banned=WORDS) == []


def test_allow_exempts_a_declared_word():
    assert find_banned("this is real", banned=WORDS, allow=("real",)) == []


def test_no_declared_words_matches_nothing():
    assert find_banned("this is real") == []


# --- the hits that must fire ----------------------------------------------


def test_whole_word_is_a_hit():
    hits = find_banned("this is real", banned=WORDS)
    assert [h.word for h in hits] == ["real"]


def test_matching_is_case_insensitive():
    hits = find_banned("Gaps everywhere", banned=WORDS)
    assert [h.word for h in hits] == ["gaps"]


def test_line_numbers_are_reported():
    hits = find_banned("clean line\nthis is real\n", banned=WORDS)
    assert len(hits) == 1
    assert hits[0].line == 2
