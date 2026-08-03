# DECISIONS — the founders' register

> **One fact, one home.** Every ratified founders' decision lives here, numbered and dated. Every
> other document — [ARCHITECTURE.md](ARCHITECTURE.md), [ORG.md](ORG.md), [VISION.md](VISION.md),
> [HANDOFF.md](HANDOFF.md), every `services/*/CHARTER.md` and canvas — **cites a D-number rather
> than restating the decision**. If you find a decision written out somewhere else, that copy is
> the bug.
>
> Only the founders (CTO + AI co-founder) add rows here. Service owners **propose** via
> [HANDOFF.md](HANDOFF.md) §Escalations; a founders' session ratifies and numbers it.

**Newest first.** A new decision is *prepended*: a row at the top of the index, and its card
directly under the index ([ORG.md](ORG.md) §Documentation protocol). Cards follow
[STYLE.md](STYLE.md) — extend one by adding to a section, never by growing an index cell.

---

## Stage: PROTOTYPE — these decisions are expected to evolve

Per **D19**, nothing here is set in stone, contracts included. As we get our hands dirtier building
more of the product, decisions taken on thin evidence will meet real load and some will be wrong.
That is expected, and this register is built for it:

- **A decision is never silently rewritten to say something different.** It is superseded by a new
  numbered row, so the record shows what changed our minds.
- D17 supersedes its own same-day first draft; D19 overturned two clauses of D18. Both are visible
  above rather than edited away.
- **A D-number never changes**, not for a revision and not for a retirement. No `D18-v1`, no
  `D18-RETIRED`.
- An id that encodes meaning has to change when the meaning does, and this repo has already paid
  for that lesson once.
- D18's `window_id` was `w<local-date>` until the window stopped having a local date; re-keying it
  moved filesystem paths, the training seed and C5 lineage at once.
- Ids are stable handles; meaning lives in the columns beside them. Roughly forty files cite a
  D-number today, and none should ever need auditing.
- **Lineage is bidirectional**, in its own column: the new row records what it supersedes, the old
  row records what superseded it.
- One direction alone lets you walk forward from history but never backward from the decision you
  are reading now, which is the question people actually have.
- **A row's *status* may change in place** — status is a fact about today, while the decision text is
  history and stays put:

  | Status | Means |
  |---|---|
  | `ratified` | Decided at a founders' session. |
  | `BUILT` | Shipped, with the date and commits. |
  | `superseded` | A later decision replaced it. The Lineage column names which. |
  | `demoted` | Still true, but no longer load-bearing — e.g. a blocker downgraded to a nice-to-have. |
  | `RETIRED` | The landscape moved and the decision no longer applies, **with nothing replacing it**. Distinct from `superseded`, which implies a successor. |

  **These five words and no others.** A bespoke status (`MET + verified`, `adopted — standing
  posture`) reads as precision but costs the only thing a status column is for: scanning the
  register and sorting it. If a row needs a qualifier, the qualifier goes in the decision text or
  the Lineage column. **`BUILT` and `RETIRED` are capitalised** because they are the two that change
  what you may do next; the other three are ordinary lifecycle and stay lowercase.
- **Status must never over-claim.** `ratified` and `BUILT` are different words and the difference
  is load-bearing; D17's own status is split for exactly this reason.
- A blanket claim over a two-part decision is the drift that costs the most to unwind.

---

## Register

| # | Decision | Date | Status | Lineage | Card |
|---|---|---|---|---|---|
| **D22** | Onboarding teaching views are a sanctioned document type | 2026-07-30 | ratified | — | [↓](#d22--onboarding-teaching-views) |
| **D21** | STYLE.md is the SOP for every document edit in `product/` | 2026-07-28 | ratified | — | [↓](#d21--the-document-style-sop) |
| **D20** | The exit bar for the storage↔continuum cutover, and a definition of "done" | 2026-07-27 | **BUILT** 2026-07-27 | — | [↓](#d20--the-cutover-exit-bar) |
| **D19** | Stage is PROTOTYPE: nothing is set in stone, contracts included | 2026-07-27 | ratified | supersedes **D18** (2 clauses) | [↓](#d19--stage-prototype) |
| **D18** | Storage owns the day-log; the window becomes an ingest-time watermark | 2026-07-26 | **BUILT** 2026-07-27 | 2 clauses superseded by **D19** | [↓](#d18--storage-owns-the-day-log) |
| **D17** | Timezone: the device owns the fact, storage owns the policy | 2026-07-26 | **BUILT** 2026-07-26 · 2026-07-27 | supersedes its own first draft | [↓](#d17--timezone-custody) |
| **D16** | The async `/ingest` reply shape | 2026-07-19 | ratified | — | [↓](#d16--the-async-ingest-reply-shape) |
| **D15** | Post-deep-session build order: continuum kickoff is next | 2026-07-19 | ratified | — | [↓](#d15--post-deep-session-build-order) |
| **D14** | Capture transport is segmented HTTP upload on every v0 surface | 2026-07-19 | ratified | — | [↓](#d14--capture-transport) |
| **D13** | The consent gate is de-prioritized to the back burner | 2026-07-18 | ratified | — | [↓](#d13--consent-gate-de-prioritized) |
| **D12** | Branching and beta model: a standing `dev` branch for testers | 2026-07-18 | ratified | — | [↓](#d12--branching-and-beta-model) |
| **D11** | C1 is two legs plus push delivery | 2026-07-09 | ratified | — | [↓](#d11--c1-is-two-legs) |
| **D10** | The learn-loop skeleton is computer mic → ASR → `/context` | 2026-07-09 | ratified | — | [↓](#d10--the-learn-loop-skeleton) |
| **D9** | Centralized observability: one shared Prometheus and Grafana | 2026-07-09 | ratified | — | [↓](#d9--centralized-observability) |
| **D8** | OCR is decoupled from the base world model | 2026-07-09 | ratified | retires the **D6** caveat | [↓](#d8--ocr-decoupled-from-the-bwm) |
| **D7** | POCs are reference, not source | 2026-07-09 | ratified | — | [↓](#d7--pocs-are-reference-not-source) |
| **D6** | The base model is Qwen3-VL-32B | 2026-07-09 | ratified | OCR caveat retired by **D8** | [↓](#d6--the-base-model) |
| **D5** | The mobile app ships in v0 | 2026-07-09 | ratified | — | [↓](#d5--the-mobile-app-ships-in-v0) |
| **D4** | The wearable is camera and mic only, with no speaker | 2026-07-09 | ratified | — | [↓](#d4--the-wearable-has-no-speaker) |
| **D3** | Serve-loop first | 2026-07-09 | ratified | — | [↓](#d3--serve-loop-first) |
| **D2** | Single-markdown doc protocol | 2026-07-09 | ratified | — | [↓](#d2--single-markdown-doc-protocol) |
| **D1** | Platform is a ratified service | 2026-07-09 | ratified | — | [↓](#d1--platform-is-a-service) |

### D22 — onboarding teaching views

> `ratified` 2026-07-30 · recorded in [ORG.md](ORG.md) §Documentation protocol +
> [STYLE.md](STYLE.md) §Teaching views · first instance:
> [services/recording/onboarding/](services/recording/onboarding/)

**In one line.** A node may keep a derived onboarding view that teaches newcomers, and it is the
one sanctioned exception to *one fact, one home*.

**What was decided**

- A node may keep `onboarding/*` — a guided explanation in whatever format teaches best, markdown
  or HTML or interactive. Optional, and opened only when a node is worth teaching.
- The view is **derived and non-authoritative**. The repo wins whenever the two disagree.
- It is exempt from [STYLE.md](STYLE.md) rule 8, and pays for the exemption with three obligations:
  linked from the node's CHARTER, repo-wins precedence, and corrected in the same session as the
  change it teaches.
- Voice and shape are governed by [STYLE.md](STYLE.md) §Teaching views, where rules 1, 5 and 6 are
  lifted and four teaching rules imposed.

**Watch out for**

- **A view nobody maintains is worse than no view.** The same-session obligation is the entire
  price of the exemption; drop it and the view becomes the stale parallel copy
  [D2](#d2--single-markdown-doc-protocol) abolished.

### D21 — the document style SOP

> `ratified` 2026-07-28 · [STYLE.md](STYLE.md)
> · recorded in [ORG.md](ORG.md) §Documentation protocol;
> [ARCHITECTURE.md](ARCHITECTURE.md) §Contracts is the reference implementation

**In one line.** [STYLE.md](STYLE.md) is the SOP for every document edit in `product/`.

**What was decided**

- Writing a new document, appending to one, or editing one in place all follow it.
- It binds root docs, service charters, boards, worklogs, and a one-line README alike.
- [ORG.md](ORG.md) decides *which file* a fact belongs in; STYLE.md decides *what that file reads
  like*.
- Binding on every session, human or agent.

**Watch out for**

- **The file is ratified, not a revision of it.** Under D19 its content is expected to evolve, and
  an edit to STYLE.md is itself a founders' act.
- **The 1,500-token ceiling is retired.** STYLE.md sits at ~1,820 and the founders accepted that
  on 2026-07-28: richer, not verbose. Do not trim it to hit a number; trim it when a passage stops
  earning its read.

**How it got here**

- **2026-07-28 — §Was / Changed / Now / Payoff trimmed, 331 → 246 tokens.**
  - **Was** — the section carried a worked example, three bullets glossing what that example
    already showed, and a second fenced block duplicating the first with its labels elided.
  - **Changed** — the glosses went, and the two blocks merged into one that keeps the status line.
  - **Now** — the shape is *shown* in full rather than described twice. No rule changed.
  - **Payoff** — STYLE.md went 1,950 → ~1,820. The remaining weight is §The ten rules, which is
    the standard itself, so further trimming means dropping a rule.

### D20 — the cutover exit bar

> `ratified` 2026-07-27 · **BUILT** 2026-07-27
> · **full reasoning:** [handoff/engineering.md](handoff/engineering.md#2026-07-27-overnight--the-d18-storage-expansion-is-built-the-seam-is-closed) §Worklog 2026-07-27
> · recorded in [ARCHITECTURE.md](ARCHITECTURE.md) §Ownership splits *Day-log: representation vs.
> content* + §Contracts *C10 card*; [storage CHARTER](services/storage/CHARTER.md) M9;
> `services/storage/scripts/daylog_parity_diff.py` · `services/continuum/scripts/seam_check.py`

**In one line.** The exit bar for the storage↔continuum cutover, and a definition of "done" that
can actually be met.

**What was decided**

- **(a) M9's parity bar narrowed to three tiers** after the first run failed it:
  - byte-identical — block `text`, ordering, `block_id`, `anchors`, `quality`; the artifact that
    trains the model.
  - proven-equivalent — `seg_id`.
  - excluded — `content_fingerprint`.
- General rule pinned: storage owns the day-log's representation; its **content** is a contract
  neither service may move alone.
- **(b) "Golden" defined:**
  - all four suites green.
  - M9 proof over a real, misaligned window origin.
  - a live two-process seam with zero blockers.
  - one adversarial round returning nothing high-severity.

### D19 — stage: PROTOTYPE

> `ratified` 2026-07-27 · supersedes **D18** (2 clauses)
> · **full reasoning:** [handoff/engineering.md](handoff/engineering.md#2026-07-27--d19-the-stage-is-prototype-and-the-docs-now-say-so) §Worklog 2026-07-27
> · recorded in [ARCHITECTURE.md](ARCHITECTURE.md) §Stage + §Contracts (C2 `discriminator`, C12);
> [ORG.md](ORG.md) §Stage; every `services/*/CHARTER.md` banner;
> [storage CHARTER](services/storage/CHARTER.md) §Retention + §Scope ·
> [continuum CHARTER](services/continuum/CHARTER.md) §OQ10

**In one line.** Nothing is set in stone, contracts included, and the docs must say so.

**What was decided**

- Licenses re-cutting a contract instead of versioning it, wiping data instead of migrating it, and
  deferring durability work with the reason written down.
- Does **not** license skipping ORG's contract-edit order, undocumented decisions, or calling a
  thing `BUILT` when it is only `ratified`.
- Seven calls taken:
  - retention = keep everything; the mechanism ships, the policy doesn't.
  - storage local now, Postgres+GCS later.
  - C2 `discriminator` surfaced.
  - existing state wiped, not migrated.
  - cycle trigger = per-user cron, materialization on demand.
  - C5 freeze deferred.
  - `home_tz` declared, not inferred.

### D18 — storage owns the day-log

> `ratified` 2026-07-26 · **BUILT** 2026-07-27 (`a5a48fb` · `1757efb` · `2698b63` · `38479df`)
> · 2 clauses superseded by **D19**
> · **full reasoning:** [handoff/engineering.md](handoff/engineering.md#2026-07-26-later--founders-storagec10-board-d18) §Worklog 2026-07-26
> · recorded in [ARCHITECTURE.md](ARCHITECTURE.md) §Contracts C10/C12/C13/C14 cards + §Ownership
> splits *Day-log + training-window custody*;
> [contracts/c12_user_profile.v0.json](contracts/c12_user_profile.v0.json) +
> [contracts/README.md](contracts/README.md);
> [storage CHARTER](services/storage/CHARTER.md) §Scope/§Time index/§OQ6-9/M5+M8+M9 ·
> [continuum CHARTER](services/continuum/CHARTER.md) §Scope/§Contracts/§OQ9-10

**In one line.** Storage owns the day-log, the training window becomes an ingest-time watermark,
and `window_id` stops meaning anything.

**What was decided**

- Day-log materialization moves continuum → storage. Replay would otherwise re-pull every prior day
  nightly, O(days²).
- The window becomes `[last_trained_t, now−δ)` on `ingest_time`, which *dissolves* late data rather
  than handling it.
- `window_id` → opaque `w<YYYYMMDD>T<HHMMSS>Z`, minted once from the end instant, parsed by nobody.
- C12, C13 and C14 minted; E-2 demoted from cutover blocker.

### D17 — timezone custody

> `ratified` 2026-07-26 · **BUILT** 2026-07-26 (tz split) · watermark clause **BUILT** 2026-07-27
> · supersedes its own same-day first draft
> · **full reasoning:** [handoff/engineering.md](handoff/engineering.md#2026-07-26--timezone-decided-then-re-decided-then-built-end-to-end-d17) §Worklog 2026-07-26
> · recorded in [ARCHITECTURE.md](ARCHITECTURE.md) §Contracts C1+C2 blocks, §Ownership splits
> *User timezone*, C10 row; [contracts/](contracts/) c1+c2 schemas;
> [storage](services/storage/CHARTER.md) · [continuum](services/continuum/CHARTER.md) ·
> [data-processing](services/data-processing/CHARTER.md) ·
> [recording](services/recording/CHARTER.md) charters

**In one line.** The device owns the fact of where the user was; storage owns the policy.

**What was decided**

- Per-chunk `device_tz` + `device_utc_offset_minutes` ride C1 → C2 `source{}` → storage columns →
  continuum's renderer, which is the only design correct under travel.
- Per-user `home_tz` (C12) does **scheduling and fallback only**.
- UTC stays canonical; never persist a derived local wall-clock.

**Watch out for**

- Abbreviations (`PST`) are rejected 400 at the capture edge.

### D16 — the async `/ingest` reply shape

> `ratified` 2026-07-19 · inter-service wire, prose-pinned in the DP canvas at merge — not a
> C-number; C1/C2 untouched
> · recorded in [handoff/engineering.md](handoff/engineering.md) ratification block; DP canvas
> (pinned prose at merge); recording canvas (verdict semantics)

**In one line.** How data-processing answers an ingest call once it stops answering inline.

**What was decided**

- `INGEST_ASYNC` is off by default; the inline path is byte-unchanged.
- Async replies:
  - **202** `{ok,accepted,chunk_id}`, plus `duplicate:true` on an in-flight dedup hit.
  - **200 + record_ids** on a done-dedup hit.
  - 400/422/501 resolve synchronously, pre-claim.
  - **503** for bounded-queue backpressure.
- `/continuity` gains additive `processed` + `dead_lettered`.
- **Invariant preserved:** `dp_acked` means "C2 durably written". Recording moves in-slice —
  `dp_state='accepted'` plus gap-report reconciliation, `clean` = every chunk confirmed,
  accepted-unconfirmed → `recording`, dead-lettered → `gaps`.
- Guarantee: **never falsely `clean`**. Auto-recovery is the M7 durable journal.

**Watch out for**

- **Condition of ratification:** the accepted-unconfirmed re-drive path is named and drilled
  in-slice.
- **Accepted caveat:** `record_ids=[]` ledger provenance on 202-path chunks. The ids are derivable.

### D15 — post-deep-session build order

> `ratified` 2026-07-19
> · recorded in [handoff/engineering.md](handoff/engineering.md) §Post-capture-alpha sequencing;
> continuum canvas; [HANDOFF.md](HANDOFF.md)

**In one line.** Continuum kickoff is the next founders-led slice.

**What was decided**

- Continuum kickoff is gated on a **C10 v0 interface freeze** — storage × continuum propose,
  founders ratify, frozen against the beta-proven `/context` range read.
- **Platform's D9 backbone**, the one shared Prometheus + Grafana, runs as the small parallel slice.
- **DP image/text pipelines (M2) are deferred until a producing surface exists.** No `image` or
  `text` C1 stream exists on the fleet today.
- Screen text already flows via the video-keyframe OCR weave (D8), and the OQ14b bbox additive
  waits with it.

**Watch out for**

- Mobile+C8 and a standalone C10 freeze were both considered and passed over. Rationale is in the
  engineering thread.

### D14 — capture transport

> `ratified` 2026-07-19
> · recorded in recording canvas §Pinned decisions (D-M1-5);
> [ARCHITECTURE.md](ARCHITECTURE.md) capture path

**In one line.** Segmented HTTP upload, on every v0 surface — phone, extension and mac CLI.

**What was decided**

- Our capture path is the loss-intolerant, offline-resilient *archive/training* job — the
  Axon-bodycam pattern, not low-latency live-view, the Ring/Nest pattern, which runs both paths
  separately.
- **Continuous streaming ingest (WebSocket/RTSP/SRT → server segmenter) is a deferred additive
  leg**, terminating in the existing spool→demux→carve→emit machinery.
- C1/C2 unchanged; C1 begins after transport.

**Watch out for**

- Live-view is out of v0 scope.

### D13 — consent gate de-prioritized

> `ratified` 2026-07-18
> · recorded in [HANDOFF.md](HANDOFF.md); recording charter §v0 deliverables

**In one line.** The consent gate moves to the back burner; capture and the learn loop mature first.

**What was decided**

- The consent/deletion layer (recording M2 plus platform's consent store) lands **before any
  non-team pilot user**, not before beta.
- Beta testers are consenting teammates, which is what makes the deferral safe.

**Watch out for**

- The M2 red-team exit bar is unchanged whenever it lands.

### D12 — branching and beta model

> `ratified` 2026-07-18
> · recorded in [HANDOFF.md](HANDOFF.md); [handoff/engineering.md](handoff/engineering.md) worklog;
> root `README.md` §Branches

**In one line.** Work happens on branches off `main`; a standing `dev` branch is the beta playground.

**What was decided**

- Service work happens on branches off `main`, merged once coded and tested at a decent revision.
- The **`dev` branch, forked from `main`, is handed to testers**. It may carry beta-only
  conveniences, never contract changes.
- First beta hand-off: the two proven loops, serve and learn, to Gnandeep, who drives them against
  his externally-stabilized fine-tunable model.

**Watch out for**

- Storage's `GET /context/records?user_id=&from=&to=` range read is his training-window feed until
  C10 lands.

### D11 — C1 is two legs

> `ratified` 2026-07-09
> · recorded in [ARCHITECTURE.md](ARCHITECTURE.md) §Contracts;
> [contracts/c1_raw_stream_envelope.v0.json](contracts/c1_raw_stream_envelope.v0.json);
> recording + data-processing charters

**In one line.** A blob leg and an envelope leg, with push delivery on the envelope.

**What was decided**

- **Blob leg:** recording `PUT`s raw bytes to storage `/raw` first, and storage mints an opaque
  `blob_ref`, idempotent on `chunk_id`. Pinned as prose, not a new C-number.
- **Envelope leg:** recording pushes the C1 envelope to data-processing, at-least-once, dedup on
  `chunk_id`.
- Ordering and gap-detection via a dense zero-based `(stream_id, sequence)`, with the blob-first
  write invariant.
- Resolves data-processing OQ1 and recording's ingest OQ.

### D10 — the learn-loop skeleton

> `ratified` 2026-07-09
> · recorded in [ARCHITECTURE.md](ARCHITECTURE.md) §Contracts (learn-loop block) +
> [contracts/](contracts/); [handoff/engineering.md](handoff/engineering.md)

**In one line.** Computer mic → ASR → `/context`, and nothing else.

**What was decided**

- The first end-to-end capture path is audio-only: ASR producing a transcript and segment
  timestamps.
- **No diarization, no enrichment, no vision.**
- Reuses POC Phase-1 (faster-whisper). C1 and C2 v0 were frozen accordingly.

### D9 — centralized observability

> `ratified` 2026-07-09
> · recorded in [ARCHITECTURE.md](ARCHITECTURE.md) §Observability; [STACK.md](STACK.md);
> [platform charter](services/platform/CHARTER.md); all service charters

**In one line.** Every service instruments itself; platform runs the one place you look.

**What was decided**

- Every service exposes `/metrics` and owns a Grafana dashboard JSON.
- **Platform runs one shared Prometheus + Grafana**, plus the standard exporters (node/dcgm/DB), and
  provisions the per-service dashboards.
- Both founders open one Grafana URL.

**Watch out for**

- Node and CPU graphs are placeholders until multi-node. App latency, error rate and GPU are what
  matter today.

### D8 — OCR decoupled from the BWM

> `ratified` 2026-07-09 · retires the **D6** caveat
> · recorded in [data-processing charter](services/data-processing/CHARTER.md)

**In one line.** A specialist OCR-strong VLM reads on-screen text, so the base model never gates it.

**What was decided**

- A specialist OCR-strong VLM transcribes on-screen text, plus frame location, inside the
  data-processing pipeline.
- The text is woven into the description target, so BWM OCR quality never gates the product.

### D7 — POCs are reference, not source

> `ratified` 2026-07-09 · recorded in [ORG.md](ORG.md) §Conventions

**In one line.** Production code is written fresh.

**What was decided**

- POCs inform contracts and learnings only. No lift-and-shift.

### D6 — the base model

> `ratified` 2026-07-09 · OCR caveat retired by **D8**
> · recorded in [ARCHITECTURE.md](ARCHITECTURE.md) §Ownership splits *Base world model*

**In one line.** Qwen3-VL-32B.

**What was decided**

- The base world model is Qwen3-VL-32B.

**Watch out for**

- The original caveat (re-verify OCR on our own screen-capture data before locking) was retired by
  [D8](#d8--ocr-decoupled-from-the-bwm).

### D5 — the mobile app ships in v0

> `ratified` 2026-07-09
> · recorded in [ARCHITECTURE.md](ARCHITECTURE.md) §Ownership splits *Mobile app* + *Speech output
> routing*; input + output charters

**In one line.** An interaction surface and the default speech-output sink.

**What was decided**

- The mobile app ships in v0 as an interaction surface **and** as the default speech-output sink,
  routing to Bluetooth headphones or earbuds.

**Watch out for**

- Only mobile *screen capture* stays deferred. The app itself ships.

### D4 — the wearable has no speaker

> `ratified` 2026-07-09
> · recorded in [ARCHITECTURE.md](ARCHITECTURE.md) §Ownership splits; recording + output charters

**In one line.** Camera and mic only.

**What was decided**

- The speaker requirement is dropped from the hardware pick, because market bodycams lack speakers.

### D3 — serve-loop first

> `ratified` 2026-07-09 · recorded in [handoff/engineering.md](handoff/engineering.md)

**In one line.** Build the thin end-to-end backbone, then grow the rest around it.

**What was decided**

- Build input → QueryBuilder → inference on the base model → output first.
- Grow capture, storage and continuum around it.

### D2 — single-markdown doc protocol

> `ratified` 2026-07-09 · recorded in [ORG.md](ORG.md) §Documentation protocol

**In one line.** One stable CHARTER and one volatile HANDOFF per node.

**What was decided**

- No parallel human-readable and AI-readable copies.

### D1 — platform is a service

> `ratified` 2026-07-09
> · recorded in [ARCHITECTURE.md](ARCHITECTURE.md) component table; [HANDOFF.md](HANDOFF.md)

**In one line.** A ninth node covering infra, CI, security, privacy and cost.

**What was decided**

- Platform is a ratified service.

**Watch out for**

- Scope was accepted as-is; the CTO is to read the internals in detail later.
