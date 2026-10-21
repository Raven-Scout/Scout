#!/usr/bin/env bash
# version-bump-only.sh
#
# Decides whether a pull request is a pure release bump — the PR that
# `scripts/release.sh prepare` opens — so the heavy suites (plugin-test's
# pytest matrix, app-ci's ScoutTests) can skip it. A version bump can't change
# what those suites test; `versioning check` (plugin-lint, contract) is what
# guards it, and both still run. The push to main after the merge runs every
# suite in full anyway.
#
# Writes exactly one line, `version_only=true` or `version_only=false`, to
# $GITHUB_OUTPUT, and logs why to stdout.
#
# version_only=true iff ALL of:
#   - the event is pull_request (anything else -> false: push, workflow_call
#     and workflow_dispatch always run the suites);
#   - at least one file changed, and every changed file is one that
#     `release.sh prepare` writes (VERSION_FILES and CHANGELOGS below);
#   - in every VERSION_FILES file, every added or removed line is a version
#     line (VERSION_LINE below). A CHANGELOG may change freely — it's prose.
# So a release PR that also edits code, or that slips a dependency change into
# pyproject.toml, or a build setting into project.pbxproj, runs everything.
#
# A pull_request event with an empty BASE_SHA or HEAD_SHA, or a failing git
# diff, exits non-zero so the gate job fails loudly (same rule as
# changed-paths.sh).
#
# Env in:  GITHUB_EVENT_NAME (always), BASE_SHA, HEAD_SHA (pull_request only)
# Env out: GITHUB_OUTPUT

set -euo pipefail

VERSION_FILES=(
  .claude-plugin/marketplace.json
  agnes/.claude-plugin/marketplace.json
  plugin/.claude-plugin/plugin.json
  plugin/engine/pyproject.toml
  plugin/engine/scout/__init__.py
  apps/macos/Scout.xcodeproj/project.pbxproj
)
CHANGELOGS=(
  plugin/CHANGELOG.md
  apps/macos/CHANGELOG.md
)
# One of: "version": "X.Y.Z",   version = "X.Y.Z"   __version__ = "X.Y.Z"
#         MARKETING_VERSION = X.Y.Z;   (X.Y.Z may carry a -rc.N suffix)
SEMVER='[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?'
VERSION_LINE="^[-+][[:space:]]*(\"version\": \"${SEMVER}\",?|version = \"${SEMVER}\"|__version__ = \"${SEMVER}\"|MARKETING_VERSION = ${SEMVER};)[[:space:]]*\$"

say() {
  echo "version_only=$1 ($2)"
  echo "version_only=$1" >>"$GITHUB_OUTPUT"
  exit 0
}

if [ "${GITHUB_EVENT_NAME:-}" != "pull_request" ]; then
  say false "event '${GITHUB_EVENT_NAME:-<unset>}' is not pull_request"
fi

if [ -z "${BASE_SHA:-}" ] || [ -z "${HEAD_SHA:-}" ]; then
  echo "::error::BASE_SHA and HEAD_SHA must both be set for a pull_request event (BASE_SHA='${BASE_SHA:-}' HEAD_SHA='${HEAD_SHA:-}')" >&2
  exit 1
fi

if ! changed="$(git diff --name-only "${BASE_SHA}...${HEAD_SHA}")"; then
  echo "::error::git diff --name-only ${BASE_SHA}...${HEAD_SHA} failed" >&2
  exit 1
fi
[ -n "$changed" ] || say false "no changed files"

while IFS= read -r path; do
  kind=""
  for f in "${VERSION_FILES[@]}"; do [ "$path" = "$f" ] && kind=version; done
  for f in "${CHANGELOGS[@]}"; do [ "$path" = "$f" ] && kind=changelog; done
  case "$kind" in
    changelog) continue ;;
    version) ;;
    *) say false "$path is not a release-bump file" ;;
  esac
  if ! diff="$(git diff -U0 "${BASE_SHA}...${HEAD_SHA}" -- "$path")"; then
    echo "::error::git diff ${BASE_SHA}...${HEAD_SHA} -- $path failed" >&2
    exit 1
  fi
  # Only lines inside a hunk are content: the `--- a/…` / `+++ b/…` header
  # comes before the first `@@`, and a removed line can itself start `--`.
  in_hunk=0
  while IFS= read -r line; do
    case "$line" in
      "@@"*) in_hunk=1; continue ;;
      [-+]*) [ "$in_hunk" = 1 ] || continue ;;
      *) continue ;;
    esac
    if ! grep -Eq -- "$VERSION_LINE" <<<"$line"; then
      say false "$path changes a non-version line: $line"
    fi
  done <<<"$diff"
done <<<"$changed"

say true "every change is a version bump or a CHANGELOG edit"
