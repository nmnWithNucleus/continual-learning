"""Synthetic day generator — C2-shaped records for headless dev + tests.

Produces a plausible mixed-modality day (per-keyframe captions, diarized
transcripts, OCR rows) inside a consolidation window, plus a few records
deliberately OUTSIDE it, so window-attribution paths are always exercised.
Dev utility; nothing in the nightly path imports it.
"""
from __future__ import annotations

import random
from datetime import timedelta

from .window import Window

_SCENES = [
    "sits at a wooden desk reviewing a stack of printed contracts",
    "walks through a farmers market holding a canvas tote",
    "pours coffee in a small kitchen while talking to a friend",
    "waits on a train platform reading timetable boards",
    "fixes a bicycle chain outside a hardware store",
    "sketches on a whiteboard in a glass-walled meeting room",
]
_SPEECH = [
    ("mara", "let's move the demo to thursday morning"),
    ("wearer", "remind me to send the invoice tonight"),
    ("vendor", "the heirloom tomatoes are two dollars off today"),
    (None, "platform two for the express service"),
    ("dev", "the migration passed on staging"),
]
_OCR = ["PLATFORM 2 — EXPRESS", "TOTAL: $14.60", "Q3 ROADMAP", "OPEN 7AM–9PM"]


def synth_records(win: Window, *, seed: int = 7, events: int = 40) -> list[dict]:
    rng = random.Random(seed)
    records: list[dict] = []
    span = (win.end_utc - win.start_utc).total_seconds()

    def rec(kind: str, text: str, offset_s: float, dur_s: float = 10.0,
            segments: list | None = None) -> dict:
        t0 = win.start_utc + timedelta(seconds=offset_s)
        t1 = t0 + timedelta(seconds=dur_s)
        r = {"contract": "C2", "version": "0",
             "record_id": f"synth-{kind}-{offset_s:.0f}",
             "user_id": win.user_id,
             "source": {"device_id": "dev-synth", "stream_id": f"st-{kind}",
                        "chunk_id": f"ch-{kind}-{offset_s:.0f}", "blob_ref": "",
                        "modality": "video" if kind == "caption" else "audio"},
             "t_start": t0.isoformat(), "t_end": t1.isoformat(),
             "content": {"kind": kind, "text": text},
             "enrichments": {"speakers": [], "faces": [], "places": [], "objects": []},
             "pipeline_version": "synth-v0", "processed_at": t1.isoformat()}
        if segments:
            r["content"]["segments"] = segments
        return r

    for i in range(events):
        # Cluster activity into the waking span of the window.
        offset = 4 * 3600 + (span * 0.7) * (i / events) + rng.uniform(0, 60)
        scene = rng.choice(_SCENES)
        records.append(rec("caption", f"The wearer {scene}.", offset))
        if rng.random() < 0.6:
            spk, line = rng.choice(_SPEECH)
            t0 = win.start_utc + timedelta(seconds=offset + 2)
            records.append(rec("transcript", line, offset + 2, segments=[
                {"t_start": t0.isoformat(),
                 "t_end": (t0 + timedelta(seconds=4)).isoformat(),
                 "text": line, "speaker": spk}]))
        if rng.random() < 0.25:
            records.append(rec("ocr", rng.choice(_OCR), offset + 5))

    # Out-of-window stragglers: before start and exactly at end (half-open ⇒ excluded).
    records.append(rec("caption", "OUT-OF-WINDOW: brushing teeth pre-boundary.", -600))
    records.append(rec("caption", "OUT-OF-WINDOW: next-day boundary record.", span))
    return records
