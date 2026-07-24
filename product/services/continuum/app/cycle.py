"""The nightly consolidation cycle — service-owned orchestration over the seam.

Stage order (the research nightly job's shape): day-log build → amplify →
replay mix → train (continue the one life adapter) → gate → publish/record →
admit reservoir. Every stage is journaled and keyed by a content hash of its
inputs, so re-running a night is idempotent (crash-safe, deterministic) and a
changed upstream invalidates exactly the stages below it — the research
sbatch pipeline's (day, stage, content-hash) discipline, in-process. The
gate/publish tail is terminal-guarded: an unchanged night replays its recorded
outcome with ZERO side effects (no re-strike, no duplicate C5 entries, no
alias motion, no double reservoir admission).

Failure policy: a gate fail records the candidate (audit) but never activates
it, and counts a strike; `consecutive_fail_freeze` strikes freeze the user's
consolidation until a human clears it. Strikes are window-monotonic — retries
of one night and re-consolidations of old nights never add strikes. The
design-of-record's failed-day merge (fold day N into night N+1's corpus) is
tracked as debt in the state file — wiring it is ws-morpheus-port scope.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import fsio
from .backends import get_backend
from .clients import DayLogClient, RecipeRegistry
from .clients import day_log_client
from .clients import recipe_registry as build_registry
from .clients import reservoir_client as build_reservoir_client
from .config import get_settings
from .gate import GateReport, run_gate
from .ids import validate_id
from .policy import GatePolicy
from .publish import ModelDirectory, PublishResult
from .recipe import Recipe
from .renderer import blocks_text, render_corpus_file
from .window import Window, window_for

BASE_MODEL_HASH = "qwen3-vl-32b-instruct"  # pinned for real once D6's exact variant lands


def _h(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


@dataclass(frozen=True)
class CycleResult:
    status: str                    # "published" | "gate_failed" | "frozen" | "skipped_no_data"
    window_id: str
    user_id: str
    adapter_version: str | None
    gate: GateReport | None
    publish: PublishResult | None
    stages_run: list[str]
    stages_skipped: list[str]


class _Journal:
    def __init__(self, var_dir: Path, user_id: str, window_id: str):
        self.path = var_dir / "journal" / user_id / f"{window_id}.json"
        self.data: dict[str, Any] = fsio.read_json(self.path, default={"stages": {}})

    def fresh(self, stage: str, key: str) -> dict | None:
        """The stage's recorded outputs, iff it already ran with the same key
        and every recorded file output still exists (files are written
        atomically, so existence == completeness)."""
        entry = self.data["stages"].get(stage)
        if not entry or entry["key"] != key:
            return None
        for out in entry.get("files", []):
            if not Path(out).exists():
                return None
        return entry

    def record(self, stage: str, key: str, *, files: list[str] | None = None,
               **payload: Any) -> None:
        self.data["stages"][stage] = {
            "key": key, "files": files or [],
            "at": datetime.now(timezone.utc).isoformat(), **payload}
        fsio.atomic_write_json(self.path, self.data)


class _UserState:
    """Strike counter + freeze flag + consolidation debt, per user.

    Window-monotonic: only outcomes for the newest window seen so far move the
    consecutive-failure counter, so retrying one bad night or re-consolidating
    an old day can neither freeze a user nor mask later windows' failures."""

    def __init__(self, var_dir: Path, user_id: str):
        self.path = var_dir / "state" / f"{user_id}.json"
        self.data = fsio.read_json(self.path, default={
            "consecutive_failures": 0, "frozen": False, "debt": [],
            "latest_window": ""})

    def save(self) -> None:
        fsio.atomic_write_json(self.path, self.data)

    def strike(self, window_id: str, freeze_at: int) -> None:
        if window_id not in self.data["debt"]:
            self.data["debt"].append(window_id)
        if window_id >= self.data.get("latest_window", ""):
            self.data["consecutive_failures"] += 1
            self.data["latest_window"] = window_id
            if self.data["consecutive_failures"] >= freeze_at:
                self.data["frozen"] = True
        self.save()

    def record_pass(self, window_id: str) -> None:
        self.data["debt"] = [w for w in self.data["debt"] if w != window_id]
        if window_id >= self.data.get("latest_window", ""):
            self.data["consecutive_failures"] = 0
            self.data["latest_window"] = window_id
        self.save()


def run_cycle(win: Window, *, daylog_client: DayLogClient | None = None,
              registry: RecipeRegistry | None = None, recipe: Recipe | None = None,
              policy: GatePolicy | None = None, force: bool = False) -> CycleResult:
    """One night's consolidation as the lean 5-verb loop:
    fetch recipe · fetch day-log · amplify · finetune · gate · publish.

    Every data-shaped input arrives through a storage CLIENT (registry, day-log,
    reservoir) — local today, HTTP-to-storage later, the cycle unchanged."""
    validate_id(win.user_id, "user_id")
    validate_id(win.window_id, "window_id")
    settings = get_settings()
    # ---- verb: fetch recipe (+ the separately-versioned gate policy) -------------
    registry = registry or build_registry(settings)
    recipe = recipe or registry.fetch_recipe(settings.recipe_id)
    # Policy is a SEPARATE artifact and never reaches a stage key: a threshold
    # change must not invalidate the amplify/train caches.
    policy = policy or registry.fetch_policy(settings.policy_id)
    var_dir = Path(settings.var_dir)
    backend = get_backend(settings.trainer_backend)
    daylog_client = daylog_client or day_log_client(settings, recipe)
    reservoir = build_reservoir_client(settings, daylog_client=daylog_client)
    directory = ModelDirectory(var_dir)
    state = _UserState(var_dir, win.user_id)
    journal = _Journal(var_dir, win.user_id, win.window_id)
    work = var_dir / "cycles" / win.user_id / win.window_id
    work.mkdir(parents=True, exist_ok=True)
    seed = int(_h(win.user_id, win.window_id)[:8], 16)
    run_stages: list[str] = []
    skipped: list[str] = []

    if state.data["frozen"] and not force:
        return CycleResult("frozen", win.window_id, win.user_id, None, None, None,
                           [], ["ALL — user frozen after consecutive gate failures; "
                                "clear state file or pass force to resume"])

    # ---- verb: fetch day-log -----------------------------------------------------
    # Continuum no longer builds the day-log inline: it asks the client for the
    # day-log for this window. The stage key hashes the day-log's CONTENT (not raw
    # records) — correct, and the only thing continuum will see once storage owns
    # materialization.
    daylog = daylog_client.fetch_daylog(win)
    records_key = _h(daylog_client.fingerprint(daylog),
                     win.window_id, str(recipe.segment_seconds), str(recipe.block_segments))
    entry = journal.fresh("daylog", records_key)
    if entry:
        skipped.append("daylog")
    else:
        paths = daylog_client.render(daylog, work / "daylog")
        journal.record("daylog", records_key, files=list(paths.values()),
                       n_segments=len(daylog.segments), n_blocks=len(daylog.blocks))
        run_stages.append("daylog")

    eligible = daylog_client.eligible_blocks(daylog, recipe.quality_min)
    if not eligible:
        return CycleResult("skipped_no_data", win.window_id, win.user_id, None,
                           None, None, run_stages,
                           skipped + ["amplify", "replay_mix", "train", "gate", "publish"])

    # ---- stage: amplify ----------------------------------------------------------
    amp_key = _h(records_key, blocks_text(eligible), recipe.recipe_id,
                 str(recipe.variants), str(recipe.neg_frac), str(seed))
    amp_path = work / "amplified.corpus.txt"
    entry = journal.fresh("amplify", amp_key)
    if entry:
        skipped.append("amplify")
    else:
        amp = backend.amplify(eligible, recipe, seed=seed)
        if amp.ok_rate < recipe.ok_rate_min:
            raise RuntimeError(f"amplify ok-rate {amp.ok_rate:.3f} < "
                               f"{recipe.ok_rate_min} — aborting the night "
                               "(serve stale adapter; consolidation debt)")
        render_corpus_file(amp.text, work, name="amplified.corpus.txt")
        journal.record("amplify", amp_key, files=[str(amp_path)],
                       ok_rate=amp.ok_rate, n_variants=amp.n_variants,
                       n_negatives=amp.n_negatives)
        run_stages.append("amplify")
    amp_text = amp_path.read_text()

    # ---- stage: replay mix -------------------------------------------------------
    # Prior consolidated windows for this user, reconstructed from the reservoir's
    # ledger (the amplified store is the record of WHICH nights ran, whichever
    # source replay then reads). Key includes each entry's content sha: a
    # re-consolidated past day must invalidate this night's mix.
    prior = reservoir.entries(win.user_id, before_window=win.window_id)
    reservoir_state = ";".join(f"{e.window_id}:{e.sha}" for e in prior)
    mix_key = _h(amp_key, reservoir_state, str(recipe.replay_frac), recipe.replay_source)
    mix_path = work / "train.corpus.txt"
    entry = journal.fresh("replay_mix", mix_key)
    if entry:
        skipped.append("replay_mix")
    else:
        # The locked decision is raw prior day-logs; recipe v1.0 pins amp for
        # parity. Both go through the reservoir client — amp reads the amplified
        # store, rawlog re-reads prior day-logs via the day-log client.
        prior_windows = None
        if recipe.replay_source == "rawlog":
            prior_windows = [window_for(win.user_id, e.local_window_date(), win.tz,
                                        recipe.boundary_local_time) for e in prior]
        replay = reservoir.sample_replay(
            win.user_id, target_chars=len(amp_text), frac=recipe.replay_frac,
            seed=seed, before_window=win.window_id,
            source=recipe.replay_source, prior_windows=prior_windows)
        mixed = amp_text + ("\n\n" + replay if replay else "")
        render_corpus_file(mixed, work, name="train.corpus.txt")
        journal.record("replay_mix", mix_key, files=[str(mix_path)],
                       replay_chars=len(replay), replay_source=recipe.replay_source)
        run_stages.append("replay_mix")

    # ---- stage: train (continue the ONE life adapter) ----------------------------
    prior = directory.active_before(win.user_id, win.window_id)
    resume_adapter = prior["adapter_dir"] if prior else None
    train_key = _h(mix_key, recipe.recipe_id, str(recipe.lora_r), str(recipe.lr),
                   str(recipe.epochs), prior["adapter_version"] if prior else "")
    entry = journal.fresh("train", train_key)
    if entry:
        skipped.append("train")
        adapter_dir, adapter_version = entry["adapter_dir"], entry["adapter_version"]
    else:
        result = backend.train(str(mix_path), recipe,
                               out_dir=str(var_dir / "adapters" / win.user_id / win.window_id),
                               resume_adapter=resume_adapter,
                               new_day_corpus_path=str(amp_path))
        adapter_dir, adapter_version = result.adapter_dir, result.adapter_version
        journal.record("train", train_key, files=[adapter_dir],
                       adapter_dir=adapter_dir, adapter_version=adapter_version)
        run_stages.append("train")

    # ---- terminal guard: gate + publish already recorded for this exact night ----
    terminal_key = _h(train_key, "terminal")
    entry = journal.fresh("publish", terminal_key)
    if entry:
        gate = GateReport(passed=entry["passed"], checks=entry.get("checks", {}),
                          reasons=entry.get("reasons", []),
                          skipped=tuple(entry.get("skipped_checks", [])),
                          scores=entry.get("scores"))
        return CycleResult(entry["status"], win.window_id, win.user_id,
                           entry.get("adapter_version"), gate, None,
                           run_stages, skipped + ["gate", "publish"])

    # ---- stage: gate -------------------------------------------------------------
    scores = backend.evaluate(adapter_dir, eligible, recipe)
    gate = run_gate(scores, policy)
    journal.record("gate", _h(train_key), passed=gate.passed, checks=gate.checks,
                   reasons=gate.reasons, scores=gate.scores,
                   skipped_checks=list(gate.skipped))
    run_stages.append("gate")
    eval_report = {**(gate.scores or {}), "checks": gate.checks,
                   "skipped_checks": list(gate.skipped), "policy_id": gate.policy_id}

    # ---- stage: publish / record + reservoir admission ---------------------------
    if gate.passed:
        publish = directory.publish(
            user_id=win.user_id, adapter_version=adapter_version,
            adapter_dir=adapter_dir, base_model_hash=BASE_MODEL_HASH,
            training_window=win.window_id, recipe_id=recipe.recipe_id,
            eval_report=eval_report,
            snapshot_retention=policy.snapshot_retention)
        state.record_pass(win.window_id)
        # A passed night admits the corpus to the permanent reservoir.
        reservoir.admit(win.user_id, win.window_id, recipe.recipe_id, amp_text)
        status = "published"
    else:
        publish = directory.record_gate_failure(
            user_id=win.user_id, adapter_version=adapter_version,
            training_window=win.window_id, recipe_id=recipe.recipe_id,
            eval_report={"reasons": gate.reasons, **eval_report})
        state.strike(win.window_id, policy.consecutive_fail_freeze)
        status = "gate_failed"

    journal.record("publish", terminal_key, status=status,
                   adapter_version=adapter_version, passed=gate.passed,
                   checks=gate.checks, reasons=gate.reasons, scores=gate.scores,
                   skipped_checks=list(gate.skipped))
    run_stages.append("publish")
    return CycleResult(status, win.window_id, win.user_id, adapter_version,
                       gate, publish, run_stages, skipped)
