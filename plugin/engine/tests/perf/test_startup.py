"""Startup-latency tests for scoutctl.

These assert that import + help + version paths stay fast so the
scout-app UI doesn't feel laggy when invoking scoutctl on user actions.

Budgets have CI headroom. A heavy import creeping onto the `version` path is
caught by counting modules, which no runner's speed can move.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import time

import pytest

HELP_BUDGET_MS = 250
# `version` runs the same import path as `--help` (typer + scout.cli) and costs
# the same: 52-100ms vs 54-109ms best-of-N on macos-latest, ~63ms on
# ubuntu-latest. It had a tighter 150ms budget that failed on shared macOS
# runners running ~3x slow for a whole test (best of 5: 153ms; #310). Best-of-N
# filters a spike, not a runner that is slow throughout, and CPU time on those
# runners varied as much as wall time did. So it shares the --help ceiling, and
# regressions are caught by `test_scoutctl_version_imports_little_beyond_typer`.
VERSION_BUDGET_MS = HELP_BUDGET_MS
# `scoutctl version` loads 14 modules beyond a bare `import typer` today (scout's
# own, a few typer submodules, dataclasses, json). One extra top-level import
# takes it to: watchdog.observers 31, yaml 38, rich.console 66, textual.app 250+.
VERSION_MAX_EXTRA_MODULES = 20
# `budget show` does real file I/O (vault config + the dotfile probe), so it
# gets a looser budget than --help — but it sits on an interactive scout-app
# path, so a heavy import creeping in must still show up here.
BUDGET_SHOW_BUDGET_MS = 400
_RUNS = 5

# Prints the loaded module names at exit. It imports nothing that `import typer`
# or scout.cli wouldn't, so the two runs differ only by what they load.
_LOADED_MODULES = (
    "import atexit, runpy, sys\n"
    "atexit.register(lambda: sys.stderr.write('\\nMODULES=' + repr(sorted(sys.modules)) + '\\n'))\n"
)


def _best_latency_ms(args: list[str]) -> tuple[float, str]:
    """Best-of-N startup latency: the minimum elapsed over ``_RUNS`` invocations.

    A single subprocess sample includes OS scheduling / cold-disk / shared-runner
    noise (the source of past CI flakes — a lone 192ms spike against a 150ms
    budget). The *minimum* reflects the achievable floor, which is what actually
    regresses when a heavy top-level import is added — so the guard stays
    meaningful while no longer flaking on one noisy sample. Returns
    ``(best_ms, last_stdout)``.
    """
    best = float("inf")
    stdout = ""
    for _ in range(_RUNS):
        start = time.perf_counter()
        result = subprocess.run(
            [sys.executable, "-m", "scout.cli", *args],
            capture_output=True,
            text=True,
            timeout=5,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert result.returncode == 0
        stdout = result.stdout
        best = min(best, elapsed_ms)
    return best, stdout


def _loaded_modules(code: str) -> set[str]:
    result = subprocess.run([sys.executable, "-c", _LOADED_MODULES + code], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    line = next(line for line in result.stderr.splitlines() if line.startswith("MODULES="))
    return set(ast.literal_eval(line.removeprefix("MODULES=")))


@pytest.mark.perf
def test_scoutctl_help_latency() -> None:
    best_ms, _ = _best_latency_ms(["--help"])
    assert best_ms < HELP_BUDGET_MS, (
        f"scoutctl --help took {best_ms:.0f}ms (best of {_RUNS}, budget: {HELP_BUDGET_MS}ms). "
        "Check for heavy top-level imports."
    )


@pytest.mark.perf
def test_scoutctl_version_latency() -> None:
    best_ms, stdout = _best_latency_ms(["version"])
    assert stdout.strip(), "version should emit to stdout"
    assert best_ms < VERSION_BUDGET_MS, (
        f"scoutctl version took {best_ms:.0f}ms (best of {_RUNS}, budget: {VERSION_BUDGET_MS}ms)."
    )


@pytest.mark.perf
def test_scoutctl_version_imports_little_beyond_typer() -> None:
    """`scoutctl version` loads few modules beyond the `import typer` it can't avoid."""
    typer_modules = _loaded_modules("import typer")
    version_modules = _loaded_modules(
        "sys.argv = ['scoutctl', 'version']\nrunpy.run_module('scout.cli', run_name='__main__', alter_sys=True)"
    )
    extra = version_modules - typer_modules
    packages = sorted({name.split(".")[0] for name in extra} - {"scout", "typer"})
    assert len(extra) <= VERSION_MAX_EXTRA_MODULES, (
        f"scoutctl version loads {len(extra)} modules beyond `import typer` (limit {VERSION_MAX_EXTRA_MODULES}). "
        f"New packages besides scout/typer: {packages}. Move heavy imports inside the commands that need them."
    )


@pytest.mark.perf
def test_scoutctl_budget_show_latency() -> None:
    """scout-app calls this when the Settings pane opens."""
    best_ms, stdout = _best_latency_ms(["budget", "show", "--json"])
    assert "skip_threshold_usd" in stdout
    assert best_ms < BUDGET_SHOW_BUDGET_MS, (
        f"scoutctl budget show took {best_ms:.0f}ms (best of {_RUNS}, "
        f"budget: {BUDGET_SHOW_BUDGET_MS}ms). Check for heavy top-level imports."
    )
