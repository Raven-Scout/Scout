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


def _gate(home: Path, command: str):
    return gate.run(stdin=_payload("Bash", {"command": command}, home / "Vault"), env={**RUN, "HOME": str(home)})


def _zsh(home: Path, command: str) -> str | None:
    """What ``command`` prints under zsh from the vault root; None when zsh or the tool isn't installed."""
    if shutil.which("zsh") is None or shutil.which(command.split()[0]) is None:
        return None
    res = subprocess.run(
        ["zsh", "-f", "-c", command],
        cwd=home / "Vault",
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
