"""Whether a script a run's interpreter runs (``python3 x.py``) is Scout's own, which the KB read gate trusts as it
trusts ``scoutctl``: what such a script reads isn't in the command, and Scout's own scripts read the KB by design
(``knowledge-base/ontology/parser.py``, ``scripts/kb-shard-log.py``).

A script is Scout's own when it is

- a file of the plugin the engine ships in, or of ``$CLAUDE_PLUGIN_ROOT``, outside any virtualenv or package folder
  in it (those hold other people's code): a run can't write there (:func:`scout.kb.hook_paths.engine_roots`); or
- a vault copy of a file the plugin installs (``scoutctl bootstrap``: a file the plugin's ``templates/`` holds under
  the same path), unchanged: the same bytes as the plugin's template, or as the render bootstrap last wrote
  (``.scout-state/last-rendered/<path>``, :mod:`scout.scripts.vault_drift`), for a vault an older engine upgraded.

A copy the run edited, or any other script, is not: the gate judges its operands and records the run of it.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

# Folders in the plugin that hold code that isn't Scout's: a script there may print any file it's handed.
_THIRD_PARTY = frozenset({".venv", "venv", "site-packages", "dist-packages", "node_modules", ".tox", ".eggs"})
_MAX_BYTES = 4 * 1024 * 1024
_RENDERED = Path(".scout-state") / "last-rendered"


def plugin_roots(env: Mapping[str, str]) -> list[Path]:
    """The plugin the running engine ships in (``<plugin>/engine/scout``), and ``$CLAUDE_PLUGIN_ROOT``."""
    import scout

    roots = [Path(scout.__file__).resolve().parent.parents[1]]
    given = env.get("CLAUDE_PLUGIN_ROOT")
    if given:
        roots.append(Path(given).resolve())
    return roots


def _bytes(path: Path) -> bytes | None:
    try:
        if not path.is_file() or path.stat().st_size > _MAX_BYTES:
            return None
        return path.read_bytes()
    except OSError:
        return None


def trusted_script(path: str | os.PathLike[str], vault: Path, env: Mapping[str, str]) -> bool:
    """Whether ``path`` (absolute, as resolved) is a script of Scout's own (see the module's docstring)."""
    try:
        real = Path(path).resolve()
    except (OSError, RuntimeError):
        return False
    if not real.is_file():
        return False
    roots = plugin_roots(env)
    for root in roots:
        if real.is_relative_to(root):
            return not _THIRD_PARTY.intersection(real.relative_to(root).parts)
    try:
        home = vault.resolve()
    except (OSError, RuntimeError):
        return False
    if not real.is_relative_to(home):
        return False
    rel = real.relative_to(home)
    live = _bytes(real)
    if live is None:
        return False
    shipped = [_bytes(root / "templates" / rel) for root in roots]
    if not any(t is not None for t in shipped):
        return False  # not a file the plugin installs
    return live in shipped or _bytes(home / _RENDERED / rel) == live
