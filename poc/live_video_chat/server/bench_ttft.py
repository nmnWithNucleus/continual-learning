#!/usr/bin/env python3
# TTFT benchmark for the WS1 vLLM server. Sends video clips of increasing
# duration (=> increasing input tokens) and records TTFT + prompt_tokens.
# Run a warmup first so CUDA-graph / encoder caches don't skew the first number.
import json, os, time, urllib.request

BASE = os.environ.get("VLLM_BASE", "http://127.0.0.1:8000")
MODEL = "Qwen/Qwen3-VL-32B-Instruct"
CLIPDIR = "/mnt/localssd/live_video_chat/clips"
Q = "Briefly: what numbers appear in this video and what color is the background?"
# High max_pixels so source resolution (not the pixel cap) drives token count;
# the 30s duration + num_frames=60 server cap fixes the frame count.
MMK = {"fps": 2.0, "max_pixels": 1048576, "min_pixels": 4096}

# Token count is driven by source RESOLUTION here (frames pinned at 60 by the
# server's --media-io-kwargs num_frames). 256px->~2K, 512px->~8K, 768px->~16K.
CLIPS = [
    ("~2K  (256px,30s)", f"{CLIPDIR}/res_lo.mp4"),
    ("~8K  (512px,30s)", f"{CLIPDIR}/res_mid.mp4"),
    ("~16K (768px,30s)", f"{CLIPDIR}/res_768.mp4"),
]


def prompt_tokens(path):
    body = {"model": MODEL, "stream": False, "max_tokens": 1,
            "messages": [{"role": "user", "content": [
                {"type": "video_url", "video_url": {"url": f"file://{path}"}},
                {"type": "text", "text": Q}]}],
            "mm_processor_kwargs": MMK}
    req = urllib.request.Request(BASE + "/v1/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.load(r)["usage"]["prompt_tokens"]


def ttft(path, salt=""):
    # `salt` perturbs the text so the video prefill is recomputed each run
    # (prefix cache would otherwise make a repeat request near-instant and
    # misreport TTFT). The video tokens dominate, so this is a true prefill TTFT.
    body = {"model": MODEL, "stream": True, "max_tokens": 64, "temperature": 0.0,
            "messages": [{"role": "user", "content": [
                {"type": "video_url", "video_url": {"url": f"file://{path}"}},
                {"type": "text", "text": Q + salt}]}],
            "mm_processor_kwargs": MMK}
    req = urllib.request.Request(BASE + "/v1/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time(); first = None; n = 0
    with urllib.request.urlopen(req, timeout=180) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            d = line[5:].strip()
            if d == "[DONE]":
                break
            try:
                c = json.loads(d)
            except json.JSONDecodeError:
                continue
            piece = c.get("choices", [{}])[0].get("delta", {}).get("content")
            if piece:
                if first is None:
                    first = time.time() - t0
                n += 1
    return first, time.time() - t0, n


def main():
    print(f"Warmup ({CLIPS[0][1]}) ...")
    ttft(CLIPS[0][1])  # warm CUDA graphs / encoder
    print(f"\n{'label':<18}{'prompt_tok':>11}{'TTFT(s)':>10}{'total(s)':>10}{'out_tok':>9}")
    print("-" * 58)
    rows = []
    for label, path in CLIPS:
        if not os.path.isfile(path):
            print(f"{label:<18}  MISSING {path}")
            continue
        pt = prompt_tokens(path)
        # 3 runs with a unique salt each -> forces real prefill; take median TTFT.
        samples = []
        for k in range(3):
            f, t, n = ttft(path, salt=f" run{k}-{time.time()}")
            if f is not None:
                samples.append((f, t, n))
        samples.sort()
        med = samples[len(samples) // 2]
        print(f"{label:<18}{pt:>11}{med[0]:>10.2f}{med[1]:>10.2f}{med[2]:>9}")
        rows.append((label, pt, med[0], med[1], med[2]))
    print("-" * 58)
    print("note: each run uses a unique text salt -> TTFT reflects true cold prefill")
    print("      (no prefix-cache hit). Video tokens dominate the prefill cost.")


if __name__ == "__main__":
    main()
