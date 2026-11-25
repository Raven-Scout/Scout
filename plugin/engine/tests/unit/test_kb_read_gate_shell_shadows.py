"""kb-read-gate judges a run's `grep` and `find` as the programs the run's shell really starts.

Claude Code's shell snapshot, which every Bash call of a run sources, defines `grep` as a function running the
Claude binary's embedded ugrep (`ARGV0=ugrep "$_cc_bin" -G --ignore-files --hidden -I --exclude-dir=.git … "$@"`)
and `find` as one running its embedded bfs (`-S dfs -regextype findutils-default`). So a run's `grep` reads a
folder it's given without -r, recurses for `-3`, `--depth`, `--index` or a glob holding a `/`, and reads the
files `--from` lists; a run's `find` searches every path in its expression, not only the leading ones. The gate
judges a bare `grep`/`egrep`/`fgrep`/`find` both as itself and as the snapshot's program, and counts a read if
either reaches the KB. `/usr/bin/grep` and `/usr/bin/find` by their path are the real BSD programs.

Each case goes through the gate, and, when this machine has a Claude binary with the embedded tools, through
real `zsh -f` with the runs' options and the snapshot's functions, in a synthetic vault: a deny must print a line
only a KB note holds, an allow must not. CI has no Claude binary, so there the gate alone is checked.

Synthetic vault only.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from scout.hooks import kb_read_gate as gate
from scout.kb import raw_read, shell_shadows
from tests.unit.test_hooks_kb_read_gate import RUN, _denied, _payload

MARK = "kb-only-line"  # appears only in the KB notes
NOTE_PATH = re.compile(r"(knowledge-base|context)/[\w./-]*\.md")  # what a find run lists


def _reads_kb(out: str) -> bool:
    """A note's text, or a note's path (a name listing)."""
    return MARK in out or bool(NOTE_PATH.search(out))


SETOPTS = "setopt NO_EXTENDED_GLOB NO_BARE_GLOB_QUAL NO_EQUALS"
# The snapshot's two functions, as Claude Code 2.1.332 writes them for zsh, with the binary passed in.
_BSD_FLAGS = "-*-filter*|-*-pager*|-*-view*|-*-format-open*|-*-config*|---*|-@*|-*-save-config*|-[Zz]*|-[!-]*[Zz]*"
SNAPSHOT_FUNCTIONS = (
    "\nfunction find {\n"
    '  ARGV0=bfs "$_cc_bin" -S dfs -regextype findutils-default ${1+"$@"}\n'
    "}\n"
    "function grep {\n"
    "  local _cc_a\n"
    '  for _cc_a in ${1+"$@"}; do\n'
    f'    case "$_cc_a" in {_BSD_FLAGS}|--null|--null-data) command grep ${{1+"$@"}}; return ;; esac\n'
    "  done\n"
    f'  ARGV0=ugrep "$_cc_bin" {" ".join(raw_read.SNAPSHOT_UGREP_FLAGS)} ${{1+"$@"}}\n'
    "}\n"
)


def _claude_bin() -> str | None:
    """A Claude binary whose embedded ugrep and bfs answer, or None (CI)."""
    for candidate in (os.environ.get("CLAUDE_CODE_EXECPATH"), str(Path.home() / ".local/bin/claude")):
        if not candidate or not os.access(candidate, os.X_OK) or shutil.which("zsh") is None:
            continue
        try:
            out = subprocess.run(
                ["zsh", "-f", "-c", 'ARGV0=ugrep "$0" -V && ARGV0=bfs "$0" -version', candidate],
                capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL,
            )  # fmt: skip
        except (OSError, subprocess.TimeoutExpired):
            continue
        if out.returncode == 0 and "ugrep" in out.stdout and "bfs" in out.stdout:
            return candidate
    return None


CLAUDE_BIN = _claude_bin()


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    files = {
        "knowledge-base/a.md": f"TODO {MARK} a\n",
        "knowledge-base/projects/p/p.md": f"TODO {MARK} p\n",
        "action-items/context/x.md": f"TODO {MARK} x\n",
        "docs/a.md": "TODO docs-line\n",
        "scripts/x.py": "TODO py-line\n",
        "list.txt": "knowledge-base/a.md\n",
        "scout-config.yaml": "user: {}\n",
    }
    for rel, text in files.items():
        (h / "Vault" / rel).parent.mkdir(parents=True, exist_ok=True)
        (h / "Vault" / rel).write_text(text)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(h / "Vault"))
    return h.resolve()


def _gate(home: Path, command: str, where: str = ""):
    cwd = home / "Vault" / where
    return gate.run(stdin=_payload("Bash", {"command": command}, cwd), env={**RUN, "HOME": str(home)})


def _run(home: Path, command: str, where: str = "", shadowed: bool = True) -> str | None:
    """What ``command`` prints in a run's shell (the snapshot's functions when ``shadowed``); None without one."""
    if CLAUDE_BIN is None:
        return None
    program = f"{SETOPTS}\n_cc_bin={CLAUDE_BIN!r}\n{SNAPSHOT_FUNCTIONS if shadowed else ''}\n{command}"
    res = subprocess.run(
        ["zsh", "-f", "-c", program],
        cwd=home / "Vault" / where,
        env={"HOME": str(home), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
        capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL,
    )  # fmt: skip
    return res.stdout + res.stderr


# (folder under the vault the run is in, command): the run's grep or find reads a KB note.
MUST_DENY = [
    # ugrep's own filters and their negations
    ("", "grep -r -g '*.md' TODO"),
    ("", "grep -rO md TODO"),
    ("", "grep -r -g '!*.py' TODO"),
    ("", "grep -r -g '^*.py' TODO"),
    ("", "grep -r -t markdown TODO"),
    ("", "grep -r -t mark TODO"),
    ("", "grep -r -g '!knowledge-base' -g '!context' TODO"),  # `!name` skips files, not the folder
    ("", r"\grep -r -g '*.md' TODO"),
    # recursion without -r
    ("", "grep -g '**/*.md' TODO"),
    ("", "grep --include='**/*.md' TODO"),
    ("", "grep -g '*.py,**/*.md' TODO"),
    ("", "grep --iglob='**/*.MD' TODO"),
    ("", "grep -g '*/' TODO"),
    ("", "grep -g 'kn*/' TODO"),
    ("", "grep --include-dir='*' TODO"),
    ("", "grep -3 TODO"),
    ("", "grep -3-5 TODO"),
    ("", "grep -1,3 TODO"),
    ("", "grep --depth=2 TODO"),
    ("", "grep --depth=1,3 TODO"),
    ("", "grep --index TODO"),
    # the files to search, from a list
    ("", "grep --from=list.txt TODO"),
    ("", "grep --from list.txt TODO"),
    ("", "printf 'knowledge-base/a.md\\n' | grep --from=- TODO"),
    # a folder operand, read one level deep without -r
    ("action-items", "grep TODO context"),
    ("action-items", "grep -n TODO context docs"),
    ("", "grep -n TODO *"),
    ("", "grep TODO */"),
    ("", "grep -n TODO action-items/*"),  # `*` names context/, which ugrep reads
    # bfs: paths anywhere in the expression
    ("", "find docs -name '*.md' ."),
    ("", "find docs -name '*.md' -o -name x ."),
    ("", "find docs ! -name x ."),
    ("", "find docs -depth 3 ."),
    ("docs", "find -f .. -name '*.md'"),
    ("", "find -name '*.md'"),
    # a substitution a reader takes, after the command's own cd
    ("", "cd action-items && cat $(find .)"),
    ("", "cd action-items; cat $(find . -name '*.md')"),
    ("", "cd docs && cat $(find ..)"),
    ("", "cd docs; cd ..; cat $(find . -name '*.md')"),
    # a cd inside ( … ) ends at its )
    ("", "(cd docs); grep -r TODO ."),
    ("", "(cd docs); cat $(find . -name '*.md')"),
    ("", "cd docs && (cd ..; cat $(find . -name '*.md'))"),
    # a cd that may not run, or may not land, leaves the old folder possible (security review of this change)
    ("", "cd nosuchdir; cat $(find . -name '*.md')"),
    ("", "false && cd docs; cat $(find . -name '*.md')"),
    ("", "if false; then cd docs; fi; cat $(find . -name '*.md')"),
    ("", "g() { cd docs; }; cat $(find . -name '*.md')"),
    ("", "cd docs/a.md; wc -l $(find .)"),
    ("", "cd nosuchdir; grep -r TODO ."),
    ("", "cd docs & grep -r TODO ."),
    ("", "cd docs | true; grep -r TODO ."),
    # a root a substitution prints: placed when a printer or lister prints it, else unknown
    ("", 'sh -c "grep -r TODO $(echo .)"'),
    ("docs", 'sh -c "cd ..; grep -r TODO $(echo .)"'),
    ("", "grep -r TODO $(echo .)"),
    ("", "find $(echo .) -name '*.md'"),
    ("", "grep -r TODO $(pwd)"),
    ("", "grep -r TODO $(cat list.txt)"),
    ("", "grep -r TODO $(echo $PWD)"),
    ("", "grep -r TODO $(git ls-files)"),
    # a substitution that hands the run's grep a folder: read one level deep
    ("", "grep -l TODO $(ls)"),
    ("action-items", "grep -l TODO $(ls)"),
    ("", "grep -l TODO $(ls -d k*)"),
    ("docs", "grep -l TODO $(find .. -maxdepth 1)"),
    # `GREP` is no shell function (they are found as spelled): BSD grep, whose -r still reads the KB
    ("", "GREP -r TODO ."),
    ("", "GREP -rl x action-items/*"),
]

# Ordinary searches: the gate leaves them alone, and no KB note shows.
ALLOWED = [
    ("", "grep -rn TODO docs"),
    ("", "grep -rn TODO scripts"),
    ("", "grep -n x docs/a.md"),
    ("", "grep -n TODO docs/a.md scripts/x.py"),
    ("", "grep -r -g '*.py' TODO"),
    ("", "grep -rO py TODO"),
    ("", "grep -r --include='*.py' TODO ."),
    ("", "grep -g 'docs/*.md' TODO"),
    ("", "grep -g '!**/x' TODO"),
    ("", "grep --exclude='**/*.py' TODO"),
    ("", "grep --include-dir=docs TODO"),
    ("", "grep -g 'docs/' TODO"),
    ("", "grep -r -g '!knowledge-base/' -g '!context/' TODO"),
    ("", "grep TODO ."),
    ("", "grep TODO action-items"),
    ("", "grep TODO"),  # a run's stdin is not a terminal: ugrep reads it, not the folder
    ("", "grep -tlist"),
    ("docs", "grep -rn TODO ."),
    ("", "find docs -name '*.py'"),
    ("", "find scripts -type f"),
    ("", "find docs -printf '%p\\n'"),
    ("", "find -L docs -name '*.md'"),  # main read -L as the end of the paths and searched `.`
    ("", "find -s docs -name '*.md'"),
    ("", "find docs -maxdepth 2 -name '*.md' .."),
    ("", "cd docs; echo $(find .)"),
    ("", 'cd docs && wc -l $(find . -name "*.md")'),
    ("", "cd scripts && cat `find .`"),
    ("", "(cd docs && cat $(find .))"),
    ("", "cd docs && cd .. && cd scripts && cat $(find .)"),
    ("", "cd nosuchdir && grep -r TODO ."),  # runs only where the cd landed
    ("", "grep -r TODO $(echo docs)"),
    ("", "find $(echo docs) -name '*.md'"),
    ("docs", "grep -l TODO $(ls)"),
    ("", "grep -l TODO $(ls docs)"),
    ("", "grep -l TODO $(ls *.yaml)"),
    ("", "GREP -l x action-items/*"),  # BSD grep: context/ is only "Is a directory"
]

# By their path, grep and find are the BSD programs: these read no KB note, though the bare names would.
BY_PATH_ALLOWED = [
    ("", "/usr/bin/grep -g '**/*.md' TODO"),
    ("", "/usr/bin/grep -r -g '*.md' TODO"),  # BSD grep has no -g: an error
    ("action-items", "/usr/bin/grep TODO context"),
    ("", "/usr/bin/find docs -name '*.md' ."),
]


@pytest.mark.parametrize(("where", "command"), MUST_DENY)
def test_a_run_grep_or_find_that_reads_the_kb_is_denied(home: Path, where: str, command: str) -> None:
    out = _run(home, command, where)
    assert out is None or _reads_kb(out), out  # the run's shell really reads a note
    assert _denied(_gate(home, command, where))


@pytest.mark.parametrize(("where", "command"), ALLOWED)
def test_an_ordinary_search_is_allowed(home: Path, where: str, command: str) -> None:
    out = _run(home, command, where)
    assert out is None or not _reads_kb(out), out
    assert _gate(home, command, where) is None


@pytest.mark.parametrize(("where", "command"), BY_PATH_ALLOWED)
def test_grep_and_find_by_their_path_are_judged_as_bsd(home: Path, where: str, command: str) -> None:
    out = _run(home, command, where)
    assert out is None or not _reads_kb(out), out
    assert _gate(home, command, where) is None
    bare = command.replace("/usr/bin/", "")
    assert _denied(_gate(home, bare, where))  # and as a bare name, as the snapshot's program


def test_the_snapshot_bypass_flags_reach_bsd_grep_and_stay_denied(home: Path) -> None:
    """The snapshot hands `-z`, `--null` and the like to BSD grep: the BSD reading of them still counts."""
    for command in ("grep -rz TODO .", "grep -r --null TODO .", "command grep -r TODO .", "egrep -r TODO ."):
        out = _run(home, command)
        assert out is None or _reads_kb(out), out
        assert _denied(_gate(home, command))


@pytest.mark.parametrize("command", ["grep -n x docs/a.md", "grep TODO context", "egrep x f", "fgrep -n x f"])
def test_every_run_grep_reaches_python(command: str) -> None:
    pattern = subprocess.run(
        ["/bin/sh", str(Path(raw_read.__file__).parents[3] / "hooks" / "prefilter.sh"), "print-bash-pattern"],
        capture_output=True, text=True, check=False,
    ).stdout.strip()  # fmt: skip
    assert pattern
    res = subprocess.run(["/usr/bin/grep", "-qE", "-e", pattern], input=command, text=True, check=False)
    assert res.returncode == 0


# --- the models, one by one ---------------------------------------------------------


def test_run_models() -> None:
    ugrep = ("ugrep", [*raw_read.SNAPSHOT_UGREP_FLAGS, "x"])
    assert raw_read.run_models("grep", "grep", ["x"]) == [("grep", ["x"]), ugrep]
    assert raw_read.run_models("\\grep", "grep", ["x"]) == [("grep", ["x"]), ugrep]
    assert raw_read.run_models("egrep", "egrep", ["x"]) == [("egrep", ["x"]), ugrep]
    assert raw_read.run_models("/usr/bin/grep", "grep", ["x"]) == [("grep", ["x"])]
    assert raw_read.run_models("GREP", "grep", ["x"]) == [("grep", ["x"])]  # no function by that spelling
    assert raw_read.run_models("FIND", "find", ["."]) == [("find", ["."])]
    assert raw_read.run_models("'grep'", "grep", ["x"]) == [("grep", ["x"]), ugrep]
    assert raw_read.run_models("find", "find", ["."]) == [("find", ["."]), ("bfs", ["."])]
    assert raw_read.run_models("/usr/bin/find", "find", ["."]) == [("find", ["."])]
    assert raw_read.run_models("cat", "cat", ["f"]) == [("cat", ["f"])]


@pytest.mark.parametrize(
    ("args", "bsd", "bfs"),
    [
        (["docs", "-name", "*.md", "."], ["docs"], ["docs", "."]),
        (["-L", "docs", "-name", "x"], ["docs"], ["docs"]),
        (["-s", "-x", "docs", "lib"], ["docs", "lib"], ["docs", "lib"]),
        (["-f", "-odd", "docs"], ["-odd", "docs"], ["-odd", "docs"]),
        (["-O3", "-j4", "-S", "dfs", "docs"], [], ["docs"]),
        (["docs", "-exec", "grep", "x", "{}", ";", "."], ["docs"], ["docs", "."]),
        (["docs", "-exec", "cat", "{}", "+", "lib"], ["docs"], ["docs", "lib"]),
        (["docs", "-fprintf", "out", "%p", "lib"], ["docs"], ["docs", "lib"]),
        (["docs", "-depth", "3", "-exit", "1", "lib"], ["docs"], ["docs", "lib"]),
        (["docs", "-newermt", "2026-01-01", "lib"], ["docs"], ["docs", "lib"]),
        (["docs", "(", "-name", "x", ")", "!", "-type", "d", ",", "lib"], ["docs"], ["docs", "lib"]),
        (["docs", "-name", "x", "-f", "-odd"], ["docs"], ["docs", "-odd"]),
        ([], [], []),
    ],
)
def test_find_roots(args: list[str], bsd: list[str], bfs: list[str]) -> None:
    assert raw_read._find_roots(args, bfs=False) == bsd
    assert raw_read._find_roots(args, bfs=True) == bfs


@pytest.mark.parametrize(
    ("command", "reads"),
    [
        ("echo x ) ; cd docs; grep -r TODO .", False),  # a stray `)` closes no subshell
        ("cd docs; (grep -r TODO ..)", True),
        ("cd docs; ( ) ; grep -r TODO .", False),
    ],
)
def test_subshell_markers(home: Path, command: str, reads: bool) -> None:
    vault = str(home / "Vault")
    assert raw_read._judge_recursive(command, False, vault, vault, {}) is reads


def test_bfs_files0_from_is_a_search_the_gate_cannot_place() -> None:
    rec = raw_read._recursive("bfs", ["-files0-from", "list"])
    assert rec is not None and rec.content and rec.roots == [raw_read._UNKNOWN]
    assert raw_read.read_operands("bfs", ["-files0-from", "list"]) == ["list"]


@pytest.mark.parametrize(
    ("args", "excluded"),
    [
        (["-g", "!context/,^knowledge-base/"], ("context", "knowledge-base")),
        (["-g", "!context", "-g", "^knowledge-base"], ()),  # files with those names, not the folders
        (["--exclude-dir=context"], ("context",)),
    ],
)
def test_ugrep_exclusions(args: list[str], excluded: tuple[str, ...]) -> None:
    assert raw_read._exclusions(args, "ugrep") == excluded


def test_grep_has_no_g_and_rg_keeps_its_own() -> None:
    assert raw_read._exclusions(["-g", "!context"], "grep") == ()
    assert raw_read._exclusions(["-g", "!context"], "rg") == ("context",)


@pytest.mark.parametrize(
    ("args", "bounded"),
    [
        (["--include-dir=docs"], True),
        (["-g", "docs/,lib/"], True),
        (["--include-dir=*"], False),
        (["--include-dir=know*"], False),
        (["--include-dir=action-items", "--include-dir=context"], False),
        (["--include-dir=action-items"], True),  # context/ under it is never entered
        (["--include-dir=docs/sub"], False),  # a path: not judged here
        ([], False),
    ],
)
def test_ugrep_include_dir_bound(args: list[str], bounded: bool) -> None:
    assert raw_read._ugrep_dir_bound("ugrep", args) is bounded


# --- the doctor's view of the snapshot ---------------------------------------------------------


def _snapshot(home: Path, text: str, name: str = "snapshot-zsh-1-a.sh") -> Path:
    folder = home / ".claude" / "shell-snapshots"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(text)
    return folder / name


def test_the_doctor_notes_a_snapshot_the_gate_models(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    _snapshot(tmp_path, "# Shadow find/grep with embedded bfs/ugrep\n" + SNAPSHOT_FUNCTIONS.lstrip())
    warnings, notes = shell_shadows.check(tmp_path)
    assert warnings == []
    (note,) = notes
    assert "`find` as bfs" in note and "`grep` as ugrep" in note


def test_the_doctor_warns_on_a_name_or_flags_the_gate_does_not_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    text = SNAPSHOT_FUNCTIONS.replace("--hidden", "--hidden -r") + (
        'function rg {\n  ARGV0=rg "$_cc_bin" --hidden ${1+"$@"}\n}\n'
        'function egrep {\n  ARGV0=ag "$_cc_bin" ${1+"$@"}\n}\n'
    )
    _snapshot(tmp_path, text)
    warnings, notes = shell_shadows.check(tmp_path)
    assert len(warnings) == 3, warnings
    assert any("`grep`" in w and "-r" in w for w in warnings)
    assert any("`rg`" in w and "does not model" in w for w in warnings)
    assert any("`egrep` as ag" in w for w in warnings)
    assert notes == ["Claude Code's shell snapshot runs `find` as bfs; the KB read gate judges both."]


def test_the_doctor_reads_the_newest_snapshot_and_is_silent_without_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    assert shell_shadows.check(tmp_path) == ([], [])
    old = _snapshot(tmp_path, 'function rg {\n  ARGV0=rg "$_cc_bin" ${1+"$@"}\n}\n', "snapshot-zsh-1-old.sh")
    os.utime(old, (1, 1))
    _snapshot(tmp_path, "# nothing shadowed\n", "snapshot-zsh-2-new.sh")
    assert shell_shadows.check(tmp_path) == ([], [])


def test_the_doctor_follows_claude_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    (tmp_path / "cfg" / "shell-snapshots").mkdir(parents=True)
    (tmp_path / "cfg" / "shell-snapshots" / "snapshot-bash-1-a.sh").write_text(
        'function grep {\n  (exec -a ugrep "$_cc_bin" -G ${1+"$@"})\n}\n'
    )
    warnings, _notes = shell_shadows.check(tmp_path)
    (warning,) = warnings
    assert "flags the KB read gate does not expect: -G" in warning


def test_the_doctor_skips_functions_that_run_nothing_and_reads_odd_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    text = 'function pkill {\n  command pkill "$@"\n}\nfunction rg {\n  ARGV0=rg "$_cc_bin" --x="a ${1+"$@"}\n}\n'
    _snapshot(tmp_path, text)
    (warning,) = shell_shadows.check(tmp_path)[0]
    assert "`rg`" in warning


def test_the_doctor_says_when_it_cannot_read_the_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    (tmp_path / ".claude" / "shell-snapshots" / "snapshot-zsh-1-a.sh").mkdir(parents=True)
    (warning,) = shell_shadows.check(tmp_path)[0]
    assert "could not read" in warning


# --- decide() never raises on tricky shell text (an IndexError once denied real runs' calls) ---------

_INNER = [
    "cat $(find .)",
    "echo x=$?",
    'grep -n "a (b) c" docs/a.md',
    "echo `date` $(ls docs)",
    "cat $(echo $(find docs))",
    "echo '$(not a substitution)' \"$(find docs)\"",
    "echo \x015\x01 \x01",  # marker bytes the command spells itself
]
_WRAPS = [
    "{}",
    'bash -c "{}"',
    "sh -c '{}'",
    'eval "{}"',
    "cd docs && {}",
    "cd docs; ({}); cd ..",
    "({}) | head -3",
    "python3 - <<'EOF'\nprint(\"x (y) $(z)\")\n{}\nEOF\n",
    "python3 - <<EOF | head -3\nimport os; print(os.getcwd(), '(a)')\nEOF\n{}",
    "{}; scripts/scoutctl kb files; echo x=$?",
    "for f in $(find docs); do {}; done",
    'x=$({}); echo "$x"',
    "`{}`",
]
TRICKY = [w.format(i) for w in _WRAPS for i in _INNER] + [
    "echo $(",
    "echo `unterminated",
    "( ( cd docs ) ) ) )",
    "cat <<'A'\n$(find .)\n`find .`\nA",
    "if true; then (cd docs; { cd ..; }); fi; done; esac; } } grep -r x $(ls -d */) $(echo)",
    "cd; cd -; pushd docs && popd || cd ~; grep -l x $(printf '%s' a) $(ls -R) $(find)",
]


def test_decide_never_raises_on_tricky_shell_text(home: Path) -> None:
    env = {**RUN, "HOME": str(home), "SCOUT_DATA_DIR": str(home / "Vault")}
    for where in ("", "docs", "action-items"):
        for command in TRICKY:
            payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(home / "Vault" / where)}
            try:
                gate.decide(payload, reader="autonomous", mode="morning-briefing", env=env)
            except Exception as e:  # noqa: BLE001 - the failure names what raised
                pytest.fail(f"decide() raised {type(e).__name__}: {e} on {command!r} from {where or 'the vault'}")
