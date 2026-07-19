"""Map ASR segments onto diarization turns — the backend-agnostic half of diarize.

A diarize backend produces speaker *turns* (who spoke when, chunk-relative). This module
does the SHAPE both the mock and pyannote paths share:

  * assign each ASR segment the speaker of its MAX-temporal-overlap turn (tie-break: the
    lexicographically smallest label, for determinism; no overlap -> ``None``);
  * write that label onto the parallel absolute-time C2 segment dict (by index — the ASR
    stage appends exactly one C2 segment per ASR segment, in order);
  * aggregate ``enrichments.speakers``: one entry per distinct assigned speaker with its
    total assigned speech seconds + segment count, sorted by label (deterministic).

Both are pure functions of the inputs, so the diarized record stays a deterministic
function of (C1, bytes, pipeline_version) — the C2 idempotency requirement.
"""
from __future__ import annotations

from typing import Any, Optional

from ...asr.result import AsrSegment
from .result import DiarizationResult, SpeakerTurn


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    """Length of the temporal overlap of [a0,a1] and [b0,b1] (0 if disjoint)."""
    return max(0.0, min(a1, b1) - max(a0, b0))


def _speaker_for(seg: AsrSegment, turns: list[SpeakerTurn]) -> Optional[str]:
    """The max-overlap turn's speaker for one ASR segment (lowest label breaks ties;
    ``None`` when no turn overlaps at all)."""
    best_overlap = 0.0
    best_speaker: Optional[str] = None
    for turn in turns:
        ov = _overlap(seg.start_s, seg.end_s, turn.start_s, turn.end_s)
        if ov <= 0.0:
            continue
        if (
            best_speaker is None
            or ov > best_overlap
            or (ov == best_overlap and turn.speaker < best_speaker)
        ):
            best_overlap = ov
            best_speaker = turn.speaker
    return best_speaker


def assign_speakers(
    asr_segments: list[AsrSegment],
    out_segments: list[dict[str, Any]],
    diarization: DiarizationResult,
) -> list[dict[str, Any]]:
    """Fill ``out_segments[i]["speaker"]`` from ``diarization`` and return the
    ``enrichments.speakers`` list.

    ``asr_segments`` and ``out_segments`` are parallel (same length + order): the ASR
    stage builds one absolute C2 segment per chunk-relative ASR segment. When there are
    no turns (or no ASR segments — e.g. an all-silence chunk), speakers stay ``None`` and
    the returned list is empty. Overlap works directly in the segments' own chunk-relative
    seconds, so no span is needed (clamping already lives in the asr stage + the backends).
    """
    turns = diarization.turns
    # speaker -> [total_speech_s, segment_count]; insertion doesn't matter (sorted below).
    agg: dict[str, list[float]] = {}

    for seg, out in zip(asr_segments, out_segments):
        speaker = _speaker_for(seg, turns) if turns else None
        out["speaker"] = speaker
        if speaker is not None:
            bucket = agg.setdefault(speaker, [0.0, 0.0])
            bucket[0] += max(0.0, seg.end_s - seg.start_s)
            bucket[1] += 1

    return [
        {
            "speaker": speaker,
            "total_speech_s": round(total, 3),
            "segment_count": int(count),
        }
        for speaker, (total, count) in sorted(agg.items())
    ]
