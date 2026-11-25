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

One deliberate change so far: the Bash pattern gained ``EXPANSION_ALTERNATIVES`` (a glob, a ``$``, a backtick, a
brace list, a leading ``~``, an absolute path, ``xargs``, a ``cd`` into action-items/), because a word the shell
rewrites need not spell the KB path it becomes. The pattern must be the old one plus exactly those, and a case may
differ from the old command only by now reaching Python where the old one let it through, for a command those
alternatives match. And a run's payload over ``MAX_PAYLOAD`` bytes is blocked before either stage (no corpus case
is that long).

The git-read change added four more alternatives at the end: any ``--git-dir``, ``--work-tree``, ``GIT_DIR`` or
``GIT_WORK_TREE`` (a git call that picks its own repository or top), and a git call whose paths cover the KB without
naming it (one naming action-items/, a ``.``/``..``/``:/x``/``:(top)x`` pathspec, ``git archive``). Every form
``test_kb_read_gate_git.py`` must deny or record reaches Python (``GIT_FORMS``).
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import NamedTuple

import pytest

from tests.unit.test_kb_read_gate_git import MUST_DENY as GIT_MUST_DENY
from tests.unit.test_kb_read_gate_git import UNRESOLVED_GIT

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

# The alternatives appended to the Bash pattern after the move, as prefilter.sh assembles them: a word the shell
# rewrites before the program sees it (so its text need not spell the KB path it becomes), a program that takes
# its paths from another's output, and a change of directory into action-items/.
EXPANSION_ALTERNATIVES = (
    r"\*",
    r"(^|[^$])\?",
    r"(^|[^[])\[[^ []",
    r"\[\[[^ ]",
    r"\$([^?#!$]|$)",
    "`",
    r"\{[^}]*(,|\.\.)",
    r"(^|[^A-Za-z0-9_.}^@/~-]|\\[nt])~",
    r"(^|[^A-Za-z0-9_.~-]|\\[nt])/",
    r"(^|[^A-Za-z0-9_.-]|\\[nt])(xargs|parallel)([^A-Za-z0-9_.-]|$)",
    r"(cd|pushd|-C|--directory|--git-dir|--work-tree)[^;&|]*action-items",
    r"\(",
    # a git call that picks its repository or top, or whose paths cover the KB without naming it
    "--git-dir|--work-tree|GIT_DIR|GIT_WORK_TREE",
    "git[^;&|]*action-items",
    r'git[^;&|]* (\.\.?/?|:/[^ ;&|]*|:\([^ ;&|]*)( |\\"|"|$)',
    "git[^;&|]* archive",
)
EXPANSION_PATTERN = "|".join(EXPANSION_ALTERNATIVES)
# Rules prefilter.sh also applies ignoring letter case, to the command value as is and again with its quotes and
# backslashes gone: a program fed paths by another, a cd into action-items/ (on macOS's case-insensitive disk
# ACTION-ITEMS is the folder and XARGS runs xargs), and, on the stripped value only, a KB folder's name.
LATE_RULES = (
    r"(^|[^A-Za-z0-9_.-]|\\[nt])(xargs|parallel)([^A-Za-z0-9_.-]|$)"
    r"|(cd|pushd|-C|--directory|--git-dir|--work-tree)[^;&|]*action-items"
)
STRIPPED_RULES = rf"knowledge[-_]base|action-items/context|{LATE_RULES}"
MAX_PAYLOAD = 32768  # bytes; a run's kb-read-gate payload over this is blocked in the shell

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
# shell lets it through unjudged. The move to prefilter.sh fixed none of them and worsened none. A fix
# flips a False to True here, in the PR that makes it; the expansion alternatives flipped those marked
# so (a `$`, or an absolute path, now reaches Python, which decides as it always has).
KNOWN_MISSED_FORMS: tuple[tuple[str, bool], ...] = (
    # #610: env changes and detaching through a wrapper or an indirect name
    ("command -p env -u FOO git commit -m x", True),
    ("nice -n 0 env -u FOO ls", True),
    ("timeout 60 env -u FOO ls", True),
    ("time -p ls", False),
    ("exec -a x bash -c ls", True),
    ("unset $v", True),  # expansion: `$`
    ("$'SCOUT\\x5fMODE'= git commit -m x", True),  # expansion: `$`
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
    ('git -C "$R" $SUB -m x', True),  # expansion: `$`
    ("git -C $R $SUB", True),  # expansion: `$`
    ("git rebase --exec 'git commit --no-verify' HEAD~1", True),
    ("timeout 5 rm .git/hooks/x", True),
    ("git stash create", False),
    ("git branch -f main abc123", False),
    ("git merge --ff-only abc123", False),
    ("cd .git/hooks && ls > ../../out", True),
    # #611: persistence through a shell rc file
    ("echo x >> ~/.zshrc", True),
    # a launcher or `env` called by its full path: the detach group wants no `/` before the name, but an
    # absolute path now reaches Python on its own
    ("/usr/bin/nohup sleep 9", True),  # expansion: absolute path
    ("/usr/bin/env -i ls", True),  # expansion: absolute path
    ("/usr/bin/open notes.txt", True),  # expansion: absolute path
    ("/bin/launchctl list", True),  # expansion: absolute path
    ("/usr/bin/ssh host ls", True),  # expansion: absolute path
)

# The forms #779's security review found: each reads a KB note in a run, and #779's filter let each through.
REVIEW_FORMS = (
    "cat k*/projects/a/a.md",
    "head k*e/*/*.md",
    "K=knowledge; cat ${K}-base/x.md",
    'cat "$(printf knowl%sdge-base/a.md e)"',
    "less $HOME/Scout/kn*",
)

# The forms the Python gate's own tests (#790) deny, or must see to record as unresolved, that the lists above
# do not already hold. The shell stage must send each to Python, or #790's answer is never asked for.
GATE_FORMS = (
    "cat $HOME/Vault/action-items/c*/x.md",
    "wc -l < k*/a.md",
    "grep -n x k*/a.md",
    "sed -n 1,5p action-items/con*/x.md",
    "find k* -name '*.md'",
    "export D=action-items; cat $D/context/x.md",
    "for d in docs knowledge-base; do cat $d/a.md; done",
    "cat $'\\x6bnowledge-base/x.md'",
    "cat k{m..o}owledge-base/a.md",
    "echo knowledge-base/a.md | xargs head -3",
    "cd action-items; ls context",
    "pushd action-items && cat context/x.md",
    "env -C action-items cat context/x.md",
    "bash -c 'cat k*/a.md'",
    "timeout 5 cat k*/a.md",
    "if true; then head k*/x.md; fi",
    'f=knowledge-base/a.md && echo "lines: $(wc -l < $f)"',
    'for f in docs/a.md knowledge-base/a.md; do echo "$(grep -c x $f)"; done',
    'grep -n "alpha\\|beta" knowledge-base/a.md',
    "sed -n '/^## /{=;p;}' knowledge-base/a.md",
    "git ls-files | xargs grep -l alpha",
    "find . -name '*.md' | xargs cat",
    "find action-items -type f | xargs wc -l",
    "grep -rl alpha . | xargs cat",
    "rg --files knowledge-base | xargs head -1",
    "echo knowledge-base/a.md | xargs -I{} cat {}",
    "echo knowledge-base/a.md | xargs -I % head %",
    'cat "$HOME"/Vault/kn*/projects/a/a.md # it\'s',
    'grep -n x "$HOME"/Vault/k*/a.md # don\'t',
    "cat **/x.md",
    "grep -n x **/a.md",
    "cat k*/**/a.md",
    "cat (knowledge|x)-base/a.md",
    "cat kn(o|)wledge-base/a.md",
    "ls (knowledge-base|docs)",
    "cat 'kn'(o|)wledge-base/a.md",
    "cat knowledge-bas[e\\$]/a.md",
    "cat k[n$]owledge-base/a.md",
    "cat ~+/k*/a.md",
    "cat ${HOME:-/nowhere}/Vault/k*/a.md",
    "cat ${UNSET_HERE:-k}nowledge-base/a.md",
    "cat ${PWD%/}/k*/a.md",
    "d=knowledge-base/; cat ${d%/}/a.md",
    "f=docs/knowledge-base; cat ${f#docs/}/a.md",
    "ls action-items/*",
    "grep -rn x action-items/*",
    "ag x k*",
    "cat `echo knowledge-base/a.md`",
    "cat $UNSET_SOMEWHERE/a.md",
    'while read -r f; do cat "$f"; done < /tmp/list.txt',
    "ls scripts | xargs cat",
    "cat < $(echo /tmp/x)",
    'cd "$(git rev-parse --show-toplevel)" && cat docs/a.md',
    "cat action-items/$(date +%Y/%m)/x.md",
    "cat action-items/$(TZ=$(scripts/run.sh) date +%F; cat x).md",
    "find docs -exec basename {} \\; | xargs cat",
    "git ls-files --full-name docs | xargs cat",
    "find docs | sort | xargs cat",
    "find docs | xargs -I{} cat x{}",
    "wc -l *.md",
    "echo knowledge-base/a.md | xargs -I{} head -1 {}",
    'f=knowledge-base/a.md && echo "$(head -1 $f)"',
    "for f in " + " ".join(f"docs/n{i}.md" for i in range(300)) + " knowledge-base/a.md; do cat $f 2>/dev/null; done",
    # zsh alternation with no glob character: a group glued to a word, or to another group
    "cat (a|b)(c|d)/x.md",
    "cat kn(o|w)ledge-base/a.md",
    "ls (docs|kn)",
    # a KB folder named in another letter case (one folder on a case-insensitive disk)
    "cat KNOWLEDGE-BASE/x.md",
    "head Knowledge_Base/a.md",
    "cat ACTION-ITEMS/Context/x.md",
    "cat KNOW''LEDGE-BASE/x.md",
    # review of #798: quoting, a backslash or letter case hid a cd or an xargs
    "cd action\\-items && cat context/a.md",
    "cd 'action'-items && cat context/a.md",
    "cd ACTION-ITEMS && cat context/a.md",
    "ls | x\\args cat",
    "ls | XARGS cat",
    'cd "action-items" && cat context/a.md',
    "true\nXARGS cat",
    "ls | Parallel cat",
    "git -C Action-Items show HEAD:context/a.md",
    # an even run of `$` before a quote that holds a backslash: bash and zsh split it differently (#790)
    "cat x$$'\\' k*/a.md '",
)

# Every git read the Python gate denies or records (test_kb_read_gate_git.py), and a few more like them: git finds
# the note through a folder (-C, a cd, --git-dir/--work-tree, GIT_DIR/GIT_WORK_TREE) or a path that covers it.
GIT_FORMS = (
    *GIT_MUST_DENY,
    *UNRESOLVED_GIT,
    "git log -p -- action-items",
    "git log -p -- .",
    "git log -p -- ./",
    "git diff HEAD -- .",
    "git -C docs log -p -- ..",
    "git log -p -- ':(top)'",
    "git grep x -- :/",
    "git show HEAD:action-items/./context/x.md",
    "git archive HEAD | tar x -C /tmp/out",
    "git archive -o /tmp/v.tar HEAD",
    "GIT_DIR=/tmp/v/.git git show HEAD:./x",
    "git --work-tree=/tmp/w status -v",
    "git -C action-items status",
    "git add action-items/action-items-2026-11-23.md && git commit -m 'update'",
)

# Commands that read a KB note once the shell has expanded them, though their text spells no KB path (the
# review's forms, and close variants). In a run every one must reach Python. All synthetic.
EXPANSION_FORMS = (
    *REVIEW_FORMS,
    # a glob in a path word
    "cat */a.md",
    "ls k*",
    "cat kn?wledge-base/a.md",
    "cat k[n]owledge-base/a.md",
    "cat kn[[o]wledge-base/a.md",
    "cat action-items/[c]ontext/x.md",
    "wc -l */*/*",
    "cat 'k'*'/a.md'",
    'cat "kn"*/a.md',
    # a variable, a substitution, a backtick
    "K=knowledge-; cat ${K}base/x.md",
    "a=k; b=nowledge-base/a.md; cat $a$b",
    "cat $(echo kn)owledge-base/a.md",
    "cat `echo kn`owledge-base/a.md",
    "cat $'knowledge\\x2dbase/a.md'",
    "set -- knowledge; cat $1-base/a.md",
    "head -n 5 ${D:-knowledge}-base/a.md",
    # $HOME, ~, an absolute path
    "less ${HOME}/Vault/kn*",
    "cat ~/Vault/k*/a.md",
    "cat ~/Vault/kn\\owledge-base/a.md",
    "cd ~/Vault && cat kn''owledge-base/a.md",
    "cat /home/u/Vault/k*/a.md",
    "cat /home/u/link-to-notes/a.md",
    "x=~/Vault; ls $x",
    # a brace list or range
    "cat kn{o,}wledge-base/a.md",
    "cat {knowledge,x}-base/a.md",
    "cat knowledge{-,_}base/a.md",
    "cat action-items/{context,x}/x.md",
    # paths built by another program, a change of directory into action-items/
    "printf 'knowledge%sbase/a.md' - | xargs cat",
    "echo knowledge@base | tr @ - | xargs -I{} cat {}/a.md",
    "parallel cat ::: kn",
    "cd action-items && cat context/x.md",
    "pushd action-items; cat context/x.md",
    "git -C action-items show HEAD:context/x.md",
    *GIT_FORMS,
    # the same over two lines
    "true\ncat k*/a.md",
    "true\n~/Vault/kno",
    "true\n/bin/cat x",
    *GATE_FORMS,
)

# Their harmless neighbours, which must stay in the shell: no expansion, or one that cannot make a path.
NOT_EXPANSIONS = (
    "git status",
    "git log --oneline -5",
    "git log HEAD~1..HEAD",
    "git diff HEAD~3",
    "echo $?",
    "echo done; exit $?",
    "[ -f notes/a.md ] && echo yes",
    "[[ -n x ]] && echo yes",
    'grep -c "^- \\[ \\]" notes/a.md',
    "date +%Y-%m-%d",
    "wc -l notes/a.md",
    "scripts/scoutctl kb search widgets",
    "cat notes/a.md",
    "ls action-items",
    # ordinary git use in a run, which names no folder above the KB
    "git add docs/a.md",
    "git add docs/a.md && git commit -m 'update docs'",
    "git diff --stat",
    "git show --stat HEAD",
    "git log -n 3 --format=%s",
    "git diff HEAD -- docs/a.md",
    "git show HEAD:docs/a.md",
    "git log -p -3",
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
            encoding="utf-8",
            timeout=60,
            check=False,
        )
        python = log.read_text(encoding="utf-8") if log.exists() else ""
        return Outcome(out.returncode, out.stdout, out.stderr, python)


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> World:
    return World(tmp_path_factory.mktemp("prefilter"))


def _command_of(payload: str) -> str | None:
    try:
        command = json.loads(payload)["tool_input"]["command"]
    except (ValueError, KeyError, TypeError):
        return None
    return command if isinstance(command, str) else None


def _widened(case: Case, old: Outcome, new: Outcome, expansions: set[str]) -> bool:
    """The one difference allowed: a run's Bash call the old command let through unjudged, whose command the
    expansion alternatives match, now reaches Python with the same payload, and ends as Python's answer does."""
    if case.entry != "kb-read-gate" or _command_of(case.payload) not in expansions:
        return False
    if old != Outcome(0, "", "", ""):
        return False
    if new.python != f"hook kb-read-gate\n{case.payload}":
        return False
    if case.stub_exit == 0:
        return new[:3] == (0, "", "")
    return new.returncode == 2 and new.stdout == "" and "could not run" in new.stderr


def _expansion_matches(commands: list[str]) -> set[str]:
    """The commands this change sends to Python, as the hook sees them (the JSON-escaped command value):
    EXPANSION_PATTERN, LATE_RULES in any letter case, and STRIPPED_RULES once quotes and backslashes are gone."""
    values = ['"command": ' + json.dumps(c) for c in commands]
    matched = {commands[n - 1] for n in _matching_lines(EXPANSION_PATTERN, values)}
    for command, value in zip(commands, values, strict=True):
        stripped = re.sub(r"[\"'\\]", "", value)
        if re.search(LATE_RULES, value, re.I) or re.search(STRIPPED_RULES, stripped, re.I):
            matched.add(command)
    return matched


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
    """The cases where the old and the new command disagree, one line each, but for an intended widening."""
    expansions = _expansion_matches([c for c in {_command_of(case.payload) for case in cases} if c is not None])

    def both(case: Case) -> str | None:
        case = _with_cwd(world, case)
        old, new = world.run(OLD[case.entry], case), world.run(NEW[case.entry], case)
        if old == new or _intended(case) or _widened(case, old, new, expansions):
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


def _new_pattern() -> str:
    return subprocess.run(
        ["/bin/sh", str(PREFILTER), "print-bash-pattern"], capture_output=True, text=True, check=True
    ).stdout.rstrip("\n")


def test_the_bash_pattern_is_the_old_one_minus_its_repeated_block_plus_the_expansions() -> None:
    old = _pattern(OLD["kb-read-gate"])
    new = _new_pattern()

    start = old.index("(^|[^A-Za-z0-9_./-]|\\\\[nt])(env|nohup|")
    end = old.index("SCOUT_SECRETS", start) + len("SCOUT_SECRETS")
    block = old[start:end]
    head, tail = old[:start], old[end + 1 + len(block) :]

    assert len(block) == 1117
    assert old == f"{head}{block}|{block}{tail}"  # the same text twice, side by side
    assert head.endswith("|") and tail.startswith("|")
    # Each piece is a run of whole top-level alternatives, so `X|B|B|Y` and `X|B|Y` match the same lines.
    assert _balanced(head[:-1]) and _balanced(block) and _balanced(tail[1:])
    # The expansions only add whole alternatives at the end, so they can add matches and never remove one.
    assert all(_balanced(a) for a in EXPANSION_ALTERNATIVES)
    assert new == f"{head}{block}{tail}|{EXPANSION_PATTERN}"
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


def test_bash_corpus_matches_the_old_pattern_plus_the_expansions_exactly() -> None:
    """Every string of the gate tests, as the hook sees it (the payload's JSON-escaped command value, one
    line each), through the old pattern, the new one and the expansion alternatives alone. One grep per
    pattern, so the whole corpus costs three processes; the end-to-end runs below cover the shell around it.
    The new pattern matches what the old one did and what the expansions add, nothing else: no old match
    is lost, and nothing beyond the expansions is gained."""
    known = [form for form, _ in KNOWN_MISSED_FORMS]
    corpus = list(dict.fromkeys([*DESCRIBED_FORMS, *known, *EXPANSION_FORMS, *NOT_EXPANSIONS, *_harvested()]))
    assert len(corpus) > 1000
    values = ['"command": ' + json.dumps(c) for c in corpus]
    assert all("\n" not in v for v in values)

    old = _matching_lines(_pattern(OLD["kb-read-gate"]), values)
    new = _matching_lines(_new_pattern(), values)
    added = _matching_lines(EXPANSION_PATTERN, values)
    assert new == old | added, sorted(corpus[n - 1] for n in new ^ (old | added))
    assert old < new  # the widening widened
    # The corpus exercises both answers, or the comparison above proves little.
    assert len(old) > 300 and len(values) - len(new) > 300


@pytest.mark.slow
def test_bash_forms_under_every_reader_shell_and_failure(world: World) -> None:
    cases = []
    # This filter asks only "is it a run?"; owner sessions are covered by the entry matrix below.
    readers = ({"SCOUT_READER": "agent:helper"},)
    forms = (*DESCRIBED_FORMS, *(form for form, _ in KNOWN_MISSED_FORMS), *EXPANSION_FORMS, *NOT_EXPANSIONS)
    for i, command in enumerate(forms):
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


def test_known_missed_forms_are_kept_as_they_were_but_for_the_expansions(world: World) -> None:
    expansions = _expansion_matches([form for form, _ in KNOWN_MISSED_FORMS])
    for form, python_judges in KNOWN_MISSED_FORMS:
        case = Case(form, "kb-read-gate", _bash(form), RUN, "vault")
        old, new = world.run(OLD["kb-read-gate"], case), world.run(NEW["kb-read-gate"], case)
        assert old == new or _widened(case, old, new, expansions), form
        assert bool(new.python) is python_judges, form


# ----- the expansions: every form reaches Python in a run, and its harmless neighbours do not ------


def _judged(world: World, command: str, shell: str = "sh") -> bool:
    out = world.run(NEW["kb-read-gate"], Case(command, "kb-read-gate", _bash(command), RUN, "vault", shell=shell))
    assert out.returncode == 0 and out.stdout == "" and out.stderr == "", (command, out)
    return out.python == f"hook kb-read-gate\n{_bash(command)}"


@pytest.mark.parametrize("shell", ["sh", "/bin/bash"])
def test_every_expansion_form_reaches_python_in_a_run(world: World, shell: str) -> None:
    assert [c for c in EXPANSION_FORMS if not _judged(world, c, shell)] == []


def test_the_old_filter_let_the_reviews_forms_through() -> None:
    """What this change is for: the five forms #779's review found reached no gate before."""
    values = ['"command": ' + json.dumps(c) for c in REVIEW_FORMS]
    assert _matching_lines(_pattern(OLD["kb-read-gate"]), values) == set()
    unquoted = [c.replace("'", "").replace('"', "") for c in REVIEW_FORMS]
    assert not any(kb in c for c in unquoted for kb in ("knowledge-base", "knowledge_base"))


def test_harmless_neighbours_stay_in_the_shell(world: World) -> None:
    assert [c for c in NOT_EXPANSIONS if _judged(world, c)] == []


def _in_cwd(command: str, cwd: str, *, compact: bool) -> str:
    payload = {"session_id": "s1", "cwd": cwd, "tool_name": "Bash", "tool_input": {"command": command}}
    return json.dumps(payload, separators=(",", ":")) if compact else json.dumps(payload)


@pytest.mark.parametrize("compact", [True, False])
def test_a_run_whose_cwd_is_in_action_items_reaches_python(world: World, compact: bool) -> None:
    """From <vault>/action-items, `cat context/x.md` reads a KB note without naming one (#790's case)."""
    reach = [
        ("cat context/x.md", "/home/u/Vault/action-items"),
        ("cat ../context/x.md", "/home/u/Vault/action-items/done"),
        ("cat context/x.md", "/home/u/Vault/Action-Items"),
    ]
    stay = [
        ("cat a.md", "/home/u/Vault/docs"),
        ("cat a.md", "/home/u/Vault/action-items-old"),
        ("ls action-items", "/home/u/Vault"),
    ]
    for command, cwd in reach + stay:
        payload = _in_cwd(command, cwd, compact=compact)
        out = world.run(NEW["kb-read-gate"], Case(command, "kb-read-gate", payload, RUN, "vault"))
        assert out[:3] == (0, "", ""), (command, cwd, out)
        assert (out.python == f"hook kb-read-gate\n{payload}") is ((command, cwd) in reach), (command, cwd)
        owner = world.run(NEW["kb-read-gate"], Case(command, "kb-read-gate", payload, {}, "vault"))
        assert owner == Outcome(0, "", "", ""), (command, cwd)


def test_the_owner_still_never_starts_python_for_an_expansion(world: World) -> None:
    for command in EXPANSION_FORMS:
        for env in ({}, {"SCOUT_READER": "owner"}):
            out = world.run(NEW["kb-read-gate"], Case("owner", "kb-read-gate", _bash(command), env, "vault"))
            assert out == Outcome(0, "", "", ""), (command, env)


# ----- a payload too long to judge in time is blocked in a run -------------------------------------


def _sized(nbytes: int, unit: str = "ab cd ") -> str:
    """A Bash payload of exactly ``nbytes`` bytes (UTF-8) whose command is harmless words.

    Non-ASCII stays raw UTF-8, as Claude Code sends it (json.dumps would write `é` as the 6 bytes `\\u00e9`).
    """

    def payload_of(command: str) -> str:
        return json.dumps({"tool_name": "Bash", "tool_input": {"command": command}}, ensure_ascii=False)

    empty = len(payload_of("echo ").encode())
    unit_bytes = len(unit.encode())
    command = "echo " + unit * ((nbytes - empty) // unit_bytes)
    command += "x" * (nbytes - len(payload_of(command).encode()))
    payload = payload_of(command)
    assert len(payload.encode()) == nbytes
    return payload


@pytest.mark.parametrize("shell", ["sh", "/bin/bash"])
def test_a_payload_over_the_cap_is_blocked_in_a_run_without_python(world: World, shell: str) -> None:
    at_cap = world.run(NEW["kb-read-gate"], Case("cap", "kb-read-gate", _sized(MAX_PAYLOAD), RUN, "vault", shell=shell))
    assert at_cap == Outcome(0, "", "", "")  # harmless, and not over: the filter judged it, in the shell
    for payload in (_sized(MAX_PAYLOAD + 1), _sized(MAX_PAYLOAD + 2, "é "), _sized(4 * 1024 * 1024)):
        for env in RUNS:
            out = world.run(NEW["kb-read-gate"], Case("over", "kb-read-gate", payload, env, "vault", shell=shell))
            assert out.returncode == 2 and out.python == "" and f"over {MAX_PAYLOAD} bytes" in out.stderr, env
        for env in OWNERS:
            assert world.run(NEW["kb-read-gate"], Case("over", "kb-read-gate", payload, env, "vault")) == Outcome(
                0, "", "", ""
            )


def test_the_cap_counts_bytes_in_a_utf8_locale(world: World) -> None:
    payload = _sized(MAX_PAYLOAD + 2, "é ")
    assert len(payload) <= MAX_PAYLOAD  # fewer characters than the cap, more bytes
    for locale in ({"LC_ALL": "en_US.UTF-8"}, {"LANG": "en_US.UTF-8"}, {"LC_ALL": "C.UTF-8"}):
        out = world.run(NEW["kb-read-gate"], Case("utf8", "kb-read-gate", payload, {**RUN, **locale}, "vault"))
        assert out.returncode == 2 and f"over {MAX_PAYLOAD} bytes" in out.stderr, locale


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
