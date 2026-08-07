"""The character budget (D-11): span-parametric caps + deterministic truncation.

The rates are explicit arguments (L4): the live values are code pins in the stage
files (clipcap 16, screentext 6, splitting 22 total), never env knobs. These tests
exercise the math at those pinned rates.
"""
from __future__ import annotations

from app.vision.budget import (
    caption_cap,
    caption_word_bounds,
    ocr_cap,
    truncate_sentence,
    truncate_word,
)

CAPTION_RATE = 16   # the clipcap stage pin
OCR_RATE = 6.0      # the screentext stage pin


# --------------------------------------------------------------------------- caps
def test_caption_cap_is_rate_times_span():
    assert caption_cap(60, CAPTION_RATE) == 16 * 60      # 960
    assert caption_cap(10, CAPTION_RATE) == 16 * 10      # 160
    assert caption_cap(0, CAPTION_RATE) == 0


def test_ocr_cap_is_rate_times_span():
    assert ocr_cap(60, OCR_RATE) == 6 * 60               # 360
    assert ocr_cap(10, OCR_RATE) == 6 * 10               # 60


def test_dose_is_span_invariant_at_a_fixed_rate():
    """The whole point of D-11: chars/second-of-life is constant, so the training dose is
    identical at any chunk length — that is what makes the design span-parametric (D-01)."""
    assert caption_cap(60, CAPTION_RATE) / 60 == caption_cap(10, CAPTION_RATE) / 10
    assert ocr_cap(60, OCR_RATE) / 60 == ocr_cap(10, OCR_RATE) / 10


def test_rate_change_moves_the_cap():
    """Changing a rate changes record bytes — which is exactly why the rates are vB-pinned
    in the stage files, never env (L4)."""
    assert caption_cap(60, 10) == 600
    assert ocr_cap(60, 5) == 300


def test_negative_inputs_are_zero():
    assert caption_cap(-5, CAPTION_RATE) == 0
    assert ocr_cap(-5, OCR_RATE) == 0
    assert caption_cap(60, -1) == 0


# ---------------------------------------------------------------------- word bounds
def test_word_bounds_are_a_band_below_the_cap():
    lo, hi = caption_word_bounds(60, CAPTION_RATE)
    assert 0 < lo < hi
    assert hi == round(caption_cap(60, CAPTION_RATE) / 6)
    # scales down with the span
    lo10, hi10 = caption_word_bounds(10, CAPTION_RATE)
    assert hi10 < hi and lo10 < lo


# ----------------------------------------------------------------- truncate_sentence
def test_truncate_sentence_keeps_short_text_verbatim():
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
