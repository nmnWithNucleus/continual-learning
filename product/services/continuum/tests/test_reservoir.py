"""The local reservoir, keyed on OPAQUE window ids.

Window ids here are minted the way storage mints them (`w<YYYYMMDD>T<HHMMSS>Z`)
rather than written as the old `w<local-date>` literals. That is not cosmetic:
`entries()` orders by the id and `before_window` filters on it with a plain string
comparison, and D18 named the hazard explicitly — mixed formats order correctly
only by ASCII accident (`-` = 0x2D sorts below `0` = 0x30), so the cutover has to
be tested under the new format rather than trusted.
"""
from app.reservoir import Reservoir
from tests._helpers import make_window

PARA = ("A long paragraph about the day that easily clears the hundred character "
        "floor used by the replay sampler for paragraph eligibility. ")

W18 = make_window("u1", 18, "UTC").window_id
W19 = make_window("u1", 19, "UTC").window_id
W20 = make_window("u1", 20, "UTC").window_id


def _corpus(tag: str, n: int = 20) -> str:
    return "\n\n".join(f"{PARA}[{tag}-{i}]" for i in range(n))


def test_admit_and_entries_ordering(tmp_path):
    res = Reservoir(tmp_path)
    res.admit("u1", W18, "r1", _corpus("a"))
    res.admit("u1", W19, "r1", _corpus("b"))
    ids = [e.window_id for e in res.entries("u1")]
    assert ids == [W18, W19]
    assert ids == sorted(ids)   # string order == chronological order


def test_before_window_excludes_current_and_future(tmp_path):
    res = Reservoir(tmp_path)
    res.admit("u1", W18, "r1", _corpus("a"))
    res.admit("u1", W19, "r1", _corpus("b"))
    ids = [e.window_id for e in res.entries("u1", before_window=W19)]
    assert ids == [W18]


def test_sample_replay_respects_budget_and_determinism(tmp_path):
    res = Reservoir(tmp_path)
    res.admit("u1", W18, "r1", _corpus("a"))
    res.admit("u1", W19, "r1", _corpus("b"))
    target = 4000
    replay = res.sample_replay("u1", target_chars=target, frac=0.30, seed=1,
                               before_window=W20)
    assert replay
    # Budget: fills up to ~frac*target, overshooting by at most one paragraph.
    assert len(replay) <= int(0.30 * target) + len(PARA) + 20
    again = res.sample_replay("u1", target_chars=target, frac=0.30, seed=1,
                              before_window=W20)
    assert replay == again  # deterministic under the same seed


def test_empty_reservoir_first_night(tmp_path):
    res = Reservoir(tmp_path)
    assert res.sample_replay("u1", target_chars=1000, frac=0.3, seed=1) == ""


def test_admit_is_idempotent(tmp_path):
    res = Reservoir(tmp_path)
    e1 = res.admit("u1", W18, "r1", _corpus("a"))
    e2 = res.admit("u1", W18, "r1", _corpus("a"))
    assert e1 == e2
    assert len(res.entries("u1")) == 1


def test_entry_cannot_parse_its_window_id_back_into_a_date(tmp_path):
    """`ReservoirEntry.local_window_date()` is DELETED (D18). It parsed the id back
    into a local date so a prior window could be reconstructed under tonight's
    timezone — wrong under travel, and meaningless once a window can span 47 h.
    Prior windows come from storage's enumeration read instead."""
    from app.reservoir import ReservoirEntry
    entry = Reservoir(tmp_path).admit("u1", W18, "r1", _corpus("a"))
    assert not hasattr(entry, "local_window_date")
    assert not hasattr(ReservoirEntry, "local_window_date")
