"""OCR post-processing → one self-anchored single line (D-07 steps 2/3/5/6, D-08, D-12).

Given the raw ``OcrRead``s a backend returned for a chunk, produce the single ``str`` that
``screentext`` both (a) commits as the ``ocr_text`` slot the caption injects (D-09) and
(b) emits as the ``kind='ocr'`` record's ``content.text`` — ONE rendered witness on two
channels (§4 R2 Corollary 2), never two independent claims.

The D-07 post-processing order, applied verbatim (event selection — step 1's floor grid ∪
change events, capped — is WS-B's ``clipprep``; here we run everything AFTER the read):

  1. drop boxes with ``confidence < min_conf``;
  2. sort into reading order by bbox and assign a REGION ROLE from bbox position — the
     semantically useful 80% of "location" as a word, at zero contract cost; the pixel
     geometry is then DISCARDED (never emitted to C2 — D-08);
  3. drop lines shorter than ``min_chars``;
  4. deterministic secret redaction (``redact.py``);
  5. drop a line >= ``dedup_ratio`` similar to the previous kept line, WITHIN this chunk
     (cross-chunk state is forbidden — it would break fleet determinism, A-5);
  6. render to ONE line (no ``\n`` ever — D-12; separator ``" · "``), truncated at the
     chars-per-second-of-life budget (D-11) on a WORD boundary.

Self-anchored (R2 corollary): every kept item carries its own ``+Ns`` offset and role
inside the text, so the record is understandable alone.
"""
from __future__ import annotations

import difflib
import re

from ..clip_types import OcrRead
from .config import OcrConfig
from .redact import redact

# The frozen role vocabulary (D-07 step 2 / clip_types.OcrRegion.role).
ROLES = (
    "titlebar", "tab", "sidebar", "main", "compose",
    "message", "toolbar", "statusbar", "dialog", "notification",
)

_WS_RUN = re.compile(r"\s+")

# ---- budget (D-11) --------------------------------------------------------------
# Local stub of WS-D's ``app/vision/budget.py`` — the OCR share is a chars-per-second-
# of-life dial (16 caption / 6 ocr split of VIDEO_CHARS_PER_SECOND=22). Kept local so a
# WS-D lag never blocks this workstream; when budget.py lands, this collapses to an
# import. cap = round(R × span_seconds), so the dose is identical at any chunk length.


def ocr_cap(span_seconds: float, cfg: OcrConfig) -> int:
    """Character budget for the OCR record at this span (D-11). Never negative."""
    return max(0, round(cfg.ocr_chars_per_second * max(0.0, span_seconds)))


def truncate_word(text: str, cap: int) -> str:
    """Truncate ``text`` to at most ``cap`` chars on a word boundary (deterministic).
    ``cap <= 0`` means "no budget" -> empty string; a text already within budget is
    returned unchanged."""
    if cap <= 0:
        return ""
    if len(text) <= cap:
        return text
    cut = text[:cap]
    sp = cut.rfind(" ")
    if sp > 0:
        cut = cut[:sp]
    return cut.rstrip()


# ---- region role from bbox ------------------------------------------------------


def _clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v


def assign_role(bbox: tuple[float, float, float, float]) -> str:
    """Assign a coarse region role from a NORMALIZED (0..1) bbox ``(x0,y0,x1,y1)``.

    A position heuristic over the box centroid — model-/engine-asserted, "good enough" to
    be the semantic 80% of location (A-14), not a substitute for real geometry (which is
    the parked ``enrichments.text_regions[]``, D-08/§6.2). Deterministic; returns one of
    ``ROLES``. Backends that already carry a role (the ``vlm`` arm's model output) skip
    this and keep theirs."""
    x0, y0, x1, y1 = (_clamp01(c) for c in bbox)
    # A degenerate / zero-area bbox carries no position — e.g. the vlm arm emits no geometry
    # (bbox=(0,0,0,0)). Default to the content region rather than letting cx=cy=0 fall through
    # to the titlebar band at the top of the heuristic.
    if x1 <= x0 or y1 <= y0:
        return "main"
    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0
    w, h = (x1 - x0), (y1 - y0)
    # Very top strip -> the window/title bar (the strongest UI signal).
    if cy <= 0.05:
        return "titlebar"
    # Top-right transient (badge / banner) — checked before the tab/toolbar band so a
    # top-right toast is not mislabelled a toolbar.
    if cy <= 0.30 and cx >= 0.82:
        return "notification"
    # Upper strip: tabs on the left, toolbar on the right.
    if cy <= 0.12:
        return "tab" if cx < 0.5 else "toolbar"
    # Very bottom strip -> the status bar.
    if cy >= 0.95:
        return "statusbar"
    # Left rail.
    if cx <= 0.18:
        return "sidebar"
    # A medium, centered box reads as a modal dialog — with a size FLOOR so a small
    # centered label (a button caption) is not mistaken for a dialog.
    if 0.30 <= cx <= 0.70 and 0.30 <= cy <= 0.70 and 0.15 <= w <= 0.45 and 0.06 <= h <= 0.30:
        return "dialog"
    # Bottom working area is usually a compose/reply field; the rest is main content.
    if cy >= 0.70:
        return "compose"
    return "main"


# ---- render ---------------------------------------------------------------------


def _normalize_text(text: str) -> str:
    """Collapse ALL whitespace (incl. newlines/tabs) to single spaces and strip — this is
    what structurally guarantees no ``\n`` reaches ``content.text`` (D-12)."""
    return _WS_RUN.sub(" ", text).strip()


def _norm_key(text: str) -> str:
    """Case-/whitespace-folded key for the dedup similarity compare (step 5)."""
    return _normalize_text(text).lower()


def render(reads, cfg: OcrConfig, span_seconds: float) -> tuple[str, int]:
    """Run D-07 steps 2-6 over the chunk's ``OcrRead``s and return
    ``(single_line_text, n_redactions)``. Empty string when nothing legible survives —
    the caller still emits the record (with ``content.text == ""``), so record PRESENCE,
    not absence, is the coverage signal (§4 R3(e))."""
    # Deterministic: process reads in time order, regions in reading order.
    items: list[tuple[float, str, str]] = []  # (t_offset_s, role, text)
    n_redactions = 0

    for read in sorted(reads, key=lambda r: r.t_offset_s):
        # (1) confidence gate.
        regions = [r for r in read.regions if r.conf >= cfg.min_conf]
        # (2) reading order by bbox top-left (stable — preserves backend order when bboxes
        #     are absent, e.g. the vlm arm's zero bboxes).
        regions = sorted(regions, key=lambda r: (round(r.bbox[1], 4), round(r.bbox[0], 4)))
        for region in regions:
            text = _normalize_text(region.text)
            # (3) minimum length (on the raw legible text, before redaction).
            if len(text) < cfg.min_chars:
                continue
            # (4) secret redaction.
            text, n = redact(text)
            n_redactions += n
            if not text:
                continue
            role = region.role if region.role in ROLES else assign_role(region.bbox)
            items.append((read.t_offset_s, role, text))

    # (5) within-chunk dedup against the previous KEPT line.
    kept: list[tuple[float, str, str]] = []
    for item in items:
        if kept:
            ratio = difflib.SequenceMatcher(
                None, _norm_key(kept[-1][2]), _norm_key(item[2])
            ).ratio()
            if ratio >= cfg.dedup_ratio:
                continue
        kept.append(item)

    # (6) single-line render + budget truncation on a word boundary.
    line = " · ".join(
        f"+{int(round(t))}s {role}: {text}" for t, role, text in kept
    )
    line = _normalize_text(line)  # belt-and-braces: no newline can survive
    line = truncate_word(line, ocr_cap(span_seconds, cfg))
    return line, n_redactions
