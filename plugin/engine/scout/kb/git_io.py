"""Thin git plumbing for kb lint. All output is read NUL-separated (-z)
where paths appear, so spaces and non-ASCII names round-trip."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        cwd=repo,
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def repo_root(start: Path) -> Path:
    return Path(git(start, "rev-parse", "--show-toplevel").stdout.strip()).resolve()


def main_root(repo: Path) -> Path:
    """The main working tree's root, even when ``repo`` is a linked worktree.

    A commit made from a vault worktree must still log under the MAIN vault's
    ``.scout-logs/`` — that's what connector-health and dreaming read — never
    under the worktree, which is invisible to both. ``--git-common-dir`` is
    shared by every worktree and lives at ``<main root>/.git``, so its parent
    is the main root. Falls back to ``repo`` itself if git fails (not a repo,
    or anything else unexpected) — never let this block logging entirely.
    """
    proc = git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir", check=False)
    if proc.returncode != 0:
        return repo
    common_dir = proc.stdout.strip()
    if not common_dir:
        return repo
    return Path(common_dir).resolve().parent


@dataclass(frozen=True)
class StagedPath:
    rel: str
    head_rel: str | None


def _has_head(repo: Path) -> bool:
    return git(repo, "rev-parse", "--verify", "-q", "HEAD", check=False).returncode == 0


def staged_paths(repo: Path) -> list[StagedPath]:
    out = git(repo, "diff", "--cached", "--name-status", "-z", "-M", "--diff-filter=ACMR").stdout
    parts = [p for p in out.split("\0") if p]
    result: list[StagedPath] = []
    i = 0
    while i < len(parts):
        status = parts[i]
        if status[0] in "RC":
            src, dst = parts[i + 1], parts[i + 2]
            result.append(StagedPath(dst, src if status[0] == "R" else None))
            i += 3
        else:
            rel = parts[i + 1]
            result.append(StagedPath(rel, rel if status[0] == "M" else None))
            i += 2
    return result


def staged_size(repo: Path, rel: str) -> int:
    return int(git(repo, "cat-file", "-s", f":{rel}").stdout.strip())


def head_size(repo: Path, rel: str) -> int | None:
    if not _has_head(repo):
        return None
    proc = git(repo, "cat-file", "-s", f"HEAD:{rel}", check=False)
    return int(proc.stdout.strip()) if proc.returncode == 0 else None


def staged_text(repo: Path, rel: str) -> str:
    return git(repo, "show", f":{rel}").stdout


def head_text(repo: Path, rel: str) -> str | None:
    """``rel``'s content at HEAD, or None when it (or HEAD) does not exist."""
    proc = git(repo, "show", f"HEAD:{rel}", check=False)
    return proc.stdout if proc.returncode == 0 else None


def head_files(repo: Path, prefix: str) -> list[str]:
    """Paths under ``prefix`` that exist at HEAD (empty before the first commit)."""
    if not _has_head(repo):
        return []
    out = git(repo, "ls-tree", "-r", "-z", "--name-only", "HEAD", "--", prefix).stdout
    return sorted(p for p in out.split("\0") if p)


def changed_lines(repo: Path, rel: str, head_rel: str | None = None) -> tuple[list[tuple[int, str]], list[str]]:
    """Added (``+``) lines with their 1-based line numbers in ``rel``, and the
    removed (``-``) lines of the same file diff.

    A pure rename restricts the pathspec to the destination, which prevents
    git from pairing it with its source: the whole file then shows as added,
    and every line re-triggers the diary/mega-line checks. When ``head_rel``
    is given and differs from ``rel``, diff both paths together with rename
    detection so a true rename yields no hunks, and only the hunks for the
    destination side (``+++ b/<rel>``) are parsed.
    """
    if head_rel and head_rel != rel:
        out = git(repo, "diff", "--cached", "-M", "-U0", "--no-color", "--no-ext-diff", "--", head_rel, rel).stdout
        return _parse_changed_lines(out, rel)
    out = git(repo, "diff", "--cached", "-U0", "--no-color", "--no-ext-diff", "--", rel).stdout
    return _parse_changed_lines(out, None)


def added_lines(repo: Path, rel: str, head_rel: str | None = None) -> list[tuple[int, str]]:
    """Added lines only — see ``changed_lines``."""
    return changed_lines(repo, rel, head_rel)[0]


_C_ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}


def _unquote_c(s: str) -> str:
    """Undo git's C-style path quoting (``"b/a\\"b.md"`` → ``b/a"b.md``).
    Octal escapes are raw UTF-8 bytes, so decode the byte string at the end."""
    out = bytearray()
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            octal = s[i + 1 : i + 4]
            if len(octal) == 3 and all(c in "01234567" for c in octal):
                out.append(int(octal, 8))
                i += 4
                continue
            if nxt in _C_ESCAPES:
                out.append(_C_ESCAPES[nxt])
                i += 2
                continue
        out += ch.encode("utf-8")
        i += 1
    return out.decode("utf-8", errors="replace")


def _header_path(line: str) -> str:
    """The path in a ``+++ `` / ``--- `` diff header. git appends a tab when the
    path contains a space, and C-quotes a path with special characters (a
    double quote, a backslash, control characters) even with core.quotepath
    off."""
    s = line[4:]
    if s.endswith("\t"):
        s = s[:-1]
    if len(s) >= 2 and s.startswith('"') and s.endswith('"'):
        s = _unquote_c(s[1:-1])
    return s


def _parse_changed_lines(diff_text: str, want_rel: str | None) -> tuple[list[tuple[int, str]], list[str]]:
    result: list[tuple[int, str]] = []
    removed: list[str] = []
    lineno = 0
    in_hunk = False
    in_wanted_file = want_rel is None
    # split("\n"), not splitlines(): a form feed or U+2028 inside a markdown
    # line must not split it into two diff lines.
    for ln in diff_text.split("\n"):
        if ln.startswith("diff --git "):
            # Start of a new file's diff entry: reset header-parsing state so a
            # multi-file diff (e.g. a rename pair) doesn't leak the previous
            # file's "+++ " match or hunk state into this one.
            in_hunk = False
            if want_rel is not None:
                in_wanted_file = False
            continue
        if not in_hunk and ln.startswith("+++ "):
            if want_rel is not None:
                in_wanted_file = _header_path(ln) == f"b/{want_rel}"
            continue
        m = _HUNK.match(ln)
        if m:
            lineno = int(m.group(1))
            in_hunk = True
            continue
        if not in_hunk or not in_wanted_file:
            continue  # file header lines (---/+++) precede the first hunk
        if ln.startswith("+"):
            result.append((lineno, ln[1:]))
            lineno += 1
        elif ln.startswith("-"):
            removed.append(ln[1:])
    return result, removed


def tracked_files(repo: Path) -> list[str]:
    out = git(repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard").stdout
    return sorted({p for p in out.split("\0") if p})


# ----- history, for `scoutctl kb delta` (#453) -----------------------------------------


def git_bytes(repo: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess[bytes]:
    """``git`` with raw bytes out: history holds files that aren't UTF-8."""
    return subprocess.run(["git", "-c", "core.quotepath=false", *args], cwd=repo, check=check, capture_output=True)


def _text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


@dataclass(frozen=True)
class Change:
    """One changed path of a commit: ``status`` is git's letter (A, M, D, R, T ...);
    ``old_path`` is the source of a rename, else None."""

    status: str
    path: str
    old_path: str | None = None


@dataclass(frozen=True)
class LogCommit:
    sha: str
    parent: str | None  # the first parent; None for a root commit
    author: str
    subject: str


def resolve_commit(repo: Path, rev: str) -> str | None:
    """The commit ``rev`` names, or None (an option-like ``rev`` never reaches git as an option)."""
    if not rev or rev.startswith("-"):
        return None
    proc = git_bytes(repo, "rev-parse", "--verify", "-q", "--end-of-options", f"{rev}^{{commit}}")
    sha = _text(proc.stdout).strip()
    return sha if proc.returncode == 0 and sha else None


def log_commits(repo: Path, *, since_sha: str | None = None, since_date: str | None = None) -> list[LogCommit]:
    """Commits reachable from HEAD, newest first: those after ``since_sha``
    (``since_sha..HEAD``), or those since ``since_date`` (``git log --since``).
    Empty when HEAD doesn't exist."""
    if not _has_head(repo):
        return []
    args = ["log", "-z", "--format=%H%x1f%P%x1f%an%x1f%s"]
    if since_date is not None:
        args += [f"--since={since_date}", "HEAD"]
    else:
        args.append(f"{since_sha}..HEAD")
    proc = git_bytes(repo, *args, check=True)
    commits: list[LogCommit] = []
    for record in _text(proc.stdout).split("\0"):
        record = record.lstrip("\n")
        if not record:
            continue
        sha, parents, author, subject = [*record.split("\x1f", 3), "", "", ""][:4]
        first = parents.split()[0] if parents.split() else None
        commits.append(LogCommit(sha=sha, parent=first, author=author, subject=subject))
    return commits


def _tree_range(commit: LogCommit) -> list[str]:
    """``diff-tree`` arguments for a commit against its first parent (a merge
    shows what it brought into the first-parent line; a root, everything)."""
    return [commit.parent, commit.sha] if commit.parent else ["--root", commit.sha]


def commit_changes(repo: Path, commit: LogCommit) -> list[Change]:
    """The paths ``commit`` changed against its first parent, renames detected.

    Paths are relative to ``repo`` (``--relative``): a vault that is a folder
    of a bigger repository sees only its own paths."""
    proc = git_bytes(
        repo,
        "diff-tree",
        "-r",
        "-z",
        "-M",
        "--name-status",
        "--no-commit-id",
        "--relative",
        *_tree_range(commit),
        check=True,
    )
    parts = [p for p in _text(proc.stdout).split("\0") if p]
    changes: list[Change] = []
    i = 0
    while i < len(parts):
        status = parts[i][:1]
        if status in ("R", "C") and i + 2 < len(parts):
            changes.append(Change(status, parts[i + 2], parts[i + 1] if status == "R" else None))
            i += 3
        elif i + 1 < len(parts):
            changes.append(Change(status, parts[i + 1]))
            i += 2
        else:
            break
    return changes


def commit_diff(repo: Path, commit: LogCommit, paths: Sequence[str]) -> str:
    """``commit``'s unified diff against its first parent, limited to ``paths``
    (binary files as git summarises them; bad UTF-8 replaced). No paths, no diff."""
    if not paths:
        return ""
    proc = git_bytes(
        repo,
        "diff-tree",
        "-p",
        "-M",
        "--no-color",
        "--no-commit-id",
        "--relative",
        *_tree_range(commit),
        "--",
        *(f":(literal){p}" for p in paths),
        check=True,
    )
    return _text(proc.stdout)


class BlobUnreadable(Exception):
    """A path exists at a revision but its content couldn't be read."""


def blob_head(repo: Path, rev: str, rel: str, limit: int) -> bytes | None:
    """The first ``limit`` bytes of ``rel`` at ``rev`` (only those are read), or
    None when it isn't there. ``BlobUnreadable`` when it is there and reading fails."""
    spec = f"{rev}:./{rel}"
    if git_bytes(repo, "cat-file", "-e", spec).returncode != 0:
        return None
    try:
        proc = subprocess.Popen(
            ["git", "cat-file", "blob", spec], cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
    except OSError as e:
        raise BlobUnreadable(rel) from e
    assert proc.stdout is not None
    try:
        data = proc.stdout.read(limit)
    finally:
        proc.stdout.close()
    if len(data) < limit:
        if proc.wait() != 0:
            raise BlobUnreadable(rel)
    else:  # the head is all we need
        proc.kill()
        proc.wait()
    return data


def shallow_boundaries(repo: Path) -> frozenset[str]:
    """Commits whose parents a shallow clone cut off (empty in a full clone)."""
    proc = git_bytes(repo, "rev-parse", "--path-format=absolute", "--git-path", "shallow")
    if proc.returncode != 0:
        return frozenset()
    try:
        text = Path(_text(proc.stdout).strip()).read_text(encoding="ascii", errors="replace")
    except OSError:
        return frozenset()
    return frozenset(line.strip() for line in text.splitlines() if line.strip())


def recorded_parent(repo: Path, sha: str) -> str | None:
    """The first parent written in ``sha``'s commit object when that parent's
    tree is present locally (a shallow boundary hides it from ``git log``), else None."""
    proc = git_bytes(repo, "cat-file", "commit", sha)
    if proc.returncode != 0:
        return None
    for line in _text(proc.stdout).split("\n"):
        if not line:
            break  # end of the header
        if line.startswith("parent "):
            parent = line.split()[1]
            ok = git_bytes(repo, "cat-file", "-e", f"{parent}^{{tree}}").returncode == 0
            return parent if ok else None
    return None
