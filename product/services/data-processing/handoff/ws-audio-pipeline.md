# WS — DP audio pipeline beyond ASR: diarization · translation · acoustic events

> The AUDIO-modality lead's fill of the three staged stubs in
> [`app/processing/processors/audio.py`](../app/processing/processors/audio.py) —
> `diarize → translate → acoustic_events` — with real backends behind off-by-default
> switches, under a NEW `app/audio/` namespace. Read [CHARTER.md](../../CHARTER.md)
> §Scope (audio pipeline) + OQ11/OQ12 and [HANDOFF.md](../HANDOFF.md) (Processor seam +
> Current state) first; this is the volatile record for the beyond-ASR audio work.

**Status:** built + tested headless; **all three real backends now SMOKE-TESTED GREEN on
node-7 (2026-07-19)** — pyannote diarization + whisper-translate + AST acoustic each ran
end-to-end on a real webm/opus speech chunk; the smoke found + fixed **two real pyannote
torch-2.x compat bugs** (see the 2026-07-19 async-observability entry below) ·
**Owner session:** audio-pipeline lead → node-7 smoke by async-observability session ·
**Last updated:** 2026-07-19

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
- 2026-07-19 — **independent verification round** (recording/DP integrator session): the
  headline claims HELD under adversarial checking — default-off path proven byte-identical
  by sha256 against a 0bb66e6 worktree; single-resolver invariant confirmed (no second
  decision site; unknown backend → off in both tag and stage); assign_speakers boundary
  probes clean; real-backend API shapes check out by inspection (pyannote 3.1.1 pin,
  faster-whisper kwargs, AST ffmpeg decode path — real runs remain the node-7 smoke test,
  as this file already states). Two documented caveats, accepted not fixed: sidecar
  (translation/acoustic) record_ids do not encode backend/target config — reprocessing
  under a CHANGED config upserts over the prior sidecar (same posture as ASR_LANGUAGE:
  config knobs are fleet-stable, not per-record dialects); and with the beta
  `ASR_LANGUAGE=en` pin, whisper translation's detected==target skip makes it a no-op on
  English-pinned fleets (translation presumes language auto-detect — enable them together).
- 2026-07-19 — **NODE-7 SMOKE TEST of the three real backends (async-observability session).**
  Env: node-7 (8× H100, all idle), conda `moe` (torch **2.8.0+cu128**, torchaudio 2.8.0,
  faster-whisper 1.1.0, transformers 4.57.6, **pyannote.audio 3.3.2** — newer than the
  `==3.1.1` pin in requirements-audio.txt; the `speaker-diarization-3.1` pipeline still loads),
  ffmpeg on PATH, `HF_TOKEN` set (gated repos accepted). Input: real **JFK speech** chunk
  (`sample_jfk.webm`, 11 s, **opus/webm** — the extension's capture codec, so the real ffmpeg
  demux path is exercised). Harness: `scripts/smoke_audio_backends.py` drives the ACTUAL
  `app/audio` backend code (not a reimplementation). **Results — ALL FOUR GREEN in one run:**
  - **ASR** (faster_whisper): PASS 10.8 s — correct transcript, lang=en, 2 segments.
  - **Diarization** (pyannote/speaker-diarization-3.1): PASS 24.0 s — 5 turns, 1 speaker
    (`spk_0`, correct for a single-speaker clip); `assign_speakers` filled `enrichments.speakers`.
  - **Translation** (whisper `task=translate`): PASS 1.0 s — English output (JFK is already
    English, so X→En translate is ~identity; this proves the SEAM runs: decode → model →
    segment lift, not translation quality on a non-English source).
  - **Acoustic** (MIT/ast-finetuned-audioset): PASS 2.9 s — caption `"Ambient background
    noise."` (AST filters the speech-class tags → fallback caption; the pipeline decodes real
    webm/opus via `ffmpeg_read` and produces a caption end-to-end).
  - **Two real pyannote bugs found + FIXED** (`app/audio/diarize/pyannote.py`), each of a class
    inspection could not catch (a torch-version default change; a torchaudio backend gap):
    (1) **`weights_only` UnpicklingError** — torch ≥ 2.6 flipped `torch.load`'s default to
    `weights_only=True`, which rejects pyannote's Lightning-checkpoint globals
    (`torch.torch_version.TorchVersion`) → `Pipeline.from_pretrained` raised. Fixed with a
    scoped `torch.load` shim (`weights_only=False` for the trusted gated load only, restored in
    `finally` — never a global monkeypatch).
    (2) **webm/opus `Format not recognised`** — torchaudio's default soundfile/libsndfile
    backend can't demux compressed capture containers, so pyannote choked on the raw chunk.
    Fixed by pre-decoding to 16 kHz mono WAV via ffmpeg (the exact decoder the ASR/AST paths
    use) before handing it to pyannote. Both fixes only touch the real `pyannote` path (the
    mock/off default never imports it), so the headless suite is unaffected.
  - **Caveats / still-open:** (a) whisper-translate on a genuine **non-English** source is
    still unproven (no non-English clip on the box — the English-identity run only proves the
    plumbing); pair it with an X-language chunk when one exists. (b) The env runs pyannote
    **3.3.2**, not the pinned **3.1.1** — both fixes are torch-version issues (not pyannote
    version), so they apply to 3.1.1 too, but pin-exact verification is a follow-up. (c) The
    `requirements-audio.txt` `torch>=2.1,<3` range now spans the 2.6 `weights_only` change —
    the shim makes the seam torch-version-robust across that range.
