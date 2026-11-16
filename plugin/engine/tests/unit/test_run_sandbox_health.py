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


def test_a_run_with_the_env_switch_off_gets_a_note(tmp_path: Path) -> None:
    v = _vault(tmp_path)
    _ledger(v, [_row(NOW - timedelta(hours=1), sandbox="off", sandbox_source="env")])
    (finding,) = health.evaluate(v, now=NOW).findings
    assert (finding.id, finding.severity) == ("runs.sandbox_env_off", "note")
    assert "SCOUT_RUN_SANDBOX" in finding.summary and "LaunchAgent" in finding.fix


def test_the_owners_switch_off_says_nothing(tmp_path: Path) -> None:
    v = _vault(tmp_path)
    _ledger(v, [_row(NOW - timedelta(hours=1), sandbox="off", sandbox_source="switch")])
    assert _ids(v) == {}


# -- the post-run integrity check (spec §13) -----------------------------------------------------------------


@pytest.mark.parametrize("status", ["restored", "unrestored"])
def test_a_run_whose_protected_files_were_put_back_is_red(tmp_path: Path, status: str) -> None:
    v = _vault(tmp_path)
    _ledger(
        v,
        [
            _row(NOW - timedelta(days=2), sandbox="enforced", integrity=status),
            _row(NOW - timedelta(hours=1), sandbox="enforced", integrity="clean"),
        ],
    )
    (finding,) = health.evaluate(v, now=NOW).findings
    assert (finding.id, finding.severity) == ("run.integrity_restored", "red")
    assert ("tag or the stash" in finding.summary) == (status == "unrestored")


def test_an_old_restore_ages_out(tmp_path: Path) -> None:
    v = _vault(tmp_path)
    _ledger(
        v,
        [
            _row(NOW - health.INTEGRITY_WINDOW - timedelta(hours=1), integrity="restored"),
            _row(NOW - timedelta(hours=1), integrity="clean"),
        ],
    )
    assert _ids(v) == {}


def test_a_check_that_couldnt_run_is_yellow(tmp_path: Path) -> None:
    v = _vault(tmp_path)
    _ledger(v, [_row(NOW - timedelta(hours=1), sandbox="enforced", integrity="error")])
    assert _ids(v) == {"run.integrity_unchecked": "yellow"}


@pytest.mark.parametrize("status", ["clean", "unchecked", None])
def test_a_clean_or_unchecked_run_says_nothing(tmp_path: Path, status: str | None) -> None:
    v = _vault(tmp_path)
    _ledger(v, [_row(NOW - timedelta(hours=1), **({"integrity": status} if status else {}))])
    assert _ids(v) == {}


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
    monkeypatch.delenv("SCOUT_RUN_SANDBOX", raising=False)
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


def test_doctor_judges_the_switch_as_the_scheduler_sees_it(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SCOUT_RUN_SANDBOX", "1")
    seen: list[str | None] = []

    def prepare(_vault, env, **_k):  # noqa: ANN001
        seen.append(env.get("SCOUT_RUN_SANDBOX"))
        return launch.Prepared("enforced", "sandbox-exec, self-test ok")

    monkeypatch.setattr(launch, "prepare", prepare)
    warnings, notes = bootstrap_doctor._check_run_sandbox(vault=tmp_path, home=tmp_path)
    assert seen == [None]  # this shell's switch isn't the scheduler's
    assert warnings == []
    assert notes == [
        "run sandbox: enforced (sandbox-exec, self-test ok)",
        "this shell has SCOUT_RUN_SANDBOX=1, which scheduled runs don't inherit",
    ]


@pytest.mark.parametrize(("file_off", "says"), [(False, "on (default)"), (True, "off (")])
def test_doctor_in_a_shell_with_the_switch_off_reports_the_owners_switch(
    tmp_path: Path, monkeypatch, file_off: bool, says: str
) -> None:
    monkeypatch.setenv("SCOUT_RUN_SANDBOX", "off")

    def boom(*_a, **_k):
        raise AssertionError("no profile is built for a shell that runs without the sandbox")

    monkeypatch.setattr(launch, "prepare", boom)
    if file_off:
        launch.set_switch(False, {"HOME": str(tmp_path)})
    warnings, notes = bootstrap_doctor._check_run_sandbox(vault=tmp_path, home=tmp_path)
    assert warnings == []
    assert notes[0].startswith("run sandbox: not checked here (SCOUT_RUN_SANDBOX=off in this shell")
    assert f"the owner's switch says {says}" in notes[0]


@pytest.mark.parametrize(("source", "where", "text"), [("env", "warnings", "LaunchAgent"), ("switch", "notes", "off")])
def test_doctor_reads_the_last_runs_switch_from_its_row(
    tmp_path: Path, monkeypatch, source: str, where: str, text: str
) -> None:
    monkeypatch.delenv("SCOUT_RUN_SANDBOX", raising=False)
    monkeypatch.setattr(launch, "prepare", lambda *_a, **_k: launch.Prepared("enforced", "ok"))
    v = _vault(tmp_path)
    _ledger(v, [_row(NOW - timedelta(hours=1), sandbox="off", sandbox_source=source)])
    warnings, notes = bootstrap_doctor._check_run_sandbox(vault=v, home=tmp_path)
    got = {"warnings": warnings, "notes": notes}[where]
    assert any("the last scheduled run ran" in line and text in line for line in got)


@pytest.mark.parametrize("error", [PermissionError("Operation not permitted"), FileNotFoundError("launchctl")])
def test_doctor_completes_when_launchctl_cant_start(monkeypatch, error: OSError) -> None:
    def refuse(*_a, **_k):
        raise error

    monkeypatch.setattr(subprocess, "run", refuse)
    errors, warnings = bootstrap_doctor._check_launchd_jobs()
    assert errors == [] and warnings == ["launchctl unavailable — skipped job registration check"]


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
            ("Bash", {"command": "python3 -c \"open('.git/hooks/post-merge','w').write('')\""}),
        ],
    )
    _session(project, "owner", "claude-desktop", [("Edit", {"file_path": str(vault / ".git" / "config")})])

    report = replay.replay(project, vault, limit=50)

    assert report["sessions"] == 1
    assert report["tool_calls"] == 7
    assert report["denied_writes_by_form"] == {"agent settings": 1, ".git/hooks": 1}
    # The program that only reads a runner script isn't counted (#637 round 3); the one that writes a hook is.
    assert report["inline_program_literals_under_a_deny"] == {".git/hooks": 1}
    assert report["denied_secret_reads"] == 1
    assert report["git_subcommands"] == {"commit": 1}
    assert str(vault) not in json.dumps(report) and str(tmp_path) not in replay.format_report(report)
