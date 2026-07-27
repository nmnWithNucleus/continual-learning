"""D18 / C14 — the training reservoir: append-only custody of the amplified corpora.

Four properties are the contract, and each has tests here that fail loudly if it is
weakened:

  1. THE CORPUS LANDS FIRST, THE META SECOND — the meta is the COMMIT MARKER, so a torn
     admission is invisible to the ledger rather than half-visible. Order and invisibility
     are both pinned.
  2. APPEND-ONLY, content-hashed, keyed (user_id, window_id, recipe_id). Re-admitting
     identical content is a no-op that does not even rewrite the files; different content
     under a committed key is a 409, never an overwrite.
  3. `window_id` IS OPAQUE AND IS NEVER PARSED FOR A DATE. Continuum's
     `ReservoirEntry.local_window_date()` did exactly that and is being deleted for it; the
     same-local-date test below is what would catch a re-introduction.
  4. RETENTION IS KEEP-EVERYTHING. No sweeper, no expiry, ever — replay needs real past
     text forever, and deletion here is a privacy act reached through M5's cascade.

The ledger is provenance, NOT the corpora: replay re-reads prior day-logs via C10.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from app import reservoir as reservoir_mod
from app import schemas
from app.reservoir import Reservoir

W1 = "w20260721T110000Z"
W2 = "w20260722T110000Z"
W3 = "w20260723T110000Z"
RECIPE = "consolidation-v1.0"


@pytest.fixture()
def store_root(client) -> Reservoir:
    """The same reservoir root the `client` fixture just pointed the app at (declares
    `client` for ordering — the env var only exists because that fixture set it)."""
    return Reservoir(root=os.environ["STORAGE_RESERVOIR_DIR"])


def _admit(client, user_id, window_id, text, recipe_id=RECIPE):
    return client.post(
        f"/reservoir/{user_id}/{window_id}",
        json={"recipe_id": recipe_id, "corpus_text": text},
    )


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- admission --------------------------------------------------------------------


def test_admit_returns_the_ledger_entry(client):
    got = _admit(client, "u1", W1, "amplified text")
    assert got.status_code == 200
    entry = got.json()
    assert entry["user_id"] == "u1"
    assert entry["window_id"] == W1
    assert entry["recipe_id"] == RECIPE
    assert entry["sha"] == _sha("amplified text")
    assert entry["chars"] == len("amplified text")
    assert entry["admitted_at"].endswith("Z")


def test_the_corpus_body_lands_on_disk_verbatim(client, store_root):
    text = "para one\n\npara two with unicode — em dash, 日本語"
    _admit(client, "u1", W1, text)
    assert store_root.read_corpus("u1", W1, RECIPE) == text
    # The hash is over UTF-8 bytes, so a non-ASCII corpus must still round-trip its sha.
    assert client.get("/reservoir/u1").json()["entries"][0]["sha"] == _sha(text)


def test_the_ack_is_exactly_a_ledger_row(client):
    """Same shape both ways, so a caller can cache the ack and compare it to the ledger."""
    ack = _admit(client, "u1", W1, "x").json()
    assert client.get("/reservoir/u1").json()["entries"] == [ack]


def test_an_empty_corpus_is_admitted(client):
    """A zero-length corpus is a defect upstream, but an append-only audit store that
    refuses what it is given destroys the evidence of the defect."""
    got = _admit(client, "u1", W1, "")
    assert got.status_code == 200
    assert got.json()["chars"] == 0


# --- idempotency, and what is NOT idempotency -------------------------------------


def test_re_admitting_identical_content_is_a_no_op(client, store_root):
    """Byte-identical response, and — the stronger claim — NOTHING IS REWRITTEN.
    `admitted_at` records when the artifact landed, not when someone last asked about it,
    exactly as /context preserves `ingest_time` across a reprocess."""
    first = _admit(client, "u1", W1, "same text").json()
    meta = store_root.meta_path("u1", W1, RECIPE)
    corpus = store_root.corpus_path("u1", W1, RECIPE)
    before = (meta.stat().st_mtime_ns, corpus.stat().st_mtime_ns)

    second = _admit(client, "u1", W1, "same text")
    assert second.status_code == 200
    assert second.json() == first
    assert (meta.stat().st_mtime_ns, corpus.stat().st_mtime_ns) == before


def test_different_content_under_a_committed_key_is_a_409(client):
    _admit(client, "u1", W1, "the night that happened")
    clash = _admit(client, "u1", W1, "a different night")
    assert clash.status_code == 409
    detail = clash.json()["detail"]
    assert detail["admitted_sha"] == _sha("the night that happened")
    assert detail["offered_sha"] == _sha("a different night")


def test_a_refused_admission_does_not_touch_the_stored_corpus(client, store_root):
    """Append-only means the first body is still there afterwards — a conflict must not be
    a partial overwrite."""
    _admit(client, "u1", W1, "the night that happened")
    _admit(client, "u1", W1, "a different night")
    assert store_root.read_corpus("u1", W1, RECIPE) == "the night that happened"
    entries = client.get("/reservoir/u1").json()["entries"]
    assert len(entries) == 1
    assert entries[0]["sha"] == _sha("the night that happened")


def test_the_key_includes_recipe_id_so_a_re_run_does_not_overwrite(client):
    """One window legitimately yields two corpora when a night is re-run under a new
    recipe. Keyed (user_id, window_id, recipe_id), so both survive — under a pair key the
    second would silently destroy the first, which is exactly what an audit store may not
    do."""
    _admit(client, "u1", W1, "amplified under v1.0", recipe_id="consolidation-v1.0")
    second = _admit(client, "u1", W1, "amplified under v1.1", recipe_id="consolidation-v1.1")
    assert second.status_code == 200
    entries = client.get("/reservoir/u1").json()["entries"]
    assert [e["recipe_id"] for e in entries] == ["consolidation-v1.0", "consolidation-v1.1"]
    assert {e["window_id"] for e in entries} == {W1}


# --- rule 1: the corpus lands first, the meta second ------------------------------


def test_the_write_order_is_corpus_then_meta(client, store_root, monkeypatch):
    """Pinned structurally, not by comment. Reversing the order would publish a ledger row
    pointing at a body that may not exist."""
    seen: list[str] = []
    original = reservoir_mod._atomic_write_text

    def spy(path: Path, text: str) -> None:
        seen.append(path.name)
        original(path, text)

    monkeypatch.setattr(reservoir_mod, "_atomic_write_text", spy)
    _admit(client, "u1", W1, "text")
    assert seen == [f"{W1}__{RECIPE}.corpus.txt", f"{W1}__{RECIPE}.meta.json"]


def test_a_torn_admission_is_invisible_to_the_ledger(client, store_root, monkeypatch):
    """THE crash case. Interrupt the admission between the two writes: the corpus body is
    on disk, the meta never landed, and the ledger shows nothing — a torn admission is
    invisible rather than half-visible."""
    original = reservoir_mod._atomic_write_text

    def die_on_meta(path: Path, text: str) -> None:
        if path.name.endswith(".meta.json"):
            raise OSError("power loss between the two writes")
        original(path, text)

    monkeypatch.setattr(reservoir_mod, "_atomic_write_text", die_on_meta)
    with pytest.raises(OSError):
        _admit(client, "u1", W1, "orphaned corpus")

    # The torn state is real: the body exists, the commit marker does not.
    assert store_root.corpus_path("u1", W1, RECIPE).is_file()
    assert not store_root.meta_path("u1", W1, RECIPE).exists()
    # And the ledger cannot see it.
    assert client.get("/reservoir/u1").json()["entries"] == []


def test_a_torn_admission_is_repaired_by_the_retry(client, store_root, monkeypatch):
    """An uncommitted admission conflicts with nothing, so the retry just commits — even
    with different content, because the orphan never became a fact."""
    original = reservoir_mod._atomic_write_text

    def die_on_meta(path: Path, text: str) -> None:
        if path.name.endswith(".meta.json"):
            raise OSError("power loss")
        original(path, text)

    monkeypatch.setattr(reservoir_mod, "_atomic_write_text", die_on_meta)
    with pytest.raises(OSError):
        _admit(client, "u1", W1, "first attempt")
    monkeypatch.setattr(reservoir_mod, "_atomic_write_text", original)

    retried = _admit(client, "u1", W1, "second attempt")
    assert retried.status_code == 200
    assert retried.json()["sha"] == _sha("second attempt")
    assert store_root.read_corpus("u1", W1, RECIPE) == "second attempt"
    assert len(client.get("/reservoir/u1").json()["entries"]) == 1


def test_a_torn_meta_file_is_skipped_without_sinking_the_ledger(client, store_root):
    """One damaged file must not make a user's whole ledger unreadable on the night that
    matters — it is skipped (logged), and its neighbours still serve."""
    _admit(client, "u1", W1, "one")
    _admit(client, "u1", W2, "two")
    store_root.meta_path("u1", W1, RECIPE).write_text('{"user_id": "u1", ')

    entries = client.get("/reservoir/u1").json()["entries"]
    assert [e["window_id"] for e in entries] == [W2]


# --- the ledger -------------------------------------------------------------------


def test_the_ledger_is_a_valid_c14_body(client):
    _admit(client, "u1", W1, "text")
    body = client.get("/reservoir/u1").json()
    assert schemas.validate_c14(body) == []
    assert body["contract"] == "C14" and body["version"] == "0"
    assert body["user_id"] == "u1"
    assert body["before_window"] is None


def test_the_ledger_never_carries_the_corpus_bodies(client):
    """Provenance, not payload. The consumer wants to know which windows were consolidated
    under which recipe over which content hash; shipping megabytes of amplified text to
    answer that would be a different question badly answered."""
    secret = "the amplified corpus body that must not be on this read"
    _admit(client, "u1", W1, secret)
    raw = client.get("/reservoir/u1").text
    assert secret not in raw
    assert "corpus_text" not in raw
    # And no server-side path leaks either — that is a fact about our disk.
    assert "path" not in json.loads(raw)["entries"][0]


def test_an_empty_ledger_is_a_200_not_a_404(client):
    """First night: no history is a normal answer, not a missing resource."""
    got = client.get("/reservoir/never-admitted")
    assert got.status_code == 200
    assert got.json()["entries"] == []


def test_the_ledger_is_ordered_by_window_then_recipe(client):
    _admit(client, "u1", W3, "c")
    _admit(client, "u1", W1, "a")
    _admit(client, "u1", W2, "b")
    _admit(client, "u1", W1, "a-prime", recipe_id="consolidation-v1.1")
    entries = client.get("/reservoir/u1").json()["entries"]
    assert [(e["window_id"], e["recipe_id"]) for e in entries] == [
        (W1, "consolidation-v1.0"),
        (W1, "consolidation-v1.1"),
        (W2, "consolidation-v1.0"),
        (W3, "consolidation-v1.0"),
    ]


def test_the_ledger_is_per_user(client):
    """Hard namespace per user; cross-user access fails closed."""
    _admit(client, "u1", W1, "mine")
    _admit(client, "u2", W1, "theirs")
    assert [e["user_id"] for e in client.get("/reservoir/u1").json()["entries"]] == ["u1"]
    assert client.get("/reservoir/u1").text.find("theirs") == -1


# --- before_window: strictly before, and NEVER a date -----------------------------


def test_before_window_filters_strictly_before(client):
    """Its caller is replay: never replay the window being trained, or the future."""
    for window in (W1, W2, W3):
        _admit(client, "u1", window, f"corpus {window}")
    body = client.get("/reservoir/u1", params={"before_window": W2}).json()
    assert [e["window_id"] for e in body["entries"]] == [W1]
    assert body["before_window"] == W2


def test_before_window_is_echoed_back(client):
    body = client.get("/reservoir/u1", params={"before_window": W2}).json()
    assert body["before_window"] == W2
    assert schemas.validate_c14(body) == []


def test_two_windows_on_the_same_local_date_are_distinguished(client):
    """RULE 3, and the test that would catch a re-introduction of
    `ReservoirEntry.local_window_date()`. Both ids sit on 2026-07-21; a date-parsing filter
    sees one value and returns both or neither. A string comparison on the opaque id
    returns exactly the earlier one — which is the only operation a consumer may perform on
    a window_id."""
    morning, evening = "w20260721T060000Z", "w20260721T230000Z"
    _admit(client, "u1", morning, "before lunch")
    _admit(client, "u1", evening, "after dinner")
    body = client.get("/reservoir/u1", params={"before_window": evening}).json()
    assert [e["window_id"] for e in body["entries"]] == [morning]


def test_a_window_spanning_more_than_a_day_still_keys_cleanly(client):
    """Under `[last_trained_t, now-delta)` a window can span 23 h, 25 h or 47 h after a
    missed night, so there is no local date the id could honestly name. The reservoir keys
    on the id and never asks how wide the window was."""
    long_window = "w20260725T043000Z"   # ends 47 h after the previous one began
    _admit(client, "u1", W3, "wednesday")
    assert _admit(client, "u1", long_window, "two days at once").status_code == 200
    assert [e["window_id"] for e in client.get("/reservoir/u1").json()["entries"]] == [
        W3, long_window
    ]


def test_a_malformed_before_window_is_a_422(client):
    """A malformed filter does not fail, it silently returns the WRONG SET: '2026-07-21'
    sorts below every `w...` id and would empty the ledger, and continuum's replay would
    then quietly train on no history at all. So it is rejected at the edge."""
    _admit(client, "u1", W1, "text")
    for bad in ("2026-07-21", "w2026-07-21", "w-day5", "w20260721T1100Z"):
        got = client.get("/reservoir/u1", params={"before_window": bad})
        assert got.status_code == 422, bad


# --- the edge gates ---------------------------------------------------------------


@pytest.mark.parametrize("bad_window", ["w2026-07-21", "w-day5", "not-a-window", "w20260721T110000"])
def test_a_malformed_window_id_is_a_422(client, bad_window):
    """Reservoir entries are keyed by the id OUR minter shapes. An id this service never
    minted cannot name a window, so it is refused at the edge rather than filed."""
    assert _admit(client, "u1", bad_window, "text").status_code == 422


@pytest.mark.parametrize("bad_user", ["a..b", ".hidden", "with space"])
def test_a_malformed_user_id_is_a_422(client, bad_user):
    """`user_id` becomes a directory name — per-user isolation is a filesystem fact here."""
    assert _admit(client, bad_user, W1, "text").status_code == 422
    assert client.get(f"/reservoir/{bad_user}").status_code == 422


@pytest.mark.parametrize("bad_recipe", ["a..b", "sub/dir", ".hidden", "trailing\n"])
def test_a_malformed_recipe_id_is_a_422(client, bad_recipe):
    """`recipe_id` arrives in the BODY, where shapes the URL could never carry (a slash, a
    newline) travel perfectly well — and it becomes part of a filename."""
    assert _admit(client, "u1", W1, "text", recipe_id=bad_recipe).status_code == 422


@pytest.mark.parametrize("body", [
    {},                                                        # no recipe_id, no corpus
    {"recipe_id": RECIPE},                                     # no corpus_text
    {"corpus_text": "x"},                                      # no recipe_id
    {"recipe_id": RECIPE, "corpus_text": "x", "sha": "0" * 64},        # minted, not settable
    {"recipe_id": RECIPE, "corpus_text": "x", "chars": 99},            # minted
    {"recipe_id": RECIPE, "corpus_text": "x", "admitted_at": "2020-01-01T00:00:00Z"},
    {"recipe_id": RECIPE, "corpus_text": "x", "user_id": "someone-else"},  # address, not body
])
def test_a_malformed_admission_body_is_a_422(client, body):
    """`sha`/`chars`/`admitted_at` are storage-minted: a content hash the caller supplies is
    not a content hash, it is a claim. `user_id`/`window_id` are the ADDRESS — carrying them
    twice invites the two copies to disagree about whose corpus this is."""
    got = client.post(f"/reservoir/u1/{W1}", json=body)
    assert got.status_code == 422, got.text


def test_a_traversal_user_id_cannot_escape_the_reservoir_root(client, store_root):
    """A bare `..` never reaches the app (the HTTP client normalizes it away and the router
    404s), so the guarantee is pinned where it actually lives: the call that turns a
    user_id into a path refuses, rather than trusting the edge to have checked."""
    assert _admit(client, "a..b", W1, "text").status_code == 422
    with pytest.raises(ValueError):
        store_root.corpus_path("../../etc", W1, RECIPE)
    with pytest.raises(ValueError):
        store_root._user_dir("..")
    assert store_root.entries("../../etc") == []


# --- rule 4: keep everything ------------------------------------------------------


def test_nothing_is_ever_swept(client):
    """Retention is KEEP-EVERYTHING. Replay is what rescues sequential consolidation from
    collapse and generative replay demonstrably fails, so real past text is required
    forever. Admit many nights; every one is still in the ledger."""
    windows = [f"w202607{day:02d}T110000Z" for day in range(1, 29)]
    for window in windows:
        assert _admit(client, "u1", window, f"night {window}").status_code == 200
    entries = client.get("/reservoir/u1").json()["entries"]
    assert [e["window_id"] for e in entries] == windows


def test_no_sweeper_exists_in_the_module():
    """Structural: the brief is 'build no sweeper, no expiry', and the way that decays is
    someone adding a tidy-up helper 'just for dev'. Deletion in this store is a deliberate
    privacy act reached through M5's cascade — it will arrive as an explicit, audited
    primitive, not as housekeeping hidden in the write path.

    Read as an AST rather than as text, so the module's own prose ABOUT not sweeping does
    not trip its own check. `os.replace` is expected and allowed: that is the atomic
    rename, not a deletion.
    """
    import ast

    tree = ast.parse(Path(reservoir_mod.__file__).read_text())
    forbidden = {"unlink", "remove", "removedirs", "rmdir", "rmtree", "truncate"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            assert node.attr not in forbidden, f"{node.attr!r} called in the reservoir"
        if isinstance(node, ast.Name):
            assert node.id not in forbidden, f"{node.id!r} used in the reservoir"
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] + [getattr(node, "module", "") or ""]
            assert "shutil" not in names


# --- the store's own root ---------------------------------------------------------


def test_the_reservoir_root_is_env_configurable(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_RESERVOIR_DIR", str(tmp_path / "elsewhere"))
    assert reservoir_mod.reservoir_dir() == str(tmp_path / "elsewhere")
    assert Reservoir().root == tmp_path / "elsewhere"


def test_the_corpora_live_outside_the_context_store(client, store_root):
    """THE one invariant of the whole data design: amplified/synthetic text NEVER lands in
    /context. Same storage discipline, different namespace — so an admitted corpus must be
    absent from every /context read, and it is, because they are different stores."""
    _admit(client, "u1", W1, "synthetic amplified retelling")
    assert client.get("/context/records", params={"user_id": "u1"}).json() == []
    store = client.app.state.store
    with store._connect() as conn:
        tables = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
    # Not a table at all: the reservoir is a filesystem store beside the DB, and nothing in
    # the metadata DB shadows it (a second index would be a second source of truth about
    # what has been admitted, which is precisely what rule 1 forbids).
    assert not any("reservoir" in name for name in tables)
