"""Install the vault's git pre-commit hook, which runs `scoutctl kb lint --staged`.

When the privacy check can't run (#453, spec §8.3; Jordan, 2026-10-22): a
missing scoutctl, a crash before ``cli.main`` (exit 1 with no ``kb-lint:``
line), or any exit other than 0 (pass) or 1 with a ``kb-lint:`` verdict, the
hook prints ``kb-lint: privacy check could not run`` and then:

- **a scheduled run's commit is refused** (``SCOUT_MODE`` set, or
  ``SCOUT_READER`` set to anything but exactly ``owner``, or a live run holding
  the session lock with its run marker, whatever the environment says:
  ``scout.kb.live_run``, #595); the run's retry/park handling applies;
- **the owner's interactive commit goes on**, with a loud ``kb-lint: WARNING``
  naming the problem and ``scoutctl bootstrap doctor``, and an audit row
  (``event: "privacy.check_unavailable"``, no content) appended to
  ``.scout-logs/kb-reads.jsonl``, which the doctor counts.

Inside scoutctl, ``scout.kb.lint.run_hook`` does the same for a privacy
checker that errors: a blocking ``privacy-check-failed`` finding (plus the
``privacy.check_failed`` event) for a run, the warning and the audit row for
the owner. The size and diary checks, and the lint's own logging, always fail
OPEN: a crash there prints ``kb-lint: size/diary check failed (<Error>) — not
blocking``.

Install, upgrade and migrate (``bootstrap``'s git-hook stage) and
``scoutctl kb install-hook`` rewrite a Scout-managed hook, so ``/scout-update``
brings every vault the current script.
"""

from __future__ import annotations

import shlex
import shutil
import sys
from pathlib import Path

from scout.kb.git_io import git

MARKER = "# scout-managed: kb-lint pre-commit hook"

_TEMPLATE = """#!/bin/sh
{marker}
# Installed by `scoutctl kb install-hook`. Blocks commits that put a private
# note into a shared file, scheduled-run commits that widen privacy, and (in
# block mode) commits that grow an over-budget KB file or add run-diary
# headings. See `scoutctl kb lint --help`.
#
# If the check can't run (no scoutctl, an engine crash, any exit but 0 or a
# 'kb-lint:' verdict with 1): a scheduled run's commit is refused; the owner's
# commit goes on with a warning and an audit row (#453, spec 8.3).
SCOUTCTL={scoutctl}

vault_root() {{
  common="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"
  if [ -n "$common" ]; then
    dirname "$common"
  else
    git rev-parse --show-toplevel 2>/dev/null
  fi
}}

# A scheduled run's runner holds the session lock and writes its run marker
# beside it (scripts/scout-lock.sh). While the lock's PID is alive and the
# marker names it (or nothing parseable), the commit is the run's, whatever its
# environment says (#595; scout.kb.live_run).
live_run() {{
  root="$(vault_root)" && [ -n "$root" ] || return 1
  lock="$root/.scout-logs/.scout-session.lock"
  [ -f "$lock.run" ] || return 1
  pid="$(tr -d ' \\t\\r\\n' <"$lock" 2>/dev/null)"
  case "$pid" in ''|*[!0-9]*) return 1 ;; esac
  [ "$pid" -gt 0 ] && {{ kill -0 "$pid" 2>/dev/null || ps -p "$pid" >/dev/null 2>&1; }} || return 1
  run_pid="$(head -n 1 "$lock.run" 2>/dev/null)"
  run_pid="${{run_pid%% *}}"
  case "$run_pid" in ''|*[!0-9]*) return 0 ;; esac
  [ "$run_pid" -eq "$pid" ] 2>/dev/null
}}

scheduled() {{
  [ -n "$SCOUT_MODE" ] || {{ [ -n "${{SCOUT_READER+set}}" ] && [ "$SCOUT_READER" != "owner" ]; }} || live_run
}}

audit_unavailable() {{
  root="$(vault_root)" && [ -n "$root" ] || return 0
  ts="$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"
  ROW='{{"args_hash": null, "blocked": false, "command": "kb lint --staged", "event": "privacy.check_unavailable", '
  ROW="$ROW"'"paths_denied": 0, "paths_returned": 0, "private_paths": 0, "reader": "owner", '
  ROW="$ROW"'"scout_mode": null, "surface": null, "ts": "'"$ts"'"}}'
  (
    umask 077
    mkdir -p "$root/.scout-logs" &&
      printf '%s\\n' "$ROW" >>"$root/.scout-logs/kb-reads.jsonl"
  ) 2>/dev/null || true
}}

cannot_run() {{
  echo "kb-lint: privacy check could not run" >&2
  if scheduled; then
    exit 1
  fi
  echo "kb-lint: WARNING: the privacy check could not run ($1); this commit was NOT checked" \\
    "for private links in shared files. Run scoutctl bootstrap doctor." >&2
  audit_unavailable
  exit 0
}}

if [ ! -x "$SCOUTCTL" ]; then
  SCOUTCTL="$(command -v scoutctl 2>/dev/null)"
fi
if [ -z "$SCOUTCTL" ]; then
  cannot_run "scoutctl not found"
fi
output="$("$SCOUTCTL" kb lint --staged 2>&1)"
rc=$?
[ -n "$output" ] && printf '%s\\n' "$output" >&2
if [ "$rc" -eq 0 ]; then
  exit 0
fi
if [ "$rc" -eq 1 ] && printf '%s\\n' "$output" | grep -q '^kb-lint:'; then
  exit 1
fi
cannot_run "engine error $rc"
"""


class HookConflict(Exception):
    """A non-Scout pre-commit hook already exists; we refuse to clobber it."""


def default_scoutctl() -> str:
    beside = Path(sys.executable).parent / "scoutctl"
    if beside.exists():
        return str(beside)
    return shutil.which("scoutctl") or "scoutctl"


def hooks_dir(repo: Path) -> Path:
    out = Path(git(repo, "rev-parse", "--git-path", "hooks").stdout.strip())
    return out if out.is_absolute() else (repo / out).resolve()


def render_hook(scoutctl: str) -> str:
    return _TEMPLATE.format(marker=MARKER, scoutctl=shlex.quote(scoutctl))


def install_hook(repo: Path, *, scoutctl: str | None = None) -> Path:
    d = hooks_dir(repo)
    d.mkdir(parents=True, exist_ok=True)
    target = d / "pre-commit"
    if target.exists() and MARKER not in target.read_text(encoding="utf-8", errors="replace"):
        raise HookConflict(f"{target} exists and is not Scout-managed — add `scoutctl kb lint --staged` to it by hand")
    target.write_text(render_hook(scoutctl or default_scoutctl()), encoding="utf-8")
    target.chmod(0o755)
    return target
