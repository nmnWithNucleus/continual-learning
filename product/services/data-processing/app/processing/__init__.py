"""The modality-agnostic Processor seam.

The core (``app.main`` + ``app.pipeline``) knows nothing about audio/image/video/
text; it dispatches a validated C1 envelope + the raw blob bytes to a *Processor*
selected by ``envelope.modality`` and assembles a C2 for every unit the Processor
returns. Each modality is a **disjoint plugin file** under ``processors/`` that
self-registers on import — so a future session owns one modality by dropping in a
single file, never touching this core.

Public surface:
  * ``base``     — ``Processor`` base class + ``ProcessedUnit`` / ``ProcessedContent``.
  * ``registry`` — ``register`` decorator, ``get_processor`` / ``registered_modalities``.
"""
