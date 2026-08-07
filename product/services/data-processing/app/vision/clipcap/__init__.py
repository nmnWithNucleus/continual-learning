"""The clip captioner wire ().

 * ``vlm`` — the real path: ONE multi-image call against an OpenAI-compatible
 ``/v1/chat/completions`` (), OCR text injected per, replies run through
 the tolerant parse ladder. Called by the ``clipcap`` stage
 (``app/stages/video/clipcap.py``), which owns every output-affecting pin.
 * ``vertex`` — the Vertex/Gemini eval-oracle STUB (): kept to document the
 verified call shape; ``describe`` raises. Deliberately UNREGISTERED — no stage
 constructs it; enabling the oracle is a budgeted decision, not a backend flip.

Dead with : the prior ``select()`` env resolver (``VIDEO_BACKEND`` was an
output-affecting knob, L4 — the backend is now named in the stage's code-resolved
``Backend``) and ``mock.py`` (mock dialects are client-level fakes in tests: a
``httpx.MockTransport`` behind ``vlm.make_async_client``, or a canned stage double).
"""
from __future__ import annotations
