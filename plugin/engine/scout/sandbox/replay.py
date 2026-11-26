"""Read-only replay of past scheduled runs against the write sandbox's rules (spec 2026-11-15 §10.3).

For each of the newest scheduled-run sessions (``entrypoint: sdk-cli``) in a Claude Code project
dir, every path a tool call writes (Write, Edit, MultiEdit, NotebookEdit, and what #637's shell
parser says a Bash command writes or removes) and every path it reads in the secrets dir is put
to :func:`scout.sandbox.profile.decide`. The report holds counts and form names only: never a
path, a command or any content, so it can go into a PR.

Limits, named in the report: a Bash command's targets are what ``live_run.run_escape`` finds (a
path built at run time is counted as unresolved), relative paths are taken from the vault root
(the session's start dir), and what git itself writes under the git dir isn't in a transcript:
the git matrix test (tests/integration/test_run_sandbox_kernel.py) covers that.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from scout.kb import hook_paths, live_run
from scout.sandbox import profile as sbx

FILE_TOOLS = {"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}
_SECRET_PATH = re.compile(r"""(?:~|\$HOME|\$\{HOME\}|/[^\s'"]*)?/?\.scout-secrets(?:/[^\s'";|&)]*)?""")
_GIT = re.compile(r"(?:^|[;&|(]\s*|\b(?:then|do|else)\s+)(?:\w+=\S+\s+)*git(?:\s+-[cC]\s+\S+)*\s+([a-z][a-z-]*)")


def scheduled_sessions(project: Path, limit: int) -> list[Path]:
    """The newest ``limit`` sessions started by ``claude -p`` (entrypoint sdk-cli), newest first."""
    files = sorted(project.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    out: list[Path] = []
    for path in files:
        if len(out) >= limit:
            break
        if _entrypoint(path) == "sdk-cli":
            out.append(path)
    return out


def _entrypoint(path: Path) -> str | None:
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i > 50:
                    return None
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and "entrypoint" in row:
                    return str(row["entrypoint"])
    except OSError:
        return None
    return None


def tool_calls(path: Path) -> Iterator[tuple[str, dict[str, Any]]]:
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict) or row.get("type") != "assistant":
                continue
            content = (row.get("message") or {}).get("content") or []
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    inp = block.get("input")
                    yield str(block.get("name")), inp if isinstance(inp, dict) else {}


def _resolve(raw: str, vault: Path, home: Path) -> str | None:
    if not raw.strip():
        return None  # a variable the parser couldn't fill in (T=$(mktemp -d); rm -rf $T)
    if "$" in raw or "`" in raw:
        expanded = raw.replace("${HOME}", str(home)).replace("$HOME", str(home))
        if "$" in expanded or "`" in expanded:
            return None
        raw = expanded
    if raw == "~" or raw.startswith("~/"):
        raw = str(home) + raw[1:]
    path = raw if raw.startswith("/") else str(vault / raw)
    return os.path.realpath(path)


def _form(target: str, vault: Path, rules: list[sbx.Rule]) -> str:
    """The protected form a denied write falls under (hook_paths' names), else the sandbox-only reason."""
    form = hook_paths.protected_write(target, vault)
    if form is not None:
        return form
    if any(r.op == "file-write-unlink" and sbx._matches(r, target) for r in rules):
        return "a folder holding a protected path"
    return "sandbox-only rule"  # the secrets are a hook_paths form too (#637), so protected_write names them


def _collect(command: str) -> list[tuple[str, bool]]:
    out: list[tuple[str, bool]] = []

    def collect(path: str, removes: bool) -> None:
        out.append((path, removes))

    live_run.run_escape(command, protect=collect)
    return out


def _bash_targets(command: str) -> tuple[list[tuple[str, bool]] | None, list[tuple[str, bool]]]:
    """(what the command writes or removes for certain, that plus every inline program's path literals)."""
    try:
        everything = _collect(command)
        # The same command with #637's inline-program check (live_run._program_escape) switched off: what is
        # left is what the shell itself writes.
        real = live_run._program_escape
        live_run._program_escape = lambda *_a, **_k: None
        try:
            definite = _collect(command)
        finally:
            live_run._program_escape = real
    except Exception:  # noqa: BLE001 - a command the parser can't read is counted, not fatal
        return None, []
    return definite, everything


# -- Phase 1d: what the run brokers change (spec 2026-11-16 §9.6) -----------------------------------------------------

# In the vault, each git subcommand that writes the git dir, and what a run does instead once the broker commits for
# it (spec §5.7). Read-only subcommands aren't here: they keep working.
BROKER_REPLACEMENTS: dict[str, str] = {
    "add": "scout/scripts/git-safe-commit.sh (the broker stages and commits)",
    "commit": "scout/scripts/git-safe-commit.sh (the broker commits)",
    "mv": "mv, then scout/scripts/git-safe-commit.sh with both paths",
    "rm": "rm, then scout/scripts/git-safe-commit.sh with the path",
    "rm --cached": "list it in .gitignore, move it aside, commit its path with the helper (a deletion), move it back",
    "checkout --": "git show HEAD:<path> > <path> (read-only git, a work-tree write)",
    "restore --worktree": "git show <rev>:<path> > <path> (read-only git, a work-tree write)",
    "restore --staged": "nothing to undo: a run no longer stages anything",
    "reset": "nothing to undo: a run no longer stages anything",
    "stash": "nothing to set aside: a run no longer stages anything; copy a file to .scout-cache/ to keep it",
    "fetch": "git ls-remote (read-only) to see the remote",
}
# In-vault git writes the 1c profile already refuses (.git/config is in GIT_REDENY), so the brokers take nothing away.
ALREADY_REFUSED: dict[str, str] = {
    "config (write)": "refused since Phase 1c (.git/config is protected); the broker commits with the vault's identity",
}
_GIT_CMD = re.compile(
    r"(?:^|[;&|(]\s*|\b(?:then|do|else)\s+)(?:\w+=\S+\s+)*git((?:\s+-[cC]\s+\S+)*)\s+([a-z][a-z-]*)([^;&|\n]*)",
    re.MULTILINE,  # each line of a multi-line command starts a command
)
_READ_ONLY = {
    "log", "status", "show", "diff", "grep", "rev-parse", "ls-files", "cat-file", "blame", "describe", "rev-list",
    "ls-tree", "shortlog", "merge-base", "ls-remote", "check-ignore", "for-each-ref", "show-ref", "name-rev",
    "whatchanged", "count-objects", "var", "help", "version",
}  # fmt: skip
_SHA_RESULT = re.compile(r"committed ([0-9a-f]{7,40})\b")
_HEX = re.compile(r"\b[0-9a-f]{7,40}\b")


def _git_where(command: str, opts: str, vault: Path, home: Path) -> str:
    m = re.search(r"-C\s+(\S+)", opts or "")
    target = m.group(1) if m else None
    if target is None:
        m = re.search(r"\bcd\s+(\"[^\"]+\"|'[^']+'|\S+)", command)
        target = m.group(1).strip("'\"") if m else None
    if target is None or target in (".", "$SCOUT_DIR", "${SCOUT_DIR}", "$SCOUT_DATA_DIR", "${SCOUT_DATA_DIR}"):
        return "vault"
    resolved = _resolve(target, vault, home)
    if resolved is None:
        return "unknown"
    return "vault" if sbx._under(resolved, os.path.realpath(vault)) else "elsewhere"


def _git_kind(sub: str, rest: str) -> str | None:
    """The :data:`BROKER_REPLACEMENTS` key for a git write, None for a read, "other <sub>" for an unlisted write."""
    words = rest.split()
    if sub in _READ_ONLY or sub == "config" and not re.search(r"--(add|unset|replace-all)\b", rest) and len(words) < 2:
        return None
    if sub == "stash" and re.match(r"\s*(list|show)\b", rest):
        return None
    if sub in ("branch", "tag") and re.match(r"\s*(-l|--list|-a|-r|--show-current|--contains|-v\b|$)", rest):
        return None
    if sub == "worktree" and re.match(r"\s*list\b", rest):
        return None
    if sub == "checkout" and "--" in words:
        return "checkout --"
    if sub == "restore":
        shorts = "".join(w[1:] for w in words if w.startswith("-") and not w.startswith("--"))
        staged = "--staged" in words or "S" in shorts
        worktree = "--worktree" in words or "W" in shorts or not staged
        # Putting the work tree back needs `git show`; unstaging alone needs nothing.
        return "restore --worktree" if worktree else "restore --staged"
    if sub == "rm" and "--cached" in words:
        return "rm --cached"
    if sub == "config":
        return "config (write)"
    if sub in BROKER_REPLACEMENTS:
        return sub
    return f"other {sub}"


def broker_session(path: Path, vault: Path, home: Path) -> dict[str, Any]:
    """One session's broker-relevant counts: helper and Telegram calls, in-vault git writes by kind, SHA reuse."""
    uses: dict[str, str] = {}
    shas: set[str] = set()
    out: dict[str, Any] = {"helper": 0, "telegram": 0, "commits_with_sha": 0, "sha_reused": 0, "git": Counter()}
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict) or row.get("type") not in ("assistant", "user"):
                continue  # other rows can repeat a call (the same filter as tool_calls)
            content = (row.get("message") or {}).get("content")
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use" and row["type"] == "assistant":
                    name, inp = str(block.get("name")), block.get("input") or {}
                    text = (
                        str(inp.get("command", "")) if name == "Bash" else json.dumps(inp) if name in FILE_TOOLS else ""
                    )
                    if name == "Bash":
                        uses[str(block.get("id"))] = text
                        out["helper"] += text.count("git-safe-commit")
                        out["telegram"] += text.count("notify telegram")
                        for m in _GIT_CMD.finditer(text):
                            kind = _git_kind(m.group(2), m.group(3))
                            if kind is not None and _git_where(text, m.group(1), vault, home) == "vault":
                                out["git"][kind] += 1
                    if shas and any(h.startswith(s) or s.startswith(h) for h in _HEX.findall(text) for s in shas):
                        out["sha_reused"] += 1
                elif block.get("type") == "tool_result" and row["type"] == "user":
                    cmd = uses.get(str(block.get("tool_use_id")), "")
                    if "git-safe-commit" not in cmd:
                        continue
                    c = block.get("content")
                    text = (
                        "\n".join(x.get("text", "") for x in c if isinstance(x, dict))
                        if isinstance(c, list)
                        else str(c or "")
                    )
                    found = _SHA_RESULT.findall(text)
                    out["commits_with_sha"] += len(found)
                    shas.update(found)
    return out


def _secret_read_kind(target: str, env: dict[str, str]) -> str:
    for d in sbx.secrets_dirs(env):
        for form in sbx._forms(str(d), env):
            if target.casefold() == form.casefold():
                return "folder listing (test -s <file> says whether it is set up)"
            if os.path.basename(target) in hook_paths.TELEGRAM_SECRETS:
                return "Telegram token or chat id (the broker sends)"
    return "another secret"


def replay(project: Path, vault: Path, *, limit: int = 50, brokers: bool = False) -> dict[str, Any]:
    vault = vault.resolve()
    home = Path.home()
    env = dict(os.environ)
    base_rules = sbx.build_rules(vault)
    # The broker folder is per run; any absolute path stands in for it here.
    rules = (
        base_rules + sbx.broker_rules(vault, Path("/private/tmp/scout-broker.replay"), env) if brokers else base_rules
    )
    sessions = scheduled_sessions(project, limit)
    newly_writes: Counter[str] = Counter()
    newly_reads: Counter[str] = Counter()
    calls: Counter[str] = Counter()
    checked = Counter[str]()
    deny_forms: Counter[str] = Counter()
    kernel_only: Counter[str] = Counter()
    inline_forms: Counter[str] = Counter()
    read_deny = 0
    unresolved = 0
    git_sub: Counter[str] = Counter()
    denied_calls = 0
    for session in sessions:
        for name, inp in tool_calls(session):
            calls[name] += 1
            targets: list[tuple[str, bool]] = []
            inline: list[tuple[str, bool]] = []
            reads: list[str] = []
            if name in FILE_TOOLS:
                raw = inp.get(FILE_TOOLS[name])
                if isinstance(raw, str):
                    targets.append((raw, False))
            elif name == "Read":
                raw = inp.get("file_path")
                if isinstance(raw, str):
                    reads.append(raw)
            elif name == "Bash":
                command = str(inp.get("command", ""))
                for m in _GIT.finditer(command):
                    git_sub[m.group(1)] += 1
                definite, everything = _bash_targets(command)
                if definite is None:
                    checked["unparsed bash"] += 1
                    definite, everything = [], []
                # A path literal in an inline program (python3 -c, a heredoc) is a write only if the program
                # writes it: #637's hook denies them all, the kernel only what is written. Counted apart.
                targets += definite
                rest = list(definite)
                for item in everything:
                    if item in rest:
                        rest.remove(item)
                    else:
                        inline.append(item)
                reads += [m.group(0) for m in _SECRET_PATH.finditer(command)]
            call_denied = False
            for raw, removes in targets:
                target = _resolve(raw, vault, home)
                if target is None:
                    unresolved += 1
                    continue
                checked["write targets"] += 1
                op = "unlink" if removes else "write"
                if sbx.decide(rules, target, op) == "deny":
                    form = _form(target, vault, rules)
                    deny_forms[form] += 1
                    call_denied = True
                    if hook_paths.protected_write(target, vault) is None and not removes:
                        kernel_only[form] += 1
                    if brokers and sbx.decide(base_rules, target, op) == "allow":
                        in_git = any(sbx._under(target, f) for g in [vault / ".git", *hook_paths.git_dirs(vault)]
                                     for f in sbx._forms(str(g), env))  # fmt: skip
                        newly_writes["the vault's git dir (the broker commits)" if in_git else "elsewhere"] += 1
            for raw, removes in inline:
                target = _resolve(raw, vault, home)
                if target is not None and sbx.decide(rules, target, "unlink" if removes else "write") == "deny":
                    inline_forms[_form(target, vault, rules)] += 1
            for raw in reads:
                target = _resolve(raw, vault, home)
                if target is None:
                    unresolved += 1
                    continue
                checked["read targets in scope"] += 1
                if sbx.decide(rules, target, "read") == "deny":
                    read_deny += 1
                    call_denied = True
                    if brokers and sbx.decide(base_rules, target, "read") == "allow":
                        newly_reads[_secret_read_kind(target, env)] += 1
            denied_calls += call_denied
    report = {
        "sessions": len(sessions),
        "tool_calls": sum(calls.values()),
        "calls_by_tool": dict(calls.most_common()),
        "checked": dict(checked),
        "unresolved_paths": unresolved,
        "calls_with_a_denied_path": denied_calls,
        "denied_writes_by_form": dict(deny_forms.most_common()),
        "denied_writes_the_hook_layer_allows": dict(kernel_only.most_common()),
        "denied_secret_reads": read_deny,
        "inline_program_literals_under_a_deny": dict(inline_forms.most_common()),
        "git_subcommands": dict(git_sub.most_common()),
        "limits": [
            "Bash targets are what live_run.run_escape finds; paths built at run time count as unresolved",
            "relative paths are taken from the vault root",
            "git's own writes under the git dir aren't in transcripts (see the git matrix test)",
        ],
    }
    if brokers:
        report["brokers"] = _broker_summary(sessions, vault, home, newly_writes, newly_reads)
    return report


def _broker_summary(
    sessions: list[Path], vault: Path, home: Path, newly_writes: Counter[str], newly_reads: Counter[str]
) -> dict[str, Any]:
    per = [broker_session(s, vault, home) for s in sessions]
    git: Counter[str] = Counter()
    sessions_using: Counter[str] = Counter()
    for p in per:
        git.update(p["git"])
        sessions_using.update(set(p["git"]))
    without = {k: n for k, n in git.items() if k not in BROKER_REPLACEMENTS and k not in ALREADY_REFUSED}
    return {
        "helper_calls_kept": sum(p["helper"] for p in per),
        "helper_commits_with_sha": sum(p["commits_with_sha"] for p in per),
        "sessions_reusing_a_sha_mid_session": sum(1 for p in per if p["sha_reused"]),
        "sha_reuses": sum(p["sha_reused"] for p in per),
        "telegram_calls_kept": sum(p["telegram"] for p in per),
        "vault_git_writes": {
            k: {
                "calls": n,
                "sessions": sessions_using[k],
                "instead": BROKER_REPLACEMENTS.get(k) or ALREADY_REFUSED.get(k) or "none",
            }
            for k, n in git.most_common()
        },
        "vault_git_writes_without_a_replacement": sum(without.values()),
        "newly_denied_writes": dict(newly_writes.most_common()),
        "newly_denied_writes_outside_the_git_dir": newly_writes.get("elsewhere", 0),
        "newly_denied_secret_reads": dict(newly_reads.most_common()),
        "notes": [
            "helper and Telegram calls keep their command, output and exit codes; the broker answers in session, "
            "so a SHA reused mid-session is still there",
            "git subcommands are counted where the command runs in the vault (no -C/cd elsewhere)",
        ],
    }


def format_report(report: dict[str, Any]) -> str:
    lines = [
        f"sessions: {report['sessions']}, tool calls: {report['tool_calls']}",
        f"checked: {report['checked']}, unresolved paths: {report['unresolved_paths']}",
        f"calls with a denied path: {report['calls_with_a_denied_path']}",
        f"denied writes by form: {report['denied_writes_by_form'] or 'none'}",
        f"  of which the hook layer allows: {report['denied_writes_the_hook_layer_allows'] or 'none'}",
        f"denied secret reads: {report['denied_secret_reads']}",
        "inline-program path literals under a deny (a write only if the program writes it): "
        f"{report['inline_program_literals_under_a_deny'] or 'none'}",
        f"git subcommands: {report['git_subcommands']}",
    ]
    b = report.get("brokers")
    if b:
        lines += [
            "with the run brokers (Phase 1d):",
            f"  helper calls kept: {b['helper_calls_kept']} ({b['helper_commits_with_sha']} commits with a SHA); "
            f"Telegram calls kept: {b['telegram_calls_kept']}",
            f"  sessions reusing a SHA mid-session: {b['sessions_reusing_a_sha_mid_session']} "
            f"({b['sha_reuses']} reuses), kept: the broker answers in session",
            *(
                f"  vault git {k}: {v['calls']} calls in {v['sessions']} sessions -> {v['instead']}"
                for k, v in b["vault_git_writes"].items()
            ),  # fmt: skip
            f"  vault git writes without a replacement: {b['vault_git_writes_without_a_replacement']}",
            f"  newly denied writes: {b['newly_denied_writes'] or 'none'} "
            f"(outside the git dir: {b['newly_denied_writes_outside_the_git_dir']})",
            f"  newly denied secret reads: {b['newly_denied_secret_reads'] or 'none'}",
        ]
    return "\n".join(lines)
