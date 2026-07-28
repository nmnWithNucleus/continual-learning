# Founders' thread — Engineering

> Running canvas for founders' engineering sessions (launch: [../PROMPTS.md](../PROMPTS.md) §D).
> Cross-service build sequencing, integration plans, infra calls. Service-internal
> engineering lives in each service's canvas, not here.
>
> **This file is the *reasoning* half of the engineering thread.** The board — where things stand,
> what needs a founders' decision — is [../HANDOFF.md](../HANDOFF.md); the ratified decisions are
> [../DECISIONS.md](../DECISIONS.md), whose rows point back at the `### <date>` worklog anchors below.

**Status:** active · **Last updated:** 2026-07-28 (style pass over `handoff/*`, D21. Earlier:
**D18/D19/D20 shipped** — the storage expansion is
built, continuum is cut over to storage's HTTP surface, the fleet runs on it, and the review backlog
is empty. Same week: **D17** timezone ownership decided *and* built)

**How this file is organised** — live work first, history second, finished work last:

| Section | What it holds |
|---|---|
| [§Open agenda](#open-agenda) | live threads + what's next |
| [§Worklog](#worklog) | one entry per founders' session, **newest first**, each a linkable `###` anchor |
| [§Archive — delivered slices](#archive--delivered-slices) | design + build records for slices that are done |

---

## Open agenda

Three open threads. Delivered slices live in [§Archive](#archive--delivered-slices); what shipped,
and when, is in [§Worklog](#worklog).

1. **Cluster split — which a3mega nodes serve (vLLM), train (continuum), and run pipeline work.**
   - Interim answer (2026-07-18): Gnandeep runs continuum-side model-stabilization experiments
     across the wider cluster — the `engram` SLURM jobs, his workspace, outside this repo.
   - Product components keep **node-7**; allocate beyond one node on demand.
   - Continuum kickoff ([D15](../DECISIONS.md)) is the forcing function. Settle the split there.
2. **Mobile app — one codebase serving both the chat surface (input) and the speech-output
   playback sink (output)** ([D5](../DECISIONS.md)).
   - Sequence it after the computer text slice proves the loop.
   - Considered at the D15 sequencing (2026-07-19) and passed for now: mobile's value binds to a
     personalized model. Revisit once the first adapter serves.
3. **The [D9](../DECISIONS.md) observability backbone is still unbuilt.**
   - The emission half shipped: recording M6 and data-processing M8.
   - The shared Prometheus + Grafana is the D15 small parallel slice, and it is
     [../HANDOFF.md](../HANDOFF.md) §Next item 2.
   - One shared stack rather than eight bespoke dashboard servers was the call; each service ships
     its own Grafana dashboard JSON and platform provisions them.
   - Shape and metrics: [../ARCHITECTURE.md](../ARCHITECTURE.md) §Observability. Ports:
     [../STACK.md](../STACK.md).


---

## Learnings

What we tried, what stopped us, and what won instead. A learning that binds a live component is a
**Watch out for** bullet on that component's card, not an entry here — most of what this thread has
learned already lives that way, in [../ARCHITECTURE.md](../ARCHITECTURE.md) and
[../ORG.md](../ORG.md). These are the ones that bind nothing specific.

- **A gate that only ever over-reports is invisible.** `style_check.py` mis-parsed its own rule 6
  for a week: 136 of 2,908 findings were the tool wrong, 64 in a document that already complied.
  Nobody looked, because a checker that flags too much still reads as strict.
  ([2026-07-28](#2026-07-28-later--the-style-pass-reaches-the-service-documents))
- **A pass that reflows blockquotes can silently swallow a heading.** Quoting a snapshot took the
  `## Archive` heading with it and broke a link, one commit into this very effort. The heading-set
  diff catches it in seconds, which is why it is in the exit bar and not in a reviewer's head.
  ([2026-07-28](#2026-07-28-later--the-style-pass-reaches-the-service-documents))
- **A ratification prompt that states its own premises gets them corrected.** D18's prompt asserted
  a `render_block` parity constraint that was wrong — it conflated two functions of the same name.
  Saying it out loud is what got it caught rather than inherited, and the habit is now
  [../PROMPTS.md](../PROMPTS.md) §D+.
  ([2026-07-26](#2026-07-26-later--founders-storagec10-board-d18))
- **Verifying a prompt's claims found more consumers than the prompt listed.** Checking D18's five
  `window_id` claims took minutes and surfaced three further consumers, one load-bearing. Cheap
  verification beats careful enumeration.
  ([2026-07-26](#2026-07-26-later--founders-storagec10-board-d18))
- **A desktop screen-picker was too fragile on real browsers**, so the Chrome extension pivoted to
  `tabCapture` (D-E7). The lesson generalises: capture surfaces get proven on real hardware before
  a wire is designed around them.
  ([2026-07-18→19](#2026-07-1819--recording-led-capture-m1--computer-surfaces-alpha-complete))
- **An exit bar written before the first run was unmeetable.** M9's parity bar failed its first run
  and had to be narrowed to three tiers ([D20](../DECISIONS.md)). Write the bar, then run once, then
  ratify the bar.
  ([2026-07-27](#2026-07-27-overnight--the-d18-storage-expansion-is-built-the-seam-is-closed))

## Worklog

> **Newest first.** New entries are *prepended* directly under this heading, never appended
> at the bottom (ORG.md §Documentation protocol). Each entry is a `###` anchor so [DECISIONS.md](../DECISIONS.md)
> and the service canvases can point at the reasoning behind a decision by name.

### 2026-07-28 (later) — the style pass reaches the service documents
> review · all services · [D21](../DECISIONS.md)

**Was** — [STYLE.md](../STYLE.md) binds every node, but only the founder-level documents and
`handoff/*` had been brought under it. The service charters and boards carried 629 of the corpus's
2,908 findings, and they are what a new session or a new hire reads first.

**Changed** — the eight charters and eight boards were restructured. A table cell that had
outgrown its row became a card below it, following [ARCHITECTURE.md](../ARCHITECTURE.md)
§Contracts. Data-processing's board had the same append-only defect the founders' board had, so its
finished items were retired verbatim into a new `handoff/worklog.md`.

**Now** — charters 374 → 185, service boards 255 → 145,
[LEARN_LOOP.md](../onboarding/LEARN_LOOP.md) 166 → 136, and the seven cited ws-files 712 → 272.
Corpus 2,908 → 2,200. No file gained findings, and the baseline is rewritten.

**Payoff** — the cold-start path reads as cards with the details on top of each section, rather
than as paragraphs a reader has to mine. Cost accepted: LEARN_LOOP's 28-to-60-word bullets are a
long tail nobody has swept, and the ~83 service-internal ws-worklogs are untouched by design.

**Watch out for**

- **`## Archive — delivered slices` had been swallowed into a blockquote** by the previous commit
  in this same effort, which broke the section link at the top of this file. Restored. It had been
  masking four findings, now fixed rather than baselined.
- **Rule 6's 60-word reasoning allowance never fired in the checker.** The guard stopping a list
  item from setting the section also excluded bold section headings, since both start with `*`.
- **[LEARN_LOOP.md](../onboarding/LEARN_LOOP.md) §4.5 is stale on content, not on form.** Its
  heading still says the day-log is continuum-side. It carries a *(pre-cutover)* marker now;
  correcting it needs a session that can verify what actually runs.
- **STYLE.md's §Was / Changed / Now / Payoff was trimmed**, 331 → 246 tokens, a founders' act this
  session. The file sits at ~1,820 against D21's 1,500 ceiling, and the founders retired the
  ceiling rather than the content: richer, not verbose.
- **[ws-video-clip.md](../services/data-processing/handoff/ws-video-clip.md) was 2,513 lines, 1,207
  of them appended build logs.** They rolled to a companion file under §Growing a worklog, which is
  most of that file's 458 → 106.
- **Rule 6's 27-word cap fights record-style documents.** Splitting a 202-word bullet into six
  well-formed 35-word ones *raises* the finding count, because only Why it's this way, Watch out
  for and How it got here get 60. Worth a founders' look before anyone optimises the number.

### 2026-07-28 — the style pass over `handoff/*` (D21)
> review · docs, all nodes · [D21](../DECISIONS.md)

**Board view.** All four aspect threads were brought under [STYLE.md](../STYLE.md). The three
seeded threads are clean; this file went from 375 findings to a residue that is deliberate.

- **The verbatim board snapshots are quoted, not consolidated.** Both 2026-07-27 board-hygiene
  entries, and the 2026-07-18→19 board view, said consolidation was "a later pass". This was that
  pass, and the answer is no: merging a dated snapshot into surrounding prose deletes record to
  remove duplication. They are now marked as quotations and exempt under STYLE rule 10.
- **`§Open agenda` lost its two delivered items.** ORG says a finished item leaves the board;
  item 0 was ~700 words of delivered planning history, now an archive record.
- **Worklog headings were not touched**, none removed and none altered, so the four
  `DECISIONS.md` anchors and every internal link still resolve.
- **`DECIDED` became `ratified` in six places inside dated entries.** A glossary correction, not a
  fact change: the register has never had a `DECIDED` status.

- **2026-07-28 — worklog entries earn the 60-word bullet cap.**
  - **Was** — rule 6 capped every bullet at ~25 words, which fits a board and not a session
    narrating why it decided something. 44 of this file's 125 bullets sat between 28 and 60.
  - **Changed** — rule 6 now grants 60 words to reasoning, and names a dated worklog entry as
    reasoning alongside Why it's this way, Watch out for and How it got here.
  - **Now** — a worklog bullet may carry a full thought; only the 31 bullets over 60 words are
    findings, and most of those are inside the quoted snapshots.
  - **Payoff** — the rule stops fighting the corpus it governs. Cost accepted: two caps to
    remember instead of one.

### 2026-07-27 (board hygiene) — compressed the service status board
> review · all services

**Was** — every status cell on the founders' service board carried an `Earlier: …` tail
recording how the service reached its current state, so the board had become append-only too.

**Changed** — each cell was compressed to present state, and the full prior cells were kept.

**Now** — the board says where each service is today. Per-service history lives in that
service's own canvas and worklog.

**Payoff** — a reader who wants today's state reads one row rather than a row plus its history.

**The prior board** — *a verbatim snapshot, kept as a quotation under
[STYLE.md](../STYLE.md) rule 10.*

> | Service | Status | Lead session | Canvas |
> |---|---|---|---|
> | Recording | **capture M1 + computer surfaces — alpha complete** (checked gap-detection + VAD-cut chunking + 3 capture clients: phone web / Chrome-MV3 extension / mac CLI, all verified `clean` on real hardware — 2026-07-19; 110 tests) **+ async seam (D16: `dp_state` ledger + `/redrive`) + D9 `/metrics`+dashboard (M6 emission) — 144 tests** | computer-capture → **M6 emission done (merged 2026-07-19)** | [canvas](../services/recording/HANDOFF.md) |
> | Data Processing | **v1 + hardening done: durable ingest journal (kill-recovery; restart-amnesia/false-`gaps` closed) · stage-graph pipeline (every step a drop-in file) · all 3 v1 review findings closed by construction (SlotView slot-ownership · mutate-overlap chaining · permit-at-dispatch fairness) · opt-in subprocess isolation (poison chunk → 1 chunk, not the service)** — on async `/ingest` (D16 wire, off-by-default) + D9 `/metrics`; audio/video byte-identical, real backends re-validated on node-7 (merged `5350f7a`, pushed 2026-07-21; suites re-verified by founders) **+ screen-video clip path BUILT + integrated (WS-VC, 2026-07-25): 8 workstreams landed + merged to trunk — clip-level captioning (one multi-image VLM call/chunk) replaces per-keyframe calls; a dedicated CPU **OCR channel** (`kind='ocr'` record); a **versioned prompt pack whose digest is the dialect**; the **record-vs-mutation law** enforced in CI + at registration; an offline eval harness that cannot write `/context` by construction — behind `VIDEO_PIPELINE=clip` (default `keyframe` = shipped legacy, byte-identical). DP suite **788 + 21 skipped** (2026-07-27; 765 at the WS-VC merge); lead-verified each WS (mutation-tested the law; caught + returned a masked order/registration bug before merge). Cutover gates (O-2 real-frame OCR bar · O-8 blind-vs-injected A/B vs a real VLM · E-2 or fresh user_id · E-3(b)) + follow-ups remain, none blocking** | DP deep session → **merged; M7 substantially done** · screen-video WS-VC → **BUILT + integrated 2026-07-25** | [canvas](../services/data-processing/HANDOFF.md) |
> | Storage | **D18 expansion BUILT + live (2026-07-27) — 310 tests:** C12 profile · training-window ledger + the sole `window_id` minter · day-log materialization (C10 evolved) · C13 registry · C14 reservoir. Day-log byte-identity vs continuum proven over **two window origins incl. a misaligned one**. Earlier: **v0.0 + capture M0 built + integrated E2E** (serve loop + `/raw`/`/context` mock capture loop 2026-07-09; 32 tests post-D17) **+ scope expansion (D18, ratified 2026-07-26; BUILT 2026-07-27):** day-log materialization + training-window ledger (**C10 evolved**) · per-user profile (**C12**, schema minted) · recipe registry (**C13**) · reservoir custody (**C14**). Four new build items, none started | serve + learn → **storage/C10 board done; build slice next** | [canvas](../services/storage/HANDOFF.md) |
> | Input | **v0.0 built + mock loop runs** (integrated E2E 2026-07-09) | serve-loop WS-A | [canvas](../services/input/HANDOFF.md) |
> | Inference | **v0.0 live on real Qwen3-VL-32B** (vLLM TP=8 on node-7, verified E2E 2026-07-09) | serve-loop WS-B | [canvas](../services/inference/HANDOFF.md) |
> | Output | **v0.0 built + mock loop runs** (integrated E2E 2026-07-09) | serve-loop WS-C | [canvas](../services/output/HANDOFF.md) |
> | Continuum | ✅ **learn-loop integration proven END-to-END (2026-07-25).** **CUT over to storage 2026-07-27 — 262 tests:** HTTP clients for C10/C12/C13/C14; `window_for()`/`closed_window_before()`/`Window.local_date` deleted; `--tz` retired for the C12 profile read; a crash now leaves the window open so a retry resumes. `app/morpheus/` + `tests/parity/` byte-unchanged. Live two-process seam green (10 steps/151 checks). *(D18 as originally ratified —* the day-log build leaves for storage (`daylog.py`/`window.py`; the parity-locked `Profile.render_block` **stays**), `window_for()` is deleted for the watermark window, and `nightly.py --tz` is replaced by the C12 profile read. Kickoff → **Morpheus** nightly-consolidation core reimplemented from the research line (`b3c58e1`), parity-proven; **M0** — a 32B life adapter our own pipeline trained → gate → C5 → **served in vLLM**; lean 5-verb loop over storage client seams; **Phase-3 DP dogfood: real Speed data through recording→DP→storage→continuum reproduces the baseline separation (pipeline sound).** Gate policy **v1.1** adopted. Now-pending (board): storage charter expansion + C10 evolution (below) | Morpheus + Phase-3 sessions | [canvas](../services/continuum/HANDOFF.md) |
> | Platform | **v0.0 serve bring-up + learn-loop bring-up** (`run_all.sh` + `run_learn.sh`, both run E2E 2026-07-09) | serve + learn | [canvas](../services/platform/HANDOFF.md) |

### 2026-07-27 (board hygiene) — retired `HANDOFF.md §Next`
> review · all services

**Was** — the board's `§Next` had become append-only history: of its fourteen items, nine were
struck-through `DONE` records or `*Superseded*` notes.

**Changed** — the section was retired here verbatim, so nothing is lost, and the board was
rewritten to carry only genuinely-open items.

**Now** — [../HANDOFF.md](../HANDOFF.md) `§Next` is rewritten in place each session
([ORG.md](../ORG.md) §Documentation protocol), and the retired text is the quotation below.

**Payoff** — the board can be read for what is open without filtering out what is closed. The
retired text stays a quotation under [STYLE.md](../STYLE.md) rule 10 rather than being
consolidated into the dated entries; merging a snapshot into surrounding prose deletes record to
remove duplication.

>
>
> - ~~**next session is prepped:** the storage/C10 board launch prompt~~ **RUN 2026-07-26 → D18.**
>   All four decisions taken plus E-2's disposition; the prompt's `window_id` gate was verified (and
>   three further consumers found), and one of its premises — the `render_block` parity constraint —
>   was corrected. The prep file itself was retired 2026-07-27 once
>   everything in it had a home — decisions in D18/D19/D20, contracts in ARCHITECTURE + `contracts/`,
>   scope in the owning charters, and the prompt-format lessons it proved in
>   [PROMPTS.md](../PROMPTS.md) §D+.
> - ~~**next founder ACT — the storage build slice**~~ **BUILT 2026-07-27** (`a5a48fb` storage ·
>   `1757efb` continuum · `2698b63` data-processing). D20's bar met and verified by the founders'
>   session, not relayed: storage **310** · continuum **251**+7 · recording **144** · DP **788**+21 ·
>   M9 parity **pass over 2 origins incl. misaligned** · live seam **pass, 151 checks, 0 blockers**.
>   Four adversarial rounds ran and every one found something real — the sharpest being that the seam
>   showed 148 green checks *while the shipped default trained on nothing*, because the harnesses
>   proved the paths they exercised and the default was not one of them.
> - ~~**The wipe (D19)**~~ **done 2026-07-27 — the fleet is live on the new code and healthy.**
>   Sequence: fleet stopped → all three stores backed up via SQLite's online-backup API and the
>   backups verified restorable (`/home/ubuntu/nmn/backups/pre-wipe-20260727-155147/`) → stores
>   cleared → restarted on the cutover code. Fresh schema carries `user_profiles`, `training_windows`
>   and `day_logs`. **Proven live, two processes, real ASR:** a `/capture/run` carried 3 chunks
>   through faster-whisper into `/context`; a C12 profile was set and a *missing* one 404'd; a nightly
>   ran over HTTP to **published**; and the three invariants held on the real fleet — the watermark
>   advanced **only** on `published` (the following `skipped_no_data` window did not move it, and the
>   next window opened exactly at the published window's `t_end`), and C5 carried **exactly one**
>   active row. Verification data was then cleared and the fleet restarted clean — note E-2's delete
>   primitive is still unbuilt, so a re-wipe is currently the only way to retract rows.
> - *Superseded:* the wipe was gated on D20 and a final adversarial round. Continuum's
>   `var_dir` and node-7's learn-fleet DBs are experiment output, not user data; lineage restarts from
>   base, which is what makes D18's `window_id` reformat free. Back up first via SQLite's
>   online-backup API (D17 precedent). **Expect, do not debug:** after the wipe a user has no
>   `home_tz`, and D19 removed auto-seed — so the first run needs it set explicitly or that user does
>   not consolidate. Visible by design.
> - *Superseded — the original build-order note:* Order is forced
>   by dependencies, not preference: **(1) C12 profile** — day-log materialization reads it, so it
>   lands first; **(2) resolve the discriminator sub-item** (additive C2 field vs `(chunk_id, kind,
>   t_start)` uniqueness) before any materializer code; **(3) the window ledger + `window_id` minter**
>   (one minter, one validator, `m0_smoke.py` moved onto it); **(4) day-log materialization**, whose
>   exit bar is the **differential byte-equality diff** against continuum's current output — and
>   continuum's local path is not deleted until that diff is green; **(5) C13/C14**, then continuum's
>   cutover (`window_for` deleted, `--tz` replaced by the C12 read, `LocalDayLogClient` retired).
>   E-2 rides M5 whenever it is scheduled — it no longer gates the WS-VC cutover.
> - **D17 follow-through** (the tz path itself is BUILT + verified; these are the pieces that
>   belong to the board, not to a single session): at the **storage/C10 board session** — (1) mint
>   the **per-user profile** contract (`home_tz`) alongside the recipe-registry + reservoir IDs;
>   (2) move **day-log materialization** continuum → storage; (3) switch the cycle window to the
>   **watermark range `[last_trained_t, now)`**, retiring `window_for`'s local-date arithmetic — all
>   three are specified in the ARCHITECTURE C10 row + the storage charter. Then at the **C5 freeze**,
>   stamp the resolved tz into the run report + C5 entry (today `training_window:"w-day5"` records
>   nothing about the zone it was derived under, so a wrong-tz adapter is unfalsifiable after the
>   fact).
> - **C5 freeze — inherited constraint (from review item O-2, 2026-07-26; C5 deliberately not frozen
>   that session).** C5's four descriptions are now correct and explicitly labelled *"as built, not
>   frozen"* — nine fields, and `status` ∈ `active | gate_failed | rolled_back`. The freeze session
>   inherits one thing that is **more than documentation**: `gate_failed` (the audit row for a
>   candidate the gate blocked) constrains the storage build, because storage's `model_directory` is
>   still only the trivial C6 row (`user_id, model_id, adapter, adapter_path`) — no entries log, no
>   status column, so hosting C5 is a build, not a transport swap. Three constraints, written into
>   the [storage charter](../services/storage/CHARTER.md) model-directory row: a **three**-value status
>   enum (or `record_gate_failure()` has nowhere to land); **nullable** `adapter_dir` +
>   `base_model_hash` (gate-failed rows carry NULLs there); and C6 eligibility as a **log replay**,
>   not "latest row wins" — else a gate-failed candidate becomes servable, the exact ungated swap the
>   gate prevents. Smaller + unblocked: **E-4**'s per-fragment local timestamps are now a pure continuum
>   renderer change; and DP **OQ9**'s residual — a `/metrics` counter on `unsynced` chunks +
>   `|ingest_time − t_end|` outliers, so clock skew is *measured*, not just detectable.
> - ~~**Fleet note (D17):** node-7's DBs predate the new columns~~ **done 2026-07-26 — the learn
>   fleet was restarted onto the new code and the migrations are applied.** Both live DBs were
>   backed up first via SQLite's online-backup API (`/home/ubuntu/nmn/backups/pre-d17-20260726-211912/`).
>   Verified after restart: `context_records` gained `device_tz` + `device_utc_offset_minutes` with
>   **125/125 rows intact and all NULL** (no backfill, as designed — a record captured before clients
>   reported a zone genuinely has none); `segments` gained both columns with **40/40 rows intact**.
>   The long-pending **`dp_state` migration also ran**, correctly backfilling all **68 chunks** to
>   `processed` — the running fleet had predated the D16/v1/hardening merges since 2026-07-19, so
>   this restart collected those too. Live checks: the capture wire **accepts** `device_tz=Asia/Tokyo`
>   and persists it, and **rejects `PST` with 400** ("must be an IANA zone id"); a real `--smoke`
>   capture run carved 3 chunks through faster-whisper ASR → C2 → `/context` (headless `/capture/run`
>   has no reporting device, so those records correctly **omit** the fields rather than nulling them).
>   Verification rows were removed; ledger is back to its 40-segment baseline. All three services
>   healthy (storage 8083 · DP 8085 · recording 8084). **Still off by default:** `INGEST_ASYNC`,
>   `INGEST_ISOLATION`. The serve loop (vLLM + app services) remains down.
> - ~~Recording-led capture M1~~ **done + alpha complete 2026-07-19** (see Current state above /
>   the recording canvas). Gap-detection enforced, ASR pipeline standing, three capture surfaces
>   verified `clean` on real hardware. Consent gate stayed back-burner per D13.
> - ~~DP-led deep session~~ **done + merged 2026-07-19 (`0ce4941`, founders' review passed):**
>   async `/ingest` behind `INGEST_ASYNC` (off = inline byte-identical; **D16 wire implemented
>   verbatim incl. the re-drive condition** — `/capture/sessions/{id}/redrive` + emitter re-push
>   + 2 drill tests) · D9 emission on both services (`/metrics` + dashboard JSONs, zero new
>   deps) · all 3 real audio backends smoke-tested green on node-7 (+2 real pyannote fixes) ·
>   OQ13 resolved + **OQ3 answered per-modality** (no ladder: 16 kHz mono audio is model-native;
>   video container-copy — resolution-bound not bitrate-bound, ~2560 px only for OCR-heavy
>   screens; cost dial = keyframe cadence). Suites re-verified independently by the founders'
>   session: **DP 98 · recording 120 · storage 26**. Honest residuals (ws file): DP-restart
>   false-`gaps` window fails safe (**now closed by the v1 durable journal, 2026-07-20**);
>   whisper-translate unproven on a genuine non-English source; pyannote pinned 3.1.1, smoked
>   3.3.2. *(This slice was superseded by DP v1 + hardening — see the current-state entries
>   above; residuals tracked there.)* **Fleet note:** node-7
>   still runs pre-merge code — restart `run_learn.sh` at convenience to start emitting
>   `/metrics` (async stays off by default; flipping `INGEST_ASYNC=1` retires
>   `RECORDING_HTTP_TIMEOUT=120`).
> - **Now (D15):** (1) **continuum kickoff** — the next founders-led slice; first act:
>   storage × continuum propose the **C10 v0 freeze** (founders ratify), then a charter-M0 plan +
>   workstreams. Kickoff deliberately forces the cluster-split (nightly window) and DP
>   reprocess-policy (OQ5) conversations; the parked **D6 OCR spot-check** rides the vLLM
>   relaunch continuum-era eval needs anyway. (2) **Platform D9 backbone** as the small parallel
>   slice — the one shared Prometheus + Grafana scraping the new `/metrics`, provisioning both
>   dashboards + node/dcgm exporters, closing D9 end-to-end. Image/text DP pipelines stay
>   **deferred until a producing surface exists** (D15).
> - **Beta hand-off (D12):** standing `dev` branch forked from `main` for Gnandeep — serve loop
>   (mock or real backend) + learn loop (real faster-whisper ASR, `ASR_LANGUAGE=en`) both run today;
>   storage's `/context` range read is his training-window feed for the black-box fine-tuning tests
>   until C10 lands. The three capture clients (`/capture/*` wire, tunnel URL from
>   `services/recording/var/tunnel_url.txt`) are the beta's data-collection front door.
> - CTO to read the Platform charter internals when time allows (D1).
> - **Fleet status (2026-07-19):** the **learn loop is UP on node-7** — storage:8083 ·
>   data-processing:8085 (`ASR_BACKEND=faster_whisper`, `ASR_LANGUAGE=en`) · recording:8084, plus
>   the cloudflared tunnel for the capture clients (URL rotates per restart →
>   `services/recording/var/tunnel_url.txt`); `run_learn.sh --status` checks it. The **serve loop
>   (vLLM + app services) is down** — relaunch `run_all.sh` + `services/inference/serve_vllm.sh`
>   when needed. The wider cluster runs Gnandeep's continuum-side experiments — product work keeps
>   to **node-7**; allocate more nodes on demand. *Learn loop re-verified up by the 2026-07-19
>   sequencing session. Post-merge: the running fleet predates all three DP merges (`0ce4941`
>   async, `86acb95` v1, `5350f7a` hardening) — restart to start emitting `/metrics` + gain the
>   durable journal + isolation knob (behavior otherwise unchanged; `INGEST_ASYNC` +
>   `INGEST_ISOLATION` both off by default). WHO restarts DP (supervisor/deploy) is an open M7
>   ops item with platform.*
>
### 2026-07-27 (close-out) — seam shipped, fleet live, review backlog empty
> review · continuum × storage · [D20](../DECISIONS.md)

**Was** — an adversarial pass against the real services proved three defects no suite caught.

- **H1** — the publish tail was not atomic: two `active` C5 rows for one window, and one
  `rollback()` flipping the alias to the *same* adapter, leaving the only safety net a bad adapter
  has silently dead. The worse branch was a C14 append-only 409 stranding a night forever.
- **H2** — the replay pool filtered on ledger *state* and never *outcome*, so nights that never
  entered the adapter were rehearsed. Measured at 50% of a night's budget, re-teaching text already
  in that night's fresh corpus.
- **M1** — one window could strike twice and freeze a user.

**Changed** — fixed at the source, not at the call sites: `publish()` idempotent with one live
activation per window · a reservoir conflict non-fatal · `prior_windows` published-only · `debt`
membership as the strike guard. 11 tests, each proven to fail without its fix.

**Now** — the fleet is cut over: stopped, three stores backed up and *verified restorable*,
cleared, restarted on the new code. Proven live rather than in tests:

- real capture through faster-whisper into `/context`;
- a C12 profile set, and a missing one 404'd;
- a nightly to **published** over HTTP, with the watermark advancing only on `published`;
- exactly one active C5 row.

All eleven accuracy-review items are closed (O-1 → D17, O-2/3/4 → 07-26, O-5…O-11 today), storage
OQ7 is resolved, and OQ9 is re-scoped now that `day_logs` is a real table.

**Payoff** — the review backlog is empty, and two false claims of my own are retracted rather than
caveated: D19's "the min-data-floor mechanism exists", which appears nowhere in the repo, and a
parity pass I reported before it covered a misaligned origin.

**Watch out for**

- `seam_check` was asserting H2 as correct behaviour — green while pinning the bug. That is the
  second harness this week to bless a defect, after the `recipe_id` test, and it is why the review
  rounds kept finding what the suites could not.
- Every serious defect this week started as a document disagreeing with the code.

### 2026-07-27 (overnight) — the D18 storage expansion is BUILT; the seam is closed
> build · storage × continuum × data-processing · [D19](../DECISIONS.md) · [D20](../DECISIONS.md)

**Was** — D18 was ratified and unbuilt. Continuum still owned the day-log, `window_for()` derived
the training window from a local date, and `nightly.py --tz` carried the timezone.

**Changed** — the expansion was built and the storage↔continuum seam closed, carried by two
founders' decisions. D19 set the prototype posture plus seven calls. D20 narrowed M9's parity bar
after it failed and added a *definition of done*, because the original wipe gate ("no defects, no
artifacts") is unfalsifiable and would justify reviewing forever. Commits `a5a48fb` storage ·
`1757efb` continuum · `2698b63` data-processing.

**Now** — storage owns the day-log, the training-window ledger and the `window_id` minter, and
hosts C12/C13/C14; continuum issues a warrant for `(user_id, window_id)` and takes what comes
back. `window_for()`, `closed_window_before()`, `Window.local_date` and
`ReservoirEntry.local_window_date()` are deleted, and with them `cycle.py`'s reconstruction of
prior windows under *tonight's* timezone. `nightly.py --tz` is retired for the C12 profile read.

D20's bar, verified by the founders' session rather than relayed: storage 310 · continuum 251 +7
skipped · recording 144 · data-processing 788 +21 skipped · M9 parity pass, 31 binding checks
over 2 window origins including a misaligned one (exit 0) · the live two-process seam pass, 10
steps / 151 checks, zero blockers (exit 0) · `app/morpheus/` and `tests/parity/` byte-unchanged.

**Payoff** — recipe `consolidation-v1.1` is minted and both services re-pinned. It forks v1.0 on
exactly one knob, `replay.source` `amp` → `rawlog`, proven by diffing the artifacts. Under `amp`
only a user's first night could run over HTTP, because amp pools amplified corpus *bodies* while
C14 serves a *ledger* by design. v1.0 is retained, being the recipe the Phase-1/Phase-3 numbers
were produced under.

**Watch out for**

- **Four review rounds ran and every one found something real.** Recorded because the pattern is
  the lesson, not the individual bugs.
  - Round 1 — the day-log stamped a `recipe_id` whose knobs it never used, so a night was
    auditable as trained under a recipe it was not.
  - Round 2 — a corrected `home_tz` never re-materialized the cached day-log, and a naive `now`
    read as server-local.
  - Round 3 — a crash *closed* the window, so a retry minted a *second* `window_id`. Measured: a
    full re-train, 2 C5 entries, 2 reservoir admissions.
  - Round 3 — the *default* nightly path fed ingest-time bounds into an event-time query.
    Measured result: an empty day-log, so the shipped default trained on nothing.
  - Round 3 — the M9 proof held only for a *grid-aligned* window origin, which no real window has.
- **Round 3's middle finding is the one to remember.** The seam had 148 green checks *while the
  default configuration silently trained on nothing*: green harnesses proved the paths they
  exercised, and the default was not one of them.
- **Two corrections to my own prior claims**, both recorded rather than quietly fixed. I reported
  the parity proof as pass when it covered only an aligned origin, a stronger claim than the
  evidence supported; and my watermark refinement reached three of four sites, leaving
  ARCHITECTURE (the doc named authoritative) contradicting the code.
- **A cross-service coupling nobody knew about.** Deleting `window_for()` broke
  *data-processing*: `scripts/prompt_ab.py` imported continuum's `app/window.py` across the
  service boundary. DP's suite caught it (788 → 787). Fixed by building the `Window` value object
  inline, reproducing the old 04:00Z/24 h semantics exactly, because those numbers are compared
  across runs.
- **On test staleness (CTO, 2026-07-27).** Tests must evolve and deleting is sometimes right, but
  "eliminate staleness" collapses into "make it green" unless you can name which case you are in.
  All four cases occurred here.
  - *Stale assertion* → two storage tests pinning `recipe_id == "consolidation-v1.0"`, rewritten
    to assert the contract (`== daylog_recipe_id()`).
  - *Deliberate behaviour change* → the crash test, whose assertion was inverted.
  - *Test right, code broken* → DP's, which deleting would have hidden.
  - *Behaviour genuinely gone* → the `window_for` tests.
- The recipe re-pin is deliberately two-sided: storage stamps the day-log's `recipe_id` and
  continuum records C5 lineage, so a one-sided re-pin trains under a recipe the artifact is not
  labelled with.

**D20 as ratified — full text.** *Relocated 2026-07-27 from the HANDOFF.md decisions log, now the register at [../DECISIONS.md](../DECISIONS.md); the D20 row there carries the headline and points back here.*

> **The exit bar for the storage↔continuum cutover — and a definition of "done" that can actually be
> met.** Two parts. **(a) M9's parity bar narrowed, after the first run failed it.** The bar as first
> written contradicted D18's own materialization rule and no code could satisfy both: continuum's
> `seg_id` is `floor((t − window_start)/segment_seconds)` over an event-time origin, while D18 deletes
> the window origin from storage's grid and puts the window on the ingest axis, where a backlog record
> yields a negative index. The narrowed bar has three tiers — **byte-identical** (block `text`,
> ordering, `block_id`, `anchors`, `quality`, segment payloads: the artifact that trains the model) ·
> **proven-equivalent** (`seg_id`: an order-preserving bijection with per-block membership preserved,
> *measured* not assumed — it is written to `segments.jsonl` and read by nothing, since the trainer
> consumes `blocks.jsonl`) · **excluded** (`content_fingerprint`, which hashes `seg_id` and is a cache
> key compared only to itself; forcing it to match would make the cache lie). The general rule this
> instantiates, now pinned in §Ownership splits: **storage owns the day-log's representation outright;
> its content is a contract neither service may move alone** — *if the trainer can see it, it is
> contract; if only storage can see it, it is storage's*. **(b) "Golden" defined**, because the
> founders' wipe gate ("no defects, no artifacts") is unfalsifiable as written and would justify
> reviewing forever: **all four suites green · the M9 proof green over a real, misaligned window
> origin · the live two-process seam green with zero blockers · one adversarial round returning
> nothing high-severity.** Hit that and we wipe and take the first clean run; anything found
> afterwards is a follow-up slice, not a hold on the cutover — because past that point the honest way
> to find the remaining bugs is to run the thing on real data, not to review it a fourth time

### 2026-07-27 — D19: the stage is PROTOTYPE, and the docs now say so
> decision · all services · [D19](../DECISIONS.md)

**Was** — every canvas in this repo was written in a production voice, so an agent or a new
teammate read it and built for durability we have not earned.

**Changed** — the stage is named PROTOTYPE, globally (ARCHITECTURE §Stage, ORG §Stage) and
locally, with a banner on all eight service charters.

**Now** — the posture licenses re-cutting contracts rather than versioning them, wiping data
rather than migrating it, and deferring durability work with the reason recorded. It explicitly
does *not* license skipping ORG's contract-edit order, leaving a decision unwritten, or calling a
thing BUILT when it is ratified.

**Seven calls taken under it.**

- **Retention → keep everything**, but the *knob ships and the policy does not*: a versioned
  per-store retention document, every store `keep_forever`, read and surfaced on `/metrics`, and
  no sweeper built. Rules mark *eligibility* while an explicit sweep acts and writes a manifest,
  so a bad config edit can produce a wrong report and never silent data loss.
- **Storage tech → local now, option (c) later**, kept cheap by a rule rather than by foresight:
  every new store goes behind a narrow interface from day one, the shape continuum already proved
  client-side.
- **C2 `discriminator` → surfaced** rather than inferred from `(chunk_id, kind, t_start)`
  uniqueness, taking the option that cannot rot now that contracts are explicitly not frozen. It
  adds no promise, since DP already rejects duplicates within a chunk, and `record_id` is
  unchanged, so nothing re-keys.
- **Existing state → wiped, not migrated**, which is what makes D18's `window_id` reformat
  genuinely free.
- **Cycle trigger → cron at the user's `home_tz` boundary**, interval configurable.
- **Materialization → on demand at fetch**, buying a slow first fetch to delete a whole
  scheduler. The min-data floor becomes a recipe knob in *characters of block text*, default 0.
- **C5 freeze → deferred**, free because C5 is unfrozen, with one standing rule recorded for
  whoever freezes it: `training_window` must be frozen opaque, never as a date.

**Payoff** — two corrections to D18 came out of the CTO's answers, and both are recorded rather
than quietly applied.

- **`home_tz` is declared, not inferred.** D18's first draft had storage auto-seed it from the
  first device-reported `device_tz`. Wrong: a guessed zone and a chosen zone are different facts,
  and only the second can be corrected by the person who knows.
- The CTO's question was whether it should change when the user travels. Exactly one answer is
  right, **no**, and it is the whole point of D17's fact/policy split.
- A week in Tokyo moves every record's `device_tz` and moves nothing here, so the boundary stays
  put instead of jumping 9 h and producing a 15 h night followed by a 33 h one.
- **The watermark advances if and only if a cycle publishes.** D18 also advanced on
  `skipped_no_data`. My own refinement, taken because the min-data floor forces it: a below-floor
  night must not advance, or the material is lost.
- Unifying gives one sentence covering gate failure, freeze, crash, no data and too-little data,
  and it makes the name literally true — `last_trained_t` is the high-water mark of what has
  actually been trained. Named cost: an inactive user's open window grows and is re-scanned
  nightly, which is correct and cheap.

**Watch out for**

- The tree was split into two commits — `6bb8f4a` (the O-2/O-3/O-4 doc slice) and `b96a1b0`
  (D18) — but perfect hunk attribution was not achievable.
- The two overlapping sessions' edits had merged into shared hunks in four docs, so the split is
  at file granularity, and `b96a1b0`'s message says so rather than implying a cleanliness it does
  not have.

**D19 as ratified — full text.** *Relocated 2026-07-27 from the HANDOFF.md decisions log, now the register at [../DECISIONS.md](../DECISIONS.md); the D19 row there carries the headline and points back here.*

> **Stage: PROTOTYPE. Nothing is set in stone — contracts included — and the docs must say so.** The
> founders' read is that every canvas in this repo is written in a production voice, so an agent or a
> new teammate reads it and builds for durability we have not earned yet. The correction is a standing
> posture, announced at global *and* local level (§Stage in [ARCHITECTURE.md](../ARCHITECTURE.md) +
> [ORG.md](../ORG.md), and a banner on **every** service CHARTER). **What the posture licenses:**
> re-cutting a contract instead of versioning it; **wiping and re-collecting** stored data instead of
> migrating it; deferring durability work with the reason written down. **What it does not license** —
> and this is the half that keeps it honest: skipping [ORG.md](../ORG.md)'s contract-edit order,
> undocumented decisions, silent breakage, or "prototype" as an excuse for a thing we know is wrong.
> Seven calls taken under it: **(1) retention = keep everything**, and the *mechanism* ships even
> though the *policy* does not — a versioned per-store retention document that storage reads and logs,
> every store set to `keep_forever`, **no sweeper built**. Retention rules mark *eligibility*; a
> separate explicit sweep acts and writes a manifest, so a config edit can never silently delete data.
> This is what makes the dev/prod retention decision a config change rather than an archaeology
> project. **(2) storage tech: local now** (SQLite + filesystem), **Postgres + GCS later, option (c)**
> — metadata in Postgres, day-logs/corpora in GCS. The migration is kept cheap by one rule, not by
> foresight: every new store goes behind a **narrow interface** in storage from day one, exactly as
> continuum already did on the client side, so the swap is a backend change. **(3) The C2
> `discriminator` is surfaced** (additive-optional, `contracts/c2_processed_record.v0.json`) rather
> than inferred from `(chunk_id, kind, t_start)` uniqueness — the option that cannot rot, taken
> because contracts are not frozen in this stage. It adds no new promise: DP already rejects duplicate
> discriminators within a chunk (`stagegraph/executor.py:396-401`), and `record_id` is unchanged, so
> nothing re-keys. **(4) existing state is wiped, not migrated** — the fleet's stores are experiment
> output, not user data. *(Scope corrected 2026-07-27 after measuring: the five continuum `var_dir`
> sub-directories named in this row **do not exist**, so that half is a no-op, while `continuum/var/`
> does hold **66 GB of Phase-1/2/3 research evidence** which must not be deleted. The wipe is the
> three fleet SQLite DBs only — and it is cleanliness, not correctness, since the migrations are
> additive.)* Lineage restarts from base. This is what makes D18's `window_id` reformat free: no
> mixed-format ordering to defend, no seed-discontinuity to reconcile, and the two `w-day5` C5 rows
> disappear rather than needing a rule. **(5) cycle trigger: a per-user cron at their `home_tz`
> boundary, interval configurable in the service** — today's human-run CLI is the prototype stand-in,
> not the design. **Materialization is on demand at fetch**, deliberately buying a slow first fetch to
> delete an entire scheduler and its failure modes. The **min-data floor** becomes a recipe knob
> (`min_block_chars`, in *characters of eligible block text* — Phase-3 showed recall tracks retellings
> per unit of text, not block count), **default 0** = today's behaviour; D18's advance-only-on-publish
> rule makes a below-floor night carry forward for free. *(**Corrected 2026-07-27:** this row
> originally said the mechanism existed and only the value was a config change. It does not —
> `min_block_chars` appears nowhere in the repo. The design stands; the build does not exist. Caught
> by an adversarial round, not by a test, and retracted here rather than caveated.)* **(6) C5 freeze
> Deferred** — its only consumer is inference (C6 resolve), which we are not building; continuum's
> local `entries.jsonl` carries the lifecycle meanwhile. Cost is zero *because* C5 is unfrozen, so
> `training_window`'s D18 format change costs nothing now. **One standing note for whoever freezes it:
> `training_window` must be frozen as an opaque token, never as a date**, or the parsing D18 just
> deleted grows back. **(7) `home_tz` is declared, not inferred** — this **overturns D18's own first
> draft**, which had storage auto-seed it from the first device-reported `device_tz`. The user sets
> it; a client may *suggest* the device zone in a UI, but a guess is never stored as though it were an
> answer. It follows that **`home_tz` does not move when the user travels** — a week in Tokyo changes
> every record's `device_tz` and changes nothing here, so the night boundary stays put instead of
> jumping 9 h and producing a 15 h night followed by a 33 h one. That is precisely the fact/policy
> split of D17 doing its job

### 2026-07-26 (later) — founders' storage/C10 board (D18)
> decision · storage × continuum · [D18](../DECISIONS.md)

The storage scope expansion and the C10 evolution were ratified. Five decisions came out of one
session, so this entry carries five blocks. **Everything below is ratified, not BUILT** — stated
that way from the first draft rather than corrected in afterwards, which is the O-12 lesson
applied prospectively.

**The gate, first — because it decided the rest**

**Was** — the launch prompt asserted five `window_id` claims and one hard constraint, none of
them verified.

**Changed** — all five claims were checked in code before anything was decided, and so was the
constraint.

**Now** — all five hold, and three further consumers the prompt did not list were found.
`publish.py:106` is a *fourth* string comparison, the alias-monotonicity guard itself, where the
prompt named only `active_before` at `:83`. `window.py:44` and `reservoir.py:65-69` *parse the id
back into a date*, which `cycle.py:217` then uses to rebuild prior windows under tonight's
timezone.

**Payoff** — a premise of the prompt was wrong, and correcting it made the session easier rather
than harder. The stated hard constraint — "`render_block` must stay byte-parity with the research
line @ `b3c58e1`; moving the renderer must not break it" — conflates two functions of the same
name.

| | Locked by | Consumes | Verdict |
|---|---|---|---|
| `Profile.render_block` (`morpheus/profiles/speed.py:89`) | `tests/parity/test_render_block.py`, 1427/1427 vs research goldens | 5-min description dicts | **recipe-coupled — stays** |
| `daylog.py:183 _render_block` | nothing; no golden has ever existed | C2 records | **moves to storage** |

The research line never materialized the 10 s-segment / 2 min-block schema — continuum's own
canvas says "zero producing code; a research 'block' = one 5-min description" — so the product
renderer could not have had a golden. `morpheus/blocks.py:5-7` had already drawn the boundary:
*"keeping that boundary narrow is what lets the day-log move behind a storage client without any
kernel noticing."*

So the move cannot break research parity. The bar became a **differential byte-equality** against
our own current output for a real window, script and result committed, on DP's byte-identity
precedent, with continuum's local path not deleted until that diff is green. That is strictly
more falsifiable than re-running a golden suite that never covered this code.

**0 · `window_id` becomes an opaque token**

**Was** — `window_id` was a local date: string-compared for ordering in four places, and parsed
back into a date in three more.

**Changed** — the format becomes `w<YYYYMMDD>T<HHMMSS>Z`, minted once from the window's **end
instant**, and parsed by nobody. Seconds rather than minutes, because a truncating id can
silently collide two windows.

**Now** — the durable output is one minter and one validator. `w-day5` is ruled a mess and not a
precedent: `m0_smoke.py:133` breaks the total order twice over.

**Payoff** — the format change is a *consequence* of the watermark window, not a cost of it,
because `[last_trained_t, now−δ)` has no local date to name. Re-keys are enumerated in full in
D18, including the training seed (`cycle.py:147`), accepted as a real discontinuity rather than
papered over. `tests/parity/` is unaffected, because it seeds from its own harness.

**1 · C12, C13 and C14 minted**

**Was** — there was no home for per-user policy, no recipe registry, and no reservoir custody.

**Changed** — C12 is a per-user *profile*, fenced to system-read policy values, carrying
`home_tz` only in v0, **404 on absence**, and declared rather than inferred: the user sets it and
storage never writes it unprompted. C13 (recipe registry) and C14 (reservoir) are minted
alongside it.

**Now** — C12's schema is written (`contracts/c12_user_profile.v0.json`). C13's and C14's land
with the build slice, per `contracts/README.md`'s own rule.

**Payoff** — declared-not-inferred was corrected into D18 the same day. The first draft had
storage auto-seed from the first device-reported zone, which would have let a guess masquerade as
an answer.

**2 · The day-log moves to storage**

**Was** — continuum materialized the day-log locally.

**Changed** — materialization moves to storage.

**Now** — storage owns the day-log, and continuum fetches it.

**Payoff** — the decisive argument is **replay**, not tidiness. Replay re-reads *prior* day-logs
nightly, so a continuum-side builder re-pulls every prior day's raw records every night — O(days²)
across the wire, to rebuild what storage could have kept.

**3 · The window watermarks on `ingest_time`**

**Was** — the cycle window was derived from a local date, and late data was an open question.

**Changed** — the window watermarks on `ingest_time`, which *dissolves* late data rather than
handling it. `last_trained_t` advances only on `published` / `skipped_no_data`.

**Now** — the design-of-record's failed-day merge is structural, and `_UserState.debt` is demoted
to reporting. Reprocessed records resolve latest `ingest_time` wins per
`(chunk_id, kind, discriminator)`.

**Payoff** — the resolution key is chosen on evidence: on `ingest_time` because
`pipeline_version` is a *composed* string and not orderable, and on `kind` because Phase-3 proved
captions and transcripts share one.

**4 · E-2 demoted, not dropped**

**Was** — E-2 gated the WS-VC cutover, on the argument that it was what fixed the double-count.

**Changed** — the one-dialect materialization rule is what actually fixes the WS-VC
double-count, so E-2 stops gating the cutover.

**Now** — E-2 reverts to the retraction, privacy and space primitive it always was, and its shape
rides storage M5.

**Payoff** — the cutover is no longer blocked on a primitive that was never the fix.

**Watch out for**

- **Two obligations this session opened rather than closed**, recorded because a decision session
  that only closes things is not being honest.
- **The day-log and the reservoir are second copies of user content**, so M5's deletion must
  cascade to both. A retraction that clears `/context` and leaves a day-log standing has deleted
  nothing.
- **The within-chunk discriminator is not independently readable from C2** — it lives only inside
  the `record_id` hash. The build must either surface it as an additive-optional C2 field
  (ARCHITECTURE → schema → *both* `extra="forbid"` mirrors, the exact D17 trap) or prove
  `(chunk_id, kind, t_start)` unique per dialect. Do not start the materializer before that is
  chosen.
- **"Storage OQ8" does not exist.** Blob-by-reference is *recording's* OQ8
  (`recording/CHARTER.md:175`); the mislabel originated at `ws-phase3-dogfood.md:55` and had
  propagated into this session's own launch prompt. Storage's OQ list now carries a note that OQ
  numbers are stable ids and are never renumbered.
- No code changed and no suite was run this session. Baselines stand as of D17: storage 32 ·
  continuum 189 · recording 144 · DP 770 (+21 skipped) · extension deno 11.

**Board view** — *merged 2026-07-27 from the retired `HANDOFF.md §Current state`, which had become a second worklog. Kept verbatim; consolidation with the text above is a later pass.*

> 2026-07-26 (founders' storage/C10 board — **D18: the day-log moves, the window becomes a
> watermark, `window_id` stops meaning anything**): the two items queued twice — by the 2026-07-25
> learn-loop close-out and by D17 — were ratified together, and **all of it is ratified, not BUILT** *(true as written; **built 2026-07-27** — see the close-out entry below)*
> (the D17/O-12 discipline, applied from the start rather than corrected into the row afterwards).
>
> **Verified before deciding, because the gate demanded it:** all five `window_id` claims hold —
> it is a path component under `ids.py:8`'s regex (which a raw RFC3339 instant fails, on colons),
> a string-compared total order, the training seed (`cycle.py:147`), embedded in `seg_id`/`block_id`,
> and C5 lineage. **Three more the launch prompt did not list:** `publish.py:106` is a **fourth**
> string comparison — the alias-monotonicity guard itself, not just `active_before` — and
> `window.py:44` + `reservoir.py:65-69` **parse the id back into a date**, which `cycle.py:217` then
> uses to rebuild *prior* windows under *tonight's* timezone.
>
> **One premise of the launch prompt was wrong.** Its hard constraint ("`render_block` must stay
> byte-parity with the research line; moving the renderer must not break it") conflates two
> different functions. `tests/parity/test_render_block.py` locks **`Profile.render_block`**
> (`morpheus/profiles/speed.py:89`) over 5-min description dicts — recipe-coupled, and it **does not
> move**. What moves is `daylog.py:183 _render_block`, the product renderer over C2 records, which
> **never had a research golden** (the research line never materialized the 10 s/2 min schema).
> `morpheus/blocks.py:5-7` had already drawn the boundary. The move therefore cannot break research
> parity — so the bar became a **differential byte-equality** against our own current output, which
> is a stronger and more falsifiable test than the one that was asked for.
>
> **Decided (full text in D18 above):** `window_id` → `w<YYYYMMDD>T<HHMMSS>Z`, minted once from the
> window's **end instant**, parsed by nobody, with one minter + one validator (because `w-day5` in
> `m0_smoke.py:133` proves the ordering invariant is only as strong as the discipline that mints
> ids) · **C12/C13/C14 minted**, C12's schema written · the day-log moves to storage, decisively
> because **replay** would otherwise re-pull every prior day's raw records every night (O(days²)) ·
> the window becomes `[last_trained_t, now−δ)` on **`ingest_time`**, which dissolves late data
> instead of handling it, and `last_trained_t` advances **only** on `published`/`skipped_no_data`,
> making the failed-day merge structural · **E-2 demoted** from cutover blocker, because the
> one-dialect materialization rule is what actually fixes the double-count.
>
> **Two new obligations this session created rather than closed:** deletion must now **cascade to
> the day-log and the reservoir** (each is a second copy of user content — M5 widened), and the
> **within-chunk discriminator is not readable from C2**, which the build slice must resolve before
> the materializer starts. Also corrected: "storage OQ8" (blob-by-reference) does not exist — that
> is **recording's** OQ8, and the mislabel had propagated from `ws-phase3-dogfood.md:55` into this
> session's own launch prompt.
>
> **No code changed. Suites unchanged and unrun this session** (baselines stand: storage 32 ·
> continuum 189 · recording 144 · DP 770 +21 skipped · extension deno 11).

**D18 as ratified — full text.** *Relocated 2026-07-27 from the HANDOFF.md decisions log, now the register at [../DECISIONS.md](../DECISIONS.md); the D18 row there carries the headline and points back here.*

> **Storage owns the day-log; the training window becomes a watermark over OUR OWN ingest clock;
> `window_id` stops meaning anything.** Ratifies the 2026-07-25 storage-charter expansion and the C10
> evolution, and completes D17's three deferred clauses. **Status as ratified: All five parts
> *ratified, not BUILT*** — **all five BUILT 2026-07-27** (`a5a48fb` · `1757efb` · `2698b63` ·
> `38479df`), D20's bar met and the fleet cut over. The row keeps its original status because the
> Decisions log is the historical record; the build is recorded here rather than by rewriting what was
> decided. Nothing below ships until it is built, and the day-log's byte-equality is *proven* rather
> than asserted. **(0) `window_id`** — an **opaque, path-safe, lexicographically-ordered per-user
> token**, `w<YYYYMMDD>T<HHMMSS>Z` (e.g. `w20260721T110000Z`), minted **once** from the window's **end
> instant** by storage and **parsed by nobody**. Seconds, not minutes, because a truncating id can
> silently collide two windows and an id collision corrupts the journal, the reservoir and C5 lineage
> at once. The format change is **not a cost of the watermark window — it is a consequence of it**:
> under `[last_trained_t, now−δ)` there is no local date to name (a window can span 23 h, 25 h, or 47
> h after a missed night), so keeping `w<local-date>` would mean synthesising a local date purely to
> name a window, reintroducing the timezone we just proved the query never needed and making the id
> lie about the window's extent. *Re-keys, in full:* filesystem paths (orphaned, not corrupted — no
> migration needed for correctness); the **four** string comparisons (`publish.py:83` **and `:106`,
> the alias-monotonicity guard itself**, `cycle.py:106,115`, `reservoir.py:105`) — which stay correct
> only if one user's history uses one format, since mixed formats order correctly by ASCII accident
> alone; the **training seed** (`cycle.py:147`), so a night re-run across the change is **not
> apples-to-apples** — accepted deliberately rather than re-pinning the seed on a value that is itself
> changing, and `tests/parity/` is unaffected because it seeds from its own harness;
> `seg_id`/`block_id` (`daylog.py:88,206`); and **C5 `training_window` lineage forks** (small today).
> *Verdict on `w-day5`:* **a mess, not a precedent** — `scripts/m0_smoke.py:133` writes it without
> ever calling `window_for`, and it breaks the total order twice (`w-day10` < `w-day5`; all `w-day*`
> sort below all `w2026-*`). The durable fix is therefore **one minter + one validator**, not a format
> preference. **(1) C12 — per-user profile** (`contracts/c12_user_profile.v0.json`, the one schema
> minted today): a **profile, not a settings blob** — values the *system* reads to decide its own
> behaviour (scheduling, fallbacks), never user-facing identity, which is input's. v0 carries
> **`home_tz` only**; `boundary_local_time` is named as the expected second field and deliberately
> **not** minted until it has a consumer (E-5 precedent). **404 on absence, no server-side default
> anywhere** (D17), so a user without `home_tz` is *not schedulable* — an operational alert, never a
> silent skip. **Auto-seeded from the first device-reported `device_tz`, never auto-updated**: a
> traveller's night boundary must not chase their device, which is the whole point of the fact/policy
> split. **(2) The day-log moves to storage** — decisively because of **replay**, not tidiness: replay
> re-reads *prior* day-logs nightly, so a continuum-side builder re-pulls every prior day's raw
> records every night, O(days²) across the wire to rebuild what storage could have kept. **A premise
> of the launch prompt was wrong and is corrected here: there are two `render_block`s and only one
> moves.** The parity-locked surface is **`Profile.render_block`** (`morpheus/profiles/speed.py:89`,
> 1427/1427 against research goldens over 5-min description dicts) — it is *recipe-coupled* and
> **stays with the amplifier**; what moves is `daylog.py:183 _render_block`, the product labeled-lines
> renderer over C2 records, which **has never had a research golden** because the research line never
> materialized the 10 s/2 min schema. `morpheus/blocks.py:5-7` had already drawn this line. So the
> move **cannot** break research parity; the bar it must clear instead is a **differential
> byte-equality** against our own current output for a real window, script and result committed (DP
> byte-identity precedent), with continuum's local path **not deleted until that diff is green**.
> **(3) Watermark semantics — the session's real design work.** The window watermarks on
> **`ingest_time`**, not event time: this *dissolves* the late-data question rather than answering it,
> because `ingest_time` is assigned by storage at write, so a record can never land below a closed
> boundary — **late data cannot exist on this axis** (`δ`, default 60 s, covers in-flight writes
> racing the boundary). Content stays event-time-correct because blocks form by temporal adjacency and
> carry their own anchors, so a week-old backlog forms its own blocks. **`last_trained_t` advances iff
> the cycle reaches `published` or `skipped_no_data`** — `gate_failed`/`frozen`/crash leave it, making
> the next window a strict superset and turning the design-of-record's **failed-day merge structural
> instead of bookkeeping**. **Reprocessed records: one dialect per record, latest `ingest_time`
> wins**, keyed `(chunk_id, content.kind, discriminator)` — on `ingest_time` because
> `pipeline_version` is a *composed* string and not orderable, and on `kind` because Phase-3 proved
> captions and transcripts share one `pipeline_version`. **A `pipeline_version` bump is a forward-only
> correction**: it never repairs past weights (irreducible on an append-only chain), and the accepted
> named cost is that one lived moment can train twice in two dialects — suppressing that would equally
> suppress the correction, and the correction wins. **(4) E-2 stays a separate storage build item, but
> is demoted from cutover blocker**: the one-dialect materialization rule is what actually fixes the
> double-count, so E-2 is the retraction/privacy/space primitive it always should have been. Shape
> recorded in the storage charter M5. **New obligation this creates:** the day-log and the reservoir
> are **second copies of user content**, so **M5's deletion primitives must cascade to both** — a
> retraction that clears `/context` and leaves a day-log standing has deleted nothing. **Blocking
> sub-item named for the builder, do not hand-wave it:** the within-chunk discriminator is folded into
> the `record_id` hash and is **not independently readable from C2**, so the build must either surface
> it as an additive-optional C2 field (ARCHITECTURE → schema → **both** `extra="forbid"` pydantic
> mirrors — the exact D17 trap) or prove `(chunk_id, kind, t_start)` unique per dialect

### 2026-07-26 (later) — review items O-2 / O-3 / O-4 closed
> review · continuum × storage

**Was** — three documentation defects arrived as one batch of eight edits across five files.
Treating them uniformly was the trap.

**Changed** — they were sorted by *shape* first, which is what made the ceremony obvious. None of
the three needed a D-number.

- **O-2 — C5's field list was short in four places.** Shape: an incomplete description, one truth
  written down only partially. Nothing was in dispute — `publish.py:83-99` writes nine fields and
  `record_gate_failure` (`:101-114`) writes a third status; the docs simply had not caught up.
  Ceremony: the judgment call below, plus doc edits and one comment-only code edit.
- **O-3 — "LoRA over all layers".** Shape: an intent/build gap, two truths about different
  things. Neither side is wrong; the defect was asserting the *intent* in the voice of the
  *build*, so a newcomer read the charter and believed vision towers were adapted. Ceremony:
  lowest — state both, name the gap.
- **O-4 — the C2 `record_id` discriminator.** Shape: prose lagging its own authoritative schema,
  one truth already recorded correctly. The frozen schema has mandated the discriminator since
  v0; only ARCHITECTURE's summary lagged. Ceremony: lowest, and explicitly not a contract change.
  Confirmed before editing against the current file, post-D17, not from memory.

**Now**

- **Eight sites edited**, only O-4 touching contract prose — and that was a summary catching up
  to its own schema, so no [ORG.md](../ORG.md):42-45 contract-change routing was owed and none
  was performed.
- The eight: ARCHITECTURE §Contracts C2 block + C5 row + §Decisions Personalization row ·
  continuum CHARTER mission + scope + C5 row · storage CHARTER model-directory row ·
  `continuum/app/publish.py` module docstring.
- The docstring edit was comment-only, and continuum's suite was re-run after it: 195 passed, 1
  skipped — the same 196 collected as the headless 189+7 baseline, with more parity tests simply
  running in this env.
- LEARN_LOOP §3 (C2 + C5) and §8 items 3 + 12 were updated to match. O-2/O-3/O-4 are struck from
  REVIEW_NOTES, which now carries only the seven charter/canvas hygiene items (O-5…O-11).
- **Verified before editing, not taken on the brief's word:** `LM_PROJECTIONS` + `_LM_SCOPE` and
  their in-source rationale (`morpheus/train.py:27-32`), `lora_target_modules()` (`:89-100`), the
  252/252 parity row
  ([phase-2a-report](../services/continuum/handoff/phase-2a-report.md):60), the nine-field
  `publish()` entry and the `gate_failed` row, the C2 schema's current `record_id.description`,
  `compute_record_id`'s empty-discriminator byte-identity
  (`data-processing/app/pipeline.py:33-46`), and all eight target lines.

**Payoff** — the O-3 reframe is the part worth keeping. "All layers" mis-names the axis: v0 *does*
adapt every layer (all 36) at all 7 projections. What it excludes is a **tower**, not a layer. So
the gap is *which stacks*, one axis and not two, and that is how it is now written.

Both facts the CTO asked to shape the wording ride beside it, so nobody re-derives the argument:
flipping back is cheap (a module-name-filter change plus a re-parity run — an option kept open,
not a door closed), and the exclusion's premise is falsifiable and self-expiring (it holds only
while the day log reaches the trainer as text; the day DP feeds it pixels, the reason evaporates
on its own). ARCHITECTURE §Decisions keeps "all layers" as the intent — still sourced to
`start.md`, an inherited founding assumption, never a ratified D-number.

**Watch out for**

- **O-2's judgment call, taken explicitly: describe as-built now, labelled.** C5 is deliberately
  unfrozen (`publish.py:3-4`) and this session did *not* freeze it — inference is not at the
  table, and the C5 → model directory → C6 → vLLM tail is still unwired.
- The counter-argument was real: a field list in ARCHITECTURE §Contracts is how things become
  de-facto frozen around here. It is answered inside the wording rather than by silence — the
  *"as built, not frozen"* label rides in the same table cell as the field list, so the row cannot
  be quoted without its caveat.
- A known-wrong description should not outlive a session that has no date.
- The most costly omission was not a field but a **status**: all four sites said
  `active/rolled-back`, hiding `gate_failed` — the audit trail for a blocked candidate, which is
  the thing a reader most needs to know exists.
- **`gate_failed` is not only documentation — it constrains the storage build at freeze time.**
  Storage's `model_directory` today is the trivial C6 row (`user_id, model_id, adapter,
  adapter_path` — `storage/app/db.py:59-63`): no entries log, no status column. So "the
  storage-hosted swap-in is a transport change, not a redesign" is true of *continuum*, not of
  storage.
- Storage has to build the log, and the short field list is exactly what an implementer would
  have built to. Three constraints are now written into the storage charter's model-directory row
  so they reach the freeze session.
  - `status` must be a **three**-value enum, or `record_gate_failure()` has nowhere to land and
    the audit trail is dropped silently at the swap.
  - `gate_failed` rows carry NULL `adapter_dir` and NULL `base_model_hash`, so neither column can
    be `NOT NULL` — a schema derived from the happy path would reject precisely the rows that
    matter most.
  - C6 eligibility is a **log replay** (`active` pushes, `rolled_back` pops the matching top, and
    `gate_failed` does neither — `publish.py:33-44`), not "latest row wins". A directory
    resolving the newest entry would serve a gate-failed candidate, which is the ungated swap the
    gate exists to prevent.
- **Two sites the review's O-3 inventory missed**, found by a repo-wide sweep after the three
  listed edits and fixed in the same pass: [VISION.md](../VISION.md):68 and :104 also say "all
  layers".
- **:68 is the worst instance of the defect anywhere in the repo** — it reads *"v0 mechanism
  (locked): per-user LoRA over all layers"*, the intent asserted not merely in the build's voice
  but as a *locked v0 mechanism*, in the company's most-quoted document.
- It was fixed minimally rather than rewritten, because VISION should stay a vision doc: "all
  layers" survives as the stated intent, with a parenthetical naming the as-built narrowing and
  pointing at continuum's charter for the argument.
- **Lesson for the next review pass:** an inventory built by reading the *engineering* docs will
  miss VISION.md. `grep -rn "all layers" --include=*.md` takes one second and would have caught
  it at pass 1.

### 2026-07-26 — timezone: decided, then re-decided, then BUILT end to end (D17)
> decision · all services · [D17](../DECISIONS.md)

Review item **O-1** is closed. Four threads ran in one session, so this entry carries four
blocks. It replaces an earlier same-day write-up whose conclusion was wrong; the reasoning is
kept, because the mistake is instructive.

**1 · Ownership — the device owns the fact, storage owns the policy**

**Was** — the wearer's tz was `nightly.py:27` `--tz`, defaulting to `"UTC"`, and
`context_records` had no tz column at all (`storage/app/db.py:78-88`; the only `timezone` token
in `storage/app/*.py` was the UTC import). Three consumers rode on the flag, not two: the window
boundary, `_render_block`, and `cycle.py:217`, which rebuilds *prior* windows with *tonight's* tz
under `replay_source="rawlog"`. All confirmed cheaply, before deciding anything.

**Changed** — the first D17 was wrong and is superseded. It gave storage a per-user `home_tz`
*only*, banned tz from C1/C2, and deferred travel. Two errors: it applied the emission law's T2
as a *veto* on a field whose consumer this very slice builds, when T2's own text says it is "a
gate on *when*, not a veto"; and it defended a `window_id`-total-order problem (dateline → one id
for two windows) that exists only because `window_for` derives bounds from a local date, which
the watermark window ARCHITECTURE's C10 row already specified dissolves.

**Now** — per-chunk `device_tz` + `device_utc_offset_minutes` on C1, verbatim through DP into C2
`source{}`, into storage columns, and read by continuum's renderer. Per-user `home_tz` does
scheduling (when does this user's night fire?) and fallback, and nothing else. UTC stays
canonical and is the only ordering/range axis.

**Payoff** — the fact that settled it is that the capturing device already knows, and we were
discarding it at the first step. `new Date(seg.tStart).toISOString()` (`clients/web/app.js:262`,
`clients/extension/uploader.js:110`) computes the local instant and throws the zone away on the
same line, and `Intl.DateTimeFormat().resolvedOptions().timeZone` is one line away.

**Watch out for**

- Two standing rules: never persist a derived local wall-clock, because that is two sources of
  truth that will disagree; never accept an abbreviation, because `PST` is ambiguous and
  DST-sensitive.
- **The offset is not redundant with the zone.** It records what the device *believed* at
  capture, so when a tzdata build is stale or wrong the offset is the only independent witness.
  The zone id alone would silently re-derive the wrong wall clock forever.
- C1 already collected `device_location` and C2 dropped it, while `device_location` and
  `device_clock` were declared in both recording's and DP's `models.py` and read by neither.
  Dead fields are how this class of bug hides.

**2 · The build — five hops, in [ORG.md](../ORG.md):44-45 order**

**Was** — the design was ratified and nothing was built. ARCHITECTURE §Contracts, `contracts/`
and the owning canvases had to move before any code did.

**Changed** — built end to end across four services and three clients, contracts first.

- **Clients** — web and extension `civilTime()`; mac `local_iana_zone()` +
  `civil_time_params()`, stdlib-only via the `/etc/localtime` symlink, because `time.tzname`
  yields forbidden abbreviations. The offset is evaluated at the chunk's own instant, so a chunk
  either side of a DST flip carries the true offset (tested: LA −480 in January, −420 in July).
- **Recording** — `/capture/segments` accepts and validates at the edge: an abbreviation is 400,
  an unknown IANA id is 400, an out-of-range offset is 422. Ledger columns plus an additive
  migration; `_build_envelope` omits rather than nulls; carried on the `/redrive` path too, since
  the query already joined `segments`.
- **DP** — `build_c2` copies `device_tz`, `device_utc_offset_minutes` and the long-dropped
  `device_location` verbatim, with zero timezone logic. `C1Envelope` and `C2Source` are
  `extra="forbid"`, so undeclared fields would have been rejected and the models had to move with
  the schema.
- **Storage** — promoted columns beside the UTC instant, so a civil-time query is a column read
  and not a JSON scan, plus an additive migration. `record_json` is still served byte-verbatim.
  Its pydantic `Source` mirror is `_Strict` and caught the omission in test: schema and mirror
  must move together.
- **Continuum** — `Segment.tz` + `_block_zone()`. The device zone wins, the window `home_tz`
  falls back, and an unresolvable id degrades instead of sinking the night. `--tz` is now
  required, so there is no default timezone anywhere.

**Now** — suites green with +26 tests and zero regressions: storage 32 · continuum 189 ·
recording 144 · DP 770 (+21 skipped) · extension deno 11. Notable coverage: two zones in one
window rendering independently *with different local dates*, the case one-tz-per-window
structurally cannot express; both migrations against hand-built pre-D17 schemas; and `record_id`
stability, because civil-time context is provenance and not a dialect input, so it must not fork
the record or every existing record would re-key on upgrade.

**Payoff** — a Tokyo chunk driven through recording → DP → storage → continuum, with the
operator's fallback deliberately set to UTC, renders "around 15:00 local time". Pre-D17 the
identical run rendered "06:00" — a UTC clock reading labelled local, with no error and no metric.

**Watch out for**

- **Deliberately no backfill on either migration.** A record captured before clients reported a
  zone genuinely has none, and inventing one (the server's, `"UTC"`) is precisely the
  silently-wrong timezone failure this slice removes. NULL is honest, and reads as "fall back to
  `home_tz`".
- Open questions closed: storage OQ3 (clock skew), DP OQ4 (device clock discipline), DP OQ9
  substantially, continuum OQ10's timezone half, and E-4's premise.
- **OQ3's "normalize upstream" lean was right and is now built**: nobody normalizes, and skew is
  *detectable* from `device_clock` + offset + `ingest_time` rather than silently corrected. OQ9's
  residual is that nobody *measures* the disagreement rate yet, though envelope time suffices and
  is now auditable.

**3 · The fleet — both migrations applied to node-7 the same session**

**Was** — node-7's learn fleet ran code predating the new columns, and its processes dated from
2026-07-19, before the D16-async, v1-journal and hardening merges.

**Changed** — the fleet was restarted onto the new code and both SQLite migrations ran against
live data, backed up first with SQLite's online-backup API →
`/home/ubuntu/nmn/backups/pre-d17-20260726-211912/`.

**Now**

- `context_records` carries both columns with 125/125 rows intact and all NULL, no backfill by
  design; `segments` carries both columns with 40/40 rows intact.
- The long-pending `dp_state` migration also fired, backfilling all 68 chunks to `processed`, so
  the board's long-standing "fleet is behind" note is cleared.
- Live wire checks against the running service: `device_tz=Asia/Tokyo` accepted and persisted,
  and `device_tz=PST` rejected 400. A real `--smoke` run carved 3 chunks through faster-whisper →
  C2 → `/context`.
- Those records correctly *omit* the civil-time fields, since headless `/capture/run` has no
  reporting device — absence, not null, is the contract. Verification rows were deleted
  afterwards, leaving the ledger at its 40-segment baseline.

**Payoff** — the restart collected three merges' worth of pending migrations at once, because a
fleet restart is the unit of deployment here whether or not the slice intended it.

**4 · The status correction (O-12, pass 2 of the review)**

**Was** — D17's headline read "BUILT + verified end to end" while the row also carried the
watermark-window clause, which is ratified and not built: `window_for()` and
`closed_window_before()` are still local-date, and `nightly.py` still calls them.

**Changed** — D17's status is split explicitly. The timezone split is BUILT and verified; the
watermark window is ratified and pending. ARCHITECTURE's C10 row is reworded from "the cycle
window is" to "is to become … and is still what runs".

**Now** — both documents name the blocking design question. `window_id` is today the local start
date, and it keys the day-log, the cycle journal, C5's `training_window`, and publish's
active-alias monotonicity. The natural watermark key is the window's end instant, monotone per
user with no dateline case, but that changes the `w2026-07-21` format and forks adapter lineage.

**Payoff** — a fair catch. The Decisions log is the most authoritative doc we have, so a blanket
BUILT claim over a two-part decision is exactly the kind of drift this session existed to remove.

**Watch out for**

- Specified and deliberately not built, because they are the board's own agenda and not something
  to slip into an unreviewed slice: day-log materialization moves continuum → storage, and the
  cycle window becomes the watermark range `[last_trained_t, now)`. Both are written into the
  ARCHITECTURE C10 row.
- The second retires `window_for` entirely, and it also changes training-window semantics, which
  is exactly why it wants the board and not a side-effect. Forking adapter lineage is a board
  call, not a refactor.

**Board view** — *merged 2026-07-27 from the retired `HANDOFF.md §Current state`, which had become a second worklog. Kept verbatim; consolidation with the text above is a later pass.*

> 2026-07-26 (founders' engineering session — **timezone: decided and built, D17**): the
> 2026-07-26 accuracy review's open item **O-1** ("timezone ownership is unowned") is **closed**,
> and the fix shipped in the same session. Verified first: `context_records` really had no tz
> column (`storage/app/db.py`), the wearer's tz really was a CLI flag defaulting to `"UTC"`
> (`continuum/app/nightly.py:27`), and — the fact that decided the design — **the capture clients
> already computed the local instant and threw the zone away on the same line**
> (`clients/web/app.js:262`, `clients/extension/uploader.js:110`: `new Date(...).toISOString()`).
> C1 also already collected `device_location`, and C2 **dropped it on the floor**; both
> `device_location` and `device_clock` were declared in recording's and DP's `models.py` and read
> by **neither**.
>
> **Decision (D17 above): the device owns the fact, storage owns the policy.** The first draft of
> D17, taken earlier the same day, was **wrong and is superseded**: it gave storage a per-user tz
> *only*, banned tz from C1/C2, and deferred the travel case — applying the emission law's T2 as a
> *veto* on a field whose consumer this very slice builds, when T2's own text calls it "a gate on
> *when*, not a veto". The correction came from the CTO's read: the capturing device is the only
> thing that can know where the user was, and it already knows.
>
> **BUILT end to end, all four services + three clients** (contracts first, per ORG.md:44-45):
> C1 gains `device_tz` + `device_utc_offset_minutes` (**additive-optional; `required` untouched on
> both schemas — re-validated**); the three capture clients emit them (`Intl…resolvedOptions()
> .timeZone` in the browser clients, an `/etc/localtime` symlink read in the stdlib-only mac CLI),
> with the **offset evaluated at each chunk's own instant so a DST flip is carried honestly**;
> recording validates at the edge (**an abbreviation like `PST` is a 400** — ambiguous and
> DST-sensitive), persists to the ledger, and carries it into C1 **including on the `/redrive`
> path**; DP passes all three fields (incl. the long-dropped `device_location`) **verbatim** into
> C2 `source{}` with zero timezone logic; storage promotes them to columns beside the UTC instant
> **and still serves the C2 back byte-verbatim**; continuum's `_block_zone` renders each block in
> the **device's** zone, falling back to the window's `home_tz`, degrading (never raising) on a bad
> id. Both SQLite stores got **additive alter migrations with deliberately no backfill** — a record
> captured before clients reported a zone genuinely has none, and inventing one is the exact
> failure this slice removes.
>
> **Cross-service E2E verified:** a Tokyo-captured chunk driven through
> recording → DP → storage → continuum with the operator's fallback set to UTC renders
> **"around 15:00 local time"**. The same run pre-D17 rendered **"06:00"** — a UTC clock reading
> *labelled* local, with no error and no metric.
>
> **Suites all green, +26 tests, zero regressions:** storage **32** (was 26) · continuum **189**
> (was 185) · recording **144** (was 133) · DP **770 + 21 skipped** (was 765) · extension deno
> **11** (was 10). New coverage includes the travel case (two zones in one window rendering
> independently, with different local *dates*), both migrations, edge rejection of abbreviations,
> and a test pinning that **civil-time context does not change `record_id`** — provenance must not
> fork the dialect, or every existing record would re-key on upgrade.
>
> **Also taken:** `nightly.py --tz` is now **required** (no default timezone anywhere).
> **Open questions closed by this work:** storage **OQ3** (clock skew), DP **OQ4** (device clock
> discipline), DP **OQ9** substantially (envelope time is enough — and now *auditable*), continuum
> **OQ10**'s timezone half, and **E-4**'s premise. **Specified but deliberately not built** (they
> are the storage/C10 board session's own agenda, not something to slip in unreviewed): moving
> day-log materialization from continuum to storage, and switching the cycle window to the
> watermark range `[last_trained_t, now)` — both now written into the ARCHITECTURE C10 row.

**D17 as ratified — full text.** *Relocated 2026-07-27 from the HANDOFF.md decisions log, now the register at [../DECISIONS.md](../DECISIONS.md); the D17 row there carries the headline and points back here.*

> **Timezone: the device owns the fact, storage owns the policy — and they are different things.**
> Conflating them was the original bug. **(1) The fact** (where the user physically was at a moment)
> is reported **per chunk by the capturing device**: `device_tz` (IANA) + `device_utc_offset_minutes`,
> **additive-optional on C1**, carried **verbatim** by DP into **C2 `source{}`** (DP does *no*
> timezone logic — it is provenance passthrough like `device_id`, so the emission law's T2 does not
> gate it), persisted by storage as promoted columns beside the UTC instant, and read by continuum's
> renderer. This is the only design that is **correct under travel**, and the device already knew it —
> every capture client computed the local instant and discarded the zone converting to UTC. **(2) The
> Policy** (when is this user's night?) is storage's per-user profile **`home_tz`**, whose only jobs
> are **scheduling** the nightly fire and **fallback** when a record carries no zone. **Timestamps
> stay UTC-canonical**: UTC is the sole ordering/range axis, C10 is a pure duration query needing no
> zone. **Never** store a derived local wall-clock (two sources of truth), **never** accept
> abbreviations (`PST` is ambiguous + DST-sensitive; rejected 400 at the capture edge). **Supersedes
> the first draft of D17** (same day), which had storage own a per-user tz *only*, banned tz from
> C1/C2, and deferred travel — wrong because it applied T2 as a veto to a field whose consumer this
> very slice builds, and because it defended a `window_id` total-order problem the watermark window
> dissolves. — **status, split deliberately (O-12, pass 2): the timezone split above is BUILT +
> verified end to end this session** (C1→C2→storage→renderer; `--tz` required, no default anywhere;
> suites storage 32 · continuum 189 · recording 144 · DP 770+21; node-7 migrated + smoked). **The
> companion window-semantics clause is ratified, not BUILT** *(as of this row's date; **BUILT
> 2026-07-27** — `window_for()`/`closed_window_before()` deleted, the watermark window live)*​**:**
> the cycle window *should become* the watermark range `[last_trained_t, now)` — what ARCHITECTURE's
> C10 row and the storage charter always said, and which retires the local-date pathologies (23 h/25 h
> days, a repeated local date colliding `window_id`) — but `window_for()` / `closed_window_before()`
> are still local-date and `nightly.py` still calls them. Nothing is broken; local-date windows work.
> **Open question the builder must answer first: does `window_id` survive the move?** It is today the
> local start date and it keys the day-log, the cycle journal, C5's `training_window`, and publish's
> active-alias monotonicity. Under a watermark window the natural key is the window's END instant
> (monotone per user by construction, no dateline case) — but that changes the `w2026-07-21` format
> and therefore forks adapter lineage, which is a board call, not a refactor. Belongs to the
> storage/C10 board session with the day-log move

### 2026-07-25 — continuum: THE LEARN LOOP IS CLOSED
> build · continuum × data-processing · [D15](../DECISIONS.md)

**Now** — the D15 continuum kickoff ran to completion. Morpheus is parity-proven against the
research line, M0 served a 32B life adapter our own pipeline trained, and the Phase-3 dogfood
reproduced the baseline separation through the real services. Verdict: the pipeline is sound.

**Board view** — *merged 2026-07-27 from the retired `HANDOFF.md §Current state`, which had become a second worklog. Kept verbatim; consolidation with the text above is a later pass.*

> 2026-07-25 (continuum — **the learn loop is closed**): the D15 continuum kickoff ran to
> completion. The nightly-consolidation core (**"Morpheus"**, our nomenclature; methods
> reimplemented cleanly from the research consolidation line `b3c58e1`, parity-proven by a
> differential harness — `render_block` byte-identical, LoRA targets 252/252, judge exact,
> ensemble indistinguishable at n=8/10, p=0.82) sits behind a `TRAINER_BACKEND` seam (mock
> default). **M0 met:** a 32B life adapter *our* pipeline trained → publish-gate v1.1 → C5 →
> **served in vLLM** (32B training needs ≥2 GPUs — a measured hard limit). Continuum was slimmed
> to a lean **5-verb loop** (fetch recipe · fetch day-log · amplify · finetune · gate · publish)
> over three storage **client seams** (local now, HTTP-to-storage later). Then the **Phase-3 DP
> dogfood** routed real Speed data (209.7 h of audio, 629 chunks) through the **actual
> recording→DP→storage→continuum services** — a replay `ChunkSource` + an injected-caption DP
> sidecar (~2 net-new files, no contract changes; test-type = config profile + `replay-speed`
> naming, not a contract field). The 1-min rule-bend collapsed recall on **dose** (fixed 48
> retellings now spread over 4.1× the block text); the **decomposition run with parity block
> content reproduced the baseline separation** (0.137 vs 0.179, permutation p=0.148 — same
> distribution; p=0.018 above the no-consolidation control). **Verdict: Pipeline sound — our real
> services carry the learn loop without losing learnability.** Suites green: continuum 185 ·
> storage 26 · recording 133 · DP 173. Detail: continuum canvas + [ws-morpheus-port](../services/continuum/handoff/ws-morpheus-port.md) · [ws-phase3-dogfood](../services/continuum/handoff/ws-phase3-dogfood.md).
> **Two founder-level follow-ups, neither an integration defect:** (a) a **recipe/dose finding for
> Gnandeep** — amplification dose is fixed *per block* but recall depends on retellings *per unit
> of text*, so at our native cadence dose must scale with block-text volume (cofounder to raise);
> (b) a **storage/C10 board session** — ratify the storage charter expansion (day-log
> materialization + recipe registry + reservoir custody) and the **C10 evolution** from a raw
> range-read to a **day-log fetch, random-access by `(user, window_id)`** (six cross-service
> friction notes captured in the Phase-3 report; new recipe-registry + reservoir contract IDs
> minted at ratification). **Gate policy v1.1** (traps ≥0.15/0.25, heldout exact-test vs each run's
> own base control, `min_probes` 148) was split from the training recipe so a threshold change
> never forks `recipe_id`.

### 2026-07-21 — DP hardening consumed + verified; docs aligned
> review · data-processing × recording × storage · [D16](../DECISIONS.md)

**Was** — the DP deep session had shipped a hardening slice and merged it (`5350f7a`,
conflict-free, carrying `aaebd88`; `dev`=`13bad86`; pushed), and the founders had a caveat drill
queued against the three v1 review findings.

**Changed** — the slice overtook the drill by closing inventory items (1)–(3) structurally
rather than by patch, and the founders re-verified it rather than accepting the report.

- **SlotView capability slot-ownership** — the order-dependent fingerprint guard is *deleted*
  rather than fixed.
- **Mutate `writes` plus deterministic overlap chaining**, with the chain order folded into the
  dialect.
- **A permit-at-dispatch queue rewrite** — the fairness knob is production-safe, and the
  experimental warning is gone.
- **Opt-in `INGEST_ISOLATION=subprocess`** — a poison chunk costs 1 chunk, not the service, and
  a drain-cancel SIGKILLs the ghost.
- **A 47-agent adversarial round** — 19 confirmed / 2 refuted → 9 fixes + 7 drills, catching two
  high bugs in the *new* code.

**Now**

- Suites re-run independently by the founders: DP 163 · recording 120 · storage 26 green. Merge
  topology, attribution-free commits and off-by-default knobs were checked, and byte-identity
  was re-proven by this session's own C2-digest diff.
- Docs are aligned to DP's final state: the DP HANDOFF H-row merged; recording's HANDOFF given a
  DP-durability alignment note (the guarantee is now durable on both legs, and `/redrive` stays
  belt-and-suspenders); the `requirements-video.txt` header clarified — no pip deps, a system
  prereq plus an endpoint recipe.
- **ARCHITECTURE §Observability was wrong and is corrected.** The shipped `/metrics` use a
  zero-dep in-house emitter, not `prometheus-fastapi-instrumentator` as the prose claimed.
- Verified accurate and left unedited: the storage docs (frozen C2 contract, unaffected),
  PROMPTS, and the root/services READMEs (serve-loop scoped).

**Payoff** — two engineering decisions moved out of the ws file and into the record.

- **The sync/inline path stays.** Retiring it "now that async is fast" was considered and
  refused: it is the C8/M6 skeleton — the single shared `process_chunk` core plus ~40 lines of
  HTTP mapping — and it is the byte-identical verification baseline.
- Flipping the async *production default* stays a founders' call after the D16 re-drive drill;
  retiring the inline *handler* waits until C8 lands its own surface, never the shared core.
- **M7 is substantially done** — backpressure, dead-letter, durable journal, kill-recovery,
  epochs, bounded re-drive, fairness and isolation all land. Remaining: dead-letter backfill
  tooling, a reprocess-by-version drill at pilot-day scale, `processed` retention, a warm child
  pool plus wall-clock kill, and the ops story for who restarts DP.

**Watch out for**

- The M7 box cannot be checked until the ops story is confirmed with platform. There is no
  supervisor config in-repo, and platform owns deploy.
- Open into the next session, all of it in the handoff docs: the D16 re-drive drill that gates
  flipping the async default, the M1 WER/DER exit measurement, M2 text/image as the next
  unstarted charter work, and the supervisor/deploy confirmation with platform.

**Board view** — *merged 2026-07-27 from the retired `HANDOFF.md §Current state`, which had become a second worklog. Kept verbatim; consolidation with the text above is a later pass.*

> 2026-07-21 (DP hardening): **the DP deep session shipped a hardening slice + merged it**
> (`5350f7a`, conflict-free merge carrying the founders' `aaebd88` board-sync; `dev` at the
> raw tip `13bad86`; pushed; DP trees identical between `main` and `dev`). It **closes all 3
> v1 review findings by construction, not by patch**: (1) a **SlotView capability proxy** —
> a sidecar can't even read the primary's mutable slots, illegal writes raise synchronously
> at the offending line (the order-dependent end-of-run fingerprint guard is *deleted*);
> (2) mutate **`writes` + deterministic overlap chaining**, with the chain order folded into
> `pipeline_version` (a future second mutate like speaker-ID composes on diarize, can't race
> it); (3) a **permit-at-dispatch** queue rewrite — the modality-fairness knob no longer
> head-of-line-blocks, so `INGEST_MODALITY_LIMITS` is production-safe and the experimental
> warning is gone. Plus a **new containment layer**: opt-in `INGEST_ISOLATION=subprocess`
> runs each chunk's Processor in a killable child (a segfault/native-OOM/`os._exit` in model
> code kills one chunk, not the service; a drain-cancel SIGKILLs the ghost compute a
> threadpool can't). A **47-agent adversarial round** (5 dimensions → 2 refuters/finding)
> confirmed 19 / refuted 2 → 9 code fixes + 7 gap drills, catching two high bugs *in the new
> code* (a reproduced retry-starvation in the new dispatch; an event-loop stall in spawn
> isolation). Byte-identity re-proven empirically (identical C2 digests vs `main` across
> dialects and under isolation). Founders re-verified independently: **DP 163 · recording
> 120 · storage 26 green**; merge topology + attribution-free commits + off-by-default knobs
> checked. The ws file also carries a full **M0–M8 milestone eval** (M0/M3/M7-core/M8 done;
> **M1 exit open** — no denoise stage + WER/DER baseline unmeasured; **M2 text/image is the
> next unstarted charter work**; M4/M5/M6 not started) and a **sync-path decision: Keep
> inline** — it's the C8/M6 skeleton and the byte-identical verification baseline; flipping
> the async production default stays a founders' call after the **D16 re-drive drill** (still
> the one open gate). Detail: [ws-dp-hardening](../services/data-processing/handoff/ws-dp-hardening.md).

### 2026-07-20 — DP v1 consumed + verified
> review · data-processing · [D16](../DECISIONS.md)

**Was** — the DP team shipped v1: the durable ingest journal and the stage-graph pipeline
(`86acb95`, pushed, commit attribution-free). Founders had not verified it.

**Changed** — suites re-run independently, refs and flags checked in code rather than taken on
report.

**Now** — DP 128 · recording 120 · storage 26 green; `main`=`dev`=origin confirmed; `INGEST_ASYNC`
still 0-default; the fairness-knob startup warning present at `ingest_queue.py:88`.

**Payoff** — the D16-era deferred false-`gaps` caveat is closed by journal rehydration, so the
async-trust rider is now satisfiable and the `INGEST_ASYNC` fleet flip becomes a live decision.

**Watch out for**

- A caveat inventory is prepared for a drill: `INGEST_MODALITY_LIMITS` HOL-block · a mutate-ordering
  rule in `resolve` as a hard prerequisite for any second mutate stage, since the ws drop-in table's
  own speaker-ID example would trip finding #7 · fingerprint-guard order-dependence (low).
- Also flagged: Architecture-Atlas custody against the D2 single-doc protocol; the journal's
  `pipeline_version`-staleness reprocess mechanics as the first real OQ5 code, which is continuum
  input; `processed` retention; the fleet is behind, restart pending.

**Board view** — *merged 2026-07-27 from the retired `HANDOFF.md §Current state`, which had become a second worklog. Kept verbatim; consolidation with the text above is a later pass.*

> 2026-07-20 (DP v1): **the DP team shipped v1 — durable ingest journal + stage-graph
> pipeline** (`86acb95`, single clean commit, pushed; `main`=`dev`=origin verified). Layer A
> journals async accepts before the 202 (kill -9 auto-recovers at startup; continuity
> rehydrates from the journal → **the D16-era deferred false-`gaps` caveat is closed**;
> durable dedup backstop with a `pipeline_version` staleness check — receipts written in
> Both modes, so inline gains restart-safe dedup too; epochs guard stale workers; bounded
> per-attempt re-drive breaks crash-loops visibly). Layer B turns every processing step into
> a **drop-in stage file** (readiness DAG runs independent stages concurrently; composed
> `pipeline_version` where a mutate stage's enabledness is its version fragment — the
> silent-overwrite class dies by construction); audio+video ported byte-identically, real
> backends re-validated through the graph on node-7. Two adversarial rounds (9 confirmed →
> 2 fix-before-merge fixed). Founders re-verified: **DP 128 · recording 120 · storage 26
> green**, refs + attribution-free commit + off-by-default knobs + the fairness-knob startup
> warning all checked in code. (The 3 tracked v1 follow-ups — `INGEST_MODALITY_LIMITS`
> HOL-block, mutate-overlap race, order-dependent fingerprint guard — were then **closed by
> the hardening slice below**, so the v1 caveat drill was overtaken by that work rather than
> held separately.)

### 2026-07-19 (later) — deep session landed + merged (`0ce4941`)
> review · data-processing × recording × storage · [D15](../DECISIONS.md)

**Was** — the deep session's work was merged but unverified by founders.

**Changed** — a founders' merge review: all three suites re-run independently, the D16 condition
checked in code and in drill tests, OQ3 and OQ13 records confirmed in the charters.

**Now** — DP 98 · recording 120 · storage 26 green; `dev` fast-forwarded with `main`; board synced.
**D15 is the active sequence**: continuum kickoff behind the C10 freeze gate, plus the platform D9
backbone.

**Payoff** — the merge is trusted rather than assumed.

**Watch out for**

- Fleet restart is pending before anything begins emitting `/metrics`.

### 2026-07-19 — post-capture-alpha sequencing; the async-`/ingest` bar → D16
> decision · data-processing × recording × continuum · [D15](../DECISIONS.md) · [D16](../DECISIONS.md)

**Was** — the capture alpha and the DP modality slices were done and verified, with nothing
sequenced behind them and no agreed bar for the async `/ingest` reply shape.

**Changed** — a DP-led deep session launched in parallel (branch
`svc/dp-async-observability`, worktree `~/nmn/cl-dp-async`) while this session held the board:
async `/ingest` (M7-early) · D9 metrics emission (DP M8 + recording M6) · node-7
real-audio-backend smokes · the OQ3 codec ladder, joint · the OQs the work answers, with DP
OQ13 resolved by the slice.

**Now**

- The **ratification bar** for the async `/ingest` reply shape is pinned — an inter-service
  wire, not a C-number (§Post-capture-alpha sequencing). Its load-bearing clause is the
  silent-loss window a `202` ACK opens. The escalation row is open.
- **D15** is the sequence: continuum kickoff next, gated on a C10 v0 freeze (storage ×
  continuum); the Platform D9 backbone as the small parallel slice; DP image/text deferred until
  a producing surface exists. Mobile + C8 and a standalone C10 freeze were considered and passed.
- **D16 is ratified** later the same session. The deep session's final async-`/ingest` design
  memo arrived five-reviewer verified, its code claims spot-checked here, and it strengthened the
  bar's headline clause into the non-negotiable `dp_acked` == "C2 durably written" invariant fix.
  Recording moves in-slice; "zero recording change" was dropped as unsound.
- The learn fleet is re-verified up on node-7, with storage, DP and recording all healthy.

**Payoff** — the bar was pinned before the memo arrived, so ratification tested a design against
a standing condition rather than against the memo's own framing.

**Watch out for**

- Two things ride out of D16 unfinished: an accepted-unconfirmed **re-drive drill** in-slice, and
  the accepted caveat that the 202 path returns `record_ids=[]` and so carries no provenance.
  Both are recorded in the ratification block above; the reply itself was left in the deep
  session's scratch dir.

**Board view** — *merged 2026-07-27 from the retired `HANDOFF.md §Current state`, which had become a second worklog. Kept verbatim; consolidation with the text above is a later pass.*

> 2026-07-19 (post-alpha sequencing): **DP-led deep session launched in parallel** (branch
> `svc/dp-async-observability`, worktree `~/nmn/cl-dp-async`) to execute **async `/ingest`**
> (DP charter M7 arriving early), **D9 metrics emission** (DP M8 + recording M6 — emission
> half; Platform's backbone follows), **node-7 smokes of the real audio backends**
> (pyannote/whisper-translate/AST), and the OQs the work answers (headline: recording OQ3
> codec ladder, joint; DP OQ13 resolved by the slice). The founders' session pinned the
> **ratification bar for the async `/ingest` reply shape** (inter-service wire, not a
> C-number; escalation row open above) and recorded **D15**: continuum kickoff next (C10 v0
> freeze as its gate) + Platform D9 backbone as the small parallel slice; DP image/text
> deferred until a producing surface exists. Learn fleet re-verified healthy on node-7.
> *Later same session:* the deep session's final async-`/ingest` design memo arrived
> (five-reviewer verified; code claims spot-checked) and was **ratified → D16** — the memo
> strengthened the bar's headline clause into the non-negotiable `dp_acked`-invariant fix;
> one condition (re-drive drill) + one accepted caveat (202-path provenance) recorded.
> *Later still:* **the slice landed + merged (`0ce4941`; `dev` fast-forwarded with it).**
> Founders' merge review re-ran all three suites independently (**98/120/26 green**) and
> verified the D16 condition + OQ3/OQ13 records in the diff. D15 is now the active sequence.

### 2026-07-18→19 — recording-led capture M1 + computer surfaces: ALPHA COMPLETE
> build · recording × data-processing · [D14](../DECISIONS.md)

**Now** — recording is wrapped to the alpha bar: the checked zero-silent-loss guarantee, the
fuller ASR pipeline, VAD-cut variable chunking, and three real capture clients on one
client-agnostic wire, all verified `clean` end to end on real hardware.

**Board view** — *merged 2026-07-27 from the retired `HANDOFF.md §Current state`, which had become a second worklog. Kept verbatim; consolidation with the text above is a later pass.*

> 2026-07-18→19 (recording-led capture M1 + computer surfaces): **the recording service is
> wrapped to the alpha bar.** M1 built the checked "zero silent loss" guarantee (SQLite
> continuity ledger + a DP-side break/dup detector on `/ingest` + a two-leg gap report with a
> `clean|gaps|recording` verdict), the **fuller ASR pipeline** (faster-whisper standing with a
> VAD gate that turns silence into an honest empty transcript; diarize/translate/acoustic-event
> stubs behind the Processor seam), and **VAD-cut variable chunking** (OQ4 → D-M1-2). Then
> **three real capture clients** landed behind the same `/capture/*` wire (client wire renamed
> from `/ingest/*` so `/ingest` is uniquely DP's C1 receiver): **phone web** (mic+camera),
> **Chrome-MV3 extension** (passive active-tab capture — pivoted to `tabCapture` per D-E7 after
> the desktop picker proved fragile on real browsers), **mac CLI** (ffmpeg avfoundation). Each
> demuxes to per-modality C1 streams; **zero server changes for the two new clients** (the wire
> is client-agnostic, by design). **Alpha complete 2026-07-19** — all three verified `clean`
> end-to-end on real hardware (blobs sha256+ffprobe-checked in storage, real ASR transcripts in
> `/context`). Multiple adversarial review rounds + a fresh-eyes runbook-accuracy pass hardened
> it (110 recording tests). **D14** (segmented-HTTP transport; streaming ingest deferred additive)
> recorded. Detail: [services/recording/HANDOFF.md](../services/recording/HANDOFF.md) +
> [alpha-runbook](../services/recording/handoff/alpha-runbook.md).

### 2026-07-18 — return sync (founders)
> decision · all services · [D12](../DECISIONS.md)

**Was** — a week's gap (2026-07-10 → 07-17) left repos unpushed and canvases stale. The founders'
board still carried a "Live now" note for a fleet that was down.

**Changed** — a return sync: everything committed and pushed, and every stale canvas trued up
against reality.

**Now**

- Cluster custody is clarified: the vacation-week jobs are Gnandeep's continuum-side experiments,
  and product keeps node-7.
- All repos are pushed — umbrella `main` and both POC submodules, with `poc/live_video_chat` now
  tracked in the umbrella.
- Doc hygiene done over the inference, storage and recording canvases, the ARCHITECTURE and ORG
  ratification remnants, and the root README.
- The fleet on node-7 is verified down and the stale note is gone.

**Payoff** — **D12 recorded**: service branches merge to `main` when solid, and a standing `dev`
branch is the beta playground. First beta is Gnandeep driving both loops against his fine-tunable
model, with storage's range read `GET /context/records?user_id=&from=&to=` — half-open `[from,to)`,
deliberately C10's read shape — as his training-window feed until C10 lands.

**Watch out for**

- Next slice pinned: recording-led capture M1.

**Board view** — *merged 2026-07-27 from the retired `HANDOFF.md §Current state`, which had become a second worklog. Kept verbatim; consolidation with the text above is a later pass.*

> 2026-07-18 (return sync): **repos pushed + docs trued up** after the 2026-07-10→07-17 gap (no
> repo changes during it; the cluster ran Gnandeep's continuum-side model-stabilization
> experiments throughout — no conflict, product work keeps to node-7). Pushed: umbrella `main`;
> `live_stream_stability` (June Phase-3.1/3.2 work committed: replay-mixture tooling, eval
> harness, frozen holdout, Day-0 baseline rows, `phase_N` dir renames); `recursive_finetuning_
> stability` (`phase-3-recursive-loop` — 20 commits, Phases 1–3 + the running V4 matrix —
> pushed and fast-forwarded into `main`). `poc/live_video_chat` brought under umbrella tracking
> (+ post-V0 addendum in its HANDOFF); `start.md` committed; root `.gitignore` + rewritten root
> `README.md` added. Stale service canvases synced to reality (inference real-model closure;
> storage/recording integration + seam state; ARCHITECTURE/ORG ratification remnants). Serve
> fleet on node-7 verified **down** — the week-old "Live now" note was stale; nothing to tear
> down. **D12** (branching + beta model) recorded; next slice pinned: **recording-led capture
> M1** (gap-detection + ASR pipeline priority).

### 2026-07-10 — modality seam: data-processing goes multi-modal
> build · data-processing × recording

**Now** — data-processing is modality-agnostic behind a `Processor` plugin seam, so parallel
sessions can each own a modality. All four `content.kind`s are proven end to end into `/context`.

**Board view** — *merged 2026-07-27 from the retired `HANDOFF.md §Current state`, which had become a second worklog. Kept verbatim; consolidation with the text above is a later pass.*

> 2026-07-10 (modality seam): **data-processing made modality-agnostic** so parallel sessions can
> each own a modality. DP refactored to a core + `Processor` plugin seam (self-registering,
> **one file to add a modality, zero core edits**; `process()` returns a **list** so one chunk → many
> records is native); audio moved behind the seam unchanged (`record_id` byte-identical);
> image/video/text **stub** processors + fixtures; recording carver generalized to a `ChunkSource`
> seam. **All 4 `content.kind`s proven E2E to `/context`** (incl. video's 3-keyframe fan-out),
> verified live + adversarially (**84 tests**: storage 26 · DP 24 · recording 34). The verifier
> caught a real live regression — DP's `/ingest` reshape (`record_id`→`record_ids[]`) 500'd
> recording's `/capture/run`, masked by stale test fakes — **fixed + re-verified 200 live**. Two
> C2-additive gaps surfaced (video per-keyframe timing, image OCR bbox) — **both deferred to the
> modality sessions, no version bump; frozen C2 untouched.** Detail + seam handoff:
> [handoff/engineering.md](../handoff/engineering.md) "Modality seam".

### 2026-07-09 — build order locked; BWM, mobile, POC-provenance recorded
> decision · all services · [D3](../DECISIONS.md) · [D5](../DECISIONS.md) · [D6](../DECISIONS.md) · [D7](../DECISIONS.md)

**Was** — five founding escalations were open, and the device/output narrative still assumed a
speaker on the wearable.

**Changed** — all five were resolved at the founders' board (Decisions log D1–D8), and the
agenda refocused on slicing the serve-loop MVP.

**Now**

- Build order is locked serve-loop-first ([D3](../DECISIONS.md)); the base world model is
  Qwen3-VL-32B ([D6](../DECISIONS.md)) with OCR decoupled into a data-processing specialist pass
  ([D8](../DECISIONS.md)); the mobile app is pulled into v0 scope as the speech-output sink
  ([D5](../DECISIONS.md)); POC-no-reuse is recorded ([D7](../DECISIONS.md)).
- The serve-loop MVP slice (v0.0) is drafted in this thread, and the `product/` tree is
  committed to git.

**Payoff** — the narrative and the build order stopped contradicting each other. A wearable with
no speaker needs the mobile app, so D5 follows from the device call rather than competing with it.

**Board view** — *merged 2026-07-27 from the retired `HANDOFF.md §Current state`, which had become a second worklog. Kept verbatim; consolidation with the text above is a later pass.*

> 2026-07-09: all five founding escalations resolved (Decisions log D1–D8). Device/output
> narrative reworked for no-speaker wearable + mobile-as-speech-sink; mobile app pulled into
> v0 scope; build order locked to serve-loop-first; BWM set to Qwen3-VL-32B with OCR decoupled
> into a data-processing specialist pass (D8). Serve-loop MVP slice (v0.0) drafted in the
> engineering thread. `product/` tree committed to git.
>
> 2026-07-09 (later): interface-freeze done (C3/C9/C4/C6 v0 locked in
> [ARCHITECTURE.md](../ARCHITECTURE.md) §Contracts + [contracts/](../contracts/)); WS A–E built their
> services; **integrator wired them and ran the mock loop end to end.** A turn typed at the
> computer surface (`:8081`) streams a base-*mock* answer in the C9 format and the C4 turn is
> persisted + re-readable by `session_id`/`turn_id`; C6 resolves to base. All suites green
> (storage 10 · inference 6 · input 19 · output 46 = **81 passed**). Deltas: output's
> `c9_reader.js` wired into the input surface; inference `run.sh` honors `HOST`/`PORT`; storage
> test-DB gitignored. **Real Qwen3-VL-32B (`vllm`) is scripted-but-unrun** (needs the a3mega
> node). Full result: [handoff/engineering.md](../handoff/engineering.md) "Serve-loop MVP — v0.0
> build result"; run guide: [services/README.md](../services/README.md). Committed (`f6805d1`).
>
> 2026-07-09 (later still): **v0.0 closed on the real base model.** Qwen3-VL-32B-Instruct
> launched on vLLM TP=8 on node-7 (driver 580 / CUDA-13, `vllm-vlm` env, model already cached);
> flipped `MODEL_BACKEND=vllm` and drove a real turn end to end — genuine Qwen answer streamed in
> the C9 format, C4 persisted with the real `model_id`. `serve_vllm.sh` updated to the verified
> recipe. Detail: [handoff/engineering.md](../handoff/engineering.md) "real model — v0.0 closed".
>
> 2026-07-09 (capture slice): **learn-loop MVP sliced + C1/C2 frozen.** Founders' engineering
> session sliced the barebones capture path **computer mic → ASR → `/context`** (D10) and froze
> **C1** (raw-stream envelope + delivery: push/at-least-once/dedup-on-`chunk_id`/dense-`(stream_id,
> sequence)`/blob-first; D11) and **C2** (processed record + `/raw` blob-ref; `record_id`
> deterministic on `(chunk_id, pipeline_version)`). Shapes in [ARCHITECTURE.md](../ARCHITECTURE.md)
> §Contracts (learn-loop block) + machine-readable in [contracts/](../contracts/)
> (`c1_raw_stream_envelope.v0.json`, `c2_processed_record.v0.json`), **adversarially stress-tested
> by a 5-lens critic pass before freeze** (13 findings → 10 verified byte-changing → 2 blockers +
> 7 fixes applied). data-processing OQ1 + recording's ingest OQ resolved. No service code built —
> this session produced the slice + the frozen contracts; the M0 builds come next. Slice:
> [handoff/engineering.md](../handoff/engineering.md) "Learn-loop MVP slice".
>
> 2026-07-09 (capture M0 built): **learn-loop capture M0 built, integrated & independently
> verified.** A 4-workstream fan-out (storage/data-processing/recording/platform) built M0 against
> the frozen C1/C2; an integrator wired them and drove one continuous-capture chunk **end to end on
> live ports** (carve WAV → `/raw` blob-first → C1 push → `/ingest` → mock ASR → C2 → `/context`),
> and an adversarial verifier re-ran the suites + re-drove the loop. **62 tests pass** (storage 26 ·
> data-processing 9 · recording 27); idempotency proven on both legs (same `chunk_id` → no dup
> blob/record); C1+C2 schema-valid E2E; the optional **real faster-whisper** leg genuinely ran once
> (restored to mock). **Zero seam fixes** — the frozen wire interoperated first try. Committed by
> this founders' session (no agent commits). Honest residuals feed capture M1: **gap-detection is
> emit-side only (not enforced)**, no consent gate, mock+file-source (no real mic). Detail:
> [handoff/engineering.md](../handoff/engineering.md) "Learn-loop capture M0 — build result".

### 2026-07-08 — thread seeded at product-structure standup
> design · all services

**Now** — thread seeded at the product-structure standup.

**Board view** — *merged 2026-07-27 from the retired `HANDOFF.md §Current state`, which had become a second worklog. Kept verbatim; consolidation with the text above is a later pass.*

> 2026-07-08: `product/` structure stood up — vision/architecture/org/prompts written,
> all 8 services chartered with seeded canvases, contracts **C1–C11** pinned in
> [ARCHITECTURE.md](../ARCHITECTURE.md). A two-critic review pass (seam consistency + narrative
> coverage, 22 findings) drove: three new contracts minted (C9 response stream, C10
> training-window read, C11 recent-context read), an §Ownership splits section deciding the
> contested seams (wearable device, deletion, consent, BWM custody, people registry,
> same-day context, `/raw` custody), and per-charter amendments. No implementation started
> anywhere. POCs (`poc/live_stream_stability`, `poc/recursive_finetuning_stability`,
> `poc/live_video_chat`) continue as continuum/inference research feeders.

## Archive — delivered slices

> Design + build records for slices that are **done**. Kept verbatim for the reasoning;
> nothing here is live work. Current state of any service lives in its own canvas.

### Serve-loop MVP slice (v0.0) — the walking skeleton

**Goal.** One text turn, end to end: a user types in a computer chat box → gets a streamed
answer from the **base** Qwen3-VL-32B → the turn is persisted. This proves the serve-loop spine
(input → QueryBuilder → inference → output → storage) with the *minimum* of every service.
Everything else (personalization, capture, mentors, extra modalities/surfaces) hangs off this
later. Deliberately un-personalized: inference serves the base model, no adapter yet.

**In this slice**

| WS | Service | M0 deliverable | Contracts it must honor |
|---|---|---|---|
| A | **input** | Computer text chat surface → request envelope → QueryBuilder text path → emit a **C3 UserPrompt**, text-only. Mint `session_id` / `turn_id`. | produces C3; C8 is a pass-through for text, with no heavy normalization yet |
| B | **inference** | vLLM up with base **Qwen3-VL-32B** (TP=8, one node). Accept C3, prepend the system prompt, single-shot generate with no harness, tools or mentors yet, stream out via C9, and write the turn via C4. C6 resolves to "base model, no adapter". | consumes C3, resolves C6 (trivial), produces C9 + C4 |
| C | **output** | Relay the **C9** token stream to the computer surface; markdown render; per-turn delivery ack. | consumes C9 |
| D | **storage** | Minimal **/sessions**: persist a C4 turn record keyed by `session_id`/`turn_id`, plus a trivial model-directory entry that C6 reads. | serves C4 write + C6 read |
| E | **platform** | One a3mega node hosting vLLM and the three app services; basic HTTPS reachability; a shared dev secret/env. | none (enables A–D) |

**Out of this slice (later slices):** recording + data-processing + `/context` (capture);
continuum + per-user adapter (personalization); mentors/C7 + agentic harness; C11 recent-context;
image/video/speech modalities; mobile / extension / wearable surfaces. Each is its own slice once
the skeleton walks.

**Gate — interface freeze (do this first, jointly).** Before A–D fan out, the input + inference +
output leads pin the **MVP-minimal shapes** of C3, C9, and the C4 turn record in
[../ARCHITECTURE.md](../ARCHITECTURE.md) §Contracts:
- **C3 (text v0):** `{user_id, session_id, turn_id, messages:[{role, text}], client_capabilities, template_version}`.
- **C9 (text v0):** `{turn_id, model_id, text chunks…, end-of-turn: {usage}}`. *Mid-turn frames deferred* (no mentors yet).
- **C4 (turn v0):** `{user_id, session_id, turn_id, user_prompt_ref, response_text, model_id, adapter:"base", t_created, t_completed, traces:[]}`.

**Launch order.** (1) Interface-freeze session (input+inference+output). (2) Then WS-A/B/C/D fan
out in parallel against the frozen shapes; WS-E runs alongside. (3) An **integrator** session
([../PROMPTS.md](../PROMPTS.md) §E) wires them.

**Integrator exit criterion (v0.0 done):** a pilot user types a question in the computer surface
and receives a streamed base-model answer; the turn is persisted in `/sessions` and re-readable by
`session_id`/`turn_id`; no personalization, no capture — just the spine, proven.

**Recommended first launch:** the **interface-freeze session** (Prompt A framing, but joint across
input+inference+output leads) — nothing safely parallelizes until C3/C9/C4 v0 are locked.
**Status: freeze done (2026-07-09)** — shapes locked in [../ARCHITECTURE.md](../ARCHITECTURE.md)
§Contracts + machine-readable in [../contracts/](../contracts/). Fan-out is unblocked.

#### MVP build conventions (v0.0) — so the 5 workstreams interoperate

Pinned so WS A–E produce compatible pieces; the integrator may finalize process topology.

- **Stack:** Python 3.11, **FastAPI + uvicorn** per backend service; `httpx` for inter-service
  calls; **pydantic** models mirroring the JSON Schemas in [../contracts/](../contracts/);
  `pytest`. Surface = static HTML/CSS/JS, **no build step**, served by input.
- **Model backend switch (critical):** env `MODEL_BACKEND=mock|vllm`. **`mock` is the default**
  — a canned, streamed answer, **no GPU needed**, so the whole loop runs on any box. `vllm` =
  OpenAI-compatible client to a vLLM server (real Qwen3-VL-32B, needs the a3mega node). Ship
  Both; only `mock` is expected to run tonight.
- **Ports (localhost dev):** input `8081`, inference `8010`, output `8082`, storage `8083`
  (vLLM `8000` when real).
- **Storage:** SQLite file DB for dev — a `/sessions` turns table (C4) + a model-directory
  table (C6). No external DB tonight.
- **Contracts are tested:** each service validates the payloads it produces/consumes against
  `../contracts/*.json` in its tests.
- **Layout per service:** `product/services/<key>/{app/, tests/, run.sh, requirements.txt}`;
  keep the worklog in `handoff/wsN-*.md`, status in the service `HANDOFF.md`.
- **Recommended flow (integrator finalizes):** browser → input `:8081 /api/turn` (JSON `{text}`)
  → QueryBuilder builds C3 → inference `:8010 /infer` (streams C9; resolves C6 + writes C4 to
  storage `:8083`) → input relays the C9 stream to the browser; **output** owns the browser-side
  C9 reader + markdown render (served with the surface) **and** a standalone relay service for
  future non-web surfaces.
- **No agent commits.** Workstreams write files; the founders' session commits after integration.
- **Honesty rule:** the `mock` loop must actually run end-to-end; the `vllm` path is
  scripted-but-unrun until the node — never report a real-model run that didn't happen.

---

### Serve-loop MVP — v0.0 build result (2026-07-09)

**Integrator session.** Wired the five workstreams, brought the mock loop up with
`services/platform/deploy/run_all.sh`, and drove real turns end to end. Honest result below.

#### What runs (executed here, not claimed)
- `run_all.sh` built a fresh shared venv, pip-installed all four services' requirements
  (PyPI reachable), and started **storage:8083 → inference:8010 (MODEL_BACKEND=mock) →
  output:8082 → input:8081**, `/health`-gated, all four healthy.
- **A real turn, streamed:** `POST http://localhost:8081/api/turn {"text":"What is 2+2?"}`
  → the answer streamed back as the **C9 wire format** (mock answer text, **exactly one**
  `U+001E` (0x1e) separator byte, then one JSON end frame
  `{contract:"C9",version:"0",turn_id,model_id:"Qwen/Qwen3-VL-32B-Instruct",adapter:"base",
  usage:{prompt_tokens:25,output_tokens:20},finished:true}`). `X-Session-Id`/`X-Turn-Id` ride
  in response headers.
- **Persistence proven:** the C4 turn was re-read via `GET /sessions/turns/{turn_id}` (full
  nested C3 `user_prompt`, `response_text`, `model_id`, `adapter:"base"`, empty trace arrays)
  **and** listed via `GET /sessions/{session_id}/turns`. A second turn on the same
  `session_id` grew the session list to 2. C6 `GET /model-directory/resolve?user_id=dev-user`
  → base model.
- **Both output roles exercised:** the browser reader (`c9_reader.js`, now wired into the
  input surface) **and** the standalone `POST /deliver` relay (pulled a live C9 stream from
  inference, echoed `X-Delivery-*` ack headers, relayed the body byte-for-byte).
- Browser surface serves: `GET /` (200 text/html), `/static/app.js` + `/static/c9_reader.js`
  (200) — `index.html` loads `app.js` as `type="module"`; `app.js` imports the reader.

#### Test results (ran each service's pytest, real counts)
| Service | Result |
|---|---|
| storage | **10 passed** |
| inference | **6 passed** (2 deprecation warnings, websockets — cosmetic) |
| input | **19 passed** |
| output | **46 passed** |
| **total** | **81 passed, 0 failed** |

#### Integration deltas (seam fixes applied)
1. **Render seam wired (primary).** Input's surface rendered answers as **plain text** with a
   TODO to adopt output's renderer. Fixed: **vendored** `output/app/static/c9_reader.js` →
   `input/app/static/c9_reader.js` (same-origin so the browser ES-module import needs no CORS
   to `:8082`), rewrote `input/app/static/app.js` to `import { renderC9Stream }` and hand it the
   `fetch()` response (streams + safe-markdown-renders into `#answer`, surfaces usage via
   `onEndFrame`), and updated `index.html` (`<pre>`→`<div id="answer">`, `<script type="module">`,
   markdown/code/error CSS). Canonical source stays output's copy — re-copy on change (a
   build-time copy step is the future fix to kill the duplication).
2. **inference `run.sh` now honors `PORT`/`HOST`.** It hardcoded `--host 0.0.0.0 --port 8010`,
   ignoring the platform↔service contract (read `HOST`/`PORT` from env). Values matched the
   defaults so nothing broke, but it now binds what `run_all.sh` passes.
3. **Storage test-DB hygiene.** The live run created `storage/app/dev.db` (a real SQLite file
   with test turns) inside an untracked dir; removed it and added `storage/.gitignore`
   (`*.db`, `__pycache__/`, `.pytest_cache/`) so it never gets committed.

Ports/URLs were already consistent (8081→8010→8083, output 8082); the ``+end-frame C9
format is produced by inference and consumed identically by input's relay, output's relay,
`c9_reader.js`, and `c9_parse.py` — verified byte-for-byte (single 0x1e in the stream).

#### Blockers / not done here
- HTTPS / remote reach (cloudflared), CI, observability: later platform work (unchanged).
- `c9_reader.js` is duplicated (input vendors output's copy); acceptable for v0.0, but a
  copy-on-build step should replace the manual vendoring.

**Exit criterion (v0.0 done): MET for the mock loop.** A turn typed at the computer surface
returns a streamed base-*mock* answer and the turn is persisted + re-readable by
`session_id`/`turn_id`.

#### REAL model — v0.0 closed on Qwen3-VL-32B (2026-07-09, node-7)

The mock ceiling is lifted — the loop now runs on the **real base model**, verified on
`nucla3m-a3meganodeset-7` (8× H100, driver 580 / CUDA-13):
- Launched **Qwen3-VL-32B-Instruct on vLLM 0.19.1** (the `vllm-vlm` conda env), TP=8, from the
  existing HF cache (~63 GB, already downloaded — no pull). Came up in a few minutes, ~75 GB/GPU
  at util 0.90. Recipe verified + recorded in [`../services/inference/serve_vllm.sh`](../services/inference/serve_vllm.sh).
- Direct `/v1/chat/completions` sanity: *"2+2 equals 4, and the capital of France is Paris."* in ~1.9 s.
- **Full serve-loop turn on real weights:** `POST :8081/api/turn {"text":"…Eiffel Tower…"}` →
  streamed C9 (real answer *"The Eiffel Tower is a wrought-iron lattice tower located in Paris,
  France."* + single `U+001E` + end frame, `model_id:"Qwen/Qwen3-VL-32B-Instruct"`, real usage
  62→19) → C4 persisted with the real answer, re-readable by turn id. Flip was just
  `MODEL_BACKEND=vllm` in `deploy/.env` + `run_all.sh --restart` (inference `/health` reports
  `backend:"vllm"`).

**Exit criterion for v0.0 is now MET on the real base model, not just mock.** One variable was
changed vs. the mock loop (the backend) — everything else (contracts, wiring, persistence) was
already proven, so the real turn worked first try.

**Follow-up done (2026-07-09):** upgraded the serving stack to **vLLM 0.24.0 / torch 2.11 /
transformers 5.13 / CUDA-13 (cu13) wheels + flashinfer** in a fresh `vllm-cu13` env, and
validated it end to end (direct completion + a real loop turn) — swapped in as primary with the
0.19.1 `vllm-vlm` env kept as fallback. Done as its own step *after* v0.0 closed, so the version
bump was isolated from app wiring; it validated first try. Recipe: `serve_vllm.sh` (now defaults
to `vllm-cu13`); stack in [../STACK.md](../STACK.md). Still open: the D6 OCR spot-check on real
screen-capture data (model is serving).

---

### Capture M1 + computer surfaces — the slice plan (2026-07-09 → 07-19)

> delivered · alpha complete 2026-07-19 · outcome in
> [§Worklog 2026-07-18→19](#2026-07-1819--recording-led-capture-m1--computer-surfaces-alpha-complete)

**In one line.** The forward plan that drove the learn-loop slice from M0 skeleton to three
verified capture clients, kept for history after the work landed.

**What was delivered.** M0 skeleton → capture M1 (enforced gap-detection via the continuity ledger
plus a DP break/dup detector; faster-whisper standing with a VAD gate; VAD-cut variable chunking,
OQ4 → D-M1-2) → three real capture clients on the `/capture/*` wire — phone web, Chrome-MV3
extension via `tabCapture` (D-E7), mac CLI — **all verified `clean` on real hardware**. Client
transport pinned to segmented HTTP ([D14](../DECISIONS.md)); streaming ingest is a deferred
additive leg. Full state lives in the recording and data-processing canvases; this thread links
rather than restates.

**The original slice plan (2026-07-09, delivered).** Skeleton = computer mic → ASR → `/context`
([D10](../DECISIONS.md)); C1/C2 frozen ([D11](../DECISIONS.md)), adversarially reviewed pre-freeze;
M0 fan-out built across storage, data-processing, recording and platform. The mock capture loop ran
E2E on live ports and the real-ASR leg ran once — 62 tests, idempotency proven, independently
verified. Six items were queued for capture M1, the audio stream:

1. **Enforce gap-detection** on `(stream_id, sequence)` — the top item. It is recording's "zero
   silent loss" guarantee, and was emit-side only: a break/dup detector on data-processing ingest,
   feeding recording's continuity report.
2. **Async `/ingest`** — ACK `202` and process on a worker or queue, so capture cadence decouples
   from ASR latency. Dedup and `record_id` determinism keep retry safe; M0 is inline.
3. **Real computer-mic capture** (recording M1), replacing the file source.
4. **Consent gate** (recording M2) before any always-on capture.
5. **A fuller audio pipeline** — VAD gate → diarize → ASR → translate → acoustic-event captioning.
   Non-speech audio is *captioned, not dropped*, because ambient sound is life-context signal, and
   the VAD also kills Whisper's silence-hallucination. Real faster-whisper becomes the standing
   backend.
6. **Chunk length** — lift the M0 5 s placeholder to ~20–30 s plus overlap (recording OQ4, joint
   with DP).

**Founders' sequencing (2026-07-18) — recording-led.** Wrap the recording service as the next big
gain: user-facing, and it gives the beta tester a touch-and-feel surface.

- Items (1) gap-detection and (5) the ASR pipeline are the priority pair.
- Capture surfaces to build behind the `ChunkSource` seam are **bodycam (device)** and **computer**
  — mic, screen recording, and browser-extension screen capture.
- **Capture-modeling note.** Screen *video* and any system or tab *audio* are separate C1 streams,
  each with its own `stream_id`, like the wearable's A/V demux. Browsers expose tab and system
  audio via `getDisplayMedia`/`tabCapture` only on some platforms — Chrome carries tab audio
  broadly, system audio on Windows and ChromeOS, and macOS needs a native-app loopback. The mic is
  always captured as its own stream, never through the screen recorder.
- A recording-lead session (Prompt B plus this scope) owns the slice.

**Founders' refinement (2026-07-18, second pass).**

- Consent gate → back-burner ([D13](../DECISIONS.md)): pre-pilot, not pre-beta.
- Capture-surface order (1): **phone web client** — camera and mic via `getUserMedia` over
  HTTPS/tunnel. The bodycam stand-in and the structured beta handover: Gnandeep gets a press-record
  URL. The `live_video_chat` POC already proved iOS capture, MediaRecorder and tunnel on this exact
  leg — reference, not lift ([D7](../DECISIONS.md)).
- Capture-surface order (2): **computer** — screen video via app and browser-extension screen
  share, tab audio via the extension (`tabCapture`). System audio out of scope for now; computer
  mic continues from M0.
- The recording server demuxes phone A/V into per-modality C1 streams (charter OQ8 pattern).
- **Chunk-length lean** (OQ4, pinned in-session with DP): variable-length chunks cut at VAD speech
  pauses within ~5–30 s bounds. Frozen C1 already supports it — per-chunk `t_start`/`t_end`, and
  `sequence` density is length-independent. Semantic cuts avoid mid-sentence splits and may obviate
  audio overlap, since exact `t_end[n] == t_start[n+1]` adjacency becomes a clean second continuity
  signal. Fixed windows remain fine for video and screen streams.

### Learn-loop MVP slice — the capture skeleton (2026-07-09)

**Goal.** One audio chunk, end to end: the **computer microphone** captures a chunk → recording
lands the bytes in `/raw` and emits a **C1** envelope → data-processing runs **ASR** and writes a
**C2** processed record to storage `/context`. This proves the learn-loop spine (recording →
data-processing → storage `/context`) with the *minimum* of every service — it starts the data
compounding the whole thesis rests on. Deliberately **audio-only, no enrichment**: ASR transcript
+ segment timestamps, no diarization, no world-data, no vision.

**Capture model (hold this — it's the user→recording reality).** The user→recording feed is a
**continuous, always-on** life stream (body-cam / always-on computer mic + screen), **not** a
press-to-record clip. Recording **carves** that live stream into dense, sequential,
wall-clock-stamped chunks (C1's `(stream_id, sequence)` + `t_start/t_end`). So downstream — to
data-processing — data arrives as **bounded chunks with start/end times**, but those boundaries are
**recording's artifact, not semantic units**: an utterance or word can straddle a chunk edge. For
the M0 skeleton, ASR each chunk independently; cross-chunk **boundary stitching** is a later
refinement, **not** an M0 gate — but build data-processing *knowing* the stream is continuous
underneath. This is also exactly why consent + delete-last-N (recording's M2) are load-bearing:
capture is always-on, so there is no natural "stop" the user leans on.

**Skeleton scope (decided in-session, D10).** One device+modality first: **computer mic → ASR-only
→ a `/context` record** — the simplest capture path. It reuses the POC Phase-1 audio machinery
(faster-whisper/WhisperX) and dodges the GPU-heavy vision/OCR path. Screen-frames→OCR and wearable
A/V are later slices.

**In this slice**

| WS | Service | M0 deliverable | Contracts it must honor |
|---|---|---|---|
| A | **recording** | Computer-mic capture → chunker → `PUT` bytes to storage `/raw` for a `blob_ref` → emit a **C1** envelope to data-processing. Mint globally-unique `stream_id`/`chunk_id`; dense zero-based `sequence`; device auth deferred. | produces C1 (both legs); push/at-least-once, dedup on `chunk_id` |
| B | **data-processing** | Consume C1; **pull bytes by `blob_ref`**; run **ASR** (transcript + segment times); stamp `pipeline_version`; write a **C2** record to `/context`; idempotent on `record_id`. | consumes C1, produces C2; C8 not in this slice |
| C | **storage** | Extend the running `:8083` service: **`/raw`** blob write (`PUT`, mints opaque `blob_ref`, idempotent on `chunk_id`) + read-by-ref; **`/context`** C2 write (idempotent on `record_id`), time-indexed on `(user_id, t_start)`. | serves the C1 blob leg + C2 write |
| E | **platform** | One box hosting the three services + an ASR runtime (GPU optional at M0 — faster-whisper runs on CPU for the skeleton); a shared dev env. Thin — just enough to run the loop. | none (enables A–C) |

**Out of this slice (later slices):** diarization + translation + full audio pipeline; text +
image + video pipelines (OCR-specialist pass, dense captioning); world-data enrichment (speakers,
faces, geo/place, objects); the cross-source time spine (multi-device skew); C8 synchronous API;
C10 training-window read; C11 recency index; wearable + browser-extension + mobile capture; consent
enforcement (recording's M2 — no always-on capture ships without it; the mic-only *dev* skeleton
predates that gate). Each is its own slice.

**Gate — interface freeze: Done (2026-07-09).** C1 + C2 v0 frozen in
[../ARCHITECTURE.md](../ARCHITECTURE.md) §Contracts (learn-loop block) + machine-readable in
[../contracts/](../contracts/) (`c1_raw_stream_envelope.v0.json`, `c2_processed_record.v0.json`),
stress-tested by a 5-lens adversarial critic pass before freeze (2 blockers + 7 fixes applied). The
frozen forks:
- **Skeleton (D10):** computer mic → ASR(+segment times) → `/context`; no diarization/enrichment.
- **C1 delivery (D11):** push, at-least-once, dedup on `chunk_id`, order/gaps via dense zero-based
  `(stream_id, sequence)`; blob-first write invariant.
- **/raw write (D11):** recording `PUT`s bytes → storage mints an opaque `blob_ref` → recording
  emits C1 carrying it; data-processing pulls bytes by ref. Blob leg pinned as prose (not a new
  C-number), like C9's wire format.
- **C2 (D10):** `record_id` deterministic on `(chunk_id, pipeline_version)` (idempotent upsert,
  version-forward reprocess); `enrichments` present-but-empty (mirrors C4 trace arrays).

**Build order + fan-out.** Storage is **not** chartered-cold — it is the running serve-loop service
on `:8083`. So:
1. **storage M0 lands first/ahead** — add `/raw` (blob write+read) and `/context` (C2 write) to the
   existing service. It is the shared dependency both A and B write to.
2. **recording M0** (mic → `/raw` PUT → C1 emit) and **data-processing M0** (C1 → ASR → C2 →
   `/context`) **fan out in parallel** against the frozen C1/C2, both targeting storage's dev
   endpoints. Shared **C1/C2 conformance fixtures** (recording ⇄ data-processing) from day one, as
   the recording charter's C1-churn mitigation requires.
3. **platform** provides the box + ASR runtime alongside.
4. An **integrator** session wires them and drives one chunk end to end.

**Integrator exit criterion (capture v0 done):** a real audio chunk captured at the computer mic
lands in `/raw`; a C1 envelope reaches data-processing; ASR produces a C2 record that persists in
`/context` and is re-readable by `record_id` and by `(user_id, time)` range; re-delivering the same
`chunk_id` is a no-op (no dup blob, no dup record). No enrichment, no vision — just the capture
spine, proven.

#### Learn-loop build conventions (v0) — so recording / data-processing / storage interoperate
- **Stack:** same as serve loop — Python 3.11, FastAPI + uvicorn per service, `httpx` inter-service,
  **pydantic** models mirroring `../contracts/*.json`, `pytest`. ASR = **faster-whisper** (POC
  Phase-1 stack), CPU-capable for the skeleton so it runs on any box; GPU is an optimization.
- **Storage endpoints (new, integrator finalizes exact paths):** `PUT /raw/blobs` (bytes +
  `chunk_id`/`user_id`/codec/sha256 → `{blob_ref, bytes, sha256}`, idempotent on `chunk_id`);
  `GET /raw/blobs/{blob_ref}` → bytes; `POST /context/records` (validates C2, idempotent on
  `record_id`); `GET /context/records/{id}` + `GET /context?user_id=&from=&to=` (time-range). Mirror
  the existing `/sessions` write style.
- **`/raw` dev layout:** local blob dir (like storage's SQLite dev DB); `blob_ref` an opaque
  storage-owned key; GCS is the production target (POC "GCS is source of truth").
- **Contracts are tested:** recording validates the C1 it emits; data-processing validates C1 it
  consumes + C2 it emits; storage validates C2 on write — all against `../contracts/*.json`.
- **No agent commits;** founders' session commits after integration. Honesty rule holds — the
  capture loop must actually run end-to-end before it is reported as run.

---

### Learn-loop capture M0 — build result (2026-07-09)

**Fan-out + integrator + adversarial verify.** The four workstreams (storage / data-processing /
recording / platform) built M0 in parallel against the frozen C1/C2; an integrator wired them and
drove one continuous-capture chunk end to end on live ports; an **independent verifier** re-ran the
suites and re-drove the loop itself (proving idempotency with its own `chunk_id`, reproducing the
real-ASR transcript byte-for-byte). Honest result below.

#### What runs (executed, not claimed — independently re-verified)
- **The mock capture loop runs end to end on real uvicorn ports** (`run_learn.sh` health-gates
  storage:8083 → data-processing:8085 → recording:8084 — **first try, zero seam fixes**). One
  `/capture/run` carved a 12 s sample WAV into **3 dense, zero-based, wall-clock-stamped chunks**
  (`sequence=[0,1,2]`, one `stream_id`), each going **blob-first**: `PUT /raw/blobs` (storage mints
  the opaque `blob_ref`) → **push C1** to data-processing `/ingest` → C1 schema-validated → **pull
  bytes by `blob_ref`** → mock ASR → **C2** → `POST /context/records`.
- **Persistence + reads proven:** every C2 re-read by `record_id` **and** by `(user_id, time)`
  range (half-open `[from,to)`, matching C10), each provably sourced from a re-pullable `/raw` blob
  whose sha256 matches; per-user isolation holds (another user sees zero).
- **Idempotency proven on both legs:** re-delivering the same `chunk_id` returned the identical
  `blob_ref` and identical `record_id`, DB row counts unchanged (no dup blob, no dup record) —
  exactly-once under at-least-once. `record_id` verified deterministic: `sha256(chunk_id \x00 pipeline_version)`.
- **Contracts validated end-to-end** against the frozen JSON Schemas: the exact on-wire C1
  (captured via a validating tee) and all stored C2s validate with zero errors.
- **Bonus — real ASR genuinely ran:** the optional `faster_whisper` leg (base/int8/CPU) was
  installed + run live, producing a real transcript persisted as a schema-valid C2
  (`pipeline_version=asr-fw-v0`); the verifier reproduced it byte-for-byte. Standing backend
  restored to **mock**.

#### Tests (re-run independently by the verifier, real counts)
| Service | Result |
|---|---|
| storage | **26 passed** (10 serve-loop unregressed + 16 capture-M0) |
| data-processing | **9 passed** |
| recording | **27 passed** |
| **total** | **62 passed, 0 failed** |

#### Residual risks / explicitly NOT in M0 (feed the next slices)
- **Gap-detection is emit-side only, not enforced.** `(stream_id, sequence)` is emitted densely +
  schema-min-validated, but **no consumer detects a gap / lost chunk / duplicate sequence** at
  runtime. "Zero silent loss" is currently an affordance, not a check — closing it (a gap-detector
  on data-processing ingest feeding recording's continuity report) is the **top M1 item**: it is
  recording's headline mission guarantee.
- **Consent / authz: none.** Anyone can drive `/capture/run` + `/ingest`; delete-last-N /
  right-to-be-forgotten unimplemented. Recording's M2 (consent enforcement) must land **before any
  real always-on capture** — load-bearing precisely because capture is continuous.
- **Mock + file-source + single-stream:** mock ASR is the standing backend; capture reads a sample
  WAV (no real mic on this box); single device, single modality (audio), single process. Real mic
  (recording M1), diarization/enrichment, multi-device time-spine, vision/text pipelines,
  C8/C10/C11 are later slices.
- **Cross-chunk boundary stitching: out of M0 by design** — each chunk is ASR'd independently, so an
  utterance straddling a chunk edge is split (per the capture-model note). Later refinement.
- **Single-process in-memory dedup:** data-processing dedup is a per-process dict; cross-restart /
  multi-replica idempotency leans on `record_id` determinism → storage `/context` upsert (the
  durable backstop, exercised). A shared dedup store is later hardening.

**Exit criterion (capture v0 done): MET for the mock loop**, independently verified. Ports: storage
8083 · recording 8084 · data-processing 8085. Run guide: `services/platform/deploy/run_learn.sh`
(`--smoke` / `--status` / `--stop`).

---

### Modality seam — data-processing goes multi-modal (2026-07-10)

**Why.** The audio path was built; the C2 contract is modality-agnostic. So we refactored DP to a
**modality-agnostic core + a `Processor` plugin seam** so future sessions can each own one modality
(video / image / text) as a **disjoint, self-registering plugin** — zero shared-core edits. Two
parallel skeleton agents (DP seam + recording `ChunkSource` seam) + an adversarial verifier.

**What's built + proven (verified live + adversarially, 84 tests: storage 26 · DP 24 · recording 34).**
- **DP core** (`app/main.py` `/ingest` + `app/pipeline.py` + `app/dedup.py`): validate C1 → dedup on
  `chunk_id` (now caches `chunk_id → [record_id,…]`) → pull blob → dispatch by `modality` to a
  registered `Processor` → **for each returned unit** assemble+validate a C2 and POST `/context` →
  return `{ok, record_ids:[…]}`. Audio moved behind the seam **unchanged** (its `record_id` is
  byte-identical to the pre-seam value — backward compatible).
- **`Processor` seam** (`app/processing/`): a plugin sets `modality`+`content_kind` and implements
  `process(c1, blob, …) -> list[ProcessedUnit]` (a **list**, so *one chunk → many records* is native).
  Self-registering via `@register` + package auto-import — **adding a modality is one new file + a
  fixture, no core edit.** `record_id = sha256(chunk_id ∥ pipeline_version [∥ discriminator])`.
- **Stubs (mock transforms):** image→1 `caption` (OCR woven in per D8), **video→3 keyframe
  `caption`s (one-chunk-many-records, discriminator=index)**, text→1 `text`. All four `content.kind`s
  proven E2E to `/context` on live services against real storage-minted `blob_ref`s; every C2
  schema-valid; `record_id`s deterministic (recomputed + idempotent on re-POST).
- **Recording `ChunkSource` seam** (`app/sources/`): the carver generalized so future capturers plug
  in; the WAV source is one impl; C1 emit path unchanged; **no new real capturers**. C1 absorbed a
  non-audio (`image`) modality with **no additive field**.

**Regression caught + fixed (the verifier's honesty audit earned its keep).** DP's `/ingest` reshape
(`record_id` → `record_ids:[…]`) **broke recording's `/capture/run` live (HTTP 500** — `capturer.py`
still read the singular field, feeding `None`s into a `list[str]` model); green unit tests **masked**
it because recording's fake still returned the old shape. Fixed: capturer reads + **flattens**
`record_ids` across chunks; the fake returns the new shape (with a `fanout` knob); added a
**fan-out regression test** (3 chunks × 3 records → 9 flattened); **re-verified `/capture/run` → 200
live** with populated `record_ids`, C2 re-readable. Data was never lost (C2s always landed) — only
the API envelope was broken.

**Two C2-additive gaps surfaced by the pressure-test — both deferred, both NON-blocking, neither
needs a version bump now** (recorded as DP charter OQs; the frozen C2 was not touched):
- **Video per-keyframe timing:** N keyframe records share the chunk's `t_start/t_end` → they collide
  on storage's `(user_id, t_start)` index. Fix is an **internal seam hook** (optional per-`ProcessedUnit`
  `t_start/t_end`; C2 already has per-record timestamps) — **no schema change.** Defer to the video session.
- **Image / keyframe OCR frame-location (bbox):** C2 `content` has no home for structured region
  geometry (OCR *text* survives, woven into the caption; only the bbox is lost). Fix = an **additive
  optional** field (`content.regions` / `enrichments.text_regions`) — touches the schema additively
  (old records still validate). Freeze-additive **when a real OCR pass lands.** Defer to the image session.

**Launch a modality session (the seam handoff).** To bolster video / image / text end-to-end:
1. DP: drop `app/processing/processors/<modality>.py` (a `Processor` subclass, `@register`) + a
   `tests/fixtures/<modality>.*` C1+blob — **no core edit**. Build the real pipeline (video: VidProc +
   keyframe captioning, wire per-keyframe timing via the seam hook; image: ImgProc + OCR-specialist +
   dense caption, add the bbox field additively; text: real normalization).
2. Recording (when its real capturer is wanted): drop `app/sources/<modality>_source.py` + one
   `SOURCE_BUILDERS` entry — no `capturer.py` edit, no C1 change.
3. Both write to the same frozen C1/C2 + the running storage `/raw`+`/context`; verify against
   `contracts/*.json` and drive via `run_learn.sh`.

---

### Post-capture-alpha sequencing — the DP deep session + what follows (2026-07-19)

**In flight — a DP-led deep-work session** (branch `svc/dp-async-observability`, worktree
`~/nmn/cl-dp-async`), launched in parallel with this founders' session. Its charge is work the
canvases already pin, bundled because it shares one service pair (DP + recording) and one node:

1. **Async `/ingest`** — DP charter **M7 territory arriving early** (the charter allows M4–M7
   to interleave after M3; video/M3 landed 2026-07-19). ACK `202` fast + process on a worker so
   capture cadence decouples from pipeline latency; retry safety rides the existing `chunk_id`
   dedup + deterministic `record_id`. Motivated by the verification-round finding that a fully
   loaded chunk (real ASR + diarization + VLM captions) can lawfully outlive recording's
   delivery timeout (fleet-mitigated today via `RECORDING_HTTP_TIMEOUT=120`). Resolves DP OQ13.
   **Scope discipline: this is the ACK+queue half of M7** — backpressure policy, dead-letter +
   backfill stay M7-proper; the queue lands observable by construction (queue depth is a
   chartered M8 metric in the same slice).
2. **D9 metrics emission** (DP **M8** + recording **M6**): `/metrics` Prometheus text + each
   service's Grafana dashboard JSON. **Emission half only** — Platform's shared
   Prometheus/Grafana backbone is the follow-on small slice (D15 below), so recording M6's
   "scraped by the shared Prometheus" exit criterion closes only when that backbone lands.
3. **node-7 smokes of the real audio backends** (pyannote diarization / whisper translation /
   AST acoustic events — built 2026-07-19 as correct-by-inspection seams, explicitly unrun):
   run each genuinely (GPU + HF-gated pyannote) before anyone trusts a switch-flip.
4. **The OQs the work naturally answers** — headline **recording OQ3, the codec/bitrate ladder
   (joint recording × DP)**: real pipelines + smokes say what fidelity each modality actually
   needs (alpha datapoint: CRF-28 mac screen video is readable but soft on fine text). Also
   informed: DP OQ3 (GPU placement for pipeline models vs continuum's future nightly window).

**Founders' ratification posture — the async `/ingest` reply shape.** It is an **inter-service
wire change, not a C-number**: C1 governs the envelope, not the reply, so the accepted shape
gets pinned as prose in the DP canvas exactly as the `/raw` blob leg rides D11. The deep
session proposes; this standing founders' session ratifies. The bar the proposal must clear:

- **At-least-once safety intact end-to-end.** Re-pushing a queued/in-flight `chunk_id` must be
  a cheap idempotent ACK, not a second enqueue. And the new loss window is named honestly:
  today, inline processing means a mid-processing DP crash fails recording's push → un-acked →
  recording retries → covered. **A `202` ACK closes that coverage** — once acked, recording
  never re-pushes, and DP's continuity detector notes "seen" at accept time, so a crash between
  ACK and processing would today be **silent** record-level loss. The proposal must either make
  the queue survive restart (durable spool, DP re-drains — the recording-side spool precedent)
  or make the loss *detected* (an accepted-vs-processed split visible to the gap report) with a
  re-drive path. "Accepted-risk + named mitigation" is not enough here — this is the zero-
  silent-loss guarantee itself.
- **Recording's consumer side moves in the same slice.** The capturer reads `/ingest` replies
  (`record_ids` today) and the gap report cross-checks DP `/continuity`; "accepted" and
  "processed" split under async, and the report must not read async lag as loss **nor claim
  `clean` while chunks are still pending** — verdict semantics need an explicit pending/drain
  signal, as `segment_states` already provides on the client leg.
- **Terminal outcomes stay discoverable.** `record_ids` reachable for whatever needs them
  (status poll, `/continuity`, or equivalent); worker failures land somewhere visible, not in a
  dropped task.
- **Mock-default + all three suites stay green; C1/C2 schemas untouched.**

**ratified — 2026-07-19, same session (→ D16).** The deep session's final design memo
(worktree `scratch/design_memo.md`, five-reviewer verified; its two load-bearing code claims
spot-checked here against `ledger.py:405` / `capturer.py:172` / `capture_web.py:297`) **clears
the bar and strengthens it**: it located the exact mechanism of the silent-loss clause —
recording's `_dp_missing_unacked` reconciliation trusts `dp_acked=1` to mean "C2 exists"; a
202-at-accept would redefine that to "merely accepted," so any accepted-then-lost chunk reads
`clean` — and made the fix **non-negotiable: preserve the invariant `dp_acked` == "C2 durably
written"**, dropping the original "zero recording change" claim as unsound (recording moves
in-slice, as the bar required). Ratified wire, pinned as prose in the DP canvas at merge:
- `INGEST_ASYNC=0` default, inline path byte-unchanged. Async: **202**
  `{ok, accepted, chunk_id}` on accept (+`duplicate:true` on a queued/in-flight dedup hit);
  **200 + record_ids** on a done-dedup-hit; deterministic rejections (400/422/501) resolve
  **synchronously pre-claim**, never deferred into a dead-letter; **503** = honest
  backpressure on a bounded queue (finite `INGEST_QUEUE_MAX` default — unbounded+volatile
  would OOM-lose-all and read `clean`).
- `/continuity/{stream_id}` gains **additive** `processed` (C2-durably-written runs) +
  `dead_lettered`; "covered ≠ processed". C1/C2 schemas untouched.
- Recording in-slice: `finalize_chunk(accepted=)` + additive `dp_state='accepted'` column;
  report reconciliation — accepted-unconfirmed → verdict **`recording`** (never `clean`),
  dead-lettered → **`gaps`**; `clean` now means "DP confirmed C2 for every chunk."
- **Honest loss boundary accepted:** this slice guarantees *never falsely `clean`* — all loss
  visible, none auto-recovered; auto-recovery (a durable DP pending journal) is explicitly
  M7-proper, and an in-memory DLQ-with-recovery illusion is explicitly rejected.

**One ratification condition:** the **re-drive path for accepted-unconfirmed chunks must be
named + drilled once in-slice** — emitter re-push keyed on `dp_state='accepted'` (on restart
or periodic), or a documented manual re-POST; either satisfies (DP idempotency makes re-push
safe: done-claim short-circuits to 200+record_ids). Without it, a `recording` verdict after
queue loss is visible but has no documented way back to confirmed until M7. **Accepted
caveat, noted:** `record_ids=[]` ledger provenance for 202-confirmed chunks (ids stay
derivable — deterministic on `(chunk_id, pipeline_version[, discriminator])`; inline/mock
fleet unaffected).

**Slice result — same day, merged (`0ce4941`; `dev` fast-forwarded with it).** The deep
session landed the full charge: the **D16 wire verbatim including the re-drive condition**
(`POST /capture/sessions/{id}/redrive` + `emitter.redrive_accepted_chunks` + 2 drill tests;
a re-drive that hits a done-claim also **backfills `record_ids`**, softening the accepted
caveat), D9 emission on both services (zero new deps, pure-ASGI middleware,
cardinality-bounded; both dashboard JSONs shipped), node-7 smokes of all three real audio
backends green (+2 real pyannote torch-2.x fixes found by the smoke), **DP OQ13 resolved +
recording OQ3 answered per-modality** (no ladder: 16 kHz mono audio is model-native — the
existing demux target was already exactly right; video is resolution-bound not bitrate-bound
→ container-copy, ~2560 px only for OCR-heavy screens, cost dial = keyframe cadence). Their
18-agent adversarial round confirmed 9 findings — 5 fixed pre-merge, 1 deferred **fails
Safe** (a DP-restart false-`gaps` over-report window; never hides loss, so
never-falsely-`clean` holds; the M7 durable journal closes it — land before async is trusted
for final archived verdicts). **Founders' merge review executed here:** all three suites
re-run independently — **DP 98 · recording 120 · storage 26, green** — and the condition +
OQ records verified in the diff. Detail:
[ws-async-observability](../services/data-processing/handoff/ws-async-observability.md).

**D15 — closed (2026-07-25): the learn loop is proven end-to-end.** The continuum kickoff ran to
completion. The consolidation core (**Morpheus**, reimplemented from the research line `b3c58e1`,
parity-proven) trains a real 32B life adapter → gate v1.1 → C5 → **served in vLLM** (M0), behind a
lean 5-verb loop over storage client seams. The **Phase-3 DP dogfood** then routed real Speed data
through the **actual recording→DP→storage→continuum services** and — once block content matched
parity (the 1-min rule-bend's collapse was **dose**, not the pipeline) — **reproduced the baseline
separation (pipeline sound)**. The captured-days-are-inert-until-continuum-runs gap (below) is now
mechanically closed. **Remaining founder acts, both scheduled not blocking:** the **C10 freeze
becomes a C10 *evolution*** (raw range-read → **day-log fetch, random-access by `(user, window_id)`**)
folded into a **storage/C10 board session** that also ratifies the storage charter expansion
(day-log materialization + recipe registry + reservoir); and a **recipe/dose finding for Gnandeep**
(dose must scale with block-text at our native cadence). The cluster-split + DP-OQ5 items below rode
along: continuum's nightly window ran on node-7 via SLURM without disturbing Gnandeep's occupancy.
Detail: [../services/continuum/HANDOFF.md](../services/continuum/HANDOFF.md).

*Original D15 plan (for the record):*

1. **Continuum kickoff is the next founders-led slice.** It is the last unstarted pillar and
   the thesis itself: every upstream leg now stands (serve loop proven on real Qwen3-VL-32B;
   capture alpha-complete on three real surfaces; DP real for both live modalities; `/context`
   filling with pipeline-versioned records) — captured days are inert exactly until continuum
   runs. **Gate: a C10 v0 interface freeze first** (storage × continuum jointly propose,
   founders ratify — the pattern that made C1/C2 interoperate first try), frozen against the
   beta-proven range read (`GET /context/records?user_id=&from=&to=`, half-open `[from,to)` —
   deliberately C10's shape since D12). Kickoff is also the deliberate forcing function for two
   parked conversations: the **cluster split** (agenda item 2 — nightly training window vs
   Gnandeep's wider-cluster occupancy vs serving) and **DP OQ5 reprocess policy** (mixed
   `pipeline_version` dialects inside a training window). The long-parked **D6 OCR spot-check**
   rides the vLLM relaunch that continuum-era eval needs anyway.
2. **Platform D9 backbone as the small parallel slice:** the one shared Prometheus + Grafana on
   node-7, scraping what the deep session emits, provisioning both dashboard JSONs + the
   standard node/dcgm exporters. No file/service contention with kickoff; closes D9 end-to-end
   so both founders open one Grafana URL.
3. **DP image/text pipelines (charter M2) explicitly deferred until a producing surface
   exists.** Nothing on the fleet emits an `image` or `text` C1 stream today (phone camera →
   video; extension/mac → video+audio), and the image pipeline's chartered payload — on-screen
   text — already flows through the video keyframe OCR weave (D8). The OQ14b bbox additive C2
   field waits with it, by design. Revisit trigger: a screenshot-still / document / clipboard
   capture surface, or continuum finding keyframe-caption density insufficient for training.

Considered and passed, on the record so we don't re-litigate:
- **Mobile app + C8 sync API now** — mobile's v0 roles (chat surface + speech-output sink) get
  their differentiated value from a personalized model, and C8's one-dialect duty binds when
  interactive requests carry media. Their moment is when the personalization era opens (first
  adapter serving), not before.
- **Standalone C10 freeze without kickoff** — freezing a contract without its consumer at the
  table breaks the freeze-with-both-sides pattern that made C1/C2 land clean; meanwhile the
  beta is not blocked (it uses the range read as-is).
