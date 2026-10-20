"""The vault's ``knowledge-base/ontology/parser.py`` is a shim over ``scoutctl kb`` (#453, spec §6.1).

The rendered template keeps the parser's CLI (same subcommands and flags) and
``exec``s ``scoutctl kb <sub>`` with the same arguments, ``--reader`` passed
through. Without ``scoutctl`` on PATH it exits non-zero, names ``scoutctl``,
and opens nothing under ``knowledge-base/`` (Review Focus 5): never a fallback
read.

Parity: for the owner, the shim's output is what the pre-shim parser printed,
plus labels only (``[private] `` markers in text, ``_label`` keys in JSON). The
pre-shim parser is kept, byte for byte, as
``tests/fixtures/vault-parser-pre-shim.py.txt`` (the v0.18.2 template): the
reference the engine's ``KnowledgeGraph`` port was checked against.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.unit.privacy_vault_helpers import CANARIES, build_privacy_vault

PLUGIN = Path(__file__).resolve().parents[3]
SHIM = PLUGIN / "templates" / "knowledge-base" / "ontology" / "parser.py"
LEGACY = Path(__file__).resolve().parents[1] / "fixtures" / "vault-parser-pre-shim.py.txt"
VENV_BIN = Path(sys.executable).parent


@pytest.fixture(scope="module")
def vault(tmp_path_factory: pytest.TempPathFactory) -> Path:
    v = build_privacy_vault(tmp_path_factory.mktemp("shim"))
    shutil.copyfile(SHIM, v / "knowledge-base" / "ontology" / "parser.py")
    return v


def _rendered(vault: Path) -> Path:
    return vault / "knowledge-base" / "ontology" / "parser.py"


def _fake_scoutctl(bin_dir: Path, record: Path) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "scoutctl"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "record = {'argv': sys.argv[1:], 'reader': os.environ.get('SCOUT_READER')}\n"
        f"open({str(record)!r}, 'w').write(json.dumps(record))\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)


def _run_shim(vault: Path, args: list[str], path_env: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_rendered(vault)), *args],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": path_env, **env},
        cwd=vault,
    )


# (parser.py args, the scoutctl kb args after the --kb-root/--schema pair)
MAPPING: list[tuple[list[str], list[str]]] = [
    (["query"], ["query"]),
    (
        ["query", "--type", "task", "--status", "open", "--exclude-status", "completed,cancelled"],
        ["query", "--type", "task", "--status", "open", "--exclude-status", "completed,cancelled"],
    ),
    (
        ["query", "--domain", "personal", "--deadline-before", "2026-12-31"],
        ["query", "--domain", "personal", "--deadline-before", "2026-12-31"],
    ),
    (["entity", "--name", "sam"], ["entity", "--name", "sam"]),
    (["related", "--name", "priya"], ["related", "--name", "priya"]),
    (["traverse", "--name", "priya"], ["traverse", "--name", "priya"]),
    (
        ["traverse", "--name", "priya", "--hops", "3", "--rels", "works_with,works_on"],
        ["traverse", "--name", "priya", "--hops", "3", "--rels", "works_with,works_on"],
    ),
    (["path", "--name", "priya", "--to", "sam"], ["path", "--name", "priya", "--to", "sam"]),
    (
        ["path", "--name", "priya", "--to", "sam", "--hops", "2"],
        ["path", "--name", "priya", "--to", "sam", "--hops", "2"],
    ),
    (["name_lookup", "--token", "Sam"], ["name-lookup", "--token", "Sam", "--threshold", "0.7"]),
    (["name_lookup", "--token", "Sam", "--threshold", "0.5"], ["name-lookup", "--token", "Sam", "--threshold", "0.5"]),
    (["export"], ["export"]),
    (["stats"], ["stats"]),
    (["validate"], ["validate"]),
]


@pytest.mark.parametrize(("args", "want"), MAPPING, ids=lambda a: " ".join(a) if isinstance(a, list) else a)
def test_shim_execs_scoutctl(vault: Path, tmp_path: Path, args: list[str], want: list[str]) -> None:
    record = tmp_path / "argv.json"
    _fake_scoutctl(tmp_path / "bin", record)
    result = _run_shim(vault, args, str(tmp_path / "bin"))
    assert result.returncode == 0, result.stderr
    kb_root = str(vault / "knowledge-base")
    schema = str(vault / "knowledge-base" / "ontology" / "schema.yaml")
    got = json.loads(record.read_text(encoding="utf-8"))
    assert got["argv"] == ["kb", want[0], "--kb-root", kb_root, "--schema", schema, *want[1:]]


def test_shim_passes_reader_and_environment_through(vault: Path, tmp_path: Path) -> None:
    record = tmp_path / "argv.json"
    _fake_scoutctl(tmp_path / "bin", record)
    result = _run_shim(vault, ["query", "--reader", "autonomous"], str(tmp_path / "bin"), SCOUT_READER="autonomous")
    assert result.returncode == 0, result.stderr
    got = json.loads(record.read_text(encoding="utf-8"))
    assert got["argv"][-2:] == ["--reader", "autonomous"]
    assert got["reader"] == "autonomous"


def test_shim_explicit_paths_are_passed(vault: Path, tmp_path: Path) -> None:
    record = tmp_path / "argv.json"
    _fake_scoutctl(tmp_path / "bin", record)
    _run_shim(vault, ["stats", "--kb-root", "/elsewhere/kb", "--schema", "/elsewhere/s.yaml"], str(tmp_path / "bin"))
    got = json.loads(record.read_text(encoding="utf-8"))
    assert got["argv"] == ["kb", "stats", "--kb-root", "/elsewhere/kb", "--schema", "/elsewhere/s.yaml"]


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["entity"], "Error: --name required for entity command"),
        (["related"], "Error: --name required for related command"),
        (["traverse"], "Error: --name required for traverse command"),
        (["path", "--name", "priya"], "Error: --name (source) and --to (target) required for path command"),
        (["name_lookup"], "Error: --token required for name_lookup command"),
    ],
)
def test_shim_keeps_the_missing_argument_messages(vault: Path, tmp_path: Path, args: list[str], message: str) -> None:
    record = tmp_path / "argv.json"
    _fake_scoutctl(tmp_path / "bin", record)
    result = _run_shim(vault, args, str(tmp_path / "bin"))
    assert (result.returncode, result.stdout.strip()) == (0, message)
    assert not record.exists()


def test_shim_rejects_unknown_command(vault: Path, tmp_path: Path) -> None:
    result = _run_shim(vault, ["frobnicate"], str(tmp_path))
    assert result.returncode == 2 and "invalid choice" in result.stderr


_AUDIT = """
import os, runpy, sys
shim = sys.argv[1]
log = sys.argv[2]
opened = []
def hook(event, args):
    if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
        opened.append(os.fsdecode(args[0]))
sys.addaudithook(hook)
sys.argv = [shim, *sys.argv[3:]]
code = 0
try:
    runpy.run_path(shim, run_name="__main__")
except SystemExit as e:
    code = e.code if isinstance(e.code, int) else 1
with open(log, "w") as fh:
    fh.write("\\n".join(opened))
sys.exit(code)
"""


@pytest.mark.parametrize("args", [["query", "--type", "task"], ["export"], ["entity", "--name", "sam"], ["stats"]])
def test_shim_without_scoutctl_fails_closed(vault: Path, tmp_path: Path, args: list[str]) -> None:
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    log = tmp_path / "opened.txt"
    result = subprocess.run(
        [sys.executable, "-c", _AUDIT, str(_rendered(vault)), str(log), *args],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": str(empty)},
        cwd=vault,
    )
    assert result.returncode != 0
    assert "scoutctl" in result.stderr
    assert result.stdout == ""
    kb_dirs = (str(vault / "knowledge-base"), str((vault / "knowledge-base").resolve()))
    opened = [p for p in log.read_text(encoding="utf-8").splitlines() if p]
    shim_paths = {str(_rendered(vault)), str(_rendered(vault).resolve())}
    touched = [p for p in opened if os.path.abspath(p).startswith(kb_dirs) and p not in shim_paths]
    assert touched == [], touched
    for canary in CANARIES.values():
        assert canary not in result.stderr


def test_shim_imports_nothing_outside_the_stdlib() -> None:
    text = SHIM.read_text(encoding="utf-8")
    assert "import yaml" not in text and "from scout" not in text and "import scout" not in text


# ----- parity: pre-shim parser vs shim → scoutctl, labels removed ----------------------

PARITY: list[list[str]] = [
    ["query"],
    ["query", "--type", "task"],
    ["query", "--type", "task", "--domain", "personal"],
    ["query", "--status", "open"],
    ["query", "--type", "task", "--exclude-status", "completed,cancelled"],
    ["query", "--deadline-before", "2999-01-01"],
    ["entity", "--name", "sam"],
    ["entity", "--name", "priya"],
    ["entity", "--name", "nobody"],
    ["related", "--name", "priya"],
    ["related", "--name", "nobody"],
    ["traverse", "--name", "priya"],
    ["traverse", "--name", "priya", "--hops", "1"],
    ["traverse", "--name", "priya", "--rels", "works_with"],
    ["traverse", "--name", "nobody"],
    ["path", "--name", "priya", "--to", "sam"],
    ["path", "--name", "sam", "--to", "priya", "--hops", "1"],
    ["path", "--name", "priya", "--to", "priya"],
    ["path", "--name", "priya", "--to", "nobody"],
    ["name_lookup", "--token", "Sam"],
    ["name_lookup", "--token", "Pryia", "--threshold", "0.5"],
    ["name_lookup", "--token", "zzz"],
    ["export"],
    ["stats"],
    ["validate"],
]


def _strip_labels(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_labels(v) for k, v in value.items() if k != "_label"}
    if isinstance(value, list):
        return [_strip_labels(v) for v in value]
    return value


def _normalise(command: str, out: str) -> Any:
    if command in ("export", "entity") and out.lstrip().startswith("{"):
        return _strip_labels(json.loads(out))
    return out.replace("[private] ", "")


@pytest.mark.parametrize("args", PARITY, ids=lambda a: " ".join(a))
def test_shim_output_matches_the_pre_shim_parser(vault: Path, args: list[str]) -> None:
    kb_root = str(vault / "knowledge-base")
    schema = str(vault / "knowledge-base" / "ontology" / "schema.yaml")
    env = {k: v for k, v in os.environ.items() if not k.startswith("SCOUT_")}
    old = subprocess.run(
        [sys.executable, str(LEGACY), *args, "--kb-root", kb_root, "--schema", schema],
        capture_output=True,
        text=True,
        env=env,
        cwd=vault,
    )
    new = _run_shim(vault, args, f"{VENV_BIN}{os.pathsep}{env.get('PATH', '')}")
    assert old.returncode == 0, old.stderr
    assert new.returncode == 0, new.stderr
    assert _normalise(args[0], new.stdout) == _normalise(args[0], old.stdout)


def test_shim_output_is_labelled_for_the_owner(vault: Path) -> None:
    path_env = f"{VENV_BIN}{os.pathsep}{os.environ.get('PATH', '')}"
    out = _run_shim(vault, ["query", "--type", "task"], path_env).stdout
    assert "  [private] [task] task-renew-permit" in out
    export = json.loads(_run_shim(vault, ["export"], path_env).stdout)
    assert export["entities"]["sam"]["_label"]["visibility"] == "private"


def test_shim_gives_an_agent_nothing(vault: Path) -> None:
    path_env = f"{VENV_BIN}{os.pathsep}{os.environ.get('PATH', '')}"
    for args in PARITY:
        result = _run_shim(vault, args, path_env, SCOUT_READER="agent:person:priya@example.com")
        assert result.returncode == 0, result.stderr
        for canary in CANARIES.values():
            assert canary not in result.stdout
        assert "task-renew-permit" not in result.stdout
