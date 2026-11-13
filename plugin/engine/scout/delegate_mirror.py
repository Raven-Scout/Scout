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
``privacy.sync_only``. Then every kept text file is redacted against the notes
this delegate can't see, with ``kb check-outbound``'s matchers
(:mod:`scout.kb.outbound`): names, aliases and titles, wikilinks, the citing
form of a shared note and quotes of notes shared with the owner.

- A markdown file loses the lines that hit. A hit inside the frontmatter takes
  its whole list item or top-level key with it, so the YAML stays whole. If the
  frontmatter then doesn't parse, or the file's label changes, the file is left
  out.
- ``.txt``, ``.csv``, ``.tsv`` and ``.jsonl`` files lose the lines that hit; any
  other text file that hits is left out whole. Binary files are kept by label.
- ``scout-config.yaml`` is rewritten so ``privacy.delegates`` lists only this
  delegate, as the engine in the clone needs; it isn't redacted.
- A symlink is kept only when it points at a folder in the mirror.

Plus ``.scout-state/privacy-policy.json`` (the policy and every file's label, as
for Agnes) and ``.scout-state/delegate-mirror.json`` (counts: files kept, files
withheld, lines removed per file; never the removed text).

**History.** One mirror commit per sync that changes the tree, with a fixed
identity and a generic message: a vault commit's subject never goes up. The
laptop is the only writer. A push is a compare-and-swap on the remote's
``main`` (``--force-with-lease``), so a remote that moved is refused
(``mirror_diverged``) and nothing is ever replayed into the vault. Before every
push the whole new tree is checked again (:func:`check_tree`).

A file that turns private leaves the mirror's ``HEAD`` but stays in its history
(spec §6.3): ``scoutctl delegate link <id> --remote <url> --replace-remote``
starts the mirror over with one commit.

**Remotes.** A GitHub repository, which must be private (``gh api`` is asked
at link and before every push; if it can't be asked, nothing is pushed), or a
local path, for tests and for a mirror the owner moves by hand. Anything else
is refused. ``link --create-repo owner/name`` makes the private repository with
``gh``. The laptop pushes with git's own credentials, or with a narrow token
the owner stores with ``delegate push-token`` (contents read/write on the
mirror repositories only), passed to git in its environment, never its argv.

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
import dataclasses
import hashlib
import json
import os
import posixpath
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

import scout
from scout import paths
from scout.kb.outbound import PrivateTerms, find_mentions, note_from_text, terms_of, with_shared
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
MAX_PASSES = 6  # removing a line can join a term split across it; re-scan until clean
GENERATED = frozenset({POLICY_PATH, MANIFEST_PATH})

_GITHUB = re.compile(
    r"^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
    r"(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)


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


def ensure_ignored(vault: Path) -> bool:
    """Add the link state to the vault's .gitignore unless already ignored. True when it was added."""
    if git(vault, "check-ignore", "-q", STATE_REL, check=False).returncode == 0:
        return False
    gi = vault / ".gitignore"
    text = gi.read_text(encoding="utf-8") if gi.exists() else ""
    head = text if not text or text.endswith("\n") else text + "\n"
    gi.write_text(f"{head}# Delegate mirrors (scoutctl delegate): this computer's state\n{STATE_REL}\n", "utf-8")
    return True


def mirror_ref(delegate: str) -> str:
    return f"{REF_PREFIX}{delegate}"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ----- remotes -------------------------------------------------------------------------------

GhRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def _gh(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=False)


def github_repo(remote: str) -> str | None:
    """``owner/name`` for a GitHub remote URL, else None."""
    m = _GITHUB.match(remote.strip())
    return m.group("repo") if m else None


def _local_remote(remote: str) -> Path | None:
    raw = remote[len("file://") :] if remote.startswith("file://") else remote
    if "://" in raw or re.match(r"^[^/]+@[^/]+:", raw) or not raw.startswith(("/", "~")):
        return None
    return Path(raw).expanduser()


def check_remote(remote: str, *, gh: GhRunner = _gh) -> None:
    """Refuse a remote that isn't a private GitHub repository or a local path (module docstring)."""
    repo = github_repo(remote)
    if repo is not None:
        try:
            proc = gh(["api", f"repos/{repo}", "--jq", ".private"])
        except OSError as e:
            raise RepoSyncError(
                "remote_unverified", f"refused: can't ask GitHub whether {repo} is private (gh: {e})"
            ) from e
        if proc.returncode != 0:
            why = (proc.stderr or proc.stdout).strip().splitlines()
            raise RepoSyncError(
                "remote_unverified",
                f"refused: can't ask GitHub whether {repo} is private: {why[-1] if why else 'gh failed'}",
            )
        if proc.stdout.strip() != "true":
            raise RepoSyncError("remote_public", f"refused: {repo} isn't a private repository; nothing was pushed")
        return
    if _local_remote(remote) is not None:
        return
    raise RepoSyncError(
        "remote_unsupported",
        "refused: a delegate mirror goes to a private GitHub repository (https://github.com/<owner>/<repo>) "
        "or a local path",
    )


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


# ----- redaction -----------------------------------------------------------------------------


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _is_item(line: str) -> bool:
    s = line.lstrip(" ")
    return s == "-" or s.startswith("- ")


def _frontmatter_end(lines: Sequence[str]) -> int | None:
    """Index of the closing ``---`` of a leading frontmatter block, or None."""
    if not lines or lines[0].strip() != "---":
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


def redact_lines(text: str, terms: PrivateTerms, *, markdown: bool) -> tuple[str, int] | None:
    """``text`` without the lines that name or link a note in ``terms``, and how many lines went.

    None when it can't be made clean in :data:`MAX_PASSES` passes.
    """
    lines = text.split("\n")
    fm_end = _frontmatter_end(lines) if markdown else None
    gone: set[int] = set()
    for _ in range(MAX_PASSES):
        kept = [i for i in range(len(lines)) if i not in gone]
        hits = find_mentions("\n".join(lines[i] for i in kept), terms, allow_markers=False)
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


def mirror_config(text: str, delegate: str, include_private: bool) -> bytes:
    """``scout-config.yaml`` for the mirror: ``privacy.delegates`` names only this delegate."""
    data = load_yaml(text)
    if not isinstance(data, dict):  # pragma: no cover - delegate_policy refused a broken config
        raise RepoSyncError("privacy_overlay_unreadable", f"refused: {paths.VAULT_MARKER} can't be read")
    privacy = dict(data.get("privacy") or {})
    privacy["delegates"] = {delegate: {"include_private": True} if include_private else {}}
    data["privacy"] = privacy
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True).encode("utf-8")


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
    left_out: int = 0  # visible files left out because redaction couldn't make them clean
    hidden_paths: set[str] = field(default_factory=set)

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

    def policy_document(self, policy: Policy) -> bytes:
        doc = {
            "schema_version": SCHEMA_VERSION,
            "generator": "scoutctl delegate sync",
            "engine_version": scout.__version__,
            "policy": policy.to_json(),
            "labels": [lb.to_json() for lb in sorted(self.labels, key=lambda lb: lb.path)],
        }
        return (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _is_text(data: bytes) -> str | None:
    if b"\0" in data[:8192]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def hidden_terms(vault: Path, entries: Sequence[TreeEntry], hidden: Sequence[str], blobs: BlobReader) -> PrivateTerms:
    """The matchable forms of every markdown note hidden from the delegate, plus what was shared with the owner."""
    from scout.kb.shared_reads import SharedReadsUnreadable

    by_path = {e.path: e for e in entries}
    notes = []
    for rel in hidden:
        e = by_path[rel]
        if rel.lower().endswith(".md") and e.mode != SYMLINK_MODE:
            notes.append(note_from_text(rel, decode_head(blobs.read(e.sha)[:HEAD_BYTES])))
    try:
        return with_shared(terms_of(notes), vault)
    except SharedReadsUnreadable as e:
        raise RepoSyncError("shared_reads_unreadable", f"refused: the shared-reads ledger can't be read ({e})") from e


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


def plan(vault: Path, head: str, delegate: str, blobs: BlobReader) -> tuple[Plan, Policy, LocalOnlyRules, PrivateTerms]:
    """The mirror of vault commit ``head`` for ``delegate``, and the terms it was redacted against. Reads only."""
    policy, policies = delegate_policy(vault, head, delegate, blobs)
    rules = LocalOnlyRules.build(policies, ())
    reader = f"delegate:{delegate}"
    generated = {fold(p) for p in GENERATED}
    entries = [e for e in ls_tree(vault, head) if e.kind == "blob"]
    out = Plan(delegate=delegate)
    visible: list[tuple[TreeEntry, Label]] = []
    links: list[TreeEntry] = []
    for e in entries:
        if fold(e.path) in generated:
            continue  # only the generated files may carry those names
        if rules.refused(e.path):
            out.hidden_paths.add(e.path)
        elif e.mode == SYMLINK_MODE:
            links.append(e)
        else:
            head_text = decode_head(blobs.read(e.sha)[:HEAD_BYTES]) if e.path.lower().endswith(".md") else None
            label = classify(e.path, head_text, policy)
            if visible_to(label, reader, policy):
                visible.append((e, label))
            else:
                out.hidden_paths.add(e.path)
    terms = hidden_terms(vault, entries, sorted(out.hidden_paths), blobs)
    include_private = policy.delegates[0].include_private
    for e, label in visible:
        data = blobs.read(e.sha)
        kept = _mirror_file(e, label, data, terms, policy, delegate, include_private, out)
        if kept is not None:
            out.files.append(kept[0])
            out.labels.append(kept[1])
    folders = _folders([f.path for f in out.files])
    for e in links:
        target = _link_target(e, blobs.read(e.sha))
        if target is not None and target in folders and not rules.refused(target):
            out.files.append(MirrorFile(e.path, e.mode, None, e.sha))
            out.labels.append(classify(e.path, None, policy))
        else:
            out.hidden_paths.add(e.path)
    out.withheld = len(out.hidden_paths)
    return out, policy, rules, terms


def _mirror_file(
    e: TreeEntry,
    label: Label,
    data: bytes,
    terms: PrivateTerms,
    policy: Policy,
    delegate: str,
    include_private: bool,
    out: Plan,
) -> tuple[MirrorFile, Label] | None:
    """What a visible file becomes in the mirror, or None (counted in ``out.left_out``) when it can't go."""
    if e.path == paths.VAULT_MARKER:
        config = mirror_config(data.decode("utf-8", "replace"), delegate, include_private)
        return MirrorFile(e.path, e.mode, config, ""), label
    text = _is_text(data)
    if text is None:
        return MirrorFile(e.path, e.mode, None, e.sha), label
    low = e.path.lower()
    markdown = low.endswith(".md")
    if not (markdown or low.endswith(LINE_FILES)):
        if find_mentions(text, terms, allow_markers=False):
            out.left_out += 1
            return None
        return MirrorFile(e.path, e.mode, None, e.sha), label
    redacted = redact_lines(text, terms, markdown=markdown)
    if redacted is None:
        out.left_out += 1
        return None
    new_text, removed = redacted
    if removed == 0:
        return MirrorFile(e.path, e.mode, None, e.sha), label
    new_label = classify(e.path, new_text[:HEAD_BYTES], policy) if markdown else label
    if markdown and (not _frontmatter_parses(new_text) or not _same_label(label, new_label)):
        out.left_out += 1
        return None
    out.redacted[e.path] = removed
    return MirrorFile(e.path, e.mode, new_text.encode("utf-8"), ""), new_label


def write_tree(vault: Path, the_plan: Plan, policy: Policy) -> str:
    """Write ``the_plan``'s blobs and tree into the vault's object store; the tree sha."""

    def blob(data: bytes) -> str:
        return git(vault, "hash-object", "-w", "--stdin", input=data).stdout.strip()

    lines = []
    for f in the_plan.files:
        sha = f.sha if f.data is None else blob(f.data)
        lines.append(f"{f.mode} {sha}\t{f.path}")
    lines.append(f"100644 {blob(the_plan.policy_document(policy))}\t{POLICY_PATH}")
    lines.append(f"100644 {blob(the_plan.manifest())}\t{MANIFEST_PATH}")
    return _write_tree(vault, lines)


def check_tree(
    vault: Path, rev: str, delegate: str, policy: Policy, rules: LocalOnlyRules, terms: PrivateTerms
) -> None:
    """Refuse when mirror commit ``rev`` holds anything this delegate may not see. Reads its tree from scratch.

    Every path must be one the rules let leave the laptop; every file but a
    symlink must be visible to the delegate by its label in the mirror; every
    text file but ``scout-config.yaml`` and the generated files must name no
    hidden note; every symlink must point at a folder the mirror holds.
    """
    reader = f"delegate:{delegate}"
    blobs = BlobReader(vault)
    try:
        entries = ls_tree(vault, rev)
        if not GENERATED <= {e.path for e in entries}:
            raise RepoSyncError("policy_missing", f"refused: mirror commit {rev[:12]} lacks its generated files")
        folders = _folders([e.path for e in entries if e.mode != SYMLINK_MODE])
        bad = 0
        for e in entries:
            if e.path in GENERATED:
                continue
            if e.kind != "blob" or rules.refused(e.path):
                bad += 1
                continue
            data = blobs.read(e.sha)
            if e.mode == SYMLINK_MODE:
                target = _link_target(e, data)
                bad += target is None or target not in folders
                continue
            head_text = decode_head(data[:HEAD_BYTES]) if e.path.lower().endswith(".md") else None
            if not visible_to(classify(e.path, head_text, policy), reader, policy):
                bad += 1
                continue
            text = None if e.path == paths.VAULT_MARKER else _is_text(data)
            if text is not None and find_mentions(text, terms, allow_markers=False):
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
    vault: Path, link: DelegateLink, *, fresh: bool, expect: str | None, gh: GhRunner
) -> tuple[Result, DelegateLink]:
    head = _head(vault)
    blobs = BlobReader(vault)
    try:
        the_plan, policy, rules, terms = plan(vault, head, link.id, blobs)
    finally:
        blobs.close()
    if paths.VAULT_MARKER not in {f.path for f in the_plan.files}:
        raise RepoSyncError(
            "config_not_mirrored",
            f"refused: {paths.VAULT_MARKER} wouldn't reach the mirror, so the engine there would show "
            f"{link.id} nothing",
        )
    trading_gate(vault, rules)
    tree = write_tree(vault, the_plan, policy)
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
    check_tree(vault, commit, link.id, policy, rules, terms)
    check_remote(link.remote, gh=gh)
    push(vault, link.remote, commit, expect)
    git(vault, "update-ref", mirror_ref(link.id), commit)
    result.pushed = commit
    if result.in_history:
        result.notes.append(
            f"{result.in_history} file(s) left {link.id}'s mirror but stay in its history; to start it over: "
            f"scoutctl delegate link {link.id} --remote <url> --replace-remote --confirm-account-unshared"
        )
    return result, dataclasses.replace(link, last_mirror=commit, last_vault=head, last_sync=_now())


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
    links = load_links(vault)
    if create_repo is not None:
        if remote:
            raise RepoSyncError("bad_arguments", "refused: pass --remote or --create-repo, not both")
        if load_policy(vault).delegate(f"delegate:{delegate}") is None:
            raise RepoSyncError(
                "delegate_not_listed", f"refused: {delegate} isn't listed under privacy.delegates in scout-config.yaml"
            )
        remote = make_repo(create_repo, gh=gh)
    check_remote(remote, gh=gh)
    current = remote_head(vault, remote)
    if current is not None and not replace_remote:
        raise RepoSyncError(
            "remote_not_empty",
            "refused: the remote already has a main branch; pass --replace-remote to start it over with this mirror",
        )
    old = links.get(delegate)
    new = DelegateLink(
        id=delegate,
        remote=remote,
        linked_at=_now(),
        account_unshared=True,
        deploy_key_id=old.deploy_key_id if old is not None and old.remote == remote else None,
    )
    result, updated = _build_and_push(vault, new, fresh=True, expect=current, gh=gh)
    links[delegate] = updated
    if ensure_ignored(vault):
        result.notes.append(f"added {STATE_REL} to .gitignore")
    save_links(vault, links)
    if updated.deploy_key_id is None and github_repo(remote) is not None:
        result.notes.append(f"next: scoutctl delegate key {delegate} makes the agent's read-only deploy key")
    return result


def sync(vault: Path, delegate: str, *, gh: GhRunner = _gh) -> Result:
    """Push a new mirror commit for ``delegate`` when the vault's HEAD changed what it may see."""
    vault = vault.resolve()
    links = load_links(vault)
    current = links.get(delegate)
    if current is None:
        raise RepoSyncError("not_linked", f"refused: {delegate} has no mirror; run scoutctl delegate link")
    result, updated = _build_and_push(vault, current, fresh=False, expect=current.last_mirror, gh=gh)
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
    links = load_links(vault)
    current = links.get(delegate)
    if current is None:
        raise RepoSyncError("not_linked", f"refused: {delegate} has no mirror")
    result = Result(delegate=delegate, action="unlink")
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
    check_remote(current.remote, gh=gh)
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
