"""The privacy phase rule (#453, spec 2026-10-20 §6.3, §8.1).

``phases/core/privacy.md`` reaches every brain file, and no assembled phase
reads the KB raw: the vault's history comes through ``scoutctl kb delta``,
queries through ``scoutctl kb query``, never ``parser.py``, ``git diff
HEAD~1`` or a shell ``cat``/``rg``/``grep``/``ls``/``find`` into
``knowledge-base/``. Slack sends go through the outbound-draft check.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scout.scripts.bootstrap import BootstrapConfig, _assemble
from scout.scripts.phase_assembly import parse_phase_file
from tests.eval.harness.checks.privacy_canary import raw_kb_read

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
KINDS = ("SKILL", "DREAMING", "RESEARCH")
PRIVACY_HEADING = "## Privacy: owner-only and shared surfaces"
_WALK_KB = re.compile(
    r"\b(?:read|open|walk|scan|go through|cat|list)\s+(?:every|each|all)\b"
    r"[^.]{0,40}?`?(?:knowledge-base|action-items/context)",
    re.IGNORECASE,
)
# "read `knowledge-base/…`": an instruction to read a KB path that doesn't say through which command.
_READ_KB_PATH = re.compile(
    r"\b(?:read|reads|reading|consult|look at|look in|check|inspect|load)\b[^.`]{0,60}`[^`\s]*"
    r"(?:knowledge-base|action-items/context)/",
    re.IGNORECASE,
)


def _all_connectors() -> set[str]:
    phases = (PLUGIN_ROOT / "phases" / "connectors").glob("*.md")
    return {s.requires for f in phases for s in parse_phase_file(f) if s.requires}


def _assembled(kind: str, connectors: set[str]) -> str:
    cfg = BootstrapConfig(
        vault=Path("/tmp/does-not-matter"),
        plugin_root=PLUGIN_ROOT,
        instance_name="TestScout",
        instance_name_lower="testscout",
        user_name="Alex",
        user_email="alex@example.com",
        timezone="UTC",
        platform="macos",
        plugin_version="0.0.0",
        enabled_connectors=connectors,
        connector_inputs={},
        skip_jobs=True,
        skip_claude=True,
    )
    return _assemble(cfg, kind, custom={})


def _code_spans(text: str) -> list[str]:
    """Fenced code lines and inline backtick spans: where a phase tells the run to run something."""
    spans: list[str] = []
    fenced = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            spans.append(line)
        else:
            spans += re.findall(r"`([^`\n]+)`", line)
    return spans


@pytest.mark.parametrize("connectors", [set(), None], ids=["no-connectors", "all-connectors"])
@pytest.mark.parametrize("kind", KINDS)
def test_every_brain_file_carries_the_privacy_rule(kind: str, connectors: set[str] | None) -> None:
    text = _assembled(kind, _all_connectors() if connectors is None else connectors)
    assert text.count(PRIVACY_HEADING) == 1
    rule = text.split(PRIVACY_HEADING, 1)[1]
    for needle in (
        "scoutctl kb check-outbound .scout-cache/outbound-draft.md --surface slack",
        "scoutctl kb check-query",
        "--no-verify",
        "visibility: private",
        "denied",
        "never drops the wrap",
    ):
        assert needle in rule, needle


def test_the_privacy_phase_applies_to_every_mode() -> None:
    (section,) = parse_phase_file(PLUGIN_ROOT / "phases" / "core" / "privacy.md")
    assert section.mode == [] and section.requires is None


@pytest.mark.parametrize("kind", KINDS)
def test_no_assembled_phase_reads_the_kb_raw(kind: str) -> None:
    text = _assembled(kind, _all_connectors())
    assert "parser.py" not in text
    assert "git diff HEAD~1" not in text
    raw = [span for span in _code_spans(text) if raw_kb_read(span)]
    assert raw == []
    # Prose that sends the run through the KB file by file must name the scoutctl kb command to use.
    prose = [
        line
        for line in text.splitlines()
        if (_WALK_KB.search(line) or _READ_KB_PATH.search(line)) and "scoutctl kb" not in line
    ]
    assert prose == []
    # claude-sessions reads the git history of *other* repos (`git -C <repo> log`): not the vault's.
    vault_only = _assembled(kind, _all_connectors() - {"claude_sessions"})
    assert not re.search(r"\bgit (?:-C \S+ )?log\b", vault_only), "the vault's history comes through scoutctl kb delta"


def test_the_raw_read_detector() -> None:
    assert raw_kb_read("cat knowledge-base/people/sam.md")
    assert raw_kb_read("cd /v && rg -n permit knowledge-base/personal")
    assert raw_kb_read("ls knowledge-base/projects/")
    assert raw_kb_read("find /v/knowledge-base -name '*.md'")
    assert raw_kb_read("grep -rn x action-items/context/")
    assert not raw_kb_read("scoutctl kb search permit --glob 'knowledge-base/**'")
    assert not raw_kb_read("scoutctl kb search x --glob 'knowledge-base/**' | grep -c .")
    assert not raw_kb_read("git add knowledge-base/people/sam.md")
    assert not raw_kb_read("cat")
    assert not raw_kb_read("scoutctl kb search head --glob knowledge-base/**")
    assert not raw_kb_read("scripts/scoutctl kb search head --glob 'knowledge-base/**'")
    assert not raw_kb_read('git commit -m "dreaming [21:00]: tidy more knowledge-base notes"')
    assert not raw_kb_read("cd /v && git add -A && git commit -m 'cat knowledge-base'")
    assert raw_kb_read("cd knowledge-base && cat people/sam.md")
    assert raw_kb_read("cd knowledge-base/personal && ls")
    assert raw_kb_read("/bin/cat knowledge-base/x.md")
    assert raw_kb_read("git show HEAD:knowledge-base/x.md")
    assert raw_kb_read("git log -p -- knowledge-base")
    assert raw_kb_read("find . -name '*.md' | xargs -0 wc -l knowledge-base/people")
    assert raw_kb_read("tree knowledge-base")
    assert not raw_kb_read("git log --oneline -- knowledge-base")  # names only: not content


def test_the_prose_read_detector() -> None:
    assert _READ_KB_PATH.search("Before building the list, read `knowledge-base/scout-mistake-audit.md`.")
    assert _READ_KB_PATH.search("read that person's entity file (`knowledge-base/people/<slug>.md`)")
    assert _READ_KB_PATH.search("Check the newest shard(s) in `knowledge-base/session-log/` for the last row")
    assert _READ_KB_PATH.search("read `/v/action-items/context/PRMT.md`")
    assert not _READ_KB_PATH.search("Open questions, people | **Project page** `knowledge-base/projects/x.md`")
    assert not _READ_KB_PATH.search("Create a per-file item `knowledge-base/research-queue/<slug>.md`")


def test_the_prose_walk_detector() -> None:
    assert _WALK_KB.search("Read every file in `knowledge-base/`. Score each file")
    assert _WALK_KB.search("Open each note under knowledge-base/people")
    assert not _WALK_KB.search("Score every file in the cache")


@pytest.mark.parametrize(
    ("kind", "heading"), [("SKILL", "## Slack Notification"), ("RESEARCH", "## Step 4d: Slack Notification")]
)
def test_slack_wrap_sections_use_the_outbound_draft_step(kind: str, heading: str) -> None:
    text = _assembled(kind, _all_connectors())
    section = text.split(heading, 1)[1].split("\n## ", 1)[0]
    assert ".scout-cache/outbound-draft.md" in section
    assert "check-outbound" in section
