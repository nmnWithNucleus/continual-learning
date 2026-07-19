"""Real translation — faster-whisper ``task="translate"`` (TRANSLATE_BACKEND=whisper).

LAZY-IMPORTED via the switch, and it REUSES the ASR ``WhisperModel`` (same model cache in
``app/asr/faster_whisper.py``) — no second model is loaded. Whisper's translate task is
X→English speech translation ONLY, so this backend supports ``TRANSLATE_TARGET=en`` (the
switch enforces that before selecting this module).

⚠️  UNVERIFIED ON REAL AUDIO IN THIS ENVIRONMENT (no model download / GPU here). The call
mirrors the exercised ASR path exactly (same ``io.BytesIO`` blob, same VAD/beam knobs,
only ``task="translate"`` added), so it is correct-by-inspection; smoke-test on node-7
before trusting a run. The mock backend is the headless-exercised path.

Design-review points baked in:
  * OUTPUT language is ALWAYS English → ``language="en"`` (hardcoded, NOT ``info.language``,
    which is the detected SOURCE language);
  * SOURCE hint is ``None`` (auto-detect) — translation implies a non-English source, so
    we do NOT inherit the beta ``ASR_LANGUAGE=en`` pin as the source hint;
  * segments come back CHUNK-RELATIVE, identical to ASR, so the stage lifts them to
    absolute RFC3339 with the same helper;
  * an empty (VAD-gated) result yields no text → the stage emits no translation record.
"""
from __future__ import annotations

import io

from ...asr.faster_whisper import load_model
from ...asr.result import AsrSegment
from ...config import Settings
from .result import TranslationResult


def translate(
    settings: Settings,
    audio_bytes: bytes,
    span_seconds: float,
    asr_result,
    target: str,
) -> TranslationResult:
    model = load_model(settings)  # reuse the cached ASR WhisperModel

    segment_iter, _info = model.transcribe(
        io.BytesIO(audio_bytes),
        task="translate",                 # X -> English (the only Whisper translate task)
        beam_size=settings.asr_beam_size,
        language=None,                    # auto-detect the SOURCE (not the ASR_LANGUAGE pin)
        vad_filter=settings.asr_vad,
        vad_parameters={"min_silence_duration_ms": 500},
    )

    segments: list[AsrSegment] = []
    parts: list[str] = []
    for seg in segment_iter:
        seg_text = (seg.text or "").strip()
        segments.append(AsrSegment(float(seg.start), float(seg.end), seg_text))
        if seg_text:
            parts.append(seg_text)

    return TranslationResult(
        text=" ".join(parts).strip(),
        language="en",                    # task=translate output is always English
        segments=segments,
    )
