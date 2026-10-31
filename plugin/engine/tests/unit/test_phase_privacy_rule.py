"""The privacy phase rule (#453, spec 2026-10-20 §6.3, §8.1).

``phases/core/privacy.md`` reaches every brain file, and no assembled phase
reads the KB raw: the vault's history comes through ``scoutctl kb delta``,
queries through ``scoutctl kb query``, never ``parser.py``, ``git diff
HEAD~1`` or a shell ``cat``/``rg``/``grep``/``ls``/``find`` into
``knowledge-base/``. Slack sends go through the outbound-draft check.

The ``scout-ask`` skill (plan Task 10, spec §6.3 row 1) is held to the same
rule, and its commands are run against the privacy fixture vault.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout.cli import app
from scout.scripts.bootstrap import BootstrapConfig, _assemble
from scout.scripts.phase_assembly import parse_phase_file
from tests.eval.harness.checks.privacy_canary import raw_kb_read
from tests.unit.privacy_vault_helpers import CANARIES, build_privacy_vault

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
        "scoutctl kb read <path>",  # the sanctioned read of a note the run isn't editing
        "copy its title exactly as written",  # owner-only surfaces: no paraphrase, no "(private)" tag
    ):
        assert needle in rule, needle


CONTEXT_NOTES_LIST = "scripts/scoutctl kb files --glob 'action-items/context/*.md'"


@pytest.mark.parametrize("kind", ["SKILL", "DREAMING"])
def test_runs_that_edit_context_notes_are_told_how_to_list_them(kind: str) -> None:
    """The 2026-10-21 eval: runs reached for `ls action-items/context/` with no sanctioned listing named."""
    assert CONTEXT_NOTES_LIST in _assembled(kind, _all_connectors())


def test_consolidation_keeps_the_personal_tasks_from_session_context() -> None:
    text = _assembled("SKILL", _all_connectors())
    (line,) = [ln for ln in text.splitlines() if "`personal_tasks`" in ln]
    assert ".scout-cache/session-context.json" in line and "daily file" in line


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


def test_connector_kb_updates_find_notes_through_scoutctl_kb() -> None:
    """#507's eval: runs that read the connector KB-update sections reached for raw Reads of people,
    topic and project notes. Each such section names the sanctioned lookups."""
    sections = [
        s
        for f in sorted((PLUGIN_ROOT / "phases" / "connectors").glob("*.md"))
        for s in parse_phase_file(f)
        if s.slot == "update"
    ]
    assert len(sections) >= 4
    for s in sections:
        for needle in ("scoutctl kb name-lookup", "kb resolve", "kb read <path>", "Read only to edit"):
            assert needle in s.body, (s.name, needle)


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


# --- the scout-ask skill (plan Task 10) ------------------------------------------------

SCOUT_ASK = PLUGIN_ROOT / "skills" / "scout-ask" / "SKILL.md"
KB_READ_COMMANDS = {"resolve", "files", "read", "search", "query", "related", "traverse", "path"}
# Vault folders the KB read gate doesn't judge (it covers knowledge-base/ and action-items/context/).
_SHELL_VAULT_READ = re.compile(r"\b(?:ls|rg|grep|find|cat|head|tail|tree)\s+[^|;&]*\b(?:action-items|meetings)/")
SAM = "knowledge-base/people/sam.md"
# The skill's placeholders, filled so its commands reach the fixture's private person note.
_PLACEHOLDERS = {"<name>": "Sammy", "<entity>": CANARIES[SAM], "<path>": SAM}
_AGENT = "agent:person:priya@example.com"


def _scout_ask() -> tuple[str, str]:
    """(frontmatter description, body)."""
    _, fm, body = SCOUT_ASK.read_text(encoding="utf-8").split("---\n", 2)
    (description,) = [ln.split(":", 1)[1].strip() for ln in fm.splitlines() if ln.startswith("description:")]
    return description, body


def _fenced_kb_calls(body: str) -> list[list[str]]:
    """Each fenced ``scripts/scoutctl kb …`` line, as ``kb`` arguments with the placeholders filled."""
    calls: list[list[str]] = []
    fenced = False
    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced and line.startswith("scripts/scoutctl kb "):
            words = shlex.split(line.split("#", 1)[0])[2:]
            calls.append([_PLACEHOLDERS.get(w, w) for w in words])
    return calls


def test_scout_ask_reads_the_vault_through_scoutctl_kb() -> None:
    _, body = _scout_ask()
    spans = _code_spans(body)
    assert [s for s in spans if raw_kb_read(s)] == []
    assert [s for s in spans if _SHELL_VAULT_READ.search(s)] == []
    assert not re.search(r"python3?\s+\S*parser\.py", body)
    # Prose that sends the reader to a KB path names the `kb` command for it on the same line.
    assert [ln for ln in body.splitlines() if _READ_KB_PATH.search(ln) and not re.search(r"\bkb [a-z]", ln)] == []
    used = set(re.findall(r"\bkb ([a-z-]+)", body))
    assert used <= KB_READ_COMMANDS, used - KB_READ_COMMANDS
    assert {"resolve", "files", "read", "search", "query"} <= used


def test_scout_ask_keeps_the_session_reader_and_its_labels() -> None:
    """`--reader "${SCOUT_READER:-owner}"` (the plan's wording) widens a SCOUT_MODE-only run, which
    fails, so the skill's commands pass no reader. The label and denial rules are spelt out."""
    _, body = _scout_ask()
    assert "${SCOUT_READER" not in body
    assert all("--reader" not in arg for call in _fenced_kb_calls(body) for arg in call)
    for needle in ("Pass no `--reader`", "`[private]`", "owner", "`denied` (exit 3)", "never read around it"):
        assert needle in body, needle


def test_scout_ask_stays_cheap() -> None:
    """#84: the description is in every session's context, the body in every scout-ask turn."""
    description, body = _scout_ask()
    assert len(description) <= 600
    assert len(body.split()) <= 1250


@pytest.fixture(scope="module")
def privacy_vault(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_privacy_vault(tmp_path_factory.mktemp("scout-ask"))


def _kb(vault: Path, args: list[str], env: dict[str, str]) -> tuple[int, str]:
    """``scoutctl kb …`` with ``env`` on top of the hermetic one (no SCOUT_* set: the owner)."""
    result = CliRunner().invoke(app, ["kb", *args, "--repo", str(vault)], env=env)
    return result.exit_code, result.output


@pytest.mark.parametrize(
    "env", [{}, {"SCOUT_MODE": "briefing"}, {"SCOUT_READER": "autonomous"}], ids=["owner", "run", "autonomous"]
)
def test_scout_ask_commands_label_a_private_note(privacy_vault: Path, env: dict[str, str]) -> None:
    """Each fenced command runs as written, and those that reach the private person note label it."""
    calls = _fenced_kb_calls(_scout_ask()[1])
    assert len(calls) >= 4
    labelled = 0
    for args in calls:
        code, out = _kb(privacy_vault, args, env)
        assert code in (0, 1), (args, out)  # 1 = no match (resolve, search); never a usage error
        if SAM in out:
            assert all(ln.startswith("[private] ") for ln in out.splitlines() if SAM in ln), (args, out)
            labelled += 1
    assert labelled >= 3  # resolve by alias, read, the entity-folder search


def test_scout_ask_commands_give_an_agent_reader_nothing(privacy_vault: Path) -> None:
    calls = _fenced_kb_calls(_scout_ask()[1])
    assert len(calls) >= 4
    for args in calls:
        code, out = _kb(privacy_vault, args, {"SCOUT_READER": _AGENT})
        assert CANARIES[SAM] not in out and SAM not in out, (args, out)
        if args[0] == "read":
            assert code == 3 and '"denied": true' in out
        else:
            assert code in (0, 1), (args, out)
