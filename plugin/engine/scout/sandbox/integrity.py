"""The post-run integrity check: a scheduled run leaves the vault's protected tracked files as it found them in git.

Spec ``docs/superpowers/specs/2026-11-15-run-write-sandbox-design.md`` §13, option 1 (Jordan, 2026-11-15). The write
sandbox keeps a run from writing a protected file on disk, but git's carve-outs let it write objects, the index and
refs, so a protected file's *committed* content could change without the file being written, and a later
unsandboxed checkout would write it. This check judges the result, not the commands that led to it.

The runner calls :func:`snapshot` outside the sandbox before claude starts and keeps the JSON in its own memory (a
shell variable, never a file the run could rewrite). After claude exits, still outside, :func:`verify` compares the
protected tracked paths in HEAD, in the index and in every branch, tag and stash ref the run created or moved
against that start state. A branch (or a detached HEAD) that changed gets one new commit putting each changed path
back the way it was at the start, and the index entries go back too. A tag or the stash that changed is reported,
not rewritten. The runner records the result in the run-outcome row (``integrity``) and run health turns red.

Linked worktrees share the vault's branches, so an interactive session in a vault worktree that commits on its own
branch during a run moves a ref too. Such a branch is left alone (a log line, no finding) only on evidence a run
can't forge: a linked worktree that existed at the snapshot still has it checked out, and that worktree's own index
holds exactly the tip's entry for every changed path. A sandboxed run can move any ref, but can't write another
worktree's HEAD or index (the profile carves out only the vault's own git dirs), so a change it plants on a
session's branch is still put back. Without an enforced sandbox (``SCOUT_SANDBOX_STATUS`` from the runner isn't
``enforced``: off, unavailable, Linux) the run could write that evidence too, so no branch is left alone. The
vault's own branch and a prunable worktree (its folder gone) never vouch. Accepted (spec §13): a run could commit
onto a session's branch exactly what that session has already staged for a protected path, and have it pass.

The protected tracked paths are ``hook_paths.protected_specs()`` entries inside the vault's work tree, outside its
git dir: the runner scripts, ``.gitattributes``, ``.gitmodules``, ``.mcp.json``, ``.claude/``, the other agent dirs,
the delegate state and ``.scout-state/schedule.yaml`` / ``triggers.yaml``. Paths compare NFC-normalised and
case-folded, as the sandbox does, so ``RUN-SCOUT.SH`` counts as ``run-scout.sh``.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from scout.kb import hook_paths
from scout.plural import noun

SNAPSHOT_VERSION = 1
# Refs a run's git can create or move that a later checkout, merge or `stash pop` reads from.
REF_PREFIXES = ("refs/heads/", "refs/tags/", "refs/stash", "refs/replace/")
# An unmerged index entry's key: the path, then this, then its stage (a NUL can't be in a git path).
STAGE_SEP = "\0"
GIT_TIMEOUT_S = 60

# verify()'s result: the status the runner records, and scoutctl's exit code for it.
STATUS_EXIT = {"clean": 0, "restored": 10, "unrestored": 11, "error": 12}

Entries = dict[str, str]  # vault-relative path as git stores it -> "<mode> <object id>"


class IntegrityError(Exception):
    """The check couldn't run (git failed, the snapshot is unreadable)."""


@dataclass(frozen=True)
class Scope:
    rel: str  # vault-relative, NFC-normalised and case-folded
    tree: bool


@dataclass
class Result:
    status: str  # a STATUS_EXIT key
    changed: dict[str, list[str]] = field(default_factory=dict)  # where ("HEAD", "index", a ref) -> paths
    restored: dict[str, str] = field(default_factory=dict)  # ref -> the restoring commit
    unrestored: list[str] = field(default_factory=list)  # refs reported but not rewritten
    left_alone: dict[str, list[str]] = field(default_factory=dict)  # a worktree session's own branch -> its paths
    left_alone_by: dict[str, str] = field(default_factory=dict)  # that branch -> the worktree's name
    reason: str = ""

    @property
    def exit_code(self) -> int:
        return STATUS_EXIT[self.status]

    def lines(self) -> list[str]:
        """What the runner's log gets: one line per finding, paths but never file content."""
        if self.status == "error":
            return [f"integrity check: couldn't run: {self.reason}"]
        out = [
            f"integrity check: {ref} changed protected {noun(len(paths), 'file')}: {', '.join(sorted(paths))}; "
            "committed in linked "
            f"worktree {self.left_alone_by.get(ref, '?')!r}, which has it checked out, so left alone"
            for ref, paths in sorted(self.left_alone.items())
        ]
        if self.status == "clean":
            return [*out, "integrity check: protected tracked files unchanged"]
        for where, paths in sorted(self.changed.items()):
            out.append(
                f"integrity check: {where} changed protected {noun(len(paths), 'file')}: {', '.join(sorted(paths))}"
            )
        for ref, commit in sorted(self.restored.items()):
            out.append(f"integrity check: restored them on {ref} in commit {commit[:12]}")
        for ref in self.unrestored:
            out.append(f"integrity check: {ref} still holds the change (not rewritten; review it by hand)")
        return out


def _worktrees(vault: Path, env: Mapping[str, str]) -> list[list[str]]:
    """``[admin dir, branch]`` for each other linked worktree with a branch checked out.

    Read from ``<common dir>/worktrees/*/HEAD``, which a sandboxed run can't write (the profile carves out only the
    vault's own git dirs). The vault's own admin dir, when the vault is itself a linked worktree, is left out, and
    so is a prunable one: its work tree is gone, so no session works there.
    """
    common = Path(_git(vault, ["rev-parse", "--git-common-dir"], env).strip())
    common = common if common.is_absolute() else vault / common
    own = os.path.realpath(_git(vault, ["rev-parse", "--absolute-git-dir"], env).strip())
    try:
        admins = sorted(p for p in (common / "worktrees").iterdir() if p.is_dir())
    except OSError:
        return []
    out = []
    for admin in admins:
        real = os.path.realpath(admin)
        if real == own or not _worktree_present(real):
            continue
        branch = _symbolic_head(vault, env, git_dir=real)
        if branch:
            out.append([real, branch])
    return out


def _worktree_present(admin: str) -> bool:
    """Whether the work tree an admin dir points at is still there (``git worktree list`` calls it prunable if not)."""
    try:
        target = Path(admin, "gitdir").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return False
    return bool(target) and os.path.exists(target)


def _symbolic_head(vault: Path, env: Mapping[str, str], *, git_dir: str | None = None) -> str | None:
    pre = [f"--git-dir={git_dir}"] if git_dir else []
    try:
        return _git(vault, [*pre, "symbolic-ref", "-q", "HEAD"], env).strip() or None
    except IntegrityError:
        return None


def _vouching_worktree(
    vault: Path,
    start: dict,
    ref: str,
    tip_entries: Entries,
    paths: list[str],
    scope_list: list[Scope],
    env: Mapping[str, str],
) -> str | None:
    """The name of a linked worktree that accounts for ``ref``'s change to ``paths``, or None.

    It must have existed at the snapshot with ``ref`` checked out, still have it checked out, and its own index must
    hold exactly the tip's entry for each changed path. A session committing from that worktree leaves it that
    way. A run can't: it can move the ref, but can't write another worktree's HEAD or index.
    """
    for admin, branch in start.get("worktrees", []):
        if branch != ref or _symbolic_head(vault, env, git_dir=admin) != ref:
            continue
        try:
            # Through its admin dir, never through that worktree's `.git` file, which a run may be able to rewrite.
            index = _index_entries(vault, scope_list, env, git_dir=admin)
        except IntegrityError:
            continue
        unmerged = any(_plain(k) in paths for k in index if STAGE_SEP in k)
        if not unmerged and all(index.get(p) == tip_entries.get(p) for p in paths):
            return os.path.basename(admin)
    return None


def _norm(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def scopes(vault: Path, env: Mapping[str, str] | None = None) -> list[Scope]:
    """The protected paths inside ``vault``'s work tree and outside its git dir, vault-relative."""
    env = os.environ if env is None else env
    bases = {os.path.realpath(vault), os.path.normpath(os.path.abspath(vault))}
    out: list[Scope] = []
    for spec in hook_paths.protected_specs(vault, env):
        expanded = hook_paths._expand(spec.path, env)
        for form in {os.path.realpath(expanded), os.path.normpath(os.path.abspath(expanded))}:
            for base in bases:
                if not form.startswith(base + "/"):
                    continue
                rel = _norm(form[len(base) + 1 :])
                if rel == ".git" or rel.startswith(".git/"):
                    continue  # never in a tree (a core.hooksPath elsewhere in the work tree is)
                scope = Scope(rel, spec.tree)
                if scope not in out:
                    out.append(scope)
    return out


def _in_scope(path: str, scope_list: list[Scope]) -> bool:
    key = _norm(path)
    return any(key == s.rel or (s.tree and key.startswith(s.rel + "/")) for s in scope_list)


def _git_env(env: Mapping[str, str]) -> dict[str, str]:
    # The runner's environment is trusted, but nothing should point git at another repository or index, or add
    # command-line config (which config files git reads stays as it is).
    drop = (
        "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_NAMESPACE",
        "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    )  # fmt: skip
    out = {
        k: v
        for k, v in env.items()
        if k not in drop
        and k not in ("GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS")
        and not k.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))
    }
    out["GIT_TERMINAL_PROMPT"] = "0"
    out["GIT_NO_REPLACE_OBJECTS"] = "1"  # read each object as stored
    return out


def _git(vault: Path, args: list[str], env: Mapping[str, str], *, input_text: str | None = None) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(vault), *args],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_S,
            check=False,
            env=dict(env),
            input=input_text,
            stdin=None if input_text is not None else subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise IntegrityError(f"git {args[0]} didn't run: {e}") from e
    if proc.returncode != 0:
        raise IntegrityError(f"git {args[0]} failed (exit {proc.returncode}): {proc.stderr.strip()[-300:]}")
    return proc.stdout


def _is_repo(vault: Path, env: Mapping[str, str]) -> bool:
    try:
        return _git(vault, ["rev-parse", "--is-inside-work-tree"], env).strip() == "true"
    except IntegrityError:
        return False


def _head(vault: Path, env: Mapping[str, str]) -> tuple[str | None, str | None]:
    """(HEAD's commit or None on an unborn branch, the branch HEAD names or None when detached)."""
    try:
        sha = _git(vault, ["rev-parse", "--verify", "-q", "HEAD^{commit}"], env).strip() or None
    except IntegrityError:
        sha = None
    try:
        branch = _git(vault, ["symbolic-ref", "-q", "HEAD"], env).strip() or None
    except IntegrityError:
        branch = None
    return sha, branch


def _refs(vault: Path, env: Mapping[str, str]) -> dict[str, str]:
    out = _git(vault, ["for-each-ref", "--format=%(objectname) %(refname)", *REF_PREFIXES], env)
    refs: dict[str, str] = {}
    for line in out.splitlines():
        sha, _, name = line.partition(" ")
        if name:
            refs[name] = sha
    return refs


def _tree_entries(vault: Path, commitish: str, scope_list: list[Scope], env: Mapping[str, str]) -> Entries:
    # The whole tree, filtered here: pathspec magic can't fold case and Unicode the way the sandbox does.
    out = _git(vault, ["ls-tree", "-r", "-z", "--full-tree", commitish], env)
    entries: Entries = {}
    for item in out.split("\0"):
        meta, _, path = item.partition("\t")
        if path and _in_scope(path, scope_list):
            mode, _kind, oid = meta.split(" ", 2)
            entries[path] = f"{mode} {oid}"
    return entries


def _index_entries(
    vault: Path, scope_list: list[Scope], env: Mapping[str, str], *, git_dir: str | None = None
) -> Entries:
    pre = [f"--git-dir={git_dir}"] if git_dir else []
    out = _git(vault, [*pre, "ls-files", "-s", "-z"], env)
    entries: Entries = {}
    for item in out.split("\0"):
        meta, _, path = item.partition("\t")
        if path and _in_scope(path, scope_list):
            mode, oid, stage = meta.split(" ", 2)
            entries[path if stage == "0" else f"{path}{STAGE_SEP}{stage}"] = f"{mode} {oid}"
    return entries


def _commit_of(vault: Path, sha: str, env: Mapping[str, str]) -> str | None:
    try:
        return _git(vault, ["rev-parse", "--verify", "-q", f"{sha}^{{commit}}"], env).strip() or None
    except IntegrityError:
        return None


def snapshot(vault: Path, env: Mapping[str, str] | None = None) -> dict:
    """The start state :func:`verify` compares against. Raises :class:`IntegrityError`."""
    env = os.environ if env is None else env
    genv = _git_env(env)
    scope_list = scopes(vault, env)
    snap: dict = {"version": SNAPSHOT_VERSION, "repo": False, "scopes": [[s.rel, s.tree] for s in scope_list]}
    if not _is_repo(vault, genv):
        return snap
    head, branch = _head(vault, genv)
    refs = _refs(vault, genv)
    trees: dict[str, Entries] = {}
    for sha in {*refs.values(), *([head] if head else [])}:
        commit = _commit_of(vault, sha, genv)
        if commit is not None and commit not in trees:
            trees[commit] = _tree_entries(vault, commit, scope_list, genv)
    snap.update(
        repo=True,
        head=head,
        branch=branch,
        refs=refs,
        index=_index_entries(vault, scope_list, genv),
        trees=trees,
        worktrees=_worktrees(vault, genv),
    )
    return snap


def _diff(before: Entries, after: Entries) -> list[str]:
    return sorted(p for p in {*before, *after} if before.get(p) != after.get(p))


def verify(vault: Path, start: dict, env: Mapping[str, str] | None = None, *, label: str = "scheduled") -> Result:
    """Compare the vault with ``start`` (from :func:`snapshot`) and put back what changed. Never raises."""
    env = os.environ if env is None else env
    try:
        return _verify(vault, start, env, label)
    except (IntegrityError, KeyError, TypeError, ValueError) as e:
        return Result("error", reason=str(e) or type(e).__name__)


def _verify(vault: Path, start: dict, env: Mapping[str, str], label: str) -> Result:
    if not isinstance(start, dict) or start.get("version") != SNAPSHOT_VERSION:
        raise IntegrityError("no start snapshot of this version")
    genv = _git_env(env)
    scope_list = [Scope(str(rel), bool(tree)) for rel, tree in start["scopes"]]
    if not start["repo"]:
        return Result("clean") if not _is_repo(vault, genv) else Result("error", reason="the vault became a repo")
    trees: dict[str, Entries] = start["trees"]
    start_head = start["head"]
    head_entries: Entries = trees.get(start_head, {}) if start_head else {}

    def baseline(ref: str) -> Entries:
        # A ref that existed keeps its own start content; a new one is held to what HEAD had.
        old = start["refs"].get(ref)
        old_commit = _commit_of(vault, old, genv) if old else None
        return trees.get(old_commit, {}) if old_commit else head_entries

    # A worktree's HEAD and index vouch for its branch only if the run couldn't write them: under an enforced
    # sandbox. The runner exports the status it started claude with; the run can't change the runner's env.
    trust_worktrees = env.get("SCOUT_SANDBOX_STATUS") == "enforced"
    result = Result("clean")
    head, branch = _head(vault, genv)
    # The vault's own branch is the run's: no linked worktree vouches for it, even one that has it checked out too.
    own_branches = {b for b in (start.get("branch"), branch) if b}
    now_refs = _refs(vault, genv)
    moved = sorted(r for r, sha in now_refs.items() if start["refs"].get(r) != sha)
    for ref in moved:
        commit = _commit_of(vault, now_refs[ref], genv)
        if commit is None:
            continue
        want = baseline(ref)
        have = _tree_entries(vault, commit, scope_list, genv)
        paths = _diff(want, have)
        if not paths:
            continue
        if trust_worktrees and ref.startswith("refs/heads/") and ref not in own_branches:
            worktree = _vouching_worktree(vault, start, ref, have, paths, scope_list, genv)
            if worktree is not None:
                result.left_alone[ref] = paths
                result.left_alone_by[ref] = worktree
                continue
        result.changed[ref] = paths
        if ref.startswith("refs/heads/"):
            result.restored[ref] = _restore_ref(vault, ref, now_refs[ref], commit, want, paths, genv, label)
        else:
            result.unrestored.append(ref)
    if branch is None and head and head != start_head:
        have = _tree_entries(vault, head, scope_list, genv)
        paths = _diff(head_entries, have)
        if paths:
            result.changed["HEAD"] = paths
            result.restored["HEAD"] = _restore_ref(vault, "HEAD", head, head, head_entries, paths, genv, label)
    want_index: Entries = start["index"]
    have_index = _index_entries(vault, scope_list, genv)
    index_paths = _diff(want_index, have_index)
    if index_paths:
        result.changed["index"] = sorted({_plain(p) for p in index_paths})
        _restore_index(vault, want_index, index_paths, genv)
    if result.changed:
        result.status = "unrestored" if result.unrestored else "restored"
    return result


def _plain(key: str) -> str:
    return key.split(STAGE_SEP, 1)[0]


def _entry_args(entries: Entries, paths: list[str], zero: str) -> str:
    """``git update-index --index-info`` lines putting each path back as ``entries`` has it (mode 0 removes it)."""
    lines = []
    for path in dict.fromkeys(_plain(p) for p in paths):
        entry = entries.get(path)
        if entry is None:
            lines.append(f"0 {zero}\t{path}")
        else:
            mode, oid = entry.split(" ", 1)
            lines.append(f"{mode} {oid}\t{path}")
    return "\n".join(lines) + "\n"


def _restore_ref(
    vault: Path,
    ref: str,
    old_sha: str,
    commit: str,
    want: Entries,
    paths: list[str],
    env: Mapping[str, str],
    label: str,
) -> str:
    """One new commit on ``ref`` with ``paths`` as ``want`` has them, everything else as ``commit`` has it."""
    with tempfile.TemporaryDirectory(prefix="scout-integrity-") as tmp:
        idx_env = {**env, "GIT_INDEX_FILE": os.path.join(tmp, "index")}
        _git(vault, ["read-tree", commit], idx_env)
        _git(vault, ["update-index", "--index-info"], idx_env, input_text=_entry_args(want, paths, "0" * len(commit)))
        tree = _git(vault, ["write-tree"], idx_env).strip()
    message = (
        f"Scout: restore protected files a {label} run changed in git\n\n"
        "The post-run integrity check (scout.sandbox.integrity) found these protected files changed on this ref\n"
        "during the run, and put them back as they were when it started:\n\n" + "".join(f"- {p}\n" for p in paths)
    )
    ident_env = dict(env)
    try:
        _git(vault, ["var", "GIT_COMMITTER_IDENT"], env)
    except IntegrityError:
        ident_env.update(
            GIT_AUTHOR_NAME="Scout", GIT_AUTHOR_EMAIL="scout@localhost",
            GIT_COMMITTER_NAME="Scout", GIT_COMMITTER_EMAIL="scout@localhost",
        )  # fmt: skip
    new = _git(vault, ["commit-tree", tree, "-p", commit, "-F", "-"], ident_env, input_text=message).strip()
    if ref == "HEAD":
        _git(vault, ["update-ref", "--no-deref", "-m", "scout: integrity restore", "HEAD", new, old_sha], env)
    else:
        _git(vault, ["update-ref", "-m", "scout: integrity restore", ref, new, old_sha], env)
    return new


def _restore_index(vault: Path, want: Entries, paths: list[str], env: Mapping[str, str]) -> None:
    """Put the index entries of ``paths`` back as ``want`` has them, unmerged stages included."""
    plain = sorted({_plain(p) for p in paths})
    _git(vault, ["update-index", "--force-remove", "--", *plain], env)
    lines = []
    for key, entry in want.items():
        if _plain(key) in plain:
            mode, oid = entry.split(" ", 1)
            stage = key.split(STAGE_SEP, 1)[1] if STAGE_SEP in key else "0"
            lines.append(f"{mode} {oid} {stage}\t{_plain(key)}" if stage != "0" else f"{mode} {oid}\t{key}")
    if lines:
        _git(vault, ["update-index", "--index-info"], env, input_text="\n".join(lines) + "\n")


def dumps(snap: dict) -> str:
    return json.dumps(snap, separators=(",", ":"), sort_keys=True)


def loads(text: str) -> dict:
    try:
        data = json.loads(text)
    except ValueError as e:
        raise IntegrityError(f"the start snapshot isn't JSON: {e}") from e
    if not isinstance(data, dict):
        raise IntegrityError("the start snapshot isn't an object")
    return data
