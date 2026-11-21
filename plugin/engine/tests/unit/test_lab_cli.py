from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout import cli
from scout.lab import paths, summary

FIXTURES = Path(__file__).parent.parent / "fixtures" / "lab"
runner = CliRunner()


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    shutil.copytree(FIXTURES / "monitors", paths.thread_dir(tmp_path, "monitors"))
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SCOUT_READER", raising=False)
    monkeypatch.delenv("SCOUT_MODE", raising=False)
    return tmp_path


def _run(*args: str) -> object:
    return runner.invoke(cli.app, ["lab", *args])


def _main(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    """Run through ``cli.main`` (which maps ``LabError`` to exit 2); returns (code, stdout, stderr)."""
    monkeypatch.setattr("sys.argv", ["scoutctl", "lab", *args])
    code = 0
    try:
        cli.main()
    except SystemExit as exc:
        code = int(exc.code or 0)
    out = capsys.readouterr()
    return code, out.out, out.err


def _front(vault: Path) -> str:
    return paths.front_page(vault, "monitors").read_text(encoding="utf-8")


def test_show_json_matches_lab_thread(vault: Path) -> None:
    result = _run("show", "monitors", "--json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == json.loads(json.dumps(summary.lab_thread(vault, "monitors")))


def test_list_json_and_vault_option(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCOUT_DATA_DIR")
    result = _run("--vault", str(vault), "list", "--json")
    assert result.exit_code == 0, result.output
    doc = json.loads(result.stdout)
    assert [t["slug"] for t in doc["threads"]] == ["monitors"]


def test_comment_then_show_counts_unanswered(vault: Path) -> None:
    before = json.loads(_run("show", "monitors", "--json").stdout)["unanswered_comments"]
    result = _run("comment", "monitors", "--on", "Q3", "--author", "alex", "--text", "one more thing")
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "ok monitors comment Q3"
    after = json.loads(_run("show", "monitors", "--json").stdout)["unanswered_comments"]
    assert after == before + 1


def test_reply_marks_comment_answered(vault: Path) -> None:
    result = _run("reply", "monitors", "--on", "Q3", "--to", "2", "--text", "Will compare.")
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "ok monitors reply Q3"
    assert json.loads(_run("show", "monitors", "--json").stdout)["unanswered_comments"] == 0


def test_approve_requires_author(vault: Path) -> None:
    result = _run("approve", "monitors")
    assert result.exit_code != 0
    assert "--author" in result.output


@pytest.mark.parametrize("author", ["", "1bad", "alex\n", "a b", "scout", "Scout"])
def test_bad_or_reserved_author_exits_2(
    vault: Path, author: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    before = _front(vault)
    for argv in (
        ("pause", "monitors", "--author", author),
        ("comment", "monitors", "--author", author, "--text", "x"),
        ("veto", "monitors", "E2", "--author", author),
    ):
        code, _out, err = _main(monkeypatch, capsys, *argv)
        assert code == 2, (argv, err)
    assert _front(vault) == before


def test_question_add_prints_qid(vault: Path) -> None:
    result = _run("question", "add", "monitors", "--text", "Does it scale?", "--by", "alex")
    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines == ["ok monitors question Q8", "Q8"]
    assert "Q8" in _front(vault)


def test_lab_error_exit_code_2(
    vault: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    code, _out, err = _main(monkeypatch, capsys, "resume", "monitors", "--author", "alex")
    assert code == 2
    assert "cannot resume an active thread" in err


def test_owner_verb_pause_then_resume(vault: Path) -> None:
    assert _run("pause", "monitors", "--author", "alex", "--reason", "away").stdout.strip() == "ok monitors pause"
    assert json.loads(_run("show", "monitors", "--json").stdout)["status"] == "paused"
    assert _run("resume", "monitors", "--author", "alex").exit_code == 0


def test_propose_with_evidence(vault: Path) -> None:
    result = _run(
        "propose", "--slug", "probes", "--title", "Probes", "--question", "Do probes transfer?",
        "--open", "Which layer?", "--open", "How many samples?",
        "--evidence", "sandbagging-and-monitor-evasion=background = with equals",
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "ok probes propose"
    text = paths.front_page(vault, "probes").read_text(encoding="utf-8")
    assert "[[sandbagging-and-monitor-evasion]] — background = with equals" in text


def test_evidence_without_equals_is_an_error(
    vault: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _out, err = _main(
        monkeypatch, capsys, "propose", "--slug", "probes", "--title", "Probes", "--question", "Q?",
        "--open", "x", "--evidence", "no-equals-here",
    )  # fmt: skip
    assert code == 2
    assert "TARGET=WHY" in err
    assert not paths.thread_dir(vault, "probes").exists()


def test_understanding_set_and_missing_file(
    vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    f = tmp_path / "u.txt"
    f.write_text("Fresh understanding, with [[sandbagging-and-monitor-evasion]].\n", encoding="utf-8")
    assert _run("understanding", "set", "monitors", "--file", str(f)).stdout.strip() == "ok monitors understanding"
    assert "Fresh understanding" in _front(vault)
    missing = tmp_path / "nope.txt"
    code, _out, err = _main(monkeypatch, capsys, "understanding", "set", "monitors", "--file", str(missing))
    assert code == 2
    assert str(missing) in err


def test_question_answer_drop_evidence_timeline(vault: Path) -> None:
    assert _run("question", "answer", "monitors", "Q3", "--note", "It does.").stdout.strip() == "ok monitors answer Q3"
    assert _run("question", "drop", "monitors", "Q5", "--reason", "moot").stdout.strip() == "ok monitors drop Q5"
    assert (
        _run(
            "evidence", "add", "monitors", "--target", "probe-monitor-false-positives-and-cascades", "--why", "again"
        ).stdout.strip()
        == "ok monitors evidence"
    )
    result = _run(
        "timeline",
        "add",
        "monitors",
        "--tag",
        "research run",
        "--did",
        "read",
        "--found",
        "stuff",
        "--questions",
        "none",
    )
    assert result.stdout.strip() == "ok monitors timeline"


def test_experiment_plan_start_abandon_flow(
    vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    body = tmp_path / "body.md"
    body.write_text("## Hypothesis\nH.\n\n## Method\nM.\n\n## Data\nD.\n", encoding="utf-8")
    result = _run(
        "experiment", "plan", "monitors", "--short", "again", "--title", "Again", "--question", "Q5",
        "--file", str(body), "--cap", "0.5",
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "ok monitors plan E3"
    code, _out, err = _main(monkeypatch, capsys, "experiment", "start", "monitors", "E3")
    assert code == 2 and "cannot start yet" in err  # the planned notice period has not elapsed
    result = _run("experiment", "abandon", "monitors", "E3", "--reason", "changed my mind")
    assert result.stdout.strip() == "ok monitors abandon E3"


def test_veto_planned_experiment(vault: Path) -> None:
    result = _run("veto", "monitors", "E2", "--author", "alex")
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "ok monitors veto E2"


def test_private_thread_hidden_from_delegate(
    vault: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    front = paths.front_page(vault, "monitors")
    front.write_text(front.read_text(encoding="utf-8").replace("visibility: normal", "visibility: private", 1))
    owner = json.loads(_run("list", "--json").stdout)
    assert [t["slug"] for t in owner["threads"]] == ["monitors"]
    result = _run("list", "--json", "--reader", "elsewhere")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["threads"] == []
    shown = _main(monkeypatch, capsys, "show", "monitors", "--reader", "elsewhere")
    missing = _main(monkeypatch, capsys, "show", "nonexistent", "--reader", "elsewhere")
    assert shown[0] == missing[0] == 2
    assert shown[2].replace("monitors", "X") == missing[2].replace("nonexistent", "X")
