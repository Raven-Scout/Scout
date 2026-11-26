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


GIT_FORMS_MESSAGE = "uses git only in a few read forms"


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
    # review round 3: options by any prefix of their name, and short flags in a cluster
    "git rebase -q --exe 'cat act*/cont*/x.md' HEAD",
    "git rebase -q --ex 'cat act*/cont*/x.md' HEAD",
    "git rebase -q --exe=$'cat act*/cont*/x.md' HEAD",
    "git rebase -q -qx 'cat act*/cont*/x.md' HEAD",
    "GIT_SEQUENCE_EDITOR=true git rebase -q -ix 'cat act*/cont*/x.md' HEAD",
    "git rebase -q -kix 'cat act*/cont*/x.md' HEAD",
    "git rebase -q -xcat\\ act\\*/cont\\*/x.md HEAD",
    "git difftool -y --extc='sh -c \"cat act*/cont*/x.md\"' HEAD -- docs",
    # review round 3: transports run a command to reach the other side
    "git ls-remote --upload-pack='sh -c \"cat act*/cont*/x.md >&2\"; false' .",
    "git ls-remote --upload-pack 'sh -c \"cat act*/cont*/x.md >&2\"; false' .",
    "git -C . ls-remote -u 'sh -c \"cat act*/cont*/x.md >&2\"; false' .",
    "git clone -q -u 'sh -c \"cat act*/cont*/x.md >&2\"; false' . ~/c",
    "git clone -q --upload-pack='sh -c \"cat act*/cont*/x.md >&2\"; false' . ~/c",
    "git fetch -q --upload-pack='sh -c \"cat act*/cont*/x.md >&2\"; false' .",
    "git pull -q --upl='sh -c \"cat act*/cont*/x.md >&2\"; false' . main",
    "git push -q --receive-pack='sh -c \"cat act*/cont*/x.md >&2\"; false' ~/b.git HEAD",
    "git push -q --exec='sh -c \"cat act*/cont*/x.md >&2\"; false' ~/b.git HEAD",
    "GIT_SSH_COMMAND='cat act*/cont*/x.md' git fetch -q origin",
    "GIT_PROXY_COMMAND='cat act*/cont*/x.md' git fetch -q git://example.com/x",
    "GIT_EXTERNAL_DIFF='sh -c \"cat act*/cont*/x.md\"' git diff HEAD -- docs",
    # review round 3: a nested shell inside a command git runs, judged as the same shell run directly
    "git rebase -q --exec 'zsh -fc \"cat act*/cont*/x.md\"' HEAD",
    "git bisect run zsh -fc 'cat act*/cont*/x.md; exit 0'",
    "git rebase -q --exec 'sh -c \"cat \\$0/x.md\" action-items/context' HEAD",
    "git rebase -q -x 'git -C action-items show HEAD:./context/x.md' HEAD",
    # review round 4: a short option that takes a value ends a cluster (`-Xx` is -X with value x)
    "git rebase -q -Xx -x 'cat act*/cont*/x.md' HEAD",
    "git rebase -q -sx -x 'cat act*/cont*/x.md' HEAD",
    "git rebase -q --strategy-option -x 'cat act*/cont*/x.md' HEAD",
    "git clone -q -bx -u 'sh -c \"cat act*/cont*/x.md >&2\"; false' . ~/c",
    # review round 5: git hands `--` to an option that takes a value and goes on
    "git rebase -q -s -- -x 'cat act*/cont*/x.md' HEAD",
    "git rebase -q -X -- -x 'cat act*/cont*/x.md' HEAD",
    "git rebase -q --strategy -- -x 'cat act*/cont*/x.md' HEAD",
    "git rebase -q -x 'cat act*/cont*/x.md' -- HEAD",
    "git difftool -y -t -- -x 'sh -c \"cat act*/cont*/x.md >&2\"' HEAD -- docs/a.md",
    "git ls-remote -o -- --upload-pack='sh -c \"cat act*/cont*/x.md >&2\"; false' .",
    "git ls-remote --sort -- --upload-pack='sh -c \"cat act*/cont*/x.md >&2\"; false' .",
    "git ls-remote --upload-pack -- --upload-pack='sh -c \"cat act*/cont*/x.md >&2\"; false' .",
    "git clone -q -b -- -u 'sh -c \"cat act*/cont*/x.md >&2\"; false' . ~/c",
    "git fetch -q -o -- --upload-pack='sh -c \"cat act*/cont*/x.md >&2\"; false' .",
    "git pull -q -o -- --upload-pack='sh -c \"cat act*/cont*/x.md >&2\"; false' .",
    "git push -q -o -- --receive-pack='sh -c \"cat act*/cont*/x.md >&2\"; false' ~/b.git HEAD",
    # review round 5: grep's pager option runs a command
    "git grep -O'sh -c \"cat act*/cont*/x.md >&2\"' DOCS -- docs",
    "git grep --open-files-in-pager='sh -c \"cat act*/cont*/x.md >&2\"' DOCS -- docs",
    "git grep -qO'sh -c \"cat act*/cont*/x.md >&2\"' DOCS -- docs",
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
    # review round 3: a link to the vault spelled from the link's own folder, and the other copies of its repository
    "cd ~ && ln -s Vault V2 && git -C V2 grep x",
    "cd ~ && ln -s Vault V2 && cd V2 && git grep x",
    "cd ~ && ln -s Vault V2 && git --git-dir=V2/.git grep x HEAD",
    "cd ~ && ln -s Vault/.git G2 && git --git-dir=G2 grep x HEAD",
    "ln -s . self && git -C self grep x",
    "ln -s ../home/Vault ~/up && git -C ~/up grep x",
    # review round 4: a link whose target is a glob, or can't be worked out, may be the vault
    "cd ~/plain && ln -s ~/V*t && git -C Vault grep x",
    "cd ~/plain && ln -s $(echo ~/Vault) v && git -C v grep x",  # relative to the link's folder (~)
    "git bundle create ~/b HEAD && git clone -q ~/b ~/cl && git -C ~/cl grep x",
    "git init -q --bare ~/bare.git && git push -q ~/bare.git HEAD:main && git --git-dir=$HOME/bare.git grep x main",
]


# Denied, though not by the KB rule's own message or not as a read git really makes: a filter-branch is a commit
# written without the hook; git itself fails on a git folder it can't find, but the gate can't tell one from a
# folder made earlier, and these name action-items/; a folder from a substitution in a command that names it.
STRICT_DENIES = [
    "git filter-branch -f --tree-filter 'cat act*/cont*/x.md' HEAD",
    "git filter-branch -f -d -- --tree-filter 'cat act*/cont*/x.md' HEAD",
    # review round 3: programs git runs that the gate can't read, and the switches that let a URL run one
    "git clone -q --template=$HOME/t . ~/c",
    "git clone -q --templ=$HOME/t . ~/c",
    "git init -q --template=$HOME/t ~/r",
    "git ls-remote 'ext::sh -c cat% act*/cont*/x.md'",
    "GIT_ALLOW_PROTOCOL=ext git ls-remote 'ext::sh -c cat% act*/cont*/x.md'",
    "git -c protocol.ext.allow=always ls-remote origin",
    "git -c protocol.allow=always fetch origin",
    "git -c core.gitProxy='cat act*/cont*/x.md' fetch git://example.com/x",
    "git config core.gitProxy 'cat act*/cont*/x.md'",
    # review round 4: config keys that make git run a command, by their name's ending
    "git config remote.x.uploadpack 'sh -c \"cat act*/cont*/x.md >&2\"; false' && git fetch x",
    "git config remote.x.receivepack 'sh -c \"cat act*/cont*/x.md >&2\"; false' && git push x HEAD",
    "git -c remote.origin.uploadpack='cat act*/cont*/x.md' fetch origin",
    "git config sendemail.tocmd 'cat act*/cont*/x.md'",
    "git config gpg.ssh.defaultKeyCommand 'cat act*/cont*/x.md'",
    "git config gc.recentObjectsHook 'cat act*/cont*/x.md'",
    "git config interactive.diffFilter 'cat act*/cont*/x.md'",
    "git config web.browser 'cat act*/cont*/x.md'",
    "git config mergetool.x.path ~/s",
    "GIT_SSH=$HOME/s git fetch -q origin",
    "export GIT_SSH=$HOME/s; git fetch -q origin",
    "GIT_ASKPASS=$HOME/s git fetch -q https://example.com/x",
    # review round 5: config keys off the allowlist, a section renamed into another, an unknown subcommand
    "git config submodule.s.update '!sh -c \"cat act*/cont*/x.md >&2\"'",
    "git config imap.tunnel 'sh -c \"cat act*/cont*/x.md >&2\"'",
    "git config instaweb.httpd 'sh -c x'",
    "git config sendemail.smtpServer /bin/sh",
    "git config remote.x.vcs foo",
    "git config help.autocorrect immediate",
    "git config core.worktree /tmp",
    "git config safe.directory '*'",
    "git config url.ext::sh.insteadOf zz:",
    "git config Remote.X.UploadPack 'cat act*/cont*/x.md'",
    "git config remote.a.b.uploadpack 'cat act*/cont*/x.md'",
    "git config --type=path remote.x.uploadpack 'cat act*/cont*/x.md'",
    "git config --comment hi remote.x.uploadpack 'cat act*/cont*/x.md'",
    "git config set --append remote.x.uploadpack 'cat act*/cont*/x.md'",
    "git config --global remote.x.uploadpack 'cat act*/cont*/x.md'",
    "git config -f .git/config user.name A",
    "git config --global user.name A",  # the user's own ~/.gitconfig: never written by a run, even a plain key
    "git config --global color.ui auto",
    "git config --system user.name A",
    "git config --worktree user.name A",
    "git config --file=~/x user.name A",
    "git config --rename-section foo diff",
    "git config rename-section foo core",
    "git config --rename-section foo.x remote.x",
    "git config --remove-section core",
    "git -c help.autocorrect=immediate grpe x",
    "git -c submodule.s.update='!cat x' submodule update",
    "git -c imap.tunnel='cat x' imap-send",
    "git --config-env=core.sshCommand=V ls-remote ssh://x/y",
    "git grpe x",
    "git shwo HEAD:action-items/context/x.md",
    "git ci -m x",
    "git send-email --to=a@example.com ~/p",
    "git instaweb --start",
    "git imap-send",
    "git daemon --base-path=.",
    "git --git-dir=.git -C action-items show HEAD:./context/x.md",
    "GIT_DIR=.git GIT_WORK_TREE=. git -C action-items show HEAD:./context/x.md",
    "git -C $(echo action-items) show HEAD:./context/x.md",
]


# Shell commands, read KB notes or not: each run through `git rebase -x` must be judged as it is run directly.
EMBEDDED_CORPUS = [
    "cat knowledge-base/a.md",
    "cat k*/a.md",
    "head -n 3 action-items/context/x.md",
    "cd action-items && cat context/x.md",
    "zsh -fc 'cat act*/cont*/x.md'",
    "sh -c 'cat $0/x.md' action-items/context",
    "bash -ec 'cat act*/cont*/x.md'",
    "grep -r x .",
    "rg x",
    "f=knowledge-base/a.md; wc -c $f",
    "echo knowledge-base/a.md | xargs cat",
    "git -C action-items show HEAD:./context/x.md",
    "git log -p -- action-items",
    "cat docs/a.md",
    "ls docs",
    "wc -l scripts/run.py",
    "echo done",
    "git status",
    "git log --oneline -3",
    "git diff --stat",
    "date +%F",
]


@pytest.mark.parametrize("command", EMBEDDED_CORPUS)
def test_a_command_git_would_run_is_refused_with_the_call(home: Path, command: str) -> None:
    """Round 3 judged the commands git runs (rebase -x, bisect run, --upload-pack) as the same commands run
    directly; round 6 refuses the subcommands and options that run them, whatever the command."""
    import shlex

    for wrapped in (
        f"git rebase -q -x {shlex.quote(command)} HEAD",
        f"git rebase -q --exe={shlex.quote(command)} HEAD",
        f"git bisect run sh -c {shlex.quote(command)}",
        f"git ls-remote --upload-pack={shlex.quote(command)} .",
    ):
        _denied(_run(home, wrapped))


@pytest.mark.parametrize("command", STRICT_DENIES)
def test_strict_denies(home: Path, command: str) -> None:
    _denied(_run(home, command))


def test_a_repository_whose_work_tree_is_the_vault_holds_the_kb(home: Path) -> None:
    """Review of #817, round 5: another repository with `core.worktree` pointing at the vault (set up by an earlier
    call) indexes the vault's files at the vault's own places."""
    r = home / "r"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "core.worktree", str(home / "Vault"))
    _git(r, "add", "-A")
    for command in (
        "git -C ~/r grep --cached x",
        "git -C ~/r show :action-items/context/x.md",
        "git -C ~/r grep --untracked x",
        "git -C ~/r log -p -- action-items",
    ):
        _denied(_run(home, command))
    assert _run(home, "git -C ~/r show :docs/a.md") is None
    # And one whose work tree is a folder inside the vault: its tree paths are below that folder.
    s = home / "s"
    s.mkdir()
    _git(s, "init", "-q")
    _git(s, "config", "core.worktree", str(home / "Vault" / "action-items"))
    _denied(_run(home, "git -C ~/s show :context/x.md"))
    _denied(_run(home, "git -C ~/s grep x"))


def test_a_repository_whose_config_includes_another_file_is_unresolved(home: Path) -> None:
    r = home / "r2"
    r.mkdir()
    _git(r, "init", "-q")
    (r / ".git" / "config").write_text((r / ".git" / "config").read_text() + "[include]\n\tpath = ~/elsewhere\n")
    assert _run(home, "git -C ~/r2 show HEAD:./context/x.md") is None
    assert "git_unresolved" in _events(home)[0]["payload"]["categories"]


@pytest.mark.parametrize("command", AFTER_FOLDER_CHANGES)
def test_a_git_read_after_the_command_changes_folders_is_denied(home: Path, command: str) -> None:
    _denied(_run(home, command))


@pytest.mark.parametrize("command", MUST_DENY)
def test_a_git_read_of_a_kb_note_is_denied(home: Path, command: str) -> None:
    reason = _denied(_run(home, command))
    assert "knowledge-base/" in reason or GIT_FORMS_MESSAGE in reason  # a KB read, or a form off the allowlist


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
    "git rev-parse HEAD",
    "git ls-files docs",
    "git add docs/a.md",
    # review round 5: config writes on the allowlist, in the repository or the user's own config
    "git config user.name A",
    "git config user.email a@example.com",
    "git config --get remote.origin.url",
    "git config --list",
    "git config --get-regexp '^remote\\.'",
    # reads of other files
    "git diff HEAD -- docs/a.md",
    "git -C docs show HEAD:./a.md",
    "git show HEAD:docs/a.md",
    "git show HEAD:action-items/action-items-2026-11-23.md",
    "git -C action-items show HEAD:./action-items-2026-11-23.md",
    "git show --stat HEAD -- ':(exclude)docs'",
    "git diff HEAD~1:docs HEAD:docs",
    "git show HEAD:",  # a tree's names
    "git show HEAD:action-items",
    "git -C action-items show HEAD:context/x.md",  # from the top: `context/x.md`, no such file
    # other repositories, and no repository
    "git -C ~/plain/action-items show HEAD:./context/x.md",
    # no path: the whole repository, recorded and allowed (DENY_GIT_WHOLE_REPO_READS off)
    "git diff",
    "git diff HEAD~1",
    "git show HEAD",
]


# Forms the gate allowed or recorded before round 6 of #817's review, refused now: a run's git is an allowlist
# (scout.kb.git_allow). Commits go through scripts/git-safe-commit.sh; KB notes are read with scoutctl kb.
REFUSED_IN_A_RUN = [
    "git -C action-items cat-file -t HEAD:./context/x.md",
    "git -C action-items cat-file -s HEAD:./context/x.md",
    "git config set user.name A",
    "git config --local user.name A",
    "git config --unset user.name",
    "git config pull.rebase true",
    "git config branch.main.remote origin",
    "git config push.default simple",
    "git config fetch.prune true",
    "git config rebase.autoStash true",
    "git config merge.ff only",
    "git config init.defaultBranch main",
    "git config commit.gpgsign false",
    "git config core.autocrlf false",
    "git config status.showUntrackedFiles no",
    "git config color.diff.meta 'yellow bold'",
    "git config credential.username x",
    "git config log.date iso",
    "git config remote.origin.prune true",
    "git config remote.origin.fetch '+refs/heads/*:refs/remotes/origin/*'",
    "git config branch.autoSetupRebase always",
    "git config --rename-section color.x color.y",
    "git -c color.ui=always log --oneline -1 -- docs",
    "git -c core.quotepath=off status --short",
    "git -c advice.detachedHead=false log -1 --oneline -- docs",
    "git push -q origin main",
    "git log -p -- docs/a.md",
    "git log -p -- '*.py'",
    "git -C docs log -p a.md",
    "git -C action-items log -p action-items-2026-11-23.md",
    "git -C action-items grep x -- action-items-2026-11-23.md",
    "git -C action-items grep x -- ':!context'",
    "git grep x -- ':!knowledge-base' ':!knowledge_base' ':!knowledge-base.md' ':!action-items/context'",
    "git -C docs grep x -- ':!x'",
    "git -C docs archive HEAD -- ':(exclude)x' > /dev/null",
    "git log -p -- docs ':(exclude)docs/x'",
    "git -C docs grep x HEAD:./",
    "git grep x docs/",
    "git grep x -- scripts",
    "git -C scripts grep x -- '*.py'",
    "git -C docs grep x",
    "git -C docs archive HEAD > /dev/null",
    "git -C scripts blame run.py",
    "git -C action-items --git-dir=../.git show HEAD:./context/x.md",
    "export GIT_DIR=$PWD/.git; git -C action-items show HEAD:./context/x.md",
    "git -C ~/other/action-items show HEAD:./context/x.md",
    "git -C ~/other log -p",
    "git log -p -3",
    "git stash show -p",
    "git format-patch --stdout -1",
    "git cat-file -p HEAD",
    "git show --stat $(git rev-parse HEAD:docs/a.md)",
    "git request-pull -p HEAD . HEAD",
    "git ls-tree -r HEAD | git cat-file --batch",
    "git -C `echo action-items` log -p context/",
    "git --git-dir=$UNSET_X show HEAD:./context/x.md",
    "GIT_WORK_TREE=$UNSET_X git show HEAD:./context/x.md",
    "git -C action-items log -p -- $UNSET_X",
    "git ls-tree -r HEAD | xargs -n1 git cat-file -p",
    "git ls-tree -r HEAD | xargs -n1 git show",
    "git ls-tree -r HEAD | parallel git cat-file -p",
    "cp docs/a.md docs/b.md && git log -p -- docs",
    "G=$UNSET_G; $G show HEAD:./x.md",
]


@pytest.mark.parametrize("command", ALLOWED)
def test_ordinary_git_use_is_allowed(home: Path, command: str) -> None:
    assert _run(home, command) is None


@pytest.mark.parametrize("command", REFUSED_IN_A_RUN)
def test_a_form_off_the_allowlist_is_refused(home: Path, command: str) -> None:
    _denied(_run(home, command))


# ----- round 6: the allowlist ----------------------------------------------------------------------------------

# Every git form 50 scheduled runs used (log 80, config 22 reads, show 17, status 16, diff 7, add 5, remote 1), with
# the options they used, and the forms the phases tell a run to use (git-setup's Step 0 and restore, the count
# guard, claude-sessions' repo checks). Pinned: a change to git_allow that refuses one is a new false deny.
RUN_USAGE = [
    "git log --oneline -10",
    "git log --since='2026-11-24 06:00' --oneline",
    "git log --since=\"2 days ago\" --date=short --format='%h %ad %s'",
    "git log --pretty=format:'%h %s' --no-merges -n 20",
    "git log --grep=fix -E --name-only -5",
    "git log --all --diff-filter=A --name-status --stat",
    "git log -n 5 --stat -- docs",
    "git log -1 --format=%H",
    "git log --format='%h %an %s' --author=alex --reverse --first-parent --decorate --graph",
    "git log --max-count=3 --shortstat",
    "git log --until=yesterday --oneline",
    "git -C docs log --oneline -3",
    "cd docs && git status --porcelain && git log --since='2026-11-24' --oneline",
    "git -C ~/other log --since='2026-11-24' --oneline --shortstat",
    "git show --stat HEAD",
    "git show --format=%s --stat HEAD~1",
    "git show --name-only --oneline HEAD",
    "git show -s --format=%H HEAD",
    "git show --no-patch --pretty=%B HEAD",
    "git show HEAD:docs/a.md",
    "git show HEAD:docs/a.md > docs/a.md",  # git-setup: restore a file
    "git -C ~/Vault show HEAD:action-items/action-items-2026-11-23.md",  # the count guard's form
    "git status",
    "git status --short",
    "git status -sb",
    "git status --porcelain=v2 --branch --untracked-files=no",
    "git diff --stat",
    "git diff --cached --name-only",
    "git diff --stat HEAD~1 -- docs",
    "git diff --numstat --shortstat",
    "git diff --check -- docs",
    "git diff -- docs/a.md",
    "git add docs/a.md",
    "git add -A docs",
    "git add -u",
    "git add -- docs/a.md",
    "git config user.name",
    "git config --get user.email",
    "git config --get-regexp '^remote\\.'",
    "git config --list",
    "git config -l --show-origin",
    "git config get user.name",
    "git config list",
    "git config --global --get user.name",  # a read may name its scope; a write may not (ROUND_6)
    "git config --system --list",
    'git config user.email "alex@example.com"',  # Step 0, outside the write sandbox
    'git config user.name "Scout Bot"',
    "git remote -v",
    "git remote",
    "git remote get-url origin",
    "git remote show -n origin",
    "git rev-parse --show-toplevel",
    "git rev-parse --abbrev-ref HEAD",
    "git rev-parse --short HEAD",
    "git rev-parse HEAD",
    "git ls-files docs",
    "git ls-files -co --exclude-standard",
    "git branch",
    "git branch -a -v",
    "git branch --show-current",
    "git branch --list 'scout/*'",
    "git branch --contains HEAD",
    "git tag",
    "git tag -l 'v*'",
    "git tag --sort=-creatordate -n3",
    "git describe --tags --always",
    "git merge-base --is-ancestor HEAD~1 HEAD",
    "git merge-base HEAD main",
    "git rev-list --count HEAD",
    "git rev-list --count --since='1 week ago' HEAD",
    "git shortlog -sn --since='1 month ago' HEAD",
    "git shortlog -s -n HEAD",
    "scripts/git-safe-commit.sh 'update docs' docs/a.md",
    "GIT log --oneline",
    "/usr/bin/git log --oneline",
    'F=docs/a.md; git log --oneline -- "$F"',
    'N=5; git log -n "$N"',
    'git log --since="$(date +%F)" --oneline',
    "G=git; $G log --oneline",
    "git --no-pager log --oneline -3",
    "git --version",
    "git -C ~/Vault/docs config user.name 'Scout Bot'",  # still the vault's repository
    # round 7: commits in the vault's repository, pickaxe searches, a prelude that prepends to PATH
    "git add docs/a.md && git commit -m 'update docs'",
    'git commit -m "update docs" -- docs/a.md',
    "git add -A docs scripts && git commit -q -m x",
    "git commit -qam 'kb: update'",
    "git commit -m 'kb: a' -m 'the body'",
    "git commit -q --allow-empty -m 'kb: checkpoint'",
    "git commit -q -F - <<'EOF'\nkb: fix PATH and GIT_DIR handling\nEOF",
    "git commit -m 'fix the path check for GIT_DIR' -- docs/a.md",
    "git -C docs commit -q -m x",
    "git log -S widget --oneline",
    "git log -Gwidget -i --oneline -- docs",
    "git log --pickaxe-all -S x --stat",
    "git log -i --grep=fix -F --all-match --oneline",
    "git log --regexp-ignore-case --fixed-strings --grep=x --oneline",
    'export PATH="$HOME/.local/bin:$PATH"; git log --oneline',
    "PATH=/usr/bin:$PATH git status",
    "path=(~/.local/bin $path); git status",
    "PATH+=:/tmp/x; git status",
    "echo $PATH; git status",
    # replay at 66b7b9a8: commit --only/--include, and words that only look like PATH's
    "cd ~/Vault; git commit -q --only -m 'kb: one\n\nand the body' -- docs/a.md; git log --oneline -1;"
    " git status --short",
    "git commit -q -o -m x docs/a.md",
    "git commit --include -m x -- docs/a.md",
    "git commit -qom x -- docs/a.md",
    "git status --short | grep -v '^??' | grep -E 'action-items/.*\\.md'",
    "git status --short | grep -E '^ M .*knowledge-base'",
    "git status --short | grep path | grep -E 'action-items/.*\\.md' | head -5",
    "git status --short | grep 'path' | grep -c 'PATH'",
    "git log --oneline -- path",
]


@pytest.mark.parametrize("command", RUN_USAGE)
def test_the_git_runs_use_is_allowed(home: Path, command: str) -> None:
    assert _run(home, command) is None


# Round 6 of the review: forms the denylists missed, all off the allowlist. `~/out` is a folder of the run's.
ROUND_6 = [
    # config writes read as reads, and option prefixes or glued values
    "git config core.sshCommand 'sh -c \"cat act*/cont*/x.md >&2\"' get && git ls-remote ssh://x/y",
    "git config core.sshCommand 'sh -c \"cat act*/cont*/x.md >&2\"' list && git ls-remote ssh://x/y",
    "git config --comment get core.sshCommand x && git ls-remote ssh://x/y",
    "git config set --comment get core.sshCommand x && git ls-remote ssh://x/y",
    "git config --replace-all core.sshCommand x get && git ls-remote ssh://x/y",
    "git config --add core.fsmonitor 'sh -c \"cat act*/cont*/x.md >&2\"; false' get && git status --short",
    "git config --comm user.name core.sshCommand x && git ls-remote ssh://x/y",
    "git config set --value user.name core.sshCommand x && git ls-remote ssh://x/y",
    "git config set --val user.name core.sshCommand x && git ls-remote ssh://x/y",
    "git config --ty bool --add core.fsmonitor x && git status --short",
    "git config --type=bool --add core.fsmonitor x",
    "git config --null core.sshCommand x",
    "git config -z core.sshCommand x",
    "git config -- core.sshCommand x",
    "git config set -- core.sshCommand x",
    "git config --show-origin core.sshCommand x",
    "git config --fixed-value --replace-all core.sshCommand x y",
    "GIT_EDITOR='sh -c \"cat act*/cont*/x.md >&2\"' git config -e",
    "EDITOR=x git config --edit",
    "VISUAL=x git config edit",
    "git config edit",
    'e=x; export GIT_EDITOR="$e"; git config -e',
    "read -r GIT_EDITOR <<< x; export GIT_EDITOR; git config -e",
    "git config --get-urlmatch http https://x",
    "git config unset user.name",
    "git config --unset user.name",
    "git config --glob user.name A",
    "git config --glo user.name A",
    "git config --sys user.name A",
    "git config -f~/out/x user.name A",
    "git config --fil=~/out/x user.name A",
    "git config --fi ~/out/x user.name A",
    "git config --work user.name A",
    "git config -f$HOME/.zshenv user.name '$(cat ~/Vault/act*/cont*/x.md >&2)'",
    "git config --file=$HOME/.bash_profile user.name x",
    "git config --global user.name x",
    "git -C ~/out config --glob user.email x",
    "git -c \"user.name=a\n[core]\n\tsshCommand = sh -c 'cat act*/cont*/x.md >&2'\" ls-remote ssh://x/y",
    "git config user.name \"a\n[core]\n\tsshCommand = sh -c 'cat act*/cont*/x.md >&2'\"",
    "git config pull.rebase interactive",
    "git config core.excludesFile action-items/context/x.md && git check-ignore -v --no-index x",
    "git -c core.excludesFile=action-items/context/x.md check-ignore -v --no-index x",
    "git -c remote.x.url='ext::sh -c cat% act*/cont*/x.md% >&2' ls-remote x",
    "git -c format.pretty='%H %(describe)' log -1",
    "git -c rerere.enabled=true rerere diff",
    # clone -c plants config
    "git clone -q -c core.fsmonitor='sh -c \"cat ~/V*t/act*/cont*/x.md >&2\"; false' ~/other ~/out/c",
    "git clone -q --config=core.sshCommand=x ~/other ~/out/c",
    "git -c user.name=x clone -q -c core.fsmonitor=y . ~/out/c",
    # subcommands the pre-filter's word class skipped (autocorrect), aliases, lfs, help pagers
    "git _rep x",
    "git +rep x",
    "git .rep x",
    "git grpe x",
    "git _how HEAD:action-items/context/x.md",
    "git gui",
    "git citool",
    "git ci",
    "git lfs env",
    "git lfs pointer --file=action-items/context/x.md",
    "git lfs ls-files",
    "MANPAGER='sh -c \"cat act*/cont*/x.md >&2\"' git help log",
    "PAGER=x git log --help",
    "git help -a",
    "git log --help",
    "GIT_PAGER=x git -p log -1 -- docs",
    "git --paginate log -1 -- docs",
    # vault copies into other repositories, and object stores
    "git -C ~/out/r --work-tree=$HOME/Vault add -A",
    "git --git-dir=~/out/r/.git add -A",
    "GIT_DIR=~/out/r/.git git add -A",
    "git -C ~/out/r fetch -q ~/Vault main",
    "git -C ~/out/r remote add v ~/Vault && git -C ~/out/r fetch -q v",
    "git remote add o ~/out/b.git && git push -q o HEAD:refs/heads/main",
    "git config remote.o.url ~/out/b.git && git push -q o HEAD:refs/heads/main",
    "GIT_COMMON_DIR=$HOME/Vault/.git git -C ~/other show main:action-items/context/x.md",
    "GIT_OBJECT_DIRECTORY=$HOME/Vault/.git/objects git -C ~/other cat-file -p 0123abcd",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES=$HOME/Vault/.git/objects git -C ~/other show 0123abcd",
    "git init -q ~/out/r",
    "git -C ~/other remote add x 'ext::sh -c x'",
    # the program's environment: set by read or print -v, exec-path, PATH
    "read -r GIT_SSH_COMMAND <<< 'sh -c \"cat act*/cont*/x.md >&2\"'; export GIT_SSH_COMMAND; git ls-remote ssh://x/y",
    "print -v GIT_SSH_COMMAND x; export GIT_SSH_COMMAND; git ls-remote ssh://x/y",
    "git --exec-path=~/out/bin ls-remote .",
    "GIT_EXEC_PATH=~/out/bin git ls-remote .",
    "export GIT_EXEC_PATH=~/out/bin; git ls-remote .",
    "export PATH=$UNSET_X:$PATH; git log --oneline",
    "read -r PATH <<< /tmp; git log --oneline",
    "export PATH; git log --oneline",
    "for path in /tmp; do git status; done",
    "typeset -U path; git status",
    "PATH=/tmp/no-git-here git log --oneline",
    "functions[git]='cat x'; git status",
    "git() { cat x; }; git status",
    # words the shell fills in or splits before git sees them
    "X=-p; git log $X -- docs",
    "A=(-p docs); git log $A",
    'git log "$(echo -p)"',
    "git log *",
    "git log {-p,docs}",
    "echo -p | xargs git log",
    "$UNSET_G log --oneline",
]


@pytest.mark.parametrize("command", ROUND_6)
def test_round_6_forms_are_refused(home: Path, command: str) -> None:
    _denied(_run(home, command))


def test_the_allowlist_refuses_every_other_subcommand_and_option() -> None:
    """Completeness: any subcommand git knows but the allowlist doesn't list, any option a listed subcommand
    doesn't list (or a prefix of one it does), and any short letter it doesn't list is refused."""
    import string

    from scout.kb import git_allow, live_run

    def judge(sub: str, *args: str) -> str | None:
        return git_allow.judge(sub, list(args), dynamic=lambda w: False, expand=lambda w: [w], plain=lambda w: w)

    for sub in sorted(live_run._GIT_KNOWN_SUBCOMMANDS - set(git_allow.ALLOWED)):
        assert judge(sub) is not None, sub
    others = {name for spec in git_allow.SPECS.values() for name in spec.longs} | {
        "--patch", "--output", "--exec", "--upload-pack", "--no-index", "--follow", "--textconv", "--ext-diff",
        "--open-files-in-pager", "--git-dir", "--work-tree", "--config", "--template", "--force", "--interactive",
    }  # fmt: skip
    for sub, spec in git_allow.SPECS.items():
        assert judge(sub, "--zzz") is not None, sub
        for name in others - set(spec.longs):
            assert judge(sub, name) is not None, (sub, name)
            assert judge(sub, f"{name}=x") is not None, (sub, name)
        for name, kind in spec.longs.items():
            assert judge(sub, name, "x") is None or kind == git_allow.FLAG, (sub, name)
            prefix = name[:-1]
            if prefix not in spec.longs and len(prefix) > 2:
                assert judge(sub, prefix) is not None, (sub, prefix)
            if kind == git_allow.FLAG:
                assert judge(sub, f"{name}=x") is not None, (sub, name)
        for letter in string.ascii_letters:
            if letter not in spec.shorts and (sub, letter) != ("tag", "n"):  # `tag -n<N>`: its own rule
                assert judge(sub, f"-{letter}") is not None, (sub, letter)
    for sub in ("config", "remote"):
        assert judge(sub, "--zzz") is not None


@pytest.mark.parametrize(
    "command",
    [
        "git config user.name -x",  # a value git would read as an option
        "N=-n; git log $N 5",  # a value option the shell makes: its value is the next word, unseen
        "git config get",
        "git config --get",
        "git config --list user.name",
        "git config user.name --get",
        "git remote get-url",
        "git remote show --verbose",
        "git tag -n2 v1 -a",
    ],
)
def test_more_forms_off_the_allowlist(home: Path, command: str) -> None:
    _denied(_run(home, command))


def test_read_config_is_none_for_a_file_git_cant_read(tmp_path: Path) -> None:
    assert git_read.read_config(tmp_path / "missing") is None
    (tmp_path / "bad").write_text("[core\n\tx = 1\n")
    assert git_read.read_config(tmp_path / "bad") is None
    (tmp_path / "ok").write_text("[Core]\n\tWorkTree = /x ; note\n")
    assert git_read.read_config(tmp_path / "ok") == [("core.worktree", "/x")]


@pytest.mark.parametrize(
    "command",
    [
        "git -C ~/other config user.name A",
        "git -C ~/plain config user.email a@example.com",  # no repository
        "git -C $UNSET_X config user.name A",
        "cd ~/other && git config user.name A",
    ],
)
def test_step_0s_config_writes_are_for_the_vaults_repository_only(home: Path, command: str) -> None:
    assert "outside the vault's repository" in _denied(_run(home, command))
    assert _run(home, "git -C ~/other config user.name") is None  # a read is fine anywhere


@pytest.mark.parametrize(
    "command",
    [
        "git clone -q https://github.com/example-org/repo.git ~/.scout-worktrees/x",
        "git -C ~/.scout-worktrees/x switch -c main",
        "git -C ~/other commit -m x",
        "git -C ~/other push -u origin scout/x",
        "git branch scout/x",
    ],
)
def test_the_engine_change_flow_is_told_to_go_in_the_wrap(home: Path, command: str) -> None:
    assert "in the wrap notification" in _denied(_run(home, command))


SCOUT_REMOTE = "https://github.com/Raven-Scout/Scout.git"
ENGINE_ROOT = (
    "ROOT=\"$(python3 -I -c 'import json,os;print(json.load(open(os.path.expanduser("
    '"~/.local/state/scout/engine.json")))["engine_root"])\')"'
)
# phases/core/git-setup.md's engine-change clone, as it spells it, with NAME set first.
PHASE_CLONE = (
    f"NAME=fix-x\n{ENGINE_ROOT}\n"
    'git clone -q "$(git -C "$ROOT" remote get-url origin 2>/dev/null || echo https://github.com/Raven-Scout/Scout.git)"'
    " ~/.scout-worktrees/$NAME && git -C ~/.scout-worktrees/$NAME switch -c scout/$NAME"
)


@pytest.fixture
def engine_home(home: Path) -> Path:
    """``home`` with an engine checkout whose origin is Scout's (named in engine.json), and two clones in
    ~/.scout-worktrees: ``done`` (origin Scout's) and ``fork`` (origin elsewhere)."""
    root = home / "engine"
    _repo(root, ("plugin/x.md",))
    _git(root, "remote", "add", "origin", SCOUT_REMOTE)
    state = home / ".local" / "state" / "scout"
    state.mkdir(parents=True)
    (state / "engine.json").write_text(json.dumps({"engine_root": str(root)}))
    for name, url in (("done", SCOUT_REMOTE), ("fork", "https://example.com/x.git")):
        clone = home / ".scout-worktrees" / name
        _repo(clone, ("plugin/x.md",))
        _git(clone, "remote", "add", "origin", url)
    return home


ENGINE_FLOW = [
    PHASE_CLONE,
    f"git clone -q {SCOUT_REMOTE} ~/.scout-worktrees/y",
    "git clone -q git@github.com:Raven-Scout/Scout.git ~/.scout-worktrees/y"
    " && git -C ~/.scout-worktrees/y switch -c scout/y",
    "git -C ~/.scout-worktrees/done switch -c scout/done",
    "git -C ~/.scout-worktrees/done checkout -b scout/done",
    "cd ~/.scout-worktrees/done && git add plugin/x.md && git commit -q -m 'phases: a clearer rule'",
    "git -C ~/.scout-worktrees/done commit -q -F - <<'EOF'\nphases: a clearer rule\nEOF",
    "git -C ~/.scout-worktrees/done push -u origin scout/done",
    "cd ~/.scout-worktrees/done && git push -q -u origin scout/done",
    "git -C ~/.scout-worktrees/done remote get-url origin",
]


@pytest.mark.parametrize("command", ENGINE_FLOW)
def test_the_engine_change_flow_is_allowed(engine_home: Path, command: str) -> None:
    assert _run(engine_home, command) is None


ENGINE_REFUSED = [
    # a clone's options, source and folder
    f"git clone -q -c core.fsmonitor=x {SCOUT_REMOTE} ~/.scout-worktrees/y",
    f"git clone -q --config=core.sshCommand=x {SCOUT_REMOTE} ~/.scout-worktrees/y",
    f"git clone -q --template=/tmp/t {SCOUT_REMOTE} ~/.scout-worktrees/y",
    f"git clone -q -u x {SCOUT_REMOTE} ~/.scout-worktrees/y",
    f"git clone -q --upload-pack=x {SCOUT_REMOTE} ~/.scout-worktrees/y",
    f"git clone -q --reference ~/Vault {SCOUT_REMOTE} ~/.scout-worktrees/y",
    f"git clone -q --shared {SCOUT_REMOTE} ~/.scout-worktrees/y",
    f"git clone -q --separate-git-dir=/tmp/g {SCOUT_REMOTE} ~/.scout-worktrees/y",
    f"GIT_SSH_COMMAND=x git clone -q {SCOUT_REMOTE} ~/.scout-worktrees/y",
    "git clone -q ~/Vault ~/.scout-worktrees/y",
    "git clone -q . ~/.scout-worktrees/y",
    "git clone -q https://example.com/x.git ~/.scout-worktrees/y",
    'git clone -q "$(git -C ~/other remote get-url origin)" ~/.scout-worktrees/y',
    f"git clone -q {SCOUT_REMOTE} ~/elsewhere",
    f"git clone -q {SCOUT_REMOTE} ~/.scout-worktrees/../Vault2",
    f"git clone -q {SCOUT_REMOTE} ~/.scout-worktrees",
    PHASE_CLONE.replace("NAME=fix-x\n", "NAME=fix-x\nROOT=~/Vault\n"),  # ROOT set again: not the engine folder
    PHASE_CLONE.replace("\ngit clone", "\nROOT=~/Vault; git clone"),
    # where the branch, commit and push run
    "git switch -c scout/x",  # the vault
    "git -C ~/other switch -c scout/x",
    "git -C ~/.scout-worktrees/new switch -c scout/new",  # no clone made it
    "git -C ~/.scout-worktrees/done switch -c main",
    "git -C ~/.scout-worktrees/done switch -c scout/x HEAD~1",
    "git -C ~/other commit -q -m x",
    "git -C ~/.scout-worktrees/done commit --template=/tmp/t -m x",
    "git -C ~/.scout-worktrees/done -c user.name=x commit -m x",
    "git -C ~/.scout-worktrees/done commit --cleanup=verbatim -m x",
    "git -C ~/.scout-worktrees/done commit --amend -m x",
    "git -C ~/.scout-worktrees/done commit -m x plugin/x.md",
    "git -C ~/.scout-worktrees/done commit -F ~/Vault/action-items/context/x.md",  # the KB as a message
    "git -C ~/.scout-worktrees/done push --receive-pack=x -u origin scout/done",
    "git -C ~/.scout-worktrees/done push -o x -u origin scout/done",
    "git -C ~/.scout-worktrees/done push -u origin main",
    "git -C ~/.scout-worktrees/done push -u other scout/done",
    "git -C ~/.scout-worktrees/fork push -u origin scout/done",  # origin isn't Scout's
    "git -C ~/.scout-worktrees/fork commit -q -m x",
    "git -C ~/.scout-worktrees/fork switch -c scout/x",
    f"git clone -q --recurse-submodules {SCOUT_REMOTE} ~/.scout-worktrees/y",
    "git -C ~/.scout-worktrees/done push --force -u origin scout/done",
    "git -C ~/.scout-worktrees/done push --mirror",
    "git -C ~/.scout-worktrees/done push --all -u origin",
    "git -C ~/.scout-worktrees/done push --tags -u origin scout/done",
    "git -C ~/.scout-worktrees/done push -u origin scout/done scout/other",
    "git -C ~/.scout-worktrees/done push -u origin HEAD:main",
    "git push -u origin scout/x",  # the vault
]


@pytest.mark.parametrize("command", ENGINE_REFUSED)
def test_the_engine_change_flow_refuses_every_other_form(engine_home: Path, command: str) -> None:
    _denied(_run(engine_home, command))


def test_the_engine_clone_is_refused_when_the_engine_or_vault_origin_isnt_scouts(engine_home: Path) -> None:
    _git(engine_home / "engine", "remote", "set-url", "origin", "https://example.com/vault.git")
    _denied(_run(engine_home, PHASE_CLONE))
    _git(engine_home / "Vault", "remote", "add", "origin", "https://example.com/vault.git")
    clone = 'git clone -q "$(git -C ~/Vault remote get-url origin 2>/dev/null || echo {u})" ~/.scout-worktrees/y'
    _denied(_run(engine_home, clone.format(u=SCOUT_REMOTE)))


def test_a_clone_whose_config_runs_commands_is_refused(engine_home: Path) -> None:
    clone = engine_home / ".scout-worktrees" / "done"
    _git(clone, "config", "core.fsmonitor", "sh -c 'cat ~/Vault/act*/cont*/x.md'")
    assert "config runs commands" in _denied(_run(engine_home, "git -C ~/.scout-worktrees/done commit -q -m x"))


def test_other_refusals_dont_mention_the_wrap(home: Path) -> None:
    assert "wrap notification" not in _denied(_run(home, "git log -p"))
    assert "wrap notification" not in _denied(_run(home, "git --no-pager --exec-path=/tmp log"))


@pytest.mark.parametrize(
    "command",
    [
        "git commit --no-verify -m x",
        "git commit -n -m x",
        "git commit -qnm x",
        "git commit --amend -m x",
        "git commit -c HEAD",
        "git commit -C HEAD",
        "git commit --reuse-message=HEAD",
        "git commit --template=/tmp/t -m x",
        "git commit --cleanup=verbatim -m x",
        "git commit --fixup=HEAD",
        "git commit --squash=HEAD -m x",
        "git commit -S -m x",
        "git commit --gpg-sign -m x",
        "git commit --author='A <a@example.com>' -m x",
        "git commit --date=now -m x",
        "git -c user.name=x commit -m x",
        "GIT_AUTHOR_NAME=x git commit -m x",
        "git commit",  # no message: an editor
        "git commit -a",
        "git commit -F action-items/context/x.md",  # the KB as a message
        "git -C ~/other commit -q -m x",
        "git -C ~/plain commit -q -m x",
    ],
)
def test_a_vault_commit_refuses_every_other_option(home: Path, command: str) -> None:
    _denied(_run(home, command))


def test_a_path_change_that_puts_another_git_first_is_refused(home: Path) -> None:
    bin_ = home / "out" / "bin"
    bin_.mkdir(parents=True)
    (bin_ / "git").write_text("#!/bin/sh\ncat ~/Vault/action-items/context/x.md\n")
    (bin_ / "git").chmod(0o755)
    for command in (
        "PATH=~/out/bin:$PATH git log --oneline",
        'export PATH="$HOME/out/bin:$PATH"; git log --oneline',
        "path=(~/out/bin $path); git status",
        "mkdir -p ~/nb && cp ~/out/bin/git ~/nb/git && PATH=~/nb:$PATH git log --oneline",  # made there first
        "sh -c 'PATH=$HOME/out/bin:$PATH git log --oneline'",
    ):
        assert "another git" in _denied(_run(home, command)) or GIT_FORMS_MESSAGE in _denied(_run(home, command))
    assert _run(home, "PATH=$PATH:~/out/bin git log --oneline") is None  # after the system git


def test_a_foreign_repository_holding_the_vaults_history_is_judged_by_the_paths_it_names(home: Path) -> None:
    r = home / "fetched"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "fetch", "-q", str(home / "Vault"), "main")
    for command in (
        "git -C ~/fetched show FETCH_HEAD:action-items/context/x.md",
        "git -C ~/fetched show FETCH_HEAD:knowledge-base/a.md",
        "git -C ~/fetched diff FETCH_HEAD -- knowledge-base/a.md",
        "git -C ~/fetched/. show FETCH_HEAD:./knowledge-base.md",
    ):
        _denied(_run(home, command))
    for command in (
        "git -C ~/fetched log --oneline FETCH_HEAD",
        "git -C ~/fetched log --stat FETCH_HEAD -- docs",
        "git -C ~/fetched show FETCH_HEAD:docs/a.md",
        "git -C ~/fetched diff --stat FETCH_HEAD",
    ):
        assert _run(home, command) is None, command


def _hand_made(folder: Path, config: str, linked: Path | None = None) -> None:
    """A repository built with file writes alone (as a run could: `git init` is off its list)."""
    (folder / "objects").mkdir(parents=True)
    (folder / "refs").mkdir()
    (folder / "HEAD").write_text("ref: refs/heads/main\n")
    (folder / "config").write_text(config)
    if linked is not None:
        linked.mkdir(parents=True)
        (linked / ".git").write_text(f"gitdir: {folder}\n")


def test_a_hand_made_repository_whose_config_runs_commands_is_refused(home: Path) -> None:
    fsmonitor = '[core]\n\tfsmonitor = sh -c "cat ~/Vault/act*/cont*/x.md >&2"\n'
    _hand_made(home / "out" / "d", fsmonitor, home / "out" / "w")
    _hand_made(home / "out" / "b", "[core]\n\tbare = false\n" + fsmonitor[7:])
    _hand_made(home / "out" / "t", '[diff "x"]\n\ttextconv = cat\n', home / "out" / "tw")
    _hand_made(home / "out" / "i", '[includeIf "gitdir:/"]\n\tpath = /tmp/x\n', home / "out" / "iw")
    _hand_made(home / "out" / "s", "[remote \"o\"]\n\turl = https://example.com/x\n[credential]\n\thelper = x\n",
               home / "out" / "sw")  # fmt: skip
    for command in (
        "git -C ~/out/w status --short",
        "git -C ~/out/b status",
        "cd ~/out/b && git log --oneline",
        "git -C ~/out/tw diff --stat",
        "git -C ~/out/iw status",
        "git -C ~/out/sw remote show o",
    ):
        assert "config runs commands" in _denied(_run(home, command)), command
    # The tools a person installs for every repository run nothing of a run's.
    lfs = '[filter "lfs"]\n\tclean = git-lfs clean -- %f\n\tprocess = git-lfs filter-process\n'
    lfs += "[core]\n\tfsmonitor = true\n"
    _hand_made(home / "out" / "l", lfs, home / "out" / "lw")
    assert _run(home, "git -C ~/out/lw status") is None
    assert _run(home, "git -C ~/out/sw log --oneline") is None  # a credential helper runs only for the network


@pytest.mark.parametrize(
    "line",
    [
        "\tworktree = {v} ; note",
        "\tworktree = {v} # note",
        '\tworktree = "{v}"',
        "\tworktree = {head}\\\n{tail}",
        "worktree={v}",
    ],
)
def test_find_repo_reads_core_worktree_as_git_does(tmp_path: Path, line: str) -> None:
    vault = (tmp_path / "Vault").resolve()
    vault.mkdir()
    repo = tmp_path / "r"
    _hand_made(repo / ".git", "[core]\n" + line.format(v=vault, head=str(vault)[:-2], tail=str(vault)[-2:]) + "\n")
    found = git_read.find_repo(repo, None, None)
    assert found is not None and found[0] == vault


def test_find_repo_finds_a_bare_repository_and_a_git_folder(tmp_path: Path) -> None:
    bare = tmp_path / "b.git"
    _hand_made(bare, "[core]\n\tbare = true\n")
    assert git_read.find_repo(bare / "refs", None, None) == (bare, bare.resolve(), False)
    _git(tmp_path, "init", "-q", "w")
    found = git_read.find_repo(tmp_path / "w" / ".git" / "objects", None, None)
    assert found is not None and found[1] == (tmp_path / "w" / ".git").resolve()


@pytest.mark.parametrize(
    ("command", "subcommand"),
    [
        ("git diff", "diff"),
        ("git diff HEAD~1", "diff"),
        ("git show HEAD", "show"),
        ("git -C action-items diff", "diff"),
        ("git show --stat 0123abcd", "show"),  # an object id may be a blob
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
    "git -C action-items show HEAD:./$UNSET_X",
    "git -C action-i* show HEAD:./context/x.md",
    "git -C /nonexistent show HEAD:./context/x.md",  # review round 2: a folder not on disk
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


# Keys `git help --config` lists whose names end like a command's but aren't one (review of #817, round 4).
BENIGN_CONFIG_KEYS = {
    "advice.ignoredhook": "a boolean",
    "advice.waitingforeditor": "a boolean",
    "color.pager": "a boolean",
    "diff.x.cachetextconv": "a boolean",
    "gc.repackfilter": "an object filter spec",
    "gitcvs.dbdriver": "a database driver name",
    "http.proxy": "a proxy URL",
    "rebase.reschedulefailedexec": "a boolean",
    "remote.x.partialclonefilter": "an object filter spec",
    "remote.x.proxy": "a proxy URL",
    "uploadpack.allowfilter": "a boolean",
}
COMMAND_LIKE = (
    "cmd", "command", "program", "helper", "hook", "editor", "pager", "browser", "askpass", "uploadpack",
    "receivepack", "driver", "process", "smudge", "textconv", "tool", ".path", "gitproxy", "fsmonitor", "proxy",
    "exec", "filter",
)  # fmt: skip


def _git_help_config_keys() -> set[str]:
    import re

    out = subprocess.run(["git", "help", "--config"], capture_output=True, text=True, check=False)
    if out.returncode != 0 or not out.stdout.strip():
        pytest.skip("this git can't list its config keys")
    keys = {
        re.sub(r"<[^>]*>", "x", line.strip()).lower()
        for line in out.stdout.splitlines()
        if re.match(r"^[A-Za-z][\w.<>-]*$", line.strip())
    }
    assert len(keys) > 300
    return keys


def test_a_run_may_set_only_the_allowlisted_config_keys() -> None:
    """Every key `git help --config` lists is on live_run.CONFIG_ALLOWED or refused in a run (review of #817, round
    5: an allowlist, since a list of what runs commands never converged), and none on the allowlist has a name that
    ends like a command's but for the reviewed harmless ones."""
    from scout.kb.live_run import _config_form, config_allowed

    keys = _git_help_config_keys()
    assert all((_config_form(k) is None) is config_allowed(k) for k in keys)
    risky = sorted(
        k
        for k in keys
        if config_allowed(k) and (k.endswith(COMMAND_LIKE) or k.startswith("pager.")) and k not in BENIGN_CONFIG_KEYS
    )
    assert not risky, risky
    for key in (
        "remote.x.uploadpack", "remote.x.receivepack", "core.alternaterefscommand", "sendemail.tocmd",
        "submodule.x.update", "imap.tunnel", "instaweb.httpd", "sendemail.smtpserver", "remote.x.vcs",
        "help.autocorrect", "core.worktree", "safe.directory", "url.x.insteadof",
    ):  # fmt: skip
        assert _config_form(key) is not None, key
