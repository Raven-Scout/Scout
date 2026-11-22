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

Reads under each home folder are denied by default (ruling R32), with only the folders a script needs
allowed back (``allow_reads``: the vault, the engine, the interpreters); the private-file and credential
denies come after that allow-back, so they hold inside it. Reads are also denied of the owner's credential
stores (:data:`CREDENTIAL_DIRS`, ~/.netrc) and of the keychain, pasteboard and download services
(:data:`DENIED_MACH`). The only terminal a script may open is its own ``/dev/tty``, never another
``/dev/ttys*`` or ``/dev/pty*`` (the owner's open terminals), to read or to write.

A script may signal, and read the process information (arguments, environment, path) of, only processes
holding its own sandbox: its own children. It can't stop or kill the runner that supervises it, nor any
other process of the owner's (an editor, the Scout app), nor read the runner's environment, which holds
the model-call token. Exec of a setuid program such as ``ps`` is refused by the kernel in any sandbox.

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
# The keychain daemons (R19), the general pasteboard (R20) and the background-download daemon (it fetches a URL
# for its caller, outside the caller's sandbox), besides the run sandbox's SPAWN_MACH.
DENIED_MACH: tuple[str, ...] = (
    "com.apple.SecurityServer",
    "com.apple.securityd",
    "com.apple.pasteboard.1",
    "com.apple.nsurlsessiond",
)
# The owner's other terminals: a script may open neither to read (what is typed there) nor to write. Its own
# /dev/tty is a literal, which these don't match.
OTHER_TERMINALS: tuple[str, ...] = ("^/dev/ttys[0-9]+$", "^/dev/pty")
# The sysctl that hands out another process's arguments and environment (KERN_PROCARGS2). The kernel also asks
# process-info* about its target, so a script's own children stay readable (checked on macOS 26).
PROCARGS_SYSCTL = "kern.procargs"


def _real(path: Path | str) -> str:
    resolved = os.path.realpath(path)
    _check_path(resolved)
    return resolved


def homes(env: Mapping[str, str] | None) -> list[str]:
    """The resolved home folders of this process's environment and of ``env``: the ones whose reads are denied
    by default. A home that resolves to ``/`` is left out (denying it would deny every read)."""
    out: list[str] = []
    for source in (os.environ, env if env is not None else os.environ):
        home = _real(Path(source.get("HOME") or Path.home()))
        if home != "/" and home not in out:
            out.append(home)
    return out


def experiment_profile(
    exp_dir: Path,
    tmp_dir: Path,
    meter_sock: Path,
    deny_reads: Sequence[Path],
    deny_execs: Sequence[Path],
    *,
    env: Mapping[str, str] | None = None,
    probe_file: Path | None = None,
    allow_reads: Sequence[Path] = (),
) -> str:
    """The SBPL text for one experiment. Raises :class:`scout.sandbox.profile.ProfileError` on a path SBPL
    can't carry.

    Reads under the home folders (:func:`homes`) are denied, except below ``allow_reads`` (folders) and the
    experiment's own two folders. ``deny_reads`` are files (a ``literal``) or folders (a ``subpath``) denied
    after that, wherever they are; the secrets dirs and credential stores of ``env`` and of this process's
    own environment are added. ``deny_execs`` are programs (``literal``), to which the run sandbox's spawn
    routes are added. ``probe_file``, a runner-owned file in a folder the script can't write, gets
    ``file-write-data`` only (no unlink, rename or mode change): the fingerprint the runner's sweep asks the
    kernel about, which the script can't remove.
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
    redeny += [Rule("deny", op, "regex", rx) for op in ("file-read*", "file-write*") for rx in OTHER_TERMINALS]
    # R32: nothing under a home folder is readable unless it's allowed back; the denies below still apply inside.
    home_reads = [Rule("deny", "file-read-data", "subpath", home) for home in homes(env)]
    home_reads += [Rule("allow", "file-read-data", "subpath", _real(p)) for p in [*allow_reads, exp_dir, tmp_dir]]
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
    for rule in [*writes, *redeny, *home_reads, *reads]:
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
        # I1/I5: signals and process information only within this sandbox (the script and its children).
        "(deny signal)",
        "(allow signal (target same-sandbox))",
        "(deny process-info*)",
        "(allow process-info* (target same-sandbox))",
        f"(deny sysctl-read (sysctl-name-prefix {_quote(PROCARGS_SYSCTL)}))",
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
