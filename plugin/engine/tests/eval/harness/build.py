"""Build the eval fixture vault (spec 2026-10-12 §5.1).

Three layers, built fresh for every sample:

1. the skeleton from this checkout's own ``bootstrap.install()``, so brain
   files, runners and scripts are always the code under test;
2. the hand-written base overlay in ``tests/eval/vault/``;
3. the scenario's own ``vault/`` overlay.

Overlay text files have their date macros rendered, in contents and in path
components. The result is committed as a one-commit git repo.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import scout
from scout.scripts.bootstrap import BootstrapConfig, install

from .checkout import Checkout
from .dates import UNRENDERED, render_dates
from .index_gen import generate_index
from .runner_env import STUB_BIN

EVAL_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = Path(__file__).resolve().parents[4]
BASE_OVERLAY = EVAL_ROOT / "vault"

FIXTURE_USER = "Alex Example"
FIXTURE_EMAIL = "alex@example.com"
FIXTURE_SLACK_ID = "U0123456789"
FIXTURE_GITHUB_USER = "alex-example"
FIXTURE_REPOS = "example-org/widgets,example-org/api"
DEFAULT_CONNECTORS = frozenset({"slack", "calendar", "linear", "github"})
NOOP_SCRIPT = "#!/bin/bash\nexit 0\n"


class FixtureError(Exception):
    def __init__(self, path: str, token: str) -> None:
        super().__init__(f"unrendered macro in fixture file {path}: {token}")
        self.path, self.token = path, token


def _check_rendered(rel: str, text: str) -> None:
    leftover = UNRENDERED.search(text)
    if leftover:
        raise FixtureError(rel, leftover.group(0))


def _overlay(src: Path, dest: Path, today: date) -> None:
    """Copy ``src`` over ``dest``, rendering date macros in every path and every text file."""
    for path in sorted(p for p in src.rglob("*") if p.is_file()):
        rel = path.relative_to(src).as_posix()
        rendered_rel = render_dates(rel, today)
        _check_rendered(rel, rendered_rel)
        target = dest / rendered_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            shutil.copy2(path, target)  # binary: copied as is (the scrub test reports it)
            continue
        text = render_dates(text, today)
        _check_rendered(rel, text)
        target.write_text(text, encoding="utf-8")
        shutil.copymode(path, target)


def _git_env(today: date) -> dict[str, str]:
    """Fixed identity and config, and no inherited GIT_* (a hook's GIT_DIR would redirect the commit)."""
    stamp = f"{today.isoformat()}T06:00:00+00:00"
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_AUTHOR_NAME=FIXTURE_USER,
        GIT_AUTHOR_EMAIL=FIXTURE_EMAIL,
        GIT_COMMITTER_NAME=FIXTURE_USER,
        GIT_COMMITTER_EMAIL=FIXTURE_EMAIL,
        GIT_AUTHOR_DATE=stamp,
        GIT_COMMITTER_DATE=stamp,
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
    )
    return env


def _git(vault: Path, env: dict[str, str], *args: str) -> None:
    subprocess.run(["git", *args], cwd=vault, env=env, check=True, capture_output=True)


def build_vault(
    dest: Path,
    *,
    today: date,
    scenario_dir: Path | None = None,
    claude_bin: str = "/usr/bin/false",
    connectors: frozenset[str] = DEFAULT_CONNECTORS,
    max_budget: str = "5",
    checkout: Checkout | None = None,
    scheduler_tick: datetime | None = None,
) -> Path:
    """Build the fixture vault. ``checkout`` (a comparison's base arm) installs from that
    checkout's own engine and phases instead of this one's."""
    if dest.exists() and any(dest.iterdir()):
        raise FileExistsError(f"eval vault destination is not empty: {dest}")
    inputs = {
        "user_slack_id": FIXTURE_SLACK_ID,
        "github_username": FIXTURE_GITHUB_USER,
        "github_repos": FIXTURE_REPOS,
        "claude_bin": claude_bin,
        "max_budget": max_budget,
    }
    if checkout is not None and checkout.pythonpath is not None:
        _install_from(checkout, dest, connectors, inputs)
    else:
        _install_here(dest, connectors, inputs)
    _overlay(BASE_OVERLAY, dest, today)
    if scenario_dir is not None and (scenario_dir / "vault").is_dir():
        _overlay(scenario_dir / "vault", dest, today)
    (dest / "knowledge-base.md").write_text(render_dates(generate_index(), today), encoding="utf-8")
    if scheduler_tick is not None:
        _seed_scheduler_tick(dest, scheduler_tick)
    cache = dest / "scripts" / "cc-session-cache.sh"
    cache.write_text(NOOP_SCRIPT, encoding="utf-8")
    cache.chmod(0o755)
    env = _git_env(today)
    _git(dest, env, "init", "-q", "-b", "main")
    # No detached auto-maintenance: its .git/maintenance.lock outlives the commit and races a caller's rmtree.
    _git(dest, env, "config", "maintenance.auto", "false")
    _git(dest, env, "config", "gc.auto", "0")
    _git(dest, env, "add", "-A")
    _git(dest, env, "commit", "-q", "-m", "fixture")
    return dest


def _refuse_system_side_effects(cmd: list[str], env: dict[str, str]) -> None:
    """A fixture install must never touch launchd, cron or ~/.local/bin. Without --no-jobs,
    `bootstrap install` boots out and replaces the user's real loaded scheduler jobs; a real HOME
    would put anything else that slips through into the user's own dotfiles."""
    if "--no-jobs" not in cmd or "--skip-claude" not in cmd:
        raise RuntimeError(f"refusing a fixture install without --no-jobs/--skip-claude: {cmd}")
    if Path(env.get("HOME", "")).resolve() == Path.home().resolve():
        raise RuntimeError("refusing a fixture install with the real HOME")


def _install_from(checkout: Checkout, dest: Path, connectors: frozenset[str], inputs: dict[str, str]) -> None:
    """``scoutctl bootstrap install`` run from another checkout's engine (its phases, runners, scripts)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SCOUT_", "GIT_"))}
    # A throwaway HOME too: with --no-jobs nothing should touch it, and if anything ever does,
    # it lands here, not in the user's ~/.local/bin or ~/Library/LaunchAgents.
    install_home = dest.parent / f".{dest.name}-install-home"
    install_home.mkdir(parents=True, exist_ok=True)
    env.update(
        SCOUT_DATA_DIR=str(dest),
        PYTHONPATH=str(checkout.pythonpath),
        HOME=str(install_home),
        PATH=f"{STUB_BIN}{os.pathsep}{env.get('PATH', '')}",  # refusing launchctl/crontab first
    )
    # -P: the current directory must not shadow PYTHONPATH. --no-jobs and --skip-claude are what
    # keep a fixture install off launchd: without them it replaces the user's real scheduler jobs.
    cmd = [
        sys.executable, "-P", "-m", "scout", "bootstrap", "install", "--no-jobs", "--skip-claude",
        "--instance-name", "Scout", "--user-name", FIXTURE_USER, "--user-email", FIXTURE_EMAIL,
        "--timezone", "UTC", "--platform", "macos", "--connectors", ",".join(sorted(connectors)),
        "--user-slack-id", inputs["user_slack_id"], "--github-username", inputs["github_username"],
        "--github-repos", inputs["github_repos"], "--claude-bin", inputs["claude_bin"],
        "--max-budget", inputs["max_budget"],
    ]  # fmt: skip
    _refuse_system_side_effects(cmd, env)
    done = subprocess.run(cmd, env=env, cwd=dest.parent, capture_output=True, text=True)
    if done.returncode != 0 or not (dest / "SKILL.md").is_file():
        raise RuntimeError(
            f"bootstrap install from {checkout.label} checkout {checkout.root} failed "
            f"(exit {done.returncode}):\n{done.stdout[-2000:]}\n{done.stderr[-2000:]}"
        )


def _refuse_jobs(cfg: BootstrapConfig) -> None:
    """The in-process install runs with the real HOME in a live eval run (the keychain login
    needs it), so jobs must be off: they'd replace the user's real scheduler jobs."""
    if not (cfg.skip_jobs and cfg.skip_claude):
        raise RuntimeError("refusing a fixture install without skip_jobs/skip_claude")


def _seed_scheduler_tick(vault: Path, at: datetime) -> None:
    """A recent completed tick, as a live install has: otherwise run-health reads a fixture vault
    as "the scheduler has never recorded a tick", and every wrap leads with that red finding
    (and its "reinstall the scheduler job" fix)."""
    stamp = at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    log = vault / ".scout-logs" / f"schedule-events-{at.astimezone(UTC):%Y-%m-%d}.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": stamp, "kind": "schedule.tick.completed"}) + "\n")


def _install_here(dest: Path, connectors: frozenset[str], inputs: dict[str, str]) -> None:
    cfg = BootstrapConfig(
        vault=dest,
        plugin_root=PLUGIN_ROOT,
        instance_name="Scout",
        instance_name_lower="scout",
        user_name=FIXTURE_USER,
        user_email=FIXTURE_EMAIL,
        timezone="UTC",
        platform="macos",
        plugin_version=scout.__version__,
        enabled_connectors=set(connectors),
        connector_inputs=dict(inputs),
        skip_jobs=True,
        skip_claude=True,
    )
    _refuse_jobs(cfg)
    install(cfg)
