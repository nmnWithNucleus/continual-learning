"""D18 / C13 — the recipe registry, and the split it exists to enforce.

TWO ARTIFACTS, TWO IDS, TWO LIFECYCLES. `recipe_id` is hashed into continuum's amplify and
train stage keys; `policy_id` is recorded in the gate report and must never enter one. So a
publish-threshold change must never fork `recipe_id` — which is why the gate policy is a
separate file, under a separate id, behind a separate endpoint, validated by a separate
schema. Several tests below exist only to make that separation impossible to un-do by
accident.

`recipe_id` == the filename stem, GLOBAL and VERSIONED — never per-user.

The reference implementation lifted here is continuum's `app/clients/registry.py`
(`LocalRecipeRegistry`); the load-bearing behaviour it carries is the id/content agreement
check, pinned below.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import registry, schemas
from app.ids import validate_path_id
from app.window_id import mint_window_id  # noqa: F401  (used via the id-gate test below)

SERVICE_ROOT = Path(__file__).resolve().parents[1]
RECIPES = SERVICE_ROOT / "recipes"
POLICIES = SERVICE_ROOT / "policies"

RECIPE_IDS = sorted(p.stem for p in RECIPES.glob("*.json"))
POLICY_IDS = sorted(p.stem for p in POLICIES.glob("*.json"))


# --- the artifacts on disk are real, and they are what gets served -----------------


def test_the_registry_ships_with_artifacts():
    """A registry with nothing in it would make every test below vacuously true."""
    assert "consolidation-v1.0" in RECIPE_IDS
    assert "gate-policy-v1.1" in POLICY_IDS


@pytest.mark.parametrize("recipe_id", RECIPE_IDS)
def test_every_recipe_on_disk_serves_and_validates(client, recipe_id):
    got = client.get(f"/recipes/{recipe_id}")
    assert got.status_code == 200
    assert schemas.validate_c13_recipe(got.json()) == []


@pytest.mark.parametrize("policy_id", POLICY_IDS)
def test_every_policy_on_disk_serves_and_validates(client, policy_id):
    got = client.get(f"/policies/{policy_id}")
    assert got.status_code == 200
    assert schemas.validate_c13_policy(got.json()) == []


@pytest.mark.parametrize("recipe_id", RECIPE_IDS)
def test_recipe_id_is_the_filename_stem(client, recipe_id):
    """The registry resolves an id to `<recipes_dir>/<id>.json` and the content agrees."""
    assert client.get(f"/recipes/{recipe_id}").json()["recipe_id"] == recipe_id


@pytest.mark.parametrize("policy_id", POLICY_IDS)
def test_policy_id_is_the_filename_stem(client, policy_id):
    assert client.get(f"/policies/{policy_id}").json()["policy_id"] == policy_id


@pytest.mark.parametrize("recipe_id", RECIPE_IDS)
def test_the_artifact_is_served_verbatim(client, recipe_id):
    """Byte-for-byte the file's own JSON — no wrapper, no strip, no reshape.

    This is what lets continuum's `load_recipe` read the HTTP response unchanged when the
    local backend is swapped for the hosted one, and it is why the endpoint returns a bare
    JSONResponse rather than declaring a strict response_model.
    """
    on_disk = json.loads((RECIPES / f"{recipe_id}.json").read_text())
    assert client.get(f"/recipes/{recipe_id}").json() == on_disk


def test_the_provenance_prose_survives_the_wire(client):
    """`source` and `note` are not decoration: they record why a number is what it is and
    whether artifacts stay comparable across a fork. A strict mirror would have stripped
    them, which is the reason C13's schemas allow additional properties."""
    body = client.get("/recipes/consolidation-test-1min-v1.0").json()
    assert body["source"].startswith("Phase-3 DP-dogfood TEST profile")
    assert "byte-identical to consolidation-v1.0" in body["note"]


def test_the_reference_loaders_field_list_survives_the_wire(client):
    """Model continuum's `load_recipe`/`load_policy` against the HTTP body. If this passes,
    swapping LocalRecipeRegistry for an HTTP one is a transport change and nothing else."""
    raw = client.get("/recipes/consolidation-v1.0").json()
    recipe = {
        "recipe_id": raw["recipe_id"],
        "variants": int(raw["amplify"]["variants"]),
        "neg_frac": float(raw["amplify"]["neg_frac"]),
        "ok_rate_min": float(raw["amplify"]["ok_rate_min"]),
        "replay_frac": float(raw["replay"]["frac"]),
        "replay_source": str(raw["replay"]["source"]),
        "replay_neg_boost": float(raw["replay"]["neg_boost"]),
        "lora_r": int(raw["train"]["lora_r"]),
        "lora_alpha": int(raw["train"]["lora_alpha"]),
        "lr": float(raw["train"]["lr"]),
        "epochs": int(raw["train"]["epochs"]),
        "batch_size": int(raw["train"]["batch_size"]),
        "chunk_tokens": int(raw["train"]["chunk_tokens"]),
        "objective": str(raw["train"]["objective"]),
        "quality_min": float(raw["corpus"]["quality_min"]),
        "block_segments": int(raw["corpus"]["block_segments"]),
        "segment_seconds": int(raw["corpus"]["segment_seconds"]),
        "boundary_local_time": str(raw["window"]["boundary_local_time"]),
    }
    assert recipe["recipe_id"] == "consolidation-v1.0"
    assert recipe["objective"] == "next-token CPT (never QA-SFT)"
    assert (recipe["variants"], recipe["neg_frac"]) == (48, 0.15)
    assert (recipe["lora_r"], recipe["lora_alpha"]) == (128, 256)
    assert (recipe["segment_seconds"], recipe["block_segments"]) == (10, 12)

    pol = client.get("/policies/gate-policy-v1.1").json()
    policy = {
        "policy_id": pol["policy_id"],
        "new_day_recall_min": float(pol["gate"]["new_day_recall_min"]),
        "traps_pass_min": float(pol["gate"]["traps_pass_min"]),
        "heldout_alpha": float(pol["gate"]["heldout"]["alpha"]),
        "heldout_backstop": float(pol["gate"]["heldout"]["backstop"]),
        "heldout_probes": int(pol["gate"]["heldout"]["probes"]),
        "min_probes": int(pol["gate"]["min_probes"]),
        "decay_retention_min": float(pol["gate"]["decay_retention_min"]),
        "consecutive_fail_freeze": int(pol["gate"]["consecutive_fail_freeze"]),
        "snapshot_retention": int(pol["publish"]["snapshot_retention"]),
    }
    assert policy["policy_id"] == "gate-policy-v1.1"
    assert policy["heldout_probes"] == 222 and policy["min_probes"] == 148


# --- the split: a threshold change must never fork recipe_id ----------------------


@pytest.mark.parametrize("recipe_id", RECIPE_IDS)
def test_no_recipe_carries_a_publish_threshold(client, recipe_id):
    """THE separation, made machine-checkable. If a `gate`/`publish` block ever reappears
    inside a recipe, editing a threshold forks `recipe_id`, invalidates the amplify and
    train caches, re-runs a night of GPU time, and implies the trained artifact changed
    when only our willingness to publish it did."""
    body = client.get(f"/recipes/{recipe_id}").json()
    assert "gate" not in body
    assert "publish" not in body
    assert "policy_id" not in body


def test_no_policy_carries_a_training_knob(client):
    """The mirror image: the gate policy must not smuggle a knob that would need to enter a
    stage key, or `policy_id` would silently become training-relevant."""
    body = client.get("/policies/gate-policy-v1.1").json()
    for training_block in ("amplify", "replay", "train", "corpus", "recipe_id"):
        assert training_block not in body


def test_a_recipe_is_not_a_policy_and_the_namespaces_do_not_cross(client):
    """Two directories, two id namespaces. Asking the recipe endpoint for a policy id is a
    404 — not a 200 with the wrong artifact."""
    assert client.get("/recipes/gate-policy-v1.1").status_code == 404
    assert client.get("/policies/consolidation-v1.0").status_code == 404


@pytest.mark.parametrize("recipe_id", RECIPE_IDS)
def test_a_recipe_is_never_per_user(client, recipe_id):
    """GLOBAL and versioned. There is no user_id on this surface and none in the artifact:
    a per-user recipe id forks the id per user and destroys artifact comparability. (It is
    also why a per-user boundary_local_time belongs on the C12 profile, not here.)"""
    body = client.get(f"/recipes/{recipe_id}").json()
    assert "user_id" not in body
    # A stray query param cannot change what is served — the artifact is one global thing.
    assert client.get(f"/recipes/{recipe_id}", params={"user_id": "u1"}).json() == body


# --- the id gate ------------------------------------------------------------------


@pytest.mark.parametrize("bad_id", [
    "a..b",            # traversal shape
    ".hidden",         # leading dot
    "with space",
    "sub\\dir",        # backslash separator
])
def test_a_malformed_recipe_id_is_a_422(client, bad_id):
    """422 = the id could never be a filesystem path component; 404 = well-formed but not
    registered. Keeping those apart is what tells an operator 'fix your id' from
    'register the artifact'."""
    got = client.get(f"/recipes/{bad_id}")
    assert got.status_code == 422, got.text


def test_an_unknown_but_well_formed_id_is_a_404(client):
    got = client.get("/recipes/consolidation-v9.9")
    assert got.status_code == 404
    assert got.json()["detail"]["recipe_id"] == "consolidation-v9.9"


def test_a_traversal_attempt_never_escapes_the_registry_dir(client, tmp_path, monkeypatch):
    """Plant a real JSON file outside the registry and try to reach it by id."""
    secret = tmp_path / "secret.json"
    secret.write_text(json.dumps({"recipe_id": "secret"}))
    monkeypatch.setenv("STORAGE_RECIPES_DIR", str(tmp_path / "recipes"))
    for attempt in ("../secret", "..%2Fsecret", "....//secret"):
        assert client.get(f"/recipes/{attempt}").status_code != 200


def test_the_id_gate_rejects_what_the_reference_rejects():
    """Continuum's LocalRecipeRegistry refuses an id containing '/', '\\' or '..'. Same
    rejections here, restated at the unit level because the HTTP router normalizes some of
    them before they ever reach the app."""
    assert not validate_path_id("a/b")
    assert not validate_path_id("a\\b")
    assert not validate_path_id("..")
    assert not validate_path_id("a..b")
    assert validate_path_id("consolidation-v1.0")
    assert validate_path_id("gate-policy-v1.1")
    # Two rejections that CANNOT be expressed over HTTP, so they are pinned only here: an
    # empty segment is a routing miss (404) before any handler runs, and httpx refuses to
    # build a URL containing a raw newline at all. The gate still has to reject them,
    # because it is also called on `recipe_id` arriving in a JSON body (the reservoir
    # admission), where both shapes travel perfectly well.
    assert not validate_path_id("")
    assert not validate_path_id("trailing\n")   # the `$`-vs-fullmatch trap


def test_every_minted_window_id_passes_the_wide_id_gate():
    """The two validators are different gates and this is the containment relation between
    them: the narrow one (`validate_window_id`) accepts only what our minter shaped, and
    everything it accepts is safe as a path component. The reservoir relies on this — it
    joins a window_id into a filename."""
    from datetime import datetime, timezone

    minted = mint_window_id(datetime(2026, 7, 21, 11, 0, 0, tzinfo=timezone.utc))
    assert minted == "w20260721T110000Z"
    assert validate_path_id(minted)


# --- the registry's own contents are wrong: 500, not 404 --------------------------


@pytest.fixture()
def synthetic_registry(tmp_path, monkeypatch):
    """A registry dir under test control, so a malformed artifact can be filed in it."""
    recipes = tmp_path / "recipes"
    policies = tmp_path / "policies"
    recipes.mkdir()
    policies.mkdir()
    monkeypatch.setenv("STORAGE_RECIPES_DIR", str(recipes))
    monkeypatch.setenv("STORAGE_POLICIES_DIR", str(policies))
    return recipes, policies


def _valid_recipe(recipe_id: str) -> dict:
    return {
        "recipe_id": recipe_id,
        "amplify": {"variants": 48, "neg_frac": 0.15, "ok_rate_min": 0.85},
        "replay": {"frac": 0.3, "source": "amp", "neg_boost": 0.0},
        "train": {"lora_r": 128, "lora_alpha": 256, "lr": 0.0001, "epochs": 3,
                  "batch_size": 2, "chunk_tokens": 1024,
                  "objective": "next-token CPT (never QA-SFT)"},
        "corpus": {"quality_min": 0.5, "block_segments": 12, "segment_seconds": 10},
        "window": {"boundary_local_time": "04:00"},
    }


def test_a_file_whose_content_id_disagrees_is_a_500(client, synthetic_registry):
    """THE check lifted from the reference. A registry that silently returned a
    differently-identified artifact would let a night mis-record what it trained under, and
    that lie is unfalsifiable afterwards.

    We deviate from the reference on the STATUS only: it raises RecipeNotFound (a 404),
    which over HTTP would tell an operator to register an artifact that is sitting right
    there under the wrong name. 500 says what is true — the registry's contents are
    inconsistent with themselves.
    """
    recipes, _ = synthetic_registry
    (recipes / "filed-as-this.json").write_text(json.dumps(_valid_recipe("but-says-this")))
    got = client.get("/recipes/filed-as-this")
    assert got.status_code == 500
    assert got.json()["detail"]["error"] == "registry id and content id disagree"
    assert got.json()["detail"]["content_recipe_id"] == "but-says-this"


def test_an_unparseable_artifact_is_a_500(client, synthetic_registry):
    recipes, _ = synthetic_registry
    (recipes / "torn.json").write_text('{"recipe_id": "torn", ')
    assert client.get("/recipes/torn").status_code == 500


def test_a_recipe_missing_a_required_knob_is_a_500(client, synthetic_registry):
    """The schema self-check has teeth: an artifact that would crash continuum's loader at
    2am fails here instead, at fetch, with the violation named."""
    recipes, _ = synthetic_registry
    broken = _valid_recipe("broken")
    del broken["replay"]["neg_boost"]
    (recipes / "broken.json").write_text(json.dumps(broken))
    got = client.get("/recipes/broken")
    assert got.status_code == 500
    assert got.json()["detail"]["violations"]


def test_an_unknown_replay_source_is_rejected_at_fetch(client, synthetic_registry):
    """`source` is a closed enum because continuum raises on anything else. Better to fail
    at fetch than after the amplify stage has already run."""
    recipes, _ = synthetic_registry
    bad = _valid_recipe("bad-source")
    bad["replay"]["source"] = "reservoir"
    (recipes / "bad-source.json").write_text(json.dumps(bad))
    assert client.get("/recipes/bad-source").status_code == 500


def test_a_qa_sft_objective_is_rejected(client, synthetic_registry):
    """The core invariant of the research line, pinned as a single-value enum: continued
    pre-training on the day's text, NEVER supervised QA fine-tuning."""
    recipes, _ = synthetic_registry
    bad = _valid_recipe("qa-sft")
    bad["train"]["objective"] = "QA-SFT"
    (recipes / "qa-sft.json").write_text(json.dumps(bad))
    assert client.get("/recipes/qa-sft").status_code == 500


def test_a_new_recipe_needs_no_code_change(client, synthetic_registry):
    """Versioned files on disk, resolved by id — registering v1.1 is a file, not a deploy."""
    recipes, _ = synthetic_registry
    (recipes / "consolidation-v1.1.json").write_text(json.dumps(_valid_recipe("consolidation-v1.1")))
    got = client.get("/recipes/consolidation-v1.1")
    assert got.status_code == 200
    assert got.json()["recipe_id"] == "consolidation-v1.1"


def test_the_registry_dirs_are_env_configurable(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_RECIPES_DIR", str(tmp_path / "r"))
    monkeypatch.setenv("STORAGE_POLICIES_DIR", str(tmp_path / "p"))
    assert registry.recipes_dir() == tmp_path / "r"
    assert registry.policies_dir() == tmp_path / "p"


# --- the lift is a copy, and a copy can be checked --------------------------------


def test_the_hosted_artifacts_match_continuums(client):
    """Storage's registry files are a copy of continuum's, and until continuum cuts over
    both exist. That is safe ONLY because a recipe file is IMMUTABLE UNDER ITS ID — every
    knob change forks `recipe_id`, so the only legal edit is a new file with a new stem.
    This test is what turns that argument into a check.

    Skipped rather than failed if continuum's tree is not beside us: a cross-service path
    is a test convenience, never a runtime dependency.
    """
    peer_recipes = SERVICE_ROOT.parent / "continuum" / "recipes"
    peer_policies = SERVICE_ROOT.parent / "continuum" / "policies"
    if not peer_recipes.is_dir():  # pragma: no cover - depends on checkout layout
        pytest.skip("continuum service tree not present")
    for peer_dir, mine in ((peer_recipes, RECIPES), (peer_policies, POLICIES)):
        for peer_file in sorted(peer_dir.glob("*.json")):
            local = mine / peer_file.name
            assert local.is_file(), f"{peer_file.name} is not hosted by the registry"
            assert local.read_bytes() == peer_file.read_bytes()
