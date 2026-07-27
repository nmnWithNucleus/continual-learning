"""The storage HTTP seams — the D18 cutover, exercised end to end over a mock
transport.

Every client is driven through `httpx.MockTransport`, so the assertions are about
the WIRE: which path, which params, which body, and what continuum does with the
response. That is the contract; standing up storage would test storage.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest

from app.clients import (HttpDayLogClient, HttpProfileClient, HttpRecipeRegistry,
                         HttpReservoirClient, HttpWindowLedger, LocalDayLogClient)
from app.clients.daylog_client import (DayLogDialectMismatch, DayLogUnavailable,
                                       daylog_fingerprint)
from app.clients.window_client import WindowNotOpenable
from app.clients.profile_client import UserNotSchedulable
from app.clients.registry import RecipeNotFound
from app.renderer import blocks_text, render_daylog_files
from app.synth import synth_records
from app.window import Window
from tests._helpers import make_window

STORAGE = "http://storage"


def _transport(handler):
    return httpx.MockTransport(handler)


def _daylog_body(win: Window, *, segments=None, blocks=None,
                 fingerprint="fp-from-storage", format_version="1",
                 recipe_id=None) -> dict:
    """A C10 v1 body as storage actually serves one.

    The two version stamps used to be invented here — `"daylog-v1"` and a recipe id
    nobody was pinned to — which was harmless only for as long as continuum ignored
    them. `daylog_format_version` is storage's `DAYLOG_FORMAT_VERSION`, the string
    `"1"`; `recipe_id` defaults to whatever THIS run is pinned to, so the happy path
    stays true when the pin moves and only the tests that mean to disagree do.
    """
    from app.config import get_settings
    return {
        "contract": "C10", "version": "1",
        "user_id": win.user_id, "window_id": win.window_id,
        "t_start": win.start_utc.isoformat().replace("+00:00", "Z"),
        "t_end": win.end_utc.isoformat().replace("+00:00", "Z"),
        "daylog_format_version": format_version,
        "recipe_id": recipe_id if recipe_id is not None else get_settings().recipe_id,
        "home_tz": win.tz,
        "segments": segments if segments is not None else [{
            "seg_id": f"{win.window_id}_s00000",
            "t_start": "2026-07-20T15:00:00Z", "t_end": "2026-07-20T15:00:10Z",
            "caption": ["The wearer sits at a wooden desk."],
            "asr": [{"spk": "mara", "text": "let's move the demo", "t": "2026-07-20T15:00:02Z"}],
            "ocr": ["Q3 ROADMAP"], "quality": None, "tz": "America/Los_Angeles"}],
        "blocks": blocks if blocks is not None else [{
            "block_id": f"{win.window_id}_b0000",
            "seg_ids": [f"{win.window_id}_s00000"],
            "text": "On 2026-07-20, around 08:00–08:00 local time:\nScene: a desk",
            "anchors": {"date": "2026-07-20", "place": None}, "quality": None}],
        "content_fingerprint": fingerprint,
    }


# ------------------------------------------------------------------ C10 day-log

def test_http_daylog_client_gets_the_c10_evolved_read():
    win = make_window("u-h", 20, "America/Los_Angeles")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json=_daylog_body(win))

    daylog = HttpDayLogClient(STORAGE, transport=_transport(handler)).fetch_daylog(win)
    assert seen["path"] == "/training/daylog"
    # Addressed by (user_id, window_id) TOGETHER — window_id is a per-user token.
    assert seen["params"] == {"user_id": "u-h", "window_id": win.window_id}
    assert daylog.window_id == win.window_id and daylog.user_id == "u-h"
    assert daylog.segments[0].caption == ["The wearer sits at a wooden desk."]
    assert daylog.segments[0].asr[0]["spk"] == "mara"
    assert daylog.segments[0].tz == "America/Los_Angeles"
    assert daylog.blocks[0].anchors["date"] == "2026-07-20"


def test_http_daylog_fingerprint_is_storages_not_recomputed():
    """`content_fingerprint` is computed BY WHOEVER RENDERS and is only ever
    compared to itself across runs. Re-deriving it here would answer a different
    question under the same name — and would make the cycle's cache lie."""
    win = make_window("u-h", 20, "UTC")
    client = HttpDayLogClient(STORAGE, transport=_transport(
        lambda r: httpx.Response(200, json=_daylog_body(win, fingerprint="server-fp"))))
    daylog = client.fetch_daylog(win)
    assert client.fingerprint(daylog) == "server-fp"
    assert client.fingerprint(daylog) != daylog_fingerprint(daylog)


def test_http_daylog_renders_the_same_trainer_seam_files(tmp_path):
    """The trainer reads segments.jsonl / blocks.jsonl / day.txt off disk and must
    not know or care who built the rows."""
    win = make_window("u-h", 20, "UTC")
    client = HttpDayLogClient(STORAGE, transport=_transport(
        lambda r: httpx.Response(200, json=_daylog_body(win))))
    paths = client.render(client.fetch_daylog(win), tmp_path)
    seg = json.loads(open(paths["segments"]).read().splitlines()[0])
    blk = json.loads(open(paths["blocks"]).read().splitlines()[0])
    assert {"seg_id", "t_start", "t_end", "caption", "asr", "ocr", "quality", "tz"} \
        == set(seg)
    assert {"block_id", "seg_ids", "text", "anchors", "quality"} == set(blk)
    assert open(paths["day_txt"]).read().startswith("On 2026-07-20")


def test_http_daylog_404_is_loud_and_is_not_an_empty_day():
    """"Storage has no day-log for this window" and "this window had no data" are
    different facts, and only one of them may quietly leave the watermark alone."""
    win = make_window("u-h", 20, "UTC")
    client = HttpDayLogClient(STORAGE, transport=_transport(
        lambda r: httpx.Response(404, json={"detail": "nope"})))
    with pytest.raises(DayLogUnavailable, match="NOT an empty day"):
        client.fetch_daylog(win)


def test_http_daylog_rejects_a_body_of_the_wrong_contract():
    win = make_window("u-h", 20, "UTC")
    body = _daylog_body(win) | {"version": "0"}
    client = HttpDayLogClient(STORAGE, transport=_transport(
        lambda r: httpx.Response(200, json=body)))
    with pytest.raises(ValueError, match="C10 v1"):
        client.fetch_daylog(win)


def test_http_daylog_rejects_a_body_for_another_window():
    """A day-log addressed to somebody else's window must never be trained on."""
    win = make_window("u-h", 20, "UTC")
    other = make_window("u-other", 19, "UTC")
    client = HttpDayLogClient(STORAGE, transport=_transport(
        lambda r: httpx.Response(200, json=_daylog_body(other))))
    with pytest.raises(ValueError, match="different window"):
        client.fetch_daylog(win)


def test_a_day_log_rendered_under_another_recipe_is_refused():
    """REGRESSION (F3). `recipe_id` on the C10 body is an ANNOUNCEMENT; reading it is
    what makes it one.

    The two sides are pinned independently — storage's `STORAGE_DAYLOG_RECIPE_ID`,
    continuum's `CONTINUUM_RECIPE_ID` — so a half-finished re-pin is an ordinary
    deployment slip, and it is silent by construction: the body parses, the night
    trains, and publish writes CONTINUUM's `recipe_id` into C5, labelling the
    artifact with a recipe it was not trained under. Storage cannot catch it (it
    stamps what it rendered, honestly); only the side that holds both facts can.
    """
    win = make_window("u-h", 20, "UTC")
    client = HttpDayLogClient(STORAGE, recipe_id="consolidation-v1.1",
                              transport=_transport(lambda r: httpx.Response(
                                  200, json=_daylog_body(win,
                                                         recipe_id="consolidation-v1.0"))))
    with pytest.raises(DayLogDialectMismatch) as exc:
        client.fetch_daylog(win)
    msg = str(exc.value)
    # Both pins are NAMED, because "mismatch" without the two env vars is a puzzle.
    assert "STORAGE_DAYLOG_RECIPE_ID" in msg and "CONTINUUM_RECIPE_ID" in msg
    assert "consolidation-v1.0" in msg and "consolidation-v1.1" in msg


def test_a_day_log_in_an_unknown_format_dialect_is_refused():
    """REGRESSION (F3), the other stamp. `daylog_format_version` bumps when the
    rendered BLOCK TEXT changes (D20) — and that string is the training corpus, so a
    dialect this build was never proven against must not reach the trainer."""
    win = make_window("u-h", 20, "UTC")
    client = HttpDayLogClient(STORAGE, transport=_transport(
        lambda r: httpx.Response(200, json=_daylog_body(win, format_version="2"))))
    with pytest.raises(DayLogDialectMismatch, match="format version"):
        client.fetch_daylog(win)


def test_the_supported_dialect_set_is_code_and_not_an_env_knob():
    """The policy, pinned: which day-log dialects this trainer can read is a property
    of the CODE (and of the parity proof run against it), not of the environment. An
    env override would let an operator wave a corpus change through as a config
    change — precisely the failure `daylog_format_version` exists to prevent."""
    import ast
    import inspect

    from app.clients import daylog_client as mod

    assert mod.SUPPORTED_DAYLOG_FORMAT_VERSIONS == ("1",)
    # Mechanical, so that prose about the policy cannot satisfy it: the set must be a
    # LITERAL tuple of literal strings at module level. A value assembled from an env
    # read, a settings field or a default-with-override could not parse as one.
    tree = ast.parse(inspect.getsource(mod))
    literals = [
        node.value for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for tgt in ([node.target] if isinstance(node, ast.AnnAssign) else node.targets)
        if isinstance(tgt, ast.Name) and tgt.id == "SUPPORTED_DAYLOG_FORMAT_VERSIONS"
    ]
    assert len(literals) == 1, "declared exactly once, at module level"
    assert isinstance(literals[0], ast.Tuple)
    assert all(isinstance(e, ast.Constant) and isinstance(e.value, str)
               for e in literals[0].elts)


def test_the_expected_recipe_is_the_one_this_night_trains_under(monkeypatch):
    """The expectation tracks the RUN, not the environment.

    `run_cycle` resolves its recipe once and passes it down; the day-log client is
    built from that object. So a caller that hands `run_cycle` a recipe explicitly
    (tests, a re-drive, a fork) gets a fetch checked against THAT recipe rather than
    against whatever `CONTINUUM_RECIPE_ID` happens to say.
    """
    from app.clients import day_log_client
    from app.config import get_settings
    from app.recipe import Recipe

    monkeypatch.setenv("CONTINUUM_STORAGE_CLIENTS", "http")
    monkeypatch.setenv("CONTINUUM_RECIPE_ID", "consolidation-v1.0")
    recipe = Recipe(recipe_id="consolidation-test-1min-v1.0", variants=1, neg_frac=0.0,
                    ok_rate_min=0.0, replay_frac=0.0, replay_source="amp",
                    replay_neg_boost=0.0, lora_r=8, lora_alpha=16, lr=1e-4, epochs=1,
                    batch_size=1, chunk_tokens=128,
                    objective="next-token CPT (never QA-SFT)", quality_min=0.5,
                    block_segments=5, segment_seconds=60, boundary_local_time="04:00")
    client = day_log_client(get_settings(), recipe)
    assert client.recipe_id == "consolidation-test-1min-v1.0"


def test_replays_fetch_of_a_prior_day_log_is_dialect_checked_too():
    """Replay re-reads PRIOR windows' day-logs through this same client (the locked
    architecture), so the check cannot be a property of "tonight's" fetch only. A
    prior day-log storage re-materialized under a new pin is rehearsal text in a
    dialect this night does not train in."""
    prior = make_window("u-res", 18, "UTC")
    daylog_client = HttpDayLogClient(
        STORAGE, recipe_id="consolidation-v1.1",
        transport=_transport(lambda r: httpx.Response(
            200, json=_daylog_body(prior, recipe_id="consolidation-v1.0"))))
    client = HttpReservoirClient(STORAGE, daylog_client=daylog_client,
                                 transport=_transport(lambda r: httpx.Response(500)))
    with pytest.raises(DayLogDialectMismatch):
        client.sample_replay("u-res", target_chars=1000, frac=0.5, seed=1,
                             source="rawlog", prior_windows=[prior])


def test_http_daylog_error_status_raises_never_truncates():
    win = make_window("u-h", 20, "UTC")
    client = HttpDayLogClient(STORAGE, transport=_transport(
        lambda r: httpx.Response(500)))
    with pytest.raises(httpx.HTTPStatusError):
        client.fetch_daylog(win)


def test_http_daylog_client_satisfies_the_same_protocol_as_local():
    """The seam's whole promise: the cycle calls four methods and cannot tell which
    backend answered."""
    win = make_window("u-h", 20, "UTC")
    http = HttpDayLogClient(STORAGE, transport=_transport(
        lambda r: httpx.Response(200, json=_daylog_body(win))))
    local = LocalDayLogClient.from_records(synth_records(win, seed=7, events=10))
    for client in (http, local):
        daylog = client.fetch_daylog(win)
        assert client.eligible_blocks(daylog, 0.5)
        assert isinstance(client.fingerprint(daylog), str)
        assert blocks_text(daylog.blocks)


# ------------------------------------------------------------- window ledger

def _window_row(win: Window, *, state="open", outcome=None) -> dict:
    return {"window_id": win.window_id, "user_id": win.user_id,
            "t_start": win.start_utc.isoformat().replace("+00:00", "Z"),
            "t_end": win.end_utc.isoformat().replace("+00:00", "Z"),
            "state": state, "outcome": outcome,
            "opened_at": "2026-07-20T04:00:00Z", "closed_at": None}


def test_window_open_posts_user_and_returns_the_ledger_row():
    win = make_window("u-w", 20, "UTC")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"], seen["path"] = request.method, request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_window_row(win))

    opened = HttpWindowLedger(STORAGE, transport=_transport(handler)).open(
        "u-w", tz="America/Los_Angeles")
    assert (seen["method"], seen["path"]) == ("POST", "/training/windows")
    assert seen["body"] == {"user_id": "u-w"}       # bounds are NOT settable
    assert opened.window_id == win.window_id
    assert opened.start_utc == win.start_utc and opened.end_utc == win.end_utc
    # home_tz is stamped on by us; storage's ledger row carries no zone because the
    # ingest-time window needs none.
    assert opened.tz == "America/Los_Angeles"
    assert opened.state == "open"


def test_window_open_is_idempotent_get_or_create():
    """A retry must return the SAME window: a fresh id would mean a fresh journal,
    a full re-train, a second C5 entry and a second reservoir admission."""
    win = make_window("u-w", 20, "UTC")
    ledger = HttpWindowLedger(STORAGE, transport=_transport(
        lambda r: httpx.Response(200, json=_window_row(win))))
    first = ledger.open("u-w", tz="UTC")
    second = ledger.open("u-w", tz="UTC")
    assert first == second


@pytest.mark.parametrize("detail,expect", [
    ({"error": "no ingest history", "user_id": "u-w",
      "reason": "user has never been trained and has no /context records, so there "
                "is no window start to name"},
     "no ingest history"),
    ({"error": "window end is not strictly greater than the floor", "user_id": "u-w",
      "t_end": "2026-07-20T03:59:00Z", "floor": "2026-07-20T04:00:00Z",
      "reason": "a window must advance past every prior window end; this is what "
                "makes an id collision impossible"},
     "not strictly greater"),
])
def test_a_409_on_open_is_a_named_condition_carrying_storages_reason(detail, expect):
    """REGRESSION (F5). A user who cannot be consolidated YET is not a crash.

    Both 409s storage raises here are ordinary — a user with no ingest history (a new
    account, an empty demo store) and one whose every record is younger than
    storage's in-flight `delta`, which fixes itself with no operator action at all.
    They used to surface as `httpx.HTTPStatusError: Client error '409 Conflict' for
    url .../training/windows` and a stack trace, which reads as a broken service.
    """
    ledger = HttpWindowLedger(STORAGE, transport=_transport(
        lambda r: httpx.Response(409, json={"detail": detail})))
    with pytest.raises(WindowNotOpenable) as exc:
        ledger.open("u-w", tz="UTC")
    assert expect in str(exc.value)
    assert detail["reason"] in str(exc.value)   # storage's own words, not a paraphrase
    assert exc.value.detail == detail           # ...and the whole body, for the log
    # A NON-409 is still an exception: only "the user is not ready" is a condition.
    boom = HttpWindowLedger(STORAGE, transport=_transport(
        lambda r: httpx.Response(500, json={"detail": "storage is on fire"})))
    with pytest.raises(httpx.HTTPStatusError):
        boom.open("u-w", tz="UTC")


def test_prior_windows_enumerates_consolidated_and_filters_strictly_before():
    """The read that replaced `cycle.py`'s reconstruction of prior windows. The id
    is compared with `<` and touched in no other way."""
    days = [make_window("u-w", d, "UTC") for d in (18, 19, 20)]
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json=[_window_row(w, state="consolidated",
                                                     outcome="published")
                                         for w in days[:2]] +
                              [_window_row(days[2])])

    ledger = HttpWindowLedger(STORAGE, transport=_transport(handler))
    prior = ledger.prior_windows("u-w", days[2].window_id, tz="UTC")
    assert seen["path"] == "/training/windows"
    assert seen["params"] == {"user_id": "u-w", "state": "consolidated"}
    assert [w.window_id for w in prior] == [days[0].window_id, days[1].window_id]
    # ...and the current window is never in its own replay pool.
    assert days[2].window_id not in [w.window_id for w in prior]
    # Bounds come back from the ledger — nothing here recomputed them.
    assert prior[0].start_utc == days[0].start_utc


def test_window_close_records_the_outcome():
    win = make_window("u-w", 20, "UTC")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"], seen["path"] = request.method, request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_window_row(win, state="consolidated",
                                                    outcome="published"))

    closed = HttpWindowLedger(STORAGE, transport=_transport(handler)).close(
        win, "published")
    assert seen["method"] == "POST"
    assert seen["path"] == f"/training/windows/{win.window_id}/close"
    # user_id rides along: window_id is a PER-USER token, so a close keyed on the id
    # alone would be a cross-user write.
    assert seen["body"] == {"user_id": "u-w", "outcome": "published"}
    assert closed.state == "consolidated" and closed.outcome == "published"


def test_window_close_rejects_an_outcome_outside_the_enum():
    """An unrecognised outcome must fail here, not become a silent non-advance (or,
    worse, a silent advance) of the watermark."""
    win = make_window("u-w", 20, "UTC")
    ledger = HttpWindowLedger(STORAGE, transport=_transport(
        lambda r: httpx.Response(200, json=_window_row(win))))
    with pytest.raises(ValueError, match="unknown window outcome"):
        ledger.close(win, "succeeded")


# ------------------------------------------------------------------ C12 profile

def _profile(home_tz: str) -> dict:
    return {"contract": "C12", "version": "0", "user_id": "u-p", "home_tz": home_tz,
            "profile_version": 3, "updated_at": "2026-07-20T04:00:00Z"}


def test_profile_read_supplies_home_tz():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json=_profile("America/Los_Angeles"))

    tz = HttpProfileClient(STORAGE, transport=_transport(handler)).home_tz("u-p")
    assert seen["path"] == "/users/u-p/profile"
    assert tz == "America/Los_Angeles"


def test_profile_404_means_not_schedulable_and_never_utc():
    """The D17 rule with teeth: no profile => no home_tz => the user is NOT
    SCHEDULABLE. There is no default timezone to fall back to, here or anywhere."""
    client = HttpProfileClient(STORAGE, transport=_transport(
        lambda r: httpx.Response(404, json={"detail": "no profile"})))
    with pytest.raises(UserNotSchedulable) as exc:
        client.home_tz("u-p")
    message = str(exc.value)
    assert "NOT SCHEDULABLE" in message
    assert "OPERATOR ACTION" in message
    assert "UTC" not in message   # no default offered, not even in the error


def test_profile_without_home_tz_is_also_not_schedulable():
    body = _profile("UTC")
    body.pop("home_tz")
    client = HttpProfileClient(STORAGE, transport=_transport(
        lambda r: httpx.Response(200, json=body)))
    with pytest.raises(UserNotSchedulable, match="no default timezone"):
        client.home_tz("u-p")


# ------------------------------------------------------------------ C13 registry

def test_http_registry_fetches_recipe_and_policy_from_separate_endpoints():
    """Two artifacts, two endpoints, two lifecycles — `recipe_id` enters the stage
    keys and `policy_id` must never."""
    from pathlib import Path

    from app.config import get_settings
    settings = get_settings()
    recipe_json = json.loads(
        (Path(settings.recipes_dir) / "consolidation-v1.0.json").read_text())
    policy_json = json.loads(
        (Path(settings.policies_dir) / "gate-policy-v1.1.json").read_text())
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.startswith("/recipes/"):
            return httpx.Response(200, json=recipe_json)
        return httpx.Response(200, json=policy_json)

    registry = HttpRecipeRegistry(STORAGE, transport=_transport(handler))
    recipe = registry.fetch_recipe("consolidation-v1.0")
    policy = registry.fetch_policy("gate-policy-v1.1")
    assert seen == ["/recipes/consolidation-v1.0", "/policies/gate-policy-v1.1"]
    assert recipe.recipe_id == "consolidation-v1.0"
    assert recipe.variants == 48 and recipe.replay_frac == 0.3
    assert policy.policy_id == "gate-policy-v1.1"
    # No user_id anywhere on this surface: a recipe is global and versioned.
    assert all("user" not in path for path in seen)


def test_http_registry_rejects_id_content_mismatch():
    """A registry that returned a differently-identified artifact would let a night
    mis-record what it trained under — the same guard the local backend makes."""
    from pathlib import Path

    from app.config import get_settings
    recipe_json = json.loads(
        (Path(get_settings().recipes_dir) / "consolidation-v1.0.json").read_text())
    registry = HttpRecipeRegistry(STORAGE, transport=_transport(
        lambda r: httpx.Response(200, json=recipe_json)))
    with pytest.raises(RecipeNotFound, match="disagree"):
        registry.fetch_recipe("some-other-id")


def test_http_registry_unknown_id_raises():
    registry = HttpRecipeRegistry(STORAGE, transport=_transport(
        lambda r: httpx.Response(404, json={"detail": "no such recipe"})))
    with pytest.raises(RecipeNotFound):
        registry.fetch_recipe("nope")
    with pytest.raises(RecipeNotFound):
        registry.fetch_policy("nope")


# ----------------------------------------------------------------- C14 reservoir

def _ledger_entry(window_id: str, sha: str = "a" * 64) -> dict:
    return {"user_id": "u-res", "window_id": window_id, "recipe_id": "test-recipe-v0",
            "sha": sha, "chars": 1234, "admitted_at": "2026-07-21T04:01:00Z"}


def test_http_reservoir_admit_posts_the_corpus_to_the_address():
    win = make_window("u-res", 20, "UTC")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"], seen["path"] = request.method, request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_ledger_entry(win.window_id))

    entry = HttpReservoirClient(STORAGE, transport=_transport(handler)).admit(
        "u-res", win.window_id, "test-recipe-v0", "amplified text")
    assert seen["method"] == "POST"
    assert seen["path"] == f"/reservoir/u-res/{win.window_id}"
    # sha/chars/admitted_at are storage-minted and deliberately not sent.
    assert seen["body"] == {"recipe_id": "test-recipe-v0", "corpus_text": "amplified text"}
    assert entry.sha == "a" * 64
    assert entry.path == ""   # the ledger answers "what", never "where on my disk"


def test_http_reservoir_ledger_read_filters_before_window():
    w18 = make_window("u-res", 18, "UTC").window_id
    w20 = make_window("u-res", 20, "UTC").window_id
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={
            "contract": "C14", "version": "0", "user_id": "u-res",
            "before_window": w20, "entries": [_ledger_entry(w18)]})

    entries = HttpReservoirClient(STORAGE, transport=_transport(handler)).entries(
        "u-res", before_window=w20)
    assert seen["path"] == "/reservoir/u-res"
    assert seen["params"] == {"before_window": w20}
    assert [e.window_id for e in entries] == [w18]
    assert entries[0].sha == "a" * 64   # what the replay-mix stage key hangs off


def test_http_reservoir_rejects_a_body_of_the_wrong_contract():
    client = HttpReservoirClient(STORAGE, transport=_transport(
        lambda r: httpx.Response(200, json={"contract": "C10", "version": "1",
                                            "entries": []})))
    with pytest.raises(ValueError, match="C14 v0"):
        client.entries("u-res")


def test_http_reservoir_rawlog_replay_reads_prior_daylogs_via_c10():
    """The locked architecture over the wire: replay pools the RAW prior day-logs,
    fetched through C10, and never touches the amplified store."""
    prior = make_window("u-res", 18, "UTC")
    body = _daylog_body(prior, blocks=[{
        "block_id": f"{prior.window_id}_b{i:04d}", "seg_ids": [],
        "text": "On 2026-07-18, around 08:00–08:02 local time:\nScene: " + "z" * 200 + str(i),
        "anchors": {"date": "2026-07-18", "place": None}, "quality": None}
        for i in range(8)])
    daylog_client = HttpDayLogClient(STORAGE, transport=_transport(
        lambda r: httpx.Response(200, json=body)))
    client = HttpReservoirClient(STORAGE, daylog_client=daylog_client,
                                 transport=_transport(
                                     lambda r: httpx.Response(500)))
    replay = client.sample_replay("u-res", target_chars=100_000, frac=0.5, seed=1,
                                  source="rawlog", prior_windows=[prior])
    assert replay
    day_text = "\n\n".join(b["text"] for b in body["blocks"])
    for para in replay.split("\n\n"):
        assert para in day_text


def _ledger_response(entries):
    return httpx.Response(200, json={"contract": "C14", "version": "0",
                                     "user_id": "u-res", "before_window": None,
                                     "entries": entries})


def test_http_reservoir_amp_replay_fails_loudly_because_c14_has_no_bodies():
    """C14 serves the LEDGER, not the corpora — that is the contract, not a gap.
    `amp` replay needs the bodies, so once there IS history to rehearse, over HTTP it
    must fail rather than silently replay nothing, which is exactly the collapse
    rehearsal exists to prevent."""
    w18 = make_window("u-res", 18, "UTC").window_id
    client = HttpReservoirClient(STORAGE, transport=_transport(
        lambda r: _ledger_response([_ledger_entry(w18)])))
    with pytest.raises(NotImplementedError, match="rawlog"):
        client.sample_replay("u-res", target_chars=100, frac=0.3, seed=1, source="amp")


def test_http_amp_replay_is_empty_not_fatal_when_there_is_nothing_to_replay():
    """A first night (empty reservoir) and the SEQ arm (frac 0) replay nothing on the
    local backend too. Blocking them would fail a night over the absence of something
    it was never going to use — the loud failure belongs only where rehearsal was
    actually owed."""
    client = HttpReservoirClient(STORAGE, transport=_transport(
        lambda r: _ledger_response([])))
    assert client.sample_replay("u-res", target_chars=100, frac=0.3, seed=1,
                                source="amp") == ""
    frac_zero = HttpReservoirClient(STORAGE, transport=_transport(
        lambda r: httpx.Response(500)))   # not even a ledger read is needed
    assert frac_zero.sample_replay("u-res", target_chars=100, frac=0.0, seed=1,
                                   source="amp") == ""


def test_http_reservoir_rawlog_needs_a_daylog_client():
    client = HttpReservoirClient(STORAGE, transport=_transport(
        lambda r: httpx.Response(500)))
    with pytest.raises(ValueError, match="day-log client"):
        client.sample_replay("u-res", target_chars=100, frac=0.3, seed=1,
                             source="rawlog",
                             prior_windows=[make_window("u-res", 18, "UTC")])


# --------------------------------------------------------------- settings wiring

def test_settings_select_the_http_backends(monkeypatch):
    from app.clients import (HttpDayLogClient as _Http, day_log_client,
                             recipe_registry, reservoir_client)
    from app.config import get_settings
    from app.recipe import Recipe

    recipe = Recipe(recipe_id="r", variants=1, neg_frac=0.0, ok_rate_min=0.0,
                    replay_frac=0.0, replay_source="rawlog", replay_neg_boost=0.0,
                    lora_r=8, lora_alpha=16, lr=1e-4, epochs=1, batch_size=1,
                    chunk_tokens=128, objective="next-token CPT (never QA-SFT)",
                    quality_min=0.5, block_segments=12, segment_seconds=10,
                    boundary_local_time="04:00")
    monkeypatch.setenv("CONTINUUM_STORAGE_CLIENTS", "http")
    settings = get_settings()
    assert isinstance(day_log_client(settings, recipe), _Http)
    assert isinstance(recipe_registry(settings), HttpRecipeRegistry)
    assert isinstance(reservoir_client(settings), HttpReservoirClient)


def test_http_is_the_default_so_the_nightly_runs_over_the_seam(monkeypatch):
    """REGRESSION (F2a). The shipped default must be the HTTP path.

    This test previously asserted the opposite — `storage_clients == "local"`, on the
    grounds that the local day-log path is the M9 parity reference. That reasoning
    confuses "not deleted" with "default": the parity reference is driven by the diff
    script and by tests that hand it records, never by a production night. The D18
    slice IS continuum talking to storage over HTTP, `scripts/seam_check.py` proves
    exactly that path against the real service, and the local default meanwhile
    routed the nightly through an event-time range read over an ingest-time window
    and trained on an EMPTY day-log. A default nobody exercises is wrong even when it
    works; this one did not work.
    """
    from app.clients import HttpDayLogClient as _Http
    from app.clients import day_log_client, recipe_registry, reservoir_client
    from app.config import get_settings
    from app.recipe import Recipe

    monkeypatch.delenv("CONTINUUM_STORAGE_CLIENTS", raising=False)
    settings = get_settings()
    assert settings.storage_clients == "http"
    recipe = Recipe(recipe_id="r", variants=1, neg_frac=0.0, ok_rate_min=0.0,
                    replay_frac=0.0, replay_source="amp", replay_neg_boost=0.0,
                    lora_r=8, lora_alpha=16, lr=1e-4, epochs=1, batch_size=1,
                    chunk_tokens=128, objective="next-token CPT (never QA-SFT)",
                    quality_min=0.5, block_segments=12, segment_seconds=10,
                    boundary_local_time="04:00")
    assert isinstance(day_log_client(settings, recipe), _Http)
    assert isinstance(recipe_registry(settings), HttpRecipeRegistry)
    assert isinstance(reservoir_client(settings), HttpReservoirClient)


def test_the_local_parity_reference_is_still_selectable(monkeypatch):
    """The other half of F2a: flipping the default does NOT retire the local path.

    It stays the parity reference storage's narrowed M9 diff is measured against, and
    it is reached the way that diff reaches it — with records in hand."""
    from app.clients import LocalRecipeRegistry, day_log_client, recipe_registry
    from app.config import get_settings
    from app.recipe import Recipe

    monkeypatch.setenv("CONTINUUM_STORAGE_CLIENTS", "local")
    settings = get_settings()
    recipe = Recipe(recipe_id="r", variants=1, neg_frac=0.0, ok_rate_min=0.0,
                    replay_frac=0.0, replay_source="amp", replay_neg_boost=0.0,
                    lora_r=8, lora_alpha=16, lr=1e-4, epochs=1, batch_size=1,
                    chunk_tokens=128, objective="next-token CPT (never QA-SFT)",
                    quality_min=0.5, block_segments=12, segment_seconds=10,
                    boundary_local_time="04:00")
    client = day_log_client(settings, recipe, record_provider=lambda w: [])
    assert isinstance(client, LocalDayLogClient)
    assert isinstance(recipe_registry(settings), LocalRecipeRegistry)


def test_local_without_records_refuses_instead_of_returning_an_empty_day(monkeypatch):
    """REGRESSION (F2b). `local` + no records in hand must REFUSE, loudly.

    The window is `[last_trained_t, now−δ)` on storage's INGEST axis;
    `GET /context/records?from=&to=` filters `t_start`, which is EVENT time. Wiring
    that read in as the local backend's default record provider produced an EMPTY
    day-log and a `skipped_no_data` night — a default configuration that trained on
    nothing and said nothing. Refusing is the requirement: silence is the one
    behaviour that is not allowed here.
    """
    from app.clients import IngestWindowNotReadable, day_log_client
    from app.config import get_settings
    from app.recipe import Recipe

    monkeypatch.setenv("CONTINUUM_STORAGE_CLIENTS", "local")
    recipe = Recipe(recipe_id="r", variants=1, neg_frac=0.0, ok_rate_min=0.0,
                    replay_frac=0.0, replay_source="amp", replay_neg_boost=0.0,
                    lora_r=8, lora_alpha=16, lr=1e-4, epochs=1, batch_size=1,
                    chunk_tokens=128, objective="next-token CPT (never QA-SFT)",
                    quality_min=0.5, block_segments=12, segment_seconds=10,
                    boundary_local_time="04:00")
    with pytest.raises(IngestWindowNotReadable) as exc:
        day_log_client(get_settings(), recipe)
    message = str(exc.value)
    # The message must name the axis mismatch and the way out, not just fail.
    assert "INGEST" in message and "EVENT" in message
    assert "CONTINUUM_STORAGE_CLIENTS=http" in message


def test_the_nightly_default_never_reads_context_records(monkeypatch):
    """REGRESSION (F2b), stated as the property rather than the wiring.

    Under the shipped default, a night must not touch `GET /context/records` at all —
    that endpoint answers an EVENT-time question and a training window is not one.
    Blow up if anything calls it, then build the default client and fetch: the C10
    day-log fetch is the only read that happens.
    """
    from app.clients import day_log_client
    from app.config import get_settings
    from app.recipe import Recipe

    def never(*a, **k):  # pragma: no cover - the assertion is that it is not reached
        raise AssertionError("the nightly path read /context/records — that is an "
                             "EVENT-time query and the window is INGEST-time")

    monkeypatch.delenv("CONTINUUM_STORAGE_CLIENTS", raising=False)
    monkeypatch.setattr("app.context_reader.fetch_window_records", never)
    recipe = Recipe(recipe_id="r", variants=1, neg_frac=0.0, ok_rate_min=0.0,
                    replay_frac=0.0, replay_source="amp", replay_neg_boost=0.0,
                    lora_r=8, lora_alpha=16, lr=1e-4, epochs=1, batch_size=1,
                    chunk_tokens=128, objective="next-token CPT (never QA-SFT)",
                    quality_min=0.5, block_segments=12, segment_seconds=10,
                    boundary_local_time="04:00")
    win = make_window("u-axis", 20, "UTC")
    paths: list[str] = []

    def handler(request):
        paths.append(request.url.path)
        # Stamped with the recipe this night is pinned to: the fetch now REFUSES a
        # body rendered under another one, and a refusal here would prove nothing
        # about which endpoints were read.
        return httpx.Response(200, json=_daylog_body(win, recipe_id=recipe.recipe_id))

    client = day_log_client(get_settings(), recipe)
    client.transport = _transport(handler)
    daylog = client.fetch_daylog(win)
    assert paths == ["/training/daylog"]
    assert len(daylog.blocks) == 1


def test_a_records_in_hand_provider_always_selects_the_local_backend(monkeypatch):
    """"I already have the records" is a question storage cannot be asked — a
    synthetic day, a test, a Phase-3 replay."""
    from app.clients import day_log_client
    from app.config import get_settings
    from app.recipe import Recipe

    monkeypatch.setenv("CONTINUUM_STORAGE_CLIENTS", "http")
    recipe = Recipe(recipe_id="r", variants=1, neg_frac=0.0, ok_rate_min=0.0,
                    replay_frac=0.0, replay_source="amp", replay_neg_boost=0.0,
                    lora_r=8, lora_alpha=16, lr=1e-4, epochs=1, batch_size=1,
                    chunk_tokens=128, objective="next-token CPT (never QA-SFT)",
                    quality_min=0.5, block_segments=12, segment_seconds=10,
                    boundary_local_time="04:00")
    client = day_log_client(get_settings(), recipe, record_provider=lambda w: [])
    assert isinstance(client, LocalDayLogClient)


def test_no_window_id_parsing_survives_anywhere_in_app():
    """Item 6 of the cutover, enforced mechanically over the AST (so prose about the
    deletion does not trip it): nothing under `app/` may index a `window_id`, reach
    for a `local_date`/`local_window_date`, or feed a window id to a date parser.

    `app/morpheus/` is included in the sweep and must stay clean — it is the
    research-parity kernel and has never known what a window is."""
    import ast
    from pathlib import Path

    app_dir = Path(__file__).resolve().parents[1] / "app"
    banned_attrs = {"local_date", "local_window_date"}
    parsers = {"fromisoformat", "strptime", "parse_ts"}
    offenders: list[str] = []

    def is_window_id(node: ast.AST) -> bool:
        """The id ITSELF, not an expression derived from it. `_h(user, window_id)[:8]`
        is hashing — allowed, and D18 names the resulting seed change explicitly —
        whereas `window_id[1:]` was reading a date out of it."""
        return ((isinstance(node, ast.Name) and node.id == "window_id")
                or (isinstance(node, ast.Attribute) and node.attr == "window_id"))

    def mentions_window_id(node: ast.AST) -> bool:
        return any(is_window_id(n) for n in ast.walk(node))

    for path in sorted(app_dir.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            where = f"{path.relative_to(app_dir)}:{getattr(node, 'lineno', '?')}"
            if isinstance(node, ast.Attribute) and node.attr in banned_attrs:
                offenders.append(f"{where}: .{node.attr}")
            elif isinstance(node, ast.Subscript) and is_window_id(node.value):
                offenders.append(f"{where}: window_id[...]")
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in parsers
                    and any(mentions_window_id(a) for a in node.args)):
                offenders.append(f"{where}: {node.func.attr}(window_id)")
    assert offenders == [], offenders


def test_cycle_never_mints_or_parses_a_window(var_dir, small_recipe, monkeypatch):
    """A window whose id encodes nothing at all still runs a night end to end —
    proof the cycle treats it as an opaque key."""
    from tests._helpers import consolidate

    win = Window(window_id="w20260721T110000Z", user_id="u-opaque", tz="UTC",
                 start_utc=datetime(2026, 7, 20, 11, tzinfo=timezone.utc),
                 end_utc=datetime(2026, 7, 21, 11, tzinfo=timezone.utc))
    result = consolidate(synth_records(win, seed=5, events=25), win,
                         recipe=small_recipe)
    assert result.status == "published"
    assert result.window_id == "w20260721T110000Z"
    assert (var_dir / "journal" / "u-opaque" / "w20260721T110000Z.json").exists()


def test_render_daylog_files_is_unused_arg_guard(tmp_path):
    """Guard against the renderer quietly gaining a dependency on window shape."""
    win = make_window("u-h", 20, "UTC")
    daylog = LocalDayLogClient.from_records(
        synth_records(win, seed=7, events=10)).fetch_daylog(win)
    paths = render_daylog_files(daylog, tmp_path)
    assert all(p for p in paths.values())
