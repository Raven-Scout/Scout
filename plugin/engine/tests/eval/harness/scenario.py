"""Scenarios: one run configuration each (spec 2026-10-12 §3, §7).

``tests/eval/scenarios/<name>/`` holds ``scenario.yaml`` (mode, personas,
checks, expectations), ``connectors.yaml`` (the scripted connectors),
``fixtures/`` (their responses), ``vault/`` (an overlay on the base
fixture vault) and ``brain/`` (text appended to the built brain files, the
way a vault's own edits to them survive upgrades). A variant that only changes
the mode or the expectations names another scenario in ``files_from:`` and
uses that one's ``connectors.yaml``, ``fixtures/``, ``vault/`` and ``brain/``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

SCENARIOS_DIR = Path(__file__).resolve().parents[1] / "scenarios"
KNOWN_CHECKS = {
    "placement", "no_drop", "context_note", "waiting_format", "no_collapse", "body_budget",
    "outage_reported", "no_false_outage", "dropoff_caught", "demotion", "privacy_canary", "privacy_hygiene",
}  # fmt: skip
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
    expected_waiting: tuple[str, ...] = ()
    personas_down: tuple[str, ...] = ()
    dropped_tags: tuple[str, ...] = ()
    expected_demotion: dict[str, str] = field(default_factory=dict)
    permit_canary: str = ""  # privacy_canary: the private task's token that must reach owner-only surfaces
    files_from: Path | None = None  # another scenario's dir holding connectors.yaml, fixtures/, vault/, brain/

    @property
    def files(self) -> Path:
        """Where the scripted connectors and the vault/brain overlays live."""
        return self.files_from or self.path

    @property
    def runner(self) -> str:
        """The vault runner this mode's scheduled run uses."""
        if self.mode.startswith("dreaming"):
            return "run-dreaming.sh"
        if self.mode.startswith("research"):
            return "run-research.sh"
        return "run-scout.sh"

    @property
    def script(self) -> Path:
        return self.files / "connectors.yaml"

    @property
    def connectors(self) -> frozenset[str]:
        """Enabled connectors: one per persona, plus github (always served by the fake gh)."""
        return frozenset({PERSONA_CONNECTORS[p] for p in self.personas} | {"github"})


def load_scenario(name: str) -> Scenario:
    path = SCENARIOS_DIR / name
    if not (path / "scenario.yaml").is_file():
        raise FileNotFoundError(f"no scenario {name!r} in {SCENARIOS_DIR}")
    raw = yaml.safe_load((path / "scenario.yaml").read_text(encoding="utf-8")) or {}
    unknown = [c for c in raw.get("checks", []) if c not in KNOWN_CHECKS]
    if unknown:  # fail before a session is spent, not at scoring time
        raise ValueError(f"scenario {name!r}: unknown check(s) {', '.join(unknown)}")
    files_from = None
    if raw.get("files_from"):
        files_from = SCENARIOS_DIR / str(raw["files_from"])
        if not (files_from / "connectors.yaml").is_file():
            raise ValueError(f"scenario {name!r}: files_from {raw['files_from']!r} has no connectors.yaml")
    return Scenario(
        name=name,
        path=path,
        mode=raw["mode"],
        weekdays_only=bool(raw.get("weekdays_only", False)),
        personas=tuple(raw.get("personas", [])),
        checks=tuple(raw.get("checks", [])),
        expected_placement={str(k): set(v) for k, v in (raw.get("expected_placement") or {}).items()},
        expected_closures=set(raw.get("expected_closures") or []),
        expected_waiting=tuple(raw.get("expected_waiting") or []),
        personas_down=tuple(raw.get("personas_down") or []),
        dropped_tags=tuple(raw.get("dropped_tags") or []),
        expected_demotion={str(k): str(v) for k, v in (raw.get("expected_demotion") or {}).items()},
        permit_canary=str(raw.get("permit_canary") or ""),
        files_from=files_from,
    )
