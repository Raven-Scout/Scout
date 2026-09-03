#!/usr/bin/env bash
#
# Enforce a line-coverage floor for the Scout.app target.
#
# Usage:
#   scripts/check-coverage.sh [path/to/TestResults.xcresult]
#
# The floor lives in scripts/coverage-floor.txt so raising it is a reviewable
# one-line diff. It is a ratchet: when coverage rises meaningfully above the
# floor, bump the file in the same PR that earned the rise. CI fails only when
# coverage drops *below* the floor, so normal noise (a few lines either way)
# never blocks a merge.
#
# Env:
#   COVERAGE_TARGET  target to measure (default: Scout.app)
#   FLOOR_FILE       path to the floor file
#   TOP_GAPS         how many least-covered files to print (default: 15)

set -euo pipefail

RESULT_BUNDLE="${1:-TestResults.xcresult}"
COVERAGE_TARGET="${COVERAGE_TARGET:-Scout.app}"
FLOOR_FILE="${FLOOR_FILE:-$(dirname "$0")/coverage-floor.txt}"
TOP_GAPS="${TOP_GAPS:-15}"

if [ ! -e "$RESULT_BUNDLE" ]; then
  echo "error: result bundle not found at $RESULT_BUNDLE" >&2
  echo "hint: run xcodebuild test with -enableCodeCoverage YES -resultBundlePath $RESULT_BUNDLE" >&2
  exit 2
fi

if [ ! -f "$FLOOR_FILE" ]; then
  echo "error: coverage floor file not found at $FLOOR_FILE" >&2
  exit 2
fi

FLOOR="$(tr -d '[:space:]' < "$FLOOR_FILE")"

JSON="$(mktemp -t scout-coverage)"
trap 'rm -f "$JSON"' EXIT
xcrun xccov view --report --json "$RESULT_BUNDLE" > "$JSON"

COVERAGE_TARGET="$COVERAGE_TARGET" FLOOR="$FLOOR" TOP_GAPS="$TOP_GAPS" \
python3 - "$JSON" <<'PY'
import json, os, sys

target_name = os.environ["COVERAGE_TARGET"]
floor = float(os.environ["FLOOR"])
top_gaps = int(os.environ["TOP_GAPS"])

with open(sys.argv[1]) as fh:
    report = json.load(fh)

target = next((t for t in report["targets"] if t["name"] == target_name), None)
if target is None:
    names = ", ".join(t["name"] for t in report["targets"])
    sys.exit(f"error: target {target_name!r} not in report (found: {names})")

covered = target["coveredLines"]
total = target["executableLines"]
pct = 100.0 * covered / total if total else 0.0

print(f"{target_name} line coverage: {pct:.2f}%  ({covered}/{total})")
print(f"floor: {floor:.2f}%")
print()

gaps = sorted(
    ((f["executableLines"] - f["coveredLines"], f["lineCoverage"], f["name"])
     for f in target["files"] if f["executableLines"] > f["coveredLines"]),
    reverse=True,
)[:top_gaps]
if gaps:
    print(f"Largest remaining gaps (top {len(gaps)}):")
    for missing, ratio, path in gaps:
        print(f"  {missing:5d} uncovered  {ratio * 100:5.1f}%  {path.split('/')[-1]}")
    print()

# GitHub Actions job summary, when running in CI.
summary = os.environ.get("GITHUB_STEP_SUMMARY")
if summary:
    with open(summary, "a") as fh:
        status = "✅" if pct >= floor else "❌"
        fh.write(f"### {status} Coverage: {pct:.2f}% (floor {floor:.2f}%)\n\n")
        fh.write(f"`{target_name}` — {covered}/{total} lines\n\n")
        if gaps:
            fh.write("| Uncovered | Coverage | File |\n|---:|---:|---|\n")
            for missing, ratio, path in gaps:
                fh.write(f"| {missing} | {ratio * 100:.1f}% | `{path.split('/')[-1]}` |\n")

if pct < floor:
    sys.exit(
        f"error: coverage {pct:.2f}% is below the floor of {floor:.2f}%.\n"
        f"Add tests for the changed code, or — if the drop is justified — "
        f"lower {os.environ.get('FLOOR_FILE', 'scripts/coverage-floor.txt')} "
        f"with an explanation in the PR."
    )

headroom = pct - floor
if headroom >= 2.0:
    print(
        f"note: coverage is {headroom:.2f} points above the floor — "
        f"consider bumping the floor to {pct - 0.5:.1f} in this PR."
    )
PY
