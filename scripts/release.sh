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
    grep -q "^## \[$v\]" "$wt/plugin/CHANGELOG.md" || die "plugin/CHANGELOG.md at $sha has no [$v] section"
    grep -q "^## \[$v\]" "$wt/apps/macos/CHANGELOG.md" || die "apps/macos/CHANGELOG.md at $sha has no [$v] section"
  fi

  idents="$(security find-identity -v -p codesigning)"
  case "$idents" in *"$ident"*) ;; *) die "no codesigning identity matching \"$ident\" in the keychain" ;; esac

  build="$wt/apps/macos/build"
  count="$(git -C "$REPO_ROOT" rev-list --count "$sha")"
  echo "→ Building Scout $v (build $count) from $sha"
  xcodebuild -project "$wt/apps/macos/Scout.xcodeproj" -scheme Scout -configuration Release \
    -destination 'generic/platform=macOS' -derivedDataPath "$build" \
    MARKETING_VERSION="$v" CURRENT_PROJECT_VERSION="$count" SCOUT_PLUGIN_FLOOR="$v" \
    CODE_SIGNING_REQUIRED=NO CODE_SIGNING_ALLOWED=NO ONLY_ACTIVE_ARCH=NO ARCHS="arm64 x86_64" \
    clean build >/dev/null
  app="$build/Build/Products/Release/Scout.app"
  [ -d "$app" ] || die "Scout.app not found at $app"
  check_bundled_engine "$app" "$v" "$kind"

  # Sparkle hook (contract with #318): active when the commit being built ships it.
  hook="$wt/apps/macos/scripts/sparkle-release.sh"
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

  notes="$build/release-notes.md"
  prev="$("$PY" -m scout.scripts.versioning --repo-root "$REPO_ROOT" previous-release --ref "$sha" --exclude "$tag")"
  if [ "$kind" = release ]; then
    "$PY" -m scout.scripts.release_notes --repo-root "$wt" "$v" --repo "$slug" --out "$notes" \
      ${prev:+--prev "${prev%% *}"}
  else
    "$PY" -m scout.scripts.release_notes --repo-root "$wt" "$v" --repo "$slug" --out "$notes" --rc "$tag"
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
    latest="$(gh api "repos/$slug/releases/latest" --jq .tag_name)"
    [ "$latest" = "$tag" ] || die "published $tag, but /releases/latest is $latest"
  else
    gh release create "$tag" "$@" --repo "$slug" --target "$sha" --title "Scout ${tag#v}" --notes-file "$notes" \
      --prerelease --latest=false
  fi
  git -C "$REPO_ROOT" worktree remove --force "$wt"
  echo "✓ Published $tag"
}

cmd_finalize() {
  local tag="${1:-}" v slug sha
  slug="$(require_slug)"
  case "$tag" in v[0-9]*.[0-9]*.[0-9]*) ;; *) die "finalize needs vX.Y.Z, got '$tag'" ;; esac
  case "$tag" in *-*) die "finalize is for releases; use 'rc' for $tag" ;; esac
  v="${tag#v}"
  git -C "$REPO_ROOT" fetch -q --tags origin
  ! git -C "$REPO_ROOT" rev-parse -q --verify "refs/tags/$tag" >/dev/null || die "tag $tag already exists locally"
  [ -z "$(git -C "$REPO_ROOT" ls-remote --tags origin "refs/tags/$tag")" ] || die "tag $tag already exists on origin"
  sha="$(git -C "$REPO_ROOT" rev-parse origin/main)"
  build_and_publish "$tag" "$v" "$sha" "$slug" release
}

cmd_rc() {
  local tag="${1:-}" v slug sha
  slug="$(require_slug)"
  case "$tag" in v[0-9]*.[0-9]*.[0-9]*-rc.[0-9]*) ;; *) die "rc needs vX.Y.Z-rc.N, got '$tag'" ;; esac
  v="${tag#v}"
  v="${v%%-rc.*}"
  [ -z "$(git -C "$REPO_ROOT" status --porcelain)" ] || die "working tree not clean"
  git -C "$REPO_ROOT" fetch -q --tags origin
  ! git -C "$REPO_ROOT" rev-parse -q --verify "refs/tags/$tag" >/dev/null || die "tag $tag already exists locally"
  [ -z "$(git -C "$REPO_ROOT" ls-remote --tags origin "refs/tags/$tag")" ] || die "tag $tag already exists on origin"
  sha="$(git -C "$REPO_ROOT" rev-parse HEAD)"
  [ -n "$(git -C "$REPO_ROOT" branch -r --contains "$sha")" ] || die "HEAD $sha is not on any pushed branch; push it first"
  build_and_publish "$tag" "$v" "$sha" "$slug" rc
}

[ -x "$PY" ] || die "no Python at $PY. Build the engine venv first: bash plugin/scripts/install-venv.sh"

case "${1:-}" in
  prepare) shift; cmd_prepare "$@" ;;
  finalize) shift; cmd_finalize "$@" ;;
  rc) shift; cmd_rc "$@" ;;
  *) echo "usage: scripts/release.sh {prepare [patch|minor|major|X.Y.Z] | finalize vX.Y.Z | rc vX.Y.Z-rc.N}" >&2; exit 2 ;;
esac
