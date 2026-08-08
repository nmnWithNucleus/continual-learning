# HANDOFF — Data Processing Service working canvas

> Single touch-point for any agent (or human) picking up work on this service.
> Read [CHARTER.md](CHARTER.md) first (mission/scope/interfaces), then this file — the
> volatile working record. Conventions: [../../ORG.md](../../ORG.md) § Documentation protocol.

**Status:** built — v1 live on `main` · DP suite green · *Last updated:* 2026-08-07

**Where we are.** The service ingests C1 chunks and writes **one C2 v1 record per chunk** —
built from `content.slots`, one stage per slot, never edited ([CHARTER.md](CHARTER.md)
§Slot Law). Models run as long-lived **model servers** (`servers/whisper`, `pyannote`, `ast`,
`ocr`) supervised from inside the DP process, which is their parent and calls them as a thin
async orchestrator (L9). A graceful DP stop takes them down with it; a `kill -9` leaves all
eight running as orphans, to be reaped by hand.
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
| 7 | **Pin `huggingface-hub` in `servers/ast/requirements.txt`** — see [the card below](#pinning-huggingface-hub-for-the-ast-server). | a deliberate pin decision |
| 8 | **L9's shutdown clause names the wrong cause.** [Card below](#l9s-shutdown-clause-names-the-wrong-cause). | a charter edit; founders' call |
| 9 | **"Enforced in CI" is not true yet.** Nothing runs `pytest`; the only workflow is `docs-style.yml`. [Card below](#enforced-in-ci-is-not-true-yet). | wire a runner, or reword |
| 10 | **T-1 and T-3 are order-dependent in isolation.** [Card below](#t-1-and-t-3-are-order-dependent-in-isolation). | nothing; needs a registry-restoring fixture |

### "Enforced in CI" is not true yet

> `designed` 2026-08-08 · surfaced while rewriting the onboarding field guide

**In one line.** [CHARTER.md](CHARTER.md) §Slot Law and this file's Gotchas both say the T-spine is
enforced in CI, and nothing runs it.

**Why it's this way** — the repository's only workflow is `docs-style.yml`, which runs
`product/scripts/style_check.py` over markdown. No runner executes `pytest` for this service, so the
law is enforced by whoever remembers to run the suite before pushing. The tests themselves are real
and green; the claim about who runs them is what is wrong.

**Watch out for** — closing this by wiring a runner and closing it by rewording the two documents
are both honest, but they are different decisions. Pick one deliberately rather than letting the
sentence stand.

### T-1 and T-3 are order-dependent in isolation

> `designed` 2026-08-08 · surfaced while rewriting the onboarding field guide

**In one line.** Running only `tests/test_t1_determinism.py` and
`tests/test_t3_version_composition.py` produces two failures that do not exist in the full suite.

**Why it's this way** — T-1's helpers first-import `app/stages/**` under a deliberately emptied
registry. Python caches those modules, so their `@register_stage` side effects can never fire again
in that process, and T-3 then reads an empty registry and reports that the contract surface moved.
The full suite stays green because other files import the stage modules earlier and normally.

**Watch out for** — the false red lands on the one test that guards the registered contract surface,
which is exactly the test a newcomer would trust. Verified 2026-08-08: full suite 569 passed and
4 skipped; the two-file subset 2 failed and 77 passed.

### L9's shutdown clause names the wrong cause

> `designed` 2026-08-08 · surfaced while rewriting the onboarding field guide

**In one line.** [CHARTER.md](CHARTER.md) §Slot Law L9 says a `kill -9` leaves the fleet running
*"because replicas own their sessions"*, and that causal claim is wrong.

**Why it's this way** — nothing on Linux kills a child when its parent dies; an orphan is
re-parented and keeps running whether or not it has its own session. There is no `PR_SET_PDEATHSIG`
anywhere in `app/`, and DP shares one user-session scope with every replica, so no cgroup teardown
would sweep them either. The real cause is narrower: `SIGKILL` cannot be handled, so the lifespan
shutdown never runs and `Supervisor.stop()` never signals anyone. `start_new_session=True`
(`app/supervisor.py:170`) exists for a different purpose — it lets `_kill` `killpg` a replica's whole
tree without signalling the supervisor itself, which the code comment at `:165-169` states correctly.

**Watch out for** — the same imprecise clause is repeated in `app/supervisor.py:14-21`,
[HANDOFF.md](HANDOFF.md) above, and `product/HANDOFF.md`; all four want the same edit. A second error
rides along in the charter and `product/HANDOFF.md`: they say all eight orphans hold GPU memory, but
the two `ocr` replicas are declared `gpu: null` and pinned to CPU, so the true figure is eight
holding ports and six holding GPU memory (confirmed against `nvidia-smi`, 2026-08-08).

### Pinning huggingface-hub for the ast server

> `designed` 2026-08-07 · engineering, not purge work

**In one line.** `servers/ast/requirements.txt` pins `transformers` but not
`huggingface-hub`, so rebuilding that venv from requirements alone does not reproduce the
environment that is running.

**Why it's this way** — it was caught live: a rebuild resolved `huggingface-hub` to 1.27.0
against the installed 1.26.0. That repair worked around it with a pip constraints file built
from the running venv, which fixes the instance and not the cause.

**Watch out for** — pinning is a deliberate act, not a tidy-up. Package versions are reported
in `/health.frameworks`, so choosing the pin means choosing what the ast server declares itself
to be. Decide the version first, then pin. Note that the identity check does not cover this. The
client compares only what the manifest's `expected_identity` pins, which is `model_name` plus the
weight revisions, so a `huggingface-hub` drift passes unnoticed rather than being caught
(verified 2026-08-08).

## Gotchas

- **The law is executable.** `tests/` (T-1…T-6) enforce the Slot Law in CI; a violation is a red
  test, not a review note. Run the suite before trusting a change to the executor or a stage.
- **No output-affecting env knob exists** (L4). If you reach for an env var to change what a record
  says, stop — that is a code change (a `vS`/`vB` bump), and the determinism test will catch it.
- **The inline ingest path is kept on purpose** as C8's skeleton; it is byte-identical to async for
  one chunk. Deleting it would orphan the synchronous contract.
