# HANDOFF — Data Processing Service working canvas

> Single touch-point for any agent (or human) picking up work on this service.
> Read [CHARTER.md](CHARTER.md) first (mission/scope/interfaces), then this file — the
> volatile working record. Conventions: [../../ORG.md](../../ORG.md) § Documentation protocol.

**Status:** built — v1 live on `main` · DP suite green · *Last updated:* 2026-08-07

**Where we are.** The service ingests C1 chunks and writes **one C2 v1 record per chunk** —
built from `content.slots`, one stage per slot, never edited ([CHARTER.md](CHARTER.md)
§Slot Law). Models run as long-lived **model servers** under a supervisor (`servers/whisper`,
`pyannote`, `ast`, `ocr`); the DP process is a thin async orchestrator that calls them (L9).
Audio and video both run end to end against the live fleet. Ingest operating default is
**async** ([D16](../../DECISIONS.md): code default off, depot default on); a durable journal
re-drives the pending set on restart. The video captioner is the self-hosted Qwen3-VL on
`:8161`.

- **Audio** — VAD-gated ASR (faster-whisper) + diarization (pyannote) + acoustic tagging (AST) +
  a derived `speaker_align` transcript slot. Real backends, live.
- **Video** — `clipprep` (ffmpeg) → `screentext` (PP-OCR on `servers/ocr`) → `clipcap` (one
  Qwen3-VL call), producing a `caption` slot and an `ocr` slot in one record.
- **Ingest** — async 202 + bounded worker pool + a durable pending journal (kill/restart recovery);
  `/continuity` reports processed/dead-lettered so recording never reads a lost chunk as `clean`.
- **Observability** — D9 `/metrics` + a Grafana dashboard (emission side; the shared backbone is
  platform's, still unbuilt).

## Next

Open items only. Finished work leaves the board.

| # | Item | Blocked on |
|---|---|---|
| 1 | **Client live-stream testing (the next phase).** A real captured day flowing recording → DP → storage → continuum end to end, on real client hardware. | real capture beginning (a lifestyle gate, not an engineering one) |
| 2 | **Backfill-by-version (`/raw` replay).** The owed reprocess-by-version tool that replays a day under a new dialect from kept `/raw` bytes. Kill/restart recovery is built; this is not. | nothing — scoped, not started |
| 3 | **Per-modality fairness on the ingest queue.** `INGEST_MODALITY_LIMITS` exists but is unset; a video burst (CPU OCR + 32B caption) can starve audio behind it. Tune when a real fleet load justifies it. | evidence from a real load |
| 4 | **The captioner-fleet `vlm.v2` deploy.** Code pins `vlm.v2`; the running fleet may still serve the prior pin until the next deliberate drain-and-replace restart. Caption bytes are unchanged. | a scheduled restart |
| 5 | **D9 observability backbone** — the shared Prometheus + Grafana. Emission shipped; the backbone is platform's. | platform's §Next |
| 6 | **Parity-apparatus retirement** — pointer only; the owed one-act retirement lives on the [storage board](../storage/HANDOFF.md) §Next 7. | storage-led |

## Gotchas

- **The law is executable.** `tests/` (T-1…T-6) enforce the Slot Law in CI; a violation is a red
  test, not a review note. Run the suite before trusting a change to the executor or a stage.
- **No output-affecting env knob exists** (L4). If you reach for an env var to change what a record
  says, stop — that is a code change (a `vS`/`vB` bump), and the determinism test will catch it.
- **The inline ingest path is kept on purpose** as C8's skeleton; it is byte-identical to async for
  one chunk. Deleting it would orphan the synchronous contract.
