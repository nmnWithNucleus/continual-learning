"""Drop-in stage files, one modality per sub-package (``audio/``, ``video/``, …).

Each module here is auto-imported by ``stagegraph.stage._discover`` and self-registers
via ``@register_stage`` — adding/removing a processing step is adding/removing ONE file.
See ``app/stagegraph/`` for the protocol and the executor.
"""
