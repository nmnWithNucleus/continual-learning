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
``fork`` (fast, inherits parent memory; used by tests to inherit monkeypatched state).
``forkserver`` is deliberately NOT offered: it freezes ``os.environ`` at server launch,
silently breaking the "child inherits the parent's env ⇒ resolves the same config"
premise this boundary rests on (a runtime backend flip would stamp the parent's fresh
``pipeline_version`` onto records a stale-env child produced). Keep this module
import-light: a spawned child imports it before anything else runs.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import multiprocessing
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

logger = logging.getLogger("data-processing.isolation")

# Dedicated executor for the blocking spawn/recv legs — NEVER the asyncio default
# executor: one thread parks per in-flight child for its whole lifetime, and
# saturating the shared default pool would wedge unrelated loop work (getaddrinfo!)
# behind hung children. Cap is far above any sane INGEST_WORKERS; threads are lazy.
_executor: Optional[ThreadPoolExecutor] = None


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=64, thread_name_prefix="dp-iso")
    return _executor


def _child_main(conn, modality: str, c1: dict, blob: bytes,
                settings, span_seconds: float) -> None:
    """Child entrypoint: run the Processor, ship ONE tagged reply tuple back.

    Never raises out (a clean exit keeps the parent's died-vs-errored signal crisp);
    imports live inside so a spawned child pays them here, not at module import.
    Every send has a STRING-ONLY fallback: a plugin's unpicklable payload (an exotic
    detail dict, an unpicklable unit) must degrade to a sanitized reply that PRESERVES
    the taxonomy flags — never to a silent clean exit the parent would misread as a
    transient hard death (flipping terminal→transient loses the classification).
    """
    def _send(payload, fallback) -> None:
        try:
            conn.send(payload)
        except Exception as exc:  # noqa: BLE001 — unpicklable/oversized payload
            with contextlib.suppress(Exception):
                conn.send(fallback(exc))

    try:
        from .ingest_core import ProcessingError
        from .processing.registry import get_processor

        try:
            units = get_processor(modality).process(c1, blob, settings, span_seconds)
            _send(("units", units),
                  lambda e: ("error", f"result not transferable: {type(e).__name__}: {e}"))
        except ProcessingError as exc:
            _send(("processing_error", (exc.detail, exc.http_status, exc.transient)),
                  lambda e, exc=exc: ("processing_error",
                                      ({"error": repr(exc.detail)[:2000],
                                        "note": f"detail not picklable: {e}"},
                                       exc.http_status, exc.transient)))
        except BaseException as exc:  # noqa: BLE001 — taxonomy decided in the parent
            _send(("error", f"{type(exc).__name__}: {exc}"),
                  lambda e: ("error", "processor error (message not transferable)"))
    except BaseException:  # conn broken / import failure — parent sees EOF + exitcode
        pass
    finally:
        with contextlib.suppress(Exception):
            conn.close()


def _spawn_and_collect(ctx, holder: dict, target_args: tuple) -> tuple:
    """Blocking (executor-thread) leg: start the child AND wait for its reply.

    ``proc.start()`` runs HERE, not on the event loop: under ``spawn`` it writes the
    whole pickled arg tuple (including the chunk blob — easily MBs) through the
    child's 64KB bootstrap pipe, blocking until the freshly-booting interpreter
    drains it — hundreds of ms the loop must never eat (review-confirmed stall).

    recv-before-join ORDER IS LOAD-BEARING: a child whose pickled reply exceeds the
    OS pipe buffer blocks inside ``conn.send`` until the parent drains it — joining
    first would deadlock (child waits for a reader, parent waits for exit).

    EOF with no reply = the child DIED (signal/os._exit/native crash). Owns both pipe
    ends; the loop side only ever touches ``holder`` — so a cancelled parent can
    abandon this thread safely (it kills via holder, the pipe EOFs, the thread
    unblocks, finishes, and is GC'd)."""
    if holder.get("cancelled"):
        return ("cancelled", None)
    parent_conn, child_conn = ctx.Pipe(duplex=False)
    try:
        proc = ctx.Process(target=_child_main, args=(child_conn, *target_args),
                           daemon=True, name=holder["name"])
        proc.start()
        holder["proc"] = proc  # published BEFORE any blocking wait — cancel can kill
        if holder.get("cancelled"):  # raced a cancel during start(): kill immediately
            with contextlib.suppress(Exception):
                proc.kill()
            proc.join()
            return ("cancelled", None)
        child_conn.close()  # parent holds only the read end; child death → EOF
        payload = None
        try:
            try:
                payload = parent_conn.recv()
            except (EOFError, OSError):
                pass  # no reply → died; exitcode read after the join below
        finally:
            proc.join()  # ALWAYS reap — even if recv raised an unpickling error
        if payload is None:
            return ("died", proc.exitcode)
        return payload
    finally:
        with contextlib.suppress(Exception):
            child_conn.close()
        with contextlib.suppress(Exception):
            parent_conn.close()


async def run_processor_in_subprocess(
    *, modality: str, c1: dict[str, Any], blob: bytes, settings, span_seconds: float,
):
    """Run one chunk's Processor step in a fresh child; return its ProcessedUnits.

    Cancellation (drain timeout / shutdown) SIGKILLs the child — the whole point:
    the kernel reclaims the computation a cancelled in-process thread would leak.
    """
    from .ingest_core import ProcessingError  # lazy: keep module import-light

    ctx = multiprocessing.get_context(settings.ingest_subproc_start)
    holder: dict[str, Any] = {"name": f"dp-chunk-{c1.get('chunk_id', '?')}"}
    loop = asyncio.get_running_loop()
    try:
        payload = await loop.run_in_executor(
            _get_executor(), _spawn_and_collect, ctx, holder,
            (modality, c1, blob, settings, span_seconds),
        )
    except asyncio.CancelledError:
        holder["cancelled"] = True  # not-yet-started thread will refuse to spawn
        proc = holder.get("proc")
        if proc is not None:
            with contextlib.suppress(Exception):
                proc.kill()  # SIGKILL: reclaim the ghost computation NOW
            threading.Thread(target=proc.join, daemon=True).start()  # reap off-loop
        logger.warning("chunk %s: processor subprocess killed on cancel (pid %s)",
                       c1.get("chunk_id"), getattr(proc, "pid", None))
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
