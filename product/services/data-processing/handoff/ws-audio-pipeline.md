# WS — DP audio pipeline beyond ASR: diarization · translation · acoustic events

> The AUDIO-modality lead's fill of the three staged stubs in
> [`app/processing/processors/audio.py`](../app/processing/processors/audio.py) —
> `diarize → translate → acoustic_events` — with real backends behind off-by-default
> switches, under a NEW `app/audio/` namespace. Read [CHARTER.md](../../CHARTER.md)
> §Scope (audio pipeline) + OQ11/OQ12 and [HANDOFF.md](../HANDOFF.md) (Processor seam +
> Current state) first; this is the volatile record for the beyond-ASR audio work.

**Status:** built + tested headless (57 DP tests: 38 baseline + 19 new); real backends are
correct-by-inspection SEAMS, unrun here (GPU / HF-gated / deps absent) ·
**Owner session:** audio-pipeline lead · **Last updated:** 2026-07-19

---

## What landed

The audio processor was already an explicit staged pipeline with the last three stages as
documented no-op stubs. This workstream fills them, each as a **stage-body swap** behind a
backend switch — no seam change, no shared-core edit:

| Stage | Switch (default) | Real backend | What it does when active |
|---|---|---|---|
| `_diarize` | `DIARIZE_BACKEND=off` \| mock \| pyannote | pyannote.audio 3.1 | fills `segments[].speaker` + `enrichments.speakers` |
| `_translate` | `TRANSLATE_BACKEND=off` \| mock \| whisper (+ `TRANSLATE_TARGET`) | faster-whisper `task=translate` | appends a `discriminator="translation"` transcript record |
| `_acoustic_events` | `ACOUSTIC_BACKEND=off` \| mock \| ast | HF AST AudioSet tagger | appends a `discriminator="acoustic"` caption record |

All new code is under `app/audio/` (+ one public accessor added to `app/asr/faster_whisper.py`).
Heavy optional deps live in a NEW `requirements-audio.txt` (never `requirements.txt`).

```
app/audio/
  config.py                 get_audio_config() — new env vars via os.getenv (config.py is READ-ONLY to us)
  diarize/{__init__,result,assign,mock,pyannote}.py
  translate/{__init__,result,mock,whisper}.py
  acoustic/{__init__,result,caption,mock,ast}.py
```

## Design decisions (the load-bearing ones)

1. **Every capability DEFAULTS OFF — a deliberate deviation from the "mock is DEFAULT"
   wording, forced by the MUST constraints.** With a backend `off`, its stage is a pure
   no-op, so the default audio output is **byte-identical** to the pre-fill processor: the
   mock ASR dialect (`asr-mock-v0`) is untouched, the 38-test baseline stays green, and
   `run_learn.sh` (ASR_BACKEND=mock) runs the loop headless with no new deps. A literal
   "mock diarization on by default" would flip `segments[].speaker`/`enrichments.speakers`
   and fork `pipeline_version` on the default path — breaking `test_ingest_mock` /
   `test_processor_seam` and the "keep the mock dialect untouched" rule. So diarization is
   **feature-flagged off**; `mock` is the DEFAULT **no-GPU backend chosen when you turn a
   capability ON** (headless, deterministic, exercises the full dialect in tests/run_learn),
   and `pyannote`/`whisper`/`ast` are the real, lazy, GPU/HF paths. Verified against the C2
   schema + the 38 tests before choosing this.

2. **Version tag and stage behavior derive from ONE resolver (`diarize._resolve`).** This
   is the invariant that keeps `record_id` safe. `pipeline_version = asr_base +
   diarize.version_tag(cfg)`; the `_diarize` stage's active/no-op decision and the tag both
   come from `_resolve`, and **any unrecognized `DIARIZE_BACKEND` value resolves to `off`
   in both**. If they could diverge (stage fills speakers but the tag stayed `''`), a
   diarized record would be written under `asr-mock-v0` and mint the SAME `record_id` as
   the pristine primary → a silent overwrite via the idempotent `/context` upsert. (Caught
   in design review; guarded + tested in `test_unknown_backend_resolves_off_everywhere`.)

3. **Version-fork semantics.** Diarization **mutates** the primary transcript record, so
   activating it version-forks it: `asr-mock-v0` → `asr-mock-v0+diar-mock-v1` (or
   `+diar-pyannote-v1`), a version-forward fork per C2. Translation + acoustic are
   **additive sidecar records** (new `discriminator`), so they never touch the primary and
   never tag `pipeline_version`. Because `build_c2` stamps ONE `pipeline_version` per chunk,
   when diarization is also active its tag forks the primary **and** the sidecars together
   — intended: one run, one dialect. Confirmed collision-free + idempotent.

4. **C2 is untouched — additive only.** `segments[].speaker` is already `["string","null"]`
   and `enrichments.speakers` items are `{}` (any) in the frozen schema, and `caption` is a
   frozen `content.kind`. So filling speakers and adding translation/acoustic records needs
   **no schema change, no ARCHITECTURE edit, no PIPELINE_VERSION bump on the mock dialect**.
   Every emitted unit is validated by the existing `main.py` gate (`validate_c2` +
   `C2Record`).

5. **Real backends are correct-by-inspection SEAMS, not verified runs.** pyannote (GPU +
   two HF-gated repos), whisper `task=translate` (model download), and AST (transformers +
   ~350 MB model) can't run in this environment. Each carries a prominent "⚠️ UNVERIFIED ON
   REAL AUDIO" banner and is lazy-imported only when selected. **No real run is claimed.**
   Smoke-test each on node-7 before trusting it (a real captured chunk + the backend flag).

## New env vars (all read via `os.getenv` in `app/audio/config.py` — `config.py` untouched)

| Var | Default | Meaning |
|---|---|---|
| `DIARIZE_BACKEND` | `off` | `off` \| `mock` \| `pyannote` |
| `DIARIZE_SPEAKERS` | `2` | mock: synthetic speaker count |
| `DIARIZE_MIN_SPEAKERS` / `DIARIZE_MAX_SPEAKERS` | `0` (unset) | pyannote hints |
| `HF_TOKEN` / `HUGGINGFACE_TOKEN` | — | HF auth for the gated pyannote model |
| `TRANSLATE_BACKEND` | `off` | `off` \| `mock` \| `whisper` |
| `TRANSLATE_TARGET` | `''` (off) | BCP-47 target; whisper supports `en` only (X→English) |
| `ACOUSTIC_BACKEND` | `off` | `off` \| `mock` \| `ast` |
| `ACOUSTIC_TOP_K` | `3` | tags folded into the caption |
| `ACOUSTIC_THRESHOLD` | `0.1` | min per-tag score (real backend) |

`learn.env.example` (platform-owned, not ours to edit) is unchanged; document these there
when platform next touches it.

## Notes / gotchas for the next session

- **Whisper translate is English-only.** `TRANSLATE_BACKEND=whisper` + a non-`en` target is
  a misconfig: it's logged and **degrades to off** (no translation record) rather than
  500-ing the ingest (which would drop the primary transcript too) or emitting an
  English record mislabeled with the wrong language. Non-English targets need a real MT
  backend — a future `translate/` module.
- **Diarization on a silent chunk** yields no speakers (no ASR segments to assign) but still
  stamps the `+diar-*` dialect — consistent.
- **Verifying diarization end-to-end live:** `main.py`'s dedup fast-path is keyed on
  `chunk_id` and short-circuits BEFORE `pipeline_version` is computed, so re-POSTing an
  already-processed `chunk_id` after flipping `DIARIZE_BACKEND` returns the cached old ids
  and does NOT reprocess/fork (pre-existing, same as the ASR mock→fw switch). Use a NEW
  `chunk_id` or restart the service when smoke-testing a backend change.
- **AST decode routes through ffmpeg:** the AST backend hands the transformers pipeline the
  RAW chunk bytes, whose `ffmpeg_read` path demuxes webm/opus + m4a/aac + wav uniformly
  (an earlier soundfile-based draft would have crashed on the real webm/mp4 capture codecs —
  caught in review, fixed). ffmpeg on PATH is the one system dep.
- Sibling-collapse of near-duplicate acoustic tags ("cutlery" + "dishes" → "dishes
  clinking") is a documented future refinement, intentionally omitted for a lean v0.

## Tests (all headless, no GPU / heavy deps)

`tests/test_audio_diarization.py`, `tests/test_audio_translation.py`,
`tests/test_audio_acoustic.py` (NEW; use `make_c1` + the existing fake-storage TestClient).
Cover: default-off byte-identity; unknown-backend → off in both tag + stage; mock
diarization fill + version fork + `enrichments.speakers` aggregation; `assign_speakers`
(max-overlap / tie-break / no-overlap / empty-turns); translation sidecar (schema-valid,
distinct id, primary unchanged, whisper+non-en degrade); acoustic caption sidecar + the
caption builder (speech drop / threshold / fallback); and all-three-stages composition into
3 forked, distinct, schema-valid records. **DP suite: 57 passed (38 + 19).** Real backends
are unrun by design (documented seams).

## Worklog

- 2026-07-19 — Built the beyond-ASR audio pipeline: `app/audio/` namespace (config + diarize
  / translate / acoustic sub-packages, each mock + real backend behind an off-by-default
  switch), filled the three `audio.py` stages, added `_absolute_segments` (shared asr/
  translate absolute-time mapping, byte-identical to the old inline loop) and the diarization
  version tag, added `load_model()` to `asr/faster_whisper.py` (translation reuses the ASR
  model), and `requirements-audio.txt`. Design + real-backend APIs pre-verified by an
  adversarial workflow (pyannote 3.1 / whisper translate / AST); the single-resolver id-safety
  fix came from that review. 57 tests green; default path proven byte-identical + heavy-dep-free
  on import; a headless all-stages-on E2E through `create_app()`/`/ingest` emitted 3 schema-valid
  C2s (diarized primary + translation + acoustic).
