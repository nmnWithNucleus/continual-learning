"""The DP_E2E heal drill (L8 case 4 against the REAL fleet).

Audio chunk with the ast server DOWN → the record ships with an acoustic hole
and the ledger shows `failed` (+ its cancelled cone — empty for acoustic, which
has no dependents); bring ast up; redeliver the same envelope → the healed
record is BYTE-IDENTICAL to a clean-fleet run of the same version; the ledger
reads all-ok; redeliver again → a pure skip with ZERO server calls.

Drill discipline (same as test_e2e_real): gated on DP_E2E=1, manifest GPUs,
foreign jobs untouched (per-GPU before/after deltas, never absolute zero),
v0 :8085 healthy before and after, no fleet port left listening. Storage is the
LOCAL STUB sink (MockTransport — structurally incapable of reaching :8083).

The partial fleet rides two FILTERED manifests written to a temp dir (the full
manifest minus ast, then ast alone) driven by two supervisor processes; the app
under test keeps the DEFAULT full manifest, so its model clients cover ast the
whole time — while ast is down those calls fail transient → the optional stage
holes (exactly the production posture during a server outage).

This file sorts AFTER test_e2e_real.py so that module's fleet (the full
manifest on the same ports) is torn down before this drill claims them.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from app import schemas
from app.models import C2RecordV1

from tests.fake_storage import FakeStorage
from tests.test_audio_stages import ASR_SLOT_REAL, C1_REAL, TRANSCRIPT_SLOT_REAL
from tests.test_e2e_real import (
    _AUDIO_FIXTURE,
    _MANIFEST,
    _SERVICE,
    _c1_for,
    _gpu_used_mib,
    _health_ok,
    _listening,
    _manifest_ports,
    _v0_health,
)

pytestmark = pytest.mark.skipif(
    os.getenv("DP_E2E") != "1",
    reason="real-fleet heal drill: set DP_E2E=1 (spawns model servers on GPUs 2-7)",
)


def _spawn_supervisor(manifest_path, ports, procs, timeout=360):
    # --base-dir: the filtered manifests live in a tmp dir, but the server dirs
    # they name are relative to the SERVICE root (the supervisor's default base
    # is the manifest's grandparent, which would resolve into the tmp dir).
    proc = subprocess.Popen(
        [str(_SERVICE / ".venv" / "bin" / "python"), "-m", "app.supervisor",
         "--manifest", str(manifest_path), "--base-dir", str(_SERVICE)],
        cwd=_SERVICE, start_new_session=True,
    )
    procs.append(proc)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if all(_health_ok(p) for p in ports):
            return proc
        assert proc.poll() is None, "supervisor died during spawn"
        time.sleep(2)
    pytest.fail(f"servers on {ports} never became healthy within {timeout}s")


@pytest.fixture(scope="module")
def heal_fleet(tmp_path_factory):
    """The split fleet: everything-but-ast up front; ast started on demand via
    the yielded ``start_ast`` callable. Teardown covers both supervisors."""
    assert _v0_health() == 200, "v0 not healthy before the drill"
    all_ports = _manifest_ports()
    assert not any(_listening(p) for p in all_ports), "fleet ports already in use"
    gpu_before = _gpu_used_mib()

    full = json.loads(_MANIFEST.read_text())
    tmp = tmp_path_factory.mktemp("heal-manifests")
    no_ast = {**full, "servers": {k: v for k, v in full["servers"].items()
                                  if k != "ast"}}
    ast_only = {**full, "servers": {"ast": full["servers"]["ast"]}}
    p_no_ast = tmp / "manifest_no_ast.json"
    p_ast = tmp / "manifest_ast_only.json"
    p_no_ast.write_text(json.dumps(no_ast))
    p_ast.write_text(json.dumps(ast_only))
    ports_no_ast = [r["port"] for spec in no_ast["servers"].values()
                    for r in spec["replicas"]]
    ports_ast = [r["port"] for r in full["servers"]["ast"]["replicas"]]

    procs: list[subprocess.Popen] = []
    started_ast = {"done": False}

    def start_ast():
        if not started_ast["done"]:
            _spawn_supervisor(p_ast, ports_ast, procs)
            started_ast["done"] = True

    try:
        _spawn_supervisor(p_no_ast, ports_no_ast, procs)
        yield {"start_ast": start_ast, "ports_ast": ports_ast}
    finally:
        for proc in procs:
            proc.send_signal(signal.SIGTERM)
        for proc in procs:
            try:
                proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=30)
        deadline = time.time() + 30
        while time.time() < deadline and any(_listening(p) for p in all_ports):
            time.sleep(1)
        assert not any(_listening(p) for p in all_ports), "fleet ports still listening"
        gpu_after = _gpu_used_mib()
        if gpu_before and gpu_after:
            leaks = {i: (gpu_before.get(i, 0), used)
                     for i, used in gpu_after.items()
                     if used > gpu_before.get(i, 0) + 256}
            assert not leaks, (
                f"per-GPU memory not released after teardown "
                f"(before, after) MiB: {leaks}"
            )
        assert _v0_health() == 200, "v0 unhealthy after the drill"


def _server_calls_snapshot(client) -> list[str]:
    return sorted(line for line in client.get("/metrics").text.splitlines()
                  if line.startswith("dp_server_calls_total"))


def test_heal_drill_hole_then_heal_then_skip(heal_fleet, monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_URL", "http://stub.sink")
    monkeypatch.setenv("DP_VAR_DIR", str(tmp_path / "var1"))
    monkeypatch.delenv("DP_SUPERVISOR", raising=False)
    from app.main import create_app

    fs1 = FakeStorage()
    app1 = create_app()
    app1.state.storage._transport = fs1.transport()
    with TestClient(app1) as c:
        j = app1.state.journal
        c1 = _c1_for(fs1, _AUDIO_FIXTURE, C1_REAL, chunk_id="e2e-heal-1",
                     blob_ref="raw/e2e/heal-1")

        # ---- Phase 1: ast DOWN -> the record ships with an acoustic hole ----
        resp = c.post("/ingest", json=c1)
        assert resp.status_code == 200, resp.text
        (rid,) = resp.json()["record_ids"]
        record = fs1.record_posts[0]
        assert schemas.validate_c2(record) == []
        C2RecordV1.model_validate(record)
        slots = record["content"]["slots"]
        assert "acoustic" not in slots                    # the hole (L7/L11)
        assert slots["asr"] == ASR_SLOT_REAL              # rest of the graph clean
        assert slots["transcript"] == TRANSCRIPT_SLOT_REAL
        row = j.done_row("e2e-heal-1")
        assert row["stage_status"]["acoustic"] == "failed"
        cone = [s for s, v in row["stage_status"].items() if v == "cancelled"]
        assert cone == []                                 # acoustic has no dependents
        assert all(v == "ok" for s, v in row["stage_status"].items()
                   if s != "acoustic")

        # ---- Phase 2: bring ast up; redeliver -> heal, same record_id -------
        heal_fleet["start_ast"]()
        resp2 = c.post("/ingest", json=c1)
        assert resp2.status_code == 200, resp2.text
        assert resp2.json()["record_ids"] == [rid]        # L3: same identity
        assert len(fs1.record_posts) == 2
        healed = fs1.record_posts[1]
        assert healed["record_id"] == rid
        assert healed["content"]["slots"]["acoustic"] == {
            "version": "acoustic.v1-ast.v1", "values": []}  # the real golden fold
        row = j.done_row("e2e-heal-1")
        assert all(v == "ok" for v in row["stage_status"].values())
        assert row["heal_attempts"] == 0                  # a green heal: no charge
        healed_bytes = fs1.record_posts_raw[1]

        # ---- Phase 3: byte-identical to a never-holed clean-fleet run -------
        monkeypatch.setenv("DP_VAR_DIR", str(tmp_path / "var2"))  # fresh journal
        fs2 = FakeStorage()
        fs2.add_blob(c1["blob_ref"], _AUDIO_FIXTURE.read_bytes())
        app2 = create_app()
        app2.state.storage._transport = fs2.transport()
        with TestClient(app2) as clean:
            assert clean.post("/ingest", json=c1).status_code == 200
        assert healed_bytes == fs2.record_posts_raw[0]

        # ---- Phase 4: redeliver again -> pure skip, ZERO server calls -------
        calls_before = _server_calls_snapshot(c)
        # Review round: the snapshot must be provably LIVE, or the equality
        # below is vacuously satisfiable by a dead family (metrics render
        # nothing for a sample-less family; ModelClient swallows recording
        # errors). Phases 1-2 made real whisper calls through this app.
        assert calls_before, "dp_server_calls_total family absent — wiring dead"
        assert any('server="whisper"' in line and 'outcome="ok"' in line
                   and not line.rstrip().endswith(" 0")
                   for line in calls_before), (
            "no nonzero whisper ok count after real calls — family not recording:\n"
            + "\n".join(calls_before))
        gets_before = len(fs1.blob_gets)
        resp3 = c.post("/ingest", json=c1)
        assert resp3.status_code == 200
        assert resp3.json()["record_ids"] == [rid]
        assert len(fs1.record_posts) == 2                 # nothing POSTed
        assert len(fs1.blob_gets) == gets_before          # blob never re-pulled
        assert _server_calls_snapshot(c) == calls_before  # zero server calls
