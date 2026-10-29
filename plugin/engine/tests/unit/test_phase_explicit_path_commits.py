"""Vault commits stage explicit paths, never the whole tree.

Scheduled lanes share one working tree and overlap, so `git add -A` (or `.`, `-u`,
`commit -a`) stages a concurrent run's half-written files into this run's commit.
The dreaming steps moved to `scripts/git-safe-commit.sh`, but research's commit step
kept `git add -A` until the hard-rule audit found it. These scan every command a
brain file, slash command or vault script tells a session to run.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[3]

# `git [-C <dir>] add -A|--all|.|-u|--update` and `git commit -a|--all|-am`.
_WHOLE_TREE = re.compile(
    r"\bgit\s+(?:-C\s+\S+\s+)?"
    r"(?:add\s+(?:-A|--all|-u|--update|\.(?:/)?)(?=[\s;&|`)\"']|$)"
    r"|commit\s+(?:-a\w*|--all)\b)"
)
_FENCE = re.compile(r"^\s*(```|~~~)")


def _markdown_commands(path: Path) -> list[tuple[int, str]]:
    """Lines inside fenced code blocks, plus prose lines that chain a commit (`… && git commit`).

    Prose may name the forbidden form to forbid it ("never bare `git add -A`"); a fence or a
    chained commit is something a session runs.
    """
    out: list[tuple[int, str]] = []
    in_fence = False
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence or re.search(r"&&\s*git\b[^\n]*\bcommit\b", line):
            out.append((n, line))
    return out


def _script_commands(path: Path) -> list[tuple[int, str]]:
    return [
        (n, line)
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if not line.lstrip().startswith("#")
    ]


def _sources() -> list[Path]:
    md = [p for d in ("phases", "commands", "skills") for p in sorted((PLUGIN / d).rglob("*.md"))]
    scripts = [
        p
        for p in sorted((PLUGIN / "templates").rglob("*"))
        if p.is_file() and p.suffix in {".tmpl", ".sh", ".py"}
    ]
    return md + scripts


def _hits(path: Path) -> list[str]:
    lines = _markdown_commands(path) if path.suffix == ".md" else _script_commands(path)
    return [f"{path.relative_to(PLUGIN)}:{n}: {line.strip()}" for n, line in lines if _WHOLE_TREE.search(line)]


def test_no_brain_command_or_vault_script_stages_the_whole_tree() -> None:
    hits = [h for p in _sources() for h in _hits(p)]
    assert hits == [], "stage explicit paths with scripts/git-safe-commit.sh:\n" + "\n".join(hits)


@pytest.mark.parametrize(
    "line",
    [
        'git -C "{{SCOUT_DIR}}" add -A && git -C "{{SCOUT_DIR}}" commit -m "research"',
        "git add -A",
        "git add --all",
        "git add . && git commit -m x",
        "git -C <SCOUT_DIR> add -u",
        "git commit -am 'msg'",
        "git commit --all -m msg",
    ],
)
def test_the_scan_catches_every_whole_tree_form(line: str) -> None:
    assert _WHOLE_TREE.search(line)


@pytest.mark.parametrize(
    "line",
    [
        '{{SCOUT_DIR}}/scripts/git-safe-commit.sh "research [HH:MM]: x" knowledge-base/topics/a.md',
        "git add knowledge-base/session-log/2026-10.md",
        "git add .gitignore",
        "git commit -m 'msg'",
        "git mv projects/a projects/archived/a",
    ],
)
def test_the_scan_lets_explicit_paths_through(line: str) -> None:
    assert not _WHOLE_TREE.search(line)


def test_prose_that_forbids_add_all_is_not_a_hit(tmp_path: Path) -> None:
    p = tmp_path / "phase.md"
    p.write_text("Stage explicit paths, NEVER bare `git add -A`.\n\n```bash\nscripts/git-safe-commit.sh m a.md\n```\n")
    assert _hits_in(p) == []


def test_a_fenced_add_all_is_a_hit(tmp_path: Path) -> None:
    p = tmp_path / "phase.md"
    p.write_text("```bash\ngit -C x add -A\n```\n")
    assert len(_hits_in(p)) == 1


def _hits_in(path: Path) -> list[tuple[int, str]]:
    return [(n, line) for n, line in _markdown_commands(path) if _WHOLE_TREE.search(line)]


def test_research_commits_through_the_safe_commit_helper() -> None:
    text = (PLUGIN / "phases" / "research" / "commit-notify.md").read_text(encoding="utf-8")
    assert '{{SCOUT_DIR}}/scripts/git-safe-commit.sh "research [HH:MM]:' in text
    assert "knowledge-base/session-log/YYYY-MM.md" in text.split("## Step 4b", 1)[1].split("## Step 4c", 1)[0]
