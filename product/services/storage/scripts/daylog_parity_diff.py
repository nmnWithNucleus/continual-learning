#!/usr/bin/env python
"""The day-log differential proof — storage's materializer vs continuum's LocalDayLogClient.

This is storage CHARTER **M9's exit bar**, executable. **The bar was NARROWED on 2026-07-27
(D20), after the first run of this script failed on it** — the bar as first written ("block
text, `seg_id`/`block_id` ordering, and anchors" all byte-identical) contradicted D18's own
materialization rule and no code could satisfy both. The failing evidence is not deleted: it
is demoted to § EXPECTED DIVERGENCE, EXPLAINED at the foot of the report, so a reader can see
exactly what differs and why rather than facing a bar that quietly stopped looking.

The narrowed bar, in three tiers, is what this script now expresses:

  (a) BYTE-IDENTICAL, non-negotiable — block `text`, block ordering, `block_id`, `anchors`,
      block `quality`, and the segment payloads (bounds, caption/asr/ocr, `tz`, `quality`).
      This is the artifact that trains the model. A tier-A difference is a real defect.

  (b) PROVEN-EQUIVALENT, not identical — `seg_id`. The two renderers label segments
      differently by design, so the script does not compare labels; it PROVES the relabelling
      is an ORDER-PRESERVING BIJECTION WITH PER-BLOCK MEMBERSHIP PRESERVED. Measured, never
      assumed. A tier-B difference is equally a real defect: it would mean the relabelling
      dropped, duplicated, reordered or re-grouped a segment rather than merely renaming it.

  (c) EXCLUDED deliberately — `content_fingerprint`. It hashes `seg_id`, and it is a cache key
      compared only to *itself* across runs. Both values are printed for information and
      NOTHING is asserted about them: forcing them to match would make the cache lie.

Run it from the service root with the service venv:

    ./.venv/bin/python scripts/daylog_parity_diff.py          # writes the report beside this file
    ./.venv/bin/python scripts/daylog_parity_diff.py --verbose --no-out

Exit code 0 iff every PRECONDITION and every tier (a) / tier (b) check holds; tier (c) cannot
affect it. The script asserts; it does not negotiate. `tests/test_daylog_parity.py` calls the
same `run()` in-process, so a future change to EITHER renderer trips this bar in the suite
rather than only when someone remembers to run the script.

--------------------------------------------------------------------------------------
WHAT THIS PROOF COVERS, AND — READ THIS — WHAT IT DELIBERATELY DOES NOT
--------------------------------------------------------------------------------------
The two paths are NOT the same function, and pretending otherwise would make the proof a
lie. D18 changed three rules on purpose (ARCHITECTURE.md § "C10 evolved", "Materialization
rules"), and each one could make the two renderers legitimately disagree — about which
records they see, about where the bucket boundaries fall, and about which of several
dialects of a unit survives. Two of the three are NEUTRALISED BY THE FIXTURE; the middle
one is no longer a divergence at all, and the story of how it stopped being one is the most
important thing on this page:

  (N1) MEMBERSHIP — storage selects by `ingest_time`, continuum filters by event `t_start`.
       Neutralised by giving every fixture record an `ingest_time` inside storage's window
       AND an event `t_start` inside continuum's window. Both paths therefore see the same
       27 records, and the script PROVES that rather than assuming it (precondition P2).
       This is the ONE rule the two renderers still genuinely differ on, and it is
       deliberate on both sides.

  (N2) THE BUCKET GRID — **ELIMINATED AT THE SOURCE ON 2026-07-27 (F4), NOT NEUTRALISED.**
       Both renderers now bucket on the GLOBAL EPOCH GRID, `floor(t/segment_seconds)`, with
       no window origin in the expression (storage `app/daylog.py::_bucket_index`, continuum
       `app/daylog.py::_bucket_index`).

       Until F4 continuum bucketed window-relatively, `floor((t - window_start)/
       segment_seconds)`, and this proof hid that behind a fixture whose event-window origin
       was chosen ALIGNED to the segment grid — the one case in which the two grids are the
       same partition of the timeline. **No real window has such an origin.** Windows are
       `[watermark, now−δ)` at second granularity (`Store.open_training_window`), so the
       origin is an arbitrary instant. Measured, on this fixture, before the fix: shifting
       the origin by 1..9 s broke tier A for ALL NINE offsets — A7 (segment bounds) at every
       one, A2 (BLOCK TEXT, the artifact that trains the model) at eight of the nine, and at
       +3 s the segment COUNT and the per-block membership too, i.e. tier B and the P6
       shape check as well. Every block's anchor line moved by a minute ("around 10:59–11:00"
       against "around 11:00–11:01").

       So the bar now runs over TWO origins — one aligned, one deliberately misaligned —
       and P3 asserts of each that it is what it claims to be, so nobody can quietly repair
       the misaligned case by aligning it. A8 states the resulting invariant directly: the
       render does not depend on the window origin at all.

  (N3) ONE DIALECT PER RECORD — storage keeps only the latest-ingested record per
       `(chunk_id, content.kind, discriminator)`; continuum has no such rule. Neutralised
       by giving the fixture 27 DISTINCT dialect keys, asserted at P4. A fixture with two
       dialects of one unit would make the two paths disagree correctly.

Everything left after those is a REAL DEFECT and the script reports it as a failure. Tier
(b) still exists and still binds: `seg_id` is a REPRESENTATION choice, the labels are
allowed to differ, and the STRUCTURE they impose is not. As of F4 continuum's reference
adopts storage's labelling rule (ordinal in the day-log) as well as its grid, so on this
fixture the labels come out identical — which is the strongest form of tier B's property,
not an escape from it. § EXPECTED DIVERGENCE, EXPLAINED says what that did and did not
settle.

NOT COVERED, and no fixture can cover it here: the behaviour the membership and dialect
rules exist FOR. A backlog record (ingested Friday, captured Tuesday) and a re-ingested
second dialect are *specified* to render differently in the two paths — they are covered by
tests/test_daylog.py PART 1, not by this diff. This script proves that where the two paths
are supposed to agree they agree to the byte, and that where they are allowed to differ
they differ only in a way that provably changes nothing; it does not prove they are the
same function.

Also not covered: the fetch HTTP surface (tests/test_daylog.py PART 3), the cache and its
invalidation, and continuum's trainer-seam file renderer (`app/renderer.py`), which
consumes a DayLog and is unchanged by the move.

One precondition is of a different kind and is worth knowing about: **P6, non-vacuity.**
Every tier (a) and tier (b) check is a comparison, and comparisons of empty lists all pass —
two renderers that both produced NOTHING would score a perfectly green report. P6 pins the
fixture's rendered shape so that cannot happen. It is not paranoia: it is the only check in
the file that fails when both sides break the same way. It runs ONCE PER ORIGIN, because an
origin that rendered nothing would otherwise buy a free green half of the report.

--------------------------------------------------------------------------------------
RE-BASELINED 2026-08-06 (D28, rebuild Stage E WP-E4): THE v2 RENDERER OVER v1 RECORDS
--------------------------------------------------------------------------------------
Storage's renderer is now the C10 v2 SLOT-WALK over C2 v1 records (one record per chunk,
content is a slots map); continuum's reference renderer still consumes v0 per-kind
records and is deliberately untouched (nothing continuum runs changes before the Stage F
cutover). The differential therefore feeds each side ITS OWN SHAPE of the SAME CONTENT:

  left   continuum/LocalDayLogClient   over `fixture_records()`      — the v0 originals
  right  storage/materialize_daylog    over `fixture_records_v1()`   — hand-built v1
         equivalents, derived record-by-record (each v0 caption/ocr PAIR on one chunk
         folds into ONE v1 record carrying both slots, per Slot Law L2; transcripts
         become asr slots, diarized transcripts become transcript slots with splits)

What tier A then proves is exactly D20's sentence made executable across the rebuild:
the block TEXT is contract — re-cutting the record shape and the renderer around it
changed nothing the trainer can see, byte for byte, over both window origins. A fourth
neutralised divergence joins N1–N3:

  (N4) THE RECORD SHAPE — v0 pairs vs v1 slot-merges, by construction. Neutralised by
       deriving the v1 fixtures from the v0 fixtures 1:1 by (chunk_id, t_start) and
       PROVING the pairing at P2 rather than assuming it. One deliberate extra: v1
       chunk-a3 carries the full video dialect with its ocr slot ABSENT — an honest
       HOLE, which must render as absence (L11) exactly like v0's never-attempted ocr.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import importlib
import importlib.util
import json
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

_SCRIPT_DIR = Path(__file__).resolve().parent
_STORAGE_ROOT = _SCRIPT_DIR.parent
_SERVICES_ROOT = _STORAGE_ROOT.parent
_CONTINUUM_APP = _SERVICES_ROOT / "continuum" / "app"

sys.path.insert(0, str(_STORAGE_ROOT))

from app import schemas                                              # noqa: E402
from app.daylog import (                                             # noqa: E402
    DEFAULT_BLOCK_SEGMENTS,
    DEFAULT_SEGMENT_SECONDS,
    daylog_recipe_id,
    dialect_key,
    materialize_daylog,
)
from app.db import Store                                             # noqa: E402

UTC = timezone.utc

# --- the pinned scenario -------------------------------------------------------------

USER_ID = "user-parity-1"
HOME_TZ = "America/New_York"          # the C12 profile fallback zone
PARIS = "Europe/Paris"                # travel case, leg 1 (UTC+2 in July)
TOKYO = "Asia/Tokyo"                  # travel case, leg 2 (UTC+9)

EVENT_DAY = "2026-07-21"


@dataclass(frozen=True)
class Origin:
    """One continuum-side EVENT-time window origin the whole bar is run over.

    The bar runs over TWO of these (``ORIGINS``) because until F4 it ran over one, and
    that one was grid-ALIGNED — a property no window storage actually mints has (see the
    module docstring, N2). ``aligned`` is not a comment: P3 asserts it of each origin, so
    the misaligned case cannot be silently repaired by aligning it.
    """

    name: str
    start: datetime
    aligned: bool
    why: str

    @property
    def end(self) -> datetime:
        return self.start + timedelta(days=1)

    def remainder(self, segment_seconds: int) -> float:
        return self.start.timestamp() % segment_seconds


# 04:00:00Z is a whole number of 10 s buckets past the epoch. This was the ONLY origin the
# proof ran over before F4.
ORIGIN_ALIGNED = Origin(
    name="grid-aligned",
    start=datetime(2026, 7, 21, 4, 0, 0, tzinfo=UTC),
    aligned=True,
    why="the pre-F4 origin: on the segment grid, where a window-relative grid and the "
        "global grid are the same partition of the timeline",
)

# 04:00:03Z is 3 s past a bucket boundary. +3 s specifically because it is the WORST of the
# nine misalignments measured on this fixture: under the old window-relative grid it did not
# merely shift the bounds, it merged records 09:00:03 / 09:00:05 / 09:00:12 into one bucket
# where the global grid puts them in two — changing the segment COUNT, the per-block
# membership and the block text at once. Whole seconds, not a fraction: storage truncates
# t_end to the second when it mints a window, so a sub-second origin is not a window this
# system can produce.
ORIGIN_MISALIGNED = Origin(
    name="misaligned",
    start=datetime(2026, 7, 21, 4, 0, 3, tzinfo=UTC),
    aligned=False,
    why="what a REAL window looks like: [watermark, now-delta) at second granularity, so "
        "nine origins in ten are off the segment grid",
)

ORIGINS = (ORIGIN_ALIGNED, ORIGIN_MISALIGNED)

# Storage's window is an INGEST-time window, and a deliberately different range: the two
# axes are different questions (D18) and the fixture is honest about that. Every record is
# ingested the following night, hours after it was captured.
INGEST_BASE = datetime(2026, 7, 22, 2, 0, 0, tzinfo=UTC)
WINDOW_NOW = datetime(2026, 7, 22, 3, 0, 0, tzinfo=UTC)   # t_end, with delta forced to 0

# The pinned fixture's rendered SHAPE, asserted at P6 so that an empty or gutted render
# cannot pass a bar made entirely of comparisons. Change these only when the fixture below
# changes on purpose.
EXPECTED_SEGMENTS = 24
EXPECTED_BLOCKS = 5

_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _ts(hh: int, mm: int, ss: int) -> str:
    return f"{EVENT_DAY}T{hh:02d}:{mm:02d}:{ss:02d}Z"


def _record_id(chunk_id: str, pipeline_version: str, discriminator: str) -> str:
    """Model data-processing's deterministic record_id — f(chunk_id, pipeline_version,
    within-chunk discriminator), per the C2 schema's own description."""
    raw = f"{chunk_id}|{pipeline_version}|{discriminator}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _c2(
    chunk_id: str,
    kind: str,
    t_start: str,
    t_end: str,
    text: str,
    *,
    pipeline_version: str,
    modality: str,
    discriminator: str = "",
    device_tz: Optional[str] = None,
    device_offset_minutes: Optional[int] = None,
    subsegments: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    source: dict[str, Any] = {
        "device_id": "dev-bodycam-1" if modality == "video" else "dev-mic-1",
        "stream_id": "stream-01",
        "chunk_id": chunk_id,
        "blob_ref": f"ab/cd/{chunk_id}",
        "modality": modality,
    }
    if device_tz is not None:
        source["device_tz"] = device_tz
        source["device_utc_offset_minutes"] = device_offset_minutes
    content: dict[str, Any] = {"kind": kind, "text": text, "language": "en"}
    if subsegments is not None:
        content["segments"] = subsegments
    record: dict[str, Any] = {
        "contract": "C2",
        "version": "0",
        "record_id": _record_id(chunk_id, pipeline_version, discriminator),
        "user_id": USER_ID,
        "source": source,
        "t_start": t_start,
        "t_end": t_end,
        "content": content,
        "enrichments": {"speakers": [], "faces": [], "places": [], "objects": []},
        "pipeline_version": pipeline_version,
        "processed_at": "2026-07-22T01:59:00Z",
    }
    if discriminator:
        # OPTIONAL-ADDITIVE on C2 (2026-07-27). Absent == the 1:1 case, and absent and ""
        # are the SAME dialect key, so the 1:1 records below simply omit it.
        record["discriminator"] = discriminator
    return record


def fixture_records() -> list[dict[str, Any]]:
    """A fixed 27-record day, in event-time order.

    Ordered by (t_start, insertion) because that is the order storage's
    `list_context_by_updated` hands the renderer, and the within-bucket list order decides
    the joined block text. Precondition P2 proves the two paths really did get this order.

    Coverage, by construction:
      * plain transcripts (no `content.segments`)                      — chunk-a2, b2, b3, c2, c3, d*
      * diarized sub-spans landing in their OWN buckets, incl. a null speaker  — chunk-a4
      * captions                                                       — chunk-a1, a3, b1, c1
      * ocr records                                                    — chunk-a1, b1, c1
      * an ocr record sharing chunk_id AND discriminator with a caption — chunk-a1/c1
        (distinct only by `content.kind`; both must survive)
      * a camera-off gap that starts a new block  — 09:01:00 -> 09:12:00, and again twice
      * two device_tz values in one window (travel)                    — Paris, then Tokyo
      * records with NO device_tz, falling back to home_tz             — the 09:35 block
      * a device zone that puts the block on a DIFFERENT LOCAL DATE than home_tz would
        — the 23:40Z stretch: Tokyo 2026-07-22, New York 2026-07-21
      * the block_segments cap: a 14-segment unbroken run -> 12 + 2     — chunk-d00..d13
      * non-ASCII text (accents, an interpunct, CJK) so "byte-equality" means something
      * text needing .strip()                                          — chunk-c2
    """
    recs: list[dict[str, Any]] = []

    # --- block A: Europe/Paris, 11:00-11:01 local -----------------------------------
    recs.append(_c2("chunk-a1", "caption", _ts(9, 0, 3), _ts(9, 0, 3),
                    "a sunlit kitchen counter with a cafetière",
                    pipeline_version="clipcap-v1", modality="video",
                    discriminator="kf0", device_tz=PARIS, device_offset_minutes=120))
    recs.append(_c2("chunk-a1", "ocr", _ts(9, 0, 3), _ts(9, 0, 3),
                    "CAFÉ · 250 g",
                    pipeline_version="ocr-v1", modality="video",
                    discriminator="kf0", device_tz=PARIS, device_offset_minutes=120))
    recs.append(_c2("chunk-a2", "transcript", _ts(9, 0, 5), _ts(9, 0, 9),
                    "morning — the kettle is on",
                    pipeline_version="asr-mock-v0", modality="audio",
                    device_tz=PARIS, device_offset_minutes=120))
    recs.append(_c2("chunk-a3", "caption", _ts(9, 0, 12), _ts(9, 0, 12),
                    "steam rising from a kettle",
                    pipeline_version="clipcap-v1", modality="video",
                    discriminator="kf1", device_tz=PARIS, device_offset_minutes=120))
    recs.append(_c2("chunk-a4", "transcript", _ts(9, 0, 20), _ts(9, 0, 55),
                    "did you sleep alright not really the street was loud",
                    pipeline_version="asr-mock-v0", modality="audio",
                    device_tz=PARIS, device_offset_minutes=120,
                    subsegments=[
                        {"t_start": _ts(9, 0, 22), "t_end": _ts(9, 0, 27),
                         "text": "did you sleep alright", "speaker": "amélie"},
                        {"t_start": _ts(9, 0, 51), "t_end": _ts(9, 0, 55),
                         "text": "not really, the street was loud", "speaker": None},
                    ]))

    # --- 11 minutes of camera-off, then block B: Asia/Tokyo, 18:12-18:13 local -------
    recs.append(_c2("chunk-b1", "caption", _ts(9, 12, 4), _ts(9, 12, 4),
                    "a crowded train platform under sodium lights",
                    pipeline_version="clipcap-v1", modality="video",
                    discriminator="kf0", device_tz=TOKYO, device_offset_minutes=540))
    recs.append(_c2("chunk-b1", "ocr", _ts(9, 12, 4), _ts(9, 12, 4),
                    "次は品川 · SHINAGAWA",
                    pipeline_version="ocr-v1", modality="video",
                    discriminator="kf0", device_tz=TOKYO, device_offset_minutes=540))
    recs.append(_c2("chunk-b2", "transcript", _ts(9, 12, 15), _ts(9, 12, 19),
                    "the next one is the local, not the rapid",
                    pipeline_version="asr-mock-v0", modality="audio",
                    device_tz=TOKYO, device_offset_minutes=540))
    recs.append(_c2("chunk-b3", "transcript", _ts(9, 12, 55), _ts(9, 12, 59),
                    "we should have taken the earlier one",
                    pipeline_version="asr-mock-v0", modality="audio",
                    device_tz=TOKYO, device_offset_minutes=540))

    # --- block C: NO device_tz at all -> the home_tz fallback, 05:35-05:36 New York ---
    recs.append(_c2("chunk-c1", "caption", _ts(9, 35, 2), _ts(9, 35, 2),
                    "a desk lamp and an open notebook",
                    pipeline_version="clipcap-v1", modality="video", discriminator="kf0"))
    recs.append(_c2("chunk-c1", "ocr", _ts(9, 35, 2), _ts(9, 35, 2),
                    "TODO: call the bank",
                    pipeline_version="ocr-v1", modality="video", discriminator="kf0"))
    recs.append(_c2("chunk-c2", "transcript", _ts(9, 35, 14), _ts(9, 35, 18),
                    "   remember to call the bank at four   ",
                    pipeline_version="asr-mock-v0", modality="audio"))
    recs.append(_c2("chunk-c3", "transcript", _ts(9, 36, 4), _ts(9, 36, 8),
                    "the notebook says four o'clock",
                    pipeline_version="asr-mock-v0", modality="audio"))

    # --- block D: 14 unbroken segments late in the UTC day ---------------------------
    # Two things at once: the block_segments cap (12 + 2), and a device zone that puts the
    # anchor line on 2026-07-22 while home_tz would have said 2026-07-21.
    stretch_start = datetime(2026, 7, 21, 23, 40, 3, tzinfo=UTC)
    for i in range(14):
        begin = stretch_start + timedelta(seconds=10 * i)
        recs.append(_c2(f"chunk-d{i:02d}", "transcript",
                        begin.strftime(_TS_FMT),
                        (begin + timedelta(seconds=4)).strftime(_TS_FMT),
                        f"line {i:02d} of the long evening stretch",
                        pipeline_version="asr-mock-v0", modality="audio",
                        device_tz=TOKYO, device_offset_minutes=540))
    return recs


# --- the v1 equivalents (the v2 renderer's input; D28 re-baseline) -------------------

_AUDIO_PV = "asr.v1-mock.v1"
_ALIGNED_PV = "asr.v1-mock.v1+diarize.v1-mock.v1+speaker_align.v1-mock.v1"
_VIDEO_PV = "clipcap.v1-mock.v1+clipprep.v1-mock.v1+screentext.v1-mock.v1"


def _c2v1(chunk_id: str, t_start: str, t_end: str, slots: dict[str, Any], *,
          pipeline_version: str, modality: str,
          device_tz: Optional[str] = None,
          device_offset_minutes: Optional[int] = None) -> dict[str, Any]:
    """A hand-built C2 v1 record — L3 identity (sha256, NUL-joined, 64 hex), root
    modality, source without modality, content = the slots map. Schema-validity is
    PROVEN at P1, not assumed."""
    source: dict[str, Any] = {
        "device_id": "dev-bodycam-1" if modality == "video" else "dev-mic-1",
        "stream_id": "stream-01",
        "chunk_id": chunk_id,
        "blob_ref": f"ab/cd/{chunk_id}",
    }
    if device_tz is not None:
        source["device_tz"] = device_tz
        source["device_utc_offset_minutes"] = device_offset_minutes
    return {
        "contract": "C2",
        "version": "1",
        "record_id": hashlib.sha256(
            f"{chunk_id}\x00{pipeline_version}".encode("utf-8")).hexdigest(),
        "user_id": USER_ID,
        "modality": modality,
        "source": source,
        "t_start": t_start,
        "t_end": t_end,
        "pipeline_version": pipeline_version,
        "content": {"slots": slots},
    }


def _cap_slot(text: str) -> dict[str, Any]:
    return {"version": "clipcap.v1-mock.v1", "value": text}


def _ocr_slot(text: str) -> dict[str, Any]:
    return {"version": "screentext.v1-mock.v1", "value": text}


def _asr_slot(text: str) -> dict[str, Any]:
    return {"version": "asr.v1-mock.v1", "language": "en", "value": text}


def fixture_records_v1() -> list[dict[str, Any]]:
    """`fixture_records()`, re-cut as C2 v1 — the SAME content in the v1 shape, derived
    record-by-record (N4) and proven paired at P2:

      * each caption/ocr PAIR on one chunk (a1, b1, c1) folds into ONE v1 record
        carrying both slots — Slot Law L2, one record per chunk;
      * plain transcripts become `asr` slots (no splits: one line at the record's own
        t_start, exactly the v0 no-segments path);
      * the diarized transcript (a4) becomes a `transcript` slot with splits — same
        sub-span times, values and speakers (incl. the null one) — plus its parent
        `asr` witness, which the renderer must NOT render on top (one witness, one
        voice);
      * chunk-a3 carries the FULL video dialect with its ocr slot absent — an honest
        hole that must render as absence, exactly like v0's never-attempted ocr.

    24 records (27 v0 minus the three folded ocr rows), event-time order preserved.
    """
    recs: list[dict[str, Any]] = []

    # --- block A: Europe/Paris ------------------------------------------------------
    recs.append(_c2v1("chunk-a1", _ts(9, 0, 3), _ts(9, 0, 3),
                      {"caption": _cap_slot("a sunlit kitchen counter with a cafetière"),
                       "ocr": _ocr_slot("CAFÉ · 250 g")},
                      pipeline_version=_VIDEO_PV, modality="video",
                      device_tz=PARIS, device_offset_minutes=120))
    recs.append(_c2v1("chunk-a2", _ts(9, 0, 5), _ts(9, 0, 9),
                      {"asr": _asr_slot("morning — the kettle is on")},
                      pipeline_version=_AUDIO_PV, modality="audio",
                      device_tz=PARIS, device_offset_minutes=120))
    recs.append(_c2v1("chunk-a3", _ts(9, 0, 12), _ts(9, 0, 12),
                      {"caption": _cap_slot("steam rising from a kettle")},   # ocr: HOLE
                      pipeline_version=_VIDEO_PV, modality="video",
                      device_tz=PARIS, device_offset_minutes=120))
    recs.append(_c2v1("chunk-a4", _ts(9, 0, 20), _ts(9, 0, 55),
                      {"asr": _asr_slot(
                          "did you sleep alright not really the street was loud"),
                       "transcript": {
                           "version": "speaker_align.v1-mock.v1",
                           "splits": [
                               {"t_start": _ts(9, 0, 22), "t_end": _ts(9, 0, 27),
                                "value": "did you sleep alright", "speaker": "amélie"},
                               {"t_start": _ts(9, 0, 51), "t_end": _ts(9, 0, 55),
                                "value": "not really, the street was loud",
                                "speaker": None},
                           ]}},
                      pipeline_version=_ALIGNED_PV, modality="audio",
                      device_tz=PARIS, device_offset_minutes=120))

    # --- block B: Asia/Tokyo --------------------------------------------------------
    recs.append(_c2v1("chunk-b1", _ts(9, 12, 4), _ts(9, 12, 4),
                      {"caption": _cap_slot("a crowded train platform under sodium lights"),
                       "ocr": _ocr_slot("次は品川 · SHINAGAWA")},
                      pipeline_version=_VIDEO_PV, modality="video",
                      device_tz=TOKYO, device_offset_minutes=540))
    recs.append(_c2v1("chunk-b2", _ts(9, 12, 15), _ts(9, 12, 19),
                      {"asr": _asr_slot("the next one is the local, not the rapid")},
                      pipeline_version=_AUDIO_PV, modality="audio",
                      device_tz=TOKYO, device_offset_minutes=540))
    recs.append(_c2v1("chunk-b3", _ts(9, 12, 55), _ts(9, 12, 59),
                      {"asr": _asr_slot("we should have taken the earlier one")},
                      pipeline_version=_AUDIO_PV, modality="audio",
                      device_tz=TOKYO, device_offset_minutes=540))

    # --- block C: no device_tz -> home_tz fallback ----------------------------------
    recs.append(_c2v1("chunk-c1", _ts(9, 35, 2), _ts(9, 35, 2),
                      {"caption": _cap_slot("a desk lamp and an open notebook"),
                       "ocr": _ocr_slot("TODO: call the bank")},
                      pipeline_version=_VIDEO_PV, modality="video"))
    recs.append(_c2v1("chunk-c2", _ts(9, 35, 14), _ts(9, 35, 18),
                      {"asr": _asr_slot("   remember to call the bank at four   ")},
                      pipeline_version=_AUDIO_PV, modality="audio"))
    recs.append(_c2v1("chunk-c3", _ts(9, 36, 4), _ts(9, 36, 8),
                      {"asr": _asr_slot("the notebook says four o'clock")},
                      pipeline_version=_AUDIO_PV, modality="audio"))

    # --- block D: the 14-segment stretch --------------------------------------------
    stretch_start = datetime(2026, 7, 21, 23, 40, 3, tzinfo=UTC)
    for i in range(14):
        begin = stretch_start + timedelta(seconds=10 * i)
        recs.append(_c2v1(f"chunk-d{i:02d}",
                          begin.strftime(_TS_FMT),
                          (begin + timedelta(seconds=4)).strftime(_TS_FMT),
                          {"asr": _asr_slot(f"line {i:02d} of the long evening stretch")},
                          pipeline_version=_AUDIO_PV, modality="audio",
                          device_tz=TOKYO, device_offset_minutes=540))
    return recs


# --- the two paths -------------------------------------------------------------------


def render_storage(records: list[dict[str, Any]], workdir: Path) -> tuple[dict[str, Any], Store, dict[str, Any]]:
    """Render through storage's REAL path: Store -> materialize_daylog -> the C10 body.

    Not `build_daylog` in isolation: the point of M9 is the artifact the service serves,
    so the window-axis range read, the dialect selection and the body assembly are all
    inside the proof. The stamps (`created_at`/`updated_at` — D27 split the old
    `ingest_time`) are storage-assigned at write, so the script forces them on the
    columns afterwards — the same idiom tests/test_daylog.py and tests/test_windows.py
    use.
    """
    store = Store(path=str(workdir / "parity.db"), raw_root=str(workdir / "raw"))
    store.put_profile(USER_ID, HOME_TZ)
    for i, record in enumerate(records):
        store.put_context(record)
        stamp = (INGEST_BASE + timedelta(seconds=i)).strftime(_TS_FMT)
        with store._connect() as conn:
            conn.execute(
                "UPDATE context_records SET created_at = ?, updated_at = ? "
                "WHERE record_id = ?",
                (stamp, stamp, record["record_id"]),
            )
            conn.commit()
    # delta forced to 0 so t_end is exactly WINDOW_NOW and the whole body is deterministic.
    window = store.open_training_window(USER_ID, now=WINDOW_NOW, delta_seconds=0)
    body = materialize_daylog(store, USER_ID, window["window_id"])
    assert body is not None, "the window we just opened must materialize"
    return body, store, window


def load_continuum_app(alias: str = "continuum_app"):
    """Import continuum's `app` package under an alias.

    Both services name their package `app`, so they cannot both be imported normally in one
    process. Aliasing is the honest fix: continuum's own source is executed unmodified,
    relative imports and all — no copy of its renderer exists in this script, which is the
    entire point of a differential proof.
    """
    if alias in sys.modules:
        return sys.modules[alias]
    init = _CONTINUUM_APP / "__init__.py"
    if not init.exists():
        raise SystemExit(f"continuum package not found at {_CONTINUUM_APP}")
    spec = importlib.util.spec_from_file_location(
        alias, init, submodule_search_locations=[str(_CONTINUUM_APP)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


def render_continuum(records: list[dict[str, Any]], window_id: str,
                     origin: Origin) -> dict[str, Any]:
    """Render through continuum's CURRENT production path: LocalDayLogClient.fetch_daylog.

    The window carries STORAGE's window_id verbatim: `seg_id`/`block_id` are
    `{window_id}_s{n:05d}` / `{window_id}_b{n:04d}` on both sides, so handing continuum a
    `w<local-date>` id would manufacture a difference that is only about naming the window
    and would hide the one that is about rendering it.

    `origin` is the EVENT-time window's start. It is a parameter, and not a constant, for
    exactly one reason: the bar has to hold for an origin off the segment grid, which is
    what every window storage mints actually has.
    """
    alias = "continuum_app"
    load_continuum_app(alias)
    window_mod = importlib.import_module(f"{alias}.window")
    client_mod = importlib.import_module(f"{alias}.clients.daylog_client")
    win = window_mod.Window(
        window_id=window_id,
        user_id=USER_ID,
        tz=HOME_TZ,
        start_utc=origin.start,
        end_utc=origin.end,
    )
    client = client_mod.LocalDayLogClient.from_records(
        records,
        segment_seconds=DEFAULT_SEGMENT_SECONDS,
        block_segments=DEFAULT_BLOCK_SEGMENTS,
    )
    daylog = client.fetch_daylog(win)
    return {
        "window_id": daylog.window_id,
        "user_id": daylog.user_id,
        "segments": [asdict(s) for s in daylog.segments],
        "blocks": [asdict(b) for b in daylog.blocks],
        "content_fingerprint": client.fingerprint(daylog),
    }


# --- the diff ------------------------------------------------------------------------

LEFT = "continuum/LocalDayLogClient"
RIGHT = "storage/materialize_daylog"


class Report:
    """The report, and the verdict.

    Every `check()` is BINDING — preconditions (P), tier A and tier B alike. There is
    deliberately no way to record a check that does not count: tier C and the demoted
    D5/D6/D9 dimensions are printed with `say()`, never with `check()`, so "informational"
    and "asserted" cannot be confused by a later edit. Weakening the bar has to look like
    deleting a check, which is visible in a diff.
    """

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.results: list[tuple[str, str, bool]] = []   # (tier, name, ok)
        self.fingerprints: dict[str, str] = {}

    @property
    def checks(self) -> int:
        return len(self.results)

    @property
    def failures(self) -> list[str]:
        return [name for _, name, ok in self.results if not ok]

    def tier(self, tier: str) -> list[tuple[str, bool]]:
        return [(name, ok) for t, name, ok in self.results if t == tier]

    def say(self, text: str = "") -> None:
        self.lines.append(text)

    def head(self, text: str) -> None:
        self.say()
        self.say(text)
        self.say("-" * len(text))

    def check(self, tier: str, name: str, ok: bool, detail: list[str] | None = None) -> bool:
        self.results.append((tier, name, bool(ok)))
        self.say(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        if not ok:
            for line in (detail or [])[:80]:
                self.say(f"         {line}")
            if detail and len(detail) > 80:
                self.say(f"         ... {len(detail) - 80} more diff lines suppressed")
        return bool(ok)


class ProofResult:
    """What `run()` returns: the verdict, the report text, and the measurements.

    The pytest wrapper asserts against the fields, not against the printed text, so the
    report's prose can be reworded without silently disarming the test.
    """

    def __init__(self, report: Report) -> None:
        self.report_text = "\n".join(report.lines) + "\n"
        self.results = list(report.results)
        self.fingerprints = dict(report.fingerprints)

    @property
    def failures(self) -> list[str]:
        return [name for _, name, ok in self.results if not ok]

    @property
    def checks(self) -> int:
        return len(self.results)

    @property
    def exit_code(self) -> int:
        return 1 if self.failures else 0

    def tier(self, tier: str) -> list[tuple[str, bool]]:
        return [(name, ok) for t, name, ok in self.results if t == tier]


def _udiff(left: list[str], right: list[str]) -> list[str]:
    return [ln.rstrip("\n") for ln in difflib.unified_diff(
        left, right, fromfile=LEFT, tofile=RIGHT, lineterm="")]


def _jlines(value: Any) -> list[str]:
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True).splitlines()


def _first_byte_difference(a: bytes, b: bytes) -> int:
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    return min(len(a), len(b))


def _payload(seg: dict[str, Any]) -> dict[str, Any]:
    """A segment row minus its `seg_id` — tier A's half of the segment comparison.

    `seg_id` is excluded here and compared by tier B instead; the two halves together
    cover the whole row, which is why neither may quietly widen.
    """
    return {k: v for k, v in seg.items() if k != "seg_id"}


def _strictly_increasing(xs: list[str]) -> bool:
    return all(a < b for a, b in zip(xs, xs[1:]))


def _origin_checks(rep: Report, origin: Origin, continuum_out: dict[str, Any],
                   storage_body: dict[str, Any]) -> None:
    """Every origin-dependent check — P3, P6, all of tier A(1..7) and all of tier B.

    Run ONCE PER ORIGIN. Before F4 this body ran once, against a grid-ALIGNED origin, and
    that alignment was the only reason it passed: continuum bucketed window-relatively, so
    every other origin gave the two renderers different segments and different block text.
    The check ids are unchanged and the check NAMES carry the origin in brackets, so
    `name.split()[0]` is still the id and tests/test_daylog_parity.py can pin the bar by id
    AND require both origins.
    """
    tag = f"[{origin.name}]"
    s_segments = storage_body["segments"]
    s_blocks = storage_body["blocks"]
    c_segments = continuum_out["segments"]
    c_blocks = continuum_out["blocks"]

    rep.head(f"ORIGIN {tag} — continuum's event window "
             f"[{origin.start.strftime(_TS_FMT)}, {origin.end.strftime(_TS_FMT)})")
    rep.say(f"  {origin.why}")
    rep.say(f"  rendered  {len(c_segments)} vs {len(s_segments)} segments, "
            f"{len(c_blocks)} vs {len(s_blocks)} blocks")

    # --- P3: the origin is what this case claims it is -------------------------------
    remainder = origin.remainder(DEFAULT_SEGMENT_SECONDS)
    on_grid = remainder == 0
    p3_ok = on_grid if origin.aligned else not on_grid
    rep.check("P", f"P3  {tag} the origin is "
              f"{'ON' if origin.aligned else 'OFF'} the {DEFAULT_SEGMENT_SECONDS}s segment "
              "grid, which is what this case exists to cover",
              p3_ok,
              [f"start_utc={origin.start.isoformat()} epoch={origin.start.timestamp()} "
               f"remainder={remainder}",
               f"case declares aligned={origin.aligned}",
               "the MISALIGNED case asserts the NEGATIVE on purpose: repairing a failure "
               "here by moving the origin onto the grid would delete the coverage instead "
               "of the defect"])

    # --- P6: non-vacuity, per origin --------------------------------------------------
    shape_detail = [
        f"expected {EXPECTED_SEGMENTS} segments / {EXPECTED_BLOCKS} blocks on both sides",
        f"{LEFT}: {len(c_segments)} segments, {len(c_blocks)} blocks",
        f"{RIGHT}: {len(s_segments)} segments, {len(s_blocks)} blocks",
        f"empty block texts: {LEFT} "
        f"{sum(1 for b in c_blocks if not b['text'].strip())}, {RIGHT} "
        f"{sum(1 for b in s_blocks if not b['text'].strip())}",
    ]
    rep.check("P", f"P6  {tag} the render is NON-VACUOUS — both sides produced the pinned "
              f"fixture's {EXPECTED_SEGMENTS} segments and {EXPECTED_BLOCKS} blocks, every "
              "block with non-empty text (an empty render passes every comparison below)",
              len(c_segments) == len(s_segments) == EXPECTED_SEGMENTS
              and len(c_blocks) == len(s_blocks) == EXPECTED_BLOCKS
              and all(b["text"].strip() for b in c_blocks)
              and all(b["text"].strip() for b in s_blocks),
              shape_detail)

    # --- tier (a): BYTE-IDENTICAL, non-negotiable -------------------------------------
    rep.say("")
    rep.say(f"  TIER A {tag} — BYTE-IDENTICAL, NON-NEGOTIABLE (M9(a))")
    rep.check("A", f"A1  {tag} block count",
              len(c_blocks) == len(s_blocks),
              [f"{LEFT}: {len(c_blocks)} blocks", f"{RIGHT}: {len(s_blocks)} blocks"])

    text_detail: list[str] = []
    text_ok = len(c_blocks) == len(s_blocks)
    for i, (cb, sb) in enumerate(zip(c_blocks, s_blocks)):
        cbytes = cb["text"].encode("utf-8")
        sbytes = sb["text"].encode("utf-8")
        if cbytes != sbytes:
            text_ok = False
            off = _first_byte_difference(cbytes, sbytes)
            text_detail.append(
                f"block {i}: {len(cbytes)} vs {len(sbytes)} bytes, first difference at "
                f"byte {off}")
            text_detail.extend(_udiff(cb["text"].splitlines(), sb["text"].splitlines()))
    rep.check("A", f"A2  {tag} block text, byte-for-byte (UTF-8)", text_ok, text_detail)

    c_block_ids = [b["block_id"] for b in c_blocks]
    s_block_ids = [b["block_id"] for b in s_blocks]
    rep.check("A", f"A3  {tag} block_id sequence — the ordering labels (values, in order)",
              c_block_ids == s_block_ids, _udiff(c_block_ids, s_block_ids))

    c_anchors = [b["anchors"] for b in c_blocks]
    s_anchors = [b["anchors"] for b in s_blocks]
    rep.check("A", f"A4  {tag} block anchors",
              c_anchors == s_anchors, _udiff(_jlines(c_anchors), _jlines(s_anchors)))

    c_quality = [b["quality"] for b in c_blocks]
    s_quality = [b["quality"] for b in s_blocks]
    rep.check("A", f"A5  {tag} block quality",
              c_quality == s_quality, _udiff(_jlines(c_quality), _jlines(s_quality)))

    rep.check("A", f"A6  {tag} segment count",
              len(c_segments) == len(s_segments),
              [f"{LEFT}: {len(c_segments)} segments", f"{RIGHT}: {len(s_segments)} segments"])

    c_payloads = [_payload(s) for s in c_segments]
    s_payloads = [_payload(s) for s in s_segments]
    rep.check("A", f"A7  {tag} segment payloads — bounds, caption/asr/ocr, tz, quality "
              "(seg_id excluded: tier B)",
              c_payloads == s_payloads, _udiff(_jlines(c_payloads), _jlines(s_payloads)))

    # --- tier (b): PROVEN-EQUIVALENT, not identical -----------------------------------
    rep.say("")
    rep.say(f"  TIER B {tag} — PROVEN-EQUIVALENT (M9(b): the seg_id relabelling is an "
            "order-preserving bijection with per-block membership preserved)")

    c_seg_ids = [s["seg_id"] for s in c_segments]
    s_seg_ids = [s["seg_id"] for s in s_segments]

    c_dupes = sorted({x for x in c_seg_ids if c_seg_ids.count(x) > 1})
    s_dupes = sorted({x for x in s_seg_ids if s_seg_ids.count(x) > 1})
    rep.check("B", f"B1  {tag} seg_id is unique within each render (a bijection needs it "
              "on both sides)",
              not c_dupes and not s_dupes,
              [f"{LEFT} duplicates: {c_dupes}", f"{RIGHT} duplicates: {s_dupes}"])

    mapping = dict(zip(c_seg_ids, s_seg_ids))
    bijection = (
        len(c_seg_ids) == len(s_seg_ids)
        and len(set(c_seg_ids)) == len(c_seg_ids)
        and len(set(s_seg_ids)) == len(s_seg_ids)
        and len(mapping) == len(c_seg_ids)
        and len(set(mapping.values())) == len(mapping)
    )
    rep.check("B", f"B2  {tag} the relabelling is a BIJECTION — {len(mapping)} left ids -> "
              f"{len(set(mapping.values()))} distinct right ids, total and injective "
              "both ways",
              bijection,
              [f"left ids: {len(c_seg_ids)} ({len(set(c_seg_ids))} distinct)",
               f"right ids: {len(s_seg_ids)} ({len(set(s_seg_ids))} distinct)",
               f"map entries: {len(mapping)} -> {len(set(mapping.values()))} distinct images"])

    c_sorted = _strictly_increasing(c_seg_ids)
    s_sorted = _strictly_increasing(s_seg_ids)
    rep.check("B", f"B3  {tag} the bijection is ORDER-PRESERVING — both renders emit "
              "seg_ids in strictly ascending order, so the k-th -> k-th map preserves '<'",
              c_sorted and s_sorted,
              [f"{LEFT} strictly ascending: {c_sorted}",
               f"{RIGHT} strictly ascending: {s_sorted}",
               "the ids are opaque tokens whose ONLY permitted relation is < / >= "
               "(ARCHITECTURE § 'C10 evolved'), which is exactly what this check uses"])

    referenced = [x for cb in c_blocks for x in cb["seg_ids"]]
    domain_ok = all(x in mapping for x in referenced)
    membership_detail: list[str] = []
    if not domain_ok:
        membership_detail.append(
            f"{LEFT} blocks reference {len([x for x in referenced if x not in mapping])} "
            "seg_id(s) that are not in its own segment list")
    membership_ok = domain_ok and len(c_blocks) == len(s_blocks)
    for i, (cb, sb) in enumerate(zip(c_blocks, s_blocks)):
        relabelled = [mapping.get(x) for x in cb["seg_ids"]]
        if relabelled != sb["seg_ids"]:
            membership_ok = False
            membership_detail.append(f"block {i}:")
            membership_detail.extend(
                _udiff(_jlines(relabelled), _jlines(sb["seg_ids"])))
    rep.check("B", f"B4  {tag} PER-BLOCK MEMBERSHIP is preserved — mapping every left "
              "block's seg_ids through the bijection reproduces the right block's, exactly "
              "and in order",
              membership_ok, membership_detail)


def run(verbose: bool = False) -> ProofResult:
    rep = Report()
    records = fixture_records()             # v0 — the reference renderer's input
    records_v1 = fixture_records_v1()       # v1 — the v2 slot-walk renderer's input

    with tempfile.TemporaryDirectory(prefix="daylog-parity-") as tmp:
        storage_body, store, window = render_storage(records_v1, Path(tmp))
        # Storage renders ONCE: its window is on the updated_at axis and knows nothing
        # about continuum's event-time origin. Continuum renders once per origin, over
        # the v0 originals — its reference renderer is untouched until cutover.
        renders = {o.name: render_continuum(records, window["window_id"], o)
                   for o in ORIGINS}
        landed = store.list_context_by_updated(USER_ID, window["t_start"], window["t_end"])

    s_segments = storage_body["segments"]
    s_blocks = storage_body["blocks"]

    rep.say("day-log differential proof — storage CHARTER M9 (bar NARROWED 2026-07-27, D20;")
    rep.say("extended to a MISALIGNED window origin 2026-07-27, F4; RE-BASELINED 2026-08-06")
    rep.say("against the v2 slot-walk renderer over hand-built C2 v1 records — D28, Stage E)")
    rep.say("=" * 86)
    rep.say(f"  left   {LEFT}  over {len(records)} v0 records")
    rep.say(f"  right  {RIGHT}  over {len(records_v1)} v1 equivalents (N4)")
    rep.say(f"  window_id            {window['window_id']}")
    rep.say(f"  storage window       [{window['t_start']}, {window['t_end']})  on updated_at (D27)")
    for origin in ORIGINS:
        rep.say(f"  continuum window     [{origin.start.strftime(_TS_FMT)}, "
                f"{origin.end.strftime(_TS_FMT)})  on event t_start   "
                f"<- {origin.name}")
    rep.say(f"  segmentation         segment_seconds={DEFAULT_SEGMENT_SECONDS} "
            f"block_segments={DEFAULT_BLOCK_SEGMENTS} recipe_id={daylog_recipe_id()}")
    rep.say(f"  home_tz              {HOME_TZ}")
    rep.say(f"  storage rendered     {len(s_segments)} segments, {len(s_blocks)} blocks")

    # --- fixture-level preconditions: origin-independent, so asserted ONCE ------------
    rep.head("PRECONDITIONS (fixture-level) — the divergences this proof neutralises")
    rep.say("  P3 and P6 are ORIGIN-dependent and appear once per origin, below.")

    # BOTH shapes, each against its own schema: the v0 originals against the v0 file
    # (the reference renderer's input), the v1 equivalents against the service's own
    # v1-only gate (validate_c2, fullmatch closure included).
    v0_errors = [{"record_id": r["record_id"], **e}
                 for r in records
                 for e in schemas.errors(
                     "https://nucleus.ai/contracts/c2_processed_record.v0.json", r)]
    v1_errors = [{"record_id": r["record_id"], **e}
                 for r in records_v1 for e in schemas.validate_c2(r)]
    rep.check("P", "P1  every fixture record is schema-valid C2 — v0 side against the v0 "
              "file, v1 side against the service's v1 gate",
              not v0_errors and not v1_errors,
              _jlines({"v0": v0_errors, "v1": v1_errors}))

    v0_chunks: list[tuple[str, str]] = []
    for r in records:
        key = (r["source"]["chunk_id"], r["t_start"])
        if key not in v0_chunks:
            v0_chunks.append(key)
    v1_chunks = [(r["source"]["chunk_id"], r["t_start"]) for r in records_v1]
    fixture_ids = [r["record_id"] for r in records_v1]
    landed_ids = [row["record"]["record_id"] for row in landed]
    rep.check("P", "P2  N1 + N4 neutralised: storage saw exactly the v1 fixtures in order "
              f"({len(fixture_ids)} records), and the v1 set pairs 1:1 by "
              f"(chunk_id, t_start) with the v0 set's {len(v0_chunks)} content chunks",
              fixture_ids == landed_ids and v0_chunks == v1_chunks,
              _udiff([repr(x) for x in v0_chunks], [repr(x) for x in v1_chunks])
              + _udiff(fixture_ids, landed_ids))

    keys = [dialect_key(r) for r in records_v1]
    dupes = sorted({repr(k) for k in keys if keys.count(k) > 1})
    rep.check("P", "P4  N3 neutralised: every v1 fixture is its own chunk under the D28 "
              f"dedup key ({len(set(keys))} distinct keys)",
              not dupes, dupes)

    c10_errors = schemas.validate_c10(storage_body)
    rep.check("P", "P5  storage's body validates against contracts/c10_daylog.v2.json",
              not c10_errors, _jlines(c10_errors))

    # --- the bar, once per origin -----------------------------------------------------
    rep.head("THE BAR, RUN OVER EVERY ORIGIN")
    rep.say("  Everything the trainer and the amplifier can SEE. ARCHITECTURE §Ownership")
    rep.say("  splits, 'Day-log: representation vs. content': block text + anchors are")
    rep.say("  CONTRACT, not storage's to move; a difference here changes what the model")
    rep.say("  learns, silently and with no error.")
    rep.say("  Where BLOCK ORDERING is proven, precisely: A2/A4/A5/A7 compare the per-block")
    rep.say("  and per-segment lists POSITIONALLY, so they are order-sensitive — reordering")
    rep.say("  two blocks fails them. A3 pins the ordering LABELS, which is a weaker claim on")
    rep.say("  its own (both renderers number blocks from 0, so equal counts alone make the")
    rep.say("  label sequences match) and is not asked to carry the ordering proof.")
    rep.say("  Tier B's positional pairing (k-th left <-> k-th right) is NOT an assumption:")
    rep.say("  A6 + A7 already proved the k-th segments are the same segment, same bounds and")
    rep.say("  same content.")
    for origin in ORIGINS:
        _origin_checks(rep, origin, renders[origin.name], storage_body)

    # --- A8: the invariant F4 is actually about ---------------------------------------
    rep.head("A8 — ORIGIN-INDEPENDENCE (the invariant the two cases above exist to state)")
    rep.say("  Both renderers bucket on the GLOBAL epoch grid, so the window origin is not")
    rep.say("  an input to the render at all. Stated directly rather than only implied by")
    rep.say("  the per-origin tiers, so that a reader can see the claim and a regression")
    rep.say("  names it. Reverting continuum to a window-relative grid fails this check by")
    rep.say("  construction, whatever else it might fail.")
    a8_detail: list[str] = []
    a8_ok = True
    base = ORIGINS[0]
    base_render = renders[base.name]
    for other in ORIGINS[1:]:
        other_render = renders[other.name]
        for field_name in ("segments", "blocks"):
            if base_render[field_name] != other_render[field_name]:
                a8_ok = False
                a8_detail.append(
                    f"continuum {field_name}: {base.name} vs {other.name} differ")
                a8_detail.extend(_udiff(_jlines(base_render[field_name]),
                                        _jlines(other_render[field_name])))
    rep.check("A", "A8  continuum's render is ORIGIN-INDEPENDENT — the "
              f"{len(ORIGINS)} origins above produce byte-identical segments and blocks",
              a8_ok, a8_detail)

    # From here on the report reads the base render; A8 has just proved the choice is
    # immaterial, and it is a binding check, so this is a licence and not an assumption.
    continuum_out = base_render
    c_segments = continuum_out["segments"]
    c_blocks = continuum_out["blocks"]
    c_seg_ids = [s["seg_id"] for s in c_segments]
    s_seg_ids = [s["seg_id"] for s in s_segments]
    c_payloads = [_payload(s) for s in c_segments]
    s_payloads = [_payload(s) for s in s_segments]

    # --- tier (c): excluded deliberately. REPORTED, NOT ASSERTED ----------------------
    rep.head("TIER C — EXCLUDED DELIBERATELY, ASSERTED NOWHERE (M9(c): content_fingerprint)")
    rep.fingerprints = {LEFT: continuum_out["content_fingerprint"],
                        RIGHT: storage_body["content_fingerprint"]}
    rep.say(f"  {LEFT}:")
    rep.say(f"    {continuum_out['content_fingerprint']}")
    rep.say(f"  {RIGHT}:")
    rep.say(f"    {storage_body['content_fingerprint']}")
    rep.say(f"  equal: "
            f"{continuum_out['content_fingerprint'] == storage_body['content_fingerprint']}"
            "   <- INFORMATION ONLY. No check above or below reads this line.")
    rep.say("  It hashes seg_id (daylog.py daylog_fingerprint hashes the segment rows whole),")
    rep.say("  and it is a CACHE KEY compared only to ITSELF across runs — the journal stage")
    rep.say("  key. It SHOULD change when the renderer changes; forcing it to match would make")
    rep.say("  the cache lie. It changes once at cutover, that night re-materializes, and that")
    rep.say("  is correct behaviour, not a defect. Equal on THIS fixture is not a promise that")
    rep.say("  it is equal in production: there the two paths select records on different axes")
    rep.say("  (N1), so they legitimately hash different day-logs.")

    # --- the demoted dimensions: kept visible, no longer binding ----------------------
    rep.head("EXPECTED DIVERGENCE, EXPLAINED — the demoted D5/D6/D9 dimensions "
             "(INFORMATIONAL)")
    rep.say("  These three were M9's bar as FIRST written, and this script FAILED them on")
    rep.say("  2026-07-27. D20 narrowed the bar rather than the code: D5/D6 became tier B's")
    rep.say("  four structural checks above and D9 became tier C. Their raw output is kept")
    rep.say("  here verbatim — a bar that stops PRINTING what differs is a bar that has")
    rep.say("  quietly stopped looking. Nothing in this section affects the verdict.")
    rep.say("")
    rep.say("  (was D5) seg_id sequence:")
    d5_diff = _udiff(c_seg_ids, s_seg_ids)
    if d5_diff:
        for line in d5_diff[:80]:
            rep.say(f"      {line}")
        if len(d5_diff) > 80:
            rep.say(f"      ... {len(d5_diff) - 80} more diff lines suppressed")
    else:
        rep.say("      (identical in this run — no divergence to show)")
    rep.say("")
    rep.say("  (was D6) per-block seg_ids membership — a pure CONSEQUENCE of D5; tier B's B4")
    rep.say("  is the same comparison with the relabelling applied first:")
    d6_diff = _udiff(_jlines([b["seg_ids"] for b in c_blocks]),
                     _jlines([b["seg_ids"] for b in s_blocks]))
    if d6_diff:
        for line in d6_diff[:80]:
            rep.say(f"      {line}")
        if len(d6_diff) > 80:
            rep.say(f"      ... {len(d6_diff) - 80} more diff lines suppressed")
    else:
        rep.say("      (identical in this run — no divergence to show)")
    rep.say("")
    rep.say("  (was D9) content_fingerprint — see tier C above for both values and for why it")
    rep.say("  is excluded rather than merely tolerated.")
    rep.say("")

    same_count = len(c_seg_ids) == len(s_seg_ids)
    relabel_only = same_count and c_payloads == s_payloads and c_seg_ids != s_seg_ids
    if relabel_only:
        rep.say("  CHARACTERISATION — the seg_id divergence is a pure RELABELLING: the k-th")
        rep.say("  segment on the left is the k-th on the right, same bounds and same content,")
        rep.say("  only the id differs. Tier B is what turns that sentence into a proof.")
        for c_id, s_id in list(zip(c_seg_ids, s_seg_ids))[:6]:
            rep.say(f"    {c_id}  ->  {s_id}")
        if len(c_seg_ids) > 6:
            rep.say(f"    ... {len(c_seg_ids) - 6} more")
    elif c_seg_ids == s_seg_ids:
        rep.say("  CHARACTERISATION — seg_ids are IDENTICAL in this run, and that is the F4")
        rep.say("  fix's doing, so read it precisely rather than as 'D20 was wrong'.")
        rep.say("")
        rep.say("  D20's argument still stands, unchanged: storage CANNOT reproduce a")
        rep.say("  window-relative seg_id. Its window bounds are on the INGEST axis, where a")
        rep.say("  backlog record yields a NEGATIVE window-relative index, and the global")
        rep.say("  epoch index is nine digits and voids the fixed {:05d} width. The labelling")
        rep.say("  could therefore only ever converge in ONE direction — and in F4 it did:")
        rep.say("  continuum's LOCAL renderer, which is the parity REFERENCE and not the")
        rep.say("  research-golden one, adopted storage's grid (the defect fix) and, with it,")
        rep.say("  storage's ordinal labelling (the consequence: once buckets are global,")
        rep.say("  labelling by bucket index means nine-digit ids).")
        rep.say("")
        rep.say("  WHAT THAT COSTS, said plainly: tier B no longer MEASURES a relabelling on")
        rep.say("  this fixture, because there is none left to measure. Its four checks still")
        rep.say("  BIND — a segment dropped, duplicated, reordered or moved between blocks by")
        rep.say("  either renderer still fails B1/B2/B3/B4 — but they no longer demonstrate")
        rep.say("  that a DIFFERENCE in labels is harmless. Identity is the strongest form of")
        rep.say("  tier B's property and the weakest evidence for it, both at once.")
    else:
        rep.say("  CHARACTERISATION — segment payloads or counts differ too, so the divergence")
        rep.say("  is NOT a pure relabelling. Tier A above will have failed; that is the real")
        rep.say("  finding, and this section is not where to look for it.")

    if verbose:
        rep.head("FULL RENDERS")
        rep.say(f"--- {LEFT}  (origin: {base.name}; A8 proves the other origins match)")
        rep.lines.extend(_jlines(continuum_out))
        rep.say(f"--- {RIGHT}")
        rep.lines.extend(_jlines(storage_body))

    rep.head("VERDICT")
    n_p, n_a, n_b = len(rep.tier("P")), len(rep.tier("A")), len(rep.tier("B"))
    if rep.failures:
        rep.say(f"  FAIL — {len(rep.failures)} of {rep.checks} binding checks failed:")
        for name in rep.failures:
            rep.say(f"    * {name}")
        rep.say("")
        rep.say("  M9's NARROWED exit bar is NOT met for this window, so continuum's local")
        rep.say("  path must not be deleted. A tier-A failure means the two renderers produce")
        rep.say("  DIFFERENT TRAINING TEXT; a tier-B failure means the seg_id relabelling did")
        rep.say("  more than rename (dropped, duplicated, reordered or re-grouped a segment).")
        rep.say("  Neither is fixable by adjusting this script. Note what is NOT a reason to")
        rep.say("  be here: content_fingerprint, which tier C excludes and no check reads.")
        rep.say("  If ONLY the misaligned origin failed, the defect is a window-relative grid")
        rep.say("  somewhere: that is F4, and aligning the fixture's origin is not the fix.")
    else:
        rep.say(f"  PASS — all {rep.checks} binding checks hold "
                f"({n_p} preconditions, {n_a} tier A byte-identical, "
                f"{n_b} tier B proven-equivalent), over {len(ORIGINS)} window origins: "
                f"{', '.join(o.name for o in ORIGINS)}.")
        rep.say("  Tier A is byte-identical: the block text, ordering, block_ids, anchors,")
        rep.say("  quality and segment payloads storage renders ARE continuum's, to the byte —")
        rep.say("  and now for a window origin OFF the segment grid, which is what every")
        rep.say("  window storage actually mints has (A8 states the invariant directly).")
        rep.say("  Tier B is proven, not assumed: the seg_id relabelling is an order-preserving")
        rep.say("  bijection with per-block membership preserved, so nothing that reads a")
        rep.say("  day-log can tell the two renderers apart.")
        rep.say("  Tier C (content_fingerprint) is excluded by M9(c); both values are printed")
        rep.say("  above rather than hidden, and nothing asserts on them.")
        rep.say("  M9's narrowed exit bar is met for this window.")

    return ProofResult(rep)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--verbose", action="store_true",
                    help="append both full day-logs to the report")
    ap.add_argument("--out", default=str(_SCRIPT_DIR / "daylog_parity_diff.out.txt"),
                    help="where to write the report (M9 requires the output be committed)")
    ap.add_argument("--no-out", action="store_true", help="print only, write nothing")
    args = ap.parse_args()

    result = run(verbose=args.verbose)
    sys.stdout.write(result.report_text)
    if not args.no_out:
        Path(args.out).write_text(result.report_text, encoding="utf-8")
        sys.stdout.write(f"\nreport written to {args.out}\n")
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
