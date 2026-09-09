"""The engine pointer — ``~/.local/state/scout/engine.json`` (spec §4.2).

One file that answers "where is the engine?" for every consumer: Scout.app
(``EngineLocator``), the ``engine/bin/scoutctl`` launcher (venv candidate),
the doctor (consistency check) and ``install.sh``. Written by every bootstrap
entrypoint in the same stage as the ``~/.local/bin/scoutctl`` shim. Readers
treat a missing or malformed file as "no pointer" and fall back to discovery.
"""

from __future__ import annotations

import datetime as _dt
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

POINTER_SCHEMA_VERSION = 1
MANAGED_BY_VALUES = ("scout-app", "install.sh", "claude-code", "dev", "unknown")


def state_dir(home: Path) -> Path:
    return home / ".local" / "state" / "scout"


def pointer_path(home: Path) -> Path:
    return state_dir(home) / "engine.json"


@dataclass(frozen=True)
class EnginePointer:
    version: str
    engine_root: str
    python: str
    scoutctl: str
    vault: str
    managed_by: str
    written_at: str
    schema_version: int = POINTER_SCHEMA_VERSION

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True) + "\n"


def current_pointer(*, vault: Path, managed_by: str) -> EnginePointer:
    """Describe the engine executing this call.

    Root comes from the imported package (editable installs point at the
    source tree); interpreter and scoutctl from ``sys.executable`` (E1).
    """
    import scout
    from scout import __version__
    from scout.scripts.install_schedule_plist import resolve_scoutctl_bin

    root = Path(scout.__file__).parent.parent.parent
    now = _dt.datetime.now(_dt.UTC).replace(microsecond=0)
    return EnginePointer(
        version=__version__,
        engine_root=str(root),
        python=str(Path(sys.executable).absolute()),
        scoutctl=str(resolve_scoutctl_bin()),
        vault=str(vault),
        managed_by=managed_by,
        written_at=now.isoformat().replace("+00:00", "Z"),
    )


def write_pointer(pointer: EnginePointer, *, home: Path) -> Path:
    """Atomically write the pointer; returns its path."""
    target = pointer_path(home)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(pointer.to_json(), encoding="utf-8")
    tmp.replace(target)
    return target


def read_pointer(*, home: Path) -> EnginePointer | None:
    """Return the pointer, or None when absent, unreadable, malformed, or of
    an unknown schema version. Never raises."""
    try:
        raw = json.loads(pointer_path(home).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict) or raw.get("schema_version") != POINTER_SCHEMA_VERSION:
        return None
    try:
        return EnginePointer(
            version=str(raw["version"]),
            engine_root=str(raw["engine_root"]),
            python=str(raw["python"]),
            scoutctl=str(raw["scoutctl"]),
            vault=str(raw["vault"]),
            managed_by=str(raw["managed_by"]),
            written_at=str(raw["written_at"]),
        )
    except (KeyError, TypeError):
        return None
