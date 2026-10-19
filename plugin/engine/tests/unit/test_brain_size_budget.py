"""Brain-file size budget (spec 2026-10-13-static-instruction-trim §8.2).

Every scheduled run reads its brain file at turn 1 and re-reads it from cache on
every later turn, so its size is a per-turn cost. This pins each assembled brain
file, with every shipped connector enabled, in tokens (chars/4). Growth past
the headroom fails; so does a shrink past it, so a cut is recorded here too.
Either way: re-measure and update the pin in the same PR, saying why.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.scripts.bootstrap import BootstrapConfig, _assemble
from scout.scripts.phase_assembly import parse_phase_file

PLUGIN_ROOT = Path(__file__).parent.parent.parent.parent

# ~tokens (chars/4) at the last re-pin. Re-pinned: 2026-10-13 (first pin).
PINS = {"SKILL": 40_050, "DREAMING": 16_970, "RESEARCH": 4_755}
GROWTH = 0.05
SHRINK = 0.10


def _shipped_connectors() -> set[str]:
    return {
        s.requires
        for f in (PLUGIN_ROOT / "phases" / "connectors").glob("*.md")
        for s in parse_phase_file(f)
        if s.requires
    }


def _config() -> BootstrapConfig:
    return BootstrapConfig(
        vault=Path("/tmp/does-not-matter"),
        plugin_root=PLUGIN_ROOT,
        instance_name="TestScout",
        instance_name_lower="testscout",
        user_name="Test User",
        user_email="test@example.com",
        timezone="America/New_York",
        platform="macos",
        plugin_version="0.0.0",
        enabled_connectors=_shipped_connectors(),
        connector_inputs={},
        skip_jobs=True,
        skip_claude=True,
    )


def test_every_shipped_connector_is_enabled() -> None:
    assert len(_shipped_connectors()) >= 9


@pytest.mark.parametrize("kind", sorted(PINS))
def test_brain_file_stays_within_its_budget(kind: str) -> None:
    tokens = len(_assemble(_config(), kind, custom={})) // 4
    pin = PINS[kind]
    assert tokens <= pin * (1 + GROWTH), (
        f"{kind}.md is ~{tokens} tokens, over its {pin} pin by more than {GROWTH:.0%}. Every run "
        "re-reads it on every turn: trim it, or raise PINS here and say why in the PR."
    )
    assert tokens >= pin * (1 - SHRINK), (
        f"{kind}.md shrank to ~{tokens} tokens from its {pin} pin: lower PINS here to record the cut."
    )
