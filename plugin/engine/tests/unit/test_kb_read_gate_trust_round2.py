"""Round 2 of #824's review: a script of Scout's own is trusted only when run bare, recorded every run, and what its
trust rests on is protected; a call the scan doesn't model records what it may open; a text value handed to something
it calls is no text (scout.kb.trusted_scripts, scout.kb.python_text, live_run._bare_run)."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from scout.hooks import kb_read_gate as gate
from tests.unit.test_hooks_kb_read_gate import RUN, _denied
from tests.unit.test_kb_read_gate_scripts_and_text import (
    PARSER,
    SEEN,
    SHARDS,
    _categories,
    _py,
    _run,
)
from tests.unit.test_kb_read_gate_scripts_and_text import home as home  # noqa: PLC0414 - the fixture

NOT_BARE = [  # each runs other code around Scout's script, or after a write that may have changed it
    f"python3 -i {PARSER} knowledge-base/a.md",
    f"python3 -X importtime {PARSER} knowledge-base/a.md",
    f"PYTHONPATH=docs python3 {PARSER} knowledge-base/a.md",
    f"PYTHONUSERBASE=docs python3 {PARSER} knowledge-base/a.md",
    f"python3 {PARSER} knowledge-base/a.md <<< 'x'",
    f"echo x | python3 {PARSER} knowledge-base/a.md",
    f"python3 {PARSER} knowledge-base/a.md < docs/a.md",
    f"python3 {PARSER} $(echo knowledge-base/a.md)",
    f"echo x > scripts/json.py; python3 {SHARDS} knowledge-base/a.md",
    f"touch docs/a.md; python3 {PARSER} knowledge-base/a.md",
    f"env python3 {PARSER} knowledge-base/a.md",
    f"export PYTHONPATH=docs; for f in knowledge-base/a.md; do python3 {PARSER} $f; done",
]


@pytest.mark.parametrize("command", NOT_BARE)
def test_scouts_script_run_any_other_way_is_not_trusted(home: Path, command: str) -> None:
    assert "knowledge-base/" in _denied(_run(home, command)), command


@pytest.mark.parametrize(
    "command",
    [
        f"cd . && scripts/scoutctl kb list | head -3; python3 {PARSER} knowledge-base/a.md",
        f"X=1; python3 {PARSER} knowledge-base/a.md",
        f"for f in knowledge-base/a.md knowledge-base/b.md; do python3 {PARSER} $f; done",
        f"python3 {PARSER} knowledge-base/a.md > /dev/null; python3 {PARSER} knowledge-base/b.md",
    ],
)
def test_scouts_script_after_harmless_steps_is_still_trusted(home: Path, command: str) -> None:
    assert _run(home, command) is None, command
    assert _categories(home) == [["program_path:trusted_script"]]


def test_a_module_planted_beside_scouts_script_takes_its_trust(home: Path) -> None:
    (home / "Vault/scripts/json.py").write_text("print(1)\n")
    assert "knowledge-base/" in _denied(_run(home, f"python3 {SHARDS} knowledge-base/a.md"))
    assert _run(home, f"python3 {PARSER} knowledge-base/a.md") is None  # another folder: still trusted


@pytest.mark.parametrize(
    "command",
    [
        f"python3 {SHARDS} knowledge-base/a.md docs/shards",
        f"python3 {SHARDS} --out=docs/x.md knowledge-base/a.md",
        f"python3 {PARSER} knowledge-base/a.md > docs/out.txt",
    ],
)
def test_scouts_script_writing_a_note_outside_the_kb_is_a_copy_out(home: Path, command: str) -> None:
    assert "knowledge-base/" in _denied(_run(home, command)), command


def test_a_vault_script_edited_in_the_run_is_recorded_again(home: Path) -> None:
    assert _run(home, "python3 scripts/mine.py") is None
    (home / "Vault/scripts/mine.py").write_text("print(2)\n")
    assert _run(home, "python3 scripts/mine.py") is None
    assert _categories(home) == [["program_path:vault_script"]] * 2


def _write(home: Path, path: Path):
    body = {
        "tool_name": "Write",
        "tool_input": {"file_path": str(path), "content": "x"},
        "cwd": str(home / "Vault"),
        "session_id": "s1",
    }
    return gate.run(stdin=io.StringIO(json.dumps(body)), env={**RUN, "HOME": str(home)})


@pytest.mark.parametrize(
    "path",
    [
        "Vault/.scout-state/last-rendered/scripts/kb-shard-log.py",
        "Vault/action-items/render.py",
        "Vault/knowledge-base/ontology/__init__.py",
        SEEN,
        "Library/Python/3.11/lib/python/site-packages/usercustomize.py",
        ".local/lib/python3.11/site-packages/x.pth",
    ],
)
def test_a_run_cant_write_what_trust_or_python_startup_rests_on(home: Path, path: str) -> None:
    assert _write(home, home / path) is not None, path
    target = path.removeprefix("Vault/") if path.startswith("Vault/") else f"~/{path}"
    assert _run(home, f"echo x > {target}") is not None, path


def test_the_vault_grows_its_parser_and_its_own_scripts(home: Path) -> None:
    assert _write(home, home / "Vault" / PARSER) is None  # the vault's to grow (bootstrap._CAT_MERGE_FILES)
    assert _write(home, home / "Vault/scripts/my-helper.py") is None
    (home / "Vault" / PARSER).write_text("print('grown')\n")
    assert "knowledge-base/" in _denied(_run(home, f"python3 {PARSER} knowledge-base/a.md"))  # no longer trusted


@pytest.mark.parametrize(
    "command",
    [  # the command word the shell builds (#823), then judged by the interpreter rules
        """p=python3; $p -c 'print(open("knowledge-base/a.md").read())'""",
        """$(which python3) -c 'print(open("knowledge-base/a.md").read())'""",
        """`which python3` -c 'print(open("knowledge-base/a.md").read())'""",
        """p=ruby; $p -e 'puts File.read("knowledge-base/a.md")'""",
        "p=python3; $p - <<'EOF'\nprint(open('knowledge-base/a.md').read())\nEOF",
    ],
)
def test_an_interpreter_named_by_a_built_command_word_is_judged(home: Path, command: str) -> None:
    assert _run(home, command) is not None, command


def test_a_built_command_word_running_harmless_code_is_allowed(home: Path) -> None:
    assert _run(home, "p=python3; $p -c 'print(1+1)'") is None


@pytest.mark.parametrize(
    "plant",
    ["json/__init__.py", "re.pyc", "x.pth", "helper.so"],  # a package, a compiled module, a path file, an extension
)
def test_any_importable_thing_beside_a_copy_takes_its_trust(home: Path, plant: str) -> None:
    folder = home / "Vault/knowledge-base/ontology"
    (folder / plant).parent.mkdir(parents=True, exist_ok=True)
    (folder / plant).write_text("x")
    assert "knowledge-base/" in _denied(_run(home, f"python3 {PARSER} knowledge-base/a.md"))


def test_a_folder_it_cant_list_takes_its_trust(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.kb import trusted_scripts

    def unlistable(self: Path):
        raise PermissionError("no")

    vault = home / "Vault"
    monkeypatch.setattr(Path, "iterdir", unlistable)
    assert trusted_scripts._foreign_modules(vault / "knowledge-base/ontology", vault, [Path("/nowhere")])


def test_a_folder_with_only_the_plugins_files_and_data_keeps_its_trust(home: Path) -> None:
    folder = home / "Vault/knowledge-base/ontology"
    (folder / "schema.yaml").write_text("x: 1\n")  # data, not a module
    (folder / "__pycache__").mkdir()  # compiled copies of what's there
    assert _run(home, f"python3 {PARSER} knowledge-base/a.md") is None


HIDDEN = [  # each opens a path built at run time through a call the scan doesn't know: recorded as program_path
    """python3 -c 'x = chr(107); import io; print(io.open_code(x).read())'""",
    """python3 -c 'x = chr(107); import io; print(io.FileIO(x).read())'""",
    """python3 -c 'x = chr(107); print(__builtins__.open(x).read())'""",
    """python3 -c 'x = chr(107); import netrc; netrc.netrc(x)'""",
    """python3 -c 'x = chr(107); import configparser as c; c.ConfigParser().read(x)'""",
    """python3 -c 'x = chr(107); import mimetypes as m; print(m.read_mime_types(x))'""",
    """python3 -c 'x = chr(107); print(f"{open(x).read()}")'""",
    """python3 -c 'xs = [chr(107)]; import io; xs.sort(key=io.open_code)'""",
]


@pytest.mark.parametrize("command", HIDDEN)
def test_a_call_that_may_open_what_it_cant_spell_is_recorded(home: Path, command: str) -> None:
    assert _run(home, command) is None, command
    assert _categories(home) == [["program_path"]]


@pytest.mark.parametrize(
    "command",
    [
        """python3 -c 'import json,sys; print(json.load(sys.stdin))'""",
        """python3 -c 'import subprocess; subprocess.run(["git", "status"])'""",
        """python3 -c 'import re; print(re.sub("a", "b", "c", flags=re.M))'""",
        """python3 -c 'print(len(sorted([3, 1])))'""",
    ],
)
def test_ordinary_calls_record_nothing(home: Path, command: str) -> None:
    assert _run(home, command) is None, command
    assert _categories(home) == []


ROUND2_NOT_TEXT = [
    'import os as o\ns = "cat knowledge-base/a.md"\nxs = s.split("\\n")\nxs.sort(key=o.system)',
    'import re, os as o\nre.sub(".+", lambda m: str(o.system("cat " + m.group(0))), "knowledge-base/a.md")',
    'import io\nxs = ["knowledge-base/a.md"]\nxs.sort(key=lambda p: print(io.open_code(p).read()))',
    'import pathlib\npathlib.Path("knowledge-base/a.md").replace("docs/x.md")',
    'print(type("knowledge-base/a.md").__call__("knowledge-base/a.md"))',
]


@pytest.mark.parametrize("program", ROUND2_NOT_TEXT)
def test_a_text_value_handed_to_something_it_calls_is_denied(home: Path, program: str) -> None:
    assert "knowledge-base/" in _denied(_run(home, _py(program))), program


@pytest.mark.parametrize(
    "command",
    [
        """python3 -c 'print("knowledge-base/a.md".split("/")[-1])'""",
        """python3 -c 'import json; print(json.dumps({"path": "knowledge-base/a.md"}))'""",
        """python3 -c 'import sys; sys.stdout.write("knowledge-base/a.md")'""",
    ],
)
def test_text_the_quoted_parse_cant_read_is_still_text(home: Path, command: str) -> None:
    assert _run(home, command) is None, command
