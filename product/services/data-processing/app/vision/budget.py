"""The character budget — a chars-per-second-of-life dial, and a CORRECTNESS knob (D-11).

The training currency is *characters per day-log block*: acquisition was measured
falling 3.2× for a 3.7× rise in chars/block. So the per-record caption/ocr length is
not a cosmetic cap — it sets the dose. This module is the one place that turns the
``VIDEO_CHARS_PER_SECOND`` dial into a per-record character cap, applied as
``cap = round(rate × span_seconds)`` so the dose is identical at any chunk length
(that span-parametricity is what lets DP ship at 10 s and absorb 60 s as an .env
change — D-01).

``VIDEO_CHARS_PER_SECOND = 22`` total, split ``16`` caption / ``6`` ocr. Truncation is
deterministic (sentence boundary for the caption, word boundary for OCR) so the same
reply truncates the same way on every worker in the fleet — a determinism contract,
not an aesthetic one. Pure functions, no I/O: importable by the clip primary (WS-D)
AND the OCR assembler (WS-C) without pulling either stage's dependencies.
"""
from __future__ import annotations

from .config import VisionSettings

# Sentence terminators that end a truncatable clause.
_TERMINATORS = ".!?"


def _caption_rate(vs: VisionSettings) -> int:
    """chars/second-of-life the caption may spend (the rest goes to the OCR record)."""
    return max(0, min(vs.caption_chars_share, vs.chars_per_second))


def _ocr_rate(vs: VisionSettings) -> int:
    """chars/second-of-life the OCR record may spend = total − caption share (>= 0)."""
    return max(0, vs.chars_per_second - _caption_rate(vs))


def caption_cap(span_seconds: float, vs: VisionSettings) -> int:
    """Max characters for one clip caption over a ``span_seconds`` chunk (D-11). The
    caption text the primary writes to /context is truncated to this on a sentence
    boundary, so the dose is span-invariant at a fixed rate."""
    return max(0, round(_caption_rate(vs) * max(0.0, span_seconds)))


def ocr_cap(span_seconds: float, vs: VisionSettings) -> int:
    """Max characters for one OCR digest record over a ``span_seconds`` chunk (D-11)."""
    return max(0, round(_ocr_rate(vs) * max(0.0, span_seconds)))


def caption_word_bounds(span_seconds: float, vs: VisionSettings) -> tuple[int, int]:
    """A (low, high) word-count band for the caption prompt, DERIVED from
    ``caption_cap`` at ~6 chars/word with a 20 % floor. This is guidance handed to the
    model in the prompt (``[[words_lo]]-[[words_hi]]``); the hard char cap is still
    ``caption_cap`` applied to the rendered text. At span=60/R=16 → ~(128, 160); at
    span=10 → ~(21, 27) — a description that scales with the length of life it covers,
    never a mandated line/word count (D-10 rejects padded static screens)."""
    cap = caption_cap(span_seconds, vs)
    hi = max(1, round(cap / 6))
    lo = max(1, round(hi * 0.8))
    return lo, hi


def truncate_sentence(text: str, cap: int) -> str:
    """Truncate ``text`` to at most ``cap`` characters on a SENTENCE boundary — the
    last ``.``/``!``/``?`` (followed by whitespace or end) at or before ``cap``. Falls
    back to :func:`truncate_word` when no sentence boundary fits, so a single run-on
    reply is still cut cleanly. Deterministic: identical (text, cap) → identical result
    on every worker."""
    text = text.strip()
    if cap <= 0:
        return ""
    if len(text) <= cap:
        return text
    cut = -1
    limit = min(cap, len(text))
    for i in range(limit):
        if text[i] in _TERMINATORS and (i + 1 >= len(text) or text[i + 1].isspace()):
            cut = i + 1  # include the terminator; length i+1 <= cap
    if cut > 0:
        return text[:cut].strip()
    return truncate_word(text, cap)


def truncate_word(text: str, cap: int) -> str:
    """Truncate ``text`` to at most ``cap`` characters on a WORD boundary (the last
    space at or before ``cap``). A single word longer than ``cap`` is hard-cut so the
    length bound always holds. Deterministic."""
    text = text.strip()
    if cap <= 0:
        return ""
    if len(text) <= cap:
        return text
    head = text[:cap]
    space = head.rfind(" ")
    if space > 0:
        return head[:space].rstrip()
    return head.rstrip()  # one word longer than the cap: hard cut, bound preserved
