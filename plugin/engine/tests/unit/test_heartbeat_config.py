"""#231 — every heartbeat cadence knob is reachable from scout-config.yaml.

`HeartbeatConfig` declared six tunables but `load_config()` wired only the two
off-peak hours. The four that decide how often the heartbeat launches a
session (`min_gap_minutes`, `off_peak_min_gap_minutes`, `dreaming_signal_hours`,
`research_min_gap_hours`) were reachable only by editing engine source, and a
key written for them was dropped without a word. Spec:
docs/superpowers/specs/2026-10-11-run-health-and-headless-updates-design.md §5.3.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from scout.scripts import heartbeat as hb
from scout.scripts.heartbeat import (
    HeartbeatConfig,
    TrackerStats,
    decide,
    load_config,
    load_config_with_issues,
)


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "scout-config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


# ----- the four cadence knobs ---------------------------------------------


def test_nested_heartbeat_block_sets_every_cadence_knob(tmp_path: Path) -> None:
    cfg = load_config(
        _write(
            tmp_path,
            "heartbeat:\n"
            "  min_gap_minutes: 90\n"
            "  off_peak_min_gap_minutes: 300\n"
            "  dreaming_signal_hours: 12\n"
            "  research_min_gap_hours: 48\n"
            "  dirty_tree_triggers: false\n",
        )
    )
    assert cfg.min_gap_minutes == 90
    assert cfg.off_peak_min_gap_minutes == 300
    assert cfg.dreaming_signal_hours == 12
    assert cfg.research_min_gap_hours == 48
    assert cfg.dirty_tree_triggers is False


def test_flat_spellings_of_the_cadence_knobs_keep_working(tmp_path: Path) -> None:
    """The issue's reproduction: `dreaming_signal_hours: 12` at top level used
    to be accepted silently and have no effect."""
    cfg = load_config(_write(tmp_path, "dreaming_signal_hours: 12\nmin_gap_minutes: 30\n"))
    assert cfg.dreaming_signal_hours == 12
    assert cfg.min_gap_minutes == 30


def test_nested_value_wins_over_a_flat_one(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, "heartbeat:\n  dreaming_signal_hours: 8\ndreaming_signal_hours: 12\n"))
    assert cfg.dreaming_signal_hours == 8


def test_a_vault_that_sets_nothing_keeps_every_default(tmp_path: Path) -> None:
    cfg, issues = load_config_with_issues(_write(tmp_path, "user:\n  timezone: ''\nbudget:\n  daily_usd: 50\n"))
    assert cfg == HeartbeatConfig()
    assert issues == []
    assert cfg.dirty_tree_triggers is True


def test_a_key_under_another_section_is_not_a_heartbeat_knob(tmp_path: Path) -> None:
    cfg, issues = load_config_with_issues(_write(tmp_path, "planning:\n  min_gap_minutes: 5\n"))
    assert cfg.min_gap_minutes == hb.DEFAULT_MIN_GAP_MINUTES
    assert issues == []


def test_the_packaged_default_block_matches_the_dataclass() -> None:
    """scout/defaults/scout-config.yaml documents the heartbeat block; its values
    must be HeartbeatConfig()'s, or the documentation lies about the defaults."""
    from importlib.resources import files

    text = (files("scout") / "defaults" / "scout-config.yaml").read_text(encoding="utf-8")
    block = yaml.safe_load(text)["heartbeat"]
    defaults = HeartbeatConfig()
    assert block == {
        "min_gap_minutes": defaults.min_gap_minutes,
        "off_peak_min_gap_minutes": defaults.off_peak_min_gap_minutes,
        "dreaming_signal_hours": defaults.dreaming_signal_hours,
        "research_min_gap_hours": defaults.research_min_gap_hours,
        "dirty_tree_triggers": defaults.dirty_tree_triggers,
    }
    assert block.keys() == set(hb.HEARTBEAT_KEYS)


# ----- validation ---------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "needle"),
    [
        ("heartbeat:\n  dreaming_signal_hours: 0\n", "heartbeat.dreaming_signal_hours: 0 is out of range (1–720)"),
        ("heartbeat:\n  min_gap_minutes: 20000\n", "heartbeat.min_gap_minutes: 20000 is out of range (0–10080)"),
        ("heartbeat:\n  research_min_gap_hours: -1\n", "heartbeat.research_min_gap_hours: -1 is out of range"),
        (
            "heartbeat:\n  off_peak_min_gap_minutes: 2.5\n",
            "heartbeat.off_peak_min_gap_minutes: 2.5 is not a whole number",
        ),
        ("off_peak:\n  start: 25\n", "off_peak.start: 25 is out of range (0–24)"),
        ("off_peak_end: soon\n", "off_peak_end: soon is not a whole number"),
        ("heartbeat:\n  dirty_tree_triggers: maybe\n", "heartbeat.dirty_tree_triggers: maybe is not true or false"),
    ],
)
def test_a_bad_value_keeps_the_default_and_says_why(tmp_path: Path, line: str, needle: str) -> None:
    cfg, issues = load_config_with_issues(_write(tmp_path, line))
    assert cfg == HeartbeatConfig()
    assert len(issues) == 1
    assert needle in issues[0]
    assert issues[0].startswith("scout-config.yaml: ")
    assert "using the default" in issues[0]


def test_an_unknown_key_under_heartbeat_is_reported_not_dropped(tmp_path: Path) -> None:
    cfg, issues = load_config_with_issues(
        _write(tmp_path, "heartbeat:\n  dreaming_signal_hour: 12\n  min_gap_minutes: 60\n")
    )
    assert cfg.min_gap_minutes == 60
    assert cfg.dreaming_signal_hours == hb.DEFAULT_DREAMING_SIGNAL_HOURS
    assert len(issues) == 1
    assert "heartbeat.dreaming_signal_hour: unknown key, ignored" in issues[0]
    assert "dreaming_signal_hours" in issues[0]  # the known keys are listed


def test_a_nested_mapping_under_heartbeat_is_an_unknown_key(tmp_path: Path) -> None:
    _, issues = load_config_with_issues(_write(tmp_path, "heartbeat:\n  gaps:\n    min: 5\n"))
    assert len(issues) == 1
    assert "heartbeat.gaps: unknown key" in issues[0]


def test_an_inline_heartbeat_mapping_is_reported(tmp_path: Path) -> None:
    """The hot-path reader takes block style only; a flow mapping would
    otherwise be dropped as silently as the original bug."""
    cfg, issues = load_config_with_issues(_write(tmp_path, "heartbeat: {dreaming_signal_hours: 12}\n"))
    assert cfg == HeartbeatConfig()
    assert len(issues) == 1
    assert "heartbeat: write one key per line" in issues[0]


def test_values_are_read_in_bool_spellings(tmp_path: Path) -> None:
    for raw, expected in (("yes", True), ("No", False), ("on", True), ("off", False), ("TRUE", True)):
        cfg = load_config(_write(tmp_path, f"heartbeat:\n  dirty_tree_triggers: {raw}\n"))
        assert cfg.dirty_tree_triggers is expected, raw


# ----- decide(): the dirty-tree operand and operand naming ------------------


def _executable(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.write_text("#!/bin/sh\nexit 0\n")
    p.chmod(0o755)
    return p


def _args(tmp_path: Path, **over: object) -> dict:
    args = {
        "stats": TrackerStats(minutes_since_last_session=300, hours_since_dreaming=5, hours_since_research=2),
        "config": HeartbeatConfig(),
        "now_hour": 14,
        "budget_ok": True,
        "session_already_running": False,
        "uncommitted_vault_changes": False,
        "research_queue_open": False,
        "research_runner": _executable(tmp_path, "run-research.sh"),
        "dreaming_runner": _executable(tmp_path, "run-dreaming.sh"),
    }
    args.update(over)
    return args


def test_dreaming_signal_hours_from_config_gates_the_launch(tmp_path: Path) -> None:
    decision = decide(**_args(tmp_path, config=HeartbeatConfig(dreaming_signal_hours=12)))
    assert decision.action == "skip"
    assert decision.reason == "no_pending_work_signals"


def test_the_reason_names_the_signal_hours_operand(tmp_path: Path) -> None:
    decision = decide(**_args(tmp_path))
    assert decision.reason == "dreaming_5h_since_last, trigger=signal_hours"


def test_the_reason_names_the_dirty_tree_operand(tmp_path: Path) -> None:
    stats = TrackerStats(minutes_since_last_session=300, hours_since_dreaming=2, hours_since_research=2)
    decision = decide(**_args(tmp_path, stats=stats, uncommitted_vault_changes=True))
    assert decision.action == "launch"
    assert decision.reason == "dreaming_2h_since_last, trigger=dirty_tree"


def test_the_reason_names_both_operands_when_both_fire(tmp_path: Path) -> None:
    decision = decide(**_args(tmp_path, uncommitted_vault_changes=True))
    assert decision.reason.endswith("trigger=signal_hours+dirty_tree")


def test_the_research_reason_names_the_operand_too(tmp_path: Path) -> None:
    stats = TrackerStats(minutes_since_last_session=300, hours_since_dreaming=5, hours_since_research=30)
    decision = decide(**_args(tmp_path, stats=stats, research_queue_open=True))
    assert decision.session_type == "research"
    assert decision.reason.endswith(", trigger=signal_hours")


def test_dirty_tree_triggers_off_ignores_uncommitted_changes(tmp_path: Path) -> None:
    stats = TrackerStats(minutes_since_last_session=300, hours_since_dreaming=2, hours_since_research=2)
    decision = decide(
        **_args(
            tmp_path,
            stats=stats,
            uncommitted_vault_changes=True,
            config=HeartbeatConfig(dirty_tree_triggers=False),
        )
    )
    assert decision.action == "skip"
    assert decision.reason == "no_pending_work_signals"


# ----- the dirty-tree probe ignores Scout's own machine state -------------


@pytest.fixture
def git_vault(tmp_path: Path) -> Path:
    if shutil.which("git") is None:  # pragma: no cover - git is on every CI image
        pytest.skip("git not installed")
    vault = tmp_path / "vault"
    vault.mkdir()
    subprocess.run(["git", "init", "-q", str(vault)], check=True)
    return vault


@pytest.mark.parametrize("state_dir", [".scout-state", ".scout-logs", ".scout-cache"])
def test_machine_state_writes_do_not_read_as_pending_work(git_vault: Path, state_dir: str) -> None:
    (git_vault / state_dir).mkdir()
    (git_vault / state_dir / "last-fire.json").write_text("{}\n")
    assert hb.vault_has_uncommitted_changes(git_vault) is False


def test_a_user_edit_still_reads_as_pending_work(git_vault: Path) -> None:
    (git_vault / ".scout-state").mkdir()
    (git_vault / ".scout-state" / "last-fire.json").write_text("{}\n")
    (git_vault / "notes.md").write_text("x\n")
    assert hb.vault_has_uncommitted_changes(git_vault) is True


# ----- run(): config issues, the probe toggle, zone, trigger env ---------


def _now() -> datetime:
    return datetime(2026, 5, 28, 14, 0, tzinfo=UTC)


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    v = tmp_path / "Scout"
    (v / ".scout-logs").mkdir(parents=True)
    _executable(v, "run-dreaming.sh")
    monkeypatch.setattr(hb, "scout_session_running", lambda *a, **k: False)
    monkeypatch.setattr(hb, "run_budget_check", lambda *a, **k: 0)
    return v


def _log(vault: Path) -> str:
    return (vault / ".scout-logs" / "heartbeat.log").read_text(encoding="utf-8")


def test_run_logs_each_config_issue(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hb, "vault_has_uncommitted_changes", lambda *a, **k: False)
    (vault / "scout-config.yaml").write_text("heartbeat:\n  dreaming_signal_hour: 12\n", encoding="utf-8")
    hb.run(data_dir=vault, dry_run=True, now=_now())
    assert "config: scout-config.yaml: heartbeat.dreaming_signal_hour: unknown key, ignored" in _log(vault)


def test_run_honours_dreaming_signal_hours_from_the_vault(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hb, "vault_has_uncommitted_changes", lambda *a, **k: False)
    (vault / "scout-config.yaml").write_text("heartbeat:\n  dreaming_signal_hours: 12\n", encoding="utf-8")
    five_hours_ago = (_now() - timedelta(hours=5)).isoformat().replace("+00:00", "Z")
    (vault / ".scout-logs" / "usage-tracker.jsonl").write_text(
        json.dumps({"ts": five_hours_ago, "type": "dreaming"}) + "\n", encoding="utf-8"
    )
    assert hb.run(data_dir=vault, now=_now()) == hb.EXIT_SKIPPED
    assert "skipped: no_pending_work_signals" in _log(vault)


def test_run_skips_the_git_probe_when_dirty_tree_triggers_is_off(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_a: object, **_k: object) -> bool:
        raise AssertionError("the dirty-tree probe must not run when it cannot trigger")

    monkeypatch.setattr(hb, "vault_has_uncommitted_changes", fail)
    (vault / "scout-config.yaml").write_text("heartbeat:\n  dirty_tree_triggers: false\n", encoding="utf-8")
    assert hb.run(data_dir=vault, dry_run=True, now=_now()) == hb.EXIT_LAUNCHED


def test_run_reads_the_off_peak_hour_in_the_configured_zone(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """14:00 UTC is 23:00 in Tokyo: inside the default 23→6 window, so a
    session 180 min ago is under the 240-min off-peak gap."""
    monkeypatch.setattr(hb, "vault_has_uncommitted_changes", lambda *a, **k: False)
    (vault / "scout-config.yaml").write_text("user:\n  timezone: Asia/Tokyo\n", encoding="utf-8")
    three_hours_ago = (_now() - timedelta(minutes=180)).isoformat().replace("+00:00", "Z")
    (vault / ".scout-logs" / "usage-tracker.jsonl").write_text(
        json.dumps({"ts": three_hours_ago, "type": "dreaming"}) + "\n", encoding="utf-8"
    )
    assert hb.run(data_dir=vault, now=_now()) == hb.EXIT_SKIPPED
    assert "off_peak_conservatism" in _log(vault)


def test_launch_runner_marks_the_run_as_a_heartbeat_trigger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Spec §5.3: the ledger counts heartbeat launches by SCOUT_TRIGGER."""
    seen: dict[str, object] = {}

    class FakePopen:
        pid = 1

        def __init__(self, argv: list[str], **kwargs: object) -> None:
            seen.update(kwargs)

    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    monkeypatch.delenv("SCOUT_TRIGGER", raising=False)
    hb.launch_runner(_executable(tmp_path, "run.sh"), vault=tmp_path, log_path=tmp_path / "hb.log")
    env = seen["env"]
    assert isinstance(env, dict)
    assert env["SCOUT_TRIGGER"] == "heartbeat"


# ----- doctor -------------------------------------------------------------


def test_doctor_warns_about_a_heartbeat_config_issue(tmp_path: Path) -> None:
    from scout.scripts.bootstrap_doctor import _check_heartbeat_config

    (tmp_path / "scout-config.yaml").write_text("heartbeat:\n  dreaming_signal_hours: 0\n", encoding="utf-8")
    warnings = _check_heartbeat_config(vault=tmp_path)
    assert len(warnings) == 1
    assert "heartbeat.dreaming_signal_hours: 0 is out of range" in warnings[0]


def test_doctor_is_quiet_for_a_clean_heartbeat_config(tmp_path: Path) -> None:
    from scout.scripts.bootstrap_doctor import _check_heartbeat_config

    (tmp_path / "scout-config.yaml").write_text("heartbeat:\n  dreaming_signal_hours: 8\n", encoding="utf-8")
    assert _check_heartbeat_config(vault=tmp_path) == []


def test_run_doctor_carries_the_heartbeat_warning(tmp_path: Path) -> None:
    from scout.scripts.bootstrap_doctor import run_doctor

    (tmp_path / "scout-config.yaml").write_text("heartbeat:\n  min_gap: 5\n", encoding="utf-8")
    report = run_doctor(vault=tmp_path, check_jobs=False)
    assert any("heartbeat.min_gap: unknown key" in w for w in report.warnings)
