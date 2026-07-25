"""WS-D — the character budget (D-11): span-parametric caps + deterministic truncation."""
from __future__ import annotations

import dataclasses

import pytest

from app.vision.budget import (
    caption_cap,
    caption_word_bounds,
    ocr_cap,
    truncate_sentence,
    truncate_word,
)
from app.vision.config import get_vision_settings


@pytest.fixture()
def vs():
    return get_vision_settings()  # defaults: chars_per_second=22, caption_chars_share=16


# --------------------------------------------------------------------------- caps
def test_caption_cap_is_rate_times_span(vs):
    assert caption_cap(60, vs) == 16 * 60      # 960
    assert caption_cap(10, vs) == 16 * 10      # 160
    assert caption_cap(0, vs) == 0


def test_ocr_cap_is_the_remaining_share(vs):
    # 22 total − 16 caption = 6 ocr chars/second-of-life
    assert ocr_cap(60, vs) == 6 * 60           # 360
    assert ocr_cap(10, vs) == 6 * 10           # 60


def test_dose_is_span_invariant_at_a_fixed_rate(vs):
    """The whole point of D-11: chars/second-of-life is constant, so the training dose is
    identical at any chunk length — that is what makes the design span-parametric (D-01)."""
    assert caption_cap(60, vs) / 60 == caption_cap(10, vs) / 10
    assert ocr_cap(60, vs) / 60 == ocr_cap(10, vs) / 10


def test_rate_change_moves_the_cap(vs):
    lean = dataclasses.replace(vs, chars_per_second=15, caption_chars_share=10)
    assert caption_cap(60, lean) == 10 * 60
    assert ocr_cap(60, lean) == 5 * 60


def test_caption_share_over_total_is_clamped_and_ocr_never_negative(vs):
    weird = dataclasses.replace(vs, chars_per_second=10, caption_chars_share=99)
    assert caption_cap(60, weird) == 10 * 60   # caption share clamped to the total
    assert ocr_cap(60, weird) == 0             # nothing left, and never below zero


def test_negative_span_is_zero(vs):
    assert caption_cap(-5, vs) == 0
    assert ocr_cap(-5, vs) == 0


# ---------------------------------------------------------------------- word bounds
def test_word_bounds_are_a_band_below_the_cap(vs):
    lo, hi = caption_word_bounds(60, vs)
    assert 0 < lo < hi
    assert hi == round(caption_cap(60, vs) / 6)
    # scales down with the span
    lo10, hi10 = caption_word_bounds(10, vs)
    assert hi10 < hi and lo10 < lo


# ----------------------------------------------------------------- truncate_sentence
def test_truncate_sentence_keeps_short_text_verbatim(vs):
    assert truncate_sentence("Short caption.", 100) == "Short caption."


def test_truncate_sentence_cuts_on_a_sentence_boundary_within_cap():
    text = "Alpha beta gamma. Delta epsilon zeta. Eta theta iota."
    out = truncate_sentence(text, 30)
    assert out == "Alpha beta gamma."            # last '.' at or before 30
    assert len(out) <= 30


def test_truncate_sentence_handles_bang_and_question():
    assert truncate_sentence("Done! Now what? Keep going.", 12) == "Done!"


def test_truncate_sentence_falls_back_to_word_when_no_boundary_fits():
    text = "one two three four five six seven eight nine ten"
    out = truncate_sentence(text, 15)
    assert len(out) <= 15
    assert not out.endswith(" ")
    assert " " not in out[-1:]                   # cut on a word, not mid-word


def test_truncate_sentence_is_deterministic():
    text = "First sentence here. Second sentence follows. Third one too."
    assert truncate_sentence(text, 40) == truncate_sentence(text, 40)


def test_truncate_sentence_zero_cap_is_empty():
    assert truncate_sentence("anything.", 0) == ""


# -------------------------------------------------------------------- truncate_word
def test_truncate_word_cuts_on_a_space_within_cap():
    out = truncate_word("the quick brown fox jumps", 12)
    assert out == "the quick"
    assert len(out) <= 12


def test_truncate_word_hard_cuts_a_single_long_token():
    out = truncate_word("supercalifragilisticexpialidocious", 10)
    assert out == "supercalif"
    assert len(out) == 10


def test_truncate_word_short_text_verbatim():
    assert truncate_word("tiny", 100) == "tiny"


def test_truncations_never_exceed_the_cap():
    text = "A moderately long caption that will certainly need to be cut down to size here."
    for cap in range(1, len(text) + 5):
        assert len(truncate_sentence(text, cap)) <= cap
        assert len(truncate_word(text, cap)) <= cap
