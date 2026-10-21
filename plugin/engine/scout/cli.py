"""scoutctl CLI entry point.

Top-level imports are intentionally minimal — Typer + stdlib only —
to keep `scoutctl --help` under 100ms. Heavy libraries (textual, rich,
jinja2, watchdog, scout.kb.*, scout.tui.*) must be imported inside
the subcommand functions, not at module level.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import typer

from scout import __version__
from scout.errors import ConfigError, ScoutError

if TYPE_CHECKING:
    from scout.scripts.bootstrap import BootstrapConfig

# Reserved for non-ScoutError exceptions escaping app(). Kept distinct
# from ScoutError.exit_code == 1 so scout-app can decode "the CLI
# crashed in an unexpected way" as its own failure mode.
INTERNAL_ERROR_EXIT_CODE = 70

app = typer.Typer(
    name="scoutctl",
    help="Scout engine control CLI.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode=None,  # avoid importing rich at startup
)


@app.command()
def version() -> None:
    """Print the engine version."""
    print(__version__)


manifest_app = typer.Typer(help="Engine capability manifest operations.")
app.add_typer(manifest_app, name="manifest")


@manifest_app.command("build")
def manifest_build() -> None:
    """Write manifest.json to the engine dir."""
    from scout.manifest import write_manifest

    path = write_manifest()
    print(f"Wrote: {path}")


@manifest_app.command("show")
def manifest_show() -> None:
    """Print the current manifest to stdout."""
    from scout.manifest import build_manifest

    print(build_manifest().to_json())


def _register_action_items() -> None:
    from scout.action_items.cli import app as action_items_app

    app.add_typer(action_items_app, name="action-items")


_register_action_items()


def _register_planning() -> None:
    from scout.planning.cli import app as planning_app

    app.add_typer(planning_app, name="planning")


_register_planning()


hook_app = typer.Typer(help="PostToolUse / lifecycle hook entry points (invoked by Claude Code).")
app.add_typer(hook_app, name="hook")


@hook_app.command("connector-log")
def hook_connector_log() -> None:
    """PostToolUse hook: log one JSONL row per tool call to .scout-logs/."""
    from scout.hooks.connector_log import main as connector_log_main

    raise typer.Exit(connector_log_main())


@hook_app.command("session-tokens")
def hook_session_tokens() -> None:
    """Stop hook: sum message.usage and append a row to .scout-logs/session-tokens.jsonl."""
    from scout.hooks.session_tokens import main as session_tokens_main

    raise typer.Exit(session_tokens_main())


@hook_app.command("session-tool-log")
def hook_session_tool_log() -> None:
    """Stop hook: replay tool calls from the session transcript into connector-calls JSONL.

    Replaces the per-PostToolUse Python spawn in `connector-log` (#72).
    """
    from scout.hooks.session_tool_log import main as session_tool_log_main

    raise typer.Exit(session_tool_log_main())


@hook_app.command("mcp-tool-gate")
def hook_mcp_tool_gate() -> None:
    """PreToolUse hook: deny a restrict_tools custom connector's unlisted MCP tools in scheduled runs."""
    from scout.hooks.mcp_tool_gate import main as mcp_tool_gate_main

    raise typer.Exit(mcp_tool_gate_main())


@hook_app.command("kb-pre-filter")
def hook_kb_pre_filter(
    session_type: str = typer.Option(
        "dreaming",
        "--session-type",
        "-s",
        help="Session type label written to the cache header (briefing | consolidation | dreaming).",
    ),
) -> None:
    """UserPromptSubmit hook: score KB freshness and write .scout-cache/kb-filter.md."""
    from scout.hooks.kb_pre_filter import main as kb_pre_filter_main

    raise typer.Exit(kb_pre_filter_main([session_type]))


kb_app = typer.Typer(help="Knowledge-base structure checks (size budgets, run-diary headings).")
app.add_typer(kb_app, name="kb")


def _kb_repo(repo: Path | None) -> Path:
    from scout import paths
    from scout.kb.git_io import repo_root

    try:
        return repo_root(repo or Path.cwd())
    except Exception:
        return repo or paths.data_dir()


@kb_app.command("lint")
def kb_lint(
    staged: bool = typer.Option(
        False, "--staged", help="Check the staged diff (what the pre-commit hook runs). Default."
    ),
    report: bool = typer.Option(False, "--report", help="Rank tracked KB files that are over budget."),
    top: int = typer.Option(20, "--top", help="Rows to show with --report."),
    lossless_rev: str | None = typer.Option(None, "--lossless-rev", help="Old revision for the lossless check."),
    lossless_path: str | None = typer.Option(
        None, "--lossless-path", help="Vault-relative file for the lossless check."
    ),
    repo: Path | None = typer.Option(None, "--repo", help="Vault repo (default: the git top level of the cwd)."),
) -> None:
    """Lint the vault's KB structure. Exit 1 = blocked (block mode) or lossless tokens missing."""
    root = _kb_repo(repo)
    if lossless_rev or lossless_path:
        from scout.kb.lossless import lossless_check

        if not (lossless_rev and lossless_path):
            print("--lossless-rev and --lossless-path go together", file=sys.stderr)
            raise typer.Exit(2)
        missing = lossless_check(root, lossless_rev, lossless_path)
        for tok in missing:
            print(f"missing: {tok}")
        print(f"lossless: {len(missing)} token(s) missing")
        raise typer.Exit(1 if missing else 0)
    if report:
        from scout.kb.lint import over_budget
        from scout.kb.lint_config import load_lint_config

        rows = over_budget(root, load_lint_config(root))
        print(f"{len(rows)} file(s) over budget")
        for rel, size, budget in rows[:top]:
            print(f"{size / 1024:9.1f} KB  (budget {budget / 1024:.0f} KB, +{(size - budget) / 1024:.1f} KB)  {rel}")
        raise typer.Exit(0)
    from scout.kb.lint import run_hook

    raise typer.Exit(run_hook(root))


@kb_app.command("install-hook")
def kb_install_hook(
    repo: Path | None = typer.Option(None, "--repo", help="Vault repo (default: the git top level of the cwd)."),
) -> None:
    """Install (or refresh) the Scout-managed git pre-commit hook in the vault."""
    from scout.kb.hook import HookConflict, install_hook

    try:
        path = install_hook(_kb_repo(repo))
    except HookConflict as e:
        print(str(e), file=sys.stderr)
        raise typer.Exit(1) from e
    print(f"Installed: {path}")


@kb_app.command("classify")
def kb_classify(
    paths: list[str] = typer.Argument(..., help="Vault files (any spelling: relative, absolute, knowledge_base/...)."),
    reader: str | None = typer.Option(
        None, "--reader", help="owner | autonomous | agent:person:<email> | agent:group:<id>. Can only narrow."
    ),
    as_json: bool = typer.Option(False, "--json", help="Print a JSON list."),
    repo: Path | None = typer.Option(None, "--repo", help="Vault repo (default: the git top level of the cwd)."),
) -> None:
    """Print each file's privacy label (visibility, local-only, shared). Exit 2 = bad path or reader.

    owner and autonomous readers get labels. An agent reader gets
    {"path": ..., "denied": true} for every path, without the file being looked
    at, so a denial says nothing about whether the file exists or what it is.
    """
    import json
    import os

    from scout.kb.privacy import FULL_READERS, label_file, load_policy, normalize_rel
    from scout.kb.reader import resolve_reader

    who = resolve_reader(reader, os.environ)
    rows: list[dict[str, object]] = []
    lines: list[str] = []
    if who not in FULL_READERS:
        rows = [{"path": p, "denied": True} for p in paths]
        lines = [f"{p}: denied" for p in paths]
    else:
        root = _kb_repo(repo)
        policy = load_policy(root)
        for p in paths:
            try:
                rel = normalize_rel(root, p)
            except ValueError as e:
                print(f"error: {e}", file=sys.stderr)
                raise typer.Exit(2) from e
            label = label_file(root, rel, policy)
            rows.append(label.to_json())
            lines.append(f"[private] {rel}" if label.visibility == "private" else rel)
    if as_json:
        print(json.dumps(rows, indent=2))
    else:
        for line in lines:
            print(line)


@kb_app.command("policy")
def kb_policy(
    reader: str | None = typer.Option(
        None, "--reader", help="owner | autonomous | agent:person:<email> | agent:group:<id>. Can only narrow."
    ),
    as_json: bool = typer.Option(False, "--json", help="Print the effective policy as JSON."),
    repo: Path | None = typer.Option(None, "--repo", help="Vault repo (default: the git top level of the cwd)."),
) -> None:
    """Print the effective privacy policy: engine default plus the vault's scout-config.yaml overlay.

    Every reader gets the same output: it is configuration (globs, surface ids),
    not note content. --reader is still resolved, so a widening flag is refused.
    """
    import json
    import os

    from scout.kb.privacy import load_policy
    from scout.kb.reader import resolve_reader

    resolve_reader(reader, os.environ)

    data = load_policy(_kb_repo(repo)).to_json()
    if as_json:
        print(json.dumps(data, indent=2))
        return
    lo = data["local_only"]
    print("local_only.always:    " + " ".join(lo["always"]))
    print("local_only.default:   " + (" ".join(lo["default"]) or "(none)"))
    print("local_only.sync_allow: " + " ".join(lo["sync_allow"]))
    print("visibility_defaults:")
    for entry in data["visibility_defaults"]:
        print(f"  {entry['glob']} -> {entry['visibility']}")
    print("surfaces.owner_only:  " + " ".join(data["surfaces"]["owner_only"]))
    print("surfaces.shared:      " + " ".join(data["surfaces"]["shared"]))
    print("shared_files:         " + (" ".join(data["shared_files"]) or "(none)"))
    print("warnings:             " + (" ".join(data["warnings"]) or "(none)"))


# Top-level command — `connector-health-report` is a script, not a hook
# (it runs AFTER the scheduled session ends, mirroring the bash invocation
# from run-scout.sh). Single-token name keeps the runner-side migration
# path simple: replace `scripts/connector-health-report.sh` with
# `scoutctl connector-health-report`.
@app.command("connector-health-report")
def connector_health_report_cmd() -> None:
    """Roll up connector-calls JSONL into knowledge-base/connector-health.md and fire alerts."""
    from scout.scripts.connector_health_report import main as chr_main

    raise typer.Exit(chr_main())


# `scoutctl budget check` replaces ~/Scout/scripts/budget-check.sh (#74). The
# bash version paid 5+ python3 cold starts per pre-session check; folded
# in-process here it's one.
budget_app = typer.Typer(help="Budget/cost gating for scheduled Scout runs.")
app.add_typer(budget_app, name="budget")


@budget_app.command("check")
def budget_check_cmd(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Print the decision and config."),
) -> None:
    """Decide whether the next session may proceed. Exit codes: 0=proceed, 1=skip, 2=backoff."""
    from scout.scripts.budget_check import run as budget_run

    raise typer.Exit(budget_run(verbose=verbose))


@budget_app.command("resets-at")
def budget_resets_at_cmd(
    log: Path = typer.Argument(..., help="A run log that ended on a Claude usage limit."),
    now: int | None = typer.Option(None, "--now", help="Reference time, epoch seconds (default: the clock)."),
    iso: bool = typer.Option(False, "--iso", help="Print 2026-01-05T13:10:00Z instead of epoch seconds."),
) -> None:
    """Print when the log's usage limit lifts (epoch seconds). Exit 1, silently, if it names no readable time."""
    from datetime import UTC, datetime

    from scout.scripts.rate_limit_reset import reset_from_log

    ref = datetime.fromtimestamp(now, UTC) if now is not None else datetime.now(UTC)
    reset = reset_from_log(log, now=ref)
    if reset is None:
        raise typer.Exit(1)
    typer.echo(reset.strftime("%Y-%m-%dT%H:%M:%SZ") if iso else int(reset.timestamp()))


@budget_app.command("show")
def budget_show_cmd(
    as_json: bool = typer.Option(False, "--json", help="Emit the effective config as JSON."),
) -> None:
    """Print the effective budget config and the gate it computes to."""
    import json as _json

    from scout.scripts.budget_config import show_payload, warn_if_legacy_dotfile

    payload = show_payload()
    if warning := warn_if_legacy_dotfile():
        typer.echo(f"warning: {warning}", err=True)

    if as_json:
        typer.echo(_json.dumps(payload, indent=2))
        return

    typer.echo(f"config:  {payload['config_path']} ({payload['source']})")
    typer.echo(f"daily:   ${payload['daily_usd']:.2f}")
    typer.echo(f"window:  {payload['window_hours']}h → ${payload['window_budget_usd']:.2f}")
    typer.echo(f"skip at: {payload['skip_at_pct']:.0f}% → ${payload['skip_threshold_usd']:.2f}")
    typer.echo(f"backoff: {payload['failure_backoff_minutes']}m after a failed run")


@budget_app.command("set")
def budget_set_cmd(
    daily_usd: float | None = typer.Option(None, "--daily-usd", help="Daily budget estimate, USD."),
    window_hours: int | None = typer.Option(None, "--window-hours", help="Rolling window for cost accumulation."),
    skip_at_pct: float | None = typer.Option(
        None, "--skip-at-pct", help="Skip a session at this percent of the window budget."
    ),
    failure_backoff_minutes: int | None = typer.Option(
        None, "--failure-backoff-minutes", help="Back off this many minutes after a failed run."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit the resulting config as JSON."),
) -> None:
    """Write one or more budget knobs to the vault's scout-config.yaml."""
    import json as _json

    from scout.scripts.budget_config import BudgetWriteError, write_budget

    candidates = {
        "daily_budget_usd": daily_usd,
        "window_hours": window_hours,
        "skip_threshold_pct": skip_at_pct,
        "failure_backoff_min": failure_backoff_minutes,
    }
    updates = {field: value for field, value in candidates.items() if value is not None}
    if not updates:
        typer.echo(
            "nothing to set — pass at least one of --daily-usd, --window-hours, "
            "--skip-at-pct, --failure-backoff-minutes",
            err=True,
        )
        raise typer.Exit(2)

    try:
        payload = write_budget(updates)
    except BudgetWriteError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1) from e

    if as_json:
        typer.echo(_json.dumps(payload, indent=2))
        return
    typer.echo(f"wrote {payload['config_path']}")
    typer.echo(f"window:  {payload['window_hours']}h → ${payload['window_budget_usd']:.2f}")
    typer.echo(f"skip at: {payload['skip_at_pct']:.0f}% → ${payload['skip_threshold_usd']:.2f}")


# `scoutctl session index` builds .scout-cache/sessions-index.json from
# desktop records, transcripts, live PIDs and gh (Agent Sessions plan 1,
# #74 + #75). `session list` reads that index back. `cc-cache` is the
# back-compat alias for `index --render` that vault scripts/cc-session-cache.sh
# still calls — it replaces the old ~/Scout/scripts/cc-session-cache.sh, whose
# bash version spawned one python3 cold start per JSONL file in
# ~/.claude/projects/* plus a 5-stage subprocess pipeline per file.
session_app = typer.Typer(help="Pre-session data caches for scheduled Scout runs.")
app.add_typer(session_app, name="session")


@session_app.command("index")
def session_index_cmd(
    json_out: bool = typer.Option(False, "--json", help="Print the index JSON to stdout."),
    render: bool = typer.Option(False, "--render", help="Also write the cc-sessions.md digest."),
    no_gh: bool = typer.Option(False, "--no-gh", help="Skip gh; PR states come from cache or read 'unknown'."),
    hours: int = typer.Option(24, "--hours", "-h", help="Activity window for the digest (default 24h)."),
    instance_name: str = typer.Option("Scout", "--instance-name", help="Instance name used in the digest header."),
    timezone: str = typer.Option(None, "--timezone", help="IANA zone for rendered timestamps (default: vault's)."),
    strict: bool = typer.Option(False, "--strict", help="Exit 1 if any source reported an error."),
) -> None:
    """Build .scout-cache/sessions-index.json from desktop records, transcripts, PIDs and gh."""
    from scout.sessions.index import main as index_main

    raise typer.Exit(
        index_main(
            json_out=json_out,
            render=render,
            use_gh=not no_gh,
            hours=hours,
            instance_name=instance_name,
            tz_name=timezone,
            strict=strict,
        )
    )


@session_app.command("list")
def session_list_cmd(
    state: list[str] = typer.Option([], "--state", help="Only these states (repeatable)."),
    project: str = typer.Option(None, "--project", help="Project name, folder name or key."),
    include_archived: bool = typer.Option(False, "--include-archived"),
    include_scout_runs: bool = typer.Option(False, "--include-scout-runs"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """List sessions from the written index (builds it first if missing)."""
    from scout.sessions.index import list_main

    raise typer.Exit(
        list_main(
            states=list(state),
            project=project,
            include_archived=include_archived,
            include_scout_runs=include_scout_runs,
            json_out=json_out,
        )
    )


@session_app.command("cc-cache")
def session_cc_cache_cmd(
    hours: int = typer.Option(24, "--hours", "-h", help="Activity window for the digest (default 24h)."),
    instance_name: str = typer.Option("Scout", "--instance-name", help="Instance name used in the digest header."),
    timezone: str = typer.Option(None, "--timezone", help="IANA zone for rendered timestamps (default: vault's)."),
) -> None:
    """Back-compat alias for `session index --render` (vault scripts/cc-session-cache.sh calls this)."""
    from scout.scripts.cc_session_cache import main as cc_main

    raise typer.Exit(cc_main(hours=hours, instance_name=instance_name, tz_name=timezone))


# `scoutctl heartbeat run` replaces ~/Scout/scripts/heartbeat.sh (#74 + #79).
# The bash version paid 3 python3 cold starts every 30 min just to walk the
# same tracker for three different "time since" values. Folded into one pass
# here.
heartbeat_app = typer.Typer(help="Heartbeat — fires every 30 min to maybe launch a session.")
app.add_typer(heartbeat_app, name="heartbeat")


@heartbeat_app.command("run")
def heartbeat_run_cmd(
    dry_run: bool = typer.Option(False, "--dry-run", help="Decide and log but do not launch the runner."),
) -> None:
    """Apply the heartbeat gating policy and optionally launch a runner."""
    from scout.scripts.heartbeat import main as heartbeat_main

    raise typer.Exit(heartbeat_main(dry_run=dry_run))


# `scoutctl pre-session data` replaces ~/Scout/scripts/pre-session-data.sh
# (#74 + #76). The bash version ran a 4-subprocess pipeline per KB file plus
# two python3 cold starts just to JSON-encode stdin. Folded into one Python
# process here with an mtime cache for the KB date extraction.
pre_session_app = typer.Typer(help="Pre-session data gathering for scheduled Scout runs.")
app.add_typer(pre_session_app, name="pre-session")


@pre_session_app.command("data")
def pre_session_data_cmd(
    session_type: str = typer.Argument(
        "unknown",
        help="Session type label (briefing | consolidation | dreaming | research).",
    ),
) -> None:
    """Write .scout-cache/session-context.json from current vault + git + gh state."""
    from scout.scripts.pre_session_data import main as psd_main

    raise typer.Exit(psd_main(session_type))


def _read_text_arg(path: str | Path) -> str:
    """The UTF-8 text of a file argument; ``-`` reads stdin."""
    return sys.stdin.read() if str(path) == "-" else Path(path).read_text(encoding="utf-8")


def _register_connectors() -> None:
    """scoutctl connectors {list,show,reload} — read-only roster ops in v0.4."""
    connectors_app = typer.Typer(help="Connector roster operations (read-only in v0.4).")
    app.add_typer(connectors_app, name="connectors")

    @connectors_app.command("list")
    def cli_connectors_list(
        json_out: bool = typer.Option(False, "--json", help="Emit the roster as JSON (consumed by Scout.app)."),
    ) -> None:
        """List the registered connector roster (shipped + overlay + custom)."""
        import json as _json

        from scout.connectors import load_registry

        reg = load_registry()
        if json_out:
            rows = [
                {
                    "key": key,
                    "display_name": reg[key].display_name,
                    "tier": reg[key].tier.value,
                    "required_in_types": [t.value for t in reg[key].required_in_types],
                }
                for key in sorted(reg.keys())
            ]
            typer.echo(_json.dumps({"connectors": rows}, indent=2))
            return
        for key in sorted(reg.keys()):
            c = reg[key]
            typer.echo(f"{key}\t{c.tier.value}\t{c.display_name}")

    @connectors_app.command("show")
    def cli_connectors_show(key: str) -> None:
        """Show one connector's full record as JSON."""
        import json as _json

        from scout.connectors import load_registry
        from scout.errors import ConfigError

        reg = load_registry()
        if key not in reg:
            raise ConfigError(f"unknown connector: {key}")
        c = reg[key]
        record = {
            "key": c.key,
            "display_name": c.display_name,
            "tier": c.tier.value,
            "capabilities": [cap.value for cap in c.capabilities],
            "required_in": "all" if c.required_in == "all" else list(c.required_in),
            "required_in_types": [t.value for t in c.required_in_types],
            "remediation": {
                "first_fix": c.remediation.first_fix,
                "detail": c.remediation.detail,
            },
            "notes": c.notes,
        }
        typer.echo(_json.dumps(record, indent=2))

    @connectors_app.command("reload")
    def cli_connectors_reload() -> None:
        """Force-reload the YAML (operational signal; load_registry is uncached in v0.4)."""
        from scout.connectors import load_registry

        load_registry()  # exercise the path; raises ConfigError on bad YAML
        typer.echo("reloaded")

    @connectors_app.command("probe-registry")
    def cli_connectors_probe_registry(
        json_out: bool = typer.Option(
            False, "--json", help="Emit the merged registry as JSON (consumed by /scout-setup)."
        ),
    ) -> None:
        """Print the connector probe registry: shipped union the ~/Scout overlay.

        Merges templates/connector-probes.yaml with an optional
        ~/Scout/connector-probes.local.yaml (overlay wins on key collision)
        so custom connector probes survive plugin updates (#97).
        """
        import json as _json
        from typing import Any

        from scout.scripts.connector_probes import ProbeKind, resolve_registry

        reg = resolve_registry()
        if json_out:
            out: dict[str, dict[str, Any]] = {}
            for name in sorted(reg):
                p = reg[name]
                entry: dict[str, Any] = {"kind": p.kind.value, "needs_user_input": p.needs_user_input}
                if p.kind is ProbeKind.BASH:
                    entry["bash_command"] = p.bash_command
                else:
                    entry["tool_chain"] = p.tool_chain
                out[name] = entry
            typer.echo(_json.dumps(out, indent=2))
        else:
            for name in sorted(reg):
                p = reg[name]
                primary = p.bash_command if p.kind is ProbeKind.BASH else (p.tool_chain[0] if p.tool_chain else "")
                typer.echo(f"{name}\t{p.kind.value}\t{primary}")

    @connectors_app.command("detect")
    def cli_connectors_detect(
        json_out: bool = typer.Option(
            False, "--json", help="Emit detections as JSON (consumed by Scout.app onboarding)."
        ),
        claude_bin: str = typer.Option("claude", "--claude-bin", help="Claude Code binary used for `claude mcp list`."),
        timeout: float = typer.Option(60.0, "--timeout", help="Seconds to wait for `claude mcp list`."),
    ) -> None:
        """Detect which connectors are reachable right now, without an LLM (spec E4)."""
        import json as _json

        from scout.scripts.connector_detect import detect, run_bash_probe, run_claude_mcp_list, to_json_dict
        from scout.scripts.connector_probes import resolve_registry

        reg = resolve_registry()
        dets = detect(reg, mcp_list_output=run_claude_mcp_list(claude_bin, timeout=timeout), run_bash=run_bash_probe)
        if json_out:
            typer.echo(_json.dumps(to_json_dict(dets), indent=2, sort_keys=True))
        else:
            for name, d in dets.items():
                typer.echo(f"{name}\t{d.status.value}\t{d.evidence}")

    @connectors_app.command("uncovered")
    def cli_connectors_uncovered(
        json_out: bool = typer.Option(False, "--json", help="Emit JSON (consumed by Scout.app and scoutctl setup)."),
        claude_bin: str = typer.Option("", "--claude-bin", help="Claude Code binary. Default: auto-detect."),
        timeout: float = typer.Option(60.0, "--timeout", help="Seconds to wait for `claude mcp list`."),
    ) -> None:
        """Connected MCP servers that no connector reads yet (no LLM)."""
        import json as _json

        from scout import paths as _paths
        from scout.scripts import connector_detect, connector_probes
        from scout.scripts.bootstrap import resolve_claude_bin
        from scout.scripts.connector_uncovered import custom_connector_servers, find_uncovered

        listing = connector_detect.run_claude_mcp_list(resolve_claude_bin(claude_bin), timeout=timeout)
        payload = find_uncovered(
            listing,
            registry=connector_probes.resolve_registry(),
            custom_servers=custom_connector_servers(_paths.data_dir()),
        )
        if json_out:
            typer.echo(_json.dumps(payload, indent=2))
        else:
            for s in payload["servers"]:
                typer.echo(f"{s['name']}\t{s['status']}")
        if payload["error"]:
            if not json_out:
                typer.echo(f"error: {payload['error']}", err=True)
            raise typer.Exit(code=1)

    @connectors_app.command("setup")
    def cli_connectors_setup(
        claude_bin: str = typer.Option("", "--claude-bin", help="Claude Code binary. Default: auto-detect."),
    ) -> None:
        """Offer every connected tool Scout doesn't read yet, draft it, and add the ones you keep."""
        from scout import paths as _paths
        from scout.setup_wizard import TtyPrompter, default_deps, run_connectors_setup

        prompter = TtyPrompter.open()
        if prompter is None:
            typer.echo("error: needs a terminal. In Claude Code, run /scout-connect instead.", err=True)
            raise typer.Exit(code=2)
        raise typer.Exit(code=run_connectors_setup(_paths.data_dir(), prompter, default_deps(claude_bin)))

    @connectors_app.command("snapshot")
    def cli_connectors_snapshot(
        target: Path | None = typer.Option(
            None,
            "--target",
            "-t",
            help=(
                "Where to write the snapshot. Defaults to scout-plugin's "
                "canonical engine/scout/connectors.snapshot.json (the file "
                "CI verifies)."
            ),
        ),
        check: bool = typer.Option(
            False,
            "--check",
            help="Exit 1 if on-disk differs from would-write; print unified diff.",
        ),
        also_write_app_fixture: bool = typer.Option(
            True,
            "--also-write-app-fixture/--no-also-write-app-fixture",
            help=(
                "Also write the scout-app bundled fixture at "
                "~/scout-app/ScoutTests/Fixtures/connectors.snapshot.json. "
                "Best-effort: silently skipped (with a warning) if the path "
                "doesn't exist on this machine. Default: enabled."
            ),
        ),
    ) -> None:
        """Write or verify connectors.snapshot.json (consumed by scout-app).

        Default behavior writes BOTH the canonical snapshot in scout-plugin
        AND the scout-app bundled fixture, so a single invocation keeps both
        repos in sync after a connectors.yaml edit. Pass
        --no-also-write-app-fixture on a build agent that doesn't have
        scout-app checked out.
        """
        from scout.scripts.connectors_snapshot import (
            app_fixture_snapshot_path,
            canonical_snapshot_path,
            check_snapshot,
            write_snapshot,
        )

        resolved_target = target if target is not None else canonical_snapshot_path()

        if check:
            ok, diff = check_snapshot(resolved_target)
            if ok:
                typer.echo(f"connectors snapshot OK: {resolved_target}")
                return
            typer.echo(diff, err=True)
            typer.echo(
                f"Drift detected: regenerate with `scoutctl connectors snapshot --target {resolved_target}`.",
                err=True,
            )
            raise typer.Exit(code=1)

        write_snapshot(resolved_target)
        typer.echo(f"Wrote: {resolved_target}")

        # Best-effort dual-write so a single invocation keeps both repos in sync.
        if also_write_app_fixture:
            app_fixture = app_fixture_snapshot_path()
            if app_fixture == resolved_target:
                # Operator pointed --target at the app fixture; primary write covered it.
                pass
            elif app_fixture.parent.is_dir():
                write_snapshot(app_fixture)
                typer.echo(f"Wrote: {app_fixture}")
            else:
                typer.echo(
                    f"warning: skipped scout-app fixture write — {app_fixture.parent} "
                    "is not a directory on this machine. Pass --no-also-write-app-fixture "
                    "to silence.",
                    err=True,
                )

    custom_app = typer.Typer(help="Custom connectors: any tool Scout should read (connectors.custom.yaml).")
    connectors_app.add_typer(custom_app, name="custom")

    def _plugin_root() -> Path:
        return Path(__file__).parent.parent.parent

    def _emit(payload: dict, code: int = 0) -> None:
        import json as _json

        # default=str: a value JSON cannot encode must never cost the one-JSON-object contract.
        typer.echo(_json.dumps(payload, indent=2, default=str))
        raise typer.Exit(code=code)

    def _read_definition(file: str):
        """Parse a definition file ('-' = stdin). Returns (data, None) or (None, Outcome)."""
        import yaml as _yaml

        from scout.custom_connectors import Issue
        from scout.scripts.custom_connector_ops import Outcome

        try:
            return _yaml.safe_load(_read_text_arg(file)), None
        except (OSError, UnicodeDecodeError, _yaml.YAMLError) as e:
            return None, Outcome("invalid", issues=[Issue("definition", f"could not be read: {e}")])

    def _read_inputs(items: list[str]) -> dict[str, str]:
        """``--input NAME=VALUE`` flags as a mapping; a malformed one emits ``invalid`` and exits."""
        from scout.custom_connectors import Issue
        from scout.scripts.custom_connector_ops import Outcome

        inputs: dict[str, str] = {}
        for item in items:
            name, sep, value = item.partition("=")
            if not sep or not name:
                # Not echoed: a bare value with no NAME= may be a secret pasted by mistake.
                bad = Outcome("invalid", issues=[Issue("--input", "every --input must be NAME=VALUE")])
                _emit(bad.to_json(), bad.exit_code)
            inputs[name] = value
        return inputs

    @custom_app.command("add")
    def cli_custom_add(
        file: str = typer.Option(..., "--file", help="Definition (YAML or JSON) with a `key` field; '-' reads stdin."),
        input_: list[str] = typer.Option([], "--input", help="NAME=VALUE for a needs_user_input entry (repeatable)."),
        dry_run: bool = typer.Option(False, "--dry-run", help="Show the sections it would render; write nothing."),
        unverified: bool = typer.Option(False, "--unverified", help="Skip the bash probe (the app path)."),
        no_wait: bool = typer.Option(
            False, "--no-wait", help="Don't wait for a held session lock; fail fast with status busy."
        ),
    ) -> None:
        """Add or replace a custom connector and apply it to the live brain files."""
        from scout import __version__
        from scout import paths as _paths
        from scout.scripts.custom_connector_ops import add

        data, failed = _read_definition(file)
        if failed is not None:
            _emit(failed.to_json(), failed.exit_code)
        inputs = _read_inputs(input_)
        outcome = add(
            _paths.data_dir(),
            data,
            plugin_root=_plugin_root(),
            plugin_version=__version__,
            inputs=inputs,
            dry_run=dry_run,
            unverified=unverified,
            wait=not no_wait,
        )
        _emit(outcome.to_json(), outcome.exit_code)

    @custom_app.command("remove")
    def cli_custom_remove(
        key: str,
        no_wait: bool = typer.Option(
            False, "--no-wait", help="Don't wait for a held session lock; fail fast with status busy."
        ),
    ) -> None:
        """Remove a custom connector and apply the removal."""
        from scout import __version__
        from scout import paths as _paths
        from scout.scripts.custom_connector_ops import remove

        outcome = remove(
            _paths.data_dir(), key, plugin_root=_plugin_root(), plugin_version=__version__, wait=not no_wait
        )
        _emit(outcome.to_json(), outcome.exit_code)

    @custom_app.command("validate")
    def cli_custom_validate(
        file: str = typer.Option(..., "--file", help="Definition file; '-' reads stdin."),
        input_: list[str] = typer.Option([], "--input", help="NAME=VALUE to check as `add` would (repeatable)."),
    ) -> None:
        """Validate one definition (and any --input values) without writing anything."""
        from scout.scripts.custom_connector_ops import validate

        data, failed = _read_definition(file)
        inputs = _read_inputs(input_)
        outcome = failed if failed is not None else validate(data, plugin_root=_plugin_root(), inputs=inputs)
        _emit(outcome.to_json(), outcome.exit_code)

    @custom_app.command("list")
    def cli_custom_list() -> None:
        """List custom connectors and any problems in connectors.custom.yaml."""
        from scout import paths as _paths
        from scout.scripts.custom_connector_ops import list_custom

        _emit(list_custom(_paths.data_dir(), plugin_root=_plugin_root()))

    @custom_app.command("draft")
    def cli_custom_draft(
        server: str = typer.Option(..., "--server", help="The server's name as `claude mcp list` shows it."),
        json_out: bool = typer.Option(True, "--json", hidden=True, help="Always JSON; accepted for symmetry."),
        claude_bin: str = typer.Option("", "--claude-bin", help="Claude Code binary. Default: auto-detect."),
        model: str = typer.Option("sonnet", "--model", help="Model for the drafting call."),
        timeout: float = typer.Option(120.0, "--timeout", help="Seconds per headless call."),
        taken: list[str] = typer.Option(
            [], "--taken", help="A key already used in this setup but not yet in the vault. Repeatable."
        ),
    ) -> None:
        """Draft custom-connector definitions for one connected server (headless claude -p; the engine checks them)."""
        from scout import paths as _paths
        from scout.scripts import connector_draft
        from scout.scripts.bootstrap import resolve_claude_bin

        payload = connector_draft.draft(
            server,
            plugin_root=_plugin_root(),
            vault=_paths.data_dir(),
            claude_bin=resolve_claude_bin(claude_bin),
            model=model,
            timeout=timeout,
            taken=set(taken),
        )
        _emit(payload, connector_draft.EXIT_CODES[payload["status"]])

    @connectors_app.command("presets")
    def cli_connectors_presets() -> None:
        """Preset names and their default guidance per activity (JSON)."""
        from scout.scripts.custom_connector_ops import presets_json

        _emit(presets_json(_plugin_root()))


_register_connectors()


def _register_schedule() -> None:
    """scoutctl schedule {list,show,validate,init,reload} — vault schedule operations.

    Tasks 3 (tick, fire-now), 4 (install-plist), 5 (install-wake-schedule),
    and 8 (snapshot, list-upcoming) extend this same sub-app with more
    commands. Keep this function open for extension.
    """

    schedule_app = typer.Typer(help="Schedule operations (vault schedule.yaml).")
    app.add_typer(schedule_app, name="schedule")

    @schedule_app.command("list")
    def cli_schedule_list(
        as_json: bool = typer.Option(
            False,
            "--json/--no-json",
            help=(
                "Emit a JSON array of full slot records (key/type/runner/"
                "fires_at_local/weekdays/missed_window_hours/on_miss/"
                "cooldown_minutes/budget_usd/tz/runtime/model) instead of "
                "tab-separated lines. Plan 6's ScheduleEditService consumes "
                "this for the in-app editor."
            ),
        ),
    ) -> None:
        """List the registered schedule slots."""
        import json as _json

        from scout import paths as _paths
        from scout.schedule import load_default_schedule, load_schedule

        vault_path = _paths.data_dir() / ".scout-state" / "schedule.yaml"
        sched = load_schedule(vault_path) if vault_path.exists() else load_default_schedule()

        if as_json:
            records = [
                {
                    "key": sched[key].key,
                    "type": sched[key].type.value,
                    "runner": sched[key].runner,
                    "fires_at_local": sched[key].fires_at_local,
                    "weekdays": list(sched[key].weekdays),
                    "missed_window_hours": sched[key].missed_window_hours,
                    "on_miss": sched[key].on_miss.value,
                    "cooldown_minutes": sched[key].cooldown_minutes,
                    "budget_usd": sched[key].budget_usd,
                    "tz": sched[key].tz,
                    "runtime": sched[key].runtime.value,
                    "model": sched[key].model,
                }
                for key in sorted(sched.keys())
            ]
            typer.echo(_json.dumps(records))
            return

        for key in sorted(sched.keys()):
            slot = sched[key]
            typer.echo(
                f"{key}\t{slot.type.value}\t{slot.fires_at_local}\t{','.join(slot.weekdays)}\t{slot.on_miss.value}"
            )

    @schedule_app.command("show")
    def cli_schedule_show(key: str) -> None:
        """Show one slot's full record as JSON."""
        import json as _json

        from scout import paths as _paths
        from scout.schedule import load_default_schedule, load_schedule

        vault_path = _paths.data_dir() / ".scout-state" / "schedule.yaml"
        sched = load_schedule(vault_path) if vault_path.exists() else load_default_schedule()
        if key not in sched:
            typer.echo(f"unknown slot: {key}", err=True)
            raise typer.Exit(code=1)
        slot = sched[key]
        record = {
            "key": slot.key,
            "type": slot.type.value,
            "runner": slot.runner,
            "fires_at_local": slot.fires_at_local,
            "weekdays": list(slot.weekdays),
            "missed_window_hours": slot.missed_window_hours,
            "on_miss": slot.on_miss.value,
            "cooldown_minutes": slot.cooldown_minutes,
            "budget_usd": slot.budget_usd,
            "tz": slot.tz,
            "model": slot.model,
        }
        typer.echo(_json.dumps(record, indent=2))

    @schedule_app.command("validate")
    def cli_schedule_validate(
        target: Path | None = typer.Option(
            None,
            "--target",
            "-t",
            help=(
                "Validate the schedule.yaml at this path instead of the vault canonical. "
                "Used by scout-app's editor to validate candidate writes before atomic-rename."
            ),
        ),
    ) -> None:
        """Re-load the schedule (canonical + overlay if present); exit 0 on success."""
        from scout import paths as _paths
        from scout.schedule import load_default_schedule, load_schedule

        if target is not None:
            if not target.exists():
                typer.echo(f"target does not exist: {target}", err=True)
                raise typer.Exit(code=1)
            try:
                load_schedule(target)
            except ConfigError as e:
                typer.echo(str(e), err=True)
                raise typer.Exit(code=1) from e
            typer.echo(f"schedule OK: {target}")
            return

        vault_path = _paths.data_dir() / ".scout-state" / "schedule.yaml"
        if vault_path.exists():
            load_schedule(vault_path)
            typer.echo(f"schedule OK: {vault_path}")
        else:
            load_default_schedule()
            typer.echo("schedule OK: (no vault file; using plugin defaults)")

    @schedule_app.command("init")
    def cli_schedule_init(
        force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing vault file."),
    ) -> None:
        """Seed the vault schedule.yaml from plugin defaults."""
        import shutil

        from scout import paths as _paths

        target = _paths.data_dir() / ".scout-state" / "schedule.yaml"
        if target.exists() and not force:
            typer.echo(
                f"{target} exists; refusing to overwrite. Use --force to replace.",
                err=True,
            )
            raise typer.Exit(code=1)
        target.parent.mkdir(parents=True, exist_ok=True)
        source = Path(__file__).parent / "defaults" / "schedule.yaml"
        shutil.copy2(source, target)
        typer.echo(f"wrote: {target}")

    @schedule_app.command("reload")
    def cli_schedule_reload() -> None:
        """Force-reload the schedule (forward-compat signal; loader has no cache in v0.5)."""
        from scout.schedule import load_default_schedule

        load_default_schedule()
        typer.echo("reloaded")

    @schedule_app.command("tick")
    def cli_schedule_tick() -> None:
        """Run a single dispatch tick. Invoked by com.scout.schedule-tick.plist every 5 min."""
        from scout.scripts.schedule_tick import main as _main

        raise typer.Exit(code=_main())

    @schedule_app.command("fire-now")
    def cli_schedule_fire_now(slot_key: str) -> None:
        """Manually fire a slot, bypassing the dispatcher's policy logic."""
        from scout.scripts.schedule_tick import fire_now as _fire_now

        ev = _fire_now(slot_key)
        if ev.kind == "slot.fire_failed":
            typer.echo(f"failed: {(ev.payload or {}).get('error', 'unknown')}", err=True)
            raise typer.Exit(code=1)
        typer.echo(f"fired: {slot_key}")

    @schedule_app.command("install-plist")
    def cli_schedule_install_plist(
        force: bool = typer.Option(False, "--force", "-f"),
        bootstrap: bool = typer.Option(
            True,
            "--bootstrap/--no-bootstrap",
            help="Run launchctl bootstrap to load the job after writing the plist.",
        ),
        uninstall: bool = typer.Option(
            False,
            "--uninstall",
            help="Remove the plist (and bootout the job) instead of installing.",
        ),
    ) -> None:
        """Install or remove com.scout.schedule-tick.plist in ~/Library/LaunchAgents/.

        The scoutctl path written into the plist is the console script beside
        the interpreter running this command (see ``resolve_scoutctl_bin``).
        No override knob is exposed by design — the scheduler should always
        point at the venv that matches the currently-loaded engine.
        """
        from pathlib import Path as _Path

        from scout import paths as _paths
        from scout.scripts.install_schedule_plist import install_plist as _i
        from scout.scripts.install_schedule_plist import uninstall_plist as _u

        if uninstall:
            _u(bootout=bootstrap)
            typer.echo("uninstalled com.scout.schedule-tick.plist")
            return
        try:
            target = _i(home=_Path.home(), force=force, bootstrap=bootstrap, vault=_paths.data_dir())
            typer.echo(f"installed: {target}")
        except FileExistsError as e:
            typer.echo(f"plist already exists at {e}; use --force to overwrite", err=True)
            raise typer.Exit(code=1) from e

    @schedule_app.command("install-wake-schedule")
    def cli_schedule_install_wake_schedule(
        uninstall: bool = typer.Option(False, "--uninstall"),
        dry_run: bool = typer.Option(False, "--dry-run"),
    ) -> None:
        """Install (or remove) a pmset repeat rule that wakes the Mac for the earliest weekday slot.

        AC-only: macOS standby suppresses wake timers when on battery + lid closed.
        Keep the laptop plugged in if you need guaranteed live firing.
        """
        from scout import paths as _paths
        from scout.schedule import load_default_schedule, load_schedule
        from scout.scripts.install_wake_schedule import (
            install_wake_schedule as _i,
        )
        from scout.scripts.install_wake_schedule import (
            uninstall_wake_schedule as _u,
        )

        if uninstall:
            typer.echo(_u(dry_run=dry_run))
            return
        vault = _paths.data_dir() / ".scout-state" / "schedule.yaml"
        sched = load_schedule(vault) if vault.exists() else load_default_schedule()
        typer.echo(
            "Note: pmset wake-schedule is AC-only. On battery + lid closed, "
            "Apple Silicon laptops enter standby and ignore wake timers. "
            "Keep the laptop plugged in if you need guaranteed live firing."
        )
        typer.echo(_i(sched, dry_run=dry_run))

    @schedule_app.command("list-upcoming")
    def cli_schedule_list_upcoming(
        window: float = typer.Option(24.0, "--window", help="Look-ahead window in hours."),
        use_json: bool = typer.Option(
            True, "--json/--no-json", help="Emit JSON array (default) or tab-separated rows."
        ),
    ) -> None:
        """List the next scheduled fire time for each slot within the given window.

        JSON output (default) is an array sorted by scheduled_at_utc:
            [{slot_key, slot_type, scheduled_at_local, scheduled_at_utc}, ...]
        Slots whose next fire falls outside the window are omitted.
        """
        import json as _json
        from datetime import UTC
        from datetime import datetime as _datetime
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        from scout import paths as _paths
        from scout.schedule import load_default_schedule, load_schedule, next_fire_per_slot
        from scout.scripts.schedule_tick import _local_tz_name

        vault_path = _paths.data_dir() / ".scout-state" / "schedule.yaml"
        sched = load_schedule(vault_path) if vault_path.exists() else load_default_schedule()

        # Reuse the canonical local-tz resolver from schedule_tick — keeps
        # CLI and dispatcher in lockstep if the helper grows fallbacks.
        try:
            local_tz: ZoneInfo = ZoneInfo(_local_tz_name())
        except ZoneInfoNotFoundError:
            local_tz = ZoneInfo("UTC")

        now = _datetime.now(tz=local_tz)

        fires = next_fire_per_slot(sched, now=now, window_hours=window)

        if use_json:
            records = []
            for key, fire_dt in sorted(fires, key=lambda x: (x[0],)):
                slot = sched[key]
                utc_dt = fire_dt.astimezone(UTC)
                records.append(
                    {
                        "slot_key": key,
                        "slot_type": slot.type.value,
                        "scheduled_at_local": fire_dt.isoformat(),
                        "scheduled_at_utc": utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    }
                )
            # Sort by slot_key alphabetically per spec
            records.sort(key=lambda r: r["slot_key"])
            typer.echo(_json.dumps(records))
        else:
            for key, fire_dt in sorted(fires, key=lambda x: x[0]):
                slot = sched[key]
                typer.echo(f"{key}\t{slot.type.value}\t{fire_dt.isoformat()}")

    @schedule_app.command("snapshot")
    def cli_schedule_snapshot(
        target: Path | None = typer.Option(
            None,
            "--target",
            "-t",
            help=(
                "Where to write the snapshot. Defaults to scout-plugin's "
                "canonical engine/scout/schedule.snapshot.json (the file "
                "CI verifies)."
            ),
        ),
        check: bool = typer.Option(
            False,
            "--check",
            help="Exit 1 if on-disk differs from would-write; print unified diff.",
        ),
        also_write_app_fixture: bool = typer.Option(
            True,
            "--also-write-app-fixture/--no-also-write-app-fixture",
            help=(
                "Also write the scout-app bundled fixture at "
                "~/scout-app/ScoutTests/Fixtures/schedule.snapshot.json. "
                "Best-effort: silently skipped (with a warning) if the path "
                "doesn't exist on this machine. Default: enabled."
            ),
        ),
    ) -> None:
        """Write or verify schedule.snapshot.json (consumed by scout-app).

        Default behavior writes BOTH the canonical snapshot in scout-plugin
        AND the scout-app bundled fixture, so a single invocation keeps both
        repos in sync after a schedule.yaml edit. Pass
        --no-also-write-app-fixture on a build agent that doesn't have
        scout-app checked out.
        """
        from scout.scripts.schedule_snapshot import (
            app_fixture_snapshot_path,
            canonical_snapshot_path,
            check_snapshot,
            write_snapshot,
        )

        resolved_target = target if target is not None else canonical_snapshot_path()

        if check:
            ok, diff = check_snapshot(resolved_target)
            if ok:
                typer.echo(f"schedule snapshot OK: {resolved_target}")
                return
            typer.echo(diff, err=True)
            typer.echo(
                f"Drift detected: regenerate with `scoutctl schedule snapshot --target {resolved_target}`.",
                err=True,
            )
            raise typer.Exit(code=1)

        write_snapshot(resolved_target)
        typer.echo(f"Wrote: {resolved_target}")

        # Best-effort dual-write so a single invocation keeps both repos in sync.
        if also_write_app_fixture:
            app_fixture = app_fixture_snapshot_path()
            if app_fixture == resolved_target:
                # Operator pointed --target at the app fixture; primary write covered it.
                pass
            elif app_fixture.parent.is_dir():
                write_snapshot(app_fixture)
                typer.echo(f"Wrote: {app_fixture}")
            else:
                typer.echo(
                    f"warning: skipped scout-app fixture write — {app_fixture.parent} "
                    "is not a directory on this machine. Pass --no-also-write-app-fixture "
                    "to silence.",
                    err=True,
                )

    @schedule_app.command("install-heartbeat-plist")
    def cli_schedule_install_heartbeat_plist(
        force: bool = typer.Option(False, "--force", "-f"),
        bootstrap: bool = typer.Option(True, "--bootstrap/--no-bootstrap"),
        uninstall: bool = typer.Option(False, "--uninstall"),
    ) -> None:
        """Install or remove com.scout.heartbeat.plist."""
        from scout import paths as _paths
        from scout.scripts.install_heartbeat_plist import (
            install_plist as _i,
        )
        from scout.scripts.install_heartbeat_plist import (
            uninstall_plist as _u,
        )

        if uninstall:
            _u(bootout=bootstrap)
            typer.echo("uninstalled com.scout.heartbeat.plist")
            return
        try:
            target = _i(home=Path.home(), force=force, bootstrap=bootstrap, vault=_paths.data_dir())
            typer.echo(f"installed: {target}")
        except FileExistsError as e:
            typer.echo(f"plist exists at {e}; use --force to overwrite", err=True)
            raise typer.Exit(code=1) from e

    @schedule_app.command("install-cron")
    def cli_schedule_install_cron(
        uninstall: bool = typer.Option(False, "--uninstall"),
    ) -> None:
        """Install or remove the Linux scout-managed crontab block.

        The scoutctl path written into the cron block is derived from the
        running engine's plugin root (same contract as ``install-plist``), and
        the block runs the vault at ``$SCOUT_DATA_DIR`` (default ~/Scout).
        """
        from scout import paths as _paths
        from scout.scripts.install_cron import (
            CrontabApplyError,
        )
        from scout.scripts.install_cron import (
            install_cron as _i,
        )
        from scout.scripts.install_cron import (
            uninstall_cron as _u,
        )

        try:
            if uninstall:
                _u(home=Path.home())
                typer.echo("removed scout-managed crontab block")
            else:
                _i(home=Path.home(), vault=_paths.data_dir())
                typer.echo("installed scout-managed crontab block")
        except CrontabApplyError as e:
            typer.echo(f"crontab apply failed: {e}", err=True)
            raise typer.Exit(code=1) from e

    @schedule_app.command("install-all")
    def cli_schedule_install_all(
        uninstall: bool = typer.Option(False, "--uninstall"),
        force: bool = typer.Option(False, "--force"),
    ) -> None:
        """Platform-aware installer (launchd on macOS, cron on Linux)."""
        import platform as _platform

        system = _platform.system()
        if system == "Darwin":
            from scout import paths as _paths
            from scout.scripts.install_heartbeat_plist import (
                install_plist as install_hb,
            )
            from scout.scripts.install_heartbeat_plist import (
                uninstall_plist as uninstall_hb,
            )
            from scout.scripts.install_schedule_plist import (
                install_plist as install_st,
            )
            from scout.scripts.install_schedule_plist import (
                uninstall_plist as uninstall_st,
            )

            if uninstall:
                uninstall_st(bootout=True)
                uninstall_hb(bootout=True)
                typer.echo("uninstalled launchd plists")
                return
            install_st(home=Path.home(), force=force, bootstrap=True, vault=_paths.data_dir())
            install_hb(home=Path.home(), force=force, bootstrap=True, vault=_paths.data_dir())
            typer.echo("installed launchd plists")
        elif system == "Linux":
            from scout.scripts.install_cron import install_cron, uninstall_cron

            if uninstall:
                uninstall_cron(home=Path.home())
                typer.echo("uninstalled scout-managed crontab block")
                return
            from scout import paths as _paths

            install_cron(home=Path.home(), vault=_paths.data_dir())
            typer.echo("installed scout-managed crontab block")
        else:
            typer.echo(f"unsupported platform: {system}", err=True)
            raise typer.Exit(code=2)


_register_schedule()


def _register_model() -> None:
    """scoutctl model {resolve,show,set-default} — which Claude model scheduled runs use.

    The runners call `model resolve --slot "$MODE"` before each session; Scout.app
    reads `model show --json` and writes the default with `model set-default`.
    Precedence and allowed values live in scout.models.
    """

    model_app = typer.Typer(help="Which Claude model scheduled runs use.")
    app.add_typer(model_app, name="model")

    @model_app.command("resolve")
    def cli_model_resolve(
        slot: str | None = typer.Option(None, "--slot", help="The run's slot key (the runners pass $MODE)."),
        as_json: bool = typer.Option(False, "--json", help='Emit {"model", "source"}.'),
    ) -> None:
        """Print the model for one run: SCOUT_MODEL, else the slot's model, else models.default, else opus."""
        import json as _json

        from scout.models import resolve_model

        try:
            resolved = resolve_model(slot_key=slot)
        except ConfigError as e:
            typer.echo(f"error: {e}", err=True)
            raise typer.Exit(1) from e
        if as_json:
            typer.echo(_json.dumps({"model": resolved.model, "source": resolved.source}))
        else:
            typer.echo(resolved.model)

    @model_app.command("show")
    def cli_model_show(
        as_json: bool = typer.Option(False, "--json", help="Emit the default and every slot's model as JSON."),
    ) -> None:
        """Show the default model and the model each slot runs on, with where it comes from."""
        import json as _json

        from scout.models import ALIASES, default_model, slot_models

        try:
            default = default_model()
            per_slot = slot_models()
        except ConfigError as e:
            typer.echo(f"error: {e}", err=True)
            raise typer.Exit(1) from e
        slots = [
            {"key": key, "model": model, "source": "slot"}
            if model
            else {"key": key, "model": default.model, "source": default.source}
            for key, model in sorted(per_slot.items())
        ]
        if as_json:
            payload = {
                "default": {"model": default.model, "source": default.source},
                "slots": slots,
                "aliases": list(ALIASES),
            }
            typer.echo(_json.dumps(payload))
            return
        origin = "built-in" if default.source == "builtin" else "scout-config.yaml"
        typer.echo(f"default: {default.model} ({origin})")
        for row in slots:
            note = "" if row["source"] == "slot" else "  (default)"
            typer.echo(f"{row['key']}\t{row['model']}{note}")

    @model_app.command("set-default")
    def cli_model_set_default(
        model: str = typer.Argument(..., help="opus, sonnet, haiku, or a full claude-… model ID."),
        as_json: bool = typer.Option(False, "--json", help="Emit {config_path, default, changed}."),
    ) -> None:
        """Write models.default to the vault's scout-config.yaml; every other line is kept."""
        import json as _json

        from scout.scripts.models_config import ModelsWriteError, write_models_default

        try:
            payload = write_models_default(model)
        except ModelsWriteError as e:
            typer.echo(f"error: {e}", err=True)
            raise typer.Exit(1) from e
        if as_json:
            typer.echo(_json.dumps(payload))
            return
        verb = "wrote" if payload["changed"] else "unchanged:"
        typer.echo(f"{verb} {payload['config_path']} (models.default: {payload['default']})")


_register_model()


def _register_triggers() -> None:
    """scoutctl trigger {list,show,validate,reload,test,fire-now,stats}.

    Event-trigger operations over the vault's .scout-state/triggers.yaml
    (docs/specs/event-triggers.md). Mirrors the schedule sub-app pattern.
    `trigger serve` (the v2 webhook receiver) is intentionally absent.
    """

    trigger_app = typer.Typer(help="Event-trigger operations (vault triggers.yaml).")
    app.add_typer(trigger_app, name="trigger")

    def _load_vault_triggers():
        from scout import paths as _paths
        from scout.triggers.config import load_triggers, triggers_path

        path = triggers_path(_paths.data_dir())
        if not path.exists():
            return path, None
        return path, load_triggers(path)

    def _trigger_record(t) -> dict:
        return {
            "id": t.id,
            "enabled": t.enabled,
            "source": t.source,
            "match": dict(t.match),
            "action": {"kind": t.action.kind.value, **t.action.params},
            "cooldown_seconds": t.cooldown_seconds,
            "daily_fire_cap": t.daily_fire_cap,
            "allow_cycle": t.allow_cycle,
        }

    @trigger_app.command("list")
    def cli_trigger_list(
        as_json: bool = typer.Option(
            False,
            "--json/--no-json",
            help="Emit a JSON array of full trigger records instead of tab-separated lines.",
        ),
    ) -> None:
        """List the configured triggers."""
        import json as _json

        _path, triggers = _load_vault_triggers()
        if triggers is None:
            typer.echo("(no triggers.yaml; no triggers configured)")
            return
        if as_json:
            typer.echo(_json.dumps([_trigger_record(t) for t in triggers]))
            return
        for t in triggers:
            enabled = "enabled" if t.enabled else "disabled"
            typer.echo(f"{t.id}\t{t.source}\t{t.match_type}\t{t.action.kind.value}\t{enabled}\tcap={t.daily_fire_cap}")

    @trigger_app.command("show")
    def cli_trigger_show(trigger_id: str) -> None:
        """Show one trigger's full record (incl. dedup/fire state) as JSON."""
        import json as _json

        from scout import paths as _paths
        from scout.triggers.dedup import DedupStore
        from scout.triggers.engine import dedup_path

        _path, triggers = _load_vault_triggers()
        found = next((t for t in (triggers or []) if t.id == trigger_id), None)
        if found is None:
            typer.echo(f"unknown trigger: {trigger_id}", err=True)
            raise typer.Exit(code=1)
        record = _trigger_record(found)
        record["fire_state"] = DedupStore(dedup_path(_paths.data_dir())).state(trigger_id)
        typer.echo(_json.dumps(record, indent=2))

    @trigger_app.command("validate")
    def cli_trigger_validate(
        target: Path | None = typer.Option(
            None,
            "--target",
            "-t",
            help="Validate the triggers.yaml at this path instead of the vault canonical.",
        ),
    ) -> None:
        """Check triggers.yaml without applying it; exit 0 on success."""
        from scout.triggers.config import load_triggers

        if target is not None:
            if not target.exists():
                typer.echo(f"target does not exist: {target}", err=True)
                raise typer.Exit(code=1)
            try:
                load_triggers(target)
            except ConfigError as e:
                typer.echo(str(e), err=True)
                raise typer.Exit(code=1) from e
            typer.echo(f"triggers OK: {target}")
            return

        try:
            path, triggers = _load_vault_triggers()
        except ConfigError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(code=1) from e
        if triggers is None:
            typer.echo("triggers OK: (no triggers.yaml; no triggers configured)")
        else:
            typer.echo(f"triggers OK: {path} ({len(triggers)} trigger(s))")

    @trigger_app.command("reload")
    def cli_trigger_reload() -> None:
        """Force-reload triggers.yaml (fires use a per-tick snapshot; next tick picks this up)."""
        _path, _triggers = _load_vault_triggers()
        typer.echo("reloaded")

    @trigger_app.command("test")
    def cli_trigger_test(
        trigger_id: str,
        window_minutes: int = typer.Option(60, "--window-minutes", help="How far back to scan for events."),
    ) -> None:
        """Simulate one trigger against the last hour of source events. Never dispatches."""
        import datetime as _dt
        import json as _json

        from scout import paths as _paths
        from scout.triggers.dedup import DedupStore
        from scout.triggers.engine import dedup_path
        from scout.triggers.matcher import matches
        from scout.triggers.sources import get_source

        _path, triggers = _load_vault_triggers()
        found = next((t for t in (triggers or []) if t.id == trigger_id), None)
        if found is None:
            typer.echo(f"unknown trigger: {trigger_id}", err=True)
            raise typer.Exit(code=1)

        vault = _paths.data_dir()
        source = get_source(found.source, vault=vault)
        healthy, reason = source.health_check()
        if not healthy:
            typer.echo(f"source {found.source} is not healthy: {reason}", err=True)
            raise typer.Exit(code=1)

        since = (_dt.datetime.now(tz=_dt.UTC) - _dt.timedelta(minutes=window_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
        events = source.scan_since(since)
        dedup = DedupStore(dedup_path(vault))
        matched = [
            {
                "source_event_id": e.source_event_id,
                "ts": e.ts,
                "fields": e.normalized_match_fields,
                "already_fired": not dedup.is_new(trigger_id, e.source_event_id),
            }
            for e in events
            if matches(found.match, e)
        ]
        typer.echo(_json.dumps({"scanned": len(events), "matched": matched}, indent=2))

    @trigger_app.command("fire-now")
    def cli_trigger_fire_now(trigger_id: str) -> None:
        """Manually fire a trigger with a synthetic event (development aid)."""
        import datetime as _dt

        from scout import paths as _paths
        from scout.events import now_iso
        from scout.ids import new_ulid
        from scout.triggers.dedup import DedupStore
        from scout.triggers.dispatcher import dispatch, log_fire
        from scout.triggers.engine import dedup_path
        from scout.triggers.sources.base import ConnectorEvent

        _path, triggers = _load_vault_triggers()
        found = next((t for t in (triggers or []) if t.id == trigger_id), None)
        if found is None:
            typer.echo(f"unknown trigger: {trigger_id}", err=True)
            raise typer.Exit(code=1)

        vault = _paths.data_dir()
        event = ConnectorEvent(
            source=found.source,
            source_event_id=f"manual-{new_ulid()}",
            ts=now_iso(),
            raw_payload={"manual": True},
            normalized_match_fields={"type": found.match_type, "manual": True},
        )
        outcome = dispatch(found, event, vault=vault)
        now = _dt.datetime.now(tz=_dt.UTC)
        DedupStore(dedup_path(vault)).record_fire(trigger_id, event.source_event_id, now)
        log_fire(_paths.logs_dir(vault), outcome, extra={"source": found.source, "manual": True})
        if outcome.status != "ok":
            typer.echo(f"failed: {outcome.detail.get('error', outcome.detail)}", err=True)
            raise typer.Exit(code=1)
        typer.echo(f"fired: {trigger_id} ({found.action.kind.value})")

    @trigger_app.command("stats")
    def cli_trigger_stats(
        days: int = typer.Option(7, "--days", help="How many days of fire logs to roll up."),
        as_json: bool = typer.Option(False, "--json/--no-json", help="Emit JSON instead of text."),
    ) -> None:
        """Roll-up: fires by trigger and fires by day, from trigger-fires-*.jsonl."""
        import datetime as _dt
        import json as _json

        from scout import paths as _paths
        from scout.triggers.dispatcher import FIRE_LOG_PREFIX

        log_dir = _paths.logs_dir(_paths.data_dir())
        today = _dt.datetime.now(tz=_dt.UTC).date()
        by_trigger: dict[str, dict[str, int]] = {}
        by_day: dict[str, int] = {}
        for offset in range(days):
            day = (today - _dt.timedelta(days=offset)).isoformat()
            log_path = log_dir / f"{FIRE_LOG_PREFIX}{day}.jsonl"
            if not log_path.exists():
                continue
            for line in log_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = _json.loads(line)
                except _json.JSONDecodeError:
                    continue
                tid = row.get("trigger_id", "?")
                entry = by_trigger.setdefault(tid, {"total": 0, "ok": 0, "error": 0})
                entry["total"] += 1
                entry["ok" if row.get("status") == "ok" else "error"] += 1
                by_day[day] = by_day.get(day, 0) + 1

        if as_json:
            typer.echo(_json.dumps({"days": days, "by_trigger": by_trigger, "by_day": by_day}))
            return
        if not by_trigger:
            typer.echo(f"(no trigger fires in the last {days} day(s))")
            return
        for tid in sorted(by_trigger):
            e = by_trigger[tid]
            typer.echo(f"{tid}\ttotal={e['total']}\tok={e['ok']}\terror={e['error']}")
        for day in sorted(by_day):
            typer.echo(f"{day}\t{by_day[day]}")


_register_triggers()


def _register_notify() -> None:
    """scoutctl notify {telegram} — outbound notification commands.

    `notify:telegram` is registered in connectors.yaml with capabilities=[outbound].
    The Claude session calls `scoutctl notify telegram` from inside its prompt at
    session-wrap time (Bash tool). Plan 7 will Pythonize the runner; for v0.4 the
    CLI command IS the integration point.
    """
    notify_app = typer.Typer(help="Outbound notifications (Telegram, etc.).")
    app.add_typer(notify_app, name="notify")

    @notify_app.command("telegram")
    def cli_notify_telegram(
        tier: str = typer.Option(
            "info",
            "--tier",
            help="info (silent) | action_required (loud)",
        ),
        body: str | None = typer.Option(
            None,
            "--body",
            help="Message body. Newlines preserved. Exactly one of --body / --body-file.",
        ),
        body_file: Path | None = typer.Option(
            None,
            "--body-file",
            help="Read the message body from this UTF-8 file ('-' = stdin) instead of --body (keeps it off the shell).",
        ),
        parse_mode: str = typer.Option(
            "none",
            "--parse-mode",
            help=(
                "none (plain text) | html (Telegram HTML: <b> <i> <u> <s> <a href> <code> <pre> "
                "<blockquote>; escape <, >, & in text). A body Telegram can't parse is resent as plain text."
            ),
        ),
        dry_run: bool = typer.Option(
            False,
            "--dry-run",
            help="Print the request without POSTing. Still requires secrets.",
        ),
        allow_duplicate: bool = typer.Option(
            False,
            "--allow-duplicate",
            help="Send even if a byte-identical message went to this chat in the last 10 minutes.",
        ),
    ) -> None:
        """Send a Telegram message via the configured bot.

        The receipt is `id=<ulid>` on the first line of stderr; stdout is the Event JSON.
        """
        import json as _json
        from dataclasses import asdict

        import requests as _requests

        from scout.errors import ConfigError as _ConfigError
        from scout.scripts.notify_telegram import DuplicateSendError, send

        if body is not None and body_file is None:
            text = body
        elif body is None and body_file is not None:
            try:
                text = _read_text_arg(body_file)
            except (OSError, UnicodeDecodeError) as e:
                typer.echo(f"scoutctl notify telegram: could not read --body-file {body_file}: {e}", err=True)
                raise typer.Exit(code=1) from e
        else:
            typer.echo("scoutctl notify telegram: pass exactly one of --body or --body-file", err=True)
            raise typer.Exit(code=1)

        try:
            ev = send(
                tier=tier,
                body=text,
                parse_mode=parse_mode,
                dry_run=dry_run,
                allow_duplicate=allow_duplicate,
                receipt=lambda event_id: typer.echo(f"id={event_id}", err=True),
            )
        except DuplicateSendError as e:
            # Receipt first (the earlier send's id), so a caller filtering
            # stderr still finds it; then the refusal and how to override.
            typer.echo(f"id={e.original_id}", err=True)
            typer.echo(
                f"scoutctl notify telegram: refused — byte-identical message sent at {e.sent_at} "
                f"(id {e.original_id}); pass --allow-duplicate to send it again",
                err=True,
            )
            raise typer.Exit(code=DuplicateSendError.exit_code) from e
        except _ConfigError as e:
            # Map ScoutError exit codes at the command boundary so the CLI
            # surface stays consistent with cli.main()'s ScoutError handler.
            # The runner relies on exit 10 to know secrets are missing.
            typer.echo(f"scoutctl notify telegram: {e}", err=True)
            raise typer.Exit(code=_ConfigError.exit_code) from e
        except ValueError as e:
            typer.echo(f"scoutctl notify telegram: {e}", err=True)
            raise typer.Exit(code=1) from e
        except _requests.HTTPError as e:
            # ``str(HTTPError)`` includes the request URL, and the Telegram
            # URL embeds the bot token in its path
            # (``/bot<token>/sendMessage``). On a 401 (revoked token) or
            # any 4xx/5xx the raw token would dump to stderr — the worst
            # leak path because it's exactly what operators debug live.
            # Rebuild the message from status_code + reason instead of
            # ``str(e)`` so the URL never appears.
            status = getattr(e.response, "status_code", "?")
            reason = getattr(e.response, "reason", "Unknown")
            typer.echo(
                f"scoutctl notify telegram: HTTP {status} {reason} (token redacted in URL)",
                err=True,
            )
            raise typer.Exit(code=2) from e
        except _requests.RequestException as e:
            # Non-HTTP failures during a live send: timeout, DNS, connection
            # refused, SSL. ``str()`` of a ConnectionError DOES include the
            # request path (``Max retries exceeded with url: /bot<token>/…``).
            # send() scrubs it; redact again here so this line can never
            # carry the token. Exit non-zero with a clear stderr line instead
            # of dumping a stack trace into the runner's prompt.
            from scout.redact import redact_secrets

            typer.echo(f"scoutctl notify telegram: HTTP error: {redact_secrets(str(e))}", err=True)
            raise typer.Exit(code=2) from None

        # Print the resulting Event JSON to stdout. Dry-run preamble (the
        # [dry-run] lines) is intentionally routed through stderr inside
        # send() so this stdout is always pure JSON and parsable by tests
        # / downstream scripts.
        typer.echo(_json.dumps(asdict(ev), indent=2))


_register_notify()


health_app = typer.Typer(
    help="Run health: are scheduled runs happening and succeeding?",
    invoke_without_command=True,
)


def _register_health() -> None:
    """scoutctl health [--json] — run-health report; exits 0 green / 1 yellow / 2 red."""
    app.add_typer(health_app, name="health")

    @health_app.callback()
    def cli_health(
        ctx: typer.Context,
        json_out: bool = typer.Option(False, "--json", help="Emit the report as JSON."),
    ) -> None:
        if ctx.invoked_subcommand is not None:
            return
        import json as _json

        from scout import health as _health
        from scout import paths as _paths

        vault = _paths.data_dir()
        if not vault.is_dir():
            error = f"vault directory missing: {vault}"
            typer.echo(f"run health: unknown — {error}", err=True)
            if json_out:
                typer.echo(_json.dumps({"severity": "unknown", "error": error}))
            raise typer.Exit(code=2)
        report = _health.evaluate(vault)
        if json_out:
            typer.echo(_json.dumps(report.to_dict()))
        else:
            typer.echo(f"run health: {report.severity}")
            for f in report.findings:
                typer.echo(f"{f.severity}  {f.id}: {f.summary}")
                if f.detail:
                    typer.echo(f"    error: {f.detail}")
                typer.echo(f"    fix: {f.fix}")
                if f.log:
                    typer.echo(f"    log: {f.log}")
        raise typer.Exit(code=report.exit_code)

    @health_app.command("notify")
    def cli_health_notify(
        quiet: bool = typer.Option(False, "--quiet", help="Print nothing when there is no push."),
    ) -> None:
        """Push newly red / overdue / recovered run-health changes; always exits 0."""
        from scout import health_notify as _hn
        from scout import paths as _paths

        try:
            vault = _paths.data_dir()
        except Exception as e:  # noqa: BLE001 - pushes are best-effort; this command always exits 0
            typer.echo(f"health notify: cannot resolve the vault ({type(e).__name__}); nothing pushed", err=True)
            return
        sent = _hn.notify(vault)
        for push, results in sent:
            ok = ",".join(r["channel"] for r in results if r.get("ok")) or "none"
            typer.echo(f"pushed {push.kind} {','.join(push.finding_ids) or push.finding_id}: {ok}")
        if not sent and not quiet:
            typer.echo("nothing to push")


_register_health()


def _register_auth() -> None:
    """scoutctl auth {setup-token,status,clear} — the long-lived token scheduled runs use (#267)."""
    auth_app = typer.Typer(help="How scheduled runs sign in to Claude (long-lived token or your login).")
    app.add_typer(auth_app, name="auth")

    @auth_app.command("setup-token")
    def cli_auth_setup_token(
        from_stdin: bool = typer.Option(
            False, "--stdin", help="Read a token made elsewhere from stdin instead of running `claude setup-token`."
        ),
        claude_bin: str = typer.Option("", "--claude-bin", help="The claude CLI. Default: auto-detect."),
    ) -> None:
        """Create a long-lived token with `claude setup-token` and store it for scheduled runs."""
        import sys as _sys

        from scout import claude_auth as _ca

        store = _ca.default_store()
        if from_stdin:
            code = _ca.store_pasted(store, _sys.stdin.read(), say=typer.echo)
        else:
            from scout.scripts.bootstrap import resolve_claude_bin

            code = _ca.setup_token(
                store,
                claude_bin=resolve_claude_bin(claude_bin),
                interactive=_ca._has_terminal(),
                run=_ca._run_inherited,
                read_secret=_ca._read_secret,
                say=typer.echo,
            )
        raise typer.Exit(code=code)

    @auth_app.command("status")
    def cli_auth_status(json_out: bool = typer.Option(False, "--json", help="Emit JSON.")) -> None:
        """Which sign-in scheduled runs use and, for a token, when it expires. Exits 1 on a problem."""
        import json as _json
        import os as _os

        from scout import claude_auth as _ca

        st = _ca.status()
        if json_out:
            typer.echo(_json.dumps(st.to_dict()))
        else:
            typer.echo(st.describe())
            expiry = st.expiry_text()
            if st.expiring and expiry:
                typer.echo(f"The token {expiry}; renew it: {_ca.SETUP_COMMAND}")
            for problem in st.problems:
                typer.echo(f"problem: {problem}")
            if _os.environ.get(_ca.ENV_VAR):
                typer.echo(
                    f"note: {_ca.ENV_VAR} is set in this shell; a runner that inherits it uses it "
                    "instead of the stored token."
                )
        raise typer.Exit(code=1 if st.problems or st.expiring else 0)

    @auth_app.command("clear")
    def cli_auth_clear() -> None:
        """Remove the stored token; scheduled runs go back to your interactive Claude login."""
        from scout import claude_auth as _ca

        store = _ca.default_store()
        try:
            removed = _ca.clear_token(store)
        except OSError as e:
            typer.echo(f"Nothing removed: {e}")
            raise typer.Exit(code=2) from None
        if removed:
            typer.echo(f"Removed the token at {store.location}. Scheduled runs use your interactive Claude login.")
        else:
            typer.echo("No stored token; scheduled runs already use your interactive Claude login.")


_register_auth()


def _resolve_claude_bin_or_warn(explicit: str, resolve: Callable[[str], str]) -> str:
    """Resolve ``--claude-bin`` once so the value persisted in scout-config.yaml
    is the concrete path the runners were rendered with; warn when it is not
    executable (every scheduled run would fail to launch Claude)."""
    import os as _os

    resolved = resolve(explicit)
    if not _os.access(resolved, _os.X_OK):
        typer.echo(
            f"  warning: claude CLI not executable at {resolved} — scheduled runs will fail until "
            "Claude Code is installed there (or re-run with --claude-bin <path>).",
            err=True,
        )
    return resolved


def _register_bootstrap() -> None:
    bootstrap_app = typer.Typer(help="Bootstrap pipeline (install/upgrade/doctor).")
    app.add_typer(bootstrap_app, name="bootstrap")

    _TEXT_VERB = {"install": "installed", "upgrade": "upgraded", "migrate-legacy": "migrated"}

    _MANAGED_BY_HELP = (
        "Who owns this engine install: scout-app | install.sh | claude-code | dev | unknown "
        "(recorded in the engine pointer). Default `preserve`: keep the existing pointer's value "
        "when it describes this engine's interpreter, else unknown."
    )

    # Two caps that read alike (#229): this one is per session; the daily
    # budget the budget check gates on is `scoutctl budget set --daily-usd`.
    _MAX_BUDGET_HELP = (
        "USD cap for ONE scheduled session (claude --max-budget-usd). Not the daily budget: "
        "set that with `scoutctl budget set --daily-usd`."
    )

    def _resolve_managed_by(value: str) -> str:
        """`preserve` → the concrete manager (engine_pointer.resolve_managed_by);
        an explicit value outside MANAGED_BY_VALUES is a usage error (exit 2)."""
        from scout.scripts.engine_pointer import resolve_managed_by

        try:
            return resolve_managed_by(value, home=Path.home())
        except ValueError as e:
            raise typer.BadParameter(str(e), param_hint="'--managed-by'") from e

    def _emit(payload: dict, *, json_out: bool) -> None:
        """One printer for every bootstrap subcommand: JSON on stdout, or the
        human lines. Warnings/errors go to stderr in text mode so a caller that
        captures stdout still gets a clean report.

        Text-mode first line keeps the pre-E3 verbs (installed/upgraded/
        migrated) so commands/scout-setup.md, commands/scout-update.md, and
        tests/unit/test_cli_surface.py keep parsing/asserting the same lines.
        migrate-legacy also keeps its pre-E3 quirks — a "snapshots recorded"
        line, and backups on stdout rather than stderr — for the same reason.
        A dry run says what it *would* do and why; a refusal names the vault,
        the error and (when it adds something) the detected reason.
        """
        import json as _json

        if json_out:
            typer.echo(_json.dumps(payload, indent=2, sort_keys=True))
            return
        reason = payload.get("reason") or ""
        if payload.get("error"):
            typer.echo(f"{payload['action']}: {payload['vault']} — {payload['error']}", err=True)
            if reason and reason != payload["error"]:
                typer.echo(f"  reason: {reason}", err=True)
            return
        action = payload["action"]
        if payload.get("dry_run"):
            verb = "refuse" if action == "refused" else action
            typer.echo(f"would {verb}: {payload['vault']} ({reason})")
            return
        typer.echo(f"{_TEXT_VERB.get(action, action)}: {payload['vault']}")
        if action == "migrate-legacy":
            typer.echo(f"snapshots recorded: {', '.join(payload.get('snapshots_recorded') or []) or 'none'}")
        for c in payload.get("conflicts", []):
            typer.echo(f"  conflict (sidecar): {c}", err=True)
        for sk in payload.get("skipped", []):
            live_name = sk.removesuffix(".proposed-merge")
            typer.echo(f"  skipped (sidecar pending): {sk} — {live_name} left as is until it is resolved", err=True)
        for held in payload.get("conflict_markers", []):
            typer.echo(
                f"  held (conflict markers): {held} — left as is; it still has <<<<<<< / >>>>>>> lines from an "
                f"unfinished merge. Remove them (then `scoutctl bootstrap resolve {held}` if a sidecar was behind it)",
                err=True,
            )
        # migrate-legacy prints these on stdout (its pre-E3 quirk), upgrade on stderr.
        to_err = action != "migrate-legacy"
        edits = payload.get("vault_edits") or []
        named = {p for e in edits for p in e["parked"]}
        for e in edits:
            typer.echo(f"  vault edit {e['outcome']}: {e['path']} — {e['message']}", err=to_err)
        for b in payload.get("backups", []):
            if b not in named:
                typer.echo(f"  backup: {b}", err=to_err)
        doctor = payload.get("doctor")
        if doctor:
            typer.echo(f"doctor: {doctor['severity']}")
            for w in doctor["warnings"]:
                typer.echo(f"  warning: {w}", err=True)
            for e in doctor["errors"]:
                typer.echo(f"  error: {e}", err=True)

    def _config_from_existing_vault(vault: Path, *, skip_jobs: bool, skip_claude: bool, managed_by: str):
        """BootstrapConfig for an existing vault, read back from scout-config.yaml
        (shared with the custom-connector commands via bootstrap.config_from_vault)."""
        import yaml as _yaml

        from scout import __version__
        from scout.scripts.bootstrap import config_from_vault

        try:
            return config_from_vault(
                vault,
                plugin_root=Path(__file__).parent.parent.parent,
                plugin_version=__version__,
                skip_jobs=skip_jobs,
                skip_claude=skip_claude,
                managed_by=managed_by,
            )
        except (_yaml.YAMLError, UnicodeDecodeError, ValueError) as e:
            raise ConfigError(f"scout-config.yaml is malformed: {e}") from e

    @bootstrap_app.command("install")
    def cli_bootstrap_install(
        instance_name: str = typer.Option("Scout", "--instance-name"),
        user_name: str = typer.Option(..., "--user-name"),
        user_email: str = typer.Option(..., "--user-email"),
        timezone: str = typer.Option(
            "", "--timezone", help="IANA zone override. Default: follow the computer's own timezone."
        ),
        platform: str = typer.Option("macos", "--platform"),
        skip_jobs: bool = typer.Option(False, "--no-jobs"),
        skip_claude: bool = typer.Option(False, "--skip-claude"),
        connectors: str = typer.Option("", "--connectors", help="Comma-separated enabled connector names"),
        # Per-connector inputs collected by /scout-setup. Mirroring the
        # migrate-legacy flags keeps the two entrypoints symmetric and
        # ensures cat-1b runners get rendered with the user's real
        # claude_bin / Slack ID / etc. instead of falling back to the
        # template defaults (which is what triggered the "regen ships
        # placeholders" bug).
        user_slack_id: str = typer.Option("", "--user-slack-id"),
        github_username: str = typer.Option("", "--github-username"),
        github_repos: str = typer.Option("", "--github-repos"),
        claude_bin: str = typer.Option(
            "", "--claude-bin", help="Path to the claude CLI. Default: auto-detect (PATH, ~/.local/bin, Homebrew)."
        ),
        max_budget: str = typer.Option("5.00", "--max-budget", help=_MAX_BUDGET_HELP),
        auto_update: bool | None = typer.Option(
            None,
            "--auto-update/--no-auto-update",
            help="Record the auto-update preference (auto_update.enabled) in scout-config.yaml.",
        ),
        managed_by: str = typer.Option("preserve", "--managed-by", help=_MANAGED_BY_HELP),
        json_out: bool = typer.Option(False, "--json", help="Emit the BootstrapResult JSON (consumed by Scout.app)."),
    ) -> None:
        """Install Scout into the user's vault directory."""
        from scout import __version__
        from scout import paths as _paths
        from scout.scripts.bootstrap import BootstrapConfig, install, resolve_claude_bin
        from scout.scripts.bootstrap_auto import AutoAction, result_dict

        managed_by = _resolve_managed_by(managed_by)
        vault = _paths.data_dir()
        claude_bin = _resolve_claude_bin_or_warn(claude_bin, resolve_claude_bin)
        cfg = BootstrapConfig(
            vault=vault,
            plugin_root=Path(__file__).parent.parent.parent,
            instance_name=instance_name,
            instance_name_lower=instance_name.lower().replace(" ", "-"),
            user_name=user_name,
            user_email=user_email,
            timezone=timezone,
            platform=platform,
            plugin_version=__version__,
            enabled_connectors=set(c.strip() for c in connectors.split(",") if c.strip()),
            connector_inputs={
                "user_slack_id": user_slack_id,
                "github_username": github_username,
                "github_repos": github_repos,
                "claude_bin": claude_bin,
                "max_budget": max_budget,
            },
            skip_jobs=skip_jobs,
            skip_claude=skip_claude,
            auto_update=auto_update,
            managed_by=managed_by,
        )
        result = install(cfg)
        _emit(
            result_dict(action=AutoAction.INSTALL, vault=vault, plugin_version=__version__, result=result),
            json_out=json_out,
        )
        raise typer.Exit(code=result.doctor.exit_code)

    @bootstrap_app.command("upgrade")
    def cli_bootstrap_upgrade(
        skip_jobs: bool = typer.Option(False, "--no-jobs"),
        skip_claude: bool = typer.Option(False, "--skip-claude"),
        managed_by: str = typer.Option("preserve", "--managed-by", help=_MANAGED_BY_HELP),
        json_out: bool = typer.Option(False, "--json", help="Emit the BootstrapResult JSON (consumed by Scout.app)."),
    ) -> None:
        """Upgrade an existing vault against the current plugin templates."""
        from scout import __version__
        from scout import paths as _paths
        from scout.scripts.bootstrap import upgrade
        from scout.scripts.bootstrap_auto import AutoAction, result_dict

        managed_by = _resolve_managed_by(managed_by)
        vault = _paths.data_dir()
        cfg_path = vault / "scout-config.yaml"
        if not cfg_path.exists():
            msg = f"no vault at {vault} — run /scout-setup"
            _emit(
                result_dict(action=AutoAction.REFUSED, vault=vault, plugin_version=__version__, result=None, error=msg),
                json_out=json_out,
            )
            raise typer.Exit(code=2)
        try:
            cfg = _config_from_existing_vault(
                vault, skip_jobs=skip_jobs, skip_claude=skip_claude, managed_by=managed_by
            )
        except ConfigError as e:
            # Preserve the pre-E3 exit code (10) for a malformed scout-config.yaml —
            # distinct from the "no vault"/"refused" (2) case above.
            typer.echo(str(e), err=True)
            raise typer.Exit(code=ConfigError.exit_code) from e
        result = upgrade(cfg)
        _emit(
            result_dict(action=AutoAction.UPGRADE, vault=vault, plugin_version=__version__, result=result),
            json_out=json_out,
        )
        raise typer.Exit(code=result.doctor.exit_code)

    @bootstrap_app.command("resolve")
    def cli_bootstrap_resolve(
        files: list[str] = typer.Argument(..., help="SKILL.md, DREAMING.md and/or RESEARCH.md"),
    ) -> None:
        """Record a brain file's sidecar as resolved.

        Make the live file the version you want first (e.g. move the sidecar into
        place and remove any conflict markers). The plugin assembly behind the
        sidecar becomes the merge base, so the next upgrade merges only later
        plugin changes into your resolution; the sidecar is removed.
        """
        from scout import paths as _paths
        from scout.scripts.bootstrap import resolve_brain_file
        from scout.scripts.brain_merge import BRAIN_KINDS

        kinds = [f.upper().removesuffix(".MD") for f in files]
        unknown = [f for f, k in zip(files, kinds, strict=True) if k not in BRAIN_KINDS]
        if unknown:
            expected = "expected SKILL.md, DREAMING.md or RESEARCH.md"
            typer.echo(f"not a brain file: {', '.join(unknown)} ({expected})", err=True)
            raise typer.Exit(code=2)
        vault = _paths.data_dir()
        failed = False
        for kind in kinds:
            try:
                r = resolve_brain_file(vault, kind)
            except (ValueError, FileNotFoundError) as e:
                typer.echo(f"refused: {e}", err=True)
                failed = True
                continue
            if r.recorded_base:
                typer.echo(f"resolved: {r.name} — the next upgrade merges plugin changes into it")
            else:
                typer.echo(f"resolved: {r.name} — sidecar removed; the next upgrade merges it again")
        raise typer.Exit(code=1 if failed else 0)

    @bootstrap_app.command("doctor")
    def cli_bootstrap_doctor(
        no_jobs: bool = typer.Option(False, "--no-jobs", help="Skip launchd registration check"),
        json_out: bool = typer.Option(False, "--json", help="Emit {severity, errors, warnings} JSON."),
    ) -> None:
        """Run the read-only health check on the current vault."""
        from scout import paths as _paths
        from scout.scripts.bootstrap_auto import doctor_dict
        from scout.scripts.bootstrap_doctor import run_doctor

        report = run_doctor(vault=_paths.data_dir(), check_jobs=not no_jobs)
        if json_out:
            import json as _json

            typer.echo(_json.dumps(doctor_dict(report)))
            raise typer.Exit(code=report.exit_code)
        typer.echo(f"severity: {report.severity.value}")
        for w in report.warnings:
            typer.echo(f"warning: {w}")
        for n in report.notes:
            typer.echo(f"note: {n}")
        for e in report.errors:
            typer.echo(f"error: {e}", err=True)
        if report.run_health is not None:
            # Never start these with warning:/error:/note: (the app's legacy
            # text parser reads those prefixes).
            typer.echo(f"run health: {report.run_health.severity}")
            for f in report.run_health.findings:
                typer.echo(f"run health {f.severity}: {f.id}: {f.summary}")
        raise typer.Exit(code=report.exit_code)

    @bootstrap_app.command("migrate-legacy")
    def cli_bootstrap_migrate_legacy(
        user_name: str = typer.Option(..., "--user-name"),
        user_email: str = typer.Option(..., "--user-email"),
        instance_name: str = typer.Option("Scout", "--instance-name"),
        user_slack_id: str = typer.Option("", "--user-slack-id"),
        github_username: str = typer.Option("", "--github-username"),
        github_repos: str = typer.Option("", "--github-repos"),
        claude_bin: str = typer.Option(
            "", "--claude-bin", help="Path to the claude CLI. Default: auto-detect (PATH, ~/.local/bin, Homebrew)."
        ),
        timezone: str = typer.Option(
            "", "--timezone", help="IANA zone override. Default: follow the computer's own timezone."
        ),
        max_budget: str = typer.Option("5.00", "--max-budget", help=_MAX_BUDGET_HELP),
        platform: str = typer.Option("macos", "--platform"),
        connectors: str = typer.Option("", "--connectors", help="Comma-separated enabled connector names"),
        skip_jobs: bool = typer.Option(
            True,
            "--no-jobs/--rebootstrap-jobs",
            help="Default --no-jobs: leave launchd/cron untouched; use --rebootstrap-jobs to reinstall them.",
        ),
        managed_by: str = typer.Option("preserve", "--managed-by", help=_MANAGED_BY_HELP),
        json_out: bool = typer.Option(False, "--json", help="Emit the BootstrapResult JSON (consumed by Scout.app)."),
    ) -> None:
        """One-time migration of a Plan-5-era vault to Plan 8 format.

        Required: vault must exist with .scout-state/ but no scout-config.yaml.
        Establishes the Plan 8 baseline (snapshots + scout-config.yaml + cat-1
        regen) without touching live SKILL/DREAMING/RESEARCH content. Legacy
        runners (or any plugin-owned file) that match no release are parked
        under .scout-state/drift/ before the current templates are installed
        (see `scoutctl bootstrap drift`).
        """
        from scout import __version__
        from scout import paths as _paths
        from scout.scripts.bootstrap import BootstrapConfig, migrate_legacy, resolve_claude_bin
        from scout.scripts.bootstrap_auto import AutoAction, result_dict

        managed_by = _resolve_managed_by(managed_by)
        vault = _paths.data_dir()
        claude_bin = _resolve_claude_bin_or_warn(claude_bin, resolve_claude_bin)
        cfg = BootstrapConfig(
            vault=vault,
            plugin_root=Path(__file__).parent.parent.parent,
            instance_name=instance_name,
            instance_name_lower=instance_name.lower().replace(" ", "-"),
            user_name=user_name,
            user_email=user_email,
            timezone=timezone,
            platform=platform,
            plugin_version=__version__,
            enabled_connectors=set(c.strip() for c in connectors.split(",") if c.strip()),
            connector_inputs={
                "user_slack_id": user_slack_id,
                "github_username": github_username,
                "github_repos": github_repos,
                "claude_bin": claude_bin,
                "max_budget": max_budget,
            },
            skip_jobs=skip_jobs,
            skip_claude=True,
            managed_by=managed_by,
        )
        result = migrate_legacy(cfg)
        _emit(
            result_dict(action=AutoAction.MIGRATE_LEGACY, vault=vault, plugin_version=__version__, result=result),
            json_out=json_out,
        )
        raise typer.Exit(code=result.doctor.exit_code)

    # `auto` flags that an UPGRADE reads from scout-config.yaml instead (named
    # in a stderr note when passed). --claude-bin is not here: an explicit
    # path is applied on upgrade, since Scout.app passes a fresh one each time.
    _AUTO_UPGRADE_IGNORED = frozenset(
        {
            "user_name",
            "user_email",
            "instance_name",
            "timezone",
            "platform_",
            "connectors",
            "user_slack_id",
            "github_username",
            "github_repos",
            "max_budget",
        }
    )

    @bootstrap_app.command("auto")
    def cli_bootstrap_auto(
        ctx: typer.Context,
        user_name: str = typer.Option("", "--user-name", help="Required for install / migrate-legacy."),
        user_email: str = typer.Option("", "--user-email", help="Required for install / migrate-legacy."),
        instance_name: str = typer.Option("Scout", "--instance-name"),
        timezone: str = typer.Option(
            "", "--timezone", help="IANA zone override. Default: follow the computer's own timezone."
        ),
        platform_: str = typer.Option("auto", "--platform", help="macos | linux | auto (from uname)"),
        connectors: str = typer.Option("", "--connectors", help="Comma-separated enabled connector names"),
        user_slack_id: str = typer.Option("", "--user-slack-id"),
        github_username: str = typer.Option("", "--github-username"),
        github_repos: str = typer.Option("", "--github-repos"),
        claude_bin: str = typer.Option("auto", "--claude-bin", help="Absolute path, or auto (`command -v claude`)"),
        max_budget: str = typer.Option("5.00", "--max-budget", help=_MAX_BUDGET_HELP),
        skip_jobs: bool = typer.Option(False, "--no-jobs"),
        skip_claude: bool = typer.Option(False, "--skip-claude"),
        managed_by: str = typer.Option("preserve", "--managed-by", help=_MANAGED_BY_HELP),
        interactive: bool | None = typer.Option(
            None,
            "--interactive/--no-interactive",
            help="Prompt for missing identity fields. Default: interactive when stdin is a TTY.",
        ),
        yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
        dry_run: bool = typer.Option(False, "--dry-run", help="Print the detected action and exit 0."),
        json_out: bool = typer.Option(False, "--json", help="Emit the BootstrapResult JSON (consumed by Scout.app)."),
        auto_update: bool | None = typer.Option(
            None,
            "--auto-update/--no-auto-update",
            help="Record auto_update.enabled in scout-config.yaml. Omit to leave it as it is.",
        ),
    ) -> None:
        """Detect the vault's state and run install, upgrade or migrate-legacy accordingly (#26)."""
        import platform as _platform

        from scout import __version__
        from scout import paths as _paths
        from scout.scripts.bootstrap import BootstrapConfig, resolve_claude_bin
        from scout.scripts.bootstrap_auto import AutoAction, detect, result_dict, run

        managed_by = _resolve_managed_by(managed_by)
        vault = _paths.data_dir()
        if interactive is None:
            interactive = sys.stdin.isatty()
        if platform_ == "auto":
            platform_ = {"Darwin": "macos", "Linux": "linux"}.get(_platform.system(), "")
            if not platform_:
                _emit(
                    result_dict(
                        action=AutoAction.REFUSED,
                        vault=vault,
                        plugin_version=__version__,
                        result=None,
                        error=f"unsupported platform {_platform.system()!r}; pass --platform",
                    ),
                    json_out=json_out,
                )
                raise typer.Exit(code=2)
        explicit_claude_bin = claude_bin != "auto"
        if not explicit_claude_bin:
            # Same discovery order as install / migrate-legacy (#254): PATH,
            # ~/.local/bin (native installer), Homebrew, /usr/local/bin.
            claude_bin = resolve_claude_bin("")

        try:
            plan = detect(vault)
        except OSError as e:
            # An unreadable vault (e.g. TCC-denied ~/Documents/Scout) still
            # answers with the refused payload, never a bare exit 70.
            _emit(
                result_dict(
                    action=AutoAction.REFUSED,
                    vault=vault,
                    plugin_version=__version__,
                    result=None,
                    error=f"cannot inspect {vault}: {e}",
                ),
                json_out=json_out,
            )
            raise typer.Exit(code=2) from e
        needs_identity = plan.action in (AutoAction.INSTALL, AutoAction.MIGRATE_LEGACY)
        if needs_identity and not dry_run:
            if interactive:
                user_name = user_name or typer.prompt("Your name")
                user_email = user_email or typer.prompt("Your email")
            missing = [f for f, v in (("--user-name", user_name), ("--user-email", user_email)) if not v]
            if missing:
                _emit(
                    result_dict(
                        action=AutoAction.REFUSED,
                        vault=vault,
                        plugin_version=__version__,
                        result=None,
                        error=f"{plan.action.value} needs {' and '.join(missing)} (or run interactively)",
                        reason=plan.reason,
                    ),
                    json_out=json_out,
                )
                raise typer.Exit(code=2)

        if plan.action is AutoAction.UPGRADE and (vault / "scout-config.yaml").exists():
            # By the enum member's name: Typer vendors its own Click, so
            # ParameterSource is not importable from a public module.
            ignored = [
                p.opts[0]
                for p in ctx.command.params
                if p.name in _AUTO_UPGRADE_IGNORED
                and getattr(ctx.get_parameter_source(p.name), "name", None) == "COMMANDLINE"
            ]
            if ignored:
                typer.echo(f"note: ignored on upgrade (read from scout-config.yaml): {', '.join(ignored)}", err=True)
            try:
                cfg = _config_from_existing_vault(
                    vault, skip_jobs=skip_jobs, skip_claude=skip_claude, managed_by=managed_by
                )
            except (ConfigError, OSError) as e:
                _emit(
                    result_dict(
                        action=AutoAction.REFUSED,
                        vault=vault,
                        plugin_version=__version__,
                        result=None,
                        error=str(e),
                        reason=plan.reason,
                    ),
                    json_out=json_out,
                )
                raise typer.Exit(code=2) from e
            if explicit_claude_bin:
                # A moved `claude` must reach the runners (and be persisted by
                # the version stamp); `auto` keeps the vault's recorded path.
                cfg.connector_inputs = {**cfg.connector_inputs, "claude_bin": claude_bin}
        else:
            cfg = BootstrapConfig(
                vault=vault,
                plugin_root=Path(__file__).parent.parent.parent,
                instance_name=instance_name,
                instance_name_lower=instance_name.lower().replace(" ", "-"),
                user_name=user_name,
                user_email=user_email,
                timezone=timezone,
                platform=platform_,
                plugin_version=__version__,
                enabled_connectors=set(c.strip() for c in connectors.split(",") if c.strip()),
                connector_inputs={
                    "user_slack_id": user_slack_id,
                    "github_username": github_username,
                    "github_repos": github_repos,
                    "claude_bin": claude_bin,
                    "max_budget": max_budget,
                },
                skip_jobs=skip_jobs,
                skip_claude=skip_claude,
                managed_by=managed_by,
            )
        cfg.auto_update = auto_update

        if not dry_run and not yes and interactive and plan.action is not AutoAction.REFUSED:
            if not typer.confirm(f"About to run bootstrap {plan.action.value} on {vault} ({plan.reason}). Proceed?"):
                raise typer.Exit(code=1)

        payload, code = run(cfg, plan=plan, dry_run=dry_run)
        _emit(payload, json_out=json_out)
        raise typer.Exit(code=code)

    @bootstrap_app.command("drift")
    def cli_bootstrap_drift(
        diff: bool = typer.Option(False, "--diff", help="Show each file's diff against the plugin's version."),
        patch: bool = typer.Option(
            False, "--patch", help="Print a git-apply-able patch against the plugin's templates/ for the edited files."
        ),
        json_out: bool = typer.Option(False, "--json", help="Machine-readable report (for notifiers and sessions)."),
        resolve: list[str] = typer.Option(  # noqa: B008
            [], "--resolve", metavar="FILE", help="Settle what an upgrade parked for FILE (vault-relative; repeatable)."
        ),
        drop_update: bool = typer.Option(
            False, "--drop-update", help="With --resolve: keep your version and drop the parked plugin update."
        ),
        vault_opt: str = typer.Option("", "--vault", help="Vault path (default: the resolved Scout data dir)"),
    ) -> None:
        """Vault edits to plugin-owned files, and a way to upstream them.

        Lists every managed file (scripts, hooks, runners, render.py, parser.py)
        that differs from what the plugin last wrote: edited (upgrades keep it),
        conflict (an update is parked; merge by hand, then --resolve), replaced
        (your previous copy is parked). --patch turns the edits into a patch for
        a plugin PR; the plugin repo is public, so review it before you open one.
        """
        import json as _json

        from scout import paths as _paths
        from scout.scripts import vault_drift
        from scout.scripts.bootstrap import _template_vars, managed_renders
        from scout.scripts.template_backport import backport_patch

        vault = Path(vault_opt).expanduser() if vault_opt else _paths.data_dir()
        if drop_update and not resolve:
            typer.echo("scoutctl bootstrap drift: --drop-update only applies with --resolve FILE", err=True)
            raise typer.Exit(code=2)
        if resolve:
            failed = False
            for rel in resolve:
                try:
                    done = vault_drift.resolve(vault, rel, drop_update=drop_update)
                except ValueError as e:
                    typer.echo(f"scoutctl bootstrap drift: {e}", err=True)
                    failed = True
                    continue
                for line in done:
                    typer.echo(line)
            if failed:
                raise typer.Exit(code=2)
            return

        cfg = _vault_bootstrap_config(vault)
        renders = {r.file.vault_rel: r for r in managed_renders(cfg)}
        rows = vault_drift.report(vault, {rel: r.text for rel, r in renders.items()})

        if json_out:
            files = [row.to_json() for row in rows]
            typer.echo(_json.dumps({"schema_version": 1, "vault": str(vault), "files": files}, indent=2))
            return

        if patch:
            vars_ = _template_vars(cfg)
            produced = 0
            for row in rows:
                path, render = row.path, renders.get(row.path)
                if row.status != "edited" or render is None:
                    if row.status == "conflict":
                        typer.echo(f"skipped {path}: a conflict — merge it and --resolve it first", err=True)
                    elif row.status == "replaced":
                        typer.echo(
                            f"skipped {path}: your copy is parked at {row.parked[-1]} — copy it back over "
                            f"{path} first if it holds a fix to upstream",
                            err=True,
                        )
                    continue
                if render.file.vault_developed:
                    typer.echo(
                        f"skipped {path}: the vault grows this file on purpose — its edits are vault content, "
                        "not a plugin fix",
                        err=True,
                    )
                    continue
                if row.stale:
                    typer.echo(
                        f"skipped {path}: the plugin changed it since the last upgrade — upgrade first", err=True
                    )
                    continue
                fp = backport_patch(
                    vault_rel=path,
                    plugin_rel=render.file.plugin_rel,
                    template=render.template,
                    rendered=render.file.rendered,
                    live=row.live,
                    vars_=vars_,
                )
                for w in fp.warnings:
                    typer.echo(f"warning: {w}", err=True)
                if fp.patch:
                    typer.echo(vault_drift.printable(fp.patch), nl=False)
                    produced += 1
            typer.echo(
                f"{produced} file(s) in the patch — apply with `git apply` in a plugin checkout, review it, "
                "then open a PR"
                if produced
                else "nothing to back-port",
                err=True,
            )
            # Only template-variable values are detected above; a name, company,
            # ID or path typed into an added line is not.
            typer.echo(
                "warning: review every added line for private vault content (names, companies, issue IDs, "
                "channels, paths) before opening a PR — the plugin repo is public, and only template-variable "
                "values are flagged automatically",
                err=True,
            )
            return

        if not rows:
            typer.echo("no vault drift — every plugin-owned file matches what the plugin last wrote")
            return
        typer.echo(f"vault drift — {len(rows)} plugin-owned file(s) differ from what the plugin last wrote:")
        for row in rows:
            typer.echo(f"  {row.status:<9} {row.path}  {row.describe()}")
            if diff and row.diff:
                typer.echo(row.diff, nl=False)
        if any(row.status == "edited" and row.path not in vault_drift.VAULT_DEVELOPED_FILES for row in rows):
            typer.echo("turn the edits into a plugin PR: scoutctl bootstrap drift --patch > fix.patch")


def _vault_bootstrap_config(vault: Path, *, plugin_root: Path | None = None) -> BootstrapConfig:
    """A BootstrapConfig for ``vault`` from its scout-config.yaml (exit 2 if absent; ConfigError's if malformed).

    ``plugin_root`` defaults to this engine's own plugin dir.
    """
    import yaml as _yaml

    from scout import __version__
    from scout.scripts.bootstrap import config_from_vault

    if not (vault / "scout-config.yaml").exists():
        typer.echo(f"no vault at {vault} — run /scout-setup", err=True)
        raise typer.Exit(code=2)
    try:
        return config_from_vault(
            vault, plugin_root=plugin_root or Path(__file__).parent.parent.parent, plugin_version=__version__
        )
    except (_yaml.YAMLError, UnicodeDecodeError, ValueError) as e:
        typer.echo(f"scout-config.yaml is malformed: {e}", err=True)
        raise typer.Exit(code=ConfigError.exit_code) from e


_register_bootstrap()


def _register_phases() -> None:
    phases_app = typer.Typer(help="Phase-fragment operations (back-port vault edits into phases/).")
    app.add_typer(phases_app, name="phases")

    @phases_app.command("backport")
    def cli_phases_backport(
        kind: str = typer.Option("all", "--kind", help="SKILL | DREAMING | RESEARCH | all"),
        apply: bool = typer.Option(
            False, "--apply", help="Write the round-trip-verified edits into phases/ (default: dry-run report)"
        ),
        vault_opt: str = typer.Option("", "--vault", help="Vault path (default: the resolved Scout data dir)"),
        plugin_root_opt: str = typer.Option(
            "",
            "--plugin-root",
            help="Plugin dir of the checkout to read and write phases/ in, e.g. a clone under "
            "~/.scout-worktrees/<name>/plugin (default: this engine's own)",
        ),
    ) -> None:
        """Back-port vault brain-file edits into their source phase fragments.

        Diffs each vault SKILL/DREAMING/RESEARCH against its last-assembled
        snapshot, maps each divergence to a phase fragment, conservatively
        re-templatizes it, and — only for hunks that round-trip — writes it back
        so a future /scout-update re-render carries it forward instead of
        sidecaring. Default is a read-only report; --apply writes the ✓ hunks.
        --apply refuses to write into the engine scheduled runs use (the engine
        pointer's root): pass --plugin-root to a separate clone instead. Never
        commits, pushes, or opens a PR — the operator does that.
        """
        from scout import paths as _paths
        from scout.engine_checkout import refuse_live_engine_write
        from scout.scripts.bootstrap import _template_vars, load_custom
        from scout.scripts.phase_backport import (
            apply_section_edits,
            apply_to_phase_text,
            build_rendered_sections,
            plan_backport,
        )

        vault = Path(vault_opt).expanduser() if vault_opt else _paths.data_dir()
        plugin_root = Path(plugin_root_opt).expanduser() if plugin_root_opt else None
        if plugin_root is not None and not (plugin_root / "phases").is_dir():
            typer.echo(f"scoutctl phases backport: {plugin_root} has no phases/ directory", err=True)
            raise typer.Exit(code=2)
        cfg = _vault_bootstrap_config(vault, plugin_root=plugin_root)
        phases_root = cfg.plugin_root / "phases"
        if apply and (why := refuse_live_engine_write(phases_root, home=Path.home())):
            typer.echo(f"scoutctl phases backport: {why}", err=True)
            raise typer.Exit(code=2)
        vars_ = _template_vars(cfg)
        snapshot_dir = vault / ".scout-state" / "last-assembled"

        kinds = ["SKILL", "DREAMING", "RESEARCH"] if kind == "all" else [kind.upper()]
        custom = load_custom(cfg)
        total_applied = 0
        for k in kinds:
            snap, live = snapshot_dir / f"{k}.md", vault / f"{k}.md"
            if not snap.exists() or not live.exists():
                missing = "snapshot" if not snap.exists() else "live"
                typer.echo(f"{k}: skip — missing {missing} file", err=True)
                continue
            sections = build_rendered_sections(
                phases_root, k, vars_, cfg.enabled_connectors, custom=custom, inputs=cfg.connector_inputs
            )
            results = plan_backport(snap.read_text(encoding="utf-8"), live.read_text(encoding="utf-8"), sections, vars_)
            applied = [r for r in results if r.status == "applied"]
            review = [r for r in results if r.status == "needs-review"]
            unmapped = [r for r in results if r.status == "unmapped"]

            typer.echo(
                f"\nphases backport — {k} ({len(results)} hunk(s): "
                f"{len(applied)} applied · {len(review)} needs-review · {len(unmapped)} unmapped)"
            )
            for r in applied:
                typer.echo(f"  ✓ applied      {r.phase_file}  «{r.section_name}»  (+{len(r.added)} line(s))")
            for r in review:
                where = f"{r.phase_file} «{r.section_name}»" if r.phase_file else "—"
                extra = f" [risky: {', '.join(r.risky_hits)}]" if r.risky_hits else ""
                typer.echo(f"  ⚠ needs-review {where} — {r.reason}{extra}")
            for r in unmapped:
                snippet = (r.added[0][:60] + "…") if r.added else ""
                typer.echo(f"  ✗ unmapped     {r.reason}  «{snippet}»")

            if apply and applied:
                sec_by_key = {(s.phase_file, s.section_name): s for s in sections}
                by_file: dict[Path, list] = {}
                for r in applied:
                    # applied hunks always round-tripped into a fragment, so
                    # phase_file is set; narrow the Path | None for the type checker.
                    if r.phase_file is None:
                        continue
                    by_file.setdefault(r.phase_file, []).append(r)
                for pf, rs in by_file.items():
                    text = pf.read_text(encoding="utf-8")
                    by_section: dict[str, list] = {}
                    for r in rs:
                        by_section.setdefault(r.section_name, []).append(r)
                    for section_name, srs in by_section.items():
                        sec = sec_by_key[(pf, section_name)]
                        # applied hunks always carry a resolved anchor + retemplatized
                        # body; assert the invariant so it's explicit (and type-safe).
                        edits = [(r.anchor, r.retemplatized) for r in srs]
                        assert all(a is not None and t is not None for a, t in edits)
                        edited_body = apply_section_edits(sec.raw_body, sec.rendered_body, edits)
                        text = apply_to_phase_text(text, sec.raw_body, edited_body)
                    pf.write_text(text, encoding="utf-8")
                    typer.echo(f"  → wrote {pf}")
                total_applied += len(applied)

        if apply:
            typer.echo(
                f"\nApplied {total_applied} hunk(s) to phases/. "
                "Review the diff, run the phase-assembly tests, commit, and open a PR."
            )
        else:
            typer.echo("\n(dry-run — re-run with --apply to write the ✓ applied hunks into phases/)")


_register_phases()


self_update_app = typer.Typer(help="Plugin self-update: check for a release, or apply one to a headless engine.")
app.add_typer(self_update_app, name="self-update")


@self_update_app.command("run")
def self_update_run_cmd(
    scheduled: bool = typer.Option(
        False, "--scheduled", help="Called by the heartbeat: honour the off-peak window and daily limits."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Say what would happen; fetch nothing, record nothing."),
    force: bool = typer.Option(False, "--force", help="Skip the scheduled gate and retry a target that kept failing."),
    json_out: bool = typer.Option(False, "--json"),
    detach: bool = typer.Option(
        False,
        "--detach",
        help="Start the attempt in its own session, logging to .scout-logs/self-update-<date>.log, and return "
        "at once (the heartbeat uses this, so the job's exit does not kill the update).",
    ),
) -> None:
    """Update an engine Scout.app doesn't manage to the latest published release (#360).

    Engines managed by Scout.app are never touched, and `dev` checkouts are only
    checked. Each attempt is recorded in .scout-logs/update-outcomes.jsonl and
    shows up in `scoutctl health`. Exits 0 whatever the outcome.
    """
    import datetime as _dt
    import json as _json
    import sys as _sys

    from scout import paths as _paths
    from scout.scripts.self_update_run import default_deps, run_lock, run_update, spawn_detached

    vault = _paths.data_dir()
    today = _dt.datetime.now().strftime("%Y-%m-%d")
    log_name = f"self-update-{today}.log"
    if detach:
        argv = [_sys.executable, "-m", "scout", "self-update", "run"]
        argv += [flag for flag, on in (("--scheduled", scheduled), ("--dry-run", dry_run), ("--force", force)) if on]
        pid = spawn_detached(argv, log_path=vault / ".scout-logs" / log_name)
        typer.echo(f"self-update: started in the background (pid {pid}); log: .scout-logs/{log_name}")
        return
    with run_lock(vault) as held:
        if not held:
            typer.echo("self-update: another attempt is still running")
            return
        deps = default_deps(vault, log_name=log_name if scheduled else None, quiet=json_out)
        attempt = run_update(deps, scheduled=scheduled, dry_run=dry_run, force=force)
    if scheduled and attempt.settled and not dry_run:
        # The heartbeat starts a scheduled run once per local day; this marker is what it checks.
        marker = vault / ".scout-cache" / f"self-update-{today}.done"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
    if json_out:
        payload = {
            "row": attempt.row.to_json() if attempt.row else None,
            "reason": attempt.reason,
            "settled": attempt.settled,
        }
        typer.echo(_json.dumps(payload))
    elif attempt.row is None:
        typer.echo(f"self-update: not now ({attempt.reason})")


@self_update_app.command("check")
def self_update_check(json_out: bool = typer.Option(False, "--json")) -> None:
    """Report installed-vs-available plugin version (read-only)."""
    import dataclasses as _dc
    import json as _json

    from scout.scripts.self_update import check as _check

    status = _check()
    if json_out:
        typer.echo(_json.dumps(_dc.asdict(status)))
    else:
        msg = (
            f"update available: {status.installed} -> {status.available}"
            if status.update_available
            else f"up to date ({status.installed})"
        )
        typer.echo(msg)


# In-place edits to the vault's scout-config.yaml. The file has several
# producers and carries comments (bootstrap state, `budget set`'s note, hand
# edits), so each command rewrites only its own block — never a pyyaml
# round-trip of the whole file, which deletes every comment in it.
config_app = typer.Typer(help="Edit the vault's scout-config.yaml in place, keeping its comments.")
app.add_typer(config_app, name="config")


@config_app.command("set-auto-update")
def config_set_auto_update_cmd(
    enabled: bool | None = typer.Option(
        None, "--enabled/--disabled", help="Turn auto-update on or off. Omit to leave it as it is."
    ),
    channel: str | None = typer.Option(
        None, "--channel", help="Release channel (stable). Omit to keep the current one, or stable if unset."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit the resulting block as JSON."),
) -> None:
    """Write the auto_update block of the vault's scout-config.yaml; every other line is kept."""
    import json as _json

    from scout.scripts.auto_update_config import AutoUpdateWriteError, write_auto_update

    if enabled is None and channel is None:
        typer.echo("nothing to set — pass --enabled, --disabled, or --channel", err=True)
        raise typer.Exit(2)

    try:
        payload = write_auto_update(enabled=enabled, channel=channel)
    except AutoUpdateWriteError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1) from e

    if as_json:
        typer.echo(_json.dumps(payload, indent=2))
        return
    state = "enabled" if payload["enabled"] is True else "disabled"
    verdict = f"wrote {payload['config_path']}" if payload["changed"] else "already set, nothing written"
    typer.echo(f"auto_update: {state} (channel: {payload['channel']}) — {verdict}")


@app.command()
def tui() -> None:
    """Launch the Textual action-items TUI."""
    try:
        # Lazy: textual is heavy; import only when the user invokes tui.
        from scout.tui.app import ScoutApp  # noqa: PLC0415
    except ImportError as e:
        from scout.errors import ActionItemError

        raise ActionItemError('Textual is not installed. Install with: uv pip install -e ".[full]"') from e
    ScoutApp().run()


@app.command("setup")
def setup_cmd(
    vault: Path | None = typer.Option(None, "--vault", help="Vault folder. Default: $SCOUT_DATA_DIR or ~/Scout."),
    instance_name: str = typer.Option("", "--instance-name"),
    name: str = typer.Option("", "--name", help="Your name (commits, the KB)."),
    email: str = typer.Option("", "--email", help="Your email (git config)."),
    timezone: str = typer.Option("", "--timezone", help="IANA zone to pin. Default: follow the computer."),
    connectors: str | None = typer.Option(None, "--connectors", help="Comma-separated. Default: detect."),
    slack_id: str = typer.Option("", "--slack-id"),
    github_username: str = typer.Option("", "--github-username"),
    github_repos: str = typer.Option("", "--github-repos"),
    max_budget: str = typer.Option("", "--max-budget", help="USD per session. Default 5.00."),
    daily_budget: str = typer.Option(
        "", "--daily-budget", help="USD per day: the budget the budget check gates on. Default: $50."
    ),
    first_run: bool | None = typer.Option(None, "--first-run/--no-first-run"),
    managed_by: str = typer.Option(
        "",
        "--managed-by",
        help="install.sh passes install.sh. Recorded on a fresh install or a vault with no recorded manager; "
        "an existing vault keeps its own (change it with `scoutctl bootstrap upgrade --managed-by`).",
    ),
    claude_bin: str = typer.Option("", "--claude-bin"),
    yes: bool = typer.Option(False, "--yes", help="Ask nothing; take the flags and defaults."),
    auto_update: bool | None = typer.Option(
        None,
        "--auto-update/--no-auto-update",
        help="Install each stable release automatically (engines set up by install.sh or Claude Code). "
        "Default: ask, or on with --yes.",
    ),
) -> None:
    """Set up Scout in a terminal: your details, connectors, the vault, the schedule."""
    import os

    from scout import paths as _paths
    from scout.setup_wizard import (
        HeadlessPrompter,
        NoTerminalPrompter,
        SetupOptions,
        TtyPrompter,
        default_deps,
        run_setup,
    )

    if vault is not None:
        os.environ["SCOUT_DATA_DIR"] = str(vault.expanduser())
    prompter = HeadlessPrompter() if yes else (TtyPrompter.open() or NoTerminalPrompter())
    opts = SetupOptions(
        vault=_paths.data_dir(),
        instance_name=instance_name,
        name=name,
        email=email,
        timezone=timezone,
        connectors=connectors,
        slack_id=slack_id,
        github_username=github_username,
        github_repos=github_repos,
        max_budget=max_budget,
        daily_budget=daily_budget,
        first_run=first_run,
        managed_by=managed_by,
        claude_bin=claude_bin,
        yes=yes,
        auto_update=auto_update,
    )
    raise typer.Exit(code=run_setup(opts, prompter, default_deps(claude_bin)))


def main() -> None:
    try:
        app()
    except ScoutError as e:
        print(str(e), file=sys.stderr)
        sys.exit(e.exit_code)
    except Exception as e:
        # KeyboardInterrupt and SystemExit are BaseException-but-not-Exception
        # and propagate naturally, preserving Ctrl-C and Typer's own exit codes.
        print(
            f"scoutctl: internal error: {type(e).__name__}: {e}",
            file=sys.stderr,
        )
        sys.exit(INTERNAL_ERROR_EXIT_CODE)


if __name__ == "__main__":
    main()
