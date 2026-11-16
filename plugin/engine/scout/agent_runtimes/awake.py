"""A clock that stops while the host sleeps, for run timeouts and run lengths (spec 2026-11-13 §8.6).

Jordan's decision (2026-11-16, option A): a run's timeout counts *awake* time, so a Mac
that sleeps through the night mid-run isn't a runaway. The clock is the one
``scripts/scout-lock.sh``'s ``_scout_awake_now`` uses: CLOCK_UPTIME_RAW on macOS,
CLOCK_MONOTONIC on Linux (both exclude suspend), and the same ``"<clock> <seconds>"``
text, so a value the runner took in shell compares with one taken here. A ``wall``
value (no python3 where the runner ran) is never subtracted from an ``awake`` one.
"""

from __future__ import annotations

import time
from collections.abc import Callable

AWAKE = "awake"
WALL = "wall"


def now() -> tuple[str, int]:
    """``("awake", seconds)`` on the sleep-excluding clock."""
    clock = getattr(time, "CLOCK_UPTIME_RAW", time.CLOCK_MONOTONIC)
    return AWAKE, int(time.clock_gettime(clock))


def parse(text: str | None) -> tuple[str, int] | None:
    """``"awake 1234"`` / ``"wall 1700000000"`` → (clock, seconds); anything else → None."""
    if not text:
        return None
    parts = text.split()
    if len(parts) != 2 or parts[0] not in (AWAKE, WALL) or not parts[1].isdigit():
        return None
    return parts[0], int(parts[1])


def elapsed(
    start: str | None,
    *,
    now_fn: Callable[[], tuple[str, int]] = now,
    wall_fn: Callable[[], float] = time.time,
) -> int | None:
    """Seconds since *start* on the clock it was taken on; None when *start* is unusable."""
    parsed = parse(start)
    if parsed is None:
        return None
    clock, t0 = parsed
    t1 = now_fn()[1] if clock == AWAKE else int(wall_fn())
    return max(0, t1 - t0)


__all__ = ["AWAKE", "WALL", "elapsed", "now", "parse"]
