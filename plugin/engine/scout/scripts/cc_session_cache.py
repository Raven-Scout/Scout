"""Back-compat shim for ``scoutctl session cc-cache`` (Agent Sessions plan 1).

The original module walked ``~/.claude/projects/*/*.jsonl`` and rendered a flat
24-hour list. That work now lives in :mod:`scout.sessions` — the index builder
plus the state-first digest — and this module only keeps the historical entry
point (and the two extractor names) importable so nothing in an installed vault
breaks: ``scripts/cc-session-cache.sh`` still calls ``scoutctl session cc-cache``.
"""

from __future__ import annotations

import sys

from scout.sessions.transcript import extract_files_touched, extract_first_message

DEFAULT_HOURS_LOOKBACK = 24
OUTPUT_FILENAME = "cc-sessions.md"


def main(
    *,
    hours: int = DEFAULT_HOURS_LOOKBACK,
    instance_name: str = "Scout",
    tz_name: str | None = None,
) -> int:
    """CLI entry — never raises. Prints the digest path so runner logs show where it landed."""
    from scout import paths
    from scout.sessions.index import run

    try:
        index, _ = run(use_gh=True, render=True, hours=hours, instance_name=instance_name, tz_name=tz_name)
    except Exception as exc:  # noqa: BLE001 — the pre-session phase must never break on this
        print(f"cc-session-cache: {exc}", file=sys.stderr)
        return 0
    output_path = paths.cache_dir() / OUTPUT_FILENAME
    active = sum(1 for s in index.sessions if not s.is_scout_run and not s.is_archived)
    print(f"CC session cache written to {output_path} ({active} sessions, {hours}h lookback)")
    return 0


__all__ = ["DEFAULT_HOURS_LOOKBACK", "OUTPUT_FILENAME", "extract_files_touched", "extract_first_message", "main"]
