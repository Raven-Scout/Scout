"""`scoutctl diagnostics`: a bug-report bundle that never carries vault content (app audit card C11).

The canary test builds a synthetic vault whose notes, folder names, owner,
delegates, custom connectors, transcripts, run logs and secrets each carry a
unique string, makes every engine section that can quote them do so, and
asserts none of those strings reaches the report.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout import cli, diagnostics
from scout.diagnostics import ScrubContext, build_report, scrub

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)

# One unique string per kind of vault content. None of these may appear in a report.
CANARIES = {
    "note_body": "CANARYBODYqv7",
    "note_name": "Zephyrine Canarywood",
    "note_name_nospace": "canarynote-kx2",
    "folder": "Project Quillfeather",
    "owner_name": "Ottoline Canarysmith",
    "owner_email": "ottoline.canary@example.com",
    "delegate": "canarydelegate",
    "custom_connector": "canaryconnector",
    "transcript": "CANARYTRANSCRIPTzz1",
    "run_log": "CANARYRUNLOGpp4",
    "token": "sk-ant-oat01-CANARYTOKENabcdefghijklmnopqrstuvwxyz0123456789",
    "github_token": "ghp_CANARYabcdefghijklmnopqrstuvwxyz012345",
}


def _synthetic_vault(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "home"
    vault = home / "Scout"
    (vault / ".scout-state" / "last-assembled").mkdir(parents=True)
    (vault / ".scout-state" / "schedule.yaml").write_text("schema_version: 1\nslots: {}\n")
    for name in ("SKILL", "DREAMING", "RESEARCH"):
        (vault / f"{name}.md").write_text(f"# {name}\n")
        (vault / ".scout-state" / "last-assembled" / f"{name}.md").write_text(f"# {name}\n")
    (vault / "scout-config.yaml").write_text(
        f"user:\n  name: {CANARIES['owner_name']}\n  email: {CANARIES['owner_email']}\n"
        "plugin:\n  version_at_last_setup: '0.18.0'\n  version_at_last_update: '0.18.0'\n"
    )
    for rel in (
        "scripts/heartbeat.sh",
        "knowledge-base/ontology/parser.py",
        "action-items/render.py",
        "hooks/kb-pre-filter.sh",
    ):
        (vault / rel).parent.mkdir(parents=True, exist_ok=True)
        (vault / rel).write_text("# engine file\n")

    # Notes: a name with spaces, a name without, a folder, and a body.
    people = vault / "knowledge-base" / "people"
    people.mkdir(parents=True)
    (people / f"{CANARIES['note_name']}.md").write_text(f"# {CANARIES['note_name']}\n{CANARIES['note_body']}\n")
    (people / f"{CANARIES['note_name_nospace']}.md").write_text(f"[[{CANARIES['note_name']}]]\n")
    project = vault / "knowledge-base" / CANARIES["folder"]
    project.mkdir()
    (project / "kickoff.md").write_text(f"{CANARIES['note_body']}\n")

    # A custom connector the doctor will complain about by name.
    (vault / "connectors.custom.yaml").write_text(
        f"schema_version: 1\nconnectors:\n  {CANARIES['custom_connector']}:\n    bogus: true\n"
    )
    # A delegate mirror state file the doctor refuses to trust.
    (vault / ".scout-state" / "delegate-mirrors.json").write_text(
        json.dumps({"links": {CANARIES["delegate"]: {"remote": "git@github.com:someone/private.git"}}})
    )
    # A transcript in the vault and in the home folder.
    (vault / ".claude" / "projects").mkdir(parents=True)
    (vault / ".claude" / "projects" / "s.jsonl").write_text(json.dumps({"text": CANARIES["transcript"]}) + "\n")
    (home / ".claude" / "projects" / "-x").mkdir(parents=True)
    (home / ".claude" / "projects" / "-x" / "t.jsonl").write_text(CANARIES["transcript"] + "\n")
    # A secret where Scout keeps them.
    (home / ".scout-secrets").mkdir()
    (home / ".scout-secrets" / "claude-oauth-token").write_text(CANARIES["token"] + "\n")

    # A failed run whose log names its error with vault content in it: the doctor quotes that line.
    logs = vault / ".scout-logs"
    logs.mkdir()
    log_name = "scout-2026-11-16_08-00.log"
    (logs / log_name).write_text(
        f"=== start ===\nreading {vault}/knowledge-base/people/{CANARIES['note_name']}.md\n"
        f"Failed to authenticate. API Error: 401 {CANARIES['run_log']} {CANARIES['token']}\n"
    )
    started = int(NOW.timestamp()) - 3600
    rows = [
        {
            "slot": "morning-briefing",
            "mode": "morning-briefing",
            "started_at": started - 86400 * i,
            "finished_at": started - 86400 * i + 60,
            "exit_code": 1,
            "outcome": "failed",
            "failure_class": "oauth_expired",
            "log": log_name,
            "commits_in_window": 0,
            "newest_commit": "0123456789abcdef0123456789abcdef01234567",
        }
        for i in range(3, -1, -1)
    ]
    (logs / "run-outcomes.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return vault, home


def _leaky_sandbox(vault: Path, ctx: ScrubContext) -> dict:
    """Stands in for the real sandbox check, with a reason that quotes vault content."""
    reason = (
        f"can't write {vault}/knowledge-base/people/{CANARIES['note_name']}.md for {CANARIES['owner_email']}"
        f" or {vault}/knowledge-base/{CANARIES['folder']}/kickoff.md"
    )
    return {"status": "failed", "reason": scrub(reason, ctx)}


def _leaky_runtime(vault: Path, ctx: ScrubContext) -> dict:
    reason = f"token {CANARIES['token']} {CANARIES['github_token']} refused for {CANARIES['delegate']}"
    return {"runtime": "claude-code", "allowed": False, "code": "check_failed", "reason": scrub(reason, ctx)}


def _assert_no_canary(text: str) -> None:
    for kind, canary in CANARIES.items():
        assert canary not in text, f"{kind} canary leaked: {canary!r}"
    # The parts of multi-word canaries must not leak on their own either.
    for word in ("Zephyrine", "Canarywood", "Quillfeather", "Ottoline", "Canarysmith"):
        assert word not in text, f"{word!r} leaked"


def test_report_never_carries_vault_content(tmp_path: Path) -> None:
    vault, home = _synthetic_vault(tmp_path)
    report = build_report(vault, home=home, now=NOW, check_jobs=False, sandbox=_leaky_sandbox, runtime=_leaky_runtime)
    text = json.dumps(report)
    _assert_no_canary(text)
    assert str(home) not in text
    # It is still a useful report: the doctor ran, the failing runs show, structure is kept.
    assert report["doctor"]["severity"] in {"yellow", "red"}
    assert report["doctor"]["run_health"]["findings"], "the failing runs should produce a finding"
    assert any(f["has_detail"] for f in report["doctor"]["run_health"]["findings"])
    assert all("detail" not in f and "log" not in f for f in report["doctor"]["run_health"]["findings"])
    assert [r["outcome"] for r in report["recent_runs"]] == ["failed"] * 4
    assert report["recent_runs"][0]["slot"] == "morning-briefing"
    assert report["sandbox"]["reason"].startswith("can't write <vault>/<path>")


def test_cli_report_never_carries_vault_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The real command, with the real sandbox and runtime sections, against the synthetic vault."""
    vault, home = _synthetic_vault(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SCOUT_DATA_DIR", str(vault))
    result = CliRunner().invoke(cli.app, ["diagnostics", "--json", "--no-jobs"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["schema"] == diagnostics.SCHEMA_VERSION
    _assert_no_canary(result.output)
    assert str(home) not in result.output


def test_text_form_never_carries_vault_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault, home = _synthetic_vault(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SCOUT_DATA_DIR", str(vault))
    result = CliRunner().invoke(cli.app, ["diagnostics", "--no-jobs"])
    assert result.exit_code == 0, result.output
    assert result.output.startswith("scout engine ")
    _assert_no_canary(result.output)


def test_missing_vault_still_reports(tmp_path: Path) -> None:
    report = build_report(tmp_path / "nope", home=tmp_path, now=NOW, check_jobs=False)
    assert report["vault"]["exists"] is False
    assert report["doctor"]["severity"] == "red"
    assert report["doctor"]["errors"] == ["vault directory missing: <vault>"]
    assert report["sandbox"] is None and report["recent_runs"] == []


def _ctx(tmp_path: Path, *names: str) -> ScrubContext:
    ctx = ScrubContext(vault=tmp_path / "Scout", home=tmp_path)
    for n in names:
        ctx.add(n, "<name>")
    return ctx.finish()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Scout's own folders and engine files stay readable.
        ("{v}/.scout-logs/scout-2026-11-16_08-00.log", "<vault>/.scout-logs/scout-2026-11-16_08-00.log"),
        ("missing {v}/.scout-state/schedule.yaml", "missing <vault>/.scout-state/schedule.yaml"),
        ("cat-1 file missing: scripts/heartbeat.sh", "cat-1 file missing: scripts/heartbeat.sh"),
        ("{v}/SKILL.md is stale", "<vault>/SKILL.md is stale"),
        # A note's path is not.
        ("{v}/knowledge-base/people/Alex.md", "<vault>/<path>"),
        ("see notes/alex.md", "see <note>.md"),
        ("linked from [[Some Note]]", "linked from [[<note>]]"),
        # Home, emails, URLs, tokens.
        ("{h}/Library/Caches", "~/Library/Caches"),
        ("ask alex@example.com", "ask <email>"),
        ("pushed to https://example.com/private/repo", "pushed to <url>"),
        ("see https://github.com/Raven-Scout/Scout/issues/1", "see https://github.com/Raven-Scout/Scout/issues/1"),
        ("key sk-ant-api03-abcdef", "key <redacted>"),
        ("Bearer abc.def", "Bearer <redacted>"),
        ("id 0123456789abcdef0123456789abcdef", "id <redacted>"),
    ],
)
def test_scrub_rules(tmp_path: Path, raw: str, expected: str) -> None:
    ctx = _ctx(tmp_path)
    raw = raw.format(v=tmp_path / "Scout", h=tmp_path)
    assert scrub(raw, ctx) == expected


def test_scrub_names_are_whole_words(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, "run", "Acme Deal")
    # "run" is hidden as a word, never inside another word.
    assert scrub("runs failed: run Acme Deal", ctx) == "runs failed: <name> <name>"
    assert scrub("rerun", ctx) == "rerun"


def test_scrub_truncates_long_strings(tmp_path: Path) -> None:
    out = scrub("word " * 200, _ctx(tmp_path))
    assert len(out) == diagnostics.MAX_STRING + 1 and out.endswith("…")
