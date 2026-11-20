"""Investigation prompts for the app's "Resolve with Claude" button (spec 2026-11-19).

``scoutctl health resolve-prompt <kind> <key>`` re-reads the live state at click time and
builds one markdown prompt for a new Claude Code session: what Scout saw, the evidence to
read, how to investigate, how to reproduce and verify, the shared guardrails, and when it
is done. Read-only: nothing here writes.

A prompt carries paths, ids, commit SHAs, timestamps and the engine's own one-line error
text, which the app already shows. It never carries a file's body: the tokens taken from
a log are matched by tight patterns, never copied as the line's remainder.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import scout
from scout import health
from scout.health import Finding, OutcomeRow

SCHEMA_VERSION = 1
KINDS = ("finding", "connector", "run")
# Whole-match patterns; the app checks keys against the same ones (ResolveSubject.isValidKey).
KEY_PATTERNS: dict[str, re.Pattern[str]] = {
    # A dotted code, optionally ":" and a slot key or lane name (slot.stale:<key>, lane.dark:<lane>).
    "finding": re.compile(r"[a-z][a-z0-9_.]{0,63}(?::[A-Za-z0-9][A-Za-z0-9._-]{0,80})?"),
    # connector-alerts.json's connector_key as written: mcp:<server>, notify:<channel>.
    "connector": re.compile(r"[a-z][a-z0-9_-]{0,31}(?::[A-Za-z0-9_.-]{1,80})?"),
    # A bare run-log basename under .scout-logs/, never a path.
    "run": re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.log"),
}

GUARDRAILS = """\
## Guardrails

- Investigate read-only first. Report what you found before you change anything.
- Never edit protected files without asking: runner scripts, `.claude/` and agent settings, `.mcp.json`, \
`.scout-state/schedule.yaml`, `triggers.yaml`, the delegate state.
- Commit in the vault only through `scripts/git-safe-commit.sh`. Never rewrite history, force-push, or delete a \
branch, tag or stash entry without asking.
- Never read secrets, `.env*` files, tokens or keychain items. Never paste file content from the vault into an \
issue or PR.
- If the cause is in Scout itself, read the engine checkout but don't edit it. Draft an issue (with no vault \
content) for the owner to file.
"""

NOT_CURRENT_FINDING = "Scout no longer reports this. Confirm it cleared, and why, before investigating further."
STANDARD_COMMANDS = ("scoutctl health --json", "scoutctl runs --json --since 7d", "scoutctl bootstrap doctor")
# Failure classes that clear by themselves (the app's Problem.softFailureClasses).
TRANSIENT_CLASSES = frozenset({"rate_limited", "auth_race", "network", "git_lock", "lease_held"})
_LOG_SCAN_BYTES = 262144
_REF_RE = r"(?P<ref>(?:refs/[A-Za-z0-9._/-]{1,200})|HEAD|index)"
_INTEGRITY_CHANGED_RE = re.compile(
    r"^integrity check: " + _REF_RE + r" changed protected file\(s\): (?P<paths>[A-Za-z0-9._/, -]{1,1000})$"
)
_INTEGRITY_RESTORED_RE = re.compile(
    r"^integrity check: restored them on " + _REF_RE + r" in commit (?P<sha>[0-9a-f]{7,40})$"
)
_INTEGRITY_HELD_RE = re.compile(r"^integrity check: " + _REF_RE + r" still holds the change")
_PATH_TOKEN_RE = re.compile(r"[A-Za-z0-9._-][A-Za-z0-9._/-]{0,200}")


class ResolveError(ValueError):
    """A bad kind or key, or nothing to build a prompt from. The CLI maps it to exit 2."""


@dataclass(frozen=True)
class ResolvePrompt:
    kind: str
    key: str
    current: bool
    title: str
    cwd: str
    prompt: str

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": self.kind,
            "key": self.key,
            "current": self.current,
            "title": self.title,
            "cwd": self.cwd,
            "prompt": self.prompt,
        }


@dataclass
class Guide:
    """A playbook's part of a prompt."""

    title: str
    steps: list[str]
    commands: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    done: str | None = None


@dataclass(frozen=True)
class _Ctx:
    vault: Path
    id: str
    finding: Finding | None
    rows: list[OutcomeRow]

    @property
    def suffix(self) -> str | None:
        return self.id.split(":", 1)[1] if ":" in self.id else None


Playbook = Callable[[_Ctx], Guide]


def validate(kind: str, key: str) -> None:
    if kind not in KINDS:
        raise ResolveError(f"unknown kind {kind!r}: expected one of {', '.join(KINDS)}")
    if not KEY_PATTERNS[kind].fullmatch(key):
        raise ResolveError(f"not a valid {kind} key")


def _iso(dt: datetime | None) -> str:
    return health.iso_z(dt) if dt is not None else "unknown"


def _code(text: str) -> str:
    """``text`` as inline code, with a fence long enough for any backticks inside it."""
    run = max((len(m) for m in re.findall(r"`+", text)), default=0)
    fence = "`" * (run + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _engine_root() -> Path:
    return Path(scout.__file__).resolve().parents[1]


def _logs(vault: Path) -> Path:
    return vault / ".scout-logs"


def _state(vault: Path) -> Path:
    return vault / ".scout-state"


def _standard_evidence(vault: Path, log: str | None) -> list[str]:
    out = []
    if log:
        out.append(f"The run log: `{log}`")
    out += [
        f"The run ledger, one JSON row per run (match a row by `slot` and `started_at`): "
        f"`{_logs(vault) / health.LEDGER_NAME}`",
        f"The schedule: `{_state(vault) / 'schedule.yaml'}`",
        f"Scout's own code (read-only): `{_engine_root()}`",
    ]
    return out


def _render(
    *, title: str, seen: list[str], evidence: list[str], steps: list[str], commands: list[str], done: str
) -> str:
    lines = [
        f"# {title}",
        "",
        "Scout's run health raised the problem below. Work out why, read-only first, and tell me what you "
        "found before you change anything.",
        "",
        "## What Scout saw",
        "",
        *(f"- {s}" for s in seen),
        "",
        "## Evidence to read",
        "",
        *(f"- {e}" for e in evidence),
        "",
        "## How to investigate",
        "",
        *(f"{i}. {s}" for i, s in enumerate(steps, 1)),
        "",
        "## Reproduce and verify",
        "",
        "Run these from the vault folder:",
        "",
        "```bash",
        *commands,
        "```",
        "",
        GUARDRAILS.rstrip("\n"),
        "",
        "## Done when",
        "",
        done,
        "",
    ]
    return "\n".join(lines)


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


# --- finding playbooks ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Integrity:
    changed: list[tuple[str, list[str]]]
    restored: list[tuple[str, str]]
    held: list[str]


def _read_tail(path: Path) -> str:
    try:
        with path.open("rb") as f:
            size = path.stat().st_size
            if size > _LOG_SCAN_BYTES:
                f.seek(size - _LOG_SCAN_BYTES)
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _integrity_lines(log: Path | None) -> _Integrity:
    """The refs, paths and SHAs the log's ``integrity check:`` lines name: matched tokens only."""
    found = _Integrity([], [], [])
    if log is None:
        return found
    for line in _read_tail(log).splitlines():
        line = line.strip()
        if m := _INTEGRITY_CHANGED_RE.match(line):
            paths = [p for p in (s.strip() for s in m["paths"].split(",")) if _PATH_TOKEN_RE.fullmatch(p)]
            found.changed.append((m["ref"], paths))
        elif m := _INTEGRITY_RESTORED_RE.match(line):
            found.restored.append((m["ref"], m["sha"]))
        elif m := _INTEGRITY_HELD_RE.match(line):
            found.held.append(m["ref"])
    return found


def _integrity_row(ctx: _Ctx) -> OutcomeRow | None:
    wanted = ("restored", "unrestored") if ctx.id == "run.integrity_restored" else ("error",)
    return next((r for r in reversed(ctx.rows) if r.integrity in wanted), None)


def _row_line(row: OutcomeRow) -> str:
    return (
        f"Its ledger row: slot `{row.slot}`, `started_at` {int(row.started_at.timestamp())} "
        f"({_iso(row.started_at)}), finished {_iso(row.finished_at)}, outcome `{row.outcome}`"
    )


def _row_log(ctx: _Ctx, row: OutcomeRow | None) -> Path | None:
    if ctx.finding is not None and ctx.finding.log:
        return Path(ctx.finding.log)
    if row is not None and row.log:
        path = _logs(ctx.vault) / row.log
        return path if path.is_file() else None
    return None


def _integrity(ctx: _Ctx) -> Guide:
    row = _integrity_row(ctx)
    log = _row_log(ctx, row)
    evidence = []
    if log is not None and (ctx.finding is None or not ctx.finding.log):
        evidence.append(f"The run log: `{log}`")
    if row is not None:
        evidence.append(_row_line(row))
    if ctx.id == "run.integrity_unchecked":
        return Guide(
            title="Investigate: the post-run integrity check couldn't run",
            evidence=evidence,
            steps=[
                "Read the run log's `integrity check: couldn't run:` line for the reason.",
                "Run `git status` in the vault to see the state the check found (a rebase, a lock, a detached HEAD).",
                "Check whether the next scheduled run's log has a normal `integrity check:` line.",
            ],
            commands=["git status", "git worktree list --porcelain"],
            done=(
                "You know why the check couldn't run, the owner has approved any fix, and the next scheduled run's "
                "log has a normal `integrity check:` line."
            ),
        )
    lines = _integrity_lines(log)
    paths = sorted({p for _ref, ps in lines.changed for p in ps})
    for ref, ps in lines.changed:
        evidence.append(f"Changed on `{ref}`: " + ", ".join(f"`{p}`" for p in ps))
    for ref, sha in lines.restored:
        evidence.append(f"Restoring commit `{sha}` on `{ref}`")
    for ref in lines.held:
        evidence.append(f"`{ref}` still holds the change (Scout didn't rewrite it)")
    commands = ["git worktree list --porcelain"]
    scope = " -- " + " ".join(paths) if paths else ""
    for _ref, sha in lines.restored:
        commands += [f"git show --stat {sha}", f"git log --format='%H %an %ad %s' -n 5 {sha}^{scope}"]
    return Guide(
        title="Investigate: a scheduled run changed a protected file",
        evidence=evidence,
        steps=[
            "Read the run log's `integrity check:` lines: which ref, which paths, which restoring commit.",
            "For each ref, run `git worktree list --porcelain` and see whether that branch is checked out in a "
            "vault worktree, for example an interactive Claude session's, rather than by the run.",
            "Find the commit that changed the file (`git log` on the restoring commit's parent, limited to the "
            "paths) and compare its author time with the run's `started_at` and finish.",
            "Run `git show --stat` on the changing commit and on the restoring commit.",
            "Decide which session made the change, whether it was the run, and whether the restore was right. If "
            "the restore was wrong, say how to undo it, and ask before doing it.",
        ],
        commands=commands,
        done=(
            "The cause is known: which session changed the file, and whether it was the run. The restore is confirmed "
            "right, or the owner has approved how to undo it. `run.integrity_restored` stays on the health panel for "
            f"{health.INTEGRITY_WINDOW.days} days after the run by design, so it clearing is not the test."
        ),
    )


def _failures(ctx: _Ctx) -> Guide:
    soft = ", ".join(f"`{c}`" for c in sorted(TRANSIENT_CLASSES))
    return Guide(
        title=f"Investigate: scheduled runs are failing ({ctx.id})",
        steps=[
            "Read the error line Scout recorded, then the end of the run log around it.",
            "Find the run's ledger row and its `failure_class`; check whether the same class repeats across slots "
            "in `scoutctl runs --json --since 7d`.",
            f"Tell a transient class ({soft}), which clears by itself, from a real failure that needs a fix.",
            "If it is real, find the cause: the log, the run's session, the schedule entry or Scout's own code.",
        ],
    )


def _auth(ctx: _Ctx) -> Guide:
    return Guide(
        title="Investigate: scheduled runs can't sign in to Claude",
        steps=[
            "Run `scoutctl auth status`: which sign-in scheduled runs use and, for a token, when it expires.",
            "Never print, copy or echo a token, and never open the file or keychain item that stores it.",
            "Check the newest failed run's log for the authentication error line.",
            "Signing in again is the owner's step (`claude auth login`, or `scoutctl auth setup-token` for "
            "unattended runs). Tell them which one; you can't do it for them.",
        ],
        commands=["scoutctl auth status"],
        done=(
            f"The owner has signed in again, and `scoutctl auth status` is healthy and `scoutctl health --json` no "
            f"longer lists `{ctx.id}`."
        ),
    )


def _sandbox(ctx: _Ctx) -> Guide:
    return Guide(
        title="Investigate: scheduled runs aren't protected by the write sandbox",
        steps=[
            "Run `scoutctl sandbox status` and `scoutctl protection status --json`: what the sandbox is set to and "
            "what the last runs reported.",
            "Read the newest run log's sandbox lines (`grep -n -i sandbox <log>`).",
            "Work out whether the sandbox was switched off on purpose (`SCOUT_RUN_SANDBOX`, `scoutctl sandbox off`) "
            "or failed to start.",
        ],
        commands=["scoutctl sandbox status", "scoutctl protection status --json"],
    )


def _gates(ctx: _Ctx) -> Guide:
    return Guide(
        title="Investigate: the privacy checks aren't guarding scheduled runs",
        steps=[
            "Run `scoutctl protection status --json`: the last privacy-check probe and what it found.",
            "Read the hook registration in the vault's `.claude/settings.json` (read it; don't edit it).",
            "Read the newest skipped run's log for the probe's exit line.",
            "Only with the owner's OK, re-run the probe: `scoutctl gates probe` (a local fake endpoint, no model "
            "and no spend).",
        ],
        commands=["scoutctl protection status --json", "scoutctl gates probe --cached"],
    )


def _scheduler(ctx: _Ctx) -> Guide:
    vault = ctx.vault
    evidence = [
        f"When each slot last fired: `{_state(vault) / 'last-fire.json'}`",
        f"The scheduler's event log: `{_logs(vault)}/schedule-events-*.jsonl`",
    ]
    commands = ["scoutctl schedule list"]
    steps = [
        "Read `last-fire.json` and the newest `schedule.tick.completed` event: is the tick job running?",
        "Run `scoutctl bootstrap doctor` to check that the schedule-tick job is loaded.",
    ]
    family = ctx.id.split(":")[0].split(".")[0]
    if family == "lease":
        steps.append("Run `scoutctl repo lease status`: who holds the writer lease and since when.")
        commands.append("scoutctl repo lease status")
    if family == "lock":
        steps.append("Read the lock skips in the ledger: which run held the lock, and for how long.")
    if family == "slot" and ctx.suffix:
        steps.append(f"Read the `{ctx.suffix}` slot's schedule entry and its newest runs in the ledger.")
        commands.append(f"scoutctl schedule show {ctx.suffix}")
    elif family == "lane" and ctx.suffix:
        steps.append(f"Find the slots in the `{ctx.suffix}` lane, their schedule entries and their newest runs.")
    steps.append("Decide whether the Mac was asleep or off, the job isn't loaded, or runs fire but fail or skip.")
    return Guide(
        title=f"Investigate: the schedule isn't running as expected ({ctx.id})",
        steps=steps,
        commands=commands,
        evidence=evidence,
    )


def _update(ctx: _Ctx) -> Guide:
    return Guide(
        title="Investigate: Scout's automatic update didn't finish",
        evidence=[f"The update ledger: `{ctx.vault / '.scout-logs' / 'update-outcomes.jsonl'}`"],
        steps=[
            "Read the newest rows of the update ledger: what was tried and how it ended.",
            "Run `scoutctl bootstrap drift` to see which vault files differ from the installed version.",
            "Look for `*.proposed-merge` sidecars waiting for a decision; list them, don't resolve them yet.",
        ],
        commands=["scoutctl bootstrap drift", "find . -maxdepth 3 -name '*.proposed-merge' -not -path './.git/*'"],
    )


def _generic(ctx: _Ctx) -> Guide:
    return Guide(
        title=f"Investigate: Scout health finding {ctx.id}",
        steps=[
            "Read the finding's summary and suggested fix above, and its log if it names one.",
            "Find the state it was computed from: the run ledger, the schedule, or the file the summary names.",
            "Work out why Scout reports it, and whether the suggested fix is the right one.",
        ],
    )


_GENERIC: Playbook = _generic
# Keyed by the id before ":", in full or by its family before the first ".".
PLAYBOOKS: dict[str, Playbook] = {
    "run.integrity_restored": _integrity,
    "run.integrity_unchecked": _integrity,
    "runs": _failures,
    "runs.unsandboxed": _sandbox,
    "runs.sandbox_env_off": _sandbox,
    "auth": _auth,
    "sandbox": _sandbox,
    "gates": _gates,
    "scheduler": _scheduler,
    "lock": _scheduler,
    "lease": _scheduler,
    "slot": _scheduler,
    "lane": _scheduler,
    "update": _update,
}


def _playbook(finding_id: str) -> Playbook:
    base = finding_id.split(":", 1)[0]
    return PLAYBOOKS.get(base) or PLAYBOOKS.get(base.split(".", 1)[0]) or _GENERIC


def for_finding(
    vault: Path, finding_id: str, *, now: datetime | None = None, home: Path | None = None
) -> ResolvePrompt:
    validate("finding", finding_id)
    now = now or datetime.now(UTC)
    report = health.evaluate(vault, now=now, home=home)
    finding = next((f for f in report.findings if f.id == finding_id), None)
    ctx = _Ctx(vault=vault, id=finding_id, finding=finding, rows=health.read_outcomes(vault))
    guide = _playbook(finding_id)(ctx)
    if finding is not None:
        seen = [
            finding.summary,
            f"Code {_code(finding.id)}, severity {finding.severity}, since {_iso(finding.since)}.",
        ]
        if finding.detail:
            seen.append(f"The error line Scout recorded: {_code(finding.detail)}")
        if finding.fix:
            seen.append(f"Scout's suggested fix: {finding.fix}")
        seen.append(f"Scout still reports this (checked {_iso(now)}).")
        done = guide.done or (
            "You have explained the cause, the owner has approved and applied any fix, and "
            f"`scoutctl health --json` no longer lists `{finding_id}`."
        )
    else:
        seen = [f"Code {_code(finding_id)}.", NOT_CURRENT_FINDING]
        done = f"You have confirmed why `{finding_id}` cleared, or found that it still happens, and told the owner."
    evidence = _dedupe(_standard_evidence(vault, finding.log if finding else None) + guide.evidence)
    commands = _dedupe(list(STANDARD_COMMANDS) + guide.commands)
    prompt = _render(title=guide.title, seen=seen, evidence=evidence, steps=guide.steps, commands=commands, done=done)
    return ResolvePrompt(
        kind="finding", key=finding_id, current=finding is not None, title=guide.title, cwd=str(vault), prompt=prompt
    )
