"""C13 — the recipe registry: versioned training recipes, and the gate policy beside them.

LIFTED from ``services/continuum/app/clients/registry.py`` (``LocalRecipeRegistry``), which
D18 names as the reference implementation. That class resolves an id to
``<dir>/<id>.json``; this module does the same thing behind two HTTP reads, which is the
whole point of the move — a run records the **id** it trained under, and the registry is
the one place that resolves an id to content.

TWO ARTIFACTS, TWO IDS, TWO LIFECYCLES — and that is the contract, not a filing decision:

  ``recipes/<recipe_id>.json``   WHAT WE TRAIN. ``recipe_id`` is hashed into continuum's
                                 amplify and train stage keys, so changing it correctly
                                 invalidates hours of GPU work.
  ``policies/<policy_id>.json``  WHETHER WE SHIP IT. ``policy_id`` is recorded in the gate
                                 report and MUST NOT enter a stage key.

Folding the thresholds back into the recipe would make re-deciding what is shippable
re-run a night of GPU time and imply the artifact had changed when only our willingness to
publish it did. Hence two directories, two schemas, two endpoints — never one artifact
with a "gate" block.

**``recipe_id`` == the filename stem, and it is GLOBAL AND VERSIONED — never per-user.**
There is no ``user_id`` anywhere in this module and there must never be one: a per-user
recipe id forks the id per user and destroys artifact comparability. (It is also why a
per-user ``boundary_local_time`` cannot ride C13 and belongs on the C12 profile.)

**Served VERBATIM.** The response body is the file's own JSON, unwrapped and unstripped —
so continuum's ``load_recipe``/``load_policy`` work on it unchanged when the local backend
is swapped for the HTTP one. Storage validates the artifact against the frozen C13 schema
before serving (a self-check on our own data, the same posture ``/model-directory/resolve``
takes with C6), but it does not reshape it.

**Why these files are a copy of continuum's, and why that is not drift waiting to happen:**
a recipe file is IMMUTABLE UNDER ITS ID — every knob change forks ``recipe_id``, so the
only legal edit is a new file with a new stem. Two copies of ``consolidation-v1.0.json``
can therefore never disagree without one of them being a contract violation, and the
mismatch check below catches the shape that violation takes. Until continuum cuts over,
its local registry stays authoritative for its own runs.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .ids import validate_path_id

# product/services/storage/app/registry.py -> parents[1] == the service root, so the
# artifact dirs sit beside app/ exactly as they do in continuum (recipes/, policies/).
_DEFAULT_RECIPES_DIR = Path(__file__).resolve().parents[1] / "recipes"
_DEFAULT_POLICIES_DIR = Path(__file__).resolve().parents[1] / "policies"

RECIPE_KIND = "recipe"
POLICY_KIND = "policy"


def recipes_dir() -> Path:
    """Directory holding the versioned recipe artifacts (env-overridable for tests/CI)."""
    return Path(os.environ.get("STORAGE_RECIPES_DIR", str(_DEFAULT_RECIPES_DIR)))


def policies_dir() -> Path:
    """Directory holding the versioned gate-policy artifacts (env-overridable)."""
    return Path(os.environ.get("STORAGE_POLICIES_DIR", str(_DEFAULT_POLICIES_DIR)))


class ArtifactMissing(Exception):
    """No artifact of that id in the registry; the route maps this to 404.

    The caller asked a well-formed question and the answer is "nothing is registered under
    that id" — register the artifact or fix the id.
    """

    def __init__(self, detail: dict[str, Any]) -> None:
        super().__init__(str(detail.get("error", "artifact missing")))
        self.detail = detail


class RegistryFault(Exception):
    """The registry's OWN CONTENTS are wrong; the route maps this to 500.

    Two cases, both "we are serving bad data" rather than "you asked badly":

      * the file does not parse as JSON;
      * the file's own ``recipe_id``/``policy_id`` disagrees with the id it is filed
        under. This one is load-bearing and is lifted straight from the reference: a
        registry that silently returned a differently-identified artifact would let a
        night mis-record what it trained under, and that lie is unfalsifiable afterwards.

    DELIBERATE DEVIATION FROM THE REFERENCE, recorded here because it is a real choice:
    ``LocalRecipeRegistry`` raises ``RecipeNotFound`` for the mismatch too. Over HTTP a 404
    would tell an operator to *register the artifact* — exactly the wrong advice when the
    file is sitting right there and merely misnamed. 500 says what is true: the registry's
    contents are inconsistent with themselves. The rejection is identical either way; only
    the diagnosis differs.
    """

    def __init__(self, detail: dict[str, Any]) -> None:
        super().__init__(str(detail.get("error", "registry fault")))
        self.detail = detail


def _resolve(base: Path, artifact_id: str, kind: str) -> Path:
    """``<base>/<artifact_id>.json``, or ``ArtifactMissing``.

    The id has already passed ``validate_path_id`` at the edge (a malformed id is a 422
    before it reaches here), so it cannot contain a separator and cannot escape ``base``.
    The reference folds that check into this function and returns the same error for both;
    this service splits them because it already distinguishes "malformed" (422) from
    "absent" (404) everywhere else — see the ``window_id`` gate on the close route.
    """
    path = base / f"{artifact_id}.json"
    if not path.is_file():
        raise ArtifactMissing(
            {
                "error": f"no such {kind}",
                f"{kind}_id": artifact_id,
                "reason": f"the registry resolves an id to {base.name}/<id>.json; "
                          f"register the artifact or fix the id",
            }
        )
    return path


def _load(path: Path, artifact_id: str, kind: str, id_field: str) -> dict[str, Any]:
    try:
        artifact = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise RegistryFault(
            {
                "error": f"unreadable {kind} artifact",
                f"{kind}_id": artifact_id,
                "reason": str(exc),
            }
        ) from exc
    if not isinstance(artifact, dict):
        raise RegistryFault(
            {"error": f"{kind} artifact is not an object", f"{kind}_id": artifact_id}
        )
    # The file's own id is authoritative and must match what was asked for.
    if artifact.get(id_field) != artifact_id:
        raise RegistryFault(
            {
                "error": "registry id and content id disagree",
                f"{kind}_id": artifact_id,
                f"content_{id_field}": artifact.get(id_field),
                "reason": f"{artifact_id!r} resolves to a file whose {id_field} is "
                          f"{artifact.get(id_field)!r}; serving it would let a run "
                          f"mis-record what it trained under",
            }
        )
    return artifact


def fetch_recipe(recipe_id: str) -> dict[str, Any]:
    """The pinned training recipe for this id/version, verbatim."""
    return _load(
        _resolve(recipes_dir(), recipe_id, RECIPE_KIND), recipe_id, RECIPE_KIND, "recipe_id"
    )


def fetch_policy(policy_id: str) -> dict[str, Any]:
    """The gate policy for this id/version, verbatim — a SEPARATE artifact from the recipe."""
    return _load(
        _resolve(policies_dir(), policy_id, POLICY_KIND), policy_id, POLICY_KIND, "policy_id"
    )


__all__ = [
    "ArtifactMissing",
    "RegistryFault",
    "fetch_policy",
    "fetch_recipe",
    "policies_dir",
    "recipes_dir",
    "validate_path_id",
]
