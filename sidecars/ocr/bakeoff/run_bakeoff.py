#!/usr/bin/env python3
# =============================================================================
# run_bakeoff.py -- O-2 bake-off runner.
#
#   Pipeline per (frame, width):  2x PNG
#     -> scale to W  -> H.264 CRF 28 (yuv420p 4:2:0)  -> extract JPEG (-q:v 2)
#     -> OCR (the SAME PPOCREngine the sidecar serves)  -> score.
#
#   The encode chain is faithful to production: recording captures at
#   min(1728, iw) and encodes H.264; DP's Pass B extracts a JPEG at -q:v 2
#   (D-04). yuv420p 4:2:0 chroma subsampling is included because it is a real
#   degrader of coloured text on coloured backgrounds.
#
#   Arms:  ppocr@1728, ppocr@1152  -- run here (the det+rec ONNX the sidecar
#          serves). The VLM arms (Qwen3-VL-32B@1536, Qwen2.5-VL-32B@1536) O-2
#          also names are DECLARED but NOT RUN: they need the GPU serving
#          endpoint (E-3(a)), which serve_vllm.sh notes is text-only-MVP and
#          often down. Recorded as status="not_run" so the report is honest.
#
#   Writes results.json. REPORT.md carries the verdict (authored, not generated).
# =============================================================================
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))  # import the sidecar's app.py
import score as S  # noqa: E402

CRF = int(os.getenv("BAKEOFF_CRF", "28"))
WIDTHS = [1728, 1152]
CACHE = os.path.join(HERE, "crf_cache")
SRC_2X_W = 3456  # gen_corpus canvas width; focus boxes are in these coords


def encode_crf(png: str, width: int) -> bytes:
    """2x PNG -> scale W -> H.264 CRF -> extract one JPEG. Cached by (id,width,crf)."""
    os.makedirs(CACHE, exist_ok=True)
    stem = os.path.splitext(os.path.basename(png))[0]
    out_jpg = os.path.join(CACHE, f"{stem}@{width}.crf{CRF}.jpg")
    if os.path.isfile(out_jpg):
        return open(out_jpg, "rb").read()
    mp4 = out_jpg + ".mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", png,
         "-t", "1", "-r", "5", "-c:v", "libx264", "-crf", str(CRF),
         "-pix_fmt", "yuv420p", "-vf", f"scale={width}:-2", mp4],
        check=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-sseof", "-0.2", "-i", mp4,
         "-frames:v", "1", "-q:v", "2", out_jpg],
        check=True,
    )
    os.remove(mp4)
    return open(out_jpg, "rb").read()


def run_ppocr_arm(gt: list[dict], width: int) -> dict:
    from app import PPOCREngine

    eng = PPOCREngine()
    per_frame = []
    per_arch: dict = {}
    t0 = time.time()
    for i, g in enumerate(gt):
        png = os.path.join(HERE, g["png"])
        jpeg = encode_crf(png, width)
        regions = eng.read(jpeg)
        rec = S.score_recall(g["key_strings"], " ".join(r["text"] for r in regions))
        scale = width / SRC_2X_W
        fbox = [v * scale for v in g["focus_bbox_2x"]]
        cer = S.score_cer(g["focus_text"], regions, fbox)
        row = {
            "id": g["id"], "archetype": g["archetype"], "n_keys": rec["n_keys"],
            "recall_substring": rec["substring"], "recall_fuzzy": rec["fuzzy"],
            "cer": cer, "n_regions": len(regions),
        }
        per_frame.append(row)
        per_arch.setdefault(g["archetype"], []).append(row)
        if (i + 1) % 40 == 0:
            print(f"    ppocr@{width}: {i+1}/{len(gt)} frames", flush=True)

    def agg(rows):
        subs = [r["recall_substring"] for r in rows if r["recall_substring"] is not None]
        fuz = [r["recall_fuzzy"] for r in rows if r["recall_fuzzy"] is not None]
        cers = [r["cer"] for r in rows if r["cer"] is not None]
        # macro = mean of per-frame ratios (one vote per frame).
        # micro = key-pooled = keys-recalled / total-keys (weights dense frames by
        # their key count). Both are reported so a dense-frame skew is visible.
        tk = sum(r["n_keys"] for r in rows if r["recall_substring"] is not None)
        psub = sum(r["recall_substring"] * r["n_keys"] for r in rows if r["recall_substring"] is not None)
        pfuz = sum(r["recall_fuzzy"] * r["n_keys"] for r in rows if r["recall_fuzzy"] is not None)
        return {
            "n_frames": len(rows),
            "n_keys": tk,
            "recall_substring": sum(subs) / len(subs) if subs else None,      # macro
            "recall_fuzzy": sum(fuz) / len(fuz) if fuz else None,             # macro
            "recall_substring_micro": psub / tk if tk else None,
            "recall_fuzzy_micro": pfuz / tk if tk else None,
            "cer": sum(cers) / len(cers) if cers else None,
        }

    return {
        "engine": eng.engine, "width": width,
        "model_sha_det": eng.model_sha_det, "model_sha_rec": eng.model_sha_rec,
        "ort_version": eng.ort_version, "ep": eng.ep, "crf": CRF,
        "wall_s": round(time.time() - t0, 1),
        "overall": agg(per_frame),
        "by_archetype": {a: agg(rows) for a, rows in sorted(per_arch.items())},
        "per_frame": per_frame,
    }


def main() -> None:
    gt = json.load(open(os.path.join(HERE, "ground_truth.json")))
    print(f"[bakeoff] {len(gt)} frames, CRF={CRF}, widths={WIDTHS}", flush=True)
    arms = []
    for w in WIDTHS:
        print(f"[bakeoff] running ppocr@{w} ...", flush=True)
        arms.append(run_ppocr_arm(gt, w))
    # Declared-but-not-run VLM arms (need E-3(a) serving endpoint).
    for name, w in (("Qwen3-VL-32B", 1536), ("Qwen2.5-VL-32B", 1536)):
        arms.append({
            "engine": name, "width": w, "status": "not_run",
            "reason": "requires the GPU VLM serving endpoint (E-3(a)); "
                      "serve_vllm.sh is text-only-MVP and often down in the learn loop.",
        })
    results = {
        "gate": {"recall_min": 0.85, "cer_max": 0.10, "recall_metric": "key-string >=5 char, lenient"},
        "corpus": {
            "kind": "synthetic-proxy",
            "frames": len(gt),
            "note": "deterministic macOS-UI mocks, 13pt-equivalent body text, "
                    "real x264 CRF encode. NOT the 200 hand-labelled real frames O-2 requires.",
        },
        "arms": arms,
    }
    out = os.path.join(HERE, "results.json")
    json.dump(results, open(out, "w"), indent=1)

    # Console summary. Gate on MICRO substring recall (the literal "fraction of
    # >=5-char key strings recalled, lenient"); macro + fuzzy shown for context.
    print("\n=== O-2 bake-off summary (gate: key-string recall >= 0.85 [micro,lenient], CER <= 0.10) ===")
    print(f"{'arm':20s} {'sub-micro':>10s} {'sub-macro':>10s} {'fuzzy-mic':>10s} {'CER':>7s} {'verdict':>8s}")
    for a in arms:
        if a.get("status") == "not_run":
            print(f"{a['engine']+'@'+str(a['width']):20s} {'-- not run --':>10s}")
            continue
        o = a["overall"]
        gate = o["recall_substring_micro"] >= 0.85 and (o["cer"] is None or o["cer"] <= 0.10)
        print(f"{a['engine']+'@'+str(a['width']):20s} "
              f"{o['recall_substring_micro']:10.3f} {o['recall_substring']:10.3f} "
              f"{o['recall_fuzzy_micro']:10.3f} {(o['cer'] or 0):7.3f} {'PASS' if gate else 'FAIL':>8s}")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
