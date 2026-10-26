"""Sync a Scout vault with the person's Agnes Personal Knowledge Repository (R1).

Agnes spec: "Scout in Agnes" (2026-10-20), §3 storage, §4 sync, §5 lease,
§15 R1. Privacy
spec: ``docs/superpowers/specs/2026-10-20-per-note-privacy-design.md`` (§4.3,
§5.1, §5.4, §10 P5/P7).

The hosted repository is a **filtered mirror** of the vault: the same files
minus Local-Only Paths, as its own linear history that starts at link time.

- ``link`` checks the gates, creates the repository on Agnes if needed, adds
  the vault's plain local-only paths to the host's list, turns OTLP content
  export off for the vault, and pushes one snapshot commit of the filtered HEAD.
- ``sync`` first replays every commit made on the mirror (in Agnes) onto the
  vault, one vault commit per mirror commit, then pushes one mirror commit per
  new vault commit: the vault commit's tree minus Local-Only Paths, plus a
  generated ``.scout-state/privacy-policy.json`` that holds the commit's
  effective policy (``scoutctl kb policy --json``) and the label of every file
  in the mirror commit (``scoutctl kb classify --json``).
- ``unlink`` forgets the link on this computer.

The Local-Only set is the **union** of the vault's effective policy (engine
default + ``privacy.local_only_add``, including what ``bootstrap
privacy-migrate`` wrote), the policy recorded in each commit being mirrored,
the host's per-person list, the host's built-in plumbing list, and the host's
secret-carrier rule. Matching is case- and Unicode-folded like the host's, so
a spelling the vault treats as different can't slip past either side. Every
mirror commit is checked again against that set before anything is pushed.

Mirror objects live in the vault's own object store under ``refs/scout-mirror/``.
A push sends only what a mirror commit reaches, so a Local-Only blob is never
packed. The commit map is ``.scout-state/repo-sync.json`` (gitignored); the
Agnes token is kept outside the vault, in ``~/.scout-secrets/``.

Every push goes up under the Repository Writer Lease (R2, ``scout.repo_lease``):
a sync during a scheduled run reuses the run's lease, read from this computer's
lease file, and any other sync or link takes the lease for itself and gives it
back when it is done.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import os
import re
import subprocess
import tempfile
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import yaml

import scout
from scout import paths
from scout.kb.lint_config import glob_match
from scout.kb.privacy import (
    HEAD_BYTES,
    Label,
    Policy,
    classify,
    decode_head,
    load_policy,
    policy_from_overlay,
)

SCHEMA_VERSION = 1
STATE_FILE = "repo-sync.json"
POLICY_PATH = ".scout-state/privacy-policy.json"
STATE_REL = f".scout-state/{STATE_FILE}"
MIRROR_REF = "refs/scout-mirror/main"
REMOTE_REF = "refs/scout-mirror/remote"
REMOTE_BRANCH = "refs/heads/main"
REPLAY_TRAILER = "Scout-Mirror-Commit"
LEASE_TOKEN_HEADER = "X-Agnes-Lease-Token"
LEASE_GENERATION_HEADER = "X-Agnes-Lease-Generation"

# A real vault syncs only once the engine has the per-note privacy guard that
# stops a scheduled run from un-privating a note right before it syncs (#504:
# ``scout.kb.lint_privacy``, run by the vault's pre-commit hook). A vault the
# test fixtures name in this variable (its resolved path) skips that one check,
# so R1 can be exercised before #504 ships; ``~/Scout`` never does.
TEST_VAULT_ENV = "SCOUT_REPO_SYNC_TEST_VAULT"
WIDENING_GUARD = ("scout.kb.lint_privacy", "privacy_staged")
HOOK_FAILS_CLOSED = "privacy check could not run"

# The host's built-in lists (Agnes ``src/knowledge_repository/paths.py``),
# applied here too so the vault never offers what Agnes would refuse.
HOST_ALWAYS_LOCAL = (
    ".scout-logs/",
    ".scout-cache/",
    ".claude/",
    ".venv/",
    ".obsidian/",
    ".pytest_cache/",
    ".mcp.json",
    ".env*",
    ".scout-state/",
)
HOST_SYNCED_STATE = frozenset(
    {".scout-state/schedule.yaml", ".scout-state/id-map.json", ".scout-state/privacy-policy.json"}
)

# Claude Code reads these from the vault's .claude/settings.json; "0" keeps
# prompts, responses and tool content out of any OTLP export for sessions run
# in the vault, whatever the user-level telemetry settings say (Agnes §6).
OTLP_CONTENT_OFF = {
    "OTEL_LOG_USER_PROMPTS": "0",
    "OTEL_LOG_ASSISTANT_RESPONSES": "0",
    "OTEL_LOG_TOOL_DETAILS": "0",
    "OTEL_LOG_TOOL_CONTENT": "0",
}

# Root-level file names that look like trading or brokerage data. Folders are
# privacy-migrate's job; a root file such as TRADING.md is not proposed there,
# so link and sync catch it here. A substring of the case-folded name matches,
# which over-matches on purpose (fail closed): the owner keeps a false positive
# by confirming it once.
TRADING_NAME_PARTS = (
    "trading",
    "brokerage",
    "broker",
    "positions",
    "portfolio",
    "investment",
    "investing",
    "holdings",
)


class RepoSyncError(Exception):
    """A refusal or failure, with a stable code for ``--json`` callers."""

    def __init__(self, code: str, message: str, detail: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = dict(detail or {})


# ----- path rules ---------------------------------------------------------------------

_IGNORABLE = dict.fromkeys(
    [0x00AD, 0x034F, 0x180E, 0xFEFF, *range(0x200B, 0x2010), *range(0x202A, 0x202F), *range(0x2060, 0x2070)]
)


def fold(path: str) -> str:
    """The host's comparison form of a path (NFKC, invisible marks dropped, case-folded, no trailing dots)."""
    cleaned = unicodedata.normalize("NFKC", path).translate(_IGNORABLE).casefold()
    return "/".join(segment.rstrip(". ") or segment for segment in cleaned.split("/"))


def _host_matches(folded_path: str, entry: str) -> bool:
    entry = fold(entry)
    if entry.endswith("*"):
        return folded_path.startswith(entry[:-1])
    if entry.endswith("/"):
        return folded_path == entry[:-1] or folded_path.startswith(entry)
    return folded_path == entry or folded_path.startswith(entry + "/")


def is_secret_file(path: str) -> bool:
    """The host's secret-carrier rule: ``.mcp.json``, ``.env*``, ``*.pem``, ``id_rsa*``, ``.scout-secrets/``."""
    segments = fold(path).split("/")
    name = segments[-1]
    return (
        ".scout-secrets" in segments[:-1]
        or name == ".mcp.json"
        or name.startswith(".env")
        or name.endswith(".pem")
        or name.startswith("id_rsa")
    )


_PLAIN_ENTRY = re.compile(r"^[^*?\[\]{}!]+(/\*\*)?$")


def host_entry(glob: str) -> str | None:
    """A policy glob in the host's Local-Only Path form, or None when it isn't a plain path.

    ``trading/**`` → ``trading/``; ``notes/x.md`` → ``notes/x.md``. A glob with
    wildcards elsewhere stays a vault-side rule only (Agnes stores paths, not
    globs); the vault still strips it.
    """
    g = glob.strip().replace("\\", "/")
    while g.startswith("./"):
        g = g[2:]
    if not g or g.startswith("/") or not _PLAIN_ENTRY.match(g):
        return None
    if g.endswith("/**"):
        g = g[:-2]
    parts = g.rstrip("/").split("/")
    if any(p in ("", ".", "..") for p in parts):
        return None
    return "/".join(parts) + ("/" if g.endswith("/") else "")


@dataclass(frozen=True)
class LocalOnlyRules:
    """The union Local-Only set for one sync (see the module docstring)."""

    globs: tuple[str, ...]
    always: tuple[str, ...]
    sync_allow: tuple[str, ...]
    host_entries: tuple[str, ...]

    @classmethod
    def build(cls, policies: Iterable[Policy], host_entries: Iterable[str]) -> LocalOnlyRules:
        globs: list[str] = []
        always: list[str] = []
        sync_allow: list[str] = []
        for p in policies:
            globs += p.local_only
            always += p.local_only_always
            sync_allow += p.sync_allow
        return cls(
            globs=tuple(dict.fromkeys(fold(g) for g in globs)),
            always=tuple(dict.fromkeys(fold(g) for g in always)),
            sync_allow=tuple(dict.fromkeys(fold(g) for g in sync_allow)),
            host_entries=tuple(dict.fromkeys(host_entries)),
        )

    def with_policy(self, policy: Policy) -> LocalOnlyRules:
        return LocalOnlyRules(
            globs=tuple(dict.fromkeys((*self.globs, *(fold(g) for g in policy.local_only)))),
            always=tuple(dict.fromkeys((*self.always, *(fold(g) for g in policy.local_only_always)))),
            sync_allow=self.sync_allow,
            host_entries=self.host_entries,
        )

    def local_only(self, rel: str) -> bool:
        """True when ``rel`` must never leave the laptop."""
        f = fold(rel)
        if any(glob_match(g, f) for g in self.globs):
            return True
        if any(_host_matches(f, e) for e in self.host_entries):
            return True
        if f in HOST_SYNCED_STATE or any(glob_match(g, f) for g in self.sync_allow):
            return False
        if any(glob_match(g, f) for g in self.always):
            return True
        return any(_host_matches(f, e) for e in HOST_ALWAYS_LOCAL)

    def refused(self, rel: str) -> bool:
        """True when a mirror commit must not contain ``rel``."""
        return self.local_only(rel) or is_secret_file(rel)


# ----- git -------------------------------------------------------------------------------


def _git_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.pop("GIT_INDEX_FILE", None)
    if extra:
        env.update(extra)
    return env


def git(
    vault: Path,
    *args: str,
    env: Mapping[str, str] | None = None,
    input: str | bytes | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        cwd=vault,
        env=_git_env(env),
        input=input.encode("utf-8") if isinstance(input, str) else input,
        capture_output=True,
        check=False,
    )
    out = subprocess.CompletedProcess(
        proc.args,
        proc.returncode,
        proc.stdout.decode("utf-8", errors="replace"),
        proc.stderr.decode("utf-8", errors="replace"),
    )
    if check and proc.returncode != 0:
        raise RepoSyncError("git_failed", f"git {args[0]} failed: {out.stderr.strip() or out.stdout.strip()}")
    return out


def _rev(vault: Path, ref: str) -> str | None:
    proc = git(vault, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}", check=False)
    return proc.stdout.strip() if proc.returncode == 0 else None


def _is_ancestor(vault: Path, old: str, new: str) -> bool:
    return git(vault, "merge-base", "--is-ancestor", old, new, check=False).returncode == 0


@dataclass(frozen=True)
class TreeEntry:
    mode: str
    kind: str
    sha: str
    path: str


def ls_tree(vault: Path, rev: str) -> list[TreeEntry]:
    out = git(vault, "ls-tree", "-r", "-z", "--full-tree", rev).stdout
    entries: list[TreeEntry] = []
    for rec in out.split("\0"):
        if not rec:
            continue
        meta, _, path = rec.partition("\t")
        mode, kind, sha = meta.split()
        entries.append(TreeEntry(mode, kind, sha, path))
    return entries


class BlobReader:
    """``git cat-file --batch`` over one process, for the classifier's head reads."""

    def __init__(self, vault: Path) -> None:
        self._proc = subprocess.Popen(
            ["git", "cat-file", "--batch"],
            cwd=vault,
            env=_git_env(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )

    def read(self, sha: str) -> bytes:
        assert self._proc.stdin is not None and self._proc.stdout is not None
        self._proc.stdin.write(f"{sha}\n".encode())
        self._proc.stdin.flush()
        header = self._proc.stdout.readline().decode().split()
        if len(header) < 3 or header[1] == "missing":  # pragma: no cover - only shas from ls-tree are read
            raise RepoSyncError("git_failed", f"object {sha} is missing")
        size = int(header[2])
        data = self._proc.stdout.read(size)
        self._proc.stdout.read(1)
        return data

    def close(self) -> None:
        assert self._proc.stdin is not None
        self._proc.stdin.close()
        self._proc.wait()


def cat_blob(vault: Path, sha: str) -> bytes:
    proc = subprocess.run(["git", "cat-file", "blob", sha], cwd=vault, env=_git_env(), capture_output=True, check=False)
    if proc.returncode != 0:
        raise RepoSyncError("git_failed", f"object {sha} is missing")
    return proc.stdout


def _blob_at(vault: Path, rev: str, rel: str) -> TreeEntry | None:
    out = git(vault, "ls-tree", "-z", "--full-tree", rev, "--", rel, check=False).stdout
    for rec in out.split("\0"):
        if rec:
            meta, _, path = rec.partition("\t")
            mode, kind, sha = meta.split()
            if path == rel and kind == "blob":
                return TreeEntry(mode, kind, sha, path)
    return None


@dataclass(frozen=True)
class CommitMeta:
    sha: str
    tree: str
    author_name: str
    author_email: str
    author_date: str
    committer_name: str
    committer_email: str
    committer_date: str
    message: str

    def env(self) -> dict[str, str]:
        return {
            "GIT_AUTHOR_NAME": self.author_name,
            "GIT_AUTHOR_EMAIL": self.author_email,
            "GIT_AUTHOR_DATE": self.author_date,
            "GIT_COMMITTER_NAME": self.committer_name,
            "GIT_COMMITTER_EMAIL": self.committer_email,
            "GIT_COMMITTER_DATE": self.committer_date,
        }


def commit_meta(vault: Path, rev: str) -> CommitMeta:
    fmt = "%H%x00%T%x00%an%x00%ae%x00%ad%x00%cn%x00%ce%x00%cd%x00%B"
    out = git(vault, "show", "-s", "--date=raw", f"--format={fmt}", rev).stdout
    sha, tree, an, ae, ad, cn, ce, cd, body = out.split("\0", 8)
    return CommitMeta(sha, tree, an, ae, ad, cn, ce, cd, message=body.rstrip("\n") + "\n")


def _commit_tree(vault: Path, tree: str, parents: Sequence[str], message: str, ident: Mapping[str, str]) -> str:
    args = ["commit-tree", "--no-gpg-sign", tree]
    for p in parents:
        args += ["-p", p]
    return git(vault, *args, env=ident, input=message).stdout.strip()


# ----- policy and labels from a commit -------------------------------------------------


def policy_at(vault: Path, rev: str, blobs: BlobReader) -> Policy:
    """``load_policy`` for the vault as it was at ``rev`` (its committed ``scout-config.yaml``)."""
    base = load_policy(None)
    entry = _blob_at(vault, rev, paths.VAULT_MARKER)
    if entry is None:
        return base
    try:
        data = yaml.safe_load(blobs.read(entry.sha).decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError):
        return dataclasses.replace(base, warnings=("privacy-overlay-unreadable",))
    if data is None:
        return base
    if not isinstance(data, dict):
        return dataclasses.replace(base, warnings=("privacy-overlay-unreadable",))
    return policy_from_overlay(data.get("privacy"))


def label_blob(rel: str, entry: TreeEntry, policy: Policy, blobs: BlobReader) -> Label:
    """The label ``scoutctl kb classify`` gives ``rel`` when its content is the blob ``entry``."""
    if not rel.lower().endswith(".md") or entry.mode == "120000":
        return classify(rel, None, policy)
    return classify(rel, decode_head(blobs.read(entry.sha)[:HEAD_BYTES]), policy)


def policy_document(policy: Policy, labels: Sequence[Label], *, host_entries: Sequence[str]) -> bytes:
    """``.scout-state/privacy-policy.json``: the effective policy plus every mirrored file's label.

    The Agnes indexer reads ``policy`` (it accepts the document or its
    ``policy`` key) and the ``labels`` manifest.
    """
    doc = {
        "schema_version": SCHEMA_VERSION,
        "generator": "scoutctl repo sync",
        "engine_version": scout.__version__,
        "policy": policy.to_json(),
        "host_local_only_paths": list(host_entries),
        "labels": [label.to_json() for label in sorted(labels, key=lambda lb: lb.path)],
    }
    return (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode("utf-8")


class MirrorBuilder:
    """Builds filtered mirror trees from vault commits, caching labels per (path, blob)."""

    def __init__(self, vault: Path, rules: LocalOnlyRules, host_entries: Sequence[str]) -> None:
        self.vault = vault
        self.rules = rules
        self.host_entries = list(host_entries)
        self.blobs = BlobReader(vault)
        self._labels: dict[tuple[str, str, str], Label] = {}

    def close(self) -> None:
        self.blobs.close()

    def policy(self, rev: str) -> Policy:
        return policy_at(self.vault, rev, self.blobs)

    def tree(self, rev: str, policy: Policy) -> tuple[str, int]:
        """``(tree sha, files stripped)`` for the mirror of vault commit ``rev``."""
        rules = self.rules.with_policy(policy)
        policy_key = hashlib.sha256(json.dumps(policy.to_json(), sort_keys=True).encode()).hexdigest()
        kept: list[TreeEntry] = []
        stripped = 0
        for entry in ls_tree(self.vault, rev):
            if entry.kind != "blob":
                stripped += 1
                continue
            if entry.path == POLICY_PATH:
                continue
            if rules.refused(entry.path):
                stripped += 1
                continue
            kept.append(entry)
        labels: list[Label] = []
        for entry in kept:
            key = (policy_key, entry.path, entry.sha)
            label = self._labels.get(key)
            if label is None:
                label = label_blob(entry.path, entry, policy, self.blobs)
                self._labels[key] = label
            labels.append(label)
        doc = policy_document(policy, labels, host_entries=self.host_entries)
        doc_sha = git(self.vault, "hash-object", "-w", "--stdin", input=doc).stdout.strip()
        lines = [f"{e.mode} {e.sha}\t{e.path}" for e in kept]
        lines.append(f"100644 {doc_sha}\t{POLICY_PATH}")
        return _write_tree(self.vault, lines), stripped


def _write_tree(vault: Path, index_lines: Sequence[str]) -> str:
    with tempfile.TemporaryDirectory(prefix="scout-repo-sync-") as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        git(vault, "read-tree", "--empty", env=env)
        git(vault, "update-index", "-z", "--index-info", env=env, input="\0".join(index_lines) + "\0")
        return git(vault, "write-tree", env=env).stdout.strip()


def check_mirror_commits(vault: Path, rules: LocalOnlyRules, revs: Iterable[str]) -> None:
    """Refuse when any mirror commit about to be pushed holds a Local-Only or secret path."""
    for rev in revs:
        bad = sorted(e.path for e in ls_tree(vault, rev) if e.path != POLICY_PATH and rules.refused(e.path))
        policy = _blob_at(vault, rev, POLICY_PATH)
        if bad:
            raise RepoSyncError(
                "local_only_path_in_push",
                f"refused: mirror commit {rev[:12]} would carry {len(bad)} local-only path(s); nothing was pushed",
            )
        if policy is None:
            raise RepoSyncError(
                "policy_missing", f"refused: mirror commit {rev[:12]} has no {POLICY_PATH}; nothing was pushed"
            )


# ----- the host -------------------------------------------------------------------------


@dataclass(frozen=True)
class HostStatus:
    exists: bool
    can_create: bool
    clone_url: str
    local_only_paths: tuple[str, ...] = ()


class Host(Protocol):
    def status(self) -> HostStatus: ...

    def create(self) -> HostStatus: ...

    def set_local_only(self, entries: Sequence[str]) -> tuple[str, ...]: ...

    def delete(self) -> None: ...

    def git_env(self) -> dict[str, str]: ...

    def lease_acquire(self, holder_ref: str, holder_label: str) -> dict[str, Any]: ...

    def lease_renew(self, token: str) -> dict[str, Any]: ...

    def lease_release(self, token: str) -> None: ...


class AgnesHost:
    """The Agnes REST + git smart HTTP contract (Agnes A1/A2), authenticated with the owner's PAT."""

    def __init__(self, base_url: str, token: str, *, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout

    def _call(self, method: str, path: str, body: Any = None, *, lease_token: str | None = None) -> Any:
        import requests

        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        if lease_token is not None:
            headers[LEASE_TOKEN_HEADER] = lease_token
        try:
            resp = requests.request(
                method,
                f"{self.base_url}{path}",
                headers=headers,
                json=body,
                timeout=self._timeout,
            )
        except requests.RequestException as e:
            raise RepoSyncError("host_unreachable", f"Agnes at {self.base_url} can't be reached: {e}") from e
        if resp.status_code in (401, 403):
            raise RepoSyncError("host_auth", f"Agnes refused the token ({resp.status_code}); link again with a new PAT")
        if resp.status_code == 404:
            raise RepoSyncError(
                "host_not_enabled",
                "Agnes answered 404: the Personal Knowledge Repository is off for this account "
                "(the knowledge_repository switch, or you are outside its rollout groups)",
            )
        if resp.status_code == 409:
            raise self._conflict(resp, method, path)
        if resp.status_code >= 400:
            raise RepoSyncError(
                "host_error", f"Agnes answered {resp.status_code} to {method} {path}: {resp.text[:300]}"
            )
        return resp.json() if resp.content else None

    @staticmethod
    def _conflict(resp: Any, method: str, path: str) -> RepoSyncError:
        """A 409 carries ``{"detail": {"code", "message", "holder"?}}``; anything else is a host error."""
        try:
            detail = (resp.json() or {}).get("detail")
        except ValueError:
            detail = None
        if not isinstance(detail, dict) or not isinstance(detail.get("code"), str):
            return RepoSyncError("host_error", f"Agnes answered 409 to {method} {path}: {resp.text[:300]}")
        holder = detail.get("holder") if isinstance(detail.get("holder"), dict) else {}
        message = str(detail.get("message") or detail["code"])
        return RepoSyncError(detail["code"], f"Agnes refused: {message}", {"holder": holder})

    @staticmethod
    def _status(data: Mapping[str, Any]) -> HostStatus:
        return HostStatus(
            exists=bool(data.get("exists")),
            can_create=bool(data.get("can_create")),
            clone_url=str(data.get("clone_url") or ""),
            local_only_paths=tuple(str(p) for p in data.get("local_only_paths") or ()),
        )

    def status(self) -> HostStatus:
        return self._status(self._call("GET", "/api/me/knowledge-repository"))

    def create(self) -> HostStatus:
        return self._status(self._call("POST", "/api/me/knowledge-repository"))

    def set_local_only(self, entries: Sequence[str]) -> tuple[str, ...]:
        data = self._call("PUT", "/api/me/knowledge-repository/local-only-paths", {"paths": list(entries)})
        return tuple(str(p) for p in data.get("local_only_paths") or ())

    def delete(self) -> None:
        self._call("DELETE", "/api/me/knowledge-repository")

    def git_env(self) -> dict[str, str]:
        basic = base64.b64encode(f"scout:{self._token}".encode()).decode()
        return {
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "http.extraHeader",
            "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
            "GIT_CONFIG_KEY_1": "credential.helper",
            "GIT_CONFIG_VALUE_1": "",
        }

    def lease_acquire(self, holder_ref: str, holder_label: str) -> dict[str, Any]:
        body = {"holder_kind": "laptop", "holder_ref": holder_ref, "holder_label": holder_label}
        return dict(self._call("POST", "/api/me/knowledge-repository/lease", body))

    def lease_renew(self, token: str) -> dict[str, Any]:
        return dict(self._call("POST", "/api/me/knowledge-repository/lease/renew", lease_token=token))

    def lease_release(self, token: str) -> None:
        self._call("DELETE", "/api/me/knowledge-repository/lease", lease_token=token)

    def lease_status(self) -> dict[str, Any]:
        return dict(self._call("GET", "/api/me/knowledge-repository/lease"))


def lease_git_env(env: Mapping[str, str], token: str, generation: int) -> dict[str, str]:
    """*env* plus the two lease headers every push to an enforced repository carries (Agnes §5).

    They go in as more ``GIT_CONFIG_KEY_n``/``GIT_CONFIG_VALUE_n`` pairs, never
    on git's command line, so the token stays off the process list.
    """
    out = dict(env)
    n = int(out.get("GIT_CONFIG_COUNT") or 0)
    for header in (f"{LEASE_TOKEN_HEADER}: {token}", f"{LEASE_GENERATION_HEADER}: {generation}"):
        out[f"GIT_CONFIG_KEY_{n}"] = "http.extraHeader"
        out[f"GIT_CONFIG_VALUE_{n}"] = header
        n += 1
    out["GIT_CONFIG_COUNT"] = str(n)
    return out


# ----- state ----------------------------------------------------------------------------------


@dataclass
class LinkState:
    agnes_url: str
    clone_url: str
    linked_at: str
    last_laptop: str
    last_mirror: str
    pairs: list[dict[str, str]] = field(default_factory=list)
    confirmed_sync: list[str] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION

    def to_json(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def state_path(vault: Path) -> Path:
    return paths.state_dir(vault) / STATE_FILE


def load_state(vault: Path) -> LinkState | None:
    p = state_path(vault)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("schema")
        return LinkState(**data)
    except (OSError, ValueError, TypeError) as e:
        raise RepoSyncError("state_unreadable", f"{p} can't be read ({e}); unlink and link again") from e


def save_state(vault: Path, state: LinkState) -> None:
    p = state_path(vault)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(state.to_json(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def _vault_id(vault: Path) -> str:
    return hashlib.sha256(str(vault.resolve()).encode()).hexdigest()[:16]


def token_path(vault: Path) -> Path:
    return Path.home() / ".scout-secrets" / "agnes-knowledge-repo" / f"{_vault_id(vault)}.token"


def save_token(vault: Path, token: str) -> None:
    p = token_path(vault)
    p.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(p.parent, 0o700)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(token.strip() + "\n")
    os.chmod(p, 0o600)


def load_token(vault: Path) -> str:
    p = token_path(vault)
    try:
        mode = p.stat().st_mode & 0o777
        if mode & 0o077:
            raise RepoSyncError("token_insecure", f"{p} has permissions {oct(mode)}; run chmod 600 {p}")
        token = p.read_text(encoding="utf-8").strip()
    except FileNotFoundError as e:
        raise RepoSyncError("token_missing", f"no Agnes token at {p}; run scoutctl repo link again") from e
    if not token:
        raise RepoSyncError("token_missing", f"{p} is empty; run scoutctl repo link again")
    return token


# ----- gates -------------------------------------------------------------------------------------


def _test_vault(vault: Path, env: Mapping[str, str]) -> bool:
    named = env.get(TEST_VAULT_ENV, "")
    live = (Path.home() / paths.DEFAULT_DATA_DIR_NAME).resolve()
    return bool(named) and Path(named).expanduser().resolve() == vault.resolve() != live


def widening_guard_gate(vault: Path, env: Mapping[str, str]) -> None:
    """Refuse a real vault until the engine and the vault's pre-commit hook carry #504's widening guard.

    Fails closed on an engine without :data:`WIDENING_GUARD`, and on a vault
    whose ``.git/hooks/pre-commit`` isn't Scout's #504 hook (its marker plus the
    line that refuses a commit when the privacy check can't run), since the hook
    is where the guard runs.
    """
    if _test_vault(vault, env):
        return
    import importlib

    from scout.kb.hook import MARKER, hooks_dir

    module, name = WIDENING_GUARD
    try:
        has_guard = hasattr(importlib.import_module(module), name)
    except ImportError:
        has_guard = False
    if not has_guard:
        raise RepoSyncError(
            "privacy_guard_missing",
            "refused: this Scout engine doesn't have the per-note privacy guard yet (#504), which keeps a scheduled "
            "run from un-privating a note before it syncs. Update Scout, then link",
        )
    try:
        hook = (hooks_dir(vault) / "pre-commit").read_text(encoding="utf-8")
    except OSError:
        hook = ""
    if MARKER not in hook or HOOK_FAILS_CLOSED not in hook:
        raise RepoSyncError(
            "privacy_hook_missing",
            "refused: the vault's git pre-commit hook isn't Scout's current one, so the privacy guard may not run "
            "on commits. Run scoutctl kb install-hook, then link",
        )


def migration_gate(vault: Path) -> None:
    """Refuse a vault whose owner hasn't been through ``bootstrap privacy-migrate`` since it last changed.

    The consent marker (#510, written by ``privacy-migrate --accept`` at the end
    of /scout-update Step 3b) must exist and be ``current``. It only proves the
    owner went through the step; :func:`trading_gate` re-checks the live report.
    """
    from scout.scripts import privacy_migrate

    accepted = privacy_migrate.acceptance(vault, privacy_migrate.report(vault, persist=False))
    if accepted is None:
        raise RepoSyncError(
            "privacy_migrate_missing",
            "refused: this vault hasn't been through the per-note privacy migration. Run /scout-update "
            "(Step 3b reviews local-only trading paths and legacy visibility markers), then link again",
        )
    if not accepted.get("current"):
        raise RepoSyncError(
            "privacy_migrate_stale",
            "refused: the privacy migration report changed since the owner accepted it (a new trading folder or "
            "legacy visibility marker). Run /scout-update again, then link",
        )


def overlay_gate(policy: Policy) -> None:
    if "privacy-overlay-unreadable" in policy.warnings:
        raise RepoSyncError(
            "privacy_overlay_unreadable",
            "refused: scout-config.yaml can't be read, so the vault's local-only paths are unknown "
            "(scoutctl kb policy warns privacy-overlay-unreadable). Fix the file, then sync",
        )


def _trading_name(name: str) -> bool:
    folded = name.casefold()
    return any(part in folded for part in TRADING_NAME_PARTS)


def trading_candidates(vault: Path, rules: LocalOnlyRules) -> list[str]:
    """Paths that look like trading/brokerage data and would sync: root-level files and folders at any depth.

    Folders come from privacy-migrate's own detector (``trading/**`` style);
    root-level files (``TRADING.md``) use :data:`TRADING_NAME_PARTS`. Sorted.
    """
    from scout.scripts.privacy_migrate import local_only_proposals

    out: list[str] = []
    for child in sorted(vault.iterdir(), key=lambda p: p.name):
        if child.is_dir() or not _trading_name(child.name):
            continue
        if not rules.refused(child.name):
            out.append(child.name)
    for glob in local_only_proposals(vault):
        folder = glob[:-3] if glob.endswith("/**") else glob
        if not rules.refused(f"{folder}/x"):
            out.append(glob)
    return sorted(dict.fromkeys(out))


def trading_gate(vault: Path, rules: LocalOnlyRules, confirmed: Iterable[str]) -> None:
    pending = [p for p in trading_candidates(vault, rules) if p not in set(confirmed)]
    if pending:
        listed = "\n".join(f"  {p}" for p in pending)
        raise RepoSyncError(
            "trading_paths_unconfirmed",
            "refused: these look like trading or brokerage data and would sync to Agnes:\n"
            f"{listed}\n"
            "Keep each on this computer (scoutctl bootstrap privacy-migrate --local-only '<path>', or "
            "'<folder>/**' for a folder), or sync it anyway with --confirm-sync '<path>' as listed",
        )


def headless_gate(env: Mapping[str, str], what: str) -> None:
    from scout.scripts.privacy_migrate import headless_reason

    why = headless_reason(env)
    if why:
        raise RepoSyncError("headless_refused", f"refused: {why}. {what} needs the owner at the keyboard")


# ----- OTLP and gitignore ------------------------------------------------------------------------


def otlp_content_off(vault: Path) -> bool:
    """Merge :data:`OTLP_CONTENT_OFF` into ``<vault>/.claude/settings.json``. True when it changed."""
    p = vault / ".claude" / "settings.json"
    data: dict[str, Any] = {}
    if p.exists():
        try:
            loaded = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise RepoSyncError("settings_unreadable", f"{p} isn't valid JSON ({e}); fix it, then link") from e
        if not isinstance(loaded, dict):
            raise RepoSyncError("settings_unreadable", f"{p} isn't a JSON object; fix it, then link")
        data = loaded
    env = data.get("env") or {}
    if not isinstance(env, dict):
        raise RepoSyncError("settings_unreadable", f"{p} → env isn't an object; fix it, then link")
    if all(env.get(k) == v for k, v in OTLP_CONTENT_OFF.items()):
        return False
    data["env"] = {**env, **OTLP_CONTENT_OFF}
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return True


def ensure_ignored(vault: Path) -> bool:
    """Add ``.scout-state/repo-sync.json`` to the vault's .gitignore unless git already ignores it."""
    if git(vault, "check-ignore", "-q", STATE_REL, check=False).returncode == 0:
        return False
    gi = vault / ".gitignore"
    text = gi.read_text(encoding="utf-8") if gi.exists() else ""
    head = text if not text or text.endswith("\n") else text + "\n"
    gi.write_text(f"{head}# Agnes sync commit map (scoutctl repo): this computer's state\n{STATE_REL}\n", "utf-8")
    return True


# ----- remote ----------------------------------------------------------------------------------


def remote_head(vault: Path, host: Host, clone_url: str) -> str | None:
    out = git(vault, "ls-remote", clone_url, REMOTE_BRANCH, env=host.git_env()).stdout.split()
    return out[0] if out else None


def fetch_remote(vault: Path, host: Host, clone_url: str) -> str:
    git(vault, "fetch", "-q", "--no-tags", clone_url, f"+{REMOTE_BRANCH}:{REMOTE_REF}", env=host.git_env())
    sha = _rev(vault, REMOTE_REF)
    assert sha is not None
    return sha


_REFUSAL = re.compile(r"\b(local_only_path_pushed|secret_file_pushed|repository_\w+)\b")


def push(vault: Path, host: Host, clone_url: str, rev: str, lease: tuple[str, int] | None = None) -> None:
    env = host.git_env() if lease is None else lease_git_env(host.git_env(), *lease)
    proc = git(vault, "push", "--porcelain", clone_url, f"{rev}:{REMOTE_BRANCH}", env=env, check=False)
    if proc.returncode != 0:
        m = _REFUSAL.search(proc.stdout + proc.stderr)
        code = m.group(1) if m else "push_failed"
        tail = (proc.stderr.strip() or proc.stdout.strip()).splitlines()[-3:]
        raise RepoSyncError(code, "Agnes refused the push" + (f" ({code})" if m else "") + ": " + " | ".join(tail))
    git(vault, "update-ref", MIRROR_REF, rev)


# ----- link -----------------------------------------------------------------------------------------


@dataclass
class Result:
    action: str
    pushed: list[str] = field(default_factory=list)
    replayed: list[str] = field(default_factory=list)
    skipped: int = 0
    stripped: int = 0
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _head(vault: Path) -> str:
    head = _rev(vault, "HEAD")
    if head is None:
        raise RepoSyncError("no_commits", "refused: the vault has no commits yet")
    return head


def _host_entries_for(policy: Policy) -> list[str]:
    return [e for e in (host_entry(g) for g in policy.local_only) if e is not None]


def link(
    vault: Path,
    host: Host,
    *,
    agnes_url: str,
    confirm_sync: Sequence[str] = (),
    replace_remote: bool = False,
    env: Mapping[str, str] | None = None,
) -> Result:
    env = os.environ if env is None else env
    vault = vault.resolve()
    headless_gate(env, "Linking a vault")
    widening_guard_gate(vault, env)
    if load_state(vault) is not None:
        raise RepoSyncError("already_linked", "refused: this vault is already linked; run scoutctl repo sync")
    policy = load_policy(vault)
    overlay_gate(policy)
    migration_gate(vault)
    head = _head(vault)

    status = host.status()
    if not status.exists and not status.can_create:
        raise RepoSyncError(
            "host_cannot_create",
            "refused: Agnes won't create a Personal Knowledge Repository for this account "
            "(ask an admin for the knowledge_repository rollout)",
        )
    wanted = tuple(dict.fromkeys((*status.local_only_paths, *_host_entries_for(policy))))
    rules = LocalOnlyRules.build([policy], wanted)
    trading_gate(vault, rules, confirm_sync)
    if rules.local_only(POLICY_PATH):
        raise RepoSyncError("policy_path_local_only", f"refused: a Local-Only Path covers {POLICY_PATH}")
    existing = remote_head(vault, host, status.clone_url) if status.exists else None
    if existing is not None and not replace_remote:
        raise RepoSyncError(
            "remote_not_empty",
            "refused: the repository on Agnes already has history (an earlier link?). Link with "
            "--replace-remote to make this vault's filtered HEAD its new main, on top of that history",
        )

    notes: list[str] = []
    if not status.exists:
        status = host.create()
        notes.append("created the Personal Knowledge Repository on Agnes")
    host_entries = tuple(status.local_only_paths)
    if len(wanted) > len(host_entries):
        host_entries = host.set_local_only(list(wanted))
        notes.append(f"Agnes now keeps {len(host_entries)} Local-Only Path(s) for this vault")
    rules = LocalOnlyRules.build([policy], host_entries)
    if existing is not None:
        fetch_remote(vault, host, status.clone_url)

    if ensure_ignored(vault):
        notes.append(f"added {STATE_REL} to .gitignore")
    if otlp_content_off(vault):
        notes.append("turned OTLP content export off for sessions in this vault (.claude/settings.json)")

    builder = MirrorBuilder(vault, rules, host_entries)
    try:
        tree, stripped = builder.tree(head, policy)
    finally:
        builder.close()
    meta = commit_meta(vault, head)
    message = f"Scout: link snapshot of {head[:12]}\n\nEarlier history stays on the laptop.\n"
    snapshot = _commit_tree(vault, tree, [existing] if existing else [], message, meta.env())
    check_mirror_commits(vault, rules, [snapshot])
    from scout import repo_lease

    with repo_lease.for_push(vault, host) as lease:
        push(vault, host, status.clone_url, snapshot, lease)

    save_state(
        vault,
        LinkState(
            agnes_url=agnes_url,
            clone_url=status.clone_url,
            linked_at=_now(),
            last_laptop=head,
            last_mirror=snapshot,
            pairs=[{"laptop": head, "mirror": snapshot, "kind": "snapshot"}],
            confirmed_sync=sorted(set(confirm_sync)),
        ),
    )
    return Result(action="link", pushed=[snapshot], stripped=stripped, notes=notes)


# ----- replay --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Change:
    path: str
    old: TreeEntry | None
    new: TreeEntry | None


def mirror_changes(vault: Path, parent: str, rev: str) -> list[Change]:
    out = git(vault, "diff-tree", "-r", "-z", "--no-renames", "--no-commit-id", parent, rev).stdout
    parts = [p for p in out.split("\0")]
    changes: list[Change] = []
    i = 0
    while i + 1 < len(parts):
        meta, path = parts[i], parts[i + 1]
        i += 2
        old_mode, new_mode, old_sha, new_sha, _status = meta[1:].split()
        old = None if set(old_sha) == {"0"} else TreeEntry(old_mode, "blob", old_sha, path)
        new = None if set(new_sha) == {"0"} else TreeEntry(new_mode, "blob", new_sha, path)
        changes.append(Change(path, old, new))
    return changes


def _merge3(vault: Path, base: TreeEntry, ours: TreeEntry, theirs: TreeEntry) -> tuple[bytes, bool]:
    with tempfile.TemporaryDirectory(prefix="scout-repo-merge-") as tmp:
        files = []
        for name, entry in (("ours", ours), ("base", base), ("theirs", theirs)):
            f = Path(tmp) / name
            f.write_bytes(cat_blob(vault, entry.sha))
            files.append(str(f))
        proc = subprocess.run(
            ["git", "merge-file", "-p", "-L", "laptop", "-L", "base", "-L", "agnes", *files],
            capture_output=True,
            env=_git_env(),
            check=False,
        )
        if proc.returncode < 0 or proc.returncode > 127:  # pragma: no cover - git merge-file crashed
            raise RepoSyncError("git_failed", "git merge-file failed")
        return proc.stdout, proc.returncode == 0


def replay_one(vault: Path, rules: LocalOnlyRules, parent: str, rev: str) -> str | None:
    """Apply mirror commit ``rev`` (on ``parent``) to the vault as one commit; its sha, or None if empty.

    Local-Only paths and the generated policy file are never touched, so a
    server-made commit that drops a newly Local-Only path from the mirror
    leaves the laptop's copy alone. A path the laptop also changed is merged
    three ways; a conflict stops the sync before anything is written, with the
    merge left in ``<path>.proposed-merge``.
    """
    head = _head(vault)
    lines: list[str] = []
    for ch in mirror_changes(vault, parent, rev):
        if ch.path == POLICY_PATH or rules.refused(ch.path):
            continue
        ours = _blob_at(vault, head, ch.path)
        if ours == ch.new:
            continue
        same_as_base = (ours is None and ch.old is None) or (
            ours is not None and ch.old is not None and ours.sha == ch.old.sha
        )
        if same_as_base:
            target = ch.new
        elif ours is not None and ch.old is not None and ch.new is not None:
            merged, clean = _merge3(vault, ch.old, ours, ch.new)
            if not clean:
                (vault / f"{ch.path}.proposed-merge").write_bytes(merged)
                raise RepoSyncError(
                    "replay_conflict",
                    f"refused: {ch.path} changed both here and on Agnes; the merge is in "
                    f"{ch.path}.proposed-merge. Resolve the file, commit it, then sync",
                )
            sha = git(vault, "hash-object", "-w", "--stdin", input=merged).stdout.strip()
            target = TreeEntry(ours.mode, "blob", sha, ch.path)
        else:
            raise RepoSyncError(
                "replay_conflict",
                f"refused: {ch.path} was added, removed or changed on both sides; resolve it on the laptop, then sync",
            )
        if target is None:
            lines.append(f"0 {'0' * 40}\t{ch.path}")
        else:
            lines.append(f"{target.mode} {target.sha}\t{ch.path}")
    if not lines:
        return None

    with tempfile.TemporaryDirectory(prefix="scout-repo-replay-") as tmp:
        idx = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        git(vault, "read-tree", head, env=idx)
        git(vault, "update-index", "-z", "--index-info", env=idx, input="\0".join(lines) + "\0")
        tree = git(vault, "write-tree", env=idx).stdout.strip()
    meta = commit_meta(vault, rev)
    message = meta.message.rstrip("\n") + f"\n\n{REPLAY_TRAILER}: {rev}\n"
    new = _commit_tree(vault, tree, [head], message, meta.env())
    proc = git(vault, "read-tree", "-m", "-u", head, new, check=False)
    if proc.returncode != 0:
        raise RepoSyncError(
            "replay_dirty",
            "refused: uncommitted changes on this computer touch files Agnes changed; commit or discard them, then "
            f"sync ({proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else 'read-tree failed'})",
        )
    git(vault, "update-ref", "-m", "scoutctl repo sync: replay from Agnes", "HEAD", new, head)
    return new


# ----- sync ------------------------------------------------------------------------------------------


def sync(
    vault: Path,
    host: Host,
    *,
    confirm_sync: Sequence[str] = (),
    env: Mapping[str, str] | None = None,
    progress: Callable[[str], None] | None = None,
) -> Result:
    env = os.environ if env is None else env
    vault = vault.resolve()
    if confirm_sync:
        headless_gate(env, "Confirming a path for sync")
    widening_guard_gate(vault, env)
    state = load_state(vault)
    if state is None:
        raise RepoSyncError("not_linked", "refused: this vault isn't linked; run scoutctl repo link")
    policy = load_policy(vault)
    overlay_gate(policy)

    from scout import repo_lease

    with repo_lease.for_push(vault, host) as lease:
        return _sync_under_lease(vault, host, state, policy, confirm_sync, progress, lease)


def _sync_under_lease(
    vault: Path,
    host: Host,
    state: LinkState,
    policy: Policy,
    confirm_sync: Sequence[str],
    progress: Callable[[str], None] | None,
    lease: tuple[str, int],
) -> Result:
    status = host.status()
    if not status.exists:
        raise RepoSyncError(
            "remote_missing", "refused: the repository on Agnes is gone (erased?); unlink, then link again"
        )
    host_entries = status.local_only_paths
    wanted = list(dict.fromkeys((*host_entries, *_host_entries_for(policy))))
    if len(wanted) > len(host_entries):
        host_entries = host.set_local_only(wanted)
    rules = LocalOnlyRules.build([policy], host_entries)
    confirmed = sorted(set(state.confirmed_sync) | set(confirm_sync))
    trading_gate(vault, rules, confirmed)
    if rules.local_only(POLICY_PATH):
        raise RepoSyncError("policy_path_local_only", f"refused: a Local-Only Path covers {POLICY_PATH}")

    result = Result(action="sync")
    state.confirmed_sync = confirmed

    remote = remote_head(vault, host, status.clone_url)
    if remote is None:
        raise RepoSyncError("remote_empty", "refused: the repository on Agnes is empty; unlink, then link again")
    if remote != state.last_mirror:
        remote = fetch_remote(vault, host, status.clone_url)
        if _rev(vault, state.last_mirror) is None or not _is_ancestor(vault, state.last_mirror, remote):
            raise RepoSyncError(
                "mirror_diverged",
                "refused: the repository on Agnes no longer contains the last synced commit; unlink, then link "
                "again with --replace-remote",
            )
        revs = git(vault, "rev-list", "--reverse", "--first-parent", f"{state.last_mirror}..{remote}").stdout.split()
        parent = state.last_mirror
        for rev in revs:
            new = replay_one(vault, rules, parent, rev)
            if new is not None:
                result.replayed.append(new)
                state.pairs.append({"laptop": new, "mirror": rev, "kind": "replay"})
                if progress:
                    progress(f"replayed {rev[:12]} from Agnes as {new[:12]}")
            state.last_mirror = rev
            parent = rev
            save_state(vault, state)

    head = _head(vault)
    replayed = {p["laptop"] for p in state.pairs if p.get("kind") == "replay"}
    if _is_ancestor(vault, state.last_laptop, head):
        todo = git(vault, "rev-list", "--reverse", "--first-parent", f"{state.last_laptop}..{head}").stdout.split()
    else:
        todo = [head]
        result.notes.append("the vault's history no longer contains the last synced commit; pushing its HEAD")
    own = [c for c in todo if c not in replayed]

    builder = MirrorBuilder(vault, rules, host_entries)
    pushes: list[tuple[str, str]] = []
    try:
        policies = {c: builder.policy(c) for c in own}
        squash = bool(result.replayed) or any("privacy-overlay-unreadable" in p.warnings for p in policies.values())
        subjects = [commit_meta(vault, c).message.splitlines()[0] for c in own]
        refresh_only = not own
        if squash or refresh_only:
            squash = True
            own = [head]
            policies = {head: policy}
            if subjects:
                result.notes.append("Agnes had new commits, so this computer's commits go up as one")
        tip = state.last_mirror
        tip_tree = git(vault, "rev-parse", f"{tip}^{{tree}}").stdout.strip()
        for c in own:
            tree, stripped = builder.tree(c, policies[c])
            result.stripped = max(result.stripped, stripped)
            if tree == tip_tree:
                if not refresh_only:
                    result.skipped += len(subjects) if squash else 1
                continue
            meta = commit_meta(vault, c)
            if not squash:
                message = meta.message
            elif subjects:
                message = f"Scout sync: {len(subjects)} commit(s) from the laptop\n\n" + "".join(
                    f"- {s}\n" for s in subjects
                )
            else:
                message = "Scout sync: refresh privacy labels\n"
            tip = _commit_tree(vault, tree, [tip], message, meta.env())
            tip_tree = tree
            pushes.append((c, tip))
    finally:
        builder.close()

    if pushes:
        check_mirror_commits(vault, rules, [m for _, m in pushes])
        push(vault, host, status.clone_url, pushes[-1][1], lease)
        for c, m in pushes:
            state.pairs.append({"laptop": c, "mirror": m, "kind": "push"})
        state.last_mirror = pushes[-1][1]
        result.pushed = [m for _, m in pushes]
    state.last_laptop = head
    save_state(vault, state)
    return result


# ----- unlink ----------------------------------------------------------------------------------------


def unlink(
    vault: Path, host: Host | None, *, delete_remote: bool = False, env: Mapping[str, str] | None = None
) -> Result:
    env = os.environ if env is None else env
    vault = vault.resolve()
    headless_gate(env, "Unlinking a vault")
    result = Result(action="unlink")
    if delete_remote:
        if host is None:
            raise RepoSyncError("token_missing", "refused: no Agnes token to delete the repository with")
        host.delete()
        result.notes.append("deleted the Personal Knowledge Repository on Agnes")
    for ref in (MIRROR_REF, REMOTE_REF):
        git(vault, "update-ref", "-d", ref, check=False)
    state_path(vault).unlink(missing_ok=True)
    token_path(vault).unlink(missing_ok=True)
    if not delete_remote:
        result.notes.append("the repository on Agnes is kept; delete it with --delete-remote or in Agnes")
    result.notes.append("OTLP content export stays off for this vault (.claude/settings.json)")
    return result
