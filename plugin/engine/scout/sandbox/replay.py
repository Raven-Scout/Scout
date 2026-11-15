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
    if any(_under_any(target, d) for d in sbx.secrets_dirs(os.environ)):
        return "Scout's secrets"
    return "sandbox-only rule"


def _under_any(target: str, d: Path) -> bool:
    return sbx._under(target, os.path.realpath(d))


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


def replay(project: Path, vault: Path, *, limit: int = 50) -> dict[str, Any]:
    vault = vault.resolve()
    home = Path.home()
    rules = sbx.build_rules(vault)
    sessions = scheduled_sessions(project, limit)
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
                if sbx.decide(rules, target, "unlink" if removes else "write") == "deny":
                    form = _form(target, vault, rules)
                    deny_forms[form] += 1
                    call_denied = True
                    if hook_paths.protected_write(target, vault) is None and not removes:
                        kernel_only[form] += 1
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
            denied_calls += call_denied
    return {
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
    return "\n".join(lines)
