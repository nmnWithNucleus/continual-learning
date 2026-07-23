"""Rehearsal sampling — what stops night N from erasing night N-1.

Sequential consolidation into one adapter collapses without it: each night's
gradient overwrites the last. Mixing a slice of PRIOR days' real text into
tonight's corpus is the whole fix, and it is a dial rather than a threshold —
recall rises monotonically through a 30% mix with no measurable cost to the new
day, because a 48x-amplified corpus is redundant enough that 70% of it still
writes the day fully.

Two things this deliberately is NOT:

  * generative ("dreaming" — rehearsing the model's own recollections) measurably
    loses to real text; the reservoir of real past prose is required forever.
  * forgetting-weighted (the `smart` arm) ties with uniform at three seeds, so
    uniform wins on simplicity. Not ported.

Source (amplified corpora vs raw day-logs) is the CALLER's choice — the two tie
empirically, and passing texts in keeps this kernel ignorant of where a day's
prose is stored, which is what lets 2c move it behind a storage client.
"""
from __future__ import annotations

import random
from typing import Callable, Sequence

# Below this a "paragraph" is a fragment — a heading, a stray line — and rehearsing
# it teaches nothing.
MIN_PARAGRAPH_CHARS = 100


def paragraphs(sources: Sequence[str]) -> list[str]:
    """Pool the sources into rehearsable paragraphs, in source order.

    Pooling (rather than sampling per-day and concatenating) is what makes the
    mix uniform over TEXT rather than over DAYS: a day with more material
    contributes proportionally more, which is the behavior that was measured."""
    return [p for text in sources for p in text.split("\n\n")
            if len(p) > MIN_PARAGRAPH_CHARS]


def _take(pool: Sequence[str], budget: float) -> list[str]:
    """Greedily fill a character budget. The budget check happens BEFORE the
    append, so the last paragraph overshoots rather than being truncated —
    rehearsing half a paragraph would teach a cut-off fact."""
    picked: list[str] = []
    used = 0
    for para in pool:
        if used >= budget:
            break
        picked.append(para)
        used += len(para)
    return picked


def sample_replay(sources: Sequence[str], *, frac: float, target_chars: int,
                  rng: random.Random, neg_boost: float = 0.0,
                  is_calibration: Callable[[str], bool] | None = None) -> str:
    """Draw ~`frac * target_chars` of prior-day prose to mix into tonight.

    `neg_boost` reserves that fraction of the rehearsal budget for deny-then-correct
    paragraphs specifically — recognized by `is_calibration`, which the PROFILE
    supplies because only the profile knows what phrasing its negative style
    mandates. Trap calibration erodes over a long horizon and re-exposing refusal
    prose is the obvious lever, but it is a SHARP one: at 40% the adapter learns
    to deny everything (recall collapses to 0.021 while traps pass), which is a
    lobotomy rather than calibration. It stays a <=10% tunable, default off;
    horizon erosion is handled at the publish gate instead.
    """
    if frac <= 0 or not sources:
        return ""
    if neg_boost > 0:
        if is_calibration is None:
            raise ValueError("neg_boost needs the profile's `is_calibration` predicate — "
                             "without it the boost would resample uniformly and do nothing")
        negatives = [p for p in paragraphs(sources) if is_calibration(p)]
        rng.shuffle(negatives)
        boosted = _take(negatives, neg_boost * frac * target_chars)
        rest = sample_replay(sources, frac=frac * (1 - neg_boost),
                             target_chars=target_chars, rng=rng)
        return "\n\n".join(boosted) + "\n\n" + rest
    pool = paragraphs(sources)
    if not pool:
        return ""
    rng.shuffle(pool)
    return "\n\n".join(_take(pool, frac * target_chars))
