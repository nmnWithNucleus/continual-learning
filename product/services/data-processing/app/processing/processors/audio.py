"""Audio plugin: an explicit staged pipeline over one chunk.

    asr -> diarize -> translate -> acoustic_events

Stage 1 (asr) is REAL and is exactly the original M0 path: select the backend
(mock default / faster_whisper), transcribe, and map each chunk-relative segment
offset to an absolute RFC3339 time clamped into the chunk span.
``pipeline_version`` delegates to the selected backend, so the mock/faster_whisper
distinction (and its record_id fork) is preserved exactly.

Stages 2-4 are the REAL audio pipeline beyond ASR (CHARTER OQ11/OQ12), each behind a
backend switch that DEFAULTS OFF (``app/audio/config.py``) — so with no new env set the
output is byte-identical to the pre-fill processor and the M0/seam test baseline stays
green:

  * diarize          — ``DIARIZE_BACKEND=off|mock|pyannote``. When active, fills
                       ``segments[].speaker`` (max-overlap with the diarizer's turns) and
                       ``enrichments.speakers``. It MUTATES the primary record, so it
                       version-forks it: ``pipeline_version`` gains ``+diar-<backend>-v1``
                       (``diarize.version_tag``). Off → no-op, no fork.
  * translate        — ``TRANSLATE_BACKEND=off|mock|whisper`` + ``TRANSLATE_TARGET``. When
                       the target differs from the detected language, APPENDS a
                       ``discriminator="translation"`` unit (a separate record beside the
                       original, never a mutation). whisper = ``task="translate"`` (→English).
  * acoustic_events  — ``ACOUSTIC_BACKEND=off|mock|ast``. APPENDS a
                       ``discriminator="acoustic"`` ``caption`` unit for the chunk's
                       non-speech audio — captioned, not dropped.

Translation + acoustic are ADDITIVE sidecar records: they don't touch the primary and
don't tag ``pipeline_version`` (they're identified by their discriminator). All three
sidecars share the chunk's single ``pipeline_version``, so when diarization is also active
its tag forks the whole chunk's records together — intended (one run, one dialect).

The stages hand an ``AudioPipelineState`` carrier down the line; final assembly emits the
transcript unit first, then whatever extra units later stages appended. Real backends
(pyannote / whisper / ast) are LAZY-IMPORTED only when selected, so the mock/off path (and
the registry's import-on-``/ingest``) never pulls torch/pyannote/transformers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from ...asr import select as select_asr
from ...asr.result import AsrResult, AsrSegment
from ...audio import acoustic, diarize, translate
from ...audio.config import get_audio_config
from ...audio.diarize.assign import assign_speakers
from ...config import Settings
from ...timeutil import abs_time, parse_rfc3339
from ..base import ProcessedContent, ProcessedUnit, Processor, empty_enrichments
from ..registry import register


def _absolute_segments(
    base: datetime, span_seconds: float, segments: list[AsrSegment]
) -> list[dict[str, Any]]:
    """Lift chunk-relative ASR/translation segments to absolute-time C2 segment dicts.

    Each offset is clamped into ``[0, span_seconds]`` and mapped to ``base + offset``.
    ``speaker`` is ``None`` here (the diarize stage fills the transcript's in place; a
    translation's segments stay null). Shared by the asr + translate stages so both speak
    exactly the 4-key C2 segment shape ``{t_start, t_end, text, speaker}``."""
    out: list[dict[str, Any]] = []
    for seg in segments:
        start = min(max(seg.start_s, 0.0), span_seconds)
        end = min(max(seg.end_s, start), span_seconds)
        out.append(
            {
                "t_start": abs_time(base, start),
                "t_end": abs_time(base, end),
                "text": seg.text,
                "speaker": None,
            }
        )
    return out


@dataclass
class AudioPipelineState:
    """State carried through the audio stages for ONE chunk.

    ``segments`` are already-absolute C2 segment dicts (the asr stage owns the
    offset->absolute mapping, and the diarize stage annotates them in place);
    ``extra_units`` collects whole additional records appended by later stages
    (translation, acoustic captions), each distinguished by its discriminator.
    """

    c1: dict[str, Any]
    blob: bytes
    settings: Settings
    span_seconds: float
    asr: Optional[AsrResult] = None                          # set by the asr stage
    segments: list[dict[str, Any]] = field(default_factory=list)
    enrichments: dict[str, list] = field(default_factory=empty_enrichments)
    extra_units: list[ProcessedUnit] = field(default_factory=list)


@register
class AudioProcessor(Processor):
    modality = "audio"
    content_kind = "transcript"

    def pipeline_version(self, settings: Settings) -> str:
        # Base ASR dialect + the diarization tag (empty unless diarization is active).
        # Diarization mutates the primary record, so activating it version-forks it; the
        # tag and the _diarize stage's behavior both derive from ``diarize._resolve``, so
        # they can never disagree (a disagreement would collide record_ids). Translation
        # / acoustic are additive sidecars → no version tag.
        base = select_asr(settings).PIPELINE_VERSION
        return base + diarize.version_tag(get_audio_config())

    def process(
        self,
        c1: dict[str, Any],
        blob: bytes,
        settings: Settings,
        span_seconds: float,
    ) -> list[ProcessedUnit]:
        state = AudioPipelineState(
            c1=c1, blob=blob, settings=settings, span_seconds=span_seconds
        )
        for stage in (self._asr, self._diarize, self._translate, self._acoustic_events):
            stage(state)

        content = ProcessedContent(
            kind="transcript",
            text=state.asr.text,
            language=state.asr.language or None,
            segments=state.segments or None,
        )
        transcript = ProcessedUnit(
            content=content,
            enrichments=state.enrichments,
            discriminator="",  # the chunk's primary record; extras carry their own
        )
        return [transcript, *state.extra_units]

    # ---- Stage 1: ASR (real) --------------------------------------------------

    def _asr(self, state: AudioPipelineState) -> None:
        """Transcribe via the selected backend and map each chunk-relative segment
        offset to an absolute RFC3339 time clamped into the chunk span."""
        state.asr = select_asr(state.settings).transcribe(
            state.settings,
            state.blob,
            state.c1["codec"],
            state.span_seconds,
            state.c1["chunk_id"],
        )
        base = parse_rfc3339(state.c1["t_start"])
        state.segments = _absolute_segments(base, state.span_seconds, state.asr.segments)

    # ---- Stage 2: diarization -------------------------------------------------

    def _diarize(self, state: AudioPipelineState) -> None:
        """Identify who spoke when and fill ``segments[].speaker`` + ``enrichments.speakers``.

        Off (default) → no-op: speakers stay ``None`` and enrichments stay empty, so the
        output is byte-identical to the pre-diarization dialect. When a backend is
        selected, each ASR segment gets its max-overlap turn's speaker and
        ``enrichments.speakers`` is aggregated — and ``pipeline_version`` carries the
        matching ``+diar-*`` fork (see ``pipeline_version``)."""
        cfg = get_audio_config()
        backend = diarize.select(cfg)
        if backend is None:
            return
        result = backend.diarize(
            state.blob, state.c1["codec"], state.span_seconds, cfg
        )
        state.enrichments["speakers"] = assign_speakers(
            state.asr.segments, state.segments, result
        )

    # ---- Stage 3: translation -------------------------------------------------

    def _translate(self, state: AudioPipelineState) -> None:
        """When a target language differs from the detected one, append a
        ``discriminator="translation"`` unit carrying the translated transcript — a stable,
        distinct record beside the original, never a mutation of it.

        Off (default) or nothing to translate (empty transcript / already in the target)
        → no-op."""
        cfg = get_audio_config()
        backend = translate.select(cfg)  # None when off (incl. whisper + non-'en' degrade)
        if backend is None:
            return
        if not state.asr.text.strip():  # silence / all-ambient → nothing to translate
            return
        target = cfg.translate_target
        detected = (state.asr.language or "").strip().lower()
        if detected and detected == target:  # already in the target language
            return
        result = backend.translate(
            state.settings, state.blob, state.span_seconds, state.asr, target
        )
        if not result.text.strip():
            return
        base = parse_rfc3339(state.c1["t_start"])
        segments = _absolute_segments(base, state.span_seconds, result.segments)
        content = ProcessedContent(
            kind="transcript",
            text=result.text,
            language=result.language or None,
            segments=segments or None,
        )
        state.extra_units.append(
            ProcessedUnit(
                content=content,
                enrichments=empty_enrichments(),  # own empty block; NOT the diarized one
                discriminator="translation",
            )
        )

    # ---- Stage 4: acoustic events ---------------------------------------------

    def _acoustic_events(self, state: AudioPipelineState) -> None:
        """Caption salient non-speech audio (doors, sirens, music) as a
        ``discriminator="acoustic"`` caption unit — captioned, not dropped, so an
        all-ambient chunk still yields a searchable record beside its (empty) transcript.

        Off (default) → no-op."""
        cfg = get_audio_config()
        backend = acoustic.select(cfg)
        if backend is None:
            return
        result = backend.caption(
            state.blob, state.c1["codec"], state.span_seconds, cfg, state.c1["chunk_id"]
        )
        if result is None or not result.text.strip():
            return
        content = ProcessedContent(kind="caption", text=result.text)
        state.extra_units.append(
            ProcessedUnit(
                content=content,
                enrichments=empty_enrichments(),  # own empty block; NOT the diarized one
                discriminator="acoustic",
            )
        )
