"""Applying what an agent proposed (#550 V4a2, spec §7.1a): ``scoutctl inbox apply`` and ``inbox revert``.

``inbox pull`` (:mod:`scout.delegate_inbox`) put each taken proposal in
``.scout-inbox/agents/<id>/`` and its HMAC in the signed record. A run can
write that folder, so apply trusts nothing there on its own:

- **Only what the record took.** A copy is applied only when its ULID is in
  the signed record and its bytes match the recorded HMAC; anything else is
  ``not_taken``. It is parsed again, strictly.
- **Only an item the delegate has seen.** The delegate's signed
  ``last_mirror`` or ``pending_mirror`` (never what a remote says) must hold
  that date's daily file with a task whose ``[#TAG]`` is the proposal's (the
  action-items parser, not a substring: a TAG in a comment doesn't count).
  The item must also still be visible now: the current daily file is redacted
  for the delegate again, and the task line must survive. Otherwise
  ``unknown_item``, the same whether hidden or missing; with no mirror commit
  at all, ``no_mirror``, retried at the next apply.
- **By id only**, through the action-items commands: ``mark-done``,
  ``snooze``, ``set-estimate``, ``set-block``, ``clear-block``.
  ``add-comment`` is ``held``: it would put the agent's text in a daily file
  that sending runs read, which spec §7.1a's observation rule forbids until
  the review queue takes it (V4b).
- **Limits.** Daily caps per op and delegate, a burst limit per apply (the
  rest are ``limited``), a snooze horizon, and ``stale`` after a week.
- **Undoable.** Each applied op records its task's lines before and after in
  the signed record, so ``inbox revert <ULID>`` restores them, provided the
  task still reads as the op left it.

Outcomes are recorded by ULID, never with the proposal's text: ``applied``,
``no_op``, ``stale``, ``unknown_item``, ``limited``, ``not_taken``,
``refused``, ``held``, ``interrupted``, ``reverted``, and ``no_mirror``
(retried). Nothing here commits the vault: the runner does (V4b).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import errno
import hmac
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scout import delegate_inbox as di
from scout import delegate_mirror as dm
from scout import delegate_proposals as dp
from scout.action_items.parser import ActionItem, parse_lines
from scout.errors import ActionItemError
from scout.repo_sync import RepoSyncError, git, headless_gate

DAILY_CAPS = {"mark-done": 10, "snooze": 10, "set-estimate": 30, "set-block": 10, "clear-block": 10}
BURST = 5  # one op from one delegate in one apply; the rest are `limited`
SNOOZE_HORIZON = dt.timedelta(days=60)
STALE_AFTER = dt.timedelta(days=7)
HELD_OPS = frozenset({"add-comment"})
RETRIED = frozenset({"no_mirror"})


@dataclass
class ApplyResult:
    delegate: str
    outcomes: dict[str, str] = field(default_factory=dict)  # ULID -> outcome, for what this apply decided
    waiting: int = 0  # observations: V4b
    notes: list[str] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for outcome in self.outcomes.values():
            out[outcome] = out.get(outcome, 0) + 1
        return dict(sorted(out.items()))

    def to_json(self) -> dict[str, Any]:
        return {**dataclasses.asdict(self), "counts": self.counts()}


def _daily_rel(date: str) -> str:
    return f"action-items/action-items-{date}.md"


def _items(text: str) -> list[ActionItem]:
    return parse_lines(text.splitlines())


def _task(text: str, tag: str) -> ActionItem | None:
    found = [i for i in _items(text) if i.short_prefix == tag]
    return found[0] if len(found) == 1 else None


def _block(lines: list[str], line_number: int) -> tuple[int, int]:
    """The task line and the indented lines under it, as ``lines[start:end]``."""
    start = line_number - 1
    end = start + 1
    while end < len(lines) and lines[end].strip() and lines[end][:1] in (" ", "\t"):
        end += 1
    return start, end


def _task_lines(path: Path, tag: str) -> list[str] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    item = _task(text, tag)
    if item is None:
        return None
    lines = text.splitlines()
    start, end = _block(lines, item.line_number)
    return lines[start:end]


def _read_copy(dir_fd: int, name: str) -> bytes | None:
    """A copy's bytes: a plain file, read without following a symlink, at most one proposal's size."""
    try:
        fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=dir_fd)
    except OSError:
        return None
    st = os.fstat(fd)  # before fdopen, which raises IsADirectoryError on a folder
    if not stat.S_ISREG(st.st_mode) or st.st_size > dp.MAX_BYTES:
        os.close(fd)
        return None
    with os.fdopen(fd, "rb") as fh:
        return fh.read(dp.MAX_BYTES + 1)


class _Seen:
    """Whether the delegate's mirror showed it a task, and whether the vault still does (computed once)."""

    def __init__(self, vault: Path, delegate: str) -> None:
        self.vault = vault
        self.delegate = delegate
        link = dm.load_links(vault).get(delegate)
        self.commits = [c for c in (link.last_mirror, link.pending_mirror) if c] if link else []
        self._scanner: dm.Scanner | None = None
        self._visible: set[str] | None = None

    def in_mirror(self, date: str, tag: str) -> bool:
        for commit in self.commits:
            shown = git(self.vault, "show", f"{commit}:{_daily_rel(date)}", check=False)
            if shown.returncode == 0 and _task(shown.stdout, tag) is not None:
                return True
        return False

    def _load(self) -> tuple[dm.Scanner, set[str]]:
        if self._scanner is None or self._visible is None:
            blobs = dm.BlobReader(self.vault)
            try:
                vis = dm.visibility(self.vault, dm._head(self.vault), self.delegate, blobs)
                self._scanner = dm.hidden_terms(self.vault, vis.entries, sorted(vis.hidden), blobs)
            finally:
                blobs.close()
            self._visible = {e.path for e, _label in vis.visible}
        return self._scanner, self._visible

    def still_visible(self, date: str, tag: str) -> bool:
        """The task as the vault has it now survives the delegate's redaction."""
        rel = _daily_rel(date)
        scanner, visible = self._load()
        if rel not in visible:
            return False
        try:
            text = (self.vault / rel).read_text(encoding="utf-8")
        except OSError:  # pragma: no cover - HEAD has the file; only a removal from the working tree races here
            return False
        redacted = dm.redact_lines(text, scanner, markdown=True, rel=rel)
        if redacted is None or _task(redacted[0], tag) is None:
            return False
        lines = text.splitlines()
        item = _task(text, tag)
        assert item is not None
        start, end = _block(lines, item.line_number)
        kept = set(redacted[0].splitlines())
        return all(line in kept for line in lines[start:end])  # its whole block, comments included


def _run_op(vault: Path, body: Mapping[str, Any]) -> None:
    from scout.action_items.mark_done import mark_done
    from scout.action_items.plan_marks import clear_block, set_block, set_estimate
    from scout.action_items.snooze import snooze

    op, tag, args = body["op"], body["tag"], body["args"]
    date = dt.date.fromisoformat(body["date"])
    common: dict[str, Any] = {"by_id": tag, "date": date, "data_dir": vault}
    if op == "mark-done":
        mark_done(undo=bool(args.get("undo", False)), **common)
    elif op == "snooze":
        snooze(until=dt.date.fromisoformat(args["until"]), **common)
    elif op == "set-estimate":
        set_estimate(minutes=args["minutes"], kind=args.get("kind"), **common)
    elif op == "set-block":
        set_block(day=args["day"], start=args["start"], end=args["end"], **common)
    elif op == "clear-block":
        clear_block(**common)
    else:  # pragma: no cover - parse admits only the ops above, and add-comment is held before this
        raise ActionItemError(f"no such op: {op}")


def _already(body: Mapping[str, Any], before: list[str]) -> bool:
    """Whether a mark-done (or its undo) would change nothing: the task is already that way."""
    if body["op"] != "mark-done":
        return False
    done = bool(re.search(r"\[[xX]\]", before[0]))
    return done != bool(body["args"].get("undo", False))


def _today_count(applied: Mapping[str, Mapping[str, Any]], op: str, today: str) -> int:
    return sum(1 for e in applied.values() if e.get("op") == op and e.get("outcome") == "applied" and
               str(e.get("at", "")).startswith(today))  # fmt: skip


def apply(vault: Path, delegate: str, *, now: dt.datetime | None = None) -> ApplyResult:
    """Apply ``delegate``'s taken action-item proposals, each at most once, in ULID order."""
    vault = vault.resolve()
    when = now or dt.datetime.now(dt.UTC)
    at = di._stamp(when)
    result = ApplyResult(delegate=delegate)
    with di.locked(vault):
        state = di.load_state(vault)
        taken = state.taken.get(delegate, {})
        applied = state.applied.setdefault(delegate, {})
        try:
            dir_fd = os.open(
                vault.joinpath(*di.INBOX_DIR, delegate), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_NOFOLLOW
            )
        except OSError as e:
            if e.errno in (errno.ENOENT,):
                return result
            raise RepoSyncError("inbox_folder_unsafe", f"refused: the copies' folder can't be opened ({e})") from e
        seen = _Seen(vault, delegate)
        burst: dict[str, int] = {}
        try:
            names = sorted(n for n in os.listdir(dir_fd) if n.endswith(".json") and dp.ULID_RE.match(n[:-5]))
            for name in names:
                ulid = name[:-5]
                prior = applied.get(ulid, {}).get("outcome")
                if prior is not None and prior not in RETRIED:
                    continue
                decided = _decide(vault, dir_fd, name, ulid, taken, applied, seen, burst, when, delegate)
                if decided is None:
                    result.waiting += 1
                    continue
                outcome, entry = decided
                if outcome == "applying":
                    applied[ulid] = {k: v for k, v in entry.items() if k != "body"} | {
                        "outcome": "interrupted",
                        "at": at,
                    }
                    di.save_state(vault, state)  # a crash during the op leaves it interrupted, never re-applied
                    outcome, entry = _apply_one(vault, entry)
                applied[ulid] = {**entry, "outcome": outcome, "at": at}
                result.outcomes[ulid] = outcome
                di.save_state(vault, state)
        finally:
            os.close(dir_fd)
    if result.waiting:
        result.notes.append(f"{result.waiting} observation(s) wait for the review queue (V4b)")
    return result


def _decide(
    vault: Path,
    dir_fd: int,
    name: str,
    ulid: str,
    taken: Mapping[str, Mapping[str, str]],
    applied: Mapping[str, Mapping[str, Any]],
    seen: _Seen,
    burst: dict[str, int],
    when: dt.datetime,
    delegate: str,
) -> tuple[str, dict[str, Any]] | None:
    """What to do with one copy: an outcome and what to record, ``("applying", …)``, or None (an observation)."""
    data = _read_copy(dir_fd, name)
    record = taken.get(ulid)
    if data is None or record is None or not hmac.compare_digest(record["mac"], di.proposal_mac(delegate, ulid, data)):
        return "not_taken", {}
    try:
        proposal = dp.parse(f"inbox/{delegate}/{name}", data, delegate)
    except dp.ProposalError:  # pragma: no cover - the pull parsed these exact bytes
        return "refused", {}
    if proposal.kind == "observation":
        return None
    body = proposal.body
    entry: dict[str, Any] = {"op": body["op"], "tag": body["tag"], "date": body["date"]}
    if when - dp.ulid_time(ulid) > STALE_AFTER:
        return "stale", entry
    if not seen.commits:
        return "no_mirror", entry
    if not seen.in_mirror(body["date"], body["tag"]):
        return "unknown_item", entry
    if body["op"] in HELD_OPS:
        return "held", entry
    if body["op"] == "snooze":
        until = dt.date.fromisoformat(body["args"]["until"])
        if until <= when.date() or until > when.date() + SNOOZE_HORIZON:
            return "refused", {**entry, "why": "snooze_horizon"}
    today = when.date().isoformat()
    cap = DAILY_CAPS[body["op"]]
    if burst.get(body["op"], 0) >= BURST or _today_count(applied, body["op"], today) >= cap:
        return "limited", entry
    if not seen.still_visible(body["date"], body["tag"]):
        return "unknown_item", entry
    burst[body["op"]] = burst.get(body["op"], 0) + 1
    path = vault / _daily_rel(body["date"])
    before = _task_lines(path, body["tag"])
    if before is None:  # pragma: no cover - still_visible just found it
        return "unknown_item", entry
    if _already(body, before):
        return "no_op", entry
    return "applying", {**entry, "body": body, "before": before}


def _apply_one(vault: Path, entry: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    body = entry.pop("body")
    path = vault / _daily_rel(body["date"])
    try:
        _run_op(vault, body)
    except ActionItemError:
        entry.pop("before", None)
        return "refused", {**entry, "why": "command"}
    after = _task_lines(path, body["tag"])
    if after is None or after == entry["before"]:
        entry.pop("before", None)
        return "no_op", entry
    return "applied", {**entry, "after": after}


# ----- revert ----------------------------------------------------------------------------------


def revert(vault: Path, ulid: str, *, env: Mapping[str, str] | None = None) -> str:
    """Put an applied op's task back as it was. The owner only. Returns the delegate."""
    env = os.environ if env is None else env
    vault = vault.resolve()
    headless_gate(env, "Reverting a delegate's change")
    if not dp.ULID_RE.match(ulid):
        raise RepoSyncError("bad_arguments", "refused: inbox revert takes a proposal's ULID")
    with di.locked(vault):
        state = di.load_state(vault)
        found = [(d, a[ulid]) for d, a in state.applied.items() if ulid in a]
        if not found or found[0][1].get("outcome") != "applied":
            raise RepoSyncError("not_applied", f"refused: {ulid} wasn't applied, so there is nothing to revert")
        delegate, entry = found[0]
        path = vault / _daily_rel(entry["date"])
        text = path.read_text(encoding="utf-8")
        item = _task(text, entry["tag"])
        lines = text.splitlines()
        if item is None or lines[slice(*_block(lines, item.line_number))] != entry["after"]:
            raise RepoSyncError(
                "revert_conflict",
                f"refused: the task [#{entry['tag']}] changed since {delegate}'s op, so it was left alone; change it "
                "by hand",
            )
        start, end = _block(lines, item.line_number)
        lines[start:end] = entry["before"]
        newline = "\r\n" if "\r\n" in text else "\n"
        tmp = path.with_name(f".{path.name}.revert.tmp")
        tmp.write_text(newline.join(lines) + (newline if text.endswith(("\n", "\r\n")) else ""), encoding="utf-8")
        os.replace(tmp, path)
        state.applied[delegate][ulid] = {
            **entry,
            "outcome": "reverted",
            "reverted_at": di._stamp(dt.datetime.now(dt.UTC)),
        }
        di.save_state(vault, state)
    return delegate


def apply_all(vault: Path, *, now: dt.datetime | None = None) -> list[ApplyResult | RepoSyncError]:
    out: list[ApplyResult | RepoSyncError] = []
    state = di.load_state(vault.resolve())
    for delegate in sorted(set(state.taken) | set(state.inboxes)):
        try:
            out.append(apply(vault, delegate, now=now))
        except RepoSyncError as e:
            e.detail.setdefault("delegate", delegate)
            out.append(e)
    return out
