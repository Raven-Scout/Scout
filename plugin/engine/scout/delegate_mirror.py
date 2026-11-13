"""Delegate mirrors (#550 V2): a filtered copy of the vault for one ``delegate:<id>``, pushed to a private remote.

Spec: ``docs/superpowers/specs/2026-10-31-vault-access-for-agents-design.md``
§4.1 (what a delegate sees), §4.4 (line redaction), §6 (the mirror).

A delegate is an agent the owner runs off the laptop (a personal Grok Bot, a
Claude Duet session). It gets a git clone of its own mirror and runs
``scoutctl`` there as ``SCOUT_READER=delegate:<id>``. The mirror is the
boundary: what isn't in it, the agent can't read, whatever tools it has.

**What a mirror commit holds.** The vault's ``HEAD`` tree, keeping a file only
when :func:`scout.kb.privacy.visible_to` shows it to this delegate (the V1
rules: never a local-only path, the phone inbox, a #553 provenance note or a
note whose frontmatter can't be read; a private note only with
``include_private``), and only when it isn't a secret carrier or outside
``privacy.sync_only``. Then every kept file is checked against the files this
delegate can't see (:class:`Scanner`): ``kb check-outbound``'s matchers
(:mod:`scout.kb.outbound`: names, aliases and titles, wikilinks, the citing
form of a shared note, quotes of notes shared with the owner), plus a hidden
note's vault path, written out or as a markdown link. Text is compared NFKC
case-folded.

- A file whose **path** names a hidden note is left out.
- A markdown file loses the lines that hit. A hit inside the frontmatter takes
  its whole list item or top-level key with it, so the YAML stays whole. If the
  frontmatter then doesn't parse, or the file's label changes, the file is left
  out.
- ``.txt``, ``.csv``, ``.tsv`` and ``.jsonl`` files lose the lines that hit; any
  other text file that hits is left out whole.
- A file that isn't UTF-8 text is left out, since it can't be scanned, unless
  its extension is a known binary type (:data:`BINARY_TYPES`), which is kept on
  its label alone.
- ``scout-config.yaml`` is rewritten (:func:`mirror_config`): ``privacy``
  keeps only known keys, and of their path entries only those that match a
  mirrored file, so no local-only or hidden folder is named; ``delegates``
  lists only this delegate; any other top-level key that names a hidden note
  is dropped.
- A symlink is kept only when it points at a folder in the mirror.

A file stays only when the policy the clone will compute from that config gives
it the same label. ``.scout-state/privacy-policy.json`` holds that policy and
every file's label; ``.scout-state/delegate-mirror.json`` holds counts (files
kept, files withheld, lines removed per file), never removed text.

What the matchers can't see: a note whose name is short (under 4 characters)
or one ordinary word is matched only by its wikilinks and its path, as for
``kb check-outbound``; prose that names it otherwise stays.

**History.** One mirror commit per sync that changes the tree, with a fixed
identity and a generic message: a vault commit's subject never goes up. The
laptop is the only writer. A push is a compare-and-swap on the remote's
``main`` (``--force-with-lease``), so a remote that moved is refused
(``mirror_diverged``) and nothing is ever replayed into the vault. The commit
about to go up is recorded first, so a sync that dies after the push adopts it
next time. Before every push the new tree is checked again from scratch
(:func:`check_tree`), against what ``HEAD`` shows the delegate, recomputed.
Link, sync and unlink take a lock on the state.

A file that turns private leaves the mirror's ``HEAD`` but stays in its history
(spec §6.3): ``scoutctl delegate link <id> --remote <url> --replace-remote``
starts the mirror over with one commit.

**Remotes.** A GitHub repository that ``gh api`` reports as ``private`` (not
``internal``), owned by a user, not an organisation, with no collaborator but
its owner; asked at link and before every push, and if it can't be asked,
nothing is pushed. Or a local path outside the vault. Anything else is refused,
and so is a remote that a ``url.*.insteadOf`` or ``pushInsteadOf`` rewrites,
so ``gh`` and ``git`` see the same repository. ``link --create-repo owner/name``
makes the private repository with ``gh``, only under the owner's own GitHub
user. The laptop pushes with git's own credentials, or with a narrow token the
owner stores with ``delegate push-token`` (contents read/write on the mirror
repositories only), passed to git in its environment, never its argv.

**The agent's key** (V2b, spec §6.4): ``delegate key <id>`` makes an ed25519
key pair, adds the public half to the mirror repository as a read-only deploy
key (refused if GitHub doesn't record it read-only), and keeps the private half
in ``~/.scout-secrets/delegate-mirrors/`` for the owner to put in the agent's
secret store. ``--rotate`` replaces it, ``--revoke`` deletes it, and ``unlink``
revokes it too.

**State.** ``.scout-state/delegate-mirrors.json`` (gitignored) holds every link,
keyed by delegate id. Mirror commits live in the vault's object store under
``refs/scout-delegate/<id>``. The Agnes link (:mod:`scout.repo_sync`) has its
own state and refs, and nothing here changes it.
"""

from __future__ import annotations

import base64
import contextlib
import dataclasses
import fcntl
import hashlib
import json
import os
import posixpath
import re
import shutil
import subprocess
import tempfile
import unicodedata
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import yaml

import scout
from scout import paths
from scout.kb.lint_config import glob_match
from scout.kb.outbound import (
    PrivateNote,
    PrivateTerms,
    _term_regex,
    find_mentions,
    note_from_text,
    terms_of,
    with_shared,
)
from scout.kb.privacy import (
    HEAD_BYTES,
    DelegateRule,
    Label,
    Policy,
    classifier_block,
    classify,
    decode_head,
    is_delegate_id,
    load_frontmatter_yaml,
    load_policy,
    load_yaml,
    policy_from_config_text,
    visible_to,
)
from scout.repo_sync import (
    POLICY_PATH,
    SYMLINK_MODE,
    BlobReader,
    LocalOnlyRules,
    RepoSyncError,
    TreeEntry,
    _blob_at,
    _broken,
    _rev,
    _stricter_policy,
    _write_tree,
    fold,
    git,
    headless_gate,
    ls_tree,
    migration_gate,
    overlay_gate,
    trading_candidates,
    widening_guard_gate,
)

SCHEMA_VERSION = 1
STATE_REL = ".scout-state/delegate-mirrors.json"
LOCK_REL = ".scout-state/delegate-mirrors.lock"
MANIFEST_PATH = ".scout-state/delegate-mirror.json"
REF_PREFIX = "refs/scout-delegate/"
REMOTE_BRANCH = "refs/heads/main"
MESSAGE = "Scout delegate mirror: sync\n"
IDENTITY = {
    "GIT_AUTHOR_NAME": "Scout",
    "GIT_AUTHOR_EMAIL": "scout@localhost",
    "GIT_COMMITTER_NAME": "Scout",
    "GIT_COMMITTER_EMAIL": "scout@localhost",
}
LINE_FILES = (".txt", ".csv", ".tsv", ".jsonl")
# Kept on their label alone: they can't be scanned as text.
BINARY_TYPES = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".bmp", ".ico", ".tif", ".tiff",
        ".pdf", ".mp3", ".m4a", ".wav", ".mp4", ".mov", ".zip", ".gz",
        ".xlsx", ".docx", ".pptx",
    }
)  # fmt: skip
MAX_PASSES = 6  # removing a line can join a term split across it; re-scan until clean
GENERATED = frozenset({POLICY_PATH, MANIFEST_PATH})
# The privacy keys a mirror's config keeps (overlay keys; see scout.kb.privacy.load_policy).
PRIVACY_KEYS = (
    "local_only_add",
    "visibility_defaults",
    "shared_files_add",
    "surfaces",
    "federation",
    "federation_ceilings",
    "federation_defaults",
    "sync_only",
)

_GITHUB = re.compile(
    r"^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
    r"(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)
_MD_LINK = re.compile(r"\]\(\s*<?([^)\s>]+)")
_REF_LINK = re.compile(r"^\s*\[[^\]]+\]:\s*<?(\S+?)>?(?:\s|$)")


# ----- state ---------------------------------------------------------------------------------


@dataclass
class DelegateLink:
    """One delegate's mirror on this computer."""

    id: str
    remote: str
    linked_at: str
    account_unshared: bool
    last_mirror: str | None = None
    last_vault: str | None = None
    last_sync: str | None = None
    pending_mirror: str | None = None
    deploy_key_id: int | None = None

    def to_json(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> DelegateLink:
        return cls(
            id=str(data["id"]),
            remote=str(data["remote"]),
            linked_at=str(data["linked_at"]),
            account_unshared=bool(data.get("account_unshared", False)),
            last_mirror=data.get("last_mirror"),
            last_vault=data.get("last_vault"),
            last_sync=data.get("last_sync"),
            pending_mirror=data.get("pending_mirror"),
            deploy_key_id=data.get("deploy_key_id"),
        )


def state_path(vault: Path) -> Path:
    return vault / STATE_REL


def load_links(vault: Path) -> dict[str, DelegateLink]:
    path = state_path(vault)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {k: DelegateLink.from_json(v) for k, v in data["links"].items()}
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise RepoSyncError("state_unreadable", f"refused: {STATE_REL} can't be read ({e}); fix or delete it") from e


def save_links(vault: Path, links: Mapping[str, DelegateLink]) -> None:
    path = state_path(vault)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"schema_version": SCHEMA_VERSION, "links": {k: v.to_json() for k, v in sorted(links.items())}}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


@contextlib.contextmanager
def locked(vault: Path) -> Iterator[None]:
    """Hold the delegate-mirror lock, or refuse (``busy``) while another link, sync or unlink holds it."""
    path = vault / LOCK_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise RepoSyncError("busy", "refused: another delegate link, sync or unlink is running") from e
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def ensure_ignored(vault: Path) -> bool:
    """Add the link state and its lock to the vault's .gitignore unless already ignored. True when added."""
    missing = [rel for rel in (STATE_REL, LOCK_REL) if git(vault, "check-ignore", "-q", rel, check=False).returncode]
    if not missing:
        return False
    gi = vault / ".gitignore"
    text = gi.read_text(encoding="utf-8") if gi.exists() else ""
    head = text if not text or text.endswith("\n") else text + "\n"
    lines = "".join(f"{rel}\n" for rel in missing)
    gi.write_text(f"{head}# Delegate mirrors (scoutctl delegate): this computer's state\n{lines}", "utf-8")
    return True


def mirror_ref(delegate: str) -> str:
    return f"{REF_PREFIX}{delegate}"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ----- remotes -------------------------------------------------------------------------------

GhRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def _gh(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "GH_REPO"}
    env["GH_HOST"] = "github.com"  # gh asks about the same host git pushes to
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=False, env=env)


def github_repo(remote: str) -> str | None:
    """``owner/name`` for a GitHub remote URL, else None."""
    m = _GITHUB.match(remote.strip())
    return m.group("repo") if m else None


def _local_remote(remote: str) -> Path | None:
    raw = remote[len("file://") :] if remote.startswith("file://") else remote
    if "://" in raw or re.match(r"^[^/]+@[^/]+:", raw) or not raw.startswith(("/", "~")):
        return None
    return Path(raw).expanduser()


def _ask_gh(gh: GhRunner, args: Sequence[str], repo: str) -> str:
    try:
        proc = gh(args)
    except OSError as e:
        raise RepoSyncError("remote_unverified", f"refused: can't ask GitHub about {repo} (gh: {e})") from e
    if proc.returncode != 0:
        why = (proc.stderr or proc.stdout).strip().splitlines()
        raise RepoSyncError(
            "remote_unverified", f"refused: can't ask GitHub about {repo}: {why[-1] if why else 'gh failed'}"
        )
    return proc.stdout


def _check_github(repo: str, gh: GhRunner) -> None:
    fields = _ask_gh(gh, ["api", f"repos/{repo}", "--jq", "[.visibility, .owner.type, .owner.login] | @tsv"], repo)
    parts = fields.strip().split("\t")
    if len(parts) != 3:
        raise RepoSyncError("remote_unverified", f"refused: GitHub's answer about {repo} didn't parse")
    visibility, owner_type, owner = parts
    if visibility != "private":
        raise RepoSyncError(
            "remote_public", f"refused: {repo} is {visibility or 'not private'}, not private; nothing was pushed"
        )
    if owner_type != "User":
        raise RepoSyncError(
            "remote_org_owned",
            f"refused: {repo} belongs to an organisation, whose members and teams may read it; "
            "put the mirror in your personal account",
        )
    people = _ask_gh(gh, ["api", "--paginate", f"repos/{repo}/collaborators", "--jq", ".[].login"], repo)
    others = sorted({p for p in people.split() if p} - {owner})
    if others:
        raise RepoSyncError(
            "remote_shared", f"refused: {repo} has {len(others)} collaborator(s) besides its owner; remove them first"
        )


def _check_rewrites(vault: Path, remote: str) -> None:
    out = git(vault, "config", "--get-regexp", r"^url\..*\.(push)?insteadof$", check=False).stdout
    for line in out.splitlines():
        _key, _, prefix = line.partition(" ")
        if prefix and remote.startswith(prefix):
            raise RepoSyncError(
                "remote_rewritten",
                f"refused: git rewrites {remote} ({_key}); drop that rewrite so the repository checked is the one "
                "pushed to",
            )
    url = git(vault, "ls-remote", "--get-url", remote, check=False).stdout.strip()
    if url and url != remote:  # pragma: no cover - a rewrite is caught above; this is the backstop
        raise RepoSyncError("remote_rewritten", f"refused: git would push {remote} somewhere else ({url})")


def check_remote(remote: str, *, vault: Path, gh: GhRunner = _gh) -> None:
    """Refuse a remote that isn't a private, personal, unshared GitHub repository or a local path outside the vault."""
    repo = github_repo(remote)
    local = _local_remote(remote) if repo is None else None
    if repo is None and local is None:
        raise RepoSyncError(
            "remote_unsupported",
            "refused: a delegate mirror goes to a private GitHub repository (https://github.com/<owner>/<repo>) "
            "or a local path",
        )
    _check_rewrites(vault, remote)
    if repo is not None:
        _check_github(repo, gh)
        return
    assert local is not None
    target, home = local.resolve(), vault.resolve()
    if target == home or home in target.parents or target in home.parents:
        raise RepoSyncError("remote_in_vault", "refused: a local mirror must live outside the vault and its folders")


_REPO_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}$")  # never an option


def secrets_dir() -> Path:
    return Path.home() / ".scout-secrets" / "delegate-mirrors"


def _vault_id(vault: Path) -> str:
    return hashlib.sha256(str(vault.resolve()).encode()).hexdigest()[:16]


def key_path(vault: Path, delegate: str) -> Path:
    """The agent's private deploy key; the public half is next to it with ``.pub``."""
    return secrets_dir() / f"{_vault_id(vault)}-{delegate}"


def push_token_path(vault: Path) -> Path:
    return secrets_dir() / f"{_vault_id(vault)}.push-token"


def _private_dir() -> Path:
    d = secrets_dir()
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    return d


def save_push_token(vault: Path, token: str) -> None:
    if not token.strip():
        raise RepoSyncError("token_missing", "refused: no token given")
    _private_dir()
    p = push_token_path(vault)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(token.strip() + "\n")
    os.chmod(p, 0o600)


def _push_token(vault: Path) -> str | None:
    p = push_token_path(vault)
    try:
        mode = p.stat().st_mode & 0o777
    except FileNotFoundError:
        return None
    if mode & 0o077:
        raise RepoSyncError("token_insecure", f"refused: {p} has permissions {oct(mode)}; run chmod 600 {p}")
    return p.read_text(encoding="utf-8").strip() or None


def auth_env(vault: Path, remote: str) -> dict[str, str]:
    """Git config, in the environment, that sends the stored push token to an ``https://github.com`` remote."""
    token = _push_token(vault) if remote.startswith("https://github.com/") else None
    if token is None:
        return {}
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}",
    }


def remote_head(vault: Path, remote: str) -> str | None:
    out = git(vault, "ls-remote", remote, REMOTE_BRANCH, env=auth_env(vault, remote)).stdout.split()
    return out[0] if out else None


def push(vault: Path, remote: str, commit: str, expect: str | None) -> None:
    """Move the remote's ``main`` from ``expect`` (None: it must not exist) to ``commit``, or refuse."""
    lease = f"--force-with-lease={REMOTE_BRANCH}:{expect or ''}"
    proc = git(
        vault,
        "push",
        "--porcelain",
        lease,
        remote,
        f"{commit}:{REMOTE_BRANCH}",
        env=auth_env(vault, remote),
        check=False,
    )
    if proc.returncode != 0:
        tail = " | ".join((proc.stderr.strip() or proc.stdout.strip()).splitlines()[-3:])
        code = "mirror_diverged" if "stale info" in proc.stdout + proc.stderr else "push_failed"
        raise RepoSyncError(code, f"refused: the push to the delegate mirror failed: {tail}")


# ----- policy --------------------------------------------------------------------------------


def delegate_policy(vault: Path, head: str, delegate: str, blobs: BlobReader) -> tuple[Policy, list[Policy]]:
    """The policy this delegate's mirror of ``head`` is built under, and the policies whose Local-Only rules apply.

    The stricter of the live ``scout-config.yaml`` and the one committed at
    ``head``. The delegate must be listed in both, and sees private notes only
    when both say ``include_private: true`` (written exactly so). Its
    ``delegates`` names this delegate alone.
    """
    reader = f"delegate:{delegate}"
    live = load_policy(vault)
    overlay_gate(live)
    entry = _blob_at(vault, head, paths.VAULT_MARKER)
    if entry is None:
        raise RepoSyncError("delegate_not_listed", f"refused: HEAD has no {paths.VAULT_MARKER}")
    committed = policy_from_config_text(blobs.read(entry.sha).decode("utf-8", errors="replace"))
    if _broken(committed):
        raise RepoSyncError(
            "privacy_overlay_unreadable", f"refused: the {paths.VAULT_MARKER} committed at HEAD can't be read"
        )
    rules = [live.delegate(reader), committed.delegate(reader)]
    if None in rules:
        raise RepoSyncError(
            "delegate_not_listed",
            f"refused: {delegate} isn't listed under privacy.delegates in scout-config.yaml "
            "(both the file and its committed version must list it)",
        )
    include_private = all(r.include_private for r in rules if r is not None)
    base = live if live == committed else _stricter_policy(live, committed)
    policy = dataclasses.replace(base, delegates=(DelegateRule(id=delegate, include_private=include_private),))
    return policy, [live, committed]


# ----- what may not be named ---------------------------------------------------------------------


def _fold_path(path: str) -> str:
    return unicodedata.normalize("NFKC", unicodedata.normalize("NFKC", path).casefold())


def _plumbing(rel: str) -> bool:
    """A path under a top-level dot folder (``.scout-state``, ``.obsidian``): its name is no secret."""
    return rel.startswith(".")


class Scanner:
    """Finds what names a file the delegate can't see: the outbound matchers, plus hidden vault paths."""

    def __init__(self, terms: PrivateTerms, hidden: Sequence[str] = ()) -> None:
        named = [p for p in hidden if not _plumbing(p)]
        stems = sorted({p[:-3] if p.lower().endswith(".md") else p for p in named})
        path_notes = [
            PrivateNote(
                path=f"path:{s}",
                slug=s,
                terms=(s,),
                link_keys=frozenset(),
                link_path="",
                pattern=_term_regex([s]),
            )
            for s in stems
        ]
        self.terms = terms_of([*terms.notes, *path_notes]) if path_notes else terms
        if terms.excerpts is not None:
            self.terms = dataclasses.replace(self.terms, excerpts=terms.excerpts)
        # Generated JSON isn't prose: the fuzzy matcher for quotes of shared notes misreads its punctuation.
        self.plain = dataclasses.replace(self.terms, excerpts=None)
        self.hidden = frozenset(_fold_path(s) for s in [*named, *stems])

    def _link_hit(self, target: str, rel: str) -> bool:
        target = unquote(target).split("#", 1)[0].split("?", 1)[0]
        if not target or "://" in target or target.startswith("mailto:"):
            return False
        here = posixpath.dirname(rel)
        candidates = {posixpath.normpath(posixpath.join(here, target)), posixpath.normpath(target.lstrip("/"))}
        return any(_fold_path(c) in self.hidden for c in candidates)

    def lines(self, text: str, rel: str = "", *, prose: bool = True) -> list[int]:
        """Sorted 1-based numbers of the lines of ``text`` that name a hidden file.

        ``prose=False`` (generated files) leaves out the quote matcher for shared notes.
        """
        out = set(find_mentions(text, self.terms if prose else self.plain, allow_markers=False))
        for n, line in enumerate(text.split("\n"), 1):
            links = [m.group(1) for m in _MD_LINK.finditer(line)]
            ref = _REF_LINK.match(line)
            if ref:
                links.append(ref.group(1))
            if any(self._link_hit(t, rel) for t in links):
                out.add(n)
        return sorted(out)

    def names(self, rel: str) -> bool:
        """Whether the path ``rel`` itself names a hidden file."""
        return bool(find_mentions(rel, self.terms, allow_markers=False))


# ----- redaction -----------------------------------------------------------------------------


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _is_item(line: str) -> bool:
    s = line.lstrip(" ")
    return s == "-" or s.startswith("- ")


def _frontmatter_end(lines: Sequence[str]) -> int | None:
    """Index of the closing ``---`` of a leading frontmatter block (a byte-order mark allowed), or None."""
    if not lines or lines[0].lstrip("﻿").strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            return i
    return None


def _yaml_block(lines: Sequence[str], i: int, start: int, end: int) -> range:
    """The frontmatter lines (``start`` ≤ i < ``end``) to drop for a hit on line ``i``.

    The list item that holds it, or else the top-level key, with every line
    indented under it, so what's left is still YAML.
    """
    j = i
    while not _is_item(lines[j]) and _indent(lines[j]) > 0:
        k = j - 1  # the nearest line above that is indented less: what holds line j
        while k >= start and (not lines[k].strip() or _indent(lines[k]) >= _indent(lines[j])):
            k -= 1
        if k < start:
            break
        j = k
    block = _indent(lines[j])
    stop = j + 1
    while stop < end and (not lines[stop].strip() or _indent(lines[stop]) > block):
        stop += 1
    return range(j, stop)


def redact_lines(
    text: str, scanner: Scanner | PrivateTerms, *, markdown: bool, rel: str = ""
) -> tuple[str, int] | None:
    """``text`` without the lines that name a hidden file, and how many lines went.

    None when it can't be made clean in :data:`MAX_PASSES` passes.
    """
    scan = scanner if isinstance(scanner, Scanner) else Scanner(scanner)
    lines = text.split("\n")
    fm_end = _frontmatter_end(lines) if markdown else None
    gone: set[int] = set()
    for _ in range(MAX_PASSES):
        kept = [i for i in range(len(lines)) if i not in gone]
        hits = scan.lines("\n".join(lines[i] for i in kept), rel)
        if not hits:
            return "\n".join(lines[i] for i in kept), len(gone)
        for n in hits:
            i = kept[n - 1]
            if fm_end is not None and 0 < i < fm_end:
                gone.update(_yaml_block(lines, i, 1, fm_end))
            elif fm_end is not None and i in (0, fm_end):  # pragma: no cover - a delimiter never names a note
                return None
            else:
                gone.add(i)
    return None


def _frontmatter_parses(text: str) -> bool:
    block = classifier_block(text)
    if block is None:
        return True
    try:
        load_frontmatter_yaml("\n".join(block))
    except yaml.YAMLError:
        return False
    return True


def _same_label(a: Label, b: Label) -> bool:
    return (a.visibility, a.local_only, a.shared_source) == (b.visibility, b.local_only, b.shared_source)


def _scoped(value: Any, mirrored: Sequence[str]) -> Any:
    """A privacy list without the entries whose glob matches no mirrored file; other values as they are."""
    if not isinstance(value, list):
        return value

    def glob_of(entry: Any) -> str | None:
        if isinstance(entry, str):
            return entry
        if isinstance(entry, dict) and isinstance(entry.get("glob"), str):
            return str(entry["glob"])
        return None

    out = []
    for entry in value:
        g = glob_of(entry)
        if g is not None and any(glob_match(g, rel) for rel in mirrored):
            out.append(entry)
    return out


def mirror_config(text: str, delegate: str, include_private: bool, mirrored: Sequence[str], scan: Scanner) -> bytes:
    """``scout-config.yaml`` for the mirror (module docstring): nothing in it names what the mirror leaves out."""
    data = load_yaml(text)
    if not isinstance(data, dict):  # pragma: no cover - delegate_policy refused a broken config
        raise RepoSyncError("privacy_overlay_unreadable", f"refused: {paths.VAULT_MARKER} can't be read")
    raw = data.get("privacy")
    source: dict[str, Any] = raw if isinstance(raw, dict) else {}
    privacy: dict[str, Any] = {}
    for key in PRIVACY_KEYS:
        if key in source:
            privacy[key] = _scoped(source[key], mirrored)
    privacy["delegates"] = {delegate: {"include_private": True} if include_private else {}}
    out: dict[str, Any] = {}
    for key, value in data.items():
        if key == "privacy":
            out[key] = privacy
            continue
        if not isinstance(key, str) or scan.names(key):
            continue
        dumped = yaml.safe_dump({key: value}, allow_unicode=True)
        if not scan.lines(dumped):
            out[key] = value
    rendered = yaml.safe_dump(out, sort_keys=False, allow_unicode=True)
    if scan.lines(rendered):
        raise RepoSyncError("config_names_hidden", f"refused: {paths.VAULT_MARKER}'s privacy block names a hidden file")
    return rendered.encode("utf-8")


# ----- building a mirror tree ----------------------------------------------------------------


@dataclass(frozen=True)
class MirrorFile:
    path: str
    mode: str
    data: bytes | None  # None: the vault's blob unchanged
    sha: str


@dataclass
class Plan:
    """What one delegate's mirror of a vault commit holds, before anything is written."""

    delegate: str
    files: list[MirrorFile] = field(default_factory=list)
    labels: list[Label] = field(default_factory=list)
    withheld: int = 0
    redacted: dict[str, int] = field(default_factory=dict)
    left_out: int = 0  # visible files left out: their name, their encoding, or redaction
    hidden_paths: set[str] = field(default_factory=set)
    mirror_policy: Policy | None = None

    def manifest(self) -> bytes:
        doc = {
            "schema_version": SCHEMA_VERSION,
            "generator": "scoutctl delegate sync",
            "engine_version": scout.__version__,
            "delegate": self.delegate,
            "files": len(self.files),
            "withheld_files": self.withheld + self.left_out,
            "redacted_lines": dict(sorted(self.redacted.items())),
        }
        return (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode("utf-8")

    def policy_document(self) -> bytes:
        assert self.mirror_policy is not None
        doc = {
            "schema_version": SCHEMA_VERSION,
            "generator": "scoutctl delegate sync",
            "engine_version": scout.__version__,
            "policy": self.mirror_policy.to_json(),
            "labels": [lb.to_json() for lb in sorted(self.labels, key=lambda lb: lb.path)],
        }
        return (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _content(rel: str, data: bytes) -> tuple[str, str | None]:
    """``("text", text)`` for UTF-8 text, ``("binary", None)`` for a known binary type, else ``("opaque", None)``."""
    if posixpath.splitext(rel.lower())[1] in BINARY_TYPES:
        return "binary", None
    if b"\0" in data:
        return "opaque", None
    try:
        return "text", data.decode("utf-8")
    except UnicodeDecodeError:
        return "opaque", None


def hidden_terms(vault: Path, entries: Sequence[TreeEntry], hidden: Sequence[str], blobs: BlobReader) -> Scanner:
    """What the mirror may not name: each hidden note's forms and path, and what was shared with the owner."""
    from scout.kb.shared_reads import SharedReadsUnreadable

    by_path = {e.path: e for e in entries}
    notes = []
    for rel in hidden:
        e = by_path[rel]
        if rel.lower().endswith(".md") and e.mode != SYMLINK_MODE:
            notes.append(note_from_text(rel, decode_head(blobs.read(e.sha)[:HEAD_BYTES])))
    try:
        terms = with_shared(terms_of(notes), vault)
    except SharedReadsUnreadable as e:
        raise RepoSyncError("shared_reads_unreadable", f"refused: the shared-reads ledger can't be read ({e})") from e
    return Scanner(terms, hidden)


def _link_target(entry: TreeEntry, target: bytes) -> str | None:
    """The vault path a relative symlink at ``entry`` points at, or None when it leaves the vault."""
    name = target.decode("utf-8", errors="replace")
    if name.startswith("/"):
        return None
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(entry.path), name))
    return None if resolved in (".", "..") or resolved.startswith("../") else resolved


def _folders(files: Sequence[str]) -> set[str]:
    """Every folder that holds one of ``files``, at any depth."""
    out: set[str] = set()
    for rel in files:
        parts = rel.split("/")[:-1]
        out.update("/".join(parts[:n]) for n in range(1, len(parts) + 1))
    return out


@dataclass
class Visibility:
    """What ``HEAD`` shows one delegate, file by file, before any redaction."""

    policy: Policy
    rules: LocalOnlyRules
    entries: list[TreeEntry]
    visible: list[tuple[TreeEntry, Label]]
    links: list[TreeEntry]
    hidden: set[str]


def visibility(vault: Path, head: str, delegate: str, blobs: BlobReader) -> Visibility:
    policy, policies = delegate_policy(vault, head, delegate, blobs)
    rules = LocalOnlyRules.build(policies, ())
    reader = f"delegate:{delegate}"
    generated = {fold(p) for p in GENERATED}
    entries = [e for e in ls_tree(vault, head) if e.kind == "blob"]
    out = Visibility(policy, rules, entries, [], [], set())
    for e in entries:
        if fold(e.path) in generated:
            continue  # only the generated files may carry those names
        if rules.refused(e.path):
            out.hidden.add(e.path)
        elif e.mode == SYMLINK_MODE:
            out.links.append(e)
        else:
            head_text = decode_head(blobs.read(e.sha)[:HEAD_BYTES]) if e.path.lower().endswith(".md") else None
            label = classify(e.path, head_text, policy)
            if visible_to(label, reader, policy):
                out.visible.append((e, label))
            else:
                out.hidden.add(e.path)
    return out


def plan(vault: Path, head: str, delegate: str, blobs: BlobReader) -> tuple[Plan, Policy, LocalOnlyRules, Scanner]:
    """The mirror of vault commit ``head`` for ``delegate``, and what it was redacted against. Reads only."""
    seen = visibility(vault, head, delegate, blobs)
    policy, rules = seen.policy, seen.rules
    out = Plan(delegate=delegate, hidden_paths=set(seen.hidden))
    scan = hidden_terms(vault, seen.entries, sorted(seen.hidden), blobs)
    kept: list[tuple[MirrorFile, Label, str | None]] = []  # file, label, its text when it is text
    config: TreeEntry | None = None
    for e, label in seen.visible:
        if scan.names(e.path):
            out.left_out += 1
            continue
        if e.path == paths.VAULT_MARKER:
            config = e
            continue
        made = _mirror_file(e, label, blobs.read(e.sha), scan, policy, out)
        if made is not None:
            kept.append(made)
    folders = _folders([f.path for f, _, _ in kept])
    for e in seen.links:
        target = _link_target(e, blobs.read(e.sha))
        if target is not None and target in folders and not rules.refused(target) and not scan.names(e.path):
            kept.append((MirrorFile(e.path, e.mode, None, e.sha), classify(e.path, None, policy), None))
        else:
            out.hidden_paths.add(e.path)
    if config is not None:
        mirrored = [f.path for f, _, _ in kept] + [paths.VAULT_MARKER]
        include_private = policy.delegates[0].include_private
        config_text = blobs.read(config.sha).decode("utf-8", "replace")
        data = mirror_config(config_text, delegate, include_private, mirrored, scan)
        kept.append((MirrorFile(config.path, config.mode, data, ""), classify(config.path, None, policy), None))
        out.mirror_policy = policy_from_config_text(data.decode("utf-8"))
    else:
        out.mirror_policy = policy
    reader = f"delegate:{delegate}"
    for f, label, text in kept:  # the clone's own policy must label each file the same
        head_text = text[:HEAD_BYTES] if text is not None and f.path.lower().endswith(".md") else None
        theirs = classify(f.path, head_text, out.mirror_policy)
        if f.mode != SYMLINK_MODE and (
            not _same_label(label, theirs) or not visible_to(theirs, reader, out.mirror_policy)
        ):
            out.left_out += 1
            continue
        out.files.append(f)
        out.labels.append(theirs)
    out.withheld = len(out.hidden_paths)
    return out, policy, rules, scan


def _mirror_file(
    e: TreeEntry, label: Label, data: bytes, scan: Scanner, policy: Policy, out: Plan
) -> tuple[MirrorFile, Label, str | None] | None:
    """What a visible file becomes in the mirror, or None (counted in ``out.left_out``) when it can't go."""
    kind, text = _content(e.path, data)
    if kind == "binary":
        return MirrorFile(e.path, e.mode, None, e.sha), label, None
    if text is None:
        out.left_out += 1
        return None
    low = e.path.lower()
    markdown = low.endswith(".md")
    if not (markdown or low.endswith(LINE_FILES)):
        if scan.lines(text, e.path):
            out.left_out += 1
            return None
        return MirrorFile(e.path, e.mode, None, e.sha), label, text
    redacted = redact_lines(text, scan, markdown=markdown, rel=e.path)
    if redacted is None:
        out.left_out += 1
        return None
    new_text, removed = redacted
    if removed == 0:
        return MirrorFile(e.path, e.mode, None, e.sha), label, text
    new_label = classify(e.path, new_text[:HEAD_BYTES], policy) if markdown else label
    if markdown and (not _frontmatter_parses(new_text) or not _same_label(label, new_label)):
        out.left_out += 1
        return None
    out.redacted[e.path] = removed
    return MirrorFile(e.path, e.mode, new_text.encode("utf-8"), ""), new_label, new_text


def write_tree(vault: Path, the_plan: Plan) -> str:
    """Write ``the_plan``'s blobs and tree into the vault's object store; the tree sha."""

    def blob(data: bytes) -> str:
        return git(vault, "hash-object", "-w", "--stdin", input=data).stdout.strip()

    lines = []
    for f in the_plan.files:
        sha = f.sha if f.data is None else blob(f.data)
        lines.append(f"{f.mode} {sha}\t{f.path}")
    lines.append(f"100644 {blob(the_plan.policy_document())}\t{POLICY_PATH}")
    lines.append(f"100644 {blob(the_plan.manifest())}\t{MANIFEST_PATH}")
    return _write_tree(vault, lines)


def check_tree(vault: Path, rev: str, delegate: str, head: str) -> None:
    """Refuse when mirror commit ``rev`` holds anything ``delegate`` may not see. Shares nothing with the build.

    What ``HEAD`` shows the delegate and what may not be named are worked out
    again from ``HEAD``; the policy the clone will use is read from the tree's
    own ``scout-config.yaml``. Every path must be one ``HEAD`` shows the
    delegate and must name no hidden file; every file must be visible under both
    policies with the same label; every text file, the config and the generated
    files included, must name no hidden file; a file that isn't text must be a
    known binary type; every symlink must point at a folder the tree holds.
    """
    reader = f"delegate:{delegate}"
    blobs = BlobReader(vault)
    try:
        seen = visibility(vault, head, delegate, blobs)
        scan = hidden_terms(vault, seen.entries, sorted(seen.hidden), blobs)
        allowed = {e.path for e, _ in seen.visible} | {e.path for e in seen.links}
        entries = ls_tree(vault, rev)
        by_path = {e.path: e for e in entries}
        if not GENERATED <= set(by_path) or paths.VAULT_MARKER not in by_path:
            raise RepoSyncError("policy_missing", f"refused: mirror commit {rev[:12]} lacks its generated files")
        theirs = policy_from_config_text(blobs.read(by_path[paths.VAULT_MARKER].sha).decode("utf-8", "replace"))
        folders = _folders([e.path for e in entries if e.mode != SYMLINK_MODE])
        bad = 0
        for e in entries:
            data = blobs.read(e.sha) if e.kind == "blob" else b""
            if e.path in GENERATED:
                kind, text = _content(e.path, data)
                bad += kind != "text" or bool(text is not None and scan.lines(text, prose=False))
                continue
            if e.kind != "blob" or e.path not in allowed or seen.rules.refused(e.path) or scan.names(e.path):
                bad += 1
                continue
            if e.mode == SYMLINK_MODE:
                target = _link_target(e, data)
                bad += target is None or target not in folders
                continue
            kind, text = _content(e.path, data)
            if kind == "opaque" or (text is not None and scan.lines(text, e.path)):
                bad += 1
                continue
            head_text = decode_head(data[:HEAD_BYTES]) if e.path.lower().endswith(".md") else None
            ours, mirrored = classify(e.path, head_text, seen.policy), classify(e.path, head_text, theirs)
            if not (visible_to(ours, reader, seen.policy) and visible_to(mirrored, reader, theirs)):
                bad += 1
            elif e.path != paths.VAULT_MARKER and not _same_label(ours, mirrored):
                bad += 1
        if bad:
            raise RepoSyncError(
                "delegate_mirror_unsafe",
                f"refused: mirror commit {rev[:12]} would show {delegate} {bad} file(s) it may not see; "
                "nothing was pushed",
            )
    finally:
        blobs.close()


# ----- link, sync, unlink --------------------------------------------------------------------


@dataclass
class Result:
    delegate: str
    action: str
    pushed: str | None = None
    files: int = 0
    withheld: int = 0
    redacted_files: int = 0
    redacted_lines: int = 0
    left_out: int = 0
    unchanged: bool = False
    in_history: int = 0
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _head(vault: Path) -> str:
    head = _rev(vault, "HEAD")
    if head is None:
        raise RepoSyncError("no_commits", "refused: the vault has no commits yet")
    return head


def _check_id(delegate: str) -> None:
    if not is_delegate_id(delegate):
        raise RepoSyncError("bad_delegate_id", "refused: a delegate id is 1-40 of a-z, 0-9 and -, starting a-z or 0-9")


def trading_gate(vault: Path, rules: LocalOnlyRules) -> None:
    """A delegate mirror takes no trading-looking path that isn't local-only: there is no confirm for delegates."""
    pending = trading_candidates(vault, rules)
    if pending:
        listed = "\n".join(f"  {p}" for p in pending)
        raise RepoSyncError(
            "trading_paths_not_local_only",
            "refused: these look like trading or brokerage data and aren't local-only:\n"
            f"{listed}\n"
            "Keep each on this computer first (scoutctl bootstrap privacy-migrate --local-only '<path>')",
        )


def _build_and_push(
    vault: Path,
    link: DelegateLink,
    *,
    fresh: bool,
    expect: str | None,
    gh: GhRunner,
    record: Callable[[DelegateLink], None],
) -> tuple[Result, DelegateLink]:
    head = _head(vault)
    blobs = BlobReader(vault)
    try:
        the_plan, _policy, rules, _scan = plan(vault, head, link.id, blobs)
    finally:
        blobs.close()
    if paths.VAULT_MARKER not in {f.path for f in the_plan.files}:
        raise RepoSyncError(
            "config_not_mirrored",
            f"refused: {paths.VAULT_MARKER} wouldn't reach the mirror, so the engine there would show "
            f"{link.id} nothing",
        )
    trading_gate(vault, rules)
    tree = write_tree(vault, the_plan)
    result = Result(
        delegate=link.id,
        action="link" if fresh else "sync",
        files=len(the_plan.files),
        withheld=the_plan.withheld,
        redacted_files=len(the_plan.redacted),
        redacted_lines=sum(the_plan.redacted.values()),
        left_out=the_plan.left_out,
    )
    parent = None if fresh else link.last_mirror
    if parent is not None and git(vault, "rev-parse", f"{parent}^{{tree}}").stdout.strip() == tree:
        result.unchanged = True
        return result, dataclasses.replace(link, last_vault=head, last_sync=_now())
    if parent is not None:
        before = set(git(vault, "ls-tree", "-r", "-z", "--name-only", parent).stdout.split("\0")) - {""}
        result.in_history = len(before & the_plan.hidden_paths)
    args = ["commit-tree", "--no-gpg-sign", tree] + (["-p", parent] if parent else [])
    commit = git(vault, *args, env=IDENTITY, input=MESSAGE).stdout.strip()
    check_tree(vault, commit, link.id, head)
    check_remote(link.remote, vault=vault, gh=gh)
    git(vault, "update-ref", mirror_ref(link.id), commit)  # keeps its objects while it is pending
    record(dataclasses.replace(link, pending_mirror=commit))
    push(vault, link.remote, commit, expect)
    result.pushed = commit
    if result.in_history:
        result.notes.append(
            f"{result.in_history} file(s) left {link.id}'s mirror but stay in its history; to start it over: "
            f"scoutctl delegate link {link.id} --remote <url> --replace-remote --confirm-account-unshared"
        )
    done = dataclasses.replace(link, last_mirror=commit, last_vault=head, last_sync=_now(), pending_mirror=None)
    return result, done


def link(
    vault: Path,
    delegate: str,
    remote: str,
    *,
    account_unshared: bool,
    replace_remote: bool = False,
    create_repo: str | None = None,
    env: Mapping[str, str] | None = None,
    gh: GhRunner = _gh,
) -> Result:
    """Link ``delegate`` to its own private remote and push the first mirror commit.

    With ``create_repo`` (``owner/name``) the private repository is made with
    ``gh`` first, after every check that doesn't need it has passed, and
    ``remote`` must be empty.

    The owner only, at the keyboard. Needs the same gates as the Agnes link (the
    privacy migration, the widening guard), the owner's confirmation that no one
    else uses the agent's account (spec §4.2), and an empty remote unless
    ``replace_remote``.
    """
    env = os.environ if env is None else env
    vault = vault.resolve()
    _check_id(delegate)
    headless_gate(env, "Linking a delegate mirror")
    if not account_unshared:
        raise RepoSyncError(
            "account_unconfirmed",
            "refused: confirm with --confirm-account-unshared that only you use the agent's account: on Grok Bot "
            "every Bot on an account shares one computer, so a Team Bot there could read the mirror (spec §4.2)",
        )
    widening_guard_gate(vault, env)
    migration_gate(vault)
    with locked(vault):
        if create_repo is not None:
            if remote:
                raise RepoSyncError("bad_arguments", "refused: pass --remote or --create-repo, not both")
            if load_policy(vault).delegate(f"delegate:{delegate}") is None:
                raise RepoSyncError(
                    "delegate_not_listed",
                    f"refused: {delegate} isn't listed under privacy.delegates in scout-config.yaml",
                )
            remote = make_repo(create_repo, gh=gh)
        check_remote(remote, vault=vault, gh=gh)
        links = load_links(vault)
        current = remote_head(vault, remote)
        if current is not None and not replace_remote:
            raise RepoSyncError(
                "remote_not_empty",
                "refused: the remote already has a main branch; pass --replace-remote to start it over with this "
                "mirror",
            )
        ignored = ensure_ignored(vault)

        def record(pending: DelegateLink) -> None:
            save_links(vault, {**links, delegate: pending})

        old = links.get(delegate)
        new = DelegateLink(
            id=delegate,
            remote=remote,
            linked_at=_now(),
            account_unshared=True,
            deploy_key_id=old.deploy_key_id if old is not None and old.remote == remote else None,
        )
        result, updated = _build_and_push(vault, new, fresh=True, expect=current, gh=gh, record=record)
        links[delegate] = updated
        if ignored:
            result.notes.append(f"added {STATE_REL} to .gitignore")
        save_links(vault, links)
    if updated.deploy_key_id is None and github_repo(remote) is not None:
        result.notes.append(f"next: scoutctl delegate key {delegate} makes the agent's read-only deploy key")
    return result


def _adopt_pending(vault: Path, current: DelegateLink) -> DelegateLink:
    """A sync that died after its push: if the remote holds the pending commit, it is the last mirror."""
    if current.pending_mirror is None:
        return current
    remote = remote_head(vault, current.remote)
    if remote == current.pending_mirror:
        return dataclasses.replace(current, last_mirror=remote, pending_mirror=None)
    if remote == current.last_mirror:
        return dataclasses.replace(current, pending_mirror=None)
    return current


def sync(vault: Path, delegate: str, *, gh: GhRunner = _gh) -> Result:
    """Push a new mirror commit for ``delegate`` when the vault's HEAD changed what it may see."""
    vault = vault.resolve()
    with locked(vault):
        links = load_links(vault)
        current = links.get(delegate)
        if current is None:
            raise RepoSyncError("not_linked", f"refused: {delegate} has no mirror; run scoutctl delegate link")
        current = _adopt_pending(vault, current)
        links[delegate] = current

        def record(pending: DelegateLink) -> None:
            save_links(vault, {**links, delegate: pending})

        result, updated = _build_and_push(vault, current, fresh=False, expect=current.last_mirror, gh=gh, record=record)
        links[delegate] = updated
        save_links(vault, links)
    return result


def sync_all(vault: Path, *, gh: GhRunner = _gh) -> tuple[list[Result], dict[str, RepoSyncError]]:
    """Sync every linked delegate; a failure of one doesn't stop the rest."""
    done: list[Result] = []
    failed: dict[str, RepoSyncError] = {}
    for delegate in sorted(load_links(vault.resolve())):
        try:
            done.append(sync(vault, delegate, gh=gh))
        except RepoSyncError as e:
            failed[delegate] = e
    return done, failed


def unlink(vault: Path, delegate: str, *, env: Mapping[str, str] | None = None, gh: GhRunner = _gh) -> Result:
    """Forget ``delegate``'s mirror on this computer, revoking its deploy key first. The repository is kept."""
    env = os.environ if env is None else env
    vault = vault.resolve()
    headless_gate(env, "Unlinking a delegate mirror")
    result = Result(delegate=delegate, action="unlink")
    with locked(vault):
        links = load_links(vault)
        current = links.get(delegate)
        if current is None:
            raise RepoSyncError("not_linked", f"refused: {delegate} has no mirror")
        repo = github_repo(current.remote)
        if current.deploy_key_id is not None and repo is not None:
            _delete_key(repo, current.deploy_key_id, gh=gh)
            result.notes.append("revoked the agent's deploy key")
        _remove_key_files(vault, delegate)
        del links[delegate]
        git(vault, "update-ref", "-d", mirror_ref(delegate), check=False)
        save_links(vault, links)
    result.notes.append(
        "the repository is kept; delete it on GitHub, and revoke any other access you gave the agent there"
    )
    return result


# ----- the repository and the agent's key (V2b) -----------------------------------------------


def make_repo(repo: str, *, gh: GhRunner = _gh) -> str:
    """Create the private GitHub repository ``owner/name``; its https remote URL."""
    if not _REPO_NAME.match(repo):
        raise RepoSyncError("bad_arguments", "refused: --create-repo takes owner/name")
    me = _ask_gh(gh, ["api", "user", "--jq", ".login"], repo).strip()
    if me.casefold() != repo.split("/", 1)[0].casefold():
        raise RepoSyncError(
            "remote_org_owned",
            f"refused: --create-repo makes the mirror only under your own GitHub user ({me or 'unknown'}), "
            "not an organisation or another account",
        )
    proc = gh(["repo", "create", repo, "--private", "--description", "Scout delegate mirror"])
    if proc.returncode != 0:
        why = (proc.stderr or proc.stdout).strip().splitlines()
        raise RepoSyncError(
            "repo_create_failed", f"refused: gh couldn't create {repo}: {why[-1] if why else 'gh failed'}"
        )
    return f"https://github.com/{repo}.git"


Keygen = Callable[[Path, str], None]


def _keygen(path: Path, comment: str) -> None:
    proc = subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", comment, "-f", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RepoSyncError("keygen_failed", f"refused: ssh-keygen failed: {proc.stderr.strip()}")


def _key_title(delegate: str) -> str:
    return f"scout-delegate-{delegate}"


def _delete_key(repo: str, key_id: int, *, gh: GhRunner) -> None:
    proc = gh(["api", "-X", "DELETE", f"repos/{repo}/keys/{key_id}"])
    if proc.returncode != 0 and "Not Found" not in proc.stdout + proc.stderr:
        why = (proc.stderr or proc.stdout).strip().splitlines()
        raise RepoSyncError(
            "key_revoke_failed",
            f"refused: couldn't delete deploy key {key_id} on {repo}: "
            f"{why[-1] if why else 'gh failed'}; delete it in the repository's settings, then retry",
        )


def _remove_key_files(vault: Path, delegate: str) -> None:
    key = key_path(vault, delegate)
    for p in (key, key.with_name(key.name + ".pub")):
        p.unlink(missing_ok=True)


def deploy_key(
    vault: Path,
    delegate: str,
    *,
    rotate: bool = False,
    revoke: bool = False,
    env: Mapping[str, str] | None = None,
    gh: GhRunner = _gh,
    keygen: Keygen = _keygen,
) -> Result:
    """Give ``delegate``'s agent a read-only deploy key on its mirror repository, or rotate or revoke it."""
    env = os.environ if env is None else env
    vault = vault.resolve()
    headless_gate(env, "Changing a delegate's key")
    with locked(vault):
        return _deploy_key(vault, delegate, rotate=rotate, revoke=revoke, gh=gh, keygen=keygen)


def _deploy_key(vault: Path, delegate: str, *, rotate: bool, revoke: bool, gh: GhRunner, keygen: Keygen) -> Result:
    links = load_links(vault)
    current = links.get(delegate)
    if current is None:
        raise RepoSyncError("not_linked", f"refused: {delegate} has no mirror; run scoutctl delegate link")
    repo = github_repo(current.remote)
    if repo is None:
        raise RepoSyncError("key_needs_github", "refused: a deploy key needs a GitHub mirror repository")
    result = Result(delegate=delegate, action="key")
    if revoke:
        if current.deploy_key_id is None:
            raise RepoSyncError("no_key", f"refused: {delegate} has no deploy key on record")
        _delete_key(repo, current.deploy_key_id, gh=gh)
        _remove_key_files(vault, delegate)
        links[delegate] = dataclasses.replace(current, deploy_key_id=None)
        save_links(vault, links)
        result.notes.append(f"revoked {delegate}'s deploy key; its agent can't fetch the mirror any more")
        return result
    if current.deploy_key_id is not None and not rotate:
        raise RepoSyncError("key_exists", f"refused: {delegate} already has a deploy key; pass --rotate to replace it")
    check_remote(current.remote, vault=vault, gh=gh)
    _private_dir()
    with tempfile.TemporaryDirectory(dir=secrets_dir(), prefix=".new-key-") as tmp:
        new = Path(tmp) / "key"
        keygen(new, _key_title(delegate))
        public = new.with_name("key.pub").read_text(encoding="utf-8").strip()
        proc = gh(
            [
                "api",
                "-X",
                "POST",
                f"repos/{repo}/keys",
                "-f",
                f"title={_key_title(delegate)}",
                "-f",
                f"key={public}",
                "-F",
                "read_only=true",
                "--jq",
                "[.id, .read_only] | @tsv",
            ]
        )
        if proc.returncode != 0:
            why = (proc.stderr or proc.stdout).strip().splitlines()
            raise RepoSyncError(
                "key_add_failed", f"refused: GitHub didn't take the key: {why[-1] if why else 'gh failed'}"
            )
        fields = proc.stdout.split()
        if len(fields) != 2 or not fields[0].isdigit():
            raise RepoSyncError("key_add_failed", "refused: GitHub's answer to the new key didn't parse")
        added = dataclasses.replace(current, deploy_key_id=int(fields[0]))
        if fields[1] != "true":
            _delete_key(repo, int(fields[0]), gh=gh)
            raise RepoSyncError("key_not_read_only", "refused: GitHub recorded the key as writable; it was deleted")
        if current.deploy_key_id is not None:
            try:
                _delete_key(repo, current.deploy_key_id, gh=gh)
            except RepoSyncError as e:
                result.notes.append(e.message.replace("refused: ", "warning: ", 1))
        key = key_path(vault, delegate)
        _remove_key_files(vault, delegate)
        shutil.move(str(new), key)
        shutil.move(str(new.with_name("key.pub")), key.with_name(key.name + ".pub"))
        os.chmod(key, 0o600)
    links[delegate] = added
    save_links(vault, links)
    result.notes += [
        f"{'replaced' if rotate and current.deploy_key_id else 'added'} a read-only deploy key on {repo}",
        f"private key: {key}; put it in the agent's secret store, then delete it here if you like",
        "the agent clones with: GIT_SSH_COMMAND='ssh -i <key file> -o IdentitiesOnly=yes' "
        f"git clone --depth 1 git@github.com:{repo}.git",
    ]
    return result


def preview(vault: Path, delegate: str) -> Result:
    """What a sync of HEAD would put in ``delegate``'s mirror. Writes nothing; no network."""
    vault = vault.resolve()
    _check_id(delegate)
    blobs = BlobReader(vault)
    try:
        the_plan = plan(vault, _head(vault), delegate, blobs)[0]
    finally:
        blobs.close()
    return Result(
        delegate=delegate,
        action="preview",
        files=len(the_plan.files),
        withheld=the_plan.withheld,
        redacted_files=len(the_plan.redacted),
        redacted_lines=sum(the_plan.redacted.values()),
        left_out=the_plan.left_out,
    )
