"""The HTTP skeleton every model server runs: warmup thread + /health + /infer.

Lifecycle: startup launches one warmup thread that calls backend.load(). Until it
returns, /health is 503 "warming" and /infer is 503 transient. If load() raises,
/health flips to 500 "load_failed" (the runner turns that into a loud process
exit; under TestClient the app object stays inspectable). Inference is serialized
with a lock — model backends are not assumed thread-safe; replicas are the
parallelism (plan §3).
"""
from __future__ import annotations

import logging
import threading
import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .backend import BackendError, ModelBackend, TransientBackendError
from .wire import HealthNotReady, HealthReady, InferError, InferOk, InferRequest

logger = logging.getLogger("dp-server")


class _State:
    def __init__(self) -> None:
        self.warm = threading.Event()
        self.load_error: str | None = None
        self.infer_lock = threading.Lock()


def build_app(backend: ModelBackend) -> FastAPI:
    state = _State()

    def _warmup() -> None:
        try:
            backend.load()
        except BaseException as exc:  # any load failure is terminal for the replica
            state.load_error = f"{type(exc).__name__}: {exc}"
            logger.error("model load failed:\n%s", traceback.format_exc())
            return
        state.warm.set()
        logger.info("model warm: %s", backend.identity())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        threading.Thread(target=_warmup, name="warmup", daemon=True).start()
        yield

    app = FastAPI(title=f"dp-server-{backend.name}", lifespan=lifespan)
    app.state.server_state = state  # the runner watches load_error for fail-loud exit
    app.state.backend = backend

    @app.get("/health")
    async def health() -> JSONResponse:
        # async def, deliberately: a sync handler here would
        # share the threadpool with queued sync /infer calls, so a busy replica's
        # backlog could starve its own liveness probe and the supervisor would
        # kill a merely-busy process. This handler only reads an Event + strings
        # — nothing blocks the loop.
        if state.load_error is not None:
            body = HealthNotReady(status="load_failed", server=backend.name,
                                  error=state.load_error)
            return JSONResponse(status_code=500, content=body.model_dump())
        if not state.warm.is_set():
            body = HealthNotReady(status="warming", server=backend.name)
            return JSONResponse(status_code=503,
                                content=body.model_dump(exclude_none=True))
        return JSONResponse(status_code=200, content=HealthReady(
            server=backend.name, identity=backend.identity()).model_dump())

    @app.post("/infer")
    def infer(request: InferRequest) -> JSONResponse:
        if not state.warm.is_set():
            reason = state.load_error or "warming"
            body = InferError(error=f"not ready: {reason}", transient=True)
            return JSONResponse(status_code=503, content=body.model_dump())
        try:
            with state.infer_lock:
                result = backend.infer(request)
        except BackendError as exc:
            body = InferError(error=str(exc), transient=False)
            return JSONResponse(status_code=422, content=body.model_dump())
        except TransientBackendError as exc:
            body = InferError(error=str(exc), transient=True)
            return JSONResponse(status_code=503, content=body.model_dump())
        except Exception as exc:
            logger.error("unexpected infer failure:\n%s", traceback.format_exc())
            body = InferError(error=f"{type(exc).__name__}: {exc}", transient=True)
            return JSONResponse(status_code=500, content=body.model_dump())
        return JSONResponse(status_code=200, content=InferOk(result=result).model_dump())

    return app
