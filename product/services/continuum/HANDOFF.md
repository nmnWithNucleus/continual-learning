# HANDOFF — Continuum service board

> Single touch-point for any agent (or human) picking up work on this service.
> Read [CHARTER.md](CHARTER.md) first (mission/scope/interfaces), then this file.
>
> **This is a board, not a log.** It is rewritten in place to describe *today*; nothing accumulates
> here ([../../ORG.md](../../ORG.md) §Documentation protocol).
>
> | Looking for | Go to |
> |---|---|
> | *How did we get here?* | [handoff/worklog.md](handoff/worklog.md) — newest first |
> | *What did this service decide?* | [DECISIONS.md](DECISIONS.md) — local register (`C-n`) |
> | *What did the founders decide?* | [../../DECISIONS.md](../../DECISIONS.md) — the `D-n` register |
> | *Per-workstream detail* | the `ws-*.md` files in [handoff/](handoff/) |

**Stage: PROTOTYPE** ([D19](../../DECISIONS.md)) · **Status:** ✅ *learn loop closed and cut over to
storage* · *Last updated:* 2026-08-05 (C10 v2 drafted by the DP rebuild; nothing running here
changed)

---

## Current state

- **The learn loop is closed end to end and runs on storage's HTTP surface**
  ([D18](../../DECISIONS.md), built 2026-07-27).
- Five clients sit behind the existing protocols — `HttpDayLogClient` (C10 v1),
  `HttpWindowLedger`, `HttpProfileClient` (C12), `HttpRecipeRegistry` (C13), `HttpReservoirClient`
  (C14), selected by `CONTINUUM_STORAGE_CLIENTS=local|http`, and *`http` is the default*.
- **Suite: 262 passed + 7 skipped** (re-run 2026-07-27). `app/morpheus/` and `tests/parity/` are
  *byte-unchanged* by the cutover; the live two-process seam check passes at 10 steps / 151 checks.
- **Window arithmetic is gone from this service.** `window_for()`, `closed_window_before()`,
  `Window.local_date` and `ReservoirEntry.local_window_date()` are deleted, along with
  `cycle.py`'s reconstruction of prior windows under tonight's timezone — prior windows now come
  from storage's enumeration read.
- A test walks `app/`'s AST and fails if window-id parsing reappears.
- **`nightly.py --tz` and `--date` are gone.** `home_tz` comes from the C12 profile; a 404 exits 2
  with an operator message and runs nothing. A *crash leaves the window open*, so the retry resumes
  the same `window_id` instead of minting a second one.
- **The day-log dialect is checked, not assumed.** `HttpDayLogClient` refuses a body whose
  `daylog_format_version` or `recipe_id` is not the one this night trains under, leaves the window
  open and exits 2 — an announcement nobody reads is a silent change.
- **The local day-log path is retained deliberately** as the parity reference storage's M9 diff is
  measured against. It refuses to *source* records for a training window (that is an ingest-time
  question and the local path filters event time).
- **Recipe `consolidation-v1.1` is what runs**, forking v1.0 on one knob (`replay.source` `amp` →
  `rawlog`).
- `replay.source="amp"` has *no HTTP implementation* by design — C14 serves a ledger, not corpora,
  so a v1.0 night over HTTP with history fails loudly. v1.0 is retained as the recipe the
  Phase-1/Phase-3 numbers were produced under.
- **D9 observability obligation is unchanged and not started** (metrics + dashboard, off the request
  path).

## Workstream index
| WS | What | Status | Working file | Owner session |
|---|---|---|---|---|
| WS1 | Nightly-loop scaffold: mock cycle headless green (window→daylog→amplify→replay→train→gate→publish, journaled + idempotent) | **done** | [handoff/ws-nightly-scaffold.md](handoff/ws-nightly-scaffold.md) | — |
| WS2 | **Morpheus port** (real `TRAINER_BACKEND=morpheus`); exit = Speed-data night reproduces recipe-v1.0 numbers through our gate + C5 path | **2a + 2b done ✅** (port proven; 32B M0 published + served) | [ws-morpheus-port.md](handoff/ws-morpheus-port.md) · [phase-2a-report.md](handoff/phase-2a-report.md) · [overnight-2-report.md](handoff/overnight-2-report.md) | Morpheus sessions |
| WS2c | **Lean storage seams** — 5-verb loop over three storage *client* interfaces (day-log fetch / recipe registry / reservoir), local impls; daylog/window/renderer migrated behind the day-log client (byte-identical); raw-source replay wired | **done ✅** | [ws-morpheus-port.md](handoff/ws-morpheus-port.md) §7 (2c) | 2c: Morpheus session |
| WS-P3 | **Phase 3 — DP dogfood**: Speed data through the real recording→DP→storage→continuum pipeline. 3a bridged 209.7 h of real audio; 3b's 1-min rule-bend collapsed on *dose*; the *decomp (parity content) reproduced the baseline separation* → *pipeline sound* | **done ✅** | [handoff/ws-phase3-dogfood.md](handoff/ws-phase3-dogfood.md) · [phase-3-decomp-report.md](handoff/phase-3-decomp-report.md) | Phase-3 sessions |
| WS3 | C10 **evolution** + real storage integration + watermark/late-data policy | **done ✅ (2026-07-27)** — five HTTP clients, `--tz`/`--date` retired, window-id parsing deleted, verified against the live storage service | [handoff/worklog.md](handoff/worklog.md) 2026-07-27 | — |
| WS4 | Eval gates v1: probe generation (generator ≠ corpus-generator), Gemini judge on our creds, the 3 unwired gate checks | **queued** — the next unstarted workstream | *(opens with work)* | — |

## Next

| # | Item | Blocked on |
|---|---|---|
| 1 | **WS4 — eval gates v1.** Probe generation with a generator distinct from the corpus generator, the Gemini judge on our own creds, and the three unwired gate checks. | nothing — next unstarted workstream |
| 2 | **D9 observability** — `/metrics` + a dashboard JSON, off the request path. | platform's shared backbone (founders' §Next item 2) |
| 3 | **Board ratification of [C-1](DECISIONS.md) and [C-2](DECISIONS.md)** — the serve-time memory harness landing in inference, and DP's data-ownership + caption-spec upgrade. Both re-cut another service's charter, so neither is ours to settle. | a founders' session |
| 4 | **Recipe/dose finding for Gnandeep** — amplification dose is fixed *per block*, but recall tracks retellings *per unit of block text*, so at our native cadence dose must scale with block-text volume. | cofounder conversation, not code |
| 5 | **`_UserState.debt` demotion to reporting** — deliberately left out of the transport cutover; it is a cycle-semantics change. | nothing |

## Cross-service flags

- **the DP rebuild (drafted 2026-08-05, awaiting ratification)** — C2 v1 (slots) and C10 day-log
  v2 (slot-walk renderer, `(chunk_id)`/`updated_at` dedup) are cut on branch `dp-rebuild-v1`
  ([../../contracts/c10_daylog.v2.json](../../contracts/c10_daylog.v2.json); D-R5/D-R6 in
  [../../DECISIONS.md](../../DECISIONS.md) §Drafted).
- Nothing to build here yet: at the cutover (rebuild Stage F) `daylog_format_version`/`recipe_id`
  bump and our already-built stamp-refusal is the transition safety net. Healed records land in
  the *next* window (accepted double-training, same class as a version bump).
- **storage** — the day-log, the training-window ledger, the `window_id` minter and C12/C13/C14 are
  *theirs and built* ([D18](../../DECISIONS.md)). Open on their side: *E-2*, the kind-aware
  retraction primitive, which must cascade to the day-log and the reservoir — redesigned
  whole-record by the drafted D-R6.
- **data-processing** — the caption-spec upgrade (event-verb dense descriptions, quality score,
  eval-only QA field) and later an `amplify` batch stage / slot-generation stage are still queued
  behind board ratification of [C-2](DECISIONS.md).
- **inference** — the serve-time memory harness is headed their way ([C-1](DECISIONS.md), noted in
  their canvas §Incoming); C5 entries are already produced here.
