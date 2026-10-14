#!/usr/bin/env python3
"""Re-exec the calling script under the vault venv when a dependency is missing.

Why this exists: DREAMING.md Step 2a and SKILL.md invoke the health scripts as
`python3 scripts/<name>.py`. On macOS that resolves to a PEP-668
externally-managed interpreter, so `pip install networkx` fails there and the
scripts died at `import networkx` — printing to stderr and exiting non-zero.
Because the dreaming lane itself was dark (Pattern #187), nobody read that
stderr for 64 days and two mandated health checks were silently dead
(`.scout-cache/graph-health.md` froze at 2026-07-01,
`.scout-cache/action-item-health.md` at 2026-06-26).

Fix the wire, not every caller: if the needed modules are absent and
`<repo>/.venv/bin/python3` has them, re-exec the same argv there. Callers keep
working verbatim. If there is no venv, we fall through and the script's own
ImportError message still fires — a missing venv should be loud, not silent.

Usage, at the very top of a script and BEFORE the third-party imports:

    import _venv_bootstrap; _venv_bootstrap.ensure("networkx")
"""
import os
import sys
from pathlib import Path

_GUARD = "SCOUT_VENV_BOOTSTRAPPED"


def _have(modules):
    import importlib.util
    for m in modules:
        try:
            if importlib.util.find_spec(m) is None:
                return False
        except (ImportError, ValueError):
            return False
    return True


def ensure(*modules):
    """Re-exec under <repo>/.venv if any of `modules` cannot be imported here."""
    if not modules or _have(modules):
        return
    if os.environ.get(_GUARD):          # already re-exec'd once — don't loop
        return
    venv_py = Path(__file__).resolve().parents[1] / ".venv" / "bin" / "python3"
    if not venv_py.exists():
        return
    env = dict(os.environ, **{_GUARD: "1"})
    os.execve(str(venv_py), [str(venv_py), os.path.abspath(sys.argv[0])] + sys.argv[1:], env)
