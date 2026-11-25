"""A run's command word that the shell builds as the command runs is judged by what it becomes.

Every rule of the run gate (run env, detach, the commit hooks, HOME, KB and secret reads) knows a program by the text
of its command word, so a word the shell fills in or globs first (`c=re; c+=ad; $c HOM? <<< h`, `rea? HOME`,
`$(echo daer | rev) HOME`, `f(){ "$@"; }; f $c …`) got past all of them. Now a variable set to a plain word once in
the same command is that word, a function call is its body with the arguments put in, and every other built word is
denied; three shapes real runs use (a loop over words that aren't plain, a variable assigned a substitution, a
function that runs its arguments) are allowed and recorded until DENY_UNRESOLVED_COMMAND_WORDS is turned on.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.hooks import kb_read_gate as gate
from scout.kb import live_run
from tests.unit.test_hooks_kb_read_gate import RUN, _decision, _denied, vault  # noqa: F401

BUILT = live_run.COMMAND_WORD_FORM


def judged(command: str, kinds: list[str] | None = None) -> str | None:
    return live_run.run_escape(command, command_word=(kinds.append if kinds is not None else None))  # type: ignore[arg-type]


# ----- the forms that got through, each denied now -----------------------------------------------------------

# (command, what it is denied as): the review's forms, and their neighbours.
NEVER_RESOLVED = [
    ("c=re; c+=ad; $c HOM? <<< h; git commit -qm m", "computed"),
    ('c=re; c+=ad; "${c}" HOM? <<< h', "computed"),
    ("c=read; c=x; $c HOME", "computed"),
    ("read -r c <<< read; $c HOME", "computed"),
    ("typeset -l c=READ; $c HOME", "computed"),
    ("c=(read); $c HOME", "computed"),
    ("c='rea?'; $c HOME", "computed"),
    ("c=re; c+=ad; d=$c; $d HOME", "computed"),
    ("for c in rea?; do $c HOME; done", "computed"),
    ("$(echo daer | rev) HOME", "substitution"),
    ("`echo daer | rev` HOME", "substitution"),
    ('"$(printf read)" HOME', "substitution"),
    ("scripts/$(ls scripts | head -1) x", "substitution"),
    ("c=READ; ${(L)c} HOME", None),
    ("c=READ; ${c:l} HOME", "modifier"),
    ("c=xread; ${c#x} HOME", "modifier"),
    ("c=read; ${c//x/y} HOME", "modifier"),
    ("c=read; $c[1,4] HOME", None),  # a glob in bash, a subscript in zsh
    ("c=READ; $c:l HOME", "modifier"),
    ("$$ HOME", "special"),
    ("rea? HOME", "glob"),
    ("rea[d] HOME", "glob"),
    ("re* HOME", "glob"),
    ("{read,HOME}", "glob"),
    ("$c HOME", "unassigned"),
    ('"${c}" HOME', "unassigned"),
    ("$PYTHON -c 'print(1)'", "unassigned"),
    ("$1 HOME", "unassigned"),
    ('f(){ "$@"; }; f $c HOME', "unassigned"),
    ("() { $1 HOME } read", "unassigned"),
    ("sudo -u alex $c HOME", "unassigned"),
    ("nohup $c", "unassigned"),
    ("timeout 5 $c", "unassigned"),
    ("find . -exec $c {} ;", "unassigned"),
    ('G=git env -S "${G} commit --no-verify"', "unassigned"),
    ("builtin $c HOME", "unassigned"),
    ("eval '$c HOME'", "unassigned"),
    ("sh -c '$c HOME'", "unassigned"),
]


@pytest.mark.parametrize(("command", "kind"), NEVER_RESOLVED)
def test_a_command_word_the_gate_cant_resolve_is_denied(command: str, kind: str | None) -> None:
    found = judged(command)
    assert found is not None and found.startswith(BUILT), (command, found)
    if kind is not None:
        assert found == live_run.denied_word(kind)


# A word the command sets to a plain word, or a function the command defines: judged as the program it is.
RESOLVED = [
    ("c=read; $c HOME", live_run.HOME_FORM),
    ('c=read; "$c" HOME', live_run.HOME_FORM),
    ("c=read; ${c} HOME", live_run.HOME_FORM),
    ("c=read; d=$c; $d HOME", live_run.HOME_FORM),
    ("for c in ls read; do $c HOME; done", live_run.HOME_FORM),
    ('c="read HOME"; $c', live_run.HOME_FORM),
    ("export c=read; $c HOME", live_run.HOME_FORM),
    ("g=git; $g commit --no-verify -m x", "--no-verify"),
    ("g=/usr/bin/git; $g -c core.hooksPath=/dev/null commit -m x", "-c core.hooksPath"),
    ("c=nohup; $c sleep 1", "detach"),
    ("e=env; $e SCOUT_MODE=x ls", "env"),
    ("=git commit --no-verify -m x", "--no-verify"),
    ('f(){ "$@"; }; f read HOME', live_run.HOME_FORM),
    ('f(){ $1 "$2"; }; f read HOME', live_run.HOME_FORM),
    ("f(){ $1 HOME; }; f read", live_run.HOME_FORM),
    ("function f { $1 HOME; }; f read", live_run.HOME_FORM),
    ('f(){ shift; "$@"; }; f x read HOME', live_run.HOME_FORM),
    ("f(){ git commit --no-verify -m x; }; f", "--no-verify"),
    ('f(){ "$@"; }; c=read; f $c HOME', live_run.HOME_FORM),
    ("c=sudo; $c HOME=/tmp ls", live_run.HOME_FORM),
]


@pytest.mark.parametrize(("command", "form"), RESOLVED)
def test_a_command_word_set_in_the_command_is_judged_as_its_program(command: str, form: str) -> None:
    assert judged(command) == form


# ----- what stays allowed ---------------------------------------------------------------------------------------

ORDINARY = [
    "S=scripts/scoutctl; $S kb resolve widgets",
    'S=scripts/scoutctl && "$S" kb read projects/widgets/widgets.md',
    "VENV=.venv/bin/python; $VENV -m pytest -q",
    'PYTHON=/usr/bin/python3; "$PYTHON" -c "print(1)"',
    'P=$HOME/.venv/bin/python; $P -c "print(1)"',
    "P=~/.venv/bin/python; $P --version",
    "B=$PWD/bin; $B/tool --help",
    "for f in a.md b.md; do wc -l $f; done",
    'for c in "git status" "git log -1"; do $c; done',
    "while read -r l; do echo $l; done < notes.txt",
    "while IFS= read -r line; do printf '%s\\n' \"$line\"; done < notes.txt",
    'log(){ echo "$1" >> notes.txt; }; log hi',
    "count(){ wc -l < notes.txt; }; count",
    'f(){ local n=$1; echo "$n"; }; f 3',
    "case $f in *.md) echo md;; *) echo other;; esac",
    "case $f in (*.md) echo md;; esac",
    "(( n > 1 )) && echo big",
    "if (( $n > 1 )); then echo big; fi",
    "echo $(( $n + 1 ))",
    "[ -f notes.txt ] && echo yes",
    '[[ "$a" == b* ]] && echo yes',
    'echo "rea?"',
    "'rea?' x",
    "ls *.md",
    "git status",
    "command -v $x",
    "x=$(date +%F); echo $x",
    'test -n "$x" && echo set',
]


@pytest.mark.parametrize("command", ORDINARY)
def test_ordinary_run_commands_stay_allowed(command: str) -> None:
    kinds: list[str] = []
    assert judged(command, kinds) is None
    assert kinds == []


# The shapes 12 of 288 real runs used: allowed by default and recorded, denied with the switch on.
SWITCHED = [
    ("for p in $(ls scripts); do $p --help; done", live_run.LOOP_WORD),
    ('for c in "$@"; do $c; done', live_run.LOOP_WORD),
    ("f=$(ls scripts | head -1); $f --help", live_run.ASSIGNED_WORD),
    ("f=$(ls scripts | head -1); scripts/$f --help", live_run.ASSIGNED_WORD),
    ("d=$DIR/bin; $d/tool", live_run.ASSIGNED_WORD),
    ('run(){ "$@"; }; run git status', live_run.WRAPPER_WORD),
]


@pytest.mark.parametrize(("command", "kind"), SWITCHED)
def test_the_shapes_runs_use_are_recorded_and_left_to_the_switch(command: str, kind: str) -> None:
    kinds: list[str] = []
    assert judged(command, kinds) is None
    assert kind in kinds
    assert live_run.run_escape(command, command_word=live_run.denied_word) == live_run.denied_word(kind)


# ----- git's config variables (the runner pins GIT_CONFIG_GLOBAL) ---------------------------------------------

GIT_CONFIG_CHANGES = [
    "unset GIT_CONFIG_GLOBAL",
    "unset -v GIT_CONFIG_GLOBAL",
    "export -n GIT_CONFIG_GLOBAL",
    "typeset +x GIT_CONFIG_GLOBAL",
    "env -u GIT_CONFIG_GLOBAL git commit -m x",
    "env --unset=GIT_CONFIG_GLOBAL git commit -m x",
    "env -uGIT_CONFIG_GLOBAL git commit -m x",
    "read GIT_CONFIG_GLOBAL <<< x",
    "GIT_CONFIG_GLOBAL+=x",
    "GIT_CONFIG_GLOBAL=x git commit -m x",
    "export GIT_CONFIG_GLOBAL=x",
    "GIT_CONFIG_GL''OBAL=x git commit -m x",
    "GIT_CONFIG_GL\\OBAL=x git commit -m x",
    "unset GIT_CONFIG_GL''OBAL",
    "declare -n r=GIT_CONFIG_GLOBAL",
    "for GIT_CONFIG_GLOBAL in x; do git commit -m x; done",
]


@pytest.mark.parametrize("command", GIT_CONFIG_CHANGES)
def test_unsetting_or_overriding_git_config_global_is_denied(command: str) -> None:
    assert judged(command) == live_run.GIT_CONFIG_FORM


@pytest.mark.parametrize("command", [": ${GIT_CONFIG_GLOBAL:=x}", "exec {GIT_CONFIG_GLOBAL}>/dev/null"])
def test_an_expansion_that_sets_git_config_global_is_denied(command: str) -> None:
    assert live_run.expansion_escape(command) == live_run.GIT_CONFIG_FORM


@pytest.mark.parametrize(
    "command",
    [
        "export GIT_CONFIG_GLOBAL",
        "echo $GIT_CONFIG_GLOBAL",
        "git config --global --get user.name",
        "unset GIT_CONFIG_NOSYSTEM",
        "printenv GIT_CONFIG_GLOBAL",
    ],
)
def test_reading_git_config_global_stays_allowed(command: str) -> None:
    assert judged(command) is None
    assert live_run.expansion_escape(command) is None


# ----- through the gate ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "c=re; c+=ad; $c HOM? <<< h; git commit -qm m",
        "rea? HOME",
        "$(echo daer | rev) HOME",
        "c=READ; ${c:l} HOME",
        'f(){ "$@"; }; f $c HOME',
    ],
)
def test_the_gate_denies_a_built_command_word_in_a_run(vault: Path, command: str) -> None:  # noqa: F811
    reason = _denied(_decision(vault, "Bash", {"command": command}))
    assert "names its program in plain text" in reason or "HOME" in reason


# Reads of a KB note through a program named at run time, from two security reviews: each was allowed on main.
# (git's own embedded commands, `rebase --exec` and the rest, are #817's.)
KB_READS = [
    "c='cat action-items/context/c.md'; sh -c \"$c\"",
    "c='cat action-items/context/c.md'; eval \"$c\"",
    "c='cat act*/cont*/c.md'; sh -c \"$c\"",
    "c='cat act*/cont*/c.md'; eval \"$c\"",
    "bash -c \"$(printf 'cat knowledge-base/people.md')\"",
    "c='cat knowledge-base/people.md'; $c",
    "bash <<EOF\nc='cat knowledge-base/people.md'\n$c\nEOF",
    "x=cat; $x knowledge-base/people.md",
    "x=at; c${x} knowledge-base/people.md",
    "c$()at knowledge-base/people.md",
    '"$(echo cat)" knowledge-base/people.md',
    "for p in $(ls scripts); do $p knowledge-base/people.md; done",
    'run(){ "$@"; }; run cat knowledge-base/people.md',
]


@pytest.mark.parametrize("command", KB_READS)
def test_a_kb_read_through_a_built_program_is_denied(vault: Path, command: str) -> None:  # noqa: F811
    assert _decide(vault, command) is not None
    gate._PENDING_AUDIT.clear()


def test_a_built_program_after_a_cd_reads_as_the_shell_reads_it(vault: Path) -> None:  # noqa: F811
    assert _decide(vault, "cd action-items && x=cat; $x context/x.md") is not None
    gate._PENDING_AUDIT.clear()


# What the parser splits but the shell doesn't: no program in any of them.
PARSER_SPLITS = [
    'for s in a "b.py --x"; do n=${s%%.py*}; n=${n%% *}; python3 scripts/${n}.py ${s#*--}; done',
    'if [[ "$d" < "$C" || "$d" == "$C" ]]; then echo y; fi',
    "case \"$st\" in open|'open'*|'') echo x;; *) echo y;; esac",
    "x=$(( ( $(date +%s) - $(date +%s) ) / 86400 ))",
    "(( ($a + 1) * 2 )) && echo big",
    'declare -a arr=("$@")',
    "echo \\`x\\` \\$y",
    "python3 -c \"import subprocess; subprocess.run('echo [x] *', shell=True); open('o','w').write('x')\"",
]


@pytest.mark.parametrize("command", PARSER_SPLITS)
def test_what_the_parser_splits_is_no_program(command: str) -> None:
    kinds: list[str] = []
    assert live_run.run_escape(command, kb_read=lambda raw, how: None, command_word=kinds.append) is None
    assert kinds == []


def test_the_deny_says_what_to_do_instead(vault: Path) -> None:  # noqa: F811
    reason = _denied(_decision(vault, "Bash", {"command": "rea? x"}))
    assert BUILT in reason and "S=scripts/scoutctl; $S kb" in reason


def _decide(where: Path, command: str) -> str | None:
    payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(where)}
    return gate.decide(payload, reader="autonomous", mode="morning-briefing", env={**RUN, "HOME": "/home/alex"})


def test_a_denied_word_is_audited_by_its_form_never_the_command(vault: Path) -> None:  # noqa: F811
    gate._PENDING_AUDIT.clear()
    assert _decide(vault, "rea? secret-word") is not None
    rows = [(kind, payload) for _v, kind, payload in gate._PENDING_AUDIT]
    gate._PENDING_AUDIT.clear()
    assert rows == [("kb_read_gate.hook_skip", {"form": live_run.denied_word("glob"), "mode": "morning-briefing"})]


def test_an_allowed_shape_is_recorded_by_its_kind(vault: Path) -> None:  # noqa: F811
    gate._PENDING_AUDIT.clear()
    assert _decide(vault, 'run(){ "$@"; }; run git status') is None
    rows = [(kind, payload) for _v, kind, payload in gate._PENDING_AUDIT]
    gate._PENDING_AUDIT.clear()
    expected = {"kinds": [live_run.WRAPPER_WORD], "mode": "morning-briefing"}
    assert rows == [("kb_read_gate.unresolved_command_word", expected)]


def test_the_switch_denies_the_allowed_shapes(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    monkeypatch.setattr(gate, "DENY_UNRESOLVED_COMMAND_WORDS", True)
    gate._PENDING_AUDIT.clear()
    reason = _decide(vault, "f=$(ls scripts | head -1); $f --help")
    gate._PENDING_AUDIT.clear()
    assert reason is not None and live_run.denied_word(live_run.ASSIGNED_WORD) in reason


def test_the_switch_is_off(vault: Path) -> None:  # noqa: F811
    assert gate.DENY_UNRESOLVED_COMMAND_WORDS is False
    assert _decide(vault, "f=$(ls scripts | head -1); $f --help") is None
    gate._PENDING_AUDIT.clear()


def test_an_ordinary_command_records_nothing(vault: Path) -> None:  # noqa: F811
    gate._PENDING_AUDIT.clear()
    assert _decide(vault, "S=scripts/scoutctl; $S kb resolve widgets") is None
    assert gate._PENDING_AUDIT == []


def test_the_owner_is_never_gated(vault: Path) -> None:  # noqa: F811
    assert _decision(vault, "Bash", {"command": "rea? HOME"}, env={}) is None


# ----- the edges of the walk ----------------------------------------------------------------------------------

EDGES = [
    ("sudo -- $c HOME", live_run.denied_word("unassigned")),
    ("for c in " + " ".join(f"p{i}" for i in range(300)) + "; do $c; done", live_run.denied_word("too_many")),
    ("c=sudo; $c $x", live_run.denied_word("unassigned")),
    ("c=a*$x; $c", live_run.denied_word("computed")),
    ("f(){ ${1} HOME; }; f read", live_run.HOME_FORM),
    ('f(){ "$@"; }; f rea? HOME', live_run.denied_word("glob")),
    ("f(){ f; f; }; f", live_run.denied_word("too_many")),
    ("c=read; c=read; $c HOME", live_run.HOME_FORM),
]


@pytest.mark.parametrize(("command", "found"), EDGES)
def test_the_edges_of_the_walk(command: str, found: str) -> None:
    assert judged(command) == found


@pytest.mark.parametrize(
    "command",
    [
        "f(){ echo $# $0; }; f a b",
        'f(){ echo "$*"; }; f a b',
        "f(){ :; }; f(){ :; }; f",
        "f(){ echo unclosed",
        "c=x; echo $c$",
    ],
)
def test_more_shapes_that_run_no_built_program(command: str) -> None:
    kinds: list[str] = []
    assert judged(command, kinds) is None
    assert kinds == []


def test_a_loop_over_the_arguments_is_left_to_the_switch() -> None:
    kinds: list[str] = []
    assert judged("for c; do $c --help; done", kinds) is None
    assert kinds == [live_run.LOOP_WORD]
