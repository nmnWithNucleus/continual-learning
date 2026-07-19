"""Real acoustic-event captioning — HF AST AudioSet tagger (ACOUSTIC_BACKEND=ast).

Model: ``MIT/ast-finetuned-audioset-10-10-0.4593`` via the transformers
``audio-classification`` pipeline. Chosen over PANNs/YAMNet for a v0 seam because AST is
NATIVELY 16 kHz mono (no 32 kHz resample trap), fully lazy-importable (transformers+torch
only on this path), GPU-optional, and ships the 527 AudioSet ``id2label`` map — so top-k
event labels come for free. The 527-way top-k tags are folded into one caption by the
SHARED ``caption.caption_from_tags`` (same dialect as the mock).

Decoding: we hand the transformers pipeline the RAW chunk bytes. Its ``ffmpeg_read`` path
shells out to ffmpeg, so it demuxes whatever the capture surfaces emit — ``audio/webm``
(opus, the extension), ``audio/mp4`` (aac, iOS web), ``audio/wav`` — and resamples to the
model's 16 kHz, exactly like the ASR (av) and pyannote (torchaudio) paths. So ffmpeg must
be on PATH (already a requirements-audio.txt system dep). We deliberately do NOT use
soundfile, which can't demux webm/mp4.

⚠️  UNVERIFIED ON REAL AUDIO IN THIS ENVIRONMENT. transformers/torch aren't installed here
and the ~350 MB model isn't downloaded, so this path has NOT been run — it is
correct-by-inspection only. Smoke-test on node-7 (a real captured chunk +
``ACOUSTIC_BACKEND=ast``) before trusting a run; never report a real run that didn't
happen. The mock backend is the exercised, headless path.

The acoustic record is identified by ``discriminator="acoustic"``, so this backend NEVER
tags the audio ``pipeline_version`` (only diarization, which mutates the primary, does).
"""
from __future__ import annotations

from ..config import AudioConfig
from .caption import caption_from_tags
from .result import AcousticResult

_MODEL_ID = "MIT/ast-finetuned-audioset-10-10-0.4593"
# Cache one classifier per device (loading the ~350 MB model is expensive).
_PIPE_CACHE: dict[str, object] = {}


def _get_classifier(device: str):
    pipe = _PIPE_CACHE.get(device)
    if pipe is None:
        import torch  # noqa: F401  (lazy; only pulled on this path)
        from transformers import pipeline  # lazy

        pipe = pipeline(
            task="audio-classification",
            model=_MODEL_ID,
            device=0 if device == "cuda" else -1,
            top_k=20,
        )
        _PIPE_CACHE[device] = pipe
    return pipe


def caption(
    audio_bytes: bytes,
    codec: str,
    span_seconds: float,
    cfg: AudioConfig,
    chunk_id: str,
) -> AcousticResult:
    import torch  # lazy

    device = "cuda" if torch.cuda.is_available() else "cpu"
    classifier = _get_classifier(device)

    # Raw bytes -> transformers ffmpeg_read (decode + resample to the model's 16 kHz),
    # so any captured container (webm/opus, mp4/aac, wav) is handled uniformly.
    preds = classifier(audio_bytes)
    tags = [(p["label"], float(p["score"])) for p in preds]
    text = caption_from_tags(tags, cfg.acoustic_top_k, cfg.acoustic_threshold)
    return AcousticResult(text=text)
