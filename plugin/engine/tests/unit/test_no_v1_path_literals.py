"""No layout-v1 vault path is spelled out in engine code (layout v2, ADR 0011).

A vault path goes through ``scout.layout`` (``for_vault(vault).script(...)``, ``space_dir(...)``), so it follows
the vault's layout. A hardcoded ``Path("scripts") / "git-safe-commit.sh"`` worked on every v1 vault and broke on
v2 (review of #832). This test finds such literals: a path that exists only on v1 (from the layout tables
themselves), as a string with a ``/`` in it, or a bare v1 folder name used to build a path (``Path("scripts")``,
``root / "hooks"``). Docstrings are skipped.

A literal that is right on purpose (it names the plugin's own folders, or lists v1 next to v2 for old commits)
goes in ``ALLOWED`` with its reason.
"""

from __future__ import annotations

import ast
import functools
import re
from pathlib import Path

from scout import layout

ENGINE = Path(__file__).resolve().parents[2] / "scout"

# Files that define or translate the v1 layout: every v1 path in them is deliberate.
WHOLE_FILES = {
    "layout.py",  # the layout tables
    "layout_migrate.py",  # the v1 -> v2 move
}

# (file, literal) pairs that are right as written. A literal ending in "*" allows that prefix.
ALLOWED: dict[tuple[str, str], str] = {
    ("agent_runtimes/gate_probe.py", "hooks"): "the plugin's own hooks/hooks.json, not the vault's",
    ("changes/scan.py", "docs/wishlist/"): "matches commits from before the move too, next to the v2 prefix",
    ("kb/hook_paths.py", "hooks"): "a git hooks directory",
    ("kb/hook_paths.py", "scripts/*"): "V1_RUNNER_FILES: the v1 names, listed next to the v2 ones",
    ("kb/hook_paths.py", "hooks/kb-pre-filter.sh"): "V1_RUNNER_FILES: the v1 name, listed next to the v2 one",
    ("kb/lossless.py", "knowledge-base/projects/**/archive/**"): "the v1 glob, next to the per-space one",
    ("scripts/bootstrap.py", "docs/kb-page-standard.md"): "_CAT_MERGE_FILES_V1: an older engine's sidecar path",
    ("scripts/self_update_run.py", "scripts"): "the plugin's own scripts/install-venv.sh, not the vault's",
}


@functools.cache
def _parsed(path: Path) -> tuple[ast.Module, dict[ast.AST, ast.AST]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return tree, {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


def _v1_only() -> tuple[set[str], set[str]]:
    v2 = set(layout._V2_PATHS.values())
    v1 = {v for k, v in layout._V1_PATHS.items() if v and v != layout._V2_PATHS[k]}
    v1 |= set(layout.V1_SPACE_PATHS.values())
    v1 = {v for v in v1 if v not in v2 and v != "knowledge-base"}
    multi = {v for v in v1 if "/" in v or "." in v}
    return multi, v1 - multi


def _is_v1(value: str, parent: ast.AST | None, multi: set[str], bare: set[str]) -> bool:
    s = value.strip().lstrip("/").removeprefix("./")
    if any(s == v or s.startswith(v + "/") for v in multi):
        return True
    if any(s.startswith(v + "/") and len(s) > len(v) + 1 for v in bare):
        return True
    if s.strip("/") in bare:
        if isinstance(parent, ast.BinOp) and isinstance(parent.op, ast.Div):
            return True
        if isinstance(parent, ast.Call):
            name = getattr(parent.func, "id", None) or getattr(parent.func, "attr", None)
            return name in ("Path", "PurePosixPath", "joinpath")
    return False


def _allowed(rel: str, value: str) -> bool:
    for (f, lit), _why in ALLOWED.items():
        if f != rel:
            continue
        if lit == value or (lit.endswith("*") and value.startswith(lit[:-1])):
            return True
    return False


def find_v1_literals(root: Path = ENGINE) -> list[str]:
    multi, bare = _v1_only()
    out: list[tuple[str, int, str]] = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel in WHOLE_FILES:
            continue
        tree, parents = _parsed(path)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            parent = parents.get(node)
            if isinstance(parent, ast.Expr):
                continue  # a docstring
            if _is_v1(node.value, parent, multi, bare) and not _allowed(rel, node.value):
                out.append((rel, node.lineno, node.value))
    return [f"scout/{rel}:{line}: {value!r}" for rel, line, value in sorted(out)]


def test_no_engine_code_spells_out_a_v1_vault_path() -> None:
    found = find_v1_literals()
    assert not found, (
        "v1 vault paths spelled out in engine code; resolve them through scout.layout "
        "(or add them to ALLOWED with the reason they are right):\n" + "\n".join(found)
    )


def test_every_allowed_entry_is_still_used() -> None:
    """An allowance whose literal is gone would quietly cover the next one."""
    multi, bare = _v1_only()
    used: set[tuple[str, str]] = set()
    for f, lit in ALLOWED:
        tree, parents = _parsed(ENGINE / f)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                hit = lit == node.value or (lit.endswith("*") and node.value.startswith(lit[:-1]))
                if hit and _is_v1(node.value, parents.get(node), multi, bare):
                    used.add((f, lit))
    assert set(ALLOWED) == used, sorted(set(ALLOWED) - used)


def test_the_scan_catches_the_retitle_bug(tmp_path: Path) -> None:
    (tmp_path / "bad.py").write_text(
        'from pathlib import Path\nHELPER = Path("scripts") / "git-safe-commit.sh"\n'
        'LOG = "knowledge-base/session-log.md"\n'
        'def f(v):\n    return f"{v}/hooks/kb-pre-filter.sh"\n'
        'OK = "scout/scripts/x.sh"\n'
    )
    found = find_v1_literals(tmp_path)
    assert [ln.split(": ", 1)[1] for ln in found] == [
        "'scripts'",
        "'knowledge-base/session-log.md'",
        "'/hooks/kb-pre-filter.sh'",
    ]


# ---- shell templates and plugin scripts ------------------------------------------------------------------------
#
# A shell template builds vault paths from a vault variable (`$VAULT/scripts/scoutctl`), which the AST scan above
# can't see. git-safe-commit.sh.tmpl's broker branch did exactly that and broke every scheduled run's commit on
# layout v2. A script finds a sibling through its own folder (`$(dirname "${BASH_SOURCE[0]}")`), and a vault path
# through scout.layout or the paths the renderer fills in.

PLUGIN = ENGINE.parents[1]
_VAULT_VAR = (
    r"(?:\$VAULT|\$\{VAULT\}|\$SCOUT_DIR|\$\{SCOUT_DIR\}|\{\{SCOUT_DIR\}\}|\$SCOUT_DATA_DIR|\$\{SCOUT_DATA_DIR\})"
)
_V1_SHELL = re.compile(
    _VAULT_VAR
    + r"/(?:scripts/|hooks/|docs/|(?:SKILL|DREAMING|RESEARCH|RESPOND)\.md|run-[a-z-]+\.sh|dreaming-proposals\.md"
    r"|inbox\.md|drop/|meetings/)"
)


def find_v1_shell_paths(roots: tuple[Path, ...] = (PLUGIN / "templates", PLUGIN / "hooks")) -> list[str]:
    out = []
    for root in roots:
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for n, line in enumerate(text.splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue
                if _V1_SHELL.search(line):
                    out.append(f"{path.relative_to(root.parent).as_posix()}:{n}: {line.strip()}")
    return out


def test_no_template_builds_a_v1_vault_path() -> None:
    found = find_v1_shell_paths()
    assert not found, "v1 vault paths built from a vault variable in plugin templates:\n" + "\n".join(found)


def test_the_shell_scan_catches_the_commit_helper_bug(tmp_path: Path) -> None:
    (tmp_path / "x.sh.tmpl").write_text(
        '# $VAULT/scripts/ in a comment is fine\nexec "$VAULT/scripts/scoutctl" broker commit\n'
        'cat "{{SCOUT_DIR}}/SKILL.md"\nok="$VAULT/scout/scripts/scoutctl"\n'
    )
    assert [ln.split(":", 2)[1] for ln in find_v1_shell_paths((tmp_path,))] == ["2", "3"]
