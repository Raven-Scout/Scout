#!/bin/bash
# Stub-based tests for scripts/sparkle-release.sh, the Sparkle hook that the
# root scripts/release.sh (#317) calls: preflight, sign, appcast.
#
# Nothing real is signed, keyed or mounted. codesign, hdiutil, xcrun and git
# are stubs on PATH, generate_keys and generate_appcast are stubs in a fake
# SPARKLE_BIN, and xmllint passes through to the real one. Every stub appends
# its argv to a log as `tool|arg|arg|...`, so an argument with spaces stays one
# field. The fake Scout.app has Sparkle 2.10.0's layout and an Info.plist
# written with plutil; the script reads it with the real PlistBuddy.
#
#   bash scripts/tests/sparkle-release.test.sh      (from apps/macos)
#
# Self-contained, macOS /bin/bash 3.2. Prints ok/FAIL lines; exits 1 on any FAIL.

set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$(cd "$HERE/.." && pwd)/sparkle-release.sh"

FEED_URL="https://github.com/Raven-Scout/Scout/releases/latest/download/appcast.xml"
# Invented, base64-shaped stand-ins. Not keys.
KEY="dGVzdC1rZXktbm90LXJlYWwtMDAwMDAwMDAwMDAwMDA="
OTHER_KEY="b3RoZXIta2V5LW5vdC1yZWFsLTAwMDAwMDAwMDAwMDA="
IDENT="Developer ID Application: Alex Example (TEAM123456)"
SLUG="example-org/scout"

REAL_XMLLINT="$(command -v xmllint || true)"

ROOT="$(mktemp -d "${TMPDIR:-/tmp}/sparkle-release-test.XXXXXX")"
trap 'rm -rf "$ROOT"' EXIT

STUBS="$ROOT/stubs"
SPARKLE_STUB="$ROOT/sparkle-bin"
STATE="$ROOT/state"
mkdir -p "$STUBS" "$SPARKLE_STUB" "$STATE" "$ROOT/tmp"

export STUB_LOG="$ROOT/calls.log"
export STUB_STATE="$STATE"
export REAL_XMLLINT
ALL_LOG="$ROOT/all-calls.log"
: >"$STUB_LOG"
: >"$ALL_LOG"

# --- stubs --------------------------------------------------------------------

# Shared prologue: log `name|arg|arg...`.
log_line='{ printf "%s" "$(basename "$0")"; printf "|%s" "$@"; printf "\n"; } >>"$STUB_LOG"'

write_stub() { # write_stub <path> <body>
  printf '#!/bin/bash\n%s\n%s\n' "$log_line" "$2" >"$1"
  chmod +x "$1"
}

write_stub "$STUBS/codesign" '
if [ -n "${CODESIGN_FAIL_MATCH:-}" ]; then
  case "$*" in *"$CODESIGN_FAIL_MATCH"*) echo "codesign stub: refusing $CODESIGN_FAIL_MATCH" >&2; exit 1 ;; esac
fi
exit 0'

write_stub "$STUBS/xcrun" 'echo "xcrun stub: sparkle-release.sh must not notarize or staple" >&2; exit 1'
write_stub "$STUBS/git" 'echo "git stub: sparkle-release.sh must be git-free" >&2; exit 1'
write_stub "$STUBS/xmllint" '[ -n "$REAL_XMLLINT" ] || exit 127
exec "$REAL_XMLLINT" "$@"'

# hdiutil: `attach ... -mountpoint <dir> <dmg>` "mounts" $FAKE_DMG_CONTENT by
# copying it into <dir>; `detach <dir>` empties it again.
write_stub "$STUBS/hdiutil" '
verb="${1:-}"; [ $# -gt 0 ] && shift
case "$verb" in
  attach)
    mnt=""
    while [ $# -gt 0 ]; do
      case "$1" in
        -mountpoint) mnt="$2"; shift 2 ;;
        *) shift ;;
      esac
    done
    [ -n "$mnt" ] && [ -d "$mnt" ] || { echo "hdiutil stub: no -mountpoint dir" >&2; exit 1; }
    [ "${HDIUTIL_ATTACH_FAIL:-0}" = 1 ] && { echo "hdiutil: attach failed - no mountable file systems" >&2; exit 1; }
    cp -R "$FAKE_DMG_CONTENT/." "$mnt/"
    ;;
  detach)
    mnt=""
    for a in "$@"; do case "$a" in -*) ;; *) [ -n "$mnt" ] || mnt="$a" ;; esac; done
    [ -d "$mnt" ] || { echo "hdiutil stub: nothing mounted at $mnt" >&2; exit 1; }
    find "$mnt" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
    ;;
  *) echo "hdiutil stub: unexpected verb $verb" >&2; exit 1 ;;
esac
exit 0'

# generate_keys: only -p is allowed. Sparkle 2.10.0 generate_keys/main.swift,
# lookUpPublicKey branch: print(pubKey.base64EncodedString()), i.e. the bare
# base64 key and a newline. failure() prints "ERROR: <msg>" to stdout, exit 1.
write_stub "$SPARKLE_STUB/generate_keys" '
if [ $# -ne 1 ] || [ "$1" != "-p" ]; then echo "generate_keys stub: only -p is allowed" >&2; exit 64; fi
if [ "${FAKE_KEYS_FAIL:-0}" = 1 ]; then echo "ERROR: No existing signing key found!"; exit 1; fi
printf "%s\n" "$FAKE_PUBKEY"'

# generate_appcast: records what was staged, then writes a canned feed to -o.
# FAKE_FEED picks the feed: ok | two-items | bad-version | no-signature |
# bad-url | malformed | none (exit 0, write nothing) | fail (exit 1).
write_stub "$SPARKLE_STUB/generate_appcast" '
prefix=""; out=""; dir=""
while [ $# -gt 0 ]; do
  case "$1" in
    --download-url-prefix) prefix="$2"; shift 2 ;;
    --full-release-notes-url|--link|--maximum-versions|--maximum-deltas) shift 2 ;;
    --embed-release-notes) shift ;;
    -o) out="$2"; shift 2 ;;
    -*) echo "generate_appcast stub: unexpected flag $1" >&2; exit 64 ;;
    *) dir="$1"; shift ;;
  esac
done
[ -d "$dir" ] || { echo "generate_appcast stub: no archives dir" >&2; exit 1; }
ls "$dir" >"$STUB_STATE/staged.txt"
printf "%s\n" "$dir" >"$STUB_STATE/stage-dir.txt"
dmg=""
for f in "$dir"/*.dmg; do [ -f "$f" ] && dmg="$(basename "$f")"; done
for f in "$dir"/*.md; do [ -f "$f" ] && cp "$f" "$STUB_STATE/staged-notes.md"; done
feed="${FAKE_FEED:-ok}"
[ "$feed" = fail ] && { echo "ERROR: Unable to load EdDSA key" >&2; exit 1; }
[ "$feed" = none ] && exit 0
ver="$FAKE_FEED_VERSION"; url="$prefix$dmg"
sig=" sparkle:edSignature=\"ZmFrZS1zaWduYXR1cmUtbm90LXJlYWw=\""
[ "$feed" = bad-version ] && ver=999
[ "$feed" = no-signature ] && sig=""
[ "$feed" = bad-url ] && url="https://example.com/elsewhere/$dmg"
item() {
  printf "    <item>\n      <title>1.2.3</title>\n      <sparkle:version>%s</sparkle:version>\n" "$ver"
  printf "      <sparkle:shortVersionString>1.2.3</sparkle:shortVersionString>\n"
  printf "      <enclosure url=\"%s\" length=\"123\" type=\"application/octet-stream\"%s/>\n    </item>\n" "$url" "$sig"
}
{
  printf "<?xml version=\"1.0\" standalone=\"yes\"?>\n"
  printf "<rss xmlns:sparkle=\"http://www.andymatuschak.org/xml-namespaces/sparkle\" version=\"2.0\">\n  <channel>\n    <title>Scout</title>\n"
  item
  [ "$feed" = two-items ] && item
  if [ "$feed" = malformed ]; then printf "  <channel>\n"; else printf "  </channel>\n</rss>\n"; fi
} >"$out"
exit 0'

export PATH="$STUBS:$PATH"
export SPARKLE_BIN="$SPARKLE_STUB"
export FAKE_PUBKEY="$KEY"
export TMPDIR="$ROOT/tmp"

# --- fixtures -------------------------------------------------------------------

# mkapp <dir> <CFBundleVersion> <SUPublicEDKey> <SUFeedURL>; "-" omits a key.
mkapp() {
  local app="$1/Scout.app" b plist
  b="$app/Contents/Frameworks/Sparkle.framework/Versions/B"
  rm -rf "$app"
  mkdir -p "$app/Contents/MacOS" "$b/Updater.app/Contents/MacOS" \
    "$b/XPCServices/Installer.xpc/Contents/MacOS" "$b/XPCServices/Downloader.xpc/Contents/MacOS"
  : >"$app/Contents/MacOS/Scout"
  : >"$b/Sparkle"
  : >"$b/Autoupdate"
  plist="$app/Contents/Info.plist"
  printf '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n<plist version="1.0">\n<dict/>\n</plist>\n' >"$plist"
  /usr/bin/plutil -insert CFBundleIdentifier -string com.example.scout "$plist"
  [ "$2" = - ] || /usr/bin/plutil -insert CFBundleVersion -string "$2" "$plist"
  [ "$3" = - ] || /usr/bin/plutil -insert SUPublicEDKey -string "$3" "$plist"
  [ "$4" = - ] || /usr/bin/plutil -insert SUFeedURL -string "$4" "$plist"
  /usr/bin/plutil -insert SUEnableAutomaticChecks -bool true "$plist"
}

APPDIR="$ROOT/app"
APP="$APPDIR/Scout.app"
FW="$APP/Contents/Frameworks/Sparkle.framework"
B="$FW/Versions/B"
COMPONENTS="Versions/B/Autoupdate Versions/B/Updater.app Versions/B/XPCServices/Installer.xpc Versions/B/XPCServices/Downloader.xpc"

# The DMG's content: a release-shaped Scout.app (build 42) plus the Applications link.
export FAKE_DMG_CONTENT="$ROOT/dmg-content"
mkdir -p "$FAKE_DMG_CONTENT"
mkapp "$FAKE_DMG_CONTENT" 42 "$KEY" "$FEED_URL"
ln -s /Applications "$FAKE_DMG_CONTENT/Applications"
export FAKE_FEED_VERSION=42

W="$ROOT/w"
mkdir -p "$W/out"
NOTES="$W/release-notes.md"
printf '## What changed\n\n- **Faster** sync for [[Notes]] — `scoutctl` _(tested)_\n' >"$NOTES"
OUT_XML="$W/out/appcast.xml"

mkdmg() { # mkdmg <name>: a dummy file standing in for the DMG
  rm -f "$W"/*.dmg
  printf 'not a real disk image\n' >"$W/$1"
  DMG="$W/$1"
}

# --- harness --------------------------------------------------------------------

PASSES=0
FAILS=0
OUT=""
RC=0

ok() { PASSES=$((PASSES + 1)); printf 'ok   %s\n' "$1"; }
bad() {
  FAILS=$((FAILS + 1))
  printf 'FAIL %s\n' "$1"
  printf '     rc=%s output:\n' "$RC"
  printf '%s\n' "$OUT" | sed 's/^/       | /'
}
# check <description> <test command...>
check() { local d="$1"; shift; if "$@"; then ok "$d"; else bad "$d"; fi; }

run() { # run <subcommand args...>: sets OUT and RC; logs to $STUB_LOG
  : >"$STUB_LOG"
  rm -f "$STATE"/*.txt "$STATE"/*.md
  OUT="$("$SCRIPT" "$@" 2>&1)"
  RC=$?
  cat "$STUB_LOG" >>"$ALL_LOG"
}

rc_is() { [ "$RC" -eq "$1" ]; }
rc_nonzero() { [ "$RC" -ne 0 ]; }
out_has() { case "$OUT" in *"$1"*) return 0 ;; esac; return 1; }
log_has() { grep -qF -- "$1" "$STUB_LOG"; }
log_lacks() { ! grep -qF -- "$1" "$STUB_LOG"; }
log_count() { grep -cF -- "$1" "$STUB_LOG" || true; }
eq() { [ "$1" = "$2" ]; }
no_file() { [ ! -e "$1" ]; }
tmp_empty() { [ -z "$(ls -A "$TMPDIR")" ]; }
outdir_clean() { # nothing but (maybe) appcast.xml in the output dir
  local f
  for f in "$W/out"/* "$W/out"/.[!.]*; do
    [ -e "$f" ] || continue
    [ "$f" = "$OUT_XML" ] || return 1
  done
  return 0
}
# The mountpoint the script attached, from the hdiutil log line.
mountpoint_used() { grep '^hdiutil|attach|' "$STUB_LOG" | sed 's/.*|-mountpoint|\([^|]*\)|.*/\1/' | head -1; }
detached_and_gone() {
  local m
  m="$(mountpoint_used)"
  [ -n "$m" ] || return 1
  grep -qF "hdiutil|detach|$m" "$STUB_LOG" && [ ! -e "$m" ]
}

# --- 0. the script itself ---------------------------------------------------------

echo "# script"
check "sparkle-release.sh exists and is executable (release.sh only calls it when -x)" [ -x "$SCRIPT" ]

# --- 1. usage ---------------------------------------------------------------------

echo "# usage"
run
check "no subcommand -> exit 2" rc_is 2
check "no subcommand -> usage text" out_has "usage"
run bogus "$APP"
check "unknown subcommand -> exit 2" rc_is 2
check "unknown subcommand -> usage text" out_has "usage"
run preflight
check "preflight without app -> exit 2" rc_is 2
run preflight "$APP" extra
check "preflight with extra arg -> exit 2" rc_is 2
run sign "$APP"
check "sign without identity -> exit 2" rc_is 2
run appcast a b c d
check "appcast with 4 args -> exit 2" rc_is 2
run appcast a b c d e f
check "appcast with 6 args -> exit 2" rc_is 2

# --- 2. preflight -----------------------------------------------------------------

echo "# preflight"
mkapp "$APPDIR" 42 "$KEY" "$FEED_URL"
run preflight "$APP"
check "preflight passes on a matching key, feed URL and build number" rc_is 0
check "preflight asks generate_keys -p for the public key" log_has "generate_keys|-p"
check "preflight signs nothing" log_lacks "codesign"

mkapp "$APPDIR" 42 "$OTHER_KEY" "$FEED_URL"
run preflight "$APP"
check "preflight dies on a mismatched SUPublicEDKey" rc_nonzero
check "  ...and names SUPublicEDKey" out_has "SUPublicEDKey"

mkapp "$APPDIR" 42 - "$FEED_URL"
run preflight "$APP"
check "preflight dies when SUPublicEDKey is missing" rc_nonzero

mkapp "$APPDIR" 42 "$KEY" "$FEED_URL"
FAKE_KEYS_FAIL=1 run preflight "$APP"
check "preflight dies when generate_keys -p fails (no key / no keychain access)" rc_nonzero
check "  ...and passes on generate_keys' message" out_has "No existing signing key found"

mkapp "$APPDIR" 42 "$KEY" "$FEED_URL"
rm -rf "$FW"
run preflight "$APP"
check "preflight dies on a missing Sparkle.framework" rc_nonzero
check "  ...and names it" out_has "Sparkle.framework"

for c in $COMPONENTS; do
  mkapp "$APPDIR" 42 "$KEY" "$FEED_URL"
  rm -rf "${FW:?}/$c"
  run preflight "$APP"
  check "preflight dies on missing $c" rc_nonzero
  check "  ...and names $c" out_has "$c"
done

mkapp "$APPDIR" 42 "$KEY" "https://example.com/appcast.xml"
run preflight "$APP"
check "preflight dies on a wrong SUFeedURL" rc_nonzero
check "  ...and names SUFeedURL" out_has "SUFeedURL"

mkapp "$APPDIR" 42 "$KEY" -
run preflight "$APP"
check "preflight dies when SUFeedURL is missing" rc_nonzero

for v in 1.2 abc 0 -3 ""; do
  mkapp "$APPDIR" "$v" "$KEY" "$FEED_URL"
  run preflight "$APP"
  check "preflight dies on CFBundleVersion \"$v\"" rc_nonzero
  check "  ...and names CFBundleVersion" out_has "CFBundleVersion"
done

mkapp "$APPDIR" - "$KEY" "$FEED_URL"
run preflight "$APP"
check "preflight dies when CFBundleVersion is missing" rc_nonzero

mkapp "$APPDIR" 42 "$KEY" "$FEED_URL"
SPARKLE_BIN="" run preflight "$APP"
check "preflight dies when SPARKLE_BIN is unset" rc_nonzero
check "  ...and names SPARKLE_BIN" out_has "SPARKLE_BIN"

run preflight "$ROOT/nope/Scout.app"
check "preflight dies on a missing app" rc_nonzero

# --- 3. sign ----------------------------------------------------------------------

echo "# sign"
mkapp "$APPDIR" 42 "$KEY" "$FEED_URL"
run sign "$APP" "$IDENT"
check "sign passes" rc_is 0

EXPECTED="$ROOT/expected-sign.log"
{
  printf 'codesign|--force|--options|runtime|--timestamp|--sign|%s|%s\n' "$IDENT" "$B/XPCServices/Installer.xpc"
  printf 'codesign|--force|--options|runtime|--timestamp|--preserve-metadata=entitlements|--sign|%s|%s\n' "$IDENT" "$B/XPCServices/Downloader.xpc"
  printf 'codesign|--force|--options|runtime|--timestamp|--sign|%s|%s\n' "$IDENT" "$B/Autoupdate"
  printf 'codesign|--force|--options|runtime|--timestamp|--sign|%s|%s\n' "$IDENT" "$B/Updater.app"
  printf 'codesign|--force|--options|runtime|--timestamp|--sign|%s|%s\n' "$IDENT" "$FW"
  printf 'codesign|--force|--options|runtime|--timestamp|--sign|%s|%s\n' "$IDENT" "$APP"
  printf 'codesign|--verify|--strict|--deep|--verbose=2|%s\n' "$APP"
} >"$EXPECTED"
check "sign runs exactly: Installer.xpc, Downloader.xpc, Autoupdate, Updater.app, Sparkle.framework, Scout.app, then verify" \
  diff -u "$EXPECTED" "$STUB_LOG"
check "exactly six codesign --force lines" eq "$(log_count 'codesign|--force|')" 6
check "only Downloader.xpc keeps its entitlements" \
  eq "$(grep -F -- '--preserve-metadata=entitlements' "$STUB_LOG" | grep -c 'Downloader\.xpc$')|$(log_count '--preserve-metadata')" "1|1"
check "every signing line has --options runtime --timestamp" \
  eq "$(grep '^codesign|--force|' "$STUB_LOG" | grep -c '|--options|runtime|--timestamp|')" 6
check "no signing line has --deep" eq "$(grep '^codesign|--force|' "$STUB_LOG" | grep -c -- '--deep')" 0
check "the identity reaches codesign as one argument" eq "$(log_count "|--sign|$IDENT|")" 6
check "the last line is codesign --verify --strict --deep on the app" \
  eq "$(tail -n 1 "$STUB_LOG")" "codesign|--verify|--strict|--deep|--verbose=2|$APP"

for c in $COMPONENTS; do
  mkapp "$APPDIR" 42 "$KEY" "$FEED_URL"
  rm -rf "${FW:?}/$c"
  run sign "$APP" "$IDENT"
  check "sign dies on missing $c" rc_nonzero
  check "  ...names $c" out_has "$c"
  check "  ...and has signed nothing" log_lacks "codesign"
done

mkapp "$APPDIR" 42 "$KEY" "$FEED_URL"
rm -rf "$FW"
run sign "$APP" "$IDENT"
check "sign dies on a missing Sparkle.framework, signing nothing" rc_nonzero
check "  ...and has signed nothing" log_lacks "codesign"

mkapp "$APPDIR" 42 "$KEY" "$FEED_URL"
CODESIGN_FAIL_MATCH="Versions/B/Autoupdate" run sign "$APP" "$IDENT"
check "sign dies when a codesign fails" rc_nonzero
check "  ...and stops there (Updater.app, the framework and the app are not signed)" \
  eq "$(log_count 'codesign|--force|')" 3

mkapp "$APPDIR" 42 "$KEY" "$FEED_URL"
CODESIGN_FAIL_MATCH="--verify" run sign "$APP" "$IDENT"
check "sign dies when the final verify fails" rc_nonzero

run sign "$APP" ""
check "sign dies on an empty identity" rc_nonzero

# --- 4. appcast -------------------------------------------------------------------

echo "# appcast"
TAG=v1.2.3
mkdmg Scout-1.2.3.dmg
rm -f "$OUT_XML"
run appcast "$DMG" "$TAG" "$SLUG" "$NOTES" "$OUT_XML"
check "appcast passes on a valid one-item feed" rc_is 0
check "  ...and writes appcast.xml" [ -s "$OUT_XML" ]
check "  ...which is well-formed" "$REAL_XMLLINT" --noout "$OUT_XML"
GA_LINE="$(grep '^generate_appcast|' "$STUB_LOG" || true)"
GA_WANT="generate_appcast|--download-url-prefix|https://github.com/$SLUG/releases/download/$TAG/|--full-release-notes-url|https://github.com/$SLUG/releases/tag/$TAG|--link|https://github.com/$SLUG|--embed-release-notes|--maximum-versions|1|--maximum-deltas|0|-o|"
check "generate_appcast gets the exact flags" eq "${GA_LINE:0:${#GA_WANT}}" "$GA_WANT"
STAGE_DIR="$(cat "$STATE/stage-dir.txt" 2>/dev/null || true)"
stage_is_last() { [ -n "$STAGE_DIR" ] && [ "${GA_LINE##*|}" = "$STAGE_DIR" ]; }
stage_gone() { [ -n "$STAGE_DIR" ] && [ ! -e "$STAGE_DIR" ]; }
check "  ...and the stage dir as its last argument" stage_is_last
GA_O="$(printf '%s\n' "$GA_LINE" | awk -F'|' '{print $(NF-1)}')"
o_is_temp() { [ -n "$GA_O" ] && [ "$GA_O" != "$OUT_XML" ] && [ "$(dirname "$GA_O")" != "$W/out" ]; }
check "  ...and -o is a temp path, not the final appcast.xml" o_is_temp
check "staged exactly the DMG and <dmg-basename>.md" \
  eq "$(tr '\n' ' ' <"$STATE/staged.txt" 2>/dev/null)" "Scout-1.2.3.dmg Scout-1.2.3.md "
check "  ...the .md is the release notes" cmp -s "$NOTES" "$STATE/staged-notes.md"
check "attached the DMG read-only at a private mountpoint" \
  eq "$(grep -c "^hdiutil|attach|-nobrowse|-readonly|-mountpoint|[^|]*|$DMG\$" "$STUB_LOG")" 1
check "detached it, and the mountpoint is gone" detached_and_gone
check "ran xmllint --noout on the feed" log_has "xmllint|--noout|"
check "stage dir is cleaned up" stage_gone
check "TMPDIR is left empty" tmp_empty
check "output dir holds only appcast.xml" outdir_clean

TAG=v1.2.3-rc.1
mkdmg Scout-1.2.3-rc.1.dmg
run appcast "$DMG" "$TAG" "$SLUG" "$NOTES" "$OUT_XML"
check "appcast passes for an rc tag" rc_is 0
check "  ...with the rc download prefix" \
  log_has "|--download-url-prefix|https://github.com/$SLUG/releases/download/v1.2.3-rc.1/|"

TAG=v1.2.3
mkdmg Scout-1.2.3.dmg
for feed in two-items bad-version no-signature bad-url malformed none fail; do
  printf 'stale\n' >"$OUT_XML" # a previous run's appcast must not survive a failure
  FAKE_FEED="$feed" run appcast "$DMG" "$TAG" "$SLUG" "$NOTES" "$OUT_XML"
  check "appcast dies on feed '$feed'" rc_nonzero
  check "  ...leaves no appcast.xml ($feed)" no_file "$OUT_XML"
  check "  ...detaches the DMG ($feed)" detached_and_gone
  check "  ...leaves TMPDIR empty ($feed)" tmp_empty
  check "  ...leaves the output dir clean ($feed)" outdir_clean
done
FAKE_FEED=two-items run appcast "$DMG" "$TAG" "$SLUG" "$NOTES" "$OUT_XML"
check "two items -> says how many" out_has "has 2 items"
FAKE_FEED=bad-version run appcast "$DMG" "$TAG" "$SLUG" "$NOTES" "$OUT_XML"
check "mismatched version -> names sparkle:version and the app's 42" out_has "sparkle:version"
check "  ...and the app's CFBundleVersion" out_has "42"
FAKE_FEED=no-signature run appcast "$DMG" "$TAG" "$SLUG" "$NOTES" "$OUT_XML"
check "no signature -> names sparkle:edSignature" out_has "edSignature"
FAKE_FEED=bad-url run appcast "$DMG" "$TAG" "$SLUG" "$NOTES" "$OUT_XML"
check "wrong enclosure URL -> names the expected URL" \
  out_has "https://github.com/$SLUG/releases/download/$TAG/Scout-1.2.3.dmg"

# The DMG's app disagrees with the feed it would be listed under.
mkapp "$FAKE_DMG_CONTENT" 41 "$KEY" "$FEED_URL"
run appcast "$DMG" "$TAG" "$SLUG" "$NOTES" "$OUT_XML"
check "appcast dies when the DMG's app is build 41 but the feed says 42" rc_nonzero
check "  ...leaves no appcast.xml" no_file "$OUT_XML"
mkapp "$FAKE_DMG_CONTENT" abc "$KEY" "$FEED_URL"
run appcast "$DMG" "$TAG" "$SLUG" "$NOTES" "$OUT_XML"
check "appcast dies when the DMG's app has a non-numeric CFBundleVersion" rc_nonzero
check "  ...without running generate_appcast" log_lacks "generate_appcast"
check "  ...and detaches the DMG" detached_and_gone
mv "$FAKE_DMG_CONTENT/Scout.app" "$ROOT/Scout.app.aside"
run appcast "$DMG" "$TAG" "$SLUG" "$NOTES" "$OUT_XML"
check "appcast dies when the DMG holds no .app" rc_nonzero
check "  ...and detaches the DMG" detached_and_gone
rm -rf "$FAKE_DMG_CONTENT/Scout.app"
mv "$ROOT/Scout.app.aside" "$FAKE_DMG_CONTENT/Scout.app"
mkapp "$FAKE_DMG_CONTENT" 42 "$KEY" "$FEED_URL"

HDIUTIL_ATTACH_FAIL=1 run appcast "$DMG" "$TAG" "$SLUG" "$NOTES" "$OUT_XML"
check "appcast dies when the DMG won't attach" rc_nonzero
check "  ...without running generate_appcast" log_lacks "generate_appcast"
check "  ...and without a detach" log_lacks "hdiutil|detach"
check "  ...leaving TMPDIR empty" tmp_empty

run appcast "$W/missing.dmg" "$TAG" "$SLUG" "$NOTES" "$OUT_XML"
check "appcast dies on a missing DMG" rc_nonzero
run appcast "$DMG" "$TAG" "$SLUG" "$W/missing.md" "$OUT_XML"
check "appcast dies on missing notes" rc_nonzero
for t in 1.2.3 v1.2 v1.2.3-beta.1 "v1.2.3 "; do
  run appcast "$DMG" "$t" "$SLUG" "$NOTES" "$OUT_XML"
  check "appcast dies on tag \"$t\"" rc_nonzero
done
for s in scout "example-org/scout/x" "example org/scout" ""; do
  run appcast "$DMG" "$TAG" "$s" "$NOTES" "$OUT_XML"
  check "appcast dies on slug \"$s\"" rc_nonzero
done
run appcast "$DMG" "$TAG" "$SLUG" "$NOTES" "$W/no-such-dir/appcast.xml"
check "appcast dies when the output dir doesn't exist" rc_nonzero
SPARKLE_BIN="" run appcast "$DMG" "$TAG" "$SLUG" "$NOTES" "$OUT_XML"
check "appcast dies when SPARKLE_BIN is unset" rc_nonzero

# --- 5. across every run ------------------------------------------------------------

echo "# all runs"
check "no git calls (the script reads only argv and SPARKLE_BIN)" eq "$(grep -c '^git|' "$ALL_LOG")" 0
check "no xcrun calls (no notarizing or stapling here)" eq "$(grep -c '^xcrun|' "$ALL_LOG")" 0
check "generate_keys is only ever called as -p" eq "$(grep '^generate_keys|' "$ALL_LOG" | grep -vc '^generate_keys|-p$')" 0

echo
echo "$PASSES passed, $FAILS failed"
[ "$FAILS" -eq 0 ]
