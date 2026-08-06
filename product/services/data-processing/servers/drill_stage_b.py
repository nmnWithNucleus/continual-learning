"""Stage B exit drill (§8): the whole fleet under the supervisor on this node.

    .venv/bin/python servers/drill_stage_b.py [--manifest servers/manifest.json]

Phases, all against the REAL manifest (real ports, real GPUs):
  1. supervisor spawns every replica of all four servers; wait ready.
  2. identity: a ModelClient per server verifies /health identity against the
     manifest's expected_identity (the L4 check).
  3. golden smoke UNDER THE SUPERVISOR: each server's committed fixture input is
     posted through the ModelClient; the result is compared to the committed
     golden with that server's declared comparison mode (exact where the
     per-server provenance measured bit-stability, tolerance otherwise).
  4. kill-one-replica: SIGKILL one replica per server, immediately re-fire the
     golden call (the client must succeed via the surviving replica), then wait
     for the supervisor to respawn the killed replica and re-verify identity.

Prints a JSON verdict; exit 0 only if every phase passed. Kills nothing it did
not spawn; binds nothing itself.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import signal
import sys
import time
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT))

from app.model_client import ModelClient  # noqa: E402
from app.supervisor import Supervisor  # noqa: E402

# Per-server golden smoke: fixture input, request params, committed golden file,
# and how to compare (set from each server's measured determinism verdict).
SMOKE = {
    "whisper": {
        "fixture": "servers/whisper/tests/fixtures/speech_two_speakers.webm",
        "codec": "audio/webm",
        "params": {"task": "transcribe", "beam_size": 1, "language": "en", "vad": True},
        "golden": "servers/whisper/tests/fixtures/golden_transcribe.json",
        "compare": "exact",
    },
    "pyannote": {
        "fixture": "servers/pyannote/tests/fixtures/speech_two_speakers.webm",
        "codec": "audio/webm",
        "params": {"span_seconds": 6.496},
        "golden": "servers/pyannote/tests/fixtures/golden_diarize.json",
        "compare": "exact",
    },
    "ast": {
        "fixture": "servers/ast/tests/fixtures/speech_two_speakers.webm",
        "codec": "audio/webm",
        "params": {"top_k": 20},
        "golden": "servers/ast/tests/fixtures/golden_tags.json",
        "compare": "exact",  # measured byte-identical (see ast PROVENANCE.md)
    },
    "ocr": {
        "fixture": "servers/ocr/tests/fixtures/screen_planning_notes.jpg",
        "codec": "image/jpeg",
        "params": {},
        "golden": "servers/ocr/tests/fixtures/golden_regions.json",
        "compare": "exact",
    },
}


def compare_result(mode: str, got: dict, golden: dict) -> str | None:
    """Return None if equivalent under `mode`, else a description of the diff."""
    if mode == "exact":
        if got == golden:
            return None
        return "byte-level mismatch vs golden"
    if mode == "ast_tags":
        # Tolerance mode justified in servers/ast/tests/fixtures/PROVENANCE.md:
        # label set exact, per-label score within atol; order may flip on ties.
        got_map = {t["label"]: t["score"] for t in got.get("tags", [])}
        gold_map = {t["label"]: t["score"] for t in golden.get("tags", [])}
        if set(got_map) != set(gold_map):
            return (f"label set differs: only-got={sorted(set(got_map)-set(gold_map))} "
                    f"only-golden={sorted(set(gold_map)-set(got_map))}")
        worst = max((abs(got_map[k] - gold_map[k]) for k in gold_map), default=0.0)
        if worst > 1e-3:
            return f"score drift {worst:.2e} exceeds atol 1e-3"
        return None
    raise ValueError(f"unknown compare mode {mode!r}")


def sanity_check(server: str, result: dict) -> str | None:
    """Shape checks that hold regardless of comparison mode."""
    try:
        if server == "whisper":
            assert isinstance(result["text"], str) and result["segments"]
        elif server == "pyannote":
            assert isinstance(result["turns"], list)
        elif server == "ast":
            assert result["tags"] and all("label" in t for t in result["tags"])
        elif server == "ocr":
            assert isinstance(result["regions"], list) and result["regions"]
    except (AssertionError, KeyError, TypeError) as exc:
        return f"shape check failed: {type(exc).__name__}: {exc}"
    return None


async def drill(manifest_path: Path) -> dict:
    manifest = json.loads(manifest_path.read_text())
    sup = Supervisor(manifest, base_dir=SERVICE_ROOT)
    verdict: dict = {"phases": {}, "ok": False}
    monitor = None
    clients: dict[str, ModelClient] = {}
    try:
        # -- phase 1: spawn + ready ------------------------------------------
        t0 = time.monotonic()
        await sup.start()
        await sup.wait_ready()
        verdict["phases"]["spawn_ready"] = {
            "ok": True, "seconds": round(time.monotonic() - t0, 1),
            "status": sup.status()}
        monitor = asyncio.create_task(sup.run())

        # -- phase 2: identity ------------------------------------------------
        identity_report = {}
        identity_ok = True
        for name, server in manifest["servers"].items():
            endpoints = [f"http://{manifest['host']}:{r['port']}"
                         for r in server["replicas"]]
            clients[name] = ModelClient(
                name, endpoints,
                expected_identity=server.get("expected_identity", {}),
                call_timeout_s=float(server.get("client_timeout_s", 120)),
                max_transient_retries=2)
            report = await clients[name].verify_identity()
            identity_report[name] = report
            if not all(v == "ok" for v in report.values()):
                identity_ok = False
        verdict["phases"]["identity"] = {"ok": identity_ok, "report": identity_report}

        # -- phase 3: golden smoke under the supervisor -----------------------
        smoke_report = {}
        smoke_ok = True
        for name, spec in SMOKE.items():
            payload = {
                "input_b64": base64.b64encode(
                    (SERVICE_ROOT / spec["fixture"]).read_bytes()).decode(),
                "codec": spec["codec"],
                "params": spec["params"],
            }
            t0 = time.monotonic()
            result = await clients[name].infer(payload)
            elapsed = round(time.monotonic() - t0, 2)
            golden = json.loads((SERVICE_ROOT / spec["golden"]).read_text())
            problem = (sanity_check(name, result)
                       or compare_result(spec["compare"], result, golden))
            smoke_report[name] = {"ok": problem is None, "seconds": elapsed,
                                  "compare": spec["compare"], "problem": problem}
            if problem:
                smoke_ok = False
        verdict["phases"]["golden_smoke"] = {"ok": smoke_ok, "report": smoke_report}

        # -- phase 4: kill one replica per server -----------------------------
        kill_report = {}
        kill_ok = True
        for name, spec in SMOKE.items():
            replica = sup.status()[name][0]
            os.kill(replica["pid"], signal.SIGKILL)
            # immediately: client must succeed via the surviving replica
            payload = {
                "input_b64": base64.b64encode(
                    (SERVICE_ROOT / spec["fixture"]).read_bytes()).decode(),
                "codec": spec["codec"],
                "params": spec["params"],
            }
            try:
                t0 = time.monotonic()
                result = await clients[name].infer(payload)
                retry_seconds = round(time.monotonic() - t0, 2)
                retry_problem = sanity_check(name, result)
            except Exception as exc:
                retry_problem = f"{type(exc).__name__}: {exc}"
                retry_seconds = None
            # then: supervisor must respawn the killed replica
            deadline = time.monotonic() + 180
            respawned = False
            while time.monotonic() < deadline:
                state = sup.status()[name][0]
                if state["state"] == "ready" and state["pid"] != replica["pid"]:
                    respawned = True
                    break
                await asyncio.sleep(1.0)
            entry = {
                "killed_pid": replica["pid"],
                "retry_during_outage_ok": retry_problem is None,
                "retry_seconds": retry_seconds,
                "retry_problem": retry_problem,
                "supervisor_respawned": respawned,
                "restarts_now": sup.status()[name][0]["restarts"],
            }
            entry["ok"] = entry["retry_during_outage_ok"] and respawned
            kill_report[name] = entry
            if not entry["ok"]:
                kill_ok = False
        verdict["phases"]["kill_one_replica"] = {"ok": kill_ok, "report": kill_report}

        verdict["ok"] = all(p["ok"] for p in verdict["phases"].values())
        return verdict
    finally:
        for client in clients.values():
            await client.aclose()
        if monitor:
            monitor.cancel()
        await sup.stop()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default=str(SERVICE_ROOT / "servers" / "manifest.json"))
    args = parser.parse_args()
    verdict = asyncio.run(drill(Path(args.manifest)))
    print(json.dumps(verdict, indent=2))
    return 0 if verdict["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
