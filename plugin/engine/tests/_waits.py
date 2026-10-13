"""Poll-until-true for tests that wait on another process instead of sleeping a fixed time."""

from __future__ import annotations

import time
from collections.abc import Callable


def wait_until(predicate: Callable[[], object], *, timeout: float, interval: float = 0.01, message: str) -> None:
    """Return as soon as ``predicate()`` is truthy; raise ``AssertionError(message)`` once ``timeout`` s pass.

    The predicate is checked once more at the deadline, so a condition that turns true
    during the last sleep still passes.
    """
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError(message)
        time.sleep(interval)
