# `tests/fixtures/chunksets/` — committed eval corpora (WS-H)

A **chunkset** is what `scripts/prompt_ab.py` scores an arm over: C1 envelopes, optional
blobs, and optional ground truth, indexed by one `manifest.json`. Built by
`scripts/capture_chunkset.py`; read by `capture_chunkset.load_chunkset()` (one reader, one
on-disk contract, shared with `scripts/oracle_gemini.py`).

```
<chunkset>/
  manifest.json          # the index; `truth` is INLINE per chunk
  c1/<chunk_id>.json     # one contract-valid C1 envelope per chunk
  blobs/<chunk_id>.mp4   # ABSENT here — house rule 5 commits no binaries
```

## What is committed

| chunkset | mode | chunks | blobs | what it is for |
|---|---|---|---|---|
| `smoke-v1` | `headless` | 12 | none | the pre-push smoke corpus — the plumbing gates, headless and offline |

`smoke-v1` cycles the six labelled synthetic screens in `capture_chunkset.SCREENS` (IDE,
Gmail, Slack, terminal, browser article, spreadsheet), each carrying `truth.app`,
`truth.activity`, `truth.entities` and `truth.ocr_regions`.

## What `headless` can and cannot prove

**Can:** the two-record set (D-05), byte-identical spans, `record_id` recomputation, the
arm fork, the day-log projection through continuum's `build_daylog`, every scorer's
plumbing, and the four `--check` gates.

**Cannot:** anything about pixels. With no blob the harness feeds an undecodable
placeholder, `clipprep` takes its documented synthetic-frames fallback, and that fallback
returns `ocr_times=()` — so **the OCR channel is dark** and `ocr_text` is `""`. Frame
selection, delta classification and the whole injection question are therefore untouched by
this corpus. The report says so in an advisory.

For the O-8 gate, O-4, or any grounding number, build a corpus with pixels:

```bash
# labelled synthetic screens, exact ground truth, no capture and no binaries in git
python scripts/capture_chunkset.py synth --out /tmp/cs --count 200 --span 10

# a real screen recording, cut by stream copy (then label `truth` before gating on it)
python scripts/capture_chunkset.py slice --out /tmp/cs-real --from-video screen.mp4 --span 60
```

## Adding a committed chunkset

Keep it JSON-only, keep it small, and regenerate it with `capture_chunkset.py` rather than
hand-editing — the ground truth is derived from the same `Screen` definitions the filter
chain draws from, and `tests/test_eval_scorers.py` asserts that the two cannot drift apart.
