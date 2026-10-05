"""A fresh vault gets the helper scripts, and the guarded steps that call them run.

The runners, ``hooks/kb-pre-filter.sh`` and ``scripts/heartbeat.sh`` each call an
optional helper and skip the step when it is absent. The plugin now ships the
helpers as cat-1 files:

  - ``scripts/run-outcome.sh``           (rendered template)
  - ``scripts/vault-freshness.py``       (copied verbatim)
  - ``scripts/session-lane-liveness.py`` (copied verbatim)

so on a vault that was only ever installed by the plugin, those steps now run.
(The connector-health roll-up needs no helper: the runner calls the engine.)

These tests install a vault from a plugin root whose ``scoutctl`` is a stand-in
that records each call, then run the installed scripts with the real shell.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scout.scripts.bootstrap import BootstrapConfig, install, upgrade

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
RUN_TIMEOUT_S = 120

SHIPPED_VERBATIM = ["scripts/vault-freshness.py", "scripts/session-lane-liveness.py"]

SCOUTCTL_STUB = """#!/usr/bin/env python3
import json, os, sys
with open({calls!r}, "a") as fh:
    fh.write(json.dumps({{"argv": sys.argv[1:], "data_dir": os.environ.get("SCOUT_DATA_DIR")}}) + "\\n")
if sys.argv[1:2] == ["connector-health-report"]:
    print("connector-health: 1 sessions in window, 0 alert(s)")
"""


def _script(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.fixture
def plugin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The real plugin's templates, phases and engine, with a stand-in scoutctl.

    Templates name the scoutctl beside the interpreter that runs the install
    (resolve_scoutctl_bin), so point that at the stand-in.
    """
    root = tmp_path / "plugin"
    root.mkdir()
    for name in ("templates", "phases", "engine"):
        (root / name).symlink_to(PLUGIN_ROOT / name)
    stub = _script(root / ".venv" / "bin" / "scoutctl", SCOUTCTL_STUB.format(calls=str(tmp_path / "scoutctl.calls")))
    monkeypatch.setattr("scout.scripts.bootstrap.resolve_scoutctl_bin", lambda: stub)
    return root


@pytest.fixture
def calls(tmp_path: Path) -> Path:
    return tmp_path / "scoutctl.calls"


def _config(vault: Path, plugin: Path, *, version: str = "0.4.0") -> BootstrapConfig:
    return BootstrapConfig(
        vault=vault,
        plugin_root=plugin,
        instance_name="TestScout",
        instance_name_lower="testscout",
        user_name="Alex",
        user_email="alex@example.com",
        timezone="America/New_York",
        platform="macos",
        plugin_version=version,
        enabled_connectors=set(),
        connector_inputs={},
        skip_jobs=True,
        skip_claude=True,
    )


@pytest.fixture
def vault(tmp_path: Path, plugin: Path) -> Path:
    v = tmp_path / "Scout"
    install(_config(v, plugin))
    return v


@pytest.fixture
def bindir(tmp_path: Path) -> Path:
    """Front of PATH: a stand-in osascript, so no test raises a real notification."""
    d = tmp_path / "bin"
    _script(d / "osascript", f'#!/bin/bash\nprintf "%s\\n" "$@" >> "{tmp_path / "osascript.calls"}"\n')
    return d


def _run(script: Path, vault: Path, bindir: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(script), *args],
        cwd=vault,
        env={**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", **env},
        capture_output=True,
        text=True,
        timeout=RUN_TIMEOUT_S,
    )


def _calls(calls: Path) -> list[list[str]]:
    if not calls.exists():
        return []
    return [json.loads(line)["argv"] for line in calls.read_text(encoding="utf-8").splitlines()]


# ---------- the files land ----------


def test_a_fresh_install_ships_run_outcome_rendered_for_this_vault(vault: Path, plugin: Path) -> None:
    script = vault / "scripts" / "run-outcome.sh"

    text = script.read_text(encoding="utf-8")
    assert os.access(script, os.X_OK)
    assert "{{" not in text
    assert f'SCOUT_DIR="${{SCOUT_DATA_DIR:-{vault}}}"' in text
    assert f'SCOUTCTL="${{SCOUTCTL_BIN:-{plugin / ".venv" / "bin" / "scoutctl"}}}"' in text


@pytest.mark.parametrize("rel", SHIPPED_VERBATIM)
def test_a_fresh_install_ships_the_python_helpers_verbatim(vault: Path, rel: str) -> None:
    assert (vault / rel).read_bytes() == (PLUGIN_ROOT / "templates" / rel).read_bytes()


@pytest.mark.parametrize("rel", ["scripts/run-outcome.sh", *SHIPPED_VERBATIM])
def test_an_upgrade_gives_an_existing_vault_the_helpers(vault: Path, plugin: Path, rel: str) -> None:
    """A vault installed before the helpers shipped picks them up on its next upgrade."""
    (vault / rel).unlink()

    upgrade(_config(vault, plugin, version="0.4.1"))

    assert (vault / rel).exists()


@pytest.mark.parametrize("rel", ["scripts/run-outcome.sh", *SHIPPED_VERBATIM])
def test_an_upgrade_parks_a_hand_copy_of_a_helper_instead_of_losing_it(vault: Path, plugin: Path, rel: str) -> None:
    """A vault that wrote its own helper before the plugin shipped one has no
    record of a plugin render for it. The upgrade installs the plugin's version
    and parks the vault's copy under .scout-state/drift/; nothing is lost silently."""
    (vault / ".scout-state" / "last-rendered" / rel).unlink()
    hand_copy = "# the vault's own helper, written before the plugin shipped one\n"
    (vault / rel).write_text(hand_copy, encoding="utf-8")

    result = upgrade(_config(vault, plugin, version="0.4.1"))

    assert (vault / rel).read_text(encoding="utf-8") != hand_copy
    (edit,) = [e for e in result.vault_edits if e.path == rel]
    assert edit.outcome == "replaced"
    (parked,) = edit.parked
    assert (vault / parked).read_text(encoding="utf-8") == hand_copy
    assert Path(parked).parts[:2] == (".scout-state", "drift")


@pytest.mark.parametrize("rel", ["scripts/run-outcome.sh", *SHIPPED_VERBATIM])
def test_an_upgrade_keeps_an_edit_to_a_shipped_helper(vault: Path, plugin: Path, rel: str) -> None:
    """Once the plugin has rendered a helper, a vault edit to it survives the next upgrade."""
    edited = (vault / rel).read_text(encoding="utf-8") + "# a local tweak\n"
    (vault / rel).write_text(edited, encoding="utf-8")

    result = upgrade(_config(vault, plugin, version="0.4.1"))

    assert (vault / rel).read_text(encoding="utf-8") == edited
    assert [e.outcome for e in result.vault_edits if e.path == rel] == ["kept"]


# ---------- the guarded steps now run ----------


def test_the_installed_runner_records_its_outcome_and_rolls_up_connector_health(
    vault: Path, bindir: Path, calls: Path
) -> None:
    # Stand in for claude: the run dies on an expired login.
    _script(
        vault / "scripts" / "claude-with-retry.sh",
        '#!/bin/bash\necho "Failed to authenticate. API Error: 401" >> "$1"\nexit 1\n',
    )

    result = _run(vault / "run-scout.sh", vault, bindir, SCOUT_FORCE_MODE="morning-briefing")

    assert result.returncode == 0, result.stderr
    (row,) = [json.loads(line) for line in (vault / ".scout-logs" / "run-outcomes.jsonl").read_text().splitlines()]
    assert (row["slot"], row["exit_code"], row["failure_class"]) == ("morning-briefing", 1, "oauth_expired")
    made = _calls(calls)
    assert ["connector-health-report"] in made
    (notice,) = [argv for argv in made if argv[:2] == ["notify", "telegram"]]
    assert notice[notice.index("--tier") + 1] == "info"


def test_the_installed_kb_pre_filter_caches_the_freshness_view(vault: Path, bindir: Path, calls: Path) -> None:
    (vault / "knowledge-base" / "projects" / "roadmap.md").write_text("# Roadmap\n", encoding="utf-8")

    result = _run(vault / "hooks" / "kb-pre-filter.sh", vault, bindir, "briefing")

    assert result.returncode == 0, result.stderr
    view = (vault / ".scout-cache" / "vault-freshness.md").read_text(encoding="utf-8")
    assert view.startswith("# Vault Freshness")
    assert "knowledge-base/projects/roadmap.md" in view
    assert ["hook", "kb-pre-filter", "--session-type", "briefing"] in _calls(calls)


def test_the_installed_heartbeat_runs_the_lane_watchdog(vault: Path, bindir: Path, calls: Path) -> None:
    # A vault whose dreaming lane last committed a month ago.
    stamp = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    git_env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Scout Bot",
        "GIT_AUTHOR_EMAIL": "alex@example.com",
        "GIT_COMMITTER_NAME": "Scout Bot",
        "GIT_COMMITTER_EMAIL": "alex@example.com",
        "GIT_AUTHOR_DATE": stamp,
        "GIT_COMMITTER_DATE": stamp,
    }
    for args in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "dreaming [22:00]: KB deep work"]):
        subprocess.run(["git", "-C", str(vault), *args], env=git_env, check=True, capture_output=True)

    result = _run(vault / "scripts" / "heartbeat.sh", vault, bindir)

    assert result.returncode == 0, result.stderr
    log = (vault / ".scout-logs" / "lane-liveness.log").read_text(encoding="utf-8")
    assert "**dreaming** — 🔴 DARK" in log
    assert list((vault / ".scout-cache").glob("lane-liveness-*.done"))
    assert ["heartbeat", "run"] in _calls(calls)
