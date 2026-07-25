#!/usr/bin/env python3
# =============================================================================
# ablate_crf.py -- isolate the CRF-28 codec's contribution to OCR degradation.
#
#   O-2 asks specifically about reading text "through CRF 28". This measures the
#   same corpus at 1728 px BOTH ways -- raw (PNG downscaled, no video codec) and
#   through H.264 CRF 28 -- so the report can attribute any loss to the codec vs
#   the downscale. Reuses the crf_cache built by run_bakeoff.py.
# =============================================================================
from __future__ import annotations

import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import score as S  # noqa: E402
from app import PPOCREngine  # noqa: E402

WIDTH = 1728


def raw_jpeg_at(png: str, width: int) -> bytes:
    from PIL import Image

    img = Image.open(png).convert("RGB")
    h = round(img.height * width / img.width)
    img = img.resize((width, h), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)  # near-lossless: isolates the codec
    return buf.getvalue()


def crf_jpeg_at(stem: str, width: int, crf: int = 28) -> bytes | None:
    p = os.path.join(HERE, "crf_cache", f"{stem}@{width}.crf{crf}.jpg")
    return open(p, "rb").read() if os.path.isfile(p) else None


def mean_recall(gt, eng, get_bytes):
    subs, fuz = [], []
    for g in gt:
        b = get_bytes(g)
        if b is None:
            continue
        regs = eng.read(b)
        r = S.score_recall(g["key_strings"], " ".join(x["text"] for x in regs))
        if r["substring"] is not None:
            subs.append(r["substring"])
            fuz.append(r["fuzzy"])
    return (sum(subs) / len(subs), sum(fuz) / len(fuz), len(subs))


def main():
    gt = json.load(open(os.path.join(HERE, "ground_truth.json")))
    eng = PPOCREngine()
    raw = mean_recall(gt, eng, lambda g: raw_jpeg_at(os.path.join(HERE, g["png"]), WIDTH))
    crf = mean_recall(gt, eng, lambda g: crf_jpeg_at(g["id"], WIDTH))
    print(f"raw@{WIDTH}    (no codec): recall_sub {raw[0]:.3f}  fuzzy {raw[1]:.3f}  (n={raw[2]})")
    print(f"crf28@{WIDTH} (H.264):    recall_sub {crf[0]:.3f}  fuzzy {crf[1]:.3f}  (n={crf[2]})")
    print(f"CRF-28 codec cost:        d_sub {crf[0]-raw[0]:+.3f}  d_fuzzy {crf[1]-raw[1]:+.3f}")
    out = {
        "width": WIDTH,
        "raw": {"recall_substring": raw[0], "recall_fuzzy": raw[1], "n": raw[2]},
        "crf28": {"recall_substring": crf[0], "recall_fuzzy": crf[1], "n": crf[2]},
    }
    json.dump(out, open(os.path.join(HERE, "ablation_crf.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
