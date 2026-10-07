"""scoutctl setup: the terminal front end of setup (spec §4.3)."""

from __future__ import annotations

import io
import os
from dataclasses import replace

import pytest
from typer.testing import CliRunner

from scout import setup_wizard as sw
from scout.cli import app
from scout.scripts.bootstrap_auto import AutoAction, Plan
from scout.scripts.connector_detect import Detection, DetectStatus


class Scripted:
    """A Prompter that answers from a list; '' takes the default. Records everything said."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.said: list[str] = []

    def say(self, text):
        self.said.append(text)

    def ask(self, question, default=""):
        self.said.append(question)
        if not self.answers:
            raise EOFError("no more scripted answers")
        return self.answers.pop(0) or default

    def confirm(self, question, default):
        a = self.ask(question).lower()
        return default if a == "" else a.startswith("y")


DETECTIONS = {
    "slack": Detection("slack", DetectStatus.CONNECTED, ["user_slack_id"], "line"),
    "calendar": Detection("calendar", DetectStatus.CONNECTED, [], "line"),
    "gmail": Detection("gmail", DetectStatus.NEEDS_AUTH, [], "line"),
}


def _deps(tmp_path, *, plan=AutoAction.INSTALL, manager=None, codes=None):
    calls: list[list[str]] = []
    codes = codes or {}

    def invoke(args):
        calls.append(args)
        return codes.get(args[0], 0)

    deps = sw.SetupDeps(
        plan=lambda vault: Plan(plan, "test"),
        pointer_manager=lambda: manager,
        git_identity=lambda: ("Alex", "alex@example.com"),
        host_zone=lambda: "America/New_York",
        detect_connectors=lambda: DETECTIONS,
        list_uncovered=lambda: {"schema_version": 1, "servers": [], "error": None},
        draft=lambda server: {"status": "error", "message": "unused"},
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


def test_end_of_input_cancels_before_anything_is_written(tmp_path):
    deps, calls = _deps(tmp_path)
    p = Scripted(["", "Alex"])  # runs out at the email question
    assert sw.run_setup(_opts(tmp_path), p, deps) == 1
    assert calls == []
    assert sw.CANCELLED in p.said


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


def test_tty_prompter_open_succeeds_when_dev_tty_opens(monkeypatch, tmp_path):
    import builtins

    fake_tty = tmp_path / "tty"
    fake_tty.write_text("", encoding="utf-8")
    real_open = builtins.open

    def fake_open(path, mode="r", *args, **kwargs):
        if path == "/dev/tty":
            return real_open(fake_tty, mode, *args, **kwargs)
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", fake_open)
    prompter = sw.TtyPrompter.open()
    assert isinstance(prompter, sw.TtyPrompter)


def test_tty_prompter_open_returns_none_when_dev_tty_cannot_open(monkeypatch):
    import builtins

    def fake_open(*args, **kwargs):
        raise OSError("no such device or address")

    monkeypatch.setattr(builtins, "open", fake_open)
    assert sw.TtyPrompter.open() is None


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


def test_default_deps_pointer_manager_reads_the_pointer(monkeypatch):
    from scout.scripts import engine_pointer

    class FakePointer:
        managed_by = "install.sh"

    monkeypatch.setattr(engine_pointer, "read_pointer", lambda home: FakePointer())
    assert sw.default_deps().pointer_manager() == "install.sh"


def test_default_deps_pointer_manager_is_none_without_a_pointer(monkeypatch):
    from scout.scripts import engine_pointer

    monkeypatch.setattr(engine_pointer, "read_pointer", lambda home: None)
    assert sw.default_deps().pointer_manager() is None


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


def test_default_deps_list_uncovered_wires_find_uncovered(monkeypatch):
    from scout.scripts import connector_detect, connector_probes, connector_uncovered

    monkeypatch.setattr(connector_detect, "run_claude_mcp_list", lambda claude: "fake mcp list output")
    monkeypatch.setattr(connector_probes, "resolve_registry", lambda: {"r": 1})
    captured: dict[str, object] = {}

    def fake_find_uncovered(mcp_list_output, *, registry):
        captured.update(mcp_list_output=mcp_list_output, registry=registry)
        return {"schema_version": 1, "servers": [], "error": None}

    monkeypatch.setattr(connector_uncovered, "find_uncovered", fake_find_uncovered)
    deps = sw.default_deps()
    assert deps.list_uncovered() == {"schema_version": 1, "servers": [], "error": None}
    assert captured == {"mcp_list_output": "fake mcp list output", "registry": {"r": 1}}


def test_default_deps_draft_wires_connector_draft(monkeypatch, tmp_path):
    from scout.scripts import connector_draft

    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path / "Scout"))
    captured: dict[str, object] = {}

    def fake_draft(server_name, *, plugin_root, vault, claude_bin):
        captured.update(server_name=server_name, plugin_root=plugin_root, vault=vault, claude_bin=claude_bin)
        return {"status": "drafted"}

    monkeypatch.setattr(connector_draft, "draft", fake_draft)
    deps = sw.default_deps(claude_bin="/usr/bin/claude")
    assert deps.draft("example_suite") == {"status": "drafted"}
    assert captured["server_name"] == "example_suite"
    assert captured["vault"] == tmp_path / "Scout"
    assert captured["claude_bin"] == "/usr/bin/claude"


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
