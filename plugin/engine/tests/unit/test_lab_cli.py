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
    shutil.copytree(FIXTURES / "summary-drift", paths.thread_dir(tmp_path, "summary-drift"))
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
    return paths.front_page(vault, "summary-drift").read_text(encoding="utf-8")


def test_show_json_matches_lab_thread(vault: Path) -> None:
    result = _run("show", "summary-drift", "--json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == json.loads(json.dumps(summary.lab_thread(vault, "summary-drift")))


def test_list_json_and_vault_option(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCOUT_DATA_DIR")
    result = _run("--vault", str(vault), "list", "--json")
    assert result.exit_code == 0, result.output
    doc = json.loads(result.stdout)
    assert [t["slug"] for t in doc["threads"]] == ["summary-drift"]


def test_comment_then_show_counts_unanswered(vault: Path) -> None:
    before = json.loads(_run("show", "summary-drift", "--json").stdout)["unanswered_comments"]
    result = _run("comment", "summary-drift", "--on", "Q3", "--author", "alex", "--text", "one more thing")
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "ok summary-drift comment Q3"
    after = json.loads(_run("show", "summary-drift", "--json").stdout)["unanswered_comments"]
    assert after == before + 1


def test_reply_marks_comment_answered(vault: Path) -> None:
    result = _run("reply", "summary-drift", "--on", "Q3", "--to", "2", "--text", "Will compare.")
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "ok summary-drift reply Q3"
    assert json.loads(_run("show", "summary-drift", "--json").stdout)["unanswered_comments"] == 0


def test_approve_requires_author(vault: Path) -> None:
    result = _run("approve", "summary-drift")
    assert result.exit_code != 0
    assert "--author" in result.output


@pytest.mark.parametrize("author", ["", "1bad", "alex\n", "a b", "scout", "Scout"])
def test_bad_or_reserved_author_exits_2(
    vault: Path, author: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    before = _front(vault)
    for argv in (
        ("pause", "summary-drift", "--author", author),
        ("comment", "summary-drift", "--author", author, "--text", "x"),
        ("veto", "summary-drift", "E2", "--author", author),
    ):
        code, _out, err = _main(monkeypatch, capsys, *argv)
        assert code == 2, (argv, err)
    assert _front(vault) == before


def test_question_add_prints_qid(vault: Path) -> None:
    result = _run("question", "add", "summary-drift", "--text", "Does it scale?", "--by", "alex")
    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines == ["ok summary-drift question Q8", "Q8"]
    assert "Q8" in _front(vault)


def test_lab_error_exit_code_2(
    vault: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    code, _out, err = _main(monkeypatch, capsys, "resume", "summary-drift", "--author", "alex")
    assert code == 2
    assert "cannot resume an active thread" in err


def test_owner_verb_pause_then_resume(vault: Path) -> None:
    assert (
        _run("pause", "summary-drift", "--author", "alex", "--reason", "away").stdout.strip()
        == "ok summary-drift pause"
    )
    assert json.loads(_run("show", "summary-drift", "--json").stdout)["status"] == "paused"
    assert _run("resume", "summary-drift", "--author", "alex").exit_code == 0


def test_propose_with_evidence(vault: Path) -> None:
    result = _run(
        "propose", "--slug", "probes", "--title", "Probes", "--question", "Do probes transfer?",
        "--open", "Which layer?", "--open", "How many samples?",
        "--evidence", "summary-drift-notes=background = with equals",
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "ok probes propose"
    text = paths.front_page(vault, "probes").read_text(encoding="utf-8")
    assert "[[summary-drift-notes]] — background = with equals" in text


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
    f.write_text("Fresh understanding, with [[summary-drift-notes]].\n", encoding="utf-8")
    assert (
        _run("understanding", "set", "summary-drift", "--file", str(f)).stdout.strip()
        == "ok summary-drift understanding"
    )
    assert "Fresh understanding" in _front(vault)
    missing = tmp_path / "nope.txt"
    code, _out, err = _main(monkeypatch, capsys, "understanding", "set", "summary-drift", "--file", str(missing))
    assert code == 2
    assert str(missing) in err


def test_question_answer_drop_evidence_timeline(vault: Path) -> None:
    assert (
        _run("question", "answer", "summary-drift", "Q3", "--note", "It does.").stdout.strip()
        == "ok summary-drift answer Q3"
    )
    assert (
        _run("question", "drop", "summary-drift", "Q5", "--reason", "moot").stdout.strip() == "ok summary-drift drop Q5"
    )
    assert (
        _run("evidence", "add", "summary-drift", "--target", "regeneration-cost-notes", "--why", "again").stdout.strip()
        == "ok summary-drift evidence"
    )
    result = _run(
        "timeline",
        "add",
        "summary-drift",
        "--tag",
        "research run",
        "--did",
        "read",
        "--found",
        "stuff",
        "--questions",
        "none",
    )
    assert result.stdout.strip() == "ok summary-drift timeline"


def test_experiment_plan_start_abandon_flow(
    vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    body = tmp_path / "body.md"
    body.write_text("## Hypothesis\nH.\n\n## Method\nM.\n\n## Data\nD.\n", encoding="utf-8")
    result = _run(
        "experiment", "plan", "summary-drift", "--short", "again", "--title", "Again", "--question", "Q5",
        "--file", str(body), "--cap", "0.5",
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "ok summary-drift plan E3"
    code, _out, err = _main(monkeypatch, capsys, "experiment", "start", "summary-drift", "E3")
    assert code == 2 and "cannot start yet" in err  # the planned notice period has not elapsed
    result = _run("experiment", "abandon", "summary-drift", "E3", "--reason", "changed my mind")
    assert result.stdout.strip() == "ok summary-drift abandon E3"


def test_veto_planned_experiment(vault: Path) -> None:
    result = _run("veto", "summary-drift", "E2", "--author", "alex")
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "ok summary-drift veto E2"


def test_private_thread_hidden_from_delegate(
    vault: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    front = paths.front_page(vault, "summary-drift")
    front.write_text(front.read_text(encoding="utf-8").replace("visibility: normal", "visibility: private", 1))
    owner = json.loads(_run("list", "--json").stdout)
    assert [t["slug"] for t in owner["threads"]] == ["summary-drift"]
    result = _run("list", "--json", "--reader", "elsewhere")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["threads"] == []
    shown = _main(monkeypatch, capsys, "show", "summary-drift", "--reader", "elsewhere")
    missing = _main(monkeypatch, capsys, "show", "nonexistent", "--reader", "elsewhere")
    assert shown[0] == missing[0] == 2
    assert shown[2].replace("summary-drift", "X") == missing[2].replace("nonexistent", "X")


def test_propose_close_prints_ok_and_writes_the_comment(vault: Path) -> None:
    result = _run("propose-close", "summary-drift", "--reason", "all questions answered")
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "ok summary-drift propose-close"
    assert "Proposing to close: all questions answered" in _front(vault)


# --- detached runs (spec §6.4) ---------------------------------------------------------------------------------


def _detached_record(vault: Path, outcome: dict[str, object] | None = None) -> Path:
    import os

    from scout.lab import runs

    path = runs.record_path(vault, "summary-drift", "E2", os.environ)
    started, deadline = "2026-11-21T10:00:00+00:00", "2026-11-21T22:00:00+00:00"
    runs.write(path, runs.RunRecord(started, deadline, os.getpid(), outcome=outcome))
    return path


def test_collect_without_a_broker_reads_the_record(
    vault: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _detached_record(vault, {"status": "ran", "exit": 0, "stdout_tail": "result\n", "stderr_tail": ""})
    code, out, err = _main(monkeypatch, capsys, "experiment", "collect", "summary-drift", "E2")
    assert code == 0 and "result" in out
    assert err.rstrip("\n").splitlines()[-1] == "experiment ran: exit 0"
    _detached_record(vault, {"status": "not run", "reason": "stopped by the owner"})
    code, _, err = _main(monkeypatch, capsys, "experiment", "collect", "summary-drift", "E2")
    assert code == 4 and err.rstrip("\n").splitlines()[-1] == "experiment not run: stopped by the owner"


def test_collect_in_a_scheduled_run_without_a_broker_is_not_run(
    vault: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _detached_record(vault, {"status": "ran", "exit": 0})
    monkeypatch.setenv("SCOUT_MODE", "research")
    code, _, err = _main(monkeypatch, capsys, "experiment", "collect", "summary-drift", "E2")
    assert code == 4 and err.rstrip("\n").splitlines()[-1].startswith("experiment not run: ")


def test_stop_signals_the_supervisor(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os
    import signal

    from scout.lab import runner as lab_runner
    from scout.lab import runs

    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(runs, "_kill", lambda pid, sig: sent.append((pid, sig)))
    path = _detached_record(vault)
    with lab_runner._run_lock(vault, "summary-drift", "E2", os.environ):  # the "supervisor" holds its run lock
        result = _run("experiment", "stop", "summary-drift", "E2", "--author", "alex")
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "ok summary-drift stop E2"
    assert (os.getpid(), signal.SIGTERM) in sent
    assert runs.stop_path(path).is_file()  # what makes the supervisor's SIGTERM the owner's stop


def test_stop_without_a_live_supervisor_is_refused(
    vault: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from scout.lab import runs

    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(runs, "_kill", lambda pid, sig: sent.append((pid, sig)))
    code, _, err = _main(monkeypatch, capsys, "experiment", "stop", "summary-drift", "E2", "--author", "alex")
    assert code == 2 and "no detached run" in err
    _detached_record(vault)  # no outcome, but nothing holds the lock: gone
    code, _, err = _main(monkeypatch, capsys, "experiment", "stop", "summary-drift", "E2", "--author", "alex")
    assert code == 2 and "isn't running" in err and sent == []
    code, _, err = _main(monkeypatch, capsys, "experiment", "stop", "summary-drift", "E2", "--author", "scout")
    assert code == 2


def test_stop_that_cant_write_its_request_is_refused_cleanly(
    vault: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import os

    from scout.lab import runner as lab_runner
    from scout.lab import runs

    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(runs, "_kill", lambda pid, sig: sent.append((pid, sig)))
    path = _detached_record(vault)
    runs.stop_path(path).mkdir()  # the stop request can't be written there
    with lab_runner._run_lock(vault, "summary-drift", "E2", os.environ):
        code, _, err = _main(monkeypatch, capsys, "experiment", "stop", "summary-drift", "E2", "--author", "alex")
    assert code == 2 and "cannot write the stop request" in err and "Traceback" not in err
    assert all(sig == 0 for _, sig in sent)  # nothing was stopped


def test_a_stop_whose_signal_fails_is_pending(
    vault: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The stop request is written, so the supervisor stops at its next poll even when SIGTERM can't reach it."""
    import os

    from scout.lab import runner as lab_runner
    from scout.lab import runs

    def kill(pid: int, sig: int) -> None:
        if sig:
            raise PermissionError("not permitted")

    monkeypatch.setattr(runs, "_kill", kill)
    path = _detached_record(vault)
    with lab_runner._run_lock(vault, "summary-drift", "E2", os.environ):
        code, out, err = _main(monkeypatch, capsys, "experiment", "stop", "summary-drift", "E2", "--author", "alex")
    assert code == 0, err
    assert out.strip() == "ok summary-drift stop E2"
    assert "the stop is pending: the supervisor will stop at its next check" in err
    assert runs.stop_path(path).is_file()


def test_run_detach_reaches_the_client(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.sandbox import broker

    seen: dict[str, object] = {}

    def fake_client(slug: str, eid: str, argv: list[str], env: object = None, **kw: object) -> int:
        seen.update(kw)
        return 0

    monkeypatch.setattr(broker, "client_experiment", fake_client)
    result = _run("experiment", "run", "summary-drift", "E2", "--detach", "--", "./run.sh")
    assert result.exit_code == 0, result.output
    assert seen == {"timeout_s": None, "detach": True}
