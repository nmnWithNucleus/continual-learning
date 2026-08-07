"""Video-stage internals: the machinery the thin video stages
under ``app/stages/video/`` call into.

  * ``clip`` / ``clip_types`` / ``delta`` — the two ffmpeg passes + the delta gate
    (``clipprep`` internals; ffmpeg is a subprocess, self-isolating — L9).
  * ``ocr`` — DP-side OCR post-processing (assemble + redact) for ``screentext``.
  * ``clipcap`` — the one-multi-image-call VLM wire (+ the vertex oracle stub) for
    the ``clipcap`` stage; ``prompts`` — the prompt pack whose aggregate digest the
    stage pins in code.
  * ``parse`` — the tolerant caption parse ladder; ``budget`` — the char-budget math.

NO CONFIG anywhere in this package (L4): the v0 ``config.py``/``mode.py``/``version.py``
env machinery is dead — every output-affecting knob is a code pin in a stage file, and
identity is composed by the stage graph (``app/stagegraph``). The legacy keyframe
pipeline's ``frames.py``/``result.py``/``vlm.py``/``mock.py`` died with the legacy
graph (plan §9).
"""
from __future__ import annotations
