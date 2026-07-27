"""M9's differential proof, as a permanent test.

`scripts/daylog_parity_diff.py` renders one fixture window through BOTH day-log renderers —
storage's `materialize_daylog` and continuum's `LocalDayLogClient` — and reports whether they
agree. This module runs that same proof in-process so **a future change to either renderer
trips the bar in the suite**, not only when someone remembers to run the script by hand. The
script is the readable artifact (M9 requires its output be committed); this is the tripwire.

The bar asserted here is CHARTER M9's, NARROWED 2026-07-27 (D20), and the three tiers are
kept apart on purpose:

  tier A  BYTE-IDENTICAL, non-negotiable — block text, block ordering, `block_id`, `anchors`,
          block `quality`, segment payloads. This is the artifact that trains the model.
  tier B  PROVEN-EQUIVALENT, not identical — `seg_id`: an order-preserving bijection with
          per-block membership preserved. Measured by the script, asserted here.
  tier C  EXCLUDED deliberately — `content_fingerprint`: reported, never asserted. It hashes
          `seg_id` and is a cache key compared only to itself.

EXTENDED 2026-07-27 (F4) TO TWO WINDOW ORIGINS. The bar used to run over exactly one
continuum-side event-window origin, and that origin was ALIGNED to the segment grid — the
only case in which continuum's then window-relative bucket grid and storage's global epoch
grid are the same partition of the timeline. No window this system mints has such an origin
(`[watermark, now-delta)` at second granularity), and with the origin shifted 1..9 s tier A
failed at every one of the nine. The grid was fixed in continuum's reference renderer; the
proof now runs the whole origin-dependent bar TWICE, once per origin, and
`test_the_bar_runs_over_both_origins` is what stops that coverage being quietly dropped
again.

Three properties of this module are deliberate and should survive edits:

  * it asserts against `ProofResult`'s recorded checks, NOT against the report's prose, so
    rewording the report cannot silently disarm the test;
  * `test_the_bar_itself_is_intact` pins the exact set of binding check ids AND the total
    number of recorded checks. Without it, deleting checks from the script would make every
    other test here pass vacuously — which is precisely the failure mode M9 exists to
    prevent ("never weaken an assertion to make a report green"); and
  * `test_the_bar_runs_over_both_origins` pins that one of the two origins is genuinely OFF
    the grid, so the misaligned case cannot be "fixed" by aligning it.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

_STORAGE_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _STORAGE_ROOT / "scripts" / "daylog_parity_diff.py"

# The bar, as check ids. A check that disappears from the script is a bar that quietly
# stopped looking, so this set is asserted verbatim below.
EXPECTED_PRECONDITIONS = {"P1", "P2", "P3", "P4", "P5", "P6"}
EXPECTED_TIER_A = {"A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8"}
EXPECTED_TIER_B = {"B1", "B2", "B3", "B4"}

# Which ids run ONCE PER ORIGIN, and which run once for the whole proof. P1/P2/P4/P5 are
# facts about the FIXTURE and about storage's body, which no continuum-side origin can
# change; A8 is the cross-origin comparison itself and exists exactly once.
ORIGIN_INDEPENDENT = {"P1", "P2", "P4", "P5", "A8"}
PER_ORIGIN = (EXPECTED_PRECONDITIONS | EXPECTED_TIER_A | EXPECTED_TIER_B) - ORIGIN_INDEPENDENT

EXPECTED_ORIGINS = 2
EXPECTED_CHECKS = len(ORIGIN_INDEPENDENT) + EXPECTED_ORIGINS * len(PER_ORIGIN)   # 5 + 2*13


def _load_script():
    """Import the proof script by path.

    By path rather than by package: `scripts/` has no `__init__.py` and is not importable as
    a package, and the script is meant to stay runnable as a standalone CLI. Importing the
    real file is the point — a copy of the proof here would prove nothing about the script
    that gets committed.

    The module is registered in `sys.modules` BEFORE it is executed, which is not
    housekeeping: `@dataclass` resolves its own annotations through
    `sys.modules[cls.__module__]`, so a module that executes while absent from the table
    raises `AttributeError: 'NoneType' object has no attribute '__dict__'` the moment the
    script declares one. Running the script as a CLI never hit it because `__main__` is
    always in the table.
    """
    spec = importlib.util.spec_from_file_location("daylog_parity_diff", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script():
    """The proof script module itself — needed to read its declared `ORIGINS`."""
    assert _SCRIPT.exists(), f"the proof script is missing: {_SCRIPT}"
    return _load_script()


@pytest.fixture(scope="module")
def proof(script):
    """Run the differential proof ONCE for this module.

    `run()` is pure: it builds its own temp SQLite DB + blob dir, renders both sides and
    returns the result. It writes no report file — only `main()` does that — so the test
    never touches the committed `daylog_parity_diff.out.txt`.
    """
    return script.run()


def _ids(pairs: list[tuple[str, bool]]) -> set[str]:
    return {name.split()[0] for name, _ in pairs}


def _failed(pairs: list[tuple[str, bool]]) -> list[str]:
    return [name for name, ok in pairs if not ok]


def _origin_of(name: str) -> str | None:
    """The origin tag a check name carries, e.g. `A2  [misaligned] block text ...`.

    None for the origin-independent checks, which carry no tag by design.
    """
    match = re.match(r"^[PAB]\d+\s+\[([^\]]+)\]", name)
    return match.group(1) if match else None


def test_the_bar_itself_is_intact(proof):
    """The set of binding checks is what M9 says it is — no check silently deleted.

    This test is the reason the others cannot pass vacuously: `test_tier_a_is_byte_identical`
    asserts that no tier-A check FAILED, which is trivially true of an empty tier.

    The count is pinned as well as the id set, and the two are different guards now that
    checks repeat per origin: the id set catches a DELETED check, the count catches a check
    that stopped running for one of the origins while still existing for the other.
    """
    assert _ids(proof.tier("P")) == EXPECTED_PRECONDITIONS
    assert _ids(proof.tier("A")) == EXPECTED_TIER_A
    assert _ids(proof.tier("B")) == EXPECTED_TIER_B
    # And nothing else is binding: tier C and the demoted D5/D6/D9 dimensions are printed,
    # never checked, so a stray `check("C", ...)` would show up here.
    assert proof.checks == EXPECTED_CHECKS, [name for _, name, _ in proof.results]


def test_the_bar_runs_over_both_origins(proof, script):
    """F4. The origin-dependent half of the bar runs over a MISALIGNED window origin too.

    Three separate claims, because dropping any one of them restores the F4 defect:

      1. the script declares exactly `EXPECTED_ORIGINS` origins, and at least one of them is
         genuinely off the segment grid — asserted against the arithmetic, not against the
         `aligned` flag, so mislabelling an aligned origin as misaligned does not pass;
      2. every per-origin check id really ran once for EVERY origin (not just the aligned
         one); and
      3. the origin-independent ids ran exactly once and carry no origin tag.

    Why it matters: continuum's local renderer used to bucket records window-relatively while
    storage buckets on the global epoch grid. Those agree only when the window origin sits on
    a segment boundary — and real windows are `[watermark, now-delta)` at second granularity,
    so nine origins in ten do not. Proving tier A on an aligned origin alone proved it for a
    window this system never mints.
    """
    origins = list(script.ORIGINS)
    assert len(origins) == EXPECTED_ORIGINS, [o.name for o in origins]
    segment_seconds = script.DEFAULT_SEGMENT_SECONDS
    off_grid = [o for o in origins if o.remainder(segment_seconds) != 0]
    on_grid = [o for o in origins if o.remainder(segment_seconds) == 0]
    assert off_grid, (
        "every declared origin sits ON the segment grid — that is precisely the F4 blind "
        "spot, and no window storage mints has such an origin: "
        f"{[(o.name, o.start.isoformat()) for o in origins]}")
    assert on_grid, "the aligned origin is still worth covering; do not drop it"
    # The declared `aligned` flag must agree with the arithmetic, or P3 asserts nothing.
    for origin in origins:
        assert origin.aligned == (origin.remainder(segment_seconds) == 0), origin

    names = [name for _, name, _ in proof.results]
    tags = {o.name for o in origins}
    for check_id in sorted(PER_ORIGIN):
        seen = {_origin_of(n) for n in names if n.split()[0] == check_id}
        assert seen == tags, (check_id, sorted(t or "<untagged>" for t in seen))
    for check_id in sorted(ORIGIN_INDEPENDENT):
        matching = [n for n in names if n.split()[0] == check_id]
        assert len(matching) == 1, (check_id, matching)
        assert _origin_of(matching[0]) is None, matching


def test_preconditions_hold(proof):
    """The fixture still neutralises the three legitimate divergences (N1/N2/N3), is still
    valid C2/C10, and still renders something (P6). If a precondition breaks, the tiers below
    are measuring the wrong thing and their result means nothing — P6 in particular, since a
    bar made entirely of comparisons is green on two empty renders."""
    assert _failed(proof.tier("P")) == [], proof.report_text


def test_tier_a_is_byte_identical(proof):
    """M9(a). Block text, ordering, `block_id`, `anchors`, block `quality` and the segment
    payloads are byte-identical across the two renderers.

    A failure here means the two renderers produce DIFFERENT TRAINING TEXT — the day-log's
    *content*, which ARCHITECTURE §Ownership splits ('Day-log: representation vs. content')
    puts outside storage's unilateral control. It is a contract break, not a test to relax:
    the remedy is a `daylog_format_version` bump and a re-run, or a fix to the renderer.
    """
    assert _failed(proof.tier("A")) == [], proof.report_text


def test_tier_b_seg_id_is_a_proven_equivalent_relabelling(proof):
    """M9(b). `seg_id` differs BY DESIGN — the labels are storage's representation — but the
    relabelling must be an order-preserving bijection with per-block membership preserved.

    A failure here means the relabelling did more than rename: a segment was dropped,
    duplicated, reordered or moved between blocks. That is a real defect even though the
    labels themselves are storage's to choose.
    """
    assert _failed(proof.tier("B")) == [], proof.report_text


def test_verdict_is_pass(proof):
    """The script's own verdict agrees with the tier assertions above — i.e. the CLI exits 0
    and a committed report says PASS. Belt and braces: this catches a future edit that adds a
    binding tier the assertions above do not enumerate."""
    assert proof.failures == [], proof.report_text
    assert proof.exit_code == 0
    assert "PASS" in proof.report_text.rsplit("VERDICT", 1)[-1]


def test_tier_c_fingerprint_is_reported_and_asserted_nowhere(proof):
    """M9(c). `content_fingerprint` is EXCLUDED: both values must be reported, and no binding
    check may depend on them.

    Note what this test does NOT do: it does not assert the two fingerprints are equal, and
    it does not assert they differ either. The fingerprint hashes `seg_id` and is a cache key
    compared only to itself across runs — it SHOULD change when the renderer changes, and
    pinning it in either direction would make the cache lie. What is worth protecting is the
    *exclusion*: that a later edit does not quietly promote it back into the bar.
    """
    values = list(proof.fingerprints.values())
    assert len(values) == 2, proof.report_text
    assert all(len(v) == 64 and set(v) <= set("0123456789abcdef") for v in values), values
    binding = {name for name, _ in proof.tier("P") + proof.tier("A") + proof.tier("B")}
    assert not any("fingerprint" in name.lower() for name in binding), sorted(binding)


def test_expected_divergence_is_still_printed(proof):
    """The demoted D5/D6/D9 output is still in the report.

    D20 narrowed the bar; it did not license hiding what differs. A reader of the committed
    report must be able to see the raw `seg_id` divergence and both fingerprints and judge
    for themselves, rather than trust a section that stopped being printed when it stopped
    being binding.
    """
    assert "EXPECTED DIVERGENCE, EXPLAINED" in proof.report_text
    assert "(was D5)" in proof.report_text
    assert "(was D6)" in proof.report_text
    assert "(was D9)" in proof.report_text
    for fingerprint in proof.fingerprints.values():
        assert fingerprint in proof.report_text
