# `app/vision/prompts/experimental/` — WS-H's offline-eval packs (NOT a production registry)

These `.prompt.md` files are **arms of an offline experiment**, not shipped prompts. They
exist for the ratified **O-8** gate (blind-vs-injected A/B) and for *O-4* (per-frame vs
per-clip):

| file | arm | architecture |
|---|---|---|
| `screen-clip-blind-v1.prompt.md` | **B** — captioner sees the frames only | blind, fuse-at-consolidation |
| `screen-clip-hint-v1.prompt.md`  | **D** — captioner sees OCR *for the app name only* | minimal-hint (the ratified fallback if A fails its gate) |

Architecture **A** (injection, the shipped design, D-09) is the packaged
`screen-clip-v1.prompt.md` — no copy is needed here.

## Why a SUBDIRECTORY and not the flat pack dir

`§11 → WS-H` names these files as `app/vision/prompts/<id>.prompt.md`, on the reasoning that
"new paths ⇒ no conflict with D's production packs". Verified against WS-D's shipped
registry, that reasoning does not hold, and dropping them in the flat directory would be a
production incident:

* `PACK_DIGEST` is `compute_digest(_PACKS, _ROUTES)` — a digest over **every** loaded pack
  (`app/vision/prompts/__init__.py`).
* `load_registry` globs `*.prompt.md` in the source dir, so two extra files in the flat dir change
  the aggregate digest, change `prompts.version_tag(vs)`, change the clip primary's
  `version_fragment`, and therefore *fork `record_id` for every production caption* — for an
  experiment that never ran.
* WS-D's `tests/test_prompt_pack.py` asserts `_ALL == set(all_packs())` over the six shipped
  ids, so the same drop-in reddens the suite (house rule 2: ≥ 465 green), in a file WS-H
  does not own (house rule 1).

`load_registry`'s glob is **non-recursive**, and so is `test_prompt_pack._copy_pack`'s. A
subdirectory is therefore *completely inert* to the packaged registry, to `PACK_DIGEST`, and
to every WS-D test — while still being a committed git path under `app/vision/prompts/`,
which is what "a pack is only reproducibly defined by a git state" needs.

## How an arm actually loads them

`scripts/prompt_ab.py` assembles a **complete registry per arm** into a temp dir: the six
packaged packs + `schemas.json` + this arm's experimental pack + a rewritten `routes.json`
whose `family_defaults.clip` names that arm's pack. It then runs the arm in a **subprocess**
with `VIDEO_PROMPT_DIR` pointed at that dir (packs load once per process at import — D-13's
TOCTOU discipline, so one process cannot hold two arms) and `DP_OFFLINE_EVAL=1`.

The fork is automatic and two-fold, so **arms cannot collide** even under the mock backend:

* `prompt_dir_fingerprint` (OUTPUT_AFFECTING) hashes the arm dir's contents into `cfg_tag`
  → forks under *every* backend, mock included;
* `PACK_DIGEST` + the pack id fork `prompt_tag` under the `vlm`/`vertex` backends.

The fingerprint is a pure function of the dir's `(relpath, bytes)`, so the arm's
`pipeline_version` is reproducible across machines and temp paths.

## Editing rules

* These files are **WS-H-owned**. They are not routed to by the packaged `routes.json` and
  no production code path can select them.
* Keep the placeholder set a subset of what `clipcap/vlm._build_messages` supplies —
  `span_s`, `scenario_label`, `n`, `offsets`, `ocr_block`, `words_lo`, `words_hi`.
  `prompts.render` **raises** on a placeholder with no context key; unused context keys are
  ignored, which is exactly how the blind pack never sees `ocr_block`.
* Keep `Reply with ONE JSON` as the last-section marker: `vlm._build_messages` splits on its
  final occurrence so the images land before the task text (D-02).
