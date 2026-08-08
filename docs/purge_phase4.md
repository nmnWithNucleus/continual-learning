# Old-world surface purge — phase 4 (the last), and the phase-3 fixes

> **Done.** The seven sibling services no longer explain themselves by the data-processing
> rebuild. Branch `cursor/purge-phase3-register`, four commits on top of phase 3, **not pushed**.
> The founder merges phases 3 and 4 to `main`.

Authorities: the ratified [`purge_classification.md`](purge_classification.md), the founder
ratification in [`purge_phase2.md`](purge_phase2.md), and **D29** — the working tree is the
teaching surface, git history is the archive.

Read with [`purge_phase3.md`](purge_phase3.md), whose §Corrections were appended in this session.

---

## The instruments are committed now

Two scripts, so nothing in this project rests on a number only one session could produce.

| Script | Home | Why there |
|---|---|---|
| [`docs/census.sh`](census.sh) | `docs/` | Its token list *is* the retired world's vocabulary. Putting that vocabulary inside `product/` would plant in the teaching surface exactly what the purge is clearing out of it. |
| `product/scripts/link_check.py` | `product/scripts/` | A standing documentation gate with no old-world vocabulary at all, and the natural sibling of `style_check.py`. |

`census.sh` walks `git ls-files` rather than the filesystem. That is the honest scope — the
repository is what teaches — and it is also the difference between 1.2 s and a multi-minute walk
on this NFS mount. It encodes **no exemptions on purpose**: every surviving hit is adjudicated by
hand below, because a script that swallowed its own exceptions would make the gate unfalsifiable.

The token list is wider than phase 3's: bare `rebuild`/`rebuilt`/`cutover`, separator-agnostic
`ingest[ _-]time`, `v0[ -]world`, `emission[ -]law`, and `.json` bodies alongside `.md` and `.py`.

---

## WP-4A — the phase-3 fixes

### 1. D15's platform-cited clause, restored

Correction 6 in phase 3 was checked against the wrong clause. The full record, quoted and
corrected, is appended to [`purge_phase3.md`](purge_phase3.md) §Corrections. In short: the card
kept the M2 image/text deferral, but what `platform/HANDOFF.md:9,55` cites is the **D9-backbone
"small parallel slice" assignment**, and that assignment is still `not started` on the platform
board — current-world content, removed.

Restored to **D15**, not moved to D9, because the board splits them exactly that way: D9 decided
the backbone, D15 sequenced it as the parallel slice. Moving the clause would have falsified
`platform/HANDOFF.md:55`'s parenthetical. The card is retitled *the parallel slice, and the
image/text deferral* so the heading covers both clauses; the index row, its anchor and D9's
reciprocal lineage move with it.

**This departs from the ratified classification**, which judged the clause spent
(`purge_classification.md:396`). The founder overruled it on the board's evidence. Recorded so the
departure is visible rather than quiet.

### 2. Dangling citations the register surgery created

| Site | What it pointed at | Fix |
|---|---|---|
| `continuum/handoff/worklog.md:81` | "first contract act, per D15" — a removed clause | The sentence stands on its own: "the first joint contract act", matching the §Cross-service flags line below it |
| `c2_processed_record.v1.json:5` and `:162` | "the charter §Slot Law dead-concepts list" — §Slot Law is L1–L12 and has no such list | The guard now stands on its own reasons: `additionalProperties:false` makes each name fail closed, and one record per chunk leaves no sibling to discriminate |
| `storage/CHARTER.md` §OQ7 | *(named for WP-4B; see below)* | A one-line tombstone, because `storage/handoff/worklog.md:104` cites it by number |
| **WS-VC** | *(named for WP-4B)* | It was never in `storage/CHARTER.md`; the two cited lines were `E-2 is no longer a correctness blocker for the WS-VC cutover` and the `WS-VC double-count`, both rewritten to the standing rule |

The schema edits are proved **description-only**: strip every `description` and `title` at every
depth from the HEAD version and the working version, serialize both with sorted keys, compare.
Identical for both files, whole-file bytes differing. That is the same proof phase 3 used.

### 3–4. Two small falsehoods

`DECISIONS.md` said "Seven calls taken:" above six bullets — the seventh was the discriminator
call phase 3 removed. It reads "Calls taken:" now: no count to go stale, and no resurrection of a
retired term. `handoff/engineering.md` cited `PROMPTS.md §E`, the Integrator prompt; the founders'
session is **§D**, which is what its three siblings say.

### 5. `c10_training_window.v1.json`

One `ingest_time` survived phase 3 as **"Ingest time"** — capitalised, space-separated — inside
`t_start`'s description on a *running* contract. The root description also named
`c10_daylog.v1.json`, a file this branch deletes. Both fixed, description-only proof re-run.

That single survivor is why the census now matches `ingest[ _-]time` case-insensitively.

### 6. The phase-4 numbers are estimates

Quoted and corrected in `purge_phase3.md` §Corrections. An independent run under the widened
tokens diverges in **both** directions — storage 125 → 138, platform 81 → 49 — which is fine for
sizing work and unfit as a gate. The gate is the adjudicated residue below, not a count.

### 7. LEARN_LOOP residue

The founder counted six sites; nine were found and fixed. Each was verified against code or
against the live fleet before it was touched.

| Site | What it said | What is true |
|---|---|---|
| §4.2 | "models **no longer** run inside this process" | Transition voice. Every model runs in a server process, so there is nothing in this one to shield |
| §4.3 | storage is "deliberately thin… the last unexpanded learn-loop service (26 tests)" and the range read is "today's C10" | Storage carries the whole D18 expansion (354 tests); C10 means `GET /training/daylog` |
| §4.4 | "185 tests passing" | 264 |
| §4.7 | "the running node-7 fleet predates the three DP merges — restart pending" | The fleet runs the merged dialect; the captioner has its own `:8161` instance, and the collision E-3(b) named is closed by *deployment*, since both code defaults still say `:8000` |
| §5 | "the gated-off target path — under today's default… ~4 keyframe captions… instead of 2 records" | `clipprep → screentext → clipcap` is the running dialect and it lands one record per chunk. No keyframe stage exists |
| §5 diagram + clip stages | "OCR sidecar (CPU)" | A model server on `:8151`/`:8152` |
| §6 milestone table | recording 133 · storage 310 · a second, different continuum count · `*pipeline sOUND` | 144 · 354 · one count · `*pipeline sound*` |
| §8 item 2 | the day-log "lives in continuum, not storage", present tense under a banner twenty lines away | Storage serves it; the correction now sits in the item |
| §8 item 4 tail | "storage's *ingest* axis", inside unbalanced markdown with an unclosed parenthesis | `updated_at` (D27), and the markdown closes |
| §8 item 7 | "the join itself is kind-based and handles both" | The join walks slots on `(chunk_id)` — and continuum's docstring, which that item quotes, is fixed in WP-4B |
| §Verification limits | four stale suite counts, closed with "Those are the numbers to quote" | Re-run them rather than trust them, and here is what no pass has exercised |

---

## WP-4B — the cross-service sweep

### storage — the charter taught an axis the code does not have

D27 renamed storage's clock and D28 replaced the day-log dedup key. The charter still taught the
old ones in **fifteen places**, including §The time index — the section it calls load-bearing.

Two of those were not stale flavour but false statements about a running service:

- **OQ9** said "E-2's `DELETE /context/records` is still unbuilt". It is built, with a day-log
  cascade two tests prove (`test_retraction.py:238,269`). What is genuinely still open is the
  reservoir leg and the full-user primitive, which is what it says now.
- **The M5 card** dated its own shape to a pending cutover.

§Day-log materialization's dedup rule was `latest ingest_time wins per (chunk_id, content.kind,
discriminator)` — two of those three fields no longer exist. It is now latest `updated_at` per
`(chunk_id)`, rowid tiebreak, with the reason given first-principles: one record per chunk (L2)
leaves a chunk no siblings, so there is nothing finer to key on.

**OQ7 is a tombstone, not a hole.** The classification offered both. The register's preamble
sanctions holes, but `storage/handoff/worklog.md:104` cites "CHARTER OQ7" by number inside a
worklog preserved verbatim, and a one-line tombstone keeps that citation resolving without
teaching the retired concept.

`ingest_time` **survives in storage's code on purpose**, in the migration ladder and in the tests
that build a pre-D27 database. Code that renames a column has to name it.

The `consolidation-v2.0` recipe's `note` explained itself by the rebuild and claimed v1.1 was what
the live service renders under. Both recipes are byte-mirrored into continuum and a green test
holds the copies identical — which is how the edit was caught before it shipped half-applied.

### continuum — the C-register

Removed: **C-4, C-6, C-7, C-11**. Two (C-7, C-11) were absorbed whole into D18, which now carries
the decision; C-4 is sequencing that has happened; C-6 defined day-log vocabulary for a record
shape that no longer exists. **Nothing cites them** — a tree-wide grep finds only C-1 and C-2
cited, both of which stay, exactly as the founder's list said. The numbering holes are declared in
place of the old provenance footer.

Two rows disagreed with themselves and were fixed rather than removed:

- **C-5** pinned snapshot `b3c58e1` in its Status and `9711f4a` in its Detail, and named a module
  (`app/engram/`) that C-9 renamed to `app/morpheus/`.
- **C-10** described recipe v1.1 as the target; the fleet trains under `consolidation-v2.0`.

The board claimed `HttpDayLogClient` speaks **C10 v1** (it refuses anything but v2), quoted a
suite count two rounds stale, and described E-2 as an unbuilt kind-aware primitive.

**`seam_check.py` is now recorded on the board as unable to pass as written.** It builds
`version:"0"` C2 records at four sites against a fleet that schema-gates v1. The board used to
quote "10 steps / 151 checks" as live evidence and LEARN_LOOP quoted "152" — a number that cannot
be reproduced without running a script that would fail. The unreproducible number is replaced by
the reason, and the rewrite is on §Next as ordinary engineering.

### recording

The charter's answer to the joint fidelity question with data-processing taught a video path that
does not exist: `VIDEO_FRAME_MAX_WIDTH=768`, `VIDEO_KEYFRAME_INTERVAL_S`, `VIDEO_MAX_KEYFRAMES`
and "keyframe VLM captioning". The **conclusions stand** — 16 kHz mono audio, container-copy
video, ~2560 px for text-dense screens — and only the mechanism changed: frames are extracted by
`clipprep`, and the cost dial is how many, not a keyframe cadence.

The R-register carried each founders' decision **twice** — once at its D-number and once verbatim
under a pre-register local id — and closed R-2 with a decision since taken: `INGEST_ASYNC=1` is
what the fleet runs (`/health` reports `ingest_mode: async`).

### platform — and one departure from the ratified disposition

Deleted per §2.8: `deploy/cutover_wipe.py`, `deploy/ROLLBACK-stage-F.md`,
`deploy/learn.env.stage-f-unrepoint`. The rollback window is closed by construction and the wipe
has no job left.

**`deploy/test_cutover_wipe.py` was NOT deleted whole, and this departs from the ratified
disposition.** Its last section is a live guard: it starts `run_vllm.sh --status` under a hostile
env file and asserts that `VLM_MODEL` and `VLM_REVISION` cannot be overridden. That is the thing
standing between an operator and silently re-captioning a user's whole training corpus without
`pipeline_version` moving — `run_vllm.sh:46` even names the test as its guard. The classification
called the file a "guard test for a tool that no longer has a job", which was true of nine tenths
of it and false of the tenth.

Rescued to `deploy/test_run_vllm_pins.py`, which passes, restating the pins rather than reading
them out of the script it guards. Only then did the rest go.

The **E-3(b)** row named an env var data-processing does not have (`VIDEO_VLM_URL`; it is
`VLM_URL`) and reported the collision closed. The code defaults on both sides still name `:8000`;
what separates them is `learn.env` pointing DP at `:8161`. The row says so now, in both places it
appears.

### input · output · inference

Census-clean apart from their own workstream ids. The serve loop was never rebuilt, and it shows.

### The top-level canvas — found by the fresh-eyes read, not by the census

`ARCHITECTURE.md`'s **System High-Level Diagram** — the second document in the cold-start read
order — described a service that does not exist:

> `RS["Recording Service<br/>(computer + wearable capture; no mobile capture)"]`
> `DPS["…audio: denoise→diarize→ASR→translate<br/>image/video: proc→dense caption→world data…"]`

No wearable exists; the phone web client *does* capture; there is no denoise stage and `translate`
is deliberately unregistered; the video leg is `clipprep → screentext → clipcap`. The §Day
walkthrough opened the same way. Both now describe the running fleet, with world-data enrichment
named as designed-not-built rather than drawn as if wired.

Not a single census token appears in either. It took reading the document as a newcomer.

---

## The exit gate

### Census — 364 → 168, every survivor adjudicated

The instrument is `docs/census.sh`; the classifier is in this session's scratchpad and its rule is
simple — a hit that matches no exemption class is a finding, not an exemption.

| # | Class | Justification |
|---|---|---|
| 40 | A live workstream id (`WS-A`…`WS-F`, `WS1`, `WS-P3`, `WS-D18`) | ORG.md:28,100,185 define `handoff/wsN-*.md` as the **current** pattern. Every id resolves to a file that exists, and the link checker proves every index row's link resolves. These are not the retired DP world; the token was aimed at `WS-VC` |
| 22 | The D27 migration ladder | `ALTER TABLE … RENAME COLUMN ingest_time TO created_at` and the tests that build a pre-D27 database. Code that renames a column must name it |
| 18 | The English verb | Rebuild a venv, rebuild state from a journal, rebuild a cache, "corpus rebuild ratio". Nothing to do with the DP rebuild |
| 15 | `docs/venv_repair.md` | The venv-repair worklog. It is *about* rebuilding virtualenvs |
| 15 | continuum's dated worklog and workstream files | Service-internal history; the founder's phase-4 scope preserves it pending a separate ruling |
| 14 | recording's, same | Same |
| 11 | The parity apparatus | `daylog_parity_diff.py` + `.out.txt`. Exempt by ratification; its retirement is a later one-act item |
| 10 | data-processing | Out of phase-4 scope; settled and merged in phase 2. Of these, three are `requirements.txt` comments about reproducing a build and two are DP tests *asserting the dead concepts do not exist* |
| 9 | storage's dated worklog and workstream files | Same as continuum's |
| 3 | `c2_processed_record.v0.json` | Exempt by founder amendment 1; `daylog_parity_diff.py:948` loads it by `$id` |
| 3+2+2+2 | platform's, inference's, input's, output's workstream files | Same |
| 1 | `PROMPTS.md:85`'s `<WS-NAME>` | A live template placeholder |
| 1 | `inference/CHARTER.md:162` — "Cut over too early" | A risk row about **graduating from the mentor APIs to our own model**. Ordinary English; a false positive of the `\bcut[ -]over` token |

**Zero hits fall outside these classes.** The census is not literally zero and cannot be, because
the founder's own phase-4 scope preserves service-internal history and the exempt apparatus. What
is zero is old-DP-world narrative in every document a newcomer is asked to read.

### The rest of the gate

| Check | Result |
|---|---|
| Internal links + section anchors, tree-wide | **108 files, 0 broken** (`product/scripts/link_check.py`) |
| Style ratchet | **0 files grew** against `main`; 7 shrank; total **330 → 281** |
| Twelve ports, before | 12 × `200` at **2026-08-08T00:58:57Z** |
| Twelve ports, after | 12 × `200` at **2026-08-08T02:01:07Z** |
| Data-processing | pid **632026** throughout, uptime 18:15:05, `clipcap.v1-vlm.v1+clipprep.v1-ffmpeg.v1+screentext.v1-ppocr.v1`. Never restarted, never POSTed to |

```
data-processing    569 passed, 4 skipped, 1 warning
storage            354 passed, 1 warning
continuum          264 passed, 7 skipped
recording          144 passed, 1 warning
servers/common      30 passed, 1 warning
servers/ocr          8 passed, 1 warning
servers/ast          6 passed, 2 warnings
servers/pyannote     6 passed, 12 warnings
```

Two test changes rode with prose edits, both deliberate and both named here: continuum's
`test_local_without_records_refuses_instead_of_returning_an_empty_day` asserted the refusal message
contains "INGEST" and now asserts it contains `updated_at`, because the message names the axis and
the axis was renamed; and storage's registry test caught the recipe-note edit before the two copies
could diverge.

---

## Fresh-eyes read

Opened the repo as a day-one engineer: `README.md` → `product/README.md` → `ORG.md` →
`ARCHITECTURE.md` → `DECISIONS.md` → `storage/CHARTER.md` → the data-processing field guide.

1. It reads as **one world**. No document introduces a term another retires, and no card describes
   a build in the voice of an intention.
2. The single thing still teaching a prior world was `ARCHITECTURE.md`'s own system diagram —
   second in the read order, and invisible to every token the census greps. Fixed above.
3. **What a newcomer still cannot parse:** 35 references across 20 data-processing files to a
   rebuild plan's sections — `plan §3`, `§5.3`, `§4 R4`, `the ratified §2 dialect`. The plan lived
   in `services/data-processing/docs/`, which phase 2 deleted. The sentences make sense; the
   pointers cannot be followed. Out of phase-4 scope, and owed.
4. Recording's `D-M1-1` / `D-M1-2` / `D-E7` ids **do** resolve — each is defined in a workstream
   file that still exists — so a newcomer can follow them even though they predate the register.
5. Every register (D, C, R, storage's OQ) has deliberate numbering holes, and every one declares
   the convention in place rather than leaving a reader to guess.
6. The most perishable claims in the tree are **suite counts in prose**. Four were stale in
   LEARN_LOOP alone, and two in continuum's board. They now say to re-run rather than trust.
7. Continuum's headline live-seam evidence is **stale by its own admission** — `seam_check.py`
   cannot pass against a v1-gating fleet. Better said out loud than quoted as proof.
8. The E-3(b) GPU separation lives in the **deployment**, not in code defaults. A newcomer reading
   only the code would conclude the collision is still live; both boards now say which is which.
9. Nothing claims `BUILT` that is not built. The two that did — storage's E-2 and continuum's C10
   client version — were corrected in this phase.
10. The honest gap is not vocabulary any more. It is that several documents assert numbers nobody
    re-derives on a schedule, and the tree has no mechanism that fails when one goes stale.

---

## OWED — an honest list

1. **The parity apparatus retirement.** Still the single deliberate act on storage's board §Next,
   still gated on modernizing continuum's synthetic and replay paths. Until then
   `c2_processed_record.v0.json`, `daylog_parity_diff.py`, its output and its test stay exempt by
   ratification.
2. **`seam_check.py`'s v1 rewrite.** It builds `version:"0"` C2 records at four sites against a
   fleet that schema-gates v1. Now recorded on continuum's board §Next 6.
3. **`servers/ast/requirements.txt` does not pin `huggingface-hub`.** Recorded on the DP board
   §Next 7 since phase 2. A deliberate act, because pins feed `/health` identity.
4. **35 dangling `plan §N` references in data-processing code**, across 20 files. Phase 2 deleted
   the plan they point at. Not purge work in this phase's scope; a newcomer hits them immediately.
5. **Service-internal histories** — the dated worklogs and workstream files in storage, recording,
   continuum and platform. The founder rules on these separately after client testing; they are 38
   of the 168 surviving census hits and every one is inside a dated entry.
6. **A staleness mechanism for quoted numbers.** Suite counts, check counts and test totals appear
   in prose across the boards and the onboarding view, and nothing fails when one drifts. The
   ratchet and the link checker exist because the same argument was made about style and links.

---

**Phase 4 complete. Not pushed.** The founder merges phases 3 and 4 to `main`.
