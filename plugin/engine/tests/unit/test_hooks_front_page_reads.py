"""front-page-reads: a run editing a front page with no read of its knowledge/ notes is warned, never blocked."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from scout.hooks import front_page_reads as hook

PLUGIN = Path(__file__).parent.parent.parent.parent
RUN = {"SCOUT_MODE": "morning-briefing", "SCOUT_READER": "autonomous"}
FRONT = "knowledge-base/projects/widgets/widgets.md"
NOTE = "knowledge-base/projects/widgets/knowledge/widgets-release.md"
OTHER_NOTE = "knowledge-base/projects/gateway/knowledge/gateway-capacity.md"


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "scout-config.yaml").write_text("schema_version: 1\n", encoding="utf-8")
    for rel in (FRONT, NOTE, "knowledge-base/projects/gateway/gateway.md", OTHER_NOTE):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\nname: x\n---\n# x\n", encoding="utf-8")
    monkeypatch.setenv("SCOUT_DATA_DIR", str(root))
    return root.resolve()


def _transcript(tmp_path: Path, calls: list[tuple[str, dict, str]]) -> Path:
    """A session JSONL: each (tool, input, result text) as a tool_use and its tool_result."""
    rows = []
    for n, (tool, tool_input, result) in enumerate(calls):
        tid = f"toolu_{n}"
        rows.append(
            {
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": tid, "name": tool, "input": tool_input}],
                }
            }
        )
        rows.append(
            {"message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": result}]}}
        )
    path = tmp_path / "session.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
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
    assert FRONT in text and NOTE in text and "project_knowledge" in text and "the edit stands" in text
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
        (
            "Bash",
            {"command": "scripts/scoutctl kb search release --glob 'knowledge-base/projects/**'"},
            f"{NOTE}:3: hit",
        ),
    ],
)
def test_a_read_of_one_of_its_notes_earlier_in_the_session_clears_it(
    vault: Path, tmp_path: Path, call: tuple[str, dict, str]
) -> None:
    assert _run(_payload(vault, _transcript(tmp_path, [call]))) is None


def test_a_listing_is_not_a_read(vault: Path, tmp_path: Path) -> None:
    call = ("Bash", {"command": "scripts/scoutctl kb files knowledge-base/projects/widgets/knowledge"}, NOTE)
    assert _run(_payload(vault, _transcript(tmp_path, [call]))) is not None


def test_only_front_pages_with_knowledge_notes_are_checked(vault: Path, tmp_path: Path) -> None:
    transcript = _transcript(tmp_path, [])
    assert _run(_payload(vault, transcript, rel=NOTE)) is None
    assert _run(_payload(vault, transcript, tool="Read")) is None
    (vault / NOTE).unlink()
    assert _run(_payload(vault, transcript)) is None  # nothing to read


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


def test_a_failing_check_adds_nothing(vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(hook, "touched_knowledge", broken)
    assert _run(_payload(vault, _transcript(tmp_path, []))) is None


def test_the_warning_names_at_most_five_notes() -> None:
    notes = [f"knowledge-base/projects/widgets/knowledge/n{i}.md" for i in range(8)]
    text = hook.warning(FRONT, notes)
    assert "n4.md" in text and "n5.md" not in text and "and 3 more" in text and "8 knowledge/ notes" in text


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
    assert "exit 2" not in command
    assert "front-page-reads" in config["description"]
