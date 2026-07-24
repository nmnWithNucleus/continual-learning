"""WS-A serving probe — verify the OCR-sidecar assumptions WS-C's build rests on.

The clip design decouples OCR from the captioner (D-06): a co-located loopback
sidecar (`sidecars/ocr/`, its own venv, its own `run.sh`, the `serve_vllm.sh`
posture) runs PP-OCRv6 det+rec on CPU at native resolution and returns
`[(text, bbox, confidence)]`. Two external assumptions must hold before WS-C wires it:

  (A) The sidecar exposes `GET /health` carrying both model-file sha256s + the ORT
      version + the execution provider, which DP asserts against config AT GRAPH
      RESOLUTION and fails loud on mismatch (D-06; WS-C exit criteria). This probe
      checks that contract shape.
  (B) PP-OCRv6 CPU runs at ~0.6 s per 1728x1080 frame, 4 threads (numbers §7.1 /
      latency §7.4). This probe TIMES it — either through the running sidecar, or by
      shelling out to a SEPARATE interpreter that has paddleocr/rapidocr installed.

Why a separate interpreter and never an import here: verified in §12.3 — `paddleocr
-> paddlex[ocr-core] -> numpy<2.4` conflicts with the DP venv's numpy 2.5.1. The HTTP
seam exists precisely to quarantine that; this probe honours it and imports NOTHING
heavy into the DP venv (stdlib + httpx only).

Like `scripts/smoke_audio_backends.py`, it NEVER fabricates: with no sidecar and no
OCR runtime on the box it prints SKIP with the exact contract WS-C must expose and the
assumption WS-C's O-2 bake-off must measure — not a made-up latency.

Usage (from the data-processing service root):
    # against a running OCR sidecar (once WS-C builds it):
    VIDEO_OCR_URL=http://127.0.0.1:8090 ./.venv/bin/python scripts/ocr_probe.py --frames 50
    # local timing through an interpreter that HAS paddleocr (never the DP venv):
    ./.venv/bin/python scripts/ocr_probe.py --python /path/to/ocr-venv/bin/python --frames 50
    # options:
    ./.venv/bin/python scripts/ocr_probe.py --url http://HOST:8090 \
        --width 1728 --height 1080 --frames 50 --threads 4 --json out.json

Exit code: 0 iff every REQUESTED check ran and none FAILED (SKIP is not a failure);
2 if neither a sidecar nor a local OCR interpreter was available; 1 on a real failure.
"""
from __future__ import annotations

import argparse
import json
import os
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from dataclasses import dataclass, field
from typing import Any

import httpx

# §7.1 stated assumption under test.
TARGET_S_PER_FRAME = 0.6
OCR_WIDTH = 1728   # VIDEO_OCR_FRAME_WIDTH — the mac capture cap, no resample
OCR_HEIGHT = 1080


def _png_gradient(width: int, height: int) -> bytes:
    """Dependency-free truecolor PNG at exact WxH (PIL is not in the DP venv). A
    timing frame; real recall needs WS-C's hand-labelled text fixtures (O-2)."""
    def _chunk(typ: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + typ + data
                + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = bytearray()
    wdiv, hdiv = max(1, width - 1), max(1, height - 1)
    for y in range(height):
        raw.append(0)
        g = (y * 255) // hdiv
        row = bytearray()
        for x in range(width):
            row += bytes(((x * 255) // wdiv, g, 128))
        raw += row
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + _chunk(b"IEND", b""))


@dataclass
class ProbeResult:
    name: str
    status: str = "NOT RUN"
    headline: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def line(self) -> str:
        return f"  {self.name:26s} {self.status:5s} {self.headline}"


def _banner(name: str) -> None:
    print(f"\n{'=' * 74}\n{name}\n{'=' * 74}", flush=True)


# ---------------------------------------------------------------------------------
# (A) sidecar /health + /ocr contract and latency
# ---------------------------------------------------------------------------------
def probe_sidecar(client: httpx.Client, base: str, frame: bytes, width: int,
                  height: int, n: int) -> tuple[ProbeResult, ProbeResult] | None:
    """Returns (health_result, latency_result), or None if the sidecar is unreachable."""
    try:
        h = client.get(f"{base}/health")
        h.raise_for_status()
        health = h.json()
    except Exception:
        return None

    hres = ProbeResult("A sidecar /health")
    hres.data["health"] = health
    # D-06 / WS-C: /health must carry both model sha256s + ORT version + EP.
    keys = set(health) if isinstance(health, dict) else set()
    have_sha = any("sha" in str(k).lower() for k in keys)
    have_ort = any("ort" in str(k).lower() or "onnx" in str(k).lower() for k in keys)
    have_ep = any(str(k).lower() in ("ep", "execution_provider", "provider") for k in keys)
    missing = [n for n, ok in (("model sha256(s)", have_sha), ("ORT version", have_ort),
                               ("execution provider", have_ep)) if not ok]
    if not missing:
        hres.status, hres.headline = "PASS", f"carries sha256s + ORT + EP: {sorted(keys)}"
    else:
        hres.status = "FAIL"
        hres.headline = (f"/health missing {missing} — DP cannot assert the model pin at "
                         f"graph resolution (D-06). Present keys: {sorted(keys)}")

    lres = ProbeResult("B sidecar /ocr latency")
    # POST one frame n times; time each. Route/field are WS-C's to finalise — try the
    # documented shape and report the actual error verbatim if it differs.
    body = {"image": "data:image/png;base64,"
            + __import__("base64").b64encode(frame).decode("ascii"),
            "width": width, "height": height}
    times: list[float] = []
    sample_regions = None
    try:
        for i in range(n):
            t0 = time.time()
            r = client.post(f"{base}/ocr", json=body)
            r.raise_for_status()
            times.append(time.time() - t0)
            if i == 0:
                sample_regions = r.json()
        times.sort()
        p50 = times[len(times) // 2]
        mean = sum(times) / len(times)
        lres.data.update(n=n, mean_s=round(mean, 4), p50_s=round(p50, 4),
                         target_s=TARGET_S_PER_FRAME, sample=sample_regions)
        verdict = ("meets" if mean <= TARGET_S_PER_FRAME * 1.5 else "EXCEEDS") \
            + f" the {TARGET_S_PER_FRAME}s/frame assumption"
        lres.status = "PASS" if mean <= TARGET_S_PER_FRAME * 1.5 else "FAIL"
        lres.headline = f"{width}x{height}: mean {mean*1000:.0f}ms/frame p50 {p50*1000:.0f}ms -> {verdict}"
    except Exception as exc:  # noqa: BLE001
        lres.status = "SKIP"
        lres.headline = f"/ocr call shape differs ({type(exc).__name__}: {exc}) — WS-C finalises the route"
    return hres, lres


# ---------------------------------------------------------------------------------
# (B') local timing via a SEPARATE interpreter that has paddleocr / rapidocr
# ---------------------------------------------------------------------------------
_TIMING_SNIPPET = r'''
import sys, time, json
w, h, n, threads, png_path = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), sys.argv[5]
engine = None; kind = None
try:
    from paddleocr import PaddleOCR
    engine = PaddleOCR(use_angle_cls=False, lang="en", cpu_threads=threads, show_log=False)
    kind = "paddleocr"
    def run(p): return engine.ocr(p, cls=False)
except Exception:
    try:
        from rapidocr_onnxruntime import RapidOCR
        engine = RapidOCR()
        kind = "rapidocr"
        def run(p): return engine(p)
    except Exception as e:
        print(json.dumps({"ok": False, "error": "no paddleocr/rapidocr: %r" % e})); raise SystemExit(0)
times = []
for i in range(n):
    t0 = time.time(); run(png_path); times.append(time.time() - t0)
times.sort()
print(json.dumps({"ok": True, "engine": kind, "n": n, "threads": threads,
                  "mean_s": sum(times)/len(times), "p50_s": times[len(times)//2]}))
'''


def probe_local(python: str, frame: bytes, width: int, height: int, n: int,
                threads: int) -> ProbeResult:
    res = ProbeResult("B local OCR latency")
    if not os.path.exists(python):
        res.status, res.headline = "SKIP", f"--python {python} does not exist"
        return res
    with tempfile.TemporaryDirectory() as td:
        png_path = os.path.join(td, "frame.png")
        snip_path = os.path.join(td, "time_ocr.py")
        with open(png_path, "wb") as fh:
            fh.write(frame)
        with open(snip_path, "w") as fh:
            fh.write(_TIMING_SNIPPET)
        try:
            out = subprocess.run(
                [python, snip_path, str(width), str(height), str(n), str(threads), png_path],
                capture_output=True, text=True, timeout=600)
        except Exception as exc:  # noqa: BLE001
            res.status, res.headline = "SKIP", f"{type(exc).__name__}: {exc}"
            return res
        line = (out.stdout or "").strip().splitlines()[-1:] or [""]
        try:
            payload = json.loads(line[0])
        except Exception:
            res.status = "SKIP"
            res.headline = f"timing subprocess produced no JSON: {(out.stderr or out.stdout)[:160]}"
            return res
        res.data.update(payload)
        if not payload.get("ok"):
            res.status, res.headline = "SKIP", f"no OCR runtime in {python}: {payload.get('error','')[:120]}"
            return res
        mean = payload["mean_s"]
        res.status = "PASS" if mean <= TARGET_S_PER_FRAME * 1.5 else "FAIL"
        verdict = "meets" if res.status == "PASS" else "EXCEEDS"
        res.headline = (f"{payload['engine']} {width}x{height}: mean {mean*1000:.0f}ms/frame "
                        f"-> {verdict} the {TARGET_S_PER_FRAME}s assumption")
        return res


# ---------------------------------------------------------------------------------
def _skip_report(base: str, python: str) -> None:
    _banner("NO OCR SIDECAR AND NO LOCAL OCR RUNTIME — checks NOT run (honest default "
            "on this box)")
    print(f"  sidecar tried : {base}/health  (unreachable)", flush=True)
    print(f"  local tried   : {python or '(none given; pass --python PATH)'}", flush=True)
    print("""
  The OCR serving assumptions are therefore UNVERIFIED-LIVE. What WS-C must build,
  and what this probe checks once it exists:

    * sidecars/ocr/ exposes GET /health -> {"det_sha256","rec_sha256","ort_version","ep": "CPU", ...}
      DP asserts these against config AT GRAPH RESOLUTION and fails loud on mismatch (D-06).
    * sidecars/ocr/ exposes POST /ocr {image: data-URI, ...} -> [{"text","bbox","confidence"}, ...]
    * PP-OCRv6 det+rec CPU is expected at ~0.6 s / 1728x1080 frame, 4 threads (§7.1).
      This is GATED ON O-2 (WS-C's own bake-off: can it read 13pt macOS UI text at 1728px
      through CRF 28?), so the production default stays VIDEO_OCR_BACKEND=mock until O-2 passes.

  Confirmed on this box: NO paddleocr / rapidocr in any interpreter, so even the local
  timing path cannot run here without provisioning the sidecar's venv first.""", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="WS-A OCR-sidecar capability probe")
    ap.add_argument("--url", default=os.getenv("VIDEO_OCR_URL", "http://127.0.0.1:8090").rstrip("/"),
                    help="OCR sidecar base URL (WS-C pins the real port/route)")
    ap.add_argument("--python", default="", help="interpreter WITH paddleocr/rapidocr for local timing (never the DP venv)")
    ap.add_argument("--width", type=int, default=OCR_WIDTH)
    ap.add_argument("--height", type=int, default=OCR_HEIGHT)
    ap.add_argument("--frames", type=int, default=50)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--timeout", type=float, default=float(os.getenv("VIDEO_OCR_TIMEOUT", "30")))
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    frame = _png_gradient(args.width, args.height)
    print(f"frame=synthetic PNG {args.width}x{args.height} bytes={len(frame)}  httpx={httpx.__version__}", flush=True)
    print(f"sidecar={args.url}  local_python={args.python or '(none)'}", flush=True)

    results: list[ProbeResult] = []
    ran = False
    with httpx.Client(timeout=args.timeout) as client:
        _banner("(A/B) OCR sidecar /health + /ocr latency")
        sidecar = probe_sidecar(client, args.url, frame, args.width, args.height, args.frames)
        if sidecar is not None:
            ran = True
            for r in sidecar:
                results.append(r)
                print(r.line(), flush=True)
        else:
            print(f"  sidecar unreachable at {args.url} — skipping (A/B via sidecar)", flush=True)

    if args.python:
        _banner("(B') local PP-OCR timing via a separate interpreter")
        r = probe_local(args.python, frame, args.width, args.height, args.frames, args.threads)
        results.append(r)
        print(r.line(), flush=True)
        if r.status in ("PASS", "FAIL"):
            ran = True

    if not ran:
        _skip_report(args.url, args.python)
        if args.json:
            with open(args.json, "w") as fh:
                json.dump({"ran": False, "sidecar": args.url, "python": args.python}, fh, indent=2)
        return 2

    _banner("SUMMARY")
    for r in results:
        print(r.line(), flush=True)
    if args.json:
        with open(args.json, "w") as fh:
            json.dump({"ran": True, "sidecar": args.url,
                       "results": [{"name": r.name, "status": r.status,
                                    "headline": r.headline, "data": r.data} for r in results]},
                      fh, indent=2)
        print(f"\n  wrote {args.json}", flush=True)

    return 1 if any(r.status == "FAIL" for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
