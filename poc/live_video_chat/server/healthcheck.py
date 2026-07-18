#!/usr/bin/env python3
# =============================================================================
# WS1 healthcheck — confirms the vLLM server answers a *streaming video*
# request end-to-end, exactly as Contract A specifies (file:// video_url +
# text part, stream:true, extra_body.mm_processor_kwargs).
#
# Usage:
#   python healthcheck.py                 # uses the bundled sample MP4
#   python healthcheck.py /abs/clip.mp4 "your question"
#
# Exit 0 if a grounded streamed answer comes back, non-zero otherwise.
# Stdlib-only (urllib) so it runs in any env.
# =============================================================================
import json, os, sys, time, urllib.request

BASE = os.environ.get("VLLM_BASE", "http://127.0.0.1:8000")
MODEL = "Qwen/Qwen3-VL-32B-Instruct"
HERE = os.path.dirname(os.path.abspath(__file__))
# Must live under the server's --allowed-local-media-path (=/mnt/localssd). serve.sh
# seeds this copy; falls back to the in-repo sample if absent.
DEFAULT_MP4 = "/mnt/localssd/live_video_chat/clips/sample_counter.mp4"
if not os.path.isfile(DEFAULT_MP4):
    DEFAULT_MP4 = os.path.join(HERE, "samples", "sample_counter.mp4")


def health():
    try:
        with urllib.request.urlopen(BASE + "/health", timeout=5) as r:
            return r.status == 200
    except Exception as e:
        print(f"[healthcheck] /health failed: {e}")
        return False


def stream_video(mp4_path, question):
    assert os.path.isfile(mp4_path), f"sample not found: {mp4_path}"
    url = f"file://{os.path.abspath(mp4_path)}"
    payload = {
        "model": MODEL,
        "stream": True,
        "max_tokens": 256,
        "temperature": 0.2,
        "messages": [
            {"role": "system",
             "content": "You are a helpful assistant. Answer in plain English about what you see."},
            {"role": "user", "content": [
                {"type": "video_url", "video_url": {"url": url}},
                {"type": "text", "text": question},
            ]},
        ],
        # Contract A: per-request mm_processor_kwargs (fps WITH the video).
        "extra_body": {"mm_processor_kwargs": {"fps": 2.0, "max_pixels": 262144, "min_pixels": 131072}},
    }
    # vLLM accepts extra_body keys merged at top level for the OpenAI route.
    body = dict(payload)
    extra = body.pop("extra_body")
    body.update(extra)

    req = urllib.request.Request(
        BASE + "/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    print(f"[healthcheck] POST {BASE}/v1/chat/completions  video={url}")
    print(f"[healthcheck] question: {question!r}")
    t0 = time.time()
    ttft = None
    text = []
    with urllib.request.urlopen(req, timeout=180) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            delta = chunk.get("choices", [{}])[0].get("delta", {})
            piece = delta.get("content")
            if piece:
                if ttft is None:
                    ttft = time.time() - t0
                text.append(piece)
                sys.stdout.write(piece)
                sys.stdout.flush()
    total = time.time() - t0
    answer = "".join(text)
    print(f"\n[healthcheck] TTFT={ttft:.2f}s  total={total:.2f}s  chars={len(answer)}"
          if ttft else "\n[healthcheck] NO TOKENS RECEIVED")
    return answer


def main():
    mp4 = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MP4
    question = sys.argv[2] if len(sys.argv) > 2 else (
        "What is in this short video? Describe the background color, any shapes "
        "that move, and read any numbers you see.")
    if not health():
        print("[healthcheck] FAIL: server not healthy")
        sys.exit(2)
    print("[healthcheck] /health OK")
    answer = stream_video(mp4, question)
    if not answer.strip():
        print("[healthcheck] FAIL: empty answer")
        sys.exit(3)
    # Light grounding check against the known sample content (blue bg, red shape, digits).
    low = answer.lower()
    hits = [w for w in ("blue", "red", "number", "digit", "count", "1", "2", "3") if w in low]
    print(f"[healthcheck] grounding hits: {hits}")
    print("[healthcheck] PASS" if hits else "[healthcheck] WARN: answer not obviously grounded")
    sys.exit(0)


if __name__ == "__main__":
    main()
