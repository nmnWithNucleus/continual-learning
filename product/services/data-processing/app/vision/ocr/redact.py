"""Deterministic secret redaction for OCR strings (step 4).

A prompt rule ("never transcribe a password") is not an access control — a CTC engine
has no notion of a rule. This is the access control: a pure, regex-driven pass that
replaces credential-shaped runs with the fixed token ``[redacted:secret]`` BEFORE the
text is injected into the caption prompt or written to ``/context``. It runs entirely
DP-side on the returned strings, so it holds identically for every backend (ppocr, vlm,
even a future model that ignores its instructions).

Deterministic by construction: same input string -> same output + same count, on any
worker in the fleet (§4 R4 / house rule 6). Every replacement is counted so the operator
sees ``dp_ocr_redactions_total`` climb — a redaction that fires a lot is a signal, not a
silent scrub.

The six shapes the OCR exit criteria pin, plus two the design names in :
AWS access-key id · ``sk-`` / ``ghp_`` (& sibling GitHub/OpenAI tokens) · ``xox[baprs]-``
Slack tokens · >=32-char base64 runs · PEM ``-----BEGIN … KEY-----`` headers ·
Luhn-valid 13-19 digit card runs · all-bullet/asterisk masked fields.
"""
from __future__ import annotations

import re

REDACTED = "[redacted:secret]"

# NOTE ON ORDER: patterns are applied in this list order. Specific credential shapes
# (AWS/OpenAI/GitHub/Slack/PEM/card) run BEFORE the generic base64 run so a token is
# labelled by its specific matcher first and a single [redacted:secret] replaces it,
# rather than the greedy base64 rule fragmenting it.
_PATTERNS: tuple[re.Pattern, ...] = (
    # AWS access key id: AKIA/ASIA/AGPA/… + 16 uppercase-alnum.
    re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|A3T[A-Z0-9])[A-Z0-9]{16}\b"),
    # OpenAI-style secret keys: sk-… / sk-proj-… (>=16 tail chars).
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{16,}\b"),
    # GitHub tokens: ghp_ / gho_ / ghu_ / ghs_ / ghr_ + >=20 alnum.
    re.compile(r"\bgh[porsu]_[A-Za-z0-9]{20,}\b"),
    # Slack tokens: xoxb-/xoxa-/xoxp-/xoxr-/xoxs-…
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    # PEM private-key (or any BEGIN) header line.
    re.compile(r"-----BEGIN [A-Z0-9 ]+-----"),
    # >=32-char base64 run (keys, blobs) — generic, applied after the specific shapes.
    re.compile(r"\b[A-Za-z0-9+/]{32,}={0,2}\b"),
    # Bullet-masked field (a rendered password) — bullets are unambiguous mask glyphs.
    re.compile(r"[•●]{3,}"),
    # A STANDALONE run of >=6 asterisks (a masked field), not adjacent to any other glyph —
    # so markdown emphasis (``***bold***``), ``***`` / ``****`` rules and ``/*****/`` comment
    # banners are NOT over-redacted (the false-positive class a bare ``[*]{3,}`` would hit).
    re.compile(r"(?<!\S)\*{6,}(?!\S)"),
)

# Candidate digit run for a payment-card Luhn check: 13-19 digits, optionally spaced or
# hyphen-grouped. Verified with the checksum before redacting so ordinary long numbers
# (a 16-digit build id that fails Luhn) are left alone.
_CARD_CANDIDATE = re.compile(r"\b\d(?:[ -]?\d){12,18}\b")


def _luhn_ok(digits: str) -> bool:
    """Standard Luhn checksum over a bare digit string (13-19 digits)."""
    if not 13 <= len(digits) <= 19:
        return False
    total, alt = 0, False
    for ch in reversed(digits):
        d = ord(ch) - 48
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def redact(text: str) -> tuple[str, int]:
    """Return ``(redacted_text, n_replacements)``. Pure and deterministic.

 Regex-shaped secrets first, then the Luhn-verified card pass. ``n`` is the number of
 distinct spans replaced (the caller folds it into ``dp_ocr_redactions_total``)."""
    count = 0

    def _sub(pattern: re.Pattern, s: str) -> str:
        nonlocal count
        n = 0

        def _repl(_m: re.Match) -> str:
            nonlocal n
            n += 1
            return REDACTED

        out = pattern.sub(_repl, s)
        count += n
        return out

    for pattern in _PATTERNS:
        text = _sub(pattern, text)

    # Luhn-gated card pass: only replace runs that actually checksum as a card.
    def _card_repl(m: re.Match) -> str:
        nonlocal count
        digits = re.sub(r"[ -]", "", m.group(0))
        if _luhn_ok(digits):
            count += 1
            return REDACTED
        return m.group(0)

    text = _CARD_CANDIDATE.sub(_card_repl, text)
    return text, count
