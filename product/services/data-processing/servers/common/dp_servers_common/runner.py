"""Process entrypoint helper: `serve(backend)` is each server.py's whole main.

Reads ONLY operational env (L4): DP_SERVER_HOST (default 127.0.0.1 — loopback,
the prior OCR service's posture), DP_SERVER_PORT (required), DP_SERVER_LOG_LEVEL. A load
failure exits the process non-zero (fail loud) so the supervisor sees a crash,
not a zombie replica.
"""
from __future__ import annotations

import logging
import os
import sys
import threading

import uvicorn

from .app import build_app
from .backend import ModelBackend


def serve(backend: ModelBackend) -> None:
 host = os.getenv("DP_SERVER_HOST", "127.0.0.1")
 port_raw = os.getenv("DP_SERVER_PORT", "")
 log_level = os.getenv("DP_SERVER_LOG_LEVEL", "info").lower
 if not port_raw.isdigit:
 print(f"[dp-server:{backend.name}] DP_SERVER_PORT={port_raw!r} is not a port",
 file=sys.stderr, flush=True)
 raise SystemExit(2)
 logging.basicConfig(level=log_level.upper,
 format="%(asctime)s %(name)s %(levelname)s %(message)s")

 app = build_app(backend)
 server = uvicorn.Server(uvicorn.Config(
 app, host=host, port=int(port_raw), log_level=log_level))

 def _watch_load_failure -> None:
 state = app.state.server_state
 while state.load_error is None and not state.warm.is_set:
 threading.Event.wait(0.5)
 if state.load_error is not None:
 print(f"[dp-server:{backend.name}] model load failed: {state.load_error}",
 file=sys.stderr, flush=True)
 os._exit(3) # fail loud; the supervisor owns the restart decision

 threading.Thread(target=_watch_load_failure, name="load-watch", daemon=True).start
 server.run
