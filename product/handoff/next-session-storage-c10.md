# Session prep — the storage/C10 founders' board session

> **Purpose:** the launch prompt for the next founders' session, written while the context was
> hot (end of the 2026-07-26 D17 session) so the session opens on decisions instead of
> explanation. Paste the block below into a fresh chat. Everything in it was verified against
> code on 2026-07-26; the citations are real and cheap to re-check.
>
> Format follows [../PROMPTS.md](../PROMPTS.md) §D (founders' session), extended with the
> finding/context/design-space shape that worked for the D17 session.

---

```
This is a Nucleus AI founders' working session on: engineering.

Read: product/HANDOFF.md (whole-company canvas: service status board, escalations, Decisions
log — D17 is the immediate predecessor to this session), then product/handoff/engineering.md
(its 2026-07-26 entry is the direct handover). You have full context of
VISION/ARCHITECTURE/ORG; open them as needed. Service canvases that matter today:
product/services/storage/HANDOFF.md + CHARTER.md, product/services/continuum/HANDOFF.md.

Today's agenda: ratify the STORAGE SCOPE EXPANSION and the C10 EVOLUTION. Four decisions, in
dependency order — and the first one gates the rest:

  0. window_id — what is it, once the window stops being a local date? (GATE)
  1. The per-user PROFILE contract (home_tz) — mint its C-number.
  2. The DAY-LOG MOVE — continuum → storage, and C10 becomes a day-log fetch.
  3. The WATERMARK WINDOW — [last_trained_t, now) replacing local-date window_for().

You are my co-founder, not a scribe: push back, propose, decide with me.

---
WHERE THIS COMES FROM

This session was queued twice: by the 2026-07-25 learn-loop close-out (storage charter
expansion + C10 evolution, both marked "pending founders'-board ratification"), and by
D17 (2026-07-26), which decided the timezone split, BUILT it end to end, and deliberately
left three things for this board rather than slipping them in unreviewed.

D17's status is split on purpose — do not re-flatten it. The timezone split is BUILT and
verified (device reports device_tz per chunk on C1 → DP passes it verbatim into C2 source{} →
storage columns → continuum's renderer; storage's per-user home_tz is scheduling + fallback
only; UTC stays canonical). The WATERMARK-WINDOW clause is DECIDED, NOT BUILT. An earlier
draft of D17 claimed both under one "BUILT" headline; that was caught as review item O-12 and
corrected across four sites. Same discipline applies to whatever we decide today.

---
DECISION 0 — window_id. THE GATE. Settle it before anything else.

Everything downstream keys on it, and it is more load-bearing than it looks. VERIFY THIS
FIRST — it is five greps — then reason:

  - It is a FILESYSTEM PATH COMPONENT and an rmtree target.
    product/services/continuum/app/ids.py:8 — ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$, no
    slashes, no "..". It becomes a path in journal/, cycles/, reservoir/, adapters/
    (cycle.py:64,145).
    ** CONSEQUENCE: a raw RFC3339 instant FAILS this regex — "2026-07-21T11:00:00Z" contains
    colons, which are not in the allowed set. So "the natural watermark key is the window's
    end instant" cannot be taken literally; it needs a path-safe compact form. **

  - It is a TOTAL ORDER, compared as a STRING, in three places:
      publish.py:71      active_before() — C5 alias monotonicity ("never move the serving
                         alias backward") does (e.get("training_window") or "") < window_id
      cycle.py:106,115   journal debt + latest_window, via >=
      reservoir.py:105   replay's before_window filter, via >=
    "w2026-07-21" works because lexicographic order == chronological order. ANY replacement
    format MUST preserve that property, or alias monotonicity and replay windowing silently
    mis-order.

  - It SEEDS TRAINING. cycle.py:147 — seed = int(_h(user_id, window_id)[:8], 16).
    ** CONSEQUENCE: changing the format changes the seed, which changes amplification and
    replay sampling. Runs before and after the change are not apples-to-apples. If we care
    about comparing against the Phase-3 / parity numbers, say so explicitly and decide
    whether to pin the seed on something stable instead. **

  - It is EMBEDDED IN CHILD IDS. daylog.py:88 (seg_id "{window_id}_s00001") and :206
    (block_id "{window_id}_b0000"). A format change re-keys every segment and block.

  - It is CARRIED IN C5 as training_window, so it is adapter LINEAGE. Note both C5 publishes
    on disk carry the literal "w-day5" (scripts/m0_smoke.py:133 — window_for never ran), so
    lineage already contains a non-date format. That is either a precedent or a mess; decide
    which.

My read (argue with it): window_id must stay an opaque, path-safe, lexicographically-sortable
per-user token, and the honest move is to STOP deriving meaning from it. Today its meaning
(a local date) is exactly what makes it fragile — the dateline case is only a problem because
the id encodes a local calendar date. Candidate: keep "w" + a UTC compact stamp of the
window's END, e.g. w20260721T1100Z — path-safe, sorts correctly, no local calendar in it.
Cost: it re-keys everything above, including the training seed. The alternative — keep
"w<local-date>" and accept that a travelling user can produce a duplicate — is cheaper today
and buys the problem back later.

---
DECISION 1 — the per-user PROFILE contract (home_tz)

D17 gave storage a per-user profile whose FIRST field is home_tz (IANA). Its only two jobs:
(a) SCHEDULING — deciding when a user's nightly cycle fires, a question asked before any of
that night's records exist; (b) FALLBACK — when a record carries no device_tz.

Already written, awaiting a C-number:
  - product/services/storage/CHARTER.md § Scope — the "In+ Per-user profile" row
  - product/ARCHITECTURE.md § Ownership splits → "User timezone"

It CANNOT ride the recipe registry: recipe_id is global and versioned (recipe_id == filename
stem, registry rule), so a per-user value there forks recipe_id per user. Mint it alongside
the recipe-registry and reservoir C-numbers.

Open: is it a "profile" (policy values: home_tz, later a boundary_local_time override,
locale, consent-state pointers) or narrowly a "user settings" read? I lean profile — home_tz
will not be alone for long — but that is a naming call with a real blast radius, so decide it
deliberately rather than by default.

---
DECISION 2 — the DAY-LOG MOVE (continuum → storage), and C10 becomes a day-log fetch

As built, the day-log is built IN CONTINUUM and C10-as-consumed is storage's beta
GET /context/records?user_id=&from=&to= range read. The seam was designed for this move:
  product/services/continuum/app/clients/daylog_client.py — LocalDayLogClient behind a
  RecordProvider interface, with "(future) HttpDayLogClient" already named in its docstring.
Reference builder to lift: continuum's daylog.py / window.py / renderer.py.

HARD CONSTRAINT: render_block must stay byte-parity with the research line @ b3c58e1. The
2026-07-25 port proved parity (render_block 1427/1427, byte-equal rendered files); moving the
renderer must not break it. Whoever builds this re-proves parity, not assumes it.

Three concrete requirements the 2c build already surfaced (product/services/storage/HANDOFF.md
§ "Sharpened by continuum's 2c build"):
  - Day-log fetch must serve ANY prior window on demand, by (user_id, window_id) — replay
    re-reads PRIOR day-logs, so this is random-access over history, not a forward cursor.
  - Storage must answer "which windows has this user consolidated?" — continuum infers this
    from the reservoir ledger today; once the reservoir is pure audit/provenance it needs a
    home in storage.
  - The materialized day-log must carry its recipe/format version — continuum keys its cache
    on the day-log content fingerprint (daylog_fingerprint in daylog_client.py).

** NEW, and the reason DECISION 1 comes first: if storage renders the day-log, STORAGE INHERITS
THE RENDERER — including the timezone resolution D17 just built (daylog._block_zone: the
record's device_tz wins, the window's home_tz falls back). So storage will need to READ the
profile to render. Profile → day-log move is a real dependency, not a preference. **

---
DECISION 3 — the WATERMARK WINDOW

D17 decided the cycle window becomes [last_trained_t, now) — a plain UTC duration query,
which is what ARCHITECTURE's C10 row and storage's charter always said. It is NOT built:
window_for() / closed_window_before() are still local-date (app/window.py) and nightly.py
still calls them. Nothing is broken; local-date windows work.

Why it is the better design: storage needs no timezone to serve C10 at all; a missed night is
ABSORBED into the next window instead of lost; and the local-date pathologies (23h/25h days,
a repeated local date across the dateline colliding window_id) cannot arise.

Still genuinely open, and it is storage's own charter OQ: C10 WATERMARK SEMANTICS — late-
arriving records, reprocessed records, pipeline_version bumps. What advances last_trained_t,
and what happens to a record that lands with a t_start inside an already-trained window?
This is the substantive design work of the session; the range arithmetic is the easy half.

---
ALSO ON THE TABLE (name them so they do not get lost again)

  - E-2 (HANDOFF § Escalations) — storage's kind-aware
    DELETE /context/records?user_id=&from=&to=&pipeline_version=&kind=. It BLOCKS the WS-VC
    screen-video cutover, it is storage-owned, and it is the same session's natural home.
    It MUST key on content.kind: Phase-3 proved captions and transcripts can share one
    pipeline_version, so a kind-blind delete removes transcripts to remove captions. Root
    cause it mitigates: daylog.py filters on NEITHER kind NOR pipeline_version, so any day
    re-consolidated across a cutover renders both dialects and double-counts.
  - C10 friction noted-not-pinned by Phase-3 (continuum/handoff/ws-phase3-dogfood.md:129-130):
    C10 kind-filtering, and blob-by-reference (storage OQ8, unbuilt — ws-phase3-dogfood.md:55).
  - The recipe-registry and reservoir C-numbers, minted at this ratification.

---
CONSTRAINTS

  - Per ORG.md:42-45, ANY contract change edits product/ARCHITECTURE.md § Contracts FIRST,
    then the machine-readable schema in product/contracts/, then BOTH owning services'
    canvases. D17 found the hard way that a claim can hide in the JSON schema's description
    and be missed by a count taken from prose — check the schema, not just the summary.
  - C1/C2 are v0 FROZEN; additive optional fields need no version bump. D17 just exercised
    this precedent cleanly (two fields on C1, three on C2 source{}, `required` untouched on
    both, re-validated) — copy that discipline.
  - When you add a field to a frozen schema, MOVE THE PYDANTIC MIRRORS WITH IT. Storage's
    Source and DP's C1Envelope/C2Source are extra="forbid"; in D17 the schema gate passed and
    the mirror rejected, caught only by a test. Same trap is waiting here.
  - Apply DP's T2 bar ("store nothing you cannot spend",
    services/data-processing/docs/record-emission-law.md) — but apply it as D17 finally read
    it: T2 is "a gate on WHEN, not a veto", and it governs signals a service PRODUCES, not
    provenance it forwards. Do not use it to veto a field whose consumer this very session is
    chartering. Precedent for genuine parking: E-5, written up and deliberately not taken.
  - Nothing about a decision is "BUILT" until it is built. Split status in the Decisions log
    row itself (see D17 post-O-12).

---
DELIVERABLE

  a) window_id: its format, and an explicit list of what re-keys if it changes (path
     components, the three string comparisons, the training seed, seg/block ids, C5 lineage).
  b) The profile C-number + shape, and whether it is "profile" or something narrower.
  c) The day-log move: the C10-evolved read's shape (random-access by (user, window_id) +
     window enumeration + recipe/format version), who renders, how render_block parity is
     RE-PROVEN, and what happens to continuum's daylog.py/window.py/renderer.py + the
     RecordProvider seam.
  d) Watermark semantics: what advances last_trained_t; the late/reprocessed-record rule; the
     pipeline_version-bump rule.
  e) E-2's shape, or an explicit decision to keep it separate.
  f) For each of (a)-(e): DECIDED vs BUILT, stated separately.

Write decisions where they live (contract → ARCHITECTURE.md § Contracts + product/contracts/;
scope → the owning CHARTER; the decision itself → product/HANDOFF.md Decisions log as D18+),
echo in product/handoff/engineering.md, and update the service status board if it moved.

Current suite baselines to quote (post-D17, 2026-07-26): storage 32 · continuum 189 ·
recording 144 · data-processing 770 +21 skipped · extension deno 11.

Still outstanding in product/onboarding/REVIEW_NOTES.md but NOT this session: O-2/O-3/O-4
(C5-freeze doc fixes — note O-2 does touch storage/CHARTER.md:46's C5 field list) and
O-5..O-11 (charter/canvas hygiene).
```

---

## Why these four, in this order

- **window_id first** because decisions 2 and 3 both key on it: the day-log fetch is
  *addressed* by `(user_id, window_id)`, and the watermark window changes what a window_id can
  mean. Deciding it late means redoing them.
- **Profile before the day-log move** because moving the renderer into storage moves D17's
  timezone resolution with it, and the `home_tz` fallback is half of that logic. Storage cannot
  render an anchor line without the profile read.
- **Watermark last** because it is the only one with genuinely open design work left
  (watermark semantics is storage's own charter OQ), and because it is the one that re-keys
  `window_id` — so it wants decision 0 already settled.
