"""Thin async client for the model-server fleet (L9 bureaucracy side).

One ModelClient per server kind (whisper, pyannote, ast, ocr): picks a replica
round-robin, enforces a per-call timeout, and retries TRANSIENT failures on other
replicas, bounded. Deterministic failures (the server said this input is bad) are
raised immediately — every replica would fail them identically, and L7 owns what
happens next.

Identity verification (the L4 hook): before a replica serves its first call, its
/health identity must subset-match the expected identity from the supervisor
manifest. A mismatch is a loud IdentityMismatchError, never a silent skip — a
stage must never talk to the wrong model. Wired into main.py, which builds one
client per manifest server.

Observability: an optional duck-typed ``metrics`` registry records the
server-call family —
``dp_server_calls_total{server,outcome}`` per completed call (outcome: ``ok`` |
``deterministic_error`` | ``unavailable`` | ``identity_mismatch``),
``dp_server_call_transient_retries_total{server}`` per transient presentation
(transport error or 5xx-transient body), ``dp_server_identity_failures_total``
per failed identity verification, and ``dp_server_call_seconds{server}`` — the
per-ATTEMPT latency of HTTP-answered ``/infer`` attempts (transport failures
carry no meaningful latency and are counted, not timed). Families are declared
by main's metrics setup; recording is guarded — metrics must never fail a call.

Stdlib + httpx only; no model code, no output-affecting env knobs (L4).
"""
from __future__ import annotations

import asyncio
import logging
from time import perf_counter

import httpx

logger = logging.getLogger("data-processing.model_client")


class ModelClientError(Exception):
    """Base for everything this client raises."""


class ModelCallError(ModelClientError):
    """Deterministic failure: the server rejected THIS input (transient=false)."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class ModelUnavailableError(ModelClientError):
    """No replica could serve the call within the transient-retry budget."""


class IdentityMismatchError(ModelClientError):
    """A replica reports a different identity than the manifest expects."""


def identity_matches(expected: dict, actual: dict) -> list[str]:
    """Subset match: every key in `expected` must equal `actual`'s value; nested
    dicts recurse. Returns the list of mismatch descriptions (empty = match)."""
    problems: list[str] = []

    def walk(exp: dict, act: object, path: str) -> None:
        if not isinstance(act, dict):
            problems.append(f"{path or '.'}: expected mapping, got {act!r}")
            return
        for key, exp_value in exp.items():
            here = f"{path}.{key}" if path else key
            if key not in act:
                problems.append(f"{here}: missing (expected {exp_value!r})")
            elif isinstance(exp_value, dict):
                walk(exp_value, act[key], here)
            elif act[key] != exp_value:
                problems.append(f"{here}: expected {exp_value!r}, got {act[key]!r}")

    walk(expected, actual, "")
    return problems


class _Replica:
    def __init__(self, url: str):
        self.url = url.rstrip("/")
        self.verified = False


class ModelClient:
    def __init__(
        self,
        server: str,
        endpoints: list[str],
        expected_identity: dict | None = None,
        *,
        call_timeout_s: float = 120.0,
        connect_timeout_s: float = 5.0,
        max_transient_retries: int = 2,
        metrics=None,
    ):
        if not endpoints:
            raise ValueError(f"model client {server!r} needs at least one endpoint")
        self.server = server
        self.expected_identity = expected_identity or {}
        self.max_transient_retries = max_transient_retries
        self._replicas = [_Replica(u) for u in endpoints]
        self._rr = 0
        self._metrics = metrics
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(call_timeout_s, connect=connect_timeout_s))
        # Seed the call-outcome series to honest zeros so rate() is gap-free from
        # process start (the counters exist the moment the client does).
        for outcome in ("ok", "deterministic_error", "unavailable",
                        "identity_mismatch"):
            self._inc("dp_server_calls_total", {"server": server, "outcome": outcome},
                      amount=0.0)
        self._inc("dp_server_call_transient_retries_total", {"server": server},
                  amount=0.0)
        self._inc("dp_server_identity_failures_total", {"server": server}, amount=0.0)

    # -- metrics (guarded: must never fail a call) ------------------------------

    def _inc(self, name: str, labels: dict, amount: float = 1.0) -> None:
        if self._metrics is None:
            return
        try:
            self._metrics.inc(name, labels, amount=amount)
        except Exception:  # noqa: BLE001
            logger.exception("metrics inc failed for %s", name)

    def _observe(self, name: str, value: float, labels: dict) -> None:
        if self._metrics is None:
            return
        try:
            self._metrics.observe(name, value, labels)
        except Exception:  # noqa: BLE001
            logger.exception("metrics observe failed for %s", name)

    def _outcome(self, outcome: str) -> None:
        self._inc("dp_server_calls_total",
                  {"server": self.server, "outcome": outcome})

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- identity ---------------------------------------------------------------

    async def _verify_replica(self, replica: _Replica) -> None:
        """Fetch /health and subset-match identity. Raises IdentityMismatchError on
        a wrong model; raises transient httpx errors upward for the caller to
        treat as replica-unavailable."""
        resp = await self._client.get(f"{replica.url}/health")
        if resp.status_code != 200:
            raise httpx.TransportError(
                f"{replica.url} not ready: /health {resp.status_code}")
        try:
            body = resp.json()
            if not isinstance(body, dict):
                raise ValueError(f"non-object /health body: {body!r:.80}")
        except ValueError as exc:
            # A 200 that isn't the framework's health envelope (a port squatter,
            # a proxy mid-respawn): a transient replica presentation, not a
            # crash — the caller's transport handling owns the retry.
            raise httpx.TransportError(
                f"{replica.url}: malformed /health body: {exc}")
        identity = body.get("identity", {})
        problems = identity_matches(self.expected_identity, identity)
        if problems:
            self._inc("dp_server_identity_failures_total", {"server": self.server})
            raise IdentityMismatchError(
                f"{self.server} replica {replica.url} runs the wrong model: "
                + "; ".join(problems))
        replica.verified = True

    async def verify_identity(self) -> dict:
        """Connect-time check: every reachable replica must match. Unreachable or
        still-warming replicas stay unverified and are re-checked before first
        use. Returns {url: 'ok' | 'unreachable: ...'}."""
        report: dict[str, str] = {}
        for replica in self._replicas:
            try:
                await self._verify_replica(replica)
                report[replica.url] = "ok"
            except IdentityMismatchError:
                raise
            except (httpx.HTTPError, httpx.TransportError) as exc:
                report[replica.url] = f"unreachable: {type(exc).__name__}: {exc}"
        if not any(r.verified for r in self._replicas):
            logger.warning("%s: no replica verifiable at connect: %s",
                           self.server, report)
        return report

    # -- inference --------------------------------------------------------------

    def _pick_order(self) -> list[_Replica]:
        start = self._rr % len(self._replicas)
        self._rr += 1
        return self._replicas[start:] + self._replicas[:start]

    async def infer(self, payload: dict) -> dict:
        """POST /infer on a replica; bounded transient retry on the others.
        Returns the server's `result`. Raises ModelCallError (deterministic),
        IdentityMismatchError (wrong model), or ModelUnavailableError."""
        attempts_left = 1 + self.max_transient_retries
        last_error = "no replica attempted"
        order = self._pick_order()
        idx = 0
        while attempts_left > 0:
            replica = order[idx % len(order)]
            idx += 1
            attempts_left -= 1
            try:
                if not replica.verified:
                    await self._verify_replica(replica)
                t0 = perf_counter()
                resp = await self._client.post(f"{replica.url}/infer", json=payload)
            except IdentityMismatchError:
                self._outcome("identity_mismatch")
                raise
            except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPError) as exc:
                # The replica may be mid-respawn under the supervisor: whatever
                # comes back on that port next is a NEW process, so its identity
                # must be re-verified before it serves again (L4 — never silently
                # the wrong model).
                replica.verified = False
                self._inc("dp_server_call_transient_retries_total",
                          {"server": self.server})
                last_error = f"{replica.url}: {type(exc).__name__}: {exc}"
                logger.warning("%s transient: %s", self.server, last_error)
                continue
            # HTTP-answered attempt: real server latency (queue wait included).
            self._observe("dp_server_call_seconds", perf_counter() - t0,
                          {"server": self.server})
            if resp.status_code == 200:
                try:
                    result = resp.json()["result"]
                except (ValueError, KeyError, TypeError) as exc:
                    # A 200 that is not the framework envelope (a proxy answering
                    # for a dead replica, a half-up port): never this input's
                    # fault — a transient presentation like any other, and
                    # whatever serves on this port next must re-verify (review
                    # round: this exit previously escaped uncounted, or counted
                    # a false "ok").
                    replica.verified = False
                    self._inc("dp_server_call_transient_retries_total",
                              {"server": self.server})
                    last_error = (f"{replica.url}: malformed 200 /infer body: "
                                  f"{type(exc).__name__}: {exc}")
                    logger.warning("%s transient: %s", self.server, last_error)
                    continue
                self._outcome("ok")
                return result
            if resp.status_code >= 500:
                # A 5xx (incl. the framework's 503-warming) can be a respawned
                # replica presenting itself over HTTP rather than as a transport
                # error — whatever serves on this port next must re-verify its
                # /health identity before it serves (cleanup round: the
                # transport-only clearing missed this presentation).
                replica.verified = False
            transient = True
            try:
                body = resp.json()
                error = body.get("error", resp.text)
                transient = bool(body.get("transient", resp.status_code >= 500))
            except ValueError:
                error = resp.text
            if not transient:
                self._outcome("deterministic_error")
                raise ModelCallError(f"{self.server}: {error}",
                                     status_code=resp.status_code)
            self._inc("dp_server_call_transient_retries_total",
                      {"server": self.server})
            last_error = f"{replica.url}: HTTP {resp.status_code}: {error}"
            logger.warning("%s transient: %s", self.server, last_error)
            await asyncio.sleep(0)  # yield; replicas rotate, no backoff needed
        self._outcome("unavailable")
        raise ModelUnavailableError(
            f"{self.server}: transient-retry budget exhausted "
            f"({1 + self.max_transient_retries} attempts); last: {last_error}")
