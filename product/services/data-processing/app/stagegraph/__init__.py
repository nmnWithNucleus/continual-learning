"""Stage graph — every processing step a drop-in file (DP v1 orchestration core).

Rebuilt at Stage C around the Slot Law (CHARTER §Slot Law, D23–D28):

  * a **stage = one auto-discovered file** under ``app/stages/<modality>/``
    declaring name, modality, vS, backend (name + vB, in code), needs, slot,
    required, byte_budget, and exactly one run method;
  * the **readiness executor** runs independent stages concurrently inside one
    chunk, composes ``pipeline_version`` from the enabled stages' sorted
    segments before any stage runs, commits slot values on success only, and
    assembles exactly ONE record's slots per chunk (L2/L5);
  * identity and versioning live entirely in **code** — no output-affecting env
    knob exists anywhere (L4).
"""
from .stage import (  # noqa: F401
    Backend,
    Stage,
    StageContext,
    StageOutput,
    StageRegistrationError,
    register_stage,
    registered_modalities,
    stages_for,
    validate_stage,
)
