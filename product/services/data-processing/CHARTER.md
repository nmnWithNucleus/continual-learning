# Data Processing Service — Charter

> The normalization layer of Nucleus v0: raw captured streams in, structured / timestamped /
> enriched records out. Stable doc — working state lives in [HANDOFF.md](HANDOFF.md);
> system-wide architecture + contracts in [../../ARCHITECTURE.md](../../ARCHITECTURE.md).

**Status:** chartered · **Last updated:** 2026-07-09

---

## Mission

Turn the raw, continuous capture of a pilot user's life — wearable body-cam A/V plus computer
screen/mic/browser capture — into structured, timestamped, enriched records ready for storage
and training. Every token the per-user model is ever fine-tuned on, and every interactive
request QueryBuilder assembles, passes through this service's pipelines: the quality ceiling of
the whole product is set here. One code path normalizes both the life stream (batch, C1→C2)
and interactive requests (synchronous, C8), so the model always sees data in one dialect.

---

## Scope — v0

### In scope
| Area | v0 treatment |
|---|---|
| Audio pipeline | denoise → speaker diarization → ASR → translation (non-native language) |
| Text pipeline | normalization (encoding, whitespace, structure) |
| Image pipeline | ImgProc → **OCR-specialist pass** (legible text + where it sits in the frame) → dense captioning (OCR woven into the description) → world-data injection |
| Video pipeline | VidProc (chunking/windows) → **OCR-specialist pass** (per keyframe: legible text + location) → dense captioning (OCR woven in) → world-data injection |
| Timestamp injection | wall-clock timestamps woven into every record, **all modalities** — the cross-source time spine; concurrent activities from different devices must be alignable |
| World-data enrichment | geolocation, known-faces/people registry lookups, place/object tagging |
| /context writes | emit processed records to storage per **C2** |
| Sync pipeline API | expose the whole pipeline synchronously to QueryBuilder per **C8** |
| Observability | expose a `/metrics` endpoint (Prometheus text) + own a Grafana dashboard JSON (`dashboards/*.json`); baseline request rate / latency / error rate **plus** pipeline throughput + queue depth per modality, per-stage latency (denoise/diarize/ASR/translate, OCR pass, dense-caption, world-data injection), C8 sync-request latency, enrichment counts. Platform owns the shared backbone — see [../../ARCHITECTURE.md](../../ARCHITECTURE.md) §Observability (D9) |

### Out of scope (owning sibling)
| Area | Owner |
|---|---|
| Capture, device endpoints, upload/streaming to backend | Recording Service (`recording`) |
| Storage engine; the /raw, /context, /sessions stores and the model directory | Storage Service (`storage`) |
| UserPrompt assembly, chat templating (input *calls* our pipeline via C8) | Input Service (`input`) |
| Model serving, agentic harness, mentor protocol | Inference Service (`inference`) |
| Response delivery to devices | Output Service (`output`) |
| Fine-tuning cadence, adapters (entries published to the model directory via C5) | Continuum Service (`continuum`) |
| Shared infra: SLURM, GCS, CI/CD, observability | Platform Service (`platform`) |

---

## Position in the system

```
recording ──C1──▶ DATA PROCESSING ──C2──▶ storage /context ──▶ continuum (nightly fine-tune)
                        ▲
input QueryBuilder ─────C8 (synchronous; SAME code path as the stream)
```

Contract payloads are owned by [../../ARCHITECTURE.md](../../ARCHITECTURE.md) § Contracts —
referenced here by ID only, never redefined.

| Contract | Direction | Our role |
|---|---|---|
| **C1** | recording → us | **v0 FROZEN (D11).** Sole ingest: the pushed raw-stream envelope (device/`stream_id`/`sequence`/`chunk_id`/modality/codec/wall-clock/`blob_ref`/optional location+clock). at-least-once, we dedup on `chunk_id`, order via `(stream_id, sequence)`, pull bytes by `blob_ref`. Capture semantics belong to `recording`. |
| **C2** | us → storage | **v0 FROZEN (D10).** Sole output for stream data: the processed record (`record_id` deterministic on `(chunk_id, pipeline_version)`; source provenance; `content{kind,text,segments}`; present-but-empty `enrichments`; raw ref; `pipeline_version`; `processed_at`). |
| **C8** | input ↔ us | Serve the pipeline as a synchronous API so interactive requests are normalized by the same code that processes the life stream. |

Indirect consumers (no direct contract with us): `continuum` reads /context + /sessions via
**C10** (storage → continuum) — which changes nothing about our C2 obligations; `input` builds
prompts from what C8 returns.

---

## v0 deliverables

| M | Deliverable | Exit criterion |
|---|---|---|
| **M0** | Walking skeleton: C1 fixture → **pull blob by `blob_ref`** → audio ASR (transcript + segment times) → C2 record in /context; `pipeline_version` stamped; idempotent (envelope `chunk_id` → deterministic `record_id`, so redelivery/reprocess is an upsert, not a dup) | End-to-end integration test green against a storage dev target; record validates against the C2 schema; a re-pushed `chunk_id` yields no duplicate /context record |
| **M1** | Full audio pipeline: denoise → diarize → ASR → translate; timestamps injected from the C1 envelope | Pilot body-cam + computer-mic sample processed; WER/DER measured on a labeled sample and published as baseline |
| **M2** | Text normalization + image pipeline (ImgProc → OCR-specialist pass → dense caption → world-data injection) | Screenshots/webcam frames from pilot computer capture land in /context with captions **and transcribed on-screen text (with frame location)**; spot-check review pass, incl. an OCR-heavy screen |
| **M3** | Video pipeline: VidProc chunking + dense describe, ported from POC Phase-2/3 machinery | Body-cam and screen-recording chunks described end-to-end; reviewed via an explorer-style spot-check tool |
| **M4** | Cross-source time spine: per-device skew handling, one per-user timeline | Two-device concurrent test capture aligns within a documented skew bound; alignment test in CI |
| **M5** | World-data enrichment: known-faces/people registry, geolocation, place/object tags | Registry-known faces tagged in pilot streams; geo/place tags from the C1 optional device-location field where captured, content-inferred otherwise |
| **M6** | C8 synchronous API — same pipeline code, interactive profile | `input` round-trips a multimodal request through C8; p95 latency within the budget agreed in ARCHITECTURE.md |
| **M7** | Production hardening: backpressure, dead-letter + backfill, reprocess-by-version | Kill/restart mid-stream loses zero records; a `pipeline_version` bump cleanly reprocesses one full pilot day |
| **M8** | Metrics + dashboard (D9): `/metrics` endpoint + Grafana dashboard JSON, per [../../ARCHITECTURE.md](../../ARCHITECTURE.md) §Observability | Service `/metrics` scraped by the shared Prometheus; dashboard shows request rate / latency / errors + pipeline throughput/queue depth per modality, per-stage + C8 latency, enrichment counts |

Order is strict M0→M3 (modality coverage first); M4–M7 may interleave after M3.

---

## Open questions

**Engineering**
1. C1 delivery semantics — push vs pull, ordering, at-least-once + dedup key. **RESOLVED (D11, 2026-07-09):** **push, at-least-once**; we are **idempotent on `chunk_id`** (the dedup key); ordering + gap detection via dense zero-based `(stream_id, sequence)` (any break = lost chunk); recording writes the blob to `/raw` **first** (blob-first) and we **pull the bytes by `blob_ref`** for ASR — and must tolerate a since-deleted blob (deletion + re-pull both exist). Frozen shape: `contracts/c1_raw_stream_envelope.v0.json`.
2. C8 latency budget vs pipeline weight: do interactive requests run a lighter captioning profile (same code, config-only difference), and what is the p95 target? Settle with `input`.
3. GPU placement for pipeline models (ASR, diarization, captioners): dedicated allocation vs sharing the a3-mega partition with `continuum`'s nightly window — contention policy needed.
4. Device clock discipline: does `recording` guarantee synced wall-clock stamps, or must M4 estimate skew from content? Changes the time-spine design.
5. Reprocessing policy on `pipeline_version` bumps: reprocess history (cost) vs version-forward only — interacts with `continuum`'s training windows.
6. Known-faces/people registry — split is pinned in [ARCHITECTURE.md § Ownership splits](../../ARCHITECTURE.md#ownership-splits-pinned--cross-referenced-from-the-charters): we own matching/enrichment, `storage` persists the registry, `input` owns curation + consent UX. Still open here: what we cache locally and how registry edits invalidate that cache.

**Research**
7. Captioning granularity for continuous life streams: the POC ran 20/10/5/1-min targets at ≈$7.8k for 753 h — which operating point (granularities × model tier) fits a per-user-day budget?
8. Teacher vs self-hosted captioner: POC gold used a frontier API; acceptable for private life data, or self-host a VLM captioner? Privacy/cost/quality triangle — escalate to CTO.
9. Real-world-time verification: with device clocks in the C1 envelope, is deterministic envelope time enough, or do we keep the POC's content-based RWT reconstruction as a cross-check?
10. Screen capture is OCR-heavy: dense captioning at ~205-px effective frames drops small text (POC token-budget math). **Resolved (CTO, D8): decouple OCR from the base model.** A dedicated OCR-strong VLM transcribes legible text + its frame location as a pipeline pass; that text is woven into the description we write to /context (and returned via C8 for interactive turns → /sessions). The user model then learns on-screen text from the *description target*, not by reading pixels natively at inference — so BWM OCR quality (D6 caveat) stops gating anything. Residual engineering choices: which OCR model, keyframe cadence for video, cost per screen-hour, dedup of static text across frames.
11. Voice-to-person linking: diarization yields anonymous speaker labels; linking them to people-registry identities (known-vs-unknown speakers) rides the same registry, and the v0-vs-deferred call is ours ([ARCHITECTURE.md § Ownership splits](../../ARCHITECTURE.md#ownership-splits-pinned--cross-referenced-from-the-charters)). **Recorded: deferred — not in the M5 exit gate**; revisit if speaker embeddings already produced by the diarizer make matching cheap.

---

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Frontier-API captioning cost scales with capture hours | Per-user-day cost blows the pilot budget | Tiered granularity; cheap bulk model + selective re-do (POC Phase-3 pattern); self-host path (Q8) |
| Raw life data sent to third-party caption/ASR APIs | Privacy/regulatory exposure | Self-host option; provider DPAs; escalated decision, recorded in ARCHITECTURE.md |
| Clock skew across devices | Misaligned time spine silently corrupts training data | M4 skew handling; alignment tests in CI; per-record time-confidence field |
| Mixed `pipeline_version` records in /context | `continuum` trains on inconsistent dialects | Version stamped in every C2 record; explicit reprocessing policy (Q5) |
| C8 path drifts from the batch path | Interactive requests stop matching the training distribution | One code path enforced; profiles are config-only; contract test diffs both paths on shared fixtures |
| GPU contention with `continuum` nightly training | Processing backlog; stale /context | Off-peak scheduling; explicit backlog tolerance; escalate allocation to `platform` |
| Corrupt/gapped capture (device offline, bad blobs) | Silent holes in the user's timeline | Dead-letter + gap records; idempotent re-pull via C1 refs; daily coverage report |

---

## Team shape

v0 = **one lead session + on-demand workstream agents**. As the service grows:

| Sub-team | Owns |
|---|---|
| Audio/text pipelines | denoise, diarization, ASR, translation, normalization |
| Vision pipelines | ImgProc/VidProc, dense captioning, the OCR path |
| Enrichment & time spine | world-data injection, registries, cross-source alignment |
| Backend/reliability | stream orchestration, C1/C2/C8 surfaces, CI/CD, backpressure, cost + observability |
| Research | captioning operating points, self-hosted captioner, RWT verification |

---

## Related work

- **[poc/live_stream_stability](../../../poc/live_stream_stability/README.md) — direct ancestor.**
  Phase-1 (download → ASR → diarize) prototypes the audio pipeline; Phase-2 (chunking + the
  20-min/1-fps operating point) prototypes VidProc; Phase-3 (dense describe with video-relative +
  real-world-time timestamps, multi-granularity, batch economics) prototypes video dense
  captioning + timestamp injection. Deep record: its HANDOFF.md + `experiments/phase3_describe/`.
- **[poc/recursive_finetuning_stability](../../../poc/recursive_finetuning_stability/HANDOFF.md)** —
  `continuum`-side lineage; relevant here for the shared operational conventions only: manifests
  as the spine, idempotent/resumable pipelines, GCS as source of truth for bulk data.
- Tooling proven in the POC: faster-whisper / WhisperX + pyannote (ASR + diarization), ffmpeg
  segment muxer (lossless chunking), Vertex Batch (bulk captioning at ~50% cost).
