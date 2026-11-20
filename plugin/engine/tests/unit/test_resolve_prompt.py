"""``scout.resolve_prompt``: the investigation prompts behind the app's "Resolve with Claude" button.

Every vault here is synthetic. The canaries planted in the run log and the agent settings must
never reach a prompt: prompts carry paths, ids and SHAs, never file bodies.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scout import health, resolve_prompt
from scout.health import Finding, RunHealth

NOW = datetime(2026, 11, 19, 20, 0, tzinfo=UTC)
LOG = "scout-2026-11-19_17-03.log"
BRANCH = "refs/heads/claude/example-branch-1a2b3c"
RESTORING = "7452d70f0000"
CANARY_LOG = "CANARY-LOG-7731"
CANARY_SETTINGS = "CANARY-SETTINGS-7731"
SCHEDULE = """\
schema_version: 1
slots:
  morning-briefing:
    type: briefing
    runner: run-scout.sh
    fires_at_local: "17:00"
    weekdays: [Mon, Tue, Wed, Thu, Fri, Sat, Sun]
    missed_window_hours: 4
    on_miss: fire
    cooldown_minutes: 0
"""


def _row(started: datetime, minutes: int, **fields) -> dict:
    finished = started + timedelta(minutes=minutes)
    row = {
        "slot": "morning-briefing",
        "mode": "scout",
        "runner": "scout",
        "started_at": int(started.timestamp()),
        "finished_at": int(finished.timestamp()),
        "duration_s": minutes * 60,
        "exit_code": 0,
        "failure_class": "ok",
        "outcome": "ok",
        "log": f"scout-{started.strftime('%Y-%m-%d_%H-%M')}.log",
        "commits_in_window": 1,
        "newest_commit": "a1b2c3d",
        "sandbox": "enforced",
        "integrity": "clean",
        "trigger": "schedule",
    }
    row.update(fields)
    return row


def build_vault(root: Path, rows: list[dict] | None = None) -> Path:
    """A vault whose newest run's integrity check restored a protected file on a branch."""
    v = root / "Scout"
    state, logs = v / ".scout-state", v / ".scout-logs"
    state.mkdir(parents=True)
    logs.mkdir()
    (v / ".claude").mkdir()
    (state / "schedule.yaml").write_text(SCHEDULE, encoding="utf-8")
    (v / "scout-config.yaml").write_text("user:\n  timezone: UTC\n", encoding="utf-8")
    (v / ".claude" / "settings.local.json").write_text(json.dumps({"note": CANARY_SETTINGS}), encoding="utf-8")
    if rows is None:
        rows = [_row(datetime(2026, 11, 19, 17, 3, tzinfo=UTC), 6, integrity="restored")]
    for r in rows:
        (logs / r["log"]).write_text("=== Scout run starting ===\n", encoding="utf-8")
    (logs / LOG).write_text(
        "=== Scout run starting ===\n"
        f"{CANARY_LOG}\n"
        f"integrity check: {BRANCH} changed protected file(s): .claude/settings.local.json\n"
        f"integrity check: restored them on {BRANCH} in commit {RESTORING}\n",
        encoding="utf-8",
    )
    (logs / "run-outcomes.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    fires = {"morning-briefing": NOW.isoformat().replace("+00:00", "Z")}
    (state / "last-fire.json").write_text(json.dumps({"schema_version": 1, "last_fire": fires}), encoding="utf-8")
    tick = {"id": "x", "ts": "2026-11-19T19:55:00Z", "kind": "schedule.tick.completed", "source": "t", "payload": {}}
    (logs / "schedule-events-2026-11-19.jsonl").write_text(json.dumps(tick) + "\n", encoding="utf-8")
    return v


@pytest.fixture
def quiet_health(monkeypatch):
    """No git, no launchd, no real home: health reads only the synthetic vault."""
    monkeypatch.setattr(health, "vault_birth", lambda _v: NOW - timedelta(days=60))
    monkeypatch.setattr(health, "tick_job_installed", lambda home=None: True)
    monkeypatch.setattr(health, "recent_commits", lambda *a, **k: [])


def _with_findings(monkeypatch, *findings: Finding) -> None:
    report = RunHealth(severity="red", findings=tuple(findings), evaluated_at=NOW, last_ok_at=None)
    monkeypatch.setattr(health, "evaluate", lambda vault, *, now=None, home=None: report)


def _finding(fid: str, **kw) -> Finding:
    return Finding(
        id=fid,
        severity=kw.pop("severity", "red"),
        since=kw.pop("since", NOW - timedelta(hours=3)),
        summary=kw.pop("summary", f"Summary for {fid}."),
        fix=kw.pop("fix", f"Fix for {fid}."),
        source=kw.pop("source", "ledger"),
        **kw,
    )


def _section(prompt: str, heading: str) -> str:
    start = prompt.index(heading)
    nxt = prompt.find("\n## ", start + len(heading))
    return prompt[start:] if nxt == -1 else prompt[start:nxt]


def test_integrity_finding_prompt(tmp_path, quiet_health):
    vault = build_vault(tmp_path)
    p = resolve_prompt.for_finding(vault, "run.integrity_restored", now=NOW, home=tmp_path / "home")
    assert (p.kind, p.key, p.current, p.cwd) == ("finding", "run.integrity_restored", True, str(vault))
    headings = (
        "## What Scout saw",
        "## Evidence to read",
        "## How to investigate",
        "## Reproduce and verify",
        "## Guardrails",
        "## Done when",
    )
    positions = [p.prompt.index(h) for h in headings]
    assert positions == sorted(positions)
    assert p.prompt.startswith(f"# {p.title}\n")
    evidence = _section(p.prompt, "## Evidence to read")
    assert str(vault / ".scout-logs" / LOG) in evidence
    assert str(vault / ".scout-logs" / "run-outcomes.jsonl") in evidence
    assert str(vault / ".scout-state" / "schedule.yaml") in evidence
    assert "Scout's own code (read-only)" in evidence
    assert BRANCH in p.prompt and RESTORING in p.prompt
    assert ".claude/settings.local.json" in p.prompt
    assert "started_at" in evidence and "morning-briefing" in evidence
    assert "git worktree list --porcelain" in p.prompt
    assert "7 days" in _section(p.prompt, "## Done when")


def test_prompt_never_contains_file_content(tmp_path, quiet_health):
    vault = build_vault(tmp_path)
    p = resolve_prompt.for_finding(vault, "run.integrity_restored", now=NOW, home=tmp_path / "home")
    assert CANARY_LOG not in p.prompt and CANARY_SETTINGS not in p.prompt


@pytest.mark.parametrize("fid", [*sorted(resolve_prompt.PLAYBOOKS), "config.unreadable"])
def test_guardrails_verbatim_in_every_playbook(tmp_path, monkeypatch, fid):
    vault = build_vault(tmp_path)
    _with_findings(monkeypatch, _finding(fid))
    p = resolve_prompt.for_finding(vault, fid, now=NOW, home=tmp_path / "home")
    assert resolve_prompt.GUARDRAILS in p.prompt
    assert p.prompt.count("## Guardrails") == 1


def test_guardrails_block_carries_the_rules():
    g = resolve_prompt.GUARDRAILS
    assert g.startswith("## Guardrails\n")
    for rule in (
        "Investigate read-only first",
        "runner scripts",
        "`.mcp.json`",
        "`.scout-state/schedule.yaml`",
        "`scripts/git-safe-commit.sh`",
        "force-push",
        "`.env*`",
        "Draft an issue",
    ):
        assert rule in g


def test_generic_playbook_for_unknown_id(tmp_path, monkeypatch):
    vault = build_vault(tmp_path)
    _with_findings(
        monkeypatch, _finding("config.unreadable", summary="scout-config.yaml won't parse.", fix="Fix the YAML.")
    )
    p = resolve_prompt.for_finding(vault, "config.unreadable", now=NOW, home=tmp_path / "home")
    assert "scout-config.yaml won't parse." in p.prompt and "Fix the YAML." in p.prompt
    assert "scoutctl health --json" in _section(p.prompt, "## Reproduce and verify")
    assert "`config.unreadable`" in _section(p.prompt, "## Done when")


def test_suffixed_finding_id_uses_scheduler_playbook(tmp_path, monkeypatch):
    vault = build_vault(tmp_path)
    _with_findings(monkeypatch, _finding("slot.stale:morning-briefing", source="schedule"))
    p = resolve_prompt.for_finding(vault, "slot.stale:morning-briefing", now=NOW, home=tmp_path / "home")
    assert p.key == "slot.stale:morning-briefing" and p.current
    assert str(vault / ".scout-state" / "last-fire.json") in p.prompt
    assert "scoutctl schedule show morning-briefing" in p.prompt


def test_lane_finding_uses_scheduler_playbook(tmp_path, monkeypatch):
    vault = build_vault(tmp_path)
    _with_findings(monkeypatch, _finding("lane.dark:mail", source="git"))
    p = resolve_prompt.for_finding(vault, "lane.dark:mail", now=NOW, home=tmp_path / "home")
    assert str(vault / ".scout-state" / "last-fire.json") in p.prompt


def test_detail_and_log_reach_what_scout_saw(tmp_path, monkeypatch):
    vault = build_vault(tmp_path)
    log = str(vault / ".scout-logs" / LOG)
    _with_findings(monkeypatch, _finding("runs.failing", detail="API Error: 529 overloaded", log=log))
    p = resolve_prompt.for_finding(vault, "runs.failing", now=NOW, home=tmp_path / "home")
    seen = _section(p.prompt, "## What Scout saw")
    assert "API Error: 529 overloaded" in seen and "`runs.failing`" in seen and "red" in seen
    assert log in _section(p.prompt, "## Evidence to read")


def test_cleared_finding_is_not_current(tmp_path, monkeypatch):
    vault = build_vault(tmp_path)
    _with_findings(monkeypatch)
    p = resolve_prompt.for_finding(vault, "run.integrity_restored", now=NOW, home=tmp_path / "home")
    assert p.current is False
    assert "confirm it cleared, and why" in p.prompt.lower()
    assert resolve_prompt.GUARDRAILS in p.prompt


def test_auth_playbook_never_prints_token(tmp_path, monkeypatch):
    vault = build_vault(tmp_path)
    _with_findings(monkeypatch, _finding("auth.expired", source="token"))
    p = resolve_prompt.for_finding(vault, "auth.expired", now=NOW, home=tmp_path / "home")
    assert "scoutctl auth status" in p.prompt
    assert "Never print" in p.prompt
    assert "the owner has signed in" in _section(p.prompt, "## Done when").lower()


def test_sandbox_and_update_families(tmp_path, monkeypatch):
    vault = build_vault(tmp_path)
    _with_findings(monkeypatch, _finding("runs.unsandboxed"), _finding("update.sidecars"))
    sand = resolve_prompt.for_finding(vault, "runs.unsandboxed", now=NOW, home=tmp_path / "home")
    upd = resolve_prompt.for_finding(vault, "update.sidecars", now=NOW, home=tmp_path / "home")
    assert "scoutctl protection status --json" in sand.prompt
    assert "scoutctl bootstrap drift" in upd.prompt and "*.proposed-merge" in upd.prompt


@pytest.mark.parametrize(
    ("kind", "key"),
    [
        ("finding", "Run.X"),
        ("finding", "a" * 70),
        ("finding", "a b"),
        ("finding", ""),
        ("run", "../x.log"),
        ("run", "run/../../etc.log"),
        ("run", "x.txt"),
        ("connector", "mcp: x"),
        ("connector", "../mcp"),
        ("nope", "x"),
    ],
)
def test_validate_rejects(kind, key):
    with pytest.raises(resolve_prompt.ResolveError):
        resolve_prompt.validate(kind, key)


@pytest.mark.parametrize(
    ("kind", "key"),
    [
        ("finding", "run.integrity_restored"),
        ("finding", "slot.stale:morning-briefing"),
        ("connector", "mcp:example_tracker"),
        ("connector", "notify:desktop"),
        ("run", LOG),
        ("run", "scout-2026-11-13_17-57-30-48213.log"),
    ],
)
def test_validate_accepts(kind, key):
    resolve_prompt.validate(kind, key)


def test_to_dict_has_every_key():
    p = resolve_prompt.ResolvePrompt(kind="finding", key="x", current=True, title="t", cwd="/v", prompt="p")
    assert p.to_dict() == {
        "schema_version": 1,
        "kind": "finding",
        "key": "x",
        "current": True,
        "title": "t",
        "cwd": "/v",
        "prompt": "p",
    }
