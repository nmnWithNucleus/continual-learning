# Recording — service worklog

> The service's own timeline, **newest first**. New entries are *prepended* under §Worklog
> ([../../../ORG.md](../../../ORG.md) §Documentation protocol). Where things stand today is
> [../HANDOFF.md](../HANDOFF.md); decisions are [../DECISIONS.md](../DECISIONS.md) and the
> founders' [../../../DECISIONS.md](../../../DECISIONS.md). Per-workstream detail is in the
> `ws-*.md` files beside this one.

---

## Worklog

### 2026-07-29 — client-wire nomenclature: `capture_id` + `segment_num`

**Was** — the client wire said `session_id` + `seq`: `session_id` collided with the serve
loop's chat `session_id` (C3/C4), and `seq` read as a sibling of C1's `sequence` when the
two are different counters with different jobs.
**Changed** — renamed across the wire, the ledger schema (with an in-place migration for
old DBs), all three clients, the deno tests, the dashboard, and the current-facing docs:
`session_id` → `capture_id`, `seq` → `segment_num`, `last_seq` → `last_segment_num`,
`missing_seqs` → `missing_segment_nums`, routes `/capture/sessions/*` →
`/capture/captures/*`, metrics `rec_sessions_*` → `rec_captures_*`. The glossary term
*capture session* became *capture*. C1's `sequence` is untouched — a segment can lack a
modality and carve/replay sources have no segments, so per-stream density needs its own
counter (CTO review, field-guide modules 00–04).
**Now** — the two legs read `(capture_id, segment_num)` client-side and
`(stream_id, sequence)` DP-side; no identifier is shared with another service's
vocabulary. Suites: 144 pytest + 18 deno, green. Dated records keep their
contemporaneous names; each ws file carries a dated note saying so.
**Payoff** — the disambiguation trap the glossary used to document is simply gone.
Deliberately not a numbered decision (CTO call): a naming cleanup inside the internal
wire, applied in place. Cost accepted: the node-7 fleet serves the old routes until its
next restart, and open phone pages need one hard refresh after it (the `/ingest` →
`/capture` precedent).

### 2026-07-27 — retired the completed `§Next` items from the canvas

*The board's `§Next` carried five struck-through `DONE` records. Retired here **verbatim** so the
detail survives; the board now carries open items only.*

- ~~Real-phone verification~~ **done 2026-07-18** — CTO's iPhone (Safari, tunnel): two sessions
  7/7 + 9/9 clean; UI leaks + an ASR auto-language hallucination found and fixed same day (ws-B
  worklog).
- *The learn fleet (faster_whisper, `ASR_LANGUAGE=en` via `deploy/learn.env`) + tunnel remain UP
  on node-7 — restarted 2026-07-18 by the computer-capture lead onto the renamed `/capture/*` wire
  (tunnel URL unchanged, `/health` + `/client/` + alias re-verified through it).
- The URL rotates per tunnel restart, so always read it from `var/tunnel_url.txt`; `run_learn.sh
  --status` checks the fleet.*

- ~~Computer capture surfaces~~ **BUILT + reviewed + live-verified 2026-07-18** (this slice —
  ws-E extension, ws-F mac CLI; server needed nothing new, as designed).

- ~~Alpha test~~ **alpha complete 2026-07-19** — the CTO drove all three surfaces per
  [handoff/alpha-runbook.md](alpha-runbook.md), each landing verdict `clean` on real hardware with
  blobs sha256-verified + ffprobe-decoded in storage and real ASR transcripts in `/context`:
  *phone* (iPhone Safari, mic+camera, 4/4), *extension* (Comet, tab video+audio, 7/7 — the run
  that drove the D-E7 pivot), *mac CLI* (real avfoundation screen+mic, 7/7).
- Results in the runbook §Worklog + each ws file.
- The fleet was purged + restarted fresh before the pass so results read from zero; it remains UP
  on node-7.

- **THE capture surfaces ARE done (v0 alpha bar).** ~~Founders' sequenced next: metrics
  emission (D9)~~ *done 2026-07-19 (WS-AO, M6):* `/metrics` (Prometheus text, zero new deps)
  + `dashboards/recording.json` — segments received/emitted/failed, chunks per modality + DP
  state, sessions, client-leg missing/dup, received→emitted latency, downstream retry counts.
  Emission side only (platform scrapes/provisions). Same slice landed the async-ingest seam
  tolerance (D-M1-6 above).

- ~~`/metrics` + dashboard JSON (M6/D9) still owed~~ **done 2026-07-19 (WS-AO).** `/metrics` +
  `dashboards/recording.json` ship now; they light up the moment Platform's shared
  Prometheus/Grafana scrapes + provisions them (emission side is complete + tested).
