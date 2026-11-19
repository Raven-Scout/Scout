"""Which vault a KB question goes to, and which engine answers it (``scoutctl kb vault``).

A session in some other repository has no vault in its cwd, and ``scoutctl kb``'s ``--repo`` defaults to the
cwd's git top level, so the scout-ask skill names the vault itself. The vault comes from configuration only:

1. ``SCOUT_DATA_DIR``, when set. Authoritative: a value that isn't an absolute path to a vault is an error, not
   a fall-through. When it differs from the engine pointer's vault, ``reason`` says so.
2. The ``vault`` in the engine pointer (``~/.local/state/scout/engine.json``). A pointer whose vault is gone or
   isn't absolute is an error too, never a silent fall-back to ``~/Scout``.
3. ``~/Scout``, when there is no pointer. A pointer file that is unreadable or malformed counts as none, and
   ``reason`` says so.

Never the cwd: a cloned repository could hold a planted ``scout-config.yaml`` and ``scripts/scoutctl``, and a
vault found there would have its script run. The cwd only decides ``in_vault``. A folder counts as a vault only
when it holds ``scout-config.yaml``. Reads no note and writes nothing.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from scout import paths
from scout.scripts.engine_pointer import pointer_path, read_pointer


@dataclass(frozen=True)
class VaultLocation:
    """``vault`` is None when there is no usable vault, and ``reason`` says why. With a vault, a non-empty
    ``reason`` is a warning the answer should name (SCOUT_DATA_DIR overriding the installed vault)."""

    vault: Path | None
    source: str  # "env" | "pointer" | "default" | "none"
    in_vault: bool  # the cwd is inside ``vault``
    reason: str = ""

    def as_json(self, scoutctl: Path | None) -> dict[str, object]:
        return {
            "vault": str(self.vault) if self.vault else None,
            "scoutctl": str(scoutctl) if scoutctl else None,
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


def _vault_at(value: str, what: str) -> tuple[Path | None, str]:
    """``value`` as a vault, or (None, why not). Relative values are refused: they'd resolve against the cwd."""
    if not Path(value).expanduser().is_absolute():
        return None, f"{what} ({value}) is not an absolute path"
    v = _resolved(value)
    if v is None or not _is_vault(v):
        return None, f"{what} ({value}) is not a Scout vault"
    return v, ""


def locate_vault(env: Mapping[str, str], cwd: Path | None) -> VaultLocation:
    """The vault for a KB read (see the module docstring). ``cwd`` only sets ``in_vault``. Never raises."""
    here = _resolved(cwd) if cwd is not None else None
    home = Path(env.get("HOME") or Path.home())
    pointer = read_pointer(home=home)
    pointed = pointer.vault if pointer is not None and pointer.vault else None

    explicit = env.get("SCOUT_DATA_DIR")
    if explicit:
        v, why = _vault_at(explicit, "SCOUT_DATA_DIR")
        if v is None:
            return VaultLocation(None, "none", False, why)
        installed = _resolved(pointed) if pointed and Path(pointed).expanduser().is_absolute() else None
        note = f"SCOUT_DATA_DIR ({v}) overrides the installed vault ({installed})" if installed not in (None, v) else ""
        return VaultLocation(v, "env", _inside(here, v), note)

    if pointed:
        v, why = _vault_at(pointed, "the engine pointer's vault")
        if v is None:
            return VaultLocation(None, "none", False, why)
        return VaultLocation(v, "pointer", _inside(here, v))

    # A pointer file that read_pointer rejects (unreadable, malformed, unknown schema) counts as no pointer.
    bad = f"the engine pointer ({pointer_path(home)}) is unreadable or malformed" if pointer_path(home).exists() else ""
    default = _resolved(home / paths.DEFAULT_DATA_DIR_NAME)
    if default is not None and _is_vault(default):
        return VaultLocation(default, "default", _inside(here, default), f"{bad}; using {default}" if bad else "")

    none = "no Scout vault is configured on this machine"
    return VaultLocation(None, "none", False, f"{bad}; {none}" if bad else none)


def _executable(p: Path) -> bool:
    return p.is_absolute() and p.is_file() and os.access(p, os.X_OK)


def engine_scoutctl() -> Path | None:
    """The absolute ``scoutctl`` of the engine running now, for the skill to call: the console script beside
    this interpreter (``engine_pointer``'s rule), else the launcher of the checkout this package was imported
    from. None when neither is an executable file."""
    import scout
    from scout.scripts.install_schedule_plist import resolve_scoutctl_bin

    for candidate in (resolve_scoutctl_bin(), Path(scout.__file__).resolve().parent.parent / "bin" / "scoutctl"):
        if _executable(candidate):
            return candidate
    return None
