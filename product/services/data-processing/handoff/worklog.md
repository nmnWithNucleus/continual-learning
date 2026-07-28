# Worklog — Data Processing Service

> Per-aspect working record for this service, **newest first**
> ([ORG.md](../../../ORG.md) §Documentation protocol). The board is
> [../HANDOFF.md](../HANDOFF.md); this file is where a finished item goes when it leaves the board.
> Per-workstream detail lives in the `ws-*.md` files beside this one.

## Worklog

### 2026-07-25 (board hygiene) — retired the completed half of `HANDOFF.md §Next`
> review · data-processing

**Was** — `§Next` had become append-only history. Of its items, most were struck-through `DONE`
records or completed-slice notes, and the board could not be read for what was still open.

**Changed** — the completed items were retired here verbatim, so nothing is lost, and the board
was rewritten to carry only genuinely-open work.

**Now** — [../HANDOFF.md](../HANDOFF.md) `§Next` holds the WS-VC cutover gates, the escalations,
and the small follow-ups. It is rewritten in place each session.

**Payoff** — a session picking up this service reads what is open, not what is closed. The text
below stays a quotation under [STYLE.md](../../../STYLE.md) rule 10 rather than being consolidated:
merging a dated record into surrounding prose deletes record to remove duplication.

**The retired items** — *verbatim, a quotation.*

> - **Hardening slice (2026-07-21, WS-H, branch `svc/dp-hardening`) —
>   [handoff/ws-dp-hardening.md](handoff/ws-dp-hardening.md):** all 3 tracked stage-graph
>   review findings closed (slot ownership by construction; mutate overlap chaining;
>   permit-at-dispatch fairness — `INGEST_MODALITY_LIMITS` now production-safe) + opt-in
>   `INGEST_ISOLATION=subprocess`. **MERGED to `main` (`5350f7a`, 2026-07-21); the D16
>   re-drive drill is the remaining gate** (to flip the async production default); the ws
>   file carries the full M0–M8 milestone evaluation (M1 exit needs WER/DER
>   baseline; M2 text/image next unstarted; M7 remaining: backfill tooling,
>   reprocess-by-version drill, retention, supervisor/deploy confirmation with platform)
>   and the sync-retirement evaluation (**KEEP inline** — it is the C8 skeleton; flip the
>   async default via a founders' decision after the re-drive drill).
> - ~~Async `/ingest` (ACK 202 + worker queue)~~ **DONE (2026-07-19, WS-AO, M7-early) —
>   [handoff/ws-async-observability.md](handoff/ws-async-observability.md).** Behind
>   `INGEST_ASYNC` (default off = inline, byte-identical). Async = ACK `202
>   {ok,accepted,chunk_id}` + a bounded worker pool (`ingest_queue.py`), one shared
>   `process_chunk` core (`ingest_core.py`) for inline + worker, `DedupStore.claim_for_async`
>   (finally-released, no orphan), graceful drain on shutdown, transient-retry-then-dead-letter.
>   **Reply shape decided JOINTLY with recording (inter-service wire, OQ4 precedent — recorded
>   in BOTH canvases):** provenance is optional-at-accept; DP `/continuity` additively reports
>   `processed` + `dead_lettered` so recording keeps `dp_acked=1 ⇔ C2 written` and never reads a
>   silent `clean` for a lost chunk. Flipping `INGEST_ASYNC=1` **retires the
>   `RECORDING_HTTP_TIMEOUT=120` mitigation.**
> - ~~Remaining for full M7: durable pending-journal (auto-recovery past a kill / drain-timeout)~~
>   **DONE (2026-07-20, WS-SG) — [handoff/ws-dp-stage-graph.md](handoff/ws-dp-stage-graph.md).**
>   `app/journal.py` (SQLite pending/processed, WAL): async accept is journaled before the 202,
>   startup re-drives every `accepted` row (**kill -9 auto-recovers**), continuity **rehydrates**
>   from the journal (**restart-amnesia / false-`gaps` caveat closed**), and the dedup done-map
>   has a durable backstop. Epochs guard stale-worker writes; bounded re-drive caps a crash-loop.
>   Still M7-proper: dead-letter *backfill* tooling + reprocess-by-version at scale + `processed`
>   retention.
> - **Stage-graph pipeline (2026-07-20, WS-SG):** the modality Processor seam evolved — a
>   modality is now a thin `GraphProcessor` shim over drop-in **stage files** under
>   `app/stages/<modality>/` (`app/stagegraph/` executor: readiness DAG, per-stage metrics,
>   composed `pipeline_version`, best_effort policy, mutate=version_fragment safety). Audio +
>   video ported byte-identically. **Adding OCR / speaker-identity / multi-level captions / bbox
>   enrichment = one new stage file, zero core edits** (see the ws file's drop-in table). The
>   "Processor seam" section below still describes the monolithic `process()` — that remains the
>   public seam (image/text stubs still use it); GraphProcessor is the richer path behind it.
> - ~~D9 `/metrics` + dashboard~~ **DONE (2026-07-19, WS-AO, M8).** `/metrics` (Prometheus text,
>   zero new deps) + `dashboards/data-processing.json`: ingest rate, async queue depth, per-stage
>   + per-modality latency, dedup hits, VAD-empty rate, continuity missing/dup/dead-letter. C8
>   sync latency lands in the same `dp_stage_seconds`/HTTP families. Follow-up: finer
>   intra-pipeline per-stage latency (asr/diarize/…) — the `stage` label already supports it;
>   owned by each modality plugin (additive).
> - **Real audio pipeline stages — BUILT** (WS A, [handoff/ws-audio-pipeline.md](handoff/ws-audio-pipeline.md)):
>   diarization / translation / acoustic-event captioning now fill their stubs behind
>   off-by-default `DIARIZE_BACKEND` / `TRANSLATE_BACKEND`+`TRANSLATE_TARGET` / `ACOUSTIC_BACKEND`
>   switches (`app/audio/`). Default output byte-identical (mock dialect untouched, 38-baseline
>   green). Diarization forks the audio `pipeline_version` (`+diar-*`); translation + acoustic are
>   additive `discriminator`-tagged sidecar records. **Node-7 smoke DONE (2026-07-19, WS-AO): all
>   three real backends ran GREEN end-to-end on a real webm/opus speech chunk (pyannote diarize,
>   whisper-translate, AST acoustic); the smoke found + fixed two real pyannote torch-2.x compat
>   bugs (`weights_only` default; webm decode via ffmpeg pre-decode).** See
>   [handoff/ws-audio-pipeline.md](handoff/ws-audio-pipeline.md). Residual: whisper-translate on a
>   genuine non-English source still unproven; pyannote pin is 3.1.1, the smoke ran 3.3.2. VAD-cut
>   chunk boundaries mean chunks arrive pause-aligned — revisit cross-chunk stitching after
>   real-data experience.
> - Continuity tracker durability (survives restart) + `sequence_conflicts` surfacing beyond the
>   warning log, when multi-replica/serious-scale arrives.
> - ~~Real video processor~~ **DONE (2026-07-19, WS-V)** — `processors/video.py` now runs a real
>   keyframe pipeline (ffmpeg scene-change selection) behind `VIDEO_BACKEND=mock|vlm`; each
>   keyframe gets its own C2 sub-span via the additive `ProcessedUnit.t_start/t_end` hook (OQ14a,
>   honored in `build_c2`, no C2 schema change); OCR woven into the caption (D8). Mock stays the
>   headless default; the `vlm` backend (httpx → OpenAI-compatible VL endpoint) was exercised
>   genuinely against a locally-served Qwen3-VL-8B. See [handoff/ws-video-pipeline.md](handoff/ws-video-pipeline.md).
>   **Independent verification round (2026-07-19, integrator session):** the headline claims of
>   BOTH slices held under adversarial checking (audio default proven byte-identical by hash vs
>   the pre-slice tree; video record_id stability, sub-span math, webm/mp4 decode all confirmed
>   empirically); 4 confirmed video-side defects fixed + regression-tested (**DP suite 72**):
>   vlm placeholder-emission on undecodable chunks now raises for redelivery, partition-invariant
>   head/tail pinning, lenient vision-config numerics. Detail + accepted caveats in both ws
>   worklogs; live E2E re-verified on the restarted fleet.
> - Real **image** processor is still a mock stub (image build owns it, incl. the OQ14b bbox
>   `content.regions[]` C2-additive field the video OCR pass will also want).
> - text/image real pipelines per CHARTER M-order (video landed).
> - ~~**D9 (2026-07-09) ratified — centralized observability**~~ **DONE (2026-07-19, WS-AO, M8):** `/metrics` (Prometheus text, zero new deps) + `dashboards/data-processing.json` — request rate/latency/errors + ingest rate + async queue depth + per-stage/modality latency + dedup/VAD-empty/continuity counts. Emission side only (platform scrapes/provisions). Finer intra-pipeline per-stage latency is the one documented follow-up (additive, per modality plugin).
