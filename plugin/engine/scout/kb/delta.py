"""``scoutctl kb delta``: the vault's own commits since a point, each changed path labelled (#453, spec §6.1).

Replaces ``git log`` / ``git diff HEAD~1`` in the phases. ``since`` is a git
revision (``HEAD~3``, a sha: the commits after it) or a duration ``<n>h``,
``<n>d`` or ``<n>w`` (``git log --since``). Commits come newest first, each
compared with its first parent (a merge shows what it brought in), with
renames detected; a rename lists both its source and its destination.

**Labels.** A path's label is the private one of: its label now (the privacy
index; a plumbing path is classified directly), and its label at the commit,
from the content it had there (for a deleted path, the content before the
commit). So a file deleted, moved out of ``personal/``, or made private since
is still labelled private. Non-markdown files take their path defaults, and a
binary or non-UTF-8 file never fails the command.

Paths the reader may not see are removed, and a commit left with none is
dropped: in v1, ``owner`` and ``autonomous`` see every path and any
``agent:*`` reader gets ``[]`` before git is run. ``with_diff`` adds each
commit's unified diff, which may hold private content: the labels tell the run.

Git plumbing lives in :mod:`scout.kb.git_io`; this module keeps
``scout/kb/read.py`` from growing further.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scout.kb.git_io import Change, LogCommit, blob_head, commit_changes, commit_diff, git_bytes, log_commits
from scout.kb.git_io import resolve_commit as git_resolve_commit
from scout.kb.privacy import (
    FULL_READERS,
    HEAD_BYTES,
    Label,
    Policy,
    classify,
    decode_head,
    label_file,
    load_policy,
    visible_to,
)
from scout.kb.read import Output, UsageError, all_labels

_DURATION = re.compile(r"^(\d{1,6})([hdw])$")
_UNITS = {"h": "hours", "d": "days", "w": "weeks"}


@dataclass(frozen=True)
class Commit:
    sha: str
    subject: str
    author: str
    paths: list[tuple[str, Label]]
    diff: str | None

    def to_json(self) -> dict[str, Any]:
        return {
            "sha": self.sha,
            "subject": self.subject,
            "author": self.author,
            "paths": [{"path": p, "label": label.to_json()} for p, label in self.paths],
            "diff": self.diff,
        }


def _is_repo(root: Path) -> bool:
    proc = git_bytes(root, "rev-parse", "--is-inside-work-tree")
    return proc.returncode == 0 and proc.stdout.strip() == b"true"


def _log(root: Path, since: str) -> list[LogCommit]:
    if not _is_repo(root):
        raise UsageError(f"not a git repository: {root}")
    m = _DURATION.match(since)
    if m:
        return log_commits(root, since_date=f"{int(m.group(1))} {_UNITS[m.group(2)]} ago")
    sha = git_resolve_commit(root, since)
    if sha is None:
        raise UsageError(f"--since must be a commit or a duration such as 8h, 2d or 1w: {since!r}")
    return log_commits(root, since_sha=sha)


class _Labeller:
    """Labels for one ``delta`` call: the current index is read once."""

    def __init__(self, root: Path, policy: Policy) -> None:
        self.root = root
        self.policy = policy
        self.now = all_labels(root, policy)

    def _current(self, rel: str) -> Label | None:
        label = self.now.get(rel)
        if label is not None:
            return label
        path = self.root / rel
        if path.is_file() and not path.is_symlink():
            return label_file(self.root, rel, self.policy)  # plumbing, or modified since the walk
        return None

    def _at(self, rev: str | None, rel: str) -> Label:
        """``rel``'s label from its content at ``rev`` (path defaults when absent or not markdown)."""
        text = None
        if rev is not None and rel.lower().endswith(".md"):
            data = blob_head(self.root, rev, rel, HEAD_BYTES)
            text = decode_head(data) if data is not None else None
        return classify(rel, text, self.policy)

    def label(self, rel: str, rev: str | None) -> Label:
        then = self._at(rev, rel)
        now = self._current(rel)
        for candidate in (now, then):
            if candidate is not None and candidate.visibility == "private":
                return candidate
        return now if now is not None else then


def _paths(labeller: _Labeller, commit: LogCommit, changes: list[Change]) -> list[tuple[str, Label]]:
    out: dict[str, Label] = {}
    for ch in changes:
        if ch.old_path is not None and ch.old_path not in out:
            out[ch.old_path] = labeller.label(ch.old_path, commit.parent)  # the rename's source, as it was
        rev = commit.parent if ch.status == "D" else commit.sha
        out[ch.path] = labeller.label(ch.path, rev)
    return list(out.items())


def delta(vault: Path, reader: str, since: str, with_diff: bool) -> list[Commit]:
    """The vault's commits since ``since`` that touch a path ``reader`` may see, labelled (module docstring).

    ``UsageError`` when ``since`` is neither a commit nor a duration, or the vault isn't a git repository.
    """
    if reader not in FULL_READERS:
        return []
    root = vault.resolve()
    commits = _log(root, since)
    if not commits:
        return []
    labeller = _Labeller(root, load_policy(root))
    out: list[Commit] = []
    for commit in commits:
        paths = [
            (p, label)
            for p, label in _paths(labeller, commit, commit_changes(root, commit))
            if visible_to(label, reader)
        ]
        if not paths:
            continue
        diff = commit_diff(root, commit) if with_diff else None
        out.append(Commit(sha=commit.sha, subject=commit.subject, author=commit.author, paths=paths, diff=diff))
    return out


def cmd_delta(vault: Path, reader: str, since: str, with_diff: bool) -> Output:
    commits = delta(vault, reader, since, with_diff)
    lines: list[str] = []
    labels: dict[str, Label] = {}
    for c in commits:
        lines.append(f"{c.sha[:12]} {c.subject}")
        for p, label in c.paths:
            lines.append(f"  {'[private] ' if label.visibility == 'private' else ''}{p}")
            labels.setdefault(p, label)
        if c.diff:
            lines.append(c.diff.rstrip("\n"))
    return Output(
        data=[c.to_json() for c in commits], lines=lines, labels=[label.to_json() for label in labels.values()]
    )
