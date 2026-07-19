"""Turn (label, score) audio-event tags into one short, deterministic caption.

Shared by every acoustic backend (mock + ast) so the caption dialect is identical
whatever produced the tags. The rules (from the design review):

  * DROP speech-family AudioSet labels — speech is the transcript record's job, not the
    acoustic caption's;
  * keep tags at/above ``threshold``, cap at ``top_k``;
  * lowercase + join naturally ("a." / "a and b." / "a, b, and c.");
  * if NOTHING survives, emit a stable fallback ("Ambient background noise.") — the
    stub's contract is that an all-ambient chunk still yields a searchable caption record
    beside its (empty) transcript, so we never return an empty caption.

Deterministic: a stable sort by (-score, label) means idempotent reprocessing upserts the
same record. (Sibling-collapse of near-duplicate labels — "cutlery" + "dishes" → "dishes
clinking" — is a documented future refinement, intentionally omitted for a lean v0.)
"""
from __future__ import annotations

FALLBACK = "Ambient background noise."

# AudioSet speech-family labels the acoustic caption must not claim (the transcript owns
# speech). Lowercased for a case-insensitive match.
_SPEECH_LABELS = {
    "speech",
    "male speech, man speaking",
    "female speech, woman speaking",
    "child speech, kid speaking",
    "conversation",
    "narration, monologue",
    "speech synthesizer",
    "silence",
}


def _is_speech(label: str) -> bool:
    return label.strip().lower() in _SPEECH_LABELS


def _join(labels: list[str]) -> str:
    if len(labels) == 1:
        return f"{labels[0]}."
    if len(labels) == 2:
        return f"{labels[0]} and {labels[1]}."
    return ", ".join(labels[:-1]) + f", and {labels[-1]}."


def caption_from_tags(
    tags: list[tuple[str, float]],
    top_k: int,
    threshold: float,
) -> str:
    """One-sentence caption from event tags (or the fallback when none survive)."""
    ranked = sorted(
        (t for t in tags if not _is_speech(t[0]) and t[1] >= threshold),
        key=lambda t: (-t[1], t[0]),
    )
    kept = [label.strip().lower() for label, _score in ranked[: max(1, top_k)]]
    if not kept:
        return FALLBACK
    caption = _join(kept)
    return caption[:1].upper() + caption[1:]
