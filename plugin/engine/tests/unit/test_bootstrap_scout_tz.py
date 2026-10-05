"""Unit tests for the scout-tz.sh resolver template (cat-1 install + runtime behavior).

scout-tz.sh is the runtime timezone resolver the assembled brain files and the
other cat-1 scripts call via ``TZ="$(scripts/scout-tz.sh)"``. These tests cover
both halves: bootstrap writes it (executable, fully rendered), and the installed
script actually resolves/validates/falls back the way its contract says.
"""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import pytest
import yaml

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
        skip_claude=True,
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
    script = installed_vault / "scripts" / "scout-tz.sh"
    assert script.exists(), "cat-1 install must write scripts/scout-tz.sh"
    assert script.stat().st_mode & stat.S_IXUSR, "scout-tz.sh must be executable"
    text = script.read_text(encoding="utf-8")
    assert "{{" not in text, "template vars must be fully rendered"
    assert "resolve_tz" in text


def test_scout_tz_reads_configured_timezone(installed_vault, tmp_path):
    script = installed_vault / "scripts" / "scout-tz.sh"
    cfg = tmp_path / "travel-config.yaml"
    cfg.write_text('timezone: "Europe/Prague"  # traveling\n', encoding="utf-8")
    result = _run(script, env_overrides={"SCOUT_CONFIG": str(cfg)})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "Europe/Prague"


def test_scout_tz_falls_back_on_invalid_zone(installed_vault, tmp_path):
    script = installed_vault / "scripts" / "scout-tz.sh"
    cfg = tmp_path / "bogus-config.yaml"
    cfg.write_text("timezone: Mars/Olympus_Mons\n", encoding="utf-8")
    result = _run(script, env_overrides={"SCOUT_CONFIG": str(cfg)})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "America/New_York"
    assert "falling back" in result.stderr


def test_scout_tz_self_test_passes_in_installed_vault(installed_vault):
    # The install renders scout-config.yaml with timezone America/New_York, so
    # the script-relative config fallback resolves inside the tmp vault and the
    # shipped self-test's assertions all hold.
    script = installed_vault / "scripts" / "scout-tz.sh"
    result = _run(script, "--self-test")
    assert result.returncode == 0, f"self-test failed:\n{result.stdout}\n{result.stderr}"
    assert "FAIL" not in result.stdout
    assert "pass ---" in result.stdout


def test_scout_tz_follows_the_host_when_unconfigured(installed_vault, tmp_path):
    script = installed_vault / "scripts" / "scout-tz.sh"
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
    result = CliRunner().invoke(cli.app, [*argv, "--no-jobs", "--skip-claude", *extra])
    assert result.exit_code in (0, 1), result.output  # the doctor's verdict
    return result


def _cli_upgrade():
    from typer.testing import CliRunner

    from scout import cli

    result = CliRunner().invoke(cli.app, ["bootstrap", "upgrade", "--no-jobs", "--skip-claude"])
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
        ["bootstrap", "upgrade", "--no-jobs", "--skip-claude"],
        ["bootstrap", "auto", "--yes", "--no-jobs", "--skip-claude"],  # what the desktop app runs
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
    for brain in ("SKILL.md", "DREAMING.md", "RESEARCH.md"):
        if (vault / brain).exists():
            rendered[brain] = (vault / brain).read_text(encoding="utf-8")
    baked = sorted(rel for rel, text in rendered.items() if zone in text)
    assert baked == [], f"rendered with a literal zone: {baked}"


@pytest.mark.parametrize("runner", ["run-scout.sh", "run-dreaming.sh", "run-research.sh"])
def test_runners_pin_the_run_zone_for_every_child(installed_vault, runner):
    """A run spans many processes (the shell scripts, the engine's Python
    hooks, the session itself). The runner resolves the zone once and exports
    it, so all of them agree even if the host's zone changes mid-run."""
    text = (installed_vault / runner).read_text(encoding="utf-8")
    assert 'RUN_TZ="$("$SCOUT_DIR/scripts/scout-tz.sh"' in text
    assert 'export SCOUT_USER_TIMEZONE="$RUN_TZ"' in text


def test_dependent_scripts_call_resolver_not_literal(installed_vault):
    """write-session-cost.sh and rate-limit-detect.sh must derive their local
    timestamp from scout-tz.sh (with the || echo double fallback), not from a
    render-time zone literal."""
    for name in ("write-session-cost.sh", "rate-limit-detect.sh"):
        text = (installed_vault / "scripts" / name).read_text(encoding="utf-8")
        assert "scout-tz.sh" in text, f"{name} must call the resolver"
        assert "|| echo America/New_York" in text, f"{name} must keep the double fallback"
        assert 'TZ="$SCOUT_TZ"' in text, f"{name} must use the resolved zone"


def test_write_session_cost_renders_localized_timestamp(installed_vault, tmp_path):
    """End-to-end: the installed write-session-cost.sh resolves the vault's
    configured timezone through scout-tz.sh when writing its tracker row."""
    script = installed_vault / "scripts" / "write-session-cost.sh"
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
