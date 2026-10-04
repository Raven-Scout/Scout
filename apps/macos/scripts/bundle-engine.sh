#!/usr/bin/env bash
# Materialize the engine payload Scout.app ships (spec §6): a tarball of the
# scout-plugin tree at the commit pinned in Scout/Resources/engine-release.json.
#
# Reuse (Ruling 42): if <out>/scout-engine-<version>.tar.gz already exists and
# its bundled .claude-plugin/plugin.json version matches the pin, it is used
# as-is — no fetch, no rebuild. This keeps local Debug builds (which rerun
# this alwaysOutOfDate phase on every build and have no sibling ../scout-plugin)
# from re-cloning over the network each time.
#
# Otherwise, source, in order:
#   1. $SCOUT_ENGINE_SOURCE            a checkout that has the pinned commit
#   2. ../scout-plugin (sibling)       if it has the pinned commit
#   3. shallow clone of the pinned tag (network; skipped if SCOUT_ENGINE_NO_NETWORK=1)
# Output: <out>/scout-engine-<version>.tar.gz — the built product's Resources
# when run as an Xcode phase, else $SCOUT_ENGINE_OUT (default build/engine/).
#
# `git archive` ships tracked files only: never .git, .venv, or caches. The
# script only ever READS a source checkout (git cat-file, git archive) — it
# never writes into SCOUT_ENGINE_SOURCE or ../scout-plugin. The manifest
# inside the archive must match the pin or the tarball is discarded.
#
# Exit: 0 with a tarball. Without a reachable source: Release/strict → 1;
# Debug → 0 with a warning and no tarball (EngineRelease reports "not bundled").
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PIN="${SCOUT_ENGINE_PIN:-$REPO_ROOT/Scout/Resources/engine-release.json}"

read_pin() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["engine"][sys.argv[2]])' "$PIN" "$1"; }
VERSION="$(read_pin version)"; TAG="$(read_pin tag)"; COMMIT="$(read_pin commit)"; REPO="$(read_pin repo)"

STRICT="${SCOUT_BUNDLE_STRICT:-0}"
[[ "${CONFIGURATION:-}" == "Release" ]] && STRICT=1

if [[ -n "${BUILT_PRODUCTS_DIR:-}" && -n "${UNLOCALIZED_RESOURCES_FOLDER_PATH:-}" ]]; then
  OUT_DIR="$BUILT_PRODUCTS_DIR/$UNLOCALIZED_RESOURCES_FOLDER_PATH"
else
  OUT_DIR="${SCOUT_ENGINE_OUT:-$REPO_ROOT/build/engine}"
fi
OUT="$OUT_DIR/scout-engine-$VERSION.tar.gz"

manifest_version() {
  tar -xzOf "$1" .claude-plugin/plugin.json 2>/dev/null \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])' 2>/dev/null
}

# Reuse an already-bundled tarball whose manifest already matches the pin.
if [[ -f "$OUT" ]]; then
  EXISTING_VERSION="$(manifest_version "$OUT" || true)"
  if [[ "$EXISTING_VERSION" == "$VERSION" ]]; then
    echo "→ engine $VERSION already bundled at $OUT"
    exit 0
  fi
fi

fail_or_warn() {
  if [[ "$STRICT" == 1 ]]; then echo "error: $* (engine must be bundled in Release builds)" >&2; exit 1; fi
  echo "warning: $* — engine not bundled in this Debug build" >&2; exit 0
}
has_commit() { git -C "$1" cat-file -e "$COMMIT^{commit}" 2>/dev/null; }

SRC=""
if [[ -n "${SCOUT_ENGINE_SOURCE:-}" ]] && has_commit "$SCOUT_ENGINE_SOURCE"; then
  SRC="$SCOUT_ENGINE_SOURCE"
elif [[ -d "$REPO_ROOT/../scout-plugin/.git" ]] && has_commit "$REPO_ROOT/../scout-plugin"; then
  SRC="$REPO_ROOT/../scout-plugin"
elif [[ "${SCOUT_ENGINE_NO_NETWORK:-0}" != 1 ]]; then
  TMPCLONE="$(mktemp -d)"; trap 'rm -rf "$TMPCLONE"' EXIT
  if git clone --quiet --depth 1 --branch "$TAG" "https://github.com/$REPO.git" "$TMPCLONE/src" 2>/dev/null && has_commit "$TMPCLONE/src"; then
    SRC="$TMPCLONE/src"
  fi
fi
[[ -n "$SRC" ]] || fail_or_warn "no source with commit $COMMIT ($REPO@$TAG) reachable"

mkdir -p "$OUT_DIR"
git -C "$SRC" archive --format=tar.gz -o "$OUT" "$COMMIT"
GOT="$(manifest_version "$OUT")"
if [[ "$GOT" != "$VERSION" ]]; then
  rm -f "$OUT"
  echo "error: bundled plugin.json version $GOT != pinned $VERSION" >&2
  exit 1
fi
echo "→ bundled engine $VERSION ($COMMIT) → $OUT"
