"""Morpheus — Nucleus's nightly-consolidation core.

The five recipe-coupled kernels that turn one day of a life into one night of
weight updates: render the day-log blocks, AMPLIFY them into a diverse synthetic
corpus, mix in rehearsal of prior days, continue-CPT the ONE life adapter, and
judge the result closed-book so the gate can decide whether it may serve.

What lives here is only what is coupled to the *method*. Orchestration (windows,
journaling, idempotency, gate verdict, C5 publish) is service-owned and lives one
level up; data-shaped work (day-log materialization, recipe registry, reservoir)
belongs to storage. Everything domain-specific is behind `profiles/` (§6): the
kernels themselves hardcode nothing about whose life is being consolidated.

Provenance: the methods reproduce the validated consolidation line at commit
`b3c58e1` of the nucleus research repo. This is a clean reimplementation, not a
copy — `tests/parity/` is the contract that we reproduced the behavior.
"""
from __future__ import annotations

# Bumped on every METHOD change (a new kernel behavior, a changed default, a
# different validity rule) — not on refactors. Recorded in every adapter's
# meta.json alongside recipe_id, so an artifact is always attributable to the
# exact code that made it.
MORPHEUS_VERSION = "1.0.0"

SOURCE_COMMIT = "b3c58e1"
