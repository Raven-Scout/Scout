"""Run one eval sample: build, run the vault's real runner, score (spec 2026-10-12 §4, §6).

This is where the I/O lives, so ``metrics`` and ``checks`` can stay pure.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from scout.action_items.parser import ActionItem, parse_lines

from .build import build_vault
from .checks import CheckResult
from .checks.no_drop import no_drop
from .checks.placement import placement
from .claude_shim import SHIM
from .mcp_config import write_mcp_config
from .metrics import SampleMetrics, parse_stream
from .runner_env import ENGINE, build_env
from .scenario import Scenario

DEFAULT_TIMEOUT_S = 1200
DEFAULT_BUDGET = "3"
HOOK_GRACE_S = 300  # the runner's own pre- and post-session steps
# The shell steps phases run (the live sample's denials named echo and mkdir). Bash is not
# path-confined, so nothing destructive or networked is listed: no rm, mv, cp or curl.
_BASH_TOOLS = (
    "git", "gh", "scoutctl", "date", "python3", "ls", "cat", "head", "tail", "wc", "grep", "rg", "find",
    "echo", "printf", "mkdir", "test", "sort", "uniq", "cut", "tr", "jq", "sed", "awk", "diff", "touch",
    "basename", "dirname", "true",
)  # fmt: skip


class LoginRequired(Exception):
    """The Claude Code login is missing or expired: no sample can run."""


@dataclass(frozen=True)
class SampleResult:
    scenario: str
    rep: int
    result_dir: Path
    aborted: dict[str, Any] | None
    metrics: SampleMetrics | None
    checks: tuple[CheckResult, ...]
    unstubbed: int


def allowed_tools(vault: Path, servers: set[str]) -> list[str]:
    root = f"/{vault}"  # the "//abs/path" form is absolute (spec §4.3)
    tools = [f"Read({root}/**)", f"Edit({root}/**)", f"Write({root}/**)", "Glob", "Grep", "ToolSearch"]
    tools += [f"Bash({t} *)" for t in _BASH_TOOLS]
    tools += [f"Bash({vault}/scripts/*)", "Bash(scripts/*)", f"Bash({ENGINE / '.venv' / 'bin' / 'scoutctl'} *)"]
    tools += [f"mcp__{s}" for s in sorted(servers)]
    return tools


def _items(path: Path) -> list[ActionItem] | None:
    return parse_lines(path.read_text(encoding="utf-8").splitlines()) if path.is_file() else None


def _archived_today(vault: Path, today: date) -> set[str]:
    tags: set[str] = set()
    for path in (vault / "action-items" / "archive").glob("stale-items-*.md"):
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.search(r"\[#([A-Z0-9]{2,8})\]", line)
            if m and f"auto-archived {today.isoformat()}" in line:
                tags.add(m.group(1))
    return tags


def _unstubbed(calls: Path) -> int:
    if not calls.exists():
        return 0
    return sum(1 for line in calls.read_text(encoding="utf-8").splitlines() if '"kind": "unstubbed"' in line)


def _score(scenario: Scenario, vault: Path, today: date, yesterday: list[ActionItem]) -> tuple[CheckResult, ...]:
    today_items = _items(vault / "action-items" / f"action-items-{today.isoformat()}.md")
    results = []
    for name in scenario.checks:
        if name == "placement":
            results.append(placement(today_items, scenario.expected_placement))
        elif name == "no_drop":
            results.append(
                no_drop(
                    yesterday,
                    today_items,
                    archived_tags=_archived_today(vault, today),
                    expected_closures=scenario.expected_closures,
                )
            )
        else:
            raise ValueError(f"scenario {scenario.name}: unknown check {name!r}")
    return tuple(results)


def run_sample(
    scenario: Scenario,
    *,
    today: date,
    rep: int,
    model: str,
    results_root: Path,
    real_claude: str,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    budget: str = DEFAULT_BUDGET,
) -> SampleResult:
    result_dir = results_root / f"{scenario.name}-{time.strftime('%Y%m%d-%H%M%S')}-{rep}"
    vault = result_dir / "vault"
    result_dir.mkdir(parents=True)
    build_vault(
        vault,
        today=today,
        scenario_dir=scenario.path,
        claude_bin=str(SHIM),
        connectors=scenario.connectors,
        max_budget=budget,
    )
    yesterday_file = vault / "action-items" / f"action-items-{(today - timedelta(days=1)).isoformat()}.md"
    yesterday = _items(yesterday_file) or []
    servers = write_mcp_config(
        result_dir / "mcp.json",
        personas=scenario.personas,
        script=scenario.script,
        calls=result_dir / "calls.jsonl",
        today=today,
        python=sys.executable,
    )
    env = build_env(
        base=os.environ,
        mode=scenario.mode,
        result_dir=result_dir,
        mcp_config=result_dir / "mcp.json",
        allowed_servers=servers,
        allowed_tools=allowed_tools(vault, servers),
        model=model,
        real_claude=real_claude,
        script=scenario.script,
        today=today.isoformat(),
        timeout_s=timeout_s,
    )
    with (result_dir / "runner.log").open("w", encoding="utf-8") as log:
        subprocess.run(
            ["bash", str(vault / "run-scout.sh")],
            cwd=vault,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=timeout_s + HOOK_GRACE_S,
            check=False,
        )
    stream = result_dir / "stream.jsonl"
    metrics = parse_stream(stream.read_text(encoding="utf-8").splitlines()) if stream.exists() else None
    stderr = result_dir / "claude.stderr"
    login_text = (stderr.read_text(encoding="utf-8") if stderr.exists() else "") + (
        stream.read_text(encoding="utf-8") if stream.exists() else ""
    )
    if "Not logged in" in login_text or "Please run /login" in login_text:
        raise LoginRequired(f"Claude Code is not logged in (see {stderr}); run `claude auth login`, then retry.")
    aborted_file = result_dir / "aborted.json"
    aborted = json.loads(aborted_file.read_text(encoding="utf-8")) if aborted_file.exists() else None
    if aborted is None and (metrics is None or (metrics.model is None and metrics.result_subtype is None)):
        aborted = {"reason": "no_session", "detail": "no init or result event; see runner.log and claude.stderr"}
    checks = () if aborted else _score(scenario, vault, today, yesterday)
    (result_dir / "checks.json").write_text(json.dumps([asdict(c) for c in checks], indent=2), encoding="utf-8")
    if metrics is not None:
        (result_dir / "metrics.json").write_text(json.dumps(asdict(metrics), indent=2), encoding="utf-8")
    return SampleResult(
        scenario=scenario.name,
        rep=rep,
        result_dir=result_dir,
        aborted=aborted,
        metrics=metrics,
        checks=checks,
        unstubbed=_unstubbed(result_dir / "calls.jsonl"),
    )
