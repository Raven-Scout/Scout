"""scoutctl setup: the terminal front end of setup (spec §4.3)."""

from __future__ import annotations

import io
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout import setup_wizard as sw
from scout.cli import app
from scout.scripts.bootstrap_auto import AutoAction, Plan
from scout.scripts.connector_detect import Detection, DetectStatus
from scout.scripts.engine_pointer import EnginePointer, read_pointer, write_pointer


class Scripted:
    """A Prompter that answers from a list; '' takes the default. Records everything said."""

    def __init__(self, answers, end=EOFError):
        self.answers = list(answers)
        self.said: list[str] = []
        self.end = end  # raised once the answers run out: Ctrl-D (EOFError) or Ctrl-C (KeyboardInterrupt)

    def say(self, text):
        self.said.append(text)

    def ask(self, question, default=""):
        self.said.append(question)
        if not self.answers:
            raise self.end("no more scripted answers")
        return self.answers.pop(0) or default

    def confirm(self, question, default):
        a = self.ask(question).lower()
        return default if a == "" else a.startswith("y")


DETECTIONS = {
    "slack": Detection("slack", DetectStatus.CONNECTED, ["user_slack_id"], "line"),
    "calendar": Detection("calendar", DetectStatus.CONNECTED, [], "line"),
    "gmail": Detection("gmail", DetectStatus.NEEDS_AUTH, [], "line"),
}


THIS_PYTHON = str(Path(sys.executable).absolute())
DEV_PYTHON = "/opt/dev/scout/plugin/engine/.venv/bin/python"


def _pointer(managed_by, python=THIS_PYTHON):
    root = str(Path(python).parent.parent.parent)
    return EnginePointer(
        version="0.15.0",
        engine_root=root,
        python=python,
        scoutctl=str(Path(python).parent / "scoutctl"),
        vault="/tmp/Scout",
        managed_by=managed_by,
        written_at="2026-10-01T00:00:00Z",
    )


def _deps(tmp_path, *, plan=AutoAction.INSTALL, manager=None, codes=None, pointer=None):
    calls: list[list[str]] = []
    codes = codes or {}

    def invoke(args):
        calls.append(args)
        return codes.get(args[0], 0)

    if pointer is None and manager is not None:
        pointer = _pointer(manager)
    deps = sw.SetupDeps(
        plan=lambda vault: Plan(plan, "test"),
        pointer=lambda: pointer,
        git_identity=lambda: ("Alex", "alex@example.com"),
        host_zone=lambda: "America/New_York",
        detect_connectors=lambda: DETECTIONS,
        list_uncovered=lambda: {"schema_version": 1, "servers": [], "error": None},
        draft=lambda server, taken: {"status": "error", "message": "unused"},
        invoke=invoke,
        add_custom=lambda definition, inputs: {"status": "applied", "message": "Live"},
        first_briefing_slot=lambda: "briefing-am",
    )
    return deps, calls


def _opts(tmp_path, **kw):
    return replace(sw.SetupOptions(vault=tmp_path / "Scout"), **kw)


def test_fresh_install_asks_and_calls_bootstrap_auto(tmp_path):
    deps, calls = _deps(tmp_path)
    # instance, name, email, keep tz, toggle (enter), slack id, per-session, daily, first run
    p = Scripted(["", "", "", "", "", "U0123", "", "20", "y"])
    assert sw.run_setup(_opts(tmp_path), p, deps) == 0
    auto = calls[0]
    assert auto[:6] == ["bootstrap", "auto", "--no-interactive", "--yes", "--managed-by", "claude-code"]
    assert auto[auto.index("--user-name") + 1] == "Alex"
    assert auto[auto.index("--user-email") + 1] == "alex@example.com"
    assert auto[auto.index("--connectors") + 1] == "calendar,slack"
    assert auto[auto.index("--user-slack-id") + 1] == "U0123"
    assert auto[auto.index("--max-budget") + 1] == "5.00"
    assert "--timezone" not in auto
    assert ["budget", "set", "--daily-usd", "20"] in calls
    assert ["schedule", "fire-now", "briefing-am"] in calls


def test_toggling_and_a_pinned_timezone(tmp_path):
    deps, calls = _deps(tmp_path)
    # instance, name, email, keep tz? n, zone (bad, then good),
    # toggle "x 99 1" (garbage + out-of-range + calendar off) then enter,
    # slack id, per-session, daily (blank), first run n
    p = Scripted(["", "", "", "n", "Mars/Olympus", "Europe/Berlin", "x 99 1", "", "U1", "", "", "n"])
    assert sw.run_setup(_opts(tmp_path), p, deps) == 0
    auto = calls[0]
    assert auto[auto.index("--connectors") + 1] == "slack"
    assert auto[auto.index("--timezone") + 1] == "Europe/Berlin"
    assert not any(c[0] == "budget" for c in calls)
    assert not any(c[0] == "schedule" for c in calls)
    assert any("isn't an IANA zone name" in s for s in p.said)


def test_existing_vault_upgrades_without_questions(tmp_path):
    deps, calls = _deps(tmp_path, plan=AutoAction.UPGRADE)
    p = Scripted([])
    assert sw.run_setup(_opts(tmp_path), p, deps) == 0
    assert calls == [["bootstrap", "auto", "--no-interactive", "--yes", "--managed-by", "preserve"]]
    assert any("upgrading" in s for s in p.said)


def test_explicit_managed_by_wins_on_upgrade(tmp_path):
    deps, calls = _deps(tmp_path, plan=AutoAction.UPGRADE)
    sw.run_setup(_opts(tmp_path, managed_by="install.sh"), Scripted([]), deps)
    assert calls[0][-1] == "install.sh"


def test_app_managed_engine_is_refused(tmp_path):
    deps, calls = _deps(tmp_path, manager="scout-app")
    p = Scripted([])
    assert sw.run_setup(_opts(tmp_path), p, deps) == 2
    assert calls == [] and any("Scout.app" in s for s in p.said)


def test_refused_plan_exits_2(tmp_path):
    deps, calls = _deps(tmp_path, plan=AutoAction.REFUSED)
    assert sw.run_setup(_opts(tmp_path), Scripted([]), deps) == 2
    assert calls == []


def test_headless_needs_name_and_email(tmp_path):
    deps, calls = _deps(tmp_path)
    p = sw.HeadlessPrompter()
    assert sw.run_setup(_opts(tmp_path, yes=True), p, deps) == 2
    assert calls == []


def test_headless_with_flags_asks_nothing(tmp_path):
    deps, calls = _deps(tmp_path)
    opts = _opts(
        tmp_path,
        yes=True,
        name="Alex",
        email="alex@example.com",
        connectors="slack,github",
        github_username="alex",
        github_repos="example-org/app",
        first_run=False,
    )
    assert sw.run_setup(opts, sw.HeadlessPrompter(), deps) == 0
    auto = calls[0]
    assert auto[auto.index("--connectors") + 1] == "github,slack"
    assert auto[auto.index("--github-repos") + 1] == "example-org/app"


def test_headless_without_connectors_enables_what_is_connected(tmp_path):
    deps, calls = _deps(tmp_path)
    opts = _opts(tmp_path, yes=True, name="Alex", email="alex@example.com", first_run=False)
    sw.run_setup(opts, sw.HeadlessPrompter(), deps)
    assert calls[0][calls[0].index("--connectors") + 1] == "calendar,slack"


def test_yes_without_an_explicit_first_run_flag_skips_the_first_run(tmp_path):
    """--yes with no --first-run/--no-first-run (opts.first_run stays None)
    must never prompt — it should default straight to "no", same as an
    explicit --no-first-run."""
    deps, calls = _deps(tmp_path)
    opts = _opts(tmp_path, yes=True, name="Alex", email="alex@example.com")
    assert sw.run_setup(opts, sw.HeadlessPrompter(), deps) == 0
    assert not any(c[0] == "schedule" for c in calls)


@pytest.mark.parametrize("end", [EOFError, KeyboardInterrupt])
def test_end_of_input_cancels_before_anything_is_written(tmp_path, end):
    deps, calls = _deps(tmp_path)
    p = Scripted(["", "Alex"], end=end)  # runs out at the email question
    assert sw.run_setup(_opts(tmp_path), p, deps) == 1
    assert calls == []
    assert sw.CANCELLED in p.said


@pytest.mark.parametrize("end", [EOFError, KeyboardInterrupt])
def test_end_of_input_at_an_add_server_prompt_cancels_before_anything_is_written(tmp_path, end):
    listing = {
        "schema_version": 1,
        "error": None,
        "servers": [
            {
                "name": "claude.ai Example Suite",
                "slug": "claude_ai_Example_Suite",
                "status": "connected",
                "evidence": "",
            }
        ],
    }
    drafted: list[str] = []
    deps, calls = _deps(tmp_path)
    deps = replace(deps, list_uncovered=lambda: listing, draft=lambda s, taken: drafted.append(s) or {})
    # instance, name, email, keep tz, toggle (enter), slack id -- then ends at "Add claude.ai Example Suite?"
    p = Scripted(["", "", "", "", "", "U1"], end=end)
    assert sw.run_setup(_opts(tmp_path), p, deps) == 1
    assert p.said[-2] == "  Add claude.ai Example Suite?"
    assert p.said[-1] == sw.CANCELLED
    assert calls == [] and drafted == []


def test_a_red_bootstrap_stops_before_budget_and_first_run(tmp_path):
    deps, calls = _deps(tmp_path, codes={"bootstrap": 2})
    p = Scripted(["", "", "", "", "", "U1", "", "20", "y"])
    assert sw.run_setup(_opts(tmp_path), p, deps) == 2
    assert [c[0] for c in calls] == ["bootstrap"]


def test_cli_without_a_terminal_and_without_yes_exits_2(monkeypatch, tmp_path):
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path / "Scout"))
    monkeypatch.setattr(sw.TtyPrompter, "open", classmethod(lambda cls: None))
    result = CliRunner().invoke(app, ["setup"])
    assert result.exit_code == 2
    assert "--yes" in result.output and "--name" in result.output and "--email" in result.output


def test_an_upgrade_needs_no_terminal(tmp_path):
    deps, calls = _deps(tmp_path, plan=AutoAction.UPGRADE)
    assert sw.run_setup(_opts(tmp_path), sw.NoTerminalPrompter(), deps) == 0
    assert calls[0][:2] == ["bootstrap", "auto"]


def test_a_first_install_without_a_terminal_lists_the_headless_flags(tmp_path, capsys):
    deps, calls = _deps(tmp_path)
    assert sw.run_setup(_opts(tmp_path), sw.NoTerminalPrompter(), deps) == 2
    assert sw.HEADLESS_FLAGS in capsys.readouterr().out
    assert calls == []


def test_no_terminal_prompter_cannot_confirm(tmp_path):
    with pytest.raises(sw.NoTerminal):
        sw.NoTerminalPrompter().confirm("Q", True)


def test_cli_upgrade_without_a_terminal_and_without_yes_proceeds(monkeypatch, tmp_path):
    deps, calls = _deps(tmp_path, plan=AutoAction.UPGRADE)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path / "Scout"))
    monkeypatch.setattr(sw.TtyPrompter, "open", classmethod(lambda cls: None))
    monkeypatch.setattr("scout.setup_wizard.default_deps", lambda claude_bin="": deps)
    result = CliRunner().invoke(app, ["setup"])
    assert result.exit_code == 0, result.output
    assert "--name" not in result.output
    assert calls[0][:2] == ["bootstrap", "auto"]


def test_invoke_scoutctl_returns_the_exit_code():
    assert sw.invoke_scoutctl(["connectors", "custom", "remove", "--help"]) == 0
    assert sw.invoke_scoutctl(["no-such-command"]) == 2


def test_cli_setup_yes_wires_flags_into_options_and_runs(monkeypatch, tmp_path):
    """The rest of `setup_cmd`'s body (the --vault override, building SetupOptions,
    and the final exit-code-from-run_setup path) isn't reached by the
    no-terminal test above. Stub default_deps so this runs end to end without
    touching a real vault or bootstrap auto."""
    vault_path = tmp_path / "MyScout"
    deps, calls = _deps(tmp_path)
    monkeypatch.setattr("scout.setup_wizard.default_deps", lambda claude_bin="": deps)

    result = CliRunner().invoke(
        app,
        [
            "setup",
            "--vault",
            str(vault_path),
            "--yes",
            "--name",
            "Alex",
            "--email",
            "alex@example.com",
            "--no-first-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert os.environ["SCOUT_DATA_DIR"] == str(vault_path)
    auto = calls[0]
    assert auto[:6] == ["bootstrap", "auto", "--no-interactive", "--yes", "--managed-by", "claude-code"]
    assert auto[auto.index("--user-name") + 1] == "Alex"
    assert auto[auto.index("--user-email") + 1] == "alex@example.com"


# ---------------------------------------------------------------------------
# Fix round 1 (code review): EOF at the first-run prompt isn't a cancel,
# non-2 failure codes from bootstrap auto still stop the flow, --claude-bin
# is forwarded to bootstrap auto.
# ---------------------------------------------------------------------------


def test_eof_at_first_run_prompt_is_treated_as_no_not_a_cancel(tmp_path):
    """Ctrl-D at "Run your first briefing now?" used to propagate as an
    uncaught EOFError (Click's "Aborted!", exit 1) even though bootstrap auto
    had already written the vault. It must be treated as "no", not a cancel:
    no CANCELLED message, exit 0, bootstrap's own return code."""
    deps, calls = _deps(tmp_path)
    # instance, name, email, keep tz, toggle enter, slack id, per-session, daily -- then EOF
    p = Scripted(["", "", "", "", "", "U1", "", "20"])
    assert sw.run_setup(_opts(tmp_path), p, deps) == 0
    assert sw.CANCELLED not in p.said
    assert [c[0] for c in calls] == ["bootstrap", "budget"]
    assert not any(c[0] == "schedule" for c in calls)
    assert any("first scheduled run" in s for s in p.said)


def test_no_terminal_at_first_run_prompt_is_treated_as_no(tmp_path, capsys):
    """No /dev/tty, no --yes, every answer given as a flag and no
    --first-run/--no-first-run: _gather asks nothing, bootstrap writes the
    vault, then the first-run confirm raised NoTerminal (a traceback after a
    successful install). It must read as "no", like end of input."""
    deps, calls = _deps(tmp_path)
    opts = _opts(
        tmp_path,
        instance_name="Scout",
        name="Alex",
        email="alex@example.com",
        timezone="Europe/Berlin",
        connectors="calendar",
        max_budget="5.00",
        daily_budget="20",
    )
    assert sw.run_setup(opts, sw.NoTerminalPrompter(), deps) == 0
    assert [c[0] for c in calls] == ["bootstrap", "budget"]
    assert not any(c[0] == "schedule" for c in calls)
    out = capsys.readouterr().out
    assert "first scheduled run" in out
    assert sw.HEADLESS_FLAGS not in out


def test_bootstrap_failure_code_other_than_2_still_stops_the_flow(tmp_path):
    """Only checking `code == 2` let other failure codes (e.g. 10 from a
    ConfigError) fall through to budget/first-run as if bootstrap had
    succeeded. Any code other than 0 (ok) or 1 (warn) must stop the flow and
    be returned as-is."""
    deps, calls = _deps(tmp_path, codes={"bootstrap": 10})
    p = Scripted(["", "", "", "", "", "U1", "", "20"])
    assert sw.run_setup(_opts(tmp_path), p, deps) == 10
    assert [c[0] for c in calls] == ["bootstrap"]


def test_claude_bin_is_forwarded_to_bootstrap_install(tmp_path):
    deps, calls = _deps(tmp_path)
    p = Scripted(["", "", "", "", "", "U1", "", "20", "n"])
    opts = _opts(tmp_path, claude_bin="/opt/homebrew/bin/claude")
    assert sw.run_setup(opts, p, deps) == 0
    auto = calls[0]
    assert auto[auto.index("--claude-bin") + 1] == "/opt/homebrew/bin/claude"


def test_claude_bin_is_forwarded_to_bootstrap_upgrade(tmp_path):
    deps, calls = _deps(tmp_path, plan=AutoAction.UPGRADE)
    opts = _opts(tmp_path, claude_bin="/opt/homebrew/bin/claude")
    assert sw.run_setup(opts, Scripted([]), deps) == 0
    upgrade = calls[0]
    assert upgrade[upgrade.index("--claude-bin") + 1] == "/opt/homebrew/bin/claude"


def test_claude_bin_omitted_from_both_argvs_when_blank(tmp_path):
    deps, calls = _deps(tmp_path, plan=AutoAction.UPGRADE)
    sw.run_setup(_opts(tmp_path), Scripted([]), deps)
    assert "--claude-bin" not in calls[0]


def test_cli_setup_forwards_claude_bin_flag(monkeypatch, tmp_path):
    vault_path = tmp_path / "MyScout"
    deps, calls = _deps(tmp_path)
    monkeypatch.setattr("scout.setup_wizard.default_deps", lambda claude_bin="": deps)

    result = CliRunner().invoke(
        app,
        [
            "setup",
            "--vault",
            str(vault_path),
            "--yes",
            "--name",
            "Alex",
            "--email",
            "alex@example.com",
            "--no-first-run",
            "--claude-bin",
            "/opt/homebrew/bin/claude",
        ],
    )
    assert result.exit_code == 0, result.output
    auto = calls[0]
    assert auto[auto.index("--claude-bin") + 1] == "/opt/homebrew/bin/claude"


# ---------------------------------------------------------------------------
# Extra coverage: no-briefing-slot message, _float_ok's ValueError branch,
# the email re-ask loop.
# ---------------------------------------------------------------------------


def test_first_run_with_no_briefing_slot_says_so(tmp_path):
    deps, calls = _deps(tmp_path)
    deps = replace(deps, first_briefing_slot=lambda: None)
    p = Scripted(["", "", "", "", "", "U1", "", "", "y"])
    assert sw.run_setup(_opts(tmp_path), p, deps) == 0
    assert any("No briefing slot" in s for s in p.said)
    assert not any(c[0] == "schedule" for c in calls)


def test_float_ok_rejects_non_numeric_text():
    assert sw._float_ok("not-a-number", allow_blank=False) is False
    assert sw._float_ok("not-a-number", allow_blank=True) is False
    assert sw._float_ok("", allow_blank=True) is True
    assert sw._float_ok("", allow_blank=False) is False
    assert sw._float_ok("5", allow_blank=False) is True
    assert sw._float_ok("0", allow_blank=False) is False


def test_budget_reask_on_non_numeric_input(tmp_path):
    deps, calls = _deps(tmp_path)
    # instance, name, email, keep tz, toggle enter, slack id, budget(bad) budget(good), daily, first run
    p = Scripted(["", "", "", "", "", "U1", "lots", "7.50", "", "n"])
    assert sw.run_setup(_opts(tmp_path), p, deps) == 0
    assert any("Enter an amount above 0." == s for s in p.said)
    auto = calls[0]
    assert auto[auto.index("--max-budget") + 1] == "7.50"


def test_email_reask_on_invalid_format(tmp_path):
    deps, calls = _deps(tmp_path)
    # instance, name, email(bad) email(good), keep tz, toggle enter, slack id, budget, daily, first run
    p = Scripted(["", "", "not-an-email", "alex@example.com", "", "", "U1", "", "", "n"])
    assert sw.run_setup(_opts(tmp_path), p, deps) == 0
    assert "That doesn't look like an email address." in p.said
    auto = calls[0]
    assert auto[auto.index("--user-email") + 1] == "alex@example.com"


def test_invoke_scoutctl_reraises_exception_without_exit_code(monkeypatch):
    class FakeCmd:
        def main(self, args, prog_name, standalone_mode):
            raise RuntimeError("boom")

    monkeypatch.setattr("typer.main.get_command", lambda app: FakeCmd())
    with pytest.raises(RuntimeError, match="boom"):
        sw.invoke_scoutctl(["whatever"])


# ---------------------------------------------------------------------------
# TtyPrompter.
#
# A real /dev/tty has independent input and output queues: writing a prompt
# doesn't disturb keystrokes the user already queued. io.StringIO is a single
# seekable buffer, so using one for both read and write would have ask()'s
# own write() overwrite the scripted input sitting at the same position
# (confirmed experimentally: it silently corrupts the answers). FakeTTY keeps
# the two apart, mirroring the real device, while still exposing exactly the
# write/flush/readline surface TtyPrompter uses.
# ---------------------------------------------------------------------------


class FakeTTY:
    def __init__(self, lines: list[str] | None = None) -> None:
        self._lines = list(lines or [])
        self._out = io.StringIO()

    def write(self, text: str) -> None:
        self._out.write(text)

    def flush(self) -> None:
        pass

    def readline(self) -> str:
        return self._lines.pop(0) if self._lines else ""

    @property
    def written(self) -> str:
        return self._out.getvalue()


def test_tty_prompter_say_writes_and_flushes():
    tty = FakeTTY()
    p = sw.TtyPrompter(tty)
    p.say("hello")
    assert tty.written == "hello\n"


def test_tty_prompter_ask_returns_default_on_blank_line():
    tty = FakeTTY(["\n"])
    p = sw.TtyPrompter(tty)
    assert p.ask("Q", "D") == "D"
    assert tty.written == "Q [D]: "


def test_tty_prompter_ask_strips_and_returns_the_typed_value():
    tty = FakeTTY(["hello \n"])
    p = sw.TtyPrompter(tty)
    assert p.ask("Q") == "hello"
    assert tty.written == "Q: "


def test_tty_prompter_ask_raises_eof_when_the_terminal_closes():
    tty = FakeTTY([])  # readline() returns "" immediately: EOF
    p = sw.TtyPrompter(tty)
    with pytest.raises(EOFError):
        p.ask("Q")


def test_tty_prompter_confirm_default_on_blank_answer():
    tty = FakeTTY(["\n"])
    p = sw.TtyPrompter(tty)
    assert p.confirm("Q", True) is True


def test_tty_prompter_confirm_recognises_yes_and_no():
    assert sw.TtyPrompter(FakeTTY(["yes\n"])).confirm("Q", False) is True
    assert sw.TtyPrompter(FakeTTY(["no\n"])).confirm("Q", True) is False


def test_tty_prompter_confirm_reasks_on_an_unrecognised_answer():
    tty = FakeTTY(["maybe\n", "n\n"])
    p = sw.TtyPrompter(tty)
    assert p.confirm("Q", True) is False
    # Asked twice: the unrecognised "maybe" didn't short-circuit the loop.
    assert tty.written.count("Q [Y/n]: ") == 2


def test_tty_prompter_open_works_on_a_real_non_seekable_pty(monkeypatch):
    """Regression for the bug where the old ``open("/dev/tty", "r+")`` built a
    buffered, SEEKABLE stream — a tty isn't seekable, so that raised
    io.UnsupportedOperation (an OSError subclass) on every real terminal, and
    TtyPrompter.open() always returned None. A regular temp file (or
    io.StringIO) IS seekable and would hide this exact bug, so this test
    drives a real pty's slave fd — the same kind of fd a real /dev/tty is —
    through os.openpty(), and proves both that open() succeeds and that the
    resulting prompter can actually read a line."""
    master_fd, slave_fd = os.openpty()
    monkeypatch.setattr(sw.os, "open", lambda path, flags: slave_fd)
    prompter = sw.TtyPrompter.open()
    try:
        assert isinstance(prompter, sw.TtyPrompter)
        os.write(master_fd, b"hello\n")
        assert prompter.ask("Q") == "hello"
    finally:
        if prompter is not None:
            prompter._tty.close()  # also closes the wrapped slave fd
        os.close(master_fd)


def test_tty_prompter_open_returns_none_when_dev_tty_cannot_open(monkeypatch):
    def fake_os_open(path, flags):
        raise OSError("no such device or address")

    monkeypatch.setattr(sw.os, "open", fake_os_open)
    assert sw.TtyPrompter.open() is None


def test_tty_prompter_open_closes_the_fd_when_wrapping_fails(monkeypatch):
    """If os.open() succeeds but wrapping the fd raises OSError, the fd must
    be closed rather than leaked."""
    master_fd, slave_fd = os.openpty()
    monkeypatch.setattr(sw.os, "open", lambda path, flags: slave_fd)
    monkeypatch.setattr(sw.io, "FileIO", lambda *a, **k: (_ for _ in ()).throw(OSError("wrap failed")))
    try:
        assert sw.TtyPrompter.open() is None
        with pytest.raises(OSError):
            os.fstat(slave_fd)  # the except branch closed it
    finally:
        os.close(master_fd)


# ---------------------------------------------------------------------------
# HeadlessPrompter — direct coverage of each method, including confirm()
# (never exercised through run_setup with --yes, since the flow short-
# circuits every confirm() call once opts.yes is True).
# ---------------------------------------------------------------------------


def test_headless_prompter_say_prints(capsys):
    sw.HeadlessPrompter().say("hello")
    assert capsys.readouterr().out == "hello\n"


def test_headless_prompter_ask_always_returns_the_default():
    p = sw.HeadlessPrompter()
    assert p.ask("Q") == ""
    assert p.ask("Q", "D") == "D"


def test_headless_prompter_confirm_always_returns_the_default():
    p = sw.HeadlessPrompter()
    assert p.confirm("Q", True) is True
    assert p.confirm("Q", False) is False


# ---------------------------------------------------------------------------
# _git_identity
# ---------------------------------------------------------------------------


def test_git_identity_reads_name_and_email_from_global_git_config(monkeypatch):
    def fake_run(args, **kwargs):
        class Result:
            stdout = {"user.name": "Alex\n", "user.email": "alex@example.com\n"}[args[-1]]

        return Result()

    monkeypatch.setattr(sw.subprocess, "run", fake_run)
    assert sw._git_identity() == ("Alex", "alex@example.com")


def test_git_identity_returns_blank_strings_on_oserror(monkeypatch):
    def fake_run(args, **kwargs):
        raise OSError("git not found")

    monkeypatch.setattr(sw.subprocess, "run", fake_run)
    assert sw._git_identity() == ("", "")


# ---------------------------------------------------------------------------
# default_deps — every closure, with the real collaborators monkeypatched so
# no test runs a real `claude` or touches ~/Scout.
# ---------------------------------------------------------------------------


def test_default_deps_wires_plan_git_identity_and_invoke():
    from scout.config import host_timezone_name
    from scout.scripts.bootstrap_auto import detect

    deps = sw.default_deps()
    assert deps.plan is detect
    assert deps.host_zone is host_timezone_name
    assert deps.invoke is sw.invoke_scoutctl
    assert deps.git_identity is sw._git_identity


def test_default_deps_pointer_reads_the_pointer(monkeypatch):
    from scout.scripts import engine_pointer

    ptr = _pointer("install.sh")
    monkeypatch.setattr(engine_pointer, "read_pointer", lambda home: ptr)
    assert sw.default_deps().pointer() is ptr


def test_default_deps_pointer_is_none_without_a_pointer(monkeypatch):
    from scout.scripts import engine_pointer

    monkeypatch.setattr(engine_pointer, "read_pointer", lambda home: None)
    assert sw.default_deps().pointer() is None


# ---------------------------------------------------------------------------
# P8: a re-run (install.sh passes --managed-by install.sh) must not change who
# manages an existing vault's engine. Real pointer files in a tmp HOME.
# ---------------------------------------------------------------------------


def _home_with_pointer(tmp_path, managed_by, python):
    home = tmp_path / "home"
    write_pointer(_pointer(managed_by, python), home=home)
    return home


def test_rerun_on_a_dev_managed_vault_leaves_the_manager_and_pointer(tmp_path):
    home = _home_with_pointer(tmp_path, "dev", DEV_PYTHON)
    before = (home / ".local/state/scout/engine.json").read_bytes()
    deps, calls = _deps(tmp_path, plan=AutoAction.UPGRADE)
    deps = replace(deps, pointer=lambda: read_pointer(home=home))
    p = Scripted([])
    assert sw.run_setup(_opts(tmp_path, managed_by="install.sh"), p, deps) == 0
    assert calls == []  # no bootstrap run from this engine: the pointer and launchd jobs stay put
    assert (home / ".local/state/scout/engine.json").read_bytes() == before
    said = "\n".join(p.said)
    assert "managed by dev" in said
    assert "/opt/dev/scout/plugin/engine/.venv/bin/scoutctl bootstrap upgrade" in said
    # The user-explicit way to hand the vault to this engine:
    assert f"{Path(THIS_PYTHON).parent / 'scoutctl'} bootstrap upgrade --managed-by install.sh" in said


def test_rerun_from_the_dev_engine_itself_keeps_dev_explicitly(tmp_path):
    home = _home_with_pointer(tmp_path, "dev", THIS_PYTHON)
    deps, calls = _deps(tmp_path, plan=AutoAction.UPGRADE)
    deps = replace(deps, pointer=lambda: read_pointer(home=home))
    assert sw.run_setup(_opts(tmp_path, managed_by="install.sh"), Scripted([]), deps) == 0
    argv = calls[0]
    assert argv[argv.index("--managed-by") + 1] == "dev"


def test_rerun_from_the_same_engine_with_an_unknown_pointer_records_install_sh(tmp_path):
    deps, calls = _deps(tmp_path, plan=AutoAction.UPGRADE, pointer=_pointer("unknown", THIS_PYTHON))
    assert sw.run_setup(_opts(tmp_path, managed_by="install.sh"), Scripted([]), deps) == 0
    argv = calls[0]
    assert argv[argv.index("--managed-by") + 1] == "install.sh"


def test_rerun_without_a_pointer_records_install_sh(tmp_path):
    deps, calls = _deps(tmp_path, plan=AutoAction.UPGRADE, pointer=None)
    assert sw.run_setup(_opts(tmp_path, managed_by="install.sh"), Scripted([]), deps) == 0
    argv = calls[0]
    assert argv[argv.index("--managed-by") + 1] == "install.sh"


def _cache_python(home, version):
    return str(home / ".claude/plugins/cache/scout-plugin/scout" / version / ".venv/bin/python")


@pytest.mark.parametrize("recorded", ["install.sh", "claude-code"])
def test_rerun_after_a_release_upgrades_and_keeps_the_label(tmp_path, monkeypatch, recorded):
    """install.sh runs the engine from a per-version plugin-cache venv, so after
    a release the pointer names the old version's python. That is still the
    same install: upgrade, and pass the recorded manager explicitly (preserve
    across interpreters would demote it to unknown)."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    home = _home_with_pointer(tmp_path, recorded, _cache_python(home, "0.15.0"))
    deps, calls = _deps(tmp_path, plan=AutoAction.UPGRADE)
    deps = replace(deps, pointer=lambda: read_pointer(home=home))
    assert sw.run_setup(_opts(tmp_path, managed_by="install.sh"), Scripted([]), deps) == 0
    argv = calls[0]
    assert argv[:2] == ["bootstrap", "auto"]
    assert argv[argv.index("--managed-by") + 1] == recorded


def test_rerun_over_an_unknown_pointer_in_the_plugin_cache_records_install_sh(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    deps, calls = _deps(tmp_path, plan=AutoAction.UPGRADE, pointer=_pointer("unknown", _cache_python(home, "0.15.0")))
    assert sw.run_setup(_opts(tmp_path, managed_by="install.sh"), Scripted([]), deps) == 0
    argv = calls[0]
    assert argv[argv.index("--managed-by") + 1] == "install.sh"


def test_rerun_over_an_unknown_pointer_on_a_dev_checkout_keeps_it(tmp_path, monkeypatch):
    """A plain `bootstrap upgrade` from a checkout records unknown; install.sh
    must not take that engine over."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    home = _home_with_pointer(tmp_path, "unknown", DEV_PYTHON)
    before = (home / ".local/state/scout/engine.json").read_bytes()
    deps, calls = _deps(tmp_path, plan=AutoAction.UPGRADE)
    deps = replace(deps, pointer=lambda: read_pointer(home=home))
    p = Scripted([])
    assert sw.run_setup(_opts(tmp_path, managed_by="install.sh"), p, deps) == 0
    assert calls == []
    assert (home / ".local/state/scout/engine.json").read_bytes() == before
    assert any("managed by unknown" in s for s in p.said)


def test_rerun_on_an_app_managed_vault_is_still_refused(tmp_path):
    deps, calls = _deps(tmp_path, plan=AutoAction.UPGRADE, pointer=_pointer("scout-app", DEV_PYTHON))
    p = Scripted([])
    assert sw.run_setup(_opts(tmp_path, managed_by="install.sh"), p, deps) == 2
    assert calls == [] and any("Scout.app" in s for s in p.said)


def test_fresh_install_records_install_sh_even_over_another_engines_pointer(tmp_path):
    deps, calls = _deps(tmp_path, pointer=_pointer("dev", DEV_PYTHON))
    opts = _opts(tmp_path, yes=True, name="Alex", email="alex@example.com", managed_by="install.sh")
    assert sw.run_setup(opts, sw.HeadlessPrompter(), deps) == 0
    argv = calls[0]
    assert argv[:2] == ["bootstrap", "auto"]
    assert argv[argv.index("--managed-by") + 1] == "install.sh"


def test_default_deps_detect_connectors_wires_detect_and_probes(monkeypatch):
    from scout.scripts import connector_detect, connector_probes

    fake_registry = {"example_suite": "probe"}
    monkeypatch.setattr(connector_detect, "run_claude_mcp_list", lambda claude: "fake mcp list output")
    monkeypatch.setattr(connector_probes, "resolve_registry", lambda: fake_registry)
    captured: dict[str, object] = {}

    def fake_detect(registry, *, mcp_list_output, run_bash):
        captured.update(registry=registry, mcp_list_output=mcp_list_output, run_bash=run_bash)
        return {"example_suite": "detection"}

    monkeypatch.setattr(connector_detect, "detect", fake_detect)
    deps = sw.default_deps()
    assert deps.detect_connectors() == {"example_suite": "detection"}
    assert captured["mcp_list_output"] == "fake mcp list output"
    assert captured["registry"] is fake_registry
    assert captured["run_bash"] is connector_detect.run_bash_probe


def test_default_deps_list_uncovered_wires_find_uncovered(monkeypatch, tmp_path):
    from scout.scripts import connector_detect, connector_probes, connector_uncovered

    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path / "Scout"))
    monkeypatch.setattr(connector_detect, "run_claude_mcp_list", lambda claude: "fake mcp list output")
    monkeypatch.setattr(connector_probes, "resolve_registry", lambda: {"r": 1})
    monkeypatch.setattr(connector_uncovered, "custom_connector_servers", lambda vault: {"servers of": vault})
    captured: dict[str, object] = {}

    def fake_find_uncovered(mcp_list_output, *, registry, custom_servers):
        captured.update(mcp_list_output=mcp_list_output, registry=registry, custom_servers=custom_servers)
        return {"schema_version": 1, "servers": [], "error": None}

    monkeypatch.setattr(connector_uncovered, "find_uncovered", fake_find_uncovered)
    deps = sw.default_deps()
    assert deps.list_uncovered() == {"schema_version": 1, "servers": [], "error": None}
    assert captured == {
        "mcp_list_output": "fake mcp list output",
        "registry": {"r": 1},
        "custom_servers": {"servers of": tmp_path / "Scout"},
    }


def test_default_deps_draft_wires_connector_draft(monkeypatch, tmp_path):
    from scout.scripts import connector_draft

    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path / "Scout"))
    captured: dict[str, object] = {}

    def fake_draft(server_name, *, plugin_root, vault, claude_bin, taken):
        captured.update(server_name=server_name, vault=vault, claude_bin=claude_bin, taken=taken)
        return {"status": "drafted"}

    monkeypatch.setattr(connector_draft, "draft", fake_draft)
    deps = sw.default_deps(claude_bin="/usr/bin/claude")
    assert deps.draft("example_suite", {"suite_mail"}) == {"status": "drafted"}
    assert captured == {
        "server_name": "example_suite",
        "vault": tmp_path / "Scout",
        "claude_bin": "/usr/bin/claude",
        "taken": {"suite_mail"},
    }


def test_default_deps_add_custom_wires_custom_connector_ops(monkeypatch, tmp_path):
    from scout.scripts import custom_connector_ops

    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path / "Scout"))
    captured: dict[str, object] = {}

    class FakeOutcome:
        def to_json(self):
            return {"status": "applied"}

    def fake_add(vault, definition, *, plugin_root, plugin_version, inputs):
        captured.update(
            vault=vault, definition=definition, plugin_root=plugin_root, plugin_version=plugin_version, inputs=inputs
        )
        return FakeOutcome()

    monkeypatch.setattr(custom_connector_ops, "add", fake_add)
    deps = sw.default_deps()
    assert deps.add_custom({"key": "x"}, {"token": "abc"}) == {"status": "applied"}
    assert captured["vault"] == tmp_path / "Scout"
    assert captured["definition"] == {"key": "x"}
    assert captured["inputs"] == {"token": "abc"}


def test_default_deps_first_briefing_slot_falls_back_to_the_packaged_default(monkeypatch, tmp_path):
    """No `.scout-state/schedule.yaml` in the vault: use the plugin default."""
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path / "Scout"))
    deps = sw.default_deps()
    # The packaged default's only briefing slots are morning-briefing and
    # weekend-briefing; sorted alphabetically, morning- comes first.
    assert deps.first_briefing_slot() == "morning-briefing"


def test_default_deps_first_briefing_slot_prefers_the_vault_schedule(monkeypatch, tmp_path):
    vault = tmp_path / "Scout"
    state = vault / ".scout-state"
    state.mkdir(parents=True)
    (state / "schedule.yaml").write_text(
        "schema_version: 1\n"
        "slots:\n"
        "  zzz-manual:\n"
        "    type: manual\n"
        "    runner: run-scout.sh\n"
        '    fires_at_local: "09:00"\n'
        "    weekdays: [Mon]\n"
        "    missed_window_hours: 1\n"
        "    on_miss: fire\n"
        "    cooldown_minutes: 5\n"
        "  aaa-briefing:\n"
        "    type: briefing\n"
        "    runner: run-scout.sh\n"
        '    fires_at_local: "08:00"\n'
        "    weekdays: [Mon]\n"
        "    missed_window_hours: 1\n"
        "    on_miss: fire\n"
        "    cooldown_minutes: 5\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SCOUT_DATA_DIR", str(vault))
    deps = sw.default_deps()
    assert deps.first_briefing_slot() == "aaa-briefing"


def test_default_deps_first_briefing_slot_is_none_without_any_briefing(monkeypatch, tmp_path):
    vault = tmp_path / "Scout"
    state = vault / ".scout-state"
    state.mkdir(parents=True)
    (state / "schedule.yaml").write_text(
        "schema_version: 1\n"
        "slots:\n"
        "  only-manual:\n"
        "    type: manual\n"
        "    runner: run-scout.sh\n"
        '    fires_at_local: "09:00"\n'
        "    weekdays: [Mon]\n"
        "    missed_window_hours: 1\n"
        "    on_miss: fire\n"
        "    cooldown_minutes: 5\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SCOUT_DATA_DIR", str(vault))
    deps = sw.default_deps()
    assert deps.first_briefing_slot() is None
