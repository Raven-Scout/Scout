"""kb-read-label: a Read of a KB note comes back with its privacy label (Jordan, 2026-10-29, #536).

Over the privacy fixture vault: ``sam`` and ``task-renew-permit`` are private, ``priya`` is normal.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
from pathlib import Path

import pytest

from scout.hooks import kb_read_gate
from scout.hooks import kb_read_label as hook
from scout.kb.ontology import clear_frontmatter_cache
from tests.eval.harness.checks.privacy_canary import LABEL_COMMAND, label_key, leading_indicators
from tests.eval.harness.metrics import ToolCall
from tests.unit.privacy_vault_helpers import build_privacy_vault

PLUGIN = Path(__file__).parent.parent.parent.parent
RUN = {"SCOUT_MODE": "morning-briefing", "SCOUT_READER": "autonomous"}
PRIVATE = "knowledge-base/personal/task-renew-permit.md"
NORMAL = "knowledge-base/people/priya.md"


@pytest.fixture(scope="module")
def shared_vault(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_privacy_vault(tmp_path_factory.mktemp("kb-read-label"))


@pytest.fixture
def vault(shared_vault: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    clear_frontmatter_cache()
    log = shared_vault / ".scout-logs" / "kb-reads.jsonl"
    log.unlink(missing_ok=True)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(shared_vault))
    return shared_vault.resolve()


def _payload(vault: Path, rel: str, *, tool: str = "Read", cwd: Path | None = None) -> io.StringIO:
    body = {
        "tool_name": tool,
        "tool_input": {"file_path": str(vault / rel)},
        "tool_response": {"type": "text"},
        "cwd": str(cwd or vault),
    }
    return io.StringIO(json.dumps(body))


def _context(out) -> str:
    assert out is not None, "expected a label"
    spec = out["hookSpecificOutput"]
    assert spec["hookEventName"] == "PostToolUse" and "permissionDecision" not in spec
    assert spec["additionalContext"].startswith(hook.MARK)
    return spec["additionalContext"]


def _rows(vault: Path) -> list[dict]:
    log = vault / ".scout-logs" / "kb-reads.jsonl"
    return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []


def test_a_private_note_is_labelled_private_with_the_shared_surface_rule(vault):
    text = _context(hook.run(stdin=_payload(vault, PRIVATE), env=RUN))
    assert f"{PRIVATE} is [private]" in text
    assert "must not reach any shared surface" in text and "Slack" in text and "drafts/" in text


def test_a_normal_note_is_labelled_normal(vault):
    text = _context(hook.run(stdin=_payload(vault, NORMAL), env=RUN))
    assert f"{NORMAL} is normal" in text and "[private]" not in text


def test_a_context_note_is_labelled(vault):
    (vault / "action-items" / "context").mkdir(parents=True, exist_ok=True)
    (vault / "action-items" / "context" / "ROLL.md").write_text("---\ntag: ROLL\n---\nStand-in.\n")
    assert "action-items/context/ROLL.md is" in _context(
        hook.run(stdin=_payload(vault, "action-items/context/ROLL.md"), env=RUN)
    )


def test_the_kb_index_is_labelled(vault):
    # knowledge-base.md is KB for the Bash gate and the eval, so a Read of it gets a label too (#536 eval).
    (vault / "knowledge-base.md").write_text("# Knowledge base\n\n- [[priya]]\n")
    try:
        assert "knowledge-base.md is" in _context(hook.run(stdin=_payload(vault, "knowledge-base.md"), env=RUN))
    finally:
        (vault / "knowledge-base.md").unlink()


# One path rule (scout.kb.raw_read.is_kb_note_path) for all three: a Read the eval counts as a KB read is
# labelled by this hook and denied to a reader scoutctl kb shows nothing, and nothing else is.
PARITY = [
    ("knowledge-base.md", True),
    ("knowledge-base/people/priya.md", True),
    ("knowledge_base/people/priya.md", True),
    ("action-items/context/ROLL.md", True),
    ("knowledge-base/ontology/schema.yaml", False),
    ("knowledge-base/ontology/parser.py", False),
    ("SKILL.md", False),
    ("action-items/action-items-2026-10-29.md", False),
    ("drafts/knowledge.md", False),
]


@pytest.mark.parametrize(("rel", "kb"), PARITY)
def test_the_label_hook_the_gate_and_the_eval_agree_on_what_is_kb(vault, rel, kb):
    labelled = hook.run(stdin=_payload(vault, rel), env=RUN) is not None
    agent = {"SCOUT_READER": "agent:person:priya@example.com"}
    body = json.dumps({"tool_name": "Read", "tool_input": {"file_path": str(vault / rel)}, "cwd": str(vault)})
    gated = kb_read_gate.run(stdin=io.StringIO(body), env=agent) is not None
    call = ToolCall(seq=0, name="Read", input={"file_path": str(vault / rel)}, is_error=False, position=0.0)
    counted = leading_indicators([call], {}, (str(vault),))["raw_kb_read"] == 1
    assert (labelled, gated, counted) == (kb, kb, kb)


def test_a_runs_label_is_logged_with_the_hash_the_eval_matches(vault):
    hook.run(stdin=_payload(vault, PRIVATE), env=RUN)
    (row,) = _rows(vault)
    assert row["command"] == LABEL_COMMAND and row["private_paths"] == 1 and row["blocked"] is False
    assert row["args_hash"] == label_key(str(vault / PRIVATE), str(vault))
    assert "renew" not in json.dumps(row)


def test_the_symlink_spelling_gets_the_same_label(vault):
    text = _context(hook.run(stdin=_payload(vault, "knowledge_base/personal/task-renew-permit.md"), env=RUN))
    assert f"{PRIVATE} is [private]" in text


@pytest.mark.parametrize("env", [{}, {"SCOUT_READER": "owner"}])
def test_owner_session_gets_the_label_only(vault, env):
    # The owner's Read already happened and nothing is blocked: the hook only adds the label, and logs nothing.
    assert "[private]" in _context(hook.run(stdin=_payload(vault, PRIVATE), env=env))
    assert _rows(vault) == []


@pytest.mark.parametrize(
    "rel", ["SKILL.md", "knowledge-base/ontology/schema.yaml", "action-items/action-items-2026-10-29.md"]
)
def test_files_that_are_not_kb_notes_get_no_label(vault, rel):
    assert hook.run(stdin=_payload(vault, rel), env=RUN) is None


def test_other_tools_get_no_label(vault):
    assert hook.run(stdin=_payload(vault, PRIVATE, tool="Edit"), env=RUN) is None


def test_a_session_outside_any_vault_gets_no_label(tmp_path, monkeypatch):
    monkeypatch.delenv("SCOUT_DATA_DIR", raising=False)
    other = tmp_path / "repo"
    (other / "knowledge-base").mkdir(parents=True)
    (other / "knowledge-base" / "x.md").write_text("x\n")
    assert hook.run(stdin=_payload(other, "knowledge-base/x.md"), env=RUN) is None


def test_a_label_that_cant_be_computed_is_private(vault, monkeypatch):
    def boom(*_a):
        raise RuntimeError("policy unreadable")

    monkeypatch.setattr("scout.kb.privacy.load_policy", boom)
    text = _context(hook.run(stdin=_payload(vault, NORMAL), env=RUN))
    assert "could not label" in text and "treat it as [private]" in text
    (row,) = _rows(vault)
    assert row["private_paths"] == 1


def test_an_unreadable_note_is_private(vault):
    # classify() fails closed on a markdown file it can't open.
    target = vault / "knowledge-base" / "people" / "locked.md"
    target.write_text("---\nname: locked\n---\n")
    target.chmod(0)
    try:
        assert "is [private]" in _context(hook.run(stdin=_payload(vault, "knowledge-base/people/locked.md"), env=RUN))
    finally:
        target.chmod(0o644)
        target.unlink()


@pytest.mark.parametrize("raw", ["not json", "[]", "{}"])
def test_a_malformed_payload_adds_nothing(vault, raw):
    # PostToolUse can't stop a Read that already happened; with no file to name there is nothing to label.
    assert hook.run(stdin=io.StringIO(raw), env=RUN) is None


def test_main_prints_the_label(vault, monkeypatch, capsys):
    for k, v in RUN.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr("sys.stdin", _payload(vault, PRIVATE))
    assert hook.main() == 0
    assert "[private]" in json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]


# --- hooks.json wiring ---------------------------------------------------------------------


def _entry() -> dict:
    (entry,) = json.loads((PLUGIN / "hooks" / "hooks.json").read_text())["hooks"]["PostToolUse"]
    return entry


def _sh(env: dict, payload: str, cwd: Path, root: Path = PLUGIN) -> subprocess.CompletedProcess[str]:
    clean = {k: v for k, v in os.environ.items() if k not in ("SCOUT_MODE", "SCOUT_READER", "SCOUT_DATA_DIR")}
    full = {**clean, **env, "CLAUDE_PLUGIN_ROOT": str(root)}
    return subprocess.run(
        ["sh", "-c", _entry()["hooks"][0]["command"]],
        env=full,
        cwd=cwd,
        input=payload,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_matcher_is_read_only():
    assert _entry()["matcher"] == "Read" and "hook kb-read-label" in _entry()["hooks"][0]["command"]


def test_a_read_naming_no_kb_path_exits_in_the_shell(vault, tmp_path):
    res = _sh({**RUN, "SCOUT_DATA_DIR": str(vault)}, _payload(vault, "SKILL.md").getvalue(), vault, root=tmp_path / "x")
    assert res.returncode == 0 and res.stdout == ""


def test_owner_outside_a_vault_exits_in_the_shell(tmp_path):
    payload = json.dumps({"tool_name": "Read", "tool_input": {"file_path": str(tmp_path / "knowledge-base/x.md")}})
    res = _sh({}, payload, tmp_path, root=tmp_path / "missing")
    assert res.returncode == 0 and res.stdout == ""


def test_a_run_gets_the_label_through_the_real_cli(vault):
    res = _sh({**RUN, "SCOUT_DATA_DIR": str(vault)}, _payload(vault, PRIVATE).getvalue(), vault)
    assert res.returncode == 0, res.stderr
    assert "[private]" in json.loads(res.stdout)["hookSpecificOutput"]["additionalContext"]


def test_scoutctl_that_cannot_run_labels_the_read_private(vault, tmp_path):
    res = _sh({**RUN, "SCOUT_DATA_DIR": str(vault)}, _payload(vault, NORMAL).getvalue(), vault, root=tmp_path / "x")
    assert res.returncode == 0
    text = json.loads(res.stdout)["hookSpecificOutput"]["additionalContext"]
    assert text.startswith(hook.MARK) and "treat it as [private]" in text
