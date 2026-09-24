"""Helper for `scoutctl schedule install-plist [--uninstall] [--force]`.

Filling __USER_HOME__ and __SCOUTCTL_BIN__ in the template at install time;
not at runtime, because launchd's plist parser doesn't expand env vars in
<string> values.
"""

from __future__ import annotations

import os
import subprocess
import sys
from html import escape
from pathlib import Path

PLIST_NAME = "com.scout.schedule-tick.plist"
TEMPLATE = Path(__file__).parent.parent / "defaults" / PLIST_NAME


def resolve_scoutctl_bin() -> Path:
    """Return the scoutctl console script beside the running interpreter.

    The interpreter executing the engine *is* the venv the engine is installed
    into, so its ``bin/`` sibling ``scoutctl`` is by construction the one that
    matches the loaded plugin — in every layout: a venv inside the checkout
    (``<root>/.venv``), a venv outside it (the app-managed layout,
    ``~/.local/share/scout/venv/<v>``), or a marketplace clone.

    Do NOT ``resolve()`` the path: in a venv ``sys.executable`` is
    ``<venv>/bin/python``, a symlink to the base interpreter; resolving it
    would point at ``/opt/homebrew/…/bin/scoutctl``, which does not exist.
    """
    return Path(sys.executable).absolute().parent / "scoutctl"


def install_plist(
    *,
    home: Path,
    agents_dir: Path | None = None,
    force: bool = False,
    bootstrap: bool = False,
    vault: Path | None = None,
) -> Path:
    """Render the template into ~/Library/LaunchAgents/."""
    vault = vault or (home / "Scout")
    agents_dir = agents_dir or (home / "Library" / "LaunchAgents")
    agents_dir.mkdir(parents=True, exist_ok=True)
    target = agents_dir / PLIST_NAME
    if target.exists() and not force:
        raise FileExistsError(target)
    # XML-escape substituted values: they land inside <string> elements, and a
    # path with `&`, `<`, `>`, `"` (all legal on macOS, e.g. ~/R&D) would
    # otherwise produce malformed XML that launchd silently refuses to load,
    # stopping every scheduled run with no error. (#49)
    rendered = (
        TEMPLATE.read_text(encoding="utf-8")
        .replace("__USER_HOME__", escape(str(home), quote=True))
        .replace("__SCOUTCTL_BIN__", escape(str(resolve_scoutctl_bin()), quote=True))
        .replace("__SCOUT_DIR__", escape(str(vault), quote=True))
    )
    target.write_text(rendered, encoding="utf-8")
    if bootstrap:
        uid = os.getuid()
        # launchctl bootstrap EIOs (errno 5) when the label is already
        # loaded and has no --force; bootout first (best-effort, mirrors
        # uninstall_plist) so re-install replaces the loaded job instead of
        # erroring with a misleading "Bootstrap failed: 5" (#48, #23).
        subprocess.run(
            ["launchctl", "bootout", f"gui/{uid}/com.scout.schedule-tick"],
            check=False,
            capture_output=True,
        )
        subprocess.run(
            ["launchctl", "bootstrap", f"gui/{uid}", str(target)],
            check=False,
        )
    return target


def uninstall_plist(*, agents_dir: Path | None = None, bootout: bool = False) -> None:
    """Remove the plist (and optionally bootout the job from launchd)."""
    agents_dir = agents_dir or (Path.home() / "Library" / "LaunchAgents")
    target = agents_dir / PLIST_NAME
    if bootout:
        uid = os.getuid()
        subprocess.run(
            ["launchctl", "bootout", f"gui/{uid}/com.scout.schedule-tick"],
            check=False,
        )
    if target.exists():
        target.unlink()
