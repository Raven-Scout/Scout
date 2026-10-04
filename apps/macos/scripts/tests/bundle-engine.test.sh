#!/usr/bin/env bash
# Tests for scripts/bundle-engine.sh against a throwaway git repo.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$HERE/../bundle-engine.sh"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
FAILS=0
assert() { if ! eval "$1"; then echo "FAIL: $2"; FAILS=$((FAILS + 1)); else echo "ok: $2"; fi; }

# A fake scout-plugin with one tagged commit.
SRC="$TMP/src"; mkdir -p "$SRC/.claude-plugin" "$SRC/engine"
git -C "$SRC" init -q
printf '{"name": "scout", "version": "9.9.9"}\n' > "$SRC/.claude-plugin/plugin.json"
echo "print('hi')" > "$SRC/engine/x.py"
mkdir -p "$SRC/.venv/bin" && echo junk > "$SRC/.venv/bin/python"   # untracked: must NOT be archived
git -C "$SRC" add .claude-plugin engine && git -C "$SRC" -c user.name=t -c user.email=t@example.com commit -qm init
git -C "$SRC" tag v9.9.9
COMMIT="$(git -C "$SRC" rev-parse HEAD)"

PIN="$TMP/pin.json"
printf '{"schema_version":1,"engine":{"repo":"example-org/scout-plugin","version":"9.9.9","tag":"v9.9.9","commit":"%s"},"uv":{"version":"0","sha256":{}}}\n' "$COMMIT" > "$PIN"

# 1. archives the pinned commit from SCOUT_ENGINE_SOURCE
OUT="$TMP/out1"
SCOUT_ENGINE_PIN="$PIN" SCOUT_ENGINE_SOURCE="$SRC" SCOUT_ENGINE_OUT="$OUT" bash "$SCRIPT"
assert '[ -f "$OUT/scout-engine-9.9.9.tar.gz" ]' "tarball produced"
assert 'tar -tzf "$OUT/scout-engine-9.9.9.tar.gz" | grep -q "^engine/x.py$"' "tracked file archived"
assert '! tar -tzf "$OUT/scout-engine-9.9.9.tar.gz" | grep -q ".venv"' "untracked .venv excluded"

# 2. refuses when the manifest version disagrees with the pin
sed 's/"version":"9.9.9"/"version":"1.0.0"/' "$PIN" > "$TMP/pin-bad.json"
set +e; SCOUT_ENGINE_PIN="$TMP/pin-bad.json" SCOUT_ENGINE_SOURCE="$SRC" SCOUT_ENGINE_OUT="$TMP/out2" bash "$SCRIPT" 2>/dev/null; RC=$?; set -e
assert '[ "$RC" -ne 0 ]' "version mismatch fails"
assert '[ ! -f "$TMP/out2/scout-engine-1.0.0.tar.gz" ]' "no tarball on mismatch"

# 3. strict mode fails when no source is reachable; lenient mode exits 0 without a tarball
sed "s/$COMMIT/ffffffffffffffffffffffffffffffffffffffff/" "$PIN" > "$TMP/pin-missing.json"
set +e; SCOUT_ENGINE_PIN="$TMP/pin-missing.json" SCOUT_ENGINE_SOURCE="$SRC" SCOUT_ENGINE_OUT="$TMP/out3" SCOUT_BUNDLE_STRICT=1 bash "$SCRIPT" 2>/dev/null; RC=$?; set -e
assert '[ "$RC" -ne 0 ]' "strict: unknown commit fails"
set +e; SCOUT_ENGINE_PIN="$TMP/pin-missing.json" SCOUT_ENGINE_SOURCE="$SRC" SCOUT_ENGINE_OUT="$TMP/out4" SCOUT_BUNDLE_STRICT=0 SCOUT_ENGINE_NO_NETWORK=1 bash "$SCRIPT" 2>/dev/null; RC=$?; set -e
assert '[ "$RC" -eq 0 ] && [ ! -d "$TMP/out4" ]' "lenient: warns and bundles nothing"

# 4. (Ruling 42) an existing tarball whose manifest version already matches the
# pin is reused as-is: no fetch, no rebuild, exit 0, even when the pin's
# commit is unreachable and networking is disabled.
OUT5="$TMP/out5"; mkdir -p "$OUT5"
FAKE="$TMP/fake-src"; mkdir -p "$FAKE/.claude-plugin"
printf '{"name": "scout", "version": "9.9.9"}\n' > "$FAKE/.claude-plugin/plugin.json"
echo "sentinel-marker-should-survive" > "$FAKE/.claude-plugin/sentinel.txt"
tar -C "$FAKE" -czf "$OUT5/scout-engine-9.9.9.tar.gz" .claude-plugin
BEFORE_HASH="$(shasum -a 256 "$OUT5/scout-engine-9.9.9.tar.gz" | awk '{print $1}')"
set +e; SCOUT_ENGINE_PIN="$TMP/pin-missing.json" SCOUT_ENGINE_SOURCE="$SRC" SCOUT_ENGINE_OUT="$OUT5" SCOUT_ENGINE_NO_NETWORK=1 bash "$SCRIPT" 2>/dev/null; RC=$?; set -e
AFTER_HASH="$(shasum -a 256 "$OUT5/scout-engine-9.9.9.tar.gz" | awk '{print $1}')"
assert '[ "$RC" -eq 0 ]' "already bundled: exits 0"
assert '[ "$BEFORE_HASH" = "$AFTER_HASH" ]' "already bundled: tarball left untouched"

# 5. (Ruling 42) a stale tarball whose manifest version does NOT match the pin
# is not reused — the script rebuilds it from the real source.
OUT6="$TMP/out6"; mkdir -p "$OUT6"
STALE="$TMP/stale-src"; mkdir -p "$STALE/.claude-plugin"
printf '{"name": "scout", "version": "0.0.1"}\n' > "$STALE/.claude-plugin/plugin.json"
tar -C "$STALE" -czf "$OUT6/scout-engine-9.9.9.tar.gz" .claude-plugin
SCOUT_ENGINE_PIN="$PIN" SCOUT_ENGINE_SOURCE="$SRC" SCOUT_ENGINE_OUT="$OUT6" bash "$SCRIPT"
assert 'tar -xzOf "$OUT6/scout-engine-9.9.9.tar.gz" .claude-plugin/plugin.json | grep -q "9.9.9"' "stale manifest version not reused: rebuilt from real source"
assert 'tar -tzf "$OUT6/scout-engine-9.9.9.tar.gz" | grep -q "^engine/x.py$"' "stale tarball replaced with the real archive"

[ "$FAILS" -eq 0 ] || exit 1
