"""data-processing service HTTP surface (FastAPI, :8085) — MODALITY-AGNOSTIC core.

POST /ingest  — body = a pushed C1 raw-stream envelope. Validate C1 -> dedup on
                chunk_id -> pull the blob by ref from storage -> dispatch to the
                Processor registered for envelope.modality -> for EACH ProcessedUnit
                it returns, assemble a C2 record and POST it to storage /context ->
                return {ok, record_ids:[...]}. This is the C1 push receiver.

                Two processing modes (INGEST_ASYNC, FROZEN once at startup):
                  * INLINE (default): process inside the request, return
                    {ok, record_ids:[...]} (200). Byte-identical to M0.
                  * ASYNC (M7, arriving early): ACK 202 {ok, accepted, chunk_id} the
                    moment the chunk is claimed, process on a worker pool. Retry safety
                    rides chunk_id dedup + deterministic record_id upserts. A redelivery
                    of an ALREADY-DONE chunk still returns its record_ids (200); an
                    in-flight redelivery re-ACKs 202; a full queue is 503 backpressure.
                Deterministic C1/modality rejections (400/422/501) resolve
                SYNCHRONOUSLY in BOTH modes — never deferred into a silent dead-letter.
GET  /health  — liveness + effective ASR backend + ingest mode.
GET  /metrics — Prometheus text exposition (D9 observability; METRICS_ENABLED).
GET  /continuity              — per-stream break/dup report (ContinuityTracker),
                the check behind "zero silent loss": recording's gap report
                queries it to close the loop across both capture legs. Async /ingest
                ACKs at ACCEPT, so this now also carries `processed` + `dead_lettered`
                so recording tells an in-flight chunk from a lost one.
GET  /continuity/{stream_id}  — one stream's entry (404 unknown).

The core knows nothing about audio/image/video/text: modality behavior lives in
disjoint stage files under ``app/stages/<modality>/`` (the stage registry), so a
future session owns a stage by dropping in one file. One chunk yields exactly ONE
C2 v1 record built from slots (L2/L5, D24); ``pipeline_version`` is resolved from
code alone before any stage runs (L4 — no output-affecting env knob exists).

Model servers (L9): ``app.state.model_clients`` carries one ModelClient per
manifest server (timeouts from the manifest's ``client_timeout_s``); the
supervisor that owns the fleet processes is started by this app's lifespan when
``DP_SUPERVISOR=1`` (operational opt-in — deploys set it; tests never spawn GPUs
by accident).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from . import schemas
from .config import get_settings
from .continuity import ContinuityTracker
from .dedup import DedupStore
from .ingest_core import ProcessingError, process_chunk
from .ingest_queue import IngestQueue, QueueFull
from .journal import Journal
from .metrics import MetricsASGIMiddleware, Metrics
from .model_client import ModelClient
from .models import C1Envelope
from .stagegraph.processor import graph_processor
from .stagegraph.stage import registered_modalities, stages_for
from .storage_client import StorageClient
from .supervisor import Supervisor
from .timeutil import now_iso

logger = logging.getLogger("data-processing")

# Prometheus text exposition content type (format version 0.0.4).
_PROM_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

# (DP_DIALECT_FREEZE and its flip-window logic died with the env-flippable
# dialect model — D26. Dialects live in code; a deploy IS the flip.)


def _default_manifest_path() -> Path:
    """<service>/servers/manifest.json — the L9 fleet manifest."""
    return Path(__file__).resolve().parents[1] / "servers" / "manifest.json"


def _manifest_path() -> Path:
    return Path(os.getenv("DP_MANIFEST", str(_default_manifest_path())))


def _supervisor_enabled() -> bool:
    """Operational opt-in: run.sh / deploy set DP_SUPERVISOR=1 so the service owns
    the fleet; tests and ad-hoc runs never spawn model servers by accident."""
    return os.getenv("DP_SUPERVISOR", "").strip().lower() in ("1", "true", "yes", "on")


def _build_model_clients(manifest_path: Path) -> dict[str, ModelClient]:
    """One ModelClient per manifest server. ``client_timeout_s`` is wired from the
    manifest (it was parsed by nothing before Stage C); remember client call
    timeouts include queue wait — replicas serialize inference."""
    if not manifest_path.exists():
        return {}
    manifest = json.loads(manifest_path.read_text())
    host = manifest.get("host", "127.0.0.1")
    clients: dict[str, ModelClient] = {}
    for server, spec in manifest.get("servers", {}).items():
        endpoints = [f"http://{host}:{r['port']}" for r in spec.get("replicas", [])]
        if not endpoints:
            continue
        clients[server] = ModelClient(
            server, endpoints, spec.get("expected_identity"),
            call_timeout_s=float(spec.get("client_timeout_s", 120.0)),
        )
    return clients


# content.text char-length distribution (dp_content_chars): captions land ~150-350,
# an OCR digest 0-~1300, the whole-block budget caps ~1320 @60s — so bucket edges span
# short caption → full-budget OCR.
_CHAR_BUCKETS: tuple[float, ...] = (0, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096)
# Per-chunk frame-delta peak (0..255): edges pinned to the design's class thresholds —
# the deterministic floor (2), IDLE ceiling (8), LAYOUT floor (40) — so the histogram
# directly validates the idle assumption from day one (design D-04 / D-07).
_DELTA_BUCKETS: tuple[float, ...] = (2, 4, 8, 16, 24, 40, 64, 128, 255)
# OCR read events selected per chunk — a small integer distribution (cap is
# VIDEO_OCR_MAX_EVENTS, default 3).
_OCR_EVENT_BUCKETS: tuple[float, ...] = (0, 1, 2, 3, 4, 6, 8, 12)


def _dp_route_template(path: str) -> str:
    """Collapse variable path segments so HTTP-metric label cardinality is bounded to
    one series per ROUTE, not per stream_id."""
    if path.startswith("/continuity/"):
        return "/continuity/{stream_id}"
    return path


def _setup_metrics(app: FastAPI, metrics: Metrics) -> None:
    """Declare the DP metric families + register pull-time gauge sources (queue depth,
    continuity aggregates read live at scrape time)."""
    metrics.declare_counter(
        "dp_ingest_total", "C1 /ingest outcomes.", ["modality", "result"],
    )  # result: accepted | processed | deduped | duplicate | rejected
    metrics.declare_counter("dp_dedup_hits_total", "Redeliveries served from the dedup map.")
    metrics.declare_counter(
        "dp_ingest_retries_total", "Worker transient-failure retries.", ["modality"],
    )
    metrics.declare_counter(
        "dp_dead_letter_total", "Chunks dead-lettered (no C2 written).", ["modality"],
    )
    metrics.declare_counter(
        "dp_vad_empty_total", "Audio chunks whose transcript was empty (VAD-gated silence).",
        ["modality"],
    )
    metrics.declare_histogram(
        "dp_stage_seconds", "Per-stage processing latency (seconds).", ["modality", "stage"],
    )
    # Stage-graph: per-STAGE latency (asr/diarize/translate/acoustic, keyframes/captions,
    # and every future drop-in stage) + per-stage failures/skips — the intra-pipeline
    # granularity the coarse dp_stage_seconds{stage=process} couldn't show.
    metrics.declare_histogram(
        "dp_graph_stage_seconds", "Per-graph-stage latency (seconds).", ["modality", "stage"],
    )
    metrics.declare_counter(
        "dp_graph_stage_failures_total", "Graph stage failures/skips.",
        ["modality", "stage", "reason"],
    )

    # ---- WS-VC screen-video observability (§8) --------------------------------------
    # The metric NAMES + label sets are frozen by §8 so WS-B/C/D emit against them from
    # day one; this is the single declaration site (as it already is for the graph-stage
    # families above). Two tiers:
    #   * PARENT-side — emitted by ingest_core's per-unit loop, where `metrics` is in
    #     scope even under INGEST_ISOLATION=subprocess (finding #15). Seeded to zero
    #     below so rate() is well-defined from process start (no missing-series gap).
    #   * STAGE-side — emitted from inside the clip stages; blind under subprocess
    #     isolation by design. Declared here so the families exist from t=0; the labelled
    #     ones surface on first emit (their label values are the stages' to choose).
    metrics.declare_counter(
        "dp_units_total", "C2 units durably written, by modality + content kind.",
        ["modality", "kind"],
    )
    metrics.declare_histogram(
        "dp_content_chars", "content.text length (chars) of each written unit.",
        ["modality", "kind"], buckets=_CHAR_BUCKETS,
    )
    metrics.declare_counter(
        "dp_empty_output_total",
        "Units whose content.text was empty/whitespace — any modality (video idle "
        "screens, VAD-silent audio, an OCR pass that found nothing legible).",
        ["modality", "kind"],
    )
    # (dp_partial_write_total is gone: ONE atomic POST per chunk (L6) makes a
    # partial write structurally impossible — the counter had nothing to count.)
    # Stage-side families (label sets verbatim from §8; bare where §8 lists them bare).
    metrics.declare_counter(
        "dp_video_parse_fallback_total",
        "Clip / OCR reply parse-ladder fallbacks, by pack and ladder step.",
        ["pack", "step"],
    )
    metrics.declare_counter(
        "dp_video_truncated_total",
        "Outputs truncated at the char/token budget, by pass (caption | ocr).", ["pass"],
    )
    metrics.declare_histogram(
        "dp_video_delta_peak",
        "Per-chunk frame-delta peak cell value (0..255) — validates the idle assumption.",
        buckets=_DELTA_BUCKETS,
    )
    metrics.declare_histogram(
        "dp_video_ocr_events", "OCR read events selected per chunk.",
        buckets=_OCR_EVENT_BUCKETS,
    )
    metrics.declare_counter(
        "dp_caption_ungrounded_quote_total",
        "Caption named-string spans absent from the chunk's OCR text (the grounding "
        "safety counter, D-09). NOTE: WS-H owns the scorer + the widening to all named "
        ">=4-char strings — coordinate the name there before it forks.",
    )
    metrics.declare_counter(
        "dp_ocr_redactions_total", "OCR spans deterministically redacted as secrets (D-07).",
    )
    metrics.declare_counter(
        "dp_video_scenario_mismatch_total",
        "device_id prefix disagreed with the configured VIDEO_SCENARIO (D-13).",
        ["expected", "seen"],
    )
    metrics.declare_counter(
        "dp_ocr_frame_errors_total",
        "Per-frame OCR errors absorbed (>50% of a chunk's frames erroring raises).",
    )

    # Seed the PARENT-side counters to zero for every registered stage's slot, so
    # a scrape before any traffic already shows the series (a missing series reads
    # as "never happened"; a 0 reads as "0 so far" — the honest state, and it
    # keeps rate() gap-free at process start). ``kind`` now labels the SLOT name
    # (the per-kind record model died with C2 v0).
    for modality in registered_modalities():
        try:
            slot_names = [s.slot_name for s in stages_for(modality)]
        except Exception:  # a modality that can't be introspected right now — skip it
            continue
        for slot_name in slot_names:
            metrics.inc("dp_units_total", {"modality": modality, "kind": slot_name},
                        amount=0.0)
            metrics.inc("dp_empty_output_total",
                        {"modality": modality, "kind": slot_name}, amount=0.0)

    # The UNLABELLED stage-side counters carry a single series each — no label values to
    # guess — so they too can be shown at zero from t=0 (WS-F EXIT: "all new counters
    # visible on /metrics at zero before any traffic"; a declared-but-never-inc'd counter
    # renders NOTHING in this registry). Under INGEST_ISOLATION=subprocess the real
    # increments happen in the child and are blind here, so the parent-side value stays
    # 0 — documented, and still the honest parent view. The LABELLED stage-side families
    # (parse_fallback{pack,step}, truncated{pass}, scenario_mismatch{expected,seen})
    # genuinely cannot be pre-seeded and surface on their first real emit; the histograms
    # (content_chars, delta_peak, ocr_events) render only once observed, by construction.
    for name in ("dp_caption_ungrounded_quote_total", "dp_ocr_redactions_total",
                 "dp_ocr_frame_errors_total"):
        metrics.inc(name, amount=0.0)

    # ---- Pull-time gauges: live state owned by the queue + continuity tracker ------
    def _queue_depth():
        q = getattr(app.state, "ingest_queue", None)
        return q.queued() if q is not None else 0

    def _queue_processing():
        q = getattr(app.state, "ingest_queue", None)
        return q.processing() if q is not None else 0

    metrics.add_gauge_source("dp_ingest_queue_depth",
                             "Chunks waiting in the async ingest queue.", _queue_depth)
    metrics.add_gauge_source("dp_ingest_processing",
                             "Chunks currently being processed by a worker.", _queue_processing)

    def _journal_counts():
        return app.state.journal.counts()

    metrics.add_gauge_source("dp_journal_pending",
                             "Accepted-but-unprocessed chunks in the durable journal.",
                             lambda: _journal_counts()["pending"])
    metrics.add_gauge_source("dp_journal_dead_letter",
                             "Durably dead-lettered chunks awaiting redelivery/triage.",
                             lambda: _journal_counts()["dead_letter"])

    def _continuity_agg():
        """Aggregate continuity counts across streams (bounded cardinality — totals,
        not per-stream series). Recomputed from live tracker state each scrape."""
        report = app.state.continuity.report()
        streams = report["streams"]

        def _width(runs):
            return sum(hi - lo + 1 for lo, hi in runs)

        return {
            "streams": len(streams),
            "missing": sum(_width(s["missing"]) for s in streams),
            "processed": sum(_width(s["processed"]) for s in streams),
            "dead_lettered": sum(_width(s["dead_lettered"]) for s in streams),
            "duplicate_deliveries": sum(s["duplicate_deliveries"] for s in streams),
            "sequence_conflicts": sum(s["sequence_conflicts"] for s in streams),
        }

    metrics.add_gauge_source("dp_continuity_streams", "Observed C1 streams.",
                             lambda: _continuity_agg()["streams"])
    metrics.add_gauge_source("dp_continuity_missing_total",
                             "Total missing (never-delivered) sequences across streams.",
                             lambda: _continuity_agg()["missing"])
    metrics.add_gauge_source("dp_continuity_processed_total",
                             "Total sequences with a C2 durably written.",
                             lambda: _continuity_agg()["processed"])
    metrics.add_gauge_source("dp_continuity_dead_lettered_total",
                             "Total dead-lettered sequences (accepted, never processed).",
                             lambda: _continuity_agg()["dead_lettered"])
    metrics.add_gauge_source("dp_continuity_duplicate_deliveries_total",
                             "Total at-least-once duplicate deliveries across streams.",
                             lambda: _continuity_agg()["duplicate_deliveries"])
    metrics.add_gauge_source("dp_continuity_sequence_conflicts_total",
                             "Total sequence conflicts (one slot, two chunk_ids).",
                             lambda: _continuity_agg()["sequence_conflicts"])

    # ---- Dialect visibility: the active pipeline_version per modality --------------
    # A pull-time gauge (=1 per modality's CURRENT resolved dialect, read at scrape time
    # from live config). Its purpose is fleet-level: a rolling deploy runs replicas
    # resolving two dialects, and a single 2-min day-log block would then mix records
    # from both (daylog.py filters on neither kind nor pipeline_version). One series per
    # (modality, pipeline_version) is exactly the shape the mixed-dialect alert needs.
    #
    #   ALERT (replica-count robust):
    #     count(count by (modality, pipeline_version) (dp_pipeline_dialect)) by (modality) > 1
    #
    # This refines §8's shorthand `count by (modality) (dp_pipeline_dialect) > 1`, which
    # over-fires once there is >1 replica: Prometheus adds an `instance` label per target,
    # so N replicas AGREEING on one dialect already yield N series and would trip the bare
    # count. The inner `count by (modality, pipeline_version)` collapses replicas first, so
    # the alert fires strictly on >1 DISTINCT dialect per modality — the drain-and-replace
    # (never rolling) signal, D-14. Per-modality resolution is guarded: a modality that
    # cannot resolve right now is simply absent, never hiding the others.
    def _pipeline_dialects():
        out = []
        for modality in registered_modalities():
            try:
                pv = graph_processor(modality).pipeline_version()
            except Exception:
                continue  # unresolvable right now (misconfig / half-built graph) — omit
            out.append(((modality, pv), 1))
        return out

    metrics.add_gauge_source(
        "dp_pipeline_dialect",
        "Active pipeline dialect per modality (=1). Alert (replica-robust): "
        "count(count by (modality,pipeline_version) (dp_pipeline_dialect)) by (modality) "
        "> 1 — a rolling deploy mixing dialects in one training window (D-14).",
        _pipeline_dialects, labelnames=["modality", "pipeline_version"],
    )


def _assert_not_offline_eval() -> None:
    """``DP_OFFLINE_EVAL=1`` ⇒ this process MUST NOT serve (WS-H, §11).

    The offline A/B harness (``scripts/prompt_ab.py``) unlocks prompt-pack overrides —
    experimental packs, a fault-injected OCR arm — under this one flag, and it drives
    ``resolve()`` / ``run_graph()`` / ``build_c2`` directly, never FastAPI and never
    ``StorageClient``, so it is structurally incapable of reaching ``/context``. This guard
    closes the mirror hazard: **the flag that enables experiments is the flag that prevents
    serving**, so an eval env leaking into a deployment fails at boot — loudly, before one
    experimental caption can be written to a real corpus under a real dialect."""
    if os.getenv("DP_OFFLINE_EVAL", "").strip().lower() not in ("", "0", "false", "no", "off"):
        raise RuntimeError(
            "DP_OFFLINE_EVAL is set — refusing to build the serving app. This flag unlocks "
            "offline-eval prompt-pack overrides (scripts/prompt_ab.py) and must never be set "
            "on a process that writes to /context. Unset it to serve."
        )


def create_app() -> FastAPI:
    """App factory. Reads env at call time so tests can point STORAGE_URL / flip
    ASR_BACKEND before construction and inject a mock storage transport after."""
    _assert_not_offline_eval()
    settings = get_settings()

    async def _redrive_pending(app: FastAPI, queue: IngestQueue, rows: list) -> None:
        """Startup auto-recovery (M7): a PURE enqueue loop over the journal's re-drive
        set. Continuity visibility already happened (rehydration marked every pending
        row as SEEN before this runs) and the durable attempt accounting + crash-loop
        cap happened inside ``pending_for_redrive`` — this loop only claims + enqueues.
        Runs as a BACKGROUND task with the WAITING submit, so a backlog larger than the
        queue bound drains completely as workers free slots and serving starts
        immediately. Re-driven jobs use CURRENT config (same posture as a redelivery);
        recording never has to notice."""
        from starlette.concurrency import run_in_threadpool as _tp
        redriven = skipped = 0
        for row in rows:
            c1, epoch = row["c1"], row["epoch"]
            chunk_id = c1["chunk_id"]
            try:
                processor = graph_processor(c1["modality"])
            except KeyError:
                # Stage set gone across a restart / mode-switch. Leaving the row
                # 'accepted' would read as PERPETUAL 'recording' (never re-driven,
                # never converges); dead-letter it so recording sees honest 'gaps'
                # + a redelivery re-arms it.
                logger.error("re-drive: no stage set for %r (chunk %s) — dead-lettering",
                             c1["modality"], chunk_id)
                await _tp(app.state.journal.mark_dead_letter, chunk_id,
                          f"no processor for modality {c1['modality']!r}", now_iso(), epoch)
                app.state.continuity.note_dead_letter(c1["stream_id"], c1["sequence"])
                skipped += 1
                continue
            claim = await app.state.dedup.claim_for_async(chunk_id)
            if claim != "claimed":  # already done (journal backstop) or in flight
                continue
            try:
                await queue.submit_wait({
                    "c1": c1, "settings": settings, "processor": processor,
                    "pipeline_version": processor.pipeline_version(),
                    "epoch": epoch,
                    "redrive": True,  # worker charges a per-chunk re-drive attempt
                })
            except asyncio.CancelledError:
                app.state.dedup.release_inflight(chunk_id)  # shutdown mid-re-drive
                raise
            redriven += 1
        if redriven or skipped:
            logger.info("journal re-drive: %d re-enqueued, %d skipped", redriven, skipped)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        from starlette.concurrency import run_in_threadpool as _tp
        journal: Journal = app.state.journal
        # ORDER MATTERS at startup:
        #   1. async mode: pending_for_redrive — durably counts this re-drive attempt and
        #      flips crash-loop suspects (> DP_REDRIVE_MAX_ATTEMPTS) to dead_letter;
        #   2. BOTH modes: rehydrate continuity from the journal — processed + dead +
        #      STILL-PENDING rows all count as SEEN coverage (the keystone: a restart can
        #      never fabricate a gap out of a chunk that is merely waiting to be
        #      re-driven), with the cap-flips from step 1 already visible as dead;
        #   3. async mode: start workers, then re-drive as a background task (pure
        #      enqueue; waits for queue capacity instead of stranding a large backlog).
        # Model-server fleet (L9): the supervisor is an operational opt-in
        # (DP_SUPERVISOR=1 — deploys set it; unit tests never spawn model
        # servers). Clients exist whenever a manifest does — an externally-run
        # fleet (drills) is reachable either way.
        supervisor: Supervisor | None = None
        supervisor_task: asyncio.Task | None = None
        if _supervisor_enabled() and _manifest_path().exists():
            supervisor = Supervisor.from_file(_manifest_path())
            await supervisor.start()
            supervisor_task = asyncio.get_running_loop().create_task(supervisor.run())
            app.state.supervisor = supervisor

        redrive_rows: list = []
        if app.state.ingest_async:
            redrive_rows = await _tp(
                journal.pending_for_redrive, settings.redrive_max_attempts, now_iso()
            )
        app.state.continuity.rehydrate(await _tp(journal.rehydration))
        redrive_task: asyncio.Task | None = None
        if app.state.ingest_async:
            app.state.dedup.reset_inflight()  # clear any claim from a prior loop reuse
            queue = IngestQueue(
                app,
                workers=settings.ingest_workers,
                maxsize=settings.ingest_queue_max,
                max_retries=settings.ingest_max_retries,
                backoff=settings.ingest_retry_backoff,
                modality_limits=settings.ingest_modality_limits,
            )
            queue.start()
            app.state.ingest_queue = queue
            if redrive_rows:
                redrive_task = asyncio.get_running_loop().create_task(
                    _redrive_pending(app, queue, redrive_rows)
                )
        yield
        if app.state.ingest_async:
            if redrive_task is not None and not redrive_task.done():
                redrive_task.cancel()
                await asyncio.gather(redrive_task, return_exceptions=True)
            queue = getattr(app.state, "ingest_queue", None)
            if queue is not None:
                await queue.drain_and_close(settings.ingest_drain_timeout)
                app.state.ingest_queue = None
        if supervisor is not None:
            if supervisor_task is not None and not supervisor_task.done():
                supervisor_task.cancel()
                await asyncio.gather(supervisor_task, return_exceptions=True)
            await supervisor.stop()
        for client in app.state.model_clients.values():
            await client.aclose()

    app = FastAPI(
        title="Nucleus data-processing service",
        version="0.0",
        summary="C1 -> Processor -> C2 capture skeleton for the learn loop.",
        lifespan=lifespan,
    )
    # State constructed HERE (not in lifespan): conftest injects app.state.storage's
    # transport AFTER create_app() but BEFORE the TestClient `with` block, and
    # process_chunk reads app.state.storage per call — so the fake transport is honored.
    app.state.storage = StorageClient(settings.storage_url, timeout=settings.http_timeout)
    # Model clients (L9): one per manifest server; timeouts from the manifest.
    app.state.model_clients = _build_model_clients(_manifest_path())
    # Journal is LAZY (no filesystem touch until first use) — safe at module import.
    app.state.journal = Journal(Path(settings.dp_var_dir) / "dp.db")
    # The journal's processed table backs dedup misses: a redelivery after a restart is
    # answered with the prior record_ids (200) — UNLESS the pipeline dialect for that
    # modality has since changed, in which case the receipt is stale and the honest
    # answer is a reprocess under the new version (version-forward; old records stay).
    # (Ledger semantics stay v0-shaped through Stage C; the L8 claim tree is Stage D.)
    def _current_pv(modality: str):
        try:
            return graph_processor(modality).pipeline_version()
        except Exception:  # unknown modality / stage set gone — can't judge, serve receipt
            return None

    app.state.dedup = DedupStore(
        done_fallback=lambda cid: app.state.journal.processed_record_ids(cid, _current_pv)
    )
    app.state.continuity = ContinuityTracker()
    app.state.ingest_async = settings.ingest_async   # FROZEN at startup (no per-request read)
    app.state.ingest_queue = None
    app.state.metrics = Metrics() if settings.metrics_enabled else None
    if app.state.metrics is not None:
        _setup_metrics(app, app.state.metrics)
        app.add_middleware(MetricsASGIMiddleware, metrics=app.state.metrics,
                           prefix="dp", templatizer=_dp_route_template)

    def _metrics_inc(name: str, labels: dict | None = None) -> None:
        if app.state.metrics is not None:
            app.state.metrics.inc(name, labels)

    @app.get("/health")
    def health() -> dict:
        # /health is a liveness probe, not a frozen contract — it additively reports
        # the effective state: the resolved dialect per modality (so a deploy can
        # confirm which pipeline_version a replica serves before it takes traffic)
        # and whether this process owns the model-server fleet. Per-modality pv
        # resolution is guarded so a half-built graph degrades to None, never 500s
        # the probe.
        versions: dict[str, str | None] = {}
        for modality in registered_modalities():
            try:
                versions[modality] = graph_processor(modality).pipeline_version()
            except Exception:
                versions[modality] = None
        return {
            "ok": True,
            "ingest_mode": "async" if app.state.ingest_async else "inline",
            "pipeline_versions": versions,
            "supervisor": getattr(app.state, "supervisor", None) is not None,
        }

    @app.get("/metrics")
    def metrics_endpoint() -> PlainTextResponse:
        if app.state.metrics is None:
            return PlainTextResponse("# metrics disabled\n", media_type=_PROM_CONTENT_TYPE)
        return PlainTextResponse(app.state.metrics.render(), media_type=_PROM_CONTENT_TYPE)

    @app.post("/ingest")
    async def ingest(request: Request) -> JSONResponse:
        settings = get_settings()

        # ---- Parse + validate the incoming C1 envelope (SYNCHRONOUS in both modes) --
        try:
            c1 = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse(status_code=400, content={"error": "body is not valid JSON"})

        # 1) Authoritative gate: validate against the frozen C1 JSON Schema.
        problems = schemas.validate_c1(c1)
        if problems:
            raise HTTPException(
                status_code=422,
                detail={"error": "C1 schema validation failed", "violations": problems},
            )
        # 2) Mirror check: the pydantic model must agree with the schema.
        C1Envelope.model_validate(c1)

        chunk_id = c1["chunk_id"]
        modality = c1["modality"]

        # ---- Continuity observation (EVERY schema-valid delivery, at ACCEPT) -------
        # Noted here — after the C1 gate, before any return — so ALL paths count:
        # fresh process, dedup fast path, in-flight dup, async accept. Dedup must never
        # silently absorb a break/duplicate signal; an invalid C1 must never register
        # one. Async ACKs here too, so "seen" no longer implies "processed" — the
        # processed/dead_lettered sets (filled at processing time) carry that.
        request.app.state.continuity.note(
            c1["stream_id"], c1["sequence"], chunk_id,
            user_id=c1["user_id"], device_id=c1["device_id"], modality=modality,
            now_iso=now_iso(),
        )

        # ---- Select the stage graph for this modality (SYNCHRONOUS 501, pre-claim) --
        # C1 schema already restricts modality to the enum; a valid modality with no
        # registered stage set is a clean 501, not a crash / a silent dead-letter.
        try:
            processor = graph_processor(modality)
        except KeyError:
            raise HTTPException(
                status_code=501,
                detail={"error": f"no stage set registered for modality {modality!r}"},
            )
        pipeline_version = processor.pipeline_version()

        if request.app.state.ingest_async:
            return await _ingest_async(request, c1, settings, processor, pipeline_version)
        return await _ingest_inline(request, c1, settings, processor, pipeline_version)

    async def _ingest_inline(request, c1, settings, processor, pipeline_version) -> JSONResponse:
        """M0 behaviour, byte-identical: process inside the request, return record_ids."""
        dedup: DedupStore = request.app.state.dedup
        chunk_id = c1["chunk_id"]
        metrics = request.app.state.metrics

        # Dedup (fast path): already-processed chunk_id -> prior record_ids.
        prior = dedup.get(chunk_id)
        if prior is not None:
            logger.info("dedup hit (processed) chunk_id=%s -> %s", chunk_id, prior)
            _metrics_inc("dp_dedup_hits_total")
            _metrics_inc("dp_ingest_total", {"modality": c1["modality"], "result": "deduped"})
            return JSONResponse(content={"ok": True, "record_ids": prior})

        # Serialize concurrent redeliveries of the same in-flight chunk_id.
        lock = await dedup.lock_for(chunk_id)
        async with lock:
            prior = dedup.get(chunk_id)
            if prior is not None:  # resolved while we waited on the lock (in-flight)
                logger.info("dedup hit (in-flight) chunk_id=%s -> %s", chunk_id, prior)
                _metrics_inc("dp_dedup_hits_total")
                _metrics_inc("dp_ingest_total", {"modality": c1["modality"], "result": "deduped"})
                return JSONResponse(content={"ok": True, "record_ids": prior})

            try:
                record_ids = await process_chunk(
                    c1=c1, settings=settings, processor=processor,
                    pipeline_version=pipeline_version, storage=request.app.state.storage,
                    dedup=dedup, metrics=metrics,
                    journal=request.app.state.journal,  # durable receipt (both modes)
                    app_state=request.app.state,
                )
            except ProcessingError as exc:
                # Map the taxonomy back to the exact M0 HTTP status at the boundary.
                raise HTTPException(status_code=exc.http_status, detail=exc.detail)

            request.app.state.continuity.note_processed(c1["stream_id"], c1["sequence"])
            _metrics_inc("dp_ingest_total", {"modality": c1["modality"], "result": "processed"})
            return JSONResponse(content={"ok": True, "record_ids": record_ids})

    async def _ingest_async(request, c1, settings, processor, pipeline_version) -> JSONResponse:
        """ACK 202 the moment the chunk is claimed; a worker processes it."""
        dedup: DedupStore = request.app.state.dedup
        chunk_id = c1["chunk_id"]

        claim = await dedup.claim_for_async(chunk_id)
        if claim == "done":  # redelivery of a completed chunk -> known record_ids
            record_ids = dedup.get(chunk_id) or []
            logger.info("dedup hit (processed) chunk_id=%s -> %s", chunk_id, record_ids)
            _metrics_inc("dp_dedup_hits_total")
            _metrics_inc("dp_ingest_total", {"modality": c1["modality"], "result": "deduped"})
            return JSONResponse(content={"ok": True, "record_ids": record_ids})
        if claim == "inflight":  # already queued/processing -> don't double-enqueue
            _metrics_inc("dp_ingest_total", {"modality": c1["modality"], "result": "duplicate"})
            return JSONResponse(
                status_code=202,
                content={"ok": True, "accepted": True, "chunk_id": chunk_id, "duplicate": True},
            )

        # claimed by us -> journal (durable accept receipt) THEN enqueue for a worker.
        queue: IngestQueue | None = getattr(request.app.state, "ingest_queue", None)
        if queue is None:  # async configured but pool not up (no lifespan) — honest 503
            dedup.release_inflight(chunk_id)
            return JSONResponse(status_code=503,
                                content={"ok": False, "error": "ingest queue not ready"})
        # Durable BEFORE the 202: a crash after the journal commits auto-recovers at
        # startup (re-drive) — the ACK never lies. accept() bumps the row's EPOCH (guards
        # terminal writes against stale workers) and resets a prior dead_letter row on
        # redelivery; the returned prior-row snapshot is the QueueFull restore point.
        # Off the loop: WAL fsyncs never stall it.
        #
        # The claim is released on EVERY exit that isn't a successful enqueue — including
        # a failed durable write (disk-full / lock-contention in accept OR unaccept).
        # Otherwise a raised accept() would strand chunk_id in dedup._inflight with NO
        # pending row and nothing enqueued → every retry ACKs 202-duplicate forever
        # (silent loss + a lying ACK). ``enqueued`` gates the finally: only a chunk truly
        # handed to a worker keeps its claim (the worker owns the release then).
        from starlette.concurrency import run_in_threadpool as _tp
        journal: Journal = request.app.state.journal
        enqueued = False
        try:
            epoch, prior = await _tp(journal.accept, c1, now_iso())
            try:
                queue.submit({
                    "c1": c1, "settings": settings,
                    "processor": processor, "pipeline_version": pipeline_version,
                    "epoch": epoch,
                })
                enqueued = True
            except QueueFull:
                # 503 = "NOT accepted"; the journal must not contradict it. Restore the
                # prior row (a replaced dead_letter keeps its history) or delete a fresh
                # one. If unaccept itself raises, the outer finally still frees the claim.
                await _tp(journal.unaccept, chunk_id, prior)
                _metrics_inc("dp_ingest_total", {"modality": c1["modality"], "result": "rejected"})
                logger.warning("ingest queue full — 503 backpressure on chunk %s", chunk_id)
                return JSONResponse(status_code=503,
                                    content={"ok": False, "error": "ingest queue full"})
        finally:
            if not enqueued:
                dedup.release_inflight(chunk_id)

        _metrics_inc("dp_ingest_total", {"modality": c1["modality"], "result": "accepted"})
        return JSONResponse(status_code=202,
                            content={"ok": True, "accepted": True, "chunk_id": chunk_id})

    @app.get("/continuity")
    def continuity_report(request: Request) -> dict:
        return request.app.state.continuity.report()

    @app.get("/continuity/{stream_id}")
    def continuity_stream(request: Request, stream_id: str) -> dict:
        entry = request.app.state.continuity.report_stream(stream_id)
        if entry is None:
            raise HTTPException(
                status_code=404,
                detail={"error": f"unknown stream_id {stream_id!r}"},
            )
        return entry

    return app


app = create_app()
