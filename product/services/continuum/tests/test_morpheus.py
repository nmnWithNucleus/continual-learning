"""Morpheus kernel unit tests — behavior that holds with no goldens and no GPU.

Parity (tests/parity/) proves we reproduce the reference numbers. These prove the
kernels behave sanely at the edges the reference chain never hit: empty inputs,
a degraded generator, a renamed base model, a missing interpreter. Those are the
cases a nightly job actually meets in production.
"""
from __future__ import annotations

import random
from types import SimpleNamespace

import pytest

from app.morpheus.amplify import AmplifyBelowOkRate, amplify, collect, plan
from app.morpheus.blocks import Block, blocks_corpus, load_blocks, render_blocks
from app.morpheus.eval import Band, Readout, decay_matrix, readout
from app.morpheus.pinned_env import EnvironmentUnusable, PinnedEnv
from app.morpheus.probes import (Probe, ProbeContamination,
                                 assert_independent_generators, day_pool,
                                 generator_family, load_suite)
from app.morpheus.profiles import get_profile
from app.morpheus.replay import MIN_PARAGRAPH_CHARS, paragraphs, sample_replay
from app.morpheus.train import MIN_CHUNK_TOKENS, chunk_corpus


@pytest.fixture
def profile():
    return get_profile("speed")


@pytest.fixture
def blocks():
    return [Block(block_id=f"b{i}", text=f"[Day 7 of 35] Block {i}. " + "detail " * 40,
                  anchors={"day": 7, "city": "Denver"}, order=i)
            for i in range(3)]


class FakeTokenizer:
    """One token per character. Chunking is about slicing, not vocabulary."""
    eos_token_id = 0

    def __call__(self, text, add_special_tokens=False):
        return SimpleNamespace(input_ids=[ord(c) for c in text])


# ----------------------------------------------------------------- profile seam

def test_profile_registry_rejects_unknown_domains():
    with pytest.raises(ValueError, match="one new module"):
        get_profile("lifestream")


def test_prompt_carries_the_day_city_and_horizon(profile, blocks):
    prompt = profile.amplify_prompt(blocks[0], profile.styles[0])
    assert "Day 7 of IShowSpeed's 35-day US tour (in Denver)" in prompt
    assert prompt.endswith("Paragraph:")
    assert blocks[0].text[:50] in prompt


def test_validity_rejects_the_three_silent_failures(profile, blocks):
    block = blocks[0]
    assert profile.is_valid("On Day 7 in Denver, " + "he walked. " * 20, block)
    assert not profile.is_valid("On Day 7 in Denver.", block)                # too short
    assert not profile.is_valid("=== RECORD === Day 7 " + "x " * 100, block)  # echoed scaffold
    assert not profile.is_valid("In Denver he walked. " * 20, block)          # no day anchor


def test_kernels_read_anchors_only_through_the_profile(profile):
    """The seam's whole point: a kernel must never index an anchor itself."""
    import inspect

    from app.morpheus import amplify as amplify_module
    from app.morpheus import blocks as blocks_module
    from app.morpheus import eval as eval_module
    from app.morpheus import replay as replay_module
    from app.morpheus import train as train_module
    for module in (amplify_module, blocks_module, eval_module, replay_module, train_module):
        source = inspect.getsource(module)
        assert "anchors[" not in source, f"{module.__name__} reaches into anchors directly"
        assert "profiles" not in source, f"{module.__name__} imports a concrete profile"
        for leak in ("IShowSpeed", "35-day", "tour", "Headline:"):
            assert leak not in source, f"{module.__name__} hardcodes the domain: {leak!r}"


# ----------------------------------------------------------------------- blocks

def test_load_blocks_passes_unreserved_columns_through_as_anchors(tmp_path):
    path = tmp_path / "blocks.jsonl"
    path.write_text('{"block_id": "b0", "text": "hello", "order": 0, "city": "Denver"}\n'
                    '{"block_id": "b1", "text": "world", "order": 1, "city": "Austin"}\n')
    loaded = load_blocks(path, extra_anchors={"day": 9})
    assert [b.block_id for b in loaded] == ["b0", "b1"]
    assert loaded[0].anchors == {"city": "Denver", "day": 9}
    assert blocks_corpus(loaded) == "hello\n\nworld"


def test_render_blocks_uses_the_profile(profile):
    records = [{"chunk_id": "c0", "day": "3", "city": "Reno", "anchor": "[Day 3 of 35]",
                "description": {"headline": "Something happened."}}]
    rendered = render_blocks(records, profile)
    assert rendered[0].text == "[Day 3 of 35]\nHeadline: Something happened."
    assert rendered[0].anchors["day"] == 3


# ---------------------------------------------------------------------- amplify

def test_amplify_is_reproducible_from_its_seed(profile, blocks):
    def generator(prompts):
        return [f"On Day 7 in Denver something happened. {i}. " + "detail " * 30
                for i, _ in enumerate(prompts)]

    kwargs = dict(variants=4, neg_frac=0.15, ok_rate_min=0.85, seed=99)
    first = amplify(blocks, generator, profile, **kwargs)
    second = amplify(blocks, generator, profile, **kwargs)
    assert first.corpus == second.corpus
    assert first.stats() == second.stats()
    assert first.planned == len(blocks) * 4


def test_amplify_reports_planned_negatives_not_surviving_ones(profile, blocks):
    """The validity gate must not be able to quietly rebalance calibration: a
    recipe that asked for 15% negatives and got 3% is a failure to report, not a
    number to launder."""
    jobs = plan(blocks, profile, variants=20, neg_frac=0.5, rng=random.Random(4))
    valid = "On Day 7 in Denver something happened. " + "detail " * 30
    generations = [valid if not job.negative else "nope" for job in jobs]
    with pytest.raises(AmplifyBelowOkRate):
        collect(jobs, generations, blocks, profile, ok_rate_min=0.85, rng=random.Random(0))


def test_generator_returning_the_wrong_count_is_an_error(profile, blocks):
    jobs = plan(blocks, profile, variants=2, neg_frac=0.0, rng=random.Random(0))
    with pytest.raises(ValueError, match="for .* jobs"):
        collect(jobs, ["x"], blocks, profile, ok_rate_min=0.0, rng=random.Random(0))


def test_amplified_corpus_is_shuffled_out_of_block_order(profile, blocks):
    """Leaving paragraphs in block order lets CPT learn the day's running order
    as a shortcut instead of the facts."""
    def generator(prompts):
        return [f"On Day 7 in Denver, item {i} occurred. " + "detail " * 30
                for i, _ in enumerate(prompts)]

    result = amplify(blocks, generator, profile, variants=8, neg_frac=0.0,
                     ok_rate_min=0.85, seed=5)
    ordered = [n.text for n in result.narratives]
    assert result.corpus.split("\n\n") != ordered
    assert sorted(result.corpus.split("\n\n")) == sorted(ordered)


# ----------------------------------------------------------------------- replay

def test_fragments_are_never_rehearsed():
    text = "\n\n".join(["short", "x" * (MIN_PARAGRAPH_CHARS + 1), "tiny"])
    assert paragraphs([text]) == ["x" * (MIN_PARAGRAPH_CHARS + 1)]


def test_empty_reservoir_yields_no_rehearsal():
    """Night one has nothing to replay and must not fabricate any."""
    assert sample_replay([], frac=0.3, target_chars=1000, rng=random.Random(0)) == ""
    assert sample_replay(["a" * 500], frac=0.0, target_chars=1000, rng=random.Random(0)) == ""


def test_budget_overshoots_by_at_most_one_paragraph():
    """Truncating instead would rehearse half a fact."""
    sources = ["\n\n".join(["p" * 300] * 100)]
    picked = sample_replay(sources, frac=0.1, target_chars=10_000,
                           rng=random.Random(0)).split("\n\n")
    assert sum(len(p) for p in picked[:-1]) < 1000 <= sum(len(p) for p in picked)


def _calibration_share(profile, text: str) -> float:
    paras = text.split("\n\n")
    return sum(profile.is_calibration(p) for p in paras) / len(paras)


def test_neg_boost_over_samples_calibration_prose_within_the_same_budget(profile):
    """The <=10% knob, default off.

    Two properties, and both matter: it draws denial-phrased paragraphs
    SPECIFICALLY (a boost that sampled uniformly would be a knob with no effect),
    and it does so INSIDE the rehearsal budget rather than on top of it —
    calibration displaces recall material, which is exactly why turning it up to
    40% lobotomizes the adapter."""
    negative = "Contrary to what one might assume, X did not happen. " + "y" * 200
    positive = "z" * 300
    sources = ["\n\n".join([positive] * 400 + [negative] * 20)]

    plain = sample_replay(sources, frac=0.5, target_chars=100_000, rng=random.Random(0))
    boosted = sample_replay(sources, frac=0.5, target_chars=100_000, rng=random.Random(0),
                            neg_boost=0.10, is_calibration=profile.is_calibration)

    assert _calibration_share(profile, boosted) > 2 * _calibration_share(profile, plain)
    assert len(boosted) == pytest.approx(len(plain), rel=0.05), (
        "neg-boost inflated the rehearsal budget instead of reallocating it")


# ------------------------------------------------------------------------ train

def test_chunking_drops_the_ragged_tail_not_the_body():
    tokenizer = FakeTokenizer()
    chunks = chunk_corpus(tokenizer, "x" * 100, 32)
    assert [len(c) for c in chunks] == [32, 32, 32]      # trailing 4 tokens dropped
    assert all(len(c) > MIN_CHUNK_TOKENS for c in chunks)


def test_chunking_at_an_exact_multiple_makes_no_empty_chunk():
    chunks = chunk_corpus(FakeTokenizer(), "x" * 64, 32)
    assert [len(c) for c in chunks] == [32, 32]


def test_chunking_an_empty_corpus_yields_nothing():
    assert chunk_corpus(FakeTokenizer(), "", 32) == []


# ------------------------------------------------------------------------ probes

def test_probe_gold_pairing_mismatch_is_fatal(tmp_path):
    (tmp_path / "probes_x.jsonl").write_text(
        '{"messages": [{"role": "user", "content": "q1"}]}\n'
        '{"messages": [{"role": "user", "content": "q2"}]}\n')
    (tmp_path / "probes_x.gold.jsonl").write_text('{"probe_id": "x#0", "gold": "a"}\n')
    with pytest.raises(ValueError, match="pairing is positional"):
        load_suite(tmp_path, "x")


def test_missing_suite_is_empty_not_an_error(tmp_path):
    assert load_suite(tmp_path, "absent") == []


def test_day_pool_is_deterministic_and_bounded():
    """Same probes every step, so a decay-matrix cell is comparable to the one
    above it — a resampled pool would make forgetting indistinguishable from
    having drawn harder questions."""
    probes = [Probe("p0", "q", "g", day=5), Probe("p1", "q", "g", day=9),
              Probe("p2", "q", "g", day=5), Probe("p3", "q", "g", day=5)]
    assert [p.probe_id for p in day_pool(probes, 5, 2)] == ["p0", "p2"]
    assert day_pool(probes, 99, 5) == []


def test_probe_generator_must_differ_from_the_corpus_generator():
    assert generator_family("vertex_ai/gemini-2.5-flash") == "gemini"
    assert_independent_generators(probe_generator="gemini-3.1-pro-preview",
                                  corpus_generator="Qwen/Qwen3-VL-8B-Instruct")
    with pytest.raises(ProbeContamination, match="same family"):
        assert_independent_generators(probe_generator="gemini-3.1-pro-preview",
                                      corpus_generator="vertex_ai/gemini-2.5-flash")


def test_unknown_probe_provenance_fails_closed():
    with pytest.raises(ProbeContamination, match="unknown"):
        assert_independent_generators(probe_generator=None, corpus_generator="qwen")


# -------------------------------------------------------------------- readouts

def _judged(final_by_day, *, heldout=0.02, base=0.01):
    days = list(final_by_day)
    out = {"judge_exact_micro": 0.16,
           "final_heldout": {"n": 60, "judge_exact": heldout},
           "base_heldout": {"n": 60, "judge_exact": 0.0}}
    for step, day in enumerate(days):
        for seen in days[:step + 1]:
            value = final_by_day[seen] if step == len(days) - 1 else 0.4
            out[f"s{step}_d{seen}"] = {"n": 60, "judge_exact": value}
        out[f"base_d{day}"] = {"n": 60, "judge_exact": base}
    return out


def test_decay_matrix_is_lower_triangular():
    days = (5, 9, 12)
    matrix = decay_matrix(_judged({5: 0.2, 9: 0.3, 12: 0.5}), days)
    assert set(matrix) == {(0, 5), (1, 5), (1, 9), (2, 5), (2, 9), (2, 12)}


def test_separation_subtracts_the_heldout_floor():
    r = readout(_judged({5: 0.2, 9: 0.3, 12: 0.4}, heldout=0.05), (5, 9, 12))
    assert r.seen_mean == pytest.approx(0.3)
    assert r.separation == pytest.approx(0.25)


def test_retention_headline_is_the_longest_decay_path():
    r = readout(_judged({5: 0.2, 9: 0.3, 12: 0.4}), (5, 9, 12))
    assert r.retention_longest_path == pytest.approx(0.5)     # 0.2 now vs 0.4 when written
    assert 12 not in r.retention_per_day or r.retention_per_day[12] == 1.0


def test_band_membership_tolerates_only_judge_rounding():
    band = Band(seen_mean=(0.20, 0.30), separation=(0.10, 0.20),
                micro=(0.15, 0.18), heldout_max=0.05)
    inside = Readout(label="x", days=[1], final_per_day={}, base_per_day={},
                     seen_mean=0.30001, base_mean=0.0, heldout=0.02,
                     base_heldout=0.0, micro=0.16)
    outside = Readout(label="y", days=[1], final_per_day={}, base_per_day={},
                      seen_mean=0.31, base_mean=0.0, heldout=0.02,
                      base_heldout=0.0, micro=0.16)
    assert band.check(inside)["seen_mean"]
    assert not band.check(outside)["seen_mean"]
    assert not band.check(Readout(label="z", days=[1], final_per_day={}, base_per_day={},
                                  seen_mean=0.25, base_mean=0.0, heldout=0.30,
                                  base_heldout=0.0, micro=0.16))["heldout"]


# --------------------------------------------------------------- exec model

def test_missing_interpreter_names_the_knob(tmp_path):
    env = PinnedEnv(name="train", interpreter=str(tmp_path / "nope"))
    with pytest.raises(EnvironmentUnusable, match="MORPHEUS_TRAIN_PYTHON"):
        env.resolved()


def test_preflight_catches_a_missing_module():
    import sys
    env = PinnedEnv(name="train", interpreter=sys.executable,
                    requires=("definitely_not_installed_xyz",))
    with pytest.raises(EnvironmentUnusable, match="cannot import"):
        env.preflight()


def test_preflight_passes_on_a_usable_interpreter():
    import sys
    PinnedEnv(name="train", interpreter=sys.executable, requires=("json",)).preflight()


def test_configured_probe_generator_is_independent_of_the_base_model():
    """The shipped defaults must satisfy the rule they declare — a contamination
    check nobody can pass is a comment, not a check."""
    from app.config import get_settings
    settings = get_settings().morpheus
    assert_independent_generators(probe_generator=settings.probe_generator,
                                  corpus_generator=settings.base_model)


def test_amplify_and_train_envs_are_separate():
    """vLLM and the training stack pin incompatible transformers, so 'the ML env'
    does not exist. A config that collapsed them would fail only at the generator
    call, after the day log was already built."""
    from app.config import get_settings
    from app.morpheus.pinned_env import amplify_env, judge_env, train_env
    settings = get_settings().morpheus
    assert settings.amplify_python != settings.train_python
    assert {e.name for e in (train_env(settings), amplify_env(settings),
                             judge_env(settings))} == {"train", "amplify", "judge"}
