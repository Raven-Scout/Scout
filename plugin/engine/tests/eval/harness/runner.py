"""Run one eval sample: build, run the vault's real runner, score (spec 2026-10-12 §4, §6).

This is where the I/O lives, so ``metrics`` and ``checks`` can stay pure.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
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
from .metrics import SampleMetrics, metrics_from_dict, parse_stream
from .runner_env import ENGINE, build_env
from .scenario import Scenario

DEFAULT_TIMEOUT_S = 1200
DEFAULT_BUDGET = "3"
HOOK_GRACE_S = 300  # the runner's own pre- and post-session steps
# The shell steps phases run (live samples' denials named echo, mkdir and python). The
# sandbox confines Bash writes (spec §12 R2a′), but still nothing destructive or networked is
# listed: no rm, mv, cp or curl. ``python`` is the engine venv's, first on PATH after the stubs.
_BASH_TOOLS = (
    "git", "gh", "scoutctl", "date", "python3", "python", "ls", "cat", "head", "tail", "wc", "grep", "rg", "find",
    "echo", "printf", "mkdir", "test", "sort", "uniq", "cut", "tr", "jq", "sed", "awk", "diff", "touch",
    "basename", "dirname", "true",
)  # fmt: skip


TERM_GRACE_S = 10  # how long the runner's group gets to stop (the shim kills claude) before SIGKILL


def _kill_group(pid: int, sig: int = signal.SIGKILL) -> None:
    try:
        os.killpg(pid, sig)
    except OSError:
        pass


def _stop_runner(proc: subprocess.Popen[bytes]) -> None:
    """SIGTERM first: the shim's handler takes claude's own process group down with it.
    SIGKILL only if the group doesn't stop; the claude.pgid backstop covers that case."""
    _kill_group(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=TERM_GRACE_S)
    except subprocess.TimeoutExpired:
        _kill_group(proc.pid)
        proc.wait()


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
    meta: dict[str, Any] = field(default_factory=dict)


def load_sample(result_dir: Path) -> SampleResult:
    """A sample read back from its result dir: what a cached base sample is."""
    meta = json.loads((result_dir / "meta.json").read_text(encoding="utf-8"))
    checks_file, metrics_file = result_dir / "checks.json", result_dir / "metrics.json"
    checks = tuple(
        CheckResult(c["name"], c["passed"], tuple(c["details"]))
        for c in (json.loads(checks_file.read_text(encoding="utf-8")) if checks_file.exists() else [])
    )
    metrics = metrics_from_dict(json.loads(metrics_file.read_text(encoding="utf-8"))) if metrics_file.exists() else None
    return SampleResult(
        scenario=meta["scenario"],
        rep=meta["rep"],
        result_dir=result_dir,
        aborted=meta["aborted"],
        metrics=metrics,
        checks=checks,
        unstubbed=_unstubbed(result_dir / "calls.jsonl"),
        meta=meta,
    )


def allowed_tools(vault: Path, servers: set[str]) -> list[str]:
    root = f"/{vault}"  # the "//abs/path" form is absolute (spec §4.3)
    tools = [f"Read({root}/**)", f"Edit({root}/**)", f"Write({root}/**)", "Glob", "Grep", "ToolSearch"]
    tools += [f"Bash({t} *)" for t in _BASH_TOOLS]
    tools += [
        f"Bash({vault}/scripts/*)",
        "Bash(scripts/*)",
        "Bash(bash scripts/*)",
        f"Bash(bash {vault}/scripts/*)",
        f"Bash({ENGINE / '.venv' / 'bin' / 'scoutctl'} *)",
    ]
    tools += [f"mcp__{s}" for s in sorted(servers)]
    return tools


def _items(path: Path) -> list[ActionItem] | None:
    return parse_lines(path.read_text(encoding="utf-8").splitlines()) if path.is_file() else None


def archived_today(data_dir: Path, today: date) -> set[str]:
    """Tags the stale archive moved out today.

    ``stale.write_archive`` writes each item's task line, then a
    ``  - _auto-archived <day> …_`` sub-line, so the tag is carried from the
    task line to its note.
    """
    tags: set[str] = set()
    mark = f"_auto-archived {today.isoformat()}"
    for path in (data_dir / "action-items" / "archive").glob("stale-items-*.md"):
        tag = None
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*- \[[ xX]\] \[#([A-Z0-9]{2,8})\]", line)
            if m:
                tag = m.group(1)
            elif tag and line.strip().startswith(f"- {mark}"):
                tags.add(tag)
    return tags


def _unstubbed(calls: Path) -> int:
    if not calls.exists():
        return 0
    return sum(1 for line in calls.read_text(encoding="utf-8").splitlines() if '"kind": "unstubbed"' in line)


CARRY_BANNER = "**Mechanical carry-forward**"  # scout/action_items/materialize.py's _BANNER


def _score(scenario: Scenario, vault: Path, today: date, yesterday: list[ActionItem]) -> tuple[CheckResult, ...]:
    today_file = vault / "action-items" / f"action-items-{today.isoformat()}.md"
    if today_file.is_file() and CARRY_BANNER in today_file.read_text(encoding="utf-8"):
        # The runner carried yesterday's file in verbatim and the session never rewrote it:
        # scoring that would credit the session with yesterday's placements and no drops.
        return tuple(CheckResult(name, None, ("not_enriched",)) for name in scenario.checks)
    today_items = _items(today_file)
    results = []
    for name in scenario.checks:
        if name == "placement":
            results.append(placement(today_items, scenario.expected_placement))
        elif name == "no_drop":
            results.append(
                no_drop(
                    yesterday,
                    today_items,
                    archived_tags=archived_today(vault, today),
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
    hook_grace_s: int = HOOK_GRACE_S,
    arm: str = "head",
    cli_version: str = "",
) -> SampleResult:
    started_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    result_dir = results_root / f"{scenario.name}-{arm}-{time.strftime('%Y%m%d-%H%M%S')}-{rep}"
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
    runner_timed_out = False
    with (result_dir / "runner.log").open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            ["bash", str(vault / "run-scout.sh")],
            cwd=vault,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # its own group, so the session under it dies with it
        )
        try:
            runner_exit: int | None = proc.wait(timeout=timeout_s + hook_grace_s)
        except subprocess.TimeoutExpired:
            runner_timed_out, runner_exit = True, None
            _stop_runner(proc)
        except BaseException:  # Ctrl-C: take the session down with the command
            _stop_runner(proc)
            raise
    _kill_group(proc.pid)
    pgid = result_dir / "claude.pgid"  # the shim runs claude in a group of its own
    if pgid.exists():
        _kill_group(int(pgid.read_text(encoding="utf-8")))
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
    if aborted is None and runner_timed_out:
        aborted = {"reason": "runner_timeout", "seconds": timeout_s + hook_grace_s}
    if aborted is None and (metrics is None or (metrics.model is None and metrics.result_subtype is None)):
        aborted = {"reason": "no_session", "detail": "no init or result event; see runner.log and claude.stderr"}
    if aborted is None and metrics is not None and metrics.result_subtype not in (None, "success"):
        aborted = {"reason": metrics.result_subtype}  # e.g. error_max_budget_usd: the session didn't finish
    (result_dir / "runner-exit.json").write_text(json.dumps({"exit": runner_exit}), encoding="utf-8")
    checks = () if aborted else _score(scenario, vault, today, yesterday)
    (result_dir / "checks.json").write_text(json.dumps([asdict(c) for c in checks], indent=2), encoding="utf-8")
    if metrics is not None:
        (result_dir / "metrics.json").write_text(json.dumps(asdict(metrics), indent=2), encoding="utf-8")
    meta = {
        "scenario": scenario.name,
        "rep": rep,
        "arm": arm,
        "model": model,
        "resolved_model": metrics.model if metrics else None,
        "cli_version": cli_version,
        "today": today.isoformat(),
        "started_at": started_at,
        "aborted": aborted,
    }
    (result_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return SampleResult(
        scenario=scenario.name,
        rep=rep,
        result_dir=result_dir,
        aborted=aborted,
        metrics=metrics,
        checks=checks,
        unstubbed=_unstubbed(result_dir / "calls.jsonl"),
        meta=meta,
    )
