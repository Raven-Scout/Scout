"""Robustness, part two: CRLF on disk through ops, linear lint, prose that cannot lock a page."""

from __future__ import annotations

import datetime as dt
import shutil
import time
from pathlib import Path

import pytest

from scout.errors import LabError
from scout.lab import experiments, lint, ops, paths
from scout.lab.parser import parse_front_page
from scout.lab.writer import add_comment, set_understanding

THREAD_DIR = Path(__file__).parent.parent / "fixtures" / "lab" / "summary-drift"
SLUG = "summary-drift"
NOW = dt.datetime(2026, 11, 22, 9, 30, tzinfo=dt.UTC)


def _front() -> str:
    return (THREAD_DIR / "summary-drift.md").read_text(encoding="utf-8")


def _crlf(text: str) -> str:
    return text.replace("\n", "\r\n")


def _only_crlf(text: str) -> bool:
    return text.count("\r\n") == text.count("\n") and "\r" not in text.replace("\r\n", "")


def _crlf_vault(tmp_path: Path) -> tuple[Path, Path, Path]:
    shutil.copytree(THREAD_DIR, paths.thread_dir(tmp_path, SLUG))
    front, tl = paths.front_page(tmp_path, SLUG), paths.timeline_path(tmp_path, SLUG)
    for f in (front, tl):
        f.write_bytes(f.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    return tmp_path, front, tl


def _bytes_only_crlf(path: Path) -> bool:
    data = path.read_bytes()
    return b"\r\n" in data and data.count(b"\r\n") == data.count(b"\n") and data.count(b"\r") == data.count(b"\r\n")


# --- real writes keep CRLF on disk -----------------------------------------------------


def test_ops_writes_keep_crlf_on_disk(tmp_path: Path) -> None:
    vault, front, tl = _crlf_vault(tmp_path)
    assert _bytes_only_crlf(front) and _bytes_only_crlf(tl)
    ops.comment(vault, SLUG, "Q3", "alex", "still CRLF?", now=NOW)
    assert _bytes_only_crlf(front)
    ops.question_add(vault, SLUG, "A new question?", now=NOW)
    assert _bytes_only_crlf(front)
    ops.timeline_add(vault, SLUG, "research run", "did it", "found it", "Q3", now=NOW)
    assert _bytes_only_crlf(tl) and _bytes_only_crlf(front)
    ops.owner_verb(vault, SLUG, "pause", "alex", now=NOW)
    assert _bytes_only_crlf(tl) and _bytes_only_crlf(front)
    assert parse_front_page(SLUG, front.read_text(encoding="utf-8")).problems == []


def test_rollback_restores_crlf_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault, front, tl = _crlf_vault(tmp_path)
    real = ops.atomic_write_text

    def flaky(path: Path | str, text: str, **kw: object) -> None:
        if Path(path) == front:
            raise OSError("disk full")
        real(path, text)

    before = (front.read_bytes(), tl.read_bytes())
    monkeypatch.setattr(ops, "atomic_write_text", flaky)
    with pytest.raises(OSError, match="disk full"):
        ops.owner_verb(vault, SLUG, "pause", "alex", now=NOW)
    assert (front.read_bytes(), tl.read_bytes()) == before
    assert _bytes_only_crlf(tl)


# --- lint heading regex is linear ------------------------------------------------------


class _NoSource:
    def files(self) -> list[str]:
        return []

    def read(self, rel: str) -> str | None:
        return None


def _lint_timeline(text: str) -> list[lint.Finding]:
    return lint._timeline(_NoSource(), f"{lint._PREFIX}{SLUG}/{SLUG}-timeline.md", SLUG, text)


@pytest.mark.parametrize("tail", [" " * 100_000 + "x", "owner" + " " * 100_000])
def test_lint_long_timeline_heading_is_fast(tail: str) -> None:
    text = f"# T — timeline\n\n### 2026-11-20 10:00 · {tail}\n- bullet\n"
    start = time.perf_counter()
    _lint_timeline(text)
    assert time.perf_counter() - start < 0.5


def test_lint_accepts_a_known_tag_with_trailing_spaces() -> None:
    found = _lint_timeline("# T — timeline\n\n### 2026-11-20 10:00 · owner   \n- bullet\n")
    assert not any("unknown timeline tag" in f.message for f in found)


# --- prose cannot lock a page ----------------------------------------------------------


@pytest.mark.parametrize("prose", ["Intro\n```\ncode", "~~~\nsome text", "````\n```\nmore"])
def test_prose_refuses_an_unbalanced_fence(prose: str) -> None:
    with pytest.raises(LabError, match="code fence"):
        set_understanding(_front(), prose)


def test_prose_with_a_balanced_fence_is_fine() -> None:
    out = set_understanding(_front(), "Look:\n```\nx = 1\n```\nDone.")
    assert parse_front_page(SLUG, out).problems == []


def test_mutate_refuses_a_result_with_damaged_headings(tmp_path: Path) -> None:
    shutil.copytree(THREAD_DIR, paths.thread_dir(tmp_path, SLUG))
    front = paths.front_page(tmp_path, SLUG)
    before = front.read_bytes()
    with pytest.raises(LabError, match="damaged"):
        ops.mutate(tmp_path, front, lambda t: t.replace("## Evidence", "```\n## Evidence", 1), slug=SLUG)
    assert front.read_bytes() == before


# --- mixed endings, experiment bodies --------------------------------------------------


def test_dominant_line_ending_wins() -> None:
    mostly_lf = _front().replace("\n", "\r\n", 3)
    out = add_comment(mostly_lf, "thread", "alex", "hi", "2026-11-22 09:00")
    assert "\r" not in out.replace("\r\n", "", 3)
    mostly_crlf = _crlf(_front()).replace("\r\n", "\n", 2)
    assert _only_crlf(add_comment(mostly_crlf, "thread", "alex", "hi", "2026-11-22 09:00"))


def test_experiment_plan_ignores_fenced_headings(tmp_path: Path) -> None:
    shutil.copytree(THREAD_DIR, paths.thread_dir(tmp_path, SLUG))
    body = "## Hypothesis\nH.\n```\n## Result\n```\n\n## Method\nRun.\n\n## Data\nPages.\n"
    exp = experiments.plan(tmp_path, SLUG, "fenced", "T", [], body, now=dt.datetime(2026, 11, 21, 1, 11, tzinfo=dt.UTC))
    assert exp.problems == []
    missing = "## Hypothesis\nH.\n```\n## Method\n## Data\n```\n"
    with pytest.raises(LabError, match="needs a '## Method'"):
        experiments.plan(tmp_path, SLUG, "other", "T", [], missing)
