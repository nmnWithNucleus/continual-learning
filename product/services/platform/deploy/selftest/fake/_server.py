#!/usr/bin/env python3
"""Stdlib-only fake service for the platform self-test.

NOT a real service — it exists solely to exercise run_all.sh's orchestration
(ordered start, /health gating, PID tracking, --status, --stop) without needing
the sibling app code or any pip install. Binds to $HOST:$PORT.

Roles:
  * every role serves  GET /health -> 200 {"status":"ok","service":<role>}
  * role "input" also serves POST /api/turn -> a minimal but VALID C9 stream:
        <answer text bytes> <U+001E> <JSON end frame>
    so the self-test can prove a turn streams end-to-end through the glue.
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROLE = "unknown"
US = b"\x1e"  # U+001E record separator, per the C9 wire format


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body=b"", ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.split("?", 1)[0] == "/health":
            self._send(200, json.dumps({"status": "ok", "service": ROLE}).encode())
        else:
            self._send(404, b'{"error":"not found"}')

    def do_POST(self):
        if ROLE == "input" and self.path.split("?", 1)[0] == "/api/turn":
            self._stream_c9()
        else:
            self._send(404, b'{"error":"not found"}')

    def _stream_c9(self):
        # Drain the request body (ignored by the fake).
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length:
            self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.end_headers()
        for chunk in ("Hello ", "from ", "the ", "fake ", "serve-loop."):
            self.wfile.write(chunk.encode())
            self.wfile.flush()
        end_frame = {
            "contract": "C9",
            "version": "0",
            "turn_id": "selftest-turn",
            "model_id": os.environ.get("MODEL_ID", "mock"),
            "adapter": "base",
            "usage": {"prompt_tokens": 3, "output_tokens": 5},
            "finished": True,
        }
        self.wfile.write(US)
        self.wfile.write(json.dumps(end_frame).encode())
        self.wfile.flush()

    def log_message(self, *args):  # keep the fake quiet
        return


def main():
    global ROLE
    ROLE = sys.argv[sys.argv.index("--role") + 1] if "--role" in sys.argv else "unknown"
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "0"))
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"[fake:{ROLE}] listening on {host}:{port}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
