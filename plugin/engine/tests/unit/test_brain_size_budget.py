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

# ~tokens (chars/4) at the last re-pin. Re-pinned: 2026-10-13 (first pin); 2026-10-20: +392 in every
# mode for git-setup's "Engine Changes: Never in the Live Engine Checkout" (a dreaming run edited the
# live engine; the rule must reach any scheduled run, research included). 2026-10-21: +1,254 SKILL,
# +1,194 DREAMING, +1,113 RESEARCH for #453: the always-included privacy rule (phases/core/privacy.md,
# ~790) plus reading the KB through `scoutctl kb` and the Slack outbound-draft step in the phases.
# 2026-10-21 (#453 eval follow-up): +189 SKILL, +109 DREAMING, +70 RESEARCH for listing/reading context
# notes via `scoutctl kb files|read`, exact private titles on owner-only surfaces, and consolidation
# keeping `personal_tasks`; the pins also absorb drift already on main (+762 SKILL, +239 RESEARCH).
# 2026-11-05 (Agnes Shared Areas S2a): +103 in every mode for the shared-notes rule in phases/core/privacy.md,
# which must reach every run that could read one (the hook carries the rest at read time). The pins also absorb
# drift already on main, which had left DREAMING 6 and RESEARCH 12 tokens under the limit (+1,862 SKILL, +930
# DREAMING, +316 RESEARCH).
# 2026-11-16 (Phase 1d run brokers, #648): RESEARCH re-pinned to its measure. git-setup's "Changing the vault's git"
# (a sandboxed run can't change the repository: the helper, mv/rm, git show, untracking) and Step 0's sandbox guard,
# +~150 in every mode, trimmed to the replacements runs used in the 50-run replay. RESEARCH also absorbs +237 of drift
# already on main, which had left it 112 tokens under the limit. SKILL and DREAMING stay inside their headroom.
# 2026-11-19 (wishlist check-log-move, C12 follow-up): DREAMING re-pinned to its measure. The wishlist phase's commit
# step runs `scoutctl wishlist check-log-move` (+~205: the command, exit 1's fix, its 3-try bound and restore,
# exit 2's note). DREAMING also absorbs ~980 of drift already on main, which had left it about 5 tokens under the
# limit.
# 2026-11-19 (changes map, plan 2 task 8): DREAMING re-pinned to its measure. Feedback processing records each DM
# reply, reaction and mistake recurrence (`scoutctl engagement record`), routes governance changes to a
# `governance-pending/` file in place of proposals, and its Step 1e now carries out the owner's rollback and governance
# decisions from `scoutctl changes due` (+~1,800); git-setup's change trailers (+~80) reach every mode. SKILL and
# RESEARCH stay inside their headroom (RESEARCH within ~60 tokens of it).
# 2026-11-19 (changes map, plan 2 final review): all three re-pinned to their measure. git-setup's change trailers
# drop the governance trailer and say how to write an apostrophe inside `$'…'` and that one engagement change per run
# is the limit (+~36 in every mode); Step 1e words a suspect `undo` would refuse, reports a blocked undo once, and says
# the move into `applied/` is what makes a governance change (+~315 DREAMING). The pins also absorb drift already on
# the branch (+1,341 SKILL, +182 DREAMING, +367 RESEARCH, which had left RESEARCH 2 tokens under the limit).
# 2026-11-21 (immediate response, spec §5): SKILL re-pinned to its measure. connectors/telegram.md's "Messages
# waiting from <owner>" (+~386): briefings and consolidations answer the Telegram messages no respond run did,
# and SKILL.md doesn't carry RESPOND.md, so the section restates the data-not-instructions rule, the guardrails,
# the `tg:<ids>` commit subject and the one reply. SKILL also absorbs drift already present, which had left it
# about 26 tokens under the limit.
# 2026-11-22 (immediate response review): RESPOND first pinned, at its measure. The waiting-messages drain moved
# out of the Telegram wrap section into its own `update` slot, so a Slack-only wrap still answers (+~214 SKILL,
# inside its headroom: the section now states its own HTML rule instead of pointing at the wrap's).
# 2026-11-22 (changes map plan 2, merge with main): all four re-pinned to their measure on the merged tree. On top of
# main's pins this carries plan 2's phase text: feedback processing's signal recording, Step 1e and the restored
# opt-out proposals (DREAMING +~3,600), and, in every mode RESPOND included (main added it to git-setup), git-setup's
# change trailers and privacy's dm-sent record (RESPOND +~450).
PINS = {"SKILL": 48_142, "DREAMING": 24_479, "RESEARCH": 8_061, "RESPOND": 6_490}
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
