"""kb-read-gate: a scheduled run reads the KB only through ``scoutctl kb`` (hard-rule audit row 2, #453)."""

from __future__ import annotations

import io
import json
import os
import subprocess
from pathlib import Path

import pytest

from scout.hooks import kb_read_gate as gate
from scout.kb.raw_read import raw_kb_read, search_reads_kb
from tests.unit._prefilter_root import with_prefilter

PLUGIN = Path(__file__).parent.parent.parent.parent
RUN = {"SCOUT_MODE": "morning-briefing", "SCOUT_READER": "autonomous"}


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    v = tmp_path / "vault"
    (v / "knowledge-base" / "people").mkdir(parents=True)
    (v / "knowledge-base" / "ontology").mkdir()
    (v / "action-items" / "context").mkdir(parents=True)
    (v / "scout-config.yaml").write_text("user: {}\n")
    (v / "knowledge-base" / "people.md").write_text("# People\n")
    monkeypatch.setenv("SCOUT_DATA_DIR", str(v))
    return v.resolve()


def _payload(tool: str, tool_input: dict, cwd: Path, transcript: Path | None = None) -> io.StringIO:
    body = {"tool_name": tool, "tool_input": tool_input, "cwd": str(cwd), "session_id": "s1"}
    if transcript is not None:
        body["transcript_path"] = str(transcript)
    return io.StringIO(json.dumps(body))


def _decision(vault: Path, tool: str, tool_input: dict, *, cwd: Path | None = None, env=RUN, transcript=None):
    return gate.run(stdin=_payload(tool, tool_input, cwd or vault, transcript), env=env)


def _denied(out) -> str:
    assert out is not None, "expected a deny"
    spec = out["hookSpecificOutput"]
    assert spec["hookEventName"] == "PreToolUse" and spec["permissionDecision"] == "deny"
    assert spec["permissionDecisionReason"].startswith(gate.MARK)
    return spec["permissionDecisionReason"]


# --- the shared classifier ---------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "cat knowledge-base/people.md",
        "cat knowledge-base.md",
        'echo "--- people ---" && cat knowledge-base/people.md',
        "ls action-items/context/",
        "find knowledge-base -name '*.md'",
        "git show HEAD:knowledge-base/people.md",
        "cd knowledge-base && grep -r Priya .",
        "xargs cat < list.txt knowledge-base/x.md",
    ],
)
def test_raw_reads_are_flagged(command):
    assert raw_kb_read(command)


@pytest.mark.parametrize(
    "command",
    [
        "scripts/scoutctl kb read knowledge-base/people.md",
        "scripts/scoutctl kb files knowledge-base/people | head -20",
        "python3 scripts/recurring-task-status.py --date 2026-10-29 --json",
        'git add knowledge-base/people.md && git commit -m "update knowledge-base/people.md"',
        "ls action-items/",
        "cat .scout-cache/session-context.json",
    ],
)
def test_engine_scripts_and_scoutctl_are_not_flagged(command):
    assert not raw_kb_read(command)


def test_a_shell_already_in_the_kb_flags_any_read():
    assert raw_kb_read("ls", in_kb=True)
    assert not raw_kb_read("cd /tmp && ls", in_kb=True)


def test_search_classifier_matches_the_eval_rule():
    assert search_reads_kb("Grep", {"pattern": "x", "path": "knowledge-base"})
    assert search_reads_kb("Grep", {"pattern": "x"})
    assert not search_reads_kb("Grep", {"pattern": "x", "glob": "action-items/*.md"})
    assert search_reads_kb("Glob", {"pattern": "**/*.md"})
    assert not search_reads_kb("Glob", {"pattern": "action-items/*.md"})


# --- blocked -------------------------------------------------------------------------


def test_run_denies_a_shell_read_and_names_scoutctl_kb(vault):
    reason = _denied(_decision(vault, "Bash", {"command": "cat knowledge-base/people.md"}))
    assert "scoutctl kb" in reason and "kb read <path>" in reason


def test_run_denies_a_shell_read_from_a_cwd_inside_the_kb(vault):
    _denied(_decision(vault, "Bash", {"command": "ls"}, cwd=vault / "knowledge-base" / "people"))


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Grep", {"pattern": "Priya", "path": "knowledge-base"}),
        ("Grep", {"pattern": "Priya"}),  # the whole vault
        ("Grep", {"pattern": "Priya", "path": "."}),
        ("Grep", {"pattern": "Priya", "glob": "knowledge-base/**/*.md"}),
        ("Glob", {"pattern": "**/*.md"}),
        ("Glob", {"pattern": "*.md", "path": "action-items/context"}),
        ("Glob", {"pattern": "knowledge-base/people/*.md"}),
        ("Grep", {"pattern": "Priya", "path": "knowledge-base.md"}),  # the KB's index
    ],
)
def test_run_denies_a_search_that_reaches_the_kb(vault, tool, tool_input):
    assert "search other folders" in _denied(_decision(vault, tool, tool_input))


def test_run_denies_a_search_from_a_folder_above_the_vault(vault):
    _denied(_decision(vault, "Grep", {"pattern": "Priya", "path": str(vault.parent)}))


AGENT = {"SCOUT_READER": "agent:person:priya@example.com"}


@pytest.mark.parametrize("env", [AGENT, {"SCOUT_READER": ""}, {"SCOUT_READER": "Owner"}])
@pytest.mark.parametrize("rel", ["knowledge-base/people.md", "knowledge-base.md"])
def test_a_reader_kb_shows_nothing_is_denied_a_kb_read(vault, env, rel):
    # #453 v1: scoutctl kb shows an agent:* reader (and a malformed one, agent:invalid) nothing.
    note = str(vault / rel)
    assert "scoutctl kb" in _denied(_decision(vault, "Read", {"file_path": note}, env=env))


# --- allowed -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Bash", {"command": "scripts/scoutctl kb search Priya"}),
        ("Bash", {"command": "scripts/scoutctl kb read knowledge-base/people.md"}),
        ("Bash", {"command": "git add knowledge-base/people.md && git commit -m 'kb: people'"}),
        ("Bash", {"command": "python3 scripts/recurring-task-status.py --json"}),
        ("Bash", {"command": "ls action-items/"}),
        ("Grep", {"pattern": "PROJ-1234", "path": "action-items", "glob": "action-items-*.md"}),
        ("Grep", {"pattern": "PROJ-1234", "glob": "action-items/*.md"}),
        ("Glob", {"pattern": "action-items/*.md"}),
        ("Read", {"file_path": "SKILL.md"}),
        # Read stays allowed in a run: the kb-read-label PostToolUse hook labels it (Jordan, 2026-10-29).
        ("Read", {"file_path": "knowledge-base/people.md"}),
        ("Read", {"file_path": "knowledge-base/personal/task.md"}),
        ("Read", {"file_path": "action-items/context/ROLL.md"}),
        ("Read", {"file_path": "knowledge-base/ontology/schema.yaml"}),
        ("Edit", {"file_path": "knowledge-base/people.md", "old_string": "a", "new_string": "b"}),
    ],
)
def test_run_allows_calls_that_stay_off_the_kb_or_go_through_scoutctl(vault, tool, tool_input):
    assert _decision(vault, tool, tool_input) is None


def test_a_search_outside_the_vault_is_allowed(vault, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    assert _decision(vault, "Grep", {"pattern": "x", "path": str(elsewhere)}) is None


# --- a run may not change the env that marks it a run, or its lock (#595) --------------


@pytest.mark.parametrize(
    "command",
    [
        "env -u SCOUT_MODE -u SCOUT_READER git commit -m notes",
        "SCOUT_MODE= SCOUT_READER=owner git commit -m notes",
        "unset SCOUT_MODE; scripts/git-safe-commit.sh notes notes/a.md",
        "env -i PATH=/usr/bin git commit -m notes",
    ],
)
def test_run_denies_a_command_that_changes_its_run_env(vault, command):
    reason = _denied(_decision(vault, "Bash", {"command": command}))
    assert "SCOUT_MODE" in reason and "owner" in reason
    assert "lock" not in reason and "PID" not in reason


@pytest.mark.parametrize("command", ["nohup git commit -m x", "( git commit -m x & )", "osascript -e 'x'"])
def test_run_denies_a_command_that_detaches(vault, command):
    reason = _denied(_decision(vault, "Bash", {"command": command}))
    assert "foreground" in reason


@pytest.mark.parametrize("command", ['echo "$SCOUT_MODE ${SCOUT_READER:-}"', "printenv SCOUT_MODE", "env | grep SCOUT"])
def test_run_may_read_its_run_env(vault, command):
    assert _decision(vault, "Bash", {"command": command}) is None


# --- a run may not point HOME elsewhere: the gate expands ~ and $HOME with the run's own -------------------


def _owner_state() -> str:
    return str(Path.home() / ".local" / "state")


@pytest.mark.parametrize(
    "command",
    [
        # The gate read `~` as the run's HOME, so these wrote the owner's stores unseen (review of #792).
        "HOME={state}/scout ; echo x >> ~/run-limits.json",
        "export HOME={state} ; cp /dev/null ~/scout/run-limits.json",
        "HOME={state}/scout/run-brokers sh -c 'echo x > $HOME/lab-vetoes.jsonl'",
        "env HOME={state}/scout python3 -c \"open('x','w')\"",
        "unset HOME; echo x > ~/x",
        "for HOME in {state}/scout; do echo x > ~/run-limits.json; done",
        # review of #800: each of these changes `~`, verified in zsh -f with a run's options
        "typeset H\\OME=$PWD/h; git commit -m msg",
        "export HO''ME=$PWD/h; git commit -m msg",
        "export HO\\ME=$PWD/h; git commit -m msg",
        "printf -v HO''ME %s $PWD/h; git commit -m msg",
        "read HO''ME <<< $PWD/h; git commit -m msg",
        "unset HO''ME",
        "IFS= read -r HOME <<< $PWD/h; git commit -m msg",
        "HOME+=/.claude; echo x >> ~/settings.json",
        "HOME[1,-1]=$PWD/h; git commit -m msg",
        ": ${{HOME::=$PWD/h}}; git commit -m msg",
        "echo ${{HOME::=/x}}",
        ": ${{HOME:=/x}}",
        "select HOME in $PWD/h; do break; done <<< 1; git commit -m msg",
        "nocorrect export HOME=$PWD/h; git commit -m msg",
        "- export HOME=$PWD/h; git commit -m msg",
        "noglob HOME=/x true",
        # a name built at run time
        "v=HO; v+=ME; : ${{(P)v::=$PWD/h}}; git commit -m msg",
        "v=HO; v+=ME; export $v=$PWD/h; git commit -m msg",
        'v=HO; v+=ME; typeset "$v=$PWD/h"; git commit -m msg',
        "v=HO; v+=ME; read $v <<< $PWD/h; git commit -m msg",
        'v=HO; v+=ME; eval "$v=$PWD/h"; git commit -m msg',
        # review 2 of #800: a name built in part, or by a glob, which the shell may expand to HOME
        "read HOM? <<< $PWD/h; git commit -m msg",
        "export HOM?=.claude; echo x >> ~/settings.json",
        "v=OME; export H$v=$PWD/h; git commit -m msg",
        "v=OME; read H$v <<< /x; echo x >> ~/settings.json",
        "v=OME; printf -v H$v /x; echo x >> ~/settings.json",
        'v=OME; typeset "H${{v}}=/x"; echo x >> ~/settings.json',
        'v=OME; eval "H${{v}}=/x"; echo x >> ~/settings.json',
        "v=HOME; : ${{(P)${{v}}::=/x}}; echo x >> ~/settings.json",
        "read -d '' HOME <<< /x",
        # review 3 of #800: verified to skip the commit hook in zsh
        "\\read HOM? <<< h; git commit -qm m1",
        "print -v HOM? h; git commit -qm m2",
        "print -v HOME /p/h; git commit -m m",
        "zstyle :x y /p/h; zstyle -s :x y HOM?; git commit -m m",
        "zformat -f HOM? /p/h; git commit -m m",
        "strftime -s HOM? %s; git commit -m m",
        "repeat 1 read HOM? <<< /p/h; git commit -m m",
        "pr''intf -v HOM? /p/h; git commit -m m",
        "\\typeset H$v=/p/h; git commit -m m",
        "exec {{HOME}}>/dev/null; echo x >> ~/settings.json",
    ],
)
def test_run_denies_a_command_that_points_home_elsewhere(vault, command):
    reason = _denied(_decision(vault, "Bash", {"command": command.format(state=_owner_state())}))
    assert "HOME" in reason and "SCOUT_MODE" not in reason
    assert not (vault / ".scout-logs").exists() or not _events(vault), "a HOME change isn't a hook skip"


@pytest.mark.parametrize(
    "command",
    [
        "HOME=$HOME python3 scripts/clock-status.py",
        'HOME="$HOME" ls notes',
        "echo $HOME",
        "ls ~/Documents",
        "export HOME",
        "HOME=~ ls",
        "HOME=$HOME/ ls",
        'echo "HOME is $HOME"',
        "read -r HOME_DIR <<< x",
        'while IFS= read -r line; do echo "$line"; done < notes.txt',
        "read -t $T ans",
        'read -p "$prompt" ans',
        "read -r -d '' body <<< x",
        "printf -v out '%s' x",
        'getopts "ab:" opt',
        "let i=$i+1",
        "for f in $HOME/docs/*; do echo $f; done",
        'echo "${HOME=}"',
    ],
)
def test_run_may_keep_or_read_its_home(vault, command):
    assert _decision(vault, "Bash", {"command": command}) is None


def test_owner_session_may_set_home(vault):
    assert _decision(vault, "Bash", {"command": "HOME=/tmp/x ls"}, env={}) is None


@pytest.mark.parametrize("env", [{}, {"SCOUT_READER": "owner"}])
def test_owner_session_may_change_the_run_env(vault, env):
    assert _decision(vault, "Bash", {"command": "env -u SCOUT_MODE git commit -m x"}, env=env) is None


# --- a run may not skip or redirect git's hooks (#607) ---------------------------------


def _events(vault: Path) -> list[dict]:
    rows: list[dict] = []
    for path in sorted((vault / ".scout-logs").glob("schedule-events-*.jsonl")):
        rows += [json.loads(line) for line in path.read_text().splitlines()]
    return rows


@pytest.mark.parametrize(
    ("command", "form"),
    [
        ("git commit --no-verify -m x", "--no-verify"),
        ("git commit -n -m x", "-n"),
        ("git push --no-verify", "--no-verify"),
        ("git -c core.hooksPath=/dev/null commit -m x", "-c core.hooksPath"),
        ("git --config-env=core.hooksPath=D commit -m x", "--config-env core.hooksPath"),
        ("git config core.hooksPath /dev/null", "git config core.hooksPath"),
        ("GIT_CONFIG_COUNT=1 git commit -m x", "GIT_CONFIG_*"),
        ("XDG_CONFIG_HOME=./x git commit -m x", "XDG_CONFIG_HOME"),
        ("export XDG_CONFIG_HOME=./x; git commit -m x", "XDG_CONFIG_HOME"),
        ("XDG_CONFIG_HOME+=/x; git commit -m x", "XDG_CONFIG_HOME"),
        (": ${XDG_CONFIG_HOME:=./x}; git commit -m x", "XDG_CONFIG_HOME"),
        ("IFS= read -r XDG_CONFIG_HOME <<< ./x; git commit -m x", "XDG_CONFIG_HOME"),
        ("git commit-tree abc -m x", "commit-tree"),
        ("git update-ref refs/heads/main abc", "update-ref"),
        ("rm .git/hooks/pre-commit", ".git/hooks"),
    ],
)
def test_run_denies_a_hook_skip_names_the_form_and_records_an_event(vault, command, form):
    reason = _denied(_decision(vault, "Bash", {"command": command}))
    assert form in reason and "fix" in reason.lower() and "git-safe-commit.sh" in reason
    assert "plain `git commit`" in reason, "the helper can't commit in an engine clone: the deny covers both places"
    for word in ("lock", "PID", "process", "ancestry", "live", "host"):
        assert word not in reason, f"the deny doesn't explain how a run is told apart: {word}"
    (row,) = _events(vault)
    assert row["kind"] == "kb_read_gate.hook_skip" and row["source"] == "hook:kb-read-gate"
    assert row["payload"] == {"form": form, "mode": RUN["SCOUT_MODE"]}


def test_the_event_never_carries_the_command(vault):
    _denied(_decision(vault, "Bash", {"command": "git commit --no-verify -m 'private words about Priya'"}))
    (row,) = _events(vault)
    assert "Priya" not in json.dumps(row)


def test_the_event_names_the_reader_when_the_run_has_no_mode(vault):
    _denied(_decision(vault, "Bash", {"command": "git commit --no-verify"}, env={"SCOUT_READER": "autonomous"}))
    assert _events(vault)[0]["payload"] == {"form": "--no-verify", "mode": "autonomous"}


def test_the_deny_stands_when_the_event_cannot_be_written(vault):
    (vault / ".scout-logs").write_text("a file where the log folder belongs\n")
    reason = _denied(_decision(vault, "Bash", {"command": "git commit --no-verify"}))
    assert "--no-verify" in reason


def test_env_and_detach_denies_keep_their_messages_and_record_no_hook_skip(vault):
    assert "SCOUT_MODE" in _denied(_decision(vault, "Bash", {"command": "unset SCOUT_MODE"}))
    assert "foreground" in _denied(_decision(vault, "Bash", {"command": "nohup ls"}))
    assert _events(vault) == []


@pytest.mark.parametrize(
    "command",
    [
        "git log -n 5",
        "git show HEAD",
        'git commit -m "why --no-verify is wrong"',
        "git commit -F - <<'EOF'\ngit commit --no-verify\nEOF",
        "grep -rn -- --no-verify phases/",
        'git config user.email "alex@example.com"',
        "scripts/git-safe-commit.sh 'x' notes/a.md",
        "cat .git/hooks/pre-commit",
        "cat < .git/config",
        "python3 x.py < .git/hooks/pre-commit",
        'grep core <<< ".git/config"',
    ],
)
def test_run_allows_what_only_mentions_a_hook_skip(vault, command):
    assert _decision(vault, "Bash", {"command": command}) is None
    assert _events(vault) == []


@pytest.mark.parametrize("env", [{}, {"SCOUT_READER": "owner"}, {"SCOUT_MODE": ""}])
@pytest.mark.parametrize(
    "command",
    ["git commit --no-verify", "git -c core.hooksPath=/dev/null commit", "git commit-tree abc", "rm .git/hooks/x"],
)
def test_owner_session_may_skip_its_own_hooks(vault, env, command):
    assert _decision(vault, "Bash", {"command": command}, env=env) is None
    assert _events(vault) == []


# --- owner and non-vault sessions -----------------------------------------------------


@pytest.mark.parametrize("env", [{}, {"SCOUT_READER": "owner"}, {"SCOUT_MODE": ""}])
def test_owner_session_is_never_gated(vault, env):
    note = str(vault / "knowledge-base" / "people.md")
    assert _decision(vault, "Bash", {"command": f"cat {note}"}, env=env) is None
    assert _decision(vault, "Read", {"file_path": note}, env=env) is None
    assert gate.run(stdin=io.StringIO("not json"), env=env) is None


@pytest.mark.parametrize("env", [{"SCOUT_READER": ""}, {"SCOUT_READER": "Owner"}, {"SCOUT_READER": "autonomous"}])
def test_any_reader_but_exactly_owner_is_a_run(vault, env):
    _denied(_decision(vault, "Bash", {"command": "cat knowledge-base/people.md"}, env=env))


def test_run_outside_any_vault_is_not_gated(tmp_path, monkeypatch):
    monkeypatch.delenv("SCOUT_DATA_DIR", raising=False)
    repo = tmp_path / "other-repo"
    (repo / "knowledge-base").mkdir(parents=True)
    out = gate.run(stdin=_payload("Bash", {"command": "cat knowledge-base/x.md"}, repo), env=RUN)
    assert out is None


# --- malformed input -----------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "[]",
        '{"tool_name": "Bash"}',
        '{"tool_name": "Bash", "tool_input": {"command": 3}}',
        '{"tool_name": "Read", "tool_input": {}}',
        '{"tool_name": 7, "tool_input": {}}',
    ],
)
def test_malformed_payload_fails_closed_in_a_run(vault, raw):
    reason = _denied(gate.run(stdin=io.StringIO(raw), env=RUN))
    assert "could not check" in reason


def test_a_gate_error_fails_closed_in_a_run(vault, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("bug")

    monkeypatch.setattr(gate, "raw_kb_read", boom)
    _denied(_decision(vault, "Bash", {"command": "cat knowledge-base/people.md"}))


def test_main_prints_the_decision(vault, monkeypatch, capsys):
    for k, v in RUN.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr("sys.stdin", _payload("Bash", {"command": "cat knowledge-base/people.md"}, vault))
    assert gate.main() == 0
    assert json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecision"] == "deny"
    monkeypatch.setattr("sys.stdin", _payload("Bash", {"command": "ls action-items"}, vault))
    assert gate.main() == 0
    assert capsys.readouterr().out == ""


# --- hooks.json wiring: the shell short-circuit ----------------------------------------


def _entry(matcher: str = "Grep|Glob|Bash") -> dict:
    hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text())["hooks"]["PreToolUse"]
    (entry,) = [h for h in hooks if "kb-read-gate" in h["hooks"][0]["command"] and h["matcher"] == matcher]
    return entry


def _sh(
    env: dict, payload: str, cwd: Path, root: Path = PLUGIN, matcher: str = "Grep|Glob|Bash"
) -> subprocess.CompletedProcess[str]:
    clean = {k: v for k, v in os.environ.items() if k not in ("SCOUT_MODE", "SCOUT_READER", "SCOUT_DATA_DIR")}
    full = {**clean, **env, "CLAUDE_PLUGIN_ROOT": str(with_prefilter(root))}
    cmd = _entry(matcher)["hooks"][0]["command"]
    return subprocess.run(
        ["sh", "-c", cmd], env=full, cwd=cwd, input=payload, capture_output=True, text=True, timeout=60
    )


def test_matchers_are_exact_tool_lists():
    # Letters and `|` only: Claude Code matches them as exact lists, so MultiEdit or ReadMcpResource never hit.
    assert _entry()["matcher"] == "Grep|Glob|Bash" and _entry("Read")["matcher"] == "Read"


def test_a_runs_read_exits_in_the_shell_without_python(vault, tmp_path):
    payload = _payload("Read", {"file_path": str(vault / "knowledge-base" / "people.md")}, vault).getvalue()
    for env in (RUN, {"SCOUT_MODE": "morning-briefing"}, {}):
        res = _sh({**env, "SCOUT_DATA_DIR": str(vault)}, payload, vault, root=tmp_path / "missing", matcher="Read")
        assert res.returncode == 0 and res.stdout == "" and res.stderr == ""


def test_an_agent_readers_kb_read_is_denied_through_the_real_cli(vault):
    payload = _payload("Read", {"file_path": str(vault / "knowledge-base" / "people.md")}, vault).getvalue()
    res = _sh({**AGENT, "SCOUT_DATA_DIR": str(vault)}, payload, vault, matcher="Read")
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_owner_session_exits_in_the_shell_without_python(vault, tmp_path):
    payload = _payload("Bash", {"command": "cat knowledge-base/people.md"}, vault).getvalue()
    res = _sh({}, payload, vault, root=tmp_path / "missing")
    assert res.returncode == 0 and res.stdout == "" and res.stderr == ""


def test_run_call_naming_no_kb_path_exits_in_the_shell_without_python(vault, tmp_path):
    payload = _payload("Bash", {"command": "ls action-items"}, vault).getvalue()
    res = _sh({**RUN, "SCOUT_DATA_DIR": str(vault)}, payload, vault, root=tmp_path / "missing")
    assert res.returncode == 0 and res.stdout == "" and res.stderr == ""


def test_run_kb_read_is_denied_through_the_real_cli(vault):
    payload = _payload("Bash", {"command": "cat knowledge-base/people.md"}, vault).getvalue()
    res = _sh({**RUN, "SCOUT_DATA_DIR": str(vault)}, payload, vault)
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_run_fails_closed_when_scoutctl_cannot_run(vault, tmp_path):
    payload = _payload("Bash", {"command": "cat knowledge-base/people.md"}, vault).getvalue()
    res = _sh({**RUN, "SCOUT_DATA_DIR": str(vault)}, payload, vault, root=tmp_path / "missing")
    assert res.returncode == 2 and gate.MARK in res.stderr


def test_run_grep_always_reaches_python(vault, tmp_path):
    # Grep with no path names no KB word, but searches the whole vault: the shell can't tell, so Python decides.
    payload = _payload("Grep", {"pattern": "Priya"}, vault).getvalue()
    res = _sh({**RUN, "SCOUT_DATA_DIR": str(vault)}, payload, vault, root=tmp_path / "missing")
    assert res.returncode == 2


@pytest.mark.parametrize(
    "command",
    [
        "env -u SCOUT_MODE git commit -m x",
        "env -i git commit -m x",
        "unset SCOUT_READER",
        "nohup git commit -m x",
        "git commit -m x &",
        "(git commit -m x &)",
        "setsid git commit",
        "osascript -e x",
        "open -a Terminal x.command",
        "echo x | at now",
        "tmux new -d x",
        "ls notes\nnohup git commit -m x",
        "ls notes\n\tsetsid git commit",
    ],
)
def test_a_run_env_change_or_detach_reaches_python_from_the_shell(vault, tmp_path, command):
    # No KB word in the payload, so only the run-escape patterns can send it to Python (here: missing, so exit 2).
    payload = _payload("Bash", {"command": command}, vault).getvalue()
    res = _sh({**RUN, "SCOUT_DATA_DIR": str(vault)}, payload, vault, root=tmp_path / "missing")
    assert res.returncode == 2


@pytest.mark.parametrize(
    "command",
    [
        "HOME=/tmp/x scripts/scoutctl status",
        "export HOME=/tmp/x",
        "unset HOME",
        "read HOME",
        "printf -v HOME %s x",
        # quoted or escaped: matched once the shell stage has removed quotes and backslashes (review of #800)
        "typeset H\\OME=/tmp/x; git commit -m msg",
        "export HO''ME=/tmp/x; git commit -m msg",
        "unset HO''ME",
        "export SCOUT_MO''DE=x; git commit -m msg",
        "unset SCOUT_REA''DER",
        # a name built at run time names no HOME at all
        "export $v=/tmp/x; git commit -m msg",
        'typeset "$v=/tmp/x"; git commit -m msg',
        "read $v <<< /tmp/x; git commit -m msg",
        'printf -v "$v" %s x',
        ": ${(P)v::=/tmp/x}",
        # a name built by a glob or in part: the builtin's name, anywhere in the command, sends it on
        "read HOM? <<< /tmp/x; git commit -m msg",
        "IFS= read -r H$v <<< /tmp/x",
        "v=OME; for H$v in /x; do :; done",
        "(( H$v = 1 ))",
        # the builtin's own name quoted or escaped, or behind a quoted prefix value (review 3 of #800)
        '"read" HOM? <<< /tmp/x; git commit -m msg',
        "r''ead HOM? <<< /tmp/x",
        "\\read HOM? <<< /tmp/x",
        'ex""port H$v=/tmp/x',
        'X="a b" read HOM? <<< /tmp/x',
        "ls\n'read' HOM? <<< /tmp/x",
        "\\typeset H$v=/tmp/x",
        "e''xport HOM?=x",
        "pr''intf -v HOM? /tmp/x",
        "print -v HOM? h; git commit -qm m2",
        "zstyle -s :x y HOM?",
        "zformat -f HOM? /tmp/x",
        "strftime -s HOM? %s",
        "repeat 1 read HOM? <<< /tmp/x",
        "zparseopts -A HOM? a",
        # the shell stage doesn't look for command position, so a builtin's name used as a plain word goes on too;
        # Python allows these (about 1.8% of replayed run calls newly reach it)
        "scripts/scoutctl kb read projects/widgets/widgets.md",
        "git log --format=export -1",
        'echo "read the notes"',
    ],
)
def test_a_home_change_reaches_python_from_the_shell(vault, tmp_path, command):
    # No KB word and no other trigger: only the HOME, run-env or built-name rules send it to Python (here: missing,
    # so exit 2).
    payload = _payload("Bash", {"command": command}, vault).getvalue()
    res = _sh({**RUN, "SCOUT_DATA_DIR": str(vault)}, payload, vault, root=tmp_path / "missing")
    assert res.returncode == 2


def test_a_home_change_is_denied_through_the_real_cli(vault):
    payload = _payload("Bash", {"command": "export HOME=/tmp/x ; echo x >> ~/run-limits.json"}, vault).getvalue()
    res = _sh({**RUN, "SCOUT_DATA_DIR": str(vault)}, payload, vault)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny" and "HOME" in out["permissionDecisionReason"]


def test_a_run_env_change_is_denied_through_the_real_cli(vault):
    payload = _payload("Bash", {"command": "env -u SCOUT_MODE git commit -m x"}, vault).getvalue()
    res = _sh({**RUN, "SCOUT_DATA_DIR": str(vault)}, payload, vault)
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize(
    "command",
    [
        "git add notes/a.md && git commit -m x 2>&1",
        "scripts/git-safe-commit.sh msg notes/a.md >&2",
        "ls notes",
        "git status --short",
        'echo "reading the notes"',  # a builtin's name only as part of a word
        "scripts/scoutctl health --json",
    ],
)
def test_ordinary_run_commands_still_exit_in_the_shell(vault, tmp_path, command):
    payload = _payload("Bash", {"command": command}, vault).getvalue()
    res = _sh({**RUN, "SCOUT_DATA_DIR": str(vault)}, payload, vault, root=tmp_path / "missing")
    assert res.returncode == 0 and res.stdout == "" and res.stderr == ""


def test_a_word_only_in_the_description_exits_in_the_shell(vault, tmp_path):
    tool_input = {"command": "ls notes", "description": "Open the script at env; nohup & screen"}
    payload = _payload("Bash", tool_input, vault).getvalue()
    res = _sh({**RUN, "SCOUT_DATA_DIR": str(vault)}, payload, vault, root=tmp_path / "missing")
    assert res.returncode == 0 and res.stdout == "" and res.stderr == ""


@pytest.mark.parametrize(
    "command",
    [
        "git commit --no-verify -m x",
        "git push --no-verify",
        "git commit --no-veri -m x",
        "git commit -n -m x",
        "git commit -nm x",
        "git commit -an -m x",
        "git commit -m x -n",
        'git commit -m "a; b" -n',
        "git commit -m 'one;two' -n -- f.md",
        'git commit -m "a | b" -n',
        "git commit -m \"$(cat <<'EOF'\nfirst; second\nEOF\n)\" -n",
        "git am -n a.patch",
        'git am "a;b.patch" -n',
        "git commit \\\n  -n -m x",
        "git commit \\\n  --no-verify",
        'git commit "-n" -m x',
        "git-commit -n",
        "git -c core.hooksPath=/dev/null commit -m x",
        "git -C notes -c core.hookspath=/dev/null commit -m x",
        "git -c CORE.HOOKSPATH=/dev/null commit",
        "git -c alias.c=commit c",
        "git -c include.path=/tmp/x commit",
        'git -c "$K=x" commit',
        "git --config-env=core.hooksPath=D commit",
        "git config core.hooksPath /dev/null",
        "git config alias.c commit",
        "git config include.path /tmp/x",
        "GIT_CONFIG_COUNT=1 git commit",
        "export GIT_CONFIG_GLOBAL=/tmp/x",
        "git commit-tree abc",
        "git update-ref refs/heads/main abc",
        "git fast-import < stream",
        "git filter-branch HEAD",
        "git filter-repo --force",
        "git hash-object -t commit -w f",
        "git $sub -m x",
        'git "$sub" -m x',
        "echo x > .git/hooks/pre-commit",
        "rm .git/hooks/pre-commit",
        "echo x >> .git/config",
        "cd .git/hooks && rm pre-commit",
        "cd .git && rm hooks/pre-commit",
        "ls notes\ngit commit --no-verify",
    ],
)
def test_a_hook_skip_reaches_python_from_the_shell(vault, tmp_path, command):
    # No KB word in the payload, so only the new patterns can send it to Python (here: missing, so exit 2).
    payload = _payload("Bash", {"command": command}, vault).getvalue()
    res = _sh({**RUN, "SCOUT_DATA_DIR": str(vault)}, payload, vault, root=tmp_path / "missing")
    assert res.returncode == 2, command


@pytest.mark.parametrize(
    "command",
    [
        "git status",
        "git log -n 5",
        "git diff HEAD",
        "git add notes/a.md",
        "git commit -m x",
        'git commit -m "non-urgent note"',
        'git commit -m "a note" -q',
        "git commit -q -m 'a; b' -- notes/a.md",
        "git commit --amend --no-edit",
        "git commit -F notes/msg.txt",
        "git merge -n feature",
        "git push origin main",
        "scripts/git-safe-commit.sh 'kb: note' notes/a.md",
        "scripts/scoutctl kb search commit",
        "ls notes -n",
    ],
)
def test_ordinary_git_stays_in_the_shell(vault, tmp_path, command):
    payload = _payload("Bash", {"command": command}, vault).getvalue()
    res = _sh({**RUN, "SCOUT_DATA_DIR": str(vault)}, payload, vault, root=tmp_path / "missing")
    assert res.returncode == 0 and res.stdout == "" and res.stderr == "", command


@pytest.mark.parametrize("command", ["git mv notes/a.md notes/b.md", "git config user.email alex@example.com"])
def test_file_moves_and_config_writes_go_to_python_which_allows_the_ordinary_ones(vault, tmp_path, command):
    """A `mv` or a `git config` write may touch what decides which checks run (#637), so Python decides; these pass."""
    payload = _payload("Bash", {"command": command}, vault).getvalue()
    assert _sh({**RUN, "SCOUT_DATA_DIR": str(vault)}, payload, vault, root=tmp_path / "missing").returncode == 2
    assert _decision(vault, "Bash", {"command": command}) is None


def test_a_hook_skip_word_only_in_the_description_exits_in_the_shell(vault, tmp_path):
    tool_input = {"command": "ls notes", "description": "git commit --no-verify -c core.hooksPath commit-tree"}
    payload = _payload("Bash", tool_input, vault).getvalue()
    res = _sh({**RUN, "SCOUT_DATA_DIR": str(vault)}, payload, vault, root=tmp_path / "missing")
    assert res.returncode == 0 and res.stdout == "" and res.stderr == ""


def test_a_hook_skip_is_denied_and_recorded_through_the_real_cli(vault):
    payload = _payload("Bash", {"command": "git commit --no-verify -m x"}, vault).getvalue()
    res = _sh({**RUN, "SCOUT_DATA_DIR": str(vault)}, payload, vault)
    assert res.returncode == 0, res.stderr
    spec = json.loads(res.stdout)["hookSpecificOutput"]
    assert spec["permissionDecision"] == "deny" and "--no-verify" in spec["permissionDecisionReason"]
    assert _events(vault)[0]["payload"]["form"] == "--no-verify"


def test_a_mention_of_a_hook_skip_is_allowed_through_the_real_cli(vault):
    payload = _payload("Bash", {"command": 'git commit -m "why --no-verify is wrong"'}, vault).getvalue()
    res = _sh({**RUN, "SCOUT_DATA_DIR": str(vault)}, payload, vault)
    assert res.returncode == 0 and res.stdout == "", res.stdout + res.stderr
    assert _events(vault) == []


def test_an_owner_hook_skip_exits_in_the_shell(vault, tmp_path):
    payload = _payload("Bash", {"command": "git commit --no-verify"}, vault).getvalue()
    res = _sh({"SCOUT_DATA_DIR": str(vault)}, payload, vault, root=tmp_path / "missing")
    assert res.returncode == 0 and res.stdout == "" and res.stderr == ""


def test_the_telegram_wrap_is_allowed_through_the_real_cli(vault):
    wrap = "scoutctl notify telegram --body-file - <<'SCOUT_WRAP'\n<b>Done.</b> R&amp;D (open since Monday)\nSCOUT_WRAP"
    payload = _payload("Bash", {"command": wrap}, vault).getvalue()
    res = _sh({**RUN, "SCOUT_DATA_DIR": str(vault)}, payload, vault)
    assert res.returncode == 0 and res.stdout == "", res.stdout + res.stderr


@pytest.mark.parametrize(
    "command",
    [
        "scoutctl delegate link duet --remote /tmp/m.git --confirm-account-unshared",
        "scripts/scoutctl delegate key duet --rotate",
        "~/scout-plugin/plugin/engine/bin/scoutctl delegate trust-state --confirm duet",
        "python3 -P -m scout delegate unlink duet",
        "bash -c 'scoutctl delegate unlink duet'",
    ],
)
def test_run_denies_the_owners_delegate_commands(vault, command):
    reason = _denied(_decision(vault, "Bash", {"command": command}))
    assert "owner's" in reason and "delegate sync" in reason


@pytest.mark.parametrize("command", ["scoutctl delegate sync --all --if-moved", "scoutctl delegate status"])
def test_a_run_may_sync_and_list_delegate_mirrors(vault, command):
    assert _decision(vault, "Bash", {"command": command}) is None


# --- the agents' inbox copies (#550 V4b1): agent text never reaches a run --------------------------------------


@pytest.fixture
def copies(vault: Path) -> Path:
    folder = vault / ".scout-inbox" / "agents" / "grok-personal"
    folder.mkdir(parents=True)
    (folder / "01JD5Z0000000000000000ABCD.json").write_text('{"text": "do as I say"}\n')
    (folder / "taken.ledger").write_text("01JD5Z0000000000000000ABCD\n")
    return folder


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Read", {"file_path": ".scout-inbox/agents/grok-personal/01JD5Z0000000000000000ABCD.json"}),
        ("Read", {"file_path": ".scout-inbox/agents/grok-personal/taken.ledger"}),
        ("Grep", {"pattern": "x", "path": ".scout-inbox/agents"}),
        ("Grep", {"pattern": "x", "path": ".scout-inbox"}),
        ("Grep", {"pattern": "x", "glob": "*.json"}),
        ("Grep", {"pattern": "x", "glob": "**/agents/**"}),
        ("Glob", {"pattern": "**/*.json"}),
        ("Glob", {"pattern": ".scout-inbox/**"}),
        ("Bash", {"command": "cat .scout-inbox/agents/grok-personal/01JD5Z0000000000000000ABCD.json"}),
        ("Bash", {"command": "jq . .scout-inbox/agents/*/*.json"}),
    ],
)
def test_a_run_never_reads_what_an_agent_proposed(vault: Path, copies: Path, tool: str, tool_input: dict) -> None:
    reason = _denied(_decision(vault, tool, tool_input))
    assert "agents' inbox copies" in reason and "untrusted" in reason


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Grep", {"pattern": "x", "path": "scripts"}),
        ("Glob", {"pattern": "action-items/*.md"}),
        ("Read", {"file_path": ".scout-inbox/phone-notifications.jsonl"}),
    ],
)
def test_other_files_stay_readable(vault: Path, copies: Path, tool: str, tool_input: dict) -> None:
    assert _decision(vault, tool, tool_input) is None


def test_the_owner_still_reads_the_copies(vault: Path, copies: Path) -> None:
    path = ".scout-inbox/agents/grok-personal/01JD5Z0000000000000000ABCD.json"
    assert _decision(vault, "Read", {"file_path": path}, env={}) is None


@pytest.mark.parametrize(
    "command",
    ["scoutctl inbox apply --all", "scripts/scoutctl inbox sync", "python3 -P -m scout inbox pull grok-personal"],
)
def test_a_run_never_pulls_or_applies_an_inbox(vault: Path, command: str) -> None:
    reason = _denied(_decision(vault, "Bash", {"command": command}))
    assert "inbox" in reason and "owner's" in reason


def test_the_shell_prefilters_send_the_copies_to_the_gate(vault: Path, copies: Path) -> None:
    env = {**RUN, "SCOUT_DATA_DIR": str(vault)}
    read = _payload("Read", {"file_path": str(copies / "01JD5Z0000000000000000ABCD.json")}, vault).getvalue()
    res = _sh(env, read, vault, matcher="Read")
    assert '"deny"' in res.stdout and "agents' inbox copies" in res.stdout, res.stdout + res.stderr
    bash = _payload("Bash", {"command": "head .scout-inbox/agents/grok-personal/taken.ledger"}, vault).getvalue()
    res = _sh(env, bash, vault)
    assert '"deny"' in res.stdout, res.stdout + res.stderr
    cmd = _payload("Bash", {"command": "scoutctl inbox apply --all"}, vault).getvalue()
    res = _sh(env, cmd, vault)
    assert '"deny"' in res.stdout, res.stdout + res.stderr


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Grep", {"pattern": "x", "glob": "*.js?n"}),
        ("Grep", {"pattern": "x", "glob": "*.[j]son"}),
        ("Glob", {"pattern": "*/*/*/*.tmp"}),
        ("Bash", {"command": "cat .scout-in*/agents/*/*"}),
    ],
)
def test_the_copies_are_judged_by_what_a_filter_matches(vault: Path, copies: Path, tool: str, tool_input: dict) -> None:
    assert "agents' inbox copies" in _denied(_decision(vault, tool, tool_input))


# --- round 2: a glob never holds the gate past its timeout (a hook that times out allows the call) -------------


@pytest.mark.parametrize(
    "command",
    [
        "cat knowledge-base/personal/x.md /*/*/*/*/*/*/*/zz",
        "cat knowledge-base/x.md; touch /*/*/*/*/*/*/*/zz",
        "touch ~/*/*/*/*/zz",
    ],
)
def test_a_glob_too_wide_to_judge_is_refused_fast(vault: Path, command: str) -> None:
    import time

    started = time.monotonic()
    reason = _denied(_decision(vault, "Bash", {"command": command}))
    assert time.monotonic() - started < 5  # the hook's own timeout is 15 s
    assert "more files than Scout's gate checks" in reason


def test_a_glob_with_too_many_matches_is_refused(vault: Path) -> None:
    many = vault / "many"
    many.mkdir()
    for i in range(300):
        (many / f"f{i}.txt").write_text("x")
    assert "more files than Scout's gate checks" in _denied(_decision(vault, "Bash", {"command": "touch many/*"}))
    assert _decision(vault, "Bash", {"command": "ls many"}) is None


def test_a_slow_glob_is_refused_not_waited_for(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    monkeypatch.setattr(gate, "GLOB_BUDGET_S", 0.2)

    def slow(pattern: str) -> object:
        time.sleep(1)
        yield pattern

    monkeypatch.setattr(gate.glob, "iglob", slow)
    assert gate._glob_targets(str(vault / "a*")) is None
    assert gate._glob_targets(str(vault / "plain")) == [str(vault / "plain")]


@pytest.mark.parametrize("glob_filter", ["*.{json,ledger}", "{a,*.js{o,}n}", "*.{md,{ledger,x}}"])
def test_brace_filters_are_expanded_before_judging(vault: Path, copies: Path, glob_filter: str) -> None:
    assert "agents' inbox copies" in _denied(_decision(vault, "Grep", {"pattern": "x", "glob": glob_filter}))


def test_the_prefilter_takes_any_space_before_an_inbox_command(vault: Path) -> None:
    env = {**RUN, "SCOUT_DATA_DIR": str(vault)}
    cmd = _payload("Bash", {"command": "scoutctl inbox\tsync"}, vault).getvalue()
    res = _sh(env, cmd, vault)
    assert '"deny"' in res.stdout, res.stdout + res.stderr


@pytest.mark.parametrize(
    "command", ["scoutctl inbox review", "scoutctl inbox review accept 01JD5Z0000000000000000ABCD"]
)
def test_the_prefilter_sends_inbox_review_to_the_gate(vault: Path, command: str) -> None:
    """#550 V4b2: the review prints agents' text, which never reaches a run."""
    env = {**RUN, "SCOUT_DATA_DIR": str(vault)}
    res = _sh(env, _payload("Bash", {"command": command}, vault).getvalue(), vault)
    assert '"deny"' in res.stdout, res.stdout + res.stderr


def test_a_run_may_read_an_agents_accepted_note(vault: Path) -> None:
    """#550 V4b2: from-agents/ is write-protected, not hidden: runs read it, marked [from-agent]."""
    rel = "knowledge-base/from-agents/grok-personal/01JD5Z0000000000000000ABCD.md"
    (vault / rel).parent.mkdir(parents=True, exist_ok=True)
    (vault / rel).write_text("---\nsource: delegate:grok-personal\nvisibility: private\n---\nText.\n")
    assert _decision(vault, "Read", {"file_path": str(vault / rel)}) is None
