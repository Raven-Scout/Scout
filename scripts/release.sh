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
#   SKIP_RELEASE=1     prepare: commit locally, no push, no PR. finalize/rc: stop before publishing; rc may then
#                      run on the local, unpushed release branch.
#   SKIP_NOTARIZE=1    finalize/rc: sign and package, but skip Apple's notary service.
#   SCOUT_SIGN_IDENTITY (default "Developer ID Application"), SCOUT_NOTARY_PROFILE (default "scout-notary")
#   SCOUT_PY           Python with the engine importable (default: the dev venv plugin/engine/.venv/bin/python,
#                      then plugin/.venv/bin/python). prepare's lint uses the ruff and mypy beside it.
#   SCOUT_REPO_SLUG    tests only: skip deriving owner/repo from origin
#   SCOUT_RELEASE_SKIP_LINT=1  tests only: skip ruff/mypy/shellcheck in prepare
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXPECTED_SLUG="Raven-Scout/Scout"
DEV_VENV_CMD="(cd plugin/engine && uv venv --python 3.12 && uv pip install -e '.[dev]')"

die() { echo "error: $*" >&2; exit 1; }
vers() { "$PY" -m scout.scripts.versioning --repo-root "$REPO_ROOT" "$@"; }

# SCOUT_PY if set, else the dev venv (it has the lint tools), else the plugin's runtime venv.
resolve_py() {
  local candidate
  if [ -n "${SCOUT_PY:-}" ]; then
    [ -x "$SCOUT_PY" ] || die "SCOUT_PY=$SCOUT_PY is not an executable Python"
    echo "$SCOUT_PY"
    return 0
  fi
  for candidate in "$REPO_ROOT/plugin/engine/.venv/bin/python" "$REPO_ROOT/plugin/.venv/bin/python"; do
    if [ -x "$candidate" ]; then
      echo "$candidate"
      return 0
    fi
  done
  die "no Python at $REPO_ROOT/plugin/engine/.venv/bin/python or $REPO_ROOT/plugin/.venv/bin/python." \
    "Build the dev venv: $DEV_VENV_CMD"
}

# prepare's fast local checks, with the tools beside $PY. They don't depend on the bump, so they run on main.
run_lint() {
  local bin tool
  bin="$(dirname "$PY")"
  for tool in ruff mypy; do
    [ -x "$bin/$tool" ] || die "no $tool at $bin/$tool; prepare's lint needs the dev venv: $DEV_VENV_CMD"
  done
  ( cd "$REPO_ROOT/plugin/engine" && "$bin/ruff" check scout tests && "$bin/ruff" format --check scout tests \
      && "$bin/mypy" scout )
  shellcheck -S error "$REPO_ROOT/scripts/release.sh"
}

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
  local level="${1:-}" slug prev since new branch rel today notes body
  slug="$(require_slug)"
  require_clean_synced_main
  vers check >/dev/null || die "versions drift across the manifests; run: versioning check"
  prev="$(vers previous-release)"
  if [ -z "$level" ]; then
    since=""
    [ -z "$prev" ] || since="${prev#* }"
    # shellcheck disable=SC2086
    level="$(vers recommend ${since:+--since "$since"})"
    echo "→ $level (feat→minor, else→patch) since ${prev%% *}"
  fi
  new="$(vers next "$level")"
  branch="release/v$new"
  ! git -C "$REPO_ROOT" rev-parse -q --verify "refs/tags/v$new" >/dev/null || die "tag v$new already exists"
  if git -C "$REPO_ROOT" rev-parse -q --verify "refs/heads/$branch" >/dev/null \
    || [ -n "$(git -C "$REPO_ROOT" ls-remote --heads origin "refs/heads/$branch")" ]; then
    die "$branch already exists (locally or on origin). Delete it or finish that release first."
  fi
  for rel in plugin/CHANGELOG.md apps/macos/CHANGELOG.md; do
    if grep -q "^## \[$new\]" "$REPO_ROOT/$rel"; then die "$rel already has a [$new] section"; fi
  done
  [ "${SCOUT_RELEASE_SKIP_LINT:-0}" = 1 ] || run_lint
  git -C "$REPO_ROOT" checkout -q -b "$branch"
  vers set "$new" >/dev/null
  today="$(date '+%Y-%m-%d')"
  vers promote "$new" "$today"
  [ "$(vers check)" = "$new" ] || die "versioning check does not report $new after set"
  git -C "$REPO_ROOT" add .claude-plugin/marketplace.json plugin/.claude-plugin/plugin.json \
    plugin/engine/pyproject.toml plugin/engine/scout/__init__.py \
    apps/macos/Scout.xcodeproj/project.pbxproj plugin/CHANGELOG.md apps/macos/CHANGELOG.md
  git -C "$REPO_ROOT" commit -q -m "release: v$new"
  mkdir -p "$REPO_ROOT/.release"
  notes="$REPO_ROOT/.release/notes-v$new.md"
  "$PY" -m scout.scripts.release_notes --repo-root "$REPO_ROOT" "$new" --repo "$slug" --out "$notes" \
    ${prev:+--prev "${prev%% *}"}
  if [ "${SKIP_RELEASE:-0}" = 1 ]; then
    echo "→ SKIP_RELEASE=1: committed 'release: v$new' on $branch locally. Not pushing, no PR."
    echo "→ Release notes: $notes"
    return 0
  fi
  body="$REPO_ROOT/.release/pr-body-v$new.md"
  {
    printf '%s\n\n---\n\n' "Release prep for Scout v$new. Once the four required checks pass, merge it with \
**Create a merge commit** (not squash), so the final build's number is above every release candidate's. \
Then run \`scripts/release.sh finalize v$new\`."
    cat "$notes"
  } >"$body"
  git -C "$REPO_ROOT" push -q -u origin "$branch"
  gh pr create --repo "$slug" --base main --head "$branch" --title "release: v$new" --body-file "$body"
  echo "→ Release PR opened. After it merges with a merge commit: scripts/release.sh finalize v$new"
}

check_bundled_engine() {
  local app="$1" v="$2" kind="$3" pin got
  pin="$app/Contents/Resources/engine-release.json"
  if [ ! -f "$pin" ]; then
    if [ "${SKIP_RELEASE:-0}" = 1 ] || [ "$kind" = rc ]; then
      echo "⚠ no bundled engine in Scout.app (Contents/Resources/engine-release.json): fine for a dry run or an rc"
      return 0
    fi
    die "Scout.app has no bundled engine (Contents/Resources/engine-release.json). A release needs Part C's bundling."
  fi
  got="$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$pin")"
  [ "$got" = "$v" ] || die "bundled engine is $got, the app is $v"
}

# build_and_publish TAG APP_VERSION SHA SLUG KIND   (KIND: release | rc)
build_and_publish() {
  local tag="$1" v="$2" sha="$3" slug="$4" kind="$5"
  local wt="$REPO_ROOT/.release/$tag" ident profile idents build app count dmg_name dmg stage notes prev latest at hook
  [ "${SKIP_NOTARIZE:-0}" != 1 ] || [ "${SKIP_RELEASE:-0}" = 1 ] \
    || die "SKIP_NOTARIZE=1 would publish an app Gatekeeper blocks; add SKIP_RELEASE=1 for a dry run"
  ident="${SCOUT_SIGN_IDENTITY:-Developer ID Application}"
  profile="${SCOUT_NOTARY_PROFILE:-scout-notary}"

  if [ -e "$wt" ]; then
    git -C "$REPO_ROOT" worktree remove --force "$wt" 2>/dev/null || rm -rf "$wt"
  fi
  git -C "$REPO_ROOT" worktree prune
  mkdir -p "$REPO_ROOT/.release"
  git -C "$REPO_ROOT" worktree add -q --detach "$wt" "$sha"

  at="$("$PY" -m scout.scripts.versioning --repo-root "$wt" check)" || die "versions drift across the manifests at $sha"
  if [ "$kind" = release ]; then
    [ "$at" = "$v" ] || die "$sha carries version $at, not $v. Merge the release PR first."
  else
    [ "$at" = "$v" ] \
      || die "$sha carries version $at, not $v. Cut release candidates from release/v$v (run scripts/release.sh prepare first)."
  fi
  grep -q "^## \[$v\]" "$wt/plugin/CHANGELOG.md" || die "plugin/CHANGELOG.md at $sha has no [$v] section"
  grep -q "^## \[$v\]" "$wt/apps/macos/CHANGELOG.md" || die "apps/macos/CHANGELOG.md at $sha has no [$v] section"

  # Sparkle hook (contract with #318): active when the commit being built ships it.
  hook="$wt/apps/macos/scripts/sparkle-release.sh"
  if [ -e "$hook" ] && [ ! -x "$hook" ]; then
    die "apps/macos/scripts/sparkle-release.sh exists but is not executable at $sha;" \
      "a release without the hook would ship without appcast.xml"
  fi

  # Notes first: a changelog problem should cost no build. At the worktree root, which already exists.
  notes="$wt/release-notes.md"
  if [ "$kind" = release ]; then
    prev="$("$PY" -m scout.scripts.versioning --repo-root "$REPO_ROOT" previous-release \
      --ref "$sha" --exclude "$tag")"
    "$PY" -m scout.scripts.release_notes --repo-root "$wt" "$v" --repo "$slug" --out "$notes" \
      ${prev:+--prev "${prev%% *}"}
  else
    "$PY" -m scout.scripts.release_notes --repo-root "$wt" "$v" --repo "$slug" --out "$notes" --rc "$tag"
  fi

  idents="$(security find-identity -v -p codesigning)"
  case "$idents" in *"$ident"*) ;; *) die "no codesigning identity matching \"$ident\" in the keychain" ;; esac

  build="$wt/apps/macos/build"
  count="$(git -C "$REPO_ROOT" rev-list --count "$sha")"
  echo "→ Building Scout $v (build $count) from $sha"
  if ! xcodebuild -project "$wt/apps/macos/Scout.xcodeproj" -scheme Scout -configuration Release \
    -destination 'generic/platform=macOS' -derivedDataPath "$build" \
    MARKETING_VERSION="$v" CURRENT_PROJECT_VERSION="$count" SCOUT_PLUGIN_FLOOR="$v" \
    CODE_SIGNING_REQUIRED=NO CODE_SIGNING_ALLOWED=NO ONLY_ACTIVE_ARCH=NO ARCHS="arm64 x86_64" \
    clean build >"$wt/xcodebuild.log" 2>&1; then
    tail -n 40 "$wt/xcodebuild.log" >&2
    die "xcodebuild failed (full log: $wt/xcodebuild.log)"
  fi
  app="$build/Build/Products/Release/Scout.app"
  [ -d "$app" ] || die "Scout.app not found at $app"
  check_bundled_engine "$app" "$v" "$kind"

  export SPARKLE_BIN="$build/SourcePackages/artifacts/sparkle/Sparkle/bin"
  if [ -x "$hook" ]; then
    "$hook" preflight "$app"
    "$hook" sign "$app" "$ident"     # inside-out: Sparkle's XPC services, Autoupdate and Updater.app first
  else
    codesign --force --options runtime --timestamp --sign "$ident" "$app"
  fi
  codesign --verify --strict --verbose=2 "$app"
  if [ "${SKIP_NOTARIZE:-0}" != 1 ]; then
    ditto -c -k --keepParent "$app" "$build/Scout-notarize.zip"
    xcrun notarytool submit "$build/Scout-notarize.zip" --keychain-profile "$profile" --wait
    xcrun stapler staple "$app"
    spctl --assess --type execute --verbose=2 "$app"
  fi

  dmg_name="Scout-${tag#v}.dmg"
  dmg="$build/release/$dmg_name"
  stage="$build/dmg-stage"
  rm -rf "$stage"
  mkdir -p "$stage" "$build/release"
  cp -R "$app" "$stage/"
  ln -s /Applications "$stage/Applications"
  hdiutil create -volname "Scout ${tag#v}" -srcfolder "$stage" -ov -format UDZO "$dmg" >/dev/null
  codesign --force --timestamp --sign "$ident" "$dmg"
  if [ "${SKIP_NOTARIZE:-0}" != 1 ]; then
    xcrun notarytool submit "$dmg" --keychain-profile "$profile" --wait
    xcrun stapler staple "$dmg"
    spctl --assess --type open --context context:primary-signature --verbose=2 "$dmg"
  fi

  if [ -x "$hook" ]; then
    "$hook" appcast "$dmg" "$tag" "$slug" "$notes" "$build/appcast.xml"
    if [ ! -f "$build/appcast.xml" ]; then
      if [ "$kind" = release ] && [ "${SKIP_RELEASE:-0}" != 1 ]; then
        die "no appcast.xml after sparkle-release.sh appcast: a Latest release without it 404s every installed app's update feed"
      fi
      echo "⚠ no appcast.xml: fine for an rc or a dry run"
    fi
  fi

  set -- "$dmg"
  [ ! -f "$build/appcast.xml" ] || set -- "$@" "$build/appcast.xml"

  if [ "${SKIP_RELEASE:-0}" = 1 ]; then
    echo "→ SKIP_RELEASE=1: built $dmg (notes in $notes). Not publishing."
    return 0
  fi
  if [ "$kind" = release ]; then
    gh release create "$tag" "$@" --repo "$slug" --target "$sha" --title "Scout $v" --notes-file "$notes" --latest
    echo "✓ Published $tag"
    latest="$(gh api "repos/$slug/releases/latest" --jq .tag_name)"
    [ "$latest" = "$tag" ] \
      || die "published $tag, but /releases/latest is $latest; fix it on GitHub (the release is live)"
  else
    gh release create "$tag" "$@" --repo "$slug" --target "$sha" --title "Scout ${tag#v}" --notes-file "$notes" \
      --prerelease --latest=false
    echo "✓ Published $tag"
  fi
  git -C "$REPO_ROOT" worktree remove --force "$wt" \
    || echo "⚠ $tag is published, but its build worktree stays at $wt: git worktree remove --force $wt" >&2
}

# Strict, bash-3.2-safe tag-shape regexes (no glob over-acceptance: no trailing junk, no leading zeros, no "-rc.0").
RELEASE_TAG_RE='^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'
RC_TAG_RE='^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)-rc\.[1-9][0-9]*$'

# The commit that released v$1 on origin/main's first-parent history: the release PR's merge commit, its squash,
# or the release commit itself (rebase or fast-forward). Prints nothing when there is none.
release_commit() {
  local v="$1" log line
  log="$(git -C "$REPO_ROOT" log --first-parent --format='%H %s' origin/main)"
  while IFS= read -r line; do
    case "${line#* }" in
      "Merge pull request #"*" from "*"/release/v$v" | "release: v$v (#"*")" | "release: v$v")
        echo "${line%% *}"
        return 0
        ;;
    esac
  done <<<"$log"
}

cmd_finalize() {
  local tag="${1:-}" v slug sha after pj tip
  slug="$(require_slug)"
  if [[ $tag =~ $RC_TAG_RE ]]; then
    die "finalize is for releases; use 'rc' for $tag"
  elif [[ ! $tag =~ $RELEASE_TAG_RE ]]; then
    die "finalize needs vX.Y.Z, got '$tag'"
  fi
  v="${tag#v}"
  git -C "$REPO_ROOT" fetch -q --tags origin
  ! git -C "$REPO_ROOT" rev-parse -q --verify "refs/tags/$tag" >/dev/null || die "tag $tag already exists locally"
  [ -z "$(git -C "$REPO_ROOT" ls-remote --tags origin "refs/tags/$tag")" ] || die "tag $tag already exists on origin"
  # Build the release commit, not origin/main's tip: anything merged after it is not part of $tag.
  sha="$(release_commit "$v")"
  [ -n "$sha" ] || die "no release commit for v$v on origin/main. Merge the release PR first."
  # A reverted release PR leaves its commit in main's history, so also require main to still carry v.
  pj="$(git -C "$REPO_ROOT" show origin/main:plugin/.claude-plugin/plugin.json)" \
    || die "cannot read plugin/.claude-plugin/plugin.json at origin/main"
  tip="$("$PY" -c 'import json,sys; print(json.load(sys.stdin)["version"])' <<<"$pj")"
  [ "$tip" = "$v" ] || die "origin/main now carries version $tip, not $v (was the release PR reverted, or a later release merged?). Not publishing."
  after="$(git -C "$REPO_ROOT" rev-list --count "$sha..origin/main")"
  [ "$after" = 0 ] || echo "note: $after commit(s) on origin/main after the release commit are not in $tag"
  build_and_publish "$tag" "$v" "$sha" "$slug" release
}

cmd_rc() {
  local tag="${1:-}" v slug sha
  slug="$(require_slug)"
  [[ $tag =~ $RC_TAG_RE ]] || die "rc needs vX.Y.Z-rc.N, got '$tag'"
  v="${tag#v}"
  v="${v%%-rc.*}"
  [ -z "$(git -C "$REPO_ROOT" status --porcelain)" ] || die "working tree not clean"
  git -C "$REPO_ROOT" fetch -q --prune --tags origin
  ! git -C "$REPO_ROOT" rev-parse -q --verify "refs/tags/$tag" >/dev/null || die "tag $tag already exists locally"
  [ -z "$(git -C "$REPO_ROOT" ls-remote --tags origin "refs/tags/$tag")" ] || die "tag $tag already exists on origin"
  sha="$(git -C "$REPO_ROOT" rev-parse HEAD)"
  # A dry run (SKIP_RELEASE=1) publishes nothing, so it may run on the local, unpushed release branch.
  if [ "${SKIP_RELEASE:-0}" != 1 ]; then
    [ -n "$(git -C "$REPO_ROOT" branch -r --list 'origin/*' --contains "$sha")" ] \
      || die "HEAD $sha is not on any pushed branch; push it first"
  fi
  build_and_publish "$tag" "$v" "$sha" "$slug" rc
}

PY="$(resolve_py)"

case "${1:-}" in
  prepare) shift; cmd_prepare "$@" ;;
  finalize) shift; cmd_finalize "$@" ;;
  rc) shift; cmd_rc "$@" ;;
  *) echo "usage: scripts/release.sh {prepare [patch|minor|major|X.Y.Z] | finalize vX.Y.Z | rc vX.Y.Z-rc.N}" >&2; exit 2 ;;
esac
