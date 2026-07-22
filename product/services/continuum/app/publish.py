"""C5 publish / rollback — adapter version entries + the active alias.

C5's v0 shape is NOT frozen yet (needs inference at the table; founders
ratify). Until then this module maintains the full lifecycle locally under
var_dir/model_directory/ — an append-only entries.jsonl (C5-shaped rows:
user_id, adapter_version, base_model_hash, training_window, eval_report,
status) plus an atomic active.json alias and N-version snapshot retention —
so the storage-hosted model directory swap-in is a transport change, not a
redesign. Retention default 14 mirrors the research design (sized for
rollback AND the ≤14-night hard-delete replay).

Alias monotonicity: publish() never moves active.json BACKWARD — re-running
or re-consolidating an old window appends its entry (audit + lineage) but the
serving alias only flips for a window >= the currently active one.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from . import fsio
from .ids import validate_id


@dataclass(frozen=True)
class PublishResult:
    adapter_version: str
    status: str          # "active" | "rolled_back" | "gate_failed"
    entry_path: str


def _active_stack(entries: list[dict]) -> list[dict]:
    """Replay the append-only log into the current stack of live activations:
    push on 'active', pop on a 'rolled_back' matching the top. rollback rows
    that don't match the top (historical duplicates) are ignored."""
    stack: list[dict] = []
    for e in entries:
        if e["status"] == "active":
            stack.append(e)
        elif (e["status"] == "rolled_back" and stack
              and stack[-1]["adapter_version"] == e["adapter_version"]):
            stack.pop()
    return stack


class ModelDirectory:
    def __init__(self, var_dir: str | Path):
        self.root = Path(var_dir) / "model_directory"

    def _udir(self, user_id: str) -> Path:
        d = self.root / validate_id(user_id, "user_id")
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _entries_path(self, user_id: str) -> Path:
        return self._udir(user_id) / "entries.jsonl"

    def entries(self, user_id: str) -> list[dict]:
        return fsio.read_jsonl(self._entries_path(user_id))

    def active(self, user_id: str) -> dict | None:
        return fsio.read_json(self._udir(user_id) / "active.json")

    def active_before(self, user_id: str, window_id: str) -> dict | None:
        """The adapter the life-adapter chain should RESUME from when (re-)
        training `window_id`: among still-live activations for strictly earlier
        windows, the one for the LATEST window — regardless of log append order,
        so re-consolidating an old day never hijacks the lineage."""
        stack = _active_stack([e for e in self.entries(user_id)
                               if (e.get("training_window") or "") < window_id])
        if not stack:
            return None
        return max(stack, key=lambda e: e.get("training_window") or "")

    def _set_active(self, user_id: str, alias: dict | None) -> None:
        path = self._udir(user_id) / "active.json"
        if alias is None:
            path.unlink(missing_ok=True)
            return
        fsio.atomic_write_json(path, alias)

    def publish(self, *, user_id: str, adapter_version: str, adapter_dir: str,
                base_model_hash: str, training_window: str, recipe_id: str,
                eval_report: dict, snapshot_retention: int) -> PublishResult:
        entry = {"contract": "C5", "user_id": user_id,
                 "adapter_version": adapter_version, "adapter_dir": adapter_dir,
                 "base_model_hash": base_model_hash,
                 "training_window": training_window, "recipe_id": recipe_id,
                 "eval_report": eval_report, "status": "active"}
        entries_path = self._entries_path(user_id)
        fsio.append_jsonl(entries_path, entry)
        current = self.active(user_id)
        if current is None or (current.get("training_window") or "") <= training_window:
            self._set_active(user_id, {"adapter_version": adapter_version,
                                       "adapter_dir": adapter_dir,
                                       "training_window": training_window})
        self._prune_snapshots(user_id, snapshot_retention)
        return PublishResult(adapter_version, "active", str(entries_path))

    def record_gate_failure(self, *, user_id: str, adapter_version: str,
                            training_window: str, recipe_id: str,
                            eval_report: dict) -> PublishResult:
        """A failed candidate is RECORDED (audit trail) but never becomes active;
        the previously active adapter keeps serving — consolidation debt, never
        an ungated swap."""
        entry = {"contract": "C5", "user_id": user_id,
                 "adapter_version": adapter_version, "adapter_dir": None,
                 "base_model_hash": None, "training_window": training_window,
                 "recipe_id": recipe_id, "eval_report": eval_report,
                 "status": "gate_failed"}
        entries_path = self._entries_path(user_id)
        fsio.append_jsonl(entries_path, entry)
        return PublishResult(adapter_version, "gate_failed", str(entries_path))

    def rollback(self, user_id: str) -> PublishResult:
        """One-command rollback: pop the top of the live activation stack and
        flip the alias to the one beneath (or base model when the stack empties).
        Re-entrant: a second rollback steps back one MORE version."""
        entries = self.entries(user_id)
        stack = _active_stack(entries)
        if not stack:
            raise RuntimeError(f"nothing to roll back for {user_id!r}")
        current, prior = stack[-1], (stack[-2] if len(stack) >= 2 else None)
        if prior is not None:
            prior_dir = prior.get("adapter_dir")
            if prior_dir and not Path(prior_dir).is_dir():
                raise RuntimeError(
                    f"rollback target {prior['adapter_version']} has no artifact dir "
                    f"({prior_dir}) — pruned beyond retention; restore it or roll "
                    "back to base explicitly")
        entries_path = self._entries_path(user_id)
        fsio.append_jsonl(entries_path, {**current, "status": "rolled_back"})
        if prior is not None:
            self._set_active(user_id, {"adapter_version": prior["adapter_version"],
                                       "adapter_dir": prior["adapter_dir"],
                                       "training_window": prior["training_window"]})
        else:
            self._set_active(user_id, None)  # base model only
        return PublishResult(current["adapter_version"], "rolled_back", str(entries_path))

    def _prune_snapshots(self, user_id: str, keep: int) -> None:
        """Retain the last `keep` DISTINCT adapter artifact dirs by most-recent
        reference (a re-published dir counts as fresh), never the active one or
        the immediate rollback target; older artifact dirs are deleted, entries
        stay."""
        if keep <= 0:
            return
        seen: list[str] = []
        for e in self.entries(user_id):
            d = e.get("adapter_dir")
            if not d:
                continue
            if d in seen:
                seen.remove(d)
            seen.append(d)  # last-appearance order = recency of reference
        stack = _active_stack(self.entries(user_id))
        protected = {e.get("adapter_dir") for e in stack[-2:]}
        active = self.active(user_id)
        if active:
            protected.add(active.get("adapter_dir"))
        for stale in seen[:-keep]:
            if stale in protected:
                continue
            path = Path(stale)
            if path.is_dir():
                shutil.rmtree(path)
