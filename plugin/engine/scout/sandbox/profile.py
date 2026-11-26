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

from scout.agent_runtimes.base import SandboxPaths
from scout.kb import hook_paths

SANDBOX_EXEC = Path("/usr/bin/sandbox-exec")

# What git writes under the git dir for commit, checkout, switch, restore, stash, reset, fetch, push, tag, rm,
# mv, merge and gc (spec §6), plus git-lfs's object store. An allowlist: any other file git adds later stays
# denied. No rebase-merge/, rebase-apply/ or sequencer/: none of the last 50 scheduled runs used them (review of
# #642), so a rebase or a multi-commit cherry-pick inside a run fails and leaves the repo as it was.
GIT_CARVE_DIRS: tuple[str, ...] = ("objects", "refs", "logs", "rr-cache", "lfs")
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
# ~/.claude (and $CLAUDE_CONFIG_DIR) is denied as a whole, like the git dir, and only what Claude Code writes for a
# session is allowed back (spec §5.2): its transcripts and per-session state, and the plugins' data dir (Q4). A
# file or folder Claude Code adds later stays denied until it is listed here.
CLAUDE_HOME_CARVE_DIRS: tuple[str, ...] = (
    "projects",
    "session-env",
    "sessions",
    "shell-snapshots",
    "backups",
    "todos",
    "file-history",
    "statsig",
    "debug",
    f"plugins/{PLUGIN_DATA}",
)
# The vault's own .claude/ is denied as a whole too (skills, commands, agents, hooks and settings alike); a session
# writes nothing there.
VAULT_CLAUDE_DIR = ".claude"
# Any git repo's hooks and config and any project's agent settings, wherever they are (another repo, the dev
# checkout, a clone in a temp dir): literal suffixes, no character classes, so case-insensitive matching (§5.4)
# only ever widens them. Review of #642.
ANYWHERE_SUFFIXES: tuple[str, ...] = (
    r"/\.git/hooks/",
    r"/\.git/config$",
    r"/\.claude/settings\.json$",
    r"/\.claude/settings\.local\.json$",
    r"/\.mcp\.json$",
)
# Another agent CLI's home folder (hook_paths.AGENT_DIRS) is protected whole and holds its hooks and config. A
# runtime may carve out only these exact data folders there, by its registered id, each relative to every home
# (review of #806): its adapter can't choose, or a carve-out could re-open ~/.codex/hooks or ~/.gemini/settings.json.
AGENT_DATA_DIRS: Mapping[str, tuple[str, ...]] = {"antigravity": (".gemini/antigravity-cli",)}
# Folders inside a runtime's data dir that a run still can't write, by registered id, each relative to every home.
# agy unpacks its built-in skills and plugins into builtin/ at first start and loads them into every later session's
# prompt (findings §7.1), so a run's edit there would reach the owner's own sessions. That is vendor content outside
# review: Scout's skills reach agy through the vault and the plugin's reviewed path, generated at install time
# outside any run (Jordan, 2026-11-25). A warm start writes nothing there.
AGENT_DATA_REDENY: Mapping[str, tuple[str, ...]] = {"antigravity": (".gemini/antigravity-cli/builtin",)}
# Under such a carve-out but still denied, at any depth below an agent CLI's home folder: its hook and config files.
# Only what a case-insensitive match widens (spec §5.4): `[^/]` stays `[^/]`.
AGENT_CONFIG_REDENY: tuple[str, ...] = (
    r"settings[^/]*\.json$",
    r"config\.toml$",
    r"hooks\.json$",
    r"mcp[^/]*\.json$",
    r"hooks/",
    r"hooks$",
)
# ~/.ssh is protected (hook_paths); ssh itself records a new host's key here.
SSH_WRITABLE: tuple[str, ...] = ("known_hosts", "known_hosts.old")

# ~/.scout-secrets: no reads of the contents, no writes (#636's review). Metadata (stat, `test -s`) stays allowed.
# The Telegram wrap is sent from inside the session (phases/connectors/telegram.md, 18 of the last 50 runs), so
# its two files stay readable in this base profile. While the run's brokers are up, :func:`broker_rules` denies
# them too (Phase 1d): the send-only broker sends the wrap.
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
    """~/.claude under $HOME and the account's home (:func:`scout.kb.hook_paths.homes`), and ``$CLAUDE_CONFIG_DIR``."""
    return hook_paths.claude_dirs(env)


def secrets_dirs(env: Mapping[str, str]) -> list[Path]:
    """~/.scout-secrets and ``$SCOUT_SECRETS_DIR``: :func:`scout.kb.hook_paths.secrets_dirs`, the one definition both
    layers use (it reads the variable :func:`scout.linear_activity.secrets_dir` reads)."""
    return hook_paths.secrets_dirs(env)


def _under(path: str, root: str) -> bool:
    p, r = path.casefold(), root.casefold().rstrip("/")
    return p == r or p.startswith(r + "/")


def kernel_trees(vault: Path | None, env: Mapping[str, str]) -> list[Path]:
    """The kernel's own, wider protected trees (the hook layer lists their parts): all of ~/.claude (and
    ``$CLAUDE_CONFIG_DIR``) and the vault's .claude/. Their carve-outs are in :func:`carve_outs`."""
    return [*_claude_dirs(env), *([vault / VAULT_CLAUDE_DIR] if vault is not None else [])]


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
        for name in CLAUDE_HOME_CARVE_DIRS:
            out.append(("subpath", str(base / name)))
    for ssh in hook_paths.ssh_dirs(env):
        for name in SSH_WRITABLE:
            out.append(("literal", str(ssh / name)))
    return out


def _agent_homes(env: Mapping[str, str]) -> list[str]:
    """Every form of each other agent CLI's home folder (``hook_paths.AGENT_DIRS`` under each of ``homes``)."""
    return [
        form
        for home in hook_paths.homes(env)
        for name in hook_paths.AGENT_DIRS
        for form in _forms(str(home / name), env)
    ]


def _agent_data_dirs(runtime_id: str | None, env: Mapping[str, str]) -> set[str]:
    """The runtime's :data:`AGENT_DATA_DIRS`, under each form of each home. Only the home is resolved: a data dir
    that is itself a symlink resolves elsewhere, so it matches none of these and is refused."""
    rels = AGENT_DATA_DIRS.get(runtime_id or "", ())
    return {
        os.path.join(home, rel).casefold()
        for h in hook_paths.homes(env)
        for home in _forms(str(h), env)
        for rel in rels
    }


def _check_runtime_paths(
    extra: SandboxPaths, base: Sequence[str], env: Mapping[str, str], runtime_id: str | None = None
) -> None:
    """Refuse (:class:`ProfileError`) a runtime's paths that could re-open what Scout protects (review of #806).

    Every path must be absolute and normalised, and a writable one must not be a file. Inside another agent
    CLI's home folder (``hook_paths.AGENT_DIRS``) a writable folder must be exactly one of the runtime's
    :data:`AGENT_DATA_DIRS`, whatever the adapter says. Anywhere else it must lie strictly under one of the
    runtime's own protected trees and outside Scout's protected paths. No writable folder may hold (or be) one
    of Scout's protected paths or secrets dirs. Each check holds for the path both as spelled and resolved.
    """
    for path in (*extra.protected, *extra.writable):
        text = str(path)
        if not os.path.isabs(text) or os.path.normpath(text) != text:
            raise ProfileError(f"the runtime's sandbox path {text!r} isn't absolute and normalised")
    agent_homes = _agent_homes(env)
    data_dirs = _agent_data_dirs(runtime_id, env)
    owned = _owned(extra, env)
    for path in extra.writable:
        for form in _forms(str(path), env):
            if os.path.lexists(form) and not os.path.isdir(form):
                raise ProfileError(f"the runtime's writable path {form!r} is a file, not a folder")
            for root in base:
                if _under(root, form):
                    raise ProfileError(f"the runtime's writable path {form!r} would re-open {root!r}")
            if any(_under(form, a) for a in agent_homes):
                if form.casefold() not in data_dirs:
                    raise ProfileError(
                        f"the runtime's writable path {form!r} is inside another agent CLI's home folder and isn't "
                        f"one of the {runtime_id or 'claude-code'!r} runtime's data folders (profile.AGENT_DATA_DIRS)"
                    )
                continue
            if not any(_under(form, own) and form.casefold() != own.casefold() for own in owned):
                raise ProfileError(f"the runtime's writable path {form!r} isn't under one of its own protected trees")
            for root in base:
                if _under(form, root):
                    raise ProfileError(f"the runtime's writable path {form!r} is inside Scout's protected {root!r}")


def _owned(extra: SandboxPaths, env: Mapping[str, str]) -> list[str]:
    return [form for tree in extra.protected for form in _forms(str(tree), env)]


def build_rules(
    vault: Path | None,
    env: Mapping[str, str] | None = None,
    *,
    runtime_paths: SandboxPaths | None = None,
    runtime_id: str | None = None,
) -> list[Rule]:
    """The file rules, in profile order (last match wins). Raises :class:`ProfileError`.

    ``runtime_paths`` is what the run's agent runtime adds (its adapter's ``sandbox_paths``, #652 S6): its
    protected trees join the kernel's own, and its writable folders join the carve-outs, so a protected tree
    of either kind under one of them is denied again. :func:`_check_runtime_paths` refuses any that could
    re-open one of Scout's own, judging a carve-out in another agent CLI's home folder by ``runtime_id`` (the
    registered id the run asked for, never the adapter's own). With any carve-out, every agent CLI's hook and
    config files (:data:`AGENT_CONFIG_REDENY`) and the runtime's :data:`AGENT_DATA_REDENY` folders are denied
    again after it."""
    env = os.environ if env is None else env
    extra = runtime_paths or SandboxPaths()
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
    for tree in kernel_trees(vault, env):
        for form in _forms(str(tree), env):
            protected.append(Rule("deny", "file-write*", "subpath", form))
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
    if vault is not None:
        # What the owner's agents proposed (#550 V4b): untrusted text a run never reads (spec §7.1b). Writes are
        # already denied above (hook_paths.AGENT_TEXT is a protected tree); this adds the kernel's read deny.
        for form in _forms(str(vault.joinpath(*hook_paths.AGENT_TEXT_DIR)), env):
            secrets.append(Rule("deny", "file-read-data", "subpath", form))
    _check_runtime_paths(extra, [r.path for r in (*protected, *secrets)], env, runtime_id)
    for tree in extra.protected:
        for form in _forms(str(tree), env):
            protected.append(Rule("deny", "file-write*", "subpath", form))
    rules += protected + secrets
    # 2. the carve-outs
    allows: list[Rule] = []
    for kind, path in [*carve_outs(vault, env), *(("subpath", str(p)) for p in extra.writable)]:
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
            # objects/ is carved out, but the folder holding alternates can't be removed or renamed either.
            for form in _forms(str(gitdir / "objects" / "info"), env):
                redeny.append(Rule("deny", "file-write-unlink", "literal", form))
    redeny += [Rule("deny", "file-write*", "regex", rx) for rx in ANYWHERE_SUFFIXES]
    if extra.writable:
        redeny += [
            Rule("deny", "file-write*", "regex", f"^{_rx_escape(home)}/(.*/)?{rx}")
            for home in _agent_homes(env)
            for rx in AGENT_CONFIG_REDENY
        ]
        redeny += [
            Rule("deny", "file-write*", "subpath", form)
            for h in hook_paths.homes(env)
            for rel in AGENT_DATA_REDENY.get(runtime_id or "", ())
            for form in _forms(str(h / rel), env)
        ]
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
    # 5. the PATH the runners start programs from: no new or changed entry (a `date` that runs before any
    # sandbox), and the folder can't be swapped. Its own entries only: a tool's store below stays writable.
    for path_dir in hook_paths.path_dirs(env):
        rules.append(Rule("deny", "file-write*", "regex", f"^{_rx_escape(path_dir)}/[^/]+$"))
        rules.append(Rule("deny", "file-write-unlink", "literal", path_dir))
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


def render_rules(rules: Sequence[Rule]) -> list[str]:
    """``rules`` as SBPL forms, in order; runs of one action+op share a form."""
    lines: list[str] = []
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
    return lines


def render(rules: Sequence[Rule], *, network: str = "") -> str:
    """The SBPL text: ``(allow default)``, ``rules`` in order (runs of one action+op share a form), the spawn denies."""
    lines = [
        "(version 1)",
        ";; Scout scheduled-run write sandbox (scout.sandbox.profile; spec 2026-11-15). Generated: don't edit.",
        "(allow default)",
        *render_rules(rules),
    ]
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


def build_profile(
    vault: Path | None,
    env: Mapping[str, str] | None = None,
    *,
    network: str = "",
    runtime_paths: SandboxPaths | None = None,
    runtime_id: str | None = None,
) -> str:
    return render(build_rules(vault, env, runtime_paths=runtime_paths, runtime_id=runtime_id), network=network)


def broker_rules(vault: Path, broker_dir: Path, env: Mapping[str, str] | None = None) -> list[Rule]:
    """What a run's profile adds while its brokers run (Phase 1d, spec 2026-11-16 §5.4), to go after the rest.

    The broker commits for the run, so every write in the vault's git dirs is denied, the 1c carve-outs
    included; it sends the Telegram wrap, so every read in the secrets dirs is denied, the Telegram files and the
    folder listing included; and the broker's own folder (its socket) can't be changed.
    """
    env = os.environ if env is None else env
    rules: list[Rule] = []
    for gitdir in [vault / ".git", *_git_dirs(vault)]:
        for form in _forms(str(gitdir), env):
            rules.append(Rule("deny", "file-write*", "subpath", form))
    for d in secrets_dirs(env):
        for form in _forms(str(d), env):
            rules.append(Rule("deny", "file-read-data", "subpath", form))
    for form in _forms(str(broker_dir), env):
        rules.append(Rule("deny", "file-write*", "subpath", form))
    out: list[Rule] = []
    for rule in rules:
        _check_path(rule.path)
        if rule not in out:
            out.append(rule)
    return out


def with_broker_rules(base: str, rules: Sequence[Rule]) -> str:
    """``base`` (a profile :func:`render` made) with ``rules`` appended last, where they win."""
    head = ";; Phase 1d (scout.sandbox.broker; spec 2026-11-16): the brokers commit and send for the run."
    return base.rstrip("\n") + "\n" + "\n".join([head, *render_rules(rules)]) + "\n"


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
        stderr = proc.stderr.strip()[-300:]
        detail = f": {stderr}" if stderr else ""
        if proc.returncode == 9 or os.path.exists(canary):
            return "the sandbox let through a write its profile denies"
        if proc.returncode == 8:
            return f"the sandbox refused an ordinary temp-file write{detail}"
        if proc.returncode != 0:
            # The script never ran: a profile sandbox-exec can't parse, or a sandbox it can't start (inside
            # another sandbox: `sandbox_apply: Operation not permitted`, exit 71).
            return f"sandbox-exec failed (exit {proc.returncode}){detail}"
        if not os.path.exists(ordinary):
            return "the sandbox refused an ordinary temp-file write"
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
