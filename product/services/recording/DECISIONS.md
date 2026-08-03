# DECISIONS — recording (service-local register)

> Decisions taken **inside this service's chartered autonomy**, numbered `R-n`, newest first.
> Anything that re-cuts a charter or a contract is escalated via [../../HANDOFF.md](../../HANDOFF.md)
> §Escalations and ratified at the founders' board with a **D-number** in
> [../../DECISIONS.md](../../DECISIONS.md).
>
> **Two of the rows below are founders' decisions, not ours.** They were recorded here under local
> `D-M1-*` numbers before the founders' register existed, which meant the same decision was written
> out in two places. The decision text now lives once, at its D-number; what stays here is
> **recording's implementation of it** — which is genuinely ours.
>
> **Naming note (2026-07-29):** dated rows below mention `/capture/sessions/*` and
> `session_id`/`seq`; the live wire says `/capture/captures/*` and
> `capture_id`/`segment_num` since the nomenclature cleanup
> ([handoff/worklog.md](handoff/worklog.md)). Records keep their contemporaneous wording.
>
> **Stage: PROTOTYPE** ([D19](../../DECISIONS.md)) — these evolve. A decision is superseded by a new
> row, never rewritten to say something different; only *status* changes in place.

---

### R-2 — async `/ingest` reply shape: recording's side of the wire

**Founders' decision: [D16](../../DECISIONS.md)** (ratified 2026-07-19, jointly designed with
data-processing). *Was `D-M1-6`.* **Status: built + tested.** Recording's implementation and its
consequences:

**D-M1-6 — async `/ingest` reply shape (inter-service wire, decided jointly with data-processing 2026-07-19; OQ4 precedent, decide once, record in both
canvases).** DP can now ACK `202 {ok, accepted:true, chunk_id}` with **NO record_ids** (it processes on a worker pool; `INGEST_ASYNC`, off by default).
Recording's implications, all built + tested: *(1) provenance is optional-at-accept* — the emitter already coerced `ack.get("record_ids") or []`, so an
empty list never crashes; *(2) an accept is recorded as `dp_state='accepted'` (`dp_acked=0`), NOT confirmed*, the invariant `dp_acked=1 ⇔ C2 durably
written` is preserved (a 200 with record_ids stays `dp_acked=1, dp_state='processed'`); *(3) the gap report reconciles against DP's additive `/continuity`
`processed` + `dead_lettered` fields*, a chunk DP reports processed is lazily `confirm_chunk`'d (persisted, so a DP restart can't un-confirm it), a
dead-lettered chunk → verdict `gaps`, an accepted-but-unconfirmed chunk → verdict `recording`; *`leg["dp"]` keeps its pinned 5-key shape*
(dead-letter/accepted are sibling leg fields). Net: the "zero silent loss" verdict never reads `clean` for a chunk DP hasn't confirmed. When the fleet sets
`INGEST_ASYNC=1`, *`RECORDING_HTTP_TIMEOUT` reverts to 30* (the 120 s mitigation is retired). *Founders ratified this wire 2026-07-19 (D16)* — the one
ratification condition (a named + drilled re-drive path for accepted-unconfirmed chunks) is satisfied in-slice: `POST /capture/sessions/{id}/redrive` (+
`emitter.redrive_accepted_chunks`, callable on restart / periodically) re-pushes each `dp_state='accepted'` chunk's original C1; DP's dedup makes it
idempotent (a done chunk short-circuits to 200+record_ids → `confirm_chunk` → `clean`; still-pending re-ACKs 202). Detail:
[../data-processing/handoff/ws-async-observability.md](../data-processing/handoff/ws-async-observability.md).
- **DP-side alignment (DP v1 + hardening, merged 2026-07-21):** DP now carries a *durable ingest
  journal* — an accepted chunk survives a DP kill/restart and *auto-recovers on the DP side* (its
  `/continuity` `processed`/`dead_lettered` sets rehydrate from the journal, so a DP restart can
  no longer mis-report intact history as a gap).
- Net: the guarantee is now durable on *both* legs.
- Recording's `/redrive` stays the belt-and-suspenders (and the means to converge a chunk lost
  past DP's drain-timeout / a hard kill, which DP's journal marks re-drivable but does not itself
  re-push to us).
- No recording change needed; the async seam is unchanged (120 tests at the time; *144* as of
  2026-07-27).
- Flipping `INGEST_ASYNC=1` on the fleet remains the open D16 re-drive-drill decision.

### R-1 — client transport: recording's side

**Founders' decision: [D14](../../DECISIONS.md)** (ratified 2026-07-19). *Was `D-M1-5`.*
**Status: built — all three v0 surfaces ship on segmented HTTP upload.** The rationale and the
deferred additive leg, as recorded by this service:

**D-M1-5 — client transport (founders × recording lead, 2026-07-19): segmented HTTP
upload for ALL v0 surfaces** (phone / extension / mac CLI). Rationale: our capture path
is the *archive/training* job, not live viewing — loss-intolerant, offline-resilient,
latency-tolerant, which maps onto segmented upload (the Axon-bodycam/dashcam pattern),
not persistent-socket streaming (the Ring/Nest *live-view* pattern; note those products
run both paths separately). **Continuous streaming ingest is a deferred additive leg**:
a socket receiver (WebSocket/RTSP/SRT per device) → per-stream continuity buffer →
server-side segmenter, terminating in the existing spool→demux→carve→emit machinery —
C1/C2 unchanged by design (C1 deliberately begins *after* transport: "chunks exist").
Build it only when a surface needs sub-segment latency (live-view is out of v0 scope) or
the bodycam firmware demands it; cheaper latency lever first: shrink `SEGMENT_SECONDS`.

---

**Provenance.** Both rows were the `## Pinned decisions & glossary` section of the service canvas
until 2026-07-27, when they moved here and the glossary moved to
[CHARTER.md](CHARTER.md) §Glossary — a glossary is stable reference, not a decision.
