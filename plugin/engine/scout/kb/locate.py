"""Which vault a KB question goes to, from any working directory (``scoutctl kb vault``).

A session in some other repository has no vault in its cwd, and ``scoutctl kb``'s ``--repo`` defaults to the
cwd's git top level, so a skill there must name the vault itself. It is found the way the installed engine
knows it, never by walking up from an unrelated cwd:

1. ``SCOUT_DATA_DIR``, when set. Authoritative: a value that isn't a vault is an error, not a fall-through.
2. The vault holding the cwd, when the session is already inside one (a vault, one of its worktrees, a sandbox).
3. The ``vault`` in the engine pointer (``~/.local/state/scout/engine.json``).
4. ``~/Scout``.

A folder counts only when it holds ``scout-config.yaml``. Reads nothing in the KB and never writes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from scout import paths
from scout.scripts.engine_pointer import read_pointer


@dataclass(frozen=True)
class VaultLocation:
    """``vault`` is None when no vault is configured; ``reason`` then says why."""

    vault: Path | None
    source: str  # "env" | "cwd" | "pointer" | "default" | "none"
    in_vault: bool  # the cwd is inside ``vault``
    reason: str = ""

    def as_json(self) -> dict[str, object]:
        return {
            "vault": str(self.vault) if self.vault else None,
            "scoutctl": str(self.vault / "scripts" / "scoutctl") if self.vault else None,
            "source": self.source,
            "in_vault": self.in_vault,
            "reason": self.reason,
        }


def _is_vault(p: Path) -> bool:
    return (p / paths.VAULT_MARKER).is_file()


def _resolved(p: str | Path) -> Path | None:
    try:
        return paths.resolve_path(p)
    except (OSError, RuntimeError):
        return None


def _inside(cwd: Path | None, vault: Path) -> bool:
    return cwd is not None and (cwd == vault or vault in cwd.parents)


def locate_vault(env: Mapping[str, str], cwd: Path | None) -> VaultLocation:
    """The vault for a KB read from ``cwd`` (see the module docstring). Never raises."""
    here = _resolved(cwd) if cwd is not None else None

    explicit = env.get("SCOUT_DATA_DIR")
    if explicit:
        v = _resolved(explicit)
        if v is None or not _is_vault(v):
            return VaultLocation(None, "none", False, f"SCOUT_DATA_DIR={explicit} is not a Scout vault")
        return VaultLocation(v, "env", _inside(here, v))

    if here is not None:
        found = paths.vault_containing(here)
        if found is not None:
            return VaultLocation(found, "cwd", True)

    home = Path(env.get("HOME") or Path.home())
    pointer = read_pointer(home=home)
    if pointer is not None and pointer.vault:
        v = _resolved(pointer.vault)
        if v is not None and _is_vault(v):
            return VaultLocation(v, "pointer", _inside(here, v))

    default = _resolved(home / paths.DEFAULT_DATA_DIR_NAME)
    if default is not None and _is_vault(default):
        return VaultLocation(default, "default", _inside(here, default))

    return VaultLocation(None, "none", False, "no Scout vault is configured on this machine")
