"""Day-log materialization: C2 records → segment rows → scene blocks (C10 evolved, D18).

LIFTED, deliberately, from ``services/continuum/app/daylog.py`` — ``build_daylog`` +
``_render_block`` + ``_block_zone``, the **product** renderer over C2 records. It is NOT
continuum's ``morpheus/profiles/*.py`` ``Profile.render_block``, which is the
research-parity surface, is recipe-coupled, and stays with the amplifier. Field names
here match the day-log schema exactly (segment rows: seg_id / t_start / t_end / caption /
asr / ocr / quality / tz; block rows: block_id / seg_ids / text / anchors / quality),
because the day log is the ONLY interface between ingest and consolidation.

THREE RULES CHANGE vs the continuum original. All three are forced by the watermark
window; everything else is preserved byte-for-byte on purpose.

  1. MEMBERSHIP IS BY ``ingest_time``, BUCKETING IS BY ``t_start``. A window holds every
     C2 record whose ``ingest_time`` falls in ``[t_start, t_end)``, whatever its event
     time — so a chunk captured Tuesday and uploaded Friday trains in Friday's window and
     renders in a block anchored "On [Tuesday]". That is correct, not a bug: content
     stays event-time-correct because blocks are formed by temporal ADJACENCY and carry
     their own local anchors, so a backlog simply forms its own blocks. Consequence:
     continuum's ``in_window(t_start, win)`` filter is not ported, it is DELETED — the
     range query on the ingest axis already decided membership, and re-filtering on the
     event axis would throw away exactly the backlog this design exists to train. The
     sub-span guard that went with it (a VAD chunk straddling the boundary must not drag
     its in-window speech out) is gone for the same reason: there is no event-time
     boundary left to straddle.
  2. SEGMENT BUCKETS SIT ON A GLOBAL EPOCH GRID — ``floor(t_start / segment_seconds)``,
     not ``floor((t_start - window_start) / segment_seconds)``. Required: window-relative
     indices go NEGATIVE the moment membership is on the ingest axis (a Tuesday record in
     Friday's window starts days before the window does). Also better: a bucket is then
     stable across re-materialization, because it no longer depends on which window
     happened to pick the record up.
  3. ONE DIALECT PER RECORD, LATEST WINS. Among records sharing
     ``(chunk_id, content.kind, discriminator)`` the materializer keeps the one with the
     latest ``ingest_time`` and drops the rest. Keyed on ``ingest_time`` because
     ``pipeline_version`` is a COMPOSED string (a mutate stage's enabledness is a version
     fragment) and therefore not orderable; keyed on ``content.kind`` because captions and
     transcripts can share one ``pipeline_version``, so a kind-blind rule would drop
     transcripts in order to drop captions. This is what fixes the re-consolidation
     double-count — ``daylog.py`` filters on neither field today.

Segmentation is RECIPE-VERSIONED: ``segment_seconds`` / ``block_segments`` are the
``corpus`` knobs of the pinned consolidation recipe, and the body carries ``recipe_id``
beside ``daylog_format_version`` so a consumer can tell which shape it got. C13 — the
recipe registry — IS hosted here now (``app/registry.py``), so ``materialize_daylog``
READS those knobs out of the very recipe it is about to stamp on the body instead of
pinning them: the stamp and the render must be the same decision, or the stamp is a claim
nothing honoured. That is not a tidiness argument. Continuum keys its journal stage on
``(recipe_id, content_fingerprint)`` and records ``recipe_id`` in C5 lineage, so a night
rendered under knobs other than the ones it claims is audited as trained under a recipe it
was not trained under, and the discrepancy is unfalsifiable afterwards.

A named recipe that cannot be read, or that carries no usable ``corpus`` block, therefore
FAILS LOUDLY (``DayLogRecipeUnusable`` -> 500) rather than falling back to the constants
below: a silent fallback to the constants is precisely the defect this arrangement closes.
The constants survive only as ``build_daylog``'s own signature defaults, for callers that
render directly without a registry (the M9 parity script, the unit tests below).
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import registry

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids an import cycle with db
    from .db import Store

# The renderer + row shape this module produces. It is NOT the C10 body version (which is
# "1") and NOT the recipe id: it names the DAY-LOG FORMAT — bump it whenever the segment/
# block shape or the rendered block text changes, and every cached day-log re-materializes
# on next fetch (see materialize_daylog) instead of silently serving an old dialect.
DAYLOG_FORMAT_VERSION = "1"

# The `corpus` knobs of consolidation-v1.0 (recipes/consolidation-v1.0.json), as
# ``build_daylog``'s signature defaults ONLY. ``materialize_daylog`` never reads them: it
# resolves the knobs from the registry entry for the recipe_id it stamps (see
# recipe_segmentation), because a body that claims a recipe it did not render under is
# exactly the lie C5 lineage cannot detect afterwards.
DEFAULT_SEGMENT_SECONDS = 10
DEFAULT_BLOCK_SEGMENTS = 12

_DEFAULT_RECIPE_ID = "consolidation-v1.1"


def daylog_recipe_id() -> str:
    """The recipe id the materializer renders under.

    Service config, never a request parameter: ``recipe_id`` is GLOBAL and versioned
    (``recipe_id`` == filename stem) and it enters continuum's stage keys, so a caller who
    could choose it per fetch could fork the id per user — the exact thing that keeps the
    registry out of the profile. The environment names the ID; the C13 registry resolves
    that id to its knobs (``recipe_segmentation``), which is the only division of labour
    that keeps "what we rendered" and "what we stamped" the same fact.
    """
    return os.environ.get("STORAGE_DAYLOG_RECIPE_ID", _DEFAULT_RECIPE_ID)


class DayLogRefused(Exception):
    """Materialization cannot run for this user; the route maps this to 409.

    Today there is exactly one reason: the user has no C12 profile, so there is no
    ``home_tz`` to fall back to when a record carries no ``device_tz``. Refusing LOUDLY is
    the requirement — there is no server-side default timezone anywhere in the system, so
    substituting UTC would render a whole night's anchor lines to the wrong local date and
    nothing downstream could tell. A user with no ``home_tz`` simply does not consolidate:
    an operational alert, never a silent skip.
    """

    def __init__(self, detail: dict[str, Any]) -> None:
        super().__init__(str(detail.get("error", "day-log refused")))
        self.detail = detail


class DayLogRecipeUnusable(Exception):
    """The configured recipe cannot supply the segmentation knobs; the route maps this to 500.

    500 and not a 4xx because the fault is OURS: ``recipe_id`` is service config
    (``STORAGE_DAYLOG_RECIPE_ID``), never a request parameter, so there is nothing the
    caller could have asked differently — the same posture ``RegistryFault`` takes when the
    registry's own contents are wrong.

    Raised rather than falling back to ``DEFAULT_SEGMENT_SECONDS`` / ``DEFAULT_BLOCK_SEGMENTS``
    on purpose: the body stamps ``recipe_id``, continuum keys its journal stage on it and C5
    records it, so rendering under the constants while stamping a recipe that says otherwise
    produces an artifact whose provenance is wrong and uncheckable. A day-log we cannot honestly
    label is not served at all.
    """

    def __init__(self, detail: dict[str, Any]) -> None:
        super().__init__(str(detail.get("error", "day-log recipe unusable")))
        self.detail = detail


def recipe_segmentation(recipe_id: str) -> tuple[int, int]:
    """``(segment_seconds, block_segments)`` — the ``corpus`` knobs of ``recipe_id``.

    Resolved through the C13 registry this service now hosts: ``registry.fetch_recipe`` is
    the same read ``GET /recipes/{recipe_id}`` serves, so the knobs the day-log renders
    under are the knobs an operator can fetch and check against the id on the body.

    Every failure is loud (``DayLogRecipeUnusable``) and none of them degrades to the module
    constants — an unknown id, a registry the file disagrees with, a recipe with no
    ``corpus``, or a knob that is not a positive integer. There is deliberately no partial
    answer either: taking ``segment_seconds`` from the recipe and ``block_segments`` from a
    default would still render a shape no recipe describes.
    """
    try:
        recipe = registry.fetch_recipe(recipe_id)
    except (registry.ArtifactMissing, registry.RegistryFault) as exc:
        raise DayLogRecipeUnusable(
            {
                "error": "day-log recipe cannot be resolved",
                "recipe_id": recipe_id,
                "reason": "the day-log renders under the corpus knobs of the recipe it "
                          "stamps, so an unresolvable recipe id cannot be rendered under "
                          "at all; fix STORAGE_DAYLOG_RECIPE_ID or register the recipe",
                "registry_detail": exc.detail,
            }
        ) from exc
    corpus = recipe.get("corpus")
    if not isinstance(corpus, dict):
        raise DayLogRecipeUnusable(
            {
                "error": "day-log recipe has no corpus block",
                "recipe_id": recipe_id,
                "reason": "segment_seconds/block_segments live in the recipe's `corpus` "
                          "block (required by c13_recipe.v0.json); without it there is no "
                          "segmentation this recipe_id could honestly name",
            }
        )
    knobs: list[int] = []
    for name in ("segment_seconds", "block_segments"):
        value = corpus.get(name)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise DayLogRecipeUnusable(
                {
                    "error": "day-log recipe knob is missing or unusable",
                    "recipe_id": recipe_id,
                    "knob": f"corpus.{name}",
                    "value": value,
                    "reason": "it must be a positive integer; it divides the epoch grid "
                              "and caps a block, and neither has a meaning at 0 or below",
                }
            )
        knobs.append(value)
    return knobs[0], knobs[1]


@dataclass
class Segment:
    seg_id: str
    t_start: str
    t_end: str
    caption: list[str] = field(default_factory=list)
    asr: list[dict[str, Any]] = field(default_factory=list)   # {spk, text, t}
    ocr: list[str] = field(default_factory=list)
    quality: Optional[float] = None   # C2 v0 has no quality field yet; None = not scored
    # The zone the CAPTURING DEVICE reported for the records in this bucket (C2
    # source.device_tz). None when no contributing record carried one, which is what makes
    # the renderer fall back to the profile's home_tz. This is the field that makes anchor
    # lines correct for a day spent in another zone (D17).
    tz: Optional[str] = None

    def is_empty(self) -> bool:
        return not (self.caption or self.asr or self.ocr)


@dataclass
class Block:
    block_id: str
    seg_ids: list[str]
    text: str
    anchors: dict[str, Any]
    quality: Optional[float] = None


@dataclass
class DayLog:
    window_id: str
    user_id: str
    home_tz: str          # the FALLBACK zone actually used (see daylog_body)
    segments: list[Segment]
    blocks: list[Block]


def _parse_ts(raw: str) -> datetime:
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _bucket_index(t: datetime, segment_seconds: int) -> int:
    """CHANGE 2: the GLOBAL epoch grid.

    ``floor(epoch_seconds / segment_seconds)`` — no window origin anywhere in the
    expression. The window start is not a parameter of this function on purpose: a
    Tuesday record in Friday's window would produce a large negative index against a
    window origin, and the bucket a moment falls in must not depend on which window
    happened to collect it.
    """
    return int(t.timestamp() // segment_seconds)


# --- CHANGE 3: one dialect per record, latest ingest_time wins ---------------------


def dialect_key(record: dict[str, Any]) -> tuple[str, str, str]:
    """The identity of the UNIT a record is a dialect OF: ``(chunk_id, kind, discriminator)``.

    ``discriminator`` is the within-chunk discriminator surfaced on C2 (2026-07-27) —
    which of a chunk's several records this is (a keyframe index, an ocr record beside its
    caption). Absent and ``""`` are the same 1:1 case and must key identically, or a
    producer that starts emitting the field would double-count against its own earlier
    records.

    A record with no ``source.chunk_id`` cannot be grouped with anything, so it keys on
    its own ``record_id`` and always survives. Schema-valid C2 always has a chunk_id; this
    is here so that a defect upstream loses a GROUPING, never a record.
    """
    source = record.get("source") or {}
    chunk_id = source.get("chunk_id")
    kind = (record.get("content") or {}).get("kind") or ""
    discriminator = record.get("discriminator") or ""
    if not chunk_id:
        return (f"\x00record_id:{record.get('record_id')}", kind, discriminator)
    return (chunk_id, kind, discriminator)


def select_dialects(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one record per ``(chunk_id, kind, discriminator)`` — latest ``updated_at``
    wins (D27 moved the axis; the v0 kind/discriminator key dies with the v2 renderer
    at WP-E2).

    ``rows`` are the store's window-range rows: ``{"seq", "updated_at", "record"}``.
    ``seq`` (the sqlite rowid) is the TIEBREAK and it is not optional: ``updated_at`` is
    minted at second granularity, so a whole data-processing flush — or two consecutive
    heals — lands inside one second and "latest wins" would otherwise be decided by
    dict iteration order. rowid is the same stable tiebreak every other ordered read in
    this service uses, and the D27 upsert keeps it stable across re-POSTs on purpose.

    Input order is PRESERVED for the survivors — the caller hands rows in event-time order
    and the renderer's within-bucket list order depends on it.
    """
    winner: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = dialect_key(row["record"])
        current = winner.get(key)
        if current is None or (row["updated_at"], row["seq"]) > (
            current["updated_at"], current["seq"]
        ):
            winner[key] = row
    kept = {row["seq"] for row in winner.values()}
    return [row for row in rows if row["seq"] in kept]


# --- the build ---------------------------------------------------------------------


def build_daylog(
    records: list[dict[str, Any]],
    *,
    window_id: str,
    user_id: str,
    home_tz: str,
    segment_seconds: int = DEFAULT_SEGMENT_SECONDS,
    block_segments: int = DEFAULT_BLOCK_SEGMENTS,
) -> DayLog:
    """Bucket C2 records into ~10 s segment rows, then group consecutive non-empty
    segments into ~2 min scene blocks.

    Every record handed in is a MEMBER — membership was decided on the ingest axis by the
    range query, and there is no event-time filter here (CHANGE 1). Records are still
    ATTRIBUTED by ``t_start``: the join is a time-window join, not a per-chunk one,
    because audio chunks are VAD-carved (5–30 s) and video captions are per-keyframe, so
    one bucket gathers every record (or diarized sub-span) whose ``t_start`` lands in it.
    """
    buckets: dict[int, Segment] = {}

    def seg_for(idx: int) -> Segment:
        if idx not in buckets:
            start = idx * segment_seconds
            end = start + segment_seconds
            buckets[idx] = Segment(
                # seg_id is assigned after the buckets are sorted (see below): it is the
                # ORDINAL in the day-log, not the epoch index, which at 10 s buckets is a
                # nine-digit number that would blow the {:05d} width immediately.
                seg_id="",
                t_start=datetime.fromtimestamp(start, tz=timezone.utc).isoformat(),
                t_end=datetime.fromtimestamp(end, tz=timezone.utc).isoformat(),
            )
        return buckets[idx]

    def note_tz(seg: Segment, rec: dict[str, Any]) -> Segment:
        """Stamp the bucket with the capturing device's zone (first writer wins).

        A ~10 s bucket cannot straddle a real zone change, so first-wins is not a lossy
        choice here; it just avoids re-deciding per contributing record."""
        if seg.tz is None:
            device_tz = (rec.get("source") or {}).get("device_tz")
            if device_tz:
                seg.tz = device_tz
        return seg

    for rec in records:
        content = rec.get("content", {})
        kind = content.get("kind")
        text = (content.get("text") or "").strip()
        subsegs = content.get("segments") or []
        if kind == "transcript" and subsegs:
            # Diarized sub-spans land in their OWN buckets by their OWN t_start, never by
            # the parent chunk's — a 30 s VAD chunk spans three buckets and its speech
            # must be anchored where it was actually said.
            for sub in subsegs:
                sub_text = (sub.get("text") or "").strip()
                if not sub_text:
                    continue
                st = _parse_ts(sub["t_start"])
                note_tz(seg_for(_bucket_index(st, segment_seconds)), rec).asr.append(
                    {"spk": sub.get("speaker"), "text": sub_text, "t": sub["t_start"]})
            continue
        if not text:
            continue
        t0 = _parse_ts(rec["t_start"])
        seg = note_tz(seg_for(_bucket_index(t0, segment_seconds)), rec)
        if kind == "transcript":
            seg.asr.append({"spk": None, "text": text, "t": rec["t_start"]})
        elif kind == "ocr":
            seg.ocr.append(text)
        else:  # caption | text
            seg.caption.append(text)

    segments = [buckets[i] for i in sorted(buckets)]
    for ordinal, seg in enumerate(segments):
        seg.seg_id = f"{window_id}_s{ordinal:05d}"
    non_empty = [s for s in segments if not s.is_empty()]

    # A block is a run of TEMPORALLY ADJACENT segments (≤ block_segments long); a
    # camera-off gap starts a new block, so one anchor line never spans hours of silence
    # (the scene-boundary rule, gap-only in v0). This is also what keeps a late-uploaded
    # backlog honest: it is temporally distant from today's capture, so it forms its own
    # blocks with its own anchors rather than being welded onto them.
    max_gap_s = 6 * segment_seconds
    blocks: list[Block] = []
    group: list[Segment] = []
    for seg in non_empty:
        if group:
            gap = (_parse_ts(seg.t_start) - _parse_ts(group[-1].t_end)).total_seconds()
            if len(group) >= block_segments or gap > max_gap_s:
                blocks.append(_render_block(window_id, home_tz, len(blocks), group))
                group = []
        group.append(seg)
    if group:
        blocks.append(_render_block(window_id, home_tz, len(blocks), group))
    return DayLog(window_id=window_id, user_id=user_id, home_tz=home_tz,
                  segments=segments, blocks=blocks)


def _block_zone(home_tz: str, group: list[Segment]) -> ZoneInfo:
    """The zone this block's anchor line is written in (D17).

    Preference order — device-reported, then the user's profile fallback:
      1. the ``device_tz`` the CAPTURING DEVICE reported for the block's first segment
         THAT CARRIES ONE: the only value that is right when the user was travelling,
         because it says where they physically were at that moment;
      2. ``home_tz`` — the C12 profile zone, for records captured before clients reported
         a zone, or by a device that can't determine one.

    So it is the first ZONE-BEARING segment that decides, not simply the first segment.
    Zone-less segments are SKIPPED rather than ending the search, because a bucket is only
    stamped by a contributing record that actually reported a ``device_tz`` (``note_tz``) —
    a block whose leading segments came from silent devices still deserves the zone the
    block's later, zone-reporting segments name, rather than being dropped to the profile
    fallback by the accident of which segment happened to be first.

    Only ONE zone is ever considered, though: the scan stops at the first one it finds,
    whether or not that value turns out to be usable. An unknown or garbage zone id must
    never sink a whole night's training run, so an unresolvable value DEGRADES to the
    profile fallback rather than raising — and it degrades to ``home_tz``, NOT to a later
    segment's zone (note the ``break``, not a ``continue``), because a device that reported
    a zone at all has made a claim about where the wearer was, and silently substituting a
    different device's claim would be a guess dressed as a reading.
    """
    for seg in group:
        if seg.tz:
            try:
                return ZoneInfo(seg.tz)
            except (ZoneInfoNotFoundError, ValueError):
                break  # bad id from a device — fall through to the profile zone
    return ZoneInfo(home_tz)


def _render_block(window_id: str, home_tz: str, index: int, group: list[Segment]) -> Block:
    """v0 labeled-lines renderer; anchors written IN the text (never metadata-only).
    Times are rendered in the WEARER'S timezone — pairing the local date with UTC clock
    readings would anchor a moment up to a day away from the event."""
    zone = _block_zone(home_tz, group)
    start_local = _parse_ts(group[0].t_start).astimezone(zone)
    end_local = _parse_ts(group[-1].t_end).astimezone(zone)
    local_date = start_local.date().isoformat()
    captions = [c for s in group for c in s.caption]
    heard = [f"{a['spk'] or 'someone'}: {a['text']}" if a.get("spk") else a["text"]
             for s in group for a in s.asr]
    world_text = [o for s in group for o in s.ocr]
    t0 = start_local.strftime("%H:%M")
    t1 = end_local.strftime("%H:%M")
    lines = [f"On {local_date}, around {t0}–{t1} local time:"]
    if captions:
        lines.append("Scene: " + " ".join(captions))
    if heard:
        lines.append("Heard: " + " | ".join(heard))
    if world_text:
        lines.append("World text (OCR): " + " | ".join(world_text))
    scored = [s.quality for s in group if s.quality is not None]
    return Block(
        block_id=f"{window_id}_b{index:04d}",
        seg_ids=[s.seg_id for s in group],
        text="\n".join(lines),
        anchors={"date": local_date, "place": None},
        quality=min(scored) if scored else None,
    )


# --- the C10 body ------------------------------------------------------------------


def daylog_fingerprint(daylog: DayLog) -> str:
    """A stable content hash of the day-log — C10's ``content_fingerprint``.

    Computed BY WHOEVER RENDERS and only ever compared to ITSELF across runs (it is a
    journal stage key, not a cross-backend equality claim). The serialization is
    deliberately byte-identical to continuum's ``daylog_client.daylog_fingerprint`` —
    same four keys, same ``sort_keys=True, default=str`` — so that while continuum's local
    path still exists the two can be compared directly instead of only within a backend.
    ``home_tz``/``recipe_id``/``daylog_format_version`` are NOT in it for the same reason:
    it hashes the day-log CONTENT, and the cache re-materializes on a format/recipe change
    by comparing those fields directly.
    """
    payload = json.dumps(
        {"window_id": daylog.window_id, "user_id": daylog.user_id,
         "segments": [asdict(s) for s in daylog.segments],
         "blocks": [asdict(b) for b in daylog.blocks]},
        sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def daylog_body(daylog: DayLog, *, window: dict[str, Any], recipe_id: str) -> dict[str, Any]:
    """The C10 v1 day-log fetch body.

    ``t_start``/``t_end`` are the WINDOW's bounds — i.e. the ingest-time range that
    decided membership, not the extent of the rendered content, which can start days
    earlier. ``home_tz`` records THE FALLBACK ZONE ACTUALLY USED, which is the D17
    follow-up this field closes: without it, a night rendered under the wrong profile zone
    is unfalsifiable after the fact.

    ONE TIME AXIS, TWO SPELLINGS, ON PURPOSE — know this before comparing anything here.
    The window's ``t_start``/``t_end`` are storage-minted and carry ``db._TS_FMT``'s
    trailing ``Z`` (``2026-07-24T02:00:00Z``); every segment bound is
    ``datetime.isoformat()`` and carries the numeric offset instead
    (``2026-07-21T09:00:00+00:00``). Both are RFC3339 UTC and name the same instants, but
    they are NOT interchangeable as strings: ``+`` (0x2B) sorts below ``Z`` (0x5A), so a
    lexicographic compare that crosses the two forms is wrong precisely at equality — the
    same moment compares "earlier" when spelled with the offset. Within one form ordering
    is still chronological, so range clauses on window bounds are unaffected; anything
    that compares a SEGMENT bound against a WINDOW bound must parse both first
    (``_parse_ts``). The spellings are deliberately not unified: continuum's renderer also
    spells segment bounds with ``isoformat()``, and the M9 parity diff's D7 check compares
    those payloads directly, so respelling storage's side would break a passing check to
    buy cosmetics.
    """
    return {
        "contract": "C10",
        "version": "1",
        "user_id": daylog.user_id,
        "window_id": daylog.window_id,
        "t_start": window["t_start"],
        "t_end": window["t_end"],
        "daylog_format_version": DAYLOG_FORMAT_VERSION,
        "recipe_id": recipe_id,
        "home_tz": daylog.home_tz,
        "segments": [asdict(s) for s in daylog.segments],
        "blocks": [asdict(b) for b in daylog.blocks],
        "content_fingerprint": daylog_fingerprint(daylog),
    }


def materialize_daylog(
    store: "Store",
    user_id: str,
    window_id: str,
    *,
    recipe_id: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Get-or-build the C10 day-log for ``(user_id, window_id)``. None == no such window.

    THE SEGMENTATION KNOBS ARE NOT PARAMETERS OF THIS FUNCTION. They are read from the
    registry entry for ``recipe_id`` (``recipe_segmentation``), which is what makes the
    ``recipe_id`` on the body a fact about the render rather than a label beside it; a
    caller that could pass knobs alongside a recipe id could re-open exactly the gap this
    closes. Callers that genuinely want a shape no recipe describes call ``build_daylog``
    directly and get no C10 stamp for it.

    ON DEMAND AT FETCH, then cached — there is no scheduler in this service and adding one
    would be a new runtime dependency for a prototype whose only caller already asks for
    the day-log exactly once per window. The cache is keyed ``(user_id, window_id)`` and is
    re-materialized when any of the three RENDER INPUTS recorded on the row no longer match
    the current ones — ``daylog_format_version``, ``recipe_id`` and ``home_tz`` — so a
    format bump, a recipe re-pin or a corrected profile zone can never serve an old dialect
    out of the cache. ``home_tz`` belongs in that comparison for exactly the same reason as
    the other two: it decides the anchor line's local date whenever a record carries no
    ``device_tz``, so a day-log rendered under an operator's typo'd zone would otherwise
    keep serving the wrong date forever after the profile was corrected.

    A window's content is stable once it is open: ``t_end`` is fixed at open time and sits
    ``delta`` behind the wall clock precisely so that every write with an ``ingest_time``
    below it has committed. The one way a materialized day-log can go stale is a
    same-version reprocess, which rewrites a record's text in place while DELIBERATELY
    preserving its ``ingest_time`` — and that is invalidated at the write, in
    ``Store.put_context``, rather than hoped about here.

    The profile is required to BUILD and not to SERVE: a cached day-log already records
    the zone it was rendered under, so deleting a profile afterwards does not retroactively
    make it unreadable — hence the currency check compares zones only when there IS a
    current one to compare against. The recipe is read on the same terms: a cached body is
    still served when the registry can no longer resolve the id it was rendered under,
    because nothing about that row became less true.
    """
    window = store.get_window(user_id, window_id)
    if window is None:
        return None
    recipe_id = recipe_id or daylog_recipe_id()

    cached = store.get_daylog(user_id, window_id)
    profile = store.get_profile(user_id)
    if (
        cached is not None
        and cached["daylog_format_version"] == DAYLOG_FORMAT_VERSION
        and cached["recipe_id"] == recipe_id
        and (profile is None or cached["home_tz"] == profile["home_tz"])
    ):
        return json.loads(cached["body_json"])

    if profile is None:
        raise DayLogRefused(
            {
                "error": "no profile",
                "user_id": user_id,
                "window_id": window_id,
                "reason": "home_tz is the fallback zone for records that carry no "
                          "device_tz, it is declared and never inferred, and there is no "
                          "server-side default timezone — so this user is not "
                          "schedulable until a profile is written",
            }
        )

    # Resolved HERE and not above the cache check, on the same terms as the profile: the
    # registry is required to BUILD, not to SERVE.
    segment_seconds, block_segments = recipe_segmentation(recipe_id)
    rows = store.list_context_by_updated(user_id, window["t_start"], window["t_end"])
    kept = select_dialects(rows)
    daylog = build_daylog(
        [row["record"] for row in kept],
        window_id=window_id,
        user_id=user_id,
        home_tz=profile["home_tz"],
        segment_seconds=segment_seconds,
        block_segments=block_segments,
    )
    body = daylog_body(daylog, window=window, recipe_id=recipe_id)
    store.put_daylog(body)
    return body
