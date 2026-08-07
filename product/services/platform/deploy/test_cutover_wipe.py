#!/usr/bin/env python3
"""Scratch-world evidence suite for the Stage F cutover kit (WP-F0c; committed
at the GATE 1 verification round so the repo can reproduce the evidence).

Builds a throwaway world mimicking every real ledger shape, then asserts the
wipe script's contract: dry-run mutates nothing; wet empties exactly the WIPE
set and cannot touch KEEP; an unclassified table or subdir aborts both modes; a
MISSING wipe-target path aborts both modes unless --allow-missing; wet refuses
while the freeze ports are still listening (and, under --recording-claims
reset, while the recording port is); the disposition flag does what the
manifest says. A final section guards run_vllm.sh's model/revision pins against
env-file override. Run: python3 test_cutover_wipe.py
"""
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

DEPLOY = Path(__file__).resolve().parent
SCRIPT = DEPLOY / "cutover_wipe.py"
PY = sys.executable


def build_world(root: Path) -> dict:
    storage_db = root / "storage" / "dev.db"
    storage_db.parent.mkdir(parents=True)
    con = sqlite3.connect(storage_db)
    con.executescript("""
      CREATE TABLE turns(id INTEGER PRIMARY KEY, body TEXT);
      CREATE TABLE model_directory(id INTEGER PRIMARY KEY, body TEXT);
      CREATE TABLE raw_blobs(chunk_id TEXT PRIMARY KEY, blob_ref TEXT);
      CREATE TABLE context_records(record_id TEXT PRIMARY KEY, body TEXT);
      CREATE TABLE user_profiles(user_id TEXT PRIMARY KEY, home_tz TEXT);
      CREATE TABLE training_windows(window_id TEXT PRIMARY KEY, outcome TEXT);
      CREATE TABLE day_logs(window_id TEXT PRIMARY KEY, body TEXT);
      INSERT INTO turns VALUES (1, 't');
      INSERT INTO model_directory VALUES (1, 'base');
      INSERT INTO raw_blobs VALUES ('c1', 'raw/x');
      INSERT INTO context_records VALUES ('r1', '{}');
      INSERT INTO context_records VALUES ('r2', '{}');
      INSERT INTO user_profiles VALUES ('u1', 'UTC');
      INSERT INTO training_windows VALUES ('w1', 'published');
      INSERT INTO day_logs VALUES ('w1', '{}');
    """)
    con.commit(); con.close()

    dp_db = root / "dp" / "dp.db"
    dp_db.parent.mkdir(parents=True)
    con = sqlite3.connect(dp_db)
    con.executescript("""
      CREATE TABLE pending(chunk_id TEXT PRIMARY KEY, epoch INTEGER);
      CREATE TABLE processed(chunk_id TEXT PRIMARY KEY, record_ids TEXT);
      INSERT INTO pending VALUES ('c9', 1);
      INSERT INTO processed VALUES ('c1', '["r1"]');
    """)
    con.commit(); con.close()

    rec_db = root / "recording" / "ledger.db"
    rec_db.parent.mkdir(parents=True)
    con = sqlite3.connect(rec_db)
    con.executescript("""
      CREATE TABLE captures(capture_id TEXT PRIMARY KEY);
      CREATE TABLE segments(segment_id TEXT PRIMARY KEY);
      CREATE TABLE streams(stream_id TEXT PRIMARY KEY);
      CREATE TABLE chunks(chunk_id TEXT PRIMARY KEY, dp_state TEXT);
      INSERT INTO captures VALUES ('cap1');
      INSERT INTO chunks VALUES ('c1', 'processed');
      INSERT INTO chunks VALUES ('c2', 'accepted');
      INSERT INTO chunks VALUES ('c3', NULL);
    """)
    con.commit(); con.close()

    raw_store = root / "storage" / "raw_store" / "aa"
    raw_store.mkdir(parents=True)
    (raw_store / "blob1").write_bytes(b"sacred-bytes")

    reservoir = root / "storage" / "reservoir" / "u1"
    reservoir.mkdir(parents=True)
    (reservoir / "w1.corpus.txt").write_text("derived corpus")

    cvar = root / "continuum" / "var"
    (cvar / "journal" / "u1").mkdir(parents=True)
    (cvar / "journal" / "u1" / "w1.json").write_text("{}")
    (cvar / "diag").mkdir(parents=True)
    (cvar / "diag" / "keepme.txt").write_text("research artifact")

    return {
        "storage_db": storage_db, "dp_db": dp_db, "rec_db": rec_db,
        "raw_store": root / "storage" / "raw_store",
        "reservoir": root / "storage" / "reservoir", "continuum_var": cvar,
    }


def run(world, *args, ports="1"):   # port 1: never listening
    cmd = [PY, str(SCRIPT),
           "--storage-db", str(world["storage_db"]),
           "--dp-db", str(world["dp_db"]),
           "--recording-db", str(world["rec_db"]),
           "--reservoir-dir", str(world["reservoir"]),
           "--continuum-var", str(world["continuum_var"]),
           "--freeze-ports", ports, *args]
    return subprocess.run(cmd, capture_output=True, text=True)


def count(db, table):
    con = sqlite3.connect(db)
    n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    con.close()
    return n


def dp_states(db):
    con = sqlite3.connect(db)
    rows = dict(con.execute(
        "SELECT COALESCE(dp_state,'NULL'), COUNT(*) FROM chunks GROUP BY 1").fetchall())
    con.close()
    return rows


failures = []


def check(name, cond, detail=""):
    print(("  ok  " if cond else "  FAIL") + f" {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(name)


with tempfile.TemporaryDirectory() as td:
    world = build_world(Path(td))

    # 1. dry-run (default): exit 0, prints a manifest, mutates nothing
    r = run(world)
    check("dry-run exits 0", r.returncode == 0, r.stderr[-500:])
    check("dry-run prints a rows-per-table manifest",
          "context_records" in r.stdout and "WIPE" in r.stdout and "KEEP" in r.stdout)
    check("dry-run left context_records", count(world["storage_db"], "context_records") == 2)
    check("dry-run left dp processed", count(world["dp_db"], "processed") == 1)
    check("dry-run left the reservoir file",
          (world["reservoir"] / "u1" / "w1.corpus.txt").exists())

    # 2. wet without --execute is impossible; wet refuses while a freeze port listens
    s = socket.socket(); s.bind(("127.0.0.1", 0)); s.listen(1)
    port = s.getsockname()[1]
    r = run(world, "--execute", ports=str(port))
    check("wet refuses while a freeze port is listening",
          r.returncode != 0 and "listening" in (r.stdout + r.stderr))
    s.close()
    check("refused wet mutated nothing", count(world["storage_db"], "context_records") == 2)

    # 3. wet, default recording disposition (keep-claims)
    r = run(world, "--execute")
    check("wet exits 0", r.returncode == 0, (r.stdout + r.stderr)[-500:])
    check("WIPE: context_records emptied", count(world["storage_db"], "context_records") == 0)
    check("WIPE: day_logs emptied", count(world["storage_db"], "day_logs") == 0)
    check("WIPE: training_windows emptied", count(world["storage_db"], "training_windows") == 0)
    check("WIPE: dp processed emptied", count(world["dp_db"], "processed") == 0)
    check("WIPE: dp pending emptied", count(world["dp_db"], "pending") == 0)
    check("KEEP: turns intact", count(world["storage_db"], "turns") == 1)
    check("KEEP: raw_blobs intact", count(world["storage_db"], "raw_blobs") == 1)
    check("KEEP: user_profiles intact", count(world["storage_db"], "user_profiles") == 1)
    check("KEEP: model_directory intact", count(world["storage_db"], "model_directory") == 1)
    check("KEEP: raw_store bytes intact",
          (world["raw_store"] / "aa" / "blob1").read_bytes() == b"sacred-bytes")
    check("KEEP: recording captures intact", count(world["rec_db"], "captures") == 1)
    check("keep-claims: dp_state untouched",
          dp_states(world["rec_db"]) == {"processed": 1, "accepted": 1, "NULL": 1})
    check("WIPE: reservoir file gone",
          not (world["reservoir"] / "u1" / "w1.corpus.txt").exists())
    check("WIPE: continuum journal gone",
          not (world["continuum_var"] / "journal").exists())
    check("KEEP: continuum diag intact",
          (world["continuum_var"] / "diag" / "keepme.txt").exists())

with tempfile.TemporaryDirectory() as td:
    world = build_world(Path(td))
    # 4. reset-claims disposition: processed -> NULL (accepted stays: those chunks
    #    are the buffered redrive the cutover RELIES on). --recording-port 1: the
    #    scratch world has no recording service; on the real box :8084 is LIVE and
    #    reset's in-code freeze append would (correctly) refuse.
    r = run(world, "--execute", "--recording-claims", "reset", "--recording-port", "1")
    check("reset-claims wet exits 0", r.returncode == 0, (r.stdout + r.stderr)[-300:])
    check("reset-claims: processed -> NULL, accepted preserved",
          dp_states(world["rec_db"]) == {"accepted": 1, "NULL": 2})

with tempfile.TemporaryDirectory() as td:
    world = build_world(Path(td))
    con = sqlite3.connect(world["storage_db"])
    con.execute("CREATE TABLE surprise_table(x)")
    con.commit(); con.close()
    # 5. full-classification-or-refusal: an unknown table aborts BOTH modes
    r = run(world)
    check("unknown table aborts dry-run", r.returncode != 0 and "surprise_table" in (r.stdout + r.stderr))
    r = run(world, "--execute")
    check("unknown table aborts wet", r.returncode != 0)
    check("aborted wet mutated nothing", count(world["storage_db"], "context_records") == 2)

with tempfile.TemporaryDirectory() as td:
    world = build_world(Path(td))
    (world["continuum_var"] / "mystery_state").mkdir()
    # 6. same refusal on an unclassified continuum var subdir
    r = run(world)
    check("unknown continuum var subdir aborts", r.returncode != 0 and "mystery_state" in (r.stdout + r.stderr))

with tempfile.TemporaryDirectory() as td:
    world = build_world(Path(td))
    world["dp_db"] = Path(td) / "typo" / "dp.db"   # a mistyped --dp-db
    # 7. a missing wipe-target db is an ABORT, not a silent no-op (a silent
    #    no-op at F1 is a corpse in the new world); --allow-missing is the
    #    explicit escape for the genuinely-absent case
    r = run(world)
    check("missing wipe-target db aborts dry-run",
          r.returncode != 0 and "missing" in (r.stdout + r.stderr).lower())
    r = run(world, "--execute")
    check("missing wipe-target db aborts wet", r.returncode != 0)
    check("aborted-on-missing wet mutated nothing",
          count(world["storage_db"], "context_records") == 2)
    r = run(world, "--allow-missing")
    check("--allow-missing dry-run exits 0", r.returncode == 0, r.stderr[-300:])
    r = run(world, "--execute", "--allow-missing")
    check("--allow-missing wet exits 0 and wipes what exists",
          r.returncode == 0 and count(world["storage_db"], "context_records") == 0,
          (r.stdout + r.stderr)[-300:])

with tempfile.TemporaryDirectory() as td:
    world = build_world(Path(td))
    s = socket.socket(); s.bind(("127.0.0.1", 0)); s.listen(1)
    rec_port = s.getsockname()[1]
    # 8. reset mode writes recording's ledger.db, so the freeze must cover the
    #    recording port too — structurally (appended in code, not via the flag);
    #    keep mode leaves recording capturing, per the F1 design
    r = run(world, "--execute", "--recording-claims", "reset",
            "--recording-port", str(rec_port))
    check("reset-mode wet refuses while the recording port is listening",
          r.returncode != 0 and "listening" in (r.stdout + r.stderr))
    check("reset-mode refusal mutated nothing",
          count(world["storage_db"], "context_records") == 2)
    r = run(world, "--execute", "--recording-claims", "keep",
            "--recording-port", str(rec_port))
    check("keep-mode wet proceeds with recording still up (its ledger is not a "
          "wet target)", r.returncode == 0, (r.stdout + r.stderr)[-300:])
    s.close()

# ---- run_vllm.sh pin guard (GATE 1 verification round, finding 3) ----------
with tempfile.TemporaryDirectory() as td:
    evil = Path(td) / "evil.env"
    evil.write_text("VLM_MODEL=evil/override-7b\nVLM_REVISION=deadbeef\n")
    r = subprocess.run(
        ["bash", str(DEPLOY / "run_vllm.sh"), "--status"],
        env={**os.environ, "ENV_FILE": str(evil)},
        capture_output=True, text=True)
    check("run_vllm.sh --status prints its pins",
          "Qwen/Qwen3-VL-32B-Instruct" in r.stdout and "0cfaf481" in r.stdout,
          r.stdout[-300:])
    check("an env file cannot override the model/revision pins",
          "evil/override-7b" not in r.stdout and "deadbeef" not in r.stdout,
          r.stdout[-300:])

print()
total = len(failures)
if failures:
    print(f"FAILED: {total} check(s): {failures}")
    sys.exit(1)
print("ALL CHECKS PASSED")
