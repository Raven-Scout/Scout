"""A delegate's inbox (#550 V4a): the agent proposes, the laptop pulls (spec §7.1a).

Each delegate has its own inbox repository, which its agent writes with a
write deploy key and which Scout reads. **Everything in it is untrusted input
from outside the laptop**, and so is anything a scheduled run can write
(``.scout-inbox/`` in the vault). This module holds:

- **The link** (``scoutctl delegate inbox <id>``): the repository is bound by
  GitHub's numeric repository and owner ids (a local bare repository by its
  file identity), and checked before every pull: private, owned by a user, not
  a fork, no collaborator but the owner, GitHub Actions off, no Pages, and no
  other inbox or mirror using it.
- **The agent's key** (``scoutctl delegate key <id> --inbox``): a deploy key
  GitHub records as writable, titled ``scout-delegate-inbox-<id>``.
- **The agent's side** (``scoutctl inbox propose``): one proposal file
  (:mod:`scout.delegate_proposals`) committed in the agent's inbox clone.
- **The pull** (``scoutctl inbox pull``): a fresh bare repository outside the
  vault for every pull, git locked down (no hooks, fsck on, only the one
  transport, no submodules, no lazy fetch, no replace refs, a file-size limit
  and a timeout), ``main`` only, blobs over 16 KiB filtered, nothing ever
  checked out. ``main`` must descend from the last commit this computer saw
  (a bounded ``--deepen`` looks for it), or the pull refuses
  (``inbox_rewritten``) until the owner runs ``delegate inbox <id> --reset``.

Taking a proposal is crash-safe and can't be forged by a run, in this order:

1. **The signed record** (``.scout-state/delegate-inbox.json``, signed with the
   state key outside the vault and protected from runs by ``hook_paths``)
   gets the ULID and an HMAC of the proposal's bytes. ``inbox apply`` (V4a2)
   acts only on a copy whose bytes match it.
2. **The copy** goes to ``.scout-inbox/agents/<id>/<ULID>.json``, and the ULID
   to ``taken.ledger`` beside it: add-only and used only to skip, so a new
   computer doesn't take a proposal twice. Never applied because of it.
3. **The removal**: one compare-and-swap commit on the inbox's ``main``
   removes everything taken or refused. On a lost race the pull runs once
   more; otherwise the removal waits for the next pull, and nothing recorded
   is taken twice either way.

Nothing here commits the vault: the runner commits the pull (V4b).
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime as dt
import errno
import fcntl
import hashlib
import hmac
import json
import os
import re
import resource
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scout import delegate_mirror as dm
from scout import delegate_proposals as dp
from scout.repo_sync import RepoSyncError, git, headless_gate

SCHEMA_VERSION = 1
STATE_REL = ".scout-state/delegate-inbox.json"
LOCK_REL = ".scout-state/delegate-inbox.lock"
INBOX_DIR = (".scout-inbox", "agents")  # then <delegate>/<ULID>.json and <delegate>/taken.ledger
LEDGER = "taken.ledger"
SIGN_KIND = "inbox"
REMOTE_MAIN = "refs/heads/main"
FETCHED = "refs/scout-inbox/main"  # in the throwaway repository
MAX_TAKEN_PER_DAY = 200  # proposals taken from one inbox in one UTC day; the rest wait in the inbox
MAX_DEEPEN = 256  # commits fetched back from main, looking for the last one this computer saw
DEEPEN_STEP = 32
STALE_SLACK = dt.timedelta(days=1)  # a ULID this far behind the newest one taken is refused (stale_id)
REFUSED_KEPT = 100  # the last refusals kept per delegate, by reason and a hash of the path
GIT_TIMEOUT = 120  # seconds for one git call in a pull
FILE_LIMIT = 64 * 1024 * 1024  # RLIMIT_FSIZE for git in a pull: no file it writes grows past this
REMOVAL_MESSAGE = "Scout: took proposals from the inbox\n"
SCOUT_IDENTITY = {
    "GIT_AUTHOR_NAME": "Scout",
    "GIT_AUTHOR_EMAIL": "scout@localhost",
    "GIT_COMMITTER_NAME": "Scout",
    "GIT_COMMITTER_EMAIL": "scout@localhost",
}

GhRunner = dm.GhRunner
Keygen = dm.Keygen


# ----- state -----------------------------------------------------------------------------------


@dataclass
class InboxLink:
    """One delegate's inbox, as this computer knows it."""

    id: str
    remote: str
    linked_at: str
    bound_to: str  # "github:<repository id>:<owner id>", or "local:<device>:<inode>" for a local bare repository
    base: str | None = None  # the newest inbox main this computer checked; the next main must descend from it
    high_water: str | None = None  # the newest ULID taken
    deploy_key_id: int | None = None
    stale_key_ids: list[int] = field(default_factory=list)
    key_file: str | None = None
    last_pull: str | None = None
    last_error: dict[str, str] | None = None
    day: str | None = None  # the UTC day day_taken counts
    day_taken: int = 0
    refused_total: dict[str, int] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> InboxLink:
        return cls(
            id=str(data["id"]),
            remote=str(data["remote"]),
            linked_at=str(data["linked_at"]),
            bound_to=str(data["bound_to"]),
            base=data.get("base"),
            high_water=data.get("high_water"),
            deploy_key_id=data.get("deploy_key_id"),
            stale_key_ids=[int(k) for k in data.get("stale_key_ids") or []],
            key_file=data.get("key_file"),
            last_pull=data.get("last_pull"),
            last_error=data.get("last_error"),
            day=data.get("day"),
            day_taken=int(data.get("day_taken") or 0),
            refused_total={str(k): int(v) for k, v in (data.get("refused_total") or {}).items()},
        )


@dataclass
class InboxState:
    """Everything signed in :data:`STATE_REL`.

    ``taken``: ``{delegate: {ULID: {"mac": HMAC of the proposal's bytes, "at": when}}}``.
    ``refused``: ``{delegate: [{"entry": hash of the path, "code", "at"}, …]}``, the last :data:`REFUSED_KEPT`.
    ``applied``: what ``inbox apply`` did, with each line's prior state for ``inbox revert`` (V4a2).
    """

    inboxes: dict[str, InboxLink] = field(default_factory=dict)
    taken: dict[str, dict[str, dict[str, str]]] = field(default_factory=dict)
    refused: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    applied: dict[str, dict[str, Any]] = field(default_factory=dict)


def state_path(vault: Path) -> Path:
    return vault / STATE_REL


def load_state(vault: Path) -> InboxState:
    """The signed inbox state, or refuse: one this computer's Scout didn't sign is never trusted."""
    path = state_path(vault)
    if not path.exists():
        return InboxState()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise RepoSyncError("inbox_state_unreadable", f"refused: {STATE_REL} can't be read ({e})") from e
    body = dm._verified(SIGN_KIND, data)
    if body is None:
        raise RepoSyncError(
            "inbox_state_unverified",
            f"refused: {STATE_REL} wasn't written by Scout on this computer (its signature doesn't match), so "
            "nothing in it is trusted. Move it aside and link each inbox again (scoutctl delegate inbox <id>); "
            "the taken ledger in .scout-inbox/agents/ keeps proposals from being taken twice",
        )
    try:
        return InboxState(
            inboxes={k: InboxLink.from_json(v) for k, v in body["inboxes"].items()},
            taken={str(d): {str(u): dict(e) for u, e in t.items()} for d, t in body.get("taken", {}).items()},
            refused={str(d): [dict(e) for e in r] for d, r in body.get("refused", {}).items()},
            applied={str(d): dict(a) for d, a in body.get("applied", {}).items()},
        )
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        raise RepoSyncError("inbox_state_unreadable", f"refused: {STATE_REL} can't be read ({e})") from e


def save_state(vault: Path, state: InboxState) -> None:
    path = state_path(vault)
    path.parent.mkdir(parents=True, exist_ok=True)
    key = dm._state_key(create=True)
    assert key is not None
    doc: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "inboxes": {k: v.to_json() for k, v in sorted(state.inboxes.items())},
        "taken": state.taken,
        "refused": state.refused,
        "applied": state.applied,
    }
    doc["mac"] = dm._sign(key, SIGN_KIND, doc)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def proposal_mac(delegate: str, ulid: str, data: bytes) -> str:
    """The HMAC the signed record keeps for a taken proposal's exact bytes."""
    key = dm._state_key(create=True)
    assert key is not None
    msg = b"inbox-proposal\0" + delegate.encode() + b"\0" + ulid.encode() + b"\0" + data
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


@contextlib.contextmanager
def locked(vault: Path) -> Iterator[None]:
    """Hold the inbox lock, or refuse (``busy``) while another inbox command holds it."""
    path = vault / LOCK_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise RepoSyncError("busy", "refused: another inbox link, key or pull is running") from e
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def ensure_ignored(vault: Path) -> bool:
    """Keep the inbox state and its lock out of the vault's commits. True when the .gitignore changed."""
    missing = [rel for rel in (STATE_REL, LOCK_REL) if git(vault, "check-ignore", "-q", rel, check=False).returncode]
    if not missing:
        return False
    gi = vault / ".gitignore"
    text = gi.read_text(encoding="utf-8") if gi.exists() else ""
    head = text if not text or text.endswith("\n") else text + "\n"
    lines = "".join(f"{rel}\n" for rel in missing)
    gi.write_text(f"{head}# Delegate inboxes (scoutctl delegate inbox): this computer's state\n{lines}", "utf-8")
    return True


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _stamp(when: dt.datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Result:
    delegate: str
    action: str
    taken: list[str] = field(default_factory=list)
    refused: dict[str, int] = field(default_factory=dict)
    already: int = 0  # entries taken before (record or ledger), removed again
    waiting: int = 0  # new entries left for a later pull (the per-pull or daily limit)
    ignored: int = 0  # paths outside inbox/<id>/, counted and left alone
    removed: int = 0
    removal: str | None = None  # the removal commit pushed
    no_main: bool = False
    raced: bool = False  # the agent pushed while the pull ran, so the removal lost its compare-and-swap
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


# ----- the repository: binding and checks ------------------------------------------------------


_FACTS_JQ = "[.id, .owner.id, .owner.login, .owner.type, .visibility, .fork, .has_pages, .full_name] | @tsv"


def _facts(gh: GhRunner, path: str, repo: str) -> dict[str, str]:
    parts = dm._ask_gh(gh, ["api", path, "--jq", _FACTS_JQ], repo).strip().split("\t")
    if len(parts) != 8 or not parts[0].isdigit() or not parts[1].isdigit() or parts[5] not in ("true", "false"):
        raise RepoSyncError("remote_unverified", f"refused: GitHub's answer about {repo} didn't parse")
    keys = ("id", "owner_id", "owner", "owner_type", "visibility", "fork", "has_pages", "full_name")
    return dict(zip(keys, parts, strict=True))


def _check_github(repo: str, facts: Mapping[str, str], gh: GhRunner) -> None:
    """Refuse an inbox repository anyone but its owner, or anything running on GitHub, could write or show."""
    if facts["fork"] == "true":
        raise RepoSyncError("remote_fork", f"refused: {repo} is a fork; make a new private repository for the inbox")
    if facts["visibility"] != "private":
        raise RepoSyncError("remote_public", f"refused: {repo} is {facts['visibility'] or 'not private'}, not private")
    if facts["owner_type"] != "User":
        raise RepoSyncError(
            "remote_org_owned", f"refused: {repo} belongs to an organisation; put the inbox in your personal account"
        )
    if facts["has_pages"] != "false":
        raise RepoSyncError(
            "inbox_pages_on",
            f"refused: {repo} has GitHub Pages on, which would publish what the agent writes; turn Pages off",
        )
    people = dm._ask_gh(gh, ["api", "--paginate", f"repos/{repo}/collaborators", "--jq", ".[].login"], repo)
    others = sorted({p for p in people.split() if p} - {facts["owner"]})
    if others:
        raise RepoSyncError(
            "remote_shared", f"refused: {repo} has {len(others)} collaborator(s) besides its owner; remove them first"
        )
    enabled = dm._ask_gh(gh, ["api", f"repos/{repo}/actions/permissions", "--jq", ".enabled"], repo).strip()
    if enabled != "false":
        raise RepoSyncError(
            "inbox_actions_on",
            f"refused: GitHub Actions is on for {repo}, so a workflow the agent pushes would run; turn it off: "
            f"gh api -X PUT repos/{repo}/actions/permissions -F enabled=false",
        )


def _local_identity(path: Path) -> str:
    try:
        st = path.resolve().stat()
    except OSError as e:
        raise RepoSyncError("remote_unverified", f"refused: {path} can't be read ({e})") from e
    return f"local:{st.st_dev}:{st.st_ino}"


def _not_shared(vault: Path, link: InboxLink, state: InboxState) -> None:
    """Refuse an inbox repository that another inbox, or any delegate's mirror, also uses."""
    for other in state.inboxes.values():
        if other.id != link.id and dm._same_remote(other.remote, link.remote):
            raise RepoSyncError("inbox_taken", f"refused: {link.remote} is {other.id}'s inbox too")
    for mirror in dm.load_links(vault).values():
        if dm._same_remote(mirror.remote, link.remote):
            raise RepoSyncError(
                "inbox_taken",
                f"refused: {link.remote} is {mirror.id}'s mirror; the inbox needs a repository of its own, since the "
                "agent writes it",
            )


def _bind(vault: Path, remote: str, gh: GhRunner) -> str:
    """What ``remote`` is, to bind an inbox to at link: GitHub's ids, or a local path's file identity."""
    repo = dm.github_repo(remote)
    if repo is not None:
        facts = _facts(gh, f"repos/{repo}", repo)
        _check_github(repo, facts, gh)
        return f"github:{facts['id']}:{facts['owner_id']}"
    local = dm._local_remote(remote)
    if local is None:
        raise RepoSyncError(
            "remote_unsupported",
            "refused: an inbox is a private GitHub repository (https://github.com/<owner>/<repo>) or a local path",
        )
    if dm._overlaps(local.resolve(), vault.resolve()):
        raise RepoSyncError("remote_in_vault", "refused: a local inbox must live outside the vault and its folders")
    return _local_identity(local)


def verify(vault: Path, link: InboxLink, state: InboxState, gh: GhRunner) -> None:
    """Refuse unless ``link.remote`` is still the repository it was bound to, and still passes every check.

    Checked before each pull and key change, whatever the (signed) state says.
    A GitHub inbox is looked up by its numeric id: a rename, a transfer or a new
    repository at the old name refuses (``inbox_rebound``).
    """
    _not_shared(vault, link, state)
    dm._check_rewrites(vault, link.remote)
    repo = dm.github_repo(link.remote)
    if repo is None:
        local = dm._local_remote(link.remote)
        if local is None or _local_identity(local) != link.bound_to:
            raise RepoSyncError(
                "inbox_rebound",
                f"refused: {link.remote} isn't the repository {link.id}'s inbox was linked to; link it again",
            )
        return
    kind, _, ids = link.bound_to.partition(":")
    repo_id, _, owner_id = ids.partition(":")
    if kind != "github" or not repo_id.isdigit():
        raise RepoSyncError("inbox_rebound", f"refused: {link.id}'s inbox isn't bound to a GitHub repository")
    facts = _facts(gh, f"repositories/{repo_id}", repo)
    if facts["full_name"].casefold() != repo.casefold() or facts["owner_id"] != owner_id:
        raise RepoSyncError(
            "inbox_rebound",
            f"refused: the repository {link.id}'s inbox was linked to is now {facts['full_name']}, not {repo} "
            "(renamed, transferred or replaced); check it, then link the inbox again",
        )
    _check_github(repo, facts, gh)


# ----- link, reset, unlink ---------------------------------------------------------------------


def make_repo(repo: str, *, gh: GhRunner) -> str:
    """Create the private inbox repository ``owner/name`` under your own user, with GitHub Actions off."""
    if not dm._REPO_NAME.match(repo):
        raise RepoSyncError("bad_arguments", "refused: --create-repo takes owner/name")
    me = dm._ask_gh(gh, ["api", "user", "--jq", ".login"], repo).strip()
    if me.casefold() != repo.split("/", 1)[0].casefold():
        raise RepoSyncError(
            "remote_org_owned", f"refused: --create-repo makes the inbox only under your own GitHub user ({me})"
        )
    proc = dm._run_gh(gh, ["repo", "create", repo, "--private", "--description", "Scout delegate inbox"], repo)
    if proc.returncode != 0:
        why = (proc.stderr or proc.stdout).strip().splitlines()
        raise RepoSyncError("repo_create_failed", f"refused: gh couldn't create {repo}: {why[-1] if why else 'failed'}")
    off = dm._run_gh(gh, ["api", "-X", "PUT", f"repos/{repo}/actions/permissions", "-F", "enabled=false"], repo)
    if off.returncode != 0:
        raise RepoSyncError(
            "inbox_actions_on",
            f"refused: created {repo} but couldn't turn GitHub Actions off; turn it off in its settings, then link it "
            f"with --remote https://github.com/{repo}.git",
        )
    return f"https://github.com/{repo}.git"


def _apps_note(gh: GhRunner) -> str:
    """A warning about GitHub Apps that can reach every repository of the owner's, the inbox included."""
    proc = dm._run_gh(
        gh,
        ["api", "user/installations", "--jq", '.installations[] | select(.repository_selection == "all") | .app_slug'],
        "your account",
    )
    apps = sorted({a for a in proc.stdout.split() if a}) if proc.returncode == 0 else None
    if apps:
        return (
            f"warning: GitHub App(s) {', '.join(apps)} can reach all of your repositories, this inbox included; "
            "limit them to the repositories they need (Settings → Applications)"
        )
    if apps is None:
        return (
            "check that no GitHub App you installed can reach all of your repositories (Settings → Applications): "
            "one could write the inbox"
        )
    return "no GitHub App you installed can reach all of your repositories"


def link(
    vault: Path,
    delegate: str,
    remote: str = "",
    *,
    create_repo: str | None = None,
    env: Mapping[str, str] | None = None,
    gh: GhRunner = dm._gh,
) -> Result:
    """Link ``delegate`` to its inbox repository, bound by GitHub's ids (or a local path's identity)."""
    env = os.environ if env is None else env
    vault = vault.resolve()
    headless_gate(env, "Linking a delegate's inbox")
    dm._check_id(delegate)
    if bool(remote) == bool(create_repo):
        raise RepoSyncError("bad_arguments", "refused: pass --remote <url> or --create-repo <owner/name>")
    if delegate not in dm.load_links(vault):
        raise RepoSyncError(
            "not_linked", f"refused: {delegate} has no mirror; link it first (scoutctl delegate link {delegate})"
        )
    result = Result(delegate=delegate, action="inbox-link")
    with locked(vault):
        state = load_state(vault)
        if delegate in state.inboxes:
            raise RepoSyncError("inbox_exists", f"refused: {delegate} already has an inbox; unlink it first (--unlink)")
        if create_repo:
            remote = make_repo(create_repo, gh=gh)
            result.notes.append(f"created {create_repo}, private, with GitHub Actions off")
        draft = InboxLink(id=delegate, remote=remote, linked_at=_stamp(_now()), bound_to="")
        _not_shared(vault, draft, state)
        dm._check_rewrites(vault, remote)
        draft.bound_to = _bind(vault, remote, gh)
        state.inboxes[delegate] = draft
        ensure_ignored(vault)
        save_state(vault, state)
    if dm.github_repo(remote) is not None:
        result.notes.append(_apps_note(gh))
    result.notes += [
        f"linked {delegate}'s inbox: {remote}",
        f"give the agent a key that can push to it: scoutctl delegate key {delegate} --inbox",
    ]
    return result


def _ls_main(vault: Path, remote: str) -> str | None:
    with _Sandbox(vault, remote) as box:
        return box.remote_main()


def reset(
    vault: Path,
    delegate: str,
    *,
    confirm: Callable[[str, str], bool],
    env: Mapping[str, str] | None = None,
    gh: GhRunner = dm._gh,
) -> Result:
    """Take the inbox's current ``main`` as the new base after ``inbox_rewritten``. The owner only; pushes nothing.

    Proposals already recorded still aren't taken twice: the signed record and
    the taken ledger outlive a reset.
    """
    env = os.environ if env is None else env
    vault = vault.resolve()
    headless_gate(env, "Resetting a delegate's inbox")
    with locked(vault):
        state = load_state(vault)
        current = _inbox(state, delegate)
        verify(vault, current, state, gh)
        main = _ls_main(vault, current.remote)
        if not confirm(delegate, main or "(no main)"):
            raise RepoSyncError("not_confirmed", f"refused: the reset of {delegate}'s inbox wasn't confirmed")
        state.inboxes[delegate] = dataclasses.replace(current, base=main, last_error=None)
        save_state(vault, state)
    return Result(
        delegate=delegate, action="inbox-reset", notes=[f"{delegate}'s inbox now builds on {main or 'nothing'}"]
    )


def unlink(vault: Path, delegate: str, *, env: Mapping[str, str] | None = None, gh: GhRunner = dm._gh) -> Result:
    """Forget ``delegate``'s inbox, revoking its key first. The repository, the copies and the ledger stay."""
    env = os.environ if env is None else env
    vault = vault.resolve()
    headless_gate(env, "Unlinking a delegate's inbox")
    result = Result(delegate=delegate, action="inbox-unlink")
    with locked(vault):
        state = load_state(vault)
        current = _inbox(state, delegate)
        repo = dm.github_repo(current.remote)
        if repo is not None and (current.deploy_key_id is not None or current.stale_key_ids):
            verify(vault, current, state, gh)
            for key_id in [k for k in [current.deploy_key_id, *current.stale_key_ids] if k is not None]:
                dm._delete_key(repo, key_id, gh=gh, title=key_title(delegate))
            result.notes.append("revoked the agent's inbox key")
        _remove_key_files(vault, delegate)
        del state.inboxes[delegate]
        save_state(vault, state)
    result.notes.append("the repository is kept; delete it on GitHub if you like")
    return result


def _inbox(state: InboxState, delegate: str) -> InboxLink:
    current = state.inboxes.get(delegate)
    if current is None:
        raise RepoSyncError(
            "inbox_not_linked", f"refused: {delegate} has no inbox (scoutctl delegate inbox {delegate})"
        )
    return current


# ----- the agent's write key -------------------------------------------------------------------


def key_title(delegate: str) -> str:
    return f"scout-delegate-inbox-{delegate}"


def key_path(vault: Path, delegate: str) -> Path:
    """The agent's private inbox key: beside its mirror key, ``.inbox`` added (no delegate id has a dot)."""
    return dm.secrets_dir() / f"{dm._vault_id(vault)}-{delegate}.inbox"


def _remove_key_files(vault: Path, delegate: str) -> None:
    key = key_path(vault, delegate)
    for p in (key, key.with_name(key.name + ".pub"), key.with_name(key.name + ".known_hosts")):
        try:
            st = p.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISREG(st.st_mode) and st.st_nlink == 1:
            p.unlink()


def deploy_key(
    vault: Path,
    delegate: str,
    *,
    rotate: bool = False,
    revoke: bool = False,
    env: Mapping[str, str] | None = None,
    gh: GhRunner = dm._gh,
    keygen: Keygen = dm._keygen,
) -> Result:
    """Give ``delegate``'s agent a deploy key that can push to its inbox (and only there), or rotate or revoke it.

    GitHub must record the new key as writable, or it is deleted again. Before
    any key changes the inbox is checked as at a pull (:func:`verify`), and a
    key is deleted only when GitHub shows this delegate's inbox title on it.
    """
    env = os.environ if env is None else env
    vault = vault.resolve()
    headless_gate(env, "Changing a delegate's inbox key")
    result = Result(delegate=delegate, action="inbox-key")
    with locked(vault):
        state = load_state(vault)
        current = _inbox(state, delegate)
        repo = dm.github_repo(current.remote)
        if repo is None:
            raise RepoSyncError("key_needs_github", "refused: an inbox key needs a GitHub inbox repository")
        verify(vault, current, state, gh)
        title = key_title(delegate)
        left: list[int] = []
        for key_id in current.stale_key_ids:
            try:
                dm._delete_key(repo, key_id, gh=gh, title=title)
            except RepoSyncError as e:
                if e.code != "key_not_ours":
                    left.append(key_id)
                result.notes.append(e.message.replace("refused: ", "warning: ", 1))
        current = dataclasses.replace(current, stale_key_ids=left)
        if revoke:
            if current.deploy_key_id is None:
                raise RepoSyncError("no_key", f"refused: {delegate} has no inbox key on record")
            dm._delete_key(repo, current.deploy_key_id, gh=gh, title=title)
            _remove_key_files(vault, delegate)
            state.inboxes[delegate] = dataclasses.replace(current, deploy_key_id=None, key_file=None)
            save_state(vault, state)
            result.notes.append(f"revoked {delegate}'s inbox key; its agent can't push proposals any more")
            return result
        if current.deploy_key_id is not None and not rotate:
            raise RepoSyncError("key_exists", f"refused: {delegate} already has an inbox key; pass --rotate")
        dm._private_dir()
        with tempfile.TemporaryDirectory(dir=dm.secrets_dir(), prefix=".new-inbox-key-") as tmp:
            new = Path(tmp) / "key"
            keygen(new, title)
            public = new.with_name("key.pub").read_text(encoding="utf-8").strip()
            proc = dm._run_gh(
                gh,
                [
                    "api", "-X", "POST", f"repos/{repo}/keys", "-f", f"title={title}", "-f", f"key={public}",
                    "-F", "read_only=false", "--jq", "[.id, .read_only] | @tsv",
                ],
                repo,
            )  # fmt: skip
            if proc.returncode != 0:
                why = (proc.stderr or proc.stdout).strip().splitlines()
                raise RepoSyncError("key_add_failed", f"refused: GitHub didn't take the key: {why[-1] if why else ''}")
            fields = proc.stdout.split()
            if len(fields) != 2 or not fields[0].isdigit():
                raise RepoSyncError("key_add_failed", "refused: GitHub's answer to the new key didn't parse")
            new_id = int(fields[0])
            if fields[1] != "false":
                dm._delete_key(repo, new_id, gh=gh, title=title)
                raise RepoSyncError(
                    "key_not_writable", "refused: GitHub recorded the inbox key as read-only; it was deleted"
                )
            added = dataclasses.replace(current, deploy_key_id=new_id)
            if current.deploy_key_id is not None:
                try:
                    dm._delete_key(repo, current.deploy_key_id, gh=gh, title=title)
                except RepoSyncError as e:
                    added = dataclasses.replace(added, stale_key_ids=[*added.stale_key_ids, current.deploy_key_id])
                    result.notes.append(e.message.replace("refused: ", "warning: ", 1))
            key = key_path(vault, delegate)
            _remove_key_files(vault, delegate)
            shutil.move(str(new), key)
            shutil.move(str(new.with_name("key.pub")), key.with_name(key.name + ".pub"))
            os.chmod(key, 0o600)
            known = key.with_name(key.name + ".known_hosts")
            known.write_text(dm.GITHUB_KNOWN_HOSTS + "\n", encoding="utf-8")
        state.inboxes[delegate] = dataclasses.replace(added, key_file=str(key))
        save_state(vault, state)
    result.notes += [
        f"{'replaced' if rotate else 'added'} a deploy key that can push to {repo} (and nothing else)",
        f"private key: {key}; put it in the agent's secret store, then delete it here if you like",
        f"known hosts: {known}; put it next to the key",
        "the agent clones its inbox with: GIT_SSH_COMMAND='ssh -i <key file> -o IdentitiesOnly=yes "
        "-o StrictHostKeyChecking=yes -o UserKnownHostsFile=<known hosts file>' "
        f"git clone git@github.com:{repo}.git, then runs: scoutctl delegate init --inbox <that clone>",
    ]
    return result


# ----- the locked-down fetch -------------------------------------------------------------------


_KEEP_ENV = frozenset({"GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_NOSYSTEM", "GIT_TERMINAL_PROMPT"})


def _limit_files() -> None:  # pragma: no cover - runs in the child, between fork and exec
    resource.setrlimit(resource.RLIMIT_FSIZE, (FILE_LIMIT, FILE_LIMIT))


def sandbox_env(vault: Path, remote: str, git_dir: Path) -> dict[str, str]:
    """The environment for git in a pull: :func:`delegate_mirror.remote_env` (TLS checks forced, the push token)
    plus everything that keeps a hostile inbox from running anything or reaching anything else.

    Every other ``GIT_*`` variable is dropped (``GIT_DIR`` is set to the throwaway repository); the
    credential setup in the user's global git config is kept, so a private GitHub inbox can be fetched.
    """
    base = dm.remote_env(vault, remote)
    count = int(base.get("GIT_CONFIG_COUNT", "0"))
    pairs = [(base[f"GIT_CONFIG_KEY_{i}"], base[f"GIT_CONFIG_VALUE_{i}"]) for i in range(count)]
    scheme = "https" if remote.startswith("https://") else "ssh" if dm.github_repo(remote) else "file"
    pairs += [
        ("core.hooksPath", "/dev/null"),
        ("core.fsmonitor", "false"),
        ("transfer.fsckObjects", "true"),
        ("fetch.fsckObjects", "true"),
        ("protocol.allow", "never"),
        (f"protocol.{scheme}.allow", "always"),
        ("submodule.recurse", "false"),
        ("fetch.recurseSubmodules", "false"),
        ("http.followRedirects", "false"),
        ("gc.auto", "0"),
        ("maintenance.auto", "false"),
        ("fetch.writeCommitGraph", "false"),
        ("core.sshCommand", "ssh"),
        ("credential.interactive", "false"),
    ]
    env = {k: v for k, v in base.items() if not k.startswith("GIT_") or k in _KEEP_ENV}
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_DIR"] = str(git_dir)
    env["GIT_NO_LAZY_FETCH"] = "1"
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    env["GIT_CONFIG_COUNT"] = str(len(pairs))
    for i, (key, value) in enumerate(pairs):
        env[f"GIT_CONFIG_KEY_{i}"] = key
        env[f"GIT_CONFIG_VALUE_{i}"] = value
    return env


class _Sandbox:
    """A throwaway bare repository outside the vault, for one pull: made fresh, removed after."""

    def __init__(self, vault: Path, remote: str) -> None:
        self.remote = remote
        self.root = Path(tempfile.mkdtemp(prefix="scout-inbox-")).resolve()
        if dm._overlaps(self.root, vault):  # pragma: no cover - only with TMPDIR inside the vault
            shutil.rmtree(self.root, ignore_errors=True)
            raise RepoSyncError("tmp_in_vault", "refused: the temporary folder is inside the vault; set TMPDIR")
        self.git_dir = self.root / "inbox.git"
        self.env = sandbox_env(vault, remote, self.git_dir)
        init = {k: v for k, v in self.env.items() if k != "GIT_DIR"}
        self._run(["init", "-q", "--bare", "--template=", str(self.git_dir)], env=init)
        for key, value in (
            ("core.repositoryformatversion", "1"),
            ("extensions.partialClone", "inbox"),
            ("remote.inbox.url", remote),
            ("remote.inbox.promisor", "true"),
            ("remote.inbox.partialclonefilter", f"blob:limit={dp.MAX_BYTES}"),
        ):
            self.git("config", key, value)

    def __enter__(self) -> _Sandbox:
        return self

    def __exit__(self, *exc: object) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _run(
        self, args: Sequence[str], *, env: Mapping[str, str] | None = None, input: bytes | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        try:
            return subprocess.run(
                ["git", *args],
                cwd=self.root,
                env=dict(self.env if env is None else env),
                input=input,
                capture_output=True,
                check=False,
                timeout=GIT_TIMEOUT,
                preexec_fn=_limit_files,  # noqa: PLW1509 - git only, no threads in between
            )
        except subprocess.TimeoutExpired as e:
            raise RepoSyncError("remote_timeout", f"refused: git {args[0]} didn't finish in {e.timeout:g}s") from e

    def git(self, *args: str, input: bytes | None = None, extra: Mapping[str, str] | None = None) -> bytes:
        proc = self._run(args, env={**self.env, **(extra or {})}, input=input)
        if proc.returncode != 0:
            why = proc.stderr.decode("utf-8", "replace").strip().splitlines()
            raise RepoSyncError("inbox_fetch_failed", f"refused: git {args[0]} failed: {why[-1] if why else ''}")
        return proc.stdout

    def ok(self, *args: str) -> bool:
        return self._run(args).returncode == 0

    def remote_main(self) -> str | None:
        out = self.git("ls-remote", "inbox", REMOTE_MAIN).split()
        return out[0].decode() if out else None

    def fetch(self, *depth: str) -> None:
        self.git(
            "fetch", "--quiet", "--no-tags", "--no-recurse-submodules", "--no-write-fetch-head",
            f"--filter=blob:limit={dp.MAX_BYTES}", *depth, "inbox", f"+{REMOTE_MAIN}:{FETCHED}",
        )  # fmt: skip

    def head(self) -> str:
        return self.git("rev-parse", "--verify", f"{FETCHED}^{{commit}}").decode().strip()

    def count(self) -> int:
        return int(self.git("rev-list", "--count", FETCHED).decode().strip() or 0)

    def descends_from(self, base: str) -> bool:
        """Whether the fetched main has ``base`` in its history, deepening at most :data:`MAX_DEEPEN` commits."""
        depth = 1
        while True:
            if self.ok("cat-file", "-e", f"{base}^{{commit}}"):
                return self.ok("merge-base", "--is-ancestor", base, FETCHED)
            if depth >= MAX_DEEPEN:
                return False
            before = self.count()
            self.fetch(f"--deepen={DEEPEN_STEP}")
            depth += DEEPEN_STEP
            if self.count() == before:  # the whole history is here, and base isn't in it
                return False

    def entries(self) -> list[tuple[str, str, str, bytes]]:
        """``(mode, type, sha, path)`` for everything in the fetched main's tree, paths as raw bytes."""
        out = []
        for record in self.git("ls-tree", "-r", "-z", "--full-tree", FETCHED).split(b"\0"):
            if not record:
                continue
            meta, _, path = record.partition(b"\t")
            mode, kind, sha = meta.decode().split(" ")
            out.append((mode, kind, sha, path))
        return out

    def blob(self, sha: str) -> bytes | None:
        """The blob's bytes, or None when it is over :data:`MAX_BYTES` (or was filtered out for being so)."""
        size = self._run(["cat-file", "-s", sha])
        if size.returncode != 0 or int(size.stdout.strip() or 0) > dp.MAX_BYTES:
            return None
        return self.git("cat-file", "blob", sha)

    def remove(self, main: str, paths: Sequence[bytes], when: dt.datetime) -> str:
        """A commit on ``main`` with ``paths`` removed, by Scout's fixed identity; its sha."""
        empty = self.root / "no-checkout"  # update-index wants a work tree; --force-remove never touches it
        empty.mkdir(exist_ok=True)
        index = {"GIT_INDEX_FILE": str(self.root / "removal.index"), "GIT_WORK_TREE": str(empty)}
        self.git("read-tree", main, extra=index)
        self.git("update-index", "--force-remove", "-z", "--stdin", input=b"\0".join(paths) + b"\0", extra=index)
        tree = self.git("write-tree", "--missing-ok", extra=index).decode().strip()
        stamp = f"{int(when.timestamp())} +0000"
        ident = {**SCOUT_IDENTITY, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
        return self.git("commit-tree", tree, "-p", main, "-m", REMOVAL_MESSAGE, extra=ident).decode().strip()

    def push(self, commit: str, expect: str) -> bool:
        """Move the inbox's main from ``expect`` to ``commit``; False when the agent pushed in between."""
        proc = self._run(
            ["push", "--quiet", "--porcelain", "--no-verify", f"--force-with-lease={REMOTE_MAIN}:{expect}",
             "inbox", f"{commit}:{REMOTE_MAIN}"]
        )  # fmt: skip
        if proc.returncode == 0:
            return True
        said = (proc.stdout + proc.stderr).decode("utf-8", "replace")
        if "stale info" in said or "rejected" in said or "fetch first" in said:
            return False
        tail = " | ".join(said.strip().splitlines()[-3:])
        raise RepoSyncError("inbox_push_failed", f"refused: the removal push to the inbox failed: {tail}")


# ----- the vault side: copies and the ledger ---------------------------------------------------


_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)


def _open_folder(root: Path, parts: Sequence[str]) -> int:
    """A file descriptor for ``root/parts…``, made where missing, never through a symlink at any step."""
    fd = os.open(root, os.O_RDONLY | _DIRECTORY)
    try:
        for part in parts:
            with contextlib.suppress(FileExistsError):
                os.mkdir(part, 0o755, dir_fd=fd)
            try:
                nxt = os.open(part, os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=fd)
            except OSError as e:
                if e.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise RepoSyncError(
                        "inbox_folder_unsafe",
                        f"refused: {'/'.join(parts)} under {root} isn't a plain folder (a symlink or a file is in "
                        "the way); nothing was taken",
                    ) from e
                raise
            os.close(fd)
            fd = nxt
    except BaseException:
        os.close(fd)
        raise
    return fd


def _write_new(dir_fd: int, name: str, data: bytes) -> None:
    """Write ``name`` in the folder whole: made aside and renamed over whatever is there (a symlink included)."""
    tmp = f".{name}.{os.getpid()}.tmp"
    with contextlib.suppress(FileNotFoundError):
        os.unlink(tmp, dir_fd=dir_fd)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o644, dir_fd=dir_fd)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp, dir_fd=dir_fd)
        raise


def _check_ledger(dir_fd: int) -> None:
    """Refuse a ledger that is a symlink, a folder or a hard link to some other file: checked before anything
    is recorded, and again as it is opened."""
    try:
        st = os.stat(LEDGER, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
        raise RepoSyncError("inbox_folder_unsafe", f"refused: {LEDGER} isn't a plain file; nothing was taken")


def _append_ledger(dir_fd: int, ulids: Sequence[str]) -> None:
    """Add lines to the ledger: a plain file with one link only, never through a symlink or a hard link."""
    try:
        fd = os.open(LEDGER, os.O_WRONLY | os.O_APPEND | os.O_CREAT | _NOFOLLOW, 0o644, dir_fd=dir_fd)
    except OSError as e:
        raise RepoSyncError("inbox_folder_unsafe", f"refused: {LEDGER} can't be opened ({e.strerror})") from e
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
        st = os.fstat(fh.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise RepoSyncError("inbox_folder_unsafe", f"refused: {LEDGER} isn't a plain file; nothing was taken")
        fh.write("".join(f"{u}\n" for u in ulids))


def ledger(vault: Path, delegate: str) -> set[str]:
    """The ULIDs the committed ledger says were taken (on any computer). Used to skip, never to apply."""
    path = vault.joinpath(*INBOX_DIR, delegate, LEDGER)
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            return set()
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    return {line.strip() for line in text.splitlines() if dp.ULID_RE.match(line.strip())}


def copy_path(vault: Path, delegate: str, ulid: str) -> Path:
    return vault.joinpath(*INBOX_DIR, delegate, f"{ulid}.json")


# ----- pull ------------------------------------------------------------------------------------


@dataclass
class _Candidate:
    path: bytes
    mode: str
    kind: str
    sha: str
    ulid: str | None  # from the file name, when it has the right shape


def _candidates(entries: Sequence[tuple[str, str, str, bytes]], delegate: str) -> tuple[list[_Candidate], int]:
    """Everything under ``inbox/<delegate>/`` (Scout judges and removes it), and how many other paths there are."""
    prefix = f"inbox/{delegate}/".encode()
    found: list[_Candidate] = []
    ignored = 0
    for mode, kind, sha, path in entries:
        if not path.startswith(prefix):
            ignored += 1
            continue
        try:
            text = path.decode("utf-8")
        except UnicodeDecodeError:
            text = ""
        m = dp.PATH_RE.match(text)
        found.append(_Candidate(path, mode, kind, sha, m.group(2) if m and m.group(1) == delegate else None))
    found.sort(key=lambda c: (c.ulid is None, c.ulid or "", c.path))
    return found, ignored


def _judge(
    box: _Sandbox, c: _Candidate, delegate: str, now: dt.datetime, high_water: str | None
) -> tuple[dp.Proposal, bytes] | str:
    """The proposal and its bytes, or the code it is refused with."""
    if c.ulid is None:
        return "bad_path"
    if c.mode != "100644" or c.kind != "blob":
        return "bad_mode"  # a symlink, an executable, a submodule or a folder at a proposal's path
    data = box.blob(c.sha)
    if data is None:
        return "too_big"
    try:
        proposal = dp.parse(c.path.decode(), data, delegate, now=now)
    except dp.ProposalError as e:
        return e.code
    if high_water and dp.ulid_time(proposal.id) < dp.ulid_time(high_water) - STALE_SLACK:
        return "stale_id"
    return proposal, data


def _remember_refusal(state: InboxState, link: InboxLink, path: bytes, code: str, at: str) -> None:
    kept = state.refused.setdefault(link.id, [])
    kept.append({"entry": hashlib.sha256(path).hexdigest()[:16], "code": code, "at": at})
    del kept[:-REFUSED_KEPT]
    link.refused_total[code] = link.refused_total.get(code, 0) + 1


def pull(vault: Path, delegate: str, *, gh: GhRunner = dm._gh, now: dt.datetime | None = None) -> Result:
    """Take the new proposals from ``delegate``'s inbox: record, copy, then remove them there."""
    vault = vault.resolve()
    when = now or _now()
    with locked(vault):
        state = load_state(vault)
        current = _inbox(state, delegate)
        try:
            verify(vault, current, state, gh)
            result = _pull_once(vault, state, delegate, when)
            if result.raced:
                result = _pull_once(vault, load_state(vault), delegate, when)  # the agent pushed in between: once more
        except RepoSyncError as e:
            state = load_state(vault)
            state.inboxes[delegate].last_error = {"code": e.code, "at": _stamp(when)}
            save_state(vault, state)
            raise
    return result


def _pull_once(vault: Path, state: InboxState, delegate: str, when: dt.datetime) -> Result:
    link = state.inboxes[delegate]
    result = Result(delegate=delegate, action="inbox-pull")
    at = _stamp(when)
    with _Sandbox(vault, link.remote) as box:
        if box.remote_main() is None:
            result.no_main = True
            result.notes.append(f"{delegate}'s inbox has no main branch yet; nothing to take")
            link.last_pull, link.last_error = at, None
            save_state(vault, state)
            return result
        box.fetch("--depth=1")
        main = box.head()
        if link.base is not None and link.base != main and not box.descends_from(link.base):
            raise RepoSyncError(
                "inbox_rewritten",
                f"refused: {delegate}'s inbox main doesn't build on {link.base[:12]}, the last one this computer saw "
                f"(within {MAX_DEEPEN} commits): it was rewritten, or ran too far ahead. Nothing was taken. Check it, "
                f"then take it as it is with: scoutctl delegate inbox {delegate} --reset",
            )
        candidates, result.ignored = _candidates(box.entries(), delegate)
        taken = state.taken.setdefault(delegate, {})
        seen = ledger(vault, delegate)
        if link.day != when.date().isoformat():
            link.day, link.day_taken = when.date().isoformat(), 0
        remove: list[bytes] = []
        new: list[tuple[str, bytes]] = []
        recopy: list[tuple[str, bytes]] = []  # recorded before, but the copy never landed: copied again
        handled = 0
        for c in candidates:
            if c.ulid is not None and (c.ulid in taken or c.ulid in seen):
                result.already += 1
                remove.append(c.path)
                if c.ulid in taken and not os.path.lexists(copy_path(vault, delegate, c.ulid)):
                    data = box.blob(c.sha) if (c.mode, c.kind) == ("100644", "blob") else None
                    if data is not None and hmac.compare_digest(
                        taken[c.ulid]["mac"], proposal_mac(delegate, c.ulid, data)
                    ):
                        recopy.append((c.ulid, data))
                continue
            if handled >= dp.MAX_PER_PULL or link.day_taken + len(new) >= MAX_TAKEN_PER_DAY:
                result.waiting += 1
                continue
            handled += 1
            verdict = _judge(box, c, delegate, when, link.high_water)
            remove.append(c.path)
            if isinstance(verdict, str):
                result.refused[verdict] = result.refused.get(verdict, 0) + 1
                _remember_refusal(state, link, c.path, verdict, at)
                continue
            proposal, data = verdict
            new.append((proposal.id, data))
        # The copies' folder is opened first: if it isn't safe, nothing is recorded, so nothing is lost.
        fd = _open_folder(vault, (*INBOX_DIR, delegate)) if new or recopy else None
        try:
            if fd is not None:
                _check_ledger(fd)
            # 1. the signed record
            for ulid, data in new:
                taken[ulid] = {"mac": proposal_mac(delegate, ulid, data), "at": at}
            if new:
                top = max(u for u, _ in new)
                link.high_water = max(top, link.high_water or top)
                link.day_taken += len(new)
            save_state(vault, state)
            # 2. the copies and the ledger
            if fd is not None:
                for ulid, data in [*new, *recopy]:
                    _write_new(fd, f"{ulid}.json", data)
                _append_ledger(fd, [u for u, _ in [*new, *recopy] if u not in seen])
        finally:
            if fd is not None:
                os.close(fd)
        result.taken = [u for u, _ in new]
        # 3. the removal, a compare-and-swap on main
        if remove:
            commit = box.remove(main, remove, when)
            if box.push(commit, main):
                result.removal, result.removed, link.base = commit, len(remove), commit
            else:
                result.raced = True
                result.notes.append("the agent pushed while this pull ran; the removal waits for the next pull")
        else:
            link.base = main
        link.last_pull, link.last_error = at, None
        save_state(vault, state)
    if result.waiting:
        result.notes.append(
            f"{result.waiting} proposal(s) wait for a later pull (at most {dp.MAX_PER_PULL} a pull, "
            f"{MAX_TAKEN_PER_DAY} a day)"
        )
    return result


def pull_all(vault: Path, *, gh: GhRunner = dm._gh) -> list[Result | RepoSyncError]:
    """Pull every linked inbox; one failing doesn't stop the rest."""
    out: list[Result | RepoSyncError] = []
    for delegate in sorted(load_state(vault.resolve()).inboxes):
        try:
            out.append(pull(vault, delegate, gh=gh))
        except RepoSyncError as e:
            e.detail.setdefault("delegate", delegate)
            out.append(e)
    return out


# ----- the agent's side ------------------------------------------------------------------------


@dataclass
class Proposed:
    delegate: str
    id: str
    path: str
    commit: str
    pushed: bool
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


_READER = re.compile(r"^delegate:([a-z0-9][a-z0-9-]{0,39})$")


def _agent_git(clone: Path, *args: str, delegate: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    who = f"delegate-{delegate}"
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}
    env.update(
        GIT_AUTHOR_NAME=who,
        GIT_AUTHOR_EMAIL=f"{who}@scout.invalid",
        GIT_COMMITTER_NAME=who,
        GIT_COMMITTER_EMAIL=f"{who}@scout.invalid",
        GIT_TERMINAL_PROMPT="0",
    )
    proc = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", *args],
        cwd=clone,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=dm.GIT_TIMEOUT,
    )
    if check and proc.returncode != 0:
        why = (proc.stderr or proc.stdout).strip().splitlines()
        raise RepoSyncError("inbox_git_failed", f"refused: git {args[0]} failed: {why[-1] if why else ''}")
    return proc


def inbox_clone(given: Path | None, env: Mapping[str, str]) -> Path:
    """The agent's inbox clone: ``--inbox``, else ``SCOUT_INBOX_DIR``. A git checkout with its own ``.git``
    folder, never the owner's vault, the delegate's mirror clone or a folder inside it."""
    from scout.delegate_agent import _own_git_dir

    raw = given or (Path(env["SCOUT_INBOX_DIR"]) if env.get("SCOUT_INBOX_DIR") else None)
    if raw is None:
        raise RepoSyncError(
            "no_inbox_clone",
            "refused: no inbox clone given; pass --inbox <clone>, or run: scoutctl delegate init --inbox <clone>",
        )
    clone = raw.expanduser().resolve()
    if _own_git_dir(clone) is None:
        raise RepoSyncError("no_inbox_clone", f"refused: {clone} isn't a git clone with its own .git folder")
    if (clone / dm.STATE_REL).exists() or (clone / dm.MANIFEST_PATH).exists():
        raise RepoSyncError(
            "not_an_inbox",
            f"refused: {clone} is a vault or a mirror, not the inbox; proposals go in a clone of the inbox repository",
        )
    mirror = env.get("SCOUT_DATA_DIR")
    if mirror and dm._overlaps(clone, Path(mirror).resolve()):
        raise RepoSyncError("not_an_inbox", f"refused: {clone} is inside the mirror clone {mirror}, or holds it")
    return clone


def propose(
    kind: str,
    body: Mapping[str, Any],
    *,
    inbox: Path | None = None,
    push: bool = False,
    env: Mapping[str, str] | None = None,
) -> Proposed:
    """Write one proposal in the agent's inbox clone and commit it there; with ``push``, push it."""
    env = os.environ if env is None else env
    m = _READER.match(env.get("SCOUT_READER", ""))
    if m is None:
        raise RepoSyncError(
            "not_a_delegate",
            "refused: inbox propose runs as a delegate (SCOUT_READER=delegate:<id>); load it with: "
            'e=$(scoutctl delegate init --print-env) && eval "$e"',
        )
    delegate = m.group(1)
    clone = inbox_clone(inbox, env)
    try:
        proposal = dp.make(delegate, kind, dict(body))
    except dp.ProposalError as e:
        raise RepoSyncError(f"proposal_{e.code}", f"refused: {e}") from e
    fd = _open_folder(clone, ("inbox", delegate))
    try:
        _write_new(fd, f"{proposal.id}.json", proposal.to_bytes())
    finally:
        os.close(fd)
    rel = proposal.path
    _agent_git(clone, "add", "--", rel, delegate=delegate)
    _agent_git(clone, "commit", "-q", "--no-verify", "-m", f"propose {proposal.id}", "--", rel, delegate=delegate)
    commit = _agent_git(clone, "rev-parse", "HEAD", delegate=delegate).stdout.strip()
    out = Proposed(delegate=delegate, id=proposal.id, path=rel, commit=commit, pushed=False)
    if push:
        for attempt in (1, 2):
            if _agent_git(clone, "fetch", "-q", "origin", delegate=delegate, check=False).returncode == 0 and (
                _agent_git(
                    clone, "rev-parse", "-q", "--verify", "origin/main", delegate=delegate, check=False
                ).returncode
                == 0
            ):
                _agent_git(clone, "rebase", "-q", "origin/main", delegate=delegate)
            if (
                _agent_git(
                    clone, "push", "-q", "origin", "HEAD:refs/heads/main", delegate=delegate, check=False
                ).returncode
                == 0
            ):
                out.pushed = True
                break
            if attempt == 2:
                out.notes.append("couldn't push; the proposal is committed in the clone, push it later (git push)")
    else:
        out.notes.append("committed in the inbox clone; push it with --push, or git push")
    return out
