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

import os
import shutil
import subprocess
from datetime import date
from pathlib import Path

import scout
from scout.scripts.bootstrap import BootstrapConfig, install

from .dates import UNRENDERED, render_dates
from .index_gen import generate_index

EVAL_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = Path(__file__).resolve().parents[4]
BASE_OVERLAY = EVAL_ROOT / "vault"

FIXTURE_USER = "Alex Example"
FIXTURE_EMAIL = "alex@example.com"
FIXTURE_SLACK_ID = "U0123456789"
FIXTURE_GITHUB_USER = "alex-example"
FIXTURE_REPOS = "example-org/widgets,example-org/api"
DEFAULT_CONNECTORS = frozenset({"slack", "calendar", "linear", "github"})
RENDERED_SUFFIXES = {".md", ".yaml", ".yml", ".json", ".jsonl", ".txt"}
NOOP_SCRIPT = "#!/bin/bash\nexit 0\n"


class FixtureError(Exception):
    def __init__(self, path: str, token: str) -> None:
        super().__init__(f"unrendered macro in fixture file {path}: {token}")
        self.path, self.token = path, token


def _overlay(src: Path, dest: Path, today: date) -> None:
    for path in sorted(p for p in src.rglob("*") if p.is_file()):
        rel = path.relative_to(src).as_posix()
        target = dest / render_dates(rel, today)
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix in RENDERED_SUFFIXES:
            text = render_dates(path.read_text(encoding="utf-8"), today)
            leftover = UNRENDERED.search(text)
            if leftover:
                raise FixtureError(rel, leftover.group(0))
            target.write_text(text, encoding="utf-8")
        else:
            shutil.copy2(path, target)


def _git_env(today: date) -> dict[str, str]:
    stamp = f"{today.isoformat()}T06:00:00+00:00"
    env = dict(os.environ)
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
) -> Path:
    if dest.exists() and any(dest.iterdir()):
        raise FileExistsError(f"eval vault destination is not empty: {dest}")
    install(
        BootstrapConfig(
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
            connector_inputs={
                "user_slack_id": FIXTURE_SLACK_ID,
                "github_username": FIXTURE_GITHUB_USER,
                "github_repos": FIXTURE_REPOS,
                "claude_bin": claude_bin,
                "max_budget": max_budget,
            },
            skip_jobs=True,
            skip_claude=True,
        )
    )
    _overlay(BASE_OVERLAY, dest, today)
    if scenario_dir is not None and (scenario_dir / "vault").is_dir():
        _overlay(scenario_dir / "vault", dest, today)
    (dest / "knowledge-base.md").write_text(render_dates(generate_index(), today), encoding="utf-8")
    cache = dest / "scripts" / "cc-session-cache.sh"
    cache.write_text(NOOP_SCRIPT, encoding="utf-8")
    cache.chmod(0o755)
    env = _git_env(today)
    _git(dest, env, "init", "-q", "-b", "main")
    _git(dest, env, "add", "-A")
    _git(dest, env, "commit", "-q", "-m", "fixture")
    return dest
