# Old-world surface purge — Phase 3 worklog: the register and the top level

> **Executed.** Phase 3 of 4. Does not start Phase 4.
>
> **Branch:** `cursor/purge-phase3-register` · **Base:** `main` at `ee095d1` (phase 2 merged).
> Not pushed, not merged.

---

## Authorities (quoted)

### Authority (i) — `docs/purge_classification.md`

> Classification: **CURRENT** (governs the running system as-is) · **OLD** (superseded by the
> rebuild or governing deleted machinery) · **MIXED** (a surviving decision wearing an old-world
> card — rewrite proposed).

On the collision between the purge policy and the register's own law:

> **Proposed resolution** (needs explicit ratification, because it amends the register's own
> rules): the register keeps its *rows* for every decision that still governs …; rows that
> governed only deleted machinery are removed whole, leaving numbering holes; the "superseded rows
> stay visible" clause is amended to "…stay visible *in git history*".

### Authority (ii) — FOUNDER RATIFICATION, quoted in `docs/purge_phase2.md` — wins on conflicts

> 1. `c2_processed_record.v0.json` is EXEMPT, not deleted — the parity proof's P1 precondition
>    loads it by `$id` and a green storage test runs that proof in-process. …
> 4. Register corrections: D3 reclassed OLD-spent … D18 reclassed MIXED … fix D12's stale 'until
>    C10 lands' watch-out; D26's rewrite must attribute `DP_DIALECT_FREEZE`'s retirement to the
>    no-knobs law (L4/D25), not L9; D16's rewrite states BOTH halves … D15's rewrite keeps the
>    clause the platform board cites; D28's keeps 'latest updated_at, rowid tiebreak' verbatim.

### Authority (iii) — D29, STYLE.md, ORG.md

D29 suspends the append-only laws for this stage, with a lapse condition; STYLE binds what a
document reads like; ORG decides which file owns a fact. The three governed every judgment below,
and the one place they conflicted is recorded under §Judgment calls.

---

## What was already done

Four of the founder's seven register corrections had **already landed** in phase 2's `3d2739c`.
Verified individually rather than assumed:

| Correction | State on arrival |
|---|---|
| D3 reclassed OLD-spent | already removed; the numbering hole is clean |
| D26 attributes `DP_DIALECT_FREEZE` to L4/D25 | already correct |
| D16 states both halves | already correct |
| D15 keeps the platform-cited deferral clause | already correct |
| D28 keeps "latest `updated_at`, rowid tiebreak" verbatim | already correct |
| D12's stale "until C10 lands" watch-out | already fixed |
| D18's title and one-liner | one-liner correct; the body still narrated `ingest_time` |

D3's only surviving citations were the two inside `handoff/engineering.md`, which WP-3C deletes,
so the hole closed itself.

---

## WP-3A — the register (`a3e04b1`)

**D23** was titled *"the Slot Law replaces the record-emission law"* and spent its card comparing
itself to a law no reader can reach. It is now **"the Slot Law"**, stating the twelve invariants
positively and why they hold structurally. **D20** was *"the cutover exit bar"*; it is now **"the
day-log parity bar"**, keeping the three tiers and the representation-vs-content rule and dropping
the spent cutover checklist.

**A lineage chain that would have pointed into the void.** Removing D19's "C2 `discriminator`
surfaced" call orphaned a bidirectional link — D24 *"retires D19 (discriminator clause)"* and D19
*"discriminator clause retired by D24"*. Both were dropped rather than left naming a clause the
tree no longer holds.

**D10** becomes the minting record it actually is. **D18**'s window is stated on `updated_at`;
**D27** says storage keeps `created_at` and `updated_at` as separate columns rather than naming
the column they replaced.

Five "full reasoning" pointers into `handoff/engineering.md` (D10, D12, D17, D19, D20) became
*"Full session record: git history."*

**Two pre-existing broken anchors** fixed while the checker was pointed here: the D15 index link
aimed at a heading renamed long ago, and the DP charter linked ARCHITECTURE's §Ownership splits by
a heading text it no longer has (2 sites). Neither was purge damage; both were found because the
link checker resolves anchors, not just files.

---

## WP-3C — the boards and the engineering thread (`759002b`)

`product/HANDOFF.md` rewritten. A board is rewritten in place, and this one had become a record of
a migration. Resolved escalations left it (E-3(a), E-3(b)); E-2 is stated as built — whole-record
— instead of carrying a Watch-out that argued for the **retired kind-granular design against
D28's ratified one**.

### The engineering thread: deleted history, reseeded thread

The classification's disposition is **DELETE** (2,193 lines, 118 census hits). Executing it
literally would have broken something: `PROMPTS.md:175` defines `<ASPECT>` as one of four —
`engineering · research · design · hiring-ops` — and the session prompt at `:108` tells the agent
to read `product/handoff/<aspect>.md`. An absent file breaks every engineering founders' session.

**Resolution:** the 2,193 lines of pre-rebuild history are deleted; the thread is reseeded in the
shape of its three siblings, with an agenda of what is genuinely open across services. The
classification's own words allow it — *"HANDOFF's aspect-threads table row gets reseeded or
dropped"* — and it satisfies both authorities rather than one.

### Fifteen citation sites, two of which the brief had not listed

| File | Site |
|---|---|
| `product/ORG.md` | narration `:122` + doc-map row `:222` |
| `product/DECISIONS.md` | D10 · D12 · D17 · D19 · D20 pointers |
| `product/HANDOFF.md` | board-protocol line **+ its own aspect-thread row** (unlisted) |
| `product/services/README.md` | `:17` |
| `platform/deploy/README-learn.md` | `:28` |
| `platform/deploy/.env.example` | `:24` — **unlisted**: claimed ports are pinned in a worklog section |
| `input/HANDOFF.md` | `:38` |
| `continuum/handoff/worklog.md` | `:23` |

### Storage board: live drift, not just stale flavour

Beyond the deferred lines, two statements were **false about the running system**:

1. §Current state claimed `POST /context/records` *"assigns storage's own `ingest_time`"*. It has
   assigned `created_at`/`updated_at` since D27 — verified against `storage/app/db.py:106-108`.
2. The day-log rule still taught the superseded `(chunk_id, content.kind, discriminator)` key. It
   now states latest `updated_at` per `(chunk_id)`, keeping the orderability reasoning verbatim
   because that argument survives the axis change unchanged.

Recording's board narration and its two workstream links into deleted DP files were fixed with it.

---

## WP-3B — the architecture canvas (`40df15d`)

The C2 and C10 cards were written as migration records: headers advertising *"live since the
Stage F cutover"* and linking the superseded schema beside the running one, Rules sections stating
the old rules and then patching them with a **"v2 deltas"** block.

The deltas block is folded into three plain rules — slot routing, what `created_at`/`updated_at`
mean, and whole-record retraction — so nothing that governs was lost with the framing.

**"How it got here":** entries whose *subject* was the transition are gone (C2's D24 fan-out story
and the discriminator-surfacing entry; C10's D28 re-cut and the built-and-cut-over entry). Entries
documenting current decisions stay — D17 civil time, D18's watermark, F3's dialect refusal, F4's
epoch grid — with the axis corrected where they named it.

**The discriminator vocabulary row is removed.** A vocabulary defines the words a reader will
meet; that row defined a retired one and sent the reader through a broken anchor to a section
phase 2 deleted, teaching the dead word in its own link text.

---

## WP-3D — the learn-loop onboarding view (`77c21b8`)

The document carried **three stacked "Amended" banners**, the last of which admitted outright that
its DP internals were the pre-rebuild service and that a full rewrite was *"a client-testing-phase
task"*. D22's bargain is that a teaching view is corrected in the same session as the change it
teaches; a view that knowingly teaches a system that no longer exists is worse than none.

Rewritten current-world: §4.2's five *(pre-rebuild)* subsections became the ingest spine with its
five redelivery verdicts, the stage graph as drop-in files under the Slot Law, models as
supervised servers with identity verification, and the prompt pack's digest pin. §3's C2 card
shows the v1 shape; §5's walk-through emits one record with `caption` and `ocr` slots rather than
two records told apart by a discriminator. §6, §7 and §8 restated on the current axis.

**The prose citations no link checker catches** — files named without being linked — are gone
(`ws-video-clip.md` ×2).

Other services' sections were touched only where they referenced old DP.

---

## WP-3E + WP-3F — contracts, top-level docs, the sidecar remnant (`76e4282`)

`contracts/README.md` rewritten per its ratified disposition. It was **materially stale
independent of the purge**: its table marked `c2…v0.json` "the running wire" and
`c10_daylog.v1.json` "the running read", with the shapes that actually run listed as *"the rebuild
target"*.

### Schema descriptions carried live drift

| File | Was | Now |
|---|---|---|
| `c10_training_window.v1.json` | bounds described as **INGEST-TIME** ×4 — the running window contract | `UPDATED_AT` (D27) |
| `c14_reservoir_ledger.v0.json` | "exactly as /context preserves `ingest_time`" | "preserves `created_at`" |
| `c2_processed_record.v1.json` | title "(DP rebuild — …)", stage citations | stated plainly; negative guards kept |
| `c10_daylog.v2.json` | v1 comparisons throughout | states the running rules |

**Description- and title-only, proved mechanically** — both keys stripped at every depth, then
compared to `main`:

```
  c10_training_window.v1.json        valid-json=True  identical-with-descriptions-stripped=True
  c10_daylog.v2.json                 valid-json=True  identical-with-descriptions-stripped=True
  c14_reservoir_ledger.v0.json       valid-json=True  identical-with-descriptions-stripped=True
  c2_processed_record.v1.json        valid-json=True  identical-with-descriptions-stripped=True
```

`c10_daylog.v1.json` deleted per its disposition — storage validates against v2
(`schemas.py:44`), and the only references were two docstring asides plus STYLE's card-template
example link, all repointed. Storage's 354 stayed green.

**WP-3F:** `sidecars/` removed. Git never tracked it, so it was invisible to every check while
still teaching a deleted service to anyone listing the tree.

---

## Judgment calls, stated rather than buried

1. **The engineering thread was reseeded, not dropped** (§WP-3C). The classification says DELETE;
   ORG and PROMPTS depend on the aspect existing. Reseeding honours both.
2. **`c2_processed_record.v0.json` stays**, against the classification's DELETE, because the
   founder's amendment 1 overrides it. Verified live: `daylog_parity_diff.py:948` loads it by `$id`.
3. **My own draft broke the ratchet, and was fixed rather than absorbed.** The `contracts/README`
   rewrite introduced three two-em-dash sentences (STYLE rule 6). The regenerated baseline was
   reverted, the violations fixed, and only then was the baseline regenerated.

---

## Exit evidence

### Token census — zero in phase-3 scope

Method note: the census is **case-sensitive** for the narrative families and case-insensitive only
for `rebuild` / `cutover`. A fully case-insensitive grep matches `stage B` and `ws-morpheus`, which
are not the tokens; that produced 110 false positives on the first run and was corrected. `.json`
files are included, per phase 2's lesson.

```
=== FINAL CENSUS — my scope ===
product/PROMPTS.md:85:You are the <WS-NAME> workstream agent inside the <SERVICE NAME> of Nucleus AI's v0
product/contracts/c2_processed_record.v0.json:5:  "description": "The processed life-stream record data-p
product/contracts/c2_processed_record.v0.json:70:    "discriminator": { "type": "string", "maxLength": 12
product/contracts/c2_processed_record.v0.json:71:    "processed_at": { "type": "string", "description": "
(end)
```

**Two exemptions, both verified rather than assumed:**

- `PROMPTS.md:85`'s `<WS-NAME>` is a **live template placeholder**, not a retired workstream id —
  `ORG.md:100,185` still define `handoff/wsN-*.md` as the current workstream pattern.
- `c2_processed_record.v0.json` is exempt by founder amendment 1, and its own descriptions are the
  retired shape, which is exactly what the parity fixture format is.

### Phase-4 remainder — per service

| Service | Hits |
|---|---|
| storage | 125 |
| platform | 81 |
| continuum | 73 |
| recording | 38 |
| input | 7 |
| data-processing | 6 (the ruled exemptions) |
| output | 4 |
| inference | 4 |
| **total in `product/`** | **342** |

Of these, three sit in `storage/HANDOFF.md` (`:70–72`) — its own workstream-index rows pointing at
`handoff/ws-storage-mvp.md` and `worklog.md`, both of which exist. They belong with storage's
workstream register in phase 4, not with the board narration this phase owned.

### Review-only signals, adjudicated

`v0` — **150 hits across 24 files in phase-3 scope**, all adjudicated and kept. Every one is the
*product version* ("Nucleus v0", "v0 has four surfaces", "what v0 built"), a current schema
filename (`c1_raw_stream_envelope.v0.json`), or the exempt parity reference. None is the retired
data-processing world.

`legacy` — 3 hits, all fixed ("legacy range read" → "raw range read"; "legacy caption volumes" →
"high caption volumes").

`migration` — 1 hit, kept: `ARCHITECTURE.md:987`'s "executes upgrade migrations (fleet retrain)"
is a genuine current concept, not a transition record.

### Link check — files **and** anchors

```
product/ :  TOTAL broken internal links: 0
docs/    :  TOTAL broken internal links: 0
```

The checker resolves section anchors under GitHub's slug rules, not just file existence. It
initially reported four false anchor failures because it stripped `_` as markdown emphasis;
underscores are legal in anchors, and the checker was corrected before its results were trusted.

### All eight suites

| Suite | Tail |
|---|---|
| data-processing | **569 passed, 4 skipped** |
| storage | **354 passed** |
| continuum | **264 passed, 7 skipped** |
| recording | **144 passed** |
| servers/common | **30 passed** |
| servers/ocr | **8 passed** |
| servers/ast | **6 passed** |
| servers/pyannote | **6 passed** |

### Style ratchet — zero files grown

```
  shrank product/ARCHITECTURE.md: 5 -> 4
  shrank product/HANDOFF.md: 11 -> 3
  shrank product/onboarding/LEARN_LOOP.md: 27 -> 23
  shrank product/services/storage/HANDOFF.md: 12 -> 10
deleted files: 2 (22 findings)
FILES THAT GREW AGAINST MAIN: 0
TOTAL: main 330 -> now 293

STYLE.md: no regressions (293 known findings held at baseline)
```

### Fleet — read-only, GET /health

Before (2026-08-07T22:27:04Z) and after (2026-08-07T23:49:12Z), all twelve:

```
8083:200 8084:200 8085:200 8121:200 8122:200 8131:200
8132:200 8141:200 8142:200 8151:200 8152:200 8161:200
  DP pid/uptime:  632026 16:03:09
```

DP is the same process throughout. No service was restarted or POSTed to.

---

## Owed to phase 4 — an honest list

1. **The four service trees**, in descending size: storage 125 · platform 81 · continuum 73 ·
   recording 38, plus input 7 · output 4 · inference 4. Charters, service registers, workstream
   files and their `handoff/` directories.
2. **`storage/HANDOFF.md:70–72`** — the workstream-index rows, which travel with storage's
   workstream register rather than with the board.
3. **The continuum C-register** (C-4, C-6, C-7, C-10, C-11), classified in §3.5 and untouched here.
4. **The parity apparatus retirement** — still the single deliberate act recorded on storage's
   board §Next, still gated on modernizing continuum's synthetic and replay paths. Until it
   happens, `c2_processed_record.v0.json` and the v0-shaped fixtures stay exempt by ratification.
5. **`seam_check.py`'s v1 rewrite** — ordinary engineering, not purge work (ratification
   amendment 2), and still owed: it builds `version:"0"` C2 records at four sites against a fleet
   that schema-gates v1.
6. **`servers/ast/requirements.txt` does not pin `huggingface-hub`** — recorded on the DP board
   §Next 7 in phase 2, still open, still a deliberate act because pins feed `/health` identity.

Phase 4 **not started**. Not pushed.
