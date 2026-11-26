"""Unit tests for the scout-tz.sh resolver template (cat-1 install + runtime behavior).

scout-tz.sh is the runtime timezone resolver the assembled brain files call as
``scout/scripts/scout-tz.sh date FMT`` and the other cat-1 scripts call via
``TZ="$(scout/scripts/scout-tz.sh)"``. These tests cover
both halves: bootstrap writes it (executable, fully rendered), and the installed
script actually resolves/validates/falls back the way its contract says.
"""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import pytest
import yaml

from scout import layout
from scout.scripts.bootstrap import BootstrapConfig, install, managed_renders

_PLUGIN_ROOT = Path(__file__).parent.parent.parent.parent  # repo root


def _config(vault: Path) -> BootstrapConfig:
    return BootstrapConfig(
        vault=vault,
        plugin_root=_PLUGIN_ROOT,
        instance_name="TestScout",
        instance_name_lower="testscout",
        user_name="Test User",
        user_email="test@example.com",
        timezone="America/New_York",
        platform="macos",
        plugin_version="0.4.0",
        enabled_connectors=set(),
        connector_inputs={},
        skip_jobs=True,
    )


@pytest.fixture()
def installed_vault(tmp_path) -> Path:
    vault = tmp_path / "Scout"
    install(_config(vault))
    return vault


def _run(script: Path, *args: str, env_overrides: dict[str, str] | None = None):
    import os

    env = dict(os.environ)
    env.update(env_overrides or {})
    return subprocess.run(
        ["bash", str(script), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def test_install_writes_scout_tz_executable(installed_vault):
    script = installed_vault / layout.script_path("scout-tz.sh")
    assert script.exists(), "cat-1 install must write scout/scripts/scout-tz.sh"
    assert script.stat().st_mode & stat.S_IXUSR, "scout-tz.sh must be executable"
    text = script.read_text(encoding="utf-8")
    assert "{{" not in text, "template vars must be fully rendered"
    assert "resolve_tz" in text


def test_scout_tz_reads_configured_timezone(installed_vault, tmp_path):
    script = installed_vault / layout.script_path("scout-tz.sh")
    cfg = tmp_path / "travel-config.yaml"
    cfg.write_text('timezone: "Europe/Prague"  # traveling\n', encoding="utf-8")
    result = _run(script, env_overrides={"SCOUT_CONFIG": str(cfg)})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "Europe/Prague"


def test_scout_tz_falls_back_on_invalid_zone(installed_vault, tmp_path):
    script = installed_vault / layout.script_path("scout-tz.sh")
    cfg = tmp_path / "bogus-config.yaml"
    cfg.write_text("timezone: Mars/Olympus_Mons\n", encoding="utf-8")
    result = _run(script, env_overrides={"SCOUT_CONFIG": str(cfg)})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "America/New_York"
    assert "falling back" in result.stderr


def test_scout_tz_date_runs_date_in_the_resolved_zone(installed_vault, tmp_path):
    """`scripts/scout-tz.sh date FMT` is the brain's call site: no command substitution, which a
    session run on an allowlist (dontAsk) is always denied, whatever its rules say."""
    script = installed_vault / layout.script_path("scout-tz.sh")
    cfg = _write_prague_config(tmp_path)
    result = _run(script, "date", "+%Z|%Y", env_overrides={"SCOUT_CONFIG": cfg, "TZ": "UTC"})
    assert result.returncode == 0, result.stderr
    zone, year = result.stdout.strip().split("|")
    assert zone in ("CET", "CEST") and year.isdigit()


_SECRET = "SCOUT-TZ-CANARY-7f3a"


@pytest.mark.parametrize(
    "args",
    [
        ("date", "-f", "{f}"),  # GNU date echoes every line of FILE: a read no KB gate sees
        ("date", "-r", "{f}"),  # FILE's mtime
        ("date", "-j", "-f", "%Y", "2026"),  # BSD parse mode
        ("date", "-s", "2026-01-01"),  # sets the clock
        ("date", "+%A", "+%b"),  # two operands
        ("date", "+%A", "%b"),  # an unquoted '+%A %b' arrives as two words
        ("date",),  # no format
        ("date", "%A"),  # a format without its +
        ("touch", "{f}.ran"),  # any other command
        ("Europe/Prague",),  # any argument at all on the zone-name path
    ],
    ids=["-f", "-r", "-j-f", "-s", "two-operands", "unquoted-format", "no-format", "no-plus", "touch", "stray-arg"],
)
def test_scout_tz_refuses_everything_but_one_format(installed_vault, tmp_path, args):
    """An allowlist admits every `scripts/*` call, so the helper runs `date +FORMAT` and nothing else: no
    option of date's (which read files or set the clock), no other command. A refusal exits 2 with an empty
    stdout, so a caller can't mistake it for a date or a zone."""
    script = installed_vault / layout.script_path("scout-tz.sh")
    secret = tmp_path / "secret.txt"
    secret.write_text(f"{_SECRET}\n", encoding="utf-8")
    argv = [a.replace("{f}", str(secret)) for a in args]
    result = _run(script, *argv, env_overrides={"SCOUT_CONFIG": _write_prague_config(tmp_path)})
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert result.stdout == ""
    assert _SECRET not in result.stderr
    assert "usage: scout-tz.sh" in result.stderr
    assert not (tmp_path / "secret.txt.ran").exists()


def test_doctor_is_quiet_about_the_installed_scout_tz(installed_vault):
    from scout.scripts.bootstrap_doctor import _check_scout_tz

    assert _check_scout_tz(vault=installed_vault) == []


def test_doctor_flags_a_scout_tz_that_prints_a_zone_for_date(installed_vault):
    """An older script ignores its arguments and prints the zone, with exit 0: every date a run wrote through
    `scripts/scout-tz.sh date …` would be a zone name. Only the doctor can say so."""
    from scout.scripts.bootstrap_doctor import _check_scout_tz

    script = installed_vault / layout.script_path("scout-tz.sh")
    script.write_text("#!/bin/bash\necho Europe/Prague\n", encoding="utf-8")
    (warning,) = _check_scout_tz(vault=installed_vault)
    assert "printed 'Europe/Prague', not a year" in warning and "/scout-update" in warning


def test_scout_tz_self_test_passes_in_installed_vault(installed_vault):
    # The install renders scout-config.yaml with timezone America/New_York, so
    # the script-relative config fallback resolves inside the tmp vault and the
    # shipped self-test's assertions all hold.
    script = installed_vault / layout.script_path("scout-tz.sh")
    result = _run(script, "--self-test")
    assert result.returncode == 0, f"self-test failed:\n{result.stdout}\n{result.stderr}"
    assert "FAIL" not in result.stdout
    assert "pass ---" in result.stdout


def test_scout_tz_follows_the_host_when_unconfigured(installed_vault, tmp_path):
    script = installed_vault / layout.script_path("scout-tz.sh")
    cfg = tmp_path / "no-zone-config.yaml"
    cfg.write_text("platform: macos\n", encoding="utf-8")
    target = tmp_path / "tzdb" / "zoneinfo" / "Asia" / "Tokyo"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"")
    link = tmp_path / "localtime"
    link.symlink_to(target)
    result = _run(script, env_overrides={"SCOUT_CONFIG": str(cfg), "SCOUT_LOCALTIME": str(link)})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "Asia/Tokyo"


# ----- bootstrap never pins a zone the user did not ask for ------------------


def test_install_without_a_timezone_writes_no_timezone_key(tmp_path):
    vault = tmp_path / "Scout"
    cfg = _config(vault)
    cfg.timezone = ""
    install(cfg)
    data = yaml.safe_load((vault / "scout-config.yaml").read_text(encoding="utf-8"))
    assert "timezone" not in data, "an unset zone must follow the host, not be stamped"


def test_install_with_a_timezone_records_the_override(tmp_path):
    vault = tmp_path / "Scout"
    cfg = _config(vault)
    cfg.timezone = "Europe/Prague"
    install(cfg)
    data = yaml.safe_load((vault / "scout-config.yaml").read_text(encoding="utf-8"))
    assert data["timezone"] == "Europe/Prague"


def _cli_install(target: Path, monkeypatch, *extra: str):
    from typer.testing import CliRunner

    from scout import cli

    monkeypatch.setenv("SCOUT_DATA_DIR", str(target))
    argv = ["bootstrap", "install", "--user-name", "Alex", "--user-email", "alex@example.com"]
    result = CliRunner().invoke(cli.app, [*argv, "--no-jobs", *extra])
    assert result.exit_code in (0, 1), result.output  # the doctor's verdict
    return result


def _cli_upgrade():
    from typer.testing import CliRunner

    from scout import cli

    result = CliRunner().invoke(cli.app, ["bootstrap", "upgrade", "--no-jobs"])
    assert result.exit_code in (0, 1), result.output
    return result


def test_cli_install_follows_the_host_and_upgrade_keeps_it_so(tmp_path, monkeypatch):
    """The regression this guards: upgrade read a missing ``timezone`` as
    America/New_York and stamped it back, so deleting the key to follow the
    host lasted only until the next upgrade."""
    vault = tmp_path / "FreshScout"
    _cli_install(vault, monkeypatch)
    config_path = vault / "scout-config.yaml"
    assert "timezone" not in yaml.safe_load(config_path.read_text(encoding="utf-8"))

    _cli_upgrade()
    assert "timezone" not in yaml.safe_load(config_path.read_text(encoding="utf-8"))


_FOLLOW_THE_HOST_NOTE = "# timezone: unset on purpose — follow this computer's own timezone\n"


@pytest.mark.parametrize(
    "command",
    [
        ["bootstrap", "upgrade", "--no-jobs"],
        ["bootstrap", "auto", "--yes", "--no-jobs"],  # what the desktop app runs
    ],
    ids=["upgrade", "auto"],
)
def test_an_upgrade_does_not_restore_a_deleted_timezone(tmp_path, monkeypatch, command):
    """The regression as it happened: a vault installed with the old
    America/New_York default deleted the key to follow the computer's zone,
    and the next upgrade read the missing key as America/New_York and wrote
    it back, so every run rendered EDT again on a machine set to CEST."""
    from typer.testing import CliRunner

    from scout import cli

    vault = tmp_path / "FreshScout"
    _cli_install(vault, monkeypatch, "--timezone", "America/New_York")
    config_path = vault / "scout-config.yaml"
    text = config_path.read_text(encoding="utf-8")
    assert "timezone: America/New_York\n" in text
    config_path.write_text(text.replace("timezone: America/New_York\n", _FOLLOW_THE_HOST_NOTE), encoding="utf-8")

    result = CliRunner().invoke(cli.app, command)
    assert result.exit_code in (0, 1), result.output  # the doctor's verdict
    assert "upgrade" in result.output
    text = config_path.read_text(encoding="utf-8")
    assert "timezone" not in yaml.safe_load(text), "upgrade re-stamped a zone the user deleted"
    assert _FOLLOW_THE_HOST_NOTE in text


def test_cli_upgrade_keeps_an_explicit_override(tmp_path, monkeypatch):
    vault = tmp_path / "FreshScout"
    _cli_install(vault, monkeypatch, "--timezone", "Europe/Prague")
    _cli_upgrade()
    data = yaml.safe_load((vault / "scout-config.yaml").read_text(encoding="utf-8"))
    assert data["timezone"] == "Europe/Prague"


def test_no_rendered_file_bakes_in_the_zone(tmp_path):
    """Every managed script, runner and brain file resolves the zone at run
    time through scout-tz.sh. A zone rendered in at install time is a
    hand-maintained copy that goes stale when the user travels."""
    zone = "Pacific/Chatham"  # appears in no template or phase text
    vault = tmp_path / "Scout"
    cfg = _config(vault)
    cfg.timezone = zone
    install(cfg)
    rendered = {r.file.vault_rel: r.text for r in managed_renders(cfg)}
    for kind in ("SKILL", "DREAMING", "RESEARCH"):
        brain = layout.brain_path(kind)
        if (vault / brain).exists():
            rendered[brain] = (vault / brain).read_text(encoding="utf-8")
    baked = sorted(rel for rel, text in rendered.items() if zone in text)
    assert baked == [], f"rendered with a literal zone: {baked}"


@pytest.mark.parametrize("runner", ["run-scout.sh", "run-dreaming.sh", "run-research.sh"])
def test_runners_pin_the_run_zone_for_every_child(installed_vault, runner):
    """A run spans many processes (the shell scripts, the engine's Python
    hooks, the session itself). The runner resolves the zone once and exports
    it, so all of them agree even if the host's zone changes mid-run."""
    text = (installed_vault / layout.runner_path(runner)).read_text(encoding="utf-8")
    assert 'RUN_TZ="$("$SCOUT_DIR/scout/scripts/scout-tz.sh"' in text
    assert 'export SCOUT_USER_TIMEZONE="$RUN_TZ"' in text


def test_dependent_scripts_call_resolver_not_literal(installed_vault):
    """write-session-cost.sh and rate-limit-detect.sh must derive their local
    timestamp from scout-tz.sh (with the || echo double fallback), not from a
    render-time zone literal."""
    for name in ("write-session-cost.sh", "rate-limit-detect.sh"):
        text = (installed_vault / layout.script_path(name)).read_text(encoding="utf-8")
        assert "scout-tz.sh" in text, f"{name} must call the resolver"
        assert "|| echo America/New_York" in text, f"{name} must keep the double fallback"
        assert 'TZ="$SCOUT_TZ"' in text, f"{name} must use the resolved zone"


def test_write_session_cost_renders_localized_timestamp(installed_vault, tmp_path):
    """End-to-end: the installed write-session-cost.sh resolves the vault's
    configured timezone through scout-tz.sh when writing its tracker row."""
    script = installed_vault / layout.script_path("write-session-cost.sh")
    result = _run(
        script,
        "dreaming",
        "10",
        "1.23",
        "0",
        "session",
        # Point the resolver at a travel config so the assertion is unambiguous
        # (EDT/EST would be indistinguishable from a hardcoded-ET regression).
        env_overrides={"SCOUT_CONFIG": _write_prague_config(tmp_path)},
    )
    assert result.returncode == 0, result.stderr
    tracker = installed_vault / ".scout-logs" / "usage-tracker.jsonl"
    last_row = tracker.read_text(encoding="utf-8").strip().splitlines()[-1]
    assert '"ts_et"' in last_row or '"ts_local"' in last_row
    assert ("CEST" in last_row) or ("CET" in last_row), (
        f"expected Prague zone abbreviation in tracker row, got: {last_row}"
    )


def _write_prague_config(tmp_path: Path) -> str:
    cfg = tmp_path / "prague-config.yaml"
    cfg.write_text("timezone: Europe/Prague\n", encoding="utf-8")
    return str(cfg)
