"""The endpoint circuit breaker's state machine (app/vision/circuit.py).

Extracted verbatim from the retired WS-F suite (test_metrics_video.py) when the
legacy video observability tests died with their subjects: the breaker module is a
plan-§9 KEEP — present, tested, currently wired nowhere (see its docstring for the
rebuild-era status and the candidate wiring above the graph).
"""
from __future__ import annotations

import pytest


class _Clock:
    """A hand-cranked monotonic clock for deterministic breaker tests."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def test_circuit_opens_after_threshold_consecutive_failures():
    from app.vision.circuit import CircuitBreaker, CLOSED, OPEN
    clk = _Clock()
    cb = CircuitBreaker(threshold=3, cooldown_s=10.0, clock=clk)
    assert cb.state == CLOSED and cb.allow() is True
    cb.record_failure(); cb.record_failure()
    assert cb.state == CLOSED and cb.allow() is True   # 2 < 3
    cb.record_failure()                                # 3rd → OPEN
    assert cb.state == OPEN
    assert cb.allow() is False                         # fast-fail


def test_circuit_success_resets_the_consecutive_counter():
    from app.vision.circuit import CircuitBreaker, CLOSED
    cb = CircuitBreaker(threshold=3, cooldown_s=10.0, clock=_Clock())
    cb.record_failure(); cb.record_failure()
    cb.record_success()                                # a reachable reply resets it
    assert cb.consecutive_failures == 0
    cb.record_failure(); cb.record_failure()
    assert cb.state == CLOSED and cb.allow() is True   # not yet 3 in a row


def test_circuit_half_opens_after_cooldown_and_admits_one_probe():
    from app.vision.circuit import CircuitBreaker, OPEN, HALF_OPEN, CLOSED
    clk = _Clock()
    cb = CircuitBreaker(threshold=1, cooldown_s=10.0, clock=clk)
    cb.record_failure()
    assert cb.state == OPEN and cb.allow() is False
    clk.advance(9.9)
    assert cb.allow() is False                         # cooldown not elapsed
    clk.advance(0.2)                                   # 10.1s total
    assert cb.allow() is True                          # promoted to HALF_OPEN, one probe
    assert cb.state == HALF_OPEN
    assert cb.allow() is False                         # the single probe is already out
    cb.record_success()                                # probe reached the server
    assert cb.state == CLOSED and cb.allow() is True


def test_circuit_half_open_failure_reopens_and_restarts_cooldown():
    from app.vision.circuit import CircuitBreaker, OPEN
    clk = _Clock()
    cb = CircuitBreaker(threshold=1, cooldown_s=10.0, clock=clk)
    cb.record_failure()
    clk.advance(10.1)
    assert cb.allow() is True                          # HALF_OPEN probe
    cb.record_failure()                                # probe still dead → reopen
    assert cb.state == OPEN
    assert cb.allow() is False                         # cooldown restarted from now
    clk.advance(10.1)
    assert cb.allow() is True                          # eligible again


def test_circuit_half_open_stale_probe_self_heals():
    """A probe whose outcome is never recorded (a caller that forgot its try/finally, or
    died) must not wedge the breaker HALF_OPEN forever. A probe older than one cooldown
    is stale, and a fresh probe is re-admitted — so a broken caller can't permanently
    fast-fail a recovered endpoint."""
    from app.vision.circuit import CircuitBreaker, HALF_OPEN
    clk = _Clock()
    cb = CircuitBreaker(threshold=1, cooldown_s=10.0, clock=clk)
    cb.record_failure()
    clk.advance(10.1)
    assert cb.allow() is True                           # probe admitted, never reported
    assert cb.state == HALF_OPEN
    assert cb.allow() is False                          # in-flight probe still fresh
    clk.advance(9.9)
    assert cb.allow() is False                          # not yet stale (< cooldown)
    clk.advance(0.2)                                    # 10.1s since admission
    assert cb.allow() is True                           # stale probe → re-admit (self-heal)


def test_breaker_for_shares_one_instance_per_key():
    from app.vision import circuit
    circuit.reset_all()
    try:
        a = circuit.breaker_for("http://vl.test:8000", threshold=2, cooldown_s=5.0)
        b = circuit.breaker_for("http://vl.test:8000")
        other = circuit.breaker_for("http://ocr.test:9000")
        assert a is b                                  # same key → shared state
        assert a is not other                          # a captioner outage ≠ OCR outage
        a.record_failure(); a.record_failure()
        assert b.state == "open"                        # state is shared
        assert other.state == "closed"
    finally:
        circuit.reset_all()


def test_breaker_rejects_bad_threshold():
    from app.vision.circuit import CircuitBreaker
    with pytest.raises(ValueError):
        CircuitBreaker(threshold=0)
