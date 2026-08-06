"""A manifest-spawnable framework server for supervisor tests.

Runs the REAL stack (build_app + runner.serve + uvicorn) with an echo backend, so
supervisor tests exercise exactly what a model server does — minus the model.
Failure injection is env-driven because the supervisor's job is to survive
arbitrary replica behavior; these knobs exist only in this test double.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dp_servers_common.backend import ModelBackend
from dp_servers_common.runner import serve
from dp_servers_common.wire import InferRequest


class FakeBackend(ModelBackend):
    name = "fake"

    def load(self) -> None:
        if os.getenv("FAKE_CRASH_AT_START"):
            print("[fake-server] crashing at start as instructed", flush=True)
            os._exit(9)
        if os.getenv("FAKE_LOAD_ERROR"):
            raise RuntimeError("injected load failure")
        time.sleep(float(os.getenv("FAKE_LOAD_DELAY_S", "0")))

    def identity(self) -> dict:
        return {
            "model_name": "fake-model",
            "weights": {"revision": "cafe0123"},
            "frameworks": {"fake": "1.0"},
            "device": "cpu",
            "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
        }

    def infer(self, request: InferRequest) -> dict:
        return {"pid": os.getpid(), "params": request.params}


if __name__ == "__main__":
    serve(FakeBackend())
