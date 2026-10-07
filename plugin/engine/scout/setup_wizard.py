"""`scoutctl setup` — the terminal front end of Scout's setup (spec §4.3, §4.4).

Asks the questions /scout-setup used to ask, then calls `bootstrap auto`
in-process. Every outside effect goes through ``SetupDeps`` so the flow is
tested without a vault, a terminal or a real `claude`.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, TextIO
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from scout.scripts.bootstrap_auto import AutoAction, Plan
from scout.scripts.connector_detect import Detection, DetectStatus

CANCELLED = "Setup cancelled; nothing was written."
HEADLESS_FLAGS = (
    "--yes --name NAME --email EMAIL [--connectors a,b] [--slack-id ID] [--github-username U] "
    "[--github-repos owner/repo,…] [--max-budget 5.00] [--daily-budget USD] [--timezone ZONE] [--no-first-run]"
)
# needs_user_input name → (bootstrap auto flag, question)
_INPUTS = {
    "user_slack_id": ("--user-slack-id", "Your Slack member ID (Slack → your profile → ⋮ → Copy member ID)"),
    "github_username": ("--github-username", "Your GitHub username"),
    "github_repos": ("--github-repos", "GitHub repos to watch (owner/repo, comma-separated)"),
}


class Prompter(Protocol):
    def say(self, text: str) -> None: ...

    def ask(self, question: str, default: str = "") -> str: ...

    def confirm(self, question: str, default: bool) -> bool: ...


class TtyPrompter:
    """Prompts on /dev/tty: under `curl | bash` stdin is the pipe, not the user."""

    def __init__(self, tty: TextIO) -> None:
        self._tty = tty

    @classmethod
    def open(cls) -> TtyPrompter | None:
        try:
            return cls(open("/dev/tty", "r+", encoding="utf-8"))  # noqa: SIM115 — lives for the whole run
        except OSError:
            return None

    def say(self, text: str) -> None:
        self._tty.write(text + "\n")
        self._tty.flush()

    def ask(self, question: str, default: str = "") -> str:
        suffix = f" [{default}]" if default else ""
        self._tty.write(f"{question}{suffix}: ")
        self._tty.flush()
        line = self._tty.readline()
        if line == "":
            raise EOFError("the terminal closed")
        return line.strip() or default

    def confirm(self, question: str, default: bool) -> bool:
        hint = "[Y/n]" if default else "[y/N]"
        while True:
            answer = self.ask(f"{question} {hint}").lower()
            if not answer:
                return default
            if answer in ("y", "yes"):
                return True
            if answer in ("n", "no"):
                return False


class HeadlessPrompter:
    """--yes: print, never ask; every question takes its default."""

    def say(self, text: str) -> None:
        print(text)

    def ask(self, question: str, default: str = "") -> str:
        return default

    def confirm(self, question: str, default: bool) -> bool:
        return default


@dataclass
class SetupOptions:
    vault: Path
    instance_name: str = ""
    name: str = ""
    email: str = ""
    timezone: str = ""
    connectors: str | None = None  # None: detect (and ask unless --yes)
    slack_id: str = ""
    github_username: str = ""
    github_repos: str = ""
    max_budget: str = ""
    daily_budget: str = ""
    first_run: bool | None = None
    managed_by: str = ""  # "": claude-code on install, preserve on upgrade
    yes: bool = False


@dataclass
class KeptDraft:
    definition: dict[str, Any]
    inputs: dict[str, str]


@dataclass
class Answers:
    instance_name: str = "Scout"
    name: str = ""
    email: str = ""
    timezone: str = ""
    connectors: set[str] = field(default_factory=set)
    slack_id: str = ""
    github_username: str = ""
    github_repos: str = ""
    max_budget: str = "5.00"
    daily_budget: str = ""
    drafts: list[KeptDraft] = field(default_factory=list)


@dataclass
class SetupDeps:
    plan: Callable[[Path], Plan]
    pointer_manager: Callable[[], str | None]
    git_identity: Callable[[], tuple[str, str]]
    host_zone: Callable[[], str | None]
    detect_connectors: Callable[[], dict[str, Detection]]
    list_uncovered: Callable[[], dict[str, Any]]
    draft: Callable[[str], dict[str, Any]]
    invoke: Callable[[list[str]], int]
    add_custom: Callable[[dict[str, Any], dict[str, str]], dict[str, Any]]
    first_briefing_slot: Callable[[], str | None]


def invoke_scoutctl(args: list[str]) -> int:
    """Run a scoutctl command in this process; its output goes to the terminal."""
    from typer.main import get_command

    from scout.cli import app

    try:
        rv = get_command(app).main(args=args, prog_name="scoutctl", standalone_mode=False)
    except Exception as e:  # Click usage errors carry an exit_code
        code = getattr(e, "exit_code", None)
        if code is None:
            raise
        sys.stderr.write(f"error: {e}\n")
        return int(code)
    return rv if isinstance(rv, int) else 0


def _git_identity() -> tuple[str, str]:
    def get(key: str) -> str:
        try:
            out = subprocess.run(["git", "config", "--global", key], capture_output=True, text=True, check=False)
        except OSError:
            return ""
        return out.stdout.strip()

    return get("user.name"), get("user.email")


def default_deps(claude_bin: str = "") -> SetupDeps:
    from scout import __version__
    from scout.config import host_timezone_name
    from scout.scripts import connector_detect, connector_draft, connector_probes, connector_uncovered
    from scout.scripts.bootstrap import resolve_claude_bin
    from scout.scripts.bootstrap_auto import detect
    from scout.scripts.custom_connector_ops import add
    from scout.scripts.engine_pointer import read_pointer

    claude = resolve_claude_bin(claude_bin)
    plugin_root = Path(__file__).parent.parent.parent

    def vault() -> Path:
        from scout import paths

        return paths.data_dir()

    def pointer_manager() -> str | None:
        p = read_pointer(home=Path.home())
        return p.managed_by if p else None

    def detect_connectors() -> dict[str, Detection]:
        return connector_detect.detect(
            connector_probes.resolve_registry(),
            mcp_list_output=connector_detect.run_claude_mcp_list(claude),
            run_bash=connector_detect.run_bash_probe,
        )

    def list_uncovered() -> dict[str, Any]:
        return connector_uncovered.find_uncovered(
            connector_detect.run_claude_mcp_list(claude), registry=connector_probes.resolve_registry()
        )

    def draft(server: str) -> dict[str, Any]:
        return connector_draft.draft(server, plugin_root=plugin_root, vault=vault(), claude_bin=claude)

    def add_custom(definition: dict[str, Any], inputs: dict[str, str]) -> dict[str, Any]:
        return add(
            vault(), dict(definition), plugin_root=plugin_root, plugin_version=__version__, inputs=inputs
        ).to_json()

    def first_briefing_slot() -> str | None:
        from scout.schedule import load_default_schedule, load_schedule

        path = vault() / ".scout-state" / "schedule.yaml"
        sched = load_schedule(path) if path.exists() else load_default_schedule()
        return next((k for k in sorted(sched.keys()) if sched[k].type.value == "briefing"), None)

    return SetupDeps(
        plan=detect,
        pointer_manager=pointer_manager,
        git_identity=_git_identity,
        host_zone=host_timezone_name,
        detect_connectors=detect_connectors,
        list_uncovered=list_uncovered,
        draft=draft,
        invoke=invoke_scoutctl,
        add_custom=add_custom,
        first_briefing_slot=first_briefing_slot,
    )


def auto_argv(a: Answers, managed_by: str) -> list[str]:
    argv = [
        "bootstrap",
        "auto",
        "--no-interactive",
        "--yes",
        "--managed-by",
        managed_by,
        "--instance-name",
        a.instance_name,
        "--user-name",
        a.name,
        "--user-email",
        a.email,
        "--connectors",
        ",".join(sorted(a.connectors)),
    ]
    if a.timezone:
        argv += ["--timezone", a.timezone]
    for flag, value in (
        ("--user-slack-id", a.slack_id),
        ("--github-username", a.github_username),
        ("--github-repos", a.github_repos),
    ):
        if value:
            argv += [flag, value]
    return [*argv, "--max-budget", a.max_budget]


def _ask_required(p: Prompter, question: str, default: str, check: Callable[[str], bool], why: str) -> str:
    while True:
        value = p.ask(question, default)
        if check(value):
            return value
        p.say(why)


def _valid_zone(zone: str) -> bool:
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def _ask_timezone(p: Prompter, opts: SetupOptions, deps: SetupDeps) -> str:
    if opts.timezone or opts.yes:
        return opts.timezone
    host = deps.host_zone()
    if host and p.confirm(
        f"Scout follows this computer's timezone ({host}), so it stays right when you travel. Keep it?", True
    ):
        return ""
    return _ask_required(
        p, "Timezone to pin (IANA name, e.g. Europe/Berlin)", "", _valid_zone, "That isn't an IANA zone name."
    )


def _float_ok(text: str, *, allow_blank: bool) -> bool:
    if not text:
        return allow_blank
    try:
        return float(text) > 0
    except ValueError:
        return False


def _ask_connectors(p: Prompter, opts: SetupOptions, deps: SetupDeps, a: Answers) -> None:
    if opts.connectors is not None:
        a.connectors = {c.strip() for c in opts.connectors.split(",") if c.strip()}
    else:
        dets = deps.detect_connectors()
        a.connectors = {k for k, d in dets.items() if d.status is DetectStatus.CONNECTED}
        if not opts.yes:
            names = sorted(dets)
            while True:
                p.say("Connected tools:")
                for i, k in enumerate(names, 1):
                    mark = "✓" if k in a.connectors else ("?" if dets[k].status is DetectStatus.UNKNOWN else "✗")
                    hint = " (sign in through /mcp in Claude Code)" if dets[k].status is DetectStatus.NEEDS_AUTH else ""
                    p.say(f"  {i} [{mark}] {k}{hint}")
                raw = p.ask("Toggle by number, or press enter to continue")
                if not raw:
                    break
                for part in raw.replace(",", " ").split():
                    if part.isdigit() and 1 <= int(part) <= len(names):
                        a.connectors ^= {names[int(part) - 1]}
    given = {"user_slack_id": opts.slack_id, "github_username": opts.github_username, "github_repos": opts.github_repos}
    needs = {
        "slack": ["user_slack_id"],
        "github": ["github_username", "github_repos"],
    }
    values = dict(given)
    for connector in sorted(a.connectors):
        for name in needs.get(connector, []):
            if not values[name] and not opts.yes:
                values[name] = p.ask(_INPUTS[name][1])
    a.slack_id, a.github_username, a.github_repos = (
        values["user_slack_id"],
        values["github_username"],
        values["github_repos"],
    )


def _gather(opts: SetupOptions, p: Prompter, deps: SetupDeps) -> Answers | None:
    a = Answers()
    git_name, git_email = deps.git_identity()
    if opts.yes:
        a.instance_name = opts.instance_name or "Scout"
        a.name, a.email = opts.name, opts.email
        if not a.name or not a.email:
            p.say(f"--yes needs --name and --email. Headless flags: {HEADLESS_FLAGS}")
            return None
    else:
        a.instance_name = opts.instance_name or p.ask("Name this Scout instance", "Scout")
        a.name = opts.name or _ask_required(p, "Your name", git_name, bool, "Scout needs a name for commits.")
        a.email = opts.email or _ask_required(
            p, "Your email", git_email, lambda v: "@" in v, "That doesn't look like an email address."
        )
    a.timezone = _ask_timezone(p, opts, deps)
    _ask_connectors(p, opts, deps, a)
    a.max_budget = opts.max_budget or (
        "5.00"
        if opts.yes
        else _ask_required(
            p,
            "Budget per session, USD",
            "5.00",
            lambda v: _float_ok(v, allow_blank=False),
            "Enter an amount above 0.",
        )
    )
    a.daily_budget = opts.daily_budget or (
        ""
        if opts.yes
        else _ask_required(
            p,
            "Daily budget, USD (blank for none)",
            "",
            lambda v: _float_ok(v, allow_blank=True),
            "Enter an amount above 0, or leave it blank.",
        )
    )
    return a


def _apply_drafts(a: Answers, p: Prompter, deps: SetupDeps) -> None:
    """Task 8 fills this in; Task 7 has no drafts to apply."""


def run_setup(opts: SetupOptions, prompter: Prompter, deps: SetupDeps) -> int:
    p = prompter
    if deps.pointer_manager() == "scout-app":
        p.say("Scout.app manages this engine — open Scout.app to set up or change Scout.")
        return 2
    plan = deps.plan(opts.vault)
    if plan.action is AutoAction.REFUSED:
        p.say(f"Can't set up Scout in {opts.vault}: {plan.reason}")
        return 2
    if plan.action is AutoAction.UPGRADE:
        p.say(f"Found your vault at {opts.vault}, upgrading.")
        return deps.invoke(
            ["bootstrap", "auto", "--no-interactive", "--yes", "--managed-by", opts.managed_by or "preserve"]
        )
    try:
        answers = _gather(opts, p, deps)
    except (EOFError, KeyboardInterrupt):
        p.say(CANCELLED)
        return 1
    if answers is None:
        return 2
    code = deps.invoke(auto_argv(answers, opts.managed_by or "claude-code"))
    if code == 2:
        return 2
    if answers.daily_budget:
        deps.invoke(["budget", "set", "--daily-usd", answers.daily_budget])
    _apply_drafts(answers, p, deps)
    first = opts.first_run
    if first is None:
        first = False if opts.yes else p.confirm("Run your first briefing now?", False)
    if first:
        slot = deps.first_briefing_slot()
        if slot:
            deps.invoke(["schedule", "fire-now", slot])
        else:
            p.say("No briefing slot in your schedule; the first scheduled run will be the first briefing.")
    else:
        p.say("Your first scheduled run fires at the next slot in .scout-state/schedule.yaml.")
    return code
