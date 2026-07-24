"""Clip caption emission — the deterministic single-line render + the record (WS-D, D2).

Turns a parsed ``ClipDesc`` into the ONE ``kind='caption'`` ``ProcessedUnit`` the clip
primary emits, enforcing three record laws in one pure, DP-side place:

  * **D-10 — one paragraph.** The render is ``"{app} — {activity}. {description}"`` (degraded
    gracefully when the parse ladder left ``app`` / ``activity`` empty). No per-interval
    lines: a mandated line count punishes honesty on a static screen (the redundancy this
    design exists to remove).
  * **D-11 — the char budget is a CORRECTNESS knob.** The rendered text is truncated to
    ``caption_cap(span, vs)`` on a sentence boundary. ``VIDEO_CHARS_PER_SECOND`` is a
    chars-per-second-of-life dial that sets the training DOSE (acquisition fell 3.2× for a
    3.7× rise in chars/block) — not a cosmetic cap. Span-parametric, so the dose is identical
    at 10 s and 60 s.
  * **D-12 — ``content.text`` is a single line.** No ``\\n`` ever (``daylog.py`` only strips
    the block edges; an embedded newline floats unlabelled in the training block). The parse
    ladder already collapses each field; the render re-asserts it on the joined string.

The record identity is a pure function of the chunk, NOT of the model output (D-05): exactly
one unit, ``discriminator=""`` (the canonical v0 1:1 id), ``t_start=None`` / ``t_end=None`` so
``build_c2`` carries the C1 span strings VERBATIM — ``c2["t_start"] == c1["t_start"]``
byte-for-byte, and ``abs_time`` (the ``Z`` vs ``+00:00`` boundary hazard) is never called.

**RULE, carried forward from D-05 (read before relaxing the OCR record count):** if
``VIDEO_OCR_MAX_EVENTS`` is ever relaxed to emit >1 ocr record per chunk, the discriminator
MUST become a quantisation of a grid that is itself a pure function of the declared C1 span —
never a survivor ordinal, never a raw decoder frame index, never a hash of model output — and
every unit MUST get a distinct ``t_start``. The caption record here is fixed at exactly one,
so it stays ``discriminator=""``.
"""
from __future__ import annotations

from ..processing.base import ProcessedContent, ProcessedUnit, empty_enrichments
from .budget import caption_cap, truncate_sentence
from .clip_types import ClipDesc
from .config import VisionSettings


def render_caption(desc: ClipDesc, span_seconds: float, vs: VisionSettings) -> str:
    """The single-line, budgeted caption string (D-10 / D-11 / D-12). Pure and
    deterministic: identical ``(desc, span, vs)`` → identical text on every worker."""
    app = desc.app.strip()
    activity = desc.activity.strip()
    description = desc.description.strip()

    # D-10: "app — activity." lead when present, degrading so a fallback (empty app /
    # activity) never renders a dangling " — " or a leading ". ".
    if app and activity:
        lead = f"{app} — {activity}."
    elif app:
        lead = f"{app}."
    elif activity:
        lead = f"{activity}."
    else:
        lead = ""
    text = f"{lead} {description}".strip() if lead else description

    # D-12: collapse every whitespace run (incl. any stray newline) to one space. The parse
    # ladder already cleaned each field; this guards the joined string (defence in depth).
    text = " ".join(text.split())

    # D-11: truncate to the per-record char budget on a sentence boundary (word-boundary
    # fallback inside truncate_sentence). caption_cap == 0 → "" (a valid empty caption).
    return truncate_sentence(text, caption_cap(span_seconds, vs))


def caption_unit(desc: ClipDesc, span_seconds: float, vs: VisionSettings) -> ProcessedUnit:
    """The ONE ``kind='caption'`` unit the clip primary emits (D-05). ``discriminator=""``
    and ``t_start=None`` are the load-bearing invariants — the record set is a pure function
    of the chunk, and ``build_c2`` carries the C1 span verbatim (byte-for-byte t_start)."""
    return ProcessedUnit(
        content=ProcessedContent(kind="caption", text=render_caption(desc, span_seconds, vs)),
        enrichments=empty_enrichments(),
        discriminator="",
        t_start=None,
        t_end=None,
    )
