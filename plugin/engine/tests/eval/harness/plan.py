"""What a model-backed command will start, before it starts it (spec 2026-10-12 §9).

Usage comes out of the maintainer's own subscription, so the defaults are
small, the plan is printed first, and anything larger is an explicit opt-in.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .scenario import Scenario

MAX_WITHOUT_MANY = 10
DEFAULT_MODEL = "sonnet"


class PlanRefused(Exception):
    pass


@dataclass(frozen=True)
class Plan:
    scenarios: tuple[Scenario, ...]
    reps: int
    model: str

    @property
    def sessions(self) -> int:
        return len(self.scenarios) * self.reps

    def describe(self) -> str:
        parts = ", ".join(f"{s.name} ×{self.reps}" for s in self.scenarios)
        noun = "session" if self.sessions == 1 else "sessions"
        return f"Will start {self.sessions} {noun} ({parts}) on {self.model} via your Claude login."


def session_plan(scenarios: Sequence[Scenario], *, reps: int, opus: bool, many: bool) -> Plan:
    if reps < 1:
        raise PlanRefused("--reps must be at least 1")
    plan = Plan(tuple(scenarios), reps, "opus" if opus else DEFAULT_MODEL)
    if plan.sessions > MAX_WITHOUT_MANY and not many:
        raise PlanRefused(
            f"{plan.sessions} sessions is more than {MAX_WITHOUT_MANY}; pass --many to start them on your subscription"
        )
    return plan
