"""Scenarios: one run configuration each (spec 2026-10-12 §3, §7).

``tests/eval/scenarios/<name>/`` holds ``scenario.yaml`` (mode, personas,
checks, expectations), ``connectors.yaml`` (the scripted connectors),
``fixtures/`` (their responses), ``vault/`` (an overlay on the base
fixture vault) and ``brain/`` (text appended to the built brain files, the
way a vault's own edits to them survive upgrades). ``fixtures/linear-graphql.json``,
when present, is the Linear API's answer to the runner's own-activity pre-fetch. A variant that only changes
the mode or the expectations names another scenario in ``files_from:`` and
uses that one's ``connectors.yaml``, ``fixtures/``, ``vault/`` and ``brain/``.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from scout.kb.structure import Marker

from .checks.kb_writing import KB_CHECKS, KbFact

SCENARIOS_DIR = Path(__file__).resolve().parents[1] / "scenarios"
KNOWN_CHECKS = {
    "placement", "no_drop", "context_note", "waiting_format", "no_collapse", "body_budget",
    "outage_reported", "no_false_outage", "dropoff_caught", "demotion", "privacy_canary", "privacy_hygiene",
    "linear_delta", "red_poll", *KB_CHECKS,
}  # fmt: skip
DEFAULT_FRONT_PAGE_GROWTH = 512  # bytes; kb-front-page's scenario.yaml says why it can be small
# A recorded Linear API response in a scenario's fixtures/: the runner's pre-fetch reads it
# (scout.linear_activity) in place of calling Linear. Without one, the pre-fetch finds no key.
LINEAR_API_FIXTURE = "fixtures/linear-graphql.json"
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
    budget: str | None = None  # the session's --max-budget-usd; None means the runner's DEFAULT_BUDGET
    expected_linear_activity: tuple[str, ...] = ()  # linear_delta: issue ids the run must report
    expected_red_channels: tuple[str, ...] = ()  # red_poll: channel ids the run must read
    kb_project: str = ""  # the KB checks: the project folder whose pages they score
    max_front_page_growth: int = DEFAULT_FRONT_PAGE_GROWTH  # front_page_growth: bytes the front page may grow
    expected_kb_facts: tuple[KbFact, ...] = ()  # kb_facts_cited: facts that must land, cited, in knowledge/
    front_page_narration: tuple[Marker, ...] = ()  # front_page_no_narration: markers on each entry a run adds

    @property
    def files(self) -> Path:
        """Where the scripted connectors and the vault/brain overlays live."""
        return self.files_from or self.path

    @property
    def linear_api(self) -> Path | None:
        """The recorded Linear API response the runner's pre-fetch reads, if the scenario has one."""
        path = self.files / LINEAR_API_FIXTURE
        return path if path.is_file() else None

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


def _budget(name: str, raw: object) -> str | None:
    """``budget:`` is a positive USD amount (``4``, ``"2.5"``); absent means the runner's default."""
    if raw is None:
        return None
    text = str(raw).strip()
    try:
        value = float(text) if isinstance(raw, int | float | str) and not isinstance(raw, bool) else math.nan
    except ValueError:
        value = math.nan
    if not (value > 0 and math.isfinite(value)):
        raise ValueError(f"scenario {name!r}: budget must be a positive USD amount, got {raw!r}")
    return text


def _growth(name: str, raw: object) -> int:
    """``max_front_page_growth:`` is a whole number of bytes, 0 or more."""
    if raw is None:
        return DEFAULT_FRONT_PAGE_GROWTH
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise ValueError(f"scenario {name!r}: max_front_page_growth must be a whole number of bytes, got {raw!r}")
    return raw


def _kb_facts(name: str, raw: object) -> tuple[KbFact, ...]:
    """``expected_kb_facts:``: ``[{id, all: [regex, …], none: [regex, …]}]``; ``none`` (optional) are
    negations that keep a block from counting. A bad pattern fails before a session is spent."""
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError(f"scenario {name!r}: expected_kb_facts must be a list")
    facts = []
    for entry in raw:
        if not isinstance(entry, dict) or not entry.get("id") or not isinstance(entry.get("all"), list):
            raise ValueError(f"scenario {name!r}: each expected_kb_facts entry needs an id and an `all` list")
        if not isinstance(entry.get("none") or [], list):
            raise ValueError(f"scenario {name!r}: expected_kb_facts {entry['id']!r}: `none` must be a list")
        try:
            patterns = tuple(re.compile(str(p)) for p in entry["all"])
            negations = tuple(re.compile(str(p)) for p in entry.get("none") or [])
        except re.error as exc:
            raise ValueError(f"scenario {name!r}: expected_kb_facts {entry['id']!r}: bad pattern ({exc})") from exc
        if not patterns:
            raise ValueError(f"scenario {name!r}: expected_kb_facts {entry['id']!r} has no patterns")
        facts.append(KbFact(str(entry["id"]), patterns, negations))
    return tuple(facts)


def _entry_markers(name: str, raw: object) -> tuple[Marker, ...]:
    """``front_page_narration:``: ``[{label, pattern}]``, matched against each entry a run adds to
    the front page. The scorer's own: ``kb_structure.narration_markers`` (and so ``kb structure``)
    stay as the vault has them."""
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError(f"scenario {name!r}: front_page_narration must be a list")
    out = []
    for entry in raw:
        if not isinstance(entry, dict) or not entry.get("pattern"):
            raise ValueError(f"scenario {name!r}: each front_page_narration entry needs a pattern (and a label)")
        label = str(entry.get("label") or entry["pattern"])
        try:
            out.append(Marker(label, re.compile(str(entry["pattern"]))))
        except re.error as exc:
            raise ValueError(f"scenario {name!r}: front_page_narration {label!r}: bad pattern ({exc})") from exc
    return tuple(out)


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
    kb_project = str(raw.get("kb_project") or "")
    if not kb_project and set(KB_CHECKS) & set(raw.get("checks", [])):
        raise ValueError(f"scenario {name!r}: the KB checks need kb_project")
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
        budget=_budget(name, raw.get("budget")),
        expected_linear_activity=tuple(str(i) for i in raw.get("expected_linear_activity") or ()),
        expected_red_channels=tuple(str(c) for c in raw.get("expected_red_channels") or ()),
        kb_project=kb_project,
        max_front_page_growth=_growth(name, raw.get("max_front_page_growth")),
        expected_kb_facts=_kb_facts(name, raw.get("expected_kb_facts")),
        front_page_narration=_entry_markers(name, raw.get("front_page_narration")),
    )
