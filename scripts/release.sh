#!/usr/bin/env bash
# Release Scout: one version, one tag, one GitHub release.
# Spec: docs/superpowers/specs/2026-10-05-unified-release-design.md
#
#   scripts/release.sh prepare [patch|minor|major|X.Y.Z]  # bump everything on release/vX.Y.Z, open the release PR
#   scripts/release.sh finalize vX.Y.Z                    # after the PR merges: build, sign, notarize, publish
#   scripts/release.sh rc vX.Y.Z-rc.N                     # release candidate from HEAD: a pre-release, never Latest
#
# LIVE: finalize and rc sign with the Developer ID, submit to Apple and publish to GitHub.
# Agents never run them for real unless Jordan asks for that specific run.
#
# Environment:
#   SKIP_RELEASE=1     prepare: commit locally, no push, no PR. finalize/rc: stop before publishing.
#   SKIP_NOTARIZE=1    finalize/rc: sign and package, but skip Apple's notary service.
#   SCOUT_SIGN_IDENTITY (default "Developer ID Application"), SCOUT_NOTARY_PROFILE (default "scout-notary")
#   SCOUT_PY           Python with the engine importable (default plugin/engine/.venv/bin/python)
#   SCOUT_REPO_SLUG    tests only: skip deriving owner/repo from origin
#   SCOUT_RELEASE_SKIP_LINT=1  tests only: skip ruff/mypy/shellcheck in prepare
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${SCOUT_PY:-$REPO_ROOT/plugin/engine/.venv/bin/python}"
EXPECTED_SLUG="Raven-Scout/Scout"

die() { echo "error: $*" >&2; exit 1; }
vers() { "$PY" -m scout.scripts.versioning --repo-root "$REPO_ROOT" "$@"; }

repo_slug() {
  if [ -n "${SCOUT_REPO_SLUG:-}" ]; then echo "$SCOUT_REPO_SLUG"; return 0; fi
  local url
  url="$(git -C "$REPO_ROOT" config --get remote.origin.url || true)"
  case "$url" in
    https://github.com/*) url="${url#https://github.com/}" ;;
    git@github.com:*) url="${url#git@github.com:}" ;;
    *) echo "${url:-<no origin>}"; return 0 ;;
  esac
  echo "${url%.git}"
}

# Prints the slug; dies unless it is Raven-Scout/Scout (case-insensitive) or SCOUT_REPO_SLUG is set.
require_slug() {
  local slug lower expected
  slug="$(repo_slug)"
  lower="$(printf '%s' "$slug" | tr '[:upper:]' '[:lower:]')"
  expected="$(printf '%s' "$EXPECTED_SLUG" | tr '[:upper:]' '[:lower:]')"
  if [ -z "${SCOUT_REPO_SLUG:-}" ] && [ "$lower" != "$expected" ]; then
    die "origin is $slug, not $EXPECTED_SLUG. Run this from a clone of $EXPECTED_SLUG."
  fi
  echo "$slug"
}

require_clean_synced_main() {
  [ "$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD)" = main ] || die "run from main"
  [ -z "$(git -C "$REPO_ROOT" status --porcelain)" ] || die "working tree not clean"
  git -C "$REPO_ROOT" fetch -q origin
  [ "$(git -C "$REPO_ROOT" rev-parse HEAD)" = "$(git -C "$REPO_ROOT" rev-parse origin/main)" ] \
    || die "local main is not in sync with origin/main"
}

cmd_prepare() {
  local level="${1:-}" slug prev since new branch today
  slug="$(require_slug)"
  require_clean_synced_main
  vers check >/dev/null || die "versions drift across the manifests; run: versioning check"
  if [ -z "$level" ]; then
    prev="$(vers previous-release)"
    since=""
    [ -z "$prev" ] || since="${prev#* }"
    # shellcheck disable=SC2086
    level="$(vers recommend ${since:+--since "$since"})"
    echo "→ $level (feat→minor, else→patch) since ${prev%% *}"
  fi
  new="$(vers next "$level")"
  branch="release/v$new"
  ! git -C "$REPO_ROOT" rev-parse -q --verify "refs/tags/v$new" >/dev/null || die "tag v$new already exists"
  git -C "$REPO_ROOT" checkout -q -b "$branch"
  vers set "$new" >/dev/null
  today="$(date '+%Y-%m-%d')"
  vers promote "$new" "$today"
  [ "$(vers check)" = "$new" ] || die "versioning check does not report $new after set"
  if [ "${SCOUT_RELEASE_SKIP_LINT:-0}" != 1 ]; then
    ( cd "$REPO_ROOT/plugin/engine" && .venv/bin/ruff check scout tests && .venv/bin/ruff format --check scout tests \
        && .venv/bin/mypy scout )
    shellcheck -S error "$REPO_ROOT/scripts/release.sh"
  fi
  git -C "$REPO_ROOT" add .claude-plugin/marketplace.json plugin/.claude-plugin/plugin.json \
    plugin/engine/pyproject.toml plugin/engine/scout/__init__.py \
    apps/macos/Scout.xcodeproj/project.pbxproj plugin/CHANGELOG.md apps/macos/CHANGELOG.md
  git -C "$REPO_ROOT" commit -q -m "release: v$new"
  if [ "${SKIP_RELEASE:-0}" = 1 ]; then
    echo "→ SKIP_RELEASE=1: committed 'release: v$new' on $branch locally. Not pushing, no PR."
    return 0
  fi
  git -C "$REPO_ROOT" push -q -u origin "$branch"
  gh pr create --repo "$slug" --base main --head "$branch" --title "release: v$new" \
    --body "Release prep for Scout v$new. Once the four required checks pass, squash-merge it, then run \`scripts/release.sh finalize v$new\`."
  echo "→ Release PR opened. After it merges: scripts/release.sh finalize v$new"
}

cmd_finalize() { require_slug >/dev/null; die "finalize: not implemented yet"; }
cmd_rc() { require_slug >/dev/null; die "rc: not implemented yet"; }

[ -x "$PY" ] || die "no Python at $PY. Build the engine venv first: bash plugin/scripts/install-venv.sh"

case "${1:-}" in
  prepare) shift; cmd_prepare "$@" ;;
  finalize) shift; cmd_finalize "$@" ;;
  rc) shift; cmd_rc "$@" ;;
  *) echo "usage: scripts/release.sh {prepare [patch|minor|major|X.Y.Z] | finalize vX.Y.Z | rc vX.Y.Z-rc.N}" >&2; exit 2 ;;
esac
