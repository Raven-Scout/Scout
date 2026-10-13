#!/usr/bin/env bash
# Headless install + self-update e2e in a disposable Linux container. See README.md in this folder.
#
#   scripts/e2e/self-update/run.sh [--from TAG] [--to REF|WORKTREE] [--as-version X.Y.Z]
#                                  [--scenarios happy,rollback,sidecars] [--work DIR] [--keep] [--cpus N]
#
#   --from        the release a user has installed (default: the newest vX.Y.Z tag; 0.18.0 or later)
#   --to          what `stable` moves to: a tag (v0.18.1), a release candidate (v0.18.1-rc.1),
#                 a branch (release/v0.18.1), a commit, or WORKTREE for this checkout's uncommitted
#                 tracked changes (default: HEAD)
#   --as-version  stamp the target as this X.Y.Z before "publishing" it. Default: only when the
#                 target is not newer than --from, as patch+1 (so HEAD on main rehearses X.Y.Z+1)
#
# Touches nothing on this machine but a temp dir and one container image: no real HOME, vault,
# launchd, crontab, Keychain or notification is reachable from inside the container.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git -C "$HERE" rev-parse --show-toplevel)"
IMAGE="scout-e2e-self-update"
FROM="" TO="HEAD" AS_VERSION="" SCENARIOS="happy,rollback,sidecars" WORK="" KEEP=0 CPUS=2

die() { echo "error: $*" >&2; exit 2; }
while [ $# -gt 0 ]; do
  case "$1" in
    --from) FROM="$2"; shift 2 ;;
    --to) TO="$2"; shift 2 ;;
    --as-version) AS_VERSION="$2"; shift 2 ;;
    --scenarios) SCENARIOS="$2"; shift 2 ;;
    --work) WORK="$2"; shift 2 ;;
    --keep) KEEP=1; shift ;;
    --cpus) CPUS="$2"; shift 2 ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

if [ -n "${SCOUT_E2E_ENGINE:-}" ]; then ENGINE="$SCOUT_E2E_ENGINE"
elif command -v docker >/dev/null 2>&1; then ENGINE=docker
elif command -v podman >/dev/null 2>&1; then ENGINE=podman
else die "needs docker or podman (or run the GitHub Actions job: .github/workflows/e2e-self-update.yml)"
fi

if [ -z "$FROM" ]; then
  FROM="$(git -C "$REPO_ROOT" tag --list 'v[0-9]*.[0-9]*.[0-9]*' --sort=-v:refname | grep -v -- - | head -1)"
  [ -n "$FROM" ] || die "no vX.Y.Z tag found; pass --from (CI: check out with fetch-depth: 0)"
fi
from_sha="$(git -C "$REPO_ROOT" rev-parse --verify "$FROM^{commit}")" || die "unknown --from $FROM"
if [ "$TO" = WORKTREE ]; then
  # `git stash create` writes a commit object for the tracked changes and touches no ref or stash entry.
  to_sha="$(git -C "$REPO_ROOT" stash create)"
  [ -n "$to_sha" ] || to_sha="$(git -C "$REPO_ROOT" rev-parse HEAD)"
else
  to_sha="$(git -C "$REPO_ROOT" rev-parse --verify "$TO^{commit}")" || die "unknown --to $TO"
fi
[ "$from_sha" != "$to_sha" ] || [ -n "$AS_VERSION" ] || echo "note: --to is the --from commit; the target will be stamped patch+1"

WORK="${WORK:-$(mktemp -d "${TMPDIR:-/tmp}/scout-e2e.XXXXXX")}"
mkdir -p "$WORK"
# The container runs as its own uid; the scratch dir is disposable, so let it write.
chmod a+rwx "$WORK"
rm -rf "$WORK/src.git"
git init -q --bare "$WORK/src.git"
# A push INTO the scratch repo: this checkout's refs are read, never written.
git -C "$REPO_ROOT" push -q "$WORK/src.git" "$from_sha:refs/heads/e2e-from" "$to_sha:refs/heads/e2e-target"
chmod -R a+rwX "$WORK/src.git"

echo "e2e: $FROM ($from_sha) -> $TO ($to_sha) in $WORK using $ENGINE"
nice -n 10 "$ENGINE" build -q -t "$IMAGE" "$HERE" >/dev/null

args=(--scenarios "$SCENARIOS")
[ -z "$AS_VERSION" ] || args+=(--as-version "$AS_VERSION")
status=0
nice -n 10 "$ENGINE" run --rm --cpus "$CPUS" --network bridge \
  -v "$REPO_ROOT:/repo:ro" -v "$WORK:/work" \
  "$IMAGE" python3 /repo/scripts/e2e/self-update/e2e.py "${args[@]}" || status=$?

if [ "$KEEP" = 0 ]; then
  # Container-owned files (homes, venvs, uv caches) are removed from inside, whatever the host uid.
  "$ENGINE" run --rm -v "$WORK:/work" --entrypoint sh "$IMAGE" -c \
    'rm -rf /work/homes /work/state /work/build /work/remote.git /work/src.git /work/src-from /work/uv-cache /work/uv-python' \
    || true
fi
echo "e2e: report $WORK/report.json, logs $WORK/logs/ (exit $status)"
exit "$status"
