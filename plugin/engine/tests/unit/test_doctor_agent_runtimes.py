"""The doctor's runner-binary and auth checks loop over the agent runtimes the schedule uses (spec 2026-11-13 §8.9).

Task 1.8. Claude Code's findings are worded exactly as before the loop; a runtime another slot names gets its own.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from scout import claude_auth
from scout.scripts import bootstrap_doctor as doc
from tests._agent_runtimes import DummyRuntime, registered

SLOT = """\
  {key}:
    type: dreaming
    runner: run-dreaming.sh
    fires_at_local: "02:00"
    weekdays: [Mon, Tue, Wed, Thu, Fri]
    missed_window_hours: 4
    on_miss: fire
    cooldown_minutes: 60
{extra}"""

# Pinned word for word: the loop must not reword Claude Code's findings.
CLAUDE_BIN_ERROR = (
    "run-scout.sh: CLAUDE_BIN=/nonexistent/claude is not an executable claude CLI — every scheduled run will "
    "fail. Fix: in scout-config.yaml set connectors.inputs.claude_bin to the output of `command -v claude` "
    '(or to "" to auto-detect), then run /scout-update.'
)
CLAUDE_LOGIN_NOTE = (
    "Scheduled runs use your interactive Claude login, which expires. For unattended runs: scoutctl auth setup-token"
)


@pytest.fixture(autouse=True)
def _quiet_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """The checks that read this machine's jobs, shim and sandbox say nothing; the auth store is the file one."""
    monkeypatch.setattr(claude_auth, "_system", lambda: "Linux")
    for name in ("_check_scheduled_jobs", "_check_scheduler_bin_path", "_check_engine_pointer", "_check_run_sandbox"):
        monkeypatch.setattr(doc, name, lambda **_k: ([], []))
    monkeypatch.setattr(doc, "_check_scoutctl_shim", lambda **_k: ([], []))


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("SCOUT_AGENT_RUNTIME", raising=False)
    v = tmp_path / "Scout"
    (v / ".scout-state").mkdir(parents=True)
    (v / ".scout-logs").mkdir()
    (v / "run-scout.sh").write_text(
        '#!/bin/bash\nCLAUDE_BIN="/nonexistent/claude"\nexport SCOUT_MODE="$1"\nexport SCOUT_READER=autonomous\n',
        encoding="utf-8",
    )
    return v


def _schedule(vault: Path, *slots: tuple[str, str | None]) -> None:
    body = "".join(
        SLOT.format(key=key, extra=f"    agent_runtime: {runtime}\n" if runtime else "") for key, runtime in slots
    )
    (vault / ".scout-state" / "schedule.yaml").write_text("schema_version: 1\nslots:\n" + body, encoding="utf-8")


def _doctor(vault: Path, home: Path) -> doc.DoctorReport:
    return doc.run_doctor(vault=vault, check_jobs=True, home=home)


def _runtime_findings(findings: list[str], tmp_path: Path) -> list[str]:
    """The findings that name a runtime (the test's own temp path, which names the test, left out)."""
    return [f for f in findings if any(w in f.replace(str(tmp_path), "").lower() for w in ("claude", "dummy"))]


@pytest.mark.parametrize("schedule", ["shipped", "vault"])
def test_claude_doctor_messages_unchanged(vault: Path, tmp_path: Path, schedule: str) -> None:
    """With every slot on Claude Code, its binary and auth findings are today's, word for word, and nothing else."""
    if schedule == "vault":
        _schedule(vault, ("night", None), ("late", "claude-code"))
    report = _doctor(vault, tmp_path / "home")
    assert _runtime_findings(report.errors, tmp_path) == [CLAUDE_BIN_ERROR]
    assert _runtime_findings(report.notes, tmp_path) == [CLAUDE_LOGIN_NOTE]
    assert _runtime_findings(report.warnings, tmp_path) == []


def test_doctor_checks_each_scheduled_runtime(vault: Path, tmp_path: Path) -> None:
    """A slot on another runtime gets that runtime's binary check, and Claude Code's slots keep theirs."""
    with registered(DummyRuntime("dummy", binary=None)):
        _schedule(vault, ("night", None), ("other", "dummy"))
        report = _doctor(vault, tmp_path / "home")
    assert CLAUDE_BIN_ERROR in report.errors
    assert CLAUDE_LOGIN_NOTE in report.notes
    dummy = [e for e in report.errors if "Dummy CLI" in e]
    assert len(dummy) == 1
    assert "(dummy)" in dummy[0] and "'other'" in dummy[0]
    assert "scoutctl agent-runtime check --slot other" in dummy[0]


def test_a_runtime_no_slot_uses_is_not_checked(vault: Path, tmp_path: Path) -> None:
    """Registered isn't enough: only the runtimes the schedule uses are checked."""
    with registered(DummyRuntime("dummy", binary=None)):
        _schedule(vault, ("night", None))
        report = _doctor(vault, tmp_path / "home")
    assert not [f for f in report.errors + report.warnings + report.notes if "Dummy CLI" in f]


def test_a_schedule_without_claude_skips_claudes_checks(vault: Path, tmp_path: Path) -> None:
    binary = tmp_path / "dummy-cli"
    with registered(DummyRuntime("dummy", binary=binary)):
        _schedule(vault, ("other", "dummy"))
        report = _doctor(vault, tmp_path / "home")
    assert CLAUDE_BIN_ERROR not in report.errors
    assert CLAUDE_LOGIN_NOTE not in report.notes
    assert "Dummy CLI signs in by login (dummy)" in report.notes


def test_the_config_default_runtime_counts(vault: Path, tmp_path: Path) -> None:
    """A slot without agent_runtime: runs on agent_runtimes.default."""
    (vault / "scout-config.yaml").write_text("agent_runtimes:\n  default: dummy\n", encoding="utf-8")
    with registered(DummyRuntime("dummy", binary=None)):
        _schedule(vault, ("night", None))
        report = _doctor(vault, tmp_path / "home")
    assert CLAUDE_BIN_ERROR not in report.errors
    assert [e for e in report.errors if "Dummy CLI" in e]


def test_an_unreadable_schedule_falls_back_to_claude(vault: Path, tmp_path: Path) -> None:
    """A schedule naming an unknown runtime is the schedule check's to report; Claude Code is still checked."""
    _schedule(vault, ("other", "nonesuch"))
    report = _doctor(vault, tmp_path / "home")
    assert CLAUDE_BIN_ERROR in report.errors
    assert any("checked Claude Code only" in n for n in report.notes)


@pytest.fixture
def dummy() -> Iterator[DummyRuntime]:
    with registered(DummyRuntime("dummy")) as adapter:
        yield adapter


def test_runtime_bin_found_is_clean(dummy: DummyRuntime, tmp_path: Path) -> None:
    dummy.binary = tmp_path / "dummy-cli"
    assert doc._check_runtime_bin(runtime="dummy", slots=["other"]) == ([], [])


def test_runtime_bin_without_a_version_warns(dummy: DummyRuntime, tmp_path: Path) -> None:
    dummy.binary = tmp_path / "dummy-cli"
    dummy._version = None
    errors, warnings = doc._check_runtime_bin(runtime="dummy", slots=["a", "b"])
    assert errors == []
    assert len(warnings) == 1 and "can't read its version" in warnings[0] and "'a', 'b'" in warnings[0]


def test_runtime_bin_lookup_that_crashes_is_a_warning(dummy: DummyRuntime, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_hint: Path | None) -> Path | None:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(dummy, "locate", boom)
    errors, warnings = doc._check_runtime_bin(runtime="dummy", slots=["other"])
    assert errors == [] and "kaboom" in warnings[0]


@pytest.mark.parametrize(
    ("signed_in", "kind", "text"),
    [
        (False, "warning", "Dummy CLI isn't signed in for scheduled runs (login: gone)"),
        (None, "note", "Dummy CLI signs in by login (gone); only a run can tell whether that still works"),
        (True, "note", "Dummy CLI signs in by login (gone)"),
    ],
)
def test_runtime_auth(
    dummy: DummyRuntime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, signed_in: bool | None, kind: str, text: str
) -> None:
    from scout.agent_runtimes.base import RuntimeAuth

    dummy.binary = tmp_path / "dummy-cli"
    monkeypatch.setattr(dummy, "auth_status", lambda _b: RuntimeAuth(signed_in=signed_in, mode="login", detail="gone"))
    warnings, notes = doc._check_runtime_auth(runtime="dummy", slots=["other"])
    found = warnings if kind == "warning" else notes
    assert len(found) == 1 and found[0].startswith(text)
    assert (notes if kind == "warning" else warnings) == []


def test_runtime_auth_without_a_binary_says_nothing(dummy: DummyRuntime) -> None:
    """The binary check already reported a missing CLI."""
    assert doc._check_runtime_auth(runtime="dummy", slots=["other"]) == ([], [])


def test_a_crashing_runtime_auth_check_does_not_break_the_doctor(
    vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(**_k: object) -> tuple[list[str], list[str]]:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(doc, "_check_runtime_auth", boom)
    with registered(DummyRuntime("dummy", binary=tmp_path / "dummy-cli")):
        _schedule(vault, ("other", "dummy"))
        report = _doctor(vault, tmp_path / "home")
    assert any("could not check how scheduled runs sign in to dummy: kaboom" in n for n in report.notes)


def test_an_empty_schedule_checks_the_default_runtime(vault: Path, tmp_path: Path) -> None:
    (vault / ".scout-state" / "schedule.yaml").write_text("schema_version: 1\nslots: {}\n", encoding="utf-8")
    (vault / "scout-config.yaml").write_text("agent_runtimes:\n  default: dummy\n", encoding="utf-8")
    with registered(DummyRuntime("dummy", binary=None)):
        report = _doctor(vault, tmp_path / "home")
    dummy = [e for e in report.errors if "Dummy CLI" in e]
    assert len(dummy) == 1 and "slot(s) (none)" in dummy[0] and "`scoutctl agent-runtime show`" in dummy[0]
