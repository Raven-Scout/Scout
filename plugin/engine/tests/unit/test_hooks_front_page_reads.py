"""front-page-reads: a run editing a front page with no read of its knowledge/ notes is warned, never blocked."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from scout.hooks import front_page_reads as hook
from scout.kb.ontology import clear_frontmatter_cache

PLUGIN = Path(__file__).parent.parent.parent.parent
RUN = {"SCOUT_MODE": "morning-briefing", "SCOUT_READER": "autonomous"}
DELEGATE = {"SCOUT_MODE": "morning-briefing", "SCOUT_READER": "delegate:helper"}
FRONT = "knowledge-base/projects/widgets/widgets.md"
NOTE = "knowledge-base/projects/widgets/knowledge/widgets-release.md"
OLD_NOTE = "knowledge-base/projects/widgets/knowledge/widgets-history.md"
SECRET_NOTE = "knowledge-base/projects/widgets/knowledge/widgets-budget.md"
OTHER_NOTE = "knowledge-base/projects/gateway/knowledge/gateway-capacity.md"
CONFIG = "schema_version: 1\nprivacy:\n  delegates:\n    helper: {}\n"


def _note(as_of: str, private: bool = False) -> str:
    return f"---\nname: x\nas_of: {as_of}\n{'visibility: private' + chr(10) if private else ''}---\n# x\n"


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    clear_frontmatter_cache()
    root = tmp_path / "vault"
    root.mkdir()
    (root / "scout-config.yaml").write_text(CONFIG, encoding="utf-8")
    files = {
        FRONT: "---\nname: widgets\nstatus: active\n---\n# Widgets\n",
        NOTE: _note("2026-04-12"),
        OLD_NOTE: _note("2026-01-02"),
        "knowledge-base/projects/gateway/gateway.md": "---\nname: gateway\n---\n# Gateway\n",
        OTHER_NOTE: _note("2026-04-01"),
    }
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    monkeypatch.setenv("SCOUT_DATA_DIR", str(root))
    return root.resolve()


def _rows(calls: list[tuple[str, dict, object]]) -> list[dict]:
    rows: list[dict] = []
    for n, (tool, tool_input, result) in enumerate(calls):
        tid = f"toolu_{n}"
        use = {"type": "tool_use", "id": tid, "name": tool, "input": tool_input}
        rows.append({"message": {"role": "assistant", "content": [use]}})
        done = {"type": "tool_result", "tool_use_id": tid, "content": result}
        rows.append({"message": {"role": "user", "content": [done]}})
    return rows


def _transcript(tmp_path: Path, calls: list[tuple[str, dict, object]], name: str = "session.jsonl") -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in _rows(calls)) + "\n", encoding="utf-8")
    return path


def _payload(vault: Path, transcript: Path | None, rel: str = FRONT, tool: str = "Edit") -> dict:
    body: dict = {"tool_name": tool, "tool_input": {"file_path": str(vault / rel)}, "cwd": str(vault)}
    if transcript is not None:
        body["transcript_path"] = str(transcript)
    return body


def _run(payload: dict, env: dict | None = None) -> str | None:
    out = hook.run(stdin=io.StringIO(json.dumps(payload)), env=RUN if env is None else env)
    if out is None:
        return None
    spec = out["hookSpecificOutput"]
    assert spec["hookEventName"] == "PostToolUse" and "permissionDecision" not in spec and "decision" not in out
    return spec["additionalContext"]


@pytest.mark.parametrize(
    ("rel", "expected"),
    [
        (FRONT, "widgets"),
        ("knowledge-base/projects/widgets/knowledge/widgets.md", None),
        ("knowledge-base/projects/widgets/other.md", None),
        ("knowledge-base/projects/projects.md", None),
        ("knowledge-base/topics/widgets/widgets.md", None),
    ],
)
def test_front_page_project(rel: str, expected: str | None) -> None:
    assert hook.front_page_project(rel, "knowledge-base/projects") == expected


def test_an_edit_with_no_knowledge_read_is_warned(vault: Path, tmp_path: Path) -> None:
    transcript = _transcript(tmp_path, [("Read", {"file_path": str(vault / OTHER_NOTE)}, "text")])
    text = _run(_payload(vault, transcript))
    assert text is not None and text.startswith(hook.MARK)
    assert FRONT in text and "project_knowledge" in text and "the edit stands" in text
    assert text.index(NOTE) < text.index(OLD_NOTE)  # newest as_of first
    assert "2 knowledge/ notes" in text and "(private)" not in text
    assert OTHER_NOTE not in text  # another project's note doesn't count, and isn't named


@pytest.mark.parametrize("tool", ["Write", "MultiEdit"])
def test_every_write_tool_is_checked(vault: Path, tmp_path: Path, tool: str) -> None:
    assert _run(_payload(vault, _transcript(tmp_path, []), tool=tool)) is not None


@pytest.mark.parametrize(
    "call",
    [
        ("Read", {"file_path": NOTE}, "text"),  # relative to the session's cwd
        ("Edit", {"file_path": NOTE}, "ok"),
        ("Bash", {"command": f"scripts/scoutctl kb read {NOTE}"}, "body"),
        ("Bash", {"command": "scripts/scoutctl kb search release --glob 'knowledge-base/projects/**'"}, f"{NOTE}:3"),
        ("Bash", {"command": "scripts/scoutctl kb entity 'Widgets release'"}, [{"type": "text", "text": NOTE}]),
    ],
)
def test_a_read_of_one_of_its_notes_earlier_in_the_session_clears_it(
    vault: Path, tmp_path: Path, call: tuple[str, dict, object]
) -> None:
    assert _run(_payload(vault, _transcript(tmp_path, [call]))) is None


def test_an_unreadable_subagent_transcript_is_skipped(vault: Path, tmp_path: Path) -> None:
    transcript = _transcript(tmp_path, [])
    (tmp_path / "session" / "subagents" / "agent-0.jsonl").mkdir(parents=True)  # a folder, not a file
    assert _run(_payload(vault, transcript)) is not None


def test_other_tools_and_kb_results_elsewhere_do_not_count(vault: Path, tmp_path: Path) -> None:
    calls: list[tuple[str, dict, object]] = [
        ("Grep", {"pattern": "x", "path": "knowledge-base/projects/widgets/knowledge"}, "nothing"),
        ("Bash", {"command": "scripts/scoutctl kb search release"}, "see the widgets/knowledge notes"),
    ]
    assert _run(_payload(vault, _transcript(tmp_path, calls))) is not None


def test_a_subagents_read_counts(vault: Path, tmp_path: Path) -> None:
    transcript = _transcript(tmp_path, [])
    _transcript(tmp_path, [("Read", {"file_path": str(vault / NOTE)}, "text")], name="session/subagents/agent-1.jsonl")
    assert _run(_payload(vault, transcript)) is None


@pytest.mark.parametrize(
    "call",
    [
        ("Bash", {"command": "scripts/scoutctl kb files knowledge-base/projects/widgets/knowledge"}, NOTE),
        ("Bash", {"command": f"echo {NOTE}"}, NOTE),  # not a kb read
        ("Read", {"file_path": 42}, "x"),  # no usable path
        ("Read", {"file_path": "/elsewhere/widgets/knowledge/x.md"}, "x"),  # outside the vault
    ],
)
def test_what_is_not_a_read(vault: Path, tmp_path: Path, call: tuple[str, dict, object]) -> None:
    assert _run(_payload(vault, _transcript(tmp_path, [call]))) is not None


def test_unparseable_and_odd_lines_are_skipped(vault: Path, tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    lines = [
        "not json, widgets/knowledge",
        json.dumps(["widgets/knowledge"]),
        json.dumps({"message": {"content": ["widgets/knowledge", {"type": "tool_use", "name": "Read", "input": 3}]}}),
        json.dumps({"message": {"content": [{"type": "tool_result", "tool_use_id": "x", "content": {"a": NOTE}}]}}),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert _run(_payload(vault, path)) is not None


def test_a_transcript_past_the_cap_says_nothing(vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _transcript(tmp_path, [("Read", {"file_path": str(vault / OTHER_NOTE)}, "x" * 500)])
    with pytest.raises(hook._TooBig):
        list(hook.transcript_lines(path, max_bytes=100))

    def too_big(_: Path) -> object:
        raise hook._TooBig

    monkeypatch.setattr(hook, "transcript_lines", too_big)
    assert _run(_payload(vault, path)) is None


def test_special_files_and_symlinks_are_skipped(vault: Path, tmp_path: Path) -> None:
    import os

    transcript = _transcript(tmp_path, [])
    sub = tmp_path / "session" / "subagents"
    sub.mkdir(parents=True)
    os.mkfifo(sub / "agent-fifo.jsonl")  # would hang a plain open()
    real = _transcript(tmp_path, [("Read", {"file_path": str(vault / NOTE)}, "x")], name="elsewhere.jsonl")
    (sub / "agent-link.jsonl").symlink_to(real)  # its read doesn't count: symlinks are skipped
    assert _run(_payload(vault, transcript)) is not None


def test_the_cap_holds_while_reading_and_long_lines_come_in_pieces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text("a" * 25 + "\nb\n", encoding="utf-8")
    monkeypatch.setattr(hook, "READ_CHUNK", 10)
    assert list(hook.transcript_lines(path)) == ["a" * 10, "a" * 10, "a" * 5 + "\n", "b\n"]
    monkeypatch.setattr(hook, "_regular_file", lambda _: 0)  # sizes that lie: the running count still stops it
    with pytest.raises(hook._TooBig):
        list(hook.transcript_lines(path, max_bytes=15))


def test_a_file_swapped_after_the_size_check_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    fifo = tmp_path / "t.jsonl"
    os.mkfifo(fifo)
    monkeypatch.setattr(hook, "_regular_file", lambda _: 0)  # as if it was a file at lstat time
    assert list(hook.transcript_lines(fifo)) == []
    assert list(hook.transcript_lines(tmp_path / "gone.jsonl")) == []


def test_no_vault_or_no_file_adds_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert hook._regular_file(tmp_path / "missing.jsonl") is None
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path / "not-a-vault"))
    payload = {"tool_name": "Edit", "tool_input": {"file_path": f"/x/{FRONT}"}, "cwd": str(tmp_path)}
    assert _run(payload) is None


def test_a_write_that_is_not_a_front_page_does_no_heavy_work(
    vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scout.kb.read
    import scout.kb.structure

    calls: list[str] = []
    monkeypatch.setattr(scout.kb.read, "visibility_map", lambda *a, **k: calls.append("labels") or {})
    real = scout.kb.structure.load_structure_config
    monkeypatch.setattr(scout.kb.structure, "load_structure_config", lambda v: calls.append("config") or real(v))
    transcript = _transcript(tmp_path, [])
    assert _run(_payload(vault, transcript, rel=NOTE)) is None  # not shaped <x>/<x>.md
    assert _run({**_payload(vault, transcript), "tool_input": {"file_path": 7}}) is None
    assert _run({**_payload(vault, transcript), "tool_input": {"file_path": "/elsewhere/x/x.md"}}) is None
    assert calls == []
    assert _run(_payload(vault, transcript, rel="knowledge-base/topics/misc/misc.md")) is None
    assert calls == ["config"]  # shaped like one, so the config is read, but it isn't a project page


@pytest.mark.parametrize(
    ("file_path", "starts"),
    [
        ("/v/knowledge-base/projects/widgets/widgets.md", True),
        ("work/projects/gateway/gateway.md", True),  # a custom projects_dir
        ("/v/knowledge-base/projects/widgets/knowledge/widgets-release.md", False),
        ("/v/action-items/action-items-2026-04-15.md", False),
        ("widgets/widgets.txt", False),
    ],
)
def test_the_shell_prefilter_matches_only_front_page_shaped_paths(file_path: str, starts: bool) -> None:
    import re
    import subprocess

    config = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    command = next(
        h["command"] for g in config["hooks"]["PostToolUse"] for h in g["hooks"] if "front-page-reads" in h["command"]
    )
    pattern = re.search(r"grep -qE '([^']+)'", command)
    assert pattern is not None
    payload = json.dumps({"tool_name": "Write", "tool_input": {"file_path": file_path, "content": "x.md y/y.md"}})
    found = subprocess.run(["grep", "-qE", pattern.group(1)], input=payload, text=True, check=False)
    assert (found.returncode == 0) is starts


def test_only_front_pages_with_knowledge_notes_are_checked(vault: Path, tmp_path: Path) -> None:
    transcript = _transcript(tmp_path, [])
    assert _run(_payload(vault, transcript, rel=NOTE)) is None
    assert _run(_payload(vault, transcript, tool="Read")) is None
    assert _run({"tool_name": "Edit", "tool_input": "x", "cwd": str(vault)}) is None
    (vault / NOTE).unlink()
    (vault / OLD_NOTE).unlink()
    assert _run(_payload(vault, transcript)) is None  # nothing to read


def test_private_notes_are_marked(vault: Path, tmp_path: Path) -> None:
    (vault / SECRET_NOTE).write_text(_note("2026-04-14", private=True), encoding="utf-8")
    text = _run(_payload(vault, _transcript(tmp_path, [])))
    assert text is not None and f"`{SECRET_NOTE}` (private)" in text and "3 knowledge/ notes" in text


def test_a_delegate_never_learns_a_hidden_note_exists(vault: Path, tmp_path: Path) -> None:
    (vault / SECRET_NOTE).write_text(_note("2026-04-14", private=True), encoding="utf-8")
    text = _run(_payload(vault, _transcript(tmp_path, [])), env=DELEGATE)
    assert text is not None and SECRET_NOTE not in text and "widgets-budget" not in text
    assert "2 knowledge/ notes" in text  # the count leaves it out too


def test_a_delegate_hears_nothing_about_a_page_it_cannot_see(vault: Path, tmp_path: Path) -> None:
    (vault / FRONT).write_text("---\nname: widgets\nvisibility: private\n---\n# Widgets\n", encoding="utf-8")
    assert _run(_payload(vault, _transcript(tmp_path, [])), env=DELEGATE) is None
    assert _run(_payload(vault, _transcript(tmp_path, [])), env={"SCOUT_READER": "agent:person:x"}) is None


def test_it_never_warns_outside_a_run_or_without_a_transcript(vault: Path, tmp_path: Path) -> None:
    assert _run(_payload(vault, _transcript(tmp_path, [])), env={"SCOUT_READER": "owner"}) is None
    assert _run(_payload(vault, None)) is None


def test_bad_input_adds_nothing_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert hook.run(stdin=io.StringIO("not json"), env=RUN) is None
    monkeypatch.setattr("sys.stdin", io.StringIO("[]"))
    monkeypatch.setenv("SCOUT_MODE", "morning-briefing")
    assert hook.main() == 0
    assert capsys.readouterr().out == ""


def test_main_prints_the_warning(
    vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_payload(vault, _transcript(tmp_path, [])))))
    monkeypatch.setenv("SCOUT_MODE", "morning-briefing")
    assert hook.main() == 0
    assert json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"].startswith(hook.MARK)


def test_a_failing_check_adds_nothing(vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(hook, "touched_knowledge", broken)
    assert _run(_payload(vault, _transcript(tmp_path, []))) is None


def test_the_warning_names_at_most_five_notes() -> None:
    notes = [{"path": f"knowledge-base/projects/widgets/knowledge/n{i}.md", "visibility": "normal"} for i in range(8)]
    text = hook.warning(FRONT, notes)
    assert "n4.md" in text and "n5.md" not in text and "and 3 more" in text and "8 knowledge/ notes" in text


def test_result_text_shapes() -> None:
    assert hook._result_text({"content": "a"}) == "a"
    assert hook._result_text({"content": [{"text": "a"}, "junk", {"text": None}]}) == "a\n"
    assert hook._result_text({"content": 3}) == ""


def test_hooks_json_runs_it_after_a_runs_writes_and_never_fails_the_call() -> None:
    config = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    entries = [
        h
        for group in config["hooks"]["PostToolUse"]
        if group["matcher"] == "Write|Edit|MultiEdit"
        for h in group["hooks"]
        if "front-page-reads" in h["command"]
    ]
    assert len(entries) == 1
    command = entries[0]["command"]
    assert command.startswith('[ -n "$SCOUT_MODE" ]') and command.endswith("; exit 0")
    assert "exit 2" not in command and "grep -qE" in command
    assert "front-page-reads" in config["description"]
