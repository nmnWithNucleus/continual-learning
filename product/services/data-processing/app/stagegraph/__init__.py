"""Stage graph — every processing step a drop-in file (DP v1 orchestration core).

The audio pipeline half-invented this shape (staged methods + a state blackboard +
single-resolver version tags); this package generalizes it for every modality and every
future stage:

  * a **stage = one auto-discovered file** under ``app/stages/<modality>/`` declaring its
    dependencies, what it provides, whether it mutates the primary record (and therefore
    forks ``pipeline_version``), and its failure policy;
  * a **readiness executor** runs independent stages concurrently inside one chunk
    (diarization no longer waits on translation; keyframe captions fan out), with
    per-stage metrics — while keeping the chunk the atomic unit of ingest (claim/dedup/
    journal semantics untouched);
  * ``pipeline_version`` is **composed** from the enabled stages' fragments, so the
    "mutation without a version fork = silent record overwrite" bug class dies by
    construction — a mutate stage's enabledness IS its fragment.

Adding OCR, speaker-identity, multi-level captions, bbox enrichment = dropping one stage
file. No core edits, no processor-file surgery.
"""
from .stage import (  # noqa: F401
    SKIPPED,
    SlotAccessError,
    SlotView,
    Stage,
    StageContext,
    StageRegistrationError,
    StageResult,
    register_stage,
    stages_for,
)
from .executor import GraphResolutionError, resolve, run_graph  # noqa: F401
from .processor import GraphProcessor  # noqa: F401
