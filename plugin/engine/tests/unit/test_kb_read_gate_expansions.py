"""kb-read-gate judges a run's read by the path the shell hands the reading command, not only by how the
command spells it: globs, brace lists, variables the command sets, ANSI-C quoting, ``xargs`` fed by
``echo``/``printf`` and a ``cd`` are worked out first, and a read whose path the gate can't work out (a command
substitution, a backtick, an unset variable, ``xargs`` input it can't see) is denied in a run, as a write of
one is (#637 round 3). A ``$(date +FORMAT)`` whose output can't hold a ``/`` stands for any one name.

Synthetic vault only. The shell pre-filter (``plugin/hooks/prefilter.sh``) decides which of these reach Python;
these tests call the gate directly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.hooks import kb_read_gate as gate
from scout.kb import live_run
from scout.kb.raw_read import brace_expand, read_operands
from tests.unit.test_hooks_kb_read_gate import RUN, _denied, _payload


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A home with a vault at ``~/Vault`` holding a KB, action items and two ordinary folders."""
    h = tmp_path / "home"
    v = h / "Vault"
    for rel in (
        "knowledge-base/a.md",
        "knowledge-base/x.md",
        "knowledge-base/projects/a/a.md",
        "knowledge-base/people/priya.md",
        "action-items/context/x.md",
        "action-items/action-items-2026-11-23.md",
        "docs/a.md",
        "scripts/a.md",
        "scripts/run.sh",
    ):
        (v / rel).parent.mkdir(parents=True, exist_ok=True)
        (v / rel).write_text("x\n")
    (v / "knowledge-base.md").write_text("# KB\n")
    (v / "scout-config.yaml").write_text("user: {}\n")
    (h / "notes.txt").write_text("x\n")
    monkeypatch.setenv("SCOUT_DATA_DIR", str(v))
    return h.resolve()


def _run(home: Path, command: str, *, cwd: Path | None = None, env: dict | None = None):
    full = {**RUN, "HOME": str(home), **(env or {})}
    return gate.run(stdin=_payload("Bash", {"command": command}, cwd or home / "Vault"), env=full)


# ----- reads that only name a KB note once the shell expands them ----------------------------------


@pytest.mark.parametrize(
    "command",
    [
        # a glob
        "cat k*/projects/a/a.md",
        "head k*e/*/*.md",
        "cat */a.md",
        "ls k*",
        "cat ~/Vault/k*/a.md",
        "cat $HOME/Vault/action-items/c*/x.md",
        "wc -l < k*/a.md",
        "grep -n x k*/a.md",
        "sed -n 1,5p action-items/con*/x.md",
        "find k* -name '*.md'",
        # a variable the command sets
        "K=knowledge; cat ${K}-base/x.md",
        "a=k; b=nowledge-base/a.md; cat $a$b",
        "export D=action-items; cat $D/context/x.md",
        "for d in docs knowledge-base; do cat $d/a.md; done",
        # ANSI-C quoting
        "cat $'knowledge\\x2dbase/a.md'",
        "cat $'\\x6bnowledge-base/x.md'",
        # a brace list
        "cat kn{o,}wledge-base/a.md",
        "cat {knowledge,x}-base/a.md",
        "cat k{m..o}owledge-base/a.md",
        # xargs fed by echo or printf
        "printf 'knowledge%sbase/a.md' - | xargs cat",
        "echo knowledge-base/a.md | xargs head -3",
        # a cd, env -C, pushd
        "cd action-items && cat context/x.md",
        "cd action-items; ls context",
        "pushd action-items && cat context/x.md",
        "env -C action-items cat context/x.md",
        # a read through bash -c, a loop body, a prefix
        "bash -c 'cat k*/a.md'",
        "timeout 5 cat k*/a.md",
        "if true; then head k*/x.md; fi",
        # a variable read inside a substitution
        'f=knowledge-base/a.md && echo "lines: $(wc -l < $f)"',
        'for f in docs/a.md knowledge-base/a.md; do echo "$(grep -c x $f)"; done',
        # a KB path spelled out, past a `|` or `;` inside a quoted pattern (main split there)
        'grep -n "alpha\\|beta" knowledge-base/a.md',
        "sed -n '/^## /{=;p;}' knowledge-base/a.md",
        # xargs fed by a lister whose root is, or holds, the KB; or a replace string
        "git ls-files | xargs grep -l alpha",
        "find . -name '*.md' | xargs cat",
        "find action-items -type f | xargs wc -l",
        "grep -rl alpha . | xargs cat",
        "rg --files knowledge-base | xargs head -1",
        "echo knowledge-base/a.md | xargs -I{} cat {}",
        "echo knowledge-base/a.md | xargs -I % head %",
        # review round 2 (zsh, the shell runs use): a comment with an apostrophe, `**`, alternation
        'cat "$HOME"/Vault/kn*/projects/a/a.md # it\'s',
        'grep -n x "$HOME"/Vault/k*/a.md # don\'t',
        "cat **/x.md",
        "grep -n x **/a.md",
        "cat k*/**/a.md",
        "cat (knowledge|x)-base/a.md",
        "cat kn(o|)wledge-base/a.md",
        "ls (knowledge-base|docs)",
        "cat 'kn'(o|)wledge-base/a.md",
        # forms that resolve: a literal `$` in a bracket, `~+`, `${X:-…}`, `${PWD%/}`
        "cat knowledge-bas[e\\$]/a.md",
        "cat k[n$]owledge-base/a.md",
        "cat ~+/k*/a.md",
        "cat ${HOME:-/nowhere}/Vault/k*/a.md",
        "cat ${UNSET_HERE:-k}nowledge-base/a.md",
        "cat ${PWD%/}/k*/a.md",
        "d=knowledge-base/; cat ${d%/}/a.md",
        "f=docs/knowledge-base; cat ${f#docs/}/a.md",
        # a folder reader given a KB folder
        "ls action-items/*",
        "grep -rn x action-items/*",
        "ag x k*",
        # review round 3: diff reads a folder; groups after `}` and in a redirect; `~-` after the command's cd
        "cd action-items; mkdir -p ../e; diff -N context ../e",
        "cat {,}(kn*|x)-base/a.md",
        "x=k; cat ${x}(n*|y)-base/a.md",
        "cat < (kn*|x)-base/a.md",
        "cd action-items; cat < (con*|x)/x.md",
        "cd kn*-base; cd ..; cat ~-/a.md",
        # review round 5: `${…}` stays live inside double quotes (only glob characters, other braces and `~` are
        # literal there)
        'X=knowledge; cat "${X}-base/projects/a/a.md"',
        'X=context; cat "action-items/${X}/x.md"',
        'cd action-items; cat "${PWD}/context/x.md"',
        'd=knowledge-base/; cat "${d%/}/a.md"',
    ],
)
def test_a_read_that_expands_into_the_kb_is_denied(home: Path, command: str) -> None:
    reason = _denied(_run(home, command))
    assert "knowledge-base/" in reason and "scoutctl kb" in reason, reason


def test_a_relative_read_from_a_cwd_above_the_kb_folder_is_denied(home: Path) -> None:
    assert _denied(_run(home, "cat context/x.md", cwd=home / "Vault" / "action-items"))
    assert _denied(_run(home, "cat */x.md", cwd=home / "Vault" / "action-items"))


def test_the_index_is_kb_through_a_glob_too(home: Path) -> None:
    assert _denied(_run(home, "wc -l *.md"))  # matches knowledge-base.md, as `wc -l knowledge-base.md` is denied


# ----- harmless neighbours ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "cat action-items/action-items-*.md",
        "ls scripts/*",
        "head docs/*.md",
        "cat d*/a.md",
        "d=scripts; cat $d/run.sh",
        "cat {docs,scripts}/a.md",
        "cat $'docs/a.md'",
        "printf 'docs/%s\\n' a.md | xargs cat",
        "echo scripts/run.sh | xargs wc -l",
        "cd docs && cat a.md",
        "cd scripts; ls *",
        "env -C docs cat a.md",
        "grep -n 'k*' docs/a.md",
        "grep -rn TODO scripts/",
        "rg -n 'kno.*' scripts",
        "sed -n '1,5p' docs/a.md",
        "awk '{print $1}' docs/a.md",
        "cat ~/notes.txt",
        "cat $HOME/notes.txt",
        "cat /tmp/x.log",
        "ls",
        "ls -la",
        "cat action-items/action-items-$(date +%Y-%m-%d).md",
        "tail -5 action-items/action-items-$(date '+%Y-%m-%d').md",
        "cat action-items/action-items-`date +%F`.md",
        # Forms scheduled runs use: a variable set earlier, read inside a substitution; a zoned date.
        'f=docs/a.md && echo "bytes: $(wc -c < $f)  lines: $(wc -l < $f)"',
        'for f in docs/a.md scripts/a.md; do echo "$f $(grep -c x $f)"; done',
        'for f in docs/*.md; do s=$(grep -m1 "^status:" "$f"); echo "$s $f"; done',
        'F=docs/a.md; n=$(grep -n "^## " $F | cut -d: -f1); sed -n "$n,\\$p" $F',
        'TODAY=$(TZ="$(scripts/run.sh)" date \'+%Y-%m-%d\'); ls -la "action-items/action-items-$TODAY.md"',
        "ls -la action-items/action-items-$(TZ=\"$(scripts/run.sh)\" date '+%Y-%m-%d').md",
        "sed -i '' 's/^import re$/import re/' scripts/a.md",
        "sed -i .bak 's/a$/b/' docs/a.md",
        # xargs fed by a lister of other folders, or with a replace string that isn't the file
        "git ls-files scripts | xargs rg TODO",
        "find docs -name '*.md' | xargs wc -l",
        "grep -l alpha docs/*.md | xargs grep -H -m1 '^priority:'",
        "rg -l alpha scripts | xargs head -1",
        "grep -n x docs/a.md | cut -d: -f1 | xargs -I{} sed -n '{}p' docs/a.md",
        "echo docs/a.md | xargs -I % cat %",
        # review round 2: a KB folder is no read for a reader that only opens files
        "/usr/bin/grep -n x action-items/*",  # a run's bare grep is ugrep, which reads context/ (shell shadows)
        "cat action-items/*",
        "head -n 3 action-items/*",
        "ls -d */",
        "ls -ld action-items/*",
        # comments, `#` that isn't one, groups that aren't globs
        "cat docs/a.md # it's a note",
        "echo ${#x} $# a#b; cat docs/a.md",
        "echo $$'plain'; cat docs/a.md",  # an even `$` run with no backslash: both shells read the same word
        "x=$((1 + 2)); arr=(a b); echo $x $arr",
        "find docs \\( -name a.md -o -name b.md \\) -print",
        "(cd docs && cat a.md)",
        "f() { cat docs/a.md; }; f",
        "cat <(sort docs/a.md)",
        # resolvable forms that point elsewhere
        "ls ~+/docs",
        "cat ${HOME:-/x}/notes.txt",
        "cat ${PWD%/}/docs/a.md",
        "cat docs/[a$]*.md",
        # review round 3: a quoted regex is no path (the secret check judged it, #798's replay); a quoted
        # character is literal (no glob, brace, `$` or `~`)
        'grep -o "projects/(alpha|beta|gamma|delta|eps|zeta)[^/]*/[^/]*\\.md" docs/a.md | sort -u',
        'grep -o "projects/(alpha|beta)[^/]*" docs/a.md',
        'grep -o -e "projects/(alpha|beta|gamma|delta|eps|zeta)[^/]*/[^/]*\\.md" docs/a.md',
        "grep -o -f scripts/a.md docs/a.md",
        "awk '/projects\\/(a|b)[^/]*\\/[^/]*\\/[^/]*/ {print}' docs/a.md",
        'cat "k*/a.md"',
        "cat 'k*'/a.md",
        'cat "kn{o,}wledge-base/a.md"',
        "cat '$HOME'/notes.txt",
        'cat "~+"/a.md',
        # `<` inside `[[ … ]]` compares strings
        'CUT=$(date -v-7d +%F); for f in action-items/action-items-*.md; do d=$(basename "$f" .md); '
        'if [[ "$d" < "$CUT" ]]; then echo "$f"; fi; done',
        "echo $(date)",
        "echo `whoami` > /tmp/who.txt",
        'git log --since="$(date -v-1d +%F)" --oneline',
        'for f in scripts/*.sh; do wc -l "$f"; done',
        'f=docs/a.md; head -3 "$f"',
        "cat docs/a.md | wc -l",
        "jq . docs/a.md",
        "cat",
        "wc -l /tmp/out.$$",
    ],
)
def test_a_harmless_neighbour_stays_allowed(home: Path, command: str) -> None:
    assert _run(home, command) is None


# ----- reads whose path the gate can't work out -------------------------------------------------------


UNRESOLVED = [
    ("cat $(echo kn)owledge-base/a.md", "command_output"),
    ('cat "$(printf knowl%sdge-base/a.md e)"', "command_output"),
    ("cat `echo knowledge-base/a.md`", "command_output"),
    ('cat "`echo knowledge-base/a.md`"', "command_output"),  # a backtick inside double quotes still runs
    ("cat $UNSET_SOMEWHERE/a.md", "unset_variable"),
    ('while read -r f; do cat "$f"; done < /tmp/list.txt', "command_output"),
    ("ls scripts | xargs cat", "xargs_input"),
    ("cat < $(echo /tmp/x)", "command_output"),
    ('cd "$(git rev-parse --show-toplevel)" && cat docs/a.md', "command_output"),
    ("cat action-items/$(date +%Y/%m)/x.md", "command_output"),  # a date whose output can hold a `/`
    ("cat action-items/$(TZ=$(scripts/run.sh) date +%F; cat x).md", "command_output"),  # more than one date call
    ("find docs -exec basename {} \\; | xargs cat", "xargs_input"),  # a lister that prints something else
    ("git ls-files --full-name docs | xargs cat", "xargs_input"),
    ("find docs | sort | xargs cat", "xargs_input"),  # only the command piped straight in is read
    ("find docs | xargs -I{} cat x{}", "xargs_input"),  # a listed path inside a longer word
]


def _events(home: Path) -> list[dict]:
    import json

    rows: list[dict] = []
    for path in sorted((home / "Vault" / ".scout-logs").glob("schedule-events-*.jsonl")):
        rows += [json.loads(line) for line in path.read_text().splitlines()]
    return rows


@pytest.mark.parametrize(("command", "category"), UNRESOLVED)
def test_a_read_the_gate_cant_work_out_is_allowed_and_recorded(home: Path, command: str, category: str) -> None:
    assert gate.DENY_UNRESOLVED_READS is False  # off until the recorded events say otherwise
    assert _run(home, command) is None
    (row,) = [r for r in _events(home) if r["kind"] == "kb_read_gate.unresolved_read"]
    assert row["source"] == "hook:kb-read-gate"
    assert row["payload"] == {"categories": [category], "mode": RUN["SCOUT_MODE"]}
    assert category in gate.UNRESOLVED_CATEGORIES


def test_the_unresolved_read_event_never_carries_the_command(home: Path) -> None:
    assert _run(home, "cat $(echo kn)owledge-base/a.md docs/$UNSET_X.md") is None
    (row,) = _events(home)
    assert row["payload"]["categories"] == ["command_output", "unset_variable"]
    assert set(row["payload"]) == {"categories", "mode"}
    text = str(row)
    for word in ("knowledge", "docs", "UNSET_X", "echo", "a.md"):
        assert word not in text


def test_an_unresolved_read_beside_a_kb_read_is_denied_and_not_recorded(home: Path) -> None:
    assert "knowledge-base/" in _denied(_run(home, "cat $(echo x) knowledge-base/a.md"))
    assert not [r for r in _events(home) if r["kind"] == "kb_read_gate.unresolved_read"]


def test_a_resolved_read_records_nothing(home: Path) -> None:
    assert _run(home, "cat docs/a.md action-items/action-items-$(date +%F).md") is None
    assert _events(home) == []


@pytest.mark.parametrize("command", [command for command, _category in UNRESOLVED])
def test_with_the_switch_on_a_read_the_gate_cant_work_out_is_denied(
    home: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    monkeypatch.setattr(gate, "DENY_UNRESOLVED_READS", True)
    reason = _denied(_run(home, command))
    assert "can't work out" in reason or "can't resolve" in reason, reason
    assert not [r for r in _events(home) if r["kind"] == "kb_read_gate.unresolved_read"]


def test_an_unresolved_read_is_still_allowed_outside_a_run(home: Path) -> None:
    out = gate.run(stdin=_payload("Bash", {"command": "cat `echo knowledge-base/a.md`"}, home / "Vault"), env={})
    assert out is None


def test_an_unresolved_write_keeps_its_own_deny(home: Path) -> None:
    reason = _denied(_run(home, "echo x > $(echo .claude)/settings.json"))
    assert "writes" in reason


@pytest.mark.parametrize(
    "command",
    [
        "cat docs/a.md 'unterminated",  # an unclosed quote: the words are a guess
        'cat docs/a.md "unterminated',
        "cat (a(b)|c)/x.md",  # a nested zsh group
        "cat (a|$(echo k)nowledge-base)/x.md",  # a substitution inside a group
        "cat x$$'\\' docs/a.md '",  # bash: `$$` and a plain quote; zsh: one `$'…'` word. The words differ
        "for f in " + " ".join(f"docs/n{i}.md" for i in range(300)) + "; do cat $f; done",  # past the value cap
    ],
)
def test_a_command_the_gate_has_to_guess_at_is_refused(home: Path, command: str) -> None:
    assert "more files than" in _denied(_run(home, command))


def test_an_even_dollar_quote_is_read_the_bash_way_too(home: Path) -> None:
    # bash: `$$`, a plain `'\'`, then k*/a.md as a word of its own, which reads the KB
    assert "knowledge-base/" in _denied(_run(home, "cat x$$'\\' k*/a.md '"))


@pytest.mark.parametrize(
    "command",
    ["cat docs/[a$]*.md", "ls ~+/docs", "cat ${PWD%/}/docs/a.md", "cat ${HOME:-/x}/notes.txt", "cat ~-/a.md"],
)
def test_a_read_that_resolves_records_nothing(home: Path, command: str) -> None:
    assert _run(home, command) is None
    expected = ["unset_variable"] if command.startswith("cat ~-") else []  # `~-` before the command's own cd
    assert [r["payload"]["categories"] for r in _events(home)] == ([expected] if expected else [])


def test_a_long_for_list_with_a_write_is_refused(home: Path) -> None:
    command = "for f in " + " ".join(f"docs/n{i}.md" for i in range(300)) + "; do : > $f; done"
    assert "more files than" in _denied(_run(home, command))


def test_a_glob_with_too_many_wild_parts_is_refused_not_expanded(home: Path) -> None:
    reason = _denied(_run(home, "cat */*/*/*/x.md"))
    assert "more files than" in reason


def test_a_glob_over_the_match_cap_is_refused(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gate, "GLOB_CAP", 1)  # scripts/ holds two files
    reason = _denied(_run(home, "cat scripts/* docs/*"))
    assert "more files than" in reason


def test_an_over_cap_glob_is_refused_wherever_it_starts(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gate, "GLOB_CAP", 1)
    outside = tmp_path / "tasks"
    for name in ("a.out", "b.out", "c.out"):
        (outside / "s" / name).parent.mkdir(parents=True, exist_ok=True)
        (outside / "s" / name).write_text("x\n")
    (outside / "s" / "k").symlink_to(home / "Vault" / "knowledge-base")  # a match past the cap may be this
    assert "more files than" in _denied(_run(home, f"ls -lt {outside}/*/*"))
    assert "more files than" in _denied(_run(home, "cat */*.md"))


def test_a_glob_that_runs_out_of_time_is_refused(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gate, "GLOB_BUDGET_S", 0.0)
    real = gate.glob.iglob

    def slow(pattern: str, *a, **k):
        import time

        time.sleep(0.2)
        return real(pattern, *a, **k)

    monkeypatch.setattr(gate.glob, "iglob", slow)
    assert "more files than" in _denied(_run(home, "cat docs/*.md"))


def test_a_brace_list_over_the_limit_is_refused(home: Path) -> None:
    reason = _denied(_run(home, "cat docs/{1..500}.md"))
    assert "more files than" in reason


# ----- the pure helpers -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("a{b,c}d", ["abd", "acd"]),
        ("kn{o,}wledge", ["knowledge", "knwledge"]),
        ("{a,b}{1,2}", ["a1", "a2", "b1", "b2"]),
        ("x{a,{b,c}}y", ["xay", "xby", "xcy"]),
        ("{1..3}", ["1", "2", "3"]),
        ("{c..a}", ["c", "b", "a"]),
        ("{1..9..4}", ["1", "5", "9"]),
        ("${K}-base", ["${K}-base"]),
        ("{}", ["{}"]),
        ("{a}", ["{a}"]),
        ("a{b", ["a{b"]),
        ("\\{a,b}", ["\\{a,b}"]),
    ],
)
def test_brace_expand(word: str, expected: list[str]) -> None:
    assert brace_expand(word) == expected


def test_brace_expand_over_the_limit_is_none() -> None:
    assert brace_expand("{1..100}", limit=64) is None
    assert brace_expand("{a,b}{a,b}{a,b}{a,b}{a,b}{a,b}{a,b}", limit=64) is None


@pytest.mark.parametrize(
    ("name", "args", "expected"),
    [
        ("cat", ["-n", "a", "b"], ["a", "b"]),
        ("grep", ["-n", "pat", "a"], ["a"]),
        ("grep", ["-e", "pat", "a"], ["a"]),
        ("rg", ["--files", "a"], ["a"]),
        ("sed", ["-n", "1p", "a"], ["a"]),
        ("sed", ["-e", "1p", "a"], ["a"]),
        ("awk", ["-F,", "{print}", "a"], ["a"]),
        ("jq", [".", "a"], ["a"]),
        ("head", ["-n", "5", "a"], ["a"]),
        ("find", ["a", "b", "-name", "*.md"], ["a", "b"]),
        ("cat", ["--", "-a"], ["-a"]),
        ("cat", ["--version"], None),
        ("du", ["-sh", "a"], None),
        # one getopt-style parser for every reader: a cluster ending in a value flag takes the next word
        ("sed", ["-ne", "p", "a"], ["a"]),
        ("sed", ["--expression", "p", "a"], ["a"]),
        ("grep", ["-ie", "pat", "a"], ["a"]),
        ("grep", ["--regexp=pat", "a"], ["a"]),
        ("head", ["-n5", "a"], ["a"]),
        ("sort", ["-rk2", "a"], ["a"]),
        ("ugrep", ["-O", "md", "pat", "a"], ["a"]),
        ("ugrep", ["-rO", "md", "pat", "a"], ["a"]),
        ("ag", ["-A", "pat", "a"], ["a"]),
        ("ag", ["-A", "3", "pat", "a"], ["a"]),
        ("fd", ["-V"], None),
        ("fd", ["-e", "md", "pat", "a"], ["a"]),
        ("ag", ["-g", "pat", "a"], ["a"]),
        ("ack", ["-f", "a"], ["a"]),
        # the file a flag opens is read too
        ("grep", ["-f", "pats", "a"], ["pats", "a"]),
        ("grep", ["--file=pats", "a"], ["pats", "a"]),
        ("sed", ["-f", "prog", "a"], ["prog", "a"]),
        ("awk", ["-f", "prog", "a"], ["prog", "a"]),
        ("jq", ["--from-file", "prog", "a"], ["prog", "a"]),
    ],
)
def test_read_operands(name: str, args: list[str], expected: list[str] | None) -> None:
    assert read_operands(name, args) == expected


def test_a_long_list_of_spelled_paths_is_checked_not_refused(home: Path) -> None:
    assert _run(home, "cat " + " ".join(f"docs/n{i}.md" for i in range(300))) is None
    reason = _denied(_run(home, "cat " + " ".join(f"docs/n{i}.md" for i in range(300)) + " knowledge-base/a.md"))
    assert "knowledge-base/" in reason


def test_more_spelled_paths_than_the_literal_cap_are_refused(home: Path) -> None:
    command = "cat " + " ".join(f"docs/n{i}.md" for i in range(live_run.MAX_LITERAL_READS + 1))
    assert "more files than" in _denied(_run(home, command))


def test_run_escape_without_a_kb_reader_judges_no_read() -> None:
    assert live_run.run_escape("cat k*/a.md") is None
    assert live_run.run_escape("cat `echo knowledge-base/a.md`") is None


@pytest.mark.parametrize(
    ("words", "expected"),
    [
        (["xargs", "-I", "%", "cat", "%"], "%"),
        (["xargs", "-I{}", "cat", "{}"], "{}"),
        (["xargs", "-J", "@", "cp", "@", "x"], "@"),
        (["xargs", "-i", "cat", "{}"], "{}"),
        (["xargs", "--replace", "cat", "{}"], "{}"),
        (["xargs", "--replace=@@", "cat", "@@"], "@@"),
        (["xargs", "-iZ", "cat", "Z"], "Z"),
        (["xargs", "-0", "cat"], None),
        (["xargs", "cat", "-I", "x"], None),  # past the command: cat's own flag
        (["parallel", "cat"], "{}"),
        (["cat", "x"], None),
    ],
)
def test_replace_string(words: list[str], expected: str | None) -> None:
    assert live_run._replace_string(words) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("knowledge\\x2dbase", "knowledge-base"),
        ("knowledge\\055base", "knowledge-base"),
        ("knowledge\\0055base", "knowledge-base"),
        ("a\\u002db", "a-b"),
        ("a\\U0000002db", "a-b"),
        ("a\\nb\\tc", "a\nb\tc"),
        ("a\\xzz", "a\\xzz"),  # no digits: kept as written
        ("a\\qb", "a\\qb"),
        ("a\\c@b", "a"),  # \c@ is NUL: the text ends there
        ("ab\\", "ab\\"),
        ("a\\x00b", "a"),
    ],
)
def test_unescape(text: str, expected: str) -> None:
    assert live_run._unescape(text) == expected


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("cat $'knowledge\\x2dbase/a.md'", "cat 'knowledge-base/a.md'"),
        ("echo \"$'x'\" $'it\\'s'", "echo \"$'x'\" 'it'\\''s'"),  # inside double quotes it is text
        ("echo 'a$' $'b'", "echo 'a$' 'b'"),
        ("echo \\$'x'", "echo \\$'x'"),  # an escaped `$` starts no ANSI-C word
        ("cat docs/a.md", "cat docs/a.md"),
    ],
)
def test_ansi_c(command: str, expected: str) -> None:
    assert live_run._ansi_c(command) == expected


SUBST = live_run.SUBST
UNSEEN = live_run.UNSEEN_INPUT


@pytest.mark.parametrize(
    ("fed", "expected"),
    [
        (None, ([UNSEEN], [])),
        ("echo docs/a.md docs/b.md", (["docs/a.md", "docs/b.md"], [])),
        ("echo -n docs/a.md", (["docs/a.md"], [])),
        ("echo -e 'knowledge\\x2dbase/a.md'", (["knowledge-base/a.md"], [])),
        ("printf '%s\\n' docs/a.md docs/b.md", (["docs/a.md", "docs/b.md"], [])),
        ("printf -- 'docs/%s' a.md", (["docs/a.md"], [])),
        ("printf -v x '%s' a", ([UNSEEN], [])),
        ("printf '%q' a", ([UNSEEN], [])),
        ("cat <<< docs/a.md", ([UNSEEN], [])),
        ("X=1 ls docs", ([UNSEEN], [])),
        ("git ls-files docs scripts", ([], ["docs", "scripts"])),
        ("git ls-files", ([], ["."])),
        ("git ls-files --format=x docs", ([UNSEEN], [])),
        ("git log --name-only", ([UNSEEN], [])),
        ("find docs scripts -name '*.md'", ([], ["docs", "scripts"])),
        ("find -name '*.md'", ([], ["."])),
        ("find docs -printf '%f\\n'", ([UNSEEN], [])),
        ("grep -l x docs/a.md", ([], ["docs/a.md"])),
        ("grep -rL x", ([], ["."])),
        ("grep --files-with-matches -e x docs", ([], ["docs"])),
        ("grep -n x docs/a.md", ([UNSEEN], [])),
        ("rg --files docs", ([], ["docs"])),
        ("rg -L x docs", ([UNSEEN], [])),  # rg's -L follows links: it prints matching lines
        ("rg -il x", ([], ["."])),
    ],
)
def test_fed_sources(fed: str | None, expected: tuple[list[str], list[str]]) -> None:
    assert live_run._fed_sources(fed) == expected


@pytest.mark.parametrize(
    ("body", "named"),
    [
        ("date +%Y-%m-%d", True),
        ("date '+%Y%m%d_%H%M'", True),
        ("gdate -d yesterday +%F", True),
        ("date -v-7d '+%Y-%m-%d'", True),
        ('TZ="$(scripts/tz.sh)" date +%F', True),
        ("TZ=UTC date +%F", True),
        ("date -I", True),
        ("date --iso-8601=date", True),
        ("date -Iseconds", False),
        ("date", False),
        ("date +%Y/%m", False),
        ("date '+%d %b'", False),
        ("date +%F +%T", False),
        ("date +%F; cat x", False),
        ("LANG=C date +%F", False),
        ("echo x", False),
    ],
)
def test_one_name_output(body: str, named: bool) -> None:
    assert (live_run._one_name_output(body) == live_run.ANY_NAME) is named


def test_pwd_and_cd_helpers() -> None:
    assert live_run._put_pwd("$PWD/a.md", "") == "./a.md"
    assert live_run._put_pwd("$PWD/a.md", "docs/") == "./docs/a.md"
    assert live_run._put_pwd("${PWD}/a.md", "/abs/") == "/abs/a.md"
    assert live_run._put_pwd("$OLDPWD/a.md", "") == live_run.UNKNOWN_OLDPWD + "/a.md"
    assert live_run._put_pwd("$OLDPWD/a.md", "docs/", "kb/") == "./kb/a.md"
    assert live_run._put_pwd("${OLDPWD%/}/a.md", "docs/", "/abs/") == "/abs/a.md"
    assert live_run._put_pwd("$HOME/a.md", "") == "$HOME/a.md"
    assert live_run._into("docs/", "-") == ""
    assert live_run._into("docs/", "/tmp/") == "/tmp/"
    assert live_run._into("docs/", "sub") == "docs/sub/"


def test_a_lister_above_the_vault_covers_it(home: Path) -> None:
    assert "knowledge-base/" in _denied(_run(home, "find ~ -name '*.md' | xargs grep -l alpha"))


def test_a_listers_root_glob_over_the_cap_is_refused(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gate, "GLOB_CAP", 1)
    assert "more files than" in _denied(_run(home, "find scripts/* | xargs cat"))


@pytest.mark.parametrize(
    ("value", "op", "word", "expected"),
    [
        (None, ":-", "d", "d"),
        ("", ":-", "d", "d"),
        ("v", ":-", "d", "v"),
        (None, "-", "d", "d"),
        ("", "-", "d", ""),
        (None, ":=", "d", "d"),
        ("", "=", "d", ""),
        ("v", ":+", "d", "d"),
        ("", ":+", "d", ""),
        ("", "+", "d", "d"),
        (None, "+", "d", ""),
        ("v", ":?", "", "v"),
        (None, "?", "", None),  # an error in the shell: nothing the gate can judge
        (None, "%", "/", ""),
        ("$__scout_subst__/x", "%", "/*", None),
        ("a/b/c", "%", "/*", "a/b"),
        ("a/b/c", "%%", "/*", "a"),
        ("a/b/c", "#", "*/", "b/c"),
        ("a/b/c", "##", "*/", "c"),
        ("abc", "%", "z", "abc"),
    ],
)
def test_param_value(value: str | None, op: str, word: str, expected: str | None) -> None:
    assert live_run.param_value(value, op, word) == expected


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("cat x # it's", "cat x "),
        ("echo a\\#b # c", "echo a\\#b "),
        ("echo \"a # b\" 'c # d' # e", "echo \"a # b\" 'c # d' "),
        ('echo "a \\" # b" # c', 'echo "a \\" # b" '),
        ("echo a#b ${#x} $#", "echo a#b ${#x} $#"),
        ("cat x # one\ncat y", "cat x \ncat y"),
        ("cat x", "cat x"),
    ],
)
def test_strip_comments(command: str, expected: str) -> None:
    assert live_run._strip_comments(command) == expected


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("cat (a|b)/x", "cat {a,b}/x"),
        ("cat (a)/x", "cat a/x"),
        ("cat (a|b)(c|d)", "cat {a,b}{c,d}"),
        ("if true; then (cd x; ls); fi", "if true; then (cd x; ls); fi"),
        ("time (ls)", "time (ls)"),
        ("cat x", "cat x"),
    ],
)
def test_zsh_groups(command: str, expected: str) -> None:
    live_run._STATE["unparsed"] = False
    assert live_run._zsh_groups(command) == expected
    assert live_run._STATE["unparsed"] is False


def test_a_secret_named_after_a_pattern_is_still_judged(home: Path) -> None:
    secrets = home / ".scout-secrets"
    secrets.mkdir()
    (secrets / "token").write_text("t\n")
    for command in (
        "grep -o x ~/.scout-secrets/token",
        "grep -f ~/.scout-secrets/token docs/a.md",
        # review round 4: an attached `-eX`, and a flag that takes no value here, must not hide the file
        "grep -eT ~/.scout-secrets/token",
        "sed -ep ~/.scout-secrets/token",
        "grep --context T ~/.scout-secrets/token",
        "sed -l p ~/.scout-secrets/token",
        'grep -o "x" "$HOME/.scout-secrets/token"',
    ):
        assert _run(home, command) is not None, command


@pytest.mark.parametrize(
    "command",
    [
        'cat "${SCOUT_SECRETS_DIR}/token"',
        'cat "${SCOUT_SECRETS_DIR}"/token',
        'cat "${HOME}/.scout-secrets/token"',
        'cat "${SCOUT_SECRETS_DIR}/"*',
        'X=$SCOUT_SECRETS_DIR; cat "${X}/token"',
    ],
)
def test_a_secret_named_through_a_quoted_parameter_is_denied(home: Path, command: str) -> None:
    secrets = home / ".scout-secrets"
    secrets.mkdir()
    (secrets / "token").write_text("t\n")
    assert _run(home, command, env={"SCOUT_SECRETS_DIR": str(secrets)}) is not None, command


def test_an_over_budget_ls_glob_through_a_symlink_is_refused(home: Path, tmp_path: Path) -> None:
    out = tmp_path / "out2"
    out.mkdir()
    (out / "l").symlink_to(home / "Vault")
    (tmp_path / "out" / "x").mkdir(parents=True)
    for command in (
        f"ls {out}/*/kn*/*/*/*.md",
        f"ls -R {out}/*/kn*/*/*/",
        f"ls {tmp_path}/out/*/../../out2/l/kn*/*/*/*.md",
    ):
        assert _run(home, command) is not None, command
