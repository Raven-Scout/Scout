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
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from scout.action_items.parser import ActionItem, parse_lines

from .build import build_vault
from .checkout import Checkout
from .checks import CheckResult
from .checks.daily import body_budget, context_note, no_collapse, waiting_format
from .checks.demotion import demotion
from .checks.no_drop import no_drop
from .checks.placement import placement
from .checks.privacy_canary import (
    LABEL_COMMAND,
    PrivacyOutputs,
    owner_surfaces_required,
    privacy_canary,
    privacy_hygiene,
)
from .checks.run import dropoff_caught, no_false_outage, outage_reported
from .claude_shim import SHIM
from .mcp_config import write_mcp_config
from .metrics import SampleMetrics, ToolCall, metrics_from_dict, parse_stream
from .runner_env import ENGINE, build_env
from .scenario import Scenario

DEFAULT_TIMEOUT_S = 1800  # a busy consolidation on a slow network ran past 1200 s (#84)
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
RUN_NOTES_HEADING = "## 🪵"


@dataclass(frozen=True)
class RunOutputs:
    notes: dict[str, str]  # tag → action-items/context/<TAG>.md
    wrap: str | None  # the captured Slack DM(s)
    run_notes: str | None  # today's 🪵 section
    review_added: str  # what review-queue.md gained since the fixture commit
    calls: dict[str, int]  # stubbed calls per persona


def _fixture_version(vault: Path, rel: str) -> str:
    root = subprocess.run(
        ["git", "-C", str(vault), "rev-list", "--max-parents=0", "HEAD"], capture_output=True, text=True
    ).stdout.split()
    if not root:
        return ""
    shown = subprocess.run(["git", "-C", str(vault), "show", f"{root[0]}:{rel}"], capture_output=True, text=True)
    return shown.stdout if shown.returncode == 0 else ""


def read_outputs(vault: Path, calls_log: Path, today: date) -> RunOutputs:
    ctx = vault / "action-items" / "context"
    notes = {p.stem: p.read_text(encoding="utf-8") for p in sorted(ctx.glob("*.md"))} if ctx.is_dir() else {}
    wraps, calls = [], {}
    if calls_log.exists():
        for line in calls_log.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            calls[row["persona"]] = calls.get(row["persona"], 0) + 1
            if row.get("capture") == "wrap":
                args = row.get("args") or {}
                wraps.append(str(args.get("message") or args.get("text") or ""))
    today_file = vault / "action-items" / f"action-items-{today.isoformat()}.md"
    run_notes = None
    if today_file.is_file():
        text = today_file.read_text(encoding="utf-8")
        if RUN_NOTES_HEADING in text:
            section = text.split(RUN_NOTES_HEADING, 1)[1]
            run_notes = section.split("\n", 1)[1].split("\n## ", 1)[0] if "\n" in section else ""
    rel = "knowledge-base/review-queue.md"
    before = set(_fixture_version(vault, rel).splitlines())
    now = (vault / rel).read_text(encoding="utf-8").splitlines() if (vault / rel).exists() else []
    review_added = "\n".join(ln for ln in now if ln.strip() and ln not in before)
    return RunOutputs(notes, "\n".join(wraps) if wraps else None, run_notes, review_added, calls)


OUTBOUND_DRAFT = ".scout-cache/outbound-draft.md"  # phases/core/privacy.md


class GitReadError(Exception):
    """git couldn't answer; never read as "nothing changed"."""


def _git_lines(vault: Path, *args: str) -> list[str]:
    done = subprocess.run(["git", "-C", str(vault), *args], capture_output=True, text=True)
    if done.returncode != 0:
        raise GitReadError(f"git {' '.join(args)}: exit {done.returncode}: {done.stderr.strip()[:200]}")
    return [line for line in done.stdout.split("\n") if line]


def drafts_changed(vault: Path) -> dict[str, str] | None:
    """``drafts/`` files this run added or changed (committed or not), against the fixture commit.

    None when git can't say, so the check scores it as unknown rather than as no change.
    """
    try:
        root = _git_lines(vault, "rev-list", "--max-parents=0", "HEAD")
        if not root:
            return None
        changed = _git_lines(vault, "diff", "--name-only", root[0], "--", "drafts")
        changed += _git_lines(vault, "ls-files", "--others", "--exclude-standard", "--", "drafts")
    except GitReadError:
        return None
    return {rel: (vault / rel).read_text(encoding="utf-8", errors="replace") for rel in sorted(set(changed))
            if (vault / rel).is_file()}  # fmt: skip


def read_privacy_outputs(
    vault: Path, calls_log: Path, today: date, transcript: Sequence[ToolCall] = ()
) -> PrivacyOutputs:
    rows: list[dict[str, Any]] = []
    if calls_log.exists():
        for line in calls_log.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    daily = vault / "action-items" / f"action-items-{today.isoformat()}.md"
    draft = vault / OUTBOUND_DRAFT
    return PrivacyOutputs(
        daily=daily.read_text(encoding="utf-8") if daily.is_file() else None,
        telegram=tuple(str((r.get("args") or {}).get("body", "")) for r in rows if r.get("persona") == "telegram"),
        slack_draft=draft.read_text(encoding="utf-8") if draft.is_file() else None,
        connector_calls=tuple(r for r in rows if r.get("persona") != "telegram"),
        drafts_changed=drafts_changed(vault),
        transcript=tuple(transcript),
    )


def read_kb_labels(vault: Path) -> dict[str, bool]:
    """``args_hash`` → private, from the kb-read-label hook's rows in ``.scout-logs/kb-reads.jsonl``."""
    log = vault / ".scout-logs" / "kb-reads.jsonl"
    out: dict[str, bool] = {}
    if not log.is_file():
        return out
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("command") == LABEL_COMMAND and isinstance(row.get("args_hash"), str):
            out[row["args_hash"]] = out.get(row["args_hash"], False) or bool(row.get("private_paths"))
    return out


def _score_one(
    name: str, scenario: Scenario, vault: Path, today: date, calls_log: Path, transcript: Sequence[ToolCall]
) -> CheckResult:
    """The privacy checks, which read only what a run sent and ran (and the daily file, when its mode owns it)."""
    if name == "privacy_hygiene":
        return privacy_hygiene(transcript, read_kb_labels(vault), (str(vault), str(vault.resolve())))
    return privacy_canary(
        read_privacy_outputs(vault, calls_log, today, transcript), scenario.permit_canary, scenario.mode
    )


def _score(
    scenario: Scenario,
    vault: Path,
    today: date,
    yesterday: list[ActionItem],
    calls_log: Path,
    transcript: Sequence[ToolCall] = (),
) -> tuple[CheckResult, ...]:
    today_file = vault / "action-items" / f"action-items-{today.isoformat()}.md"
    if today_file.is_file() and CARRY_BANNER in today_file.read_text(encoding="utf-8"):
        # The runner carried yesterday's file in verbatim and the session never rewrote it:
        # scoring that would credit the session with yesterday's placements and no drops.
        # A run that doesn't own the daily file (dreaming) is still scored on what it sent and ran.
        keep = {"privacy_hygiene"} | (set() if owner_surfaces_required(scenario.mode) else {"privacy_canary"})
        if not keep & set(scenario.checks):
            return tuple(CheckResult(name, None, ("not_enriched",)) for name in scenario.checks)
        return tuple(
            _score_one(name, scenario, vault, today, calls_log, transcript)
            if name in keep
            else CheckResult(name, None, ("not_enriched",))
            for name in scenario.checks
        )
    today_items = _items(today_file)
    out = read_outputs(vault, calls_log, today)
    up = [p for p in scenario.personas if p not in scenario.personas_down]
    simple = {
        "context_note": lambda: context_note(today_items, yesterday, out.notes),
        "waiting_format": lambda: waiting_format(today_items, scenario.expected_waiting),
        "no_collapse": lambda: no_collapse(today_items),
        "body_budget": lambda: body_budget(today_items),
        "outage_reported": lambda: outage_reported(scenario.personas_down, out.wrap, out.run_notes, out.calls),
        "no_false_outage": lambda: no_false_outage(up, out.wrap, out.run_notes),
        "demotion": lambda: demotion(today_items, yesterday, out.notes, scenario.expected_demotion),
        "dropoff_caught": lambda: dropoff_caught(
            scenario.dropped_tags, today_items, out.wrap, review_added=out.review_added
        ),
        "privacy_canary": lambda: _score_one("privacy_canary", scenario, vault, today, calls_log, transcript),
        "privacy_hygiene": lambda: _score_one("privacy_hygiene", scenario, vault, today, calls_log, transcript),
    }
    results = []
    for name in scenario.checks:
        if name in simple:
            results.append(simple[name]())
        elif name == "placement":
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
    budget: str | None = None,  # None: the scenario's budget:, else DEFAULT_BUDGET
    hook_grace_s: int = HOOK_GRACE_S,
    arm: str = "head",
    cli_version: str = "",
    checkout: Checkout | None = None,
) -> SampleResult:
    started_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    result_dir = results_root / f"{scenario.name}-{arm}-{time.strftime('%Y%m%d-%H%M%S')}-{rep}"
    vault = result_dir / "vault"
    effective_budget = budget or scenario.budget or DEFAULT_BUDGET
    result_dir.mkdir(parents=True)
    build_vault(
        vault,
        today=today,
        scenario_dir=scenario.files,
        claude_bin=str(SHIM),
        connectors=scenario.connectors,
        max_budget=effective_budget,
        checkout=checkout,
        scheduler_tick=datetime.now(UTC),
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
        pythonpath=checkout.pythonpath if checkout else None,
    )
    runner_timed_out = False
    with (result_dir / "runner.log").open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            ["bash", str(vault / scenario.runner)],
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
        # e.g. error_max_budget_usd: the session didn't finish; say which cap it hit
        aborted = {"reason": metrics.result_subtype, "budget_usd": effective_budget}
    if aborted is None and metrics is not None and metrics.api_error is not None:
        # claude gave up on the API (retries exhausted, no network): its result still says
        # "success". A harness failure, never a score: the run did not do the work.
        aborted = {"reason": "api_error", "detail": metrics.api_error[:200], "api_retries": metrics.api_retries}
    (result_dir / "runner-exit.json").write_text(json.dumps({"exit": runner_exit}), encoding="utf-8")
    transcript = metrics.calls if metrics is not None else ()
    checks = () if aborted else _score(scenario, vault, today, yesterday, result_dir / "calls.jsonl", transcript)
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
        "checkout": {"label": checkout.label, "commit": checkout.commit} if checkout else None,
        "budget_usd": effective_budget,
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
