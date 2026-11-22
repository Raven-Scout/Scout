"""scoutctl CLI entry point.

Top-level imports are intentionally minimal — Typer + stdlib only —
to keep `scoutctl --help` under 100ms. Heavy libraries (textual, rich,
jinja2, watchdog, scout.kb.*, scout.tui.*) must be imported inside
the subcommand functions, not at module level.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

import typer

from scout import __version__
from scout.errors import ConfigError, ScoutError
from scout.plural import agree, count

if TYPE_CHECKING:
    from scout.agent_runtimes.base import AgentRuntime
    from scout.repo_sync import AgnesHost
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


@app.command("diagnostics")
def cli_diagnostics(
    as_json: bool = typer.Option(False, "--json", help="Emit the full report as JSON."),
    no_jobs: bool = typer.Option(False, "--no-jobs", help="Skip the scheduler registration check."),
) -> None:
    """A redacted report for a bug report: versions, doctor, sandbox, runtime, recent runs. Never vault content."""
    from scout import diagnostics
    from scout import paths as _paths

    typer.echo(diagnostics.main(_paths.data_dir(), as_json=as_json, check_jobs=not no_jobs))


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


def _register_wishlist() -> None:
    from scout.wishlist.cli import app as wishlist_app

    app.add_typer(wishlist_app, name="wishlist")


_register_wishlist()


hook_app = typer.Typer(help="PostToolUse / lifecycle hook entry points (invoked by Claude Code).")
app.add_typer(hook_app, name="hook")

gates_app = typer.Typer(help="Checks that Scout's privacy gates are live before a scheduled run.")
app.add_typer(gates_app, name="gates")


@gates_app.command("probe")
def cli_gates_probe(
    claude_bin: str = typer.Option(
        "", "--claude-bin", help="The claude the runners launch (default: as bootstrap finds it)."
    ),
    vault: str | None = typer.Option(
        None, "--vault", help="Vault whose .scout-state caches the result (default: SCOUT_DATA_DIR / cwd)."
    ),
    cached: bool = typer.Option(
        False, "--cached", help="Reuse a fresh matching pass from .scout-state/gate-probe.json."
    ),
    as_json: bool = typer.Option(False, "--json", help="Print the result as JSON."),
    plugin_root: str | None = typer.Option(None, "--plugin-root", hidden=True),
    extra_flag: list[str] = typer.Option(
        [], "--extra-flag", hidden=True, help="Append a claude flag (manual checks, e.g. --bare)."
    ),
) -> None:
    """Exit 0 when claude, run with the runners' flags, fires Scout's kb-read-gate; exit 3 when it doesn't.

    Runs claude once against a local fake endpoint (no model, no spend, never your
    credentials) in a throwaway fixture vault. Where there's no network sandbox it
    refuses instead of running: exit 4 when a setting names an endpoint it can't
    keep on this machine, exit 5 when the claude launcher hides its provider
    switches. The runners call this with --cached before each run and skip the run
    on any non-zero exit. See scout/agent_runtimes/gate_probe.py and spec
    2026-11-13 §3.5.
    """
    import json

    from scout import paths
    from scout.agent_runtimes import gate_probe
    from scout.scripts.bootstrap import resolve_claude_bin

    binary = Path(resolve_claude_bin(claude_bin))
    root = Path(plugin_root) if plugin_root else gate_probe.default_plugin_root()
    flags = (*gate_probe.PROBE_FLAGS, *extra_flag)
    data_dir = paths.data_dir(vault)  # its .claude/settings*.json go into the probe, as they would into a run
    if cached:
        result = gate_probe.cached_probe(data_dir, binary, plugin_root=root, flags=flags)
    else:
        result = gate_probe.probe(binary, plugin_root=root, flags=flags, vault=data_dir)
    if as_json:
        typer.echo(json.dumps(result.to_json(), indent=2))
    else:
        typer.echo(f"{'ok' if result.ok else 'REFUSED'}: {result.reason} (claude {result.claude_version or 'unknown'})")
    if not result.ok:
        typer.echo(f"[scout:gates] {result.reason}", err=True)
        raise typer.Exit(gate_probe.UNSAFE_EXIT.get(result.unsafe, 3))


sandbox_app = typer.Typer(help="The scheduled runs' OS write sandbox (spec 2026-11-15, Phase 1c).")
app.add_typer(sandbox_app, name="sandbox")


@sandbox_app.command("profile")
def cli_sandbox_profile(
    vault: str | None = typer.Option(None, "--vault", help="The run's vault (default: SCOUT_DATA_DIR / cwd)."),
) -> None:
    """Print the sandbox-exec profile for a scheduled run of this vault; the exit code is the status.

    0 enforced (profile on stdout), 3 failed (a sandbox is expected here but couldn't be built or self-tested:
    the runners skip the run), 6 unavailable on this platform, 7 turned off (`scoutctl sandbox off`), 8 Claude
    Code's own Bash sandbox is on (they can't nest). The runners run the session sandboxed only on 0, go ahead
    as today on 6/7/8, and skip on anything else. The reason goes to stderr as `[scout:sandbox] <status>: …`.
    """
    from scout import paths
    from scout.sandbox import launch

    result = launch.prepare(paths.data_dir(vault))
    if result.profile:
        typer.echo(result.profile, nl=False)
    typer.echo(f"[scout:sandbox] {result.status}: {result.reason}", err=True)
    raise typer.Exit(result.exit_code)


@sandbox_app.command("status")
def cli_sandbox_status(
    vault: str | None = typer.Option(None, "--vault", help="The run's vault (default: SCOUT_DATA_DIR / cwd)."),
    as_json: bool = typer.Option(False, "--json", help="Print the status as JSON."),
) -> None:
    """Say whether the next scheduled run would be sandboxed, and why (builds and self-tests the profile)."""
    import json

    from scout import paths
    from scout.sandbox import launch

    result = launch.prepare(paths.data_dir(vault))
    if as_json:
        typer.echo(json.dumps({"status": result.status, "reason": result.reason}))
    else:
        typer.echo(f"run sandbox: {result.status} ({result.reason})")


@sandbox_app.command("off")
def cli_sandbox_off() -> None:
    """Run scheduled sessions without the OS write sandbox (the hook gates stay on). Health will say so.

    The owner only: a scheduled or engine-launched run is refused (exit 2), as for `agent-runtime set-limit`.
    """
    import os

    from scout.sandbox import launch
    from scout.scripts.privacy_migrate import headless_reason

    why = headless_reason(os.environ)
    if why:
        typer.echo(f"refused: {why}. Only the owner may turn the run sandbox off; nothing was changed.", err=True)
        raise typer.Exit(2)
    path = launch.set_switch(False)
    typer.echo(f"run sandbox: off (wrote {path}); turn it back on with `scoutctl sandbox on`")


@sandbox_app.command("on")
def cli_sandbox_on() -> None:
    """Run scheduled sessions inside the OS write sandbox (the default)."""
    from scout.sandbox import launch

    path = launch.set_switch(True)
    typer.echo(f"run sandbox: on (wrote {path})")


protection_app = typer.Typer(help="What protects scheduled runs: sandbox, privacy-check probe, integrity, limits.")
app.add_typer(protection_app, name="protection")


@protection_app.command("status")
def cli_protection_status(
    vault: str | None = typer.Option(None, "--vault", help="The vault (default: SCOUT_DATA_DIR / cwd)."),
    as_json: bool = typer.Option(False, "--json", help="Print the summary as JSON (the app's contract)."),
    check_sandbox: bool = typer.Option(
        False,
        "--check-sandbox",
        help="Build and self-test the write sandbox's profile now (runs sandbox-exec in a temp dir) instead of "
        "taking its status from the last run.",
    ),
) -> None:
    """The write sandbox, the last privacy-check probe, the last run's integrity check, the broker, and each slot's
    runtime and limits, with what needs fixing. Always exits 0; never starts claude.

    Read-only: it starts no process and writes nothing, so whether the sandbox works comes from the newest run that
    recorded it. --check-sandbox self-tests it instead, as `scoutctl sandbox status` does.
    """
    import json

    from scout import paths, run_protection

    data = run_protection.report(paths.data_dir(vault), check_sandbox=check_sandbox)
    typer.echo(json.dumps(data, indent=2) if as_json else run_protection.format_text(data))


@sandbox_app.command("snapshot")
def cli_sandbox_snapshot(
    vault: str | None = typer.Option(None, "--vault", help="The run's vault (default: SCOUT_DATA_DIR / cwd)."),
) -> None:
    """Print the start state the post-run integrity check compares against (JSON, one line).

    The runners call it outside the sandbox before claude starts and keep the output in memory, then pipe it to
    `scoutctl sandbox verify` after claude exits. Exit 12 (and nothing on stdout) when it can't be taken.
    """
    from scout import paths
    from scout.sandbox import integrity

    try:
        snap = integrity.snapshot(paths.data_dir(vault))
    except integrity.IntegrityError as e:
        typer.echo(f"[scout:integrity] couldn't take the start snapshot: {e}", err=True)
        raise typer.Exit(integrity.STATUS_EXIT["error"]) from None
    typer.echo(integrity.dumps(snap))


@sandbox_app.command("verify")
def cli_sandbox_verify(
    vault: str | None = typer.Option(None, "--vault", help="The run's vault (default: SCOUT_DATA_DIR / cwd)."),
    label: str = typer.Option("scheduled", "--label", help="The run's mode, for the restoring commit's message."),
) -> None:
    """After a run: compare the vault's protected tracked files with the start snapshot on stdin, and put back
    what the run changed in git.

    Exit 0 clean, 10 restored (a new commit on each changed branch, and the index), 11 a tag or the stash still
    holds a change (reported, not rewritten), 12 the check couldn't run. Paths go to stdout, never file content.
    """
    import sys

    from scout import paths
    from scout.sandbox import integrity

    data_dir = paths.data_dir(vault)
    try:
        start = integrity.loads(sys.stdin.read())
    except integrity.IntegrityError as e:
        result = integrity.Result("error", reason=str(e))
    else:
        result = integrity.verify(data_dir, start, label=label)
    for line in result.lines():
        typer.echo(line)
    raise typer.Exit(result.exit_code)


@sandbox_app.command("replay")
def cli_sandbox_replay(
    transcripts: str = typer.Option(..., "--transcripts", help="A Claude Code project dir of session .jsonl files."),
    vault: str = typer.Option(..., "--vault", help="The vault those sessions ran in (their paths resolve here)."),
    limit: int = typer.Option(50, "--limit", help="How many of the newest scheduled-run sessions to read."),
    as_json: bool = typer.Option(False, "--json", help="Print the counts as JSON."),
    brokers: bool = typer.Option(
        False,
        "--brokers",
        help="Judge against the profile the run brokers tighten (Phase 1d); say what replaces each refused call.",
    ),
) -> None:
    """Read-only: which tool calls of past scheduled runs the sandbox would refuse. Prints counts and forms only."""
    import json

    from scout.sandbox import replay

    report = replay.replay(Path(transcripts).expanduser(), Path(vault).expanduser(), limit=limit, brokers=brokers)
    typer.echo(json.dumps(report, indent=2) if as_json else replay.format_report(report))


broker_app = typer.Typer(help="The scheduled runs' commit and Telegram brokers (spec 2026-11-16, Phase 1d).")
app.add_typer(broker_app, name="broker")


@broker_app.command("serve")
def cli_broker_serve(
    vault: str = typer.Option(..., "--vault", help="The run's vault."),
    broker_dir: str = typer.Option(..., "--dir", help="The run's broker folder (mode 0700); the socket goes here."),
    parent: int | None = typer.Option(None, "--parent", help="Stop when this process (the wrapper) is gone."),
) -> None:
    """Outside the sandbox: commit and send for one run until SIGTERM. claude-with-retry.sh starts it."""
    from scout.sandbox import broker

    try:
        code = broker.serve(Path(vault), Path(broker_dir), parent=parent)
    except (broker.BrokerError, OSError) as e:
        typer.echo(f"broker: not started: {e}", err=True)
        raise typer.Exit(1) from None
    raise typer.Exit(code)


@broker_app.command("profile")
def cli_broker_profile(
    vault: str = typer.Option(..., "--vault", help="The run's vault."),
    broker_dir: str = typer.Option(..., "--dir", help="The run's broker folder."),
) -> None:
    """Read the run's 1c profile on stdin; print it tightened for a run whose brokers answer (exit 0).

    Exit 1 (reason on stderr, `.scout-logs/.run-broker.json` says unavailable) when the broker doesn't answer, the
    vault's git-safe-commit.sh predates it, or the tightened profile fails its self-test: the run keeps the 1c
    profile then.
    """
    import sys

    from scout.sandbox import broker

    data_dir = Path(vault)
    try:
        text = broker.build_profile(data_dir, Path(broker_dir), sys.stdin.read())
    except broker.BrokerError as e:
        broker.write_status(data_dir, "unavailable", str(e))
        typer.echo(f"[scout:broker] unavailable: {e}", err=True)
        raise typer.Exit(1) from None
    broker.write_status(data_dir, "on", "commits and Telegram go through the run's broker")
    typer.echo(text, nl=False)


@broker_app.command("commit", context_settings={"ignore_unknown_options": True, "allow_interspersed_args": False})
def cli_broker_commit(
    args: list[str] = typer.Argument(None, help="MESSAGE PATH…, as git-safe-commit.sh takes them."),
) -> None:
    """In-session client for scripts/git-safe-commit.sh: the run's broker commits (exit 6: queued, no SHA yet)."""
    from scout.sandbox import broker

    args = list(args or [])
    if args and args[0] == "--":
        args = args[1:]
    if len(args) < 2:
        typer.echo('git-safe-commit: usage: git-safe-commit.sh "commit message" <path> [<path> ...]', err=True)
        raise typer.Exit(2)
    raise typer.Exit(broker.client_commit(args[0], args[1:]))


@broker_app.command("drain")
def cli_broker_drain(
    vault: str | None = typer.Option(None, "--vault", help="The vault (default: SCOUT_DATA_DIR / cwd)."),
    deadline: float = typer.Option(120.0, "--deadline", help="Start no entry after this many seconds; the rest wait."),
    run: str | None = typer.Option(None, "--run", help="The wrapper's drain: close this run afterwards."),
) -> None:
    """Outside the sandbox: apply what runs queued while their broker didn't answer, each checked like a live one."""
    import signal

    from scout import paths
    from scout.sandbox import broker

    def stopped(_sig: int, _frame: object) -> None:
        raise SystemExit(143)  # unwinds through run_commit, which stops the helper's process group first

    signal.signal(signal.SIGTERM, stopped)
    result = broker.drain(paths.data_dir(vault), deadline_s=deadline, run=run)
    if result.done or result.kept or result.failed or result.busy or result.unsafe:
        typer.echo(result.line())
    for reason in result.reasons:
        typer.echo(f"  failed: {reason}")


changes_app = typer.Typer(
    help="The changes map: what changed in Scout's own brain, and why.", invoke_without_command=True
)
engagement_app = typer.Typer(
    help="Engagement signals for the changes map, and the owner's consent.", invoke_without_command=True
)
app.add_typer(changes_app, name="changes")
app.add_typer(engagement_app, name="engagement")


def _changes_document(command: str, since: str, build: Callable[..., dict], *, installed: bool = False) -> None:
    """Shared tail of ``scoutctl changes --json`` / ``engagement --json``: resolve --since and the vault, print.

    ``installed``: owner engagement is keyed to the installed vault (``scout.changes.vault``), never the cwd's."""
    import json as _json
    import os
    from datetime import UTC
    from datetime import datetime as _dt

    from scout import paths as _paths
    from scout.changes.vault import installed_vault
    from scout.run_ledger import parse_since

    now = _dt.now(UTC)
    try:
        cutoff = parse_since(since, now=now)
    except ValueError as e:
        typer.echo(f"scoutctl {command}: {e}", err=True)
        raise typer.Exit(code=2) from None
    vault = installed_vault(os.environ) if installed else _paths.data_dir()
    if not vault.is_dir():
        typer.echo(f"scoutctl {command}: vault directory missing", err=True)
        raise typer.Exit(code=2)
    typer.echo(_json.dumps(build(vault, now, cutoff)))


@changes_app.callback()
def cli_changes(
    ctx: typer.Context,
    json_out: bool = typer.Option(False, "--json", help="Emit the versioned JSON document the app reads."),
    since: str = typer.Option("30d", "--since", help="How far back: 30d, 48h, 90m, or an ISO time."),
    limit: int = typer.Option(500, "--limit", min=1, max=5000, help="At most this many changes, newest first."),
) -> None:
    if ctx.invoked_subcommand is not None:
        return
    if not json_out:
        typer.echo(ctx.get_help())
        return
    from scout.changes.report import changes_report

    _changes_document(
        "changes", since, lambda vault, now, cutoff: changes_report(vault, now=now, since=cutoff, limit=limit)
    )


@engagement_app.callback()
def cli_engagement(
    ctx: typer.Context,
    json_out: bool = typer.Option(False, "--json", help="Emit the versioned JSON document the app reads."),
    since: str = typer.Option("30d", "--since", help="How far back: 30d, 48h, 90m, or an ISO time."),
) -> None:
    if ctx.invoked_subcommand is not None:
        return
    if not json_out:
        typer.echo(ctx.get_help())
        return
    import os

    from scout.changes.report import engagement_report
    from scout.config import resolve_timezone

    _changes_document(
        "engagement",
        since,
        lambda vault, now, cutoff: engagement_report(
            vault, os.environ, now=now, since=cutoff, tz=resolve_timezone(vault)
        ),
        installed=True,
    )


@changes_app.command("scan")
def cli_changes_scan(
    since: str | None = typer.Option(
        None,
        "--since",
        help="How far back: 2d, 48h, 90m, or an ISO time. Default: every commit since the last plain scan.",
    ),
    backfill: bool = typer.Option(False, "--backfill", help="Mark rows as backfilled; --since defaults to 2026-06-01."),
) -> None:
    """Read the vault's git log and record any new changes (idempotent).

    With no --since, it reads from where the last plain scan stopped (its first time: the last 14 days), so a
    gap between runs loses nothing. An explicit --since or --backfill reads that window and leaves the mark."""
    from datetime import UTC
    from datetime import datetime as _dt

    from scout import paths
    from scout.changes.scan import scan, scan_new
    from scout.run_ledger import parse_since

    now = _dt.now(UTC)
    if not (since or backfill):
        typer.echo(count(len(scan_new(paths.require_data_dir(), now=now)), "new change"))
        return
    try:
        cutoff = parse_since(since or "2026-06-01", now=now)
    except ValueError as e:
        typer.echo(f"scoutctl changes scan: {e}", err=True)
        raise typer.Exit(2) from None
    typer.echo(count(len(scan(paths.require_data_dir(), since=cutoff, backfilled=backfill)), "new change"))


@engagement_app.command("record")
def cli_engagement_record(
    kind: str = typer.Argument(
        ..., help="session-reliance, app-edit, inline-comment, approval (owner only) or a run kind."
    ),
    surface: str = typer.Option(
        ..., "--surface", help="slack-dm, telegram, kb, action-items, skill, scoutctl, vault-session, app."
    ),
    ref: str | None = typer.Option(
        None,
        "--ref",
        help="An id: a Slack message ts, a commit sha or a slug. Never content; a path outside the vault is refused.",
    ),
    sentiment: str | None = typer.Option(None, "--sentiment", help="positive, negative or neutral."),
    ts: str | None = typer.Option(None, "--ts", help="ISO time; default now."),
) -> None:
    """Append one engagement row. Owner kinds are refused (exit 2) in a scheduled run."""
    import os
    from datetime import UTC
    from datetime import datetime as _dt

    from scout.changes.models import OWNER_KINDS, Signal
    from scout.changes.store import append_signal, is_owner_caller
    from scout.changes.vault import installed_vault

    vault = installed_vault(os.environ)
    try:
        if ref is not None and ref.startswith(("/", "~")):
            raise ValueError("--ref is an id (a Slack ts, a commit sha), not a path")
        when = _dt.fromisoformat(ts) if ts else _dt.now(UTC)
        signal = Signal.from_row(
            {"ts": when.isoformat(), "kind": kind, "surface": surface, "ref": ref, "sentiment": sentiment}
        )
        if signal.kind in OWNER_KINDS and not is_owner_caller(os.environ, vault):
            raise PermissionError(f"{signal.kind!r} is owner-sourced; a run may not write it")
        if not vault.is_dir():
            raise ValueError(f"vault directory missing: {vault}")
        append_signal(signal, vault=vault, env=os.environ)
    except (ValueError, PermissionError) as e:
        typer.echo(f"scoutctl engagement record: {e}", err=True)
        raise typer.Exit(2) from None


@engagement_app.command("consent")
def cli_engagement_consent(
    out_of_vault: str | None = typer.Option(
        None, "--out-of-vault", help="yes or no: may Scout read activity outside the vault?"
    ),
    show: bool = typer.Option(False, "--show", help="Print yes, no or 'not asked'."),
) -> None:
    """The owner's out-of-vault answer. Only an owner session may set it."""
    import os

    from scout.changes import consent

    if show:
        answer = consent.out_of_vault_answer(os.environ)
        typer.echo("not asked" if answer is None else "yes" if answer else "no")
        return
    if out_of_vault not in ("yes", "no"):
        typer.echo("scoutctl engagement consent: pass --out-of-vault yes|no or --show", err=True)
        raise typer.Exit(2)
    from scout.changes.store import is_owner_caller
    from scout.changes.vault import installed_vault

    try:
        if not is_owner_caller(os.environ, installed_vault(os.environ)):
            raise PermissionError("the consent answer is the owner's; a scheduled run may not write it")
        consent.set_out_of_vault(out_of_vault == "yes", os.environ)
    except PermissionError as e:
        typer.echo(f"scoutctl engagement consent: {e}", err=True)
        raise typer.Exit(2) from None


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
    """PreToolUse hook: deny a restrict_tools custom connector's unlisted MCP tools, in every session in the vault."""
    from scout.hooks.mcp_tool_gate import main as mcp_tool_gate_main

    raise typer.Exit(mcp_tool_gate_main())


@hook_app.command("kb-read-gate")
def hook_kb_read_gate() -> None:
    """PreToolUse hook: deny a scheduled run's shell/Grep/Glob/Read of knowledge-base/ around scoutctl kb."""
    from scout.hooks.kb_read_gate import main as kb_read_gate_main

    raise typer.Exit(kb_read_gate_main())


@hook_app.command("kb-write-gate")
def hook_kb_write_gate() -> None:
    """PreToolUse hook: deny a scheduled run's file write into what decides which checks run (Phase 1b)."""
    from scout.hooks.kb_write_gate import main as kb_write_gate_main

    raise typer.Exit(kb_write_gate_main())


@hook_app.command("kb-read-label")
def hook_kb_read_label() -> None:
    """PostToolUse hook: attach a KB note's privacy label to a Read of it (private: never to a shared surface)."""
    from scout.hooks.kb_read_label import main as kb_read_label_main

    raise typer.Exit(kb_read_label_main())


@hook_app.command("front-page-reads")
def hook_front_page_reads() -> None:
    """PostToolUse hook: warn a run that edits a front page without reading its project's knowledge/ notes."""
    from scout.hooks.front_page_reads import main as front_page_reads_main

    raise typer.Exit(front_page_reads_main())


@hook_app.command("shared-read-record")
def hook_shared_read_record() -> None:
    """PostToolUse hook: record what an Agnes shared-notes tool showed, so check-outbound recognises it."""
    from scout.hooks.shared_read_record import main as shared_read_record_main

    raise typer.Exit(shared_read_record_main())


@hook_app.command("outbound-gate")
def hook_outbound_gate() -> None:
    """PreToolUse hook: check-outbound on a Slack/connector write tool's input; runs blocked, the owner warned."""
    from scout.hooks.outbound_gate import main as outbound_gate_main

    raise typer.Exit(outbound_gate_main())


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

_READER_HELP = (
    "owner | autonomous | elsewhere | delegate:<id> | agent:person:<email> | agent:group:<id>. Can only narrow."
)


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
    report: bool = typer.Option(
        False, "--report", help="Rank over-budget KB files; list privacy markers and problems."
    ),
    top: int = typer.Option(20, "--top", help="Rows to show with --report."),
    lossless_rev: str | None = typer.Option(None, "--lossless-rev", help="Old revision for the lossless check."),
    lossless_path: str | None = typer.Option(
        None, "--lossless-path", help="Vault-relative file for the lossless check."
    ),
    repo: Path | None = typer.Option(None, "--repo", help="Vault repo (default: the git top level of the cwd)."),
) -> None:
    """Lint the vault's KB structure. Exit 1 = blocked (block-mode path, or privacy) or lossless tokens missing."""
    root = _kb_repo(repo)
    if lossless_rev or lossless_path:
        from scout.kb.lossless import lossless_check

        if not (lossless_rev and lossless_path):
            print("--lossless-rev and --lossless-path go together", file=sys.stderr)
            raise typer.Exit(2)
        missing = lossless_check(root, lossless_rev, lossless_path)
        for tok in missing:
            print(f"missing: {tok}")
        print(f"lossless: {count(len(missing), 'token')} missing")
        raise typer.Exit(1 if missing else 0)
    if report:
        from scout.kb.lint import config_problems, over_budget
        from scout.kb.lint_config import load_lint_config
        from scout.kb.lint_privacy import format_report, privacy_report

        cfg = load_lint_config(root)
        for problem in config_problems(root, cfg):
            print(f"config: {problem}")
        rows = over_budget(root, cfg)
        print(f"{count(len(rows), 'file')} over budget")
        for rel, size, budget in rows[:top]:
            print(f"{size / 1024:9.1f} KB  (budget {budget / 1024:.0f} KB, +{(size - budget) / 1024:.1f} KB)  {rel}")
        for line in format_report(privacy_report(root)):
            print(line)
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
        None,
        "--reader",
        help=_READER_HELP,
    ),
    as_json: bool = typer.Option(False, "--json", help="Print a JSON list."),
    repo: Path | None = typer.Option(None, "--repo", help="Vault repo (default: the git top level of the cwd)."),
) -> None:
    """Print each file's privacy label (visibility, local-only, shared). Exit 2 = bad path or reader.

    owner and autonomous readers get labels. An agent reader gets
    {"path": ..., "denied": true} for every path, without the file being looked
    at, so a denial says nothing about whether the file exists or what it is.
    A delegate reader gets the label of each existing file it may see and the
    same denial for every other path, missing ones included.
    """
    args = _kb_args("classify", dict(locals()))

    def body(who: str, root: Path) -> object:
        from scout.kb.privacy import FULL_READERS, label_file, load_policy, normalize_rel, reads_any, visible_to
        from scout.kb.read import Output, UsageError

        if not reads_any(who):
            return Output(
                data=[{"path": p, "denied": True} for p in paths],
                lines=[f"{p}: denied" for p in paths],
                denied=len(paths),
            )
        policy = load_policy(root)
        data: list[dict[str, object]] = []
        lines: list[str] = []
        shown = []
        for p in paths:
            try:
                rel = normalize_rel(root, p)
            except ValueError as e:
                raise UsageError(str(e)) from e
            label = label_file(root, rel, policy)
            hidden_kind = ".git" in rel.split("/") or not (root / rel).is_file()
            if not visible_to(label, who, policy) or (who not in FULL_READERS and hidden_kind):
                # a delegate: a hidden file, a missing one and anything under .git/ get the same denial
                data.append({"path": p, "denied": True})
                lines.append(f"{p}: denied")
                continue
            data.append(label.to_json())
            shown.append(label)
            lines.append(f"[private] {label.path}" if label.visibility == "private" else label.path)
        return Output(
            data=data, lines=lines, labels=[label.to_json() for label in shown], denied=len(paths) - len(shown)
        )

    _kb_run(args, reader, repo, as_json, body)


@kb_app.command("policy")
def kb_policy(
    reader: str | None = typer.Option(
        None,
        "--reader",
        help=_READER_HELP,
    ),
    as_json: bool = typer.Option(False, "--json", help="Print the effective policy as JSON."),
    repo: Path | None = typer.Option(None, "--repo", help="Vault repo (default: the git top level of the cwd)."),
) -> None:
    """Print the effective privacy policy: engine default plus the vault's scout-config.yaml overlay.

    Every reader gets the same output: it is configuration (globs, surface ids),
    not note content. --reader is still resolved, so a widening flag is refused.
    """
    args = _kb_args("policy", dict(locals()))

    def body(_who: str, root: Path) -> object:
        from scout.kb.privacy import load_policy
        from scout.kb.read import Output

        data = load_policy(root).to_json()
        lo = data["local_only"]
        lines = [
            "local_only.always:    " + " ".join(lo["always"]),
            "local_only.default:   " + (" ".join(lo["default"]) or "(none)"),
            "local_only.sync_allow: " + " ".join(lo["sync_allow"]),
            "visibility_defaults:",
            *(f"  {entry['glob']} -> {entry['visibility']}" for entry in data["visibility_defaults"]),
            "surfaces.owner_only:  " + " ".join(data["surfaces"]["owner_only"]),
            "surfaces.shared:      " + " ".join(data["surfaces"]["shared"]),
            "shared_files:         " + (" ".join(data["shared_files"]) or "(none)"),
            "federation.enabled:   " + ("true" if data["federation"]["enabled"] else "false"),
            "federation_ceilings:",
            *(_ceiling_line(entry) for entry in data["federation_ceilings"]),
            *(
                ["federation_ceilings (overlay, narrowing only):"]
                + [_ceiling_line(entry) for entry in data["federation_ceilings_overlay"]]
                if data["federation_ceilings_overlay"]
                else []
            ),
            "federation_defaults:" + ("" if data["federation_defaults"] else "  (none)"),
            *(f"  {entry['glob']} -> {_scope_text(entry['scope'])}" for entry in data["federation_defaults"]),
            "warnings:             " + (" ".join(data["warnings"]) or "(none)"),
        ]
        return Output(data=data, lines=lines)

    _kb_run(args, reader, repo, as_json, body)


# ----- Shared Areas (Agnes #6090 §4, slice S1a): logic in scout/kb/share.py -------


def _share_values(extra: list[str], with_: list[str]) -> list[str]:
    """``--with a --with b``, ``--with a,b`` and ``--with a b`` (spare positionals) all name people."""
    return [v.strip() for raw in (*with_, *extra) for v in raw.split(",") if v.strip()]


def _owner_only(as_json: bool) -> None:
    """Sharing is the owner's act: a scheduled run (or any non-owner reader) may not change it (#486 §8.3)."""
    import os

    from scout.kb.reader import is_scheduled

    if is_scheduled(os.environ):
        _share_refused("refused: only the owner, in their own interactive session, may change what is shared", as_json)


def _share_refused(message: str, as_json: bool, code: int = 1) -> NoReturn:
    import json

    if as_json:
        print(json.dumps({"refused": message, "written": False}, indent=2))
    print(message, file=sys.stderr)
    raise typer.Exit(code)


def _interactive() -> bool:
    return sys.stdin.isatty()


def _pick_groups(directory: object) -> list[str]:
    """``--pick-group``: number the owner's Agnes share targets (Everyone left out) and read a choice."""
    from scout.kb.share import Directory

    if not isinstance(directory, Directory) or not _interactive():
        _share_refused("refused: --pick-group needs a vault linked to Agnes and a terminal", False)
    targets = [t for t in directory.share_targets() or [] if not t.is_everyone]
    if not targets:
        _share_refused("refused: Agnes returned no groups you can share with", False)
    for n, t in enumerate(targets, 1):
        print(f"  {n}. {t.name}  (group:{t.id})")
    answer = typer.prompt("Groups to share with (numbers, comma-separated)")
    picked: list[str] = []
    for part in answer.split(","):
        if not part.strip().isdigit() or not 1 <= int(part) <= len(targets):
            _share_refused(f"refused: {part.strip()!r} isn't one of the numbers above", False, 2)
        picked.append(f"group:{targets[int(part) - 1].id}")
    return picked


@kb_app.command("share")
def kb_share(
    target: str = typer.Argument(..., help="A folder (shared as <folder>/**) or one note of the vault."),
    extra: list[str] = typer.Argument(None, help="More people for --with (`--with a@example.com b@example.com`)."),
    with_: list[str] = typer.Option(
        [], "--with", help="An email, person:<email>, or group:<id or name>. Repeatable; commas work too."
    ),
    pick_group: bool = typer.Option(False, "--pick-group", help="Choose groups from your Agnes share targets."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Write without asking (after reading the preview)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the preview; write nothing."),
    as_json: bool = typer.Option(False, "--json", help="Print the preview (and whether it was written) as JSON."),
    repo: Path | None = typer.Option(None, "--repo", help="Vault repo (default: the git top level of the cwd)."),
) -> None:
    """Share a folder or note with named people or your Agnes groups (owner only).

    Previews the notes that would be newly shared, the ones left out (private,
    local-only, over their ceiling, deciding for themselves) and the copy
    warning, then writes scout-config.yaml → privacy.federation_defaults and
    turns sharing on. Agnes enforces it once you commit scout-config.yaml and
    then run `scoutctl repo sync`, which refuses while the change is uncommitted.

    On a vault linked to Agnes it also clears the stops `kb unshare` (or Agnes)
    made on this share for the people named here, so they read it again at once.
    That is the only way a stop is cleared, and only by the owner at the
    keyboard: a scheduled run, an agent run and a delegate checkout are refused.
    Exit 1 = refused or not confirmed, 2 = bad argument or a clear Scout refuses.
    """
    import json
    import os

    from scout.kb import share_stops
    from scout.kb.share import ShareRefused, ShareUsage, apply, directory_for, plan_share

    _owner_only(as_json)
    root = _kb_repo(repo)
    directory = directory_for(root)
    values = _share_values(extra or [], with_)
    if pick_group:
        values += _pick_groups(directory)
    try:
        plan = plan_share(root, target, values, directory=directory)
    except ShareUsage as e:
        _share_refused(f"error: {e}", as_json, 2)
    except ShareRefused as e:
        _share_refused(str(e), as_json)
    client, link_problem = share_stops.connect(root)
    view, why = share_stops.fetch_view(client)
    if view is not None:
        plan.clears = share_stops.share_clears(view, plan.target, plan.principals)
    else:
        plan.stops_note = link_problem or why
    if link_problem and not as_json:
        print(f"warning: {link_problem}: its stops on this share, if any, stay in force.", file=sys.stderr)
    clears = plan.clears if plan.clears else None
    if clears is not None and client is not None and not dry_run:
        refusal = share_stops.clear_refusal(root, os.environ, client)
        if refusal:
            _share_refused(refusal + "; nothing was written", as_json, 2)
    if not as_json:
        for line in plan.lines():
            print(line)
    written = False
    cleared: list[str] = []
    if (plan.changed or clears is not None) and not dry_run:
        if not yes:
            if not _interactive() or as_json:
                if as_json:
                    print(json.dumps({**plan.to_json(), "written": False, "cleared": []}, indent=2))
                print("Preview only: nothing was written. Re-run with --yes to share.", file=sys.stderr)
                raise typer.Exit(1)
            if not typer.confirm("Share these notes?"):
                print("Nothing was written.")
                raise typer.Exit(1)
        if plan.changed:
            apply(root, plan)
            written = True
        if clears is not None and client is not None:
            try:
                cleared = share_stops.apply_clears(root, os.environ, client, clears)
            except share_stops.ClearRefused as e:
                _share_refused(str(e), as_json, 2)
    if as_json:
        print(json.dumps({**plan.to_json(), "written": written, "cleared": cleared}, indent=2))
        return
    for line in cleared:
        print(line)
    if written:
        from scout.kb.share import SYNC_NOTE

        print("Wrote scout-config.yaml → privacy.federation_defaults.")
        print(SYNC_NOTE)
    elif not plan.changed and clears is None:
        print("Nothing to change: already shared that way.")


@kb_app.command("unshare")
def kb_unshare(
    target: str = typer.Argument(..., help="A shared folder or note."),
    extra: list[str] = typer.Argument(None, help="More people for --with."),
    with_: list[str] = typer.Option(
        [], "--with", help="Stop sharing with this person or group only (default: everyone). Repeatable."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would stop; write nothing."),
    as_json: bool = typer.Option(False, "--json", help="Print the result as JSON."),
    repo: Path | None = typer.Option(None, "--repo", help="Vault repo (default: the git top level of the cwd)."),
) -> None:
    """Stop sharing a folder or note, with some people or everyone (owner only).

    On a vault linked to Agnes, first asks Agnes to stop the share at once, so
    readers lose it from their next read (a stop for one member of a shared
    group included), then narrows scout-config.yaml →
    privacy.federation_defaults. Only `kb share` clears that stop. While Agnes
    has no stop endpoint, or can't be reached, the stop takes effect once you
    commit scout-config.yaml and run `scoutctl repo sync`. A note that shares
    itself in its own frontmatter is listed, not edited.

    Exit 1 = refused, 2 = bad argument, 4 = the vault was narrowed but Agnes
    didn't confirm every stop (unreachable, an error, a refusal): it may serve
    the share until the next sync, and running the same command again retries
    the stop. An Agnes without the stop endpoint, or with sharing off, isn't a
    failure (exit 0).
    """
    import json

    from scout.kb import share_stops
    from scout.kb.share import ShareRefused, ShareUsage, apply, directory_for, plan_unshare

    _owner_only(as_json)
    root = _kb_repo(repo)
    values = _share_values(extra or [], with_)
    try:
        plan = plan_unshare(root, target, values, directory=directory_for(root) if values else None)
    except ShareUsage as e:
        _share_refused(f"error: {e}", as_json, 2)
    except ShareRefused as e:
        _share_refused(str(e), as_json)
    client, link_problem = share_stops.connect(root)
    view, why = share_stops.fetch_view(client)
    wanted, notes = share_stops.unshare_stops(view, plan.target, plan.principals) if view is not None else ([], [])
    # The stop goes first (ruling 15): Agnes leaves the share out from the next read, before the vault changes.
    plan.agnes = share_stops.make_stops(client, view, why, wanted, notes, dry_run=dry_run, link_problem=link_problem)
    written = False
    if plan.changed and not dry_run:
        apply(root, plan)
        written = True
    if as_json:
        print(json.dumps({**plan.to_json(), "written": written}, indent=2))
        if plan.agnes.failed:
            raise typer.Exit(4)
        return
    for line in plan.lines():
        print(line)
    if not plan.changed and not plan.agnes.made and not plan.agnes.planned:
        print("Nothing to change: it wasn't shared that way.")
    if plan.agnes.failed:
        raise typer.Exit(4)


@kb_app.command("shares")
def kb_shares(
    reader: str | None = typer.Option(
        None,
        "--reader",
        help=_READER_HELP,
    ),
    as_json: bool = typer.Option(False, "--json", help="Print the shares as JSON."),
    repo: Path | None = typer.Option(None, "--repo", help="Vault repo (default: the git top level of the cwd)."),
) -> None:
    """List what this vault shares: per folder or note, the people, the notes shared and the notes left out.

    On a vault linked to Agnes it also shows the stops Agnes holds: open ones
    per share (who, since when), ones on shares the vault no longer lists, and
    the ones cleared in the last 30 days. Owner and autonomous readers only;
    another person's agent is denied (exit 3). Group names come from Agnes when
    the vault is linked.
    """
    args = _kb_args("shares", dict(locals()))

    def body(who: str, root: Path) -> object:
        from typing import Any

        from scout.kb import share_stops
        from scout.kb.privacy import FULL_READERS
        from scout.kb.read import Output
        from scout.kb.share import directory_for, group_names, list_shares, shares_lines

        if who not in FULL_READERS:
            return Output(data={"denied": True}, lines=["denied"], denied=1, code=3)
        enabled, rows = list_shares(root)
        names = group_names(directory_for(root), rows)
        client, link_problem = share_stops.connect(root)
        view, why = share_stops.fetch_view(client) if client is not None else (None, None)
        if link_problem and who == "owner":  # a sandboxed autonomous run can't read the token: stay quiet there
            why = link_problem
        stops = {row.glob: share_stops.row_stops(view, row.glob) for row in rows}
        shares = [{**row.to_json(), "stops": [s.to_json() for s in stops[row.glob]]} for row in rows]
        agnes: dict[str, Any] = view.to_json() if view is not None else {"available": False, "note": why}
        granted = {row.glob: row.principals for row in rows if row.source == "default"} if enabled else {}
        served = share_stops.still_served(view, granted) if view is not None else []
        if view is not None:
            agnes["still_served"] = [{"glob": g, "principals": p} for g, p in served]
        data = {"enabled": enabled, "group_names": names, "shares": shares, "agnes": agnes}
        lines = shares_lines(enabled, rows, names, stops)
        lines += share_stops.stop_lines(view, why, [row.glob for row in rows])
        lines += share_stops.still_served_lines(served, granted)
        return Output(data=data, lines=lines)

    _kb_run(args, reader, repo, as_json, body)


def _ceiling_line(entry: dict[str, object]) -> str:
    kinds = entry.get("kinds")
    only = f" (kind: {', '.join(str(k) for k in kinds)})" if isinstance(kinds, list) else ""
    return f"  {entry['glob']}{only} -> {entry['ceiling']}"


def _scope_text(scope: object) -> str:
    return ", ".join(str(p) for p in scope) if isinstance(scope, list) else str(scope)


# ----- the labelled read gate (#453, spec §6.1): logic in scout/kb/read.py ------

_KB_ROOT_HELP = "KB root (default: <vault>/knowledge-base); the vault is the one holding it."
_SCHEMA_HELP = "Ontology schema (default: the KB's ontology/schema.yaml, else the engine's)."


def _kb_args(command: str, params: dict[str, object]) -> list[str]:
    """A command's arguments for the audit log's hash: ``[command, "k=v", ...]``."""
    return [command, *(f"{k}={params[k]}" for k in sorted(params))]


def _kb_run(
    args: list[str],
    reader: str | None,
    repo: Path | None,
    as_json: bool,
    body: Callable[[str, Path], object],
    kb_root: str | None = None,
) -> None:
    """Resolve the reader and vault, run ``body(reader, vault)`` (a ``scout.kb.read.Output``), print.

    Every call writes its audit line (non-owner readers, spec §7.4), failures
    too: a refused ``--reader``, a missing argument, a bad pattern or path
    (exit 2), a KB error, each with ``paths_returned`` 0.
    """
    import json
    import os

    from scout.errors import KBError
    from scout.kb.read import Output, UsageError, audit, graph_vault
    from scout.kb.reader import delegate_id, resolve_reader

    # A delegate never gets --kb-root's vault lookup: it would stat a path of its choosing.
    delegate = delegate_id(resolve_reader(None, os.environ)) is not None
    vault = graph_vault(_kb_repo(repo), None if repo or delegate else kb_root)
    try:
        who = resolve_reader(reader, os.environ)
    except typer.BadParameter:
        audit(vault, resolve_reader(None, os.environ), args[0], args, Output(data=None))
        raise
    try:
        out = body(who, vault)
    except UsageError as e:
        audit(vault, who, args[0], args, Output(data=None))
        print(f"error: {e}", file=sys.stderr)
        raise typer.Exit(2) from e
    except (typer.BadParameter, KBError):
        audit(vault, who, args[0], args, Output(data=None))
        raise
    assert isinstance(out, Output)
    audit(vault, who, args[0], args, out)
    if as_json:
        print(json.dumps(out.data, indent=2, default=str))
    elif out.raw is not None:
        sys.stdout.write(out.raw)
    else:
        for line in out.lines:
            print(line)
    raise typer.Exit(out.code)


def _kb_graph_cmd(
    args: list[str],
    reader: str | None,
    repo: Path | None,
    kb_root: str | None,
    schema: str | None,
    as_json: bool,
    run: Callable[..., object],
    needs: tuple[tuple[str | None, str], ...] = (),
) -> None:
    """``_kb_run`` over the labelled graph: checks ``needs`` (value, name), then ``run(graph, vault)``."""

    def body(who: str, vault: Path) -> object:
        from scout.kb.read import load_graph

        for value, what in needs:
            _need(value, what)
        return run(load_graph(vault, who, kb_root, schema), vault)

    _kb_run(args, reader, repo, as_json, body, kb_root)


def _need(value: str | None, what: str) -> str:
    if not value:
        raise typer.BadParameter(f"{what} is required")
    return value


_R = typer.Option(None, "--reader", help=_READER_HELP)
_J = typer.Option(False, "--json", help="Print JSON; every result carries its label.")
_REPO = typer.Option(None, "--repo", help="Vault repo (default: the git top level of the cwd).")
_KBR = typer.Option(None, "--kb-root", help=_KB_ROOT_HELP)
_SCH = typer.Option(None, "--schema", help=_SCHEMA_HELP)


@kb_app.command("vault")
def kb_vault(
    as_json: bool = typer.Option(False, "--json", help="Print {vault, scoutctl, source, in_vault, reason}."),
) -> None:
    """Print the vault's absolute path, from any working directory. Exit 1 = no usable vault.

    From configuration only: SCOUT_DATA_DIR, else the engine pointer's vault, else ~/Scout (when there is no
    pointer); never the cwd, which only sets `in_vault`. `--json` adds the absolute `scoutctl` of this engine,
    which a skill calls with `--repo <vault>`; nothing in the vault is ever run. Reads no note.
    """
    import json
    import os

    from scout.kb.locate import engine_scoutctl, locate_vault

    try:
        cwd: Path | None = Path.cwd()
    except OSError:  # the working directory was deleted
        cwd = None
    loc = locate_vault(os.environ, cwd)
    if as_json:
        typer.echo(json.dumps(loc.as_json(engine_scoutctl())))
    else:
        if loc.vault is not None:
            typer.echo(str(loc.vault))
        if loc.reason:
            typer.echo(f"scoutctl kb vault: {loc.reason}", err=True)
    raise typer.Exit(0 if loc.vault is not None else 1)


@kb_app.command("files")
def kb_files(
    glob: list[str] | None = typer.Option(None, "--glob", help="Vault-relative glob; repeat to OR them."),
    reader: str | None = _R,
    as_json: bool = _J,
    repo: Path | None = _REPO,
) -> None:
    """List the files the reader may see, labelled (text: [private] prefix)."""
    args = _kb_args("files", dict(locals()))
    from scout.kb.read import cmd_files

    _kb_run(args, reader, repo, as_json, lambda who, vault: cmd_files(vault, who, glob or ()))


@kb_app.command("search")
def kb_search(
    pattern: str = typer.Argument(..., help="A regex (ripgrep syntax; Python's when rg is absent)."),
    glob: list[str] | None = typer.Option(None, "--glob", help="Vault-relative glob; repeat to OR them."),
    fixed: bool = typer.Option(False, "--fixed", help="Treat the pattern as a literal string."),
    ignore_case: bool = typer.Option(False, "-i", "--ignore-case", help="Case-insensitive."),
    reader: str | None = _R,
    as_json: bool = _J,
    repo: Path | None = _REPO,
) -> None:
    """Search the reader's visible files only. Each match carries its file's label. Exit 2 = bad pattern."""
    args = _kb_args("search", dict(locals()))
    from scout.kb.read import cmd_search

    _kb_run(
        args, reader, repo, as_json, lambda who, vault: cmd_search(vault, who, pattern, glob or (), fixed, ignore_case)
    )


@kb_app.command("read")
def kb_read(
    path: str = typer.Argument(..., help="A vault file (any spelling)."),
    lines: str | None = typer.Option(
        None,
        "--lines",
        help="Only these lines, 1-based and inclusive: A:B, A: (to the end), :B (the first B), -N: (the last N). "
        "In place of sed -n, head and tail.",
    ),
    reader: str | None = _R,
    as_json: bool = _J,
    repo: Path | None = _REPO,
) -> None:
    """Print a file with its label. Exit 3 = not visible to the reader; 2 = not a vault file or a bad --lines."""
    params = dict(locals())
    if lines is None:
        del params["lines"]  # a whole-file read's audit arguments stay as they were
    args = _kb_args("read", params)
    from scout.kb.read import cmd_read

    _kb_run(args, reader, repo, as_json, lambda who, vault: cmd_read(vault, who, path, lines))


@kb_app.command("stat")
def kb_stat(
    paths: list[str] | None = typer.Argument(None, help="Vault files (any spelling)."),
    glob: list[str] | None = typer.Option(None, "--glob", help="Vault-relative glob; repeat to OR them."),
    reader: str | None = _R,
    as_json: bool = _J,
    repo: Path | None = _REPO,
) -> None:
    """Each file's size, labelled: bytes, lines, words. In place of wc, ls -l and stat.

    Exit 3 = a named file is not visible to the reader (its row has no counts); 2 = not a vault file.
    """
    args = _kb_args("stat", dict(locals()))
    from scout.kb.read import cmd_stat

    _kb_run(args, reader, repo, as_json, lambda who, vault: cmd_stat(vault, who, paths or (), glob or ()))


@kb_app.command("delta")
def kb_delta(
    since: str = typer.Option(..., "--since", help="A commit (the commits after it) or a duration: 8h, 2d, 1w."),
    diff: bool = typer.Option(False, "--diff", help="Include each commit's unified diff (may hold private content)."),
    reader: str | None = _R,
    as_json: bool = _J,
    repo: Path | None = _REPO,
) -> None:
    """The vault's commits since a point, newest first, each changed path labelled. Exit 2 = bad --since.

    Paths the reader can't see are removed and commits left with none dropped:
    an agent reader gets nothing.
    """
    args = _kb_args("delta", dict(locals()))
    from scout.kb.delta import cmd_delta

    _kb_run(args, reader, repo, as_json, lambda who, vault: cmd_delta(vault, who, since, diff))


_SURFACE_HELP = "Where the text goes: a surface id from `kb policy` (slack, web_query ...). Unknown ids are shared."


@kb_app.command("check-outbound")
def kb_check_outbound(
    file: str = typer.Argument(..., help="The draft to check (e.g. .scout-cache/outbound-draft.md)."),
    surface: str = typer.Option(..., "--surface", help=_SURFACE_HELP),
    reader: str | None = _R,
    as_json: bool = typer.Option(False, "--json", help='Print {"ok": bool, "lines": [...]}.'),
    repo: Path | None = _REPO,
) -> None:
    """Check a draft bound for a surface for private content (spec §8.2 matchers).

    Exit 0 (`ok`): it may go. Exit 1: it names or links a private note; prints
    only the offending line numbers, never what matched. An owner-only surface
    always passes. Exit 2: the file can't be read, or an agent reader.
    """
    args = _kb_args("check-outbound", dict(locals()))
    from scout.kb.outbound import cmd_check_outbound

    _kb_run(args, reader, repo, as_json, lambda who, vault: cmd_check_outbound(vault, who, file, surface))


@kb_app.command("check-query")
def kb_check_query(
    text: str = typer.Argument(..., help="The query text, before it goes to a connector or web search."),
    surface: str = typer.Option(..., "--surface", help=_SURFACE_HELP),
    reader: str | None = _R,
    as_json: bool = typer.Option(False, "--json", help='Print {"ok": bool}.'),
    repo: Path | None = _REPO,
) -> None:
    """Check a connector or web query for private content. Exit 1 (`blocked`) = don't send it.

    Prints only `ok` or `blocked`. Exit 2: an agent reader.
    """
    args = _kb_args("check-query", dict(locals()))
    from scout.kb.outbound import cmd_check_query

    _kb_run(args, reader, repo, as_json, lambda who, vault: cmd_check_query(vault, who, text, surface))


@kb_app.command("resolve")
def kb_resolve(
    name: str = typer.Argument(..., help="A name, alias or slug."),
    reader: str | None = _R,
    as_json: bool = _J,
    repo: Path | None = _REPO,
    kb_root: str | None = _KBR,
    schema: str | None = _SCH,
) -> None:
    """Map a name, alias or slug to its canonical file among visible notes. Exit 1 = no match."""
    args = _kb_args("resolve", dict(locals()))
    from scout.kb.read import cmd_resolve

    _kb_graph_cmd(args, reader, repo, kb_root, schema, as_json, lambda g, vault: cmd_resolve(vault, g.reader, g, name))


@kb_app.command("query")
def kb_query(
    type_: str | None = typer.Option(None, "--type", help="Entity type."),
    status: str | None = typer.Option(None, "--status", help="Status."),
    exclude_status: str | None = typer.Option(None, "--exclude-status", help="Comma-separated statuses to exclude."),
    domain: str | None = typer.Option(None, "--domain", help="Domain."),
    deadline_before: str | None = typer.Option(None, "--deadline-before", help="ISO date: deadline <= this."),
    visibility: str | None = typer.Option(None, "--visibility", help="normal | private."),
    reader: str | None = _R,
    as_json: bool = _J,
    repo: Path | None = _REPO,
    kb_root: str | None = _KBR,
    schema: str | None = _SCH,
) -> None:
    """The ontology query (parser.py query), with the reader filter and labels."""
    args = _kb_args("query", dict(locals()))
    from scout.kb.read import cmd_query

    if visibility not in (None, "normal", "private"):
        visibility_error = typer.BadParameter("use normal or private", param_hint="--visibility")

        def refuse(_who: str, _vault: Path) -> object:
            raise visibility_error

        _kb_run(args, reader, repo, as_json, refuse, kb_root)
    filters = {
        "type": type_,
        "status": status,
        "domain": domain,
        "deadline_before": deadline_before,
        "exclude_status": exclude_status,
    }
    _kb_graph_cmd(args, reader, repo, kb_root, schema, as_json, lambda g, _v: cmd_query(g, filters, visibility))


@kb_app.command("related")
def kb_related(
    name: str | None = typer.Argument(None, help="Entity name."),
    name_opt: str | None = typer.Option(None, "--name", help="Entity name (parser.py spelling)."),
    reader: str | None = _R,
    as_json: bool = _J,
    repo: Path | None = _REPO,
    kb_root: str | None = _KBR,
    schema: str | None = _SCH,
) -> None:
    """Relationships from an entity, each target labelled."""
    args = _kb_args("related", dict(locals()))
    from scout.kb.read import cmd_related

    target = name_opt or name
    run = lambda g, _v: cmd_related(g, str(target))  # noqa: E731
    _kb_graph_cmd(args, reader, repo, kb_root, schema, as_json, run, ((target, "NAME"),))


@kb_app.command("traverse")
def kb_traverse(
    name: str | None = typer.Argument(None, help="Start entity."),
    name_opt: str | None = typer.Option(None, "--name", help="Start entity (parser.py spelling)."),
    hops: int | None = typer.Option(None, "--hops", help="Max hops (default 2)."),
    rels: str | None = typer.Option(
        None, "--rels", "--rel-types", help="Comma-separated relationship types to follow."
    ),
    reader: str | None = _R,
    as_json: bool = _J,
    repo: Path | None = _REPO,
    kb_root: str | None = _KBR,
    schema: str | None = _SCH,
) -> None:
    """Entities reachable from a start entity (BFS), each labelled."""
    args = _kb_args("traverse", dict(locals()))
    from scout.kb.read import cmd_traverse

    start = name_opt or name
    run = lambda g, _v: cmd_traverse(g, str(start), hops, rels)  # noqa: E731
    _kb_graph_cmd(args, reader, repo, kb_root, schema, as_json, run, ((start, "NAME"),))


@kb_app.command("path")
def kb_path(
    source: str | None = typer.Argument(None, help="Source entity."),
    target: str | None = typer.Argument(None, help="Target entity."),
    name_opt: str | None = typer.Option(None, "--name", help="Source entity (parser.py spelling)."),
    to: str | None = typer.Option(None, "--to", help="Target entity (parser.py spelling)."),
    hops: int | None = typer.Option(None, "--hops", help="Max hops (default 6)."),
    reader: str | None = _R,
    as_json: bool = _J,
    repo: Path | None = _REPO,
    kb_root: str | None = _KBR,
    schema: str | None = _SCH,
) -> None:
    """The shortest typed path between two entities, each node labelled."""
    args = _kb_args("path", dict(locals()))
    from scout.kb.read import cmd_path

    src, dst = name_opt or source, to or target
    run = lambda g, _v: cmd_path(g, str(src), str(dst), hops)  # noqa: E731
    _kb_graph_cmd(args, reader, repo, kb_root, schema, as_json, run, ((src, "SOURCE"), (dst, "TARGET")))


@kb_app.command("entity")
def kb_entity(
    name: str | None = typer.Argument(None, help="Entity name."),
    name_opt: str | None = typer.Option(None, "--name", help="Entity name (parser.py spelling)."),
    reader: str | None = _R,
    as_json: bool = _J,
    repo: Path | None = _REPO,
    kb_root: str | None = _KBR,
    schema: str | None = _SCH,
) -> None:
    """One entity's frontmatter as JSON, with its _label (parser.py entity)."""
    args = _kb_args("entity", dict(locals()))
    from scout.kb.read import cmd_entity

    target = name_opt or name
    run = lambda g, _v: cmd_entity(g, str(target))  # noqa: E731
    _kb_graph_cmd(args, reader, repo, kb_root, schema, as_json, run, ((target, "NAME"),))


@kb_app.command("name-lookup")
def kb_name_lookup(
    token: str | None = typer.Argument(None, help="Name token."),
    token_opt: str | None = typer.Option(None, "--token", help="Name token (parser.py spelling)."),
    threshold: float = typer.Option(0.7, "--threshold", help="Fuzzy cutoff (0.0-1.0)."),
    lookup_type: str = typer.Option(
        "person",
        "--lookup-type",
        help="The entity types to search: person (default), organization, any, or a comma-separated list.",
    ),
    reader: str | None = _R,
    as_json: bool = _J,
    repo: Path | None = _REPO,
    kb_root: str | None = _KBR,
    schema: str | None = _SCH,
) -> None:
    """Resolve a transcribed name against entities and aliases (parser.py name_lookup), labelled."""
    args = _kb_args("name-lookup", dict(locals()))
    from scout.kb.read import cmd_name_lookup

    tok = token_opt or token
    run = lambda g, _v: cmd_name_lookup(g, str(tok), threshold, lookup_type)  # noqa: E731
    _kb_graph_cmd(args, reader, repo, kb_root, schema, as_json, run, ((tok, "TOKEN"),))


@kb_app.command("export")
def kb_export(
    reader: str | None = _R,
    as_json: bool = _J,
    repo: Path | None = _REPO,
    kb_root: str | None = _KBR,
    schema: str | None = _SCH,
) -> None:
    """The whole graph as JSON, each entity with its _label (parser.py export)."""
    args = _kb_args("export", dict(locals()))
    from scout.kb.read import cmd_export

    _kb_graph_cmd(args, reader, repo, kb_root, schema, as_json, lambda g, _v: cmd_export(g))


@kb_app.command("stats")
def kb_stats(
    reader: str | None = _R,
    as_json: bool = _J,
    repo: Path | None = _REPO,
    kb_root: str | None = _KBR,
    schema: str | None = _SCH,
) -> None:
    """Entity and relationship counts (parser.py stats)."""
    args = _kb_args("stats", dict(locals()))
    from scout.kb.read import cmd_stats

    _kb_graph_cmd(args, reader, repo, kb_root, schema, as_json, lambda g, _v: cmd_stats(g))


@kb_app.command("validate")
def kb_validate(
    reader: str | None = _R,
    as_json: bool = _J,
    repo: Path | None = _REPO,
    kb_root: str | None = _KBR,
    schema: str | None = _SCH,
) -> None:
    """Check entities against the schema (parser.py validate), each error labelled."""
    args = _kb_args("validate", dict(locals()))
    from scout.kb.read import cmd_validate

    _kb_graph_cmd(args, reader, repo, kb_root, schema, as_json, lambda g, _v: cmd_validate(g))


@kb_app.command("structure")
def kb_structure(
    next_: bool = typer.Option(
        False, "--next", help="Print only the next rebuild target (a project name), or an empty line if all conform."
    ),
    reader: str | None = _R,
    as_json: bool = typer.Option(False, "--json", help="Print the table (or, with --next, the target) as JSON."),
    repo: Path | None = _REPO,
) -> None:
    """Check each project folder against the KB page standard. Report-only: exit 0, or 20 on a bad setting.

    Loose files, odd subfolders, front-page size, required sections, run
    narration, `as_of` and staleness; worst first. A private front page shows
    only paths, counts and verdicts. Settings: `kb_structure` in scout-config.yaml.
    """
    args = _kb_args("structure", dict(locals()))
    from scout.kb.structure import cmd_structure

    _kb_run(args, reader, repo, as_json, lambda who, vault: cmd_structure(vault, who, next_))


@kb_app.command("check-fact-check")
def kb_check_fact_check(
    date: str | None = typer.Option(
        None, "--date", help="The run's day, YYYY-MM-DD (default: today in the configured timezone)."
    ),
    data_dir: Path | None = typer.Option(None, "--data-dir", help="Vault root (default: $SCOUT_DATA_DIR or ~/Scout)."),
    context: Path | None = typer.Option(
        None, "--context", help="Pre-fetched context (default: <vault>/.scout-cache/session-context.json)."
    ),
    skipped: str | None = typer.Option(
        None, "--skipped", help="Why no live source could be reached this run; accepts the run as it stands."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit the result as JSON."),
) -> None:
    """Check a deep-work run re-verified the KB notes the runner pre-fetched for its fact-check pass.

    The runner lists the KB notes changed in the last 3 days that no run has
    re-verified since (`fact_check_targets`, most consequential first). At least
    2 of them (all, if fewer) must now carry `last_verified:` of today and a
    `verified_against:`. A target moved, archived or shared since counts as done;
    a shared note is never a target. `--skipped "<reason>"` accepts a run that
    could not reach a live source, and prints the line for its session-log row.
    Run it before committing a dreaming deep-work run. Exit codes: 0 = done,
    1 = not done, 2 = cannot check (or no reason given). Prints paths only,
    private ones marked `[private]`.
    """
    import datetime as dt
    import json as _json

    from scout.action_items.prefetched import PrefetchCheckError
    from scout.config import today as _today
    from scout.kb.fact_check import SKIP_MARK, check_fact_check

    name = "check-fact-check"
    if date is not None:
        try:
            dt.date.fromisoformat(date)
        except ValueError:
            typer.echo(f"{name}: --date: invalid date {date!r}")
            raise typer.Exit(2) from None
    day = date or _today(data_dir).isoformat()
    try:
        result = check_fact_check(data_dir=data_dir, today=day, context=context, skipped=skipped)
    except PrefetchCheckError as e:
        typer.echo(_json.dumps({"error": str(e)}) if as_json else f"{name}: cannot check — {e}")
        raise typer.Exit(2) from e

    def mark(visibility: str) -> str:
        return "[private] " if visibility != "normal" else ""

    if result.skipped is not None:
        import os

        from scout import paths as _paths
        from scout.kb.fact_check import record_skip

        try:
            record_skip(_paths.data_dir(data_dir), day, len(result.targets), os.environ.get("SCOUT_MODE"))
        except OSError:
            pass  # the skip stands; `scoutctl doctor` just won't count it

    if as_json:
        typer.echo(
            _json.dumps(
                {
                    "targets": [t.to_json() for t in result.targets],
                    "verified": result.verified,
                    "required": result.required,
                    "skipped": result.skipped is not None,
                    "settled": result.settled,
                    "passed": result.passed,
                }
            )
        )
    elif not result.targets:
        typer.echo(f"{name}: no KB note changed in the last days is waiting to be re-verified")
    elif result.passed and result.skipped is None:
        typer.echo(f"{name}: {len(result.verified)} of {count(len(result.targets), 'recent note')} re-verified today")
    elif result.passed:
        typer.echo(
            f"{name}: skipped. Put this in this run's session-log row and report: `{SKIP_MARK} {result.skipped}`"
        )
    else:
        typer.echo(
            f"{name}: {len(result.verified)} of the {count(result.required, 'required recent note')} "
            "re-verified today. "
            "Re-check the highest-stakes claims in these against a freshly read primary source, correct or mark "
            "what doesn't hold, and set `last_verified:` (today) and `verified_against:` in each one you check:"
        )
        done = set(result.verified)
        for t in result.targets:
            typer.echo(f"  {'✓' if t.path in done else '·'} {mark(t.visibility)}{t.path} (changed {t.changed})")
        typer.echo(
            'If no live source could be reached this run, run this again with `--skipped "<why>"`, '
            "and put the line it prints in this run's session-log row."
        )
    if not result.passed:
        raise typer.Exit(1)


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


@budget_app.command("record-run")
def budget_record_run_cmd(
    run_type: str = typer.Argument(..., help="The row's type: the slot (run-scout.sh), dreaming or research."),
    name: str = typer.Option(..., "--name", help="The --name the runner gave claude for this run."),
    started: int = typer.Option(..., "--started", help="When the run's claude call started, epoch seconds."),
    cap: float = typer.Option(0.0, "--cap", help="The run's --max-budget-usd."),
    exit_code: int = typer.Option(0, "--exit-code", help="The run's exit code."),
) -> None:
    """Append the runner's usage-tracker row with what the run cost by claude's own figure.

    Sums the cost-state of every transcript carrying --name since --started (retry
    attempts included). With none found the row records $0, as before, without
    cost_source.
    """
    from datetime import UTC, datetime

    from scout.scripts.run_cost import record

    _row, cost = record(
        run_type,
        name=name,
        started=datetime.fromtimestamp(started, UTC),
        cap=cap,
        exit_code=exit_code,
    )
    if cost.measured:
        typer.echo(
            f"run cost: ${cost.usd:.2f} by claude's figure "
            f"({cost.measured} of {count(cost.attempts, 'attempt')} measured)"
        )
    else:
        typer.echo(f"run cost: not measured ({count(cost.attempts, 'transcript')} named {name}) — recorded $0")


@budget_app.command("usage")
def budget_usage_cmd(
    as_json: bool = typer.Option(False, "--json", help="Print the summary the app's usage card reads, as JSON."),
) -> None:
    """What Scout's own scheduled runs used today and this week: tokens and cost.

    Counts finished scheduled runs only (the usage tracker's runner rows), never
    interactive Claude sessions. Tokens come from those runs' transcripts, each
    message counted once; cost is the budget gate's figure, with how it was found
    (cost_kind: reported, priced, unpriced, partial or none). Read-only.
    """
    from scout.scripts.usage_summary import describe, dumps, summarize

    summary = summarize()
    typer.echo(dumps(summary) if as_json else describe(summary), nl=not as_json)


@budget_app.command("record-attempt")
def budget_record_attempt_cmd(
    run_type: str = typer.Argument(..., help="The row's type: the slot (run-scout.sh), dreaming or research."),
    name: str = typer.Option(..., "--name", help="The --name the runner gave claude for this run."),
    started: int = typer.Option(..., "--started", help="When the run's claude call started, epoch seconds."),
    cap: float = typer.Option(0.0, "--cap", help="The run's --max-budget-usd, before any retry."),
    exit_code: int = typer.Option(1, "--exit-code", help="The failed attempt's exit code."),
) -> None:
    """Record a failed attempt's spend; print the retry's --max-budget-usd, or exit 3 for no retry.

    claude-with-retry.sh calls this after every failed attempt. Each attempt not
    yet recorded gets a source=attempt row, priced by claude's cost-state or else
    by its usage. stdout holds only the next attempt's cap (what the run has left;
    empty with no --cap). Exit 3 means no retry should follow: the run's attempts
    spent its cap, or the window reached the skip threshold. The rest goes to stderr.
    """
    from datetime import UTC, datetime

    from scout.scripts.run_cost import EXIT_NO_RETRY, record_attempt

    outcome = record_attempt(
        run_type,
        name=name,
        started=datetime.fromtimestamp(started, UTC),
        cap=cap,
        exit_code=exit_code,
    )
    attempt_usd = sum(a.usd for a in outcome.recorded)
    typer.echo(
        f"attempt cost: ${attempt_usd:.2f} ({count(len(outcome.recorded), 'attempt')} recorded); "
        f"this run so far ${outcome.cost.usd:.2f}; {outcome.reason}",
        err=True,
    )
    if not outcome.retry:
        # run-outcome.sh's BUDGET_RE and health.FAILURE_LINE_RES match this line.
        typer.echo(f"budget spent, no retry: {outcome.reason}", err=True)
        raise typer.Exit(EXIT_NO_RETRY)
    if outcome.next_cap is not None:
        typer.echo(f"{outcome.next_cap:.2f}")


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


progress_app = typer.Typer(help="Is the run holding the vault lock making progress? (scout.run_progress)")
app.add_typer(progress_app, name="progress")


@progress_app.command("watch")
def cli_progress_watch(
    runner_pid: int = typer.Option(..., "--runner-pid", help="The runner holding the session lock."),
    log: str = typer.Option(..., "--log", help="The run's log, named in the progress line."),
    vault: str = typer.Option(..., "--vault", help="The vault whose session lock the runner holds."),
) -> None:
    """Sample the runner's session and keep .scout-logs/.scout-session.lock.alive current while it holds the lock.

    Started by scripts/scout-lock.sh beside every scheduled run. The lock
    reaper, `scoutctl health` and the app read the line it writes; see
    scout/run_progress.py. Exits when the runner exits or loses the lock.
    """
    import signal

    from scout import run_progress

    def _stop(_signum: int, _frame: object) -> NoReturn:
        raise SystemExit(0)  # ends the sample's sleep at once; watch() removes its line on the way out

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    run_progress.watch(
        Path(vault),
        runner_pid=runner_pid,
        log=log,
        sample_s=run_progress.env_secs(run_progress.SAMPLE_SECS_ENV, run_progress.DEFAULT_SAMPLE_SECS),
        cpu_threshold_cs=run_progress.env_secs(
            run_progress.CPU_SECS_ENV, run_progress.DEFAULT_CPU_SECS, allow_zero=True
        )
        * 100,
    )


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

    @custom_app.command("allow-shared-notes")
    def cli_custom_allow_shared_notes(
        key: str,
        no_wait: bool = typer.Option(
            False, "--no-wait", help="Don't wait for a held session lock; fail fast with status busy."
        ),
    ) -> None:
        """List Agnes's three read-only shared-notes tools on a restrict_tools connector (not from a scheduled run)."""
        from scout import __version__
        from scout import paths as _paths
        from scout.scripts.custom_connector_ops import allow_shared_notes

        outcome = allow_shared_notes(
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
                "cooldown_minutes/budget_usd/tz/runtime/model/agent_runtime) "
                "instead of tab-separated lines. Plan 6's ScheduleEditService consumes "
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
                    # The app rewrites schedule.yaml from this list, so every Slot
                    # field must appear here or an in-app save drops it.
                    "agent_runtime": sched[key].agent_runtime,
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
            "runtime": slot.runtime.value,
            "model": slot.model,
            "agent_runtime": slot.agent_runtime,
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
        from scout import safe_write

        safe_write.atomic_write_bytes(target, source.read_bytes())
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
        runtime: str | None = typer.Option(
            None, "--runtime", help="The run's agent runtime, when not claude-code (spec 2026-11-13 §8.7)."
        ),
        as_json: bool = typer.Option(False, "--json", help='Emit {"model", "source"}.'),
    ) -> None:
        """Print the model for one run: SCOUT_MODEL, else the slot's model, else models.default, else opus.

        With --runtime other than claude-code: SCOUT_MODEL, the slot's model, agent_runtimes.<id>.model, then
        that runtime's default; models.default is Claude Code's only.
        """
        import json as _json

        from scout.agent_runtimes.resolve import resolve_model_for
        from scout.models import resolve_model

        try:
            if runtime and runtime != "claude-code":
                resolved = resolve_model_for(None, slot, runtime)
            else:
                resolved = resolve_model(slot_key=slot)
        except KeyError as e:
            typer.echo(f"error: {e.args[0]}", err=True)
            raise typer.Exit(1) from e
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


def _register_agent_runtime() -> None:
    """scoutctl agent-runtime {list,show,resolve,check,exec} — which agent CLI runs a session (spec 2026-11-13 §8).

    The runners call `resolve --slot "$MODE"`, then `check`, then launch through
    `exec` (by way of claude-with-retry.sh). Only `claude-code` is registered in
    Phase 1, and `exec` launches it exactly as the runners did.
    """
    import json as _json
    import os as _os

    agent_runtime_app = typer.Typer(help="Which agent CLI (Claude Code, …) scheduled runs use.")
    app.add_typer(agent_runtime_app, name="agent-runtime")

    def _adapter(runtime_id: str) -> AgentRuntime:
        from scout.agent_runtimes import registry

        try:
            return registry.get(runtime_id)
        except KeyError as e:
            typer.echo(f"error: {e.args[0]}", err=True)
            raise typer.Exit(2) from e

    @agent_runtime_app.command("list")
    def cli_agent_runtime_list(
        as_json: bool = typer.Option(False, "--json", help="Emit a JSON list."),
    ) -> None:
        """Every runtime this engine knows: id, name, provider, binary, version, provider consent."""
        from scout import paths as _paths
        from scout.agent_runtimes import consent, registry

        vault = _paths.data_dir()
        rows = []
        for rid in registry.ids():
            adapter = registry.get(rid)
            binary = adapter.locate(None)
            try:
                consented = consent.is_consented(vault, adapter.provider)
            except Exception:  # noqa: BLE001 - shown as unknown, never as consented
                consented = None
            rows.append(
                {
                    "id": rid,
                    "name": adapter.display_name,
                    "provider": adapter.provider,
                    "binary": str(binary) if binary else None,
                    "version": adapter.version(binary) if binary else None,
                    "consented": consented,
                    "default_model": adapter.default_model(),
                    "models": list(adapter.known_models()),
                }
            )
        if as_json:
            typer.echo(_json.dumps(rows))
            return
        for r in rows:
            typer.echo(
                f"{r['id']}\t{r['name']}\t{r['provider']}\t{r['version'] or 'not installed'}"
                f"\t{'consented' if r['consented'] else 'not consented'}"
            )

    @agent_runtime_app.command("show")
    def cli_agent_runtime_show(
        slot: str | None = typer.Option(None, "--slot", help="A slot key: show what its run would use."),
        as_json: bool = typer.Option(False, "--json", help="Emit JSON."),
    ) -> None:
        """The runtime and model a run of --slot would use (or the default), where each comes from, and its limits."""
        from scout import paths as _paths
        from scout.agent_runtimes import limits as _limits
        from scout.agent_runtimes.resolve import resolve_model_for, resolve_runtime

        vault = _paths.data_dir()
        try:
            rt = resolve_runtime(vault, slot)
            model = resolve_model_for(vault, slot, rt.runtime)
        except ConfigError as e:
            typer.echo(f"error: {e}", err=True)
            raise typer.Exit(1) from e
        lim = _limits.limits_for(vault, _adapter(rt.runtime), slot)
        payload = {
            "slot": slot,
            "runtime": rt.runtime,
            "runtime_source": rt.source,
            "model": model.model,
            "model_source": model.source,
            "run_timeout_s": lim.run_timeout_s,
            "timeout_source": lim.timeout_source,
            "max_tokens_per_day": lim.max_tokens_per_day,
            "ceiling_source": lim.ceiling_source,
            "ignored": list(lim.ignored),
        }
        if as_json:
            typer.echo(_json.dumps(payload))
            return
        for key, value in payload.items():
            typer.echo(f"{key}: {value}")

    @agent_runtime_app.command("set-limit")
    def cli_agent_runtime_set_limit(
        runtime: str = typer.Argument(..., help="The runtime id, e.g. claude-code."),
        run_timeout_minutes: str | None = typer.Option(
            None, "--run-timeout-minutes", help="Minutes of awake time per run, or off."
        ),
        max_tokens_per_day: str | None = typer.Option(
            None, "--max-tokens-per-day", help="Tokens per 24 hours, auto (the default formula), or off."
        ),
        clear: bool = typer.Option(False, "--clear", help="Drop this runtime's limits, back to the defaults."),
    ) -> None:
        """Set the owner's run limits for RUNTIME on this vault. The owner only, never a run (exit 2).

        Kept in ~/.local/state/scout/run-limits.json, which runs can't write. This is the only way to raise a
        limit or turn it off; scout-config.yaml's budget.per_runtime can only make one stricter.
        """
        from scout import paths as _paths
        from scout.agent_runtimes import limits as _limits
        from scout.scripts.privacy_migrate import headless_reason

        why = headless_reason(_os.environ)
        if why:
            typer.echo(f"refused: {why}. Only the owner may set run limits; nothing was changed.", err=True)
            raise typer.Exit(2)
        _adapter(runtime)
        if not clear and run_timeout_minutes is None and max_tokens_per_day is None:
            typer.echo("error: give --run-timeout-minutes, --max-tokens-per-day or --clear", err=True)
            raise typer.Exit(1)
        try:
            block = _limits.set_owner_limit(
                _paths.data_dir(),
                runtime,
                run_timeout_minutes=run_timeout_minutes,
                max_tokens_per_day=max_tokens_per_day,
                clear=clear,
            )
        except ValueError as e:
            typer.echo(f"error: {e}", err=True)
            raise typer.Exit(1) from e
        typer.echo(f"{runtime}: {_json.dumps(block, sort_keys=True) if block else 'defaults'}")

    @agent_runtime_app.command("resolve")
    def cli_agent_runtime_resolve(
        slot: str | None = typer.Option(None, "--slot", help="The run's slot key (the runners pass $MODE)."),
        as_json: bool = typer.Option(False, "--json", help='Emit {"runtime", "source"}.'),
    ) -> None:
        """Print the runtime for one run: SCOUT_AGENT_RUNTIME, the slot's, agent_runtimes.default, else claude-code.

        Exit 1 on a config error; the runners then fall back to claude-code, as model resolve falls back to opus.
        """
        from scout.agent_runtimes.resolve import resolve_runtime

        try:
            resolved = resolve_runtime(None, slot)
        except ConfigError as e:
            typer.echo(f"error: {e}", err=True)
            raise typer.Exit(1) from e
        if as_json:
            typer.echo(_json.dumps({"runtime": resolved.runtime, "source": resolved.source}))
        else:
            typer.echo(resolved.runtime)

    @agent_runtime_app.command("check")
    def cli_agent_runtime_check(
        slot: str | None = typer.Option(None, "--slot", help="The run's slot key (the runners pass $MODE)."),
        runtime: str | None = typer.Option(None, "--runtime", help="Check this runtime (default: resolve --slot)."),
        binary: str = typer.Option("", "--bin", help="The CLI the runner will start (default: the adapter finds it)."),
        interactive: bool = typer.Option(False, "--interactive", help="For a skill's preamble, not a scheduled run."),
        as_json: bool = typer.Option(False, "--json", help="Emit the report as JSON."),
    ) -> None:
        """Exit 0 when the runtime may run Scout on this vault; 3 when refused; 4 when over its daily token ceiling.

        The reason goes to stderr as `[scout:agent-runtime] <code>: <reason>`. A check that can't
        run refuses (check_failed). There is no fallback to another runtime (spec §8.3).
        """
        from scout import paths as _paths
        from scout.agent_runtimes import check as _check
        from scout.agent_runtimes.resolve import resolve_runtime

        vault = _paths.data_dir()
        try:
            rid = runtime or resolve_runtime(vault, slot).runtime
        except ConfigError as e:
            typer.echo(f"[scout:agent-runtime] check_failed: {e}", err=True)
            raise typer.Exit(_check.EXIT_REFUSE) from e
        # An unknown runtime is a refusal (3), never a usage error (2): the runners read 2 as "this engine
        # has no agent-runtime command" and launch claude directly, as before.
        from scout.agent_runtimes import registry as _registry

        try:
            adapter = _registry.get(rid)
        except KeyError as e:
            typer.echo(f"[scout:agent-runtime] check_failed: {e.args[0]}", err=True)
            raise typer.Exit(_check.EXIT_REFUSE) from e
        reader = _os.environ.get("SCOUT_READER") or ("owner" if interactive else "autonomous")
        report = _check.evaluate(
            vault,
            adapter,
            reader=reader,
            mode=_os.environ.get("SCOUT_MODE") or slot,
            interactive=interactive,
            binary=Path(binary) if binary else None,
        )
        if as_json:
            refusal = report.refusal
            typer.echo(
                _json.dumps(
                    {
                        "runtime": report.runtime,
                        "provider": report.provider,
                        "binary": report.binary,
                        "version": report.version,
                        "tier": report.tier,
                        "allowed": refusal is None,
                        "code": refusal.code if refusal else None,
                        "reason": refusal.reason if refusal else None,
                    }
                )
            )
        if report.refusal is not None:
            typer.echo(f"[scout:agent-runtime] {report.refusal.code}: {report.refusal.reason}", err=True)
            raise typer.Exit(report.refusal.exit_code)
        if not as_json:
            typer.echo(f"ok: {report.runtime} {report.version or ''} (Tier {report.tier or '-'})".rstrip())

    @agent_runtime_app.command("exec")
    def cli_agent_runtime_exec(
        runtime: str = typer.Option(..., "--runtime", help="The runtime id (the runners pass $SCOUT_AGENT_RUNTIME)."),
        binary: str = typer.Option("", "--bin", help="The CLI to start (default: the adapter finds it)."),
        model: str = typer.Option(..., "--model", help="The run's model."),
        budget_cap: str | None = typer.Option(None, "--max-budget-usd", help="The run's USD cap, passed as text."),
        name: str = typer.Option(..., "--name", help="The run's name."),
        prompt: str = typer.Option(..., "-p", help="The run's prompt."),
        print_argv: bool = typer.Option(False, "--print-argv", hidden=True, help="Print the argv as JSON; don't run."),
    ) -> None:
        """Replace this process with the runtime's CLI, launched for one scheduled run.

        The flag names are the retry wrapper's (it reads --name and rewrites --max-budget-usd).
        For claude-code the CLI's argv is the runners' launch line, word for word. With a run
        timeout (on by default only for runtimes that can't cap their own spend) this process
        stays as the CLI's parent and stops it at the deadline (exit 124).
        """
        from scout import paths as _paths
        from scout.agent_runtimes import exec as _exec
        from scout.agent_runtimes import limits as _limits

        adapter = _adapter(runtime)
        vault = _paths.data_dir()
        req = _exec.build_request(
            vault=vault,
            model=model,
            budget_cap=budget_cap,
            run_name=name,
            prompt=prompt,
            binary=Path(binary) if binary else None,
            env=_os.environ,
        )
        if print_argv:
            typer.echo(_json.dumps(list(adapter.headless_argv(req).argv)))
            return
        try:
            run_type = _os.environ.get("SCOUT_RUN_TYPE") or None
            lim = _limits.limits_for(vault, adapter, req.mode or None, run_type)
            timeout_s = lim.run_timeout_s
            if lim.ignored:
                # Into the run's log (the wrapper sends exec's stderr there), so a run that tried to change
                # its own limits leaves a trace (review of #653).
                typer.echo(
                    f"=== run limits: scout-config.yaml budget.per_runtime.{adapter.id} ignored: "
                    + "; ".join(lim.ignored)
                    + " ===",
                    err=True,
                )
        except Exception:  # noqa: BLE001 - limits never stop a start; a runtime without a cap keeps its default
            kind = _limits.kind_of(_os.environ.get("SCOUT_RUN_TYPE")) or _limits.kind_of(req.mode)
            timeout_s = None if adapter.caps_own_spend else _limits.default_timeout_minutes(kind) * 60
        try:
            code = _exec.run(adapter, req, timeout_s=timeout_s)
        except FileNotFoundError as e:
            typer.echo(f"[scout:agent-runtime] can't start {adapter.display_name}: {e}", err=True)
            raise typer.Exit(127) from e
        except PermissionError as e:
            typer.echo(f"[scout:agent-runtime] can't start {adapter.display_name}: {e}", err=True)
            raise typer.Exit(126) from e
        raise typer.Exit(int(code) if isinstance(code, int) else 0)


_register_agent_runtime()


def _register_privacy() -> None:
    """scoutctl privacy {providers,consent-provider,revoke-provider} — model-provider consent (spec 2026-11-13 §8.4)."""
    import json as _json
    import os as _os

    privacy_app = typer.Typer(help="Which model providers a run may send this vault's content to.")
    app.add_typer(privacy_app, name="privacy")

    @privacy_app.command("providers")
    def cli_privacy_providers(
        as_json: bool = typer.Option(False, "--json", help="Emit a JSON list of {provider, consented_at}."),
    ) -> None:
        """The providers runs on this vault may use: anthropic, plus each one the owner consented to."""
        from scout import paths as _paths
        from scout.agent_runtimes import consent

        try:
            rows = consent.consented(_paths.data_dir())
        except ValueError as e:
            typer.echo(f"error: {e}", err=True)
            raise typer.Exit(1) from e
        if as_json:
            typer.echo(_json.dumps([{"provider": r.provider, "consented_at": r.consented_at} for r in rows]))
            return
        for r in rows:
            typer.echo(f"{r.provider}\t{r.consented_at or 'default'}")

    def _owner_only(verb: str) -> None:
        from scout.scripts.privacy_migrate import headless_reason

        why = headless_reason(_os.environ)
        if why:
            typer.echo(f"refused: {why}. Only the owner may {verb} a provider; nothing was changed.", err=True)
            raise typer.Exit(2)

    @privacy_app.command("consent-provider")
    def cli_privacy_consent_provider(
        provider: str = typer.Argument(..., help="The provider id, e.g. openai, google, xai."),
    ) -> None:
        """Let runs on this vault send its content to PROVIDER. The owner only, never a run (exit 2).

        Recorded with the time in ~/.local/state/scout/provider-consent.json, which runs can't write.
        A `privacy.providers` list in scout-config.yaml, if you keep one, must name it too.
        Local-only paths never go to a provider other than Anthropic, consent or not.
        """
        from scout import paths as _paths
        from scout.agent_runtimes import consent

        _owner_only("consent to")
        vault = _paths.data_dir()
        try:
            at = consent.consent(vault, provider)
            overlay = consent.overlay_providers(vault)
        except ValueError as e:
            typer.echo(f"error: {e}", err=True)
            raise typer.Exit(1) from e
        name = provider.strip().lower()
        if not at:
            typer.echo(f"{name}: always allowed (Claude Code's provider)")
            return
        typer.echo(f"consented: {name} at {at}")
        if overlay is not None and name not in overlay:
            typer.echo(f"note: scout-config.yaml privacy.providers doesn't list {name}, so runs still may not use it.")

    @privacy_app.command("revoke-provider")
    def cli_privacy_revoke_provider(
        provider: str = typer.Argument(..., help="The provider id to revoke."),
    ) -> None:
        """Stop runs on this vault from using PROVIDER. The owner only (exit 2)."""
        from scout import paths as _paths
        from scout.agent_runtimes import consent

        _owner_only("revoke")
        try:
            removed = consent.revoke(_paths.data_dir(), provider)
        except ValueError as e:
            typer.echo(f"error: {e}", err=True)
            raise typer.Exit(1) from e
        typer.echo(f"{'revoked' if removed else 'not consented'}: {provider.strip().lower()}")


_register_privacy()


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
            typer.echo(f"triggers OK: {path} ({count(len(triggers), 'trigger')})")

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
            typer.echo(f"(no trigger fires in the last {count(days, 'day')})")
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
        import os as _os

        from scout.scripts.notify_telegram import cli_send

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

        if _os.environ.get("SCOUT_RUN_BROKER"):
            # A scheduled run with its send-only broker (Phase 1d): the broker sends it to the owner's chat, within
            # the owner's limits, and the run never reads the token. Same output and exit codes, plus 5 (limits).
            from scout.sandbox import broker

            fields = {
                "tier": tier,
                "parse_mode": parse_mode,
                "body": text,
                "allow_duplicate": allow_duplicate,
                "dry_run": dry_run,
            }
            raise typer.Exit(code=broker.client_telegram(fields))
        code = cli_send(
            tier=tier,
            text=text,
            parse_mode=parse_mode,
            dry_run=dry_run,
            allow_duplicate=allow_duplicate,
            echo_out=typer.echo,
            echo_err=lambda line: typer.echo(line, err=True),
        )
        if code:
            raise typer.Exit(code=code)


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

    @health_app.command("resolve-prompt")
    def cli_health_resolve_prompt(
        kind: str = typer.Argument(..., help="finding, connector or run."),
        key: str = typer.Argument(..., help="A finding id, a connector_key, or a run log's file name."),
        json_out: bool = typer.Option(False, "--json", help="Emit the versioned JSON document the app reads."),
    ) -> None:
        """Print an investigation prompt for one alert ("Resolve with Claude"). Read-only; exit 2 on a bad key."""
        import json as _json

        from scout import paths as _paths
        from scout import resolve_prompt as _rp

        def fail(error: str) -> NoReturn:
            typer.echo(f"resolve-prompt: {error}", err=True)
            if json_out:
                typer.echo(_json.dumps({"schema_version": _rp.SCHEMA_VERSION, "error": error}))
            raise typer.Exit(code=2)

        vault = _paths.data_dir()
        if not vault.is_dir():
            fail(f"vault directory missing: {vault}")
        try:
            prompt = _rp.build(vault, kind, key)
        except _rp.ResolveError as e:
            fail(str(e))
        typer.echo(_json.dumps(prompt.to_dict()) if json_out else prompt.prompt)


_register_health()


def _register_runs() -> None:
    """scoutctl runs [--json] [--since 7d] [--limit N] — the run ledger plus run health, for the app."""

    @app.command("runs")
    def cli_runs(
        json_out: bool = typer.Option(False, "--json", help="Emit the versioned JSON document the app reads."),
        since: str = typer.Option("7d", "--since", help="How far back: 7d, 48h, 90m, or an ISO time."),
        limit: int = typer.Option(200, "--limit", min=1, max=2000, help="At most this many runs, newest first."),
    ) -> None:
        """Recent scheduled runs (outcome, skip reason, sandbox, integrity) and run-health findings.

        Read-only. Exits 0 whatever the runs' health is (use `scoutctl health` for an exit code);
        2 on a bad --since or a missing vault.
        """
        import json as _json
        from datetime import UTC
        from datetime import datetime as _dt

        from scout import paths as _paths
        from scout import run_ledger as _rl

        now = _dt.now(UTC)
        try:
            cutoff = _rl.parse_since(since, now=now)
        except ValueError as e:
            typer.echo(f"scoutctl runs: {e}", err=True)
            raise typer.Exit(code=2) from None
        vault = _paths.data_dir()
        if not vault.is_dir():
            error = f"vault directory missing: {vault}"
            typer.echo(f"scoutctl runs: {error}", err=True)
            if json_out:
                typer.echo(_json.dumps({"schema_version": _rl.SCHEMA_VERSION, "error": error}))
            raise typer.Exit(code=2)
        doc = _rl.ledger_report(vault, now=now, since=cutoff, limit=limit)
        if json_out:
            typer.echo(_json.dumps(doc))
            return
        typer.echo(f"run health: {doc['health']['severity']}")
        for run in doc["runs"]:
            why = run["failure_class"] if run["outcome"] != "ok" else ""
            extra = " ".join(
                f"{k}={run[k]}" for k in ("sandbox", "integrity") if run[k] and run[k] not in ("enforced", "clean")
            )
            typer.echo(f"{run['finished_at']}  {run['slot']:<24} {run['outcome']:<16} {why} {extra}".rstrip())
        if doc["truncated"]:
            typer.echo(f"(showing the newest {limit}; use --limit for more)")


_register_runs()


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

    # Deprecated (spec 2026-11-13 §8.9): bootstrap never starts Claude, so the flag did nothing. Still
    # accepted, hidden, so a script that passes it keeps working.
    _SKIP_CLAUDE_HELP = "Deprecated and ignored: bootstrap never starts Claude."

    def _warn_skip_claude(skip_claude: bool) -> None:
        if skip_claude:
            typer.echo("note: --skip-claude is deprecated and does nothing (bootstrap never starts Claude)", err=True)

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

    def _config_from_existing_vault(vault: Path, *, skip_jobs: bool, managed_by: str):
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
        skip_claude: bool = typer.Option(False, "--skip-claude", hidden=True, help=_SKIP_CLAUDE_HELP),
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
        _warn_skip_claude(skip_claude)
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
        skip_claude: bool = typer.Option(False, "--skip-claude", hidden=True, help=_SKIP_CLAUDE_HELP),
        managed_by: str = typer.Option("preserve", "--managed-by", help=_MANAGED_BY_HELP),
        json_out: bool = typer.Option(False, "--json", help="Emit the BootstrapResult JSON (consumed by Scout.app)."),
    ) -> None:
        """Upgrade an existing vault against the current plugin templates."""
        _warn_skip_claude(skip_claude)
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
            cfg = _config_from_existing_vault(vault, skip_jobs=skip_jobs, managed_by=managed_by)
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

    @bootstrap_app.command("privacy-migrate")
    def cli_bootstrap_privacy_migrate(
        set_private: list[str] = typer.Option(
            [],
            "--set-private",
            help="Rewrite this note's non-standard visibility: value to `private` (only that line). Repeatable.",
        ),
        local_only: list[str] = typer.Option(
            [],
            "--local-only",
            help="Add this glob (e.g. 'trading/**') to scout-config.yaml → privacy.local_only_add. Repeatable.",
        ),
        accept: bool = typer.Option(
            False,
            "--accept",
            help="Record that the owner has answered every proposal (.scout-state/privacy-migrate.json).",
        ),
        json_out: bool = typer.Option(False, "--json", help="Print JSON."),
    ) -> None:
        """Per-note privacy migration (#453). Without options: a read-only report.

        Lists notes whose visibility: value isn't normal/private (they already
        count as private), lines of shared files that link or name a private
        note (file and line only), and trading/brokerage folders to keep on this
        computer, plus whether the owner's consent is recorded and current.
        --set-private and --local-only make the changes the owner agreed to;
        --accept (on its own) records that the owner has answered every
        proposal, which hosts such as Agnes check before linking the vault. All
        three refuse a scheduled or autonomous caller (exit 2). Exit 1 when a
        --set-private note was refused or the report for --accept failed. A
        reader other than owner or autonomous (SCOUT_READER=agent:…) gets exit 2
        and no output.
        """
        import json as _json
        import os as _os

        from scout import paths as _paths
        from scout.kb.privacy import FULL_READERS
        from scout.kb.reader import resolve_reader
        from scout.scripts import privacy_migrate as pm

        # The report names private notes' paths: an agent reader gets nothing at all.
        if resolve_reader(None, _os.environ) not in FULL_READERS:
            raise typer.Exit(2)

        vault = _paths.data_dir()
        if not set_private and not local_only and not accept:
            rep = pm.report(vault)
            accepted = pm.acceptance(vault, rep)
            if json_out:
                typer.echo(_json.dumps({**rep.to_json(), "accepted": accepted}, indent=2, sort_keys=True))
            else:
                for line in rep.lines():
                    typer.echo(line)
                typer.echo(pm.acceptance_line(accepted))
            raise typer.Exit(0)

        why = pm.headless_reason(_os.environ)
        if why:
            typer.echo(
                f"refused: {why}. Only the owner, in an interactive /scout-update, may change notes, "
                "the privacy overlay or the privacy consent; nothing was changed.",
                err=True,
            )
            raise typer.Exit(2)

        if accept:
            if set_private or local_only:
                typer.echo("refused: run --accept on its own, after the other changes; nothing was changed.", err=True)
                raise typer.Exit(2)
            try:
                marker = pm.accept(vault)
            except Exception as e:  # the report or the write failed: no marker
                typer.echo(
                    f"refused: the privacy migration report couldn't be computed or recorded "
                    f"({type(e).__name__}); nothing was recorded.",
                    err=True,
                )
                raise typer.Exit(1) from e
            if json_out:
                typer.echo(_json.dumps(marker, indent=2, sort_keys=True))
            else:
                typer.echo(f"accepted: privacy migration consent recorded at {marker['accepted_at']}")
            raise typer.Exit(0)

        outcomes: list[dict[str, str]] = []
        added: list[str] = []
        try:
            for path in set_private:
                outcome, reason = pm.set_private(vault, path)
                outcomes.append({"path": path, "outcome": outcome, "reason": reason})
            if local_only:
                added = pm.add_local_only(vault, local_only)
        except ValueError as e:
            typer.echo(f"refused: {e}", err=True)
            raise typer.Exit(2) from e
        if json_out:
            typer.echo(_json.dumps({"set_private": outcomes, "local_only_added": added}, indent=2, sort_keys=True))
        else:
            for o in outcomes:
                tail = f" — {o['reason']}" if o["reason"] else ""
                typer.echo(f"{o['outcome']}: {o['path']}{tail}", err=o["outcome"] == "refused")
            for g in local_only:
                typer.echo(f"{'added' if g.strip() in added else 'already local-only'}: {g.strip()}")
        raise typer.Exit(1 if any(o["outcome"] == "refused" for o in outcomes) else 0)

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
        for entry in getattr(report, "warning_triage", None) or []:  # stand-in reports in tests lack it
            if entry.reviewed_at:
                typer.echo(f"reviewed {entry.reviewed_at}: {entry.id}: {entry.summary}")
        raise typer.Exit(code=report.exit_code)

    @bootstrap_app.command("review-warning")
    def cli_bootstrap_review_warning(
        warning: str = typer.Argument(..., help="The warning's id, from `bootstrap doctor --json` warning_triage."),
        fingerprint: str | None = typer.Option(
            None, "--fingerprint", help="The fingerprint shown beside it: the review holds only while it matches."
        ),
        undo: bool = typer.Option(False, "--undo", help="Drop the review, so the warning shows again."),
        json_out: bool = typer.Option(False, "--json", help='Emit {"id", "fingerprint", "reviewed_at"}.'),
    ) -> None:
        """Mark a doctor warning reviewed, or (--undo) not. The owner only, never a run (exit 2).

        A reviewed warning leaves the doctor's warnings and severity until what it is about changes (its
        files, a high-risk file's content, or its text). Kept in ~/.local/state/scout/scout-reviewed-warnings.json,
        which runs can't write.
        """
        import json as _json
        import os

        from scout import doctor_triage
        from scout import paths as _paths
        from scout.scripts.privacy_migrate import headless_reason

        why = headless_reason(os.environ)
        if not why:
            # Whatever its environment says (a run can `env -u SCOUT_MODE`): is a scheduled run the caller?
            from scout.kb import live_run

            src = live_run.commit_source_now(_paths.data_dir())
            if src is not None and src.runs:
                why = f"this command runs inside a scheduled run ({src.mode}, by {src.by})"
        if why:
            typer.echo(f"refused: {why}. Only the owner may mark a warning reviewed; nothing was changed.", err=True)
            raise typer.Exit(2)
        if undo == (fingerprint is not None):
            typer.echo("error: give --fingerprint to mark it reviewed, or --undo", err=True)
            raise typer.Exit(1)
        try:
            record = doctor_triage.set_review(_paths.data_dir(), warning, None if undo else fingerprint)
        except ValueError as e:
            typer.echo(f"error: {e}", err=True)
            raise typer.Exit(1) from e
        except OSError as e:
            typer.echo(f"error: couldn't save the review: {e}", err=True)
            raise typer.Exit(1) from e
        if json_out:
            out = {"id": warning, "fingerprint": None, "reviewed_at": None, **(record or {})}
            typer.echo(_json.dumps(out))
        elif record:
            typer.echo(f"{warning}: reviewed {record['reviewed_at']}")
        else:
            typer.echo(f"{warning}: not reviewed")

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
        skip_claude: bool = typer.Option(False, "--skip-claude", hidden=True, help=_SKIP_CLAUDE_HELP),
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
        _warn_skip_claude(skip_claude)
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
                cfg = _config_from_existing_vault(vault, skip_jobs=skip_jobs, managed_by=managed_by)
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
        privacy = vault_drift.privacy_drift(vault)

        if json_out:
            files = [row.to_json() for row in rows]
            payload = {
                "schema_version": 1,
                "vault": str(vault),
                "files": files,
                "privacy": [finding.to_json() for finding in privacy],
            }
            typer.echo(_json.dumps(payload, indent=2))
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
                f"{count(produced, 'file')} in the patch — apply with `git apply` in a plugin checkout, review it, "
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

        if privacy:
            typer.echo(
                f"privacy drift — {count(len(privacy), 'vault file')} {agree(len(privacy), 'reads', 'read')} the vault "
                "around the privacy labels:"
            )
            for finding in privacy:
                typer.echo(f"  {finding.rule:<18} {finding.path}  {finding.message}")
        if not rows:
            typer.echo("no vault drift — every plugin-owned file matches what the plugin last wrote")
            return
        typer.echo(
            f"vault drift — {count(len(rows), 'plugin-owned file')} {agree(len(rows), 'differs', 'differ')} from what "
            "the plugin last wrote:"
        )
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
            build_rendered_sections,
            bundle_files,
            bundle_privacy_hits,
            combine_applied,
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

        # Plan every kind once. A hunk in a core fragment shows up in each brain
        # file that assembles it; the bundle applies it once.
        plans: list[tuple[str, list, list]] = []
        for k in kinds:
            snap, live = snapshot_dir / f"{k}.md", vault / f"{k}.md"
            if not snap.exists() or not live.exists():
                missing = "snapshot" if not snap.exists() else "live"
                typer.echo(f"{k}: skip — missing {missing} file", err=True)
                continue
            sections = build_rendered_sections(
                phases_root,
                k,
                vars_,
                cfg.enabled_connectors,
                custom=custom,
                inputs=cfg.connector_inputs,
                wrap_channels=cfg.wrap_channels,
            )
            results = plan_backport(snap.read_text(encoding="utf-8"), live.read_text(encoding="utf-8"), sections, vars_)
            plans.append((k, sections, results))
        all_applied = combine_applied([[r for r in results if r.status == "applied"] for _k, _s, results in plans])
        bundle = bundle_files(all_applied, [s for _k, sections, _r in plans for s in sections])

        # The plugin repo is public (#453, spec §8.4): a bundle naming a private
        # note is refused, dry run or not, before anything is reported or written.
        refused = bundle_privacy_hits(vault, bundle)
        if refused:
            typer.echo(
                "scoutctl phases backport: refused — the back-port names a private note of this vault, and the "
                "plugin repo is public. Remove it from the vault's brain file (or genericize it) and re-run:",
                err=True,
            )
            for pf, lines in refused:
                where = ("lines " if len(lines) > 1 else "line ") + ", ".join(str(n) for n in lines)
                typer.echo(f"  {pf}: {where} (as it would be written)", err=True)
            raise typer.Exit(code=1)

        for k, _sections, results in plans:
            applied = [r for r in results if r.status == "applied"]
            review = [r for r in results if r.status == "needs-review"]
            unmapped = [r for r in results if r.status == "unmapped"]

            typer.echo(
                f"\nphases backport — {k} ({count(len(results), 'hunk')}: "
                f"{len(applied)} applied · {len(review)} needs-review · {len(unmapped)} unmapped)"
            )
            for r in applied:
                typer.echo(f"  ✓ applied      {r.phase_file}  «{r.section_name}»  (+{count(len(r.added), 'line')})")
            for r in review:
                where = f"{r.phase_file} «{r.section_name}»" if r.phase_file else "—"
                extra = f" [risky: {', '.join(r.risky_hits)}]" if r.risky_hits else ""
                typer.echo(f"  ⚠ needs-review {where} — {r.reason}{extra}")
            for r in unmapped:
                snippet = (r.added[0][:60] + "…") if r.added else ""
                typer.echo(f"  ✗ unmapped     {r.reason}  «{snippet}»")

        total_applied = len(all_applied) if apply else 0
        if apply:
            for pf, (_old, text) in bundle.items():
                from scout import safe_write

                safe_write.atomic_write_text(pf, text)
                typer.echo(f"  → wrote {pf}")

        if apply:
            typer.echo(
                f"\nApplied {count(total_applied, 'hunk')} to phases/. "
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

    from scout import paths as _paths
    from scout._spawn import python_module_argv
    from scout.scripts.self_update_run import default_deps, run_lock, run_update, spawn_detached

    vault = _paths.data_dir()
    today = _dt.datetime.now().strftime("%Y-%m-%d")
    log_name = f"self-update-{today}.log"
    if detach:
        # The heartbeat runs this with the vault as cwd; -P keeps vault files off the child's sys.path.
        flags = [flag for flag, on in (("--scheduled", scheduled), ("--dry-run", dry_run), ("--force", force)) if on]
        argv = python_module_argv("scout", "self-update", "run", *flags)
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
        from scout import safe_write

        safe_write.atomic_write_text(marker, "")  # never through a symlink a run put in the cache
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
    no_jobs: bool = typer.Option(
        False,
        "--no-jobs",
        help="Register no launchd/cron jobs (passed to `bootstrap auto --no-jobs`): for sandboxes and containers.",
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
        no_jobs=no_jobs,
    )
    raise typer.Exit(code=run_setup(opts, prompter, default_deps(claude_bin)))


def _register_repo() -> None:
    repo_app = typer.Typer(
        help="Sync this vault with your Agnes Personal Knowledge Repository; local-only paths stay on this computer."
    )
    app.add_typer(repo_app, name="repo")

    def _fail(e: Exception, as_json: bool) -> NoReturn:
        import json as _json

        from scout.repo_sync import RepoSyncError

        assert isinstance(e, RepoSyncError)
        if as_json:
            typer.echo(_json.dumps({"ok": False, "code": e.code, "message": e.message}, indent=2))
        else:
            typer.echo(e.message, err=True)
        raise typer.Exit(2) from e

    def _done(result: object, as_json: bool) -> None:
        import json as _json

        from scout.repo_sync import Result

        assert isinstance(result, Result)
        if as_json:
            typer.echo(_json.dumps({"ok": True, **result.to_json()}, indent=2, sort_keys=True))
            return
        for note in result.notes:
            typer.echo(note)
        if result.replayed:
            typer.echo(f"replayed {count(len(result.replayed), 'commit')} from Agnes")
        if result.action in ("link", "sync"):
            typer.echo(
                f"pushed {count(len(result.pushed), 'commit')}"
                + (f", {result.skipped} unchanged after filtering" if result.skipped else "")
                + (f"; {count(result.stripped, 'local-only file')} kept on this computer" if result.stripped else "")
            )

    @repo_app.command("link")
    def cli_repo_link(
        agnes_url: str = typer.Option(..., "--agnes-url", help="Your Agnes instance, e.g. https://agnes.example.com"),
        token_file: Path | None = typer.Option(
            None, "--token-file", help="A file holding your Agnes personal access token. Default: ask for it."
        ),
        confirm_sync: list[str] = typer.Option(
            [],
            "--confirm-sync",
            help="Sync this trading-looking path anyway, exactly as link lists it (e.g. TRADING.md). Repeatable.",
        ),
        replace_remote: bool = typer.Option(
            False, "--replace-remote", help="The Agnes repository has history: put this vault's HEAD on top of it."
        ),
        as_json: bool = typer.Option(False, "--json", help="Print JSON."),
    ) -> None:
        """Link this vault to your Agnes Personal Knowledge Repository and push its first snapshot.

        Refuses a vault that hasn't been through /scout-update's privacy step, an
        unreadable scout-config.yaml, and trading-looking paths that aren't
        local-only or confirmed. Turns OTLP content export off for the vault.
        Run scoutctl repo preview first to see what would upload.
        """
        from scout import paths as _paths
        from scout.repo_sync import AgnesHost, RepoSyncError, link, save_token

        vault = _paths.data_dir()
        try:
            if token_file is not None:
                token = token_file.expanduser().read_text(encoding="utf-8").strip()
            else:
                token = typer.prompt("Agnes personal access token", hide_input=True).strip()
            if not token:
                raise RepoSyncError("token_missing", "refused: no token given")
            result = link(
                vault,
                AgnesHost(agnes_url, token),
                agnes_url=agnes_url,
                confirm_sync=confirm_sync,
                replace_remote=replace_remote,
            )
            save_token(vault, token)
        except RepoSyncError as e:
            _fail(e, as_json)
        except OSError as e:
            _fail(RepoSyncError("token_missing", f"refused: can't read {token_file}: {e}"), as_json)
        _done(result, as_json)

    @repo_app.command("sync")
    def cli_repo_sync(
        confirm_sync: list[str] = typer.Option(
            [],
            "--confirm-sync",
            help="Sync this path anyway, exactly as listed: a trading-looking one, or a file moved out of a path "
            "Agnes keeps local-only. Repeatable.",
        ),
        as_json: bool = typer.Option(False, "--json", help="Print JSON."),
    ) -> None:
        """Replay commits made in Agnes onto this vault, then push this vault's new commits (filtered).

        Refuses while scoutctl kb policy warns privacy-overlay-unreadable.
        Never pushes a local-only path: the union of this vault's effective
        policy and the list Agnes keeps for you. Refuses, before changing
        anything, a commit that moves a file out of a path on Agnes's list
        (not one from privacy.local_only_add) to one that uploads, until
        --confirm-sync names its new path for that sync.
        """
        from scout import paths as _paths
        from scout.repo_sync import AgnesHost, RepoSyncError, load_state, load_token, sync

        vault = _paths.data_dir()
        try:
            state = load_state(vault)
            if state is None:
                raise RepoSyncError("not_linked", "refused: this vault isn't linked; run scoutctl repo link")
            host = AgnesHost(state.agnes_url, load_token(vault))
            result = sync(vault, host, confirm_sync=confirm_sync, progress=lambda m: typer.echo(m, err=True))
        except RepoSyncError as e:
            _fail(e, as_json)
        _done(result, as_json)

    @repo_app.command("preview")
    def cli_repo_preview(
        with_paths: bool = typer.Option(False, "--paths", help="List every file that would upload."),
        depth: int = typer.Option(2, "--depth", help="How many folder levels the counts group by."),
        as_json: bool = typer.Option(False, "--json", help="Print JSON."),
    ) -> None:
        """Show what would upload to Agnes from HEAD. Read-only: no network, no token, nothing written.

        Counts by folder, and with --paths every file. Works before linking and
        after, and follows privacy.sync_only.
        """
        import json as _json

        from scout import paths as _paths
        from scout.repo_sync import RepoSyncError, preview

        try:
            result = preview(_paths.data_dir(), depth=depth)
        except RepoSyncError as e:
            _fail(e, as_json)
        if as_json:
            typer.echo(_json.dumps({"ok": True, **result.to_json(with_paths=with_paths)}, indent=2, sort_keys=True))
            return
        for line in result.lines():
            typer.echo(line)
        if with_paths:
            typer.echo("Files:")
            for rel in result.paths:
                typer.echo(f"  {rel}")

    @repo_app.command("sync-only")
    def cli_repo_sync_only(
        accept: bool = typer.Option(
            False, "--accept", help="Pin the list in scout-config.yaml now. With no list there, lift the allowlist."
        ),
        as_json: bool = typer.Option(False, "--json", help="Print JSON."),
    ) -> None:
        """Show this link's pinned privacy.sync_only next to scout-config.yaml's, or accept the config's.

        Editing the config can only narrow what uploads. Widening or removing the
        list takes --accept from you at the keyboard; a scheduled run is refused.
        """
        import json as _json

        from scout import paths as _paths
        from scout.kb.privacy import load_policy
        from scout.repo_sync import RepoSyncError, accept_sync_only, load_state

        vault = _paths.data_dir()
        try:
            if accept:
                _done(accept_sync_only(vault), as_json)
                return
            state = load_state(vault)
            if state is None:
                raise RepoSyncError("not_linked", "refused: this vault isn't linked; run scoutctl repo link")
        except RepoSyncError as e:
            _fail(e, as_json)
        config = load_policy(vault).sync_only
        if as_json:
            body = {"ok": True, "pinned": state.sync_only, "config": None if config is None else list(config)}
            typer.echo(_json.dumps(body, indent=2, sort_keys=True))
            return
        typer.echo("Pinned for this link: " + ("none" if state.sync_only is None else ", ".join(state.sync_only)))
        typer.echo("scout-config.yaml: " + ("no privacy.sync_only" if config is None else ", ".join(config)))

    @repo_app.command("unlink")
    def cli_repo_unlink(
        delete_remote: bool = typer.Option(
            False, "--delete-remote", help="Also delete the repository on Agnes (it can't be undone)."
        ),
        as_json: bool = typer.Option(False, "--json", help="Print JSON."),
    ) -> None:
        """Forget the link on this computer: the commit map, the stored token and the mirror refs."""
        from scout import paths as _paths
        from scout.repo_sync import AgnesHost, RepoSyncError, load_state, load_token, unlink

        vault = _paths.data_dir()
        try:
            host = None
            if delete_remote:
                state = load_state(vault)
                if state is None:
                    raise RepoSyncError("not_linked", "refused: this vault isn't linked")
                host = AgnesHost(state.agnes_url, load_token(vault))
            result = unlink(vault, host, delete_remote=delete_remote)
        except RepoSyncError as e:
            _fail(e, as_json)
        _done(result, as_json)

    lease_app = typer.Typer(
        help="The Repository Writer Lease: runs take it, so this computer and Agnes never write at once."
    )
    repo_app.add_typer(lease_app, name="lease")

    def _lease_host() -> tuple[Path, AgnesHost]:
        from scout import paths as _paths
        from scout.repo_sync import AgnesHost, RepoSyncError, load_state, load_token

        vault = _paths.data_dir()
        state = load_state(vault)
        if state is None:
            raise RepoSyncError("not_linked", "this vault isn't linked; no lease is needed")
        return vault, AgnesHost(state.agnes_url, load_token(vault))

    def _run_id(run_id: str) -> str:
        from scout.repo_lease import RUN_ID_RE

        if not RUN_ID_RE.fullmatch(run_id):
            raise typer.BadParameter("letters, digits and . _ : - only, at most 120", param_hint="--run-id")
        return run_id

    @lease_app.command("acquire")
    def cli_repo_lease_acquire(
        run_id: str = typer.Option(
            ..., "--run-id", help="This run's id, e.g. scout-morning-briefing-20261025T0800-4242."
        ),
        what: str = typer.Option("scheduled run", "--what", help="What the run is, for the holder label Agnes shows."),
        pid: int = typer.Option(0, "--pid", help="The runner's PID, watched by the keepalive. Default: the parent."),
        as_json: bool = typer.Option(False, "--json", help="Print JSON."),
    ) -> None:
        """Take the lease for one scheduled run; the token goes to the lease file, never to stdout.

        Exit 0: held (or the vault isn't linked: exit 5). Exit 3: Agnes or
        another process holds it, so skip the run (lease_held). Exit 4: Agnes
        can't grant it now, so skip the run (lease_unavailable).
        """
        import json as _json
        import os as _os

        from scout import repo_lease
        from scout.repo_sync import RepoSyncError

        run_id = _run_id(run_id)
        try:
            vault, host = _lease_host()
            lease, notes = repo_lease.acquire_for_run(
                vault,
                host,
                run_id=run_id,
                what=what,
                pid=pid or _os.getppid(),
                wait_s=repo_lease.env_secs(repo_lease.SYNC_WAIT_SECS_ENV, repo_lease.DEFAULT_SYNC_WAIT_SECS),
                poll_s=repo_lease.env_secs(repo_lease.POLL_SECS_ENV, repo_lease.DEFAULT_POLL_SECS),
            )
        except RepoSyncError as e:
            if e.code == "not_linked":
                code, outcome = 5, "not_linked"
            elif e.code == repo_lease.HELD_CODE:
                code, outcome = 3, "lease_held"
            else:
                code, outcome = 4, "lease_unavailable"
            if as_json:
                typer.echo(_json.dumps({"ok": False, "outcome": outcome, "code": e.code, "message": e.message}))
            else:
                holder = f" Held by {repo_lease.holder_text(e)}." if e.code == repo_lease.HELD_CODE else ""
                typer.echo(f"lease: {outcome} ({e.code}): {e.message.rstrip('.')}.{holder}", err=True)
            raise typer.Exit(code) from e
        if as_json:
            typer.echo(_json.dumps({"ok": True, "outcome": "held", "notes": notes, **lease.public()}, sort_keys=True))
            return
        for note in notes:
            typer.echo(f"lease: {note}")
        typer.echo(
            f"lease: held by {lease.holder_label} (generation {lease.generation}, until {lease.expires_at})"
            + (", taken over from an expired holder" if lease.took_over else "")
        )

    @lease_app.command("keepalive")
    def cli_repo_lease_keepalive(
        run_id: str = typer.Option(..., "--run-id", help="The run whose lease to renew."),
        parent_pid: int = typer.Option(..., "--parent-pid", help="The runner's PID; when it is gone, release."),
    ) -> None:
        """Renew a run's lease every five minutes while its runner lives; release it if the runner dies first."""
        import signal

        from scout import repo_lease
        from scout.repo_sync import RepoSyncError

        run_id = _run_id(run_id)
        stopping: list[int] = []
        signal.signal(signal.SIGTERM, lambda signum, _frame: stopping.append(signum))
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        try:
            vault, host = _lease_host()
        except RepoSyncError as e:
            typer.echo(f"lease keepalive: {e.message}", err=True)
            raise typer.Exit(4) from e
        why = repo_lease.keepalive(
            vault,
            host,
            run_id=run_id,
            parent_pid=parent_pid,
            renew_s=repo_lease.env_secs(repo_lease.RENEW_SECS_ENV, repo_lease.DEFAULT_RENEW_SECS),
            poll_s=repo_lease.env_secs(repo_lease.POLL_SECS_ENV, repo_lease.DEFAULT_POLL_SECS),
            log=lambda m: typer.echo(m, err=True),
            stop=lambda: bool(stopping),
        )
        typer.echo(f"lease keepalive: {why}", err=True)

    @lease_app.command("release")
    def cli_repo_lease_release(
        run_id: str = typer.Option(..., "--run-id", help="The run whose lease to give back."),
    ) -> None:
        """Give back a run's lease. Exit 0 also when there was none; 4 when Agnes can't be reached (it then expires)."""
        from scout import repo_lease
        from scout.repo_sync import RepoSyncError

        run_id = _run_id(run_id)
        try:
            vault, host = _lease_host()
            if repo_lease.release(vault, host, run_id):
                typer.echo(f"lease: released run {run_id}'s lease")
        except RepoSyncError as e:
            if e.code == "not_linked":
                return
            typer.echo(f"lease: release failed ({e.code}): {e.message}; Agnes lets it expire", err=True)
            raise typer.Exit(4) from e

    @lease_app.command("status")
    def cli_repo_lease_status(as_json: bool = typer.Option(False, "--json", help="Print JSON.")) -> None:
        """Who holds the lease, on Agnes and in this computer's lease file. Never the token."""
        import json as _json

        from scout import repo_lease
        from scout.repo_sync import RepoSyncError

        try:
            vault, host = _lease_host()
            local = repo_lease.load(vault)
            remote = host.lease_status()
        except RepoSyncError as e:
            _fail(e, as_json)
        doc = {"ok": True, "agnes": remote, "local": local.public() if local else None}
        if as_json:
            typer.echo(_json.dumps(doc, indent=2, sort_keys=True, default=str))
            return
        if remote.get("held") and not remote.get("expired"):
            typer.echo(
                f"Agnes: held by {remote.get('holder_kind')} ({remote.get('holder_label') or '-'}), "
                f"generation {remote.get('generation')}, until {remote.get('expires_at')}"
            )
        else:
            typer.echo("Agnes: free" + (" (the last lease expired)" if remote.get("expired") else ""))
        typer.echo(f"Pushes need the lease: {'yes' if remote.get('enforced') else 'not yet'}")
        if local:
            typer.echo(
                f"This computer: {local.owner} {local.run_id} (pid {local.pid}){' — LOST' if local.lost else ''}"
            )


_register_repo()


def _register_delegate() -> None:
    delegate_app = typer.Typer(
        help="Mirrors for your own agents off this computer (#550): each delegate:<id> gets a filtered copy of "
        "the vault in its own private repository."
    )
    app.add_typer(delegate_app, name="delegate")

    def _fail(e: Exception, as_json: bool) -> NoReturn:
        import json as _json

        from scout.repo_sync import RepoSyncError

        assert isinstance(e, RepoSyncError)
        if as_json:
            typer.echo(_json.dumps({"ok": False, "code": e.code, "message": e.message}, indent=2))
        else:
            typer.echo(e.message, err=True)
        raise typer.Exit(2) from e

    def _say(result: object, as_json: bool) -> None:
        import json as _json

        from scout.delegate_mirror import Result

        assert isinstance(result, Result)
        if as_json:
            typer.echo(_json.dumps({"ok": True, **result.to_json()}, indent=2, sort_keys=True))
            return
        r = result
        if r.action in ("link", "sync", "preview"):
            if r.unchanged:
                typer.echo(f"{r.delegate}: up to date")
            else:
                verb = "would hold" if r.action == "preview" else "pushed a mirror commit with"
                typer.echo(
                    f"{r.delegate}: {verb} {count(r.files, 'file')}; {r.withheld} withheld"
                    + (f", {r.left_out} left out after redaction" if r.left_out else "")
                    + (
                        f"; {count(r.redacted_lines, 'line')} removed from {count(r.redacted_files, 'file')}"
                        if r.redacted_files
                        else ""
                    )
                )
        for note in r.notes:
            typer.echo(note)

    @delegate_app.command("link")
    def cli_delegate_link(
        delegate: str = typer.Argument(..., help="The delegate id, as listed under privacy.delegates."),
        remote: str = typer.Option(
            "", "--remote", help="Its private GitHub repository (https://github.com/<owner>/<repo>) or a local path."
        ),
        create_repo: str | None = typer.Option(
            None, "--create-repo", help="Create this private GitHub repository (owner/name) with gh and use it."
        ),
        confirm_account_unshared: bool = typer.Option(
            False,
            "--confirm-account-unshared",
            help="You confirm that only you use the agent's account: no team bot, shared handle or shared channel.",
        ),
        replace_remote: bool = typer.Option(
            False, "--replace-remote", help="The remote has history: start it over with one mirror commit."
        ),
        as_json: bool = typer.Option(False, "--json", help="Print JSON."),
    ) -> None:
        """Link a delegate to its own private repository and push its first mirror.

        The mirror holds what scoutctl kb shows that delegate, with the lines that
        name or link a note it can't see removed. Run scoutctl delegate preview first.
        """
        from scout import paths as _paths
        from scout.delegate_mirror import link
        from scout.repo_sync import RepoSyncError

        try:
            result = link(
                _paths.data_dir(),
                delegate,
                remote,
                account_unshared=confirm_account_unshared,
                replace_remote=replace_remote,
                create_repo=create_repo,
            )
        except RepoSyncError as e:
            _fail(e, as_json)
        _say(result, as_json)

    @delegate_app.command("sync")
    def cli_delegate_sync(
        delegate: str | None = typer.Argument(None, help="One delegate id. Omit with --all."),
        all_: bool = typer.Option(False, "--all", help="Every linked delegate; one failing doesn't stop the rest."),
        if_moved: bool = typer.Option(
            False,
            "--if-moved",
            help="With --all: only delegates whose mirror may be behind (HEAD moved, or the last sync failed).",
        ),
        as_json: bool = typer.Option(False, "--json", help="Print JSON."),
    ) -> None:
        """Push a new mirror commit when the vault's HEAD changed what the delegate may see.

        Scheduled runs call `sync --all` when they finish, and the schedule tick
        calls `sync --all --if-moved` at most every 15 minutes. A failure is
        recorded for run health and doctor, never failing the run.
        """
        import json as _json

        from scout import paths as _paths
        from scout.delegate_mirror import sync, sync_all
        from scout.repo_sync import RepoSyncError

        if (delegate is None) == (not all_) or (if_moved and not all_):
            typer.echo(
                "scoutctl delegate sync: name one delegate, or pass --all (and --if-moved only with --all)", err=True
            )
            raise typer.Exit(2)
        if delegate is not None:
            try:
                result = sync(_paths.data_dir(), delegate)
            except RepoSyncError as e:
                _fail(e, as_json)
            _say(result, as_json)
            return
        try:
            done, failed = sync_all(_paths.data_dir(), only_moved=if_moved)
        except RepoSyncError as e:
            _fail(e, as_json)
        if as_json:
            doc = {
                "ok": not failed,
                "synced": [r.to_json() for r in done],
                "failed": {k: {"code": err.code, "message": err.message} for k, err in failed.items()},
            }
            typer.echo(_json.dumps(doc, indent=2, sort_keys=True))
        else:
            for r in done:
                _say(r, False)
            for k, err in failed.items():
                typer.echo(f"{k}: {err.message}", err=True)
        if failed:
            raise typer.Exit(2)

    @delegate_app.command("preview")
    def cli_delegate_preview(
        delegate: str = typer.Argument(..., help="The delegate id, as listed under privacy.delegates."),
        as_json: bool = typer.Option(False, "--json", help="Print JSON."),
    ) -> None:
        """Show what a sync of HEAD would put in the delegate's mirror. Writes nothing; no network."""
        from scout import paths as _paths
        from scout.delegate_mirror import preview
        from scout.repo_sync import RepoSyncError

        try:
            result = preview(_paths.data_dir(), delegate)
        except RepoSyncError as e:
            _fail(e, as_json)
        _say(result, as_json)

    @delegate_app.command("init")
    def cli_delegate_init(
        clone: Path | None = typer.Option(  # noqa: B008
            None, "--clone", help="The mirror clone (default: $SCOUT_DATA_DIR, else this folder or one above it)."
        ),
        inbox: Path | None = typer.Option(  # noqa: B008
            None, "--inbox", help="The agent's clone of its inbox repository, where scoutctl inbox propose writes."
        ),
        print_env: bool = typer.Option(
            False,
            "--print-env",
            help='Print only shell exports, for: e=$(scoutctl delegate init --print-env) && eval "$e".',
        ),
        as_json: bool = typer.Option(False, "--json", help="Print JSON."),
    ) -> None:
        """Set up the agent's side of a delegate mirror: run in its clone, on the agent's platform.

        Checks the clone, its config and this engine, reads once as the delegate,
        and writes the environment Scout needs here inside the clone's .git (never
        the tree). Safe to run again; the mirror's AGENTS.md says when.
        """
        import json as _json

        from scout.delegate_agent import init
        from scout.repo_sync import RepoSyncError

        try:
            result = init(clone, inbox=inbox)
        except Exception as e:  # every failure exits 2, so `e=$(... --print-env) && eval "$e"` loads nothing
            failure = e if isinstance(e, RepoSyncError) else RepoSyncError("init_failed", f"refused: {e}")
            if print_env:
                typer.echo(failure.message, err=True)
                raise typer.Exit(2) from e
            _fail(failure, as_json)
        if print_env:
            for note in result.notes:
                typer.echo(f"scoutctl delegate init: {note}", err=True)
            typer.echo(result.exports(), nl=False)
            return
        if as_json:
            typer.echo(_json.dumps({"ok": True, **result.to_json()}, indent=2, sort_keys=True))
            return
        typer.echo(f"{result.clone} is {result.delegate}'s mirror; {count(result.files, 'file')} visible to it")
        for note in result.notes:
            typer.echo(f"warning: {note}")
        if result.env_file:
            typer.echo(f"wrote {result.env_file}")
        typer.echo('load it in each task with: e=$(scoutctl delegate init --print-env) && eval "$e"')

    @delegate_app.command("status")
    def cli_delegate_status(
        as_json: bool = typer.Option(
            False,
            "--json",
            help="Print the versioned status document the app reads (scout/delegate_status.py). Read-only; "
            "a state it can't trust shows in it rather than failing.",
        ),
    ) -> None:
        """List the linked delegates: remote, last sync and the vault commit it mirrors."""
        import json as _json
        import os as _os

        from scout import paths as _paths
        from scout.delegate_mirror import load_links
        from scout.repo_sync import RepoSyncError

        if as_json:
            from scout.delegate_status import report

            typer.echo(_json.dumps(report(_paths.data_dir(), env=_os.environ), indent=2, sort_keys=True))
            return
        try:
            links = load_links(_paths.data_dir())
        except RepoSyncError as e:
            _fail(e, as_json)
        from scout.delegate_status import remote_text

        if not links:
            typer.echo("no delegate mirrors on this computer")
        for k, v in sorted(links.items()):
            key = "deploy key" if v.deploy_key_id is not None else "no deploy key"
            if v.stale_key_ids:
                key += f", {count(len(v.stale_key_ids), 'old key')} still live (scoutctl delegate key {k} retries)"
            synced = f"last sync {v.last_sync or 'never'}; vault {(v.last_vault or '-')[:12]}"
            typer.echo(f"{k}: {remote_text(v.remote, _os.environ)}; {key}; {synced}")

    @delegate_app.command("key")
    def cli_delegate_key(
        delegate: str = typer.Argument(..., help="The delegate id."),
        rotate: bool = typer.Option(False, "--rotate", help="Replace the agent's deploy key with a new one."),
        revoke: bool = typer.Option(False, "--revoke", help="Delete the agent's deploy key: it can't fetch any more."),
        inbox: bool = typer.Option(
            False, "--inbox", help="The key the agent pushes proposals to its inbox with (writable, the inbox only)."
        ),
        as_json: bool = typer.Option(False, "--json", help="Print JSON."),
    ) -> None:
        """Give the agent a read-only deploy key on its mirror repository (or rotate or revoke it).

        With --inbox, the key is for its inbox repository instead, and can push there.

        The private key is kept in ~/.scout-secrets/delegate-mirrors/ for you to
        put in the agent's secret store; the command prints where, and how the
        agent clones with it.
        """
        from scout import paths as _paths
        from scout.delegate_mirror import deploy_key
        from scout.repo_sync import RepoSyncError

        if rotate and revoke:
            typer.echo("scoutctl delegate key: pass --rotate or --revoke, not both", err=True)
            raise typer.Exit(2)
        if inbox:
            from scout import delegate_inbox

            try:
                inbox_result = delegate_inbox.deploy_key(_paths.data_dir(), delegate, rotate=rotate, revoke=revoke)
            except RepoSyncError as e:
                _fail(e, as_json)
            _say_inbox(inbox_result, as_json)
            return
        try:
            result = deploy_key(_paths.data_dir(), delegate, rotate=rotate, revoke=revoke)
        except RepoSyncError as e:
            _fail(e, as_json)
        _say(result, as_json)

    @delegate_app.command("push-token")
    def cli_delegate_push_token(
        token_file: Path | None = typer.Option(
            None, "--token-file", help="A file holding the token. Default: ask for it."
        ),
    ) -> None:
        """Store a GitHub token this computer pushes the mirrors with, instead of git's own login.

        Make it fine-grained: contents read and write on the mirror repositories
        only. It is kept in ~/.scout-secrets/delegate-mirrors/, never in the vault.
        """
        from scout import paths as _paths
        from scout.delegate_mirror import push_token_path, save_push_token
        from scout.repo_sync import RepoSyncError

        vault = _paths.data_dir()
        try:
            if token_file is not None:
                token = token_file.expanduser().read_text(encoding="utf-8")
            else:
                token = typer.prompt("GitHub token for the delegate mirrors", hide_input=True)
            save_push_token(vault, token)
        except RepoSyncError as e:
            _fail(e, False)
        except OSError as e:
            _fail(RepoSyncError("token_missing", f"refused: can't read {token_file}: {e}"), False)
        typer.echo(f"stored at {push_token_path(vault)}")

    @delegate_app.command("trust-state")
    def cli_delegate_trust_state(
        confirm: list[str] = typer.Option(  # noqa: B008
            [], "--confirm", help="A delegate id whose link you checked (once per link), instead of the prompt."
        ),
        as_json: bool = typer.Option(False, "--json", help="Print JSON."),
    ) -> None:
        """Check every link in a state file whose signature doesn't match, and sign it again (the owner only).

        Each delegate and its remote are shown and must be confirmed: at a
        terminal by typing the delegate id, otherwise with --confirm <id> per link.
        """
        import sys as _sys

        from scout import paths as _paths
        from scout.delegate_mirror import trust_state
        from scout.repo_sync import RepoSyncError

        confirmed = set(confirm)

        def ask(delegate: str, remote: str) -> bool:
            if confirmed:
                return delegate in confirmed
            if not _sys.stdin.isatty():
                return False
            typer.echo(f"{delegate} -> {remote}")
            return typer.prompt(f"Type {delegate} to trust this link", default="", show_default=False) == delegate

        try:
            result = trust_state(_paths.data_dir(), confirm=ask)
        except RepoSyncError as e:
            _fail(e, as_json)
        _say(result, as_json)

    @delegate_app.command("inbox")
    def cli_delegate_inbox(
        delegate: str = typer.Argument(..., help="The delegate id."),
        remote: str = typer.Option(
            "", "--remote", help="Its private inbox repository (https://github.com/<owner>/<repo>) or a local path."
        ),
        create_repo: str | None = typer.Option(
            None, "--create-repo", help="Create this private GitHub repository (owner/name), Actions off, and use it."
        ),
        reset: bool = typer.Option(
            False, "--reset", help="After inbox_rewritten: take the inbox's main as it is now. Pushes nothing."
        ),
        unlink: bool = typer.Option(False, "--unlink", help="Forget the inbox and revoke its key. The repo stays."),
        confirm: bool = typer.Option(False, "--confirm", help="With --reset: don't ask."),
        as_json: bool = typer.Option(False, "--json", help="Print JSON."),
    ) -> None:
        """Link a delegate to its inbox: a private repository its agent pushes proposals to (#550 V4).

        The repository is bound by GitHub's own ids and checked before every pull:
        private, no collaborators, no deploy key but the agent's, not a fork, GitHub Actions and Pages off.
        """
        import sys as _sys

        from scout import delegate_inbox
        from scout import paths as _paths
        from scout.repo_sync import RepoSyncError

        if sum(map(bool, (remote or create_repo, reset, unlink))) != 1:
            typer.echo("scoutctl delegate inbox: pass --remote or --create-repo, or --reset, or --unlink", err=True)
            raise typer.Exit(2)

        def ask(name: str, main: str) -> bool:
            if confirm:
                return True
            if not _sys.stdin.isatty():
                return False
            typer.echo(f"{name}'s inbox main is now {main}")
            return typer.prompt(f"Type {name} to build on it", default="", show_default=False) == name

        vault = _paths.data_dir()
        try:
            if reset:
                result = delegate_inbox.reset(vault, delegate, confirm=ask)
            elif unlink:
                result = delegate_inbox.unlink(vault, delegate)
            else:
                result = delegate_inbox.link(vault, delegate, remote, create_repo=create_repo)
        except RepoSyncError as e:
            _fail(e, as_json)
        _say_inbox(result, as_json)

    @delegate_app.command("unlink")
    def cli_delegate_unlink(
        delegate: str = typer.Argument(..., help="The delegate id."),
        as_json: bool = typer.Option(False, "--json", help="Print JSON."),
    ) -> None:
        """Forget a delegate's mirror on this computer and revoke its deploy key. The repository stays."""
        from scout import paths as _paths
        from scout.delegate_mirror import unlink
        from scout.repo_sync import RepoSyncError

        try:
            result = unlink(_paths.data_dir(), delegate)
        except RepoSyncError as e:
            _fail(e, as_json)
        _say(result, as_json)


_register_delegate()


def _say_inbox(result: object, as_json: bool) -> None:
    import json as _json

    from scout.delegate_inbox import Result

    assert isinstance(result, Result)
    if as_json:
        typer.echo(_json.dumps({"ok": True, **result.to_json()}, indent=2, sort_keys=True))
        return
    r = result
    if r.action == "inbox-pull":
        refused = sum(r.refused.values())
        line = f"{r.delegate}: took {len(r.taken)}"
        line += (
            f", refused {refused} ({', '.join(f'{k} {v}' for k, v in sorted(r.refused.items()))})" if refused else ""
        )
        line += f", {r.already} taken before" if r.already else ""
        typer.echo(line)
    for note in r.notes:
        typer.echo(note)


def _register_inbox() -> None:
    inbox_app = typer.Typer(
        help="Delegates' inboxes (#550 V4): an agent proposes a change, the laptop pulls it. The agent's side is "
        "inbox propose; the owner's is inbox pull."
    )
    app.add_typer(inbox_app, name="inbox")
    propose_app = typer.Typer(help="Propose a change from a delegate's agent, in its inbox clone.")
    inbox_app.add_typer(propose_app, name="propose")

    def _fail(e: Exception, as_json: bool) -> NoReturn:
        import json as _json

        from scout.repo_sync import RepoSyncError

        assert isinstance(e, RepoSyncError)
        if as_json:
            typer.echo(_json.dumps({"ok": False, "code": e.code, "message": e.message}, indent=2))
        else:
            typer.echo(e.message, err=True)
        raise typer.Exit(2) from e

    def _propose(kind: str, body: dict[str, object], inbox: Path | None, push: bool, as_json: bool) -> None:
        import json as _json

        from scout.delegate_inbox import propose
        from scout.repo_sync import RepoSyncError

        try:
            out = propose(kind, body, inbox=inbox, push=push)
        except RepoSyncError as e:
            _fail(e, as_json)
        if as_json:
            typer.echo(_json.dumps({"ok": True, **out.to_json()}, indent=2, sort_keys=True))
            return
        typer.echo(f"proposed {out.id} ({out.path}){', pushed' if out.pushed else ''}")
        for note in out.notes:
            typer.echo(note)

    inbox_opt = typer.Option(None, "--inbox", help="The inbox clone (default: $SCOUT_INBOX_DIR, from delegate init).")
    push_opt = typer.Option(False, "--push", help="Push it to the inbox now.")
    json_opt = typer.Option(False, "--json", help="Print JSON.")

    @propose_app.command("observation")
    def cli_inbox_propose_observation(
        text: str = typer.Option(..., "--text", help="What the agent noticed (up to 4000 characters)."),
        about: str | None = typer.Option(None, "--about", help="What it is about: a name or slug (one line)."),
        source: str | None = typer.Option(None, "--source", help="Where it came from: a URL or a note (one line)."),
        inbox: Path | None = inbox_opt,
        push: bool = push_opt,
        as_json: bool = json_opt,
    ) -> None:
        """Propose an observation. The owner's Scout reviews it; nothing is applied from it automatically."""
        body: dict[str, object] = {"text": text}
        if about is not None:
            body["about"] = about
        if source is not None:
            body["source"] = source
        _propose("observation", body, inbox, push, as_json)

    @propose_app.command("action")
    def cli_inbox_propose_action(
        op: str = typer.Argument(..., help="mark-done | snooze | add-comment | set-estimate | set-block | clear-block"),
        tag: str = typer.Option(..., "--tag", help="The item's [#TAG], without the brackets."),
        date: str = typer.Option(..., "--date", help="The daily file's date, YYYY-MM-DD."),
        undo: bool = typer.Option(False, "--undo", help="mark-done: mark it not done."),
        until: str | None = typer.Option(None, "--until", help="snooze: until YYYY-MM-DD."),
        comment: str | None = typer.Option(None, "--comment", help="add-comment: one line."),
        minutes: int | None = typer.Option(None, "--minutes", help="set-estimate: 1-1440."),
        kind: str | None = typer.Option(None, "--kind", help="set-estimate: the kind of work (one word)."),
        day: str | None = typer.Option(None, "--day", help="set-block: YYYY-MM-DD."),
        start: str | None = typer.Option(None, "--start", help="set-block: HH:MM."),
        end: str | None = typer.Option(None, "--end", help="set-block: HH:MM."),
        inbox: Path | None = inbox_opt,
        push: bool = push_opt,
        as_json: bool = json_opt,
    ) -> None:
        """Propose a change to one action item, addressed by its [#TAG] in one day's file."""
        given = {
            "undo": undo or None,
            "until": until,
            "comment": comment,
            "minutes": minutes,
            "kind": kind,
            "day": day,
            "start": start,
            "end": end,
        }
        args = {k: v for k, v in given.items() if v is not None}
        _propose("action_item_op", {"op": op, "tag": tag, "date": date, "args": args}, inbox, push, as_json)

    @inbox_app.command("pull")
    def cli_inbox_pull(
        delegate: str | None = typer.Argument(None, help="One delegate id. Omit with --all."),
        all_: bool = typer.Option(False, "--all", help="Every linked inbox; one failing doesn't stop the rest."),
        as_json: bool = json_opt,
    ) -> None:
        """Take new proposals from the delegates' inboxes into .scout-inbox/agents/ (the owner's computer).

        Each is checked, recorded under this computer's signature, copied, then
        removed from the inbox. Nothing is applied and nothing is committed here.
        """
        import json as _json

        from scout import delegate_inbox
        from scout import paths as _paths
        from scout.repo_sync import RepoSyncError

        if (delegate is None) == (not all_):
            typer.echo("scoutctl inbox pull: name one delegate, or pass --all", err=True)
            raise typer.Exit(2)
        vault = _paths.data_dir()
        if delegate is not None:
            try:
                result = delegate_inbox.pull(vault, delegate)
            except RepoSyncError as e:
                _fail(e, as_json)
            _say_inbox(result, as_json)
            return
        try:
            results = delegate_inbox.pull_all(vault)
        except RepoSyncError as e:
            _fail(e, as_json)
        failed = [r for r in results if isinstance(r, RepoSyncError)]
        if as_json:
            typer.echo(
                _json.dumps(
                    [
                        {"ok": False, "delegate": r.detail.get("delegate"), "code": r.code, "message": r.message}
                        if isinstance(r, RepoSyncError)
                        else {"ok": True, **r.to_json()}
                        for r in results
                    ],
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            for r in results:
                if isinstance(r, RepoSyncError):
                    typer.echo(f"{r.detail.get('delegate')}: {r.message}", err=True)
                else:
                    _say_inbox(r, False)
            if not results:
                typer.echo("no delegate inboxes on this computer")
        if failed:
            raise typer.Exit(2)

    @inbox_app.command("sync")
    def cli_inbox_sync(as_json: bool = json_opt) -> None:
        """Pull every inbox, commit the ledgers, then apply what was taken (the schedule tick starts this).

        The owner's, outside any run. The session lock is held only to commit and apply, for about a minute at
        most; a live run makes it wait for the next tick.
        """
        import json as _json

        from scout import delegate_inbox_sync
        from scout import paths as _paths
        from scout.repo_sync import RepoSyncError

        try:
            result = delegate_inbox_sync.sync(_paths.data_dir())
        except RepoSyncError as e:
            _fail(e, as_json)
        except Exception as e:  # never a traceback from the tick's own step
            _fail(RepoSyncError("sync_failed", f"refused: the inbox sync failed ({type(e).__name__}: {e})"), as_json)
        if as_json:
            typer.echo(_json.dumps({"ok": True, **result.to_json()}, indent=2, sort_keys=True))
            return
        for row in result.pulled + result.applied:
            typer.echo(_json.dumps(row, sort_keys=True))
        if result.ledger_commit:
            typer.echo(f"committed the ledgers: {result.ledger_commit}")
        if result.skipped:
            typer.echo(f"commits and applies waited: {result.skipped}")
        for note in result.notes:
            typer.echo(note)

    @inbox_app.command("apply")
    def cli_inbox_apply(
        delegate: str | None = typer.Argument(None, help="One delegate id. Omit with --all."),
        all_: bool = typer.Option(False, "--all", help="Every delegate with taken proposals."),
        as_json: bool = json_opt,
    ) -> None:
        """Apply the action-item proposals inbox pull took, each at most once (the owner's computer).

        Only a copy whose bytes match what the pull recorded is applied, only to an
        item the delegate's mirror showed it and still would, within daily limits.
        Each applied change can be undone with: scoutctl inbox revert <ULID>.
        """
        import json as _json

        from scout import delegate_apply
        from scout import paths as _paths
        from scout.repo_sync import RepoSyncError

        if (delegate is None) == (not all_):
            typer.echo("scoutctl inbox apply: name one delegate, or pass --all", err=True)
            raise typer.Exit(2)
        vault = _paths.data_dir()
        try:
            results = (
                [delegate_apply.apply(vault, delegate)] if delegate is not None else delegate_apply.apply_all(vault)
            )
        except RepoSyncError as e:
            _fail(e, as_json)
        except Exception as e:  # as --all does per delegate: a failure is a refusal, never a traceback
            _fail(
                RepoSyncError("apply_failed", f"refused: {delegate}'s apply failed ({type(e).__name__}: {e})"), as_json
            )
        failed = [r for r in results if isinstance(r, RepoSyncError)]
        if as_json:
            typer.echo(
                _json.dumps(
                    [
                        {"ok": False, "delegate": r.detail.get("delegate"), "code": r.code, "message": r.message}
                        if isinstance(r, RepoSyncError)
                        else {"ok": True, **r.to_json()}
                        for r in results
                    ],
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            for r in results:
                if isinstance(r, RepoSyncError):
                    typer.echo(f"{r.detail.get('delegate')}: {r.message}", err=True)
                    continue
                counts = ", ".join(f"{k} {v}" for k, v in r.counts().items()) or "nothing new"
                typer.echo(f"{r.delegate}: {counts}")
                for note in r.notes:
                    typer.echo(note)
        if failed:
            raise typer.Exit(2)

    @inbox_app.command("revert")
    def cli_inbox_revert(
        ulid: str = typer.Argument(..., help="The proposal's ULID, as inbox apply --json lists it."),
        as_json: bool = json_opt,
    ) -> None:
        """Put back a task a delegate's applied proposal changed, if nothing changed it since (the owner only)."""
        from scout import delegate_apply
        from scout import paths as _paths
        from scout.repo_sync import RepoSyncError

        try:
            delegate = delegate_apply.revert(_paths.data_dir(), ulid)
        except RepoSyncError as e:
            _fail(e, as_json)
        typer.echo(f'{{"ok": true, "delegate": "{delegate}", "reverted": "{ulid}"}}' if as_json else
                   f"reverted {delegate}'s {ulid}")  # fmt: skip

    # ----- inbox review (#550 V4b2): the owner reads, accepts, rejects and promotes agents' text -----------------

    review_app = typer.Typer(
        help="What agents proposed that waits for you: observations and held comments (the owner only).",
        invoke_without_command=True,
    )
    inbox_app.add_typer(review_app, name="review")

    def _show_item(item: object) -> None:
        from scout.delegate_review import Item

        assert isinstance(item, Item)
        what = item.kind if item.op is None else f"{item.kind} {item.op} [#{item.tag}] {item.date}"
        when = f"{item.status}, proposed {item.proposed}"
        typer.echo(f"{item.ulid}  FROM AGENT delegate-{item.delegate}  {what}  ({when})")
        if item.about:
            typer.echo(f"  about: {item.about}")
        if item.source:
            typer.echo(f"  source: {item.source}")
        if item.look_alikes:
            typer.echo(f"  LOOK-ALIKE WORDS (mixed or other scripts): {', '.join(item.look_alikes)}")
        for line in item.text.split("\n"):
            typer.echo(f"  | {line}")

    def _typed(ulid: str, confirm: str | None, what: str) -> str | None:
        """--confirm, or the ULID typed at a terminal; never a bare flag."""
        import sys

        if confirm is not None or not sys.stdin.isatty():
            return confirm
        return typer.prompt(f"To {what} it, type its ULID", default="", show_default=False)

    @review_app.callback()
    def cli_inbox_review(
        ctx: typer.Context,
        delegate: str | None = typer.Option(None, "--delegate", help="One delegate's items only."),
        accepted: bool = typer.Option(
            False, "--accepted", help="List the accepted observations instead (ids and times; promote acts on them)."
        ),
        as_json: bool = json_opt,
    ) -> None:
        """List what waits for your review, with hidden characters shown as \\u{...}."""
        import json as _json

        from scout import delegate_review
        from scout import paths as _paths
        from scout.repo_sync import RepoSyncError

        if ctx.invoked_subcommand is not None:
            return
        if accepted:
            try:
                notes = delegate_review.accepted(_paths.data_dir(), delegate=delegate)
            except RepoSyncError as e:
                _fail(e, as_json)
            if as_json:
                typer.echo(_json.dumps([a.to_json() for a in notes], indent=2, sort_keys=True))
                return
            for a in notes:
                where = (f"promoted to {', '.join(a.promoted_to)}" if a.promoted_to else "promoted") if a.promoted \
                    else "private"  # fmt: skip
                typer.echo(f"{a.ulid}  FROM AGENT delegate-{a.delegate}  {a.note}  ({where})")
            if not notes:
                typer.echo("No accepted observations.")
            return
        try:
            items = delegate_review.review(_paths.data_dir(), delegate=delegate)
        except RepoSyncError as e:
            _fail(e, as_json)
        if as_json:
            typer.echo(_json.dumps([i.to_json() for i in items], indent=2, sort_keys=True))
            return
        if not items:
            typer.echo("Nothing waits for review.")
            return
        for item in items:
            _show_item(item)
            typer.echo("")
        typer.echo("Accept: scoutctl inbox review accept <ULID>. Reject: scoutctl inbox review reject <ULID>.")
        if any(i.kind == "observation" for i in items):
            typer.echo(delegate_review.EDITED)

    @review_app.command("accept")
    def cli_inbox_review_accept(
        ulid: str = typer.Argument(..., help="The item's ULID."),
        confirm: str | None = typer.Option(None, "--confirm", help="The ULID again, to confirm without a terminal."),
        as_json: bool = json_opt,
    ) -> None:
        """Write an observation into knowledge-base/from-agents/, or a held comment into today's file.

        Runs read it from then on. An observation stays private and hidden from every delegate until you promote it.
        """
        from scout import delegate_review
        from scout import paths as _paths
        from scout.repo_sync import RepoSyncError

        vault = _paths.data_dir()
        try:
            if not as_json and confirm is None:
                for item in delegate_review.review(vault):
                    if item.ulid == ulid:
                        _show_item(item)
            decision = delegate_review.accept(vault, ulid, confirm=_typed(ulid, confirm, "accept"))
        except RepoSyncError as e:
            _fail(e, as_json)
        _decided(decision, as_json)

    def _decided(decision: object, as_json: bool) -> None:
        import json as _json

        from scout.delegate_review import Decision

        assert isinstance(decision, Decision)
        if as_json:
            typer.echo(_json.dumps({"ok": True, **decision.to_json()}, indent=2, sort_keys=True))
            return
        where = f" ({decision.note})" if decision.note else ""
        typer.echo(f"{decision.outcome}: delegate-{decision.delegate}'s {decision.ulid}{where}")
        for note in decision.notes:
            typer.echo(note)

    @review_app.command("reject")
    def cli_inbox_review_reject(
        ulid: str = typer.Argument(..., help="The item's ULID."),
        as_json: bool = json_opt,
    ) -> None:
        """Record it as rejected and delete the local copy. Nothing reaches the vault."""
        from scout import delegate_review
        from scout import paths as _paths
        from scout.repo_sync import RepoSyncError

        try:
            decision = delegate_review.reject(_paths.data_dir(), ulid)
        except RepoSyncError as e:
            _fail(e, as_json)
        _decided(decision, as_json)

    @review_app.command("promote")
    def cli_inbox_review_promote(
        ulid: str = typer.Argument(..., help="An accepted observation's ULID."),
        to: str | None = typer.Option(
            None, "--to", help="Only these delegates, comma-separated; 'author' is its author. Default: every delegate."
        ),
        confirm: str | None = typer.Option(None, "--confirm", help="The ULID again, to confirm without a terminal."),
        preview_only: bool = typer.Option(
            False, "--preview", help="Show the text and who would see it, with its sha256; write nothing."
        ),
        previewed: str | None = typer.Option(
            None, "--previewed", help="A --preview's sha256: promote only if the note is still exactly that."
        ),
        as_json: bool = json_opt,
    ) -> None:
        """Let delegates' agents read an accepted observation (it becomes a normal note, promoted_from its agent)."""
        from scout import delegate_review
        from scout import paths as _paths
        from scout.repo_sync import RepoSyncError

        vault = _paths.data_dir()
        targets = [t.strip() for t in to.split(",") if t.strip()] if to is not None else None
        try:
            if preview_only or previewed is None:
                preview = delegate_review.preview_promote(vault, ulid, to=targets)  # shown with --confirm too
                if preview_only:
                    _previewed(preview, as_json, "Would be visible to")
                    return
                if not as_json:
                    _show_item(preview)
                previewed = preview.sha256
            decision = delegate_review.promote(
                vault, ulid, confirm=_typed(ulid, confirm, "promote"), previewed=previewed, to=targets
            )
        except RepoSyncError as e:
            _fail(e, as_json)
        _decided(decision, as_json)

    def _previewed(item: object, as_json: bool, who: str) -> None:
        import json as _json

        from scout.delegate_review import Item

        assert isinstance(item, Item)
        if as_json:
            typer.echo(_json.dumps({"ok": True, **item.to_json()}, indent=2, sort_keys=True))
            return
        _show_item(item)
        typer.echo(f"{who}: {', '.join(f'delegate-{d}' for d in item.audience or []) or 'no delegate'}")
        typer.echo(f"sha256: {item.sha256}")

    @review_app.command("unpromote")
    def cli_inbox_review_unpromote(
        ulid: str = typer.Argument(..., help="A promoted observation's ULID."),
        preview_only: bool = typer.Option(
            False, "--preview", help="Show the text and who sees it now, with its sha256; write nothing."
        ),
        previewed: str | None = typer.Option(
            None, "--previewed", help="A --preview's sha256: unpromote only if the note is still exactly that."
        ),
        as_json: bool = json_opt,
    ) -> None:
        """Make a promoted note private again and hide it from every delegate, if it is unchanged since."""
        from scout import delegate_review
        from scout import paths as _paths
        from scout.repo_sync import RepoSyncError

        try:
            if preview_only:
                _previewed(delegate_review.preview_unpromote(_paths.data_dir(), ulid), as_json, "Seen now by")
                return
            decision = delegate_review.unpromote(_paths.data_dir(), ulid, previewed=previewed)
        except RepoSyncError as e:
            _fail(e, as_json)
        _decided(decision, as_json)


_register_inbox()


# What a delegate checkout (SCOUT_READER=delegate:<id>, #550 §6.5) may run: reads only. A
# delegate never writes the vault; it proposes through its inbox. This guards against an
# accident, not an attack: it covers `scoutctl` only (not `python -m scout.scripts.*`), and
# the boundary is that a delegate's copy can't push the vault. `action-items list` waits for
# V2: the daily file links private notes, which only the delegate mirror redacts (#550 §4.4).
_DELEGATE_COMMANDS = frozenset(
    {
        ("version",),
        *(
            ("kb", name)
            for name in (
                "files",
                "search",
                "read",
                "stat",
                "resolve",
                "query",
                "related",
                "traverse",
                "path",
                "name-lookup",
                "delta",
                "classify",
                "policy",
                "entity",
                "export",
                "stats",
                "validate",
            )
        ),
        ("pre-session", "data"),
        ("bootstrap", "doctor"),
        ("manifest", "show"),
        ("delegate", "init"),  # writes only the clone's .git/scout-delegate.env, never the tree
        ("inbox", "propose"),  # writes only the agent's inbox clone, never the vault or the mirror
    }
)
_DELEGATE_GROUPS = frozenset({"hook"})  # Claude Code's hooks, in a delegate session that runs them


def delegate_write_refusal(argv: list[str], env: Mapping[str, str]) -> str | None:
    """Why a ``delegate:<id>`` reader may not run ``argv`` (the words after ``scoutctl``), else None.

    None for any other reader. Only the commands in ``_DELEGATE_COMMANDS`` (and
    the ``hook`` group) pass, and ``--help`` right after the command words
    (``scoutctl kb share --help``), where it can't be another option's value;
    anything else, a bare group included, is refused. ``-h`` isn't help here:
    on some commands it is ``--hours``.
    """
    from scout.kb.reader import delegate_id, resolve_reader

    reader = resolve_reader(None, env)
    if delegate_id(reader) is None:
        return None
    words: list[str] = []
    for arg in argv:
        if arg.startswith("-"):
            break
        words.append(arg)
    if argv[len(words) : len(words) + 1] == ["--help"]:
        return None
    if words and (words[0] in _DELEGATE_GROUPS or tuple(words[:1]) in _DELEGATE_COMMANDS):
        return None
    if tuple(words[:2]) in _DELEGATE_COMMANDS:
        return None
    command = " ".join(words[:2]) or "(no command)"
    return (
        f"scoutctl: `{command}` isn't available to {reader}: this is a delegate checkout, which only reads "
        "the vault. Propose the change through the delegate's inbox instead, or run it on the owner's computer."
    )


def main() -> None:
    import os

    refusal = delegate_write_refusal(sys.argv[1:], os.environ)
    if refusal is not None:
        print(refusal, file=sys.stderr)
        sys.exit(3)
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
