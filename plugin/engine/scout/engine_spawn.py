"""Starting the engine in a child process without running code from the vault.

``python -m scout`` puts the working directory first on ``sys.path``, so a child
started with its working directory in the vault imports any vault-root module
that shadows one the engine uses (a ``typer.py``, a ``json.py``) before the real
one. :func:`engine_argv` runs the same interpreter with ``-P`` (Python 3.11+),
which leaves the working directory and the script's folder off ``sys.path``;
:data:`NEUTRAL_CWD` is a working directory outside any vault for good measure.
The vault reaches the child through ``SCOUT_DATA_DIR``, never through its cwd.
"""

from __future__ import annotations

import sys
from pathlib import Path


def engine_argv(*args: str) -> list[str]:
    """The command that runs ``scout <args>`` with this interpreter, the working directory off ``sys.path``."""
    return [sys.executable, "-P", "-m", "scout", *args]


def neutral_cwd() -> str:
    """A working directory for an engine child that is not the vault: the user's home."""
    return str(Path.home())
