from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scout import update_outcomes as uo
from scout.scripts import self_update_run as sur
from scout.scripts.self_update_run import Proc
from tests.unit.self_update_fakes import FakeWorld, forbidden, make_deps, pointer


def _fail(code: int = 1, err: str = "boom") -> Proc:
    return Proc(code, "", err)


def test_happy_path_runs_every_step_in_order(tmp_path):
    deps, world, notified = make_deps(tmp_path)
    attempt = sur.run_update(deps, scheduled=True)
    row = attempt.row
    assert row.result == "applied" and row.step == "verify" and row.doctor == "green"
    assert row.from_version == "0.16.0" and row.target == "0.17.0" and row.managed_by == "install.sh"
    assert row.log == "self-update-2026-10-12.log"
    assert attempt.settled
    assert world.keys() == [
        "marketplace update",
        "plugin update",
        "plugin list",
        "install-venv.sh",
        "version",
        "bootstrap auto",
        "doctor",
    ]
    assert world.calls[0] == ["/bin/claude-fake", "plugin", "marketplace", "update", "scout-plugin"]
    assert world.calls[1] == ["/bin/claude-fake", "plugin", "update", "scout@scout-plugin", "--scope", "user"]
    new_scoutctl = str(world.new_root / ".venv" / "bin" / "scoutctl")
    auto = world.calls[5]
    assert auto[:2] == [new_scoutctl, "bootstrap"]
    assert auto[2:] == [
        "auto",
        "--no-interactive",
        "--yes",
        "--json",
        "--managed-by",
        "install.sh",
        "--claude-bin",
        "/bin/claude-fake",
    ]
    assert world.envs[5]["SCOUT_DATA_DIR"] == str(deps.vault)
    assert world.calls[6] == [new_scoutctl, "bootstrap", "doctor", "--json"]
    assert [r.result for r in uo.read(deps.vault)] == ["applied"]
    assert notified == [(row, [])]


def test_venv_env_never_carries_scout_venv_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_VENV_DIR", "/live/venv")
    deps, world, _ = make_deps(tmp_path)
    sur.run_update(deps, scheduled=True)
    venv_env = world.envs[world.keys().index("install-venv.sh")]
    assert "SCOUT_VENV_DIR" not in venv_env
    assert world.calls[3] == ["bash", str(world.new_root / "scripts" / "install-venv.sh")]


@pytest.mark.parametrize(
    ("key", "override"),
    [
        ("marketplace update", _fail()),
        ("plugin update", _fail(2)),
        ("plugin list", _fail()),
        ("plugin list", Proc(0, "not json", "")),
        ("plugin list", Proc(0, json.dumps([{"id": "other@x", "installPath": "/x"}]), "")),
    ],
)
def test_fetch_failure_leaves_the_old_engine(tmp_path, key, override):
    deps, world, _ = make_deps(tmp_path)
    world.overrides[key] = override
    row = sur.run_update(deps, scheduled=True).row
    assert row.result == "failed" and row.step == "fetch"
    assert "install-venv.sh" not in world.keys() and "bootstrap auto" not in world.keys()


def test_fetch_version_mismatch(tmp_path):
    deps, world, _ = make_deps(tmp_path)
    world.installed_version = "0.16.5"
    row = sur.run_update(deps, scheduled=True).row
    assert row.result == "failed" and row.step == "fetch" and "0.16.5" in row.detail


def test_fetch_missing_plugin_json(tmp_path):
    deps, world, _ = make_deps(tmp_path)
    world.overrides["plugin update"] = Proc(0, "", "")  # registry names a root with no plugin.json
    row = sur.run_update(deps, scheduled=True).row
    assert row.result == "failed" and row.step == "fetch" and "unknown version" in row.detail


def test_fetch_refuses_the_live_root(tmp_path):
    world = FakeWorld(tmp_path)
    deps, _, _ = make_deps(tmp_path, ptr=pointer(root=str(world.new_root)), world=world)
    row = sur.run_update(deps, scheduled=True).row
    assert row.result == "failed" and row.step == "fetch" and "live engine root" in row.detail
    assert "install-venv.sh" not in world.keys()


def test_runner_oserror_is_a_failure_of_that_step(tmp_path):
    deps, world, _ = make_deps(tmp_path)

    def missing(argv, env):
        raise FileNotFoundError(argv[0])

    world.overrides["marketplace update"] = missing
    row = sur.run_update(deps, scheduled=True).row
    assert row.result == "failed" and row.step == "fetch" and "FileNotFoundError" in row.detail


def test_runner_timeout_is_a_failure_of_that_step(tmp_path):
    deps, world, _ = make_deps(tmp_path)

    def slow(argv, env):
        raise subprocess.TimeoutExpired(argv, 900)

    world.overrides["install-venv.sh"] = slow
    row = sur.run_update(deps, scheduled=True).row
    assert row.result == "failed" and row.step == "venv"


@pytest.mark.parametrize(
    "override",
    [_fail(1, "uv exploded"), None],
)
def test_venv_failure_leaves_the_old_engine(tmp_path, override):
    deps, world, _ = make_deps(tmp_path)
    if override is None:
        world.overrides["version"] = Proc(0, "0.16.0\n", "")
    else:
        world.overrides["install-venv.sh"] = override
    row = sur.run_update(deps, scheduled=True).row
    assert row.result == "failed" and row.step == "venv"
    assert "bootstrap auto" not in world.keys()


def test_venv_version_empty(tmp_path):
    deps, world, _ = make_deps(tmp_path)
    world.overrides["version"] = Proc(1, "", "Traceback")
    row = sur.run_update(deps, scheduled=True).row
    assert row.step == "venv" and "no version" in row.detail


def test_lock_busy_at_the_vault_step_defers(tmp_path):
    deps, world, _ = make_deps(tmp_path)
    world.auto_result = {
        "action": "refused",
        "error": "lock /v/.scout-logs/.scout-session.lock held by live PID 4242",
        "mutated": False,
    }
    attempt = sur.run_update(deps, scheduled=True)
    assert attempt.row.result == "deferred" and attempt.row.step == "vault" and not attempt.settled
    assert "doctor" not in world.keys()


@pytest.mark.parametrize(
    "result",
    [
        {"action": "refused", "error": "held by live PID 1", "mutated": True},
        {"action": "refused", "error": "unresolved proposed-merge sidecar(s)", "mutated": False},
        {"action": "refused", "error": None, "reason": "weird", "mutated": True},
    ],
)
def test_other_vault_refusals_fail(tmp_path, result):
    deps, world, _ = make_deps(tmp_path)
    world.auto_result = result
    row = sur.run_update(deps, scheduled=True).row
    assert row.result == "failed" and row.step == "vault"


def test_vault_step_without_json_fails(tmp_path):
    deps, world, _ = make_deps(tmp_path)
    world.overrides["bootstrap auto"] = Proc(70, "", "Traceback (most recent call last)")
    row = sur.run_update(deps, scheduled=True).row
    assert row.result == "failed" and row.step == "vault" and "exited 70" in row.detail


def test_vault_json_after_progress_lines_is_parsed(tmp_path):
    deps, world, _ = make_deps(tmp_path)
    payload = json.dumps({"action": "upgrade", "conflicts": [], "mutated": False})
    world.overrides["bootstrap auto"] = Proc(0, "rendering...\n" + payload + "\n", "")
    assert sur.run_update(deps, scheduled=True).row.result == "applied"


def test_conflicts_give_applied_with_sidecars(tmp_path):
    deps, world, _ = make_deps(tmp_path)
    world.auto_result = {"conflicts": ["SKILL.md.proposed-merge"]}
    world.doctor_result = {"severity": "yellow", "errors": [], "warnings": ["sidecar pending"]}
    row = sur.run_update(deps, scheduled=True).row
    assert row.result == "applied_with_sidecars" and row.sidecars == ("SKILL.md.proposed-merge",)
    assert row.doctor == "yellow"


@pytest.mark.parametrize("doctor", [{"severity": "red", "errors": ["plist missing"]}, None])
def test_red_or_unreadable_verify_fails(tmp_path, doctor):
    deps, world, _ = make_deps(tmp_path)
    if doctor is None:
        world.overrides["doctor"] = Proc(1, "garbage", "")
    else:
        world.doctor_result = doctor
    row = sur.run_update(deps, scheduled=True).row
    assert row.result == "failed" and row.step == "verify"
    if doctor:
        assert row.doctor == "red" and "plist missing" in row.detail
    else:
        assert row.doctor == "unknown"


def test_red_doctor_before_the_update_holds(tmp_path):
    deps, world, _ = make_deps(tmp_path, doctor_severity=lambda: "red")
    attempt = sur.run_update(deps, scheduled=True)
    assert attempt.row.result == "held" and attempt.row.doctor == "red" and attempt.settled
    assert world.calls == []


def test_blocking_sidecar_holds(tmp_path):
    deps, world, _ = make_deps(tmp_path, blocking_sidecars=lambda: ["scripts/parser.py.proposed-merge"])
    row = sur.run_update(deps, scheduled=True).row
    assert row.result == "held" and "parser.py" in row.detail and world.calls == []


def test_dry_run_runs_and_writes_nothing(tmp_path):
    lines = []
    deps, world, notified = make_deps(tmp_path, run=forbidden("run"), say=lines.append)
    attempt = sur.run_update(deps, scheduled=False, dry_run=True)
    assert attempt.row is None and attempt.reason == "dry run" and not attempt.settled
    assert uo.read(deps.vault) == [] and notified == []
    assert any("would update 0.16.0 -> 0.17.0" in line for line in lines)


def test_dry_run_of_a_stop_writes_nothing(tmp_path):
    deps, _, notified = make_deps(tmp_path, ptr=pointer("scout-app"))
    attempt = sur.run_update(deps, scheduled=False, dry_run=True)
    assert attempt.row.result == "not_eligible" and uo.read(deps.vault) == [] and notified == []


def test_long_detail_is_clipped(tmp_path):
    deps, world, _ = make_deps(tmp_path)
    world.overrides["marketplace update"] = _fail(1, "x" * 2000)
    row = sur.run_update(deps, scheduled=True).row
    assert len(row.detail) <= 300 and row.detail.endswith("…")


def test_output_is_echoed_to_the_log(tmp_path):
    lines = []
    deps, world, _ = make_deps(tmp_path, say=lines.append)
    world.overrides["marketplace update"] = Proc(0, "updated\n", "warn\n")
    sur.run_update(deps, scheduled=True)
    assert "updated" in lines and "warn" in lines
    assert lines[-1].startswith("applied (verify) 0.16.0 -> 0.17.0")


# --- resolve_install_path -------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        [{"id": "scout@scout-plugin", "installPath": "/c/scout/0.17.0"}],
        {"plugins": {"scout@scout-plugin": [{"id": "scout@scout-plugin", "installPath": "/c/scout/0.17.0"}]}},
        {
            "plugins": {
                "x": "bad",
                "scout@scout-plugin": [7, {"id": "scout@scout-plugin", "installPath": "/c/scout/0.17.0"}],
            }
        },
    ],
)
def test_resolve_install_path_both_shapes(payload):
    assert sur.resolve_install_path(json.dumps(payload)) == Path("/c/scout/0.17.0")


@pytest.mark.parametrize("text", ["nope", "{}", '{"plugins": []}', "[]", '[{"id": "scout@scout-plugin"}]', "3"])
def test_resolve_install_path_none(text):
    assert sur.resolve_install_path(text) is None


# --- real wiring helpers ----------------------------------------------------------------------


def test_subprocess_runner(tmp_path):
    proc = sur._subprocess_runner(["sh", "-c", "echo out; echo err >&2; exit 3"], None)
    assert proc == Proc(3, "out\n", "err\n")


def test_claude_bin_prefers_the_persisted_value():
    assert sur._claude_bin(lambda: {"connectors": {"inputs": {"claude_bin": "/opt/claude"}}}) == "/opt/claude"


def test_claude_bin_falls_back_to_discovery(monkeypatch):
    from scout.scripts import bootstrap

    monkeypatch.setattr(bootstrap, "resolve_claude_bin", lambda explicit="", home=None: f"found:{explicit}")
    assert sur._claude_bin(lambda: {"connectors": "bad"}) == "found:"
    assert sur._claude_bin(lambda: {"connectors": {"inputs": {"claude_bin": 3}}}) == "found:"


def test_slot_due_within(tmp_path):
    from datetime import timedelta

    vault = tmp_path / "v"
    assert sur._slot_due_within(vault, timedelta(minutes=45)) is False  # no schedule
    (vault / ".scout-state").mkdir(parents=True)
    (vault / ".scout-state" / "schedule.yaml").write_text(": : not yaml [")
    assert sur._slot_due_within(vault, timedelta(minutes=45)) is False
    import shutil

    from scout import schedule as sched_mod

    default = Path(sched_mod.__file__).parent / "defaults" / "schedule.yaml"
    shutil.copy(default, vault / ".scout-state" / "schedule.yaml")
    # A week's window always contains the default schedule's next fire.
    assert sur._slot_due_within(vault, timedelta(hours=24 * 7)) is True


def test_in_off_peak_reads_the_vault_config(tmp_path, monkeypatch):
    from datetime import datetime

    from scout import config as scout_config

    vault = tmp_path / "v"
    vault.mkdir()
    (vault / "scout-config.yaml").write_text("off_peak:\n  start: 22\n  end: 5\n")
    monkeypatch.setattr(scout_config, "now", lambda data_dir=None: datetime(2026, 10, 12, 23, 0))
    assert sur._in_off_peak(vault) is True
    monkeypatch.setattr(scout_config, "now", lambda data_dir=None: datetime(2026, 10, 12, 12, 0))
    assert sur._in_off_peak(vault) is False


def test_slot_due_within_survives_a_bad_zone(tmp_path, monkeypatch):
    import shutil
    from datetime import timedelta

    from scout import schedule as sched_mod
    from scout.scripts import schedule_tick

    vault = tmp_path / "v"
    (vault / ".scout-state").mkdir(parents=True)
    shutil.copy(
        Path(sched_mod.__file__).parent / "defaults" / "schedule.yaml", vault / ".scout-state" / "schedule.yaml"
    )
    monkeypatch.setattr(schedule_tick, "_local_tz_name", lambda: "Not/AZone")
    assert sur._slot_due_within(vault, timedelta(hours=24 * 7)) is True


def test_slot_lookahead_and_off_peak_each_read_the_clock_their_scheduler_uses(tmp_path, monkeypatch):
    """#449: the two gates read different zones on purpose, and must keep doing so.

    Slots fire in `schedule tick`'s zone ($TZ, then the host), so the lookahead asks for
    that zone; the off-peak window is the heartbeat's, in the vault's zone. With a vault
    override that differs from the host, a lookahead in the vault's zone would mispredict
    every slot.
    """
    import shutil
    from datetime import timedelta

    from scout import config as scout_config
    from scout import schedule as sched_mod
    from scout.scripts import schedule_tick

    vault = tmp_path / "v"
    (vault / ".scout-state").mkdir(parents=True)
    shutil.copy(
        Path(sched_mod.__file__).parent / "defaults" / "schedule.yaml", vault / ".scout-state" / "schedule.yaml"
    )
    (vault / "scout-config.yaml").write_text("user:\n  timezone: Asia/Tokyo\n")
    monkeypatch.setattr(schedule_tick, "_local_tz_name", lambda: "America/Chicago")
    seen: list[str] = []
    monkeypatch.setattr(sched_mod, "next_fires", lambda sched, now, window_hours: seen.append(now.tzinfo.key) or [])
    sur._slot_due_within(vault, timedelta(minutes=45))
    assert seen == ["America/Chicago"]

    seen_dirs: list[Path | None] = []

    def fake_now(data_dir=None):
        from datetime import datetime

        seen_dirs.append(data_dir)
        return datetime(2026, 10, 12, 12, 0)

    monkeypatch.setattr(scout_config, "now", fake_now)
    sur._in_off_peak(vault)
    assert seen_dirs == [vault]  # the vault's zone, as heartbeat.decide uses
    assert scout_config.resolve_timezone(vault).key == "Asia/Tokyo"


def test_unparseable_target_is_up_to_date(tmp_path):
    from scout.scripts.self_update import UpdateStatus

    deps, world, _ = make_deps(tmp_path, check=lambda: UpdateStatus("0.16.0", "next", True))
    assert sur.run_update(deps, scheduled=True).row.result == "up_to_date"
    assert world.calls == []


def test_default_deps_wires_read_only_probes(tmp_path, capsys):
    """The real Deps, exercised only through its read-only probes (no network, no subprocess)."""
    import sys

    from scout.scripts import self_update

    vault = tmp_path / "vault"
    vault.mkdir()
    home = tmp_path / "home"
    deps = sur.default_deps(vault, home=home, log_name="x.log")
    assert deps.vault == vault and deps.log_name == "x.log"
    assert deps.read_pointer() is None
    assert deps.interpreter == str(Path(sys.executable).absolute())
    assert isinstance(deps.load_config(), dict)
    assert deps.check is self_update.check and deps.fetch_release is self_update.fetch_release
    assert deps.run is sur._subprocess_runner
    assert deps.lock_busy() is False
    assert deps.slot_due_within(sur.SLOT_LOOKAHEAD) is False
    assert deps.in_off_peak() in (True, False)
    assert deps.doctor_severity() == "red"  # an empty folder is not a set-up vault
    assert deps.blocking_sidecars() == []
    assert isinstance(deps.claude_bin(), str)
    assert deps.now().tzinfo is not None
    deps.say("hello")
    assert capsys.readouterr().out == "hello\n"
    sur.default_deps(vault, home=home, quiet=True).say("silent")
    assert capsys.readouterr().out == ""
