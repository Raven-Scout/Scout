"""The doctor's scheduled-job check per platform (#449).

macOS schedules through launchd, Linux through the scout-managed crontab block
(`scoutctl schedule install-cron`). The launchd check used to run on every POSIX
host, so on Linux it always warned "launchctl unavailable" and every good install
came out yellow, which made `install.sh` exit 1.

subprocess.run is faked throughout: no test reaches a real launchctl or crontab.
"""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess

from scout.scripts.bootstrap_doctor import DoctorReport, Severity, run_doctor


def _populate_minimal_vault(vault: Path) -> None:
    """The files a healthy vault has after bootstrap (as in test_bootstrap_doctor.py)."""
    (vault / ".scout-state" / "last-assembled").mkdir(parents=True)
    (vault / ".scout-state" / "schedule.yaml").write_text("schema_version: 1\nslots: {}\n")
    for name in ("SKILL", "DREAMING", "RESEARCH"):
        (vault / f"{name}.md").write_text(f"# {name}\n")
        (vault / ".scout-state" / "last-assembled" / f"{name}.md").write_text(f"# {name}\n")
    (vault / "scout-config.yaml").write_text(
        "user:\n  name: Test\nplugin:\n  version_at_last_setup: '0.4.0'\n  version_at_last_update: '0.4.0'\n"
    )
    for rel, text in (
        ("scripts/heartbeat.sh", "#!/bin/bash\necho ok\n"),
        ("knowledge-base/ontology/parser.py", "# parser\n"),
        ("action-items/render.py", "# render\n"),
        ("hooks/kb-pre-filter.sh", "#!/bin/bash\n"),
    ):
        (vault / rel).parent.mkdir(parents=True, exist_ok=True)
        (vault / rel).write_text(text)


def _cron_block(vault: Path, *, tick: bool = True, heartbeat: bool = True) -> str:
    """The block install_cron writes (defaults/cron-managed-block.tmpl), for ``vault``."""
    log = f"{vault}/.scout-logs/cron.log"
    scoutctl = vault.parent / "engine-bin" / "scoutctl"  # real and executable, so the bin-path check passes
    scoutctl.parent.mkdir(parents=True, exist_ok=True)
    scoutctl.write_text("#!/bin/sh\n")
    scoutctl.chmod(0o755)
    lines = ["# >>> scout-managed >>>", "SHELL=/bin/bash", f"SCOUT_DATA_DIR={vault}"]
    if tick:
        lines.append(f"*/5 * * * * {scoutctl} schedule tick >> {log} 2>&1")
    if heartbeat:
        lines.append(f"*/30 * * * * {vault}/scripts/heartbeat.sh >> {log} 2>&1")
    lines.append("# <<< scout-managed <<<")
    return "\n".join(lines) + "\n"


def _fake(monkeypatch, system: str, crontab: str | None = "", *, rc: int = 0) -> list[list[str]]:
    """Run the doctor as ``system``. ``crontab=None``: no crontab binary. Returns the argv of every call."""
    calls: list[list[str]] = []

    def fake_run(args, **_kwargs):
        calls.append(list(args))
        if args[:2] == ["crontab", "-l"]:
            if crontab is None:
                raise FileNotFoundError("crontab")
            return CompletedProcess(args, rc, stdout=crontab if rc == 0 else "", stderr="no crontab for user")
        if args[:2] == ["launchctl", "list"]:
            return CompletedProcess(args, 0, stdout="-\t0\tcom.scout.schedule-tick\n-\t0\tcom.scout.heartbeat\n")
        return CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("scout.scripts.bootstrap_doctor.subprocess.run", fake_run)
    monkeypatch.setattr("scout.scripts.bootstrap_doctor.platform.system", lambda: system)
    monkeypatch.setattr("scout.scripts.bootstrap_doctor.os.name", "posix")
    return calls


def _job_findings(report: DoctorReport) -> list[str]:
    return [m for m in report.errors + report.warnings if any(w in m for w in ("cron", "launchctl", "launchd"))]


def _ran(calls: list[list[str]], tool: str) -> bool:
    return any(c[:1] == [tool] for c in calls)


# ---- Linux -------------------------------------------------------------------


def test_linux_with_the_scout_cron_block_is_green_and_never_runs_launchctl(tmp_path, monkeypatch):
    _populate_minimal_vault(tmp_path)
    calls = _fake(monkeypatch, "Linux", _cron_block(tmp_path))
    report = run_doctor(vault=tmp_path, check_jobs=True, home=tmp_path)
    assert _job_findings(report) == []
    assert not _ran(calls, "launchctl")


def test_linux_missing_schedule_tick_is_red(tmp_path, monkeypatch):
    _populate_minimal_vault(tmp_path)
    _fake(monkeypatch, "Linux", _cron_block(tmp_path, tick=False))
    report = run_doctor(vault=tmp_path, check_jobs=True, home=tmp_path)
    assert report.severity is Severity.RED
    assert any("schedule tick" in e and "install-cron" in e for e in report.errors)
    assert not any("heartbeat" in e for e in report.errors)


def test_linux_missing_heartbeat_is_red(tmp_path, monkeypatch):
    _populate_minimal_vault(tmp_path)
    _fake(monkeypatch, "Linux", _cron_block(tmp_path, heartbeat=False))
    report = run_doctor(vault=tmp_path, check_jobs=True, home=tmp_path)
    assert report.severity is Severity.RED
    assert any("heartbeat" in e and "install-cron" in e for e in report.errors)
    assert not any("schedule tick" in e for e in report.errors)


def test_linux_with_no_crontab_at_all_is_red(tmp_path, monkeypatch):
    _populate_minimal_vault(tmp_path)
    _fake(monkeypatch, "Linux", "", rc=1)  # `crontab -l` exits 1: "no crontab for user"
    report = run_doctor(vault=tmp_path, check_jobs=True, home=tmp_path)
    assert report.severity is Severity.RED
    assert any("schedule tick" in e for e in report.errors)
    assert any("heartbeat" in e for e in report.errors)


def test_linux_scout_lines_outside_the_managed_block_do_not_count(tmp_path, monkeypatch):
    _populate_minimal_vault(tmp_path)
    loose = "*/5 * * * * /opt/scout/.venv/bin/scoutctl schedule tick\n"
    _fake(monkeypatch, "Linux", loose)
    report = run_doctor(vault=tmp_path, check_jobs=True, home=tmp_path)
    assert any("schedule tick" in e for e in report.errors)


def test_linux_cron_block_for_another_vault_is_red(tmp_path, monkeypatch):
    vault = tmp_path / "Scout"
    _populate_minimal_vault(vault)
    _fake(monkeypatch, "Linux", _cron_block(tmp_path / "Other"))
    report = run_doctor(vault=vault, check_jobs=True, home=tmp_path)
    assert report.severity is Severity.RED
    assert any(str(tmp_path / "Other") in e and "install-cron" in e for e in report.errors)


def test_linux_cron_block_naming_this_vault_through_a_symlink_is_fine(tmp_path, monkeypatch):
    real = tmp_path / "real"
    _populate_minimal_vault(real)
    link = tmp_path / "link"
    link.symlink_to(real)
    _fake(monkeypatch, "Linux", _cron_block(link))
    report = run_doctor(vault=real, check_jobs=True, home=tmp_path)
    assert _job_findings(report) == []


def test_linux_without_a_crontab_binary_warns_once(tmp_path, monkeypatch):
    _populate_minimal_vault(tmp_path)
    _fake(monkeypatch, "Linux", None)
    report = run_doctor(vault=tmp_path, check_jobs=True, home=tmp_path)
    assert [w for w in report.warnings if "crontab" in w] == ["crontab unavailable — skipped job registration check"]
    assert _job_findings(report) == ["crontab unavailable — skipped job registration check"]


def test_linux_job_checks_are_skipped_without_check_jobs(tmp_path, monkeypatch):
    _populate_minimal_vault(tmp_path)
    calls = _fake(monkeypatch, "Linux", "", rc=1)
    report = run_doctor(vault=tmp_path, check_jobs=False, home=tmp_path)
    assert report.severity is Severity.GREEN
    assert not _ran(calls, "crontab")


def test_linux_fix_hint_names_the_vault_being_checked(tmp_path, monkeypatch):
    """install-cron schedules $SCOUT_DATA_DIR (default ~/Scout), so the hint carries the vault."""
    vault = tmp_path / "My Vault"
    _populate_minimal_vault(vault)
    _fake(monkeypatch, "Linux", "", rc=1)
    report = run_doctor(vault=vault, check_jobs=True, home=tmp_path)
    assert any(f"SCOUT_DATA_DIR='{vault}' scoutctl schedule install-cron" in e for e in report.errors)


# ---- macOS and the rest ------------------------------------------------------


def test_macos_still_checks_launchd_and_never_runs_crontab(tmp_path, monkeypatch):
    _populate_minimal_vault(tmp_path)
    calls = _fake(monkeypatch, "Darwin")
    report = run_doctor(vault=tmp_path, check_jobs=True, home=tmp_path)
    assert _job_findings(report) == []
    assert _ran(calls, "launchctl")
    assert not _ran(calls, "crontab")


def test_other_platforms_check_no_scheduler(tmp_path, monkeypatch):
    _populate_minimal_vault(tmp_path)
    calls = _fake(monkeypatch, "FreeBSD")
    report = run_doctor(vault=tmp_path, check_jobs=True, home=tmp_path)
    assert _job_findings(report) == []
    assert not _ran(calls, "launchctl") and not _ran(calls, "crontab")
