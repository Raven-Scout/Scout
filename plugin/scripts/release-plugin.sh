#!/usr/bin/env bash
# Cut a release.
#
# `main` is ruleset-protected (requires pull requests, no direct pushes), so a
# release lands via a PR — not a direct push. This script has two phases:
#
#   scripts/release-plugin.sh [patch|minor|major|X.Y.Z]   # PREPARE: bump on a release branch + open the release PR
#   scripts/release-plugin.sh --finalize plugin/vX.Y.Z    # FINALIZE (after the PR merges): tag the merge commit + push
#
# The pushed tag triggers .github/workflows/release-plugin.yml, which re-runs the
# test matrix and publishes the GitHub Release from the CHANGELOG section.
#
# Test gating: the PR's required CI runs the full pytest matrix in a clean
# environment. Locally we run only the fast, deterministic checks (ruff, mypy,
# version-sync) — the test suite has environment-dependent cases that are green
# in CI but can be noisy on a maintainer's machine with a live vault.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$ROOT/.." && pwd)"
PY="$ROOT/engine/.venv/bin/python"
cd "$ROOT"
[ -x "$PY" ] || { echo "error: engine venv missing — run scripts/install-venv.sh" >&2; exit 1; }

# ---------------------------------------------------------------- finalize ----
if [ "${1:-}" = "--finalize" ]; then
    TAG="${2:-}"
    [ -n "$TAG" ] || { echo "error: --finalize requires a tag, e.g. --finalize plugin/v0.12.0" >&2; exit 1; }
    case "$TAG" in
        plugin/v*) ;;
        *) echo "error: --finalize tag must start with plugin/v, e.g. --finalize plugin/v0.12.0" >&2; exit 1 ;;
    esac
    VER="${TAG#plugin/v}"
    git -C "$REPO_ROOT" fetch -q origin
    # The release PR must be merged first: origin/main's CHANGELOG carries this version.
    #
    # No pipeline here, deliberately. `git show … | grep -q` kills the writer with
    # SIGPIPE (141) the instant grep matches, and `pipefail` promotes that 141 to the
    # pipeline's status — so `!` inverted a *match* into the error branch and
    # --finalize could never succeed. Feeding grep from a herestring removes the pipe
    # (and the writer) entirely, so pipefail has nothing to promote.
    CHANGELOG_MAIN="$(git -C "$REPO_ROOT" show "origin/main:plugin/CHANGELOG.md" 2>/dev/null || true)"
    if ! grep -q "## \[$VER\]" <<<"$CHANGELOG_MAIN"; then
        echo "error: origin/main plugin/CHANGELOG.md has no [$VER] section — merge the release PR first" >&2
        exit 1
    fi
    if git -C "$REPO_ROOT" rev-parse "$TAG" >/dev/null 2>&1; then
        echo "error: tag $TAG already exists" >&2
        exit 1
    fi
    git -C "$REPO_ROOT" tag "$TAG" origin/main
    git -C "$REPO_ROOT" push origin "$TAG"
    echo "Tagged $TAG at origin/main and pushed. release-plugin.yml will publish the GitHub Release."
    exit 0
fi

# ----------------------------------------------------------------- prepare ----
LEVEL="${1:-patch}"
[ "$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD)" = "main" ] || { echo "error: prepare a release from main" >&2; exit 1; }
[ -z "$(git -C "$REPO_ROOT" status --porcelain)" ] || { echo "error: working tree not clean" >&2; exit 1; }
git -C "$REPO_ROOT" fetch -q origin
[ "$(git -C "$REPO_ROOT" rev-list --count origin/main..HEAD)" = "0" ] && [ "$(git -C "$REPO_ROOT" rev-list --count HEAD..origin/main)" = "0" ] \
    || { echo "error: local main not in sync with origin/main" >&2; exit 1; }

"$PY" -m scout.scripts.versioning check >/dev/null   # refuse if already drifted
NEW="$("$PY" -m scout.scripts.versioning bump "$LEVEL")"
BRANCH="release/v$NEW"
TODAY="$(TZ=America/New_York date '+%Y-%m-%d')"

git -C "$REPO_ROOT" checkout -b "$BRANCH"
"$PY" -m scout.scripts.versioning set "$NEW" >/dev/null
"$PY" - "$NEW" "$TODAY" <<'EOF'
import sys
from scout.scripts import versioning
versioning.promote_changelog(version=sys.argv[1], date=sys.argv[2])
EOF
echo "Prepared plugin/v$NEW on $BRANCH"

# Fast, deterministic local gate (the PR's CI runs the full pytest matrix).
( cd engine && .venv/bin/ruff check scout tests && .venv/bin/ruff format --check scout tests \
    && .venv/bin/mypy scout && .venv/bin/python -m scout.scripts.versioning check )

git -C "$REPO_ROOT" add \
        .claude-plugin/marketplace.json \
        plugin/.claude-plugin/plugin.json \
        plugin/engine/pyproject.toml \
        plugin/engine/scout/__init__.py \
        apps/macos/Scout.xcodeproj/project.pbxproj \
        plugin/CHANGELOG.md
git -C "$REPO_ROOT" commit -m "release: plugin/v$NEW"
git -C "$REPO_ROOT" push -u origin "$BRANCH"
gh pr create --base main --head "$BRANCH" --title "release: plugin/v$NEW" \
    --body "Automated release prep for plugin/v$NEW. Review, ensure CI is green, and merge — then run \`scripts/release-plugin.sh --finalize plugin/v$NEW\` to tag and publish the GitHub Release."
echo "Release PR opened for plugin/v$NEW. After it merges: scripts/release-plugin.sh --finalize plugin/v$NEW"
