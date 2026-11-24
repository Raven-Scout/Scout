"""A thread slug is checked against ``SLUG_RE`` at every entry, before any path is built from it."""

from __future__ import annotations

import datetime as dt
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout import cli
from scout.errors import LabError
from scout.lab import experiments, ops, paths, summary
from scout.lab.parser import load_thread

FIXTURES = Path(__file__).parent.parent / "fixtures" / "lab"
NOW = dt.datetime(2026, 11, 22, 9, 30, tzinfo=dt.UTC)
BAD_SLUGS = ["..", "a/b", "", "Summary-Drift", "../summary-drift", "-x"]
BODY = "## Hypothesis\nh\n\n## Method\nm\n\n## Data\nd\n"


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    shutil.copytree(FIXTURES / "summary-drift", paths.thread_dir(tmp_path, "summary-drift"))
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SCOUT_READER", raising=False)
    monkeypatch.delenv("SCOUT_MODE", raising=False)
    return tmp_path


def _listing(vault: Path) -> list[str]:
    return sorted(p.relative_to(vault).as_posix() for p in vault.rglob("*"))


@pytest.mark.parametrize("slug", BAD_SLUGS)
def test_every_entry_rejects_a_bad_slug(vault: Path, slug: str) -> None:
    before = _listing(vault)
    calls = [
        lambda: load_thread(vault, slug),
        lambda: summary.lab_thread(vault, slug),
        lambda: ops.owner_verb(vault, slug, "pause", "alex", now=NOW),
        lambda: ops.comment(vault, slug, "thread", "alex", "hi", now=NOW),
        lambda: ops.reply(vault, slug, "Q3", 1, "hi", now=NOW),
        lambda: ops.propose_close(vault, slug, "done", now=NOW),
        lambda: ops.question_add(vault, slug, "Why?", now=NOW),
        lambda: ops.question_answer(vault, slug, "Q3", "ok", now=NOW),
        lambda: ops.question_drop(vault, slug, "Q3", "no", now=NOW),
        lambda: ops.understanding_set(vault, slug, "x"),
        lambda: ops.evidence_add(vault, slug, "a-note", "why"),
        lambda: ops.timeline_add(vault, slug, "research run", "d", "f", "q", now=NOW),
        lambda: ops.propose(vault, slug, "T", "Q?", [], [], now=NOW),
        lambda: experiments.plan(vault, slug, "x", "T", ["Q3"], BODY, now=NOW),
        lambda: experiments.start(vault, slug, "E2", now=NOW),
        lambda: experiments.finish(vault, slug, "E1", "r", "m", "o", now=NOW),
        lambda: experiments.abandon(vault, slug, "E2", "no", now=NOW),
        lambda: experiments.veto(vault, slug, "E2", "alex", now=NOW),
    ]
    for call in calls:
        with pytest.raises(LabError, match="bad thread slug"):
            call()
    assert _listing(vault) == before


@pytest.mark.parametrize("slug", ["..", "a/b", "", "Summary-Drift"])
@pytest.mark.parametrize(
    "verb",
    [
        ["show", "--json"],
        ["approve", "--author", "alex"],
        ["comment", "--on", "Q3", "--author", "alex", "--text", "x"],
        ["reply", "--on", "Q3", "--to", "1", "--text", "x"],
        ["propose-close", "--reason", "x"],
    ],
)
def test_cli_rejects_a_bad_slug(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], vault: Path, slug: str, verb: list[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["scoutctl", "lab", verb[0], slug, *verb[1:]])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert "bad thread slug" in capsys.readouterr().err


def test_a_good_slug_still_works_through_the_cli(vault: Path) -> None:
    result = CliRunner().invoke(cli.app, ["lab", "show", "summary-drift", "--json"])
    assert result.exit_code == 0, result.output
