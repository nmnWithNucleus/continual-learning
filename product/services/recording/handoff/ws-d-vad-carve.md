# WS-D — Variable-length VAD-cut chunking (charter OQ4, pinned)

> Recording-led capture M1, priority 4. Pins OQ4 jointly with data-processing and implements
> the server-carve half. Founders' lean (2026-07-18) adopted: variable-length chunks cut at
> speech pauses within bounds. C1 is untouched (frozen shape already supports it: per-chunk
> `t_start`/`t_end`; `sequence` density is length-independent).

**Status:** built + verified (unit + live on real speech) · **Owner session:** recording M1 lead

---

## Decision D-M1-2 — OQ4 pinned (recording × data-processing)

Chunking is decided **per client/source**, by who owns a continuous feed:

| Source | Chunker | Why |
|---|---|---|
| Continuous audio the server owns (M0 WAV source today; bodycam device link later) | **Server carve, VAD-cut variable length**: cut at detected speech pauses, chunk duration within **[5 s, 30 s]**; hard-cut at 30 s when no pause offers | Semantic cuts avoid mid-word/mid-utterance splits (better ASR per chunk, no overlap needed); exact `t_end[n] == t_start[n+1]` adjacency holds → a second continuity signal on top of dense `sequence` |
| Phone web client (WS-B) | **Edge chunking, fixed ~10 s segments** (recorder restart) | MediaRecorder fragments aren't self-contained; self-contained segments are the durable upload/offline-queue unit; VAD in client JS = complexity + battery for little gain; restart gaps would corrupt a server-side re-carve's time spine |
| Video / screen streams | **Fixed windows** | Founders' note: fine as-is; VAD is meaningless for video |

The M0 5 s fixed placeholder is retired as the default: `chunk_seconds` omitted → VAD carve;
`chunk_seconds` set → fixed carve (compatibility + tests + video-like uses). This supersedes
the earlier "~20–30 s + overlap" recommendation — pause-aligned cuts make overlap unnecessary
for ASR continuity (DP's cross-chunk stitching can still land later; nothing forecloses it).
DP pairing: DP gains a **VAD gate before ASR** (their ws file) — same lens, different leg.

## Implementation (recording side)

- `app/carve.py` (new) — energy-based VAD boundary finder over decoded PCM (s16le mono):
  windowed RMS (~30 ms hop) → a silence threshold calibrated per stream (noise floor +
  margin, e.g. `max(200, p20(rms) * 2.5)` — deterministic, no deps) → speech-pause runs
  (≥ ~300 ms) → cut points: earliest pause after `min_s=5`, else hard cut at `max_s=30`.
  Pure function `find_cuts(pcm: bytes, sample_rate: int, *, min_s, max_s) -> list[int]`
  (frame offsets), unit-testable on synthetic tone/silence patterns.
- `app/sources/wav_source.py` (edit) — `chunk_seconds=None` → carve via `find_cuts` (bounds
  from env `VAD_MIN_CHUNK_SECONDS`/`VAD_MAX_CHUNK_SECONDS`, defaults 5/30, read in the
  builder); explicit `chunk_seconds` → existing fixed `wav.carve`. Chunks stay standalone
  WAVs with exact-adjacent wall-clock spans.
- Tests: cuts land inside pauses (synthetic: tone-silence-tone patterns); bounds respected
  (no chunk < min unless final remainder, none > max); pure-silence and pure-tone inputs
  degrade to max_s fixed cuts; adjacency `t_end[n] == t_start[n+1]` holds through the source;
  fixed-mode regression (M0 behaviour byte-identical when `chunk_seconds` is set).

## Worklog
- 2026-07-18 — OQ4 pinned as above (joint with DP, recorded in both canvases); handed to the
  build fan-out.
- 2026-07-18 — built: `app/carve.py` (`find_cuts` windowed-RMS energy VAD, threshold
  `max(200, 2.5·p20)`, pause = ≥300 ms below-threshold run, cut at pause midpoint, walk
  honors [min_s, max_s] with exact `max_s` hard cuts; `carve_at` slices standalone WAVs
  with frame-accurate adjacency) + `wav_source.py` mode resolution (explicit
  `chunk_seconds` > `CHUNK_SECONDS` env > VAD default with `VAD_MIN/MAX_CHUNK_SECONDS`
  bounds). 16 new tests; fixed path byte-identical (M0 regression held).
- 2026-07-18 — **verified live**: `/capture/run` with `chunk_seconds` omitted on an 11 s
  real-speech WAV (JFK sample) → 2 chunks cut at 7.86 s inside the natural pause,
  `t_end[0] == t_start[1]` exact to the microsecond in the stored C2 spans. Degenerate
  synthetic tone (the M0 sample) degrades to max_s cuts as documented. Known limit
  (documented in carve.py): the p20 calibration wants silence in ≥~20 % of hops; noisy
  always-loud streams degrade to fixed max_s cuts — a real VAD model swaps in behind
  `find_cuts` later.
