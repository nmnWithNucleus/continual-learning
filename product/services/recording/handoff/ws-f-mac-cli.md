# WS-F — Mac capture client, basic CLI (`clients/mac/`)

> Computer capture surfaces slice, priority 2 (founders 2026-07-18 follow-up). A **CLI
> capture agent, not a GUI app**: ffmpeg avfoundation (screen + mic) cut into ~10 s
> self-contained segments + a small uploader speaking the exact `POST /capture/segments`
> wire ([ws-b](ws-b-phone-web-client.md) §Wire / [ws-c](ws-c-ingest-demux-ledger.md)).
> **Zero server changes.** A menu-bar/GUI app (ScreenCaptureKit, visible capture
> indicator, autostart) is an explicitly **LATER surface** — capability today, UX later.

**Status:** built + unit-tested + **live-E2E-verified on this box in `--source test` mode**
(the avfoundation capture leg itself needs a human mac — §Runbook) · **Owner session:**
recording computer-capture lead

---

## Decisions

- **D-F1 — one Python file, stdlib only.** `clients/mac/nucleus_capture.py` (python3 ≥3.9,
  no pip deps — macOS's CLT python or any brew python runs it; ffmpeg via
  `brew install ffmpeg`). Two subcommands: `record` and `list-devices`. Architecture:
  ffmpeg is the capture+segmenter (`-f segment`, ~10 s self-contained mp4s into a spool
  dir); a Python uploader thread watches the spool and speaks the wire. Muxed A/V per
  segment **exactly like the phone client** — the server demuxes into two C1 streams (own
  `stream_id` each, same `device_id`). Why not Swift/ScreenCaptureKit now: that is the
  later GUI surface; this CLI proves the capture capability and the wire with zero new
  server or build machinery.
- **D-F2 — wall-clock stamps are duration-chained.** Anchor = the wall-clock when capture
  actually starts (`st_birthtime` of segment 0 where the OS provides it — macOS does —
  else the ffmpeg spawn time). Then `t_start[0] = anchor`,
  `t_end[n] = t_start[n] + duration[n]` (ffprobe per segment), `t_start[n+1] = t_end[n]`.
  Segment cuts land on forced keyframes so durations are ~exactly SEGMENT_SECONDS but can
  vary slightly; chaining keeps the time axis continuous and honest — capture IS
  continuous, unlike the phone's restart-gap segments. Known v0 approximation, stated:
  the anchor can sit up to ~1–2 s late/early of true first-frame time (device-open and
  birthtime granularity); second-level alignment is the beta bar.
- **D-F3 — `--source test` is a first-class mode.** lavfi `testsrc2` + `sine` through the
  SAME encode/segment/upload path (only the ffmpeg input differs). It is (a) how this
  headless Linux box verifies everything but avfoundation E2E against the live fleet,
  (b) the conformance-test driver, and (c) a mac user's no-permissions smoke test.
  On non-darwin, `record` without `--source test` refuses with a clear message —
  avfoundation exists only on macOS; nothing is faked.
- **D-F4 — segment completeness = next-file-appears.** ffmpeg's segment muxer opens
  `seg-%06d.mp4` n+1 only after finalizing n, so a segment is uploadable when a
  higher-numbered file exists; on ffmpeg exit, all remaining files are final. No inotify,
  no size-polling heuristics. Upload strictly in filename (= seq) order, serialized —
  ws-b's queue semantics: retry forever on network/5xx (backoff 1 s·2ⁿ, cap 30 s), 4xx
  surfaced + counted + dropped, sha256 always computed (stdlib hashlib). Segment files
  are deleted after ack (`--keep-segments` keeps them).
- **Stop semantics:** first Ctrl-C → graceful: `q` to ffmpeg's stdin (clean mp4 close;
  SIGINT fallback), finalize + upload the tail, `POST /capture/sessions/{id}/end
  {last_seq}`, then poll the gap report until the verdict is terminal AND
  `segment_states.received == 0` (drained), print the report summary. Exit code: 0
  `clean`, 2 `gaps`, 1 error. Second Ctrl-C → abandon politely: print the session id, the
  spool path, and the fact that the ledger will flag the session unterminated. Bounded
  runs via `--duration N` (mainly test mode) end the same graceful way.
- **Identity:** `session_id` ULID-ish minted per run (same alphabet as the phone client);
  `--user` default `beta-user`; `device_id` = `mac-cli-<suffix>`, suffix persisted at
  `~/.nucleus/device_id` (0600, dir 0700).

## ffmpeg shapes (pinned; flags documented in --help too)

- **mac (default):** `-f avfoundation -capture_cursor 1 -framerate <15> -i
  "<screen-idx>:<audio-idx>"` → `-vf scale='min(1728,iw)':-2` (retina downscale: raw
  avfoundation is pixel-resolution, 2× on retina — halve to keep bitrate sane) →
  `-c:v libx264 -preset veryfast -crf 28 -pix_fmt yuv420p` (avfoundation delivers bgra;
  yuv420p required for players/demux) `-c:a aac -b:a 128k` →
  `-f segment -segment_time 10 -reset_timestamps 1 -force_key_frames
  "expr:gte(t,n_forced*10)" -segment_format mp4 -segment_format_options
  movflags=+faststart` → `spool/seg-%06d.mp4`. Device indices via `list-devices`
  (wraps `ffmpeg -f avfoundation -list_devices true -i ""`); defaults
  `--screen-index 1 --audio-index 0`, the common laptop layout — **verify with
  list-devices first**, indices shift with cameras/mics attached.
- **test:** `-f lavfi -i testsrc2=size=640x360:rate=15 -f lavfi -i
  sine=frequency=440:sample_rate=44100` + optional `-t <duration>`, then the same encode
  (mpeg4 fallback when libx264 is absent, e.g. conda ffmpeg) + segment flags.

## macOS one-time setup (the permission grant)

1. `brew install ffmpeg`.
2. **Screen Recording permission** goes to the app that *launches* ffmpeg — the terminal:
   System Settings → Privacy & Security → **Screen Recording** → enable your terminal
   (Terminal.app / iTerm2 / VS Code). macOS prompts on the first capture attempt; after
   granting you must **quit and reopen the terminal** for it to stick. Without it,
   avfoundation returns a black screen or `Operation not permitted`.
3. **Microphone permission** prompts inline on first run (same panel → Microphone if it
   was ever denied).
4. If ffmpeg errors `Selected framerate (...) is not supported`, retry with
   `--framerate 30` (screen devices advertise fixed rate sets).

## Runbook (human mac leg)

```bash
cd product/services/recording/clients/mac
python3 nucleus_capture.py list-devices                  # find screen/audio indices
python3 nucleus_capture.py record \
  --server https://<tunnel-from-var/tunnel_url.txt>      # or http://localhost:8084
  --user <you> --screen-index 1 --audio-index 0
# ... work for a bit ... Ctrl-C once → drain → report summary; expect verdict: clean
```
Smoke test without any permissions: `python3 nucleus_capture.py record --source test
--duration 25 --server http://localhost:8084`. Server-side cross-check is the session
report (two streams, `video` + `audio`, dense sequences; transcripts in `/context`).

## Tests

`tests/test_mac_client.py` (module loaded via importlib from `clients/mac/`): stamp
chaining math; spool-scan completeness rule (next-file / ffmpeg-exit); serialized upload
order + retry-then-success on 5xx; 4xx dropped + counted; sha256 + wire params against a
stdlib http.server stub; end-marker `last_seq`; ffmpeg argv construction for both sources
(no avfoundation needed); plus an ffmpeg-marked integration run: `--source test
--duration ~4 s` against the stub, asserting real mp4 segments cross the wire in order.
Wire conformance against the real ingest app lives in `tests/test_wire_conformance.py`
(shared with WS-E shapes).

## Worklog

- 2026-07-18 — spec written (decisions D-F1…D-F4); handed to the build fan-out.
- 2026-07-18 — built as specced: single-file `nucleus_capture.py` (~660 lines, stdlib only,
  executable), 27 tests in `tests/test_mac_client.py` incl. a real-ffmpeg CLI subprocess
  run against a stdlib HTTP stub (dense seq order, real mp4 bodies, sha256, exact stamp
  adjacency, end marker, exit codes). Notable build decisions: ffmpeg spawned
  `start_new_session=True` so Ctrl-C routes through the CLI; 4xx-dropped files kept in the
  spool as evidence; ffmpeg-dies-with-zero-segments short-circuits with a permission hint.
- 2026-07-18 — **adversarial review round** (5-lens find → 2-skeptic verify) fixed here:
  (1) *stamp corruption on Ctrl-C* (HIGH): an interrupt landing inside an in-flight upload
  re-processed that seq on the graceful pass and APPENDED its duration twice, silently
  shifting every later wire timestamp — durations are now idempotently slotted
  (`slot_duration`, regression-tested); (2) *stale spool*: a reused `--spool` dir holding a
  prior run's `seg-*.mp4` would upload them into the NEW session — `record` now refuses a
  non-empty spool; (3) *mid-response server death*: `http.client.HTTPException`
  (BadStatusLine/IncompleteRead is NOT OSError) escaped the retry pump and crashed the run —
  now retried; (4) zero-segments-with-clean-ffmpeg-exit no longer posts a doomed end marker
  and burns the 120 s report timeout — fast exit 1; (5) the spool regex follows ffmpeg's
  `%06d` widening past seg-999999.
- 2026-07-18 — **wire rename adopted** (founders): `/capture/*` URLs; report-poll +
  messages updated. **Live E2E on the run_learn fleet (faster_whisper)**: `record --source
  test --duration 25` → 3 segments, exact `t_end[n]==t_start[n+1]` adjacency on the wire,
  demux → audio (3 wav chunks) + video (3 mp4 chunks), DP continuity `checked:true` /
  `missing_unacked:[]` both streams, verdict **clean**, exit 0; 12 C2 records in `/context`
  with matching spans (sine audio → honest empty transcripts via the VAD gate). The
  deprecated `/ingest` alias was also drilled live (old-prefix upload+end, new-prefix
  report → clean). **avfoundation leg untested here (headless Linux) — §Runbook is the
  human mac leg.**
