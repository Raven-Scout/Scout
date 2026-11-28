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
    for rel in ("docs/a.md", "scripts/a.md", "action-items/action-items-2026-11-23.md", ".scout-state/index.md"):
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
        errors="replace",  # an archive's bytes: only whether a note's word shows matters
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
    # round 3: diff reads a folder; zsh groups after `}` and as a redirect's target; `~-` after the command's cd
    "cd action-items; mkdir -p ../e; diff -N context ../e",
    "cat {,}(kn*|x)-base/*/*/*.md",
    "x=k; cat ${x}(n*|y)-base/*/*/*.md",
    "cat < (kn*|x)-base/*/*/a.md",
    "cd action-items; cat < (con*|x)/x.md",
    "cd kn*-base; cd ..; cat ~-/*/*/*.md",
    # round 5: `${…}` inside double quotes expands
    'X=knowledge; cat "${X}-base/projects/a/a.md"',
    'X=context; cat "action-items/${X}/x.md"',
    'cd action-items; cat "${PWD}/context/x.md"',
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
    # By its path: a run's bare grep is Claude Code's ugrep, which reads context/ through `*` (shell shadows).
    "/usr/bin/grep -n x action-items/* 2>/dev/null",
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
    # round 3: a quoted regex over a non-KB file; quoted characters are literal
    'grep -o "projects/(alpha|beta|gamma|delta|eps|zeta)[^/]*/[^/]*\\.md" .scout-state/index.md | sort -u',
    'grep -o -e "projects/(alpha|beta)[^/]*" .scout-state/index.md',
    'cat "k*/a.md" 2>/dev/null',
    'cat "kn{o,}wledge-base/a.md" 2>/dev/null',
]


# Readers whose files aren't simply their operands (scout.kb.other_reads, #790 follow-up), with the programs each
# needs: a copy or an archive out of the KB, diff -r over a folder holding it, a program naming a file in its text,
# dd, source, and plain readers the gate lacked. Skipped where a program isn't installed (ditto is macOS's).
READS_A_NOTE = [
    ("cd action-items; tar cf - con* | tar xOf -", ("tar",)),
    ("tar cf - . | tar xOf - 2>/dev/null", ("tar",)),
    ("cd action-items; cp -r con* ../x; cat ../x/x.md", ("cp",)),
    ("cd action-items; zip -qr ../z.zip con*; unzip -p ../z.zip", ("zip", "unzip")),
    ("zip -qr - . | cat", ("zip",)),
    ("cd action-items; rsync -a con* ../x/; cat ../x/context/x.md", ("rsync",)),
    ("cd action-items; pax -w con*", ("pax",)),
    ("pax -w . 2>/dev/null", ("pax",)),
    ("cd action-items; ditto con* ../x; cat ../x/x.md", ("ditto",)),
    ("cd action-items; split -l 1 con*/x.md ../sp; cat ../sp*", ("split",)),
    ("cd action-items; gzip -c con*/x.md | gunzip", ("gzip", "gunzip")),
    ("mkdir -p e; diff -rN action-items e", ("diff",)),
    ("mkdir -p e; diff -rN . e", ("diff",)),
    ("cd action-items; sed 'r context/x.md' ../docs/a.md", ("sed",)),
    ("cd action-items; awk 'BEGIN{while((getline l < \"context/x.md\")>0) print l}'", ("awk",)),
    ("cd action-items; awk 'BEGIN{f=\"context/x.md\"; while ((getline l < f) > 0) print l}'", ("awk",)),
    ("cd action-items; awk -v f=context/x.md 'BEGIN{while ((getline l < f) > 0) print l}'", ("awk",)),
    ("cd action-items; awk 'BEGIN{\"cat context/x.md\" | getline l; print l}'", ("awk",)),
    ("cd action-items; perl -ne print con*/x.md", ("perl",)),
    ('cd action-items; perl -e \'open(F, "<", "context/x.md"); print <F>\'', ("perl",)),
    ("cd action-items; dd if=context/x.md 2>/dev/null", ("dd",)),
    ("cd action-items; source context/x.md 2>&1", ()),
    ("cd action-items; . context/x.md 2>&1", ()),
    ("cd action-items; join con*/x.md con*/x.md", ("join",)),
    ("cd action-items; expand con*/x.md", ("expand",)),
    ("cd action-items; look kb con*/x.md", ("look",)),
    ("cd action-items; shuf con*/x.md", ("shuf",)),
    ("cat \"$(cat <<'EOF'\nknowledge-base/a.md\nEOF\n)\"", ()),
    ("X=kn; cat <<EOF\n$(cat ${X}owledge-base/a.md)\nEOF", ()),
    ("cd action-items; cat $(find .) 2>/dev/null", ("find",)),
    ('cd action-items; for f in $(find .); do cat "$f" 2>/dev/null; done', ("find",)),
    ('cd action-items; find . | while read f; do cat "$f" 2>/dev/null; done', ("find",)),
    ('cd action-items; find . -type f | while IFS= read -r f; do cat "$f"; done', ("find",)),
    ('cd action-items; rg --files | while read f; do cat "$f"; done', ("rg",)),
    ("cd action-items; cat $(rg --files)", ("rg",)),
    # #819 review
    ('perl -e \'system "cat", "knowledge-base/a.md"\'', ("perl",)),
    ('perl -e \'exec "cat", "knowledge-base/a.md"\'', ("perl",)),
    ("perl -e 'local @ARGV=(\"knowledge-base/a.md\"); print <>'", ("perl",)),
    ("perl -e 'print qx{cat knowledge-base/a.md}'", ("perl",)),
    ("perl -e 'open(my $f, \"<\", q{knowledge-base/a.md}); print <$f>'", ("perl",)),
    ("perl -e '$f = q{knowledge-base/a.md}; open(F, $f); print <F>'", ("perl",)),
    ('awk \'BEGIN{system("cat kn" "owledge-base/a.md")}\'', ("awk",)),
    ("awk 'BEGIN{c = \"cat knowledge-base/a.md\"; system(c)}'", ("awk",)),
    ('awk \'BEGIN{("cat kn" "owledge-base/a.md") | getline l; print l}\'', ("awk",)),
    ('awk \'BEGIN{print "cat knowledge-base/a.md" | "sh"}\'', ("awk",)),
    ("F=knowledge-base/a.md awk 'BEGIN{getline l < ENVIRON[\"F\"]; print l}'", ("awk",)),
    ("tar cf knowledge-base/x.tar --use-compress-program='tee /dev/stderr' knowledge-base/a.md 2>&1", ("tar",)),
    ('D=knowledge-base; sed "r $D/a.md" docs/a.md', ("sed",)),
    ("printf '%s\\n' */*/* | while read f; do cat \"$f\" 2>/dev/null; done", ()),
]

READS_NO_NOTE = [
    ("tar cf ../out.tar docs/", ("tar",)),
    ("cp -r docs/ ../x", ("cp",)),
    ("mkdir -p ../other; diff -r scripts/ ../other/ 2>/dev/null", ("diff",)),
    ("sed 's/a/b/' docs/a.md", ("sed",)),
    ("sed 'r docs/a.md' scripts/a.md", ("sed",)),
    ("awk '{print $1}' docs/a.md", ("awk",)),
    ("awk 'BEGIN{while((getline l < \"docs/a.md\")>0) print l}'", ("awk",)),
    ("perl -ne print docs/a.md", ("perl",)),
    ("cat \"$(cat <<'EOF'\ndocs/a.md\nEOF\n)\"", ()),
    ("X=kn; cat <<'EOF'\n$(cat ${X}owledge-base/a.md)\nEOF", ()),
    ('find docs | while read f; do cat "$f" 2>/dev/null; done', ("find",)),
]


def _needs(tools: tuple[str, ...]) -> None:
    missing = [t for t in tools if shutil.which(t) is None]
    if missing:
        pytest.skip(f"not installed: {', '.join(missing)}")


@pytest.mark.parametrize(("command", "tools"), READS_A_NOTE)
def test_a_reader_zsh_reads_a_note_with_the_gate_denies(home: Path, command: str, tools: tuple[str, ...]) -> None:
    _needs(tools)
    assert "kbnote" in _zsh(home, command).lower(), "the case must really print a note under zsh"
    out = _gate(home, command)
    assert out is not None and out["hookSpecificOutput"]["permissionDecision"] == "deny", command


@pytest.mark.parametrize(("command", "tools"), READS_NO_NOTE)
def test_a_reader_that_reads_no_note_the_gate_allows(home: Path, command: str, tools: tuple[str, ...]) -> None:
    _needs(tools)
    assert "kbnote" not in _zsh(home, command).lower()
    assert _gate(home, command) is None, command


@pytest.mark.parametrize("command", PRINTS_A_NOTE)
def test_what_zsh_reads_from_the_kb_the_gate_denies(home: Path, command: str) -> None:
    assert "kbnote" in _zsh(home, command).lower(), "the case must really print a note under zsh"
    out = _gate(home, command)
    assert out is not None and out["hookSpecificOutput"]["permissionDecision"] == "deny", command


@pytest.mark.parametrize("command", PRINTS_NO_NOTE)
def test_what_reads_no_kb_note_under_zsh_the_gate_allows(home: Path, command: str) -> None:
    assert "kbnote" not in _zsh(home, command).lower()
    assert _gate(home, command) is None, command


# Under Claude Code's shell snapshot a run's bare grep is the Claude binary's ugrep, which reads a folder `*` names
# one level deep: these print a note in a run though BSD grep prints none. The snapshot's own functions run them
# where a Claude binary with the embedded tools is installed; the gate's deny is checked everywhere.
PRINTS_A_NOTE_UNDER_THE_SNAPSHOT = [
    "grep -n x action-items/* 2>/dev/null",
    "grep -l x */ 2>/dev/null",
    "cd action-items && grep -n x context 2>/dev/null",
]


@pytest.mark.parametrize("command", PRINTS_A_NOTE_UNDER_THE_SNAPSHOT)
def test_what_the_snapshots_grep_reads_from_the_kb_the_gate_denies(home: Path, command: str) -> None:
    from tests.unit.test_kb_read_gate_shell_shadows import CLAUDE_BIN, SNAPSHOT_FUNCTIONS

    assert "kbnote" not in _zsh(home, command).lower()  # BSD grep reads no note
    if CLAUDE_BIN is not None:
        shadowed = f"_cc_bin={CLAUDE_BIN!r}\n{SNAPSHOT_FUNCTIONS}\n{command}"
        assert "kbnote" in _zsh(home, shadowed).lower(), "the case must really print a note under the snapshot"
    out = _gate(home, command)
    assert out is not None and out["hookSpecificOutput"]["permissionDecision"] == "deny", command
