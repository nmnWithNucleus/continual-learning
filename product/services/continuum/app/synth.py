"""Synthetic day generator — C2-shaped records for headless dev + tests.

Produces a plausible mixed-modality day (per-keyframe captions, diarized
transcripts, OCR rows) inside a consolidation window, plus a few records
deliberately OUTSIDE it, so window-attribution paths are always exercised.

Dev utility — but `nightly.py --synthetic` DOES import it, so "nothing in the
nightly path imports it" stopped being true at the cutover and the difference
mattered: the placement below used to assume the window was a 24 h local day
(`window_for(date, tz)`, deleted by D18) and hard-coded a 4 h lead-in before the
first event. Storage's windows are `[last_trained_t, now−delta)` on the INGEST
clock and are routinely minutes long, so every synthetic event landed past the
end, `build_daylog` filtered them all out, and the demo night reported
`skipped_no_data` and exit 0 — a night that trained on nothing and called it
success. Every offset is now a FRACTION OF THE WINDOW'S OWN SPAN, so the
generator is honest for any window storage can mint. Measured: for a 24 h window
the arithmetic is unchanged (`min(4h, 0.2·span)` is 4 h and `min(60, 0.05·span)`
is 60 s at span = 86400 s), so existing fixtures and goldens are byte-identical.
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

    # A per-day sequence number, NOT the offset in seconds, is what makes the ids
    # unique. The offset spelling (`synth-caption-14419`) collided as soon as the
    # window got short enough for two events to round to the same second — invisible
    # to `build_daylog`, which reads neither field, but fatal the moment these
    # records are POSTed to storage, where `put_context` UPSERTS on `record_id` and
    # `select_dialects` keeps one record per `(chunk_id, kind, discriminator)`. A
    # generator whose output silently collapses to a handful of rows when written to
    # the real store is not a usable seed for the demo.
    seq = 0

    def rec(kind: str, text: str, offset_s: float, dur_s: float = 10.0,
            segments: list | None = None) -> dict:
        nonlocal seq
        seq += 1
        rid = f"synth-{kind}-{seq:04d}"
        t0 = win.start_utc + timedelta(seconds=offset_s)
        t1 = t0 + timedelta(seconds=dur_s)
        r = {"contract": "C2", "version": "0",
             "record_id": rid,
             "user_id": win.user_id,
             # `blob_ref` is REQUIRED non-empty by c2_processed_record.v0.json, and
             # the empty string it used to carry made every one of these records a
             # 422 at `POST /context/records` — a "C2-shaped" record the C2 store
             # would not accept.
             "source": {"device_id": "dev-synth", "stream_id": f"st-{kind}",
                        "chunk_id": f"ch-{kind}-{seq:04d}",
                        "blob_ref": f"raw/{win.user_id}/{rid}",
                        "modality": "video" if kind == "caption" else "audio"},
             "t_start": t0.isoformat(), "t_end": t1.isoformat(),
             "content": {"kind": kind, "text": text},
             "enrichments": {"speakers": [], "faces": [], "places": [], "objects": []},
             "pipeline_version": "synth-v0", "processed_at": t1.isoformat()}
        if segments:
            r["content"]["segments"] = segments
        return r

    # Where the day's activity sits INSIDE the window, as fractions of its span.
    # The caps keep the familiar shape on a 24 h window (a 4 h lead-in, up to a
    # minute of jitter) while guaranteeing the invariant that actually matters:
    # lead_in + 0.7·span + jitter <= 0.95·span, so every generated event is inside
    # the window it was asked for, whatever its length.
    lead_in = min(4 * 3600, span * 0.2)
    jitter = min(60, span * 0.05)

    for i in range(events):
        # Cluster activity into the waking span of the window.
        offset = lead_in + (span * 0.7) * (i / events) + rng.uniform(0, jitter)
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
