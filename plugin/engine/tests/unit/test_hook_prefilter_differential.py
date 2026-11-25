"""hooks/prefilter.sh decides exactly what the inline hooks.json commands it replaced decided.

The shell stage of each tool hook chooses whether Python (``scoutctl hook <name>``) starts at all, so
for a scheduled run a call it lets through is a call the gate never judges. Moving that stage out of
hooks.json into ``hooks/prefilter.sh`` was meant to change no decision. This file holds that to account:

- ``tests/fixtures/hooks/hooks-inline-f3d30663.json`` is hooks.json as it was on main before the move
  (commit f3d30663, #788, after v0.18.23), byte for byte. Its commands are the reference.
- Every case runs the old command and the new one against a plugin root whose ``scoutctl`` only records
  that it was started, with what arguments and what stdin, and compares everything observable: exit
  code, stdout, stderr, whether Python would have started, and the payload it would have been given.
- The Bash corpus is every string literal of the existing gate tests (so a form someone adds there is
  compared here too) plus the forms hooks.json's description names.

The old Bash pattern held one 1,117-character block twice. ``test_the_bash_pattern_is_the_old_one_minus_
its_repeated_block`` shows the two copies were identical runs of whole alternatives, so dropping one
cannot change a match, and that nothing else in the pattern moved.

A deliberate change to a filter belongs in ``INTENDED_DIFFERENCES`` with the reason; until then any
difference fails.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import NamedTuple

import pytest

ENGINE = Path(__file__).resolve().parents[2]
PLUGIN = ENGINE.parent
PREFILTER = PLUGIN / "hooks" / "prefilter.sh"
OLD_HOOKS = ENGINE / "tests" / "fixtures" / "hooks" / "hooks-inline-f3d30663.json"
NEW_HOOKS = PLUGIN / "hooks" / "hooks.json"

# (event, matcher prefix) -> the filter name its entry passes to prefilter.sh.
ENTRIES: dict[str, tuple[str, str]] = {
    "mcp-tool-gate": ("PreToolUse", "mcp__.*"),
    "outbound-gate": ("PreToolUse", "^mcp__.+__.*(send|"),
    "kb-read-gate": ("PreToolUse", "Grep|Glob|Bash"),
    "kb-read-gate-read": ("PreToolUse", "Read"),
    "kb-write-gate": ("PreToolUse", "Write|Edit|MultiEdit|NotebookEdit"),
    "kb-read-label": ("PostToolUse", "Read"),
    "shared-read-record": ("PostToolUse", "^mcp__.+__(shared_with_me|"),
    "front-page-reads": ("PostToolUse", "Write|Edit|MultiEdit"),
}

# Cases where the new command is meant to differ from the old one: {case id: reason}. Empty on purpose.
INTENDED_DIFFERENCES: dict[str, str] = {}

# The filters that find the session's vault from the payload's cwd, as their Python hooks do (#803
# follow-up 1). The old commands walked up from their own $PWD, so for these the world puts the
# case's place into the payload as Claude Code does (`"cwd"`, before the tool call): the old command
# and the new one then look at the same place, and every other decision is still compared.
PAYLOAD_CWD = frozenset({"mcp-tool-gate", "outbound-gate", "kb-read-label", "shared-read-record"})
# A payload that is not a JSON object has no cwd to go by. Python then goes by its own working
# directory, so the new filters start it where the old ones may not have (and the owner's payload
# reaches it through read_payload, which drops trailing newlines): the one intended difference.
NO_CWD_TO_READ = "no cwd in the payload: the filter now starts Python, which decides (#803 follow-up 1)"


def _intended(case: Case) -> str | None:
    if case.id in INTENDED_DIFFERENCES:
        return INTENDED_DIFFERENCES[case.id]
    if case.entry in PAYLOAD_CWD and not case.payload.lstrip().startswith("{"):
        return NO_CWD_TO_READ
    return None


# The gate tests whose string literals make up the Bash corpus.
CORPUS_SOURCES = (
    "tests/unit/test_hooks_kb_read_gate.py",
    "tests/unit/test_kb_read_gate_hardening.py",
    "tests/unit/test_hooks_write_gate.py",
    "tests/unit/test_hooks_outbound_gate.py",
    "tests/unit/test_hooks_kb_read_label.py",
    "tests/unit/test_hooks_front_page_reads.py",
    "tests/integration/test_scheduled_commit_hook_skip.py",
    "tests/integration/test_scheduled_commit_env_bypass.py",
)

# The forms hooks.json's description names, one or more per clause, and their harmless neighbours.
DESCRIBED_FORMS = (
    # no KB path, no flagged word: the shell lets these through
    "ls",
    "ls -la notes",
    "git status",
    "git log --oneline -5",
    "echo hello && echo bye",
    "scripts/scoutctl kb search widgets",
    "scripts/scoutctl kb read projects/widgets/widgets.md | head -5",
    "cat README.md 2>&1",
    "grep -n TODO src/main.c",
    "wc -l action-items/action-items-2026-04-15.md",
    "date; uptime",
    "true || false",
    "/usr/bin/open x",
    "catalog --batch-size 3",
    "printf '%s' attic",
    # a KB path, spelled plainly or through quotes and backslashes
    "cat knowledge-base/people/a.md",
    "cat knowledge_base/people/a.md",
    "cat action-items/context/x.md",
    "cat know'ledge'-base/a.md",
    'cat know"ledge"-base/a.md',
    "cat knowledge\\-base/a.md",
    # a glob that could expand to one
    "cat kno*/a.md",
    "cat *-base/a.md",
    "cat ?nowledge_base/a.md",
    "cat action-items/c*/x.md",
    "cat [k]nowledge-base/a.md",
    # the run's identity and environment
    "SCOUT_MODE= git commit -m x",
    "unset SCOUT_READER",
    "export SCOUT_READER=owner",
    "env -i git commit -m x",
    "env FOO=1 ls",
    # detaching
    "nohup sleep 9",
    "setsid sleep 9",
    "sleep 9 & disown",
    "sleep 9 &",
    "sleep 9 &\nls",
    "launchctl list",
    "osascript -e 'beep'",
    "open notes.txt",
    "tmux new -d",
    "screen -dm x",
    "script -q /dev/null ls",
    "at now",
    "batch",
    "ssh host ls",
    "mosh host",
    "systemd-run ls",
    "ls 2>&1 | head",
    "ls &> out",
    "ls |& head",
    # skipping or redirecting the commit hooks
    "git commit --no-verify -m x",
    "git commit -n -m x",
    "git commit -anm x",
    "git am -n x.patch",
    "git -c core.hooksPath=/dev/null commit -m x",
    "git --config-env=core.hooksPath=X commit",
    "git config core.hooksPath x",
    "git config alias.ci commit",
    "git config include.path x",
    "git config includeIf.gitdir:x.path y",
    "GIT_CONFIG_COUNT=1 git commit",
    "git commit-tree abc",
    "git update-ref refs/heads/main abc",
    "git fast-import",
    "git filter-branch x",
    "git filter-repo x",
    "git hash-object -w x",
    "echo x > .git/hooks/pre-commit",
    "cat .git/config",
    "cd .git && ls",
    'git "$sub" -m x',
    "git $sub",
    # `scoutctl kb` feeding a reader
    "scripts/scoutctl kb files | xargs cat",
    "scripts/scoutctl kb files | while read f; do cat $f; done",
    "for f in $(scripts/scoutctl kb files); do cat $f; done",
    "cat $(scripts/scoutctl kb resolve x)",
    "cat `scripts/scoutctl kb resolve x`",
    "mapfile -t a < <(scripts/scoutctl kb files)",
    "readarray -t a < <(scripts/scoutctl kb files)",
    # recursive reads
    "rg widgets",
    "find . -name '*.md'",
    # #788: the other search tools, from the vault root and with a KB path
    "ag widgets",
    "ack widgets",
    "fd . -X cat",
    "ugrep -r widgets . --include='*.yaml'",
    "ag widgets knowledge-base",
    "ls\nfd md",
    "agent ls",
    "fdisk -l",
    "tree",
    "du -a",
    "grep -r widgets .",
    "grep -R widgets .",
    "egrep -ir widgets .",
    "fgrep --recursive widgets .",
    "grep --dereference-recursive widgets .",
    "grep -d recurse widgets .",
    "grep --directories=recurse widgets .",
    "ls -R",
    "ls -laR .",
    "git grep widgets",
    "git -C x grep widgets",
    "ls\nrg widgets",
    "ls\tfind .",
    # a variable, a loop, a redirection, a quoted program
    "d=knowledge-base; cat $d/a.md",
    "cat < knowledge-base/a.md",
    "sh -c 'cat a.md'",
    "bash -c 'cat a.md'",
    "zsh -c ls",
    "dash x.sh",
    "ksh x.sh",
    "eval ls",
    "source x.sh",
    "ls | sh",
    "sh <<EOF\nls\nEOF",
    # writes, protected names, inline programs
    "echo x > out.txt",
    "echo x >> out.txt",
    "tee out.txt",
    "rm x",
    "mv a b",
    "cp a b",
    "ln -s a b",
    "touch x",
    "chmod +x x",
    "sed -i s/a/b/ x",
    "perl -e 1",
    "curl https://example.com",
    "wget https://example.com",
    "tar xf x.tar",
    "dd if=a of=b",
    "python3 -c 'print(1)'",
    "python3.12 x.py",
    "node -e 1",
    "ruby -e 1",
    "ls .claude/settings.json",
    "ls ~/.CLAUDE/plugins",
    "cat ~/.gitconfig",
    "cat ~/.config/git/config",
    "ls .codex",
    "ls .gemini/x",
    "ls .agents",
    "ls .grok",
    "ls .cursor",
    "ls ~/Library/LaunchAgents",
    "cat ~/.zshrc",
    "cat ~/.bash_profile",
    "cat ~/.profile",
    "cat .mcp.json",
    # secrets and the delegate inbox
    "cat ~/.scout-secrets/telegram-bot-token",
    "ls $SCOUT_SECRETS_DIR",
    "cat /opt/secret-store/key",
    "ls .scout-inbox/agents",
    "ls agents/x",
    "scripts/scoutctl inbox pull",
    "scripts/scoutctl inbox  apply x",
    "scripts/scoutctl inbox list",
)

# Forms the open gate issues name (#610, #541, #611, #618), and launchers called by their full path,
# with what the shell stage does with each: True when Python gets to judge the call, False when the
# shell lets it through unjudged. This refactor fixes none of them and worsens none: the answers below
# are the inline commands' answers, and the test holds the script to them. A fix flips a False to True
# here, in the PR that makes it.
KNOWN_MISSED_FORMS: tuple[tuple[str, bool], ...] = (
    # #610: env changes and detaching through a wrapper or an indirect name
    ("command -p env -u FOO git commit -m x", True),
    ("nice -n 0 env -u FOO ls", True),
    ("timeout 60 env -u FOO ls", True),
    ("time -p ls", False),
    ("exec -a x bash -c ls", True),
    ("unset $v", False),
    ("$'SCOUT\\x5fMODE'= git commit -m x", False),
    ("git ci -m x", False),
    ("make commit", False),
    ("./commit.sh", False),
    ("coproc sleep 9", False),
    ("crontab -l", False),
    ("crontab -", False),
    # #541: a recursive search rooted at the vault (the shell does send these on; the rule is Python's)
    ("grep -r widgets .", True),
    ("rg widgets", True),
    ("find . -name '*.yaml'", True),
    ("ag widgets", True),
    ("ack widgets", True),
    ("fd . -X cat", True),
    ("ugrep -r widgets .", True),
    # #618: hook-skip forms
    ("git config core.hooksPath get", True),
    ('git -C "$R" $SUB -m x', False),
    ("git -C $R $SUB", False),
    ("git rebase --exec 'git commit --no-verify' HEAD~1", True),
    ("timeout 5 rm .git/hooks/x", True),
    ("git stash create", False),
    ("git branch -f main abc123", False),
    ("git merge --ff-only abc123", False),
    ("cd .git/hooks && ls > ../../out", True),
    # #611: persistence through a shell rc file
    ("echo x >> ~/.zshrc", True),
    # a launcher or `env` called by its full path: the detach group wants no `/` before the name
    ("/usr/bin/nohup sleep 9", False),
    ("/usr/bin/env -i ls", False),
    ("/usr/bin/open notes.txt", False),
    ("/bin/launchctl list", False),
    ("/usr/bin/ssh host ls", False),
)

RUN = {"SCOUT_MODE": "briefing"}
READERS: tuple[dict[str, str], ...] = (
    {},
    {"SCOUT_READER": "owner"},
    {"SCOUT_READER": ""},
    {"SCOUT_READER": "autonomous"},
    {"SCOUT_READER": "agent:helper"},
    {"SCOUT_READER": "delegate:d1"},
    {"SCOUT_READER": "Delegate:D1"},
    RUN,
    {"SCOUT_MODE": "briefing", "SCOUT_READER": "owner"},
)


class Outcome(NamedTuple):
    returncode: int
    stdout: str
    stderr: str
    python: str  # what the stub scoutctl recorded: its arguments, a newline, its stdin; "" if it never started


class Case(NamedTuple):
    id: str
    entry: str
    payload: str
    env: dict[str, str]
    cwd: str  # "vault", "deep" (a folder inside the vault) or "outside"
    stub_exit: int = 0
    shell: str = "/bin/sh"  # by path: a case may run with a PATH that has no sh


def _commands(path: Path) -> dict[str, str]:
    hooks = json.loads(path.read_text(encoding="utf-8"))["hooks"]
    out = {}
    for name, (event, matcher) in ENTRIES.items():
        (entry,) = [
            e
            for e in hooks[event]
            if e["matcher"] == matcher or (matcher[-1] == "|" and e["matcher"].startswith(matcher))
        ]
        (hook,) = entry["hooks"]
        out[name] = hook["command"]
    return out


OLD = _commands(OLD_HOOKS)
NEW = _commands(NEW_HOOKS)


class World:
    """A vault, a folder outside it, and a plugin root whose scoutctl only records that it started."""

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.vault = tmp / "vault"
        (self.vault / "projects" / "repo").mkdir(parents=True)
        (self.vault / "scout-config.yaml").write_text("x: 1\n")
        self.outside = tmp / "outside"
        self.outside.mkdir()
        self.home = tmp / "home"
        self.home.mkdir()
        self.root = tmp / "plugin"
        (self.root / "engine" / "bin").mkdir(parents=True)
        (self.root / "hooks").mkdir()
        (self.root / "hooks" / "prefilter.sh").symlink_to(PREFILTER)
        stub = self.root / "engine" / "bin" / "scoutctl"
        stub.write_text('#!/bin/sh\n{ printf \'%s\\n\' "$*"; /bin/cat; } >"$STUB_LOG"\nexit "${STUB_EXIT:-0}"\n')
        stub.chmod(0o755)
        self.logs = tmp / "logs"
        self.logs.mkdir()
        self._n = 0

    def cwd(self, where: str) -> Path:
        return {"vault": self.vault, "deep": self.vault / "projects" / "repo", "outside": self.outside}[where]

    def run(self, command: str, case: Case) -> Outcome:
        self._n += 1
        log = self.logs / f"{self._n}.log"
        env = {
            "HOME": str(self.home),
            "PATH": "/usr/bin:/bin",
            "CLAUDE_PLUGIN_ROOT": str(self.root),
            "STUB_LOG": str(log),
            "STUB_EXIT": str(case.stub_exit),
            **{k: v.replace("{vault}", str(self.vault)) for k, v in case.env.items()},
        }
        out = subprocess.run(
            [case.shell, "-c", command],
            env=env,
            cwd=self.cwd(case.cwd),
            input=case.payload,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        python = log.read_text(encoding="utf-8") if log.exists() else ""
        return Outcome(out.returncode, out.stdout, out.stderr, python)


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> World:
    return World(tmp_path_factory.mktemp("prefilter"))


def _with_cwd(world: World, case: Case, where: str | None = None) -> Case:
    """``case`` with a top-level ``"cwd"`` in its payload, for a PAYLOAD_CWD entry whose payload is a JSON
    object (as Claude Code sends it: cwd before the tool call). The place is ``where``, by default the
    one the case runs in."""
    payload = case.payload.lstrip()
    if case.entry not in PAYLOAD_CWD or not payload.startswith("{"):
        return case
    rest = payload[1:]
    sep = "" if rest.lstrip().startswith("}") else ", "
    place = str(world.cwd(where or case.cwd))
    return case._replace(payload='{"cwd": ' + json.dumps(place) + sep + rest)


def _differences(world: World, cases: list[Case]) -> list[str]:
    """The cases where the old and the new command disagree, one line each."""

    def both(case: Case) -> str | None:
        case = _with_cwd(world, case)
        old, new = world.run(OLD[case.entry], case), world.run(NEW[case.entry], case)
        if old == new or _intended(case):
            return None
        return f"{case.id}: old={old!r} new={new!r}"

    with ThreadPoolExecutor(max_workers=8) as pool:
        return [d for d in pool.map(both, cases) if d]


def _bash(command: str, *, compact: bool = False) -> str:
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    return json.dumps(payload, separators=(",", ":")) if compact else json.dumps(payload)


def _harvested() -> list[str]:
    """Every string literal of the gate tests: their example commands, and plenty that are not commands.
    Any string is a fair input here, since the only claim is that both commands treat it alike."""
    found: dict[str, None] = {}
    for rel in CORPUS_SOURCES:
        for node in ast.walk(ast.parse((ENGINE / rel).read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and 1 < len(node.value) <= 600:
                if "\x00" not in node.value:
                    found.setdefault(node.value)
    return list(found)


# ----- the Bash pattern, as text --------------------------------------------------------------


def _pattern(command: str) -> str:
    start = command.index("grep -qE '") + len("grep -qE '")
    return command[start : command.index("'", start)]


def _balanced(pattern: str) -> bool:
    """True when ``pattern``'s groups all open and close inside it, so it is a run of whole alternatives."""
    depth, i = 0, 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "[":  # a bracket expression: `]` first (after an optional ^) is literal, as is `\`
            i += 1
            if pattern[i] == "^":
                i += 1
            if pattern[i] == "]":
                i += 1
            while pattern[i] != "]":
                i = pattern.index(":]", i) + 2 if pattern.startswith("[:", i) else i + 1
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return False
        i += 1
    return depth == 0


def test_the_bash_pattern_is_the_old_one_minus_its_repeated_block() -> None:
    old = _pattern(OLD["kb-read-gate"])
    new = subprocess.run(
        ["/bin/sh", str(PREFILTER), "print-bash-pattern"], capture_output=True, text=True, check=True
    ).stdout.rstrip("\n")

    start = old.index("(^|[^A-Za-z0-9_./-]|\\\\[nt])(env|nohup|")
    end = old.index("SCOUT_SECRETS", start) + len("SCOUT_SECRETS")
    block = old[start:end]
    head, tail = old[:start], old[end + 1 + len(block) :]

    assert len(block) == 1117
    assert old == f"{head}{block}|{block}{tail}"  # the same text twice, side by side
    assert head.endswith("|") and tail.startswith("|")
    # Each piece is a run of whole top-level alternatives, so `X|B|B|Y` and `X|B|Y` match the same lines.
    assert _balanced(head[:-1]) and _balanced(block) and _balanced(tail[1:])
    assert new == f"{head}{block}{tail}"
    assert "'" not in new and "\n" not in new


def test_the_fixture_is_the_inline_hooks_json() -> None:
    assert len(OLD["kb-read-gate"]) == 4105
    assert all("prefilter.sh" not in c for c in OLD.values())
    assert all(c.count("prefilter.sh") >= 1 for c in NEW.values())
    # Entries are short calls now. mcp-tool-gate's also carries the vault walk that keeps it closed when
    # neither the script nor the engine can run (test_mcp_tool_gate_fail_closed.py): it can't live in
    # the script, since it is for when the script is missing.
    assert max(len(c) for name, c in NEW.items() if name != "mcp-tool-gate") < 700
    assert len(NEW["mcp-tool-gate"]) < 900


# ----- the differential runs ------------------------------------------------------------------


def _matching_lines(pattern: str, lines: list[str]) -> set[int]:
    """The 1-based numbers of the ``lines`` that ``grep -E pattern`` matches, in one grep process."""
    out = subprocess.run(
        ["grep", "-nE", pattern],
        input="".join(f"{line}\n" for line in lines),
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
        check=False,
    )
    assert out.returncode in (0, 1), out.stderr
    return {int(line.split(":", 1)[0]) for line in out.stdout.splitlines()}


def test_bash_corpus_matches_the_old_pattern_exactly() -> None:
    """Every string of the gate tests, as the hook sees it (the payload's JSON-escaped command value, one
    line each), through the old pattern and the new one. One grep per pattern, so the whole corpus costs
    two processes; the end-to-end runs below cover the shell around the pattern."""
    corpus = list(dict.fromkeys([*DESCRIBED_FORMS, *(form for form, _ in KNOWN_MISSED_FORMS), *_harvested()]))
    assert len(corpus) > 1000
    values = ['"command": ' + json.dumps(c) for c in corpus]
    assert all("\n" not in v for v in values)
    new_pattern = subprocess.run(
        ["/bin/sh", str(PREFILTER), "print-bash-pattern"], capture_output=True, text=True, check=True
    ).stdout.rstrip("\n")

    old, new = _matching_lines(_pattern(OLD["kb-read-gate"]), values), _matching_lines(new_pattern, values)
    assert old == new, sorted(corpus[n - 1] for n in old ^ new)
    # The corpus exercises both answers, or the comparison above proves little.
    assert len(new) > 300 and len(values) - len(new) > 300


@pytest.mark.slow
def test_bash_forms_under_every_reader_shell_and_failure(world: World) -> None:
    cases = []
    # This filter asks only "is it a run?"; owner sessions are covered by the entry matrix below.
    readers = ({"SCOUT_READER": "agent:helper"},)
    for i, command in enumerate((*DESCRIBED_FORMS, *(form for form, _ in KNOWN_MISSED_FORMS))):
        for j, env in enumerate(readers):
            cases.append(
                Case(f"bash[{i}] env[{j}] {command!r}", "kb-read-gate", _bash(command, compact=True), env, "deep")
            )
        cases += [
            Case(f"bash[{i}] python fails {command!r}", "kb-read-gate", _bash(command), RUN, "vault", stub_exit=1),
            Case(f"bash[{i}] bash {command!r}", "kb-read-gate", _bash(command), RUN, "outside", shell="/bin/bash"),
            Case(
                f"bash[{i}] secrets dir {command!r}",
                "kb-read-gate",
                _bash(command),
                {**RUN, "SCOUT_SECRETS_DIR": "/opt/secret-store"},
                "vault",
            ),
        ]
    assert _differences(world, cases) == []


TOOL_PAYLOADS = (
    '{"tool_name": "Grep", "tool_input": {"pattern": "x", "path": "notes"}}',
    '{"tool_name": "Glob", "tool_input": {"pattern": "**/*.md"}}',
    '{"tool_name": "Read", "tool_input": {"file_path": "/v/knowledge-base/people/a.md"}}',
    '{"tool_name": "Read", "tool_input": {"file_path": "/v/knowledge_base/a.md"}}',
    '{"tool_name": "Read", "tool_input": {"file_path": "/v/action-items/context/a.md"}}',
    '{"tool_name": "Read", "tool_input": {"file_path": "/v/README.md"}}',
    '{"tool_name": "Read", "tool_input": {"file_path": "/h/.SCOUT-secrets/key"}}',
    '{"tool_name": "Read", "tool_input": {"file_path": "/v/.scout-inbox/agents/d1/a.md"}}',
    '{"tool_name": "Read", "tool_input": {"file_path": "/v/Agents/a.md"}}',
    '{"tool_name": "Read", "tool_input": {"file_path": "/opt/secret-store/key"}}',
    '{"tool_name": "Write", "tool_input": {"file_path": "/v/projects/widgets/widgets.md", "content": "x"}}',
    '{"tool_name": "Write", "tool_input": {"file_path": "/v/projects/widgets/notes.md", "content": "a/a.md"}}',
    '{"tool_name": "Edit", "tool_input": {"file_path": "/v/.claude/settings.json"}}',
    '{"tool_name": "Edit","tool_input": {"file_path":"work/gateway/gateway.md"}}',
    '{"tool_name": "mcp__slack__send_message", "tool_input": {"text": "knowledge-base/a.md"}}',
    '{"tool_name": "mcp__x__shared_knowledge_read", "tool_response": {"text": "note"}}',
    "{}",
    "",
    "not json\n\n",
)


@pytest.mark.slow
def test_every_entry_under_every_reader_place_and_failure(world: World) -> None:
    cases = []
    reads_payload = {"kb-read-gate", "kb-read-gate-read", "kb-read-label", "front-page-reads"}
    for entry in ENTRIES:
        payloads = TOOL_PAYLOADS if entry in reads_payload else TOOL_PAYLOADS[:: len(TOOL_PAYLOADS) // 3]
        for i, payload in enumerate(payloads):
            for j, env in enumerate(READERS):
                # Every reader in a vault; Python failing, and no vault, for a run and for the owner.
                combos = [("vault", 0)]
                if env in ({}, RUN):
                    combos += [("vault", 1), ("outside", 0)]
                for where, stub_exit in combos:
                    cid = f"{entry} payload[{i}] env[{j}] {where} exit={stub_exit}"
                    cases.append(Case(cid, entry, payload, env, where, stub_exit))
            cases.append(Case(f"{entry} payload[{i}] outside, python fails", entry, payload, RUN, "outside", 1))
            secrets = {**RUN, "SCOUT_SECRETS_DIR": "/opt/secret-store"}
            cases.append(Case(f"{entry} payload[{i}] secrets dir", entry, payload, secrets, "outside"))
            data_dir = {"SCOUT_DATA_DIR": "{vault}"}
            cases.append(Case(f"{entry} payload[{i}] data dir", entry, payload, data_dir, "outside"))
            cases.append(Case(f"{entry} payload[{i}] bash", entry, payload, RUN, "deep", shell="/bin/bash"))
    assert _differences(world, cases) == []


@pytest.mark.parametrize(
    "config",
    ["", "y: 1\n", "restrict_tools: {}\n", '"restrict\\u005ftools": {}\n', "y: 1\x00\n", "# restrict_tools\n"],
    ids=["empty", "plain", "key", "escaped", "nul", "comment"],
)
def test_mcp_tool_gate_reads_the_connectors_file_as_before(tmp_path: Path, config: str) -> None:
    world = World(tmp_path)
    (world.vault / "connectors.custom.yaml").write_bytes(config.encode())
    cases = [
        Case(f"mcp {where} env[{j}]", "mcp-tool-gate", "{}", env, where, stub_exit)
        for where in ("vault", "deep", "outside")
        for j, env in enumerate(({}, RUN, {"SCOUT_DATA_DIR": "{vault}"}))
        for stub_exit in (0, 1)
    ]
    assert _differences(world, cases) == []


# ----- what the move itself must guarantee ----------------------------------------------------


@pytest.mark.slow
def test_an_owner_session_goes_by_the_payload_cwd_not_its_own(world: World) -> None:
    """The one change of #803's follow-up 1, as a differential: an owner session whose hook runs in one
    place while the payload's cwd names another. For each owner-session filter, the new command run from
    anywhere decides exactly as the old one did when run from the payload's cwd (where the session is),
    both ways: hook outside and session in the vault, and hook in the vault and session outside."""
    payloads = [p for p in TOOL_PAYLOADS if p.startswith("{")]
    moves = (("outside", "vault"), ("outside", "deep"), ("vault", "outside"), ("deep", "outside"))
    cases = [
        (_with_cwd(world, Case(f"{entry} payload[{i}] env[{j}] {here}->{there}", entry, p, env, here, x), there), there)
        for entry in ("outbound-gate", "kb-read-label", "shared-read-record")
        for i, p in enumerate(payloads)
        for j, env in enumerate(OWNERS)
        for here, there in moves
        for x in (0, 1)
    ]

    def compare(item: tuple[Case, str]) -> tuple[str | None, bool]:
        case, there = item
        new = world.run(NEW[case.entry], case)
        by_session = world.run(OLD[case.entry], case._replace(cwd=there))
        by_hook = world.run(OLD[case.entry], case)
        differs = None if new == by_session else f"{case.id}: new={new!r} old-from-the-session={by_session!r}"
        return differs, by_hook != new

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(compare, cases))
    assert [d for d, _ in results if d] == []
    # Not vacuous: for each filter, the old command run where the hook runs decided otherwise somewhere.
    for entry in ("outbound-gate", "kb-read-label", "shared-read-record"):
        assert any(moved for (case, _), (_, moved) in zip(cases, results, strict=True) if case.entry == entry), entry


def test_an_owner_session_never_starts_python_for_the_run_only_hooks(world: World) -> None:
    for entry in ("kb-read-gate", "kb-read-gate-read", "kb-write-gate", "front-page-reads"):
        for env in ({}, {"SCOUT_READER": "owner"}):
            for payload in (_bash("cat knowledge-base/a.md"), TOOL_PAYLOADS[2], TOOL_PAYLOADS[10]):
                out = world.run(NEW[entry], Case("owner", entry, payload, env, "vault"))
                assert out == Outcome(0, "", "", ""), (entry, env, payload)


def test_known_missed_forms_are_kept_exactly_as_they_were(world: World) -> None:
    for form, python_judges in KNOWN_MISSED_FORMS:
        case = Case(form, "kb-read-gate", _bash(form), RUN, "vault")
        old, new = world.run(OLD["kb-read-gate"], case), world.run(NEW["kb-read-gate"], case)
        assert old == new, form
        assert bool(new.python) is python_judges, form


# ----- a pre-filter that is missing or broken is never trusted ---------------------------------

GATES = ("kb-read-gate", "kb-read-gate-read", "kb-write-gate", "outbound-gate")
RUN_ONLY_GATES = GATES[:3]
# For each gate, a payload its filter sends to Python in a run.
JUDGED = {
    "kb-read-gate": _bash("cat knowledge-base/a.md"),
    "kb-read-gate-read": TOOL_PAYLOADS[6],
    "kb-write-gate": TOOL_PAYLOADS[10],
    "outbound-gate": TOOL_PAYLOADS[14],
}
RUNS: tuple[dict[str, str], ...] = (RUN, {"SCOUT_READER": "agent:helper"}, {"SCOUT_READER": ""})
OWNERS: tuple[dict[str, str], ...] = ({}, {"SCOUT_READER": "owner"})


DIRECTORY = "<a directory>"


def _world_with_script(tmp_path: Path, text: str | None, mode: int = 0o755) -> World:
    """A world whose prefilter.sh is ``text`` (None: no script at all)."""
    world = World(tmp_path)
    script = world.root / "hooks" / "prefilter.sh"
    script.unlink()
    if text == DIRECTORY:
        script.mkdir()
    elif text is not None:
        script.write_bytes(text.encode())
        script.chmod(mode)
    return world


def _hook_of(entry: str) -> str:
    return "kb-read-gate" if entry == "kb-read-gate-read" else entry


@pytest.mark.parametrize("entry", GATES)
@pytest.mark.parametrize("content", [None, ""], ids=["missing", "empty"])
def test_a_missing_or_empty_script_sends_the_call_straight_to_python(
    tmp_path: Path, entry: str, content: str | None
) -> None:
    world = _world_with_script(tmp_path, content)
    for env in RUNS:
        judged = world.run(NEW[entry], Case("missing", entry, JUDGED[entry], env, "vault"))
        assert judged.returncode == 0 and judged.python == f"hook {_hook_of(entry)}\n{JUDGED[entry]}", (env, judged)
        failed = world.run(NEW[entry], Case("missing", entry, JUDGED[entry], env, "vault", stub_exit=1))
        assert failed.returncode == 2 and "is blocked in a scheduled run" in failed.stderr, (env, failed)

    (world.root / "engine" / "bin" / "scoutctl").unlink()  # neither stage can start
    for env in RUNS:
        blocked = world.run(NEW[entry], Case("missing", entry, JUDGED[entry], env, "vault"))
        assert blocked.returncode == 2 and "(exit 127)" in blocked.stderr, (env, blocked)
    for env in OWNERS:
        assert world.run(NEW[entry], Case("missing", entry, JUDGED[entry], env, "outside")).returncode != 2


@pytest.mark.parametrize("entry", GATES)
def test_a_script_without_its_executable_bit_works_the_same(world: World, tmp_path: Path, entry: str) -> None:
    plain = _world_with_script(tmp_path, PREFILTER.read_text(encoding="utf-8"), mode=0o644)
    for env in (*RUNS, *OWNERS):
        for payload in (JUDGED[entry], _bash("ls"), "{}"):
            case = Case("mode 644", entry, payload, env, "vault")
            assert plain.run(NEW[entry], case) == world.run(NEW[entry], case), (env, payload)


_REAL = PREFILTER.read_text(encoding="utf-8")
# Copies that are not the script. None of them may be trusted: a syntax error exits 2 (a hook's
# "block", for the owner too) and a no-op exits 0 (a hook's "allow", in a run too).
BROKEN = {
    "does not parse": "#!/bin/sh\nif then (\n",
    "only whitespace": "  \n\t\n\n",
    "only a comment": "# Scout hook pre-filters\n",
    "NUL-filled": "\x00" * 4096,
    "a directory": DIRECTORY,
    "exits 0": "#!/bin/sh\nexit 0\n",
    "exits 2": "#!/bin/sh\nexit 2\n",
    # A partial write during a plugin update: the brace group is left open, so the copy cannot parse.
    "cut in half": _REAL[: len(_REAL) // 2],
    "cut before its closing brace": _REAL[: _REAL.rindex("}")],
    "cut to its first line": _REAL[: _REAL.index("\n") + 1],
    "cut to its first character": _REAL[:1],
    "exits 1": "#!/bin/sh\nexit 1\n",
    "exits 7": "#!/bin/sh\nexit 7\n",
    "fails before the filter": '#!/bin/sh\nset -eu\n: "$NO_SUCH_VARIABLE"\nexit 0\n',
}


@pytest.mark.parametrize("how", list(BROKEN))
def test_a_broken_script_is_bypassed_as_if_it_were_missing(tmp_path: Path, how: str) -> None:
    """Whatever the copy does, each entry ends as it does with no script, plus any shell error: Python decides."""
    broken = _world_with_script(tmp_path / "broken", BROKEN[how])
    missing = _world_with_script(tmp_path / "missing", None)
    for entry in ENTRIES:
        payload = JUDGED.get(entry, TOOL_PAYLOADS[2])
        for env in (*RUNS, *OWNERS):
            for p, stub_exit in ((payload, 0), (payload, 1), (payload, 2), (_bash("ls"), 0)):
                case = Case(how, entry, p, env, "vault", stub_exit)
                got, want = broken.run(NEW[entry], case), missing.run(NEW[entry], case)
                # The broken copy may add its own shell diagnostic first; everything after it is the same.
                assert got._replace(stderr="") == want._replace(stderr=""), (entry, env, p, stub_exit)
                assert got.stderr.endswith(want.stderr), (entry, env, p, stub_exit)


def test_a_killed_script_blocks_a_runs_gated_call_and_never_the_owner(tmp_path: Path) -> None:
    """A filter killed mid-way may have read the payload already, so a gate does not hand Python an empty one."""
    world = _world_with_script(tmp_path, "#!/bin/sh\nkill -9 $$\n")
    for entry in ENTRIES:
        payload = JUDGED.get(entry, TOOL_PAYLOADS[2])
        for env in RUNS:
            out = world.run(NEW[entry], Case("killed", entry, payload, env, "vault"))
            if entry in (*GATES, "mcp-tool-gate"):
                assert out.returncode == 2 and "(exit 137)" in out.stderr and out.python == "", (entry, env, out)
            else:  # PostToolUse: nothing to block; Python labels or records
                assert out.returncode == 0 and out.python, (entry, env, out)
        for env in OWNERS:
            out = world.run(NEW[entry], Case("killed", entry, payload, env, "vault"))
            assert out.returncode != 2, (entry, env, out)
            if entry not in (*RUN_ONLY_GATES, "front-page-reads"):
                assert out.python, (entry, env, out)


def test_an_unknown_filter_or_a_missing_program_is_not_a_verdict() -> None:
    env = {"PATH": "/usr/bin:/bin"}
    for argv in (["no-such-filter", "/usr/bin/true"], ["kb-read-gate"], []):
        for extra in (RUN, {}):
            out = subprocess.run(
                ["/bin/sh", str(PREFILTER), *argv], env={**env, **extra}, input="{}", capture_output=True, text=True
            )
            assert out.returncode == 1 and "[scout:prefilter]" in out.stderr, (argv, extra)


def test_every_filter_ends_in_a_verdict() -> None:
    """The entries trust only 200 + code, so a filter that ends any other way is bypassed: slower, never
    wrong. Pin it anyway: outside `verdict` and the unknown-filter branch, the script never exits itself."""
    code = [line.split("#")[0] for line in PREFILTER.read_text(encoding="utf-8").splitlines()]
    exits = [line.strip() for line in code if "exit " in line or "exec " in line]
    assert exits == ['if [ "$1" -le 55 ]; then exit $((200 + $1)); fi', "exit 255", "exit 1"], exits


# ----- wiring ---------------------------------------------------------------------------------


def test_each_entry_names_its_filter_and_the_hook_it_may_start() -> None:
    script = PREFILTER.read_text(encoding="utf-8")
    paths = 'h="${CLAUDE_PLUGIN_ROOT}/hooks/prefilter.sh"; c="${CLAUDE_PLUGIN_ROOT}/engine/bin/scoutctl"; '
    is_run = '[ -n "$SCOUT_MODE" ] || { [ -n "${SCOUT_READER+set}" ] && [ "$SCOUT_READER" != owner ]; }'
    for name, command in NEW.items():
        hook = _hook_of(name)
        assert command.count(f'/bin/sh "$h" {name} "$c" hook {hook}') == 1, name
        assert command.count(f'"$c" hook {hook}') == 2, name  # through the filter, and without it if it is missing
        assert command.startswith((paths, f"{is_run} || exit 0; {paths}")), name
        assert f"\n{name})\n" in script, name
    filters = [
        line[:-1] for line in script.splitlines() if line.endswith(")") and line[:1].isalpha() and " " not in line
    ]
    assert sorted(filters) == sorted([*ENTRIES, "print-bash-pattern"])


def test_the_script_ships_inside_the_plugin_and_is_executable() -> None:
    assert PREFILTER.parent == PLUGIN / "hooks" and os.access(PREFILTER, os.X_OK)
    # One brace group from the first byte to the last: no prefix of the file is a script that runs.
    text = PREFILTER.read_text(encoding="utf-8")
    assert text.startswith("{ #") and text.endswith("\n}\n")


# ----- the filters' own tools -----------------------------------------------------------------

# Calls each run-only filter must send to Python, whatever PATH the session has.
NEEDS_PYTHON = {
    "kb-read-gate": [_bash("rm -rf x"), _bash("cat kno*/a.md"), _bash("cat know'ledge'-base/a.md"), TOOL_PAYLOADS[0]],
    "kb-read-gate-read": [TOOL_PAYLOADS[6], TOOL_PAYLOADS[7]],
    "front-page-reads": [TOOL_PAYLOADS[10]],
}


@pytest.mark.parametrize("path", ["", "/opt/homebrew/bin", "/nonexistent", "/usr/local/bin:/nonexistent"])
def test_a_path_without_the_system_tools_changes_no_decision(world: World, path: str) -> None:
    """The inline commands looked cat, grep and tr up in PATH, so a run whose PATH lacked them (macOS keeps
    cat in /bin only) let every call through. The script takes them from /usr/bin or /bin first."""
    for entry, payloads in NEEDS_PYTHON.items():
        for payload in payloads:
            out = world.run(NEW[entry], Case("path", entry, payload, {**RUN, "PATH": path}, "vault"))
            assert out.returncode == 0 and out.python == f"hook {_hook_of(entry)}\n{payload}", (entry, payload, out)
    harmless = world.run(NEW["kb-read-gate"], Case("path", "kb-read-gate", _bash("ls"), {**RUN, "PATH": path}, "vault"))
    assert harmless == Outcome(0, "", "", "")


def test_the_old_commands_let_a_run_through_without_cat() -> None:
    """What the fix above is for, shown on the inline command (no behaviour of the new code here)."""
    out = subprocess.run(
        ["/bin/sh", "-c", OLD["kb-read-gate"]],
        env={"PATH": "/nonexistent", "SCOUT_MODE": "briefing", "CLAUDE_PLUGIN_ROOT": "/nonexistent"},
        input=_bash("rm -rf x"),
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.returncode == 0  # allowed, and no gate ran


def test_a_payload_that_cannot_be_read_blocks_a_run(tmp_path: Path) -> None:
    """cat failing (here: stdin closed) leaves nothing for Python to judge, so the gates block."""
    world = World(tmp_path)
    for entry in ("kb-read-gate", "kb-read-gate-read"):
        out = subprocess.run(
            ["/bin/sh", "-c", "exec 0<&-; " + NEW[entry]],
            env={
                "PATH": "/usr/bin:/bin",
                "SCOUT_MODE": "briefing",
                "CLAUDE_PLUGIN_ROOT": str(world.root),
                "STUB_LOG": str(tmp_path / "log"),
            },
            capture_output=True,
            text=True,
            check=False,
            timeout=30,  # cat blocking on a closed stdin hung here once; fail instead
        )
        assert out.returncode == 2 and "could not read" in out.stderr, (entry, out)
