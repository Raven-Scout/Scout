"""The experiment sandbox: a narrower ``sandbox-exec`` profile for one experiment's scripts (spec §6.3).

The owner chose that experiment scripts be held by the kernel, not only by instructions. Under this
profile a script can write only its own experiment folder and its scratch dir, can't read the files
it's given as private (nor the secrets dirs), has no network but the meter's unix socket, and can't
start ``git``, ``claude`` or the programs that run a command outside a sandbox for their caller
(:data:`scout.sandbox.profile.SPAWN_EXECS` and friends, the run sandbox's own list).

SBPL is last-match-wins, so the order is the design: allow by default; deny every write; allow the
experiment's own folders back; deny again the git hooks/config and agent settings a nested folder
could hold (:data:`scout.sandbox.profile.ANYWHERE_SUFFIXES`); then the read, network and exec denies.
Every path is resolved with ``os.path.realpath`` first: the kernel matches resolved paths.

``sandbox-exec`` can't start inside another sandbox (``sandbox_apply: Operation not permitted``,
exit 71), so a scheduled run never applies this itself: the run broker, outside the run's sandbox,
does (spec §14a, Task 11).
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from pathlib import Path

from scout.sandbox.profile import (
    ANYWHERE_SUFFIXES,
    SPAWN_EXECS,
    SPAWN_MACH,
    Rule,
    _check_path,
    _quote,
    render_rules,
    secrets_dirs,
)


def _real(path: Path | str) -> str:
    resolved = os.path.realpath(path)
    _check_path(resolved)
    return resolved


def experiment_profile(
    exp_dir: Path,
    tmp_dir: Path,
    meter_sock: Path,
    deny_reads: Sequence[Path],
    deny_execs: Sequence[Path],
    *,
    env: Mapping[str, str] | None = None,
) -> str:
    """The SBPL text for one experiment. Raises :class:`scout.sandbox.profile.ProfileError` on a path SBPL
    can't carry.

    ``deny_reads`` are files (a ``literal``) or folders (a ``subpath``); the secrets dirs of ``env`` and of
    this process's own environment are added. ``deny_execs`` are programs (``literal``), to which the
    run sandbox's spawn routes are added.
    """
    exp, tmp, sock = _real(exp_dir), _real(tmp_dir), _real(meter_sock)
    writes = [
        Rule("deny", "file-write*", "subpath", "/"),
        Rule("allow", "file-write*", "subpath", exp),
        Rule("allow", "file-write*", "subpath", tmp),
        Rule("allow", "file-write*", "literal", "/dev/null"),
        Rule("allow", "file-write*", "regex", "^/dev/tty"),
    ]
    redeny = [Rule("deny", "file-write*", "regex", rx) for rx in ANYWHERE_SUFFIXES]
    reads: list[Rule] = []
    for path in deny_reads:
        real = _real(path)
        reads.append(Rule("deny", "file-read-data", "subpath" if os.path.isdir(real) else "literal", real))
    for source in (os.environ, env if env is not None else os.environ):
        for d in secrets_dirs(source):
            reads.append(Rule("deny", "file-read-data", "subpath", _real(d)))
    file_rules: list[Rule] = []
    seen: set[Rule] = set()
    for rule in [*writes, *redeny, *reads]:
        if rule not in seen:
            seen.add(rule)
            file_rules.append(rule)
    execs: list[str] = []
    programs: list[Path | str] = [*deny_execs, *SPAWN_EXECS]
    for program in programs:
        resolved = _real(program)
        if resolved not in execs:
            execs.append(resolved)
    lines = [
        "(version 1)",
        ";; Scout lab experiment sandbox (scout.lab.sandbox; spec 2026-11-21 §6.3). Generated: don't edit.",
        "(allow default)",
        *render_rules(file_rules),
        "(deny network*)",
        f"(allow network-outbound (remote unix-socket (path-literal {_quote(sock)})))",
        "(deny process-exec\n" + "\n".join(f"  (literal {_quote(p)})" for p in execs) + ")",
        "(deny file-mount)",
        "(deny job-creation)",
        "(deny appleevent-send)",
        "(deny mach-lookup\n" + "\n".join(f"  (global-name {_quote(n)})" for n in SPAWN_MACH) + ")",
    ]
    return "\n".join(lines) + "\n"
