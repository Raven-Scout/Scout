#!/usr/bin/env bash
# Build a Release Scout.app, sign it with Developer ID + hardened runtime,
# notarize and staple both the app and the DMG it ships in, then publish as a
# GitHub Release. A notarized + stapled app opens with a normal double-click,
# and a notarized DMG mounts without a Gatekeeper prompt — no right-click dance
# for downloaders.
#
# Release notes are auto-generated from `git log <prev-tag>..HEAD`, grouped
# by conventional-commit prefix (feat / fix / other), followed by the
# standard install + configure boilerplate.
#
# Usage:
#   scripts/release.sh            # auto-pick version from commits (see below)
#   scripts/release.sh 0.1.0      # explicit version (overrides the rule)
#
# Version rule: the next version is derived from the conventional-commit
# prefixes already used across the repo (and grouped in the changelog below).
# Any `feat:` commit since the latest v* tag ⇒ minor bump; otherwise
# (fix / perf / refactor / docs / chore / …) ⇒ patch bump. Pass an explicit
# version to override — e.g. a major/pre-1.0 bump; the script warns if the
# override disagrees with the rule but proceeds with what you passed.
#
# Requirements: xcodebuild, hdiutil, codesign, xcrun (notarytool + stapler),
# gh (logged in, with write access to the repo).
#
# Signing / notarization config (override via env if your setup differs):
#   SCOUT_SIGN_IDENTITY   codesign identity   (default: "Developer ID Application")
#   SCOUT_NOTARY_PROFILE  notarytool profile  (default: "scout-notary")
# The default identity substring resolves as long as exactly one "Developer ID
# Application" cert is in the keychain. Set the notary profile up once with:
#   xcrun notarytool store-credentials "scout-notary" \
#     --apple-id <you@email> --team-id <TEAMID> --password <app-specific-pw>
#
# Escape hatches (for local iteration):
#   SKIP_NOTARIZE=1  build + sign + DMG, but skip the Apple round-trips
#                    (neither app nor DMG is stapled — Gatekeeper will still block)
#   SKIP_RELEASE=1   do everything except tag/push/upload to GitHub

set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# Version selection (feat → minor, else → patch; explicit arg overrides)
# ─────────────────────────────────────────────────────────────────────────────
LATEST_TAG="$(git tag --list 'v*' --sort=-v:refname | head -1 || true)"

# Print the rule-recommended next version given the latest v*.*.* tag. Reads the
# commit subjects since that tag: a `feat:` (optionally scoped / breaking, e.g.
# `feat(kb):` or `feat!:`) bumps the minor and zeroes the patch; anything else
# bumps the patch. First-ever release (no tag) starts at 0.1.0.
recommend_version() {
  local latest="${1:-}"
  if [[ -z "$latest" ]]; then echo "0.1.0"; return; fi
  local base="${latest#v}"
  local maj="${base%%.*}"
  local rest="${base#*.}"
  local min="${rest%%.*}"
  local pat="${rest#*.}"
  local subjects
  subjects="$(git log "${latest}..HEAD" --no-merges --format='%s' 2>/dev/null || true)"
  if printf '%s\n' "$subjects" | grep -qE '^feat(\(.*\))?!?:'; then
    echo "${maj}.$((min + 1)).0"
  else
    echo "${maj}.${min}.$((pat + 1))"
  fi
}

RECOMMENDED="$(recommend_version "$LATEST_TAG")"

if [[ $# -ge 1 && -n "${1:-}" ]]; then
  VERSION="$1"
  if [[ "$VERSION" != "$RECOMMENDED" ]]; then
    echo "⚠ Version $VERSION overrides the rule-recommended $RECOMMENDED" >&2
    echo "  (feat→minor, else→patch, from commits since ${LATEST_TAG:-<none>})." >&2
  fi
else
  VERSION="$RECOMMENDED"
  echo "→ Auto-selected v$VERSION (feat→minor, else→patch) from commits since ${LATEST_TAG:-<none>}"
fi
TAG="v$VERSION"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_DIR="$REPO_ROOT/build"
RELEASE_DIR="$BUILD_DIR/release"
DMG="$RELEASE_DIR/Scout-$VERSION.dmg"

SIGN_IDENTITY="${SCOUT_SIGN_IDENTITY:-Developer ID Application}"
NOTARY_PROFILE="${SCOUT_NOTARY_PROFILE:-scout-notary}"

# Fail fast if the signing identity isn't in the keychain — otherwise we'd
# burn a full universal build before codesign errors out at the end.
if ! security find-identity -v -p codesigning | grep -q "$SIGN_IDENTITY"; then
  echo "✗ No codesigning identity matching \"$SIGN_IDENTITY\" found in keychain." >&2
  echo "  Create one in Xcode → Settings → Accounts → Manage Certificates →" >&2
  echo "  + → Developer ID Application, or set SCOUT_SIGN_IDENTITY to match." >&2
  exit 1
fi

# Submit $1 to Apple's notary service, wait for a verdict, then staple the
# ticket onto $2. stapler fails loudly on a rejected (Invalid) submission, so it
# doubles as the success gate. To inspect a rejection:
#   xcrun notarytool log <submission-id> --keychain-profile "$NOTARY_PROFILE"
notarize_and_staple() {
  xcrun notarytool submit "$1" --keychain-profile "$NOTARY_PROFILE" --wait
  xcrun stapler staple "$2"
}

echo "→ Cleaning previous build"
rm -rf "$BUILD_DIR"
mkdir -p "$RELEASE_DIR"

echo "→ Building Release configuration (universal, unsigned) at v$VERSION"
# Stamp MARKETING_VERSION + CURRENT_PROJECT_VERSION into Info.plist so the
# About panel and Settings → About both read the real release tag instead
# of relying on the xcodeproj development default. Build number is the commit count
# on HEAD — monotonic and reproducible without a state file.
#
# Build with signing disabled and sign explicitly below: the build step never
# reaches for a provisioning profile, and we control the exact Developer ID
# identity + hardened-runtime flags applied to the shipped artifact.
BUILD_NUMBER="$(git -C "$REPO_ROOT" rev-list --count HEAD)"
xcodebuild \
  -project "$REPO_ROOT/Scout.xcodeproj" \
  -scheme Scout \
  -configuration Release \
  -destination 'generic/platform=macOS' \
  -derivedDataPath "$BUILD_DIR" \
  MARKETING_VERSION="$VERSION" \
  CURRENT_PROJECT_VERSION="$BUILD_NUMBER" \
  CODE_SIGNING_REQUIRED=NO \
  CODE_SIGNING_ALLOWED=NO \
  ONLY_ACTIVE_ARCH=NO \
  ARCHS="arm64 x86_64" \
  clean build >/dev/null

APP="$BUILD_DIR/Build/Products/Release/Scout.app"
if [[ ! -d "$APP" ]]; then
  echo "✗ Scout.app not found at $APP" >&2
  exit 1
fi

echo "→ Signing Scout.app with Developer ID + hardened runtime"
# --options runtime enables the hardened runtime (required for notarization);
# --timestamp embeds a secure timestamp so the signature stays valid past the
# signing cert's expiry. The bundle has no nested frameworks/helpers, so one
# signature on the .app is sufficient (no --deep, which Apple now discourages).
codesign --force --options runtime --timestamp --sign "$SIGN_IDENTITY" "$APP"
codesign --verify --strict --verbose=2 "$APP"

if [[ "${SKIP_NOTARIZE:-0}" == "1" ]]; then
  echo "→ SKIP_NOTARIZE=1 set; skipping notarization (app + DMG will NOT be stapled)."
else
  echo "→ Notarizing Scout.app (profile: $NOTARY_PROFILE) — this can take a few minutes"
  # notarytool wants an archive, not a raw .app — zip the bundle preserving its
  # top-level dir. Stapling the app means it launches offline even after a user
  # copies it out of the DMG into /Applications.
  ZIP="$BUILD_DIR/Scout-$VERSION-notarize.zip"
  ditto -c -k --keepParent "$APP" "$ZIP"
  notarize_and_staple "$ZIP" "$APP"
  # Confirm Gatekeeper will accept the stapled app offline.
  spctl --assess --type execute --verbose=2 "$APP"
fi

echo "→ Packaging as DMG"
# Stage directory with the app and a symlink to /Applications so the DMG
# window shows a drag-and-drop install layout.
STAGE="$BUILD_DIR/dmg-stage"
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"

hdiutil create \
  -volname "Scout $VERSION" \
  -srcfolder "$STAGE" \
  -ov \
  -format UDZO \
  "$DMG" >/dev/null

echo "→ DMG ready: $DMG"
ls -lh "$DMG" | awk '{print "  size:", $5}'

if [[ "${SKIP_NOTARIZE:-0}" != "1" ]]; then
  echo "→ Signing + notarizing the DMG so it also mounts without a Gatekeeper prompt"
  # Sign the container itself (timestamp, but no hardened runtime — that flag is
  # for executables, not disk images), then notarize + staple the DMG so the
  # download is verifiable offline and skips the "downloaded from the Internet"
  # mount prompt.
  codesign --force --timestamp --sign "$SIGN_IDENTITY" "$DMG"
  notarize_and_staple "$DMG" "$DMG"
  # Disk images assess as type "open" (not "execute"); the primary-signature
  # context is required for non-app artifacts.
  spctl --assess --type open --context context:primary-signature --verbose=2 "$DMG"
fi

if [[ "${SKIP_RELEASE:-0}" == "1" ]]; then
  echo "→ SKIP_RELEASE=1 set; not tagging or uploading."
  exit 0
fi

# ─────────────────────────────────────────────────────────────────────────────
# Release notes
# ─────────────────────────────────────────────────────────────────────────────
# Find the most recent existing v*.*.* tag (excluding the one we're about to
# create) by sorting tags by semver and taking the highest. `sort:-v:refname`
# orders descending so head -1 is the latest.
PREV_TAG="$(git tag --list 'v*' --sort=-v:refname | grep -vx "$TAG" | head -1 || true)"

# Derive `owner/repo` from origin so we can build a github.com/.../compare/ link.
ORIGIN_URL="$(git config --get remote.origin.url || true)"
REPO_SLUG=""
case "$ORIGIN_URL" in
  https://github.com/*)
    REPO_SLUG="${ORIGIN_URL#https://github.com/}"
    REPO_SLUG="${REPO_SLUG%.git}"
    ;;
  git@github.com:*)
    REPO_SLUG="${ORIGIN_URL#git@github.com:}"
    REPO_SLUG="${REPO_SLUG%.git}"
    ;;
esac

# Build the changelog body in a tempfile so we can pass it via --notes-file.
NOTES="$BUILD_DIR/release-notes.md"
{
  if [[ -n "$PREV_TAG" ]]; then
    # Grab subject + short hash for every commit between PREV_TAG and HEAD.
    # %s = subject only (skips body / Co-Authored-By trailers); %h = short hash.
    COMMITS="$(git log "$PREV_TAG"..HEAD --no-merges --format='%s|%h')"
    if [[ -z "$COMMITS" ]]; then
      echo "## What's changed"
      echo
      echo "_No commits between \`$PREV_TAG\` and \`$TAG\`._"
    else
      FEATS="$(printf '%s\n' "$COMMITS" | grep -E '^feat(\(|:)' || true)"
      FIXES="$(printf '%s\n' "$COMMITS" | grep -E '^fix(\(|:)'  || true)"
      OTHER="$(printf '%s\n' "$COMMITS" | grep -vE '^(feat|fix)(\(|:)' || true)"

      echo "## What's changed"
      echo
      if [[ -n "$FEATS" ]]; then
        echo "### Features"
        echo
        printf '%s\n' "$FEATS" | awk -F'|' '{printf "- %s (`%s`)\n", $1, $2}'
        echo
      fi
      if [[ -n "$FIXES" ]]; then
        echo "### Fixes"
        echo
        printf '%s\n' "$FIXES" | awk -F'|' '{printf "- %s (`%s`)\n", $1, $2}'
        echo
      fi
      if [[ -n "$OTHER" ]]; then
        echo "### Other changes"
        echo
        printf '%s\n' "$OTHER" | awk -F'|' '{printf "- %s (`%s`)\n", $1, $2}'
        echo
      fi
    fi
    if [[ -n "$REPO_SLUG" ]]; then
      echo "**Full changelog**: https://github.com/$REPO_SLUG/compare/$PREV_TAG...$TAG"
      echo
    fi
  else
    echo "## What's changed"
    echo
    echo "_First tagged release._"
    echo
  fi

  echo "---"
  echo
  echo "## Install"
  echo
  echo "1. Download \`Scout-$VERSION.dmg\` from the Assets below."
  echo "2. Open the DMG and drag **Scout.app** into the **Applications** folder."
  echo "3. Launch it. Scout is signed with a Developer ID and notarized by Apple, so it opens with a normal double-click."
  echo
  echo "## Configure"
  echo
  echo "Open the app, press ⌘, to open Settings. Fill in your Linear workspace and author name so deep-links and comment authorship work correctly."
  echo
  echo "The app expects a Scout instance at \`~/Scout\`. Install the [scout-plugin](https://github.com/jordanrburger/scout-plugin) into Claude Code and run \`/scout-setup\` first if you don't have one yet."
} > "$NOTES"

echo "→ Tagging $TAG and creating GitHub release"
if git rev-parse "$TAG" >/dev/null 2>&1; then
  echo "  tag $TAG already exists locally — skipping tag/push"
else
  git tag -a "$TAG" -m "Release $VERSION"
  git push origin "$TAG"
fi

gh release create "$TAG" "$DMG" \
  --title "Scout $VERSION" \
  --notes-file "$NOTES"

echo "✓ Released $TAG"
