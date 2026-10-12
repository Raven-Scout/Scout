"""The scoring layer is pure: metrics and checks take parsed data and do no I/O.

The source is parsed, never imported, so a violation is caught before the
module could run anything. Purity is what lets every check be tested free,
against committed golden outputs (spec 2026-10-12 §9).
"""

from __future__ import annotations

import ast
from pathlib import Path

HARNESS = Path(__file__).resolve().parents[1] / "eval" / "harness"
FORBIDDEN_MODULES = {"os", "subprocess", "socket", "shutil", "urllib", "requests", "http", "pathlib", "io"}
FORBIDDEN_CALLS = {"open", "exec", "eval", "__import__"}


def violations(source: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [f"import {a.name}" for a in node.names if a.name.split(".")[0] in FORBIDDEN_MODULES]
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] in FORBIDDEN_MODULES:
            found.append(f"from {node.module} import")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALLS:
            found.append(f"call {node.func.id}()")
    return found


def _scored_modules() -> list[Path]:
    return [HARNESS / "metrics.py", HARNESS / "verdicts.py", *sorted((HARNESS / "checks").glob("*.py"))]


def test_the_checker_catches_io() -> None:
    bad = "import os\nfrom subprocess import run\nfrom pathlib import Path\nopen('x')\n"
    assert violations(bad) == ["import os", "from subprocess import", "from pathlib import", "call open()"]


def test_metrics_and_checks_do_no_io() -> None:
    modules = _scored_modules()
    assert HARNESS / "metrics.py" in modules
    problems = {m.name: v for m in modules if (v := violations(m.read_text(encoding="utf-8")))}
    assert problems == {}
