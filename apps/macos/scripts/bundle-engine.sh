#!/usr/bin/env bash
# Materialize the engine payload Scout.app ships (unified-release spec §5):
#
#   <out>/scout-engine-<version>.tar.gz   `git archive HEAD:plugin` of the repo
#                                          containing $SRCROOT, so the archive
#                                          ROOT holds .claude-plugin/plugin.json
#   <out>/engine-release.json             generated: top-level "version" and
#                                          engine.version (both plugin.json's),
#                                          the build commit (diagnostics only)
#                                          and the checked-in uv pin
#                                          (Scout/Resources/uv-release.json)
#
# Deterministic: the payload is the COMMITTED plugin/ tree at HEAD — no pinned
# repo or commit, no sibling checkout, no network. Uncommitted or untracked
# changes under plugin/ are not shipped; the script says so with a warning.
# `<version>` is plugin.json's "version" at HEAD (one version for Scout, D2);
# when Xcode passes MARKETING_VERSION it must be the same string.
#
# <out> is the built product's Resources folder when run as an Xcode phase,
# else $SCOUT_ENGINE_OUT (default <app>/build/engine). Every run rewrites both
# files (archiving plugin/ takes ~0.1 s) and removes any other
# scout-engine-*.tar.gz there, so a version bump never leaves a stale tarball.
#
# Env (all optional): SRCROOT (Xcode; else this script's app dir),
# SCOUT_UV_PIN (default $SRCROOT/Scout/Resources/uv-release.json),
# SCOUT_ENGINE_OUT, MARKETING_VERSION, CONFIGURATION / SCOUT_BUNDLE_STRICT=1
# (Release or strict: a MARKETING_VERSION mismatch is an error, not a warning).
# Exit: 0 with both files written; 1 on any failure (no git repo, no
# plugin.json at HEAD, non-SemVer version, strict version mismatch,
# missing or malformed uv pin).
set -euo pipefail

APP_ROOT="${SRCROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
UV_PIN="${SCOUT_UV_PIN:-$APP_ROOT/Scout/Resources/uv-release.json}"
MANIFEST="plugin/.claude-plugin/plugin.json"

fail() { echo "error: bundle-engine: $*" >&2; exit 1; }

TOP="$(git -C "$APP_ROOT" rev-parse --show-toplevel 2>/dev/null)" \
  || fail "$APP_ROOT is not inside a git repository; the engine is archived from the repo's committed plugin/ tree"
COMMIT="$(git -C "$TOP" rev-parse --verify -q 'HEAD^{commit}')" \
  || fail "$TOP has no HEAD commit to archive plugin/ from"
MANIFEST_JSON="$(git -C "$TOP" show "HEAD:$MANIFEST" 2>/dev/null)" \
  || fail "HEAD has no $MANIFEST in $TOP"
VERSION="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])' <<<"$MANIFEST_JSON" 2>/dev/null)" \
  || fail "HEAD:$MANIFEST has no readable \"version\""

# The version becomes a file name here and a directory name on the user's Mac
# (engine/<version>), so hold it to SemVer's shape: no "/", no "..".
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?$ ]] \
  || fail "HEAD:$MANIFEST version \"$VERSION\" is not a SemVer X.Y.Z[-pre]"
if [[ -n "${MARKETING_VERSION:-}" && "$MARKETING_VERSION" != "$VERSION" ]]; then
  DRIFT="MARKETING_VERSION $MARKETING_VERSION != $MANIFEST version $VERSION (one version for Scout, spec D2) — set them together"
  # A Release can never ship drift; a Debug build warns and still builds (the
  # D2 test in EngineReleaseTests fails it in CI).
  if [[ "${CONFIGURATION:-}" == "Release" || "${SCOUT_BUNDLE_STRICT:-0}" == 1 ]]; then fail "$DRIFT"; fi
  echo "warning: bundle-engine: $DRIFT" >&2
fi

[[ -f "$UV_PIN" ]] || fail "uv pin $UV_PIN not found"

if [[ -n "$(git -C "$TOP" status --porcelain -- plugin)" ]]; then
  echo "warning: bundle-engine: plugin/ has uncommitted changes; bundling the committed tree at HEAD (${COMMIT:0:12}) without them" >&2
fi

if [[ -n "${BUILT_PRODUCTS_DIR:-}" && -n "${UNLOCALIZED_RESOURCES_FOLDER_PATH:-}" ]]; then
  OUT_DIR="$BUILT_PRODUCTS_DIR/$UNLOCALIZED_RESOURCES_FOLDER_PATH"
else
  OUT_DIR="${SCOUT_ENGINE_OUT:-$APP_ROOT/build/engine}"
fi
mkdir -p "$OUT_DIR"
TARBALL="$OUT_DIR/scout-engine-$VERSION.tar.gz"
RELEASE_JSON="$OUT_DIR/engine-release.json"

# Entry mtimes come from the commit, not the clock (a tree-ish archive
# otherwise stamps "now"), so the same commit yields the same bytes.
MTIME_ARGS=()
ARCHIVE_HELP="$(git -C "$TOP" archive -h 2>&1 || true)"   # `-h` exits 129
if [[ "$ARCHIVE_HELP" == *--mtime* ]]; then
  MTIME_ARGS=(--mtime="@$(git -C "$TOP" show -s --format=%ct "$COMMIT")")
fi

TMP_TARBALL="$TARBALL.tmp.$$"; TMP_JSON="$RELEASE_JSON.tmp.$$"
trap 'rm -f "$TMP_TARBALL" "$TMP_JSON"' EXIT
git -C "$TOP" archive --format=tar.gz ${MTIME_ARGS[@]+"${MTIME_ARGS[@]}"} -o "$TMP_TARBALL" "$COMMIT:plugin"

GOT="$(tar -xzOf "$TMP_TARBALL" .claude-plugin/plugin.json 2>/dev/null \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])' 2>/dev/null || true)"
[[ "$GOT" == "$VERSION" ]] || fail "archived .claude-plugin/plugin.json version \"$GOT\" != \"$VERSION\""

python3 - "$UV_PIN" "$VERSION" "$COMMIT" "$TMP_JSON" <<'PY' || fail "could not generate engine-release.json from $UV_PIN"
import json, re, sys
uv_pin, version, commit, out = sys.argv[1:5]
uv = json.load(open(uv_pin))
sha = uv.get("sha256")
if not isinstance(uv.get("version"), str) or not isinstance(sha, dict) or not sha:
    sys.exit("uv pin needs a string \"version\" and a non-empty \"sha256\" map")
if not all(isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v) for v in sha.values()):
    sys.exit("uv pin sha256 values must be 64 lowercase hex characters")
release = {
    "schema_version": 2,
    # Top-level `version` is Scout's one version (D2); `release.sh finalize`
    # requires it to equal the app's version. `engine.version` is the same
    # string, kept for EngineRelease's engine-shaped consumers.
    "version": version,
    "engine": {"version": version, "commit": commit},
    "uv": {"version": uv["version"], "sha256": sha},
}
with open(out, "w") as f:
    json.dump(release, f, indent=2, sort_keys=True)
    f.write("\n")
PY

for old in "$OUT_DIR"/scout-engine-*.tar.gz; do
  if [[ -e "$old" && "$old" != "$TARBALL" ]]; then rm -f "$old"; fi
done
mv -f "$TMP_TARBALL" "$TARBALL"
mv -f "$TMP_JSON" "$RELEASE_JSON"
echo "→ bundled engine $VERSION (plugin/ @ ${COMMIT:0:12}) → $TARBALL"
