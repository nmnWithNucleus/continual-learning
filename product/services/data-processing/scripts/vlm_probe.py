"""WS-A serving probe — verify the VL-endpoint assumptions the clip design rests on.

The `VIDEO_PIPELINE=clip` design (handoff/ws-video-clip.md, D-02/D-03/D-09) sends
ONE multi-image `/v1/chat/completions` call per chunk and asks the served model for
guided-JSON output. `services/inference/serve_vllm.sh` launches the model TEXT-ONLY
today and states verbatim that the video knobs (`--limit-mm-per-prompt`,
`--mm-processor-kwargs`, `--media-io-kwargs`) are "intentionally omitted". So four
external facts must be measured against a REAL endpoint before app code assumes them:

  (1) N `image_url` parts in one user message  -> is `--limit-mm-per-prompt` raised,
      and to what? (design needs image >= VIDEO_CLIP_MAX_FRAMES=12; E-3(a) asks 16.)
  (2) `response_format: {"type":"json_schema"}` -> is guided decoding available? It is
      the primary discipline lever for a Flash-class 32B (D-13/5.3), not an optimisation.
  (3) `usage.prompt_tokens` for one 768x480 frame -> 360 (Qwen3-VL factor 32), 470
      (factor 28), or materially lower (server-side `max_pixels` clamping)?
  (4) `video_url` data-URI -> informational only, for O-4's future.

This script is the INSTRUMENT that makes E-3(a) precise. It speaks the exact wire
`app/vision/vlm.py` speaks (same `/v1/chat/completions`, same `image_url` data part,
same env-var config), needs only stdlib + httpx (already a base dep), needs NO GPU in
this process, and — like `scripts/smoke_audio_backends.py` — NEVER fabricates a result:
an unreachable endpoint prints exactly which probes could not run and why, and the
launch flags required to bring one up.

Usage (from the data-processing service root):
    # point at a live VL endpoint (defaults mirror app/vision/config.py):
    VIDEO_VLM_URL=http://127.0.0.1:8000 ./.venv/bin/python scripts/vlm_probe.py
    # options:
    ./.venv/bin/python scripts/vlm_probe.py --url http://HOST:8000 \
        --model Qwen/Qwen3-VL-32B-Instruct --width 768 --height 480 \
        --frames 1,2,4,8,12,16 --sweep 768,1024,1280 --probe all --json out.json
    # a real captured frame instead of the synthetic gradient (optional):
    ./.venv/bin/python scripts/vlm_probe.py --image frame.jpg
    # probe 4 needs a sample clip; auto-synthesised via ffmpeg if found, else:
    ./.venv/bin/python scripts/vlm_probe.py --video clip.mp4

Exit code: 0 iff every REQUESTED probe ran and none FAILED (SKIP is not a failure);
2 if the endpoint was unreachable; 1 if a probe FAILED against a reachable endpoint.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import struct
import subprocess
import sys
import time
import zlib
from dataclasses import dataclass, field
from typing import Any

import httpx

# --- Qwen3-VL-32B-Instruct token geometry (from the model's own config) ----------
# preprocessor_config.json: patch_size=16, merge_size=2 -> factor 32.
# vision_config: patch_size=16, spatial_merge_size=2. size (AREA px) in
# preprocessor_config: shortest_edge=65536 (min_pixels), longest_edge=16777216
# (max_pixels). merged vision tokens = ceil(H/32) * ceil(W/32).
QWEN3VL_FACTOR = 32          # patch_size(16) * spatial_merge_size(2)
QWEN2VL_FACTOR = 28         # the factor-28 (patch 14) alternative the design hedges for
DEFAULT_MAX_PIXELS = 16_777_216
DEFAULT_MIN_PIXELS = 65_536


# ---------------------------------------------------------------------------------
# Deterministic, dependency-free test image (PIL is NOT in the DP venv). A truecolor
# PNG at EXACT WxH — token count depends on decoded pixel dims, not the codec, so PNG
# vs the app's JPEG is immaterial to probe (3). A gradient (not a flat fill) so a
# server that special-cases blank images still tokenises it normally.
# ---------------------------------------------------------------------------------
def _png_gradient(width: int, height: int) -> bytes:
    def _chunk(typ: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + typ
            + data
            + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit truecolor RGB
    raw = bytearray()
    wdiv = max(1, width - 1)
    hdiv = max(1, height - 1)
    for y in range(height):
        raw.append(0)  # filter type 0 (None)
        g = (y * 255) // hdiv
        row = bytearray()
        for x in range(width):
            row += bytes(((x * 255) // wdiv, g, 128))
        raw += row
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + _chunk(b"IEND", b"")
    )


def _data_url(data: bytes, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")


def _smart_resize(w: int, h: int, factor: int,
                  min_px: int = DEFAULT_MIN_PIXELS, max_px: int = DEFAULT_MAX_PIXELS
                  ) -> tuple[int, int, int]:
    """Reproduce Qwen's smart_resize locally so we can PREDICT tokens and detect a
    server-side clamp. Rounds each edge to a multiple of `factor`, then rescales so
    total pixels land within [min_px, max_px]. Returns (H', W', merged_tokens)."""
    import math

    def _round(v: int) -> int:
        return max(factor, round(v / factor) * factor)

    hb, wb = _round(h), _round(w)
    if hb * wb > max_px:
        beta = math.sqrt((h * w) / max_px)
        hb = max(factor, math.floor(h / beta / factor) * factor)
        wb = max(factor, math.floor(w / beta / factor) * factor)
    elif hb * wb < min_px:
        beta = math.sqrt(min_px / (h * w))
        hb = math.ceil(h * beta / factor) * factor
        wb = math.ceil(w * beta / factor) * factor
    return hb, wb, (hb // factor) * (wb // factor)


# ---------------------------------------------------------------------------------
# Result plumbing
# ---------------------------------------------------------------------------------
@dataclass
class ProbeResult:
    name: str
    status: str = "NOT RUN"        # PASS | FAIL | SKIP | NOT RUN
    headline: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def line(self) -> str:
        return f"  {self.name:26s} {self.status:5s} {self.headline}"


def _banner(name: str) -> None:
    print(f"\n{'=' * 74}\n{name}\n{'=' * 74}", flush=True)


def _post_chat(client: httpx.Client, base: str, model: str, api_key: str,
               content: list[dict], *, max_tokens: int = 16,
               response_format: dict | None = None) -> httpx.Response:
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": max_tokens,
        "temperature": 0,
        "stream": False,
    }
    if response_format is not None:
        payload["response_format"] = response_format
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    return client.post(f"{base}/v1/chat/completions", json=payload, headers=headers)


def _prompt_tokens(resp: httpx.Response) -> int | None:
    try:
        return int(resp.json().get("usage", {}).get("prompt_tokens"))
    except Exception:
        return None


def _err_snippet(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        msg = body.get("message") or body.get("error", {})
        if isinstance(msg, dict):
            msg = msg.get("message", msg)
        return str(msg)[:300]
    except Exception:
        return resp.text[:300]


# ---------------------------------------------------------------------------------
# Reachability
# ---------------------------------------------------------------------------------
def check_reachable(client: httpx.Client, base: str, api_key: str) -> tuple[bool, str, list[str]]:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        r = client.get(f"{base}/v1/models", headers=headers)
        r.raise_for_status()
        served = [m.get("id") for m in r.json().get("data", [])]
        return True, f"reachable; served models: {served}", served
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}", []


# ---------------------------------------------------------------------------------
# Probe 3 — token count for one frame (run first: cheapest, one image)
# ---------------------------------------------------------------------------------
def probe_tokens(client: httpx.Client, base: str, model: str, api_key: str,
                 image: bytes, mime: str, w: int, h: int,
                 sweep: list[int]) -> ProbeResult:
    res = ProbeResult("3 token-count")
    label = "Describe the image."
    try:
        # Isolate the image's token contribution: (text+image) - (text only).
        text_only = _post_chat(client, base, model, api_key,
                               [{"type": "text", "text": label}], max_tokens=1)
        text_only.raise_for_status()
        base_tok = _prompt_tokens(text_only)

        withimg = _post_chat(client, base, model, api_key, [
            {"type": "image_url", "image_url": {"url": _data_url(image, mime)}},
            {"type": "text", "text": label},
        ], max_tokens=1)
        withimg.raise_for_status()
        total_tok = _prompt_tokens(withimg)
        img_tok = (total_tok - base_tok) if (total_tok and base_tok) else None

        exp32 = _smart_resize(w, h, QWEN3VL_FACTOR)[2]
        exp28 = _smart_resize(w, h, QWEN2VL_FACTOR)[2]
        res.data.update(prompt_tokens_total=total_tok, prompt_tokens_text_only=base_tok,
                        image_tokens=img_tok, expected_factor32=exp32,
                        expected_factor28=exp28, width=w, height=h)

        if img_tok is None:
            res.status, res.headline = "FAIL", "endpoint returned no usage.prompt_tokens"
            return res
        # +/- a couple special tokens (<|vision_start|>/<|vision_end|>).
        if abs(img_tok - exp32) <= 4:
            verdict = f"factor 32 CONFIRMED ({img_tok}~={exp32}); no server-side clamp"
        elif abs(img_tok - exp28) <= 4:
            verdict = (f"factor 28 ({img_tok}~={exp28}) -> every vision figure inflates "
                       f"~+31%; --mm-processor-kwargs pin recommended")
        elif img_tok < exp32 - 4:
            verdict = (f"MATERIALLY LOWER ({img_tok} < {exp32}) -> server-side max_pixels "
                       f"is CLAMPING; --mm-processor-kwargs max_pixels is MANDATORY")
        else:
            verdict = f"unexpected ({img_tok}); expected {exp32} (f32) or {exp28} (f28)"
        res.status, res.headline = "PASS", f"{w}x{h}: {img_tok} img tok -> {verdict}"

        # Optional width sweep — confirms the count scales as ceil(W/32)*ceil(H/32).
        if sweep:
            rows = []
            for sw in sweep:
                sh = round(sw * h / w)
                png = _png_gradient(sw, sh)
                rr = _post_chat(client, base, model, api_key, [
                    {"type": "image_url", "image_url": {"url": _data_url(png, "image/png")}},
                    {"type": "text", "text": label},
                ], max_tokens=1)
                itok = (_prompt_tokens(rr) - base_tok) if rr.status_code == 200 else None
                rows.append({"w": sw, "h": sh, "img_tokens": itok,
                             "expected_f32": _smart_resize(sw, sh, QWEN3VL_FACTOR)[2]})
                print(f"    sweep {sw}x{sh}: img_tokens={itok} "
                      f"expected_f32={rows[-1]['expected_f32']}", flush=True)
            res.data["sweep"] = rows
        return res
    except httpx.HTTPStatusError as exc:
        res.status, res.headline = "FAIL", f"HTTP {exc.response.status_code}: {_err_snippet(exc.response)}"
        return res
    except Exception as exc:  # noqa: BLE001
        res.status, res.headline = "FAIL", f"{type(exc).__name__}: {exc}"
        return res


# ---------------------------------------------------------------------------------
# Probe 1 — how many image_url parts fit in one message (measures --limit-mm-per-prompt)
# ---------------------------------------------------------------------------------
def probe_multiimage(client: httpx.Client, base: str, model: str, api_key: str,
                     image: bytes, mime: str, counts: list[int]) -> ProbeResult:
    res = ProbeResult("1 multi-image limit")
    url = _data_url(image, mime)
    ladder: list[dict] = []
    max_ok, min_fail = 0, None
    try:
        for k in sorted(set(counts)):
            # Design's target shape: interleaved 'Frame j (+t.s):' labels + task last.
            content: list[dict] = []
            for j in range(k):
                content.append({"type": "text", "text": f"Frame {j} (+{j * 2.5:.1f}s):"})
                content.append({"type": "image_url", "image_url": {"url": url}})
            content.append({"type": "text", "text": "Reply with the single word OK."})
            r = _post_chat(client, base, model, api_key, content, max_tokens=4)
            ok = r.status_code == 200
            row = {"k": k, "status": r.status_code, "ok": ok}
            if not ok:
                row["error"] = _err_snippet(r)
            ladder.append(row)
            print(f"    k={k:3d} images -> HTTP {r.status_code} {'OK' if ok else row.get('error','')}",
                  flush=True)
            if ok:
                max_ok = max(max_ok, k)
            elif min_fail is None:
                min_fail = k
        res.data.update(ladder=ladder, max_ok=max_ok, min_fail=min_fail)
        need = 12  # VIDEO_CLIP_MAX_FRAMES
        if max_ok >= need:
            res.status = "PASS"
            res.headline = (f"limit-mm-per-prompt image >= {max_ok} (>= design K={need}); "
                            + ("flag already sufficient" if min_fail is None
                               else f"cap at {min_fail}"))
        elif max_ok >= 1:
            res.status = "FAIL"
            res.headline = (f"image limit is {max_ok} (< K={need}) -> --limit-mm-per-prompt "
                            f"MUST be raised; else WS-D default pack falls back to "
                            f"screen-clip-single-v1 (K=1)")
        else:
            res.status = "FAIL"
            res.headline = "even a single image was rejected"
        return res
    except Exception as exc:  # noqa: BLE001
        res.status, res.headline = "FAIL", f"{type(exc).__name__}: {exc}"
        res.data["ladder"] = ladder
        return res


# ---------------------------------------------------------------------------------
# Probe 2 — guided decoding (response_format json_schema / json_object)
# ---------------------------------------------------------------------------------
_CLIP_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "clip",
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["app", "activity", "sensitive"],
            "properties": {
                "app": {"type": "string"},
                "activity": {"type": "string"},
                "sensitive": {"type": "boolean"},
            },
        },
    },
}


def probe_guided(client: httpx.Client, base: str, model: str, api_key: str,
                 image: bytes, mime: str) -> ProbeResult:
    res = ProbeResult("2 guided-json")
    content = [
        {"type": "image_url", "image_url": {"url": _data_url(image, mime)}},
        {"type": "text", "text": "Return app, activity and sensitive for this frame."},
    ]
    out: dict[str, Any] = {}
    try:
        for name, rf in (("json_schema", _CLIP_SCHEMA),
                         ("json_object", {"type": "json_object"})):
            r = _post_chat(client, base, model, api_key, content, max_tokens=128,
                           response_format=rf)
            entry: dict[str, Any] = {"status": r.status_code}
            if r.status_code == 200:
                reply = (((r.json().get("choices") or [{}])[0].get("message") or {})
                         .get("content") or "")
                try:
                    parsed = json.loads(reply)
                    entry["valid_json"] = True
                    entry["keys"] = sorted(parsed) if isinstance(parsed, dict) else None
                except Exception:
                    entry["valid_json"] = False
                    entry["reply_head"] = reply[:120]
            else:
                entry["error"] = _err_snippet(r)
            out[name] = entry
            print(f"    response_format={name:12s} -> HTTP {r.status_code} "
                  f"{entry.get('valid_json', entry.get('error',''))}", flush=True)
        res.data["response_formats"] = out
        js = out.get("json_schema", {})
        if js.get("status") == 200 and js.get("valid_json"):
            res.status = "PASS"
            res.headline = ("guided decoding AVAILABLE (json_schema honored, valid JSON); "
                            "no launch flag required")
        elif js.get("status") == 200:
            res.status = "FAIL"
            res.headline = "json_schema accepted but reply was not valid JSON — check backend"
        else:
            res.status = "FAIL"
            res.headline = (f"json_schema rejected (HTTP {js.get('status')}): "
                            f"{js.get('error','')[:120]} -> enable structured outputs flag")
        return res
    except Exception as exc:  # noqa: BLE001
        res.status, res.headline = "FAIL", f"{type(exc).__name__}: {exc}"
        return res


# ---------------------------------------------------------------------------------
# Probe 4 — video_url data-URI (informational, O-4)
# ---------------------------------------------------------------------------------
def _find_ffmpeg() -> str | None:
    if shutil.which("ffmpeg"):
        return "ffmpeg"
    for cand in ("/home/ubuntu/miniconda3/envs/ffmpeg/bin/ffmpeg",
                 "/home/ubuntu/miniconda3/envs/moe/bin/ffmpeg"):
        if os.path.exists(cand):
            return cand
    return None


def _synth_mp4() -> bytes | None:
    ff = _find_ffmpeg()
    if not ff:
        return None
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "clip.mp4")
        cmd = [ff, "-v", "error", "-y", "-f", "lavfi", "-i",
               "testsrc=size=256x256:rate=4:duration=2", "-pix_fmt", "yuv420p", out]
        try:
            subprocess.run(cmd, check=True, timeout=60)
            with open(out, "rb") as fh:
                return fh.read()
        except Exception:
            return None


def probe_video(client: httpx.Client, base: str, model: str, api_key: str,
                video_path: str | None) -> ProbeResult:
    res = ProbeResult("4 video_url (info)")
    if video_path:
        with open(video_path, "rb") as fh:
            video = fh.read()
    else:
        video = _synth_mp4()
    if not video:
        res.status = "SKIP"
        res.headline = "no sample clip (ffmpeg not found) — pass --video PATH to run"
        return res
    content = [
        {"type": "video_url", "video_url": {"url": _data_url(video, "video/mp4")}},
        {"type": "text", "text": "What is in this video? One sentence."},
    ]
    try:
        r = _post_chat(client, base, model, api_key, content, max_tokens=32)
        res.data.update(status=r.status_code, bytes=len(video))
        if r.status_code == 200:
            res.status = "PASS"
            res.headline = ("video_url data-URI ACCEPTED (informational; DP still chooses "
                            "K-stills for decode determinism — D-02)")
        else:
            res.data["error"] = _err_snippet(r)
            res.status = "SKIP"  # informational: a rejection is a finding, not a WS-A failure
            res.headline = (f"video_url rejected (HTTP {r.status_code}): "
                            f"{res.data['error'][:120]} — likely needs video limit/flags")
        return res
    except Exception as exc:  # noqa: BLE001
        res.status, res.headline = "SKIP", f"{type(exc).__name__}: {exc}"
        return res


# ---------------------------------------------------------------------------------
def _unreachable_report(base: str, why: str) -> None:
    _banner("ENDPOINT UNREACHABLE — probes NOT run (this is the honest default on a "
            "box with no VL server)")
    print(f"  target : {base}/v1/chat/completions", flush=True)
    print(f"  reason : {why}", flush=True)
    print("\n  To bring one up (E-3(a) — inference/platform own serving), launch the "
          "served model\n  with the video knobs that services/inference/serve_vllm.sh "
          "OMITS today:", flush=True)
    print("""
    vllm serve Qwen/Qwen3-VL-32B-Instruct \\
      --tensor-parallel-size 8 --host 127.0.0.1 --port 8000 \\
      --limit-mm-per-prompt '{"image":16}' \\
      --mm-processor-kwargs '{"max_pixels":16777216,"min_pixels":65536}'

  then re-run this probe against it. Until then, probes (1)-(4) are UNVERIFIED-LIVE;
  the config-derived predictions are in handoff/ws-video-clip-probe.md.""", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="WS-A VL-endpoint capability probe")
    ap.add_argument("--url", default=os.getenv("VIDEO_VLM_URL", "http://127.0.0.1:8000").rstrip("/"))
    ap.add_argument("--model", default=os.getenv("VIDEO_VLM_MODEL", "Qwen/Qwen3-VL-32B-Instruct"))
    ap.add_argument("--api-key", default=os.getenv("VIDEO_VLM_API_KEY", ""))
    ap.add_argument("--timeout", type=float, default=float(os.getenv("VIDEO_VLM_TIMEOUT", "120")))
    ap.add_argument("--width", type=int, default=768)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--frames", default="1,2,4,8,12,16", help="image counts to try for probe 1")
    ap.add_argument("--sweep", default="", help="extra widths for probe 3, e.g. 768,1024,1280")
    ap.add_argument("--image", default="", help="use a real frame instead of the synthetic gradient")
    ap.add_argument("--video", default="", help="sample clip for probe 4 (else ffmpeg-synth or SKIP)")
    ap.add_argument("--probe", default="all", help="all | comma list of 1,2,3,4")
    ap.add_argument("--json", default="", help="write the machine-readable summary here")
    args = ap.parse_args()

    want = {"1", "2", "3", "4"} if args.probe == "all" else set(args.probe.split(","))
    counts = [int(x) for x in args.frames.split(",") if x.strip()]
    sweep = [int(x) for x in args.sweep.split(",") if x.strip()]

    if args.image:
        with open(args.image, "rb") as fh:
            image = fh.read()
        mime = "image/png" if args.image.lower().endswith(".png") else "image/jpeg"
        print(f"image={args.image} bytes={len(image)} (real frame; dims not re-derived)", flush=True)
    else:
        image = _png_gradient(args.width, args.height)
        mime = "image/png"
        print(f"image=synthetic PNG {args.width}x{args.height} bytes={len(image)}", flush=True)

    print(f"target={args.url}  model={args.model}  httpx={httpx.__version__}", flush=True)

    results: list[ProbeResult] = []
    with httpx.Client(timeout=args.timeout) as client:
        ok, why, served = check_reachable(client, args.url, args.api_key)
        if not ok:
            _unreachable_report(args.url, why)
            if args.json:
                with open(args.json, "w") as fh:
                    json.dump({"reachable": False, "reason": why, "target": args.url}, fh, indent=2)
            return 2
        _banner(f"ENDPOINT REACHABLE — {why}")

        if "3" in want:
            _banner("PROBE 3 — usage.prompt_tokens for one frame (factor 32 vs 28 vs clamp)")
            results.append(probe_tokens(client, args.url, args.model, args.api_key,
                                        image, mime, args.width, args.height, sweep))
        if "1" in want:
            _banner("PROBE 1 — N image_url parts in one message (--limit-mm-per-prompt)")
            results.append(probe_multiimage(client, args.url, args.model, args.api_key,
                                            image, mime, counts))
        if "2" in want:
            _banner("PROBE 2 — response_format json_schema / json_object (guided decoding)")
            results.append(probe_guided(client, args.url, args.model, args.api_key, image, mime))
        if "4" in want:
            _banner("PROBE 4 — video_url data-URI (informational, O-4)")
            results.append(probe_video(client, args.url, args.model, args.api_key,
                                       args.video or None))

    _banner("SUMMARY")
    print(f"  endpoint: {args.url}  served: {served}", flush=True)
    for r in results:
        print(r.line(), flush=True)

    if args.json:
        with open(args.json, "w") as fh:
            json.dump({"reachable": True, "target": args.url, "model": args.model,
                       "served": served,
                       "results": [{"name": r.name, "status": r.status,
                                    "headline": r.headline, "data": r.data}
                                   for r in results]}, fh, indent=2)
        print(f"\n  wrote {args.json}", flush=True)

    failed = [r for r in results if r.status == "FAIL"]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
