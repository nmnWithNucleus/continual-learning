# Nucleus AI — Vision

> The stable "why" document. The system design lives in [ARCHITECTURE.md](ARCHITECTURE.md);
> how we work lives in [ORG.md](ORG.md). This file changes rarely and deliberately.

**Last updated:** 2026-07-08

---

## What we are building

A personal AI that sits at the same perception level as its user. It sees what they see,
hears what they hear, and — unlike every assistant on the market — it does not meet the user
fresh on every request. The user's entire life context is **continuously distilled into the
weights of a model that belongs to them**. v0's mission: evolve a personal AI over time by
consuming the ever-growing context of a user's life. Later versions grow from assistant to
coach — guiding, steering, and nudging, always grounded in how that user has actually lived.

## How the industry got here

- **Chatbox era.** ChatGPT put AI in front of the world. Users graduated from party tricks to
  real, complicated, day-to-day work questions — but the interface stayed a text box across
  dozens of disconnected threads.
- **Modality era.** Image, speech, video moved into the models themselves.
- **Harness era.** Engineering scaffolding — Codex, Claude Code, Cursor, agent frameworks —
  wrapped models with context injection, memory, tools, search, and planning loops. AI became
  the brain of a system rather than a text-completion box.

Every inch of human work is getting easier with an AI alongside. But all of it still lives
inside a computer or a phone, and two structural problems remain.

## The two problems

**1 — Context length is a bound, not a solution.** 128k became 1M and will become 10M; it is
still a ceiling. Everything built on top — condensation, summarize-and-restart, thread
juggling — is coping machinery. The user's knowledge never truly accumulates; it gets lossily
compressed and hopefully survives the next session.

**2 — Personalization doesn't exist yet.** Frontier models are static between releases and
unbiased toward whoever is prompting. Me and my brother get the same underlying intelligence;
only our prompts differ. The industry hasn't felt this pain yet — the raw intelligence is
still too thrilling. But this is the barrier personal computing broke decades ago (computers
were institutional until Jobs made them personal), and it is the barrier the previous
generation of consumer companies (Google, Meta, YouTube) spent fortunes crossing with
recommendation and personalization layers. AI will have to cross it too. We intend to be
there first.

## Our answer: context becomes weights

A human's "context" is what they perceive — sight, sound, speech, the physical world and the
digital one. Upper bound on a whole life of it: 24 h × 365 d × 100 y ≈ **876K hours**.
Frontier models pre-train on millions of hours. A single life fits.

So: take a performant open base vision-language model — our **Base World Model (BWM)** — and
instead of a rolling context window, **continuously fine-tune it on the user's life stream**:

- A wearable (v0: body cam — camera + mic, no speaker) captures the physical world.
- Computer capture (screen recording, browser extension, mic) covers the digital world.
  (A v0 mobile app is an interaction + speech-output surface; only mobile *screen capture* is
  deferred — iOS won't allow full-screen recording to private servers.)
- The stream is processed, timestamped, enriched, stored — and periodically trained into the
  user's personal adapter. The context doesn't scroll away; it **compounds in the weights**.

The bet mirrors pre-training itself: enough continuous life context should produce not just
recall but **emergent behaviors** — patterns, skills, preferences that mimic the user.
A digital twin, grown rather than configured.

**v0 mechanism (locked):** per-user LoRA over all layers, hot-swapped in vLLM for both
inference and fine-tuning. Crude but right for a handful of pilot users.
**Research path:** users-as-experts in an MoE — one big network, experts allocated per user,
routing by identity — unexplored territory and our long-range scaling story. Both live in the
[continuum service](services/continuum/CHARTER.md).

## Why this matters — a day in a life

A user wakes, wears the device, makes eggs and toast, drives the freeway past an accident and
a couple of billboards, works, hears colleagues' weekend stories at lunch, calls an old friend
on the drive home and picks a place and time to meet, has dinner, watches a show, reads, and
sleeps. Two days later, the human remembers almost none of the texture — humans keep only
what's active. The model remembers all of it, because that night it was trained on the day.

Then the compounding starts: it knows the weekend plan exists and whether a reservation was
ever actually made. It noticed the last two eggs went into breakfast and adds eggs to the
list. It connects a billboard to something the user browsed two months ago and surfaces the
sale. None of this is a scripted feature — it's what falls out of a model that has the user's
life in its weights.

## While the model matures: mentorship

A freshly personalized model won't out-reason frontier models on day one, and users shouldn't
pay for that gap. So v0 wraps our model in an agentic harness with a **mentor protocol**:
when our model wants help, it composes an assistance prompt — injecting what it knows about
the user — and consults frontier models (Claude, GPT, Gemini). Their plans, thinking, and
outputs flow back through our model to the user, and **their full traces become training
data**. The personal model is simultaneously the memory, the router, and the student. The
graduation criterion — when does it answer solo? — is one of our defining research questions.

## What v0 is, and is not

| | v0 |
|---|---|
| Users | A handful of pilots, hands-on |
| Devices | Computer capture + wearable body cam; **no mobile capture** |
| Personalization | LoRA-per-user, all layers, vLLM hot-swap |
| Learning cadence | Periodic (nightly-ish), gated by evals — not realtime |
| Assistant posture | Reactive assistant with mentor support — coach/nudge comes later |
| Trust | Consent controls, per-user isolation, deletion are day-one requirements, not v1 polish |

The end state we're walking toward: an AI that has lived alongside you, remembers what you
can't, and eventually knows you well enough to help steer — not because it was prompted well,
but because it was *there*.
