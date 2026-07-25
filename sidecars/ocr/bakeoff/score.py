#!/usr/bin/env python3
# =============================================================================
# score.py -- the O-2 scoring metrics, isolated and unit-testable.
#
#   Two metrics, exactly per O-2:
#     * key-string RECALL of >=5-char strings, with LENIENT substring matching
#       (NOT exact equality). The POC measured 0.000 strict on a model that was
#       reading correctly but wrapping the answer in prose / substituting a
#       glyph -- so leniency is the whole point. Two leniency tiers are reported:
#         - substring : normalized key is a substring of the normalized OCR text
#                       (case-folded, whitespace-removed, full-width punctuation
#                        folded to ASCII -- an engine artifact, not a mis-read)
#         - fuzzy     : best windowed edit-similarity >= 0.85 (tolerates a couple
#                       of scattered OCR substitutions in a long string)
#       `fuzzy` implies `substring` (it is strictly more permissive).
#     * CER (character error rate) over the FOCUSED region only.
#
#   Pure functions of (ground truth, OCR regions). No I/O, no globals.
# =============================================================================
from __future__ import annotations

# Full-width / smart punctuation an OCR engine emits for ASCII equivalents.
# Folding these is a fair leniency: the reader got the character right, the
# codepoint is a rendering artifact.
_PUNCT_FOLD = {
    "（": "(", "）": ")", "：": ":", "，": ",", "．": ".",
    "；": ";", "！": "!", "？": "?", "％": "%", "＃": "#",
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "−": "-", " ": " ",
}


def _fold(s: str) -> str:
    return "".join(_PUNCT_FOLD.get(ch, ch) for ch in s)


def norm_tight(s: str) -> str:
    """Case-fold, punctuation-fold, remove ALL whitespace. For substring recall."""
    return "".join(_fold(s).casefold().split())


def norm_soft(s: str) -> str:
    """Case-fold, punctuation-fold, collapse whitespace to single spaces. For CER."""
    return " ".join(_fold(s).casefold().split())


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def best_window_similarity(needle: str, hay: str) -> float:
    """Max over sliding windows of `1 - editdist/len(needle)`. Windows of length
    len(needle) and +/-2 to absorb insert/delete drift. 0..1."""
    n = len(needle)
    if n == 0:
        return 1.0
    if not hay:
        return 0.0
    best = 0.0
    for wlen in (n, n - 2, n + 2):
        if wlen <= 0 or wlen > len(hay):
            if wlen > len(hay):
                # whole hay is the only window
                d = levenshtein(needle, hay)
                best = max(best, 1.0 - d / n)
            continue
        for i in range(0, len(hay) - wlen + 1):
            d = levenshtein(needle, hay[i : i + wlen])
            sim = 1.0 - d / n
            if sim > best:
                best = sim
                if best >= 0.999:
                    return best
    return best


def score_recall(key_strings: list[str], ocr_text: str, fuzzy_thresh: float = 0.85) -> dict:
    """Recall of >=5-char key strings against the full OCR text of one frame."""
    keys = [k for k in key_strings if len(k.strip()) >= 5]
    if not keys:
        return {"n_keys": 0, "substring": None, "fuzzy": None, "misses": []}
    tight = norm_tight(ocr_text)
    hit_sub = 0
    hit_fuzzy = 0
    misses = []
    for k in keys:
        nk = norm_tight(k)
        sub = nk in tight
        if sub:
            hit_sub += 1
            hit_fuzzy += 1
            continue
        sim = best_window_similarity(nk, tight)
        if sim >= fuzzy_thresh:
            hit_fuzzy += 1
        else:
            misses.append(k)
    return {
        "n_keys": len(keys),
        "substring": hit_sub / len(keys),
        "fuzzy": hit_fuzzy / len(keys),
        "misses": misses,
    }


def regions_in_box(regions: list[dict], box: list[float]) -> list[dict]:
    x0, y0, x1, y1 = box
    out = []
    for r in regions:
        bx0, by0, bx1, by1 = r["bbox"]
        cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            out.append(r)
    return out


def reading_order_text(regions: list[dict], row_tol: float = 20.0) -> str:
    """Concatenate region texts in reading order (top->bottom, left->right)."""
    rows = sorted(regions, key=lambda r: (round(r["bbox"][1] / row_tol), r["bbox"][0]))
    return " ".join(r["text"] for r in rows)


def score_cer(focus_text: str, regions: list[dict], focus_box: list[float]) -> float | None:
    """CER over the focused region: reference vs the OCR regions inside the box."""
    ref = norm_soft(focus_text)
    if not ref:
        return None
    hyp = norm_soft(reading_order_text(regions_in_box(regions, focus_box)))
    return levenshtein(ref, hyp) / len(ref)
