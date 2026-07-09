#!/usr/bin/env python3
"""Stdlib-only fake service for the LEARN-loop platform self-test.

NOT a real service — it exists solely to exercise run_learn.sh's orchestration
(ordered start, /health gating, PID tracking, --status, --smoke, --stop) without
needing the sibling app code (recording / data-processing / storage /raw+/context
are built by parallel workstreams) or any pip install. Binds to $HOST:$PORT.

Roles + the health shapes run_learn.sh's siblings promise:
  * storage         GET /health -> 200 {"ok": true}
  * data-processing GET /health -> 200 {"ok": true, "asr_backend": <ASR_BACKEND>}
  * recording       GET /health -> 200 {"ok": true}
                    POST /capture/run {source, chunk_seconds, dp_url, storage_url}
                        -> 200 {ok, stream_id, chunks:[{sequence, chunk_id,
                                 record_id}...], record_ids:[...]}

The fake recording SYNTHESIZES the capture result: it opens `source` (the sample
WAV the smoke generated) with the stdlib `wave` module, computes how many
`chunk_seconds` chunks it carves into, and returns a dense zero-based chunk list
with a record_id per chunk — enough to prove run_learn.sh passes the right body
and parses record_ids back out. It does NOT run real ASR or contact the peers;
that is the parallel siblings' job, verified by the integrator on the real loop.
"""
import contextlib
import json
import os
import sys
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROLE = "unknown"


def _n_chunks(source: str, chunk_seconds: int) -> int:
    """Chunks the source WAV carves into (dense, zero-based). Falls back to 3."""
    if chunk_seconds <= 0:
        chunk_seconds = 5
    try:
        with contextlib.closing(wave.open(source, "rb")) as w:
            seconds = w.getnframes() / float(w.getframerate() or 1)
        return max(1, -(-int(seconds * 1000) // (chunk_seconds * 1000)))  # ceil
    except Exception:
        return 3


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body=b"", ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.split("?", 1)[0] == "/health":
            health = {"ok": True}
            if ROLE == "data-processing":
                health["asr_backend"] = os.environ.get("ASR_BACKEND", "mock")
            self._send(200, json.dumps(health).encode())
        else:
            self._send(404, b'{"error":"not found"}')

    def do_POST(self):
        if ROLE == "recording" and self.path.split("?", 1)[0] == "/capture/run":
            self._capture_run()
        else:
            self._send(404, b'{"error":"not found"}')

    def _capture_run(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            req = json.loads(raw.decode() or "{}")
        except Exception:
            req = {}
        source = req.get("source", "")
        chunk_seconds = int(req.get("chunk_seconds", 5) or 5)
        n = _n_chunks(source, chunk_seconds)

        stream_id = "01SELFTESTSTREAM0000000000"
        chunks = []
        for seq in range(n):
            chunk_id = f"01SELFTESTCHUNK{seq:010d}"
            # record_id is deterministic on (chunk_id, pipeline_version) in the
            # real C2; here just a stable synthetic stand-in per chunk.
            record_id = f"rec-selftest-{seq:04d}"
            chunks.append({
                "sequence": seq,
                "chunk_id": chunk_id,
                "record_id": record_id,
            })
        resp = {
            "ok": True,
            "stream_id": stream_id,
            "chunks": chunks,
            "record_ids": [c["record_id"] for c in chunks],
        }
        self._send(200, json.dumps(resp).encode())

    def log_message(self, *args):  # keep the fake quiet
        return


def main():
    global ROLE
    ROLE = sys.argv[sys.argv.index("--role") + 1] if "--role" in sys.argv else "unknown"
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "0"))
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"[fake-learn:{ROLE}] listening on {host}:{port}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
