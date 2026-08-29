"""Banned-word matcher: edge cases first, then the ordinary hit."""

from __future__ import annotations

from shiplock._style import find_banned


# --- adversarial edges: things that must NOT trip -------------------------


def test_substring_inside_identifier_is_not_a_hit():
    # "realtime" contains "real" but has no word boundary; it must not trip.
    assert find_banned("a realtime pipeline") == []


def test_plural_boundary_is_respected():
    # "gaps_analysis" is an identifier; "gaps" is only a hit as a whole word.
    assert find_banned("the gaps_analysis module") == []


def test_allow_exempts_a_house_word():
    hits = find_banned("this is real", allow=("real",))
    assert hits == []


# --- the hits that must fire ----------------------------------------------


def test_whole_word_is_a_hit():
    hits = find_banned("this is real")
    assert [h.word for h in hits] == ["real"]


def test_matching_is_case_insensitive():
    hits = find_banned("Gaps everywhere")
    assert [h.word for h in hits] == ["gaps"]


def test_extra_banned_extends_the_list():
    hits = find_banned("a frobnicate call", extra_banned=("frobnicate",))
    assert [h.word for h in hits] == ["frobnicate"]


def test_line_numbers_are_reported():
    text = "clean line\nthis is real\n"
    hits = find_banned(text)
    assert len(hits) == 1
    assert hits[0].line == 2
