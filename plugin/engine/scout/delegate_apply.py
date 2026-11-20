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

- **The owner's, between runs.** Apply and revert refuse in a scheduled run
  or as any reader but the owner (``headless_gate``), and hold the vault's
  session lock (refusing at once while a run is live) as well as the inbox
  lock, so no run or other writer edits a daily file under them.
- **A plain, committed daily file.** The day's file must be a regular file
  in a real ``action-items/`` folder, never a symlink, and the same as HEAD
  has it; one with uncommitted edits waits (``dirty``, retried). The task's
  visibility is judged on HEAD's text, the same snapshot as the hidden-note
  scan.
- **One commit per change**, made at once through the vault's
  ``scripts/git-safe-commit.sh`` with a message naming the delegate, so the
  next run's commit never sweeps it in as the run's own. If no commit lands
  and HEAD hasn't moved, the files are put back and the proposal is tried
  again at the next apply.

Outcomes are recorded by ULID, never with the proposal's text: ``applied``,
``no_op``, ``stale``, ``unknown_item``, ``limited`` (tried again on a later
day), ``not_taken`` and ``no_mirror`` and ``dirty`` (tried again),
``refused``, ``held``, ``interrupted``, and ``reverted``. A copy that fails
in any unexpected way is ``refused`` (``why: error``); one delegate's failure
never stops another's.
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime as dt
import errno
import hmac
import os
import re
import stat
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping
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
BLOCK_HORIZON = dt.timedelta(days=60)
RETRIED = frozenset({"no_mirror", "not_taken", "dirty"})  # and `limited`, on a later day
APPLIED_KEPT = dt.timedelta(days=90)  # how long the signed record keeps what an op changed (for revert)
Committer = Callable[[Path, str, list[str]], str]  # (vault, message, paths) -> the new HEAD


def _today(vault: Path) -> str:
    """Today in the vault's configured zone, as its daily files name it (``paths._today``, as retitle uses)."""
    from scout import paths

    return paths._today(vault).isoformat()


def _event(vault: Path, payload: dict[str, Any], delegate: str) -> None:
    """A telemetry row for an applied change (ids only). ``.scout-logs`` is run-writable, so it proves nothing;
    the signed state and the commit do. Best effort."""
    try:
        from scout import paths
        from scout.events import append_event

        append_event(
            paths.logs_dir(vault), kind="delegate_inbox.applied", source=f"delegate:{delegate}", payload=payload
        )
    except Exception:  # pragma: no cover - an event log that can't be written never stops an apply
        pass


@dataclass
class ApplyResult:
    delegate: str
    outcomes: dict[str, str] = field(default_factory=dict)  # ULID -> outcome, for what this apply decided
    waiting: int = 0  # observations: for the owner's review (inbox review, V4b2)
    untaken: int = 0  # copies with no record at all: skipped, nothing recorded
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
    except (OSError, ValueError):  # missing, or not UTF-8 (a run can write one)
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
        """The task as HEAD has it survives the delegate's redaction: the same snapshot as the visibility and
        hidden-note scan (the working tree's file was just checked to be the same as HEAD's)."""
        rel = _daily_rel(date)
        scanner, visible = self._load()
        if rel not in visible:
            return False
        shown = git(self.vault, "show", f"HEAD:{rel}", check=False)
        if shown.returncode != 0:  # pragma: no cover - visible means HEAD has it
            return False
        text = shown.stdout
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


# ----- the vault: one plain, committed file; one commit per change -------------------------------


def _default_commit(vault: Path, message: str, rel_paths: list[str]) -> str:
    """Commit through the vault's own ``scripts/git-safe-commit.sh`` (as #708's retitle does)."""
    from scout.action_items import retitle

    try:
        return retitle.git_safe_commit(vault, message, rel_paths)
    except (retitle.RetitleError, OSError) as e:
        raise RepoSyncError("commit_failed", f"refused: no commit landed ({e})") from e


@contextlib.contextmanager
def _session(vault: Path) -> Iterator[None]:
    """Hold the vault's session lock, refusing at once (``busy``) while a run is live."""
    from scout.action_items import retitle

    lock = retitle.session_lock(vault)
    try:
        lock.__enter__()
    except retitle.RetitleError as e:
        raise RepoSyncError(
            "busy", "refused: a Scout run of this vault is live; inbox apply and revert wait until it has finished"
        ) from e
    try:
        yield
    finally:
        lock.__exit__(None, None, None)


def _head(vault: Path) -> str | None:
    out = git(vault, "rev-parse", "-q", "--verify", "HEAD", check=False)
    return out.stdout.strip() or None if out.returncode == 0 else None


def _plain(vault: Path, rel: str) -> bool:
    """Whether ``rel`` is a regular, singly linked file in a real folder: no symlink at any step."""
    here = vault
    for part in rel.split("/")[:-1]:
        here = here / part
        try:
            if not stat.S_ISDIR(here.lstat().st_mode):
                return False
        except OSError:
            return False
    try:
        st = (vault / rel).lstat()
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode) and st.st_nlink == 1


def _clean(vault: Path, rel: str) -> bool:
    """Whether the working tree's ``rel`` is HEAD's: tracked, and no edit since."""
    tracked = git(vault, "ls-files", "--error-unmatch", "--", rel, check=False).returncode == 0
    return tracked and git(vault, "diff", "--quiet", "HEAD", "--", rel, check=False).returncode == 0


def _write_bytes(path: Path, data: bytes) -> None:
    """Replace ``path`` whole: written to a new mkstemp file beside it, then renamed over it, so nothing a run
    planted (a symlink at a fixed temporary name, or at the path itself) is ever written through."""
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:  # pragma: no cover - a full disk: leave nothing half-written
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _read_plain(vault: Path, rel: str) -> bytes | None:
    """``rel``'s bytes when it is a plain file (:func:`_plain`), else None: never read through a link."""
    return (vault / rel).read_bytes() if _plain(vault, rel) else None


def _snapshot(vault: Path, rels: list[str]) -> dict[Path, bytes | None]:
    return {vault / r: _read_plain(vault, r) for r in rels}


def _changed(vault: Path, saved: Mapping[Path, bytes | None]) -> list[str]:
    return sorted(
        str(p.relative_to(vault)) for p, data in saved.items() if _read_plain(vault, str(p.relative_to(vault))) != data
    )


def _commit_change(vault: Path, message: str, saved: Mapping[Path, bytes | None], commit: Committer) -> str | None:
    """Commit what changed since ``saved``; the new HEAD (None: nothing changed).

    If no commit lands and HEAD hasn't moved, the paths are unstaged, and the files are put back only if each
    still holds what the op wrote (``commit_failed``); one edited again meanwhile (the app, an editor) leaves
    everything as it is (``commit_unclear``). If HEAD moved some other way, ``commit_unclear``, nothing put back.
    """
    rels = _changed(vault, saved)
    if not rels:
        return None
    post = {vault / r: _read_plain(vault, r) for r in rels}  # what the op left: restored only if still so
    before = _head(vault)
    try:
        return commit(vault, message, rels)
    except BaseException as e:
        after = _head(vault)
        if after == before:
            git(vault, "reset", "-q", "--", *rels, check=False)  # unstage: the next run must not commit it as its own
            if any(_read_plain(vault, str(p.relative_to(vault))) != data for p, data in post.items()):
                raise RepoSyncError(
                    "commit_unclear",
                    f"refused: no commit landed ({e}), and {', '.join(rels)} changed again since the op; left as it "
                    "is for you to check (git diff)",
                ) from e
            for path in post:
                data = saved[path]
                if data is None:
                    path.unlink(missing_ok=True)
                else:
                    _write_bytes(path, data)
            raise RepoSyncError("commit_failed", f"refused: no commit landed ({e}); the change was put back") from e
        raise RepoSyncError(
            "commit_unclear",
            f"refused: HEAD moved to {after} but the commit didn't report ({e}); check git log -1 and git status",
        ) from e


ID_MAP_REL = ".scout-state/id-map.json"  # resolve_target registers a prefix it hasn't seen here


# ----- apply -----------------------------------------------------------------------------------


def _prune(applied: dict[str, dict[str, Any]], when: dt.datetime) -> None:
    horizon = di._stamp(when - APPLIED_KEPT)
    for ulid in [u for u, e in applied.items() if di._STAMP_RE.match(str(e.get("at", ""))) and e["at"] < horizon]:
        del applied[ulid]


def _retry(entry: Mapping[str, Any] | None, today: str) -> bool:
    if entry is None:
        return True
    outcome = entry.get("outcome")
    return outcome in RETRIED or (outcome == "limited" and not str(entry.get("at", "")).startswith(today))


def apply(
    vault: Path,
    delegate: str,
    *,
    now: dt.datetime | None = None,
    env: Mapping[str, str] | None = None,
    commit: Committer | None = None,
    deadline: float | None = None,
) -> ApplyResult:
    """Apply ``delegate``'s taken action-item proposals, each at most once, in ULID order, one commit each.

    With ``deadline`` (a ``time.monotonic()`` value), no new copy is started after it: the rest wait for the next
    apply, so the session lock is never held long (``inbox sync``).

    The owner's, between runs: refused in a scheduled run or as another reader, and while a run holds the
    vault's session lock. The runner's V4b step must run it outside any run, without SCOUT_MODE, as the owner.
    """
    env = os.environ if env is None else env
    headless_gate(env, "Applying a delegate's proposals (the runner does it between runs)")
    commit = commit or _default_commit
    vault = vault.resolve()
    when = now or dt.datetime.now(dt.UTC)
    at = di._stamp(when)
    today = when.date().isoformat()
    result = ApplyResult(delegate=delegate)
    with _session(vault), di.locked(vault):
        state = di.load_state(vault)
        taken = state.taken.get(delegate, {})
        applied = state.applied.setdefault(delegate, {})
        _prune(applied, when)
        try:
            dir_fd = os.open(
                vault.joinpath(*di.INBOX_DIR, delegate), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_NOFOLLOW
            )
        except OSError as e:
            if e.errno in (errno.ENOENT,):
                di.save_state(vault, state)
                return result
            raise RepoSyncError("inbox_folder_unsafe", f"refused: the copies' folder can't be opened ({e})") from e
        seen = _Seen(vault, delegate)
        burst: dict[str, int] = {}
        try:
            names = sorted(n for n in os.listdir(dir_fd) if n.endswith(".json") and dp.ULID_RE.match(n[:-5]))
            changed = False
            for name in names:
                if deadline is not None and time.monotonic() >= deadline:
                    result.notes.append("out of time for this apply; the rest wait for the next one")
                    break
                ulid = name[:-5]
                if ulid not in taken:  # never taken: counted, nothing recorded (a run can plant any number)
                    result.untaken += 1
                    continue
                if not _retry(applied.get(ulid), today):
                    continue
                try:
                    decided = _decide(vault, dir_fd, name, ulid, taken, applied, seen, burst, when, delegate)
                except Exception:  # noqa: BLE001 - whatever one copy does, it is refused, never a stop
                    decided = ("refused", {"why": "error"})
                if decided is None:
                    result.waiting += 1
                    continue
                outcome, entry = decided
                applying = outcome == "applying"
                if applying:
                    # The op's own arguments go with it (dates, times, minutes: never free text), so a sync can
                    # tell later whether an uncommitted change is exactly this op's (delegate_inbox_sync.settle).
                    applied[ulid] = {k: v for k, v in entry.items() if k != "body"} | {
                        "args": entry["body"]["args"],
                        "outcome": "interrupted",
                        "at": at,
                    }
                    di.save_state(vault, state)  # a crash during the op leaves it interrupted, never re-applied
                    outcome, entry = _apply_one(vault, entry, delegate, ulid, commit, state, applied)
                applied[ulid] = {**entry, "outcome": outcome, "at": at}
                result.outcomes[ulid] = outcome
                if applying:
                    di.save_state(vault, state)  # an applied change is recorded at once, with its commit
                else:
                    changed = True  # the rest are saved once, at the end
            if changed:
                di.save_state(vault, state)
        finally:
            os.close(dir_fd)
    if result.waiting:
        result.notes.append(f"{result.waiting} observation(s) wait for your review: scoutctl inbox review")
    if result.untaken:
        result.notes.append(f"{result.untaken} cop(ies) in the copies folder were never taken by a pull; ignored")
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
    if body["date"] != _today(vault):
        # Only today's daily file: consolidation carries open items forward into it, so a change to an older
        # file would be lost, or applied twice once the item is carried.
        return "stale", {**entry, "why": "not_today"}
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
    if body["op"] == "set-block":
        day = dt.date.fromisoformat(body["args"]["day"])
        if day < when.date() or day > when.date() + BLOCK_HORIZON:
            return "refused", {**entry, "why": "block_horizon"}
    today = when.date().isoformat()
    cap = DAILY_CAPS[body["op"]]
    if burst.get(body["op"], 0) >= BURST or _today_count(applied, body["op"], today) >= cap:
        return "limited", entry
    rel = _daily_rel(body["date"])
    if not _plain(vault, rel):
        return "unknown_item", entry  # missing, or a symlink or a hard link a run planted: never followed
    if not _clean(vault, rel):
        return "dirty", entry  # an edit not committed yet: tried again once it is
    if not seen.still_visible(body["date"], body["tag"]):
        return "unknown_item", entry
    burst[body["op"]] = burst.get(body["op"], 0) + 1
    before = _task_lines(vault / rel, body["tag"])
    if before is None:  # pragma: no cover - still_visible just found it
        return "unknown_item", entry
    if _already(body, before):
        return "no_op", entry
    return "applying", {**entry, "body": body, "before": before}


def _apply_one(
    vault: Path,
    entry: dict[str, Any],
    delegate: str,
    ulid: str,
    commit: Committer,
    state: di.InboxState,
    applied: dict[str, dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    body = entry.pop("body")
    rel = _daily_rel(body["date"])
    saved = _snapshot(vault, [rel, ID_MAP_REL])
    try:
        _run_op(vault, body)
    except ActionItemError:
        entry.pop("before", None)
        return "refused", {**entry, "why": "command"}
    except Exception:  # noqa: BLE001 - left `interrupted`: the op may have half-run, so never again
        entry.pop("before", None)
        return "interrupted", {**entry, "why": "error"}
    after = _task_lines(vault / rel, body["tag"])
    if after is None or after == entry["before"]:
        entry.pop("before", None)
        return "no_op", entry
    message = f"inbox: {body['op']} [#{body['tag']}] from delegate-{delegate} ({ulid})"
    try:
        sha = _commit_change(vault, message, saved, commit)
    except RepoSyncError as e:
        if e.code == "commit_failed":  # put back: the proposal is tried again at the next apply
            applied.pop(ulid, None)
            di.save_state(vault, state)
        raise
    _event(vault, {"ulid": ulid, "op": body["op"], "tag": body["tag"], "date": body["date"], "commit": sha}, delegate)
    return "applied", {**entry, "after": after, "commit": sha}


# ----- revert ----------------------------------------------------------------------------------


def revert(vault: Path, ulid: str, *, env: Mapping[str, str] | None = None, commit: Committer | None = None) -> str:
    """Put an applied op's task back as it was, in a commit of its own. The owner only. Returns the delegate.

    It also undoes what the owner accepted in ``inbox review`` (V4b2): a comment, the same way, and an
    observation, by removing its note while it is unchanged and not promoted."""
    env = os.environ if env is None else env
    vault = vault.resolve()
    headless_gate(env, "Reverting a delegate's change")
    commit = commit or _default_commit
    if not dp.ULID_RE.match(ulid):
        raise RepoSyncError("bad_arguments", "refused: inbox revert takes a proposal's ULID")
    with _session(vault), di.locked(vault):
        state = di.load_state(vault)
        found = [(d, a[ulid]) for d, a in state.applied.items() if ulid in a]
        if len(found) > 1:
            raise RepoSyncError(
                "ambiguous", f"refused: {ulid} is on record for {len(found)} delegates; fix the state by hand"
            )
        if not found or found[0][1].get("outcome") not in ("applied", "accepted"):
            raise RepoSyncError("not_applied", f"refused: {ulid} wasn't applied, so there is nothing to revert")
        delegate, entry = found[0]
        if entry.get("kind") == "observation":
            from scout.delegate_review import revert_observation

            revert_observation(vault, delegate, ulid, entry, state, commit)
            return delegate
        rel = _daily_rel(entry["date"])
        path = vault / rel
        if not _plain(vault, rel):
            raise RepoSyncError("revert_conflict", f"refused: {rel} isn't a plain file any more; left alone")
        if not _clean(vault, rel):
            raise RepoSyncError(
                "revert_dirty", f"refused: {rel} has edits not committed yet; commit or discard them, then revert"
            )
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
        saved = _snapshot(vault, [rel])
        _write_bytes(path, (newline.join(lines) + (newline if text.endswith(("\n", "\r\n")) else "")).encode())
        message = f"inbox: revert {entry['op']} [#{entry['tag']}] from delegate-{delegate} ({ulid})"
        sha = _commit_change(vault, message, saved, commit)
        state.applied[delegate][ulid] = {
            **entry,
            "outcome": "reverted",
            "reverted_at": di._stamp(dt.datetime.now(dt.UTC)),
            "revert_commit": sha,
        }
        di.save_state(vault, state)
    return delegate


def apply_all(
    vault: Path,
    *,
    now: dt.datetime | None = None,
    env: Mapping[str, str] | None = None,
    commit: Committer | None = None,
    deadline: float | None = None,
) -> list[ApplyResult | RepoSyncError]:
    """Apply every delegate's proposals; one failing, however it fails, doesn't stop the rest."""
    out: list[ApplyResult | RepoSyncError] = []
    state = di.load_state(vault.resolve())
    for delegate in sorted(set(state.taken) | set(state.inboxes)):
        try:
            out.append(apply(vault, delegate, now=now, env=env, commit=commit, deadline=deadline))
        except RepoSyncError as e:
            e.detail.setdefault("delegate", delegate)
            out.append(e)
        except Exception as e:  # noqa: BLE001 - one delegate's failure is reported, never a stop for the others
            failure = RepoSyncError("apply_failed", f"refused: {delegate}'s apply failed ({type(e).__name__})")
            failure.detail["delegate"] = delegate
            out.append(failure)
    return out
