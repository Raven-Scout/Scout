"""The owner's one-off title cleanup: ``scoutctl action-items retitle`` (#687, #708).

*Clean Title, Prose Body, Refs Block* caps a task title at 80 characters, but a
mature vault carries well over a hundred older titles that run past it, and a
run may shorten only a few a day: every rewording restarts that item's 60-day
stale clock (*Stale Items Auto-Archive*). This pass shortens them all at once,
on the owner's say-so, and keeps each clock:

- **Owner at the keyboard only** (:func:`owner_gate`): never in a scheduled
  run, never without a terminal, never while a run of the vault is live.
  ``--apply`` and ``--undo`` also hold the session lock from their first read
  to their commit, so no run can start in between.
- **The model proposes, the engine checks** (:func:`ask_claude`,
  :func:`problems`): the owner's own ``claude`` CLI, with API keys, base URLs
  and every provider switch taken out of its environment and no settings
  files, hooks, tools or MCP servers, so it runs on the login ``claude``
  itself holds. The items go in on stdin.
- **A dry run comes first** (:func:`plan`): it writes only a worksheet under
  ``.scout-cache/retitle/``, which the owner may hand-edit, and prints a short
  sha of it. :func:`apply` takes that sha, so it applies exactly what the
  owner reviewed.
- **Every fact is kept**: only the bold segment of the task line changes,
  byte for byte everywhere else. The original title goes, word for word, into
  the item's context note as ``former_title``.
- **The clock is kept** by a rewrite record per item in
  ``.scout-state/title-rewrites.jsonl`` (see :mod:`scout.action_items.stale`).
- **One commit** through the vault's ``scripts/git-safe-commit.sh``. The files
  are put back only while HEAD hasn't moved. Undone by :func:`undo`, which
  records its own rewrites. A plain ``git revert`` on a later day would drop
  the records and restart every clock from the pass day.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path

from scout import paths
from scout.action_items import stale
from scout.action_items.context_notes import context_note_rel
from scout.action_items.prefetched import daily_lines
from scout.action_items.titles import TITLE_MAX, list_long_titles, title_of

DEFAULT_MODEL = "sonnet"
DEFAULT_SKIP_WITHIN = 7
BATCH = 25
MODEL_TIMEOUT_S = 600
COMMIT_TIMEOUT_S = 300
MAX_BUDGET_USD = "2"
COMMIT_HELPER = Path("scripts") / "git-safe-commit.sh"
SESSION_LOCK = Path(".scout-logs") / ".scout-session.lock"

# Removed from the model's environment: a key, a token that isn't the login's, or an endpoint. Every provider
# switch (CLAUDE_CODE_USE_*) is set to "0" as well, so nothing can send the call to a billed account.
CREDENTIAL_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")

_TASK_HEAD_RE = re.compile(r"^- \[ \]\s+\[#[A-Za-z0-9]{2,8}\]\s*")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ID_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d+\b")
_STATUS_RE = re.compile(r"\b(?:MERGED|DEPLOYED|SHIPPED|CLOSED|DONE|BLOCKED|WIP)\b")
_QUOTES = '"“”‘’'
_MARKUP = "*_[]<>|\\`"
# Strings that change how the file is read: an HTML comment hides the rows after it from the task parser, and a
# dreaming marker is an instruction the next dreaming run follows.
_BANNED_STRINGS = ("<!--", "-->", "//==<<", ">>==//")


class RetitleError(Exception):
    """The pass refuses or can't go on; the CLI exits 2."""


# ----- who may run it -------------------------------------------------------------------


def _interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _live_run(vault: Path) -> bool:
    from scout.kb.live_run import commit_source_now

    return commit_source_now(vault) is not None


def owner_gate(vault: Path, env: Mapping[str, str] | None = None) -> None:
    """Refuse unless the owner is at a terminal and no run of ``vault`` is live."""
    from scout.kb.reader import is_scheduled

    if is_scheduled(os.environ if env is None else env):
        raise RetitleError("refused: this is a scheduled run (SCOUT_MODE or a non-owner SCOUT_READER); run it yourself")
    if not _interactive():
        raise RetitleError("refused: run it yourself in a terminal (stdin and stdout must be a TTY)")
    if _live_run(vault):
        raise RetitleError("refused: a Scout run of this vault is live; try again when it has finished")


@contextmanager
def session_lock(vault: Path) -> Iterator[None]:
    """Hold the vault's session lock, or refuse at once: a run holds it for its whole session."""
    from scout.scripts.bootstrap_lock import LockBusyError, acquire_lock_with_wait, release_lock

    lock = vault / SESSION_LOCK
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        acquire_lock_with_wait(lock, timeout_s=0)
    except LockBusyError as e:
        raise RetitleError("refused: a Scout session holds the vault's lock (a run is live); try again later") from e
    try:
        yield
    finally:
        release_lock(lock)


# ----- what a title may be --------------------------------------------------------------


def problems(new: str | None, old: str) -> list[str]:
    """Why ``new`` can't replace ``old`` under *Clean Title, Prose Body, Refs Block*; empty when it can."""
    if new is None:
        return ["no proposal"]
    if not new.strip():
        return ["empty"]
    out: list[str] = []
    if any(unicodedata.category(c) in ("Zl", "Zp") for c in new) or "\n" in new or "\r" in new:
        out.append("more than one line")
    if any(unicodedata.category(c).startswith("C") for c in new):
        out.append("a control or invisible character")
    if new != new.strip():
        out.append("leading or trailing space")
    if len(new) > TITLE_MAX:
        out.append(f"{len(new)} characters, over {TITLE_MAX}")
    if new == old:
        out.append("unchanged")
    if any(s in new for s in _BANNED_STRINGS):
        out.append("an HTML comment or a dreaming marker")
    if any(c in _MARKUP for c in new):
        out.append("markdown or markup characters")
    if " — " in new or " – " in new:
        out.append("a ` — ` separator")
    if _ID_RE.search(new):
        out.append("an issue id")
    if "#" in new:
        out.append("a # ref or hashtag")
    if _STATUS_RE.search(new):
        out.append("a status word")
    if stale.contains_date(new):
        out.append("a date, time or age")
    if any(c in _QUOTES for c in new):
        out.append("a quote")
    if any(unicodedata.category(c) == "So" or ord(c) >= 0x1F000 for c in new):
        out.append("an emoji or symbol")
    return out


# ----- asking the model -----------------------------------------------------------------

PROMPT = f"""You shorten task titles for one person's to-do list.

For each item, write a new title that:
- is a short natural imperative phrase: what to do, in plain words;
- is at most {TITLE_MAX} characters (aim for about 12 words or fewer);
- keeps what the task is about. The full original title is saved separately, so detail may go;
- has no ids (PROJ-1234), no #refs or hashtags, no status words (MERGED, DEPLOYED, done),
  no dates, times or ages, no quotes, no emoji, no " — " dash and no markdown or symbols.

"body" is the text already shown after the title: don't repeat it.

Reply with only a JSON object mapping each item's "tag" to its new title, nothing else.

Items:
"""

Proposer = Callable[[list[dict[str, str]]], dict[str, str]]


def model_env(base: Mapping[str, str]) -> dict[str, str]:
    """``base`` without credentials or endpoints, and with every provider switch off.

    A switch is set to "0", not removed, as in the gate probe: the process
    environment wins over any settings ``env`` that might turn it back on.
    """
    from scout.agent_runtimes.gate_probe import provider_switches

    env = {k: v for k, v in base.items() if k not in CREDENTIAL_VARS}
    env.update({k: "0" for k in provider_switches(None, [base])})
    return env


def model_argv(model: str) -> list[str]:
    """No settings files (so no user ``env``, ``apiKeyHelper`` or hooks), hooks off, no tools, no MCP servers."""
    return [
        "claude",
        "-p",
        "--model",
        model,
        "--output-format",
        "json",
        "--setting-sources",
        "",
        "--settings",
        json.dumps({"disableAllHooks": True}),
        "--tools",
        "",
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--no-session-persistence",
        "--max-budget-usd",
        MAX_BUDGET_USD,
    ]


def _json_object(text: str) -> dict[str, str]:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object in the reply")
    raw = json.loads(text[start : end + 1])
    if not isinstance(raw, dict):
        raise ValueError("the reply is not a JSON object")
    return {str(k): str(v).strip() for k, v in raw.items() if isinstance(v, str)}


def ask_claude(
    model: str = DEFAULT_MODEL,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    env: Mapping[str, str] | None = None,
) -> Proposer:
    """A proposer that asks the owner's ``claude`` CLI, one call per batch, the items on stdin."""

    def propose(items: list[dict[str, str]]) -> dict[str, str]:
        with tempfile.TemporaryDirectory(prefix="scout-retitle-") as cwd:  # no vault CLAUDE.md
            out = run(
                model_argv(model),
                input=PROMPT + json.dumps(items, ensure_ascii=False, indent=1),
                capture_output=True,
                text=True,
                timeout=MODEL_TIMEOUT_S,
                check=False,
                cwd=cwd,
                env=model_env(os.environ if env is None else env),
            )
        if out.returncode != 0:
            raise RuntimeError(f"claude exited {out.returncode}: {(out.stderr or out.stdout).strip()[:200]}")
        envelope = json.loads(out.stdout)
        if envelope.get("is_error"):
            raise RuntimeError(f"claude reported an error: {str(envelope.get('result'))[:200]}")
        return _json_object(str(envelope.get("result", "")))

    return propose


# ----- reading the vault exactly --------------------------------------------------------


def _decode(path: Path) -> str:
    """The file's text with its line endings as written (``read_text`` would turn CRLF into LF)."""
    return path.read_bytes().decode("utf-8")


def ledger_lines(text: str) -> list[str]:
    """``text`` split the way the activity ledger reads a file (``read_text().splitlines()``)."""
    return text.replace("\r\n", "\n").replace("\r", "\n").splitlines()


def _daily_text(vault: Path, today: str) -> str:
    daily_lines(vault, today)  # raises PrefetchCheckError when the file is missing
    return _decode(paths.action_items_daily_path(vault, date=_dt.date.fromisoformat(today)))


def _task_line_index(lines: Sequence[str]) -> dict[str, list[int]]:
    """Tag → the line numbers of its open top-level task rows."""
    out: dict[str, list[int]] = {}
    for block in stale.task_blocks(list(lines)):
        out.setdefault(block.tag, []).append(block.start)
    return out


def _body_of(line: str) -> str:
    head = _TASK_HEAD_RE.match(line)
    rest = line[head.end() :] if head else line
    bold = _BOLD_RE.search(rest)
    after = rest[bold.end() :] if bold else ""
    return re.sub(r"^\s*[—–]\s*", "", after).strip()


# ----- context-note frontmatter ---------------------------------------------------------


def _newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _frontmatter(text: str) -> tuple[list[str], int, int] | None:
    """(lines, first, last): the frontmatter body is ``lines[first:last]``; None when there's no closed block."""
    lines = text.split(_newline(text))
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines, 1, i
    return None


def _frontmatter_line(text: str, key: str) -> str | None:
    found = _frontmatter(text)
    if found is None:
        return None
    lines, first, last = found
    return next((line for line in lines[first:last] if line.startswith(f"{key}:")), None)


def _frontmatter_value(text: str, key: str) -> str | None:
    line = _frontmatter_line(text, key)
    if line is None:
        return None
    raw = line[len(key) + 1 :].strip()
    try:
        value = json.loads(raw)
        return value if isinstance(value, str) else raw
    except ValueError:
        return raw.strip("'\"")


def note_problem(text: str) -> str | None:
    """Why a context note's frontmatter can't be edited safely, or None.

    Only a closed block whose ``title:`` (if any) is a one-line value is
    edited: a folded or multi-line title would leave continuation lines behind.
    """
    if text.startswith("---") and _frontmatter(text) is None:
        return "its context note's frontmatter isn't closed"
    found = _frontmatter(text)
    if found is None:
        return None
    lines, first, last = found
    for i in range(first, last):
        if not lines[i].startswith("title:"):
            continue
        raw = lines[i][len("title:") :].strip()
        nxt = lines[i + 1] if i + 1 < last else ""
        if not raw or raw[0] in "|>" or nxt[:1] in (" ", "\t"):
            return "its context note's title is not a one-line value"
        if raw[0] in "\"'" and (len(raw) < 2 or raw[-1] != raw[0]):
            return "its context note's title is not a one-line value"
    return None


def _kv(key: str, value: str) -> str:
    return f"{key}: {json.dumps(value, ensure_ascii=False)}"


def _set_frontmatter(text: str, lines_by_key: Mapping[str, str | None]) -> str:
    """``text`` with each key's line replaced by the given full line, or removed for None.

    New keys go after ``title:``. With no frontmatter at all, a block is
    prepended. Callers check :func:`note_problem` first.
    """
    nl = _newline(text)
    wanted = {k: v for k, v in lines_by_key.items() if v is not None}
    found = _frontmatter(text)
    if found is None:
        return "---" + nl + "".join(v + nl for v in wanted.values()) + "---" + nl + nl + text
    lines, first, last = found
    out: list[str] = []
    placed: set[str] = set()
    for line in lines[first:last]:
        key = next((k for k in lines_by_key if line.startswith(f"{k}:")), None)
        if key is None:
            out.append(line)
        elif key in wanted and key not in placed:
            out.append(wanted[key])
            placed.add(key)
    anchor = next((i + 1 for i, line in enumerate(out) if line.startswith("title:")), len(out))
    out[anchor:anchor] = [v for k, v in wanted.items() if k not in placed]
    return nl.join(lines[:first] + out + lines[last:])


def _note_skip(vault: Path, tag: str) -> str | None:
    """Why this item's context note rules the row out, or None."""
    note = vault / context_note_rel(tag)
    if not note.exists():
        return None
    try:
        text = _decode(note)
    except (OSError, UnicodeDecodeError):
        return "its context note can't be read"
    if _frontmatter_value(text, "former_title") is not None:
        return "already shortened once (its note has former_title)"
    return note_problem(text)


# ----- the worksheet --------------------------------------------------------------------


@dataclass
class Row:
    tag: str
    section: str
    chars: int
    old_title: str
    body: str
    before: str  # the block's clock fingerprint when the row was made
    last_changed: str
    archive_due: str
    touched_today: bool
    proposed: str | None = None
    problems: list[str] = field(default_factory=list)
    skip: bool | str = False  # True: the owner's; a string: the engine's reason

    @property
    def ready(self) -> bool:
        return not self.skip and self.proposed is not None and not self.problems


@dataclass
class Worksheet:
    date: _dt.date
    rows: list[Row]
    path: Path

    @property
    def sha(self) -> str:
        """What ``--apply`` must name: the rows as they will be applied."""
        key = [[r.tag, r.old_title, r.before, r.proposed, bool(r.skip)] for r in self.rows]
        blob = json.dumps([self.date.isoformat(), key], ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:8]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"date": self.date.isoformat(), "rows": [asdict(r) for r in self.rows]}
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def worksheet_path(vault: Path, day: _dt.date) -> Path:
    return paths.cache_dir(vault) / "retitle" / f"{day.isoformat()}.json"


def load_worksheet(vault: Path, day: _dt.date) -> Worksheet | None:
    path = worksheet_path(vault, day)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        rows = [Row(**r) for r in raw["rows"]]
    except (OSError, ValueError, KeyError, TypeError) as e:
        if path.exists():
            raise RetitleError(f"cannot read the worksheet {path}: {e}") from e
        return None
    return Worksheet(date=day, rows=rows, path=path)


def plan(
    *,
    vault: Path,
    today: str,
    proposer: Proposer,
    sections: Sequence[str] = (),
    skip_within: int = DEFAULT_SKIP_WITHIN,
    refresh: bool = False,
) -> Worksheet:
    """The dry run: list, ask the model for what's missing, check every proposal, save the worksheet.

    Writes nothing but the worksheet. A row the worksheet already has (same
    tag, title and fingerprint) keeps its proposal and the owner's skip, so a
    re-run asks the model only for new rows; ``refresh`` asks again for all.
    """
    day = _dt.date.fromisoformat(today)
    lines = ledger_lines(_daily_text(vault, today))
    index = _task_line_index(lines)
    prints = stale.fingerprints(lines)
    old = load_worksheet(vault, day)
    kept = {(r.tag, r.old_title, r.before): r for r in (old.rows if old else [])}

    rows: list[Row] = []
    for item in list_long_titles(data_dir=vault, today=today).long:
        if sections and item.section not in sections:
            continue
        starts = index.get(item.tag, [])
        line = lines[starts[0]] if starts else ""
        row = Row(
            tag=item.tag,
            section=item.section,
            chars=item.chars,
            old_title=title_of(line),
            body=_body_of(line),
            before=prints.get(item.tag, ""),
            last_changed=item.last_changed.isoformat(),
            archive_due=item.archive_due.isoformat(),
            touched_today=item.last_changed == day,
        )
        prior = kept.get((row.tag, row.old_title, row.before))
        if prior is not None and not refresh:
            row.proposed = prior.proposed
        note_reason = _note_skip(vault, row.tag)
        if prior is not None and prior.skip is True:
            row.skip = True
        elif len(starts) != 1:
            row.skip = "tag used on more than one row"
        elif _BOLD_RE.search(line) is None:
            row.skip = "title isn't bold"
        elif note_reason is not None:
            row.skip = note_reason
        elif (item.archive_due - day).days <= skip_within:
            row.skip = f"archives within {skip_within} days"
        rows.append(row)

    wanted = [r for r in rows if not r.skip and r.proposed is None]
    for i in range(0, len(wanted), BATCH):
        batch = wanted[i : i + BATCH]
        try:
            got = proposer([{"tag": r.tag, "title": r.old_title, "body": r.body} for r in batch])
        except Exception as e:  # noqa: BLE001 - a failed batch is a row problem, never a crash
            for r in batch:
                r.problems = [f"no proposal: {e}"[:240]]
            continue
        for r in batch:
            r.proposed = got.get(r.tag)

    for r in rows:
        if not r.skip and not (r.problems and r.proposed is None):
            r.problems = problems(r.proposed, r.old_title)
    sheet = Worksheet(date=day, rows=rows, path=worksheet_path(vault, day))
    sheet.save()
    return sheet


# ----- committing -----------------------------------------------------------------------

Committer = Callable[[Path, str, list[str]], str]


def _git(vault: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(vault), *args], capture_output=True, text=True, timeout=30, check=False)


def _head(vault: Path) -> str | None:
    """HEAD's full SHA, or None (no repository, no commit yet, git missing)."""
    try:
        out = _git(vault, "rev-parse", "--verify", "-q", "HEAD")
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


def git_safe_commit(vault: Path, message: str, rel_paths: list[str]) -> str:
    """Commit ``rel_paths`` with the vault's own helper; the new HEAD, or ``RetitleError`` when none landed.

    Success is HEAD moving, not the helper's words: it reports ``committed
    <sha>`` on stderr, and an exit code alone can't say whether a commit
    landed before a later step failed.
    """
    before = _head(vault)
    out = subprocess.run(
        [str(vault / COMMIT_HELPER), message, *rel_paths],
        cwd=vault,
        capture_output=True,
        text=True,
        timeout=COMMIT_TIMEOUT_S,
        check=False,
    )
    after = _head(vault)
    if after is not None and after != before:
        return after[:12]
    detail = (out.stderr + out.stdout).strip()[-300:]
    raise RetitleError(f"{COMMIT_HELPER} exited {out.returncode} and made no commit: {detail}")


class _Transaction:
    """Files written so far, put back as they were if no commit lands."""

    def __init__(self, vault: Path) -> None:
        self.vault = vault
        self.saved: dict[Path, bytes | None] = {}

    def _keep(self, path: Path) -> None:
        if path not in self.saved:
            self.saved[path] = path.read_bytes() if path.exists() else None

    def write(self, path: Path, text: str) -> None:
        self._keep(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.retitle-tmp")
        tmp.write_bytes(text.encode("utf-8"))
        tmp.replace(path)

    def remove(self, path: Path) -> None:
        self._keep(path)
        path.unlink(missing_ok=True)

    def rel(self) -> list[str]:
        return sorted(str(p.relative_to(self.vault)) for p in self.saved)

    def rollback(self) -> None:
        for path, data in self.saved.items():
            if data is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(data)


def _commit(tx: _Transaction, vault: Path, message: str, commit: Committer) -> str:
    """Commit ``tx``'s files. Put them back only when HEAD didn't move, whatever went wrong (a timeout too)."""
    before = _head(vault)
    try:
        sha = commit(vault, message, tx.rel())
    except BaseException:
        if _head(vault) == before:
            tx.rollback()
        raise
    records = str(stale.rewrites_path(vault).relative_to(vault))
    landed = _git(vault, "show", "--name-only", "--format=", sha).stdout.splitlines()
    if _head(vault) is not None and records not in landed:
        raise RetitleError(
            f"committed {sha}, but without {records}: the clocks will restart unless it is committed today "
            f"(`git add -f {records}` and commit it)"
        )
    return sha


def _require_today(vault: Path, day: _dt.date) -> Path:
    today = paths._today(vault)
    if day != today:
        raise RetitleError(f"refused: only today's file ({today}) can be changed, not {day}")
    daily = paths.action_items_daily_path(vault, date=day)
    newest = max((d for d, _p in stale.daily_files(vault)), default=None)
    if not daily.exists() or newest != day:
        raise RetitleError(f"refused: {daily.name} is not the newest daily file")
    if not (vault / COMMIT_HELPER).exists():
        raise RetitleError(f"refused: {COMMIT_HELPER} is missing from the vault")
    records = str(stale.rewrites_path(vault).relative_to(vault))
    if _git(vault, "check-ignore", "-q", "--", records).returncode == 0:
        raise RetitleError(f"refused: {records} is gitignored, so its records would never be committed")
    return daily


def _forget_today_in_cache(vault: Path, day: _dt.date) -> None:
    """If the cached ledger already saw ``day``, drop it: a rebuild replays the day with its records."""
    ledger = stale.ActivityLedger.load(vault)
    if ledger.seen_through is not None and ledger.seen_through >= day:
        ledger.path.unlink(missing_ok=True)


def _with_title(line: str, new: str) -> str:
    head = _TASK_HEAD_RE.match(line)
    bold = _BOLD_RE.search(line, head.end() if head else 0)
    if bold is None:
        raise RetitleError("title isn't bold")
    return line[: bold.start()] + f"**{new}**" + line[bold.end() :]


def _rewrite_daily(
    tx: _Transaction, daily: Path, changes: Mapping[str, tuple[str, str]], day: _dt.date
) -> tuple[list[str], list[str], list[stale.Rewrite]]:
    """Swap each tag's bold title (``changes``: tag → (expected old, new)); (done, drifted, records).

    Byte for byte: only the bold span of each task line changes, and every
    line keeps its own ending.
    """
    raw = _decode(daily)
    parts = raw.splitlines(keepends=True)
    lines = ledger_lines(raw)
    if len(parts) != len(lines):
        raise RetitleError(f"{daily.name} has line breaks this pass can't keep byte for byte")
    index = _task_line_index(lines)
    before = stale.fingerprints(lines)
    done: list[str] = []
    drifted: list[str] = []
    for tag, (expected, new) in changes.items():
        starts = index.get(tag, [])
        if len(starts) != 1 or title_of(lines[starts[0]]) != expected:
            drifted.append(tag)
            continue
        i = starts[0]
        content = parts[i].splitlines()[0] if parts[i].splitlines() else ""
        parts[i] = _with_title(content, new) + parts[i][len(content) :]
        done.append(tag)
    if not done:
        return done, drifted, []
    text = "".join(parts)
    after = stale.fingerprints(ledger_lines(text))
    tx.write(daily, text)
    records = [stale.Rewrite(tag=t, on=day, before=before[t], after=after[t]) for t in done]
    return done, drifted, records


def _write_records(tx: _Transaction, vault: Path, records: list[stale.Rewrite]) -> None:
    target = stale.rewrites_path(vault)
    existing = _decode(target) if target.exists() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    tx.write(target, existing + "".join(r.to_json() + "\n" for r in records))


def _first_seen(vault: Path, tags: set[str]) -> dict[str, _dt.date]:
    out: dict[str, _dt.date] = {}
    for day, path in stale.daily_files(vault):
        if not tags - out.keys():
            break
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for block in stale.task_blocks(lines):
            if block.tag in tags:
                out.setdefault(block.tag, day)
    return out


def _created_line(day: str) -> str:
    return (
        f"Title shortened in the one-off title cleanup on {day}; `former_title` above is the original, word for word."
    )


_CREATED_LINE_RE = re.compile(r"^Title shortened in the one-off title cleanup on \d{4}-\d{2}-\d{2}; .*word for word\.$")


def _new_note(tag: str, title: str, former: str, created: _dt.date, day: _dt.date) -> str:
    return (
        f"---\ntag: {tag}\n{_kv('title', title)}\n{_kv('former_title', former)}\n"
        f"created: {created.isoformat()}\nupdated: {day.isoformat()}\n---\n\n{_created_line(day.isoformat())}\n"
    )


def _without_created_line(text: str) -> str:
    """A note the cleanup made, minus its own sentence about ``former_title`` (undo keeps the rest)."""
    nl = _newline(text)
    return nl.join(line for line in text.split(nl) if not _CREATED_LINE_RE.match(line))


# ----- applying it ----------------------------------------------------------------------


@dataclass
class ApplyResult:
    applied: list[str] = field(default_factory=list)
    drifted: list[str] = field(default_factory=list)
    commit: str | None = None


def apply(*, vault: Path, today: str, sha: str, commit: Committer = git_safe_commit) -> ApplyResult:
    """Apply the worksheet the dry run printed ``sha`` for, as one commit, holding the session lock."""
    with session_lock(vault):
        return _apply(vault, _dt.date.fromisoformat(today), sha, commit)


def _apply(vault: Path, day: _dt.date, sha: str, commit: Committer) -> ApplyResult:
    sheet = load_worksheet(vault, day)
    if sheet is None:
        raise RetitleError("no worksheet for today: run the dry run first (`retitle` without --apply)")
    if sheet.sha != sha.strip().lower():
        raise RetitleError(
            f"the worksheet is {sheet.sha}, not {sha}: it changed since that dry run; run the dry run again"
        )
    daily = _require_today(vault, day)

    prints = stale.fingerprints(ledger_lines(_decode(daily)))
    result = ApplyResult()
    changes: dict[str, tuple[str, str]] = {}
    for r in sheet.rows:
        if r.skip or problems(r.proposed, r.old_title):
            continue
        if prints.get(r.tag) != r.before or _note_skip(vault, r.tag) is not None:
            result.drifted.append(r.tag)
        else:
            changes[r.tag] = (r.old_title, r.proposed or "")
    if not changes:
        return result

    tx = _Transaction(vault)
    try:
        done, drifted, records = _rewrite_daily(tx, daily, changes, day)
        result.drifted += drifted
        if not done:
            return result
        created = _first_seen(vault, {t for t in done if not (vault / context_note_rel(t)).exists()})
        for tag in done:
            note = vault / context_note_rel(tag)
            former, new = changes[tag]
            if note.exists():
                tx.write(
                    note,
                    _set_frontmatter(
                        _decode(note), {"title": _kv("title", new), "former_title": _kv("former_title", former)}
                    ),
                )
            else:
                tx.write(note, _new_note(tag, new, former, created.get(tag, day), day))
        _write_records(tx, vault, records)
        _forget_today_in_cache(vault, day)
    except BaseException:
        tx.rollback()
        raise
    n = len(done)
    result.commit = _commit(
        tx, vault, f"action items: shorten {n} title{'s' if n != 1 else ''} (title cleanup)", commit
    )
    result.applied = done
    return result


# ----- undoing it -----------------------------------------------------------------------


@dataclass
class UndoPlan:
    restore: dict[str, tuple[str, str]] = field(default_factory=dict)  # tag → (current title, former title)
    # tag → the note's change: "delete" (the pass made it and nobody touched it since), or the `title:` line to
    # put back (None: remove it), with `former_title` removed either way.
    notes: dict[str, str | None] = field(default_factory=dict)
    changed: list[str] = field(default_factory=list)  # tags changed since the pass: left alone

    @property
    def delete_notes(self) -> list[str]:
        return [t for t, v in self.notes.items() if v == "delete"]


_DELETE = "delete"


def plan_undo(*, vault: Path, today: str, commit_sha: str) -> UndoPlan:
    """What undoing the pass committed as ``commit_sha`` would do to today's file and the notes."""
    rel = str(stale.rewrites_path(vault).relative_to(vault))
    after = _git(vault, "show", f"{commit_sha}:{rel}")
    if after.returncode != 0:
        raise RetitleError(f"{commit_sha} is not a title-cleanup commit (no {rel} in it)")
    prior = _git(vault, "show", f"{commit_sha}^:{rel}")
    parent_rows = len(prior.stdout.splitlines()) if prior.returncode == 0 else 0
    tags: list[str] = []
    for line in after.stdout.splitlines()[parent_rows:]:  # the rows this commit appended
        try:
            tag = str(json.loads(line)["tag"])
        except (ValueError, KeyError, TypeError):
            continue
        if tag not in tags:
            tags.append(tag)
    if not tags:
        raise RetitleError(f"{commit_sha} recorded no title rewrites")
    added = set(_git(vault, "show", "--format=", "--name-only", "--diff-filter=A", commit_sha).stdout.splitlines())

    lines = ledger_lines(_daily_text(vault, today))
    index = _task_line_index(lines)
    out = UndoPlan()
    for tag in tags:
        note_rel = context_note_rel(tag)
        try:
            text = _decode(vault / note_rel)
        except (OSError, UnicodeDecodeError):
            out.changed.append(tag)
            continue
        former, title = _frontmatter_value(text, "former_title"), _frontmatter_value(text, "title")
        starts = index.get(tag, [])
        if (
            former is None
            or title is None
            or note_problem(text) is not None
            or len(starts) != 1
            or title_of(lines[starts[0]]) != title
        ):
            out.changed.append(tag)
            continue
        out.restore[tag] = (title, former)
        if note_rel in added:
            as_committed = _git(vault, "show", f"{commit_sha}:{note_rel}").stdout
            out.notes[tag] = _DELETE if as_committed == text else _kv("title", former)
        else:
            parent = _git(vault, "show", f"{commit_sha}^:{note_rel}")
            out.notes[tag] = _frontmatter_line(parent.stdout, "title") if parent.returncode == 0 else None
    return out


def undo(*, vault: Path, today: str, commit_sha: str, commit: Committer = git_safe_commit) -> tuple[UndoPlan, str]:
    """Put back the titles ``commit_sha`` shortened, keeping their clocks; one commit, holding the session lock."""
    with session_lock(vault):
        return _undo(vault, today, commit_sha, commit)


def _undo(vault: Path, today: str, commit_sha: str, commit: Committer) -> tuple[UndoPlan, str]:
    day = _dt.date.fromisoformat(today)
    undo_plan = plan_undo(vault=vault, today=today, commit_sha=commit_sha)
    if not undo_plan.restore:
        raise RetitleError("nothing to undo: every item changed since the pass")
    daily = _require_today(vault, day)
    tx = _Transaction(vault)
    try:
        done, drifted, records = _rewrite_daily(tx, daily, dict(undo_plan.restore), day)
        undo_plan.changed += drifted
        for tag in drifted:
            undo_plan.restore.pop(tag, None)
            undo_plan.notes.pop(tag, None)
        if not done:
            raise RetitleError("nothing to undo: every item changed since the pass")
        for tag in done:
            note = vault / context_note_rel(tag)
            action = undo_plan.notes.get(tag)
            if action == _DELETE:
                tx.remove(note)
            else:
                text = _set_frontmatter(_decode(note), {"title": action, "former_title": None})
                tx.write(note, _without_created_line(text))
        _write_records(tx, vault, records)
        _forget_today_in_cache(vault, day)
    except BaseException:
        tx.rollback()
        raise
    n = len(done)
    sha = _commit(tx, vault, f"action items: undo the title cleanup for {n} item{'s' if n != 1 else ''}", commit)
    return undo_plan, sha
