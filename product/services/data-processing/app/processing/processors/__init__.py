"""Modality plugins — one disjoint file per modality, each self-registering.

A future session owns a modality by DROPPING A NEW FILE here (e.g. ``video.py``)
that subclasses ``processing.base.Processor`` and is decorated with
``processing.registry.register``. The registry auto-imports every module in this
package, so no shared-core file (not even a registry list) needs editing.

v0 plugins: ``audio`` (real mock ASR), ``image`` / ``video`` / ``text`` (mock
transforms — the vision/text models land in later modality-owned sessions).
"""
