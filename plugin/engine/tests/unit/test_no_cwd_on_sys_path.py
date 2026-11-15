"""Nothing Scout starts runs Python with the working directory on sys.path.

``python -m``, ``python -c`` and ``python -`` (stdin) put the cwd first on
sys.path. Scout starts processes with the vault as cwd, so a vault-root
``json.py`` or ``typer.py`` would be imported in place of the real module.
The engine builds its argv with ``scout._spawn.python_module_argv`` (``-P``);
shell snippets in templates, commands, phases and skills pass ``-I`` (stdlib only, and it
works on Apple's python3 3.9, which has no ``-P``).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from scout._spawn import python_module_argv

ENGINE = Path(__file__).resolve().parents[2]
PLUGIN = ENGINE.parent

# `python3 -c`, `python -m`, `python3 -` with no flag first: the unsafe forms.
_BARE = re.compile(r"\bpython3?(?:\.\d+)?\s+-(?:[cm]\b|\s)")
# `executable, "-m"` / `executable, "-c"` in engine code: argv built by hand.
_HAND_ARGV = re.compile(r"""executable\)?\s*,\s*["']-[cm]["']""")


def _offenders(paths: list[Path], pattern: re.Pattern[str], *, skip_comments: bool) -> list[str]:
    found = []
    for path in paths:
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if skip_comments and line.lstrip().startswith("#"):
                continue
            if pattern.search(line):
                found.append(f"{path.relative_to(PLUGIN)}:{n}: {line.strip()}")
    return found


def test_python_module_argv_leaves_the_cwd_off_sys_path():
    assert python_module_argv("scout", "self-update", "run") == [
        sys.executable,
        "-P",
        "-m",
        "scout",
        "self-update",
        "run",
    ]


def test_engine_code_builds_python_argv_through_the_helper():
    sources = [p for p in (ENGINE / "scout").rglob("*.py") if p.name != "_spawn.py"]
    assert _offenders(sources, _HAND_ARGV, skip_comments=True) == []


def test_templates_commands_and_phases_never_run_bare_python_snippets():
    """Phases and skills count too: a run's session executes their shell blocks with the vault as cwd."""
    files = [
        p
        for root in (PLUGIN / "templates", PLUGIN / "commands", PLUGIN / "hooks", PLUGIN / "phases", PLUGIN / "skills")
        for p in root.rglob("*")
        if p.is_file() and p.suffix in {".tmpl", ".sh", ".md", ".json"}
    ]
    assert files
    assert _offenders(files, _BARE, skip_comments=True) == []


# `python3 scripts/x.py` with no flag first: the script's folder goes on sys.path (review of #642).
_BARE_SCRIPT = re.compile(r"\bpython3?(?:\.\d+)?\s+[\"']?[\w$./{}\"-]*\.py\b")


def test_runner_templates_never_run_a_vault_script_bare():
    """Runner and hook templates run outside the sandbox, so a vault script they start gets -I."""
    files = [p for p in (PLUGIN / "templates").rglob("*") if p.is_file() and p.suffix in {".tmpl", ".sh"}]
    assert files
    assert _offenders(files, _BARE_SCRIPT, skip_comments=True) == []
