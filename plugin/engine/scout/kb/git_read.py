"""What a ``git`` call reads, by the paths git itself reads them from (the KB read gate, in a scheduled run).

A git read names a note in three ways, none of which need spell the note's path:

- ``<rev>:<path>`` (``git show HEAD:./context/a.md``, ``git cat-file -p :./a.md``) names a path in a commit's
  TREE. ``./x`` and ``../x`` are relative to the folder git runs in; ``x`` and ``:/x`` are relative to the
  repository's top, whatever folder git runs in.
- A pathspec (``git log -p context/``, ``git diff HEAD -- a.md``, ``git grep x context/``) is relative to the
  folder git runs in (``:/x`` and ``:(top)x`` to the top) and matches every path below it, or every path its
  wildcards match (``'*.md'`` crosses folders).
- A content read with no path at all reads the whole repository (``git show HEAD``, ``git diff``, ``git log
  -p``), or, for ``git grep`` and ``git archive``, everything below the folder git runs in.

The folder git runs in is the shell's, then each ``-C`` (repeatable, cumulative). ``--git-dir``/``GIT_DIR``
and ``--work-tree``/``GIT_WORK_TREE`` choose the repository and its top. :func:`parse` reads a call's words
into a :class:`GitRead`; the gate (``scout.hooks.kb_read_gate``) resolves its folders on disk and judges each
tree path with :func:`tree_path` and :func:`top_and_repo`. A call that prints names only (``git status``,
``git log --oneline``, ``git diff --stat``) reads nothing here.
"""

from __future__ import annotations

import os
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path

# git's own options before the subcommand, and the ones that take the next word as their value.
_GLOBAL_VALUES = {
    "-C",
    "-c",
    "--git-dir",
    "--work-tree",
    "--namespace",
    "--config-env",
    "--super-prefix",
    "--attr-source",
}

# Output flags that print a patch (file contents), for the subcommands that print one only when asked.
_PATCH_FLAGS = {
    "-p", "-u", "--patch", "--patch-with-raw", "--patch-with-stat", "--cc", "-c", "--binary", "--remerge-diff",
    "--color-words", "--word-diff", "-W", "--function-context", "--unified",
}  # fmt: skip
_PATCH_PREFIXES = ("--word-diff=", "--color-words=", "--unified=", "-U")
# Output flags that print names, counts or ids only: with none of _PATCH_FLAGS beside them, no file is read.
_NAMES_FLAGS = {
    "--stat", "--numstat", "--shortstat", "--name-only", "--name-status", "--summary", "--dirstat", "--raw",
    "-s", "--no-patch", "--quiet", "--compact-summary", "-X", "--cumulative",
}  # fmt: skip
_NAMES_PREFIXES = ("--stat=", "--dirstat=", "-X")

# Subcommands that print a patch unless told to print names only.
_PATCH_BY_DEFAULT = {"show", "diff", "difftool", "range-diff", "format-patch"}
# Subcommands that print a patch only when asked (a patch flag, or log's -L, which prints the lines it follows).
_PATCH_WHEN_ASKED = {"log", "whatchanged", "reflog", "diff-tree", "diff-index", "diff-files", "status", "request-pull"}
# Subcommands that always print what they read.
_ALWAYS = {"blame", "annotate", "grep", "archive", "fast-export", "merge-tree", "unpack-file"}
# Subcommands that print hunks of the files they are given with -p/--patch (interactive: a run's empty stdin
# still gets the first hunk).
_INTERACTIVE = {"add", "checkout", "reset", "restore", "commit"}

# Each subcommand's options whose value is the next word (git's parse-options and revision.c accept it there).
# Only ones that surely take one: an option left out reads its value as a positional, which can only make the
# gate stricter, while one wrongly here would hide the path after it.
_DIFF_LOG_VALUES = {"-n", "--author", "--committer", "--grep", "-S", "-G", "-O", "--output"}
_VALUE_OPTIONS: dict[str, set[str]] = {
    **dict.fromkeys(("log", "whatchanged", "reflog", "show", "diff", "diff-tree", "diff-index", "diff-files"),
                    _DIFF_LOG_VALUES),
    "grep": {"-A", "-B", "-C", "-m", "--max-depth", "--threads", "--context", "--after-context", "--before-context"},
    "blame": {"--ignore-rev", "--date"},
    "annotate": {"--ignore-rev", "--date"},
    "archive": {"--format", "-o", "--output", "--prefix", "--remote", "--exec", "--add-virtual-file"},
    "format-patch": {*_DIFF_LOG_VALUES, "-o", "--output-directory", "--start-number", "--subject-prefix", "--to",
                     "--cc", "--in-reply-to", "--signature-file", "--suffix", "--base"},
    "range-diff": {"--creation-factor"},
    "restore": {"-s", "--source"},
}  # fmt: skip
# Option values that are files the subcommand reads from disk: `git grep -f patterns`, `git blame --contents f`,
# `git blame -S revs-file`, `git archive --add-file f`.
_FILE_VALUES: dict[str, set[str]] = {
    "grep": {"-f"},
    "blame": {"--contents", "-S", "--ignore-revs-file"},
    "annotate": {"--contents", "-S", "--ignore-revs-file"},
    "archive": {"--add-file"},
}


@dataclass
class GitRead:
    """The paths a git call reads, as written (shell expansion is the caller's): see the module docstring."""

    sub: str
    chdirs: list[str] = field(default_factory=list)  # each -C, in order
    git_dir: str | None = None  # --git-dir; None leaves it to GIT_DIR (the caller's)
    work_tree: str | None = None  # --work-tree; None leaves it to GIT_WORK_TREE
    trees: list[str] = field(default_factory=list)  # the path of each `<rev>:<path>`
    pathspecs: list[str] = field(default_factory=list)  # after `--`, or magic (`:(top)x`): paths for sure
    maybe_paths: list[str] = field(default_factory=list)  # before `--`: a path if one exists there (else a rev)
    files: list[str] = field(default_factory=list)  # read from disk, relative to the folder git runs in
    no_index: bool = False  # `diff --no-index`, `grep --no-index`: every path is a file on disk
    # With no path: "repo" reads the whole repository (the vault's KB included), "cwd" everything below the
    # folder git runs in, None nothing.
    unbounded: str | None = None
    # An earlier part of the command moved, linked or copied folders (`ln -s`, `mv`, `cp`): "changed", what the
    # folders are when git runs isn't what the gate sees now; "vault-copy", it also copied the repository it runs
    # in (`git worktree add`, `git clone .`), the vault's in a run.
    fs_changed: str = ""
    # The targets of the links the command made before (`ln -s Vault V2`), each placed from the session's folder
    # (a relative one is from the link's own folder): one that is the vault, or in or above it, makes a copy of it.
    links: list[str] = field(default_factory=list)


def split_global(args: list[str]) -> tuple[list[str], str | None, str | None, int]:
    """``(-C folders, --git-dir, --work-tree, index of the subcommand)`` of a git call's arguments."""
    chdirs: list[str] = []
    git_dir = work_tree = None
    i = 0
    while i < len(args):
        a = args[i]
        if a in _GLOBAL_VALUES and i + 1 < len(args):
            value = args[i + 1]
            if a == "-C":
                chdirs.append(value)
            elif a == "--git-dir":
                git_dir = value
            elif a == "--work-tree":
                work_tree = value
            i += 2
            continue
        if a.startswith("--git-dir="):
            git_dir = a.split("=", 1)[1]
        elif a.startswith("--work-tree="):
            work_tree = a.split("=", 1)[1]
        elif not a.startswith("-"):
            return chdirs, git_dir, work_tree, i
        i += 1
    return chdirs, git_dir, work_tree, len(args)


def _patch(args: list[str]) -> bool:
    return any(a in _PATCH_FLAGS or a.startswith(_PATCH_PREFIXES) for a in args)


def _names_only(args: list[str]) -> bool:
    return any(a in _NAMES_FLAGS or a.startswith(_NAMES_PREFIXES) for a in args)


def rev_path(word: str) -> str | None:
    """The path of ``<rev>:<path>`` (``HEAD:./a.md`` → ``./a.md``, ``:0:a.md`` → ``a.md``, ``:./a.md``), else None.
    A ``:`` inside ``@{…}`` or ``^{…}`` belongs to the rev (``HEAD@{10:00}:a.md``). ``:/x`` keeps its ``/``."""
    if word.startswith((":(", ":!", ":^")):
        return None  # pathspec magic, not a rev
    if re.match(r"^:[0-3]:", word):
        return word[3:]
    depth = 0
    for i, c in enumerate(word):
        if c == "{" and i and word[i - 1] in "@^":
            depth += 1
        elif c == "}" and depth:
            depth -= 1
        elif c == ":" and not depth:
            return word[i + 1 :]
    return None


def option_values(args: list[str], longs: set[str], shorts: set[str]) -> list[str]:
    """The values a subcommand's words give the value-taking options named, as git's parse-options reads them: a
    long option (``longs``, without ``--``) by its name or any prefix of it (``--exe`` for ``--exec``; a prefix git
    finds ambiguous only over-judges), as ``--opt=value`` or ``--opt value``; a short one (``shorts``) alone or at
    the end of a cluster of flags (``-x cmd``, ``-qx cmd``, ``-ixcmd``), its value the rest of the word or the
    next word. Stops at ``--``."""
    out: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            break
        if a.startswith("--") and len(a) > 2:
            name, eq, value = a[2:].partition("=")
            if any(long.startswith(name) for long in longs):
                if eq:
                    out.append(value)
                elif i + 1 < len(args):
                    out.append(args[i + 1])
                    i += 1
        elif a.startswith("-") and len(a) > 1 and not a.startswith("--"):
            for j, c in enumerate(a[1:], 1):
                if c in shorts:
                    if a[j + 1 :]:
                        out.append(a[j + 1 :])
                    elif i + 1 < len(args):
                        out.append(args[i + 1])
                        i += 1
                    break
        i += 1
    return out


def _cat_file(sub_args: list[str], read: GitRead) -> GitRead | None:
    """``git cat-file -p|--textconv|--filters|--batch|blob <object>`` prints a file; ``-t``, ``-s``, ``-e``,
    ``--batch-check`` and a commit, tag or tree print no file's content."""
    flags = [a for a in sub_args if a.startswith("-")]
    plain = [a for a in sub_args if not a.startswith("-")]
    content = any(
        f in ("-p", "--textconv", "--filters", "--batch-command") or f == "--batch" or f.startswith("--batch=")
        for f in flags
    )
    if plain and plain[0] == "blob":
        content, plain = True, plain[1:]
    if not content:
        return None
    for word in plain:
        path = rev_path(word)
        if path is not None:
            read.trees.append(path)
    if not read.trees:
        read.unbounded = "repo"  # an object id, or objects named on stdin: any file in the repository
    return read


def parse(args: list[str]) -> GitRead | None:
    """What the git call with these arguments (the words after ``git``, quotes removed) reads, or None when it
    prints no file's content."""
    from scout.kb.raw_read import fold

    chdirs, git_dir, work_tree, at = split_global(args)
    if at >= len(args):
        return None
    # On a case-insensitive disk `git ARCHIVE` runs git-archive from git's exec path: compare as the disk does.
    sub, sub_args = fold(args[at]), args[at + 1 :]
    read = GitRead(sub=sub, chdirs=chdirs, git_dir=git_dir, work_tree=work_tree)
    if sub == "cat-file":
        return _cat_file(sub_args, read)
    if sub == "request-pull":  # `<start> <url> [<end>]`: no paths, and -p prints the whole diff
        if "-p" not in sub_args:
            return None
        read.unbounded = "repo"
        return read
    if sub == "merge-file":  # `-p`/`--stdout` prints the merge of the three files it names
        if not any(a in ("-p", "--stdout") for a in sub_args):
            return None
        read.files = [a for a in sub_args if not a.startswith("-")]
        return read
    if sub == "stash":
        action = next((a for a in sub_args if not a.startswith("-")), "push")
        shows = action in ("show", "list") and _patch(sub_args)
        if not (shows or (action == "push" and any(a in ("-p", "--patch") for a in sub_args))):
            return None
        read.unbounded = "repo"  # a stash holds any file's changes; `stash show` takes no path
        return read
    if sub in _INTERACTIVE:
        if not any(a in ("-p", "--patch") for a in sub_args):
            return None
        read.unbounded = "repo"
    elif sub in _PATCH_BY_DEFAULT:
        if _names_only(sub_args) and not _patch(sub_args) and sub not in ("format-patch", "range-diff"):
            # `git show --stat HEAD:a.md` still prints the blob: a tree path is read whatever the flags say.
            _collect(sub, sub_args, read)
            # A bare object id may be a blob (`git show --stat <blob>` prints it): any file, so recorded.
            # A rev from a substitution (`$(git rev-parse HEAD:x)`) may be one too.
            blob = sub == "show" and any(OBJECT_ID.match(w) or "$" in w or "`" in w for w in read.maybe_paths)
            read.pathspecs, read.maybe_paths = [], []
            if blob:
                read.unbounded = "repo"
                return read
            return read if read.trees or read.files else None
        read.unbounded = "repo"
    elif sub in _PATCH_WHEN_ASKED:
        asked = _patch(sub_args) or any(a == "-L" or a.startswith("-L") for a in sub_args)
        if sub == "log":
            asked = asked or "--follow" in sub_args
        if sub == "status":
            asked = any(a in ("-v", "--verbose", "-vv") for a in sub_args)
        if not asked:
            return None
        read.unbounded = "repo"
    elif sub in _ALWAYS:
        read.unbounded = "cwd" if sub in ("grep", "archive") else "repo"
    else:
        return None
    _collect(sub, sub_args, read)
    if sub == "unpack-file" and not read.trees:
        read.unbounded = "repo"
    return read


def _collect(sub: str, args: list[str], read: GitRead) -> None:
    """Sort a subcommand's words into ``read``'s tree paths, pathspecs, maybe-paths and files."""
    pattern_given = sub != "grep"  # git grep's first positional is the pattern, unless -e/-f gives it
    values, files = _VALUE_OPTIONS.get(sub, set()), _FILE_VALUES.get(sub, set())
    after_dashes = False
    i = 0
    while i < len(args):
        a = args[i]
        if after_dashes:
            read.pathspecs.append(a)
        elif a == "--":
            after_dashes = True
        elif a == "--no-index":
            read.no_index = True
        elif a in files and i + 1 < len(args):
            read.files.append(args[i + 1])
            pattern_given = pattern_given or a in ("-f", "--file")
            i += 1
        elif a.startswith(("--contents=", "--add-file=", "--file=")):
            read.files.append(a.split("=", 1)[1])
        elif a == "-L" and i + 1 < len(args):
            _line_range(args[i + 1], read)
            i += 1
        elif a.startswith("-L") and len(a) > 2:
            _line_range(a[2:], read)
        elif a in ("-e", "--and", "--or", "--not", "(", ")") and sub == "grep":
            pattern_given = True
            i += 1 if a == "-e" else 0
        elif a in values and i + 1 < len(args):
            i += 1
        elif a.startswith("-") and a != "-":
            pass
        elif not pattern_given:
            pattern_given = True
        else:
            path = rev_path(a)
            if path is not None:
                read.trees.append(path)
            if a.startswith((":(", ":!", ":^", ":/")):
                read.pathspecs.append(a)
            else:  # a rev, or a path (`notes/10:00.md` too): the gate looks for it on disk, as git does
                read.maybe_paths.append(a)
        i += 1


def _line_range(value: str, read: GitRead) -> None:
    """``git log -L <start>,<end>:<file>`` / ``-L :<func>:<file>``: the file is a path from the folder git runs in.
    blame's ``-L`` names none (its file is a positional)."""
    parts = value.split(":", 2) if value.startswith(":") else value.split(":", 1)
    if len(parts) == (3 if value.startswith(":") else 2) and parts[-1]:
        read.pathspecs.append(parts[-1])


# ----- judging tree paths (the gate's side: it has the disk) -----------------------------------------------


def find_repo(start: Path, git_dir: Path | None, work_tree: Path | None) -> tuple[Path, Path] | None:
    """``(top, common git folder)`` of the repository git uses from ``start``, as git finds it: ``--git-dir`` with
    ``--work-tree`` (or ``start`` itself as the top, git's rule for a git dir given alone), else the nearest
    folder at or above ``start`` holding ``.git`` (a folder, or a file naming one: a linked worktree, whose
    ``commondir`` is the main repository's). None when there is none."""
    if git_dir is None:
        for folder in (start, *start.parents):
            dot = folder / ".git"
            if dot.is_dir() or dot.is_file():
                git_dir = _gitdir_of(dot)
                top = work_tree or folder
                break
        else:
            return None
        if git_dir is None:
            return None
    else:
        top = work_tree or start
    return top, _common(git_dir)


def _gitdir_of(dot: Path) -> Path | None:
    if dot.is_dir():
        return dot
    try:
        text = dot.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = re.match(r"gitdir:\s*(.+)", text.strip())
    if m is None:
        return None
    target = Path(m[1].strip())
    return (target if target.is_absolute() else dot.parent / target).resolve()


def _common(git_dir: Path) -> Path:
    try:
        common = (git_dir / "commondir").read_text(encoding="utf-8").strip()
    except OSError:
        return git_dir.resolve()
    target = Path(common)
    return (target if target.is_absolute() else git_dir / target).resolve()


def same_folder(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def rel_to(top: Path, folder: Path) -> str | None:
    """``folder`` relative to ``top`` by file identity (a case-insensitive disk, a symlinked spelling), with each
    component's own letter case where it exists; ``""`` for ``top`` itself, None when ``folder`` is not below it.
    A folder or top not on disk yet (made earlier in the same command) is compared by its resolved spelling,
    ignoring letter case."""
    for depth, anc in enumerate((folder, *folder.parents)):
        if same_folder(anc, top):
            parts = folder.parts[len(folder.parts) - depth :] if depth else ()
            return "/".join(parts)
    if folder.exists() and top.exists():
        return None
    a, b = [p.casefold() for p in top.parts], [p.casefold() for p in folder.parts]
    return "/".join(folder.parts[len(a) :]) if b[: len(a)] == a else None


def pathspec_magic(word: str) -> tuple[set[str], str] | None:
    """A pathspec's magic and the path after it, as git reads them: ``:(top,exclude)x`` (long), or the short form,
    a run of ``/`` (top), ``!`` and ``^`` (exclude) in any order after the ``:``, perhaps closed by another ``:``
    (``:/!docs``, ``:!/docs``, ``:^:docs``). None for a word with no leading ``:``."""
    if not word.startswith(":"):
        return None
    m = re.match(r"^:\(([^)]*)\)(.*)$", word)
    if m:
        return {w.strip() for w in m[1].split(",")}, m[2]
    magic: set[str] = set()
    i = 1
    while i < len(word) and word[i] in "/!^":
        magic.add("top" if word[i] == "/" else "exclude")
        i += 1
    if i < len(word) and word[i] == ":":
        i += 1
    return magic, word[i:]


def is_exclusion(word: str) -> bool:
    parsed = pathspec_magic(word)
    return parsed is not None and "exclude" in parsed[0]


def exclusion(word: str, prefix: str) -> str | None:
    """The repository path an exclusion pathspec leaves out (``:!x``, ``:^x``, ``:(exclude)x`` from the folder,
    ``:/!x``, ``:(exclude,top)x`` from the top), else None (not an exclusion, or one above the top)."""
    parsed = pathspec_magic(word)
    if parsed is None or "exclude" not in parsed[0]:
        return None
    magic, rest = parsed
    return tree_path(f":(top){rest}" if "top" in magic else rest, prefix, pathspec=True)


# Subcommands for which a pathspec made only of exclusions means "everything below the folder git runs in" (git's
# PATHSPEC_PREFER_CWD); for the rest it means the whole tree.
PREFER_CWD = {"grep", "archive"}
# Subcommands that read every file below a `<rev>:<folder>` they are given (a tree): `git grep x HEAD:action-items`,
# `git archive HEAD:`, `git diff A:dir B:dir`. `git show`/`cat-file` of a tree print only its names.
TREE_READERS = {"grep", "archive", "diff", "diff-tree", "diff-index", "difftool", "merge-tree"}
# A word that may be an object id (a blob's included): 7 to 64 hex digits.
OBJECT_ID = re.compile(r"^[0-9a-fA-F]{7,64}$")


def tree_path(word: str, prefix: str, *, pathspec: bool) -> str | None:
    """The repository path ``word`` names, from a folder ``prefix`` below the top: a ``<rev>:<path>`` path
    (``./x``, ``../x`` from the folder; ``x``, ``/x`` from the top) or a pathspec (from the folder, but
    ``:/x``/``:(top)x`` from the top). None for an exclusion, or a path above the top (git refuses it)."""
    top = False
    if pathspec:
        parsed = pathspec_magic(word)
        if parsed is not None:
            magic, word = parsed
            if "exclude" in magic:
                return None
            top = "top" in magic
    else:
        top = not (word in (".", "..") or word.startswith(("./", "../")))
    joined = posixpath.normpath(word.lstrip("/") if top else posixpath.join(prefix or ".", word))
    if joined == ".." or joined.startswith("../"):
        return None
    return "" if joined == "." else joined


WILD = re.compile(r"[*?\[]")


def fixed_part(path: str) -> str:
    """A pathspec up to the component before its first wildcard: what it matches is all below that folder."""
    parts = path.split("/")
    for i, part in enumerate(parts):
        if WILD.search(part):
            return "/".join(parts[:i])
    return path
