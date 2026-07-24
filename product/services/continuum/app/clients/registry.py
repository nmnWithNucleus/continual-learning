"""Recipe-registry fetch — the pinned training recipe and gate policy, BY ID.

The lean loop's first verb is "fetch recipe". Storage will host a versioned
registry that both continuum (consolidation recipe) and inference (serving knobs)
pull from; continuum asks for a recipe/policy by id, not by reaching into a local
file path. That indirection is the point: a run records the *id* it trained under,
and the registry is the one place that resolves an id to content.

The recipe and the gate policy are fetched SEPARATELY and on purpose. `recipe_id`
enters the amplify/train stage keys (changing it re-trains); `policy_id` never
does (re-deciding what is shippable must not re-train). Two ids, two lifecycles —
see recipe.py / policy.py.

  LocalRecipeRegistry   resolves an id to `<recipes_dir>/<id>.json` (and policy to
                        `<policies_dir>/<id>.json`), i.e. the files already on disk.
  (future) HttpRecipeRegistry  GETs the versioned artifact from storage.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..policy import GatePolicy, load_policy
from ..recipe import Recipe, load_recipe


class RecipeNotFound(KeyError):
    """No recipe/policy of that id in the registry."""


class RecipeRegistry(Protocol):
    def fetch_recipe(self, recipe_id: str) -> Recipe:
        """The pinned training recipe for this id/version."""

    def fetch_policy(self, policy_id: str) -> GatePolicy:
        """The gate policy for this id/version — a SEPARATE artifact from the recipe."""


class LocalRecipeRegistry:
    """Local backend: the versioned files under recipes/ and policies/.

    Resolving by id (not by handing in a path) is what a storage-hosted registry
    will do; the local impl just maps the id to its file. An id with a path
    separator is rejected rather than allowed to escape the registry dir."""

    def __init__(self, *, recipes_dir: str | Path, policies_dir: str | Path):
        self.recipes_dir = Path(recipes_dir)
        self.policies_dir = Path(policies_dir)

    def _resolve(self, base: Path, artifact_id: str, kind: str) -> Path:
        if "/" in artifact_id or "\\" in artifact_id or ".." in artifact_id:
            raise RecipeNotFound(f"unsafe {kind} id {artifact_id!r}")
        path = base / f"{artifact_id}.json"
        if not path.is_file():
            raise RecipeNotFound(
                f"no {kind} {artifact_id!r} in {base} — the registry resolves an id to "
                f"{base.name}/<id>.json; register the artifact or fix the id")
        return path

    def fetch_recipe(self, recipe_id: str) -> Recipe:
        recipe = load_recipe(self._resolve(self.recipes_dir, recipe_id, "recipe"))
        # The file's own recipe_id is authoritative and must match what was asked
        # for — a registry that silently returns a differently-identified artifact
        # would let a night mis-record what it trained under.
        if recipe.recipe_id != recipe_id:
            raise RecipeNotFound(
                f"recipe {recipe_id!r} resolves to a file whose recipe_id is "
                f"{recipe.recipe_id!r} — registry id and content id disagree")
        return recipe

    def fetch_policy(self, policy_id: str) -> GatePolicy:
        policy = load_policy(self._resolve(self.policies_dir, policy_id, "policy"))
        if policy.policy_id != policy_id:
            raise RecipeNotFound(
                f"policy {policy_id!r} resolves to a file whose policy_id is "
                f"{policy.policy_id!r} — registry id and content id disagree")
        return policy
