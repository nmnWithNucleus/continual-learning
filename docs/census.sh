#!/usr/bin/env bash
# Old-world token census — the instrument behind every count in docs/purge_*.md.
#
# Usage:  docs/census.sh                     # the whole tree
#         docs/census.sh product/services/storage   # one subtree
#         docs/census.sh | wc -l             # the headline number
#
# It greps for the vocabulary of the retired data-processing world, so "the purge
# is done" is a number anyone can reproduce rather than a feeling.
#
# WHAT IT WALKS: `git ls-files`, i.e. exactly what the repository teaches. Working
# -tree noise (virtualenvs, __pycache__, .pytest_cache, model weights) is not in
# the index, so it needs no exclude list and costs no read — which matters, since
# this tree lives on NFS and a naive `grep -r .` takes minutes. Submodules are
# separate repositories and are not walked; `readings/` is imported source
# material rather than our prose, and is skipped for the same reason.
#
# TWO TOKEN CLASSES, because one regex flavour cannot serve both:
#
#   NARRATIVE tokens are CASE-SENSITIVE. Matching them case-insensitively turns
#   "Stage B" into every "stage b" in the tree and "WS-" into "ws-morpheus" —
#   that mistake produced 110 false positives on the first run of this census.
#
#   PROSE tokens (rebuild / cutover / ingest-time / v0 world / emission law) are
#   CASE-INSENSITIVE and accept a space, hyphen or underscore between the words,
#   because they open sentences and get title-cased in headings. One survived a
#   stricter regex as "Ingest time" — capitalised, space-separated — inside a
#   running contract's field description.
#
# It reads .md, .json and .py alike: a schema `description` teaches as loudly as
# a board does, and a code comment that explains today by yesterday teaches worse.
#
# EXEMPTIONS ARE DELIBERATELY NOT ENCODED HERE. Every surviving hit is adjudicated
# by hand and justified on its merits in the phase worklog. A script that swallowed
# its own exceptions would make the gate unfalsifiable.

set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

# Case-sensitive: names, identifiers, labelled phases.
NARRATIVE='Stage [A-G]\b|WP-[A-G][0-9]|WS-[A-Z]|VidProc|ProcessedUnit|SlotView'\
'|refactor_stage|refactor_dp_service|dp-rebuild-v1|OD-[123]\b|ingest_time'

# Case-insensitive, separator-agnostic: prose that gets title-cased and re-spaced.
PROSE='\brebuil[dt]|\bcutover|\bcut[ -]over|ingest[ _-]time'\
'|beside[ -]build|fresh[ -]forward|v0[ -]world|pre[ -]rebuild|pre[ -]cutover'\
'|emission[ -]law|record[ -]emission|migration drill|the rebuilt world'

PREFIX="${1:-}"

FILES=$(git ls-files -- "${PREFIX:-.}" | grep -v '^poc/' | grep -v '/readings/')
[ -z "$FILES" ] && exit 0

{
  printf '%s\n' "$FILES" | xargs -d '\n' grep -nE  "$NARRATIVE" -- 2>/dev/null
  printf '%s\n' "$FILES" | xargs -d '\n' grep -niE "$PROSE"     -- 2>/dev/null
} | grep -vE '^docs/(census\.sh|purge_)' | sort -u
