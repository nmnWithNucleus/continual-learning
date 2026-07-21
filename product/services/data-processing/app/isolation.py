"""Per-chunk subprocess isolation — the poison-chunk / ghost-thread hardening (M7).

Two failure classes the in-process pool cannot contain, both closed by running the
Processor step of a chunk in a KILLABLE child process:

  * **Poison chunk, hard crash.** A segfault / native OOM inside model code (torch,
    ffmpeg, CUDA) kills whatever process it runs in. In-process, that is the WHOLE
    service: every in-flight chunk's work is thrown away, the restart re-drives the
    backlog, and the poison chunk crash-loops the service until the durable re-drive
    cap dead-letters it (bounded + visible, but expensive). In a child, the blast
    radius is ONE chunk: the parent sees the child die, applies the normal
    transient-retry-then-dead-letter taxonomy, and every other chunk keeps flowing.
  * **Ghost computation on cancel.** Cancelling an asyncio task does NOT stop the
    threadpool thread running a stage's blocking model call — a drain-timeout leaves
    an unkillable thread burning CPU/GPU to compute a result nobody will read
    (CPython threads cannot be killed). A child CAN be killed: cancellation SIGKILLs
    it and the kernel reclaims everything, immediately.

Boundary: exactly the Processor call — ``get_processor(modality).process(...)`` runs in
the child (resolved from the registry THERE; the child inherits the parent's env so it
resolves the same config); blob fetch, sha verify, C2 assembly/validation/write, journal
and dedup all stay in the parent. The chunk stays the atomic unit (one job = one chunk =
one child), and the parent-computed ``pipeline_version`` still stamps the records.

Failure taxonomy across the boundary (mirrors in-process behaviour):
  * child ``ProcessingError``  → re-raised in the parent with the same detail /
    http_status / transient flags (the load-bearing retry-vs-dead-letter split);
  * child generic exception    → ``RuntimeError`` in the parent (inline: 500; async
    worker: transient retry, then dead-letter) — exactly how an unexpected in-process
    processor error is treated;
  * child DIED without a reply (signal / os._exit / native crash) → ``RuntimeError``
    with the exit code — transient by the same rule, so a genuine poison chunk burns
    its bounded retries (each in a fresh child) and dead-letters VISIBLY, service up.

OFF by default (``INGEST_ISOLATION=off``): the in-process path is byte-identical and
mock-default tests never pay a fork/spawn. Costs when on: per-chunk process start +
model load (no warm pool in this slice — real-backend deployments amortize via
``fork``/``forkserver``), pickled args/units, and per-graph-stage metrics from inside
the child are not recorded (the parent's coarse ``stage="process"`` timing remains).
A hung child (no crash, no cancel) still holds its worker — a wall-clock kill knob can
land later; today that chunk is at least ops-killable, unlike a hung thread.

Start methods: ``spawn`` (default — safest with CUDA/threads; fresh interpreter) |
``fork`` (fast, inherits parent memory; used by tests to inherit monkeypatched state) |
``forkserver``. Keep this module import-light: a spawned child imports it before
anything else runs.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import multiprocessing
import threading
from typing import Any

logger = logging.getLogger("data-processing.isolation")


def _child_main(conn, modality: str, c1: dict, blob: bytes,
                settings, span_seconds: float) -> None:
    """Child entrypoint: run the Processor, ship ONE tagged reply tuple back.

    Never raises out (a clean exit keeps the parent's died-vs-errored signal crisp);
    imports live inside so a spawned child pays them here, not at module import.
    """
    try:
        from .ingest_core import ProcessingError
        from .processing.registry import get_processor

        try:
            units = get_processor(modality).process(c1, blob, settings, span_seconds)
            conn.send(("units", units))
        except ProcessingError as exc:
            conn.send(("processing_error",
                       (exc.detail, exc.http_status, exc.transient)))
        except BaseException as exc:  # noqa: BLE001 — taxonomy decided in the parent
            conn.send(("error", f"{type(exc).__name__}: {exc}"))
    except BaseException:  # conn broken / import failure — parent sees EOF + exitcode
        pass
    finally:
        with contextlib.suppress(Exception):
            conn.close()


def _collect(proc, conn) -> tuple:
    """Blocking (executor-thread) wait for the child's reply. EOF with no reply means
    the child DIED (signal/os._exit/native crash). Owns closing the parent end — the
    loop side never touches it, so a cancelled parent can abandon this thread safely
    (a SIGKILLed child EOFs the pipe, the thread unblocks, finishes, and is GC'd)."""
    try:
        try:
            payload = conn.recv()
        except (EOFError, OSError):
            payload = None
        proc.join()
        if payload is None:
            return ("died", proc.exitcode)
        return payload
    finally:
        with contextlib.suppress(Exception):
            conn.close()


async def run_processor_in_subprocess(
    *, modality: str, c1: dict[str, Any], blob: bytes, settings, span_seconds: float,
):
    """Run one chunk's Processor step in a fresh child; return its ProcessedUnits.

    Cancellation (drain timeout / shutdown) SIGKILLs the child — the whole point:
    the kernel reclaims the computation a cancelled in-process thread would leak.
    """
    from .ingest_core import ProcessingError  # lazy: keep module import-light

    ctx = multiprocessing.get_context(settings.ingest_subproc_start)
    parent_conn, child_conn = ctx.Pipe(duplex=False)
    proc = ctx.Process(
        target=_child_main,
        args=(child_conn, modality, c1, blob, settings, span_seconds),
        daemon=True,
        name=f"dp-chunk-{c1.get('chunk_id', '?')}",
    )
    proc.start()
    child_conn.close()  # parent holds only the read end; child death → EOF
    loop = asyncio.get_running_loop()
    try:
        payload = await loop.run_in_executor(None, _collect, proc, parent_conn)
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            proc.kill()  # SIGKILL: reclaim the ghost computation NOW
        threading.Thread(target=proc.join, daemon=True).start()  # reap off-loop
        logger.warning("chunk %s: processor subprocess killed on cancel (pid %s)",
                       c1.get("chunk_id"), proc.pid)
        raise

    kind, value = payload
    if kind == "units":
        return value
    if kind == "processing_error":
        detail, http_status, transient = value
        raise ProcessingError(detail, http_status=http_status, transient=transient)
    if kind == "error":
        raise RuntimeError(f"processor subprocess error: {value}")
    # "died": no reply, only an exit code. Same transient treatment as any unexpected
    # processor error — a true poison chunk exhausts its bounded retries (one fresh
    # child each) and dead-letters visibly; the service never goes down with it.
    raise RuntimeError(
        f"processor subprocess died without a reply (exitcode {value}) — "
        "hard crash in model/native code; chunk will retry then dead-letter"
    )
