"""Inference service — the brain of the serve-loop MVP (base model only).

Serve-loop v0.0: consume a C3 UserPrompt, resolve the model via C6, generate
(mock or vLLM), stream the answer as the C9 wire format, then persist a C4 turn
record to storage. No adapter, no harness, no mentors yet.
"""
