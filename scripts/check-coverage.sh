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
# CALIBRATE THE FLOOR FROM A CI RUN, NOT A LOCAL ONE.
#
# A developer machine reports ~0.5–1 point HIGHER than the runner, because
# several probes find things locally that don't exist in CI and so take a
# different branch: AppState.resolveScoutctlPath (scoutctl on disk),
# ClaudeLauncher.resolveClaudePath (the claude CLI),
# ConnectorHealthService.loadRoster (~/scout-plugin's connectors.snapshot.json),
# and DS.serif/DS.mono (Newsreader / JetBrains Mono installed). Same code, same
# denominator — different lines executed. A floor set from a local number will
# fail on the runner the moment it merges.
#
# The floor therefore also carries headroom for incoming features: a merge that
# adds production code without proportional tests dilutes the percentage, and
# that shouldn't fail an unrelated PR.
#
# Env:
#   COVERAGE_TARGET  target to measure (default: Scout.app)
#   FLOOR_FILE       path to the floor file
#   TOP_GAPS         how many least-covered files to print (default: 15)
#   TESTS_OUTCOME    CI only: the test step's outcome ("success"/"failure").
#                    When it is not "success", a missing bundle or missing
#                    coverage data is a warning and the floor is skipped — the
#                    test step is already red, and a second red step here would
#                    only point at the wrong remedy.

set -euo pipefail

RESULT_BUNDLE="${1:-TestResults.xcresult}"
COVERAGE_TARGET="${COVERAGE_TARGET:-Scout.app}"
FLOOR_FILE="${FLOOR_FILE:-$(dirname "$0")/coverage-floor.txt}"
TOP_GAPS="${TOP_GAPS:-15}"
TESTS_OUTCOME="${TESTS_OUTCOME:-success}"

skip_when_tests_failed() {
  # $1: what is missing. Exits 0 with a warning if the tests did not succeed.
  if [ "$TESTS_OUTCOME" != "success" ]; then
    echo "::warning::$1 — the test step did not succeed (outcome: $TESTS_OUTCOME); skipping the coverage floor"
    exit 0
  fi
}

if [ ! -e "$RESULT_BUNDLE" ]; then
  skip_when_tests_failed "no result bundle at $RESULT_BUNDLE"
  echo "error: result bundle not found at $RESULT_BUNDLE" >&2
  echo "hint: run xcodebuild test with -resultBundlePath $RESULT_BUNDLE (the shared scheme enables coverage)" >&2
  exit 2
fi

if [ ! -f "$FLOOR_FILE" ]; then
  echo "error: coverage floor file not found at $FLOOR_FILE" >&2
  exit 2
fi

FLOOR="$(tr -d '[:space:]' < "$FLOOR_FILE")"

JSON="$(mktemp -t scout-coverage)"
XCCOV_ERR="$(mktemp -t scout-coverage-err)"
trap 'rm -f "$JSON" "$XCCOV_ERR"' EXIT
if ! xcrun xccov view --report --json "$RESULT_BUNDLE" > "$JSON" 2> "$XCCOV_ERR"; then
  # "No coverage data in result bundle": the tests never ran.
  skip_when_tests_failed "no coverage data in $RESULT_BUNDLE"
  echo "error: xccov could not read coverage from $RESULT_BUNDLE:" >&2
  sed 's/^/  /' "$XCCOV_ERR" >&2
  exit 2
fi

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
in_ci = bool(os.environ.get("GITHUB_ACTIONS"))
if not in_ci:
    print(
        "note: this is a local run — it reads higher than CI (host-dependent "
        "probes find scoutctl / claude / the connectors snapshot here and not "
        "on the runner). Do not set the floor from this number; use the value "
        "the CI job prints."
    )
elif headroom >= 2.0:
    # Only suggest a bump from a CI number, and leave a full point of slack so
    # the next feature merge doesn't immediately trip the raised floor.
    print(
        f"note: coverage is {headroom:.2f} points above the floor — "
        f"consider bumping the floor to {pct - 1.0:.1f} in this PR."
    )
PY
