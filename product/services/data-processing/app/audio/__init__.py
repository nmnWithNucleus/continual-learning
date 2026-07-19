"""Real audio-pipeline backends beyond ASR — the AUDIO-modality lead's namespace.

The audio Processor (``app/processing/processors/audio.py``) is a staged pipeline

    asr -> diarize -> translate -> acoustic_events

Stage 1 (asr) lives in ``app/asr/`` (the M0 path). Stages 2-4's real backends live
HERE, each mirroring the ``app/asr/`` backend-switch shape:

  * ``diarize/``   — speaker diarization (DIARIZE_BACKEND=off|mock|pyannote);
  * ``translate/`` — transcript translation (TRANSLATE_BACKEND=off|mock|whisper);
  * ``acoustic/``  — non-speech acoustic-event captioning (ACOUSTIC_BACKEND=off|mock|panns).

Design invariants (see ``config.py`` for the knobs, and ``handoff/ws-audio-pipeline.md``):

  * EVERY capability defaults to OFF, so the default audio output is byte-identical to
    the pre-fill processor — the mock ASR dialect stays untouched and the M0/seam test
    baseline stays green. A capability only does work (and only then forks / adds records)
    when its backend env var selects a non-off value.
  * The ``mock`` backend of each capability is the DEFAULT no-GPU choice WHEN a capability
    is turned on: deterministic, imports no heavy deps, so the whole feature dialect is
    exercisable headless in tests and in run_learn. The real backend (``pyannote`` /
    ``whisper`` / ``panns``) is LAZY-IMPORTED only when selected, so importing this package
    (which the registry does on every ``/ingest``) never pulls torch/pyannote.
  * ``config.py`` reads the new env vars via ``os.getenv()`` locally — the shared
    ``app/config.py`` Settings is READ-ONLY to this workstream, so we never touch it.
"""
