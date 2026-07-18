# WS5 — Tunnel / HTTPS exposure (cloudflared)

Status: **DONE — WS6 confirmed real backend turns + ASR stream through the HTTPS tunnel (real-iPhone camera/mic prompt pending the user)** · Owner/agent: WS5 build agent · Last updated: 2026-06-30

> **Start here:** read the global [`../HANDOFF.md`](../HANDOFF.md) in full, then this file. Small but
> load-bearing: without HTTPS the iPhone camera API won't work at all. You can de-risk this **early**
> against a stub server, before WS2/WS3 are done. Keep the Worklog current; flip your status row when done.

## Goal
Expose the backend (WS2, local port — default **8080**) to my **iPhone over the public internet with
HTTPS**, via **Cloudflare Tunnel**. Hand back a working `https://…` URL that loads the UI and supports
large video POSTs + token streaming.

## Deliverables (in `scripts/`)
- `tunnel.sh` — starts `cloudflared` pointing at the backend's local port; prints the public HTTPS URL.
- Short notes: install steps, whether a Cloudflare account/named tunnel is used vs the quick ephemeral
  tunnel, and how to restart. Document the URL hand-off to WS3/WS6.

## Why HTTPS is non-negotiable
iOS Safari `getUserMedia` is **secure-context only** — over plain HTTP `navigator.mediaDevices` is
`undefined`. cloudflared auto-provisions TLS, so the phone gets a valid HTTPS origin with no cert work.

## Suggested steps
1. Install `cloudflared` on the a3mega node (same node as WS2).
2. **Quick start (ephemeral):** `cloudflared tunnel --url http://localhost:8080` → prints a
   `https://<random>.trycloudflare.com` URL. Good enough for V0; URL changes each run.
   - **Optional (stable URL):** a named tunnel under a Cloudflare account gives a fixed hostname — only
     bother if the ephemeral URL churn is annoying.
3. **Verify the hard parts** through the tunnel, not just a homepage load:
   - a **large multipart POST** (tens of MB video) succeeds (no body-size cap surprises);
   - **streaming** responses arrive incrementally (chunked `text/plain` from `/api/turn` isn't buffered);
   - load it on the **actual iPhone** and confirm the camera permission prompt appears (proves HTTPS).
4. Hand the URL to WS3/WS6 and document restart steps.

## Key files & paths
- `scripts/tunnel.sh`. Backend port (coordinate with WS2; default **8080**).

## Gotchas / decisions
- Point the tunnel at **WS2's port**, not WS1's vLLM port — only the backend should be public.
- If cloudflared buffers streaming responses, check for any response-buffering setting; chunked
  `text/plain` should pass through. Raise with WS6 if first-token latency looks buffered.
- Ephemeral `trycloudflare.com` URLs rotate per launch — fine for a personal POC; just re-share the link.
- Don't expose the vLLM endpoint or the clip scratch dir publicly.

## Definition of done
A live `https://…` URL (documented in this file + handed to WS6) that, from a real iPhone, loads the UI,
triggers the camera permission prompt, accepts a large video POST, and streams the answer incrementally.

## Build result & how it works
`scripts/tunnel.sh` is the single deliverable. It:
1. Locates `cloudflared` (PATH or `~/.local/bin`); prints an install hint if missing.
2. Warns if nothing is listening on the target port yet (non-fatal — tunnel can precede the backend).
3. Starts `cloudflared tunnel --no-autoupdate --url http://localhost:<PORT>` **backgrounded**, logging to
   `scripts/.tunnel.log`.
4. Parses the `https://<random>.trycloudflare.com` URL from the log, prints a banner, and writes the URL to
   `scripts/.tunnel_url` (single line, no trailing newline) for programmatic hand-off.
5. Stays attached via `tail -f` so you can watch traffic/errors; **Ctrl-C tears the tunnel down cleanly**
   (trap also removes `.tunnel_url`).

Port: defaults to **8080** (WS2 backend). Override with `PORT=9000 scripts/tunnel.sh` or positional `scripts/tunnel.sh 8080`.
Points ONLY at the backend port — vLLM:8000 and the clip dir are never exposed.

## Install (done on this node)
cloudflared **2026.6.1** installed as a standalone binary (no root needed for the binary itself):
```
mkdir -p ~/.local/bin
curl -fL -o ~/.local/bin/cloudflared \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
chmod +x ~/.local/bin/cloudflared
```
Also copied to `/usr/local/bin/cloudflared` so it's on the default PATH. (The `.deb` install path hit a
sandbox restriction on `/var/log/dpkg.log`; the standalone binary sidesteps that and works fine.)

## How the URL is handed to WS3 / WS6
- **stdout banner** (human).
- **`scripts/.tunnel_url`** — one line, the integrator (WS6) reads this to know where the phone points.
- Full cloudflared log at **`scripts/.tunnel.log`**.
WS3 needs no build-time knowledge of the URL: the phone just opens it, the UI is served from `/`, and the UI
fetches `/api/config` + posts to `/api/transcribe` / `/api/turn` on the **same origin** (relative paths), so a
rotating hostname doesn't break anything.

## Validation (the 3 hard parts — all PASSED through the live HTTPS tunnel)
Validated independently of WS2 by spinning up throwaway stubs (kept in scratchpad, NOT under backend/, torn
down afterward). Tested against TWO origins: (1) a raw `http.server` stub, (2) a **uvicorn/Starlette stub that
mirrors WS2's exact stack** (StreamingResponse text/plain + multipart). Final end-to-end run was through the
real `scripts/tunnel.sh` pointed at port 8080.

- **(a) HTTPS page load** — `GET /` returns HTTP/2 200, `server: cloudflare`, valid auto-provisioned TLS. ✓
- **(b) Large multipart POST** — 40MB POST received in full (0.75s); **90MB also succeeded** (2.4s). No
  body-size cap surprises for realistic tens-of-MB clips. ✓
- **(c) Incremental streaming (THE critical risk)** — chunked `text/plain` from `/api/turn` arrives token-by-
  token, **~0.3s apart, NOT buffered**; first-token latency is preserved end-to-end. ✓
  Also verified the combined path: a 40MB video upload + streamed token response on the **same** `/api/turn`
  request works.

## ⚠️ KEY FINDING for WS2 / WS6 (streaming only passes if the origin frames chunks properly)
cloudflared forwards a streamed response **incrementally only if the origin emits proper HTTP chunked
framing**. A naive Python `http.server` that omits `Content-Length`/`Transfer-Encoding` and relies on
connection-close gets **BUFFERED by cloudflared until the connection closes** (looked like a tunnel bug, was
actually a stub artifact — headers arrived in ~80ms but body bytes were withheld). **uvicorn/Starlette
`StreamingResponse` emits correct chunked framing and streams through cleanly.** Since WS2 is FastAPI/Starlette
on uvicorn, this is the correct path and needs no special config — but **do not** hand-roll a non-chunked
streaming response, and avoid any reverse proxy in front that buffers. cloudflared itself needs **no** anti-
buffering flag for `text/plain` chunked; it just works. (The stub set `X-Accel-Buffering: no` /
`Cache-Control: no-transform` as belt-and-suspenders; not required by cloudflared, harmless to keep.)

## Other findings / notes
- Edge protocol: default is `quic`; tested `--protocol http2` too. Neither caused buffering (the buffering was
  the stub-framing issue above). Quick tunnels default to quic; left as default in `tunnel.sh`.
- Benign log line on startup: `failed to sufficiently increase receive buffer size (... quic-go ...)` — a UDP
  socket-buffer tuning note, does not affect function. (Could be silenced with a sysctl bump to
  `net.core.rmem_max`; not necessary.)
- **Ephemeral URL churn:** every launch yields a new `https://<random>.trycloudflare.com`. Fine for a personal
  POC — just re-run `tunnel.sh` and re-share. A **named tunnel** (Cloudflare account + domain) gives a fixed
  hostname; commands are documented at the bottom of `tunnel.sh`. Not required for V0.

## How to run
```
# default: expose backend on :8080
poc/live_video_chat/scripts/tunnel.sh
# -> prints https://<random>.trycloudflare.com, writes scripts/.tunnel_url
# open that URL on the iPhone. Ctrl-C to stop.

# custom port:
PORT=9000 poc/live_video_chat/scripts/tunnel.sh
```
**Restart:** just re-run it (URL rotates). Read the new URL from `scripts/.tunnel_url`.

## Still open (for WS6, needs the actual phone)
- Confirm on a real iPhone that the camera/mic **permission prompt appears** (proves secure-context HTTPS) and
  that a real ~30s MP4 clip POSTs + the answer streams in. The tunnel/HTTPS plumbing is proven; only the
  on-device leg remains, which requires the physical device WS6 owns.

## Worklog
- 2026-06-30 — file created (scaffolding). Not started.
- 2026-06-30 — **DONE.** Installed cloudflared 2026.6.1. Wrote `scripts/tunnel.sh` (backgrounds cloudflared,
  captures URL → stdout + `.tunnel_url`, clean Ctrl-C teardown). Validated all 3 hard parts through the live
  HTTPS tunnel against a faithful uvicorn/Starlette stub AND the real `tunnel.sh`→:8080 path: HTTPS page load,
  40MB+90MB POSTs, and incremental token streaming (not buffered). Found + documented the chunked-framing
  caveat for WS2/WS6. Tore down all stubs/test tunnels; ports freed. Remaining: real-iPhone confirm (WS6).
