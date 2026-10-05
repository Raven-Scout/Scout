#!/usr/bin/env bash
# changed-paths.sh <ERE pattern>
#
# Decides whether a path-scoped CI job should run, without relying on a
# `paths:` filter on the workflow's `pull_request` trigger. A job skipped by
# `paths:` filtering leaves its required status check stuck "Pending" forever
# and blocks merging; a job skipped by an `if:` conditional reports
# "Success". This script is the `if:` side of that pattern (ruling R30).
#
# Writes exactly one line, `run=true` or `run=false`, to $GITHUB_OUTPUT, and
# logs its decision plus the changed-file list to stdout.
#
# Behavior:
#   - $GITHUB_EVENT_NAME != pull_request  -> run=true, unconditionally.
#     Push events are already path-filtered by the workflow's own `on.push.
#     paths:`; workflow_call (release-plugin calling plugin-test) and
#     workflow_dispatch must always run.
#   - $GITHUB_EVENT_NAME == pull_request  -> run=true iff some file changed
#     between the PR's base and head (three-dot diff: base...head, i.e.
#     everything on the PR since it diverged from base) matches the given
#     ERE pattern. An empty changed-file list -> run=false.
#
# A pull_request event with an empty BASE_SHA or HEAD_SHA, or a failing git
# diff, is NOT treated as "no changes" — it exits non-zero so the gate job
# fails loudly instead of silently skipping a real check.
#
# Usage:   changed-paths.sh '<ERE pattern>'
# Env in:  GITHUB_EVENT_NAME (always), BASE_SHA, HEAD_SHA (pull_request only)
# Env out: GITHUB_OUTPUT

set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: $0 <ERE pattern>" >&2
  exit 1
fi

pattern="$1"

if [ "${GITHUB_EVENT_NAME:-}" != "pull_request" ]; then
  echo "event '${GITHUB_EVENT_NAME:-<unset>}' is not pull_request — running unconditionally"
  echo "run=true" >>"$GITHUB_OUTPUT"
  exit 0
fi

if [ -z "${BASE_SHA:-}" ] || [ -z "${HEAD_SHA:-}" ]; then
  echo "::error::BASE_SHA and HEAD_SHA must both be set for a pull_request event (BASE_SHA='${BASE_SHA:-}' HEAD_SHA='${HEAD_SHA:-}')" >&2
  exit 1
fi

if ! changed="$(git diff --name-only "${BASE_SHA}...${HEAD_SHA}")"; then
  echo "::error::git diff --name-only ${BASE_SHA}...${HEAD_SHA} failed" >&2
  exit 1
fi

echo "changed files (${BASE_SHA}...${HEAD_SHA}):"
if [ -n "$changed" ]; then
  echo "$changed"
else
  echo "(none)"
fi

# Match without piping into `grep -q`: under `pipefail`, `printf … | grep -q`
# can SIGPIPE the writer when grep matches early and exits, and pipefail turns
# that into a pipeline failure that an `if` reads as "no match" — a silent
# false negative. A herestring avoids the pipe entirely.
if [ -n "$changed" ] && grep -Eq -- "$pattern" <<<"$changed"; then
  echo "run=true"
  echo "run=true" >>"$GITHUB_OUTPUT"
else
  echo "run=false"
  echo "run=false" >>"$GITHUB_OUTPUT"
fi
