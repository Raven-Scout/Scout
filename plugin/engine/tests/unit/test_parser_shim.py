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
import re
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
    (
        ["name_lookup", "--token", "Acme", "--lookup-type", "any"],
        ["name-lookup", "--token", "Acme", "--threshold", "0.7", "--lookup-type", "any"],
    ),
    (
        ["name_lookup", "--token", "Acme", "--lookup-type", "person,organization"],
        ["name-lookup", "--token", "Acme", "--threshold", "0.7", "--lookup-type", "person,organization"],
    ),
    (
        ["traverse", "--name", "priya", "--rel-types", "works_with,works_on"],
        ["traverse", "--name", "priya", "--rels", "works_with,works_on"],
    ),
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


def test_shim_falls_back_to_the_vaults_own_scoutctl(tmp_path: Path) -> None:
    """No scoutctl on PATH: the vault's scripts/scoutctl wrapper runs instead."""
    own = build_privacy_vault(tmp_path / "own")
    shutil.copyfile(SHIM, _rendered(own))
    record = tmp_path / "argv.json"
    _fake_scoutctl(own / "scripts", record)
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    result = _run_shim(own, ["stats"], str(empty))
    assert result.returncode == 0, result.stderr
    got = json.loads(record.read_text(encoding="utf-8"))
    assert got["argv"][:2] == ["kb", "stats"]
    # scoutctl on PATH still wins over the vault's copy.
    path_record = tmp_path / "path-argv.json"
    _fake_scoutctl(tmp_path / "bin", path_record)
    record.unlink()
    assert _run_shim(own, ["stats"], str(tmp_path / "bin")).returncode == 0
    assert path_record.exists() and not record.exists()


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


_IMPORT_ONLY = """
import os, sys, importlib.util
shim, kb = sys.argv[1], sys.argv[2]
opened = []
def hook(event, args):
    if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
        opened.append(os.path.abspath(os.fsdecode(args[0])))
sys.addaudithook(hook)
spec = importlib.util.spec_from_file_location("parser", shim)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
bad = sorted(m for m in sys.modules if m.split(".")[0] in ("scout", "yaml"))
pyc = os.path.join(os.path.dirname(os.path.abspath(shim)), "__pycache__") + os.sep
touched = [p for p in opened if p.startswith(kb) and p != os.path.abspath(shim) and not p.startswith(pyc)]
print(bad, touched)
"""


def test_importing_the_shim_reads_nothing_and_needs_no_engine(vault: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-c", _IMPORT_ONLY, str(_rendered(vault)), str(vault / "knowledge-base")],
        capture_output=True,
        text=True,
        cwd=vault,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[] []"


# ----- the Python API: `from parser import KnowledgeGraph` (compat class) ---------------


def _load_module(path: Path, name: str) -> Any:
    from importlib.machinery import SourceFileLoader
    from importlib.util import module_from_spec, spec_from_loader

    loader = SourceFileLoader(name, str(path))
    spec = spec_from_loader(name, loader)
    assert spec is not None
    module = module_from_spec(spec)
    loader.exec_module(module)
    return module


def _api_results(graph: Any) -> dict[str, Any]:
    return {
        "entities": graph.entities,
        "relationships": graph.relationships,
        "entity": [graph.entity("sam"), graph.entity("priya"), graph.entity("nobody")],
        "query": [
            graph.query(),
            graph.query(type="task"),
            graph.query(type="task", domain="personal"),
            graph.query(exclude_status="completed,cancelled"),
            graph.query(deadline_before="2999-01-01"),
        ],
        "related": [graph.related("priya"), graph.related("nobody")],
        "export": json.loads(graph.export_json()),
        "validate": graph.validate(),
        "name_lookup": [graph.name_lookup("Sam"), graph.name_lookup("Pryia", distance_threshold=0.5)],
        "traverse": [graph.traverse("priya"), graph.traverse("priya", max_hops=1, rel_types={"works_with"})],
        "path": [graph.path("priya", "sam"), graph.path("priya", "priya"), graph.path("priya", "nobody")],
    }


@pytest.mark.parametrize("env", [{}, {"SCOUT_MODE": "briefing"}], ids=["owner", "autonomous"])
def test_python_api_matches_the_pre_shim_class(
    vault: Path, monkeypatch: pytest.MonkeyPatch, env: dict[str, str]
) -> None:
    from scout.kb.ontology import clear_frontmatter_cache

    for key in [k for k in os.environ if k.startswith("SCOUT_")]:
        monkeypatch.delenv(key)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    kb_root = str(vault / "knowledge-base")
    schema = str(vault / "knowledge-base" / "ontology" / "schema.yaml")
    clear_frontmatter_cache()
    shim = _load_module(_rendered(vault), "vault_parser_shim")
    graph = shim.KnowledgeGraph(schema_path=schema, kb_root=kb_root).load()
    assert graph.reader == ("autonomous" if env else "owner")
    assert graph.entity("sam")["_label"]["visibility"] == "private"
    assert all("_label" in e for e in graph.entities.values())
    old = _load_module(LEGACY, "vault_parser_pre_shim").KnowledgeGraph(schema_path=schema, kb_root=kb_root).load()
    new = _strip_labels(_api_results(graph))
    # Deliberate additions since the pre-shim parser: each candidate's `type`, and
    # validate() reporting the malformed note it used to drop silently.
    new["name_lookup"] = [[{k: v for k, v in c.items() if k != "type"} for c in cs] for cs in new["name_lookup"]]
    new["validate"] = [e for e in new["validate"] if not e["message"].startswith("YAML parse failure")]
    assert new == _api_results(old)


def test_python_api_gives_an_agent_nothing(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCOUT_READER", "agent:person:priya@example.com")
    shim = _load_module(_rendered(vault), "vault_parser_shim_agent")
    graph = shim.KnowledgeGraph(
        schema_path=str(vault / "knowledge-base" / "ontology" / "schema.yaml"), kb_root=str(vault / "knowledge-base")
    ).load()
    assert graph.entities == {} and graph.relationships == []
    assert graph.query() == [] and graph.entity("sam") is None
    assert json.loads(graph.export_json()) == {"entities": {}, "relationships": []}


_NO_ENGINE = """
import sys, importlib.util
sys.path = [p for p in sys.path if "site-packages" not in p and "engine" not in p]
spec = importlib.util.spec_from_file_location("parser", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
sys.modules["parser"] = mod
assert "scout" not in sys.modules
try:
    from parser import KnowledgeGraph
except ImportError as e:
    print("ImportError:", e)
else:
    print("imported", KnowledgeGraph)
"""


def test_python_api_without_the_engine_names_scoutctl(vault: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-I", "-c", _NO_ENGINE, str(_rendered(vault))],
        capture_output=True,
        text=True,
        cwd=vault,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("ImportError:") and "scoutctl kb" in result.stdout


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


_SEARCHED = re.compile(r"(?:, type=[^,]+| — searched: [^.]+|, searched: [^)]+)")


def _normalise(command: str, out: str) -> Any:
    """``out`` without labels, and without what the engine adds on purpose: name_lookup's
    candidate type and search scope (and its PEOPLE-only hint), validate's parse failures."""
    if command in ("export", "entity") and out.lstrip().startswith("{"):
        return _strip_labels(json.loads(out))
    out = out.replace("[private] ", "")
    if command == "name_lookup":
        out = "".join(line for line in out.splitlines(True) if "Searched PEOPLE only" not in line)
        out = _SEARCHED.sub("", out)
    if command == "validate":
        kept = [line for line in out.splitlines() if "YAML parse failure" not in line]
        errors = sum(1 for line in kept if line.startswith("  ["))
        kept = [f"{errors} validation error(s) found." if "validation error(s) found" in ln else ln for ln in kept]
        out = "\n".join(kept) + "\n"
    return out


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


def test_shim_labels_private_results_for_autonomous(vault: Path) -> None:
    """The real shim → scoutctl, as a scheduled run reads (SCOUT_READER=autonomous)."""
    path_env = f"{VENV_BIN}{os.pathsep}{os.environ.get('PATH', '')}"
    env = {"SCOUT_READER": "autonomous"}
    query = _run_shim(vault, ["query", "--type", "task", "--domain", "personal"], path_env, **env)
    assert query.returncode == 0, query.stderr
    assert "  [private] [task] task-renew-permit" in query.stdout
    assert "  [task] task-buy-gift" in query.stdout
    related = _run_shim(vault, ["related", "--name", "priya"], path_env, **env).stdout
    assert "  [private] works_with → sam" in related
    entity = json.loads(_run_shim(vault, ["entity", "--name", "sam"], path_env, **env).stdout)
    assert entity["_label"]["visibility"] == "private"
    log = vault / ".scout-logs" / "kb-reads.jsonl"
    assert log.is_file() and '"reader": "autonomous"' in log.read_text(encoding="utf-8")


def test_shim_gives_an_agent_nothing(vault: Path) -> None:
    path_env = f"{VENV_BIN}{os.pathsep}{os.environ.get('PATH', '')}"
    for args in PARITY:
        result = _run_shim(vault, args, path_env, SCOUT_READER="agent:person:priya@example.com")
        assert result.returncode == 0, result.stderr
        for canary in CANARIES.values():
            assert canary not in result.stdout
        assert "task-renew-permit" not in result.stdout and "knowledge-base/" not in result.stdout
        assert "[private]" not in result.stdout and "_label" not in result.stdout
        if args[0] == "query":
            assert result.stdout.strip() == "0 result(s)."
        if args[0] == "export":
            assert json.loads(result.stdout) == {"entities": {}, "relationships": []}
