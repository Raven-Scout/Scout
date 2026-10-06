#!/usr/bin/env bash
# Tests for scripts/bundle-engine.sh against a throwaway monorepo-shaped git
# repo: plugin/ (the engine) beside apps/macos/ (SRCROOT).
# Assertions are single-quoted on purpose: `assert` evals them later.
# shellcheck disable=SC2016,SC2034
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$HERE/../bundle-engine.sh"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
FAILS=0
assert() { if ! eval "$1"; then echo "FAIL: $2"; FAILS=$((FAILS + 1)); else echo "ok: $2"; fi; }
g() { git -C "$REPO" -c user.name=t -c user.email=t@example.com -c commit.gpgsign=false "$@"; }
json() { python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); [d := d[k] for k in sys.argv[2:]]; print(d)' "$@"; }
# run <out-dir> [VAR=value ...]: the script as Xcode would run it, minus the
# product paths; stdout+stderr to <out-dir>.log, exit code in $RC.
run() {
  local out="$1"; shift
  set +e; env SRCROOT="$APP" SCOUT_ENGINE_OUT="$out" "$@" bash "$SCRIPT" >"$out.log" 2>&1; RC=$?; set -e
}

REPO="$TMP/repo"; APP="$REPO/apps/macos"
mkdir -p "$REPO/plugin/.claude-plugin" "$REPO/plugin/engine" "$APP/Scout/Resources"
g init -q
printf '{"name": "scout", "version": "9.9.9"}\n' > "$REPO/plugin/.claude-plugin/plugin.json"
echo "print('hi')" > "$REPO/plugin/engine/x.py"
UV_SHA_ARM="$(printf 'a%.0s' {1..64})"; UV_SHA_X86="$(printf 'b%.0s' {1..64})"
printf '{"version": "0.12.1", "sha256": {"aarch64-apple-darwin": "%s", "x86_64-apple-darwin": "%s"}}\n' \
  "$UV_SHA_ARM" "$UV_SHA_X86" > "$APP/Scout/Resources/uv-release.json"
echo ".venv/" > "$REPO/plugin/.gitignore"
g add plugin apps && g commit -qm init
COMMIT="$(g rev-parse HEAD)"
mkdir -p "$REPO/plugin/.venv/bin" && echo junk > "$REPO/plugin/.venv/bin/python"   # ignored: must NOT ship

# 1. archives HEAD:plugin, manifest at the archive root, named from plugin.json
OUT="$TMP/out1"; run "$OUT"
TB="$OUT/scout-engine-9.9.9.tar.gz"
assert '[ "$RC" -eq 0 ]' "bundles: exit 0"
assert '[ -f "$TB" ]' "tarball named from plugin.json's version"
assert 'tar -tzf "$TB" | grep -qx ".claude-plugin/plugin.json"' "plugin.json at the archive root"
assert 'tar -tzf "$TB" | grep -qx "engine/x.py"' "tracked file archived"
assert '! tar -tzf "$TB" | grep -q "^plugin/"' "no plugin/ prefix in the archive"
assert '! tar -tzf "$TB" | grep -q "\.venv"' "untracked files excluded"
assert '! tar -tzf "$TB" | grep -q "^apps/"' "only plugin/ is archived"

# 2. engine-release.json is generated: version + build commit + the uv pin
RJ="$OUT/engine-release.json"
assert '[ -f "$RJ" ]' "engine-release.json generated"
assert '[ "$(json "$RJ" schema_version)" = 2 ]' "schema_version 2"
assert '[ "$(json "$RJ" version)" = 9.9.9 ]' "top-level version from plugin.json (release.sh finalize reads it)"
assert '[ "$(json "$RJ" engine version)" = 9.9.9 ]' "engine.version from plugin.json"
assert '[ "$(json "$RJ" engine commit)" = "$COMMIT" ]' "engine.commit is the build commit"
assert '[ "$(json "$RJ" uv version)" = 0.12.1 ]' "uv.version from the pin file"
assert '[ "$(json "$RJ" uv sha256 aarch64-apple-darwin)" = "$UV_SHA_ARM" ]' "uv arm64 sha256 from the pin file"
assert '[ "$(json "$RJ" uv sha256 x86_64-apple-darwin)" = "$UV_SHA_X86" ]' "uv x86_64 sha256 from the pin file"

# 3. deterministic: the same commit gives the same bytes
H1="$(shasum -a 256 "$TB" | awk '{print $1}')"
sleep 1   # a clock-stamped archive would now differ
run "$OUT"
assert '[ "$H1" = "$(shasum -a 256 "$TB" | awk "{print \$1}")" ]' "same commit, byte-identical tarball"

# 4. uncommitted plugin/ changes: the committed tree ships, with a warning
printf '{"name": "scout", "version": "8.8.8"}\n' > "$REPO/plugin/.claude-plugin/plugin.json"
echo "new" > "$REPO/plugin/engine/untracked.py"
OUT4="$TMP/out4"; run "$OUT4"
assert '[ "$RC" -eq 0 ]' "dirty plugin/: still exit 0"
assert 'grep -q "^warning: .*uncommitted changes" "$OUT4.log"' "dirty plugin/: warns"
assert '[ -f "$OUT4/scout-engine-9.9.9.tar.gz" ] && [ ! -e "$OUT4/scout-engine-8.8.8.tar.gz" ]' "dirty plugin/: HEAD's version, not the working tree's"
assert '[ "$H1" = "$(shasum -a 256 "$OUT4/scout-engine-9.9.9.tar.gz" | awk "{print \$1}")" ]' "dirty plugin/: committed bytes"
assert '! tar -tzf "$OUT4/scout-engine-9.9.9.tar.gz" | grep -q "untracked.py"' "dirty plugin/: untracked file not shipped"
git -C "$REPO" checkout -q -- plugin && rm -f "$REPO/plugin/engine/untracked.py"
run "$TMP/out4b"
assert '! grep -q "warning" "$TMP/out4b.log"' "clean plugin/: no warning"

# 5. a version bump replaces the old tarball instead of leaving it beside the new one
printf '{"name": "scout", "version": "9.10.0"}\n' > "$REPO/plugin/.claude-plugin/plugin.json"
g commit -qam bump
run "$OUT"
assert '[ -f "$OUT/scout-engine-9.10.0.tar.gz" ] && [ ! -e "$OUT/scout-engine-9.9.9.tar.gz" ]' "bump: stale tarball removed"
assert '[ "$(json "$OUT/engine-release.json" engine version)" = 9.10.0 ] && [ "$(json "$OUT/engine-release.json" version)" = 9.10.0 ]' "bump: engine-release.json follows"

# 6. MARKETING_VERSION (Xcode) must equal plugin.json's version (spec D2):
#    an error in Release/strict builds, a warning in Debug
run "$TMP/out6" MARKETING_VERSION=9.10.0 CONFIGURATION=Release
assert '[ "$RC" -eq 0 ] && ! grep -q warning "$TMP/out6.log"' "matching MARKETING_VERSION: ok"
run "$TMP/out6b" MARKETING_VERSION=1.0.0 CONFIGURATION=Release
assert '[ "$RC" -ne 0 ]' "Release, mismatched MARKETING_VERSION: fails"
assert 'grep -q "MARKETING_VERSION 1.0.0" "$TMP/out6b.log"' "Release, mismatched MARKETING_VERSION: says why"
assert '[ ! -e "$TMP/out6b/engine-release.json" ] && ! ls "$TMP/out6b"/scout-engine-* >/dev/null 2>&1' "Release, mismatched MARKETING_VERSION: writes nothing"
run "$TMP/out6c" MARKETING_VERSION=1.0.0 SCOUT_BUNDLE_STRICT=1
assert '[ "$RC" -ne 0 ]' "strict, mismatched MARKETING_VERSION: fails"
run "$TMP/out6d" MARKETING_VERSION=1.0.0 CONFIGURATION=Debug
assert '[ "$RC" -eq 0 ] && grep -q "^warning: .*MARKETING_VERSION 1.0.0" "$TMP/out6d.log"' "Debug, mismatched MARKETING_VERSION: warns"
assert '[ "$(json "$TMP/out6d/engine-release.json" engine version)" = 9.10.0 ]' "Debug, mismatched MARKETING_VERSION: still bundles plugin.json's version"

# 7. a version that isn't SemVer-shaped (it becomes a path component) fails
printf '{"name": "scout", "version": "../evil"}\n' > "$REPO/plugin/.claude-plugin/plugin.json"
g commit -qam evil
run "$TMP/out7"
assert '[ "$RC" -ne 0 ] && [ ! -e "$TMP/out7/engine-release.json" ]' "non-SemVer version: fails"
g reset -q --hard HEAD~1

# 8. no plugin.json at HEAD fails (only a committed manifest counts)
g rm -q plugin/.claude-plugin/plugin.json && g commit -qm "drop manifest"
mkdir -p "$REPO/plugin/.claude-plugin"
printf '{"name": "scout", "version": "9.10.0"}\n' > "$REPO/plugin/.claude-plugin/plugin.json"   # working tree only
run "$TMP/out8"
assert '[ "$RC" -ne 0 ]' "missing manifest at HEAD: fails"
assert 'grep -q "HEAD has no plugin/.claude-plugin/plugin.json" "$TMP/out8.log"' "missing manifest: says why"
rm -f "$REPO/plugin/.claude-plugin/plugin.json"; g reset -q --hard HEAD~1

# 9. a missing or malformed uv pin fails
run "$TMP/out9" SCOUT_UV_PIN="$TMP/nope.json"
assert '[ "$RC" -ne 0 ]' "missing uv pin: fails"
printf '{"version": "0.12.1", "sha256": {"aarch64-apple-darwin": "xyz"}}\n' > "$TMP/bad-uv.json"
run "$TMP/out9b" SCOUT_UV_PIN="$TMP/bad-uv.json"
assert '[ "$RC" -ne 0 ] && [ ! -e "$TMP/out9b/engine-release.json" ]' "malformed uv sha256: fails, no JSON"

# 10. a pre-release identifier with a numeric leading zero is not SemVer
# (§9) — EngineVersion rejects it too, so this keeps the two in agreement
printf '{"name": "scout", "version": "1.0.0-01"}\n' > "$REPO/plugin/.claude-plugin/plugin.json"
g commit -qam "leading-zero prerelease"
run "$TMP/out10"
assert '[ "$RC" -ne 0 ] && [ ! -e "$TMP/out10/engine-release.json" ]' "leading-zero pre-release identifier: fails (SemVer §9)"
g reset -q --hard HEAD~1

# 11. the uv pin must name BOTH Mac architectures, not just one
printf '{"version": "0.12.1", "sha256": {"aarch64-apple-darwin": "%s"}}\n' "$UV_SHA_ARM" > "$TMP/missing-arch-uv.json"
run "$TMP/out11" SCOUT_UV_PIN="$TMP/missing-arch-uv.json"
assert '[ "$RC" -ne 0 ] && [ ! -e "$TMP/out11/engine-release.json" ]' "uv pin missing x86_64-apple-darwin: fails"
assert 'grep -q "x86_64-apple-darwin" "$TMP/out11.log"' "uv pin missing arch: names the missing key"

# 12. `git status` must not take index.lock or rewrite .git/index — a
# concurrent `git commit` could otherwise fail mid-build. A "stat-dirty" file
# (mtime touched, content unchanged) is exactly what makes plain `git status`
# refresh-and-rewrite the index; --no-optional-locks must suppress that.
touch "$REPO/plugin/.claude-plugin/plugin.json"
IDX="$REPO/.git/index"
if stat -f '%i' "$IDX" >/dev/null 2>&1; then
  INODE_BEFORE="$(stat -f '%i' "$IDX")"; MTIME_BEFORE="$(stat -f '%m' "$IDX")"
  sleep 1
  run "$TMP/out12"
  assert '[ "$RC" -eq 0 ] && ! grep -q warning "$TMP/out12.log"' "stat-dirty plugin.json: still bundles cleanly"
  INODE_AFTER="$(stat -f '%i' "$IDX")"; MTIME_AFTER="$(stat -f '%m' "$IDX")"
  assert '[ "$INODE_BEFORE" = "$INODE_AFTER" ] && [ "$MTIME_BEFORE" = "$MTIME_AFTER" ]' "git status (--no-optional-locks) leaves .git/index untouched"
else
  echo "skip: .git/index mtime/inode check (BSD stat -f unavailable)"
fi

# 13. outside a git repository there is nothing to archive
NOGIT="$TMP/nogit/apps/macos"; mkdir -p "$NOGIT"
set +e; env SRCROOT="$NOGIT" GIT_CEILING_DIRECTORIES="$TMP" SCOUT_ENGINE_OUT="$TMP/out13" bash "$SCRIPT" >/dev/null 2>&1; RC=$?; set -e
assert '[ "$RC" -ne 0 ]' "not a git repo: fails"

# 14. the script only reads the repo: HEAD and the index are untouched
assert '[ -z "$(git -C "$REPO" status --porcelain --untracked-files=no)" ]' "repo left clean"

[ "$FAILS" -eq 0 ] || exit 1
