"""Deterministic answer scorers.

These are METRIC DEFINITIONS, not implementation choices: every number the
consolidation line has ever reported was computed with exactly this token
normalization and exactly this trap-marker list. Changing one silently re-bases
the whole history, so treat them as a frozen contract — a better scorer is a NEW
scorer, reported alongside, never a redefinition of these.

`f1` and `contains` are cheap and ceiling-limited (barely any gold answer is
short enough to exact-match); the judged-recall metric in `judge.py` is what has
real resolution. These stay because they are free, offline, and un-flaky.
"""
from __future__ import annotations

import json
import string
from collections import defaultdict

# Refusal/correction phrasings. A trap probe asks about something that never
# happened; passing means the answer pushes back rather than confabulating.
TRAP_MARKERS = (
    "didn't", "did not", "never", "wasn't", "was not", "no record", "not part",
    "doesn't appear", "does not appear", "not visit", "only 35", "35 days",
    "no evidence", "i don't", "unknown", "not aware", "no information",
    "fictional", "fabricated", "made up", "there is no", "there was no",
    "isn't", "is not", "cannot find", "can't find", "not mention",
)

_STOPWORDS = frozenset({"a", "an", "the"})


def normalize(text: str) -> list[str]:
    """Lowercase, strip punctuation, drop bare articles -> comparable tokens."""
    lowered = (text or "").lower()
    depunctuated = "".join(c if c not in string.punctuation else " " for c in lowered)
    return [t for t in depunctuated.split() if t not in _STOPWORDS]


def f1(gold: str, response: str) -> float:
    """Multiset token-F1 between gold and response."""
    gold_tokens, response_tokens = normalize(gold), normalize(response)
    if not gold_tokens or not response_tokens:
        return 0.0
    remaining: defaultdict[str, int] = defaultdict(int)
    for token in gold_tokens:
        remaining[token] += 1
    common = 0
    for token in response_tokens:
        if remaining[token] > 0:
            remaining[token] -= 1
            common += 1
    if not common:
        return 0.0
    precision = common / len(response_tokens)
    recall = common / len(gold_tokens)
    return 2 * precision * recall / (precision + recall)


def contains(gold: str, response: str) -> float:
    """1.0 iff the normalized gold appears verbatim inside the normalized response."""
    return 1.0 if " ".join(normalize(gold)) in " ".join(normalize(response)) else 0.0


def trap_score(_gold: str, response: str) -> float:
    """1.0 iff the answer refuses or corrects the false premise."""
    lowered = (response or "").lower()
    return 1.0 if any(marker in lowered for marker in TRAP_MARKERS) else 0.0


def order_score(gold_json: str, response: str) -> float:
    """Pairwise-order accuracy over a city sequence, penalized by coverage.

    Used by the route suite: getting the itinerary's ORDER right is a different
    memory than getting any single day right, and a model that names two cities
    correctly out of ten should not score like one that names all ten."""
    cities = json.loads(gold_json)
    lowered = (response or "").lower()
    found = []
    for index, city in enumerate(cities):
        key = city.split(",")[0].split(" then ")[0].strip().lower()
        position = lowered.find(key)
        if position >= 0:
            found.append((index, position))
    if len(found) < 2:
        return 0.0
    correct = total = 0
    for a in range(len(found)):
        for b in range(a + 1, len(found)):
            total += 1
            if (found[a][0] < found[b][0]) == (found[a][1] < found[b][1]):
                correct += 1
    return (correct / total) * (len(found) / len(cities))


SCORERS = {"f1": f1, "contains": contains, "trap": trap_score, "order": order_score}
