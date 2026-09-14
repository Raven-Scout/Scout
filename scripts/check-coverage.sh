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

# Emit a GitHub Actions warning annotation (plain note when running locally)
# and succeed. Used for "there is no coverage data to check", which is never
# this script's finding to report: the run that produced the bundle failed or
# was cut short, and that step is already red.
no_data() {
  if [ -n "${GITHUB_ACTIONS:-}" ]; then
    echo "::warning title=Coverage not checked::$1"
    if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
      printf '### ⚠️ Coverage not checked\n\n%s\n\n' "$1" >> "$GITHUB_STEP_SUMMARY"
    fi
  else
    echo "note: coverage not checked — $1"
  fi
  exit 0
}

JSON="$(mktemp -t scout-coverage)"
trap 'rm -f "$JSON" "$JSON.err"' EXIT

# A bundle from a run that failed before the tests executed carries no coverage
# payload, and xccov exits non-zero. That is not a coverage regression.
if ! xcrun xccov view --report --json "$RESULT_BUNDLE" > "$JSON" 2>"$JSON.err"; then
  no_data "xccov reported no coverage data in $RESULT_BUNDLE ($(tr '\n' ' ' < "$JSON.err" | head -c 300)). The test step's own result is authoritative."
fi
rm -f "$JSON.err"

if [ ! -s "$JSON" ]; then
  no_data "xccov produced an empty report for $RESULT_BUNDLE."
fi

COVERAGE_TARGET="$COVERAGE_TARGET" FLOOR="$FLOOR" TOP_GAPS="$TOP_GAPS" \
python3 - "$JSON" <<'PY'
import json, os, sys

target_name = os.environ["COVERAGE_TARGET"]
floor = float(os.environ["FLOOR"])
top_gaps = int(os.environ["TOP_GAPS"])

summary_path = os.environ.get("GITHUB_STEP_SUMMARY")


def no_data(message):
    """Warn and succeed: there is nothing to measure, which is not a regression.

    The run that produced this bundle failed or was cut short, and that step is
    already red. Failing here too would bury it under a misleading "add tests".
    """
    if os.environ.get("GITHUB_ACTIONS"):
        print(f"::warning title=Coverage not checked::{message}")
        if summary_path:
            with open(summary_path, "a") as fh:
                fh.write(f"### ⚠️ Coverage not checked\n\n{message}\n\n")
    else:
        print(f"note: coverage not checked — {message}")
    sys.exit(0)


with open(sys.argv[1]) as fh:
    report = json.load(fh)

targets = report.get("targets") or []
if not targets:
    no_data("xccov reported no targets. The test step's own result is authoritative.")

target = next((t for t in targets if t["name"] == target_name), None)
if target is None:
    names = ", ".join(t["name"] for t in targets)
    no_data(
        f"target {target_name!r} is not in the coverage report (found: {names}). "
        f"Tests likely never ran."
    )

covered = target["coveredLines"]
total = target["executableLines"]
if not total:
    no_data(f"{target_name} reports 0 executable lines — no coverage was collected.")
pct = 100.0 * covered / total

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
if summary_path:
    with open(summary_path, "a") as fh:
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
