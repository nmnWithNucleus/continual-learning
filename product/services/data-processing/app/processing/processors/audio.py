"""Audio plugin: ASR -> a single 'transcript' unit.

This is the ORIGINAL audio M0 path, moved behind the Processor seam unchanged:
select the ASR backend (mock default / faster_whisper), transcribe, and map each
chunk-relative ASR segment offset to an absolute RFC3339 time clamped into the
chunk span. ``pipeline_version`` delegates to the selected backend, so the
mock/faster_whisper distinction (and its record_id fork) is preserved exactly.

One audio chunk -> one record (``discriminator=''``). The fuller audio pipeline
(VAD gate, diarization, translation, acoustic-event captioning — CHARTER OQ12) is
later work; when a chunk yields both speech and an ambient-sound caption it will
return TWO units via the discriminator, with no seam change.
"""
from __future__ import annotations

from typing import Any

from ...asr import select as select_asr
from ...config import Settings
from ...timeutil import abs_time, parse_rfc3339
from ..base import ProcessedContent, ProcessedUnit, Processor, empty_enrichments
from ..registry import register


@register
class AudioProcessor(Processor):
    modality = "audio"
    content_kind = "transcript"

    def pipeline_version(self, settings: Settings) -> str:
        return select_asr(settings).PIPELINE_VERSION

    def process(
        self,
        c1: dict[str, Any],
        blob: bytes,
        settings: Settings,
        span_seconds: float,
    ) -> list[ProcessedUnit]:
        backend = select_asr(settings)
        result = backend.transcribe(
            settings, blob, c1["codec"], span_seconds, c1["chunk_id"]
        )

        base = parse_rfc3339(c1["t_start"])
        segments: list[dict[str, Any]] = []
        for seg in result.segments:
            start = min(max(seg.start_s, 0.0), span_seconds)
            end = min(max(seg.end_s, start), span_seconds)
            segments.append(
                {
                    "t_start": abs_time(base, start),
                    "t_end": abs_time(base, end),
                    "text": seg.text,
                    "speaker": None,  # required-nullable; no diarization in v0
                }
            )

        content = ProcessedContent(
            kind="transcript",
            text=result.text,
            language=result.language or None,
            segments=segments or None,
        )
        return [
            ProcessedUnit(
                content=content,
                enrichments=empty_enrichments(),
                discriminator="",  # 1:1 — one chunk, one transcript
            )
        ]
