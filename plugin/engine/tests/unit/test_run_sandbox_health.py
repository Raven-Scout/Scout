"""Run health, the doctor and the replay read the runners' write sandbox (spec 2026-11-15 §8, §10.3)."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scout import health
from scout.sandbox import launch, replay
from scout.scripts import bootstrap_doctor

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)


def _vault(tmp_path: Path) -> Path:
    v = tmp_path / "Scout"
    (v / ".scout-logs").mkdir(parents=True)
    return v


def _row(at: datetime, *, code: int = 0, **extra: object) -> dict[str, object]:
    started = int((at - timedelta(minutes=5)).timestamp())
    return {
        "slot": "morning-briefing",
        "mode": "scout",
        "started_at": started,
        "finished_at": int(at.timestamp()),
        "duration_s": 300,
        "exit_code": code,
        "failure_class": "ok" if code == 0 else "unknown",
        "log": f"scout-{started}.log",
        **extra,
    }


def _ledger(vault: Path, rows: list[dict[str, object]]) -> None:
    text = "\n".join(json.dumps(r) for r in rows) + "\n"
    (vault / ".scout-logs" / "run-outcomes.jsonl").write_text(text, encoding="utf-8")


def _ids(vault: Path) -> dict[str, str]:
    return {f.id: f.severity for f in health.evaluate(vault, now=NOW).findings}


def test_a_sandbox_skip_is_red(tmp_path: Path) -> None:
    v = _vault(tmp_path)
    _ledger(
        v,
        [
            _row(NOW - timedelta(hours=5), sandbox="enforced"),
            _row(NOW - timedelta(hours=1), outcome="skipped_sandbox", sandbox="failed"),
        ],
    )
    assert _ids(v) == {"sandbox.failed": "red"}


def test_a_run_after_the_skip_clears_it(tmp_path: Path) -> None:
    v = _vault(tmp_path)
    _ledger(
        v,
        [
            _row(NOW - timedelta(hours=5), outcome="skipped_sandbox", sandbox="failed"),
            _row(NOW - timedelta(hours=1), sandbox="enforced"),
        ],
    )
    assert _ids(v) == {}


@pytest.mark.parametrize(("status", "severity"), [("unavailable", "yellow"), ("conflict", "yellow")])
def test_an_unsandboxed_run_says_so(tmp_path: Path, status: str, severity: str) -> None:
    v = _vault(tmp_path)
    _ledger(v, [_row(NOW - timedelta(hours=1), sandbox=status)])
    assert _ids(v) == {"runs.unsandboxed": severity}


@pytest.mark.parametrize("status", ["enforced", "off", None])
def test_sandboxed_turned_off_or_older_runners_say_nothing(tmp_path: Path, status: str | None) -> None:
    v = _vault(tmp_path)
    _ledger(v, [_row(NOW - timedelta(hours=1), **({"sandbox": status} if status else {}))])
    assert _ids(v) == {}


def test_a_sandbox_skip_counts_as_a_recorded_fire(tmp_path: Path) -> None:
    assert "skipped_sandbox" in health.SKIP_OUTCOMES


# -- doctor -----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "where"),
    [
        ("enforced", "notes"),
        ("unavailable", "notes"),
        ("off", "notes"),
        ("conflict", "warnings"),
        ("failed", "warnings"),
    ],
)
def test_doctor_reports_the_sandbox(tmp_path: Path, monkeypatch, status: str, where: str) -> None:
    monkeypatch.setattr(launch, "prepare", lambda *_a, **_k: launch.Prepared(status, "because"))
    warnings, notes = bootstrap_doctor._check_run_sandbox(vault=tmp_path, home=tmp_path)
    got = {"warnings": warnings, "notes": notes}[where]
    assert len(got) == 1 and got[0].startswith(f"run sandbox: {status} (because)")


def test_doctor_inside_a_sandboxed_run_doesnt_try_to_nest(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SCOUT_SANDBOX_STATUS", "enforced")

    def boom(*_a, **_k):
        raise AssertionError("prepare must not run inside the sandbox")

    monkeypatch.setattr(launch, "prepare", boom)
    assert bootstrap_doctor._check_run_sandbox(vault=tmp_path) == (
        [],
        ["run sandbox: enforced (this session runs inside it)"],
    )


def test_doctor_a_refused_launchctl_list_is_not_missing_jobs(monkeypatch) -> None:
    monkeypatch.setattr(
        subprocess, "run", lambda *_a, **_k: subprocess.CompletedProcess(["launchctl"], 1, stdout="", stderr="")
    )
    errors, warnings = bootstrap_doctor._check_launchd_jobs()
    assert errors == [] and "skipped" in warnings[0]


# -- replay (synthetic transcripts only) ---------------------------------------------------------------------


def _session(project: Path, name: str, entrypoint: str, calls: list[tuple[str, dict[str, object]]]) -> None:
    rows: list[dict[str, object]] = [{"type": "system", "entrypoint": entrypoint, "permissionMode": "auto"}]
    for tool, inp in calls:
        rows.append({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": tool, "input": inp}]}})
    (project / f"{name}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_replay_counts_denies_by_form_and_reports_no_paths(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / "notes").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(vault)], check=True)
    project = tmp_path / "project"
    project.mkdir()
    _session(
        project,
        "run",
        "sdk-cli",
        [
            ("Write", {"file_path": str(vault / "notes" / "ok.md")}),
            ("Edit", {"file_path": str(vault / ".claude" / "settings.local.json")}),
            ("Bash", {"command": "echo x > .git/hooks/pre-commit && git commit -qm x"}),
            ("Bash", {"command": "python3 -c \"print(open('scripts/claude-with-retry.sh').read())\""}),
            ("Bash", {"command": "ls ~/.scout-secrets/ | grep telegram; cat ~/.scout-secrets/linear-api-key"}),
            ("Bash", {"command": "T=$(mktemp -d); rm -rf $T"}),
        ],
    )
    _session(project, "owner", "claude-desktop", [("Edit", {"file_path": str(vault / ".git" / "config")})])

    report = replay.replay(project, vault, limit=50)

    assert report["sessions"] == 1
    assert report["tool_calls"] == 6
    assert report["denied_writes_by_form"] == {"agent settings": 1, ".git/hooks": 1}
    assert report["inline_program_literals_under_a_deny"] == {"Scout's runner scripts": 1}
    assert report["denied_secret_reads"] == 1
    assert report["git_subcommands"] == {"commit": 1}
    assert str(vault) not in json.dumps(report) and str(tmp_path) not in replay.format_report(report)
