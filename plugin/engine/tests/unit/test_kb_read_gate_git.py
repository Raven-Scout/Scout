"""kb-read-gate judges a run's git read by the repository paths git reads, not by how the command spells them.

``git -C action-items show HEAD:./context/x.md`` names no KB path, yet git reads ``action-items/context/x.md``
from the commit's tree: ``./`` is relative to the folder git runs in (the shell's, then each ``-C``), and a
plain ``x`` or ``:/x`` to the repository's top (``--work-tree``/``GIT_WORK_TREE``, or the folder holding
``.git``). Pathspecs (``git log -p context/``, ``git grep x``) are relative to the folder too and cover every
path below them. Folders count by file identity, so letter case and symlinks don't matter, and only the vault's
own repository holds the KB. A content read with no path at all (``git diff``, ``git show HEAD``) is recorded,
not denied (DENY_GIT_WHOLE_REPO_READS), and so is one the gate can't work out (DENY_UNRESOLVED_GIT_READS).

Synthetic vaults and repositories only. The shell pre-filter decides which calls reach Python
(``test_hook_prefilter_differential.py`` checks it sends these); these tests call the gate directly.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from scout.hooks import kb_read_gate as gate
from scout.kb import git_read
from tests.unit.test_hooks_kb_read_gate import RUN, _denied, _payload


def _git(cwd: Path, *args: str) -> None:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(cwd),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Alex",
        "GIT_AUTHOR_EMAIL": "alex@example.com",
        "GIT_COMMITTER_NAME": "Alex",
        "GIT_COMMITTER_EMAIL": "alex@example.com",
    }
    subprocess.run(
        ["git", "-c", "maintenance.auto=false", "-c", "gc.auto=0", "-c", "init.defaultBranch=main", *args],
        cwd=cwd, env=env, check=True, capture_output=True,
    )  # fmt: skip


def _repo(root: Path, files: tuple[str, ...]) -> None:
    for rel in files:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("x\n")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")


VAULT_FILES = (
    "knowledge-base/a.md",
    "knowledge-base/people/priya.md",
    "knowledge-base.md",
    "action-items/context/x.md",
    "action-items/action-items-2026-11-23.md",
    "docs/a.md",
    "scripts/run.py",
    "scout-config.yaml",
)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A home with a vault at ``~/Vault`` that is its own git repository; ``~/other`` is another repository laid
    out like it, and ``~/plain/action-items`` a folder outside any repository."""
    h = (tmp_path / "home").resolve()
    _repo(h / "Vault", VAULT_FILES)
    _repo(h / "other", ("action-items/context/x.md", "docs/a.md"))
    (h / "plain" / "action-items").mkdir(parents=True)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(h / "Vault"))
    return h


def _run(home: Path, command: str, *, cwd: Path | None = None, env: dict | None = None):
    full = {**RUN, "HOME": str(home), **(env or {})}
    return gate.run(stdin=_payload("Bash", {"command": command}, cwd or home / "Vault"), env=full)


def _events(home: Path) -> list[dict]:
    rows: list[dict] = []
    for path in sorted((home / "Vault" / ".scout-logs").glob("schedule-events-*.jsonl")):
        rows += [json.loads(line) for line in path.read_text().splitlines()]
    return [r for r in rows if r["kind"] == "kb_read_gate.unresolved_read"]


# ----- must deny ---------------------------------------------------------------------------------------------

# Every form reads a KB note (or folder) git finds through a folder or a path that doesn't spell it.
MUST_DENY = [
    # the forms found in review: allowed on main
    "git -C action-items show HEAD:./context/x.md",
    "git -C action-items diff HEAD -- context/x.md",
    "git -C action-items log -p context/",
    "git -C action-items grep x context/",
    # the other subcommands that print a file
    "git -C action-items cat-file -p HEAD:./context/x.md",
    "git -C action-items cat-file blob HEAD:./context/x.md",
    "git -C action-items log --patch -- context/x.md",
    "git -C action-items log -u context/x.md",
    "git -C action-items log --follow context/x.md",
    "git -C action-items log -L 1,1:context/x.md",
    "git -C action-items blame context/x.md",
    "git -C action-items annotate context/x.md",
    "git -C action-items grep x",  # no path: everything below action-items
    "git -C action-items grep -e x HEAD",
    "git -C action-items grep x HEAD:./context",
    "git -C action-items archive HEAD context/ | tar x",
    "git -C action-items archive HEAD:./context | tar x",
    "git -C action-items archive HEAD | tar x",  # no path: everything below action-items
    "git -C action-items show HEAD:./context/",  # the folder's note names
    "git -C action-items show HEAD -- context/",
    "git -C action-items diff-tree -p HEAD -- context/",
    "git -C action-items format-patch --stdout -1 -- context/",
    "git -C action-items difftool HEAD -- context/x.md",
    "git -C action-items unpack-file HEAD:./context/x.md",
    "git -C action-items show HEAD:../action-items/context/x.md",
    "git -C action-items show :./context/x.md",
    "git -C action-items show :0:./context/x.md",
    "git -C action-items show 'HEAD@{0}:./context/x.md'",
    "git -C docs show HEAD:../knowledge-base/a.md",
    "git -C docs log -p -- :/knowledge-base",
    "git -C docs log -p -- ':(top)action-items/context'",
    "git -C docs show HEAD:knowledge-base.md",  # a plain path is from the top, wherever git runs
    "git show :/knowledge-base/a.md",
    "git log -p -- action-items",  # a folder above the KB covers it
    "git log -p -- .",
    "git grep x",  # from the vault: everything
    "git -C action-items add -p context/",
    "git -C action-items status -v context/",
    # a file read from disk
    "git -C action-items grep -f context/x.md y docs",
    "git -C action-items blame --contents context/x.md action-items-2026-11-23.md",
    "git -C action-items diff --no-index context/x.md ../docs/a.md",
    "git -C action-items grep --no-index x",
    # letter case (a case-insensitive disk's folder names; git's tree paths judged ignoring case)
    "git -C ACTION-ITEMS show HEAD:./context/x.md",
    "git -C Action-Items show HEAD:./Context/x.md",
    "git show HEAD:ACTION-ITEMS/context/x.md",
    "git -C action-items diff HEAD -- CONTEXT/x.md",  # git's history match is case-sensitive: the gate errs strict
    "git -C DOCS show HEAD:../KNOWLEDGE-BASE/a.md",
    # quotes
    "git -C 'action-items' show 'HEAD:./context/x.md'",
    'git -C "action-items" log -p -- "context/"',
    "git -C action-items grep x -- 'context/*.md'",
    "git -C action-items log -p -- '*.md'",  # a wildcard crosses folders in git
    "git log -p -- 'action-items/c*'",
    'git -C "action-"items show HEAD:./con"text"/x.md',
    # --git-dir / --work-tree, GIT_DIR / GIT_WORK_TREE
    "git --git-dir=.git --work-tree=. show HEAD:action-items/context/x.md",
    "git --git-dir=$PWD/.git --work-tree=$PWD -C action-items show HEAD:./context/x.md",
    "git -C action-items --git-dir=$PWD/.git --work-tree=.. show HEAD:./context/x.md",
    "git --git-dir .git --work-tree . log -p -- action-items/context",
    "git -C action-items --git-dir=../.git --work-tree=.. show HEAD:./context/x.md",
    "git -C action-items --git-dir=../.git show HEAD:action-items/context/x.md",
    "GIT_DIR=$PWD/.git GIT_WORK_TREE=$PWD git -C action-items show HEAD:./context/x.md",
    "env GIT_DIR=$PWD/.git GIT_WORK_TREE=$PWD git -C action-items show HEAD:./context/x.md",
    "GIT_DIR=../.git GIT_WORK_TREE=.. git -C action-items show HEAD:./context/x.md",  # from the -C folder
    "export GIT_DIR=$PWD/.git GIT_WORK_TREE=$PWD; git -C action-items show HEAD:./context/x.md",
    "export GIT_DIR=$PWD/.git; git -C action-items show HEAD:action-items/context/x.md",
    # the vault's git folder with another work tree: the tree is still the vault's
    "git --git-dir=$HOME/Vault/.git --work-tree=$HOME/plain -C $HOME/plain/action-items show HEAD:./context/x.md",
    "GIT_DIR=~/Vault/.git GIT_WORK_TREE=~/plain git -C ~/plain/action-items log -p -- context/",
    # -C chains (cumulative; an absolute one starts again)
    "git -C action-items -C context show HEAD:./x.md",
    "git -C . -C action-items show HEAD:./context/x.md",
    "git -C docs -C ../action-items log -p context/",
    "git -C docs -C $HOME/Vault/action-items show HEAD:./context/x.md",
    "git -C action-items -C '' show HEAD:./context/x.md",
    "git -C $HOME/Vault/action-items show HEAD:./context/x.md",
    "git -C ~/Vault/action-items show HEAD:./context/x.md",
    "d=action-items; git -C $d show HEAD:./context/x.md",
    "for d in docs action-items; do git -C $d show HEAD:./context/x.md; done",
    "git -C action-{items,x} show HEAD:./context/x.md",
    "cd action-items && git show HEAD:./context/x.md",
    "cd action-items; git -C context log -p .",
    "env -C action-items git show HEAD:./context/x.md",
    # review of #817: a pathspec made only of exclusions reads everything else
    "git log -p -- ':(exclude)docs'",
    "git log -p -- ':!docs'",
    "git log -p -- ':^docs'",
    "git diff HEAD~1 -- ':(exclude)docs'",
    "git diff HEAD~1 -- ':!docs'",
    "git grep x -- ':(exclude)docs'",
    "git grep x -- ':!docs'",
    "git archive HEAD -- ':(exclude)docs' | tar x",
    "git log -p -1 -- ':(exclude,top)docs'",
    "git -C docs log -p -1 -- ':(exclude,top)docs'",  # log's exclusions apply to the whole tree
    "git -C docs log -p -- ':(exclude)x'",
    "git -C docs diff HEAD~1 -- ':!x'",
    "git -C action-items grep x -- ':!action-items-2026-11-23.md'",
    "git grep x -- ':!knowledge-base' ':!knowledge_base' ':!knowledge-base.md'",  # context/ is left
    # review of #817: a <rev>:<folder> tree that grep, archive or a two-tree diff reads all of
    "git grep x HEAD:",
    "git grep x HEAD:action-items",
    "git grep x HEAD:knowledge-base",
    "git archive HEAD: | tar x",
    "git archive HEAD:action-items | tar x",
    "git diff HEAD~1: HEAD:",
    "git diff HEAD~1:action-items HEAD:action-items",
    "git diff-tree -p HEAD~1:action-items HEAD:action-items",
    "git -C docs grep x HEAD:..",
    "git -C docs grep x HEAD:",
    "git -C docs archive HEAD: | tar x",
    # review of #817: inside .git, git works from the top
    "git -C .git grep x HEAD",
    "git -C .git archive HEAD | tar x",
    "git -C .git log -p -1 -- .",
    "git -C .git/refs grep x HEAD",
    "cd .git && git grep x HEAD",
    "cd .git && git log -p -1 -- .",
    "cd .git/refs && git -C .. grep x HEAD",
    "git -C .GIT grep x HEAD",
    # review of #817: a work tree made in the same command (not on disk when the gate looks)
    "mkdir -p ~/new/action-items && git --git-dir=$HOME/Vault/.git --work-tree=$HOME/new"
    " -C $HOME/new/action-items show HEAD:./context/x.md",
    "mkdir -p ~/new/action-items && git --git-dir=$HOME/Vault/.git --work-tree=$HOME/new"
    " -C $HOME/new/action-items log -p -- context",
    "mkdir -p ~/new/q && GIT_DIR=$HOME/Vault/.git GIT_WORK_TREE=$HOME/new git -C $HOME/new/q grep x HEAD -- ..",
    "mkdir -p ~/new/q && git --git-dir=$HOME/Vault/.git --work-tree=$HOME/new -C $HOME/new/q"
    " show HEAD:../action-items/context/x.md",
    # review round 2: short magic in any order (`/` top, `!`/`^` exclude), perhaps closed by `:`
    "git log -p -- ':/!docs'",
    "git log -p -- ':/^docs'",
    "git log -p -- ':!/docs'",
    "git log -p -- ':!:docs'",
    "git log -p -- ':^:docs'",
    "git diff HEAD~1 -- ':/!docs'",
    "git grep x -- ':/!docs'",
    "git log -p -- \\:\\!docs",
    "git -C docs log -p -- ':/!x'",
    # review round 2: merge-tree over trees
    "git merge-tree HEAD:action-items HEAD:action-items HEAD:action-items",
    "git merge-tree HEAD: HEAD: HEAD:",
    # review round 2: the commands git runs for you, judged as if run directly
    "git rebase -q -x 'cat act*/cont*/x.md' HEAD",
    "git rebase -q --exec 'cat action-items/context/x.md' HEAD",
    "git rebase --exec='cat act*/cont*/x.md' HEAD",
    "git -C docs rebase -x 'cat ../act*/cont*/x.md' HEAD",
    "git bisect run sh -c 'cat act*/cont*/x.md; exit 0'",
    "git difftool -y --extcmd='sh -c \"cat act*/cont*/x.md\"' HEAD -- docs",
    "git difftool -y -x 'cat act*/cont*/x.md' HEAD -- docs",
    "git submodule foreach 'cat act*/cont*/x.md'",
    "git submodule foreach --recursive cat act*/cont*/x.md",
    "GIT_PAGER='cat act*/cont*/x.md;:' git log -1",
    "PAGER='cat act*/cont*/x.md;:' git log -1",
    "export GIT_PAGER='cat act*/cont*/x.md;:'; git log -1",
    # review round 2: a variable as the program
    "G=git; $G archive HEAD | tar x",
    "G=git; $G -C action-items show HEAD:./context/x.md",
    # review round 2: whitespace, quotes and backslashes in git's words (the shell removes them)
    "git\tgrep x",
    "git\tlog\t-p\t--\t.",
    "git 'log' -p -- .",
    'git "grep" x',
    "git l\\og -p -- .",
    "git gr\\ep x",
    "g\\it grep x",
    "git \\\nlog -p -- .",
    "git ar''chive HEAD | tar x",
    '"git" archive HEAD | tar x',
    # letter case in git's own words (a case-insensitive disk runs git and git-archive for them; #818)
    "GIT archive HEAD | tar x",
    "Git grep x",
    "git ARCHIVE HEAD | tar x",
    "git -C action-items SHOW HEAD:./context/x.md",
    "GIT -C action-items Log -p context/",
    "git REBASE -x 'cat act*/cont*/x.md' HEAD",
    # review round 2: merge-file prints the files it names
    "git merge-file -p action-items/context/x.md docs/a.md docs/a.md",
    "git -C action-items merge-file -p context/x.md ../docs/a.md ../docs/a.md",
]

# Reads after the command changes folders the gate looks at (review round 2): denied when the command names the
# vault or a KB folder, or copied the vault's repository. The deny may come from the write checks first.
AFTER_FOLDER_CHANGES = [
    "ln -s $HOME/Vault/action-items ~/l2 && git -C ~/l2 show HEAD:./context/x.md",
    "ln -s $HOME/Vault/action-items ~/l2 && git -C ~/l2 log -p .",
    "ln -s $HOME/Vault ~/v2 && git -C ~/v2/action-items show HEAD:./context/x.md",
    "ln -s $HOME/Vault ~/v2 && cd ~/v2/action-items && git grep x",
    "ln -s $HOME/Vault/.git ~/g && git --git-dir=$HOME/g grep x HEAD",
    "ln -s $HOME/Vault/.git ~/g && cd ~ && GIT_DIR=g git log -p -1 -- .",
    "git worktree add -q ~/wt2 && git -C ~/wt2/action-items show HEAD:./context/x.md",
    "git worktree add -q ~/wt2 && git -C ~/wt2 grep x",
    "git worktree add -q ~/wt2 && cd ~/wt2 && git log -p -- .",
    "git clone -q . ~/cl && git -C ~/cl/action-items show HEAD:./context/x.md",
    "git clone -q . ~/cl && git -C ~/cl grep x",
    "mv docs docs2 && ln -s action-items docs && git -C docs show HEAD:./context/x.md",
]


# Denied, though not by the KB rule's own message or not as a read git really makes: a filter-branch is a commit
# written without the hook; git itself fails on a git folder it can't find, but the gate can't tell one from a
# folder made earlier, and these name action-items/; a folder from a substitution in a command that names it.
STRICT_DENIES = [
    "git filter-branch -f --tree-filter 'cat act*/cont*/x.md' HEAD",
    "git --git-dir=.git -C action-items show HEAD:./context/x.md",
    "GIT_DIR=.git GIT_WORK_TREE=. git -C action-items show HEAD:./context/x.md",
    "git -C $(echo action-items) show HEAD:./context/x.md",
]


@pytest.mark.parametrize("command", STRICT_DENIES)
def test_strict_denies(home: Path, command: str) -> None:
    _denied(_run(home, command))


@pytest.mark.parametrize("command", AFTER_FOLDER_CHANGES)
def test_a_git_read_after_the_command_changes_folders_is_denied(home: Path, command: str) -> None:
    _denied(_run(home, command))


@pytest.mark.parametrize("command", MUST_DENY)
def test_a_git_read_of_a_kb_note_is_denied(home: Path, command: str) -> None:
    reason = _denied(_run(home, command))
    assert "knowledge-base/" in reason


@pytest.mark.parametrize(
    "command",
    [
        "git show HEAD:./context/x.md",
        "git log -p context/",
        "git grep x",
        "git -C .. show HEAD:./action-items/context/x.md",
        "git show HEAD:../action-items/context/x.md",
    ],
)
def test_from_a_cwd_in_action_items_git_reads_its_context(home: Path, command: str) -> None:
    _denied(_run(home, command, cwd=home / "Vault" / "action-items"))


def test_a_symlinked_folder_is_the_folder_it_points_to(home: Path) -> None:
    (home / "link").symlink_to(home / "Vault" / "action-items")
    _denied(_run(home, "git -C ~/link show HEAD:./context/x.md"))
    _denied(_run(home, "git -C ~/link log -p context/"))


def test_a_linked_worktree_of_the_vault_holds_the_kb(home: Path) -> None:
    _git(home / "Vault", "worktree", "add", "-q", str(home / "wt"))
    _denied(_run(home, "git -C ~/wt/action-items show HEAD:./context/x.md"))


def test_the_run_environments_git_dir_counts(home: Path) -> None:
    env = {"GIT_DIR": str(home / "Vault" / ".git"), "GIT_WORK_TREE": str(home / "plain")}
    _denied(_run(home, "git -C ~/plain/action-items show HEAD:./context/x.md", env=env))


def test_a_vault_inside_a_bigger_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    h = (tmp_path / "home").resolve()
    _repo(h, tuple(f"Vault/{rel}" for rel in VAULT_FILES))
    monkeypatch.setenv("SCOUT_DATA_DIR", str(h / "Vault"))
    _denied(_run(h, "git -C action-items show HEAD:./context/x.md"))
    _denied(_run(h, "git -C ~ show HEAD:Vault/action-items/context/x.md"))
    assert _run(h, "git show HEAD:action-items/context/x.md") is not None  # the textual rule's: names the KB
    assert _run(h, "git -C action-items show HEAD:./action-items-2026-11-23.md") is None
    assert _run(h, "git -C ~ show HEAD:./docs/a.md") is None


# ----- allowed -----------------------------------------------------------------------------------------------

ALLOWED = [
    # ordinary git use in a run
    "git status",
    "git status --short",
    "git status -- action-items/context",  # names only
    "git log --oneline -5",
    "git log --oneline -- action-items/context",
    "git log --stat -3",
    "git log -n 3 --format=%s",
    "git diff --stat",
    "git diff --stat HEAD~1",
    "git diff --name-only",
    "git diff --name-status HEAD -- docs",
    "git show --stat HEAD",
    "git show --name-only HEAD",
    "git show -s --format=%H HEAD",
    "git -C action-items log --oneline context/",
    "git -C action-items cat-file -t HEAD:./context/x.md",
    "git -C action-items cat-file -s HEAD:./context/x.md",
    "git rev-parse HEAD",
    "git ls-files docs",
    "git add docs/a.md",
    "git add docs/a.md && git commit -m 'update docs'",
    'git commit -m "update docs" -- docs/a.md',
    "git add -A docs scripts && git commit -q -m x",
    # reads of other files
    "git diff HEAD -- docs/a.md",
    "git log -p -- docs/a.md",
    "git log -p -- '*.py'",
    "git -C docs log -p a.md",
    "git -C docs show HEAD:./a.md",
    "git show HEAD:docs/a.md",
    "git show HEAD:action-items/action-items-2026-11-23.md",
    "git -C action-items show HEAD:./action-items-2026-11-23.md",
    "git -C action-items log -p action-items-2026-11-23.md",
    "git -C action-items grep x -- action-items-2026-11-23.md",
    "git -C action-items grep x -- ':!context'",  # grep's exclusions apply below its folder
    "git grep x -- ':!knowledge-base' ':!knowledge_base' ':!knowledge-base.md' ':!action-items/context'",
    "git -C docs grep x -- ':!x'",
    "git -C docs archive HEAD -- ':(exclude)x' | tar t",
    "git log -p -- docs ':(exclude)docs/x'",
    "git show --stat HEAD -- ':(exclude)docs'",
    "git -C docs grep x HEAD:./",
    "git diff HEAD~1:docs HEAD:docs",
    "git show HEAD:",  # a tree's names
    "git show HEAD:action-items",
    "git grep x docs/",
    "git grep x -- scripts",
    "git -C scripts grep x -- '*.py'",
    "git -C docs grep x",
    "git -C docs archive HEAD | tar t",
    "git -C scripts blame run.py",
    "git -C action-items show HEAD:context/x.md",  # from the top: `context/x.md`, no such file
    "git -C action-items --git-dir=../.git show HEAD:./context/x.md",  # top is action-items: `context/x.md` again
    "export GIT_DIR=$PWD/.git; git -C action-items show HEAD:./context/x.md",  # top is action-items
    # other repositories, and no repository
    "git -C ~/other/action-items show HEAD:./context/x.md",
    "git -C ~/other log -p",
    "git -C ~/plain/action-items show HEAD:./context/x.md",
    # no path: the whole repository, recorded and allowed (DENY_GIT_WHOLE_REPO_READS off)
    "git diff",
    "git diff HEAD~1",
    "git show HEAD",
    "git log -p -3",
    "git stash show -p",
    "git format-patch --stdout -1",
    "git cat-file -p HEAD",
]


@pytest.mark.parametrize("command", ALLOWED)
def test_ordinary_git_use_is_allowed(home: Path, command: str) -> None:
    assert _run(home, command) is None


@pytest.mark.parametrize(
    ("command", "subcommand"),
    [
        ("git diff", "diff"),
        ("git diff HEAD~1", "diff"),
        ("git show HEAD", "show"),
        ("git log -p -3", "log"),
        ("git stash show -p", "stash"),
        ("git format-patch --stdout -1", "format-patch"),
        ("git cat-file -p HEAD", "cat-file"),
        ("git -C action-items diff", "diff"),
        ("git show --stat 0123abcd", "show"),  # an object id may be a blob
        ("git show --stat $(git rev-parse HEAD:docs/a.md)", "show"),  # so may a substituted rev
        ("git request-pull -p HEAD . HEAD", "request-pull"),
        ("git ls-tree -r HEAD | git cat-file --batch", "cat-file"),
    ],
)
def test_a_whole_repository_read_is_recorded_with_its_subcommand(home: Path, command: str, subcommand: str) -> None:
    assert gate.DENY_GIT_WHOLE_REPO_READS is False  # off until the owner decides from the replay's counts
    assert _run(home, command) is None
    (row,) = _events(home)
    assert row["payload"] == {
        "categories": ["git_whole_repo"],
        "mode": RUN["SCOUT_MODE"],
        "git_whole_repo": [subcommand],
    }
    assert "git_whole_repo" in gate.UNRESOLVED_CATEGORIES


@pytest.mark.parametrize(
    "command",
    [
        "git status",
        "git diff --stat",
        "git diff --name-only",
        "git log --oneline",
        "git show --name-status HEAD",
        "git diff HEAD -- docs/a.md",
        "git -C ~/other diff",
    ],
)
def test_names_only_bounded_and_other_repositories_record_nothing(home: Path, command: str) -> None:
    assert _run(home, command) is None
    assert _events(home) == []


def test_with_the_switch_on_a_whole_repository_read_is_denied(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gate, "DENY_GIT_WHOLE_REPO_READS", True)
    _denied(_run(home, "git diff"))
    _denied(_run(home, "git log -p -1"))
    assert _run(home, "git diff --stat") is None
    assert _run(home, "git -C ~/other diff") is None
    assert _events(home) == []


UNRESOLVED_GIT = [
    "git -C $UNSET_X show HEAD:./context/x.md",
    "git -C $(echo docs) show HEAD:./context/x.md",
    "git -C `echo action-items` log -p context/",
    "git --git-dir=$UNSET_X show HEAD:./context/x.md",
    "GIT_WORK_TREE=$UNSET_X git show HEAD:./context/x.md",
    "git -C action-items show HEAD:./$UNSET_X",
    "git -C action-items log -p -- $UNSET_X",
    "git -C action-i* show HEAD:./context/x.md",
    "git ls-tree -r HEAD | xargs -n1 git cat-file -p",  # review of #817: objects from stdin
    "git ls-tree -r HEAD | xargs -n1 git show",
    "git ls-tree -r HEAD | parallel git cat-file -p",
    "git -C /nonexistent show HEAD:./context/x.md",  # review round 2: a folder not on disk
    "cp docs/a.md docs/b.md && git log -p -- docs",  # review round 2: after a copy, names nothing of the KB
    "G=$UNSET_G; $G show HEAD:./x.md",  # review round 2: a program the gate can't know
]


@pytest.mark.parametrize("command", UNRESOLVED_GIT)
def test_a_git_read_the_gate_cant_work_out_is_allowed_and_recorded(home: Path, command: str) -> None:
    assert gate.DENY_UNRESOLVED_GIT_READS is False  # off, like DENY_UNRESOLVED_READS, until the replay's counts
    assert _run(home, command) is None
    (row,) = _events(home)
    assert "git_unresolved" in row["payload"]["categories"]
    for word in ("action", "context", "UNSET", "echo", "HEAD"):
        assert word not in json.dumps(row)


@pytest.mark.parametrize("command", UNRESOLVED_GIT)
def test_with_the_switch_on_a_git_read_the_gate_cant_work_out_is_denied(
    home: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    monkeypatch.setattr(gate, "DENY_UNRESOLVED_GIT_READS", True)
    monkeypatch.setattr(gate, "DENY_UNRESOLVED_READS", True)  # `$(…)` inside -C is a plain unresolved word too
    _denied(_run(home, command))


@pytest.mark.parametrize("command", ["git -C $UNSET_X status", "git -C $UNSET_X diff --stat", "git -C $X log -3"])
def test_an_unresolved_folder_for_a_names_only_call_is_nothing(home: Path, command: str) -> None:
    assert _run(home, command) is None
    assert _events(home) == []


def test_outside_a_run_git_reads_are_not_judged(home: Path) -> None:
    out = gate.run(stdin=_payload("Bash", {"command": MUST_DENY[0]}, home / "Vault"), env={"HOME": str(home)})
    assert out is None


# ----- the parser ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("args", "trees", "pathspecs", "maybe", "unbounded"),
    [
        ("-C a show HEAD:./x", ["./x"], [], ["HEAD:./x"], "repo"),
        ("show HEAD@{10:00}:x.md", ["x.md"], [], ["HEAD@{10:00}:x.md"], "repo"),
        ("diff HEAD -- c/x", [], ["c/x"], ["HEAD"], "repo"),
        ("log -m -p c/", [], [], ["c/"], "repo"),
        ("log -n 3 -p c/", [], [], ["c/"], "repo"),
        ("log -L 1,5:c/x", [], ["c/x"], [], "repo"),
        ("log -L :fn:c/x", [], ["c/x"], [], "repo"),
        ("grep -n x c/", [], [], ["c/"], "cwd"),
        ("grep -C 2 x c/", [], [], ["c/"], "cwd"),
        ("grep -e x -e y c/", [], [], ["c/"], "cwd"),
        ("blame -L 1,5 c/x", [], [], ["c/x"], "repo"),
        ("show --stat HEAD:x", ["x"], [], [], None),
        ("cat-file -p :0:x", ["x"], [], [], None),
    ],
)
def test_parse_sorts_a_calls_words(args: str, trees: list, pathspecs: list, maybe: list, unbounded: str | None) -> None:
    read = git_read.parse(args.split())
    assert read is not None
    assert (read.trees, read.pathspecs, read.maybe_paths, read.unbounded) == (trees, pathspecs, maybe, unbounded)


@pytest.mark.parametrize(
    "args",
    [
        "status", "status -s", "log --oneline", "log --stat", "diff --stat", "diff --name-only HEAD",
        "show --name-status HEAD", "show -s HEAD", "cat-file -t HEAD", "cat-file -e HEAD:x", "cat-file commit HEAD",
        "add x", "commit -m x", "stash", "stash show", "stash list", "ls-files", "ls-tree HEAD", "rev-parse HEAD",
        "push", "fetch", "-C a status", "request-pull HEAD . HEAD", "merge-file a b c",
    ],
)  # fmt: skip
def test_parse_finds_no_read_in_a_names_only_call(args: str) -> None:
    assert git_read.parse(args.split()) is None


@pytest.mark.parametrize(
    ("word", "prefix", "pathspec", "tree"),
    [
        ("./x.md", "action-items/context", False, "action-items/context/x.md"),
        ("../x.md", "action-items/context", False, "action-items/x.md"),
        ("x.md", "action-items", False, "x.md"),
        ("/x.md", "action-items", False, "x.md"),
        ("../../x", "a", False, None),
        ("x.md", "action-items", True, "action-items/x.md"),
        (":/x.md", "action-items", True, "x.md"),
        (":(top,icase)x.md", "a", True, "x.md"),
        (":(icase)x.md", "a", True, "a/x.md"),
        (":!x.md", "a", True, None),
        (":(exclude)x", "a", True, None),
        (".", "", True, ""),
        (":x.md", "a", True, "a/x.md"),
    ],
)
def test_tree_path(word: str, prefix: str, pathspec: bool, tree: str | None) -> None:
    assert git_read.tree_path(word, prefix, pathspec=pathspec) == tree


@pytest.mark.parametrize(
    ("args", "pathspecs", "files", "unbounded"),
    [
        ("--no-pager --work-tree w -C a show :(top)x", [":(top)x"], [], "repo"),
        ("log -L1,5:c/x", ["c/x"], [], "repo"),
        ("blame --contents=f x", [], ["f"], "repo"),
        ("unpack-file abc123", [], [], "repo"),
    ],
)
def test_parse_more_forms(args: str, pathspecs: list, files: list, unbounded: str) -> None:
    read = git_read.parse(args.split())
    assert read is not None
    assert (read.trees, read.pathspecs, read.files, read.unbounded) == ([], pathspecs, files, unbounded)


def test_split_global_reads_the_options_before_the_subcommand() -> None:
    assert git_read.split_global(["--work-tree", "w", "-C", "a"]) == (["a"], None, "w", 4)
    assert git_read.parse(["-C", "a"]) is None  # no subcommand


def test_find_repo_reads_a_dot_git_file(tmp_path: Path) -> None:
    (tmp_path / "junk").mkdir()
    (tmp_path / "junk" / ".git").write_text("not a gitdir line\n")
    assert git_read.find_repo(tmp_path / "junk", None, None) is None
    (tmp_path / "locked").mkdir()
    dot = tmp_path / "locked" / ".git"
    dot.write_text("gitdir: elsewhere\n")
    dot.chmod(0)
    try:
        if not os.access(dot, os.R_OK):  # root reads it anyway
            assert git_read.find_repo(tmp_path / "locked", None, None) is None
    finally:
        dot.chmod(0o644)


@pytest.mark.parametrize(
    ("word", "prefix", "left"),
    [
        (":!docs", "a", "a/docs"),
        (":^/docs", "a", "docs"),
        (":(exclude)docs", "a", "a/docs"),
        (":(exclude,top)docs", "a", "docs"),
        (":(icase)docs", "a", None),  # not an exclusion
        ("docs", "a", None),
    ],
)
def test_exclusion(word: str, prefix: str, left: str | None) -> None:
    assert git_read.exclusion(word, prefix) == left


def test_rel_to_a_folder_outside_the_top(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    assert git_read.rel_to(tmp_path / "a", tmp_path / "b") is None
    assert git_read.rel_to(tmp_path, tmp_path / "a") == "a"
