"""Model-server supervisor (Stage B, L9 machinery side).

Reads the manifest (servers/manifest.json: server -> dir, entry, replicas with
port + GPU pin), spawns each replica in its OWN venv as a long-lived process,
health-checks it, and restarts it on crash or on sustained health failure with a
capped backoff ladder. GPU pinning is CUDA_VISIBLE_DEVICES per replica (gpu: null
pins to CPU by hiding every device) — which physical GPU a replica lands on is
operational, never output-affecting (L4).

The supervisor lives in DP (plan §3) but is imported by nothing in v0 — main.py
wiring is Stage C's. Stage B drives it standalone:

    .venv/bin/python -m app.supervisor --manifest servers/manifest.json

Never touches processes it did not spawn; binds nothing itself. Stdlib + httpx.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

logger = logging.getLogger("data-processing.supervisor")

_DEFAULT_BACKOFF_S = [1.0, 2.0, 5.0, 10.0, 30.0]
_STABLE_RESET_S = 60.0   # healthy this long -> backoff ladder resets
_STOP_GRACE_S = 10.0     # SIGTERM -> SIGKILL grace


@dataclass
class ReplicaSpec:
    server: str
    index: int
    port: int
    gpu: int | None
    host: str
    dir: Path
    python: str
    entry: str
    env: dict[str, str]
    startup_timeout_s: float
    health_interval_s: float
    health_fail_threshold: int
    backoff_s: list[float]

    @property
    def name(self) -> str:
        return f"{self.server}-{self.port}"

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"


@dataclass
class _Replica:
    spec: ReplicaSpec
    proc: asyncio.subprocess.Process | None = None
    state: str = "starting"  # starting | ready | down
    restarts: int = 0
    consecutive_health_fails: int = 0
    backoff_index: int = 0
    ready_since: float | None = None
    spawned_at: float | None = None
    last_error: str | None = None
    log_path: Path | None = None
    log_handle: object = field(default=None, repr=False)


class Supervisor:
    def __init__(self, manifest: dict, base_dir: Path, log_dir: Path | None = None):
        self.base_dir = Path(base_dir)
        self.log_dir = Path(log_dir) if log_dir else self.base_dir / "servers" / "logs"
        self.manifest = manifest
        host = manifest.get("host", "127.0.0.1")
        self._replicas: list[_Replica] = []
        for server_name, server in manifest["servers"].items():
            server_dir = Path(server["dir"])
            if not server_dir.is_absolute():
                server_dir = self.base_dir / server_dir
            for i, rep in enumerate(server["replicas"]):
                spec = ReplicaSpec(
                    server=server_name,
                    index=i,
                    port=int(rep["port"]),
                    gpu=rep.get("gpu"),
                    host=host,
                    dir=server_dir,
                    python=server.get("python", ".venv/bin/python"),
                    entry=server.get("entry", "server.py"),
                    env={str(k): str(v) for k, v in server.get("env", {}).items()},
                    startup_timeout_s=float(server.get("startup_timeout_s", 300)),
                    health_interval_s=float(server.get("health_interval_s", 5)),
                    health_fail_threshold=int(server.get("health_fail_threshold", 3)),
                    backoff_s=[float(b) for b in
                               server.get("restart_backoff_s", _DEFAULT_BACKOFF_S)],
                )
                self._replicas.append(_Replica(spec=spec))
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=2.0))
        self._stopping = False

    @classmethod
    def from_file(cls, manifest_path: str | Path,
                  base_dir: str | Path | None = None) -> "Supervisor":
        """base_dir defaults to the manifest's grandparent — the service root,
        since the manifest lives at <service>/servers/manifest.json."""
        path = Path(manifest_path).resolve()
        manifest = json.loads(path.read_text())
        return cls(manifest, base_dir=Path(base_dir) if base_dir else path.parents[1])

    # -- lifecycle --------------------------------------------------------------

    async def start(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        for replica in self._replicas:
            await self._spawn(replica)

    async def _spawn(self, replica: _Replica) -> None:
        spec = replica.spec
        python = spec.python
        if not os.path.isabs(python):
            python = str(spec.dir / python)
        env = dict(os.environ)
        env.update(spec.env)
        env["DP_SERVER_HOST"] = spec.host
        env["DP_SERVER_PORT"] = str(spec.port)
        # gpu: null -> hide every device (an explicit CPU replica); gpu: N -> pin.
        env["CUDA_VISIBLE_DEVICES"] = "" if spec.gpu is None else str(spec.gpu)
        if replica.log_handle is None:
            replica.log_path = self.log_dir / f"{spec.name}.log"
            replica.log_handle = open(replica.log_path, "ab", buffering=0)
        replica.log_handle.write(
            f"\n--- supervisor spawn {spec.name} pid=? gpu={spec.gpu} "
            f"restarts={replica.restarts} ---\n".encode())
        replica.proc = await asyncio.create_subprocess_exec(
            python, spec.entry,
            cwd=str(spec.dir),
            env=env,
            stdout=replica.log_handle,
            stderr=replica.log_handle,
            # Own session/process group so _kill can killpg the replica's whole
            # tree without touching the supervisor. The flip side: replicas do
            # NOT die with the supervisor on their own — the SIGTERM handler in
            # _main (stop()) is what keeps plain `kill <supervisor>` from
            # orphaning the fleet.
            start_new_session=True,
        )
        replica.state = "starting"
        replica.consecutive_health_fails = 0
        replica.ready_since = None
        replica.spawned_at = time.monotonic()
        logger.info("spawned %s pid=%s gpu=%s port=%s",
                    spec.name, replica.proc.pid, spec.gpu, spec.port)

    async def wait_ready(self, timeout_s: float | None = None) -> None:
        """Block until every replica reports /health 200, or raise TimeoutError
        naming the stragglers. Default timeout: the max startup_timeout_s."""
        deadline = time.monotonic() + (
            timeout_s if timeout_s is not None
            else max(r.spec.startup_timeout_s for r in self._replicas))
        while True:
            pending = []
            for replica in self._replicas:
                if replica.state != "ready":
                    if await self._probe(replica):
                        self._mark_ready(replica)
                    else:
                        pending.append(replica)
            if not pending:
                return
            if time.monotonic() > deadline:
                detail = ", ".join(
                    f"{r.spec.name}: {r.state}"
                    + (f" ({r.last_error})" if r.last_error else "")
                    for r in pending)
                raise TimeoutError(f"replicas not ready: {detail}")
            await asyncio.sleep(min(0.2, min(
                r.spec.health_interval_s for r in self._replicas)))

    async def _probe(self, replica: _Replica) -> bool:
        if replica.proc is not None and replica.proc.returncode is not None:
            replica.state = "down"
            replica.last_error = f"process exited rc={replica.proc.returncode}"
            return False
        try:
            resp = await self._http.get(f"{replica.spec.url}/health")
        except (httpx.HTTPError, OSError) as exc:
            replica.last_error = f"{type(exc).__name__}: {exc}"
            return False
        if resp.status_code == 200:
            replica.last_error = None
            return True
        with contextlib.suppress(ValueError):
            replica.last_error = f"/health {resp.status_code}: {resp.json()}"
        return False

    def _mark_ready(self, replica: _Replica) -> None:
        if replica.state != "ready":
            replica.state = "ready"
            replica.ready_since = time.monotonic()
            logger.info("%s ready", replica.spec.name)
        replica.consecutive_health_fails = 0

    async def run(self) -> None:
        """Monitor forever: restart-on-crash and restart-on-sustained-unhealth,
        with the per-server backoff ladder (reset after a stable-ready period)."""
        await asyncio.gather(*(self._monitor(r) for r in self._replicas))

    async def _monitor(self, replica: _Replica) -> None:
        spec = replica.spec
        while not self._stopping:
            await asyncio.sleep(spec.health_interval_s)
            if self._stopping:
                return
            if replica.proc is not None and replica.proc.returncode is not None:
                await self._restart(replica,
                                    f"exited rc={replica.proc.returncode}")
                continue
            if await self._probe(replica):
                if (replica.ready_since is not None
                        and time.monotonic() - replica.ready_since > _STABLE_RESET_S):
                    replica.backoff_index = 0
                self._mark_ready(replica)
                continue
            replica.consecutive_health_fails += 1
            if (replica.state == "ready"
                    and replica.consecutive_health_fails >= spec.health_fail_threshold):
                await self._kill(replica)
                await self._restart(
                    replica,
                    f"health failed x{replica.consecutive_health_fails}: "
                    f"{replica.last_error}")
            elif (replica.state == "starting"
                    and replica.spawned_at is not None
                    and time.monotonic() - replica.spawned_at
                    > spec.startup_timeout_s):
                # A (re)spawned replica that never goes ready — e.g. a hung
                # warmup after a crash-restart — must be recycled at the
                # startup deadline, not monitored in "starting" forever.
                await self._kill(replica)
                await self._restart(
                    replica,
                    f"warmup timeout after {spec.startup_timeout_s:.0f}s: "
                    f"{replica.last_error}")

    async def _restart(self, replica: _Replica, why: str) -> None:
        spec = replica.spec
        replica.state = "down"
        backoff = spec.backoff_s[min(replica.backoff_index, len(spec.backoff_s) - 1)]
        replica.backoff_index += 1
        replica.restarts += 1
        logger.warning("%s down (%s); restart #%s in %.1fs",
                       spec.name, why, replica.restarts, backoff)
        await asyncio.sleep(backoff)
        if not self._stopping:
            await self._spawn(replica)

    async def _kill(self, replica: _Replica) -> None:
        proc = replica.proc
        if proc is None or proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        try:
            await asyncio.wait_for(proc.wait(), timeout=_STOP_GRACE_S)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            await proc.wait()

    async def stop(self) -> None:
        self._stopping = True
        for replica in self._replicas:
            await self._kill(replica)
            replica.state = "down"
            if replica.log_handle is not None:
                replica.log_handle.close()
                replica.log_handle = None
        await self._http.aclose()

    # -- introspection ----------------------------------------------------------

    def status(self) -> dict:
        out: dict[str, list[dict]] = {}
        for replica in self._replicas:
            out.setdefault(replica.spec.server, []).append({
                "port": replica.spec.port,
                "gpu": replica.spec.gpu,
                "pid": replica.proc.pid if replica.proc else None,
                "state": replica.state,
                "restarts": replica.restarts,
                "last_error": replica.last_error,
                "log": str(replica.log_path) if replica.log_path else None,
            })
        return out


async def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="DP model-server supervisor (Stage B standalone runner)")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--base-dir", default=None,
                        help="service root; default: manifest's grandparent")
    parser.add_argument("--status-only", action="store_true",
                        help="start, wait ready, print status JSON, stop, exit")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    sup = Supervisor.from_file(args.manifest, base_dir=args.base_dir)

    # Replicas run in their own sessions (see _spawn) and would survive this
    # process dying — a graceful-stop signal handler is what makes plain
    # `kill <supervisor>` take the fleet down with it.
    stop_requested = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_requested.set)

    await sup.start()
    try:
        await sup.wait_ready()
        print(json.dumps(sup.status(), indent=2), flush=True)
        if args.status_only:
            return 0
        run_task = asyncio.create_task(sup.run())
        stop_task = asyncio.create_task(stop_requested.wait())
        done, _pending = await asyncio.wait(
            {run_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
        if run_task in done:
            run_task.result()  # surface unexpected monitor crashes
        else:
            run_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await run_task
            logger.info("stop signal received; shutting the fleet down")
        return 0
    except TimeoutError as exc:
        print(f"supervisor: {exc}", file=sys.stderr, flush=True)
        print(json.dumps(sup.status(), indent=2), flush=True)
        return 1
    finally:
        await sup.stop()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(sys.argv[1:])))
