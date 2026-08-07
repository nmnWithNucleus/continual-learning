"""The backend seam: one class per model server implements this.

Error contract (what the DP model client keys retries off):
  BackendError          -> deterministic failure of THIS input (422, transient=false);
                           every replica would fail it identically (L7 territory).
  TransientBackendError -> this replica hiccuped (503, transient=true); another
                           replica (or a retry) may succeed.
  anything else         -> unexpected crash (500, transient=true) — retrying is safe
                           because /infer is side-effect-free; if it is actually
                           deterministic the bounded retry just fails fast elsewhere.
"""
from __future__ import annotations

from .wire import InferRequest


class BackendError(Exception):
    """Deterministic per-input failure: same input fails on every replica."""


class TransientBackendError(Exception):
    """Replica-local failure: retry on another replica may succeed."""


class ModelBackend:
    """One model server = one subclass. All methods run in worker threads."""

    name: str = "backend"

    def load(self) -> None:
        """Load weights onto the assigned device. Called once, in the warmup
        thread, before any infer. Raise to fail the server loudly."""
        raise NotImplementedError

    def identity(self) -> dict:
        """What this server actually runs — model_name, weights (revision/hash),
        frameworks (package versions), device. Only called after load()."""
        raise NotImplementedError

    def infer(self, request: InferRequest) -> dict:
        """Serve one request. Serialized by the framework (one call at a time)."""
        raise NotImplementedError
