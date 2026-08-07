# Model-server venv repair — 2026-08-07

> **Done.** Three corrupted model-server virtual environments rebuilt from their pinned
> requirements and swapped in with zero downtime. No service was restarted; DP `:8085` kept
> its original process throughout.

---

## What happened

An automated text transform, run during a documentation cleanup on 2026-08-07, was scoped
widely enough to reach **gitignored directories**. Inside `.py` files it collapsed leading
indentation to a single space and ate `()` off identifiers, producing syntactically invalid
Python. It reached three model-server venvs:

```
 format = "BMP"
 # -------------------------------------------------- BMP Compression values
 COMPRESSIONS = {"RAW": 0, "RLE8": 1, ...}
 for k, v in COMPRESSIONS.items:
 vars[k] = v
```

The venvs are gitignored, so `git status` was clean, every diff was clean, and all five test
suites stayed green — none of them read those directories. The damage was invisible to every
verification the repo had.

**Why it was urgent.** Ports 8131/8132 (pyannote), 8141/8142 (ast) and 8151/8152 (ocr)
answered `200` only because those processes booted at 07:46:04, before the corruption, and
held their code in memory. The supervisor respawns dead replicas; a respawn would have failed
at import and then retry-failed forever on a 30-second ceiling. Any crash, OOM or restart
would have taken three of the four model kinds down and kept them down.

**Timing evidence.** Corrupted files are stamped minutes before the sweep commit; an
untouched venv still carries its install date.

```
corrupted: 2026-08-07 17:12:48  servers/ocr/.venv/.../PIL/BmpImagePlugin.py
corrupted: 2026-08-07 17:13:09  servers/pyannote/.venv/.../PIL/BmpImagePlugin.py
sweep commit b6843a6:  2026-08-07 17:31:48
intact whisper venv:   2026-08-06 04:00:10  (install time, untouched)
```

---

## Step 1 — Blast-radius scan

Every venv in the repository was parsed with `ast.parse`. A file that fails to parse is a file
that breaks on import.

| venv | `.py` scanned | syntax-invalid | verdict |
|---|---|---|---|
| `data-processing/servers/ast/.venv` | 9451 | **7421** | corrupt |
| `data-processing/servers/pyannote/.venv` | 13918 | **1168** | corrupt |
| `data-processing/servers/ocr/.venv` | 2326 | **1809** | corrupt |
| `data-processing/servers/whisper/.venv` | 2751 | 0 | clean (control) |
| `data-processing/servers/common/.venv` | 1244 | 0 | clean |
| `data-processing/.venv` | 16262 | 0 | clean |
| `platform/deploy/.venv` | 1390 | 0 | clean |
| `platform/deploy/.venv-learn` (fleet launcher) | 2884 | 0 | clean |
| `platform/deploy/.venv-vllm` | 19857 | 0 | clean |
| `storage/.venv` | 1297 | 0 | clean |
| `recording/.venv` | 1297 | 0 | clean |
| `continuum/.venv` | 987 | 0 | clean |
| `continuum/.venv-train` | 945 | 0 | clean |

Exactly the three named venvs were affected; the other ten are clean.

**Note on counts.** The task brief counted files matching a narrower signature (`def name ->`
plus eaten method parens): 557 / 172 / 112. The counts above are every file that fails to
parse, which is the operationally relevant number and is roughly ten times larger. The
affected *set* is identical either way.

### Preserving ground truth

`pip freeze` could not be used on the damaged venvs — **pip itself was corrupted** in all
three (`python -m pip --version` failed). The `*.dist-info` metadata was intact, so the
installed set was read with `importlib.metadata` from a clean external interpreter, without
importing any package.

That method was validated against the clean whisper venv, where real `pip freeze` works. The
two were identical except for entries `pip freeze` renders differently by design:

- `pip` itself — excluded from `pip freeze` output by default.
- `dp-servers-common` — an **editable** install of `servers/common`, which `pip freeze`
  renders as `-e git+ssh://…#egg=dp_servers_common&subdirectory=…`.

| venv | packages preserved |
|---|---|
| ast | 61 |
| ocr | 35 |
| pyannote | 118 |
| whisper (control) | 41 |

---

## Step 2 — Rebuild beside, and prove equivalence

Each server was rebuilt into `<server>/.venv-rebuild` with the same base interpreter every
venv records in its `pyvenv.cfg` (`/home/ubuntu/miniconda3/envs/moe/bin/python3.12`,
Python 3.12.12), from its own `requirements.txt`, honouring the `--extra-index-url
https://download.pytorch.org/whl/cu128` directive that ast and pyannote specify.

**No `requirements.txt` was modified.**

### 2a. Freeze equality — and one real substitution, caught

The first ast build from `requirements.txt` alone produced a genuine substitution:

```
-huggingface-hub==1.26.0
+huggingface-hub==1.27.0
```

`huggingface-hub` is an unpinned transitive dependency of `transformers`, so a fresh resolve
today picked a newer release than the one installed. **This is exactly the drift the
"any substitution = stop" rule exists to catch.**

Remedy, without touching `requirements.txt`: the preserved freeze was supplied to pip as a
**constraints file** (`-c`), which forces the resolver to reproduce the recorded environment.
Both GPU servers were rebuilt that way.

Final state — live venvs against preserved ground truth:

| server | packages | differing lines |
|---|---|---|
| ocr | 35 | **0** |
| ast | 61 | 1 (`torch` label, below) |
| pyannote | 118 | 2 (`torch`, `torchaudio` labels, below) |

### The torch label — not a substitution

`torch` records as `2.8.0` in the preserved metadata and `2.8.0+cu128` in the rebuild. This is
a metadata label difference only. The artifacts are the same build:

| | preserved | rebuild |
|---|---|---|
| dist-info directory | `torch-2.8.0.dist-info` | `torch-2.8.0+cu128.dist-info` |
| `torch/version.py` `__version__` | `2.8.0+cu128` | `2.8.0+cu128` |
| `torch.version.cuda` | `12.8` | `12.8` |
| `torch/lib` inventory | identical (names + byte sizes) | identical |
| `sha256 libtorch_cuda.so` | `e371cd8f07cb7ecb…` | `e371cd8f07cb7ecb…` |
| `sha256 libtorch_cpu.so` | `75540e9ff02ef34b…` | `75540e9ff02ef34b…` |
| `sha256 libtorch_cuda_linalg.so` | `8db2767d5bc9813b…` | `8db2767d5bc9813b…` |

`torchaudio` matches the same way (`libctc_prefix_decoder.so` → `b4409b45f34be2c1…` both
sides). The 14 `nvidia-*` CUDA wheels are identical in both.

This matters because `/health` reports `torch.__version__`, not the dist-info version — so the
identity string the model client verifies is unchanged. The label differs only because the
original install resolved a wheel whose recorded version was normalised without the local
`+cu128` segment.

### 2b. Corruption scan of the rebuilds

| venv | `.py` scanned | syntax-invalid |
|---|---|---|
| `ast/.venv-rebuild` | 9451 | **0** |
| `ocr/.venv-rebuild` | 2326 | **0** |
| `pyannote/.venv-rebuild` | 13918 | **0** |

The `.py` counts match the originals file-for-file, which is independent evidence that the
package set is equivalent.

### 2c. Each server's own suite, on the rebuilt venv

GPU headroom was checked first (`nvidia-smi`): GPUs 0–1 held vLLM at ~70 GB each, GPUs 2–7 had
roughly 77 GB free apiece, so the suites were run one server at a time out of caution rather
than necessity.

```
########## ocr — 8 passed, 1 warning in 6.80s
########## ast — 6 passed, 2 warnings in 47.33s
########## pyannote — 6 passed, 12 warnings in 57.49s
```

The golden tests are exact compares, which is the real proof of output equivalence:

- ocr — `test_golden_smoke_screen_planning_notes`, `test_health_identity_matches_manifest_and_shape`
- ast — `test_golden_smoke_speech_clip`, `test_golden_smoke_real_dialog`, `test_health_identity_matches_manifest`
- pyannote — `test_golden_smoke_two_speakers`, `test_golden_smoke_real_dialog`, `test_health_identity_matches_manifest`

---

## Step 3 — Swap and heal, one replica at a time

The supervisor is **not** a separate process: it runs inside the DP service, pid `632026` on
`:8085`, which is the parent of all eight replicas. Killing a replica therefore exercises DP's
own respawn path and required no DP restart. Every kill re-confirmed the pid→port mapping
immediately beforehand and refused to proceed if the target was `632026`.

The supervisor resolves `.venv/bin/python` relative to the server directory at spawn time, so
renaming the directory is sufficient — the respawn picks up the rebuilt environment.

### Swap log

| Server | Swap (UTC) | Port | Old pid | Killed (UTC) | New pid | Healthy after | Peer during |
|---|---|---|---|---|---|---|---|
| ocr | 20:20:07 | 8151 | 632080 | 20:20:18 | 1202901 | ~1 s | `:8152` 200 |
| ocr | — | 8152 | 632083 | 20:21:25 | 1204009 | ~10 s | `:8151` 200 |
| ast | 20:22:20 | 8141 | 632076 | 20:22:30 | 1205027 | ~34 s | `:8142` 200 |
| ast | — | 8142 | 632078 | 20:23:28 | 1205625 | ~22 s | `:8141` 200 |
| pyannote | 20:24:00 | 8131 | 632072 | 20:24:14 | 1206288 | ~26 s | `:8132` 200 |
| pyannote | — | 8132 | 632074 | 20:24:57 | 1207063 | ~20 s | `:8131` 200 |

The peer replica stayed `200` through every kill, so the fleet never lost a model kind.

### Per-replica verification

Each fresh replica was checked for the venv it actually runs from, then its `/health` identity
was matched against `servers/manifest.json` `expected_identity` field by field, using the same
subset-match rule the model client applies. All eight replicas match, including the two
whisper replicas that were never touched.

The strongest single check: the rebuilt `:8151` identity was diffed against its peer `:8152`,
which was still running the pre-corruption in-memory code — **byte-identical**.

Golden exact-compare was then run through each live replica over HTTP (not in-process):

```
  ocr :8151 golden exact-compare: BYTE-IDENTICAL  (7 regions)
  ocr :8152 golden exact-compare: BYTE-IDENTICAL  (7 regions)
  ast :8141 golden exact-compare: BYTE-IDENTICAL  (20 tags, top='Speech' 0.6633811593055725)
  ast :8142 golden exact-compare: BYTE-IDENTICAL  (20 tags, top='Speech' 0.6633811593055725)
  pyannote :8131 golden exact-compare: BYTE-IDENTICAL  (2 turns)
  pyannote :8132 golden exact-compare: BYTE-IDENTICAL  (2 turns)
```

Float scores agree to full precision, which is what byte-identical goldens are supposed to
demonstrate.

---

## Step 4 — Final state

### All twelve ports (2026-08-07T20:25:26Z)

```
  8083 200   8084 200   8085 200   8121 200
  8122 200   8131 200   8132 200   8141 200
  8142 200   8151 200   8152 200   8161 200
```

### Identity vs manifest

```
  whisper :8121  MATCHES      ast :8141  MATCHES
  whisper :8122  MATCHES      ast :8142  MATCHES
  pyannote :8131 MATCHES      ocr :8151  MATCHES
  pyannote :8132 MATCHES      ocr :8152  MATCHES
```

### DP dialects unchanged — the clipcap flip did NOT happen

```json
{
    "ok": true,
    "ingest_mode": "async",
    "pipeline_versions": {
        "audio": "acoustic.v1-ast.v1+asr.v1-fw.v1+diarize.v1-pyannote.v1+speaker_align.v1-builtin.v1",
        "video": "clipcap.v1-vlm.v1+clipprep.v1-ffmpeg.v1+screentext.v1-ppocr.v1"
    },
    "supervisor": true
}
```

`clipcap.v1-vlm.v1` — still `v1`. DP is the same process it was before this work:

```
    PID                  STARTED     ELAPSED
 632026 Fri Aug  7 07:46:02 2026    12:39:33
```

DP's own counters show no damage: `dp_server_identity_failures_total{server="whisper"} 0`,
and `dp_server_calls_total{…,outcome="identity_mismatch"} 0` for all four servers, with zero
transient retries. Those call counts (637/637/637/327) predate the swap — no chunk arrived
during the maintenance window, so DP has not yet re-verified against the new replicas. The
direct `/health` checks above applied the client's own subset-match rule, so the next call
will pass.

### GPU map unchanged

Pinning is exactly as before — pyannote on GPUs 2–3, whisper 4–5, ast 6–7, ocr CPU-only,
vLLM 0–1 untouched at ~70 GB each. Reported usage on the rebuilt replicas is lower than
before (pyannote 2497/2625 MiB → 950 MiB, ast 1267 MiB → 1138 MiB) because the processes are
freshly booted and have not accumulated caching-allocator blocks from twelve hours of serving.

### Cleanup

`.venv-corrupt-20260807` was deleted for all three servers after both of that server's
replicas were green, along with one `.venv-discard-*` left by an interrupted build. Final
layout:

```
ast/.venv   common/.venv   ocr/.venv   pyannote/.venv   whisper/.venv
```

Final corruption scan of every server venv: **0 syntax-invalid files** in all five.

---

## Notes for whoever reads this next

**An interrupted rebuild was thrown away rather than shipped.** `rm -rf` on this NFS mount
partially failed with *Directory not empty*, so one ast build ran on top of a non-empty
directory. No `.nfs*` remnants or duplicate `dist-info` were found, but stale untracked files
could not be ruled out, so that build was discarded and redone from a genuinely empty path.
An environment you cannot vouch for should not be swapped into a live fleet.

**pip was pinned to `25.0.1`,** matching what all three venvs carried. An initial
`pip install --upgrade pip` produced the only difference in the first ocr comparison; it was
reverted so the environments match on every package including pip.

## What this teaches

**Automated transforms must never be run with a scope that can reach gitignored
directories.** A sweep aimed at comments in source files reached three virtual environments
and rewrote ten thousand third-party files. Scope every transform to tracked files —
`git ls-files` — rather than to a directory walk.

**Verification that only reads git is blind to this class of damage.** Clean `git status`,
clean diffs and five green suites all held while three of four model kinds were one respawn
from a permanent outage. Health checks passed too, because the processes already had their
code in memory: liveness said nothing about restartability. A fleet whose environments are
gitignored needs a check that parses what is on disk, not one that asks git what changed.
