# review_actions — data-processing onboarding review

> The **active list only** for review of the onboarding field guide
> ([field-guide.html](field-guide.html)). Completed rounds are trimmed once executed; every
> durable outcome lands in its proper home — code, the worklog, the board, or the charter — and
> this file tracks only what is still open.

**Last updated:** 2026-08-07 · the guide was **rewritten for the v1 world** at the rebuild's
Stage G ([../docs/refactor_stage_G.md](../docs/refactor_stage_G.md)). Every prior review round is
superseded by that rewrite, and this board is reset to match.

## Active

- **First review of the rewritten (v1) guide — pending.** The old module-by-module review rounds
  (CTO, 2026-08-05, modules 06–07 executed / 08–15 parked) are **retired**: they reviewed the v0
  guide, whose subject — the record-vs-mutation law, discriminators, the two-record video shape,
  the OCR sidecar — the rebuild deleted. The guide is now ten modules teaching one record per
  chunk, slots, the version law, the machinery/bureaucracy split, heal, and an honest
  real-vs-unbuilt board. It wants a fresh read against the running service.

- **The v0-guide finding list is cleared, not carried.** Those findings were repo defects the v0
  guide taught around; the rebuild resolved or dissolved them. For the record: the OCR sidecar and
  its `VIDEO_OCR_*`/`VIDEO_PIXEL_*` output-affecting env knobs are gone (no output-affecting env
  knob exists now — Slot Law L4); the clip path is the shipped path, not a branch; `ASR_VAD` and
  the other v0 knobs that could re-key records under an unchanged id cannot exist under L4's
  determinism gate. Anything a fresh read surfaces about the v1 code goes to the
  [service board](../HANDOFF.md) §Next, not here.

## How to edit the guide

[STYLE.md](../../../STYLE.md) §Teaching views governs the voice, and [ORG.md](../../../ORG.md)
§Documentation protocol governs what a teaching view owes ([D22](../../../DECISIONS.md)): it is
linked from the charter, the repo wins on any disagreement, and the guide is corrected in the same
session as the change it teaches. The Stage G rewrite is itself that same-session correction — the
guide moved to the v1 world in the same stage that demolished the v0 code and rewrote the charter.

## Where completed outcomes live

- The v1 rewrite and its rationale: [../docs/refactor_stage_G.md](../docs/refactor_stage_G.md)
  (WP-G6) and this stage's commit.
- The world the guide now teaches: the charter's §Slot Law and §Condensed history
  ([../CHARTER.md](../CHARTER.md)).
