"""Shared reading for the pre-commit checks on what the runner pre-fetched.

``scoutctl pre-session data`` writes ``.scout-cache/session-context.json``
before each scheduled session. ``check-linear-delta`` and ``check-red-polls``
compare part of it with today's daily file; both need the file to be today's
and the daily file to exist, and both read sections of the daily file.
"""

from __future__ import annotations

import datetime as _dt
import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any


class PrefetchCheckError(Exception):
    """The check cannot run: the pre-fetched context or today's file is missing or unusable."""


def load_context(context: Path, *, today: str) -> dict[str, Any]:
    """The pre-fetched context, which must have been written ``today`` (YYYY-MM-DD)."""
    try:
        data = json.loads(context.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise PrefetchCheckError(f"{context} not found; run `scoutctl pre-session data` first") from e
    except (OSError, json.JSONDecodeError) as e:
        raise PrefetchCheckError(f"{context} is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise PrefetchCheckError(f"{context} is not a JSON object")
    generated = str(data.get("generated_at") or "")
    if not generated:
        raise PrefetchCheckError(f"{context} has no generated_at")
    if generated[:10] != today:
        raise PrefetchCheckError(
            f"{context} is from {generated[:10]}, not {today}; run `scoutctl pre-session data` first"
        )
    return data


def context_path(data_dir: Path | None, context: Path | None) -> Path:
    from scout import paths
    from scout.scripts.pre_session_data import OUTPUT_FILENAME

    return context or paths.cache_dir(data_dir) / OUTPUT_FILENAME


def daily_lines(data_dir: Path | None, today: str) -> list[str]:
    """Today's daily file, unfenced. Raises when it doesn't exist yet."""
    from scout import paths

    daily = paths.action_items_daily_path(data_dir, date=_dt.date.fromisoformat(today))
    if not daily.is_file():
        raise PrefetchCheckError(f"no action-items file for {today} ({daily}); write it first")
    return unfenced(daily.read_text(encoding="utf-8").splitlines())


def unfenced(lines: Iterable[str]) -> list[str]:
    out, fenced = [], False
    for line in lines:
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        elif not fenced:
            out.append(line)
    return out


def section(lines: Sequence[str], heading: str, *, level: int) -> list[str] | None:
    """The lines under the first heading of ``level`` that starts with ``heading``, up to the next
    heading of that level or higher. None when there is no such heading."""
    marks = "#" * level + " "
    for i, line in enumerate(lines):
        if line.startswith(marks) and line[len(marks) :].strip().startswith(heading):
            out = []
            for later in lines[i + 1 :]:
                hashes = len(later) - len(later.lstrip("#"))
                if 0 < hashes <= level and later[hashes : hashes + 1] == " ":
                    break
                out.append(later)
            return out
    return None
