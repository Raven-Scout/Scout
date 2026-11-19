"""The owner's one-off title cleanup: ``scoutctl action-items retitle`` (#687, #708).

*Clean Title, Prose Body, Refs Block* caps a task title at 80 characters, but a
mature vault carries well over a hundred older titles that run past it, and a
run may shorten only a few a day: every rewording restarts that item's 60-day
stale clock (*Stale Items Auto-Archive*). This pass shortens them all at once,
on the owner's say-so, and keeps each clock:

- **Owner at the keyboard only** (:func:`owner_gate`): never in a scheduled
  run, never without a terminal, never while a run of the vault is live.
- **The model proposes, the engine checks** (:func:`ask_claude`,
  :func:`problems`): the owner's own ``claude`` CLI, with every API-key
  variable taken out of its environment, so it runs on their Claude login and
  never on a paid key. No tools, no MCP servers, the items on stdin.
- **A dry run comes first** (:func:`plan`): it writes only a worksheet under
  ``.scout-cache/retitle/``, which the owner may hand-edit, and prints a short
  sha of it. :func:`apply` takes that sha, so it applies exactly what the
  owner reviewed.
- **Every fact is kept**: only the bold segment of the task line changes. The
  original title goes, word for word, into the item's context note as
  ``former_title``.
- **The clock is kept** by a rewrite record per item in
  ``.scout-state/title-rewrites.jsonl`` (see :mod:`scout.action_items.stale`).
- **One commit** through the vault's ``scripts/git-safe-commit.sh``, undone by
  :func:`undo`, which records its own rewrites. A plain ``git revert`` on a
  later day would drop the records and restart every clock from the pass day.
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
from collections.abc import Callable, Mapping, Sequence
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
MAX_BUDGET_USD = "2"
COMMIT_HELPER = Path("scripts") / "git-safe-commit.sh"

# Taken out of the model's environment, so `claude` uses the owner's login, never a key or a cloud account.
KEY_VARS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
)

_TASK_HEAD_RE = re.compile(r"^- \[ \]\s+\[#[A-Za-z0-9]{2,8}\]\s*")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ID_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d+\b")
_STATUS_RE = re.compile(r"\b(?:MERGED|DEPLOYED|SHIPPED|CLOSED|DONE|BLOCKED|WIP)\b")
_QUOTES = '"“”‘’`'


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


# ----- what a title may be --------------------------------------------------------------


def problems(new: str | None, old: str) -> list[str]:
    """Why ``new`` can't replace ``old`` under *Clean Title, Prose Body, Refs Block*; empty when it can."""
    if new is None:
        return ["no proposal"]
    out: list[str] = []
    if not new.strip():
        return ["empty"]
    if "\n" in new or "\r" in new:
        out.append("more than one line")
    if new != new.strip():
        out.append("leading or trailing space")
    if len(new) > TITLE_MAX:
        out.append(f"{len(new)} characters, over {TITLE_MAX}")
    if new == old:
        out.append("unchanged")
    if "**" in new or "[#" in new:
        out.append("markdown or a tag")
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
    if any(unicodedata.category(c) == "So" or ord(c) >= 0x1F000 or c == "️" for c in new):
        out.append("an emoji or symbol")
    return out


# ----- asking the model -----------------------------------------------------------------

PROMPT = f"""You shorten task titles for one person's to-do list.

For each item, write a new title that:
- is a short natural imperative phrase: what to do, in plain words;
- is at most {TITLE_MAX} characters (aim for about 12 words or fewer);
- keeps what the task is about. The full original title is saved separately, so detail may go;
- has no ids (PROJ-1234), no #refs or hashtags, no status words (MERGED, DEPLOYED, done),
  no dates, times or ages, no quotes, no emoji, no " — " dash and no markdown.

"body" is the text already shown after the title: don't repeat it.

Reply with only a JSON object mapping each item's "tag" to its new title, nothing else.

Items:
"""

Proposer = Callable[[list[dict[str, str]]], dict[str, str]]


def model_env(base: Mapping[str, str]) -> dict[str, str]:
    """``base`` without any variable that would send the call to a paid key or a cloud account."""
    return {k: v for k, v in base.items() if k not in KEY_VARS}


def model_argv(model: str) -> list[str]:
    return [
        "claude",
        "-p",
        "--model",
        model,
        "--output-format",
        "json",
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
        with tempfile.TemporaryDirectory(prefix="scout-retitle-") as cwd:  # no vault CLAUDE.md, hooks or settings
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


def _note_has_former_title(vault: Path, tag: str) -> bool:
    try:
        return (
            _frontmatter_value((vault / context_note_rel(tag)).read_text(encoding="utf-8"), "former_title") is not None
        )
    except (OSError, UnicodeDecodeError):
        return False


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
    lines = daily_lines(vault, today)
    index = _task_line_index(lines)
    prints = stale.fingerprints(list(lines))
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
        if prior is not None and prior.skip is True:
            row.skip = True
        elif len(starts) != 1:
            row.skip = "tag used on more than one row"
        elif _BOLD_RE.search(line) is None:
            row.skip = "title isn't bold"
        elif _note_has_former_title(vault, row.tag):
            row.skip = "already shortened once (its note has former_title)"
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


# ----- applying it ----------------------------------------------------------------------


@dataclass
class ApplyResult:
    applied: list[str] = field(default_factory=list)
    drifted: list[str] = field(default_factory=list)
    commit: str | None = None


Committer = Callable[[Path, str, list[str]], str]


def git_safe_commit(vault: Path, message: str, rel_paths: list[str]) -> str:
    """Commit ``rel_paths`` with the vault's own helper; the new SHA, or ``RetitleError``."""
    helper = vault / COMMIT_HELPER
    out = subprocess.run(
        [str(helper), message, *rel_paths], cwd=vault, capture_output=True, text=True, timeout=300, check=False
    )
    m = re.search(r"committed ([0-9a-f]{7,40})", out.stdout)
    if out.returncode != 0 or m is None:
        detail = (out.stderr or out.stdout).strip()[:300]
        raise RetitleError(f"{COMMIT_HELPER} exited {out.returncode} without a commit: {detail}")
    return m.group(1)


def _frontmatter_bounds(text: str) -> tuple[int, int] | None:
    """(first, last) line numbers of the frontmatter's body, between its ``---`` lines."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return 1, i
    return None


def _frontmatter_value(text: str, key: str) -> str | None:
    bounds = _frontmatter_bounds(text)
    if bounds is None:
        return None
    for line in text.split("\n")[bounds[0] : bounds[1]]:
        if line.startswith(f"{key}:"):
            raw = line[len(key) + 1 :].strip()
            try:
                value = json.loads(raw)
                return value if isinstance(value, str) else raw
            except ValueError:
                return raw.strip("'\"")
    return None


def _set_frontmatter(text: str, values: Mapping[str, str | None]) -> str:
    """``text`` with each key in ``values`` set (JSON-quoted) or, for None, removed."""
    rendered = {k: f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in values.items() if v is not None}
    bounds = _frontmatter_bounds(text)
    if bounds is None:
        return "---\n" + "".join(line + "\n" for line in rendered.values()) + "---\n\n" + text
    lines = text.split("\n")
    head, fm, tail = lines[: bounds[0]], lines[bounds[0] : bounds[1]], lines[bounds[1] :]
    out: list[str] = []
    placed: set[str] = set()
    for line in fm:
        key = next((k for k in values if line.startswith(f"{k}:")), None)
        if key is None:
            out.append(line)
        elif key in rendered and key not in placed:
            out.append(rendered[key])
            placed.add(key)
    anchor = next((i + 1 for i, line in enumerate(out) if line.startswith("title:")), len(out))
    out[anchor:anchor] = [rendered[k] for k in rendered if k not in placed]
    return "\n".join(head + out + tail)


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


def _new_note(tag: str, title: str, former: str, created: _dt.date, day: _dt.date) -> str:
    return (
        f"---\ntag: {tag}\ntitle: {json.dumps(title, ensure_ascii=False)}\n"
        f"former_title: {json.dumps(former, ensure_ascii=False)}\n"
        f"created: {created.isoformat()}\nupdated: {day.isoformat()}\n---\n\n"
        f"Title shortened in the one-off title cleanup on {day.isoformat()}; "
        "`former_title` above is the original, word for word.\n"
    )


def _with_title(line: str, new: str) -> str:
    head = _TASK_HEAD_RE.match(line)
    start = head.end() if head else 0
    bold = _BOLD_RE.search(line, start)
    if bold is None:
        raise RetitleError("title isn't bold")
    return line[: bold.start()] + f"**{new}**" + line[bold.end() :]


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
    return daily


class _Transaction:
    """Files written so far, restored as they were if the commit fails."""

    def __init__(self, vault: Path) -> None:
        self.vault = vault
        self.saved: dict[Path, bytes | None] = {}

    def write(self, path: Path, text: str) -> None:
        if path not in self.saved:
            self.saved[path] = path.read_bytes() if path.exists() else None
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.retitle-tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)

    def remove(self, path: Path) -> None:
        if path not in self.saved:
            self.saved[path] = path.read_bytes() if path.exists() else None
        path.unlink(missing_ok=True)

    def rel(self) -> list[str]:
        return sorted(str(p.relative_to(self.vault)) for p in self.saved)

    def rollback(self) -> None:
        for path, data in self.saved.items():
            if data is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(data)


def _forget_today_in_cache(vault: Path, day: _dt.date) -> None:
    """If the cached ledger already saw ``day``, drop it: a rebuild replays the day with its records."""
    ledger = stale.ActivityLedger.load(vault)
    if ledger.seen_through is not None and ledger.seen_through >= day:
        ledger.path.unlink(missing_ok=True)


def _rewrite_daily(
    tx: _Transaction, daily: Path, changes: Mapping[str, tuple[str, str]], day: _dt.date
) -> tuple[list[str], list[str], list[stale.Rewrite]]:
    """Swap each tag's bold title (``changes``: tag → (expected old, new)); (done, drifted, records)."""
    raw = daily.read_text(encoding="utf-8")
    lines = raw.splitlines()
    index = _task_line_index(lines)
    before = stale.fingerprints(lines)
    done: list[str] = []
    drifted: list[str] = []
    for tag, (expected, new) in changes.items():
        starts = index.get(tag, [])
        if len(starts) != 1 or title_of(lines[starts[0]]) != expected:
            drifted.append(tag)
            continue
        lines[starts[0]] = _with_title(lines[starts[0]], new)
        done.append(tag)
    if not done:
        return done, drifted, []
    after = stale.fingerprints(lines)
    newline = "\r\n" if "\r\n" in raw else "\n"
    tx.write(daily, newline.join(lines) + (newline if raw.endswith(("\n", "\r\n")) else ""))
    records = [stale.Rewrite(tag=t, on=day, before=before[t], after=after[t]) for t in done]
    return done, drifted, records


def _write_records(tx: _Transaction, vault: Path, records: list[stale.Rewrite]) -> None:
    target = stale.rewrites_path(vault)
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    tx.write(target, existing + "".join(r.to_json() + "\n" for r in records))


def apply(*, vault: Path, today: str, sha: str, commit: Committer = git_safe_commit) -> ApplyResult:
    """Apply the worksheet the dry run printed ``sha`` for, as one commit."""
    day = _dt.date.fromisoformat(today)
    sheet = load_worksheet(vault, day)
    if sheet is None:
        raise RetitleError("no worksheet for today: run the dry run first (`retitle` without --apply)")
    if sheet.sha != sha.strip().lower():
        raise RetitleError(
            f"the worksheet is {sheet.sha}, not {sha}: it changed since that dry run; run the dry run again"
        )
    daily = _require_today(vault, day)

    ready = [r for r in sheet.rows if not r.skip and not problems(r.proposed, r.old_title)]
    prints = stale.fingerprints(daily.read_text(encoding="utf-8").splitlines())
    result = ApplyResult()
    changes: dict[str, tuple[str, str]] = {}
    for r in ready:
        if prints.get(r.tag) != r.before or _note_has_former_title(vault, r.tag):
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
                text = note.read_text(encoding="utf-8")
                tx.write(note, _set_frontmatter(text, {"title": new, "former_title": former}))
            else:
                tx.write(note, _new_note(tag, new, former, created.get(tag, day), day))
        _write_records(tx, vault, records)
        _forget_today_in_cache(vault, day)
        n = len(done)
        result.commit = commit(
            vault, f"action items: shorten {n} title{'s' if n != 1 else ''} (title cleanup)", tx.rel()
        )
    except BaseException:
        tx.rollback()
        raise
    result.applied = done
    return result


# ----- undoing it -----------------------------------------------------------------------


@dataclass
class UndoPlan:
    restore: dict[str, tuple[str, str]] = field(default_factory=dict)  # tag → (current title, former title)
    delete_notes: list[str] = field(default_factory=list)  # tags whose note the pass created
    changed: list[str] = field(default_factory=list)  # tags changed since the pass: left alone


def _git(vault: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(vault), *args], capture_output=True, text=True, timeout=30, check=False)


def plan_undo(*, vault: Path, today: str, commit_sha: str) -> UndoPlan:
    """What undoing the pass committed as ``commit_sha`` would do to today's file and the notes."""
    rel = str(stale.rewrites_path(vault).relative_to(vault))
    after = _git(vault, "show", f"{commit_sha}:{rel}")
    if after.returncode != 0:
        raise RetitleError(f"{commit_sha} is not a title-cleanup commit (no {rel} in it)")
    prior = _git(vault, "show", f"{commit_sha}^:{rel}")
    before_rows = set(prior.stdout.splitlines()) if prior.returncode == 0 else set()
    tags: list[str] = []
    for line in after.stdout.splitlines():
        if line in before_rows:
            continue
        try:
            tag = str(json.loads(line)["tag"])
        except (ValueError, KeyError, TypeError):
            continue
        if tag not in tags:
            tags.append(tag)
    if not tags:
        raise RetitleError(f"{commit_sha} recorded no title rewrites")
    added = _git(vault, "show", "--format=", "--name-only", "--diff-filter=A", commit_sha).stdout.splitlines()

    lines = daily_lines(vault, today)
    index = _task_line_index(lines)
    out = UndoPlan()
    for tag in tags:
        note = vault / context_note_rel(tag)
        try:
            text = note.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            out.changed.append(tag)
            continue
        former, title = _frontmatter_value(text, "former_title"), _frontmatter_value(text, "title")
        starts = index.get(tag, [])
        if former is None or title is None or len(starts) != 1 or title_of(lines[starts[0]]) != title:
            out.changed.append(tag)
            continue
        out.restore[tag] = (title, former)
        if context_note_rel(tag) in added:
            out.delete_notes.append(tag)
    return out


def undo(*, vault: Path, today: str, commit_sha: str, commit: Committer = git_safe_commit) -> tuple[UndoPlan, str]:
    """Put back the titles ``commit_sha`` shortened, keeping their clocks; one commit."""
    day = _dt.date.fromisoformat(today)
    undo_plan = plan_undo(vault=vault, today=today, commit_sha=commit_sha)
    if not undo_plan.restore:
        raise RetitleError("nothing to undo: every item changed since the pass")
    daily = _require_today(vault, day)
    tx = _Transaction(vault)
    try:
        done, drifted, records = _rewrite_daily(
            tx, daily, {t: (cur, former) for t, (cur, former) in undo_plan.restore.items()}, day
        )
        undo_plan.changed += drifted
        for tag in drifted:
            undo_plan.restore.pop(tag, None)
        for tag in done:
            note = vault / context_note_rel(tag)
            if tag in undo_plan.delete_notes:
                tx.remove(note)
            else:
                text = note.read_text(encoding="utf-8")
                tx.write(note, _set_frontmatter(text, {"title": undo_plan.restore[tag][1], "former_title": None}))
        if not done:
            raise RetitleError("nothing to undo: every item changed since the pass")
        _write_records(tx, vault, records)
        _forget_today_in_cache(vault, day)
        n = len(done)
        sha = commit(vault, f"action items: undo the title cleanup for {n} item{'s' if n != 1 else ''}", tx.rel())
    except BaseException:
        tx.rollback()
        raise
    return undo_plan, sha


__all__ = [
    "DEFAULT_MODEL",
    "DEFAULT_SKIP_WITHIN",
    "ApplyResult",
    "RetitleError",
    "Row",
    "UndoPlan",
    "Worksheet",
    "apply",
    "ask_claude",
    "owner_gate",
    "plan",
    "plan_undo",
    "problems",
    "undo",
]
