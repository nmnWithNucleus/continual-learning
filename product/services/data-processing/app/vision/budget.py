"""The character budget — a chars-per-second-of-life dial, and a CORRECTNESS knob.

The training currency is *characters per day-log block*: acquisition was measured
falling 3.2× for a 3.7× rise in chars/block. So the per-record caption/ocr length is
not a cosmetic cap — it sets the dose. This module turns a chars-per-second-of-life
RATE into a per-record character cap, applied as ``cap = round(rate × span_seconds)``
so the dose is identical at any chunk length (that span-parametricity is what lets DP
ship at 10 s and absorb 60 s without touching identity).

NO CONFIG (L4): no env knob sets these rates. They are code pins in the stage files that
spend them — ``app/stages/video/clipcap.py`` pins the caption rate (16),
``app/stages/video/screentext.py`` pins the OCR rate (6), splitting 22 chars per
second-of-life between them. Changing a rate there is a vB bump.
Everything here is a pure function of ``(span_seconds, rate)`` / ``(text, cap)``.

Truncation is deterministic (sentence boundary for the caption, word boundary for OCR)
so the same reply truncates the same way on every worker in the fleet — a determinism
contract, not an aesthetic one.
"""
from __future__ import annotations

# Sentence terminators that end a truncatable clause.
_TERMINATORS = ".!?"


def caption_cap(span_seconds: float, rate: float) -> int:
    """Max characters for one clip caption over a ``span_seconds`` chunk at ``rate``
    chars-per-second-of-life. The caption text is truncated to this on a
    sentence boundary, so the dose is span-invariant at a fixed rate."""
    return max(0, round(max(0.0, rate) * max(0.0, span_seconds)))


def ocr_cap(span_seconds: float, rate: float) -> int:
    """Max characters for one OCR digest over a ``span_seconds`` chunk."""
    return max(0, round(max(0.0, rate) * max(0.0, span_seconds)))


def caption_word_bounds(span_seconds: float, rate: float) -> tuple[int, int]:
    """A (low, high) word-count band for the caption prompt, DERIVED from
    ``caption_cap`` at ~6 chars/word with a 20 % floor. This is guidance handed to the
    model in the prompt (``[[words_lo]]-[[words_hi]]``); the hard char cap is still
    ``caption_cap`` applied to the rendered text. At span=60/rate=16 → ~(128, 160); at
    span=10 → ~(21, 27) — a description that scales with the length of life it covers,
    never a mandated line/word count — a mandate pads a static screen with words."""
    cap = caption_cap(span_seconds, rate)
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
