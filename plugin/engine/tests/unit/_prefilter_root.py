"""Stand-in plugin roots for tests of the hooks.json commands.

A hook entry starts ``<root>/hooks/prefilter.sh`` and, when the filter says so, ``<root>/engine/bin/scoutctl``.
A test that wants to see what the shell stage decided points CLAUDE_PLUGIN_ROOT at a root where Python
cannot start; that root still needs the real pre-filter, or the entry's "script is missing" fallback
answers instead.
"""

from __future__ import annotations

from pathlib import Path

PREFILTER = Path(__file__).resolve().parents[3] / "hooks" / "prefilter.sh"


def with_prefilter(root: Path) -> Path:
    """``root`` with the real hooks/prefilter.sh linked in (created if needed). The real plugin is left alone."""
    target = root / "hooks" / "prefilter.sh"
    if target.resolve() != PREFILTER.resolve() and not target.is_symlink():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(PREFILTER)
    return root
