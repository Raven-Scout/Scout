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

Reads are also denied of the owner's credential stores (:data:`CREDENTIAL_DIRS`, ~/.netrc) and of the
keychain and pasteboard services (:data:`DENIED_MACH`). The only terminal a script may write is its own
``/dev/tty``, never another ``/dev/ttys*`` (the owner's open terminals).

:class:`SandboxProbe` asks the kernel (``sandbox_check``) what another process's sandbox allows; the
runner uses it to find every process still holding an experiment's profile after the run.

``sandbox-exec`` can't start inside another sandbox (``sandbox_apply: Operation not permitted``,
exit 71), so a scheduled run never applies this itself: the run broker, outside the run's sandbox,
does (spec §14a, Task 11).
"""

from __future__ import annotations

import ctypes
import os
import platform
from collections.abc import Mapping, Sequence
from pathlib import Path

from scout.kb import hook_paths
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

# Under $HOME: credential stores a script has no reason to read (ruling R19). ~/.ssh comes from hook_paths.ssh_dir.
CREDENTIAL_DIRS: tuple[str, ...] = (".aws", ".config/gh", ".gnupg", "Library/Keychains")
CREDENTIAL_FILES: tuple[str, ...] = (".netrc",)
# The keychain daemons (R19) and the general pasteboard (R20), besides the run sandbox's SPAWN_MACH.
DENIED_MACH: tuple[str, ...] = ("com.apple.SecurityServer", "com.apple.securityd", "com.apple.pasteboard.1")


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
    probe_file: Path | None = None,
) -> str:
    """The SBPL text for one experiment. Raises :class:`scout.sandbox.profile.ProfileError` on a path SBPL
    can't carry.

    ``deny_reads`` are files (a ``literal``) or folders (a ``subpath``); the secrets dirs of ``env`` and of
    this process's own environment are added. ``deny_execs`` are programs (``literal``), to which the
    run sandbox's spawn routes are added. ``probe_file``, a runner-owned file in a folder the script
    can't write, gets ``file-write-data`` only (no unlink, rename or mode change): the fingerprint the
    runner's sweep asks the kernel about, which the script can't remove.
    """
    exp, tmp, sock = _real(exp_dir), _real(tmp_dir), _real(meter_sock)
    writes = [
        Rule("deny", "file-write*", "subpath", "/"),
        Rule("allow", "file-write*", "subpath", exp),
        Rule("allow", "file-write*", "subpath", tmp),
        Rule("allow", "file-write*", "literal", "/dev/null"),
        Rule("allow", "file-write*", "literal", "/dev/tty"),
    ]
    if probe_file is not None:
        writes.append(Rule("allow", "file-write-data", "literal", _real(probe_file)))
    redeny = [Rule("deny", "file-write*", "regex", rx) for rx in ANYWHERE_SUFFIXES]
    reads: list[Rule] = []
    for path in deny_reads:
        real = _real(path)
        reads.append(Rule("deny", "file-read-data", "subpath" if os.path.isdir(real) else "literal", real))
    for source in (os.environ, env if env is not None else os.environ):
        for d in secrets_dirs(source):
            reads.append(Rule("deny", "file-read-data", "subpath", _real(d)))
        reads.append(Rule("deny", "file-read-data", "subpath", _real(hook_paths.ssh_dir(source))))
        home = Path(source.get("HOME") or Path.home())
        reads += [Rule("deny", "file-read-data", "subpath", _real(home / d)) for d in CREDENTIAL_DIRS]
        reads += [Rule("deny", "file-read-data", "literal", _real(home / f)) for f in CREDENTIAL_FILES]
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
        "(deny mach-lookup\n" + "\n".join(f"  (global-name {_quote(n)})" for n in (*SPAWN_MACH, *DENIED_MACH)) + ")",
    ]
    return "\n".join(lines) + "\n"


# -- asking the kernel about another process's sandbox ------------------------------------------

_LIBSANDBOX = "/usr/lib/system/libsystem_sandbox.dylib"
_FILTER_PATH = 1  # SANDBOX_FILTER_PATH
_CHECK_NO_REPORT = 0x40000000  # SANDBOX_CHECK_NO_REPORT: don't log the probe as a violation


class SandboxProbe:
    """``sandbox_check(pid, operation, SANDBOX_FILTER_PATH, path)`` from libsystem_sandbox, through ctypes.

    ``sandbox_check`` is variadic and ctypes calls every function as fixed-argument. On arm64 Apple
    passes variadic arguments on the stack, so the path goes after five padding arguments that fill
    x3-x7; on x86-64 variadic and fixed arguments travel alike. :meth:`works` checks the call on this
    process before anything relies on it. The path must exist: for a missing one the kernel answers
    "denied" whatever the profile says.
    """

    def __init__(self) -> None:
        self._check = ctypes.CDLL(_LIBSANDBOX).sandbox_check
        self._check.restype = ctypes.c_int
        self._pad = [ctypes.c_long(0)] * (5 if platform.machine() == "arm64" else 0)

    def sandboxed(self, pid: int) -> bool:
        return int(self._check(ctypes.c_int(pid), None, ctypes.c_int(0))) == 1

    def denies(self, pid: int, operation: str, path: str) -> bool | None:
        """True denied, False allowed, None when the kernel can't say (no such process, bad arguments)."""
        got = int(
            self._check(
                ctypes.c_int(pid),
                operation.encode(),
                ctypes.c_int(_FILTER_PATH | _CHECK_NO_REPORT),
                *self._pad,
                ctypes.c_char_p(os.fsencode(path)),
            )
        )
        return {0: False, 1: True}.get(got)

    def works(self, existing_dir: str) -> bool:
        """This (unsandboxed) process is reported unsandboxed and allowed to write ``existing_dir``."""
        me = os.getpid()
        return not self.sandboxed(me) and self.denies(me, "file-write-data", existing_dir) is False
