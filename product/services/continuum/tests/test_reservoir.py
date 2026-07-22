from app.reservoir import Reservoir

PARA = ("A long paragraph about the day that easily clears the hundred character "
        "floor used by the replay sampler for paragraph eligibility. ")


def _corpus(tag: str, n: int = 20) -> str:
    return "\n\n".join(f"{PARA}[{tag}-{i}]" for i in range(n))


def test_admit_and_entries_ordering(tmp_path):
    res = Reservoir(tmp_path)
    res.admit("u1", "w2026-07-18", "r1", _corpus("a"))
    res.admit("u1", "w2026-07-19", "r1", _corpus("b"))
    ids = [e.window_id for e in res.entries("u1")]
    assert ids == ["w2026-07-18", "w2026-07-19"]


def test_before_window_excludes_current_and_future(tmp_path):
    res = Reservoir(tmp_path)
    res.admit("u1", "w2026-07-18", "r1", _corpus("a"))
    res.admit("u1", "w2026-07-19", "r1", _corpus("b"))
    ids = [e.window_id for e in res.entries("u1", before_window="w2026-07-19")]
    assert ids == ["w2026-07-18"]


def test_sample_replay_respects_budget_and_determinism(tmp_path):
    res = Reservoir(tmp_path)
    res.admit("u1", "w2026-07-18", "r1", _corpus("a"))
    res.admit("u1", "w2026-07-19", "r1", _corpus("b"))
    target = 4000
    replay = res.sample_replay("u1", target_chars=target, frac=0.30, seed=1,
                               before_window="w2026-07-20")
    assert replay
    # Budget: fills up to ~frac*target, overshooting by at most one paragraph.
    assert len(replay) <= int(0.30 * target) + len(PARA) + 20
    again = res.sample_replay("u1", target_chars=target, frac=0.30, seed=1,
                              before_window="w2026-07-20")
    assert replay == again  # deterministic under the same seed


def test_empty_reservoir_first_night(tmp_path):
    res = Reservoir(tmp_path)
    assert res.sample_replay("u1", target_chars=1000, frac=0.3, seed=1) == ""


def test_admit_is_idempotent(tmp_path):
    res = Reservoir(tmp_path)
    e1 = res.admit("u1", "w2026-07-18", "r1", _corpus("a"))
    e2 = res.admit("u1", "w2026-07-18", "r1", _corpus("a"))
    assert e1 == e2
    assert len(res.entries("u1")) == 1
