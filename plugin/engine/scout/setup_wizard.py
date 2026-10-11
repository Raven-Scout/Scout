"""`scoutctl setup` — the terminal front end of Scout's setup (spec §4.3, §4.4).

Asks the questions /scout-setup used to ask, then calls `bootstrap auto`
in-process. Every outside effect goes through ``SetupDeps`` so the flow is
tested without a vault, a terminal or a real `claude`.
"""

from __future__ import annotations

import io
import os
import re
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
        """Open /dev/tty for reading and writing.

        The builtin ``open("/dev/tty", "r+")`` builds a buffered, seekable
        stream, and a tty isn't seekable — that raises ``io.UnsupportedOperation``
        (an ``OSError`` subclass) on every real terminal, so the wizard always
        reported "no terminal". Mirror ``getpass``: open the fd directly and
        wrap it in a non-seeking ``TextIOWrapper``.
        """
        try:
            fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
        except OSError:  # ENXIO: no controlling tty
            return None
        try:
            return cls(io.TextIOWrapper(io.FileIO(fd, "r+"), encoding="utf-8", write_through=True))
        except OSError:
            os.close(fd)
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


class NoTerminal(Exception):
    """A question needed an answer and there is no terminal to ask on."""


class NoTerminalPrompter(HeadlessPrompter):
    """No /dev/tty and no --yes: prints, but the first question raises ``NoTerminal``.

    An upgrade asks nothing, so it runs to the end; a first install fails at its
    first question with the headless flags.
    """

    def ask(self, question: str, default: str = "") -> str:
        raise NoTerminal(question)

    def confirm(self, question: str, default: bool) -> bool:
        raise NoTerminal(question)


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
    claude_bin: str = ""
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
    draft: Callable[[str, set[str]], dict[str, Any]]  # (server name, keys kept earlier in this setup)
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
            connector_detect.run_claude_mcp_list(claude),
            registry=connector_probes.resolve_registry(),
            custom_servers=connector_uncovered.custom_connector_servers(vault()),
        )

    def draft(server: str, taken: set[str]) -> dict[str, Any]:
        return connector_draft.draft(server, plugin_root=plugin_root, vault=vault(), claude_bin=claude, taken=taken)

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
    if opts.yes:
        listing = deps.list_uncovered()
        names = [s["name"] for s in listing.get("servers") or []]
        if names:
            p.say(f"You also have {', '.join(names)} connected — run `scoutctl connectors setup` to add them.")
    else:
        a.drafts = offer_uncovered(p, deps)
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


_SIGN_IN = (
    "sign in first: in Claude Code run /mcp (or claude.ai ▸ Settings ▸ Connectors), "
    "then run `scoutctl connectors setup`."
)


_RETRY = "Retry with `scoutctl connectors setup`."
# Fixed copy per failed draft status. The draft's `message` (claude's stderr, or
# text a model wrote) is never echoed: it could carry a dead end to the user.
# Spec §4.2: `no_read_tools` and `invalid` both read as "couldn't find anything to read".
_DRAFT_FAILED = {
    "no_read_tools": "Scout couldn't find anything to read in {name} yet. " + _RETRY,
    "invalid": "Scout couldn't find anything to read in {name} yet. " + _RETRY,
    "timeout": "Drafting {name} took too long. " + _RETRY,
}
_DRAFT_DIDNT_FINISH = "Drafting {name} didn't finish. " + _RETRY
# C0 and C1 control characters (ESC, BEL, CR, CSI …): model-written text must not
# drive the terminal (colours, cursor moves, OSC title or hyperlink sequences).
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def strip_controls(text: object) -> str:
    """``text`` as a string with every C0/C1 control character removed."""
    return _CONTROL_RE.sub("", str(text))


def offer_uncovered(p: Prompter, deps: SetupDeps) -> list[KeptDraft]:
    """Offer every connected server no connector reads; return the drafts the user keeps."""
    listing = deps.list_uncovered()
    if listing.get("error"):
        p.say(f"Couldn't list your other connected tools ({listing['error']}). Run `scoutctl connectors setup` later.")
        return []
    servers = listing.get("servers") or []
    if not servers:
        return []
    p.say("Also connected: " + ", ".join(s["name"] for s in servers) + ".")
    kept: list[KeptDraft] = []
    kept_keys: set[str] = set()  # not in the vault until apply_drafts, so each draft is told
    for s in servers:
        name = s["name"]
        if s["status"] != "connected":
            p.say(f"  {name}: {_SIGN_IN}")
            continue
        if not p.confirm(f"  Add {name}?", True):
            continue
        p.say("    drafting (about 30 s)…")
        result = deps.draft(name, set(kept_keys))
        status = result.get("status")
        if status == "needs_auth":
            p.say(f"    {name}: {_SIGN_IN}")
            continue
        if status != "drafted":
            p.say("    " + _DRAFT_FAILED.get(str(status), _DRAFT_DIDNT_FINISH).format(name=name))
            continue
        summary = {x["key"]: x for x in result.get("summary", [])}
        for d in result.get("definitions", []):
            label = strip_controls(d["display_name"])
            if d["key"] in kept_keys:
                p.say(
                    f"    {label}: a connector named {d['key']} is already in this setup. "
                    "Retry with `scoutctl connectors setup` after setup."
                )
                continue
            line = summary.get(d["key"], {})
            p.say(f"    {label} · {strip_controls(line.get('scans', ''))} · {strip_controls(line.get('looks_up', ''))}")
            inputs = {n: p.ask(f"    {n.replace('_', ' ')} for {label}") for n in d.get("needs_user_input", [])}
            if p.confirm("    Keep it?", True):
                kept.append(KeptDraft(d, inputs))
                kept_keys.add(d["key"])
    return kept


def apply_drafts(drafts: list[KeptDraft], p: Prompter, deps: SetupDeps) -> int:
    """Add each kept draft with `custom add`; return how many are live."""
    applied = 0
    for k in drafts:
        out = deps.add_custom(k.definition, k.inputs)
        label = strip_controls(k.definition.get("display_name", k.definition.get("key", "")))
        status = out.get("status")
        if status in ("applied", "unchanged"):
            applied += 1
            p.say(f"  Added {label}.")
        elif status == "busy":
            p.say(f"  {label}: a Scout session is running. Run `scoutctl connectors setup` again when it finishes.")
        else:
            issues = "; ".join(f"{i['path']}: {i['message']}" for i in out.get("issues", []))
            p.say(f"  {label}: {out.get('message') or status}{(' — ' + issues) if issues else ''}")
    return applied


def run_connectors_setup(vault: Path, p: Prompter, deps: SetupDeps) -> int:
    """`scoutctl connectors setup`: the "Also connected" step on its own, for an existing vault."""
    if not (vault / "scout-config.yaml").exists():
        p.say(f"No Scout vault at {vault}. Set Scout up first: open Scout.app or run `scoutctl setup`.")
        return 2
    try:
        kept = offer_uncovered(p, deps)
    except (EOFError, KeyboardInterrupt):
        p.say(CANCELLED)
        return 1
    apply_drafts(kept, p, deps)
    return 0


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
        upgrade_argv = ["bootstrap", "auto", "--no-interactive", "--yes", "--managed-by", opts.managed_by or "preserve"]
        if opts.claude_bin:
            upgrade_argv += ["--claude-bin", opts.claude_bin]
        return deps.invoke(upgrade_argv)
    try:
        answers = _gather(opts, p, deps)
    except NoTerminal:
        p.say(f"No terminal to ask questions on. Run it headless: scoutctl setup {HEADLESS_FLAGS}")
        return 2
    except (EOFError, KeyboardInterrupt):
        p.say(CANCELLED)
        return 1
    if answers is None:
        return 2
    install_argv = auto_argv(answers, opts.managed_by or "claude-code")
    if opts.claude_bin:
        install_argv += ["--claude-bin", opts.claude_bin]
    code = deps.invoke(install_argv)
    if code not in (0, 1):
        return code
    if answers.daily_budget:
        deps.invoke(["budget", "set", "--daily-usd", answers.daily_budget])
    apply_drafts(answers.drafts, p, deps)
    first = opts.first_run
    if first is None:
        if opts.yes:
            first = False
        else:
            try:
                first = p.confirm("Run your first briefing now?", False)
            except (EOFError, KeyboardInterrupt):
                # Something was already written (bootstrap ran above) — this
                # isn't a cancel, just "no" to one optional, final question.
                first = False
    if first:
        slot = deps.first_briefing_slot()
        if slot:
            deps.invoke(["schedule", "fire-now", slot])
        else:
            p.say("No briefing slot in your schedule; the first scheduled run will be the first briefing.")
    else:
        p.say("Your first scheduled run fires at the next slot in .scout-state/schedule.yaml.")
    return code
