"""VLM/OCR endpoint circuit breaker — fast-fail BEFORE the ffmpeg passes.

STATUS (DP rebuild Stage C, per plan §9 KEEP): present and tested, WIRED NOWHERE — the
same state it shipped in under v0 (nothing ever imported it). In the v1 world its two
v0 customers changed shape: the OCR path now rides ``app/model_client.py`` (replica
rotation + bounded transient retry against the supervised fleet — a breaker adds
little for a supervisor-restarted local server), and the VLM endpoint is called by the
optional ``clipcap`` stage, whose failure is a HOLE healed by redrive (L7/L8). The
remaining honest use is the original one: fast-failing a chunk BEFORE ``clipprep``'s
ffmpeg passes during a sustained captioner outage — but that gate must live ABOVE the
graph (the worker/ingest layer), since inside the graph clipprep has already run by
the time clipcap touches the endpoint. Left for the ledger/cutover stages to wire or
retire; the state machine below is unchanged and covered by ``tests/test_circuit.py``.

Original design (§8 / caveat A-10): *N* consecutive connect-refused failures against a
captioner trip the breaker OPEN, so subsequent chunks fast-fail at ~0 CPU cost
*before* the two ffmpeg prep passes run — instead of paying the full decode + delta +
extract on every chunk only to hit the same dead socket. A sustained endpoint outage
then stops silently burning each chunk's retry budget on futile prep.

State machine (per endpoint key):

    CLOSED    --(threshold consecutive failures)-->  OPEN
    OPEN      --(cooldown elapsed, on next allow())-> HALF_OPEN   (one probe admitted)
    HALF_OPEN --record_success()-->                   CLOSED      (counter reset)
    HALF_OPEN --record_failure()-->                   OPEN        (cooldown restarts)

Only the CALLER decides what counts as a failure, and the design is deliberate: it trips
on *connect-refused* specifically (a dead endpoint), NOT on a slow or malformed reply. A
200 carrying garbage is an application problem, not an outage, and must never open the
breaker (that is the parse ladder's job, and a redelivery would not help). So the wiring
records a failure only for a connect error (``httpx.ConnectError`` / connection refused)
and a success for any reply that reached the server. The breaker itself is
transport-agnostic: it counts what it is told to count.

Thread-safe: clip stages run in the worker threadpool (``run_sync``) and across the
async worker pool, so every breaker touch takes a lock. The clock is injectable for
tests; production uses a MONOTONIC clock (never wall-clock — an NTP step or a DST jump
must not spuriously reopen or heal a breaker).

Nothing imports this on the legacy path, so it is inert until a clip stage wires it in
(WS-B ``clipprep`` gates on ``allow()`` before the ffmpeg passes; WS-D ``clipcap`` /
WS-C ``screentext`` record the outcome of their HTTP call).
"""
from __future__ import annotations

import threading
import time
from typing import Callable

# State names, exported so a consumer / test can compare without magic strings.
CLOSED = "closed"
OPEN = "open"
HALF_OPEN = "half_open"


class CircuitOpenError(RuntimeError):
    """Raised by a caller when ``allow()`` is False: the endpoint's breaker is OPEN, so
    the chunk fast-fails before any ffmpeg prep. It is a TRANSIENT signal — the breaker
    half-opens after the cooldown — so the worker's retry taxonomy treats it like any
    other endpoint blip (retried cheaply on a later delivery, never a silent success)."""


class CircuitBreaker:
    """One endpoint's breaker. Usually obtained via :func:`breaker_for` so the instance
    (and therefore the state) is shared across the worker pool; constructed directly only
    in tests that want an isolated instance with an injected clock."""

    __slots__ = ("_threshold", "_cooldown_s", "_clock", "_lock",
                 "_state", "_failures", "_opened_at", "_probe_pending", "_probe_at")

    def __init__(self, *, threshold: int = 5, cooldown_s: float = 30.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if threshold < 1:
            raise ValueError("threshold must be >= 1")
        self._threshold = threshold
        self._cooldown_s = max(0.0, cooldown_s)
        self._clock = clock
        self._lock = threading.Lock()
        self._state = CLOSED
        self._failures = 0          # consecutive failures since the last success
        self._opened_at = 0.0       # monotonic stamp of the last OPEN transition
        self._probe_pending = False  # a HALF_OPEN probe has been admitted, not yet resolved
        self._probe_at = 0.0        # monotonic stamp of that admission (stale-probe guard)

    def allow(self) -> bool:
        """True if a call may proceed. OPEN fast-fails until the cooldown elapses, then
        promotes to HALF_OPEN and admits exactly ONE probe caller; further callers keep
        fast-failing until that probe records a success or failure (no thundering herd on
        a still-dead endpoint).

        Self-healing: a probe whose outcome is never recorded — a caller that forgot its
        try/finally, or died between allow() and record_* — would otherwise wedge the
        breaker HALF_OPEN forever (allow() False for every future call, a permanent
        fast-fail WORSE than the outage). So an admitted probe older than one cooldown is
        treated as stale and a fresh probe is re-admitted."""
        with self._lock:
            now = self._clock()
            if self._state == OPEN:
                if (now - self._opened_at) >= self._cooldown_s:
                    self._state = HALF_OPEN
                    self._probe_pending = False
                else:
                    return False
            if self._state == HALF_OPEN:
                if self._probe_pending and (now - self._probe_at) < self._cooldown_s:
                    return False  # a fresh probe is already in flight
                self._probe_pending = True
                self._probe_at = now
                return True
            return True  # CLOSED

    def record_success(self) -> None:
        """A call reached the server and got a reply → the endpoint is alive. Heal fully
        (a half-open probe that succeeds closes the breaker)."""
        with self._lock:
            self._state = CLOSED
            self._failures = 0
            self._probe_pending = False

    def record_failure(self) -> None:
        """A connect-refused against the endpoint. The ``threshold``-th consecutive one
        (or ANY failure while HALF_OPEN) opens the breaker and (re)starts the cooldown."""
        with self._lock:
            self._failures += 1
            if self._state == HALF_OPEN or self._failures >= self._threshold:
                self._state = OPEN
                self._opened_at = self._clock()
                self._probe_pending = False

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def consecutive_failures(self) -> int:
        with self._lock:
            return self._failures


# ---- Process-global registry: one shared breaker per endpoint key --------------
_registry_lock = threading.Lock()
_breakers: dict[str, CircuitBreaker] = {}


def breaker_for(key: str, *, threshold: int = 5, cooldown_s: float = 30.0,
                clock: Callable[[], float] = time.monotonic) -> CircuitBreaker:
    """The shared breaker for ``key`` (e.g. a VLM / OCR base URL). Constructed once, with
    the first caller's params; later callers get the SAME instance so breaker state is
    shared across every worker thread. Keyed by endpoint so a captioner outage never
    trips the ocr server's breaker and vice-versa."""
    with _registry_lock:
        breaker = _breakers.get(key)
        if breaker is None:
            breaker = CircuitBreaker(threshold=threshold, cooldown_s=cooldown_s, clock=clock)
            _breakers[key] = breaker
        return breaker


def reset_all() -> None:
    """Drop every registered breaker. Test-isolation only (production breakers live for
    the process lifetime)."""
    with _registry_lock:
        _breakers.clear()
