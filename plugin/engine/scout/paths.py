"""Path resolution for Scout engine and data dirs.

All paths are expanded (~) and resolved (symlinks, relative segments).
"""

from __future__ import annotations

import datetime as _dt
import os
from pathlib import Path

from scout.errors import DataDirError

DEFAULT_DATA_DIR_NAME = "Scout"
# The file that makes a folder a Scout vault: bootstrap writes it first, and every vault has one.
VAULT_MARKER = "scout-config.yaml"


def resolve_path(p: str | Path) -> Path:
    """Expand ~ and resolve symlinks/relative segments to an absolute Path."""
    return Path(p).expanduser().resolve()


def vault_containing(start: str | Path) -> Path | None:
    """The nearest folder at or above ``start`` that holds a ``scout-config.yaml``, else None."""
    try:
        here = resolve_path(start)
    except (OSError, RuntimeError):
        return None
    for folder in (here, *here.parents):
        if (folder / VAULT_MARKER).is_file():
            return folder
    return None


def _cwd() -> Path | None:
    try:
        return Path.cwd()
    except OSError:  # the working directory was deleted
        return None


def data_dir(explicit: str | Path | None = None, *, cwd: str | Path | None = None) -> Path:
    """Resolve the Scout data directory.

    Precedence:
      1. Explicit argument
      2. $SCOUT_DATA_DIR env var
      3. The vault containing ``cwd`` (default: the process's working directory)
      4. ~/Scout

    Step 3 lets a session started inside a vault that isn't ~/Scout (a sandbox,
    a second vault) find it without SCOUT_DATA_DIR. The scheduled runners all
    export SCOUT_DATA_DIR, so they never reach it.

    Does NOT validate that the dir exists — callers use require_data_dir().
    """
    if explicit is not None:
        return resolve_path(explicit)

    env = os.environ.get("SCOUT_DATA_DIR")
    if env:
        return resolve_path(env)

    start = cwd if cwd is not None else _cwd()
    found = vault_containing(start) if start is not None and str(start) else None
    if found is not None:
        return found

    return resolve_path(Path.home() / DEFAULT_DATA_DIR_NAME)


def logs_dir(data: Path | None = None) -> Path:
    return (data or data_dir()) / ".scout-logs"


def cache_dir(data: Path | None = None) -> Path:
    return (data or data_dir()) / ".scout-cache"


def state_dir(data: Path | None = None) -> Path:
    return (data or data_dir()) / ".scout-state"


def config_path(data: Path | None = None) -> Path:
    """The vault's config file — ``scout-config.yaml``, NO dot.

    This is the file /scout-setup and bootstrap actually write. The loader
    historically pointed at ``.scout-config.yaml``, a dotfile no code path
    ever wrote, which silently disabled the whole user-override layer
    (#207/#202). The undotted file holds bootstrap state (version stamps,
    connectors, schedule) alongside user overrides; scout.config.load_config
    normalizes its legacy key shapes on read.
    """
    return (data or data_dir()) / "scout-config.yaml"


def kb_dir(data: Path | None = None) -> Path:
    return (data or data_dir()) / "knowledge-base"


def action_items_dir(data: Path | None = None) -> Path:
    return (data or data_dir()) / "action-items"


def require_data_dir(data: Path | None = None) -> Path:
    """Return the data dir, raising DataDirError if it does not exist or is not a directory.

    Uses a single `is_dir()` check (False for both missing paths and non-dirs)
    to eliminate the exists()/is_dir() TOCTOU window. The helpful message
    distinguishes the two failure modes by reading `d.exists()` only inside
    the failure branch.
    """
    d = data or data_dir()
    if not d.is_dir():
        if not d.exists():
            raise DataDirError(f"Scout data dir does not exist: {d}\nRun: scoutctl setup data-dir")
        raise DataDirError(f"Scout data dir is not a directory: {d}")
    return d


def _today(data: Path | None = None) -> _dt.date:
    """Today in the configured day-boundary zone (scout.config.today).

    Indirection so tests can monkeypatch the date without freezing time.
    Imported lazily: scout.config imports scout.paths, so a module-level
    import here would be circular.
    """
    from scout.config import today

    return today(data)


def action_items_daily_path(data: Path | None = None, date: _dt.date | None = None) -> Path:
    """Return the daily action-items markdown path for `date` (default: today
    in the configured timezone — see scout.config.today, #207).

    Filename format matches the existing ~/Scout convention:
    `action-items-YYYY-MM-DD.md` under the data dir's `action-items/`.
    """
    d = date or _today(data)
    return action_items_dir(data) / f"action-items-{d.isoformat()}.md"


def id_map_path(data: Path | None = None) -> Path:
    """Return the path to the prefix↔ULID map JSON file.

    Lives under `$SCOUT_DATA_DIR/.scout-state/id-map.json`. Parent dir
    is created on first write; readers may find it absent and treat
    that as an empty map.
    """
    target = data if data is not None else data_dir()
    return target / ".scout-state" / "id-map.json"
