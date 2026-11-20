"""The owner's review of what agents proposed (#550 V4b2, spec §7.1b): ``scoutctl inbox review``.

``inbox apply`` changes action items by id only and never writes an agent's text. Text waits here, for the
owner: an observation (``kind: observation``), and a held ``add-comment``. ``limited`` ops (over a daily cap)
are listed too, while still within a week, so the owner can reject one.

- **list** (``inbox review``) shows each item with its ULID, delegate, kind, op, ``[#TAG]`` and date, and its
  text with every character outside printable ASCII and letters shown as ``\\u{…}``, plus any word that mixes
  scripts or stands in another script than the text around it (a look-alike). Only a copy whose bytes match
  the signed record's HMAC is listed, parsed again strictly.
- **accept** asks for the ULID again (typed at a terminal, or ``--confirm <ULID>``), re-runs every text rule on
  the copy's exact bytes, and writes:

  - a comment through ``add_comment``, author ``delegate-<id>``, with apply's checks (today's file, seen in the
    delegate's mirror, still visible to it, a plain committed file), in one commit; ``inbox revert`` undoes it;
  - an observation as ``knowledge-base/from-agents/<id>/<ULID>.md``: frontmatter from ``yaml.safe_dump``
    (``title``, ``source: delegate:<id>``, ``visibility: private``, ``accepted_at``, ``agent_source``), a line
    saying whose text it is, and the text as a block quote, in one commit. It stays private and hidden from
    every delegate, its author included (``Label.delegate_source``). ``inbox revert`` removes it while
    unchanged.

  After accepting, runs read the text. That is the one way an agent's text reaches a run, and only because the
  owner reviewed it.
- **reject** records ``rejected`` and removes the local copy.
- **promote** (an accepted observation only) re-reads the note, runs the text rules on it, shows it, asks
  again, and rewrites its provenance: ``source: delegate:<id>`` becomes ``promoted_from: delegate:<id>`` and
  ``visibility`` becomes ``normal``, so the note reaches delegate mirrors. ``--to`` limits it to named
  delegates (``delegate_scope``; ``author`` names its author). **unpromote** puts the note back as it was,
  only while it is unchanged since the promotion.

Every step is the owner's, between runs (``headless_gate``), under the vault's session lock and the inbox
lock, and is recorded by ULID in the signed state; never with the text.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import errno
import hashlib
import hmac
import os
import re
import stat
import textwrap
import unicodedata
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from scout import delegate_apply as da
from scout import delegate_inbox as di
from scout import delegate_proposals as dp
from scout.kb.privacy import FROM_AGENTS
from scout.kb.reader import is_delegate_id
from scout.repo_sync import RepoSyncError, headless_gate

FROM_AGENTS_PARTS = tuple(FROM_AGENTS.strip("/").split("/"))  # ("knowledge-base", "from-agents")
REVIEW_WINDOW = da.STALE_AFTER  # a limited op is listed while it could still be applied
WAIT_WARNING = dt.timedelta(days=7)  # health: an item waiting longer than this
REVIEWED = frozenset({"accepted", "rejected", "reverted"})
WRAP = 400  # body lines are wrapped below kb-lint's strict line limit
_LETTERS = re.compile(r"[^\W\d_]+")


# ----- showing text --------------------------------------------------------------------------------


def shown(text: str) -> str:
    """``text`` with every character outside printable ASCII and letters as ``\\u{XXXX}`` (a newline kept)."""
    out: list[str] = []
    for ch in text:
        if ch == "\\":
            out.append("\\\\")
        elif ch == "\n" or " " <= ch <= "~" or unicodedata.category(ch).startswith("L"):
            out.append(ch)
        else:
            out.append(f"\\u{{{ord(ch):04X}}}")
    return "".join(out)


def _script(ch: str) -> str:
    name = unicodedata.name(ch, "")
    return name.split(" ", 1)[0] if name else "UNKNOWN"


def look_alikes(text: str) -> list[str]:
    """Words that mix scripts (``pаypal`` with a Cyrillic ``а``), or stand wholly in another script than most of
    the text's letters: the ways a word can pass for another. Each once, in order."""
    words = _LETTERS.findall(text)
    counts = Counter(_script(ch) for w in words for ch in w)
    main = counts.most_common(1)[0][0] if counts else None
    out: list[str] = []
    for word in words:
        scripts = {_script(ch) for ch in word}
        if (len(scripts) > 1 or (main is not None and scripts != {main})) and word not in out:
            out.append(word)
    return out


# ----- reading what waits --------------------------------------------------------------------------


@dataclass
class Item:
    delegate: str
    ulid: str
    kind: str  # observation | comment | op
    status: str  # waiting | held | limited
    proposed: str  # the ULID's own time
    made_at: str  # the agent's claim
    op: str | None = None
    tag: str | None = None
    date: str | None = None
    text: str = ""  # shown(): hidden characters made visible
    about: str | None = None
    source: str | None = None
    look_alikes: list[str] = field(default_factory=list)
    sha256: str | None = None  # promote's preview: the note's bytes it shows, which promote must still find

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@contextlib.contextmanager
def _copies(vault: Path, delegate: str) -> Iterator[int | None]:
    """The delegate's copies folder, opened without following a link (None when there is none)."""
    try:
        fd = os.open(
            vault.joinpath(*di.INBOX_DIR, delegate), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_NOFOLLOW
        )
    except OSError as e:
        if e.errno == errno.ENOENT:
            yield None
            return
        raise RepoSyncError("inbox_folder_unsafe", f"refused: the copies' folder can't be opened ({e})") from e
    try:
        yield fd
    finally:
        os.close(fd)


def _verified(dir_fd: int, delegate: str, ulid: str, taken: Mapping[str, Mapping[str, str]]) -> dp.Proposal | None:
    """The copy of ``ulid`` when its bytes are what the pull recorded, parsed again strictly; else None."""
    data = da._read_copy(dir_fd, f"{ulid}.json")
    record = taken.get(ulid)
    if data is None or record is None or not hmac.compare_digest(record["mac"], di.proposal_mac(delegate, ulid, data)):
        return None
    try:
        return dp.parse(f"inbox/{delegate}/{ulid}.json", data, delegate)
    except dp.ProposalError:
        return None


def _status(proposal: dp.Proposal, entry: Mapping[str, Any] | None, when: dt.datetime) -> str | None:
    outcome = entry.get("outcome") if entry else None
    if proposal.kind == "observation":
        return "waiting" if outcome is None else None
    if outcome == "held":
        return "held"
    if outcome == "limited" and when - dp.ulid_time(proposal.id) <= REVIEW_WINDOW:
        return "limited"
    return None


def _item(proposal: dp.Proposal, status: str) -> Item:
    body = proposal.body
    raw = body.get("text") or body.get("args", {}).get("comment") or ""
    item = Item(
        delegate=proposal.delegate,
        ulid=proposal.id,
        kind="observation" if proposal.kind == "observation" else "comment" if body["op"] == "add-comment" else "op",
        status=status,
        proposed=di._stamp(dp.ulid_time(proposal.id)),
        made_at=proposal.made_at,
        op=body.get("op"),
        tag=body.get("tag"),
        date=body.get("date"),
        text=shown(raw),
        about=shown(body["about"]) if "about" in body else None,
        source=shown(body["source"]) if "source" in body else None,
    )
    item.look_alikes = look_alikes(" ".join(v for v in (raw, body.get("about"), body.get("source")) if v))
    return item


def review(
    vault: Path, *, delegate: str | None = None, now: dt.datetime | None = None, env: Mapping[str, str] | None = None
) -> list[Item]:
    """What waits for the owner, oldest first. The owner only: the text is an agent's."""
    headless_gate(os.environ if env is None else env, "Reviewing what agents proposed")
    vault = vault.resolve()
    when = now or dt.datetime.now(dt.UTC)
    items: list[Item] = []
    with di.locked(vault):
        state = di.load_state(vault)
        for who in sorted(state.taken):
            if delegate is not None and who != delegate:
                continue
            taken, applied = state.taken[who], state.applied.get(who, {})
            with _copies(vault, who) as dir_fd:
                if dir_fd is None:
                    continue
                for ulid in sorted(taken):
                    status = None
                    proposal = _verified(dir_fd, who, ulid, taken)
                    if proposal is not None:
                        status = _status(proposal, applied.get(ulid), when)
                    if proposal is not None and status is not None:
                        items.append(_item(proposal, status))
    return sorted(items, key=lambda i: i.ulid)


def waiting_reviews(state: di.InboxState, delegate: str) -> list[str]:
    """ULIDs taken from ``delegate`` that wait for the owner (no outcome yet, or held), from the state alone."""
    applied = state.applied.get(delegate, {})
    return sorted(u for u in state.taken.get(delegate, {}) if applied.get(u, {}).get("outcome") in (None, "held"))


# ----- deciding --------------------------------------------------------------------------------


def _confirmed(ulid: str, confirm: str | None, what: str) -> None:
    if not dp.ULID_RE.match(ulid):
        raise RepoSyncError("bad_arguments", f"refused: inbox review {what} takes a proposal's ULID")
    if confirm is None or not hmac.compare_digest(confirm.strip(), ulid):
        raise RepoSyncError(
            "not_confirmed",
            f"refused: to {what} {ulid}, type its ULID again (or pass --confirm {ulid}); nothing was changed",
        )


def _owner_of(state: di.InboxState, ulid: str) -> str:
    found = [d for d, t in state.taken.items() if ulid in t]
    if len(found) != 1:
        raise RepoSyncError(
            "not_found" if not found else "ambiguous",
            f"refused: {ulid} isn't a proposal any pull took"
            if not found
            else f"refused: {ulid} is on record for {len(found)} delegates; fix the state by hand",
        )
    return found[0]


def _remove_copy(dir_fd: int, ulid: str) -> None:
    with contextlib.suppress(FileNotFoundError):
        os.unlink(f"{ulid}.json", dir_fd=dir_fd)


@dataclass
class Decision:
    delegate: str
    ulid: str
    outcome: str
    commit: str | None = None
    note: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def accept(
    vault: Path,
    ulid: str,
    *,
    confirm: str | None,
    now: dt.datetime | None = None,
    env: Mapping[str, str] | None = None,
    commit: da.Committer | None = None,
) -> Decision:
    """Write an observation or a held comment into the vault, after the owner typed its ULID again."""
    env = os.environ if env is None else env
    headless_gate(env, "Accepting an agent's proposal")
    _confirmed(ulid, confirm, "accept")
    commit = commit or da._default_commit
    vault = vault.resolve()
    when = now or dt.datetime.now(dt.UTC)
    with da._session(vault), di.locked(vault):
        state = di.load_state(vault)
        delegate = _owner_of(state, ulid)
        applied = state.applied.setdefault(delegate, {})
        with _copies(vault, delegate) as dir_fd:
            proposal = _verified(dir_fd, delegate, ulid, state.taken[delegate]) if dir_fd is not None else None
            if proposal is None or dir_fd is None:
                raise RepoSyncError(
                    "not_taken", f"refused: {ulid}'s copy is missing or isn't what the pull took; nothing was written"
                )
            status = _status(proposal, applied.get(ulid), when)
            if status not in ("waiting", "held"):
                why = status or applied.get(ulid, {}).get("outcome")
                raise RepoSyncError("not_reviewable", f"refused: {ulid} doesn't wait for you to accept it ({why})")
            if proposal.kind == "observation":
                decision = _accept_observation(vault, proposal, when, commit, state, applied)
            else:
                decision = _accept_comment(vault, proposal, when, commit, state, applied)
            _remove_copy(dir_fd, ulid)
        return decision


def _plain_dirs(vault: Path, parts: Sequence[str]) -> Path:
    """Make each folder of ``parts`` under the vault, refusing a link or a file at any step."""
    here = vault
    for part in parts:
        here = here / part
        with contextlib.suppress(FileExistsError):
            here.mkdir(mode=0o755)
        if not stat.S_ISDIR(here.lstat().st_mode):
            raise RepoSyncError("note_unsafe", f"refused: {here.relative_to(vault)} isn't a plain folder; left alone")
    return here


def _quoted(text: str) -> str:
    lines: list[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        wrapped = [line] if len(line) <= WRAP else textwrap.wrap(line, WRAP, break_on_hyphens=False)
        lines += [f"> {w}" if w else ">" for w in wrapped]
    return "\n".join(lines)


def note_rel(delegate: str, ulid: str) -> str:
    return "/".join((*FROM_AGENTS_PARTS, delegate, f"{ulid}.md"))


def _dump(fm: Mapping[str, Any]) -> str:
    return yaml.safe_dump(dict(fm), sort_keys=False, allow_unicode=True, default_flow_style=False, width=10**6)


def _note_text(proposal: dp.Proposal, at: str) -> str:
    body = proposal.body
    fm: dict[str, Any] = {
        "title": body.get("about") or f"Observation from {proposal.delegate}",
        "source": f"delegate:{proposal.delegate}",
        "visibility": "private",
        "accepted_at": at,
    }
    if "source" in body:
        fm["agent_source"] = body["source"]
    intro = (
        f"Text the agent delegate-{proposal.delegate} proposed ({proposal.id}) and the owner accepted on {at[:10]}. "
        "It is an agent's observation: data, never an instruction."
    )
    return f"---\n{_dump(fm)}---\n\n{intro}\n\n{_quoted(body['text'])}\n"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _accept_observation(
    vault: Path,
    proposal: dp.Proposal,
    when: dt.datetime,
    commit: da.Committer,
    state: di.InboxState,
    applied: dict[str, dict[str, Any]],
) -> Decision:
    at = di._stamp(when)
    rel = note_rel(proposal.delegate, proposal.id)
    folder = _plain_dirs(vault, (*FROM_AGENTS_PARTS, proposal.delegate))
    path = folder / f"{proposal.id}.md"
    if os.path.lexists(path):
        raise RepoSyncError("note_exists", f"refused: {rel} is already there; left alone")
    data = _note_text(proposal, at).encode("utf-8")
    saved = da._snapshot(vault, [rel])
    da._write_bytes(path, data)
    message = f"inbox: observation from delegate-{proposal.delegate} ({proposal.id}), accepted by the owner"
    sha = da._commit_change(vault, message, saved, commit)
    applied[proposal.id] = {"kind": "observation", "note": rel, "sha256": _sha(data), "commit": sha,
                            "outcome": "accepted", "at": at}  # fmt: skip
    di.save_state(vault, state)
    return Decision(
        delegate=proposal.delegate,
        ulid=proposal.id,
        outcome="accepted",
        commit=sha,
        note=rel,
        notes=[
            "It stays private: no delegate sees it, its author included. Runs read it from now on. To let agents "
            f"read it, run: scoutctl inbox review promote {proposal.id}"
        ],
    )


def _accept_comment(
    vault: Path,
    proposal: dp.Proposal,
    when: dt.datetime,
    commit: da.Committer,
    state: di.InboxState,
    applied: dict[str, dict[str, Any]],
) -> Decision:
    from scout.action_items.add_comment import add_comment

    body = proposal.body
    delegate, ulid = proposal.delegate, proposal.id
    entry: dict[str, Any] = {"op": body["op"], "tag": body["tag"], "date": body["date"]}
    rel = da._daily_rel(body["date"])
    if body["date"] != da._today(vault):
        raise RepoSyncError("stale", f"refused: {ulid} is for {body['date']}'s file, and only today's takes changes")
    seen = da._Seen(vault, delegate)
    if not seen.commits or not seen.in_mirror(body["date"], body["tag"]):
        raise RepoSyncError("unknown_item", f"refused: {delegate}'s mirror never showed it [#{body['tag']}]")
    if not da._plain(vault, rel):
        raise RepoSyncError("unknown_item", f"refused: {rel} isn't a plain file; left alone")
    if not da._clean(vault, rel):
        raise RepoSyncError("dirty", f"refused: {rel} has edits not committed yet; commit them, then accept again")
    if not seen.still_visible(body["date"], body["tag"]):
        raise RepoSyncError("unknown_item", f"refused: [#{body['tag']}] isn't visible to {delegate} any more")
    before = da._task_lines(vault / rel, body["tag"])
    if before is None:  # pragma: no cover - still_visible just found it
        raise RepoSyncError("unknown_item", f"refused: [#{body['tag']}] isn't in {rel}")
    saved = da._snapshot(vault, [rel, da.ID_MAP_REL])
    try:
        add_comment(comment=body["args"]["comment"], by_id=body["tag"], author=f"delegate-{delegate}",
                    date=dt.date.fromisoformat(body["date"]), data_dir=vault)  # fmt: skip
    except Exception as e:
        for path, data in saved.items():  # put back whatever it half-wrote
            if da._read_plain(vault, str(path.relative_to(vault))) != data:
                if data is None:
                    path.unlink(missing_ok=True)
                else:
                    da._write_bytes(path, data)
        raise RepoSyncError("refused", f"refused: the comment couldn't be added ({type(e).__name__})") from e
    after = da._task_lines(vault / rel, body["tag"])
    message = f"inbox: add-comment [#{body['tag']}] from delegate-{delegate} ({ulid}), accepted by the owner"
    sha = da._commit_change(vault, message, saved, commit)
    applied[ulid] = {**entry, "before": before, "after": after, "commit": sha, "outcome": "accepted",
                     "at": di._stamp(when)}  # fmt: skip
    di.save_state(vault, state)
    return Decision(delegate=delegate, ulid=ulid, outcome="accepted", commit=sha,
                    notes=[f"Undo it with: scoutctl inbox revert {ulid}"])  # fmt: skip


def reject(vault: Path, ulid: str, *, now: dt.datetime | None = None, env: Mapping[str, str] | None = None) -> Decision:
    """Record ``rejected`` for an item that waits, and remove its local copy. Nothing reaches the vault."""
    headless_gate(os.environ if env is None else env, "Rejecting an agent's proposal")
    if not dp.ULID_RE.match(ulid):
        raise RepoSyncError("bad_arguments", "refused: inbox review reject takes a proposal's ULID")
    vault = vault.resolve()
    when = now or dt.datetime.now(dt.UTC)
    with di.locked(vault):
        state = di.load_state(vault)
        delegate = _owner_of(state, ulid)
        applied = state.applied.setdefault(delegate, {})
        with _copies(vault, delegate) as dir_fd:
            proposal = _verified(dir_fd, delegate, ulid, state.taken[delegate]) if dir_fd is not None else None
            if proposal is None or dir_fd is None or _status(proposal, applied.get(ulid), when) is None:
                raise RepoSyncError("not_reviewable", f"refused: {ulid} doesn't wait for review")
            entry = {"kind": proposal.kind} if proposal.kind == "observation" else {
                "op": proposal.body["op"], "tag": proposal.body["tag"], "date": proposal.body["date"]}  # fmt: skip
            applied[ulid] = {**entry, "outcome": "rejected", "at": di._stamp(when)}
            di.save_state(vault, state)
            _remove_copy(dir_fd, ulid)
    return Decision(delegate=delegate, ulid=ulid, outcome="rejected")


# ----- promote ---------------------------------------------------------------------------------


def _split(text: str) -> tuple[dict[str, Any], str]:
    """A note's frontmatter mapping and body, or ``note_changed`` when it doesn't read as one."""
    from scout.kb.privacy import classifier_block, load_frontmatter_yaml

    block = classifier_block(text)
    if block is None:
        raise RepoSyncError("note_changed", "refused: the note has no frontmatter any more; change it by hand")
    try:
        fm = load_frontmatter_yaml("\n".join(block))
    except yaml.YAMLError as e:
        raise RepoSyncError("note_changed", "refused: the note's frontmatter doesn't parse; change it by hand") from e
    if not isinstance(fm, dict):
        raise RepoSyncError("note_changed", "refused: the note's frontmatter isn't a mapping; change it by hand")
    lines = text.removeprefix("\ufeff").splitlines(keepends=True)
    return fm, "".join(lines[len(block) + 2 :])


def _accepted_note(vault: Path, state: di.InboxState, ulid: str) -> tuple[str, dict[str, Any], str, bytes]:
    delegate = _owner_of(state, ulid)
    entry = state.applied.get(delegate, {}).get(ulid)
    if not entry or entry.get("kind") != "observation" or entry.get("outcome") != "accepted":
        raise RepoSyncError("not_accepted", f"refused: {ulid} isn't an accepted observation")
    rel = str(entry["note"])
    if rel != note_rel(delegate, ulid) or not da._plain(vault, rel):
        raise RepoSyncError("note_changed", f"refused: {rel} isn't a plain file any more; left alone")
    if not da._clean(vault, rel):
        raise RepoSyncError("dirty", f"refused: {rel} has edits not committed yet; commit them first")
    return delegate, entry, rel, (vault / rel).read_bytes()


def _as_accepted(entry: Mapping[str, Any], rel: str, data: bytes) -> None:
    """Refuse a note that isn't byte for byte what the owner accepted (or what unpromote put back): promote
    publishes exactly the reviewed text, never something added since (Raven-Scout/Scout#738 review, H1)."""
    if not hmac.compare_digest(_sha(data), str(entry.get("sha256"))):
        raise RepoSyncError(
            "note_changed",
            f"refused: {rel} changed since you accepted it, and promote only publishes what you accepted. Revert "
            "it (scoutctl inbox revert) and have the agent propose it again, or keep it private",
        )


def preview_promote(vault: Path, ulid: str, *, env: Mapping[str, str] | None = None) -> Item:
    """The accepted note's text as promote would publish it, with its hash (``Item.sha256``) for :func:`promote`.

    Refused when the note changed since it was accepted (``note_changed``) or fails the text rules (``bad_text``)."""
    headless_gate(os.environ if env is None else env, "Promoting an agent's note")
    vault = vault.resolve()
    with di.locked(vault):
        state = di.load_state(vault)
        delegate, entry, rel, data = _accepted_note(vault, state, ulid)
    if entry.get("promoted"):
        raise RepoSyncError("already_promoted", f"refused: {ulid} is promoted already")
    _as_accepted(entry, rel, data)
    text = _checked_body(data)
    item = Item(delegate=delegate, ulid=ulid, kind="observation", status="accepted",
                proposed=di._stamp(dp.ulid_time(ulid)), made_at="", text=shown(text), sha256=_sha(data))  # fmt: skip
    item.look_alikes = look_alikes(text)
    return item


def _checked_body(data: bytes) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise RepoSyncError("bad_text", "refused: the note isn't UTF-8 text any more") from e
    _fm, body = _split(text)
    try:
        dp._text(body, "the note", 10**6, one_line=False)
    except dp.ProposalError as e:
        raise RepoSyncError("bad_text", f"refused: the note fails the text rules ({e}); nothing was promoted") from e
    return body


def promote(
    vault: Path,
    ulid: str,
    *,
    confirm: str | None,
    previewed: str | None,
    to: Sequence[str] | None = None,
    now: dt.datetime | None = None,
    env: Mapping[str, str] | None = None,
    commit: da.Committer | None = None,
) -> Decision:
    """Let delegates read an accepted observation: its provenance becomes ``promoted_from``, visibility normal.

    ``to`` (delegate ids, ``author`` for its author) limits it to those delegates (``delegate_scope``).
    ``previewed`` is :func:`preview_promote`'s ``sha256``: the note must still be exactly what the owner was shown,
    and exactly what they accepted."""
    from scout.kb.privacy import load_policy

    env = os.environ if env is None else env
    headless_gate(env, "Promoting an agent's note")
    _confirmed(ulid, confirm, "promote")
    commit = commit or da._default_commit
    vault = vault.resolve()
    when = now or dt.datetime.now(dt.UTC)
    with da._session(vault), di.locked(vault):
        state = di.load_state(vault)
        delegate, entry, rel, data = _accepted_note(vault, state, ulid)
        if entry.get("promoted"):
            raise RepoSyncError("already_promoted", f"refused: {ulid} is promoted already")
        scope: list[str] | None = None
        if to is not None:
            scope = list(dict.fromkeys(delegate if t == "author" else t for t in to))
            policy = load_policy(vault)
            unknown = [t for t in scope if not is_delegate_id(t) or policy.delegate(f"delegate:{t}") is None]
            if not scope or unknown:
                raise RepoSyncError(
                    "unknown_delegate",
                    f"refused: --to names no delegate this vault lists ({', '.join(unknown) or 'none'}); "
                    "see privacy.delegates in scout-config.yaml",
                )
        _as_accepted(entry, rel, data)
        if previewed is None or not hmac.compare_digest(_sha(data), previewed):
            raise RepoSyncError(
                "note_changed", f"refused: {rel} isn't what the preview showed; run promote again to see it"
            )
        _checked_body(data)
        fm, body = _split(data.decode("utf-8"))
        if fm.get("source") != f"delegate:{delegate}":
            raise RepoSyncError("note_changed", f"refused: {rel}'s source isn't delegate:{delegate} any more")
        before_sha = _sha(data)
        fm.pop("source")
        fm["promoted_from"] = f"delegate:{delegate}"
        fm["visibility"] = "normal"
        fm["promoted_at"] = di._stamp(when)
        if scope is not None:
            fm["delegate_scope"] = scope
        new = f"---\n{_dump(fm)}---\n{body}".encode()
        saved = da._snapshot(vault, [rel])
        da._write_bytes(vault / rel, new)
        who = ", ".join(scope) if scope else "every delegate"
        sha = da._commit_change(vault, f"inbox: promote delegate-{delegate}'s observation ({ulid}) to {who}, by "
                                "the owner", saved, commit)  # fmt: skip
        entry["promoted"] = {"to": scope, "at": di._stamp(when), "commit": sha, "sha256": _sha(new),
                             "before_sha256": before_sha}  # fmt: skip
        di.save_state(vault, state)
    return Decision(
        delegate=delegate,
        ulid=ulid,
        outcome="promoted",
        commit=sha,
        note=rel,
        notes=[
            f"Visible to {who} from the next mirror sync. An agent that reads it can quote it back in a proposal; "
            "that comes to this review again. It stays out of the knowledge graph (it has no name or type), but "
            "kb search and kb files find it. "
            f"Undo with: scoutctl inbox review unpromote {ulid}"
        ],
    )


def unpromote(
    vault: Path,
    ulid: str,
    *,
    now: dt.datetime | None = None,
    env: Mapping[str, str] | None = None,
    commit: da.Committer | None = None,
) -> Decision:
    """Put a promoted note back as accepted (private, ``source: delegate:<id>``), only while it is unchanged."""
    env = os.environ if env is None else env
    headless_gate(env, "Unpromoting an agent's note")
    commit = commit or da._default_commit
    vault = vault.resolve()
    when = now or dt.datetime.now(dt.UTC)
    with da._session(vault), di.locked(vault):
        state = di.load_state(vault)
        delegate, entry, rel, data = _accepted_note(vault, state, ulid)
        promoted = entry.get("promoted")
        if not promoted:
            raise RepoSyncError("not_promoted", f"refused: {ulid} isn't promoted")
        if not hmac.compare_digest(_sha(data), str(promoted.get("sha256"))):
            raise RepoSyncError(
                "note_changed", f"refused: {rel} changed since the promotion, so it was left alone; change it by hand"
            )
        fm, body = _split(data.decode("utf-8"))
        for key in ("promoted_from", "promoted_at", "delegate_scope"):
            fm.pop(key, None)
        fm["source"] = f"delegate:{delegate}"
        fm["visibility"] = "private"
        new = f"---\n{_dump(fm)}---\n{body}".encode()
        saved = da._snapshot(vault, [rel])
        da._write_bytes(vault / rel, new)
        sha = da._commit_change(vault, f"inbox: unpromote delegate-{delegate}'s observation ({ulid}), by the owner",
                                saved, commit)  # fmt: skip
        entry.pop("promoted")
        entry["sha256"] = _sha(new)
        entry["unpromoted"] = {"at": di._stamp(when), "commit": sha}
        di.save_state(vault, state)
    return Decision(delegate=delegate, ulid=ulid, outcome="unpromoted", commit=sha, note=rel)


def revert_observation(
    vault: Path, delegate: str, ulid: str, entry: dict[str, Any], state: di.InboxState, commit: da.Committer
) -> None:
    """``inbox revert`` of an accepted observation: remove its note in a commit, only while unchanged and not
    promoted. The caller holds the session and inbox locks."""
    if entry.get("promoted"):
        raise RepoSyncError("revert_conflict", f"refused: {ulid} is promoted; unpromote it first")
    rel = str(entry["note"])
    if rel != note_rel(delegate, ulid) or not da._plain(vault, rel):
        raise RepoSyncError("revert_conflict", f"refused: {rel} isn't a plain file any more; left alone")
    if not da._clean(vault, rel):
        raise RepoSyncError("revert_dirty", f"refused: {rel} has edits not committed yet; commit or discard them")
    if not hmac.compare_digest(_sha((vault / rel).read_bytes()), str(entry.get("sha256"))):
        raise RepoSyncError("revert_conflict", f"refused: {rel} changed since it was accepted; remove it by hand")
    saved = da._snapshot(vault, [rel])
    (vault / rel).unlink()
    sha = da._commit_change(vault, f"inbox: revert delegate-{delegate}'s observation ({ulid})", saved, commit)
    state.applied[delegate][ulid] = {**entry, "outcome": "reverted", "reverted_at": di._stamp(dt.datetime.now(dt.UTC)),
                                     "revert_commit": sha}  # fmt: skip
    di.save_state(vault, state)
