"""The KB read gate's judge of Scout's own scripts, of the run's other scripts, and of a KB name in text a program
only writes, prints or compares (#824's replay of real runs).

- A script a run's interpreter runs that is Scout's own (scout.kb.trusted_scripts: a plugin file, or a vault copy
  the plugin installs, unchanged) is trusted as scoutctl is: allowed, nothing recorded.
- Any other vault script is recorded as `program_path:vault_script`, once per script per run; given a KB note, it's
  denied as before.
- A KB name in a Python literal that is only ever text (scout.kb.python_text) names no read; one that reaches a
  call the scan doesn't model is still denied.
- A program that edits a KB note and a vault file in place is an edit (recorded), not a copy out; loops over a
  dict or a list of tuples of KB paths are judged by those paths; and the lexer keeps a `\\"` in a triple-quoted
  string inside it.
"""

from __future__ import annotations

import io
import json
import shutil
from pathlib import Path

import pytest

import scout
from scout.hooks import kb_read_gate as gate
from scout.kb.program_reads import program_reads
from scout.kb.python_text import text_checker
from scout.kb.raw_read import raw_kb_read
from scout.kb.trusted_scripts import plugin_roots, trusted_script
from tests.unit.test_hooks_kb_read_gate import RUN, _denied

PLUGIN = Path(scout.__file__).resolve().parents[2]
PARSER = "knowledge-base/ontology/parser.py"
SHARDS = "scripts/kb-shard-log.py"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A home with a vault at ``~/Vault`` holding the plugin's own copies of two scripts, as bootstrap installs
    them, and one script of the vault's own."""
    h = tmp_path / "home"
    v = h / "Vault"
    for rel in ("knowledge-base/a.md", "knowledge-base/b.md", "docs/a.md", "action-items/action-items-2026-11-23.md"):
        (v / rel).parent.mkdir(parents=True, exist_ok=True)
        (v / rel).write_text("x\n")
    for rel in (PARSER, SHARDS):
        (v / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(PLUGIN / "templates" / rel, v / rel)
    (v / "scripts/mine.py").write_text("print(1)\n")
    (v / "scout-config.yaml").write_text("user: {}\n")
    monkeypatch.setenv("SCOUT_DATA_DIR", str(v))
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    return h.resolve()


def _run(home: Path, command: str, *, run: str = "s1"):
    body = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(home / "Vault"), "session_id": run}
    return gate.run(stdin=io.StringIO(json.dumps(body)), env={**RUN, "HOME": str(home)})


def _events(home: Path) -> list[dict]:
    rows: list[dict] = []
    for path in sorted((home / "Vault" / ".scout-logs").glob("schedule-events-*.jsonl")):
        rows += [json.loads(line) for line in path.read_text().splitlines()]
    return rows


def _categories(home: Path) -> list[list[str]]:
    return [r["payload"]["categories"] for r in _events(home) if r["kind"] == "kb_read_gate.unresolved_read"]


# ----- Scout's own scripts ----------------------------------------------------------------------------------

TRUSTED = [
    f"python3 {PARSER} knowledge-base/a.md",
    f"python3 {PARSER} knowledge-base/a.md --json 2>&1 | head -5",
    "cd knowledge-base/ontology && python3 parser.py ../a.md",
    f"python3 {SHARDS} knowledge-base/a.md",
    f"python3 {PLUGIN}/templates/{SHARDS} knowledge-base/a.md",  # the plugin's own file
]


@pytest.mark.parametrize("command", TRUSTED)
def test_scouts_own_script_is_trusted_and_nothing_is_recorded(home: Path, command: str) -> None:
    assert _run(home, command) is None, command
    assert _events(home) == []


@pytest.mark.parametrize("command", TRUSTED[:2])
def test_the_evals_judge_trusts_it_too(home: Path, command: str) -> None:
    v = str(home / "Vault")
    assert not raw_kb_read(command, cwd=v, vault=v, env={"HOME": str(home)})


def test_a_copy_an_older_engine_rendered_is_trusted(home: Path) -> None:
    """A vault an older engine upgraded: the copy matches what bootstrap last wrote, not this plugin's template."""
    v = home / "Vault"
    (v / SHARDS).write_text("print('an older render')\n")
    rendered = v / ".scout-state/last-rendered" / SHARDS
    rendered.parent.mkdir(parents=True)
    rendered.write_text("print('an older render')\n")
    assert _run(home, f"python3 {SHARDS} knowledge-base/a.md") is None
    assert _events(home) == []


@pytest.mark.parametrize("edited", [SHARDS, PARSER])
def test_a_copy_the_run_edited_is_not_trusted(home: Path, edited: str) -> None:
    (home / "Vault" / edited).write_text("import sys; print(open(sys.argv[1]).read())\n")
    assert "knowledge-base/" in _denied(_run(home, f"python3 {edited} knowledge-base/a.md"))


def test_trusted_script_is_a_plugin_file_outside_its_virtualenvs(tmp_path: Path, home: Path) -> None:
    root = tmp_path / "plugin"
    for rel in ("scripts/x.py", ".venv/lib/x.py", "engine/.venv/lib/site-packages/y.py"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("print(1)\n")
    env = {"CLAUDE_PLUGIN_ROOT": str(root)}
    vault = home / "Vault"
    assert root.resolve() in plugin_roots(env)
    assert trusted_script(root / "scripts/x.py", vault, env)
    assert not trusted_script(root / ".venv/lib/x.py", vault, env)  # someone else's code: may print what it's given
    assert not trusted_script(root / "engine/.venv/lib/site-packages/y.py", vault, env)
    assert not trusted_script(root / "scripts/missing.py", vault, env)
    assert not trusted_script(vault / "scripts/mine.py", vault, {})  # not a file the plugin installs
    assert trusted_script(vault / PARSER, vault, {})
    assert not trusted_script(tmp_path / "elsewhere.py", vault, {})


# ----- the run's other scripts -----------------------------------------------------------------------------


def test_a_vault_script_is_recorded_once_per_run(home: Path) -> None:
    assert _run(home, "python3 scripts/mine.py docs/a.md") is None
    assert _run(home, "python3 scripts/mine.py docs/a.md") is None  # the same run: no second record
    assert _run(home, "python3 scripts/mine.py") is None
    assert _categories(home) == [["program_path:vault_script"]]
    assert _run(home, "python3 scripts/mine.py", run="s2") is None  # another run records it again
    assert _run(home, "perl scripts/other.pl", run="s2") is None  # another script: its own record
    assert _categories(home) == [["program_path:vault_script"]] * 3
    seen = json.loads((home / "Vault/.scout-logs/.kb-read-gate-scripts.json").read_text())
    assert set(seen["runs"]) == {"s1", "s2"}
    assert "mine" not in json.dumps(seen)  # names are hashed, never kept


def test_a_script_outside_the_vault_is_a_program_path(home: Path) -> None:
    assert _run(home, "python3 /tmp/elsewhere.py docs/a.md") is None
    assert _categories(home) == [["program_path"]]


def test_a_vault_script_given_a_kb_note_is_denied(home: Path) -> None:
    assert "knowledge-base/" in _denied(_run(home, "python3 scripts/mine.py knowledge-base/a.md"))


def test_with_the_switch_on_a_script_that_isnt_scouts_own_is_denied(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gate, "DENY_UNRESOLVED_READS", True)
    assert "can't work out" in _denied(_run(home, "python3 scripts/mine.py docs/a.md"))
    assert _run(home, f"python3 {PARSER} knowledge-base/a.md") is None  # Scout's own stays trusted


def test_the_record_survives_a_seen_file_it_cant_read(home: Path) -> None:
    logs = home / "Vault/.scout-logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / ".kb-read-gate-scripts.json").write_text("not json")
    assert _run(home, "python3 scripts/mine.py") is None
    assert _categories(home) == [["program_path:vault_script"]]


def test_without_a_run_id_every_call_is_recorded(home: Path) -> None:
    assert _run(home, "python3 scripts/mine.py", run="") is None
    assert _run(home, "python3 scripts/mine.py", run="") is None
    assert _categories(home) == [["program_path:vault_script"]] * 2


# ----- a KB name in text ------------------------------------------------------------------------------------


def _py(program: str) -> str:
    return "python3 - <<'EOF'\n" + program + "\nEOF"


TEXT = [
    'p = "docs/a.md"\ns = open(p).read()\ns = s.replace("x", "[[knowledge-base/a.md]]")\nopen(p, "w").write(s)',
    'print("see knowledge-base/a.md")',
    'lines = open("docs/a.md").read().splitlines()\nlines.insert(0, "- [[knowledge-base/a.md]]")\n'
    'open("docs/a.md", "w").write("\\n".join(lines))',
    'lines = [l for l in open("docs/a.md").read().splitlines() if not l.startswith("knowledge-base/")]\n'
    'open("docs/a.md", "w").write("\\n".join(lines))',
    'from pathlib import Path\np = Path("docs/a.md")\nt = p.read_text()\ni = t.find("knowledge-base/")\n'
    'p.write_text(t[:i] + "x")',
    'NEW = """- [[knowledge-base/a.md]] \\"quoted\\""""\nwith open("docs/a.md", "a") as f:\n    f.write(NEW)',
    'import re\ns = open("docs/a.md").read()\nopen("docs/a.md", "w").write(re.sub("old", "knowledge-base/a.md", s))',
    'xs = ["knowledge-base/a.md", "knowledge-base/b.md"]\nprint(", ".join(sorted(xs)))',
]
NOT_TEXT = [  # each reaches a call that opens, copies, archives or runs it
    'from subprocess import run\nrun(["cat", "knowledge-base/a.md"])',
    'import shutil as s\ns.copy("knowledge-base/a.md", "docs/x.md")',
    'import os as o\no.replace("knowledge-base/a.md", "docs/x.md")',
    'import zipfile\nz = zipfile.ZipFile("docs/o.zip", "w")\nz.write("knowledge-base/a.md")\nz.close()',
    'import tarfile\nt = tarfile.open("docs/o.tar", "w")\nt.add("knowledge-base/a.md")\nt.close()',
    'print(sorted(["knowledge-base/a.md"], key=lambda p: open(p).read()))',
    'd = {}\nd["k"] = "knowledge-base/a.md"\nprint(open(d["k"]).read())',
    'xs = []\nxs.append("knowledge-base/a.md")\nprint(open(xs[0]).read())',
    'p = "knowledge-base/a.md"\nq = p.replace("zz", "")\nprint(open(q).read())',
    'class C:\n    pass\nc = C()\nc.p = "knowledge-base/a.md"\nprint(open(c.p).read())',
    'def r(p):\n    return open(p).read()\nprint(r("knowledge-base/a.md"))',
    'open("docs/w.txt", "w").write("knowledge-base/a.md")\nprint(open(open("docs/w.txt").read()).read())',
    'import io\nb = io.StringIO()\nb.write("knowledge-base/a.md")\nprint(open(b.getvalue()).read())',
    'import fsspec\nfs = fsspec.filesystem("file")\nfs.copy("knowledge-base/a.md", "docs/x.md")',
    "print(f\"{open('knowledge-base/a.md').read()}\")",
    "# knowledge-base/a.md\nprint(1)",
    'print("knowledge-base/a.md"',  # doesn't parse: nothing counts as text
]


@pytest.mark.parametrize("program", TEXT)
def test_a_kb_name_in_text_is_no_read(home: Path, program: str) -> None:
    assert _run(home, _py(program)) is None, program
    assert not [r for r in _events(home) if r["kind"] == "kb_read_gate.kb_edit"]


@pytest.mark.parametrize("program", NOT_TEXT)
def test_a_kb_name_that_reaches_a_call_is_denied(home: Path, program: str) -> None:
    assert "knowledge-base/" in _denied(_run(home, _py(program))), program


def test_a_program_that_runs_programs_gets_no_text_literals(home: Path) -> None:
    """Text it writes may be a script it runs: so none of its literals count as text."""
    program = 'open("docs/x.py", "w").write("print(open(\'knowledge-base/a.md\').read())")\n'
    program += 'import subprocess\nsubprocess.run(["python3", "docs/x.py"])'
    assert "knowledge-base/" in _denied(_run(home, _py(program)))


def test_python_text_finds_the_literal_at_an_offset() -> None:
    program = 'x = 1\ns = "é knowledge-base/a.md"\nprint(s)'
    checker = text_checker(program)
    assert checker is not None
    at = program.index('"é')
    assert checker.literal_at(at) is not None and checker.is_text(at)
    assert checker.literal_at(0) is None
    assert text_checker("print(") is None


# ----- edits in place, loops, the lexer -------------------------------------------------------------------

IN_PLACE = [
    'for p in ["knowledge-base/a.md", "action-items/action-items-2026-11-23.md"]:\n'
    '    s = open(p).read()\n    open(p, "w").write(s.replace("x", "y"))',
    'D = {"knowledge-base/a.md": "x", "knowledge-base/b.md": "y"}\nfor p, v in D.items():\n'
    '    s = open(p).read()\n    open(p, "w").write(s.replace(v, "z"))',
]


@pytest.mark.parametrize("program", IN_PLACE)
def test_an_edit_in_place_of_kb_notes_and_vault_files_is_recorded(home: Path, program: str) -> None:
    assert _run(home, _py(program)) is None, program
    (row,) = [r for r in _events(home) if r["kind"] == "kb_read_gate.kb_edit"]
    assert row["payload"] == {"mode": RUN["SCOUT_MODE"], "targets": 2 if "D =" in program else 1}


@pytest.mark.parametrize(
    "program",
    [  # what it reads in the KB may land outside it
        's = open("knowledge-base/a.md").read()\nt = open("docs/a.md").read()\nopen("docs/a.md", "w").write(t + s)',
        's = open("knowledge-base/a.md").read()\nopen("knowledge-base/a.md", "w").write(s)\n'
        'open("docs/new.md", "w").write(s)',
    ],
)
def test_a_kb_read_that_may_land_outside_the_kb_is_denied(home: Path, program: str) -> None:
    assert "knowledge-base/" in _denied(_run(home, _py(program)))


@pytest.mark.parametrize(
    ("program", "literal"),
    [
        ('D = {"a": "k/x.md"}\nfor p in D.values():\n    open(p)', ["k/x.md"]),
        ('D = {"k/x.md": 1}\nfor p in D:\n    open(p)', ["k/x.md"]),
        ('L = [("k/x.md", "x"), ("d/y.md", "y")]\nfor p, v in L:\n    open(p)', ["k/x.md", "d/y.md"]),
        ('for p in sorted(["a", "b"]):\n    open(p)', ["a", "b"]),
        ('print([open(p).read() for p in ("a", "b") if p])', ["a", "b"]),
    ],
)
def test_a_loop_variable_takes_what_the_loop_goes_over(program: str, literal: list[str]) -> None:
    found = program_reads("python3", ["-c", program])
    assert found is not None and found.literal == literal and not found.built


@pytest.mark.parametrize("program", ["for p in q:\n    open(p)", 'for i, p in enumerate(["a"]):\n    open(p)'])
def test_a_loop_over_what_the_scan_cant_spell_is_built(program: str) -> None:
    found = program_reads("python3", ["-c", program])
    assert found is not None and found.built


def test_a_triple_quoted_string_keeps_an_escaped_quote() -> None:
    program = 'x = """a \\""""\nopen("knowledge-base/a.md", "w").write(x)'
    found = program_reads("python3", ["-c", program])
    assert found is not None and found.writes == ["knowledge-base/a.md"] and not found.unaccounted


def test_code_in_an_f_string_that_opens_something_is_built() -> None:
    found = program_reads("python3", ["-c", "print(f\"{open(chr(107) + 'x').read()}\")"])
    assert found is not None and found.built
    assert program_reads("python3", ["-c", 'print(f"{len(x)} items")']) is None
