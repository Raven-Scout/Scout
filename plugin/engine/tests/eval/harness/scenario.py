"""Scenarios: one run configuration each (spec 2026-10-12 §3, §7).

``tests/eval/scenarios/<name>/`` holds ``scenario.yaml`` (mode, personas,
checks, expectations), ``connectors.yaml`` (the scripted connectors),
``fixtures/`` (their responses) and ``vault/`` (an overlay on the base
fixture vault).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

SCENARIOS_DIR = Path(__file__).resolve().parents[1] / "scenarios"
PERSONA_CONNECTORS = {"slack": "slack", "linear": "linear", "calendar": "calendar", "email": "email"}


@dataclass(frozen=True)
class Scenario:
    name: str
    path: Path
    mode: str
    weekdays_only: bool
    personas: tuple[str, ...]
    checks: tuple[str, ...]
    expected_placement: dict[str, set[str]]
    expected_closures: set[str]

    @property
    def script(self) -> Path:
        return self.path / "connectors.yaml"

    @property
    def connectors(self) -> frozenset[str]:
        """Enabled connectors: one per persona, plus github (always served by the fake gh)."""
        return frozenset({PERSONA_CONNECTORS[p] for p in self.personas} | {"github"})


def load_scenario(name: str) -> Scenario:
    path = SCENARIOS_DIR / name
    if not (path / "scenario.yaml").is_file():
        raise FileNotFoundError(f"no scenario {name!r} in {SCENARIOS_DIR}")
    raw = yaml.safe_load((path / "scenario.yaml").read_text(encoding="utf-8")) or {}
    return Scenario(
        name=name,
        path=path,
        mode=raw["mode"],
        weekdays_only=bool(raw.get("weekdays_only", False)),
        personas=tuple(raw.get("personas", [])),
        checks=tuple(raw.get("checks", [])),
        expected_placement={str(k): set(v) for k, v in (raw.get("expected_placement") or {}).items()},
        expected_closures=set(raw.get("expected_closures") or []),
    )
