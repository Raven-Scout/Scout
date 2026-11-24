"""The lab CLI's plain-text output, the owner verbs not covered elsewhere, and its refusals."""

from __future__ import annotations

import datetime as dt
import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout import cli
from scout.lab import experiments, ops, paths

FIXTURES = Path(__file__).parent.parent / "fixtures" / "lab"
SLUG = "summary-drift"
runner = CliRunner()


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    shutil.copytree(FIXTURES / SLUG, paths.thread_dir(tmp_path, SLUG))
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SCOUT_READER", raising=False)
    monkeypatch.delenv("SCOUT_MODE", raising=False)
    return tmp_path


def _run(*args: str) -> object:
    return runner.invoke(cli.app, ["lab", *args])


def _main(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    monkeypatch.setattr("sys.argv", ["scoutctl", "lab", *args])
    code = 0
    try:
        cli.main()
    except SystemExit as exc:
        code = int(exc.code or 0)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_list_and_show_print_text(vault: Path) -> None:
    listed = _run("list")
    assert listed.exit_code == 0, listed.output
    assert listed.stdout == f"{SLUG}\tactive\tKeeping cached summaries honest\n"
    shown = _run("show", SLUG)
    assert shown.exit_code == 0, shown.output
    lines = shown.stdout.splitlines()
    assert lines[0] == f"{SLUG}\tactive\tKeeping cached summaries honest"
    assert lines[1].startswith("question: What is the cheapest signal")
    assert lines[2].startswith("open 2 · answered 2 · experiments 2 · unanswered comments ")


def test_show_prints_each_problem_with_its_line(vault: Path) -> None:
    front = paths.front_page(vault, SLUG)
    front.write_text(front.read_text("utf-8") + "\n## Stray section\nnot part of the format\n", "utf-8")
    shown = _run("show", SLUG)
    assert shown.exit_code == 0, shown.output
    assert any(line.startswith("problem (line ") for line in shown.stdout.splitlines()), shown.stdout


def test_a_reader_that_reads_nothing_sees_no_threads(vault: Path) -> None:
    result = _run("list", "--json", "--reader", "agent:person:alex@example.org")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["threads"] == []


def test_lint_refuses_a_file_outside_the_vault(
    vault: Path, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:  # fmt: skip
    outside = tmp_path_factory.mktemp("elsewhere") / "x.md"
    outside.write_text("# x\n", encoding="utf-8")
    code, _out, err = _main(monkeypatch, capsys, "lint", str(outside))
    assert code == 2 and "is not inside the vault" in err


def test_reject_close_and_reopen(vault: Path, tmp_path: Path) -> None:
    assert _run("close", SLUG, "--author", "alex", "--reason", "done").stdout.strip() == f"ok {SLUG} close"
    assert _run("reopen", SLUG, "--author", "alex").stdout.strip() == f"ok {SLUG} reopen"
    proposed = _run(
        "propose", "--slug", "side-notes", "--title", "Side notes", "--question", "Are side notes read?",
        "--open", "Who reads them?",
    )  # fmt: skip
    assert proposed.exit_code == 0, proposed.output
    assert _run("reject", "side-notes", "--author", "alex").stdout.strip() == "ok side-notes reject"
    assert json.loads(_run("show", "side-notes", "--json").stdout)["status"] == "rejected"


def test_experiment_start_and_finish(vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    late = dt.datetime(2026, 11, 21, 13, 30, tzinfo=dt.UTC)
    ops.reply(vault, SLUG, "Q3", 2, "Will report the false alarms.", now=late)  # the owner comment holding E2
    real_start = experiments.start
    monkeypatch.setattr(experiments, "start", lambda v, s, e: real_start(v, s, e, now=late))
    started = _run("experiment", "start", SLUG, "E2")
    assert started.exit_code == 0, started.output
    assert started.stdout.strip() == f"ok {SLUG} start E2"
    result, means = tmp_path / "result.md", tmp_path / "means.md"
    result.write_text("The section-hash check flagged every stale sample.\n", encoding="utf-8")
    means.write_text("A section hash works as a staleness signal for Q3.\n", encoding="utf-8")
    finished = _run(
        "experiment", "finish", SLUG, "E2", "--result-file", str(result), "--means-file", str(means),
        "--one-line", "flagged every stale sample",
    )  # fmt: skip
    assert finished.exit_code == 0, finished.output
    assert finished.stdout.strip() == f"ok {SLUG} finish E2"
    note = paths.experiment_note(vault, SLUG, "E2")
    assert note is not None and "status: done" in note.read_text("utf-8")


def test_experiment_run_in_a_scheduled_run_without_a_broker_is_not_run(
    vault: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SCOUT_MODE", "research")
    code, _out, err = _main(monkeypatch, capsys, "experiment", "run", SLUG, "E2", "--", "sh", "run.sh")
    assert code == 4
    assert err.rstrip().splitlines()[-1] == "experiment not run: experiments need the run broker"


def test_experiment_run_by_the_owner_is_checked_like_the_brokers(
    vault: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The owner's own path checks the request as the broker would: a vault with no commit is refused (exit 2)."""
    code, _out, err = _main(monkeypatch, capsys, "experiment", "run", SLUG, "E2", "--timeout", "5", "--", "sh", "x.sh")
    assert code == 2
    assert err.rstrip().splitlines()[-1] == "experiment refused: the vault has no commit to run an experiment from"
