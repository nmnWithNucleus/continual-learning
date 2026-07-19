"""Real speaker diarization — pyannote.audio 3.1 (DIARIZE_BACKEND=pyannote).

LAZY-IMPORTED: torch + pyannote.audio are imported only inside the functions here, so
importing this module never happens on the mock/off default and the unit tests never need
torch. Selected via ``DIARIZE_BACKEND=pyannote``; needs a GPU (optional but strongly
preferred) and an HF token that has accepted the gated user conditions.

⚠️  UNVERIFIED ON REAL AUDIO IN THIS ENVIRONMENT. The seam is correct-by-inspection
(pyannote 3.1 API, per the design review); it has NOT been run here (no GPU, HF-gated
model). Smoke-test on node-7 before trusting a run — and never report a real run that
did not happen. The mock backend is the exercised, headless path.

Gotchas baked in below (from the design review):
  * ``Pipeline.from_pretrained`` returns ``None`` (does not raise) when the token is
    missing/invalid OR the gated conditions aren't accepted — we check and raise loudly.
  * TWO gated repos must both be accepted with the same account:
    ``pyannote/speaker-diarization-3.1`` AND its dep ``pyannote/segmentation-3.0``.
  * ``use_auth_token=`` on ``from_pretrained``; GPU placement is a separate
    ``pipeline.to(torch.device(...))`` AFTER load (never ``device=`` on from_pretrained).
  * torchaudio decodes the chunk via ffmpeg — give the temp file the codec's extension.
  * ``itertracks(yield_label=True)`` yields ``(Segment, track_id, label)`` — the THIRD
    value is the speaker; ``Segment.start/.end`` are already chunk-relative seconds.
  * pyannote may emit temporally OVERLAPPING turns (overlapped speech) — the downstream
    max-overlap assignment (``assign.py``) handles that; we don't assume disjoint turns.
  * raw ``SPEAKER_xx`` labels are an internal detail — we renormalize to ``spk_0..`` by
    first onset so the vocabulary is stable + version-independent.

The canonical version tag for this backend lives in ``diarize/__init__._TAGS`` — this
module intentionally does not redefine it, so the two can't drift.
"""
from __future__ import annotations

import tempfile

from ..config import AudioConfig
from .result import DiarizationResult, SpeakerTurn

_MODEL_ID = "pyannote/speaker-diarization-3.1"
# Loading the pipeline (segmentation + embedding stack, several hundred MB) is expensive;
# cache per (model_id, device).
_PIPELINE_CACHE: dict[tuple[str, str], object] = {}


def _get_pipeline(cfg: AudioConfig):
    import torch  # lazy
    from pyannote.audio import Pipeline  # lazy

    device = "cuda" if torch.cuda.is_available() else "cpu"
    key = (_MODEL_ID, device)
    pipeline = _PIPELINE_CACHE.get(key)
    if pipeline is None:
        token = cfg.hf_token or None
        pipeline = Pipeline.from_pretrained(_MODEL_ID, use_auth_token=token)
        if pipeline is None:  # from_pretrained signals auth failure by returning None
            raise RuntimeError(
                "pyannote Pipeline.from_pretrained returned None — set a valid HF token "
                "(HF_TOKEN / HUGGINGFACE_TOKEN) and accept the gated user conditions for "
                "BOTH pyannote/speaker-diarization-3.1 and pyannote/segmentation-3.0."
            )
        pipeline.to(torch.device(device))  # in-place; do once, before inference
        _PIPELINE_CACHE[key] = pipeline
    return pipeline


def _speaker_hints(cfg: AudioConfig) -> dict[str, int]:
    """min/max speaker hints for the pipeline call (0 = unset → let pyannote decide)."""
    hints: dict[str, int] = {}
    if cfg.diarize_min_speakers > 0:
        hints["min_speakers"] = cfg.diarize_min_speakers
    if cfg.diarize_max_speakers > 0:
        hints["max_speakers"] = cfg.diarize_max_speakers
    return hints


def _temp_suffix(codec: str) -> str:
    """Extension for the temp file so torchaudio/ffmpeg detects the container."""
    base = (codec or "wav").split(";")[0].split("/")[-1].strip().lower()
    return "." + (base or "wav")


def _normalize(raw_turns: list[tuple[float, float, str]]) -> list[SpeakerTurn]:
    """Map raw pyannote labels to a stable ``spk_0..`` vocabulary by first onset
    (raw label as the deterministic tie-break)."""
    first_onset: dict[str, float] = {}
    for start, _end, raw in raw_turns:
        if raw not in first_onset or start < first_onset[raw]:
            first_onset[raw] = start
    order = sorted(first_onset, key=lambda r: (first_onset[r], r))
    remap = {raw: f"spk_{i}" for i, raw in enumerate(order)}
    return [SpeakerTurn(s, e, remap[raw]) for (s, e, raw) in raw_turns]


def diarize(
    audio_bytes: bytes,
    codec: str,
    span_seconds: float,
    cfg: AudioConfig,
) -> DiarizationResult:
    pipeline = _get_pipeline(cfg)

    with tempfile.NamedTemporaryFile(suffix=_temp_suffix(codec)) as tmp:
        tmp.write(audio_bytes)
        tmp.flush()
        annotation = pipeline(tmp.name, **_speaker_hints(cfg))

    raw_turns: list[tuple[float, float, str]] = []
    for segment, _track, label in annotation.itertracks(yield_label=True):
        start = min(max(float(segment.start), 0.0), span_seconds)
        end = min(max(float(segment.end), start), span_seconds)
        if end > start:
            raw_turns.append((start, end, str(label)))

    raw_turns.sort(key=lambda t: (t[0], t[1]))
    return DiarizationResult(turns=_normalize(raw_turns))
