# HANDOFF — Data Processing Service working canvas

> Single touch-point for any agent (or human) picking up work on this service.
> Read [CHARTER.md](CHARTER.md) first (mission/scope/interfaces), then this file — the
> volatile working record. Conventions: [../../ORG.md](../../ORG.md) § Documentation protocol.

**Status:** built — **v1, the rebuilt service, is live** on `main` (the beside-build rebuild
Stages A–G, D23–D28, executed and merged) · DP suite **569 passed, 4 skipped** (post-Stage-G) ·
*Last updated:* 2026-08-07 (rebuild EXECUTED; the demolition + doc rewrite are Stage G)

**Where we are.** The service ingests C1 chunks and writes **one C2 v1 record per chunk** — built
from `content.slots`, one stage per slot, never edited (the Slot Law, [CHARTER.md](CHARTER.md)
§Slot Law). Models run as long-lived **model servers** under a supervisor (`servers/whisper`,
`pyannote`, `ast`, `ocr`); the DP process is a thin async orchestrator that calls them (the
machinery/bureaucracy split, L9). Audio and video both run end to end against the live fleet.
Ingest is **async** (the D16 default, paid at the Stage F cutover); a durable journal re-drives
the pending set on restart. The video captioner is the self-hosted Qwen3-VL on `:8161`.

- **Audio** — VAD-gated ASR (faster-whisper) + diarization (pyannote) + acoustic tagging (AST) +
  a derived `speaker_align` transcript slot. Real backends, live.
- **Video** — `clipprep` (ffmpeg) → `screentext` (PP-OCR on `servers/ocr`) → `clipcap` (one
  Qwen3-VL call), producing a `caption` slot and an `ocr` slot in one record.
- **Ingest** — async 202 + bounded worker pool + a durable pending journal (kill/restart recovery);
  `/continuity` reports processed/dead-lettered so recording never reads a lost chunk as `clean`.
- **Observability** — D9 `/metrics` + a Grafana dashboard (emission side; the shared backbone is
  platform's, still unbuilt).

## The rebuild is history

The v1 service replaced the v0 multi-record + in-place-mutation model. That work is complete and
lives as the rebuild's historical record, not on this board:

- Plan + decision rows: [docs/refactor_dp_service.md](docs/refactor_dp_service.md) (Status:
  EXECUTED 2026-08-07; D23–D28; the stage→commit-range table).
- Per-stage worklogs: [docs/refactor_stage_A.md](docs/refactor_stage_A.md) …
  [docs/refactor_stage_G.md](docs/refactor_stage_G.md).
- Why the v0 governance existed and what killed it: [CHARTER.md](CHARTER.md) §Condensed history.
- The v0 workstream detail (M0–M8, the screen-video clip path, the hardening slice) is retired in
  [handoff/worklog.md](handoff/worklog.md) with its dates and commits intact.

| Stage | What landed | Range |
|---|---|---|
| A–E | ratify · machinery · stagegraph · ledger v2 · storage v2 | `f639fda` → `e3cedb6` |
| F | cutover (merge `bf1e806`), five drills, the amended soak + in-flight kill + train leg | `413b3a6` → `26f763f` |
| G | demolition (sidecars/, dead scripts, circuit.py, per-frame-v0) + doc rewrite | `afe0103` → `242eb31` |

## Next

Open items only. Finished work leaves the board for [handoff/worklog.md](handoff/worklog.md).

| # | Item | Blocked on |
|---|---|---|
| 1 | **Client live-stream testing (the next phase).** The Stage F soak proved the engineering bar synthetically; the founder ruling (R2) transfers the *live pilot-day* shape here — a real captured day flowing recording → DP → storage → continuum end to end, on real client hardware. This is the phase Stage G opens onto. | real capture beginning (a lifestyle gate, not an engineering one) |
| 2 | **Backfill-by-version (`/raw` replay).** The OD-2 tool that reprocesses a day under a new dialect from the kept `/raw` bytes — the owed half of reprocess-by-version. Kill/restart recovery is built; this is not. | nothing — scoped, not started |
| 3 | **Per-modality fairness on the ingest queue.** `INGEST_MODALITY_LIMITS` exists but is unset; the Stage F soak showed a video burst (CPU OCR + 32B caption) can starve audio behind it. Tune when a real fleet load justifies it. | evidence from a real load |
| 4 | **The captioner-fleet vB deploy.** Stage G bumped clipcap `vlm.v1 → vlm.v2` (per-frame-v0 removal); the running fleet still serves `vlm.v1`. The next deliberate fleet restart (drain-and-replace, D-14) flips it — no rush; caption bytes are unchanged. | a scheduled restart |
| 5 | **D9 observability backbone** — the shared Prometheus + Grafana. Emission shipped long ago; the backbone is platform's. | platform's §Next |

## Gotchas

- **The law is executable.** `tests/` (T-1…T-6) enforce the Slot Law in CI; a violation is a red
  test, not a review note. Run the suite before trusting a change to the executor or a stage.
- **No output-affecting env knob exists** (L4). If you reach for an env var to change what a record
  says, stop — that is a code change (a `vS`/`vB` bump), and the determinism test will catch it.
- **The inline ingest path is kept on purpose** as C8's skeleton; it is byte-identical to async for
  one chunk. Do not delete it "because async exists" — proposed and refused on that ground.
