"""The KB read gate against the shell scheduled runs actually use: zsh, with NO_EXTENDED_GLOB, NO_BARE_GLOB_QUAL
and NO_EQUALS (Claude Code evals each Bash call there). Each command runs for real in a synthetic vault, and the
gate must deny every one that prints a KB note (its text or its name carries ``kbnote``) and allow the neighbours,
which print none. Kernel-independent: no sandbox, only zsh; skipped where zsh isn't installed."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from scout.hooks import kb_read_gate as gate
from tests.unit.test_hooks_kb_read_gate import RUN, _payload

ZSH = shutil.which("zsh")
pytestmark = pytest.mark.skipif(ZSH is None, reason="zsh isn't installed")
OPTIONS = "setopt NO_EXTENDED_GLOB NO_BARE_GLOB_QUAL NO_EQUALS"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """``~/Vault`` with KB notes whose text says ``kbnote`` (one is named so, for listings) and ordinary files."""
    h = (tmp_path / "home").resolve()
    v = h / "Vault"
    kb = ["knowledge-base/a.md", "knowledge-base/x.md", "knowledge-base/projects/a/a.md", "action-items/context/x.md"]
    for rel in [*kb, "knowledge-base/kbnote-index.md", "action-items/context/kbnote-ctx.md"]:
        (v / rel).parent.mkdir(parents=True, exist_ok=True)
        (v / rel).write_text(f"kbnote x alpha {rel}\n")
    for rel in ("docs/a.md", "scripts/a.md", "action-items/action-items-2026-11-23.md"):
        (v / rel).parent.mkdir(parents=True, exist_ok=True)
        (v / rel).write_text("plain x alpha\n")
    (v / "scout-config.yaml").write_text("user: {}\n")
    (h / "notes.txt").write_text("plain\n")
    monkeypatch.setenv("SCOUT_DATA_DIR", str(v))
    return h


def _zsh(home: Path, command: str) -> str:
    assert ZSH is not None
    env = {"HOME": str(home), "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C"}
    script = f'{OPTIONS}; cd "$1" && eval "$2"'
    out = subprocess.run(  # noqa: S603 - a fixed zsh, a synthetic vault
        [ZSH, "-f", "-c", script, "zsh", str(home / "Vault"), command],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    return out.stdout


def _gate(home: Path, command: str) -> dict | None:
    env = {**RUN, "HOME": str(home), "SCOUT_DATA_DIR": str(home / "Vault")}
    return gate.run(stdin=_payload("Bash", {"command": command}, home / "Vault"), env=env)


PRINTS_A_NOTE = [
    # round 2 of the review: zsh forms the gate read the bash way
    'cat "$HOME"/Vault/kn*/projects/a/a.md # it\'s',
    'grep -n x "$HOME"/Vault/k*/a.md # don\'t',
    "cat **/x.md",
    "grep -n x **/a.md",
    "cat k*/**/a.md",
    "cat (knowledge|x)-base/a.md",
    "cat kn(o|)wledge-base/a.md",
    "cat knowledge-bas[e\\$]/a.md",
    "cat ~+/k*/a.md",
    "cat ${HOME:-/nowhere}/Vault/k*/a.md",
    "cat ${PWD%/}/k*/a.md",
    "ls action-items/*",
    "for f in " + " ".join(f"docs/n{i}.md" for i in range(300)) + " knowledge-base/a.md; do cat $f 2>/dev/null; done",
    # round 1: globs, the command's own variables, braces, ANSI-C, xargs, cd
    "cat k*/projects/a/a.md",
    "cat */a.md",
    "K=knowledge; cat ${K}-base/x.md",
    "a=k; b=nowledge-base/a.md; cat $a$b",
    "cat kn{o,}wledge-base/a.md",
    "cat $'knowledge\\x2dbase/a.md'",
    "printf 'knowledge%sbase/a.md' - | xargs cat",
    "echo knowledge-base/a.md | xargs -I{} head -1 {}",
    "cd action-items && cat context/x.md",
    'f=knowledge-base/a.md && echo "$(head -1 $f)"',
    'grep -n "alpha\\|beta" knowledge-base/a.md',
]

PRINTS_NO_NOTE = [
    "grep -n x action-items/* 2>/dev/null",
    "cat action-items/* 2>/dev/null",
    "head -n 3 action-items/* 2>/dev/null",
    "ls -d */",
    "cat docs/a.md # it's a note",
    "cat ${PWD%/}/docs/a.md",
    "ls ~+/docs",
    "cat ${HOME:-/x}/notes.txt",
    "cat docs/[a$]*.md",
    "cat action-items/action-items-$(date +%Y-%m-%d).md 2>/dev/null",
    "find docs \\( -name a.md -o -name b.md \\) -print",
    "git ls-files docs 2>/dev/null | xargs cat 2>/dev/null",
    "grep -l x docs/*.md | xargs grep -H alpha",
]


@pytest.mark.parametrize("command", PRINTS_A_NOTE)
def test_what_zsh_reads_from_the_kb_the_gate_denies(home: Path, command: str) -> None:
    assert "kbnote" in _zsh(home, command).lower(), "the case must really print a note under zsh"
    out = _gate(home, command)
    assert out is not None and out["hookSpecificOutput"]["permissionDecision"] == "deny", command


@pytest.mark.parametrize("command", PRINTS_NO_NOTE)
def test_what_reads_no_kb_note_under_zsh_the_gate_allows(home: Path, command: str) -> None:
    assert "kbnote" not in _zsh(home, command).lower()
    assert _gate(home, command) is None, command
