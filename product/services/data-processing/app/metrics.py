"""Zero-dependency Prometheus metrics — the emission side of D9 observability.

The service owes a ``/metrics`` endpoint in Prometheus text-exposition format
(ARCHITECTURE.md §Observability). The shared Prometheus (platform) scrapes it; we
only EMIT. We deliberately DON'T pull in ``prometheus-fastapi-instrumentator`` /
``prometheus_client``: those aren't in the frozen ``requirements.txt`` and the whole
loop MUST stay headless-green with zero new deps. The text format is small and
stable, so a tiny in-house registry + renderer satisfies the contract and stays
fully unit-testable without any model, GPU, or extra package.

Design:
  * a ``Metrics`` registry is INSTANCE-scoped (attached to ``app.state``), not a
    process-global singleton — so tests that build several apps in one process don't
    leak counters into each other (prometheus_client's global default registry is a
    known test-isolation footgun; we avoid it by construction).
  * Counter / Gauge / Histogram, each with optional labels. Values live under the
    tuple of label values in declared order, so the render is stable + ordered.
  * PULL-TIME gauge sources (``add_gauge_source``): queue depth and continuity
    counts are live state owned elsewhere; a source callback is invoked at render
    time so the scrape always reflects the current value without a push on every
    change.
  * thread-safe: handlers touch it from the event loop AND from the threadpool
    workers (async /ingest), so every mutation takes a ``threading.Lock``.

Prometheus text format (0.0.4) is emitted verbatim: ``# HELP`` / ``# TYPE`` header
lines per family, one sample line per label-set, histograms as
``_bucket{le=…}`` / ``_sum`` / ``_count``. Label values are escaped (backslash,
double-quote, newline) per the spec.
"""
from __future__ import annotations

import math
import threading
from time import perf_counter
from typing import Callable, Iterable, Optional

# Default latency buckets (seconds) — spans sub-ms HTTP handlers through minute-scale
# fully-loaded chunk processing (real ASR + diarization + VLM), so both the fast C8
# path and the heavy batch path land in meaningful buckets.
DEFAULT_BUCKETS: tuple[float, ...] = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0,
    10.0, 30.0, 60.0, 120.0, 300.0, math.inf,
)

_LabelKey = tuple[str, ...]


def _escape(value: str) -> str:
    """Escape a label VALUE per the exposition spec (\\, \", newline)."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _fmt(value: float) -> str:
    """Render a float the Prometheus way: +Inf/-Inf, integers without a trailing
    ``.0`` when exact, else repr (round-trippable)."""
    if value == math.inf:
        return "+Inf"
    if value == -math.inf:
        return "-Inf"
    if math.isnan(value):
        return "NaN"
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return repr(value)


class _Family:
    __slots__ = ("name", "help", "typ", "labelnames", "values", "buckets", "hist")

    def __init__(self, name: str, help: str, typ: str, labelnames: tuple[str, ...],
                 buckets: Optional[tuple[float, ...]] = None) -> None:
        self.name = name
        self.help = help
        self.typ = typ
        self.labelnames = labelnames
        self.values: dict[_LabelKey, float] = {}
        self.buckets = buckets
        # histogram state per label-set: (bucket_counts:list, sum, count)
        self.hist: dict[_LabelKey, list] = {}


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._families: dict[str, _Family] = {}
        # Pull-time gauge sources: name -> (help, labelnames, fn). fn() returns either a
        # float (unlabelled) or an iterable of (label_values_tuple, value).
        self._sources: dict[str, tuple[str, tuple[str, ...], Callable[[], object]]] = {}

    # --------------------------------------------------------------- declaration
    def declare_counter(self, name: str, help: str, labelnames: Iterable[str] = ()) -> None:
        self._declare(name, help, "counter", tuple(labelnames))

    def declare_gauge(self, name: str, help: str, labelnames: Iterable[str] = ()) -> None:
        self._declare(name, help, "gauge", tuple(labelnames))

    def declare_histogram(self, name: str, help: str, labelnames: Iterable[str] = (),
                          buckets: Iterable[float] = DEFAULT_BUCKETS) -> None:
        buckets = tuple(sorted(set(buckets)))
        if math.inf not in buckets:
            buckets = buckets + (math.inf,)
        self._declare(name, help, "histogram", tuple(labelnames), buckets)

    def _declare(self, name, help, typ, labelnames, buckets=None) -> None:
        with self._lock:
            if name in self._families:
                return  # idempotent — declaring twice is fine (e.g. re-imported)
            self._families[name] = _Family(name, help, typ, labelnames, buckets)

    def add_gauge_source(self, name: str, help: str, fn: Callable[[], object],
                         labelnames: Iterable[str] = ()) -> None:
        """Register a PULL-TIME gauge computed at render (scrape) time — for live
        state owned elsewhere (queue depth, continuity counts)."""
        with self._lock:
            self._sources[name] = (help, tuple(labelnames), fn)

    # ------------------------------------------------------------------ mutation
    def _key(self, fam: _Family, labels: Optional[dict]) -> _LabelKey:
        labels = labels or {}
        if set(labels) != set(fam.labelnames):
            raise KeyError(
                f"metric {fam.name!r} expects labels {fam.labelnames}, got {tuple(labels)}"
            )
        return tuple(str(labels[k]) for k in fam.labelnames)

    def inc(self, name: str, labels: Optional[dict] = None, amount: float = 1.0) -> None:
        with self._lock:
            fam = self._families[name]
            key = self._key(fam, labels)
            fam.values[key] = fam.values.get(key, 0.0) + amount

    def set(self, name: str, value: float, labels: Optional[dict] = None) -> None:
        with self._lock:
            fam = self._families[name]
            fam.values[self._key(fam, labels)] = value

    def observe(self, name: str, value: float, labels: Optional[dict] = None) -> None:
        with self._lock:
            fam = self._families[name]
            key = self._key(fam, labels)
            state = fam.hist.get(key)
            if state is None:
                state = [[0] * len(fam.buckets), 0.0, 0]  # counts, sum, count
                fam.hist[key] = state
            counts, total, n = state
            for i, edge in enumerate(fam.buckets):
                if value <= edge:
                    counts[i] += 1
            state[1] = total + value
            state[2] = n + 1

    # -------------------------------------------------------------------- render
    def render(self) -> str:
        with self._lock:
            lines: list[str] = []
            for fam in self._families.values():
                self._render_family(fam, lines)
            # Pull-time gauge sources rendered last (their own HELP/TYPE blocks).
            for name, (help, labelnames, fn) in self._sources.items():
                self._render_source(name, help, labelnames, fn, lines)
        return "\n".join(lines) + "\n"

    def _render_family(self, fam: _Family, lines: list[str]) -> None:
        if fam.typ == "histogram":
            if not fam.hist:
                return
            lines.append(f"# HELP {fam.name} {fam.help}")
            lines.append(f"# TYPE {fam.name} histogram")
            for key, (counts, total, n) in fam.hist.items():
                # observe() tallies counts[i] as "# observations <= buckets[i]"; buckets
                # are sorted, so counts are non-decreasing and ARE the cumulative
                # ``_bucket{le=…}`` values the format wants — emit them directly.
                for edge, c in zip(fam.buckets, counts):
                    lbl = self._labels_str(fam.labelnames, key, extra=("le", _fmt(edge)))
                    lines.append(f"{fam.name}_bucket{lbl} {c}")
                base = self._labels_str(fam.labelnames, key)
                lines.append(f"{fam.name}_sum{base} {_fmt(total)}")
                lines.append(f"{fam.name}_count{base} {n}")
            return
        if not fam.values:
            return
        lines.append(f"# HELP {fam.name} {fam.help}")
        lines.append(f"# TYPE {fam.name} {fam.typ}")
        for key, value in fam.values.items():
            lines.append(f"{fam.name}{self._labels_str(fam.labelnames, key)} {_fmt(value)}")

    def _render_source(self, name, help, labelnames, fn, lines) -> None:
        try:
            result = fn()
        except Exception:  # a metric source must never break the whole scrape
            return
        if result is None:
            return
        lines.append(f"# HELP {name} {help}")
        lines.append(f"# TYPE {name} gauge")
        if isinstance(result, (int, float)):
            lines.append(f"{name} {_fmt(float(result))}")
            return
        for label_values, value in result:
            key = tuple(str(v) for v in label_values)
            lines.append(f"{name}{self._labels_str(labelnames, key)} {_fmt(float(value))}")

    @staticmethod
    def _labels_str(labelnames: tuple[str, ...], key: _LabelKey,
                    extra: Optional[tuple[str, str]] = None) -> str:
        pairs = [f'{n}="{_escape(v)}"' for n, v in zip(labelnames, key)]
        if extra is not None:
            pairs.append(f'{extra[0]}="{_escape(extra[1])}"')
        return "{" + ",".join(pairs) + "}" if pairs else ""


# ---------------------------------------------------------------------------------
# Baseline HTTP metrics — a PURE ASGI middleware (NOT BaseHTTPMiddleware).
#
# It only reads the response START message's status and times the call; it NEVER
# touches or buffers the body, so it can't perturb a single response (two exact-dict
# body assertions in the suite rely on that). A path TEMPLATER collapses variable
# segments (e.g. /continuity/<stream_id>) so label cardinality stays bounded — one
# series per ROUTE, not per id.
# ---------------------------------------------------------------------------------

def default_templatizer(path: str) -> str:
    """Fallback: identity. Services pass their own to collapse variable path
    segments (routes with ids) into templates."""
    return path


class MetricsASGIMiddleware:
    def __init__(self, app, metrics: Metrics, *, prefix: str,
                 templatizer: Callable[[str], str] = default_templatizer) -> None:
        self.app = app
        self.metrics = metrics
        self._req = f"{prefix}_http_requests_total"
        self._dur = f"{prefix}_http_request_duration_seconds"
        self._templatize = templatizer
        metrics.declare_counter(
            self._req, "HTTP requests by method, route template, and status code.",
            ["method", "path", "status"],
        )
        metrics.declare_histogram(
            self._dur, "HTTP request latency (seconds) by method and route template.",
            ["method", "path"],
        )

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method = scope.get("method", "GET")
        path = self._templatize(scope.get("path", ""))
        start = perf_counter()
        status = {"code": 500}  # default if the app dies before sending a start frame

        async def _send(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, _send)
        finally:
            dur = perf_counter() - start
            self.metrics.inc(self._req, {"method": method, "path": path,
                                         "status": str(status["code"])})
            self.metrics.observe(self._dur, dur, {"method": method, "path": path})
