"""Day-log fetch — the C10-evolved read.

The lean loop's second verb: continuum asks for "the day-log for (user, window)"
and gets the rendered segment/block day-log back. It does NOT build the day-log
itself — that is storage's job (a scheduled materialization over C2, served by
the evolved C10). This client is where that boundary lives.

Two implementations, one interface:

  LocalDayLogClient   builds the day-log HERE — records from a provider (a synthetic
                      day, or the `/context` range read) through this service's own
                      `build_daylog` + renderer.
  HttpDayLogClient    GETs the already-materialized day-log from storage
                      (`GET /training/daylog?user_id=&window_id=`, C10 v2).

The `RecordProvider` seam is why the local client can stand in for storage without
continuum knowing which it is talking to: records→day-log is fully behind the
interface. **The local path is retained deliberately** — it is the parity reference
the storage-side differential diff is measured against (storage CHARTER M9), and two
independent renderers are the only thing that can catch a renderer defect. Which
backend runs is a settings choice (`CONTINUUM_STORAGE_CLIENTS`), never a code change
in the cycle.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any, Callable, Protocol

from ..daylog import DayLog, build_daylog, corpus_blocks
from ..daylog import Block, Segment
from ..window import Window
from ._http import request_json

RecordProvider = Callable[[Window], list[dict[str, Any]]]


def daylog_fingerprint(daylog: DayLog) -> str:
    """A stable content hash of the day-log.

    The cycle keys its daylog stage on this. Hashing the day-LOG rather than the
    raw records is both correct and forward-looking: two different record sets
    that render to the same day-log are legitimately the same night's input, and
    when storage owns materialization the raw records never reach continuum at
    all — only the day-log does."""
    payload = json.dumps(
        {"window_id": daylog.window_id, "user_id": daylog.user_id,
         "segments": [asdict(s) for s in daylog.segments],
         "blocks": [asdict(b) for b in daylog.blocks]},
        sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


class DayLogClient(Protocol):
    def fetch_daylog(self, win: Window) -> DayLog:
        """The rendered segment/block day-log for this window."""

    def eligible_blocks(self, daylog: DayLog, quality_min: float) -> list[Block]:
        """The blocks that pass the amplification quality gate."""

    def render(self, daylog: DayLog, outdir) -> dict[str, str]:
        """Materialize the day-log as trainer-seam files (segments/blocks/day.txt)."""

    def fingerprint(self, daylog: DayLog) -> str:
        """A stable content hash, for the cycle's idempotency key."""


class LocalDayLogClient:
    """Local backend: materialize the day-log from a record provider, in-process.

    Segmentation is fixed at construction because the day-log's shape is
    recipe-versioned (storage will serve a "recipe-versioned format" per the
    storage charter). The local client reproduces exactly the shape the pre-2c
    cycle built — same buckets, same blocks, same rendered bytes.

    There is nothing here to version-check, and that is not an omission: this
    client RENDERS rather than fetches, under the knobs it was handed and this
    build's renderer, so the recipe and the format cannot disagree with themselves.
    The check `HttpDayLogClient` performs exists because a fetch crosses a service
    boundary where the two pins are set independently.
    """

    def __init__(self, record_provider: RecordProvider, *,
                 segment_seconds: int = 10, block_segments: int = 12):
        self._records = record_provider
        self.segment_seconds = segment_seconds
        self.block_segments = block_segments

    @classmethod
    def from_records(cls, records: list[dict[str, Any]], *,
                     segment_seconds: int = 10, block_segments: int = 12) -> "LocalDayLogClient":
        """A client backed by a fixed record list — the shape a test or a
        single-window run holds records already in hand."""
        return cls(lambda _win: records, segment_seconds=segment_seconds,
                   block_segments=block_segments)

    def fetch_daylog(self, win: Window) -> DayLog:
        return build_daylog(self._records(win), win,
                            segment_seconds=self.segment_seconds,
                            block_segments=self.block_segments)

    def eligible_blocks(self, daylog: DayLog, quality_min: float) -> list[Block]:
        return corpus_blocks(daylog, quality_min)

    def render(self, daylog: DayLog, outdir) -> dict[str, str]:
        from ..renderer import render_daylog_files
        return render_daylog_files(daylog, outdir)

    def fingerprint(self, daylog: DayLog) -> str:
        return daylog_fingerprint(daylog)


class DayLogUnavailable(RuntimeError):
    """Storage has no materialized day-log for this (user, window).

    Loud on purpose: a night that cannot fetch its day-log must fail and retry,
    never fall through to "no data" — the two are different facts and only one of
    them is allowed to leave the watermark where it is quietly."""


# The day-log DIALECTS this build of continuum can read — storage's
# `DAYLOG_FORMAT_VERSION`, from the consumer's side.
#
# A TUPLE IN CODE, DELIBERATELY NOT AN ENV KNOB. What a deployment may set is which
# recipe is pinned; what it may NOT set is which renderer output this trainer has
# been proven against. `daylog_format_version` bumps when the segment/block shape or
# the rendered BLOCK TEXT changes (ARCHITECTURE §"Day-log: representation vs.
# content", D20) — and that string is the training corpus. An operator who could
# wave a new dialect through with `CONTINUUM_DAYLOG_FORMAT=2` would be doing exactly
# what the field exists to prevent: shipping a corpus change as a config change.
# Widening this set is a code edit, reviewed alongside a re-run of storage's M9
# differential proof, and the set is a TUPLE rather than a single value so a
# deliberate two-dialect transition can be expressed here and nowhere else.
# The set is "2" alone. Storage's slot-walk renderer ships daylog_format_version "2"
# with recipe consolidation-v2.0 (D28), and the acceptance evidence is exactly the
# proof this comment demands: the D20/M9 differential, 31 checks green over both
# window origins (storage/scripts/daylog_parity_diff.out.txt). An older format arriving
# here can only mean a stale or rolled-back storage, and refusing it is the net
# working rather than a hazard.
SUPPORTED_DAYLOG_FORMAT_VERSIONS: tuple[str, ...] = ("2",)


class DayLogDialectMismatch(ValueError):
    """The fetched day-log was rendered under a recipe / format this night did not expect.

    C10 bodies carry `daylog_format_version` and `recipe_id` for one reason: so a
    change in how the day-log is built is ANNOUNCED rather than silent. Reading them
    and refusing is what makes them announcements; ignoring them makes them
    decoration, and a format change then lands as a corpus change nobody sees.

    REFUSE, NOT WARN — the policy, and why:

      * The failure is silent by construction. Both halves of the mismatch produce
        a day-log that parses perfectly and trains perfectly. There is no crash to
        investigate afterwards, and nothing downstream can tell: `recipe_id` is what
        C5 lineage RECORDS the artifact as trained under, so a mismatch does not
        merely mis-train, it mis-LABELS, and the label is the only evidence that
        would have survived.
      * A warning is read after the fact, if ever. Nightly runs unattended. By the
        time anyone greps the log the adapter is in the model directory, published,
        and indistinguishable from an honest one.
      * Refusing is cheap here and nowhere else. This exception leaves the window
        OPEN (nightly does not close it), so the retry after the operator fixes the
        pin gets the SAME `window_id`, the same journal and the same bounds. Nothing
        is lost but tonight's latency; the watermark does not move, so the next
        window is a strict superset. That asymmetry — an unfixable silent mislabel
        against a recoverable delay — is the whole argument.

    THE TWO SIDES ARE CONFIGURED INDEPENDENTLY, which is why this can happen at all:
    continuum pins `CONTINUUM_RECIPE_ID` and storage pins `STORAGE_DAYLOG_RECIPE_ID`.
    Re-pinning one and not the other is a real, ordinary deployment slip (it is the
    deployment step `scripts/seam_check.py` STEP 7c performs deliberately, and the
    blocker `shipped_recipe_defaults()` exists to catch). Storage cannot detect it:
    it renders honestly under its own pin and stamps what it rendered. Only the
    consumer holds both facts, so only the consumer can refuse.
    """


class HttpDayLogClient:
    """Storage backend: the C10-evolved day-log fetch.

    `GET /training/daylog?user_id=&window_id=` returns the whole materialized
    day-log for a window. Addressed by (user_id, window_id) TOGETHER because
    `window_id` is a PER-USER token derived from an instant — two users' windows
    can legitimately share one, so an id-only fetch would be a cross-user read.

    Random access, not a forward cursor: replay re-reads PRIOR windows' day-logs
    through this same call, which is why storage serves any window's day-log and
    not just the open one.

    Note what this client does NOT do: it never parses `window_id`, never derives
    a window, and never builds a day-log. It also does not re-derive
    `content_fingerprint` — the body carries the renderer's own, and that value is
    only ever compared to itself across runs.

    What it DOES do, and this is the whole of continuum's say in the matter: it
    checks the two VERSION STAMPS on the body and refuses a day-log rendered under a
    dialect or a recipe this night is not training under (`DayLogDialectMismatch`).
    That is not a veto over storage's representation — `seg_id` labelling, ordering,
    the fingerprint algorithm and the table schema are storage's to change freely
    (D20) and nothing here looks at them. It is the consumer half of the announcement
    the two fields exist to carry.
    """

    def __init__(self, storage_url: str, *, timeout: float = 60.0,
                 transport=None, recipe_id: str | None = None):
        """`recipe_id` is the recipe THIS NIGHT trains under — the one whose knobs
        the cycle hashes into its stage keys and whose id publish writes into C5.

        It defaults to the configured pin rather than to "no expectation": an
        unset expectation would silently restore the decoration this check exists to
        end, and there is no legitimate caller who wants an unchecked fetch. The
        factory (`clients.day_log_client`) passes the recipe object the cycle
        actually resolved, so the expectation tracks the run and not the environment
        when the two differ."""
        self.storage_url = storage_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport
        if recipe_id is None:
            from ..config import get_settings
            recipe_id = get_settings().recipe_id
        self.recipe_id = recipe_id

    def fetch_daylog(self, win: Window) -> DayLog:
        status, body = request_json(
            "GET", f"{self.storage_url}/training/daylog",
            params={"user_id": win.user_id, "window_id": win.window_id},
            timeout=self.timeout, transport=self.transport, allow_status=(404,))
        if status == 404:
            raise DayLogUnavailable(
                f"storage has no day-log for user {win.user_id!r} window "
                f"{win.window_id!r} — storage owns materialization (C10 v2); "
                "this night cannot run and must retry, it is NOT an empty day")
        return self._to_daylog(win, body)

    def _to_daylog(self, win: Window, body: dict[str, Any]) -> DayLog:
        # Contract check before anything is trusted: a body of the wrong contract or
        # version is a deployment mismatch, and training on a shape we did not agree
        # to is worse than not training tonight.
        if body.get("contract") != "C10" or str(body.get("version")) != "2":
            raise ValueError(
                f"expected a C10 v2 day-log body, got contract={body.get('contract')!r} "
                f"version={body.get('version')!r} — refusing to build a night from it")
        if body.get("user_id") != win.user_id or body.get("window_id") != win.window_id:
            raise ValueError(
                "day-log body addresses a different window than was asked for: "
                f"asked ({win.user_id!r}, {win.window_id!r}), got "
                f"({body.get('user_id')!r}, {body.get('window_id')!r})")
        # The two version stamps, consumed. See `DayLogDialectMismatch` for why this
        # refuses rather than warns. Format first: the dialect decides what the rows
        # and the block text MEAN, so a body in an unknown dialect is not a body we
        # can even judge the recipe of.
        fmt = body.get("daylog_format_version")
        if str(fmt) not in SUPPORTED_DAYLOG_FORMAT_VERSIONS:
            raise DayLogDialectMismatch(
                f"day-log for {win.user_id!r}/{win.window_id!r} was rendered in format "
                f"version {fmt!r}; this build of continuum reads "
                f"{list(SUPPORTED_DAYLOG_FORMAT_VERSIONS)}. A format bump changes the "
                "segment/block shape or the rendered BLOCK TEXT — which IS the training "
                "corpus — so training on it would silently make every number measured to "
                "date incomparable. Refusing: the window stays open, nothing is lost but "
                "tonight. Deploy a continuum that declares this dialect in "
                "SUPPORTED_DAYLOG_FORMAT_VERSIONS (a code change, reviewed with storage's "
                "M9 differential proof re-run), or roll storage back.")
        stamped = body.get("recipe_id")
        if stamped != self.recipe_id:
            raise DayLogDialectMismatch(
                f"day-log for {win.user_id!r}/{win.window_id!r} was rendered under recipe "
                f"{stamped!r}, but this night trains under {self.recipe_id!r}. The two are "
                "pinned INDEPENDENTLY (storage: STORAGE_DAYLOG_RECIPE_ID, continuum: "
                "CONTINUUM_RECIPE_ID), so this is a half-finished re-pin. It cannot be "
                "waved through: the recipe's `corpus` knobs (segment_seconds, "
                "block_segments) shaped the day-log, continuum hashes ITS knobs into the "
                "journal's stage keys, and publish records CONTINUUM's recipe_id in C5 — "
                "so the artifact would be audited as trained under a recipe it was not "
                "trained under, with nothing downstream able to tell. Re-pin the other "
                "side and retry; the window stays open and the retry resumes it.")
        segments = [
            Segment(seg_id=s["seg_id"], t_start=s["t_start"], t_end=s["t_end"],
                    caption=list(s.get("caption") or []),
                    asr=[dict(a) for a in (s.get("asr") or [])],
                    ocr=list(s.get("ocr") or []),
                    quality=s.get("quality"), tz=s.get("tz"))
            for s in body["segments"]]
        blocks = [
            Block(block_id=b["block_id"], seg_ids=list(b["seg_ids"]), text=b["text"],
                  anchors=dict(b.get("anchors") or {}), quality=b.get("quality"))
            for b in body["blocks"]]
        return DayLog(window_id=body["window_id"], user_id=body["user_id"],
                      segments=segments, blocks=blocks,
                      content_fingerprint=body["content_fingerprint"])

    def eligible_blocks(self, daylog: DayLog, quality_min: float) -> list[Block]:
        # The amplification quality gate lives in continuum, not in the day log:
        # low-quality rows are excluded from TRAINING, never from the record.
        return corpus_blocks(daylog, quality_min)

    def render(self, daylog: DayLog, outdir) -> dict[str, str]:
        # Identical trainer-seam materialization: the ported research code reads
        # segments.jsonl / blocks.jsonl / day.txt from disk regardless of who built
        # the rows.
        from ..renderer import render_daylog_files
        return render_daylog_files(daylog, outdir)

    def fingerprint(self, daylog: DayLog) -> str:
        """Storage's own `content_fingerprint`, passed through.

        Re-hashing here would answer a different question ("do OUR bytes match")
        with the same name, and the field is defined as a self-comparison across
        runs. When the renderer changes it moves once and that night re-runs, which
        is correct: the input genuinely changed."""
        if daylog.content_fingerprint:
            return daylog.content_fingerprint
        return daylog_fingerprint(daylog)
