"""OCR post-processing → one self-anchored single line.

Given the raw ``OcrRead``s the OCR server returned for a chunk, produce the single
``str`` that ``screentext`` commits as its ``ocr`` slot value — the same string the
caption injects: ONE rendered witness on two channels (the L11 provenance
corollary), never two independent claims.

The post-processing order, and it is an order rather than a set (event selection — the floor grid ∪
change events, capped — is ``clipprep``'s delta gate; here we run everything AFTER the read):

  1. drop boxes with ``confidence < min_conf``;
  2. sort into reading order by bbox and assign a REGION ROLE from bbox position — the
     semantically useful 80% of "location" as a word, at zero contract cost; the pixel
     geometry is then DISCARDED (never emitted to C2);
  3. drop lines shorter than ``min_chars``;
  4. deterministic secret redaction (``redact.py``) — ALWAYS on (an access control, not
     a knob; the v0 ``VIDEO_PRIVACY_FILTER`` env flag was never read and is dead);
  5. drop a line >= ``dedup_ratio`` similar to the previous kept line, WITHIN this chunk
     (cross-chunk state is forbidden — it would break fleet determinism, L1);
  6. render to ONE line (no ``\n`` ever; separator ``" · "``), truncated at the
     chars-per-second-of-life budget on a WORD boundary.

NO CONFIG (L4): the thresholds arrive as EXPLICIT keyword arguments; the one
live set of values is pinned in ``app/stages/video/screentext.py`` under that stage's
backend version. Pure functions of their inputs — identical reads + pins → identical
string on every worker.

Self-anchored (the L11 corollary): every kept item carries its own ``+Ns`` offset and
role inside the text, so the record is understandable alone.
"""
from __future__ import annotations

import difflib
import re

from ..budget import ocr_cap, truncate_word
from ..clip_types import OcrRead
from .redact import redact

# The frozen role vocabulary (see ``clip_types.OcrRegion.role``).
ROLES = (
    "titlebar", "tab", "sidebar", "main", "compose",
    "message", "toolbar", "statusbar", "dialog", "notification",
)

_WS_RUN = re.compile(r"\s+")


# ---- region role from bbox ------------------------------------------------------


def _clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v


def assign_role(bbox: tuple[float, float, float, float]) -> str:
    """Assign a coarse region role from a NORMALIZED (0..1) bbox ``(x0,y0,x1,y1)``.

    A position heuristic over the box centroid — model-/engine-asserted, "good enough" to
    be the semantic 80% of location, not a substitute for real geometry (which is
    geometry, which is never emitted). Deterministic; returns one of
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
    what structurally guarantees no ``\n`` reaches ``content.text``."""
    return _WS_RUN.sub(" ", text).strip()


def _norm_key(text: str) -> str:
    """Case-/whitespace-folded key for the dedup similarity compare (step 5)."""
    return _normalize_text(text).lower()


def render(
    reads,
    span_seconds: float,
    *,
    min_conf: float,
    min_chars: int,
    dedup_ratio: float,
    chars_per_second: float,
) -> tuple[str, int, bool]:
    """Run steps 2-6 of the order above over the chunk's ``OcrRead``s and return
    ``(single_line_text, n_redactions, truncated)``. Empty string when nothing
    legible survives — the caller still emits its slot (with ``value == ""``), so
    slot PRESENCE, not absence, is the coverage signal (L11: the honest empty
    claim). ``truncated`` = the budget cap actually cut the line."""
    # Deterministic: process reads in time order, regions in reading order.
    items: list[tuple[float, str, str]] = []  # (t_offset_s, role, text)
    n_redactions = 0

    for read in sorted(reads, key=lambda r: r.t_offset_s):
        # (1) confidence gate.
        regions = [r for r in read.regions if r.conf >= min_conf]
        # (2) reading order by bbox top-left (stable — preserves backend order when
        #     bboxes are absent/zero).
        regions = sorted(regions, key=lambda r: (round(r.bbox[1], 4), round(r.bbox[0], 4)))
        for region in regions:
            text = _normalize_text(region.text)
            # (3) minimum length (on the raw legible text, before redaction).
            if len(text) < min_chars:
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
            if ratio >= dedup_ratio:
                continue
        kept.append(item)

    # (6) single-line render + budget truncation on a word boundary. The third
    # return says whether the cap actually truncated (the
    # dp_video_truncated_total{pass="ocr"} signal — cleanup round).
    full = " · ".join(
        f"+{int(round(t))}s {role}: {text}" for t, role, text in kept
    )
    full = _normalize_text(full)  # belt-and-braces: no newline can survive
    line = truncate_word(full, ocr_cap(span_seconds, chars_per_second))
    return line, n_redactions, line != full
