"""kb-read-gate agrees with zsh about which file a flag's value is.

Scheduled runs execute their Bash calls under zsh. Each case here goes through the gate and, when the tool
is installed, through real zsh in a synthetic vault. That shows the verdict matches what the shell actually
reads.
- A deny must be a command that really reads a KB note: its output carries a line only a KB note holds.
- An allow must be one whose output doesn't.

Covered: a value attached to ``-e`` (``grep -eTODO k*/…``, ``grep -nex …``, ``sed -n -e'1,20p' …``), the file
``-f``/``--file`` opens (a pattern file, a sed or awk script), and a search tool's pattern that merely looks
like a KB glob (``ag 'k*' docs``).

Synthetic vault only.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from scout.hooks import kb_read_gate as gate
from scout.kb.raw_read import listed_roots
from tests.integration.test_kb_read_gate_zsh import OPTIONS
from tests.unit.test_hooks_kb_read_gate import RUN, _denied, _payload

MARK = "kb-only-line"  # appears only in the KB notes, or in what a KB script prints


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    files = {
        "knowledge-base/a.md": f"TODO {MARK} x\n",
        "knowledge-base/pats.md": "kb-only\n",  # a grep pattern file
        "knowledge-base/sub.md": f"s/.*/{MARK}/\n",  # a sed script
        "knowledge-base/print.md": f'{{print "{MARK}"}}\n',  # an awk program
        "action-items/context/x.md": f"{MARK}\n",
        "docs/a.md": "TODO docs-line x\n",
        "docs/b.md": f"{MARK}\n",  # matched only by the KB pattern file
        "docs/pats.txt": "docs\n",
        "scripts/p.awk": "{print $1}\n",
        "scout-config.yaml": "user: {}\n",
    }
    for rel, text in files.items():
        (h / "Vault" / rel).parent.mkdir(parents=True, exist_ok=True)
        (h / "Vault" / rel).write_text(text)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(h / "Vault"))
    return h.resolve()


def _gate(home: Path, command: str, where: str = ""):
    cwd = home / "Vault" / where
    return gate.run(stdin=_payload("Bash", {"command": command}, cwd), env={**RUN, "HOME": str(home)})


def _zsh(home: Path, command: str) -> str | None:
    """What ``command`` prints under zsh from the vault root; None when zsh or the tool isn't installed."""
    if shutil.which("zsh") is None or shutil.which(command.split()[0]) is None:
        return None
    res = subprocess.run(  # the run's shell: zsh with the options Claude Code evals each Bash call under
        ["zsh", "-f", "-c", f'{OPTIONS}; cd "$1" && eval "$2"', "zsh", str(home / "Vault"), command],
        env={"HOME": str(home), "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"},
        capture_output=True,
        text=True,
        timeout=10,
    )
    return res.stdout + res.stderr


@pytest.mark.parametrize(
    "command",
    [
        "grep -eTODO k*/a.md",
        "grep -rnA3 TODO",  # #791 review: grep's recursion inside a cluster
        "grep -rC2 TODO",
        "grep -r3 TODO .",
        "grep -nex k*/a.md",
        "sed -e1p k*/a.md",
        "sed -n -e'1,20p' k*/a.md",
        "grep -f k*/pats.md docs/b.md",
        "grep --file k*/pats.md docs/b.md",  # `--file=k*/…` isn't globbed: zsh says "no matches"
        "sed -f k*/sub.md docs/a.md",
        "awk -f k*/print.md docs/a.md",
    ],
)
def test_a_flag_that_reads_a_kb_note_under_zsh_is_denied(home: Path, command: str) -> None:
    out = _zsh(home, command)
    assert out is None or MARK in out, out  # zsh really reads the note
    assert _denied(_gate(home, command))


@pytest.mark.parametrize(
    "command",
    [
        "grep -e 'k*' docs/a.md",
        "grep -eTODO docs/a.md",
        "sed -e1p docs/a.md",
        "sed -n -e'1,20p' docs/a.md",
        "grep -f docs/pats.txt docs/a.md",
        "awk -f scripts/p.awk docs/a.md",
        "ag 'k*' docs",
        "ack 'k*' docs",
        "ugrep -r 'k*' docs",
        "fd 'k*' docs",
    ],
)
def test_a_flag_that_reads_no_kb_note_under_zsh_is_allowed(home: Path, command: str) -> None:
    out = _zsh(home, command)
    assert out is None or MARK not in out, out
    assert _gate(home, command) is None


# ----- the search tools as listers that feed xargs ----------------------------------------------------
# Run from action-items/, a lister there prints context/x.md, and `xargs cat` reads it. (From the vault
# root the recursive-read rule denies these already.)


@pytest.mark.parametrize(
    "command",
    [
        "fd . | xargs cat",
        "fd -e md | xargs head -1",
        "fdfind x | xargs cat",
        "ag -g x | xargs cat",
        "ag -l TODO | xargs cat",
        "ack -f | xargs cat",
        "ack-grep -g x | xargs cat",
        "ugrep -rl TODO . | xargs cat",
        "ug -l TODO context/x.md | xargs cat",
    ],
)
def test_a_search_tool_listing_kb_files_into_xargs_is_denied(home: Path, command: str) -> None:
    assert _denied(_gate(home, command, "action-items"))


@pytest.mark.parametrize(
    "command",
    [
        "fd . scripts | xargs cat",
        "fd -e sh | xargs wc -l",
        "ag -g run scripts | xargs cat",
        "ack -f scripts | xargs wc -l",
        "ugrep -rl TODO scripts | xargs cat",
    ],
)
def test_a_search_tool_listing_other_files_into_xargs_is_allowed(home: Path, command: str) -> None:
    assert _gate(home, command) is None


@pytest.mark.parametrize(
    ("name", "args", "expected"),
    [
        ("fd", ["."], ["."]),
        ("fdfind", ["x", "docs"], ["docs"]),
        ("fd", ["-e", "py"], []),  # a filter that keeps every KB note out
        ("fd", [".", "-X", "cat"], None),  # prints what cat prints
        ("fd", ["-V"], None),
        ("ag", ["-g", "x", "docs"], ["docs"]),
        ("ag", ["-l", "x"], ["."]),
        ("ag", ["x"], None),  # prints matching lines, not paths
        ("ack", ["-f"], ["."]),
        ("ack-grep", ["-g", "x"], ["."]),
        ("ugrep", ["-rl", "x", "docs"], ["docs"]),
        ("ug", ["-r", "x", "."], None),
        ("cat", ["a"], None),
    ],
)
def test_listed_roots_of_the_other_search_tools(name: str, args: list[str], expected: list[str] | None) -> None:
    assert listed_roots(name, args) == expected
