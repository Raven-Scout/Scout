"""Which programs Claude Code's shell snapshot swaps in for a run's commands, for ``scoutctl bootstrap doctor``.

Every Bash call of a scheduled run sources Claude Code's shell snapshot
(``~/.claude/shell-snapshots/snapshot-zsh-*.sh``). It defines shell functions that run programs embedded in the
Claude binary under another name: ``grep`` runs ugrep (``ARGV0=ugrep "$_cc_bin" <flags> "$@"``), ``find`` runs
bfs. The KB read gate judges a bare ``grep``/``egrep``/``fgrep`` and ``find`` both ways
(:func:`scout.kb.raw_read.run_models`), so it stays right whichever program runs. What it can't know is a name the
snapshot shadows that it doesn't model, or new fixed flags. This module reads the newest snapshot and says so.
It never changes what the gate decides.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from scout.kb.raw_read import SHADOWED, SNAPSHOT_BFS_FLAGS, SNAPSHOT_UGREP_FLAGS

# The flags the gate assumes each shadowing program is started with.
MODELLED_FLAGS = {"ugrep": SNAPSHOT_UGREP_FLAGS, "bfs": SNAPSHOT_BFS_FLAGS}

_FUNCTION = re.compile(r"^function ([A-Za-z0-9_.+-]+) \{\n(.*?)^\}", re.MULTILINE | re.DOTALL)
# `ARGV0=ugrep "$_cc_bin" -G … ${1+"$@"}` (zsh) or `exec -a bfs "$_cc_bin" -S dfs … ${1+"$@"}` (bash).
_LAUNCH = re.compile(r'(?:ARGV0=|exec -a )([A-Za-z0-9_.+-]+) "\$_cc_bin" (.*?)\$\{1\+"\$@"\}')


@dataclass(frozen=True)
class Shadow:
    """A command name the snapshot defines as a function that runs ``program`` with ``flags`` first."""

    name: str
    program: str
    flags: tuple[str, ...]


def newest_snapshot(home: Path) -> Path | None:
    """The newest zsh snapshot (the shell scheduled runs use on macOS), else the newest bash one."""
    folder = Path(os.environ.get("CLAUDE_CONFIG_DIR") or home / ".claude") / "shell-snapshots"
    for shell in ("zsh", "bash"):
        try:
            found = sorted(folder.glob(f"snapshot-{shell}-*.sh"), key=lambda p: p.stat().st_mtime)
        except OSError:
            continue
        if found:
            return found[-1]
    return None


def parse(text: str) -> list[Shadow]:
    """The functions in a snapshot that run another program, with the flags they put before the caller's."""
    shadows = []
    for m in _FUNCTION.finditer(text):
        launch = _LAUNCH.search(m.group(2))
        if launch is None:
            continue
        try:
            flags = tuple(shlex.split(launch.group(2)))
        except ValueError:
            flags = tuple(launch.group(2).split())
        shadows.append(Shadow(m.group(1), launch.group(1), flags))
    return shadows


def check(home: Path) -> tuple[list[str], list[str]]:
    """(warnings, notes) about the newest snapshot. No snapshot (Claude Code has not run a Bash call here, or
    another runtime runs the schedule) says nothing."""
    path = newest_snapshot(home)
    if path is None:
        return [], []
    try:
        shadows = parse(path.read_text(encoding="utf-8", errors="replace"))
    except OSError as e:
        return [f"could not read Claude Code's shell snapshot {path.name}: {e}"], []
    warnings, known = [], []
    for s in shadows:
        modelled = SHADOWED.get(s.name)
        if modelled is None:
            warnings.append(
                f"Claude Code's shell snapshot ({path.name}) runs `{s.name}` as its embedded {s.program}, which "
                f"the KB read gate does not model: a run's `{s.name}` may read files the gate does not see. "
                "Update Scout, or report it."
            )
        elif s.program != modelled:
            warnings.append(
                f"Claude Code's shell snapshot ({path.name}) runs `{s.name}` as {s.program}; the KB read gate "
                f"models it as {modelled}. Update Scout, or report it."
            )
        elif s.flags != MODELLED_FLAGS[modelled]:
            warnings.append(
                f"Claude Code's shell snapshot ({path.name}) starts `{s.name}` as {s.program} with flags the KB "
                f"read gate does not expect: {' '.join(s.flags) or '(none)'}. Update Scout, or report it."
            )
        else:
            known.append(f"`{s.name}` as {s.program}")
    notes = [f"Claude Code's shell snapshot runs {', '.join(known)}; the KB read gate judges both."] if known else []
    return warnings, notes
