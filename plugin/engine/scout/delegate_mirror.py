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
- Any file that decodes as UTF-8 is text and is scanned, whatever its
  extension. A file that isn't UTF-8 text is left out, since it can't be
  scanned, unless its extension is a known binary type (:data:`BINARY_TYPES`),
  which is kept on its label alone.
- ``scout-config.yaml`` is rewritten (:func:`mirror_config`): ``privacy``
  keeps only known keys, and of their path entries only those that match a
  mirrored file, so no local-only or hidden folder is named; ``delegates``
  lists only this delegate; any other top-level key that names a hidden note
  is dropped.
- A symlink is kept only when it points at a folder in the mirror, checked
  after every other file is settled.

A file stays only when the policy the clone will compute from that config gives
it the same label. ``.scout-state/privacy-policy.json`` holds that policy and
every file's label; ``.scout-state/delegate-mirror.json`` holds counts (files
kept, files withheld, lines removed per file), never removed text.

What the matchers can't see: a note whose name is short (under 4 characters)
or one ordinary word is matched only by its wikilinks and its path, as for
``kb check-outbound``; prose that names it otherwise stays. A hidden file's path
counts as naming it, matched exactly (``-`` and ``_`` are not folded together):
in a folder, as written (``Clients/Acme``); at the vault root, only by its full
name with its extension (``health.md``). A root name with no extension
(``knowledge_base``) and a hidden symlink are matched only by an exact link.
A sync that leaves out most of the files the delegate may see says so.

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
import functools
import hashlib
import hmac
import json
import os
import posixpath
import re
import shutil
import stat
import subprocess
import tempfile
import unicodedata
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import yaml

import scout
from scout import paths
from scout.kb.lint_config import glob_match
from scout.kb.outbound import (
    _AFTER,
    _BEFORE,
    PrivateNote,
    PrivateTerms,
    find_mentions,
    note_from_text,
    terms_of,
    with_shared,
)
from scout.kb.privacy import (
    HEAD_BYTES,
    DelegateRule,
    Label,
    NoteFacts,
    Policy,
    classifier_block,
    decode_head,
    is_delegate_id,
    label_from_facts,
    load_frontmatter_yaml,
    load_policy,
    load_yaml,
    note_facts,
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
TICK_REL = ".scout-state/delegate-sync.tick"  # when the schedule tick last started a sync (its mtime)
SCAN_DIR = ".scout-state/delegate-scan"  # per delegate: what each file's scan gave, under one fingerprint
SYNC_INTERVAL = timedelta(minutes=15)
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
    last_attempt: str | None = None
    # The last failed sync, until one succeeds: {"code", "at", "first_at" (since when this code), "head",
    # "inputs" (what the vault looked like then, so an unchanged vault retries on a backoff, not every tick)}.
    last_error: dict[str, str] | None = None
    # Old deploy keys GitHub didn't delete when they were replaced: still live, retried by key, sync and unlink.
    stale_key_ids: list[int] = field(default_factory=list)
    key_file: str | None = None  # where the agent's private key was written (outside the vault)
    checked_fingerprint: str | None = None  # the scan fingerprint check_tree passed last_mirror under
    last_inputs: str | None = None  # the live config and shared-reads ledger at the last good sync (sync_inputs)

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
            last_attempt=data.get("last_attempt"),
            last_error=data.get("last_error"),
            stale_key_ids=[int(k) for k in data.get("stale_key_ids") or []],
            key_file=data.get("key_file"),
            checked_fingerprint=data.get("checked_fingerprint"),
            last_inputs=data.get("last_inputs"),
        )


def state_path(vault: Path) -> Path:
    return vault / STATE_REL


STATE_KEY = "state.key"  # in secrets_dir(): signs what Scout writes to .scout-state, so nothing else can forge it


VAULT_SALT = "vault.salt"  # in secrets_dir(): makes each vault's mark (_vault_mark) unguessable from its path


def _state_key(*, create: bool) -> bytes | None:
    """This computer's key for :func:`_sign`, made on first use (0600, in the 0700 secrets folder)."""
    return _secret(STATE_KEY, create=create)


def _vault_mark(vault: Path, *, create: bool = False) -> str:
    """Which vault a mirror is of, as its manifest records it: this computer's salt and the vault's real path.

    A run can read the salt but can't move the vault, so it can't make one vault's
    mark match another's; a delegate can't confirm a guessed path without the salt.
    A vault that moves gets a new mark: its mirrors are re-linked with --replace-remote.

    Only a build that will push makes the salt (``create=True``, in
    :func:`_build_and_push`); everything else reads it, so a preview writes
    nothing. No salt yet means no mark yet: ``""``, which no manifest matches.
    """
    salt = _secret(VAULT_SALT, create=create)
    if salt is None:
        return ""
    return hashlib.sha256(salt + b"\0" + str(vault.resolve()).encode("utf-8")).hexdigest()[:32]


def _secret(name: str, *, create: bool) -> bytes | None:
    """A random 32-byte secret in the secrets folder, made on first use (0600, O_EXCL)."""
    path = secrets_dir() / name
    try:
        return bytes.fromhex(path.read_text(encoding="utf-8").strip())
    except FileNotFoundError:
        if not create:
            return None
    except (OSError, ValueError):
        return None
    _private_dir()
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:  # another process made it first
        return _secret(name, create=False)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(os.urandom(32).hex() + "\n")
    return _secret(name, create=False)


def _sign(key: bytes, kind: str, doc: Mapping[str, Any]) -> str:
    body = json.dumps(doc, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hmac.new(key, kind.encode("utf-8") + b"\0" + body, hashlib.sha256).hexdigest()


def _verified(kind: str, data: Any) -> dict[str, Any] | None:
    """``data`` without its signature when this computer's Scout signed it as ``kind``, else None.

    The link state and the scan memo live in the vault's ``.scout-state``, where a
    run, a replayed commit or a hand edit can write. What the build trusts in them
    (the last mirror and the fingerprint it passed under, the remote, the key file)
    counts only when the signature, made with a key outside the vault, matches.
    """
    if not isinstance(data, dict) or not isinstance(data.get("mac"), str):
        return None
    key = _state_key(create=False)
    body = {k: v for k, v in data.items() if k != "mac"}
    if key is None or not hmac.compare_digest(data["mac"], _sign(key, kind, body)):
        return None
    return body


def load_links(vault: Path) -> dict[str, DelegateLink]:
    path = state_path(vault)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise RepoSyncError("state_unreadable", f"refused: {STATE_REL} can't be read ({e}); fix or delete it") from e
    body = _verified("links", data)
    if body is None:
        raise RepoSyncError(
            "state_unverified",
            f"refused: {STATE_REL} wasn't written by Scout on this computer (its signature doesn't match), so "
            "nothing in it is trusted; check each link and sign it again with: scoutctl delegate trust-state",
        )
    try:
        return {k: DelegateLink.from_json(v) for k, v in body["links"].items()}
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        raise RepoSyncError("state_unreadable", f"refused: {STATE_REL} can't be read ({e}); fix or delete it") from e


def save_links(vault: Path, links: Mapping[str, DelegateLink]) -> None:
    path = state_path(vault)
    path.parent.mkdir(parents=True, exist_ok=True)
    key = _state_key(create=True)
    assert key is not None
    doc: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "links": {k: v.to_json() for k, v in sorted(links.items())},
    }
    doc["mac"] = _sign(key, "links", doc)
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
    rels = {STATE_REL: STATE_REL, LOCK_REL: LOCK_REL, TICK_REL: TICK_REL, f"{SCAN_DIR}/x.json": f"{SCAN_DIR}/"}
    missing = [line for probe, line in rels.items() if git(vault, "check-ignore", "-q", probe, check=False).returncode]
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
GH_TIMEOUT = 60  # seconds for one gh call: GitHub's API answers in well under that, or something hangs
GIT_TIMEOUT = 1800  # seconds for one git call to a mirror remote; a first push of a large vault takes a while
LOW_SPEED = ("1000", "60")  # http.lowSpeedLimit bytes/s for http.lowSpeedTime seconds: a stalled push gives up


def _gh(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "GH_REPO"}
    env["GH_HOST"] = "github.com"  # gh asks about the same host git pushes to
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=False, env=env, timeout=GH_TIMEOUT)


def github_repo(remote: str) -> str | None:
    """``owner/name`` for a GitHub remote URL, else None."""
    m = _GITHUB.match(remote.strip())
    return m.group("repo") if m else None


def _local_remote(remote: str) -> Path | None:
    raw = remote[len("file://") :] if remote.startswith("file://") else remote
    if "://" in raw or re.match(r"^[^/]+@[^/]+:", raw) or not raw.startswith(("/", "~")):
        return None
    return Path(raw).expanduser()


def _run_gh(gh: GhRunner, args: Sequence[str], repo: str) -> subprocess.CompletedProcess[str]:
    """Run ``gh``; a missing or unrunnable ``gh`` is a refusal, never a traceback."""
    try:
        return gh(args)
    except subprocess.TimeoutExpired as e:
        raise RepoSyncError("remote_unverified", f"refused: gh didn't answer about {repo} in {e.timeout:g}s") from e
    except OSError as e:
        raise RepoSyncError("remote_unverified", f"refused: can't ask GitHub about {repo} (gh: {e})") from e


def _ask_gh(gh: GhRunner, args: Sequence[str], repo: str) -> str:
    proc = _run_gh(gh, args, repo)
    if proc.returncode != 0:
        why = (proc.stderr or proc.stdout).strip().splitlines()
        raise RepoSyncError(
            "remote_unverified", f"refused: can't ask GitHub about {repo}: {why[-1] if why else 'gh failed'}"
        )
    return proc.stdout


def _check_github(repo: str, gh: GhRunner) -> None:
    jq = "[.visibility, .owner.type, .owner.login, .fork] | @tsv"
    fields = _ask_gh(gh, ["api", f"repos/{repo}", "--jq", jq], repo)
    parts = fields.strip().split("\t")
    if len(parts) != 4 or parts[3] not in ("true", "false"):
        raise RepoSyncError("remote_unverified", f"refused: GitHub's answer about {repo} didn't parse")
    visibility, owner_type, owner, fork = parts
    if fork == "true":
        raise RepoSyncError(
            "remote_fork",
            f"refused: {repo} is a fork, and the people with access to the repository it was forked from can "
            "reach it; make a new private repository for the mirror",
        )
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
    """Refuse a remote git would rewrite. Read under :func:`remote_env`, the environment the push runs in,
    so the config checked (``GIT_CONFIG_GLOBAL``, ``GIT_CONFIG_SYSTEM`` and all) is the config pushed with."""
    out = _remote_git(vault, remote, "config", "--get-regexp", r"^url\..*\.(push)?insteadof$", check=False).stdout
    for line in out.splitlines():
        _key, _, prefix = line.partition(" ")
        if prefix and remote.startswith(prefix):
            raise RepoSyncError(
                "remote_rewritten",
                f"refused: git rewrites {remote} ({_key}); drop that rewrite so the repository checked is the one "
                "pushed to",
            )
    url = _remote_git(vault, remote, "ls-remote", "--get-url", remote, check=False).stdout.strip()
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
    if _overlaps(local.resolve(), vault.resolve()):
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


_COMMAND_LINE_CONFIG = re.compile(r"^GIT_CONFIG_(COUNT|PARAMETERS|KEY_\d+|VALUE_\d+)$")


def remote_env(vault: Path, remote: str) -> dict[str, str]:
    """The environment for git talking to a mirror remote: certificate checks forced on, the push token added.

    ``GIT_SSL_NO_VERIFY`` is dropped and ``http.sslVerify`` set true, for every URL and for this remote's own
    (the most specific match, so a user's ``http.<url>.sslVerify false`` can't win), so the token never goes
    out over a connection that isn't verified. Inherited command-line config (``GIT_CONFIG_COUNT`` and its
    ``GIT_CONFIG_KEY_<n>``/``GIT_CONFIG_VALUE_<n>`` pairs, ``GIT_CONFIG_PARAMETERS``) is replaced by these;
    which config files git reads (``GIT_CONFIG_GLOBAL``, ``GIT_CONFIG_SYSTEM``, ``GIT_CONFIG_NOSYSTEM``) is
    kept, and :func:`_check_rewrites` reads them in this same environment.
    """
    env = {k: v for k, v in os.environ.items() if k != "GIT_SSL_NO_VERIFY" and not _COMMAND_LINE_CONFIG.match(k)}
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.pop("GIT_INDEX_FILE", None)
    pairs = [("http.sslVerify", "true"), ("http.lowSpeedLimit", LOW_SPEED[0]), ("http.lowSpeedTime", LOW_SPEED[1])]
    if remote.startswith("https://"):
        pairs.append((f"http.{remote}.sslVerify", "true"))
    token = auth_env(vault, remote)
    if token:
        pairs.append((token["GIT_CONFIG_KEY_0"], token["GIT_CONFIG_VALUE_0"]))
    env["GIT_CONFIG_COUNT"] = str(len(pairs))
    for i, (key, value) in enumerate(pairs):
        env[f"GIT_CONFIG_KEY_{i}"] = key
        env[f"GIT_CONFIG_VALUE_{i}"] = value
    return env


def _remote_git(vault: Path, remote: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """``git *args`` in ``vault`` against ``remote``, in :func:`remote_env`."""
    try:
        proc = subprocess.run(
            ["git", "-c", "core.quotepath=false", *args],
            cwd=vault,
            env=remote_env(vault, remote),
            capture_output=True,
            check=False,
            timeout=GIT_TIMEOUT,
        )
    except subprocess.TimeoutExpired as e:
        raise RepoSyncError("remote_timeout", f"refused: git {args[0]} didn't finish in {e.timeout:g}s") from e
    out = subprocess.CompletedProcess(
        proc.args, proc.returncode, proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace")
    )
    if check and proc.returncode != 0:
        raise RepoSyncError("git_failed", f"git {args[0]} failed: {out.stderr.strip() or out.stdout.strip()}")
    return out


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


def _inode(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_dev, st.st_ino


def _overlaps(target: Path, home: Path) -> bool:
    """Whether ``target`` is ``home``, inside it, or a folder above it.

    By file identity, not by name: on a case-insensitive volume (APFS's
    default) ``…/Vault/x.git`` and ``…/vault/x.git`` are the same place.
    """
    if target == home or home in target.parents or target in home.parents:
        return True
    home_id = _inode(home)
    if home_id is not None and any(_inode(p) == home_id for p in (target, *target.parents)):
        return True
    target_id = _inode(target)
    return target_id is not None and any(_inode(p) == target_id for p in home.parents)


def _same_remote(a: str, b: str) -> bool:
    """Whether two remote URLs are the same repository (GitHub by owner/name, a local path by where it is)."""
    ra, rb = github_repo(a), github_repo(b)
    if ra is not None or rb is not None:
        return (ra or "").casefold() == (rb or "").casefold()
    la, lb = _local_remote(a), _local_remote(b)
    if la is not None and lb is not None:
        return la.resolve() == lb.resolve() or (_inode(la) is not None and _inode(la) == _inode(lb))
    return a == b


def _mirror_owner(vault: Path, remote: str, commit: str) -> tuple[str, str] | None:
    """(delegate, vault mark) the manifest in mirror commit ``commit`` names, or None when it holds none.

    A commit that isn't in the vault is fetched into a throwaway bare repository,
    never the vault's own object store: a remote's main is untrusted until this says whose it is.
    """
    if git(vault, "cat-file", "-e", f"{commit}^{{commit}}", check=False).returncode == 0:
        shown = git(vault, "show", f"{commit}:{MANIFEST_PATH}", check=False)
    else:
        with tempfile.TemporaryDirectory(prefix="scout-mirror-check-") as tmp:
            git(Path(tmp), "init", "-q", "--bare")
            _remote_git(
                vault, remote, f"--git-dir={tmp}", "fetch", "--no-tags", "--quiet", remote, REMOTE_BRANCH, check=False
            )
            shown = git(Path(tmp), "show", f"{commit}:{MANIFEST_PATH}", check=False)
    try:
        doc = json.loads(shown.stdout) if shown.returncode == 0 else None
    except ValueError:
        return None
    named, mark = (doc.get("delegate"), doc.get("vault")) if isinstance(doc, dict) else (None, None)
    return (named, mark if isinstance(mark, str) else "") if isinstance(named, str) else None


def bind_remote(
    vault: Path, link: DelegateLink, links: Mapping[str, DelegateLink], *, gh: GhRunner, mode: str
) -> str | None:
    """Refuse unless ``link.remote`` is this delegate's own mirror; the remote's current main (None: empty).

    Checked when used, whatever the (signed) link state says, so state forged by
    anything that can read the signing key still can't point one delegate's
    push, or a key deletion, at another repository:

    - no other link may use the same remote;
    - the remote's main must hold a mirror manifest naming this delegate and
      this vault (:func:`_vault_mark`). At ``mode="link"`` an empty remote, or
      one that isn't a Scout mirror, is allowed; at ``mode="replace"`` (link
      with ``--replace-remote``, the owner at the keyboard) also this delegate's
      mirror from another vault or an older engine; never another delegate's;
    - for ``mode="push"`` with a deploy key on record, the GitHub repository
      must have a key titled for this delegate.
    """
    for other_id, other in links.items():
        if other_id != link.id and _same_remote(other.remote, link.remote):
            raise RepoSyncError(
                "remote_taken",
                f"refused: {link.remote} is {other_id}'s mirror too; each delegate needs its own repository",
            )
    head = remote_head(vault, link.remote)
    owner = _mirror_owner(vault, link.remote, head) if head is not None else None
    ours = owner == (link.id, _vault_mark(vault))
    if not (
        ours
        or (mode in ("link", "replace") and owner is None)
        or (mode == "replace" and owner is not None and owner[0] == link.id)
    ):
        if owner is None:
            found = "empty" if head is None else "not a Scout delegate mirror"
        elif owner[0] != link.id:
            found = f"{owner[0]}'s mirror"
        else:
            found = f"{link.id}'s mirror of another vault, or from before mirrors recorded their vault"
        raise RepoSyncError(
            "remote_unbound",
            f"refused: {link.remote} is {found}, not this vault's {link.id} mirror; nothing was pushed or deleted. "
            f"If it is this vault's (moved, or linked before), start it over with: scoutctl delegate link {link.id} "
            f"--remote {link.remote} --replace-remote --confirm-account-unshared",
        )
    repo = github_repo(link.remote)
    if mode == "push" and repo is not None and link.deploy_key_id is not None:
        titles = _ask_gh(gh, ["api", "--paginate", f"repos/{repo}/keys", "--jq", ".[].title"], repo).split()
        if _key_title(link.id) not in titles:
            raise RepoSyncError(
                "remote_unbound",
                f"refused: {repo} has no deploy key for {link.id}, though one is on record; check the link with: "
                "scoutctl delegate trust-state",
            )
    return head


def remote_head(vault: Path, remote: str) -> str | None:
    out = _remote_git(vault, remote, "ls-remote", remote, REMOTE_BRANCH).stdout.split()
    return out[0] if out else None


def push(vault: Path, remote: str, commit: str, expect: str | None) -> None:
    """Move the remote's ``main`` from ``expect`` (None: it must not exist) to ``commit``, or refuse."""
    lease = f"--force-with-lease={REMOTE_BRANCH}:{expect or ''}"
    proc = _remote_git(vault, remote, "push", "--porcelain", lease, remote, f"{commit}:{REMOTE_BRANCH}", check=False)
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


def _stem(rel: str) -> str:
    return rel[:-3] if rel.lower().endswith(".md") else rel


def _path_term(rel: str) -> str | None:
    """What of the hidden path ``rel`` counts as naming it in text, matched exactly (no ``-``/``_`` folding).

    In a folder, the path as written (``clients/acme-renewal``). At the vault root, the full file name
    (``health.md``), never a bare word: a root note's name is matched by its own rules (length, ordinary
    words), and a root file with no extension (``knowledge_base``, often a symlink) only by an exact link.
    Hidden symlinks make no path term at all (:class:`Scanner`): the files behind them do, and the symlink's
    own name is caught only by an exact link to it.
    """
    if "/" in rel:
        return _stem(rel)
    return rel if posixpath.splitext(rel)[1] else None


def _plumbing(rel: str) -> bool:
    """A path under a top-level dot folder (``.scout-state``, ``.obsidian``): its name is no secret."""
    return rel.startswith(".")


class Scanner:
    """Finds what names a file the delegate can't see: the outbound matchers, plus hidden vault paths."""

    def __init__(self, terms: PrivateTerms, hidden: Sequence[str] = (), links: Collection[str] = ()) -> None:
        named = [p for p in hidden if not _plumbing(p)]
        stems = sorted({_stem(p) for p in named})
        self.terms = terms
        path_notes = [
            PrivateNote(
                path=f"path:{t}",
                slug=t,
                terms=(t,),
                link_keys=frozenset(),
                link_path="",
                pattern=re.compile(_BEFORE + re.escape(_fold_path(t)) + _AFTER),
            )
            for t in sorted({t for t in (_path_term(p) for p in named if p not in links) if t is not None})
        ]
        if path_notes:
            # The pre-check is the union of patterns, not rebuilt from terms: the citing form and the
            # shared paths (:func:`with_shared`) have patterns but no terms.
            pre = [x.pattern for x in [terms.any_term, *(n.pattern for n in path_notes)] if x is not None]
            self.terms = dataclasses.replace(
                terms, notes=(*terms.notes, *path_notes), any_term=re.compile("|".join(pre))
            )
        self.hidden = frozenset(_fold_path(s) for s in [*named, *stems])

    def _link_hit(self, target: str, rel: str) -> bool:
        target = unquote(target).split("#", 1)[0].split("?", 1)[0]
        if not target or "://" in target or target.startswith("mailto:"):
            return False
        here = posixpath.dirname(rel)
        candidates = {posixpath.normpath(posixpath.join(here, target)), posixpath.normpath(target.lstrip("/"))}
        return any(_fold_path(c) in self.hidden for c in candidates)

    def lines(self, text: str, rel: str = "") -> list[int]:
        """Sorted 1-based numbers of the lines of ``text`` that name a hidden file."""
        out = set(find_mentions(text, self.terms, allow_markers=False))
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
    fingerprint: str = ""  # what the scan depended on (:func:`scan_fingerprint`)
    vault: str = ""  # the vault's mark (:func:`_vault_mark`), recorded in the manifest
    written: dict[str, str] = field(default_factory=dict)  # path -> blob sha of each new (redacted) blob

    def manifest(self) -> bytes:
        doc = {
            "schema_version": SCHEMA_VERSION,
            "generator": "scoutctl delegate sync",
            "engine_version": scout.__version__,
            "delegate": self.delegate,
            "vault": self.vault,
            "files": len(self.files),
            "withheld_files": self.withheld + self.left_out,
            "redacted_lines": dict(sorted(self.redacted.items())),
        }
        return (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode("utf-8")

    def policy_document(self) -> bytes:
        assert self.mirror_policy is not None
        return policy_document(self.mirror_policy, self.labels)


def policy_document(policy: Policy, labels: Sequence[Label]) -> bytes:
    """``.scout-state/privacy-policy.json``: the clone's policy and each file's label under it."""
    doc = {
        "schema_version": SCHEMA_VERSION,
        "generator": "scoutctl delegate sync",
        "engine_version": scout.__version__,
        "policy": policy.to_json(),
        "labels": [lb.to_json() for lb in sorted(labels, key=lambda lb: lb.path)],
    }
    return (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode("utf-8")


_MANIFEST_KEYS = frozenset(
    {"schema_version", "generator", "engine_version", "delegate", "vault", "files", "withheld_files", "redacted_lines"}
)


def _manifest_ok(data: bytes, delegate: str, tree_paths: set[str], vault: str = "") -> bool:
    """Whether ``data`` is a manifest :meth:`Plan.manifest` could have written: constants, counts, tree paths."""
    try:
        doc = json.loads(data)
    except ValueError:
        return False
    if not isinstance(doc, dict) or set(doc) != _MANIFEST_KEYS or not isinstance(doc["redacted_lines"], dict):
        return False
    if data != (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode("utf-8"):
        return False  # only the bytes Plan.manifest writes: no duplicate key can carry a first value
    counts = [doc["files"], doc["withheld_files"], *doc["redacted_lines"].values()]
    return (
        (doc["schema_version"], doc["generator"], doc["engine_version"], doc["delegate"], doc["vault"])
        == (SCHEMA_VERSION, "scoutctl delegate sync", scout.__version__, delegate, vault)
        and set(doc["redacted_lines"]) <= tree_paths
        and all(type(n) is int and n >= 0 for n in counts)
    )


def _content(rel: str, data: bytes) -> tuple[str, str | None]:
    """``("text", text)`` for UTF-8 text, ``("binary", None)`` for a known binary type, else ``("opaque", None)``.

    The bytes decide before the extension: a ``.pdf`` that is really UTF-8 text is text, and is scanned.
    """
    text: str | None = None
    if b"\0" not in data:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            pass
    if text is not None:
        return "text", text
    if posixpath.splitext(rel.lower())[1] in BINARY_TYPES:
        return "binary", None
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
    return Scanner(terms, hidden, links={rel for rel in hidden if by_path[rel].mode == SYMLINK_MODE})


def _link_target(entry: TreeEntry, target: bytes) -> str | None:
    """The vault path a relative symlink at ``entry`` points at, or None when it leaves the vault."""
    name = target.decode("utf-8", errors="replace")
    if name.startswith("/"):
        return None
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(entry.path), name))
    return None if resolved in (".", "..") or resolved.startswith("../") else resolved


@functools.lru_cache(maxsize=16384)
def _facts(text: str | None) -> NoteFacts:
    """:func:`~scout.kb.privacy.note_facts`, memoised: it is pure and policy-free, and a sync asks it about the
    same note up to five times (each pass labels each file under one or two policies)."""
    return note_facts(text)


def _classify(rel: str, text: str | None, policy: Policy) -> Label:
    """:func:`~scout.kb.privacy.classify`, parsing each note's frontmatter once."""
    return label_from_facts(rel, _facts(text), policy)


def _note_head(rel: str, data: bytes) -> str | None:
    """What :func:`classify` reads of a file: a note's first bytes, decoded; nothing for any other file."""
    return decode_head(data[:HEAD_BYTES]) if rel.lower().endswith(".md") else None


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
            head_text = _note_head(e.path, blobs.read(e.sha))
            label = _classify(e.path, head_text, policy)
            if visible_to(label, reader, policy):
                out.visible.append((e, label))
            else:
                out.hidden.add(e.path)
    return out


def scan_fingerprint(vault: Path, policy: Policy, entries: Sequence[TreeEntry], hidden: Collection[str]) -> str:
    """Everything a file's scan result depends on besides its own path and bytes.

    The engine version and matcher settings, the delegate's policy, each hidden
    file (path, blob, mode), the shared-reads ledger, and each hidden note on
    disk (its ``source:`` adds shared notes). Equal fingerprints mean a file with
    the same path and blob scans the same, so its result can be reused.
    """
    from scout.kb import outbound

    h = hashlib.sha256()
    settings = [scout.__version__, _matcher_source(), outbound.MIN_TERM_CHARS, outbound.UNACCENT_MIN_CHARS]
    settings.append(outbound.UNACCENT_MULTI_WORD)
    h.update(json.dumps([*settings, MAX_PASSES, SCHEMA_VERSION]).encode())
    h.update(json.dumps(policy.to_json(), sort_keys=True, default=str).encode())
    by_path = {e.path: e for e in entries}
    for rel in sorted(hidden):
        e = by_path[rel]
        h.update(f"h\0{rel}\0{e.sha}\0{e.mode}\n".encode())
        if rel.lower().endswith(".md"):
            h.update(f"d\0{_stat_key(vault / rel)}\n".encode())
    ledger = vault / outbound.LEDGER_DIR
    with contextlib.suppress(OSError):
        for f in sorted(ledger.iterdir()):
            h.update(f"l\0{f.name}\0{hashlib.sha256(f.read_bytes()).hexdigest()}\n".encode())
    return h.hexdigest()


@functools.cache
def _matcher_source() -> str:
    """A hash of the code a scan result depends on, once per process: a dev checkout changes it without a version
    bump, and a memo made by other code must not be reused."""
    from scout.kb import outbound, privacy, shared_reads

    h = hashlib.sha256()
    for source in (outbound.__file__, privacy.__file__, shared_reads.__file__, __file__):
        with contextlib.suppress(OSError, TypeError):
            h.update(Path(str(source)).read_bytes())
    return h.hexdigest()


def _stat_key(path: Path) -> str:
    try:
        st = path.stat()
    except OSError:
        return "-"
    return f"{st.st_size}:{st.st_mtime_ns}"


@dataclass
class ScanMemo:
    """One delegate's per-file scan results, valid under one :func:`scan_fingerprint`.

    The build's memo only (:func:`plan`); :func:`check_tree` never reads it.
    ``files[path] = [source blob, kind, mirror blob, lines removed]``, kind
    ``keep``, ``redact`` or ``drop``.
    """

    path: Path
    fingerprint: str = ""
    files: dict[str, list[Any]] = field(default_factory=dict)
    seen: dict[str, list[Any]] = field(default_factory=dict)
    pending: dict[str, tuple[str, int]] = field(default_factory=dict)  # redactions waiting for their blob sha

    delegate: str = ""

    @classmethod
    def load(cls, vault: Path, delegate: str) -> ScanMemo:
        """The memo, or an empty one when it is missing, broken or not signed by this computer's Scout."""
        path = vault / SCAN_DIR / f"{delegate}.json"
        try:
            body = _verified(f"memo:{delegate}", json.loads(path.read_text(encoding="utf-8")))
            if body is None:
                return cls(path, delegate=delegate)
            return cls(path, str(body["fingerprint"]), dict(body["files"]), delegate=delegate)
        except (OSError, ValueError, KeyError, TypeError):
            return cls(path, delegate=delegate)

    def get(self, fingerprint: str, rel: str, sha: str) -> list[Any] | None:
        hit = self.files.get(rel) if fingerprint == self.fingerprint else None
        if hit is None or len(hit) != 4 or hit[0] != sha or hit[1] not in ("keep", "redact", "drop"):
            return None
        return hit

    def put(self, rel: str, entry: list[Any]) -> None:
        self.seen[rel] = entry

    def save(self, the_plan: Plan) -> None:
        """Keep this build's results (the files it saw, nothing stale), once its new blobs are written."""
        for rel, (sha, removed) in self.pending.items():
            if rel in the_plan.written:
                self.seen[rel] = [sha, "redact", the_plan.written[rel], removed]
        doc: dict[str, Any] = {"fingerprint": the_plan.fingerprint, "files": dict(sorted(self.seen.items()))}
        key = _state_key(create=True)
        assert key is not None
        doc["mac"] = _sign(key, f"memo:{self.delegate}", doc)
        with contextlib.suppress(OSError):  # a memo that can't be written only costs a full scan next time
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
            tmp.replace(self.path)


def plan(
    vault: Path, head: str, delegate: str, blobs: BlobReader, *, memo: ScanMemo | None = None
) -> tuple[Plan, Policy, LocalOnlyRules, Scanner]:
    """The mirror of vault commit ``head`` for ``delegate``, and what it was redacted against. Reads only.

    With ``memo``, a file whose path and blob were scanned under the same
    fingerprint reuses that result instead of being scanned again.
    """
    seen = visibility(vault, head, delegate, blobs)
    policy, rules = seen.policy, seen.rules
    out = Plan(delegate=delegate, hidden_paths=set(seen.hidden), vault=_vault_mark(vault))
    out.fingerprint = scan_fingerprint(vault, policy, seen.entries, seen.hidden)
    scan = hidden_terms(vault, seen.entries, sorted(seen.hidden), blobs)
    kept: list[tuple[MirrorFile, Label, str | None]] = []  # file, label, its text when it is text
    config: TreeEntry | None = None
    for e, label in seen.visible:
        if e.path == paths.VAULT_MARKER and not scan.names(e.path):
            config = e
            continue
        hit = memo.get(out.fingerprint, e.path, e.sha) if memo is not None else None
        if hit is not None:
            reused = _reuse(e, label, hit, blobs, policy, out)
            if not isinstance(reused, _Miss):
                assert memo is not None
                memo.put(e.path, hit)
                if reused is not None:
                    kept.append(reused)
                continue
        if scan.names(e.path):
            out.left_out += 1
            if memo is not None:
                memo.put(e.path, [e.sha, "drop", None, 0])
            continue
        made = _mirror_file(e, label, blobs.read(e.sha), scan, policy, out)
        if made is not None:
            kept.append(made)
        if memo is not None:
            if made is None:
                memo.put(e.path, [e.sha, "drop", None, 0])
            elif made[0].data is None:
                memo.put(e.path, [e.sha, "keep", e.sha, 0])
            else:
                memo.pending[e.path] = (e.sha, out.redacted[e.path])
    folders = _folders([f.path for f, _, _ in kept])
    link_targets: dict[str, str] = {}
    for e in seen.links:
        target = _link_target(e, blobs.read(e.sha))
        if target is not None and target in folders and not rules.refused(target) and not scan.names(e.path):
            link_targets[e.path] = target
            kept.append((MirrorFile(e.path, e.mode, None, e.sha), _classify(e.path, None, policy), None))
        else:
            out.hidden_paths.add(e.path)
    # The config lists only what is mirrored, and the clone's policy (from that config) can leave more
    # out, so settle the two together: the config the mirror holds is exactly mirror_config() of its own
    # file list, which check_tree regenerates byte for byte.
    reader = f"delegate:{delegate}"
    include_private = policy.delegates[0].include_private
    config_text = blobs.read(config.sha).decode("utf-8", "replace") if config is not None else None
    for _ in range(MAX_PASSES):
        mirrored = [f.path for f, _, _ in kept] + ([paths.VAULT_MARKER] if config is not None else [])
        candidates = list(kept)
        if config is not None and config_text is not None:
            data = mirror_config(config_text, delegate, include_private, mirrored, scan)
            config_file = MirrorFile(config.path, config.mode, data, "")
            candidates.append((config_file, _classify(config.path, None, policy), None))
            out.mirror_policy = policy_from_config_text(data.decode("utf-8"))
        else:
            out.mirror_policy = policy
        final: list[tuple[MirrorFile, Label]] = []
        relabelled: set[str] = set()
        for f, label, text in candidates:  # the clone's own policy must label each file the same
            head_text = _note_head(f.path, text.encode("utf-8")) if text is not None else None
            theirs = _classify(f.path, head_text, out.mirror_policy)
            if f.mode != SYMLINK_MODE and (
                not _same_label(label, theirs) or not visible_to(theirs, reader, out.mirror_policy)
            ):
                relabelled.add(f.path)
                continue
            final.append((f, theirs))
        # A folder can empty out above, so each symlink is checked again against what is left.
        left = _folders([f.path for f, _ in final if f.mode != SYMLINK_MODE])
        dangling = {f.path for f, _ in final if f.mode == SYMLINK_MODE and link_targets[f.path] not in left}
        settled = [(f, t) for f, t in final if f.path not in dangling]
        if {f.path for f, _ in settled} == set(mirrored) or paths.VAULT_MARKER in relabelled:
            break
        out.left_out += len(relabelled - {paths.VAULT_MARKER})
        out.hidden_paths |= dangling
        kept = [k for k in kept if k[0].path not in relabelled and k[0].path not in dangling]
    else:  # pragma: no cover - each pass only shrinks the list, so it settles well inside MAX_PASSES
        raise RepoSyncError("mirror_unsettled", "refused: the mirror's file list and its config didn't settle")
    out.left_out += len(relabelled - {paths.VAULT_MARKER})
    out.hidden_paths |= dangling
    for f, theirs in settled:
        out.files.append(f)
        out.labels.append(theirs)
    out.withheld = len(out.hidden_paths)
    return out, policy, rules, scan


class _Miss:
    """A memoised result that can't be used: scan the file again."""


def _reuse(
    e: TreeEntry, label: Label, hit: list[Any], blobs: BlobReader, policy: Policy, out: Plan
) -> tuple[MirrorFile, Label, str | None] | None | _Miss:
    """A memoised scan result for ``e``: what it becomes, None when it was left out, :class:`_Miss` when unusable."""
    _sha, kind, mirror_sha, removed = hit
    if kind == "drop":
        out.left_out += 1
        return None
    markdown = e.path.lower().endswith(".md")
    if kind == "keep" and mirror_sha != e.sha:
        return _Miss()
    try:
        data = blobs.read(mirror_sha) if (markdown or kind == "redact") else b""
        if kind == "redact" and not _derives(data, blobs.read(e.sha)):
            return _Miss()  # not this file with lines removed: never trusted
    except RepoSyncError:  # the redacted blob is gone (gc): scan again
        return _Miss()
    text = data.decode("utf-8", "replace") if markdown else None
    if kind == "keep":
        return MirrorFile(e.path, e.mode, None, e.sha), label, text
    out.redacted[e.path] = int(removed)
    new_label = _classify(e.path, _note_head(e.path, data), policy) if markdown else label
    return MirrorFile(e.path, e.mode, None, mirror_sha), new_label, text


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
    new_label = _classify(e.path, new_text[:HEAD_BYTES], policy) if markdown else label
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
        if f.data is not None:
            the_plan.written[f.path] = sha
        lines.append(f"{f.mode} {sha}\t{f.path}")
    lines.append(f"100644 {blob(the_plan.policy_document())}\t{POLICY_PATH}")
    lines.append(f"100644 {blob(the_plan.manifest())}\t{MANIFEST_PATH}")
    return _write_tree(vault, lines)


def _derives(mirror: bytes, source: bytes) -> bool:
    """Whether ``mirror`` is ``source`` with some lines removed (what redaction does), both UTF-8 text."""
    try:
        theirs, ours = mirror.decode("utf-8").split("\n"), iter(source.decode("utf-8").split("\n"))
    except UnicodeDecodeError:
        return False
    return all(any(line == mine for mine in ours) for line in theirs)  # an ordered subsequence


def _config_regenerates(
    mirror: bytes, source: bytes, delegate: str, policy: Policy, tree_paths: Collection[str], scan: Scanner
) -> bool:
    """Whether the mirror's ``scout-config.yaml`` is exactly :func:`mirror_config` of HEAD's for this tree."""
    try:
        expected = mirror_config(
            source.decode("utf-8", "replace"), delegate, policy.delegates[0].include_private, list(tree_paths), scan
        )
    except RepoSyncError:
        return False
    return mirror == expected


def check_tree(vault: Path, rev: str, delegate: str, head: str, *, trusted: tuple[str, str] | None = None) -> str:
    """Refuse when mirror commit ``rev`` holds anything ``delegate`` may not see. Shares nothing with the build.

    What ``HEAD`` shows the delegate and what may not be named are worked out
    again from ``HEAD``; the policy the clone will use is read from the tree's
    own ``scout-config.yaml``. Every path must be one ``HEAD`` shows the
    delegate and must name no hidden file; every file must be HEAD's blob at
    that path, or (redacted) that blob with lines removed, or for the config
    nothing HEAD's config doesn't hold, so no file can carry another file's
    content; every file must be visible under both
    policies with the same label; every text file, the config included, must
    name no hidden file; a file that isn't text must be a known binary type;
    every symlink must point at a folder the tree holds. The generated files
    must be exactly what the tree implies: the policy document is regenerated
    from the tree's config and files and compared byte for byte, and the
    manifest may hold only constants, counts and the tree's own paths.

    ``trusted`` is ``(an earlier mirror commit this check passed, the fingerprint
    it passed under)``. When this check's own fingerprint (worked out from
    ``HEAD``) is the same, a file at the same path with the same blob as in that
    commit isn't scanned for names again; every other check still runs on it.
    Returns this check's fingerprint, to pass back as ``trusted`` next time.
    """
    reader = f"delegate:{delegate}"
    blobs = BlobReader(vault)
    try:
        seen = visibility(vault, head, delegate, blobs)
        fingerprint = scan_fingerprint(vault, seen.policy, seen.entries, seen.hidden)
        scan = hidden_terms(vault, seen.entries, sorted(seen.hidden), blobs)
        checked: set[tuple[str, str]] = set()
        # TODO(#637, Phase 1c): this skip trusts the signed checked_fingerprint, which is sound only while runs
        # can't read ~/.scout-secrets (the signing key). Until #637 gates that folder and Phase 1c sandboxes a
        # run's Bash, a run holding the key can sign a forged fingerprint (and a "keep" memo entry), so a blob
        # already on the mirror isn't rescanned for a name hidden since; it can push there with the owner's git
        # login anyway. If that changes before then, drop the skip (every push rescans). Pinned by
        # test_known_residual_a_key_holder_can_keep_a_newly_hidden_name_on_main.
        if trusted is not None and trusted[1] == fingerprint:
            checked = {(e.path, e.sha) for e in ls_tree(vault, trusted[0])}
        allowed = {e.path for e, _ in seen.visible} | {e.path for e in seen.links}
        entries = ls_tree(vault, rev)
        by_path = {e.path: e for e in entries}
        if not GENERATED <= set(by_path) or paths.VAULT_MARKER not in by_path:
            raise RepoSyncError("policy_missing", f"refused: mirror commit {rev[:12]} lacks its generated files")
        theirs = policy_from_config_text(blobs.read(by_path[paths.VAULT_MARKER].sha).decode("utf-8", "replace"))
        folders = _folders([e.path for e in entries if e.mode != SYMLINK_MODE])
        tree_paths = {e.path for e in entries if e.path not in GENERATED}
        at_head = {e.path: e for e in seen.entries}
        labels: list[Label] = []
        bad = 0
        for e in entries:
            if e.path in GENERATED:
                continue
            data = blobs.read(e.sha) if e.kind == "blob" else b""
            if e.kind != "blob" or e.path not in allowed or seen.rules.refused(e.path) or scan.names(e.path):
                bad += 1
                continue
            source = at_head[e.path]
            if e.mode != source.mode:
                bad += 1
                continue
            if e.sha != source.sha:  # a changed blob must come from HEAD's at this path
                if e.mode == SYMLINK_MODE:
                    bad += 1
                    continue
                at_source = blobs.read(source.sha)
                derived = (
                    _config_regenerates(data, at_source, delegate, seen.policy, tree_paths, scan)
                    if e.path == paths.VAULT_MARKER
                    else _content(e.path, at_source)[0] == "text" and _derives(data, at_source)
                )
                if not derived:
                    bad += 1
                    continue
            if e.mode == SYMLINK_MODE:
                target = _link_target(e, data)
                bad += target is None or target not in folders
                labels.append(_classify(e.path, None, theirs))
                continue
            kind, text = _content(e.path, data)
            scanned_before = (e.path, e.sha) in checked
            if kind == "opaque" or (text is not None and not scanned_before and scan.lines(text, e.path)):
                bad += 1
                continue
            head_text = _note_head(e.path, data) if text is not None else None
            ours, mirrored = _classify(e.path, head_text, seen.policy), _classify(e.path, head_text, theirs)
            labels.append(mirrored)
            if not (visible_to(ours, reader, seen.policy) and visible_to(mirrored, reader, theirs)):
                bad += 1
            elif e.path != paths.VAULT_MARKER and not _same_label(ours, mirrored):
                bad += 1
        bad += blobs.read(by_path[POLICY_PATH].sha) != policy_document(theirs, labels)
        bad += not _manifest_ok(blobs.read(by_path[MANIFEST_PATH].sha), delegate, tree_paths, _vault_mark(vault))
        if bad:
            raise RepoSyncError(
                "delegate_mirror_unsafe",
                f"refused: mirror commit {rev[:12]} would show {delegate} {bad} file(s) it may not see; "
                "nothing was pushed",
            )
        return fingerprint
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
    links: Mapping[str, DelegateLink] | None = None,
    replace: bool = False,
) -> tuple[Result, DelegateLink]:
    head = _head(vault)
    _vault_mark(vault, create=True)  # the first push makes this computer's salt; nothing before it does
    memo = ScanMemo.load(vault, link.id)
    blobs = BlobReader(vault)
    try:
        the_plan, _policy, rules, _scan = plan(vault, head, link.id, blobs, memo=memo)
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
    memo.save(the_plan)
    result = Result(
        delegate=link.id,
        action="link" if fresh else "sync",
        files=len(the_plan.files),
        withheld=the_plan.withheld,
        redacted_files=len(the_plan.redacted),
        redacted_lines=sum(the_plan.redacted.values()),
        left_out=the_plan.left_out,
        notes=_drop_warning(link.id, the_plan),
    )
    parent = None if fresh else link.last_mirror
    parent_tree = git(vault, "rev-parse", "--verify", "-q", f"{parent}^{{tree}}", check=False) if parent else None
    if parent_tree is not None and parent_tree.stdout.strip() == tree:
        result.unchanged = True
        return result, dataclasses.replace(link, last_vault=head, last_sync=_now())
    # The remote must be this delegate's own mirror before anything is built on it: the link state is no proof,
    # since whatever can read the signing key can sign a forged one.
    check_remote(link.remote, vault=vault, gh=gh)
    mode = ("replace" if replace else "link") if fresh else "push"
    bound = bind_remote(vault, link, links or {}, gh=gh, mode=mode)
    if bound != expect:
        raise RepoSyncError(
            "mirror_diverged", f"refused: {link.remote}'s main moved since the last mirror; nothing was pushed"
        )
    if parent is not None:
        before = set(git(vault, "ls-tree", "-r", "-z", "--name-only", parent).stdout.split("\0")) - {""}
        result.in_history = len(before & the_plan.hidden_paths)
    args = ["commit-tree", "--no-gpg-sign", tree] + (["-p", parent] if parent else [])
    commit = git(vault, *args, env=IDENTITY, input=MESSAGE).stdout.strip()
    trusted = (link.last_mirror, link.checked_fingerprint) if link.last_mirror and link.checked_fingerprint else None
    fingerprint = check_tree(vault, commit, link.id, head, trusted=None if fresh else trusted)
    check_remote(link.remote, vault=vault, gh=gh)  # again, right before the push
    git(vault, "update-ref", mirror_ref(link.id), commit)  # keeps its objects while it is pending
    record(dataclasses.replace(link, pending_mirror=commit))
    push(vault, link.remote, commit, expect)
    result.pushed = commit
    if result.in_history:
        result.notes.append(
            f"{result.in_history} file(s) left {link.id}'s mirror but stay in its history; to start it over: "
            f"scoutctl delegate link {link.id} --remote <url> --replace-remote --confirm-account-unshared"
        )
    done = dataclasses.replace(
        link,
        last_mirror=commit,
        last_vault=head,
        last_sync=_now(),
        pending_mirror=None,
        checked_fingerprint=fingerprint,
    )
    return result, done


DROP_WARN_MIN = 5  # left-out files below this never warn: a few redaction casualties are normal


def _drop_warning(delegate: str, the_plan: Plan) -> list[str]:
    """A warning when most files the delegate may see were left out: a path term or a rule matching too much."""
    total = len(the_plan.files) + the_plan.left_out
    if the_plan.left_out < DROP_WARN_MIN or the_plan.left_out * 2 <= total:
        return []
    return [
        f"warning: {the_plan.left_out} of the {total} file(s) {delegate} may see were left out of its mirror, "
        "because they name a hidden file or can't be scanned; check `scoutctl delegate preview "
        f"{delegate}` and the privacy settings before relying on this mirror"
    ]


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
        if create_repo is not None and remote:
            raise RepoSyncError("bad_arguments", "refused: pass --remote or --create-repo, not both")
        before = load_links(vault).get(delegate)
        target = f"https://github.com/{create_repo}.git" if create_repo is not None else remote
        if (
            before is not None
            and before.remote != target
            and (before.deploy_key_id is not None or before.stale_key_ids)
        ):
            raise RepoSyncError(
                "key_on_record",
                f"refused: {delegate}'s deploy key on {before.remote} is still on record and live; run "
                f"scoutctl delegate key {delegate} --revoke first, so it doesn't stay with the agent untracked",
            )
        if create_repo is not None:
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
            stale_key_ids=list(old.stale_key_ids) if old is not None and old.remote == remote else [],
            key_file=old.key_file if old is not None and old.remote == remote else None,
        )
        result, updated = _build_and_push(
            vault, new, fresh=True, expect=current, gh=gh, record=record, links=links, replace=replace_remote
        )
        links[delegate] = dataclasses.replace(updated, last_inputs=sync_inputs(vault))  # else the first tick re-syncs
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
        # Checked under a fingerprint not recorded: the next check scans every file.
        return dataclasses.replace(current, last_mirror=remote, pending_mirror=None, checked_fingerprint=None)
    if remote == current.last_mirror:
        return dataclasses.replace(current, pending_mirror=None)
    return current


def _event(vault: Path, kind: str, payload: dict[str, Any]) -> None:
    """A run-health event row (``.scout-logs/schedule-events-*.jsonl``); best effort. Codes and ids only."""
    try:
        from scout.events import append_event

        append_event(paths.logs_dir(vault), kind=kind, source="cli:delegate", payload=payload)
    except Exception:  # pragma: no cover - an event log that can't be written never stops a sync
        pass


def sync(vault: Path, delegate: str, *, gh: GhRunner = _gh) -> Result:
    """Push a new mirror commit for ``delegate`` when the vault's HEAD changed what it may see.

    The outcome is recorded on the link (``last_attempt``; ``last_error`` until
    a sync succeeds) and as a run-health event. A failure isn't a run failure:
    run health and ``bootstrap doctor`` show it. ``busy`` records nothing.
    """
    vault = vault.resolve()
    with locked(vault):
        links = load_links(vault)
        current = links.get(delegate)
        if current is None:
            raise RepoSyncError("not_linked", f"refused: {delegate} has no mirror; run scoutctl delegate link")
        inputs = sync_inputs(vault)
        try:
            ignored = ensure_ignored(vault)  # links made before V2c: the tick file and scan memo too
            current = _adopt_pending(vault, current)
            links[delegate] = current

            def record(pending: DelegateLink) -> None:
                save_links(vault, {**links, delegate: pending})

            result, updated = _build_and_push(
                vault, current, fresh=False, expect=current.last_mirror, gh=gh, record=record, links=links
            )
        except Exception as e:
            err = e if isinstance(e, RepoSyncError) else RepoSyncError("internal", f"{type(e).__name__}: {e}")
            _record_failure(vault, delegate, current, err.code, inputs)
            if err is e:
                raise
            raise err from e
        if ignored:
            result.notes.append(f"added {STATE_REL} and its companions to .gitignore")
        updated, notes = _retry_stale_quietly(vault, updated, links, gh)
        result.notes += notes
        links[delegate] = dataclasses.replace(updated, last_attempt=_now(), last_error=None, last_inputs=inputs)
        save_links(vault, links)
    _event(vault, "delegate_mirror.synced", {"delegate": delegate, "pushed": result.pushed is not None})
    return result


def _record_failure(vault: Path, delegate: str, current: DelegateLink, code: str, inputs: str) -> None:
    """Record a failed sync on the link (keeping since when, while the code is the same) and as an event."""
    latest = load_links(vault)
    link = latest.get(delegate, current)
    now = _now()
    before = link.last_error or {}
    first = before.get("first_at") or before.get("at")  # failing since: the streak, whichever code each try gave
    error = {"code": code, "at": now, "first_at": first or now, "head": _rev(vault, "HEAD") or "", "inputs": inputs}
    save_links(vault, {**latest, delegate: dataclasses.replace(link, last_attempt=now, last_error=error)})
    _event(vault, "delegate_mirror.sync_failed", {"delegate": delegate, "code": code})


def sync_inputs(vault: Path) -> str:
    """A fingerprint of what shapes a mirror besides HEAD: the live ``scout-config.yaml`` and the shared-reads ledger.

    Read cheaply (the config's bytes, the ledger files' sizes and times), so the tick can compare it every time.
    """
    from scout.kb.outbound import LEDGER_DIR

    h = hashlib.sha256()
    with contextlib.suppress(OSError):
        h.update((vault / paths.VAULT_MARKER).read_bytes())
    with contextlib.suppress(OSError):
        for f in sorted((vault / LEDGER_DIR).iterdir()):
            h.update(f"\0{f.name}\0{_stat_key(f)}".encode())
    return h.hexdigest()


RETRY_SOON = timedelta(hours=1)  # an unchanged vault retries a failing mirror this often for its first day,
RETRY_LATER = timedelta(days=1)  # then this often; a new commit or config edit retries at the next tick


def _when(stamp: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")) if stamp else None
    except ValueError:
        return None


def _retry_due(link: DelegateLink, head: str | None, inputs: str, now: datetime) -> bool:
    """Whether a failing mirror should be tried again: the vault changed since the failure, or the backoff ran out."""
    error = link.last_error or {}
    if error.get("head") != (head or "") or error.get("inputs") != inputs:
        return True
    first, tried = _when(error.get("first_at") or error.get("at")), _when(link.last_attempt or error.get("at"))
    if first is None or tried is None:
        return True
    wait = RETRY_SOON if now - first < timedelta(days=1) else RETRY_LATER
    return now - tried >= wait or now < tried


def _retry_stale_quietly(
    vault: Path, link: DelegateLink, links: Mapping[str, DelegateLink], gh: GhRunner
) -> tuple[DelegateLink, list[str]]:
    """A sync's best try at the old keys a rotation left live: it never fails the sync.

    Only after the remote is checked and bound to this delegate again, also when
    the sync changed nothing (that path skips the push and its checks).
    """
    repo = github_repo(link.remote)
    if not link.stale_key_ids or repo is None:
        return link, []
    try:
        check_remote(link.remote, vault=vault, gh=gh)
        bind_remote(vault, link, links, gh=gh, mode="keys")
    except RepoSyncError as e:
        return link, [e.message.replace("refused: ", "warning: old deploy keys not retried: ", 1)]
    return _retry_stale(repo, link, gh)


def needs_sync(vault: Path, links: Mapping[str, DelegateLink], *, now: datetime | None = None) -> list[str]:
    """The linked delegates whose mirror may be behind.

    HEAD, the live config or the shared-reads ledger changed since the last good
    sync, or a push didn't finish. A mirror whose last sync failed is retried when
    the vault changed since that failure, and otherwise on a backoff
    (:data:`RETRY_SOON`, then :data:`RETRY_LATER`), not on every tick.
    """
    if not links:
        return []
    head, inputs, now = _rev(vault, "HEAD"), sync_inputs(vault), now or datetime.now(UTC)
    out = []
    for key, link in links.items():
        if link.last_error is not None:  # a failed push leaves pending_mirror too: the backoff still decides
            if _retry_due(link, head, inputs, now):
                out.append(key)
        elif link.pending_mirror is not None or link.last_vault != head or link.last_inputs != inputs:
            out.append(key)
    return sorted(out)


def due(vault: Path, *, now: datetime | None = None) -> bool:
    """Whether the schedule tick should start a sync: some mirror may be behind and none started for 15 minutes.

    Cheap when nothing is linked: one ``stat``. Never raises on a state it can't read (the sync reports that).
    """
    if not state_path(vault).exists():
        return False
    now = now or datetime.now(UTC)
    try:
        last = datetime.fromtimestamp((vault / TICK_REL).stat().st_mtime, UTC)
    except OSError:
        last = None
    if last is not None and timedelta(0) <= now - last < SYNC_INTERVAL:  # a time ahead of now is due, not stuck
        return False
    try:
        return bool(needs_sync(vault, load_links(vault), now=now))
    except RepoSyncError:
        return True  # the sync records why it can't run, for run health


def mark_started(vault: Path) -> None:
    """Note that the tick started a sync now (the tick file's mtime)."""
    tick = vault / TICK_REL
    tick.parent.mkdir(parents=True, exist_ok=True)
    tick.touch()
    os.utime(tick)


def sync_all(
    vault: Path, *, only_moved: bool = False, gh: GhRunner = _gh
) -> tuple[list[Result], dict[str, RepoSyncError]]:
    """Sync every linked delegate, or with ``only_moved`` those :func:`needs_sync` names. One failing doesn't stop
    the rest."""
    done: list[Result] = []
    failed: dict[str, RepoSyncError] = {}
    links = load_links(vault.resolve())
    for delegate in needs_sync(vault.resolve(), links) if only_moved else sorted(links):
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
        if repo is not None and (current.deploy_key_id is not None or current.stale_key_ids):
            bind_remote(vault, current, links, gh=gh, mode="keys")
        if current.deploy_key_id is not None and repo is not None:
            _delete_key(repo, current.deploy_key_id, gh=gh, title=_key_title(delegate))
            current = dataclasses.replace(current, deploy_key_id=None)
            save_links(vault, {**links, delegate: current})
            result.notes.append("revoked the agent's deploy key")
        if current.stale_key_ids and repo is not None:
            current, notes = _retry_stale(repo, current, gh)
            save_links(vault, {**links, delegate: current})
            if current.stale_key_ids:
                raise RepoSyncError(
                    "key_revoke_failed",
                    f"refused: old deploy key(s) {', '.join(map(str, current.stale_key_ids))} on {repo} are still "
                    "live; delete them in the repository's settings, then unlink again",
                )
            result.notes += notes
        _remove_key_files(vault, delegate, current)
        (vault / SCAN_DIR / f"{delegate}.json").unlink(missing_ok=True)
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
    proc = _run_gh(gh, ["repo", "create", repo, "--private", "--description", "Scout delegate mirror"], repo)
    if proc.returncode != 0:
        why = (proc.stderr or proc.stdout).strip().splitlines()
        hint = ""
        if "already exists" in proc.stderr + proc.stdout:
            hint = (
                f"; if an earlier try made it, link it with --remote https://github.com/{repo}.git (the checks "
                "still run, so it must be private and empty)"
            )
        raise RepoSyncError(
            "repo_create_failed", f"refused: gh couldn't create {repo}: {why[-1] if why else 'gh failed'}{hint}"
        )
    return f"https://github.com/{repo}.git"


Keygen = Callable[[Path, str], None]
# GitHub's published SSH host key (https://api.github.com/meta "ssh_keys"; docs: "GitHub's SSH key fingerprints").
# If GitHub ever rotates it, agents' clones fail closed (StrictHostKeyChecking=yes). To recover: after an engine
# release with the new key, `scoutctl delegate key <id> --rotate` writes a new pin; or replace the line in the
# agent's `.known_hosts` file with `github.com <key>` from `gh api meta --jq '.ssh_keys[]'`.
GITHUB_KNOWN_HOSTS = "github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"


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


def _not_found(proc: subprocess.CompletedProcess[str]) -> bool:
    return "Not Found" in proc.stdout + proc.stderr or "HTTP 404" in proc.stdout + proc.stderr


def _key_gone(repo: str, key_id: int, gh: GhRunner) -> bool:
    """Whether GitHub's key list for ``repo`` shows ``key_id`` gone (a 404 alone also means "can't see the repo")."""
    try:
        listed = _ask_gh(gh, ["api", "--paginate", f"repos/{repo}/keys", "--jq", ".[].id"], repo)
    except RepoSyncError:
        return False
    return str(key_id) not in listed.split()


def _delete_key(repo: str, key_id: int, *, gh: GhRunner, title: str) -> None:
    """Delete deploy key ``key_id`` on ``repo`` if it is titled ``title`` (this delegate's), or refuse.

    Never trusts the link state for whose key it is: GitHub must show the
    title first. Gone already counts only when the key list shows it gone,
    since GitHub answers 404 both for a deleted key and for a private
    repository this ``gh`` account can't see.
    """
    unseen = (
        f"refused: couldn't delete deploy key {key_id} on {repo}: GitHub says Not Found, and this gh account "
        "can't list its keys (check gh auth status); delete it in the repository's settings, then retry"
    )
    got = _run_gh(gh, ["api", f"repos/{repo}/keys/{key_id}", "--jq", ".title"], repo)
    if got.returncode != 0:
        if _not_found(got):
            if _key_gone(repo, key_id, gh):
                return
            raise RepoSyncError("key_revoke_failed", unseen)
        why = (got.stderr or got.stdout).strip().splitlines()
        raise RepoSyncError(
            "key_revoke_failed",
            f"refused: couldn't read deploy key {key_id} on {repo}: {why[-1] if why else 'gh failed'}",
        )
    if got.stdout.strip() != title:
        raise RepoSyncError(
            "key_not_ours",
            f"refused: deploy key {key_id} on {repo} is titled {got.stdout.strip()!r}, not {title!r}: it isn't "
            "this delegate's, so it was left alone",
        )
    proc = _run_gh(gh, ["api", "-X", "DELETE", f"repos/{repo}/keys/{key_id}"], repo)
    if proc.returncode == 0 or (_not_found(proc) and _key_gone(repo, key_id, gh)):
        return
    why = (proc.stderr or proc.stdout).strip().splitlines()
    raise RepoSyncError(
        "key_revoke_failed",
        f"refused: couldn't delete deploy key {key_id} on {repo}: {why[-1] if why else 'gh failed'}; delete it "
        "in the repository's settings, then retry",
    )


def _retry_stale(repo: str, link: DelegateLink, gh: GhRunner) -> tuple[DelegateLink, list[str]]:
    """Try again to delete the old keys a rotation couldn't; the link with those still live, and notes.

    A key GitHub shows under another title isn't this delegate's: it is dropped from the list, untouched.
    """
    left: list[int] = []
    notes: list[str] = []
    for key_id in link.stale_key_ids:
        try:
            _delete_key(repo, key_id, gh=gh, title=_key_title(link.id))
        except RepoSyncError as e:
            if e.code != "key_not_ours":
                left.append(key_id)
            notes.append(e.message.replace("refused: ", "warning: ", 1))
        else:
            notes.append(f"deleted old deploy key {key_id} on {repo}")
    return dataclasses.replace(link, stale_key_ids=left), notes


_KEY_FILE = re.compile(r"[0-9a-f]{16}-(?P<delegate>[a-z0-9][a-z0-9-]{0,39})")


def _key_files(vault: Path, delegate: str, link: DelegateLink | None) -> list[Path]:
    """The agent's key files that may be deleted: this vault's path for them, and the one the link recorded only
    when it is a key file for this delegate directly in the secrets folder (a forged path is ignored)."""
    keys = {key_path(vault, delegate)}
    if link is not None and link.key_file:
        recorded = Path(link.key_file)
        match = _KEY_FILE.fullmatch(recorded.name)
        with contextlib.suppress(OSError):
            if match and match["delegate"] == delegate and recorded.parent.resolve() == secrets_dir().resolve():
                keys.add(recorded)
    return [p for k in sorted(keys) for p in (k, k.with_name(k.name + ".pub"), k.with_name(k.name + ".known_hosts"))]


def _remove_key_files(vault: Path, delegate: str, link: DelegateLink | None = None) -> None:
    """Delete the agent's key files: regular files with one link only, never through a symlink or hard link."""
    for p in _key_files(vault, delegate, link):
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
    bind_remote(vault, current, links, gh=gh, mode="keys")  # whatever the state says, before any key changes
    if current.stale_key_ids:
        current, notes = _retry_stale(repo, current, gh)
        links[delegate] = current
        save_links(vault, links)
        result.notes += notes
    if revoke:
        if current.deploy_key_id is None:
            if result.notes:  # only old keys were on record; they were retried above
                return result
            raise RepoSyncError("no_key", f"refused: {delegate} has no deploy key on record")
        _delete_key(repo, current.deploy_key_id, gh=gh, title=_key_title(delegate))
        _remove_key_files(vault, delegate, current)
        links[delegate] = dataclasses.replace(current, deploy_key_id=None, key_file=None)
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
        proc = _run_gh(
            gh,
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
            ],
            repo,
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
            _delete_key(repo, int(fields[0]), gh=gh, title=_key_title(delegate))
            raise RepoSyncError("key_not_read_only", "refused: GitHub recorded the key as writable; it was deleted")
        if current.deploy_key_id is not None:
            try:
                _delete_key(repo, current.deploy_key_id, gh=gh, title=_key_title(delegate))
            except RepoSyncError as e:
                added = dataclasses.replace(added, stale_key_ids=[*added.stale_key_ids, current.deploy_key_id])
                result.notes.append(
                    e.message.replace("refused: ", "warning: ", 1)
                    + f"; it stays on record, and delegate key, sync and unlink try again (scoutctl delegate key "
                    f"{delegate})"
                )
        key = key_path(vault, delegate)
        _remove_key_files(vault, delegate, current)
        shutil.move(str(new), key)
        shutil.move(str(new.with_name("key.pub")), key.with_name(key.name + ".pub"))
        os.chmod(key, 0o600)
        known = key.with_name(key.name + ".known_hosts")
        known.write_text(GITHUB_KNOWN_HOSTS + "\n", encoding="utf-8")
        added = dataclasses.replace(added, key_file=str(key))
    links[delegate] = added
    save_links(vault, links)
    result.notes += [
        f"{'replaced' if rotate and current.deploy_key_id else 'added'} a read-only deploy key on {repo}",
        f"private key: {key}; put it in the agent's secret store, then delete it here if you like",
        f"known hosts: {known}; it pins GitHub's published host key, so put it next to the key",
        "the agent clones with: GIT_SSH_COMMAND='ssh -i <key file> -o IdentitiesOnly=yes "
        "-o StrictHostKeyChecking=yes -o UserKnownHostsFile=<known hosts file>' "
        f"git clone --depth 1 git@github.com:{repo}.git",
    ]
    return result


def trust_state(
    vault: Path,
    *,
    confirm: Callable[[str, str], bool] | None = None,
    env: Mapping[str, str] | None = None,
    gh: GhRunner = _gh,
) -> Result:
    """Check each link in a state file whose signature doesn't verify, and sign it again. The owner only.

    For when the signing key was lost or the file was edited. Nothing in the
    file is taken on trust except which delegates have which remotes: each
    remote must pass :func:`check_remote` and hold this delegate's mirror
    (:func:`bind_remote`), the last mirror becomes what the remote holds, and
    the deploy keys are read back from GitHub by title (the one on record is
    kept only if GitHub shows it as this delegate's; any other key titled for
    this delegate is kept on record as an old key, so it is deleted next time).
    Sync times, fingerprints, the key file and the scan memos are reset. Each
    ``delegate → remote`` must also be confirmed by the owner (``confirm``; the
    CLI asks at a terminal or takes ``--confirm <id>``). If any link fails or
    isn't confirmed, nothing is written.
    """
    env = os.environ if env is None else env
    vault = vault.resolve()
    headless_gate(env, "Signing the delegate mirror state again")
    result = Result(delegate="*", action="trust-state")
    with locked(vault):
        path = state_path(vault)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))["links"]
            claimed = {str(k): DelegateLink.from_json(v) for k, v in raw.items()}
        except FileNotFoundError as e:
            raise RepoSyncError("not_linked", f"refused: {STATE_REL} doesn't exist; there is nothing to check") from e
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
            raise RepoSyncError(
                "state_unreadable", f"refused: {STATE_REL} can't be read ({e}); fix or delete it"
            ) from e
        for key, link in claimed.items():  # the file's own shape first, before any link is checked or confirmed
            _check_id(key)
            if link.id != key:
                raise RepoSyncError("state_unreadable", f"refused: the link under {key!r} names {link.id!r}")
        checked: dict[str, DelegateLink] = {}
        for key, link in sorted(claimed.items()):
            check_remote(link.remote, vault=vault, gh=gh)
            head = bind_remote(vault, link, claimed, gh=gh, mode="keys")
            if confirm is None or not confirm(key, link.remote):
                raise RepoSyncError(
                    "not_confirmed",
                    f"refused: {key} -> {link.remote} wasn't confirmed, so nothing was signed; confirm each link "
                    f"(at a terminal, or with --confirm {key})",
                )
            deploy, stale = _keys_on_github(link, gh)
            if head is not None and git(vault, "cat-file", "-e", f"{head}^{{commit}}", check=False).returncode == 0:
                git(vault, "update-ref", mirror_ref(key), head)
            checked[key] = DelegateLink(
                id=key,
                remote=link.remote,
                linked_at=link.linked_at,
                account_unshared=link.account_unshared,
                last_mirror=head,
                deploy_key_id=deploy,
                stale_key_ids=stale,
            )
            keys = f"deploy key {deploy}" if deploy is not None else "no deploy key"
            old = f", {len(stale)} old key(s) to delete" if stale else ""
            result.notes.append(f"{key}: {link.remote} holds {key}'s mirror; {keys}{old}; the next sync rebuilds it")
        save_links(vault, checked)
        for memo in (vault / SCAN_DIR).glob("*.json"):
            memo.unlink(missing_ok=True)
    result.notes.append(f"signed {STATE_REL} again for {len(checked)} delegate(s)")
    return result


def _keys_on_github(link: DelegateLink, gh: GhRunner) -> tuple[int | None, list[int]]:
    """The deploy key to keep on record for ``link`` and the old ones to delete, as GitHub lists them by title."""
    repo = github_repo(link.remote)
    if repo is None:
        return None, []
    listed = _ask_gh(gh, ["api", "--paginate", f"repos/{repo}/keys", "--jq", ".[] | [.id, .title] | @tsv"], repo)
    ours = sorted(
        int(i)
        for i, _, t in (line.partition("\t") for line in listed.splitlines())
        if i.isdigit() and t == _key_title(link.id)
    )
    if link.deploy_key_id in ours:
        return link.deploy_key_id, [k for k in ours if k != link.deploy_key_id]
    if len(ours) == 1:
        return ours[0], []
    return None, ours


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
        notes=_drop_warning(delegate, the_plan),
    )
