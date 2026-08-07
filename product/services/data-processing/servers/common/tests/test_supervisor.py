"""Tests for app/supervisor.py: manifest -> spawn, health-check,
restart-on-crash, GPU pinning env, clean stop.

Replicas here are real framework servers (fake_server.py) spawned exactly the way
the production manifest spawns model servers.
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import time
from pathlib import Path

import httpx
import pytest

from app.supervisor import Supervisor

TESTS_DIR = Path(__file__).resolve().parent
COMMON_VENV_PY = str(TESTS_DIR.parent / ".venv" / "bin" / "python")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def manifest(replicas: list[dict], env: dict | None = None, name: str = "fake") -> dict:
    return {
        "manifest_version": 1,
        "host": "127.0.0.1",
        "servers": {
            name: {
                "dir": str(TESTS_DIR),
                "python": COMMON_VENV_PY,
                "entry": "fake_server.py",
                "replicas": replicas,
                "startup_timeout_s": 30,
                "health_interval_s": 0.2,
                "health_fail_threshold": 3,
                "restart_backoff_s": [0.2, 0.5, 1.0],
                "env": env or {},
                "expected_identity": {"model_name": "fake-model"},
            }
        },
    }


def run(coro):
    return asyncio.run(coro)


async def wait_for(predicate, timeout_s: float, what: str):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"timed out waiting for {what}")


def test_spawns_replicas_and_reports_ready(tmp_path):
    ports = [free_port(), free_port()]
    m = manifest([{"port": ports[0], "gpu": None}, {"port": ports[1], "gpu": None}])

    async def go():
        sup = Supervisor(m, base_dir=TESTS_DIR, log_dir=tmp_path)
        try:
            await sup.start()
            await sup.wait_ready()
            status = sup.status()
            replicas = status["fake"]
            assert [r["state"] for r in replicas] == ["ready", "ready"]
            assert all(isinstance(r["pid"], int) for r in replicas)
            assert [r["restarts"] for r in replicas] == [0, 0]
            async with httpx.AsyncClient() as client:
                for port in ports:
                    health = await client.get(f"http://127.0.0.1:{port}/health")
                    assert health.status_code == 200
                    assert health.json()["identity"]["model_name"] == "fake-model"
        finally:
            await sup.stop()
        for port in ports:
            with pytest.raises(httpx.ConnectError):
                async with httpx.AsyncClient() as client:
                    await client.get(f"http://127.0.0.1:{port}/health", timeout=1.0)

    run(go())


def test_gpu_pin_is_cuda_visible_devices(tmp_path):
    port_gpu, port_cpu = free_port(), free_port()
    m = manifest([{"port": port_gpu, "gpu": 5}, {"port": port_cpu, "gpu": None}])

    async def go():
        sup = Supervisor(m, base_dir=TESTS_DIR, log_dir=tmp_path)
        try:
            await sup.start()
            await sup.wait_ready()
            async with httpx.AsyncClient() as client:
                gpu_identity = (await client.get(
                    f"http://127.0.0.1:{port_gpu}/health")).json()["identity"]
                cpu_identity = (await client.get(
                    f"http://127.0.0.1:{port_cpu}/health")).json()["identity"]
            assert gpu_identity["cuda_visible_devices"] == "5"
            assert cpu_identity["cuda_visible_devices"] == ""  # CPU replica sees no GPU
        finally:
            await sup.stop()

    run(go())


def test_killed_replica_is_restarted_and_serves_again(tmp_path):
    port = free_port()
    m = manifest([{"port": port, "gpu": None}])

    async def go():
        sup = Supervisor(m, base_dir=TESTS_DIR, log_dir=tmp_path)
        monitor_task = None
        try:
            await sup.start()
            await sup.wait_ready()
            monitor_task = asyncio.create_task(sup.run())
            old_pid = sup.status()["fake"][0]["pid"]

            os.kill(old_pid, signal.SIGKILL)

            await wait_for(
                lambda: sup.status()["fake"][0]["state"] == "ready"
                and sup.status()["fake"][0]["pid"] != old_pid,
                timeout_s=20, what="replica restart")
            replica = sup.status()["fake"][0]
            assert replica["restarts"] == 1
            async with httpx.AsyncClient() as client:
                resp = await client.post(f"http://127.0.0.1:{port}/infer",
                                         json={"params": {}})
            assert resp.status_code == 200
            assert resp.json()["result"]["pid"] == replica["pid"]
        finally:
            if monitor_task:
                monitor_task.cancel()
            await sup.stop()

    run(go())


def test_crash_looping_replica_keeps_restarting_with_backoff(tmp_path):
    port = free_port()
    m = manifest([{"port": port, "gpu": None}], env={"FAKE_CRASH_AT_START": "1"})

    async def go():
        sup = Supervisor(m, base_dir=TESTS_DIR, log_dir=tmp_path)
        monitor_task = None
        try:
            await sup.start()
            monitor_task = asyncio.create_task(sup.run())
            await wait_for(lambda: sup.status()["fake"][0]["restarts"] >= 2,
                           timeout_s=20, what="two restarts of a crash-looper")
            assert sup.status()["fake"][0]["state"] != "ready"
        finally:
            if monitor_task:
                monitor_task.cancel()
            await sup.stop()

    run(go())


def test_load_failure_exits_loud_and_wait_ready_times_out(tmp_path):
    """A backend whose load() raises must fail the process (runner exit 3), and
    wait_ready must surface that instead of hanging."""
    port = free_port()
    m = manifest([{"port": port, "gpu": None}], env={"FAKE_LOAD_ERROR": "1"})
    m["servers"]["fake"]["startup_timeout_s"] = 3

    async def go():
        sup = Supervisor(m, base_dir=TESTS_DIR, log_dir=tmp_path)
        try:
            await sup.start()
            with pytest.raises(TimeoutError):
                await sup.wait_ready()
        finally:
            await sup.stop()

    run(go())


def test_sigterm_reaps_all_replicas(tmp_path):
    """`kill <supervisor>` must take the fleet down with it: replicas run in their
    own sessions (so the supervisor can killpg them without dying itself), which
    means ONLY an explicit SIGTERM handler stands between plain `kill` and a node
    full of orphaned model servers."""
    import subprocess

    port = free_port()
    m = manifest([{"port": port, "gpu": None}])
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(m))
    service_root = TESTS_DIR.parents[2]

    proc = subprocess.Popen(
        [COMMON_VENV_PY, "-m", "app.supervisor",
         "--manifest", str(manifest_path), "--base-dir", str(service_root)],
        cwd=str(service_root),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 30
        replica_pid = None
        while time.monotonic() < deadline:
            try:
                resp = httpx.post(f"http://127.0.0.1:{port}/infer",
                                  json={"params": {}}, timeout=2.0)
                if resp.status_code == 200:
                    replica_pid = resp.json()["result"]["pid"]
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        assert replica_pid is not None, "fleet never came up under the CLI supervisor"

        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=20) is not None

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(replica_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            os.kill(replica_pid, signal.SIGKILL)  # do not leak it beyond the test
            raise AssertionError(f"replica {replica_pid} orphaned after SIGTERM")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)


def test_crash_restart_into_hung_warmup_is_recycled(tmp_path):
    """A replica that crashes and then hangs in warmup on the restart (health 503
    forever) must be killed and respawned when startup_timeout_s expires — not
    monitored forever in 'starting'."""
    port = free_port()
    marker = tmp_path / "crashed-once.marker"
    m = manifest([{"port": port, "gpu": None}],
                 env={"FAKE_CRASH_THEN_HANG_MARKER": str(marker)})
    m["servers"]["fake"]["startup_timeout_s"] = 2

    async def go():
        sup = Supervisor(m, base_dir=TESTS_DIR, log_dir=tmp_path)
        monitor_task = None
        try:
            await sup.start()
            monitor_task = asyncio.create_task(sup.run())
            # restart #1 = the crash; restart #2 proves the hung warmup was
            # detected at the deadline and recycled.
            await wait_for(lambda: sup.status()["fake"][0]["restarts"] >= 2,
                           timeout_s=30, what="warmup-timeout recycle")
            assert sup.status()["fake"][0]["state"] != "ready"
        finally:
            if monitor_task:
                monitor_task.cancel()
            await sup.stop()

    run(go())


def test_replica_logs_are_written(tmp_path):
    port = free_port()
    m = manifest([{"port": port, "gpu": None}])

    async def go():
        sup = Supervisor(m, base_dir=TESTS_DIR, log_dir=tmp_path)
        try:
            await sup.start()
            await sup.wait_ready()
        finally:
            await sup.stop()

    run(go())
    logs = list(tmp_path.glob("fake-*.log"))
    assert logs, "no replica log written"
    assert "Uvicorn running" in logs[0].read_text()
