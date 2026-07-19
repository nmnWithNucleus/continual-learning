# Alpha test runbook — all three capture surfaces against the live fleet

> One tester drives phone web + Chrome extension + mac CLI end-to-end and checks that the
> user-facing setup is sturdy and every nuance lands as designed. The unit/conformance
> suites already prove the wire hermetically; THIS is the human-experience pass. State at
> writing: **all captured data purged 2026-07-19, fleet restarted fresh** (no alias — the
> wire is `/capture/*` only). Deep per-surface rationale: [ws-e](ws-e-extension.md) /
> [ws-f](ws-f-mac-cli.md) / [ws-b](ws-b-phone-web-client.md).

**Owner session:** recording computer-capture lead · **Last updated:** 2026-07-19

## Before you start (once)

- Fleet + tunnel run on node-7. Check: `bash product/services/platform/deploy/run_learn.sh
  --status` (3× `up`). The tunnel URL **rotates when the tunnel restarts** — always read
  it fresh: `cat product/services/recording/var/tunnel_url.txt`. Below, `$TUNNEL` = that URL.
- On your mac: `git pull` this repo (the extension + CLI ship in it), `brew install ffmpeg`.
- Use the SAME user id on every surface (suggestion: `nmn`) — it groups your `/context`
  records. Each surface auto-mints its own stable `device_id` (`phone-web-*`,
  `ext-chrome-*`, `mac-cli-*`), which is how sessions stay attributable per device.
- Server-side truth is read on node-7 (storage/DP are not tunneled):
  ```bash
  curl -s $TUNNEL/capture/sessions | python3 -m json.tool        # all sessions, any machine
  curl -s $TUNNEL/capture/sessions/<id>/report | python3 -m json.tool
  # on node-7 only — C2 records that landed for you today:
  curl -s "http://127.0.0.1:8083/context/records?user_id=nmn&from=2026-07-19T00:00:00Z&to=2026-07-20T00:00:00Z" | python3 -m json.tool
  ```

## Surface 1 — phone web client

**Launch:** open `$TUNNEL/client/` on the phone — and **hard-refresh the page first**
(the wire moved to `/capture/*`; a page cached from before the rename will fail every
upload with HTTP 404 in the status area — that symptom = stale page, refresh again).

| Step | Expect |
|---|---|
| Set user id `nmn`, tap record, film ~35 s | timer runs; captured/uploaded tick together every ~10 s; verdict badge `recording` |
| Tap stop | brief `uploading`, then badge → **`clean`**; report lines show `audio: N chunks` + `video: N chunks`, received N/N |
| Pause 10 s mid-recording, resume, stop | no new segments while paused; final verdict `clean`; total segments ≈ recorded time / 10 s |
| Camera toggle OFF, record 15 s, stop | mic-only: report shows an `audio` stream ONLY; verdict `clean` |
| Airplane mode 20 s mid-recording, back on, stop | queued climbs while offline, drains after; "retrying in Ns" appears then clears; verdict `clean`, **no missing seqs** |
| Background the browser mid-recording, reopen | the hidden page beacons an end marker (ledger `ended` at that moment), but if iOS did NOT kill the tab the page still shows `recording` on return — that's expected: capture state survives backgrounding. If segments keep flowing, the server REOPENS the session (stale-marker protection, by design). Tap **Stop** for the real end marker → report typically `clean`. Only if the OS killed the tab do you come back to an idle page — then the old session's report is `clean` for what it received (a lost in-memory queue tail shows as client-leg missing — the design being honest; note it if seen) |

## Surface 2 — Chrome extension (on the mac)

**Install (once):** Chrome → `chrome://extensions` → toggle **Developer mode** → **Load
unpacked** → pick `<repo>/product/services/recording/clients/extension/`. Pin "Nucleus
Capture" to the toolbar.

**Configure (once):** click the icon → server URL = `$TUNNEL` → user id `nmn` → **Save**
→ Chrome prompts for access to that origin → **Allow**. Then **close and reopen the
popup and confirm the fields still show YOUR values** — a Save that reverts to
`localhost:8084`/`beta-user` is the fixed alpha bug regressing; report it. (If you skip
Save, Record re-prompts. If the tunnel URL ever rotates, repeat this step with the new
URL. A recording accidentally started against a wrong/unreachable server retries
forever and locks the settings — use the **Discard unsent** button that appears in the
draining state to bail out, then fix the URL.)

| Step | Expect |
|---|---|
| Open the popup ON a tab that is playing audio (e.g. YouTube); both sources ✓; **Record** | a tiny "Nucleus Capture — choose what to share" window opens WITH Chrome's share dialog (the popup may close — that's fine, capture is worker-driven); pick a screen/window and the tiny window closes itself |
| Reopen the popup | state `recording`; TWO source blocks (screen / tab audio), separate session ids, counters ticking ~10 s |
| Listen | the captured tab is still audible (passthrough) |
| Wait ~40 s → **Stop** | both blocks drain; you may glimpse the first source's `clean` badge, then the popup resets to the idle hint within seconds — that is the capture document closing after full drain, NOT a failure (a popup that persists both final verdicts is a noted follow-up). **The verdict check for this surface is server-side**: `/capture/sessions` → both sessions `clean`; screen session's report = `video` stream only, tab session's = `audio` only; same `ext-chrome-*` device on both |
| Drill: take >15 s choosing in the picker before confirming | both sources must STILL start (stream-id expiry was a fixed defect — if tab audio shows a "did not start" error row instead, that is a bug, report it) |
| Drill: Chrome's "Stop sharing" bar mid-recording | screen session ends `clean` on its own; tab audio KEEPS recording until you Stop |
| Drill: cancel the screen picker | tab-audio-only recording proceeds; the reopened popup shows a LONE tab-audio block — the screen source is simply absent (its "cancelled" reply died with the popup the picker closed; known cosmetic limit, not a failure) |
| Drill: close the captured tab mid-recording | tab-audio session ends `clean`; screen continues |
| Drill: escape hatch — set a bogus server URL, Save, record a few seconds, Stop | state sticks at `draining` with "network error … retrying" (by design); **Discard unsent** appears → click it → popup returns to idle and settings unlock; fix the URL and re-record |

## Surface 3 — mac CLI

**One-time permission:** System Settings → Privacy & Security → **Screen Recording** →
enable your terminal app, then **quit and reopen the terminal**. (Without it: black
frames or `Operation not permitted`.) Mic permission prompts inline on first run.

```bash
cd <repo>/product/services/recording/clients/mac

# 0) no-permissions smoke test first — proves wire + fleet from the mac:
python3 nucleus_capture.py record --source test --duration 25 --server $TUNNEL --user nmn
#    expect: 3 "seg N ... -> ..." lines, then "verdict: clean", exit 0

# 1) find your device indices (they shift with attached cameras/mics):
python3 nucleus_capture.py list-devices
#    NOTE: the trailing "Error opening input: Input/output error" is NORMAL —
#    ffmpeg always "fails" the fake empty input after printing the table.
#    Read the table: pick the "Capture screen N" video index (a webcam index
#    instead records the camera); avoid virtual audio devices (e.g. "Microsoft
#    Teams Audio"); a Bluetooth headset mic may drop the headset into
#    call-quality mode while capturing.

# 2) the real thing — screen + mic:
python3 nucleus_capture.py record --server $TUNNEL --user nmn \
  --screen-index 1 --audio-index 0
#    if ffmpeg refuses the framerate: add --framerate 30
#    "Configuration of video device failed, falling back to default" + pixel-format
#    override lines are warnings, not failures. The real liveness signal is a
#    "seg 0 ..." line within ~15 s; if none appears, Ctrl-C and report it.
```

| Step | Expect |
|---|---|
| Let it run ~1 min, then **Ctrl-C once** | "stopping capture…", tail segment uploads, "waiting for the server's continuity report…", summary with `audio` + `video` chunk counts, **`verdict: clean`**, exit 0 |
| Stamps in the seg lines | `t_end` of seg N == `t_start` of seg N+1 (exact adjacency — the CLI's continuity signal) |
| Drill: pull Wi-Fi ~20 s mid-run, restore, Ctrl-C | "retrying in Ns" lines, then uploads resume; final verdict `clean` |
| Drill: Ctrl-C twice quickly | polite abandon message with the session id + spool path; the report (from another machine) shows `unterminated` — expected, honest |

## After each surface — the server-side cross-check

1. `curl -s $TUNNEL/capture/sessions` — your session appears with the right device_id,
   `ended: true`, received == expected.
2. Its `/report` — verdict `clean`; every `emit_leg` entry has `dp.checked: true` and
   `dp.missing_unacked: []`; `segment_states.received == 0` (fully drained).
3. On node-7: the `/context` query above — transcripts (`kind: transcript`, real speech →
   real text; language pinned `en`) + video caption records, spans matching your
   recording's wall-clock. **Speak while testing** so transcripts are non-empty — silence
   correctly yields empty transcripts (VAD gate), which is not a failure.

## Pass bar + what to report

**Pass** = every non-drill session ends verdict `clean` with zero unexplained missing
seqs, and every drill behaves per its Expect cell. Anything else: grab the `session_id`,
the full `/report` JSON, what you did, and (for the extension) any popup error rows —
drop them in the founders' channel / an escalation note in this file's Worklog. Known
truths that are NOT bugs: ~tens-of-ms capture gaps between phone/extension segments
(D-M1-1 restart reality); chunk counts trailing the segment counter by a few seconds
while faster-whisper (CPU) catches up; empty transcripts for silent/toneless audio.

## Worklog

- 2026-07-19 — runbook written; data purged, fleet restarted fresh, `/ingest` alias
  removed (single-tester decision — refresh beats route versioning). Alpha pass is the
  tester's step.
- 2026-07-19 — **Surface 3 (mac CLI): PASSED** (CTO, real avfoundation runs over the
  tunnel). Results: smoke test clean; first real run surfaced the output-frame-rate
  defect (zero segments finalized — fixed same hour, fps pin, ws-F worklog); retry
  7/7 segments both streams, verdict `clean`, graceful Ctrl-C with tail segment.
  Content checks: VLC on a kept segment shows the REAL screen (permission/black-frame
  question closed); a spoken run produced audible audio in the spool AND real ASR
  transcripts in `/context` for the window. Known items recorded: seg-0 warm-up span
  (18.4 s, first segment only — ws-F); video quality soft at the default
  `--max-width 1728` + pinned CRF — acceptable for alpha, raise `--max-width` for
  crisper text; the real fidelity bar is charter OQ3 (codec/bitrate ladder, joint
  with DP).
- Surface 1 (phone web) and Surface 2 (extension): pending.
