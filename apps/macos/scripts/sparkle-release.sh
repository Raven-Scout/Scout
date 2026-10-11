#!/bin/bash
# sparkle-release.sh: the Sparkle steps of a Scout.app release.
#
# The root scripts/release.sh (Raven-Scout/Scout#317) calls this at three fixed
# points in build_and_publish, with SPARKLE_BIN exported as
# "$build/SourcePackages/artifacts/sparkle/Sparkle/bin". It uses the hook only
# when this file is executable in the commit being built, so it must stay
# committed as mode 100755 (git ls-files -s apps/macos/scripts/sparkle-release.sh).
#
#   when                                 call                                        effect
#   -----------------------------------  ------------------------------------------  ------------------------------------------
#   after xcodebuild, before any         sparkle-release.sh preflight <app>          dies unless Sparkle.framework and its four
#   codesign (the app is unsigned)                                                   nested components exist, SUPublicEDKey ==
#                                                                                    `generate_keys -p`, SUFeedURL is the
#                                                                                    release-asset URL, and CFBundleVersion is a
#                                                                                    positive integer
#   replaces the flat app codesign       sparkle-release.sh sign <app> <identity>    signs inside-out: Installer.xpc,
#                                                                                    Downloader.xpc (entitlements kept),
#                                                                                    Autoupdate, Updater.app, Sparkle.framework,
#                                                                                    then the app; each --force --options
#                                                                                    runtime --timestamp, never --deep; then
#                                                                                    codesign --verify --strict --deep
#   after the DMG is final (stapled, or  sparkle-release.sh appcast <dmg> <tag>      runs generate_appcast on the DMG + notes,
#   signed only under SKIP_NOTARIZE=1)     <owner/repo> <notes.md> <out.xml>         checks the one-item feed against the app in
#                                                                                    the DMG, then writes <out.xml>
#
# release.sh runs under `set -e`, so any non-zero exit here stops the release,
# which is intended. Usage errors exit 2; every other failure exits 1, and a
# failed `appcast` leaves no <out.xml> behind (a stale one is removed first).
#
# Keychain: `preflight` runs `generate_keys -p` and `appcast` runs
# `generate_appcast`, which both read Sparkle's EdDSA key from the login
# keychain. There is no fallback, so dry runs (SKIP_NOTARIZE=1, SKIP_RELEASE=1)
# need that access too. The release machine has it; sandboxed agent shells don't, and
# fail here by design.
#
# Git-free on purpose: this reads only its argv and SPARKLE_BIN, never HEAD,
# main or a working tree. release.sh builds the release PR's own commit.
#
# Spec: docs/superpowers/specs/2026-07-07-in-app-updates-design.md, "Amendments
# (2026-10-05)" item 3. Plan: docs/superpowers/plans/2026-10-05-in-app-updates.md,
# Task 10 (the interface agreed on #317). Tests:
# scripts/tests/sparkle-release.test.sh, against stubs.
#
# macOS /bin/bash 3.2.

set -euo pipefail

FEED_URL="https://github.com/Raven-Scout/Scout/releases/latest/download/appcast.xml"
SPARKLE_NS="http://www.andymatuschak.org/xml-namespaces/sparkle"
PLISTBUDDY=/usr/libexec/PlistBuddy
TAG_RE='^v[0-9]+\.[0-9]+\.[0-9]+(-rc\.[0-9]+)?$'
SLUG_RE='^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$'

# Sparkle 2.10.0's nested code, relative to Sparkle.framework.
AUTOUPDATE=Versions/B/Autoupdate
UPDATER=Versions/B/Updater.app
INSTALLER=Versions/B/XPCServices/Installer.xpc
DOWNLOADER=Versions/B/XPCServices/Downloader.xpc

# State the EXIT trap cleans up (appcast only).
WORK=""     # temp dir: stage/ and the unvalidated feed
MNT=""      # temp mountpoint, deliberately outside WORK
MOUNTED=0
PARTIAL=""  # temp file beside <out.xml> before the final rename

die() {
  echo "✗ sparkle-release.sh: $*" >&2
  exit 1
}

usage() {
  cat >&2 <<'EOF'
usage: sparkle-release.sh preflight <Scout.app>
       sparkle-release.sh sign <Scout.app> <codesign-identity>
       sparkle-release.sh appcast <dmg> <vX.Y.Z[-rc.N]> <owner/repo> <notes.md> <out-appcast.xml>

SPARKLE_BIN must point at Sparkle's bin/ (generate_keys, generate_appcast) for
preflight and appcast. Called by the root scripts/release.sh.
EOF
  exit 2
}

detach_dmg() {
  [ "$MOUNTED" = 1 ] || return 0
  if hdiutil detach "$MNT" -quiet || hdiutil detach "$MNT" -force -quiet; then
    MOUNTED=0
    return 0
  fi
  return 1
}

cleanup() {
  local rc=$?
  if ! detach_dmg; then
    echo "⚠ sparkle-release.sh: could not detach $MNT; run: hdiutil detach -force \"$MNT\"" >&2
  fi
  # rmdir, not rm -rf: never recurse into a volume that is still mounted.
  if [ -n "$MNT" ] && [ "$MOUNTED" = 0 ]; then rmdir "$MNT" 2>/dev/null || true; fi
  if [ -n "$WORK" ]; then rm -rf "$WORK" || true; fi
  if [ -n "$PARTIAL" ]; then rm -f "$PARTIAL" || true; fi
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# plist_get <plist> <key>: prints the value; non-zero when the key is absent.
plist_get() {
  "$PLISTBUDDY" -c "Print :$2" "$1" 2>/dev/null
}

is_positive_int() {
  case "$1" in
    '' | *[!0-9]* | 0*) return 1 ;;
  esac
  return 0
}

require_tool() {
  [ -n "${SPARKLE_BIN:-}" ] \
    || die "SPARKLE_BIN is not set (release.sh exports \$build/SourcePackages/artifacts/sparkle/Sparkle/bin)"
  [ -x "$SPARKLE_BIN/$1" ] || die "$SPARKLE_BIN/$1 is missing or not executable"
}

# Dies, naming the path, unless the app embeds Sparkle with all its nested code.
require_sparkle_tree() {
  local app="$1" fw
  [ -d "$app" ] || die "no app bundle at $app"
  fw="$app/Contents/Frameworks/Sparkle.framework"
  [ -d "$fw" ] || die "missing $fw (this build doesn't embed Sparkle)"
  [ -f "$fw/$AUTOUPDATE" ] || die "missing Sparkle component $fw/$AUTOUPDATE"
  [ -d "$fw/$UPDATER" ] || die "missing Sparkle component $fw/$UPDATER"
  [ -d "$fw/$INSTALLER" ] || die "missing Sparkle component $fw/$INSTALLER"
  [ -d "$fw/$DOWNLOADER" ] || die "missing Sparkle component $fw/$DOWNLOADER"
}

# --- preflight ---------------------------------------------------------------

cmd_preflight() {
  local app="$1" plist feed build key want
  require_sparkle_tree "$app"
  plist="$app/Contents/Info.plist"
  [ -f "$plist" ] || die "no Info.plist at $plist"

  feed="$(plist_get "$plist" SUFeedURL)" || die "$plist has no SUFeedURL"
  [ "$feed" = "$FEED_URL" ] || die "SUFeedURL is \"$feed\", expected \"$FEED_URL\""

  build="$(plist_get "$plist" CFBundleVersion)" || die "$plist has no CFBundleVersion"
  is_positive_int "$build" \
    || die "CFBundleVersion is \"$build\", not a positive integer (Sparkle compares it to order updates)"

  key="$(plist_get "$plist" SUPublicEDKey)" || die "$plist has no SUPublicEDKey"
  [ -n "$key" ] || die "SUPublicEDKey in $plist is empty"

  require_tool generate_keys
  # Sparkle 2.10.0 generate_keys -p prints just the base64 public key and a
  # newline (generate_keys/main.swift, lookUpPublicKey branch). On failure it
  # prints "ERROR: <reason>" to stdout and exits 1.
  if ! want="$("$SPARKLE_BIN/generate_keys" -p)"; then
    die "generate_keys -p failed; it needs keychain access to Sparkle's signing key: $want"
  fi
  case "$want" in
    '' | *[!A-Za-z0-9+/=]*) die "generate_keys -p printed something other than a bare base64 key: $want" ;;
  esac
  [ "$key" = "$want" ] \
    || die "SUPublicEDKey in the built app ($key) is not the keychain's signing key (generate_keys -p: $want);" \
      "installed copies would reject updates signed with one or the other"

  echo "✓ sparkle-release preflight: Sparkle.framework complete, SUPublicEDKey matches, SUFeedURL ok, build $build"
}

# --- sign ----------------------------------------------------------------------

# sign_one <identity> <path> [keep-entitlements]
sign_one() {
  echo "→ codesign $2"
  if [ "${3:-}" = keep-entitlements ]; then
    codesign --force --options runtime --timestamp --preserve-metadata=entitlements --sign "$1" "$2" \
      || die "codesign failed on $2"
  else
    codesign --force --options runtime --timestamp --sign "$1" "$2" || die "codesign failed on $2"
  fi
}

cmd_sign() {
  local app="$1" ident="$2" fw
  [ -n "$ident" ] || die "empty codesign identity"
  # Check every path before signing anything.
  require_sparkle_tree "$app"
  fw="$app/Contents/Frameworks/Sparkle.framework"

  # Inside-out. Never --deep on a signing line: it would re-sign the nested
  # code without Downloader.xpc's entitlements.
  sign_one "$ident" "$fw/$INSTALLER"
  sign_one "$ident" "$fw/$DOWNLOADER" keep-entitlements
  sign_one "$ident" "$fw/$AUTOUPDATE"
  sign_one "$ident" "$fw/$UPDATER"
  sign_one "$ident" "$fw"
  sign_one "$ident" "$app"

  codesign --verify --strict --deep --verbose=2 "$app" || die "codesign --verify --strict --deep failed on $app"
  echo "✓ sparkle-release sign: Sparkle's nested code, Sparkle.framework and $app signed"
}

# --- appcast -------------------------------------------------------------------

# xpath <expr> <file>: prints the result (string() and count() expressions).
xpath() {
  xmllint --xpath "$1" "$2" 2>/dev/null
}

cmd_appcast() {
  local dmg="$1" tag="$2" slug="$3" notes="$4" out="$5"
  local dmg_name base out_dir prefix tmp_root p n app app_build feed items ver url sig

  [ -f "$dmg" ] || die "no DMG at $dmg"
  dmg_name="$(basename "$dmg")"
  case "$dmg_name" in
    *.dmg) ;;
    *) die "$dmg is not a .dmg" ;;
  esac
  base="${dmg_name%.dmg}"
  [ -f "$notes" ] || die "no release notes at $notes"
  [[ "$tag" =~ $TAG_RE ]] || die "tag \"$tag\" is not vX.Y.Z or vX.Y.Z-rc.N"
  [[ "$slug" =~ $SLUG_RE ]] || die "repo \"$slug\" is not owner/repo"
  out_dir="$(dirname "$out")"
  [ -d "$out_dir" ] || die "output directory $out_dir doesn't exist"
  require_tool generate_appcast

  # A failed run must not leave a previous appcast.xml for release.sh to attach.
  rm -f "$out"

  prefix="https://github.com/$slug/releases/download/$tag/"
  tmp_root="${TMPDIR:-/tmp}"
  tmp_root="${tmp_root%/}"
  WORK="$(mktemp -d "$tmp_root/sparkle-release.XXXXXX")"
  MNT="$(mktemp -d "$tmp_root/sparkle-release-mnt.XXXXXX")"

  # 1. The build number the feed must carry, from the app inside the DMG.
  hdiutil attach -nobrowse -readonly -mountpoint "$MNT" "$dmg" >/dev/null || die "hdiutil attach failed for $dmg"
  MOUNTED=1
  n=0
  app=""
  for p in "$MNT"/*.app; do
    [ -d "$p" ] || continue
    n=$((n + 1))
    app="$p"
  done
  [ "$n" -eq 1 ] || die "expected exactly one .app in $dmg, found $n"
  app_build="$(plist_get "$app/Contents/Info.plist" CFBundleVersion)" \
    || die "$(basename "$app") in $dmg has no CFBundleVersion"
  is_positive_int "$app_build" \
    || die "$(basename "$app") in $dmg has CFBundleVersion \"$app_build\", not a positive integer"
  detach_dmg || die "could not detach $MNT"

  # 2. generate_appcast, on a stage holding just the DMG and its notes. It signs
  #    the DMG with the EdDSA key in the login keychain.
  mkdir "$WORK/stage"
  cp "$dmg" "$WORK/stage/$dmg_name"
  cp "$notes" "$WORK/stage/$base.md"
  feed="$WORK/appcast.xml"
  "$SPARKLE_BIN/generate_appcast" \
    --download-url-prefix "$prefix" \
    --full-release-notes-url "https://github.com/$slug/releases/tag/$tag" \
    --link "https://github.com/$slug" \
    --embed-release-notes --maximum-versions 1 --maximum-deltas 0 \
    -o "$feed" "$WORK/stage" \
    || die "generate_appcast failed (it needs keychain access to Sparkle's signing key)"

  # 3. Check the feed before it goes anywhere.
  [ -s "$feed" ] || die "generate_appcast wrote no appcast"
  xmllint --noout "$feed" || die "the generated appcast is not well-formed XML"
  items="$(xpath 'count(//item)' "$feed")" || die "could not count the appcast's items"
  [ "$items" = 1 ] || die "the appcast has $items items, expected exactly 1"
  # Sparkle 2.10.0 writes <sparkle:version> as an element of the item; older
  # feeds carried it as an enclosure attribute.
  ver="$(xpath "string(//item/*[local-name()='version' and namespace-uri()='$SPARKLE_NS'])" "$feed")" || ver=""
  if [ -z "$ver" ]; then
    ver="$(xpath "string(//item/enclosure/@*[local-name()='version' and namespace-uri()='$SPARKLE_NS'])" "$feed")" || ver=""
  fi
  [ "$ver" = "$app_build" ] \
    || die "the appcast's sparkle:version is \"$ver\", but the app in $dmg_name has CFBundleVersion $app_build"
  url="$(xpath 'string(//item/enclosure/@url)' "$feed")" || url=""
  [ "$url" = "$prefix$dmg_name" ] || die "the appcast's enclosure URL is \"$url\", expected \"$prefix$dmg_name\""
  sig="$(xpath "string(//item/enclosure/@*[local-name()='edSignature' and namespace-uri()='$SPARKLE_NS'])" "$feed")" \
    || sig=""
  [ -n "$sig" ] || die "the appcast's enclosure has no sparkle:edSignature (was the DMG signed?)"

  # 4. Only now does <out.xml> appear.
  PARTIAL="$(mktemp "$out_dir/.appcast.XXXXXX")"
  cp "$feed" "$PARTIAL"
  chmod 644 "$PARTIAL"
  mv -f "$PARTIAL" "$out"
  PARTIAL=""
  echo "✓ sparkle-release appcast: $out (build $app_build, $prefix$dmg_name)"
}

# --- main ----------------------------------------------------------------------

[ $# -ge 1 ] || usage
sub="$1"
shift
case "$sub" in
  preflight)
    [ $# -eq 1 ] || usage
    cmd_preflight "$1"
    ;;
  sign)
    [ $# -eq 2 ] || usage
    cmd_sign "$1" "$2"
    ;;
  appcast)
    [ $# -eq 5 ] || usage
    cmd_appcast "$1" "$2" "$3" "$4" "$5"
    ;;
  *) usage ;;
esac
