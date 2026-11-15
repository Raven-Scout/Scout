"""The scheduled runs' OS write sandbox: a macOS sandbox-exec profile built from hook_paths (Phase 1c).

Spec ``docs/superpowers/specs/2026-11-15-run-write-sandbox-design.md``. Scout's gates are
hooks (Claude Code's and git's) and the engine they run; #637's hook layer keeps a run's
tools from writing what decides whether they run, but a hook can't parse every shell or
inline program and a timed-out hook allows. Here the kernel refuses the write instead,
for ``claude`` and every process under it, whatever program makes it.

The profile is generated at each launch, outside the sandbox, from
:func:`scout.kb.hook_paths.protected_specs` (the one protected-path list both layers use),
plus the few kernel-only carve-outs trusted programs need (:data:`GIT_CARVE_DIRS`,
:data:`GIT_CARVE_FILES`, ``plugins/data``) and the ways around a sandbox (§3). SBPL is
last-match-wins, so the order of :func:`build_rules` is the design: deny the protected
roots, allow the carve-outs, deny again whatever protected root lies under a carve-out.

:func:`decide` is a Python model of the same rules; the replay
(:mod:`scout.sandbox.replay`) uses it, and a macOS test checks it against the kernel.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from scout.kb import hook_paths

SANDBOX_EXEC = Path("/usr/bin/sandbox-exec")

# What git writes under the git dir for commit, checkout, switch, restore, stash, reset, fetch, push, tag, rm,
# mv, merge, rebase, cherry-pick and gc (spec §6). An allowlist: any other file git adds later stays denied.
GIT_CARVE_DIRS: tuple[str, ...] = ("objects", "refs", "logs", "rebase-merge", "rebase-apply", "sequencer", "rr-cache")
GIT_CARVE_FILES: tuple[str, ...] = (
    "index",
    "HEAD",
    "ORIG_HEAD",
    "FETCH_HEAD",
    "MERGE_HEAD",
    "MERGE_MSG",
    "MERGE_MODE",
    "MERGE_RR",
    "AUTO_MERGE",
    "CHERRY_PICK_HEAD",
    "REVERT_HEAD",
    "REBASE_HEAD",
    "BISECT_LOG",
    "COMMIT_EDITMSG",
    "SQUASH_MSG",
    "TAG_EDITMSG",
    "packed-refs",
    "gc.pid",
    "gc.log",
    "shallow",
)
# Git's per-process temp files in the git dir: a partial commit's next index (`git commit <paths>`, what
# git-safe-commit.sh does) and stash's scratch index. Regexes, digits only: SBPL matches regexes
# case-insensitively here too (spec §5.4), which a digit can't exploit.
GIT_CARVE_REGEXES: tuple[str, ...] = (r"next-index-[0-9]+\.lock", r"index\.stash\.[0-9]+(\.lock)?")
# Under a carve-out but still denied: another object store git would read, and git's own pointers and config
# (belt: they're not in the carve-outs, but matching here is case-insensitive, spec §5.4).
GIT_REDENY: tuple[str, ...] = (
    "objects/info/alternates",
    "objects/info/http-alternates",
    "config",
    "config.lock",
    "config.worktree",
    "commondir",
    "gitdir",
)
# Claude Code's CLAUDE_PLUGIN_DATA: plugins and their MCP servers keep their state here (Q4, Jordan 2026-11-15).
PLUGIN_DATA = "data"

# ~/.scout-secrets: no reads of the contents, no writes (#636's review). Metadata (stat, `test -s`) stays allowed.
# The Telegram wrap is sent from inside the session (phases/connectors/telegram.md, 18 of the last 50 runs), so
# its two files stay readable until a send-only broker exists (spec Q8).
SECRETS_READABLE: tuple[str, ...] = hook_paths.TELEGRAM_SECRETS

# The ways out of a sandbox (spec §3): a daemon or service that runs a command for its client, outside it.
SPAWN_EXECS: tuple[str, ...] = ("/bin/launchctl", "/usr/bin/open", "/usr/bin/osascript")
SPAWN_MACH: tuple[str, ...] = ("com.apple.coreservices.launchservicesd",)
SPAWN_SOCKETS: tuple[str, ...] = (r"docker\.sock$", r"^/private/tmp/tmux-[0-9]+/", r"/\.?screens?/")
SPAWN_WRITE_TREES: tuple[str, ...] = ("/private/var/at", "/usr/lib/cron")
LOOPBACK_SSH = "localhost:22"

SELF_TEST_TIMEOUT_S = 10.0


class ProfileError(Exception):
    """The profile can't be built safely (a path SBPL can't carry, an unreadable list)."""


@dataclass(frozen=True)
class Rule:
    action: str  # "allow" | "deny"
    op: str  # "file-write*" | "file-write-unlink" | "file-read-data" | "file-read*"
    kind: str  # "subpath" | "literal" | "regex" (path is then an anchored SBPL regex)
    path: str


def _forms(path: str, env: Mapping[str, str]) -> list[str]:
    """``path`` resolved (what the kernel matches) and as spelled (a protected symlink itself), absolute."""
    expanded = hook_paths._expand(path, env)
    out: list[str] = []
    for form in (os.path.realpath(expanded), os.path.normpath(os.path.abspath(expanded))):
        if form not in out:
            out.append(form)
    return out


def _git_dirs(vault: Path) -> list[Path]:
    return hook_paths.git_dirs(vault)


def _claude_dirs(env: Mapping[str, str]) -> list[Path]:
    home = Path(env.get("HOME") or Path.home())
    dirs = [home / ".claude"]
    if env.get("CLAUDE_CONFIG_DIR"):
        dirs.append(Path(env["CLAUDE_CONFIG_DIR"]))
    return dirs


def secrets_dirs(env: Mapping[str, str]) -> list[Path]:
    """~/.scout-secrets and ``$SCOUT_SECRETS_DIR``: :func:`scout.kb.hook_paths.secrets_dirs`, the one definition both
    layers use (it reads the variable :func:`scout.linear_activity.secrets_dir` reads)."""
    return hook_paths.secrets_dirs(env)


def _under(path: str, root: str) -> bool:
    p, r = path.casefold(), root.casefold().rstrip("/")
    return p == r or p.startswith(r + "/")


def carve_outs(vault: Path | None, env: Mapping[str, str]) -> list[tuple[str, str]]:
    """(kind, path) of each kernel-only allow under a protected root (spec §5.3)."""
    out: list[tuple[str, str]] = []
    if vault is not None:
        for gitdir in _git_dirs(vault):
            for name in GIT_CARVE_DIRS:
                out.append(("subpath", str(gitdir / name)))
            for name in GIT_CARVE_FILES:
                out.append(("literal", str(gitdir / name)))
                out.append(("literal", str(gitdir / f"{name}.lock")))
            for rx in GIT_CARVE_REGEXES:
                for form in _forms(str(gitdir), env):
                    out.append(("regex", f"^{_rx_escape(form)}/{rx}$"))
    for base in _claude_dirs(env):
        out.append(("subpath", str(base / "plugins" / PLUGIN_DATA)))
    return out


def build_rules(vault: Path | None, env: Mapping[str, str] | None = None) -> list[Rule]:
    """The file rules, in profile order (last match wins). Raises :class:`ProfileError`."""
    env = os.environ if env is None else env
    try:
        specs = hook_paths.protected_specs(vault, env)
    except Exception as e:  # noqa: BLE001 - no list, no profile: the run is skipped, never run unprotected
        raise ProfileError(f"couldn't build the protected-path list: {e}") from e
    if not specs:
        raise ProfileError("the protected-path list is empty")
    rules: list[Rule] = []
    protected: list[Rule] = []
    for spec in specs:
        for form in _forms(spec.path, env):
            if _device(form):
                # GIT_CONFIG_GLOBAL=/dev/null names no file a run could change; denying it breaks `> /dev/null`.
                continue
            protected.append(Rule("deny", "file-write*", "subpath" if spec.tree else "literal", form))
    secrets: list[Rule] = []
    readable: list[Rule] = []
    for d in secrets_dirs(env):
        for form in _forms(str(d), env):
            secrets.append(Rule("deny", "file-write*", "subpath", form))
            secrets.append(Rule("deny", "file-read-data", "subpath", form))
            # The allow names the deny's own operation: sandbox-exec doesn't let a broader file-read* allow
            # override a file-read-data deny (tests/integration/test_run_sandbox_kernel.py).
            readable += [Rule("allow", "file-read-data", "literal", f"{form}/{name}") for name in SECRETS_READABLE]
            # Listing the folder shows names, not secrets: runs check `ls ~/.scout-secrets/ | grep telegram`.
            readable.append(Rule("allow", "file-read-data", "literal", form))
    rules += protected + secrets
    # 2. the carve-outs
    allows: list[Rule] = []
    for kind, path in carve_outs(vault, env):
        for form in [path] if kind == "regex" else _forms(path, env):
            allows.append(Rule("allow", "file-write*", kind, form))
    rules += allows
    # 3. deny again: git's own pointers and config, and every protected root under a carve-out
    redeny: list[Rule] = []
    if vault is not None:
        for gitdir in _git_dirs(vault):
            for rel in GIT_REDENY:
                for form in _forms(str(gitdir / rel), env):
                    redeny.append(Rule("deny", "file-write*", "literal", form))
    for rule in protected:
        if any(
            a.kind != "regex"
            and _under(rule.path, a.path)
            and (a.kind == "subpath" or a.path.casefold() == rule.path.casefold())
            for a in allows
        ):
            redeny.append(rule)
    rules += redeny + readable
    # 4. ancestors: no rename or unlink of a folder that holds a protected root (or the secrets)
    ancestors: list[str] = []
    for rule in [*protected, *secrets]:
        parent = os.path.dirname(rule.path.rstrip("/"))
        while parent and parent != "/":
            if parent not in ancestors:
                ancestors.append(parent)
            parent = os.path.dirname(parent)
    rules += [Rule("deny", "file-write-unlink", "literal", a) for a in ancestors]
    rules += [Rule("deny", "file-write*", "subpath", p) for p in SPAWN_WRITE_TREES]
    # Drop repeats, keeping each rule's LAST copy: with last-match-wins only that one decides (a protected root
    # denied again after a carve-out must stay after it).
    seen: set[Rule] = set()
    ordered: list[Rule] = []
    for rule in reversed(rules):
        _check_path(rule.path)
        if rule not in seen:
            seen.add(rule)
            ordered.append(rule)
    return ordered[::-1]


def _device(path: str) -> bool:
    return path == "/dev" or path.startswith("/dev/")


_RX_META = set("\\.^$*+?()[]{}|")


def _rx_escape(path: str) -> str:
    if '"' in path:
        raise ProfileError(f"a git dir path SBPL can't carry in a regex: {path!r}")
    return "".join("\\" + c if c in _RX_META else c for c in path)


def _check_path(path: str) -> None:
    if path.startswith("^"):
        path = path[1:]
    if not path.startswith("/"):
        raise ProfileError(f"not an absolute path: {path!r}")
    if any(ord(c) < 0x20 or c == "\x7f" for c in path):
        raise ProfileError(f"a protected path holds a control character: {path!r}")


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render(rules: Sequence[Rule], *, network: str = "") -> str:
    """The SBPL text: ``(allow default)``, ``rules`` in order (runs of one action+op share a form), the spawn denies."""
    lines = [
        "(version 1)",
        ";; Scout scheduled-run write sandbox (scout.sandbox.profile; spec 2026-11-15). Generated: don't edit.",
        "(allow default)",
    ]
    i = 0
    while i < len(rules):
        j = i
        while j < len(rules) and (rules[j].action, rules[j].op) == (rules[i].action, rules[i].op):
            j += 1
        filters = "\n".join(
            f'  (regex #"{r.path}")' if r.kind == "regex" else f"  ({r.kind} {_quote(r.path)})" for r in rules[i:j]
        )
        lines.append(f"({rules[i].action} {rules[i].op}\n{filters})")
        i = j
    lines += [
        "(deny file-mount)",
        "(deny job-creation)",
        "(deny appleevent-send)",
        "(deny process-exec\n" + "\n".join(f"  (literal {_quote(p)})" for p in SPAWN_EXECS) + ")",
        "(deny mach-lookup\n" + "\n".join(f"  (global-name {_quote(n)})" for n in SPAWN_MACH) + ")",
        "(deny network-outbound\n"
        + "\n".join(f'  (remote unix-socket (path-regex #"{rx}"))' for rx in SPAWN_SOCKETS)
        + f"\n  (remote tcp {_quote(LOOPBACK_SSH)}))",
    ]
    if network:
        lines.append(network)
    return "\n".join(lines) + "\n"


def build_profile(vault: Path | None, env: Mapping[str, str] | None = None, *, network: str = "") -> str:
    return render(build_rules(vault, env), network=network)


# -- the model -----------------------------------------------------------------------------------------------------

_OPS = {
    "write": ("file-write*",),
    "unlink": ("file-write*", "file-write-unlink"),
    "read": ("file-read-data",),
}


def _matches(rule: Rule, path: str) -> bool:
    if rule.kind == "regex":
        return re.search(rule.path, path, re.IGNORECASE) is not None
    if rule.kind == "literal":
        return path.casefold() == rule.path.casefold()
    return _under(path, rule.path)


def decide(rules: Iterable[Rule], path: str, op: str = "write") -> str:
    """ "allow" or "deny" for ``op`` ("write", "unlink", "read") on the resolved absolute ``path``: last match wins,
    case-insensitively (APFS), the default is allow. Models the file rules only, not the spawn denies."""
    verdict = "allow"
    ops = _OPS[op]
    for rule in rules:
        if rule.op in ops and _matches(rule, path):
            verdict = rule.action
    return verdict


# -- applying it -----------------------------------------------------------------------------------------------------


def available() -> bool:
    return sys.platform == "darwin" and os.access(SANDBOX_EXEC, os.X_OK)


def self_test(profile: str, *, timeout_s: float = SELF_TEST_TIMEOUT_S) -> str | None:
    """None when ``profile`` applies and refuses a write it denies while allowing an ordinary one, else why not."""
    with tempfile.TemporaryDirectory(prefix="scout-sandbox-selftest-") as tmp:
        root = os.path.realpath(tmp)
        canary, ordinary = f"{root}/canary", f"{root}/ordinary"
        probe = profile + f"(deny file-write* (literal {_quote(canary)}))\n"
        script = 'echo x > "$1" 2>/dev/null && exit 9; echo y > "$2" || exit 8; exit 0'
        try:
            proc = subprocess.run(
                [str(SANDBOX_EXEC), "-p", probe, "/bin/sh", "-c", script, "sh", canary, ordinary],
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError) as e:
            return f"sandbox-exec didn't run: {e}"
        if proc.returncode == 9 or os.path.exists(canary):
            return "the sandbox let through a write its profile denies"
        if proc.returncode == 8 or not os.path.exists(ordinary):
            return "the sandbox refused an ordinary temp-file write"
        if proc.returncode != 0:
            return f"sandbox-exec failed (exit {proc.returncode}): {proc.stderr.strip()[-300:]}"
    return None


# -- Claude Code's own Bash sandbox (can't nest inside this one, spec §4) -------------------------------------------


def claude_sandbox_enabled(vault: Path | None, env: Mapping[str, str] | None = None) -> bool:
    """Whether the effective Claude Code settings turn on its Bash sandbox (user < project < local < managed)."""
    from scout.agent_runtimes import gate_probe

    env = os.environ if env is None else env
    config = gate_probe.claude_config_dir(env)
    files = [config / "settings.json"]
    if vault is not None:
        files += [vault / ".claude" / "settings.json", vault / ".claude" / "settings.local.json"]
    files += gate_probe.managed_files()
    enabled = False
    for path in files:
        data = gate_probe._load_settings(path)
        block = data.get("sandbox") if data is not None else None
        if isinstance(block, Mapping) and isinstance(block.get("enabled"), bool):
            enabled = block["enabled"]
    return enabled


def to_json(rules: Sequence[Rule]) -> str:
    return json.dumps([r.__dict__ for r in rules], indent=2)
