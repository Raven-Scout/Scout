"""Argv for starting this engine's Python on one of its own modules.

``python -m mod`` puts the current working directory first on ``sys.path``.
Scout starts many processes with the vault as their cwd (the heartbeat's
launchd job, every Claude Code hook, the pre-commit hook), so a ``typer.py``,
``yaml.py`` or ``scout/`` at the vault root would be imported in place of the
real package. Vault content can arrive by git sync from another machine or be
written by an autonomous run, and these processes upgrade the engine and run
the privacy gates.

``-P`` (Python 3.11+, which the engine requires) leaves the cwd off
``sys.path``. It is a flag, not ``PYTHONSAFEPATH`` in the environment, so it
does not reach grandchildren such as a vault's own ``python3 scripts/x.py``,
which still needs its script directory on the path. ``engine/bin/scoutctl``
passes the same flag. Build every ``sys.executable -m …`` argv here.
"""

from __future__ import annotations

import sys

SAFE_PATH_FLAG = "-P"


def python_module_argv(module: str, *args: str) -> list[str]:
    """``[sys.executable, "-P", "-m", module, *args]``: run ``module`` without the cwd on ``sys.path``."""
    return [sys.executable, SAFE_PATH_FLAG, "-m", module, *args]
