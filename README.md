# Continual Learning — Nucleus AI workspace

Umbrella repo for Nucleus AI's continual-learning **v0 product** and its research **POCs**.

## Product (v0)

- [`product/`](product/) — the v0 product workspace: vision, architecture, org model, the
  frozen contract registry ([`product/contracts/`](product/contracts/)), and the service
  nodes ([`product/services/`](product/services/)). Cold-start read order is in
  [`product/README.md`](product/README.md); the founders' working canvas is
  [`product/HANDOFF.md`](product/HANDOFF.md).

`start.md` is the founder's original v0 vision memo (2026-07-08) that seeded `product/`.

## POCs (research feeders — reference, not source; see decision D7)

- `poc/live_stream_stability/` — VLM continual-learning on a 35-day IRL livestream
  (submodule).
- `poc/recursive_finetuning_stability/` — recursive self-SFT: a coding researcher fine-tuned
  on its own grounded autoresearch execution traces, looped V0→V_N (weights vs. context)
  (submodule).
- `poc/live_video_chat/` — phone web app for live-video Q&A against self-hosted
  Qwen3-VL-32B (plain directory tracked here; becomes a submodule when it gets its own
  remote).

Submodules: clone with `--recurse-submodules`.

## Branches

- `main` — integrated, tested work only. Service work happens on branches off `main`,
  merged once coded + tested at a decent revision (D12).
- `dev` — the beta playground, forked from `main` and handed to testers; may carry
  beta-only conveniences, never contract changes.
