"""Stop hooks outside a runner: the vault from cwd, the mode from the run prompt, the cost to the budget.

A session started without run-*.sh (a sandbox running the runner's prompt itself) has
no SCOUT_DATA_DIR or SCOUT_MODE. These pin what the hooks do then, and that a runner's
own exports still win.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from scout.hooks import run_context, session_tokens, session_tool_log

RESEARCH_PROMPT = (
    "You are Scout running in RESEARCH mode — the knowledge expansion session. Your working directory is /vault."
)
SLOT_PROMPT = "You are Scout.\n\nStep 2: Your run mode is `midday-consolidation` (the dispatcher's slot key)."


@pytest.fixture(autouse=True)
def _no_runner_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for var in ("SCOUT_MODE", "SCOUT_DATA_DIR", "SESSION_TOKENS_TRACKER", "SCOUT_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(session_tokens, "_POLL_ATTEMPTS", 1)
    monkeypatch.chdir(tmp_path)  # the hook process runs outside the vault


def _vault(root: Path) -> Path:
    (root / ".scout-logs").mkdir(parents=True)
    (root / "scout-config.yaml").write_text("user:\n  name: Alex\n", encoding="utf-8")
    return root


def _transcript(path: Path, prompt: str | list, *, cost_turns: int = 1) -> Path:
    rows: list[dict] = [{"type": "user", "message": {"role": "user", "content": prompt}}]
    for _ in range(cost_turns):
        rows.append(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "model": "claude-sonnet-4-5",
                    "usage": {"input_tokens": 1_000_000, "output_tokens": 0},
                    "content": [{"type": "tool_use", "id": f"t{len(rows)}", "name": "WebFetch", "input": {}}],
                },
            }
        )
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def _payload(transcript: Path, cwd: Path | str, session_id: str = "sess-1") -> io.StringIO:
    return io.StringIO(json.dumps({"session_id": session_id, "transcript_path": str(transcript), "cwd": str(cwd)}))


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


# -- mode from the prompt -----------------------------------------------------


@pytest.mark.parametrize(
    ("prompt", "mode"),
    [
        (RESEARCH_PROMPT, "research"),
        ("You are Scout running in DREAMING mode — the evening session.", "dreaming"),
        (SLOT_PROMPT, "midday-consolidation"),
        ([{"type": "text", "text": RESEARCH_PROMPT}], "research"),
        ("summarize my inbox", None),
    ],
)
def test_mode_from_the_first_user_message(tmp_path: Path, prompt: str | list, mode: str | None) -> None:
    assert run_context.mode_from_prompt(_transcript(tmp_path / "t.jsonl", prompt)) == mode


def test_only_the_first_user_message_is_the_prompt(tmp_path: Path) -> None:
    t = tmp_path / "t.jsonl"
    rows = [
        {"message": {"role": "user", "content": "hello"}},
        {"message": {"role": "user", "content": RESEARCH_PROMPT}},
    ]
    t.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    assert run_context.mode_from_prompt(t) is None


def test_no_mode_is_inferred_outside_a_vault(tmp_path: Path) -> None:
    t = _transcript(tmp_path / "t.jsonl", RESEARCH_PROMPT)
    ctx = run_context.resolve({"cwd": str(tmp_path)}, t)
    assert ctx.mode is None
    assert ctx.vault == (Path.home() / "Scout").resolve()


def test_the_runner_env_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = _vault(tmp_path / "vault")
    monkeypatch.setenv("SCOUT_MODE", "research-manual")
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path / "env-vault"))
    ctx = run_context.resolve({"cwd": str(vault)}, _transcript(tmp_path / "t.jsonl", SLOT_PROMPT))
    assert (ctx.vault, ctx.mode, ctx.mode_source) == ((tmp_path / "env-vault").resolve(), "research-manual", "env")


# -- session-tokens -----------------------------------------------------------


def test_a_run_in_a_vault_is_attributed_and_charged_to_its_budget(tmp_path: Path) -> None:
    vault = _vault(tmp_path / "sandbox" / "vault")
    t = _transcript(tmp_path / "t.jsonl", RESEARCH_PROMPT)

    session_tokens.run(stdin=_payload(t, vault / "knowledge-base"))

    [tokens] = _rows(vault / ".scout-logs" / "session-tokens.jsonl")
    assert tokens["scout_mode"] == "research"
    assert tokens["cost_usd"] == pytest.approx(3.0)  # 1M Sonnet input tokens
    [usage] = _rows(vault / ".scout-logs" / "usage-tracker.jsonl")
    assert usage["type"] == "research"
    assert usage["budget_spent"] == pytest.approx(3.0)
    assert usage["source"] == "session-tokens" and usage["session_id"] == "sess-1"
    assert usage["model"] == "claude-sonnet-4-5"


def test_each_stop_charges_only_the_new_spend(tmp_path: Path) -> None:
    """Stop fires after every turn with the running total; the budget must not count it twice."""
    vault = _vault(tmp_path / "vault")
    t = tmp_path / "t.jsonl"
    session_tokens.run(stdin=_payload(_transcript(t, RESEARCH_PROMPT, cost_turns=1), vault))
    session_tokens.run(stdin=_payload(_transcript(t, RESEARCH_PROMPT, cost_turns=3), vault))
    session_tokens.run(stdin=_payload(_transcript(t, RESEARCH_PROMPT, cost_turns=3), vault))

    spent = [r["budget_spent"] for r in _rows(vault / ".scout-logs" / "usage-tracker.jsonl")]
    assert spent == [pytest.approx(3.0), pytest.approx(6.0)]
    # Another session's spend is its own.
    session_tokens.run(stdin=_payload(t, vault, session_id="sess-2"))
    assert sum(r["budget_spent"] for r in _rows(vault / ".scout-logs" / "usage-tracker.jsonl")) == pytest.approx(18.0)


def test_a_runner_run_writes_no_budget_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Under run-*.sh the runner and the session record cost themselves, as before."""
    vault = _vault(tmp_path / "vault")
    monkeypatch.setenv("SCOUT_MODE", "research")
    monkeypatch.setenv("SCOUT_DATA_DIR", str(vault))
    session_tokens.run(stdin=_payload(_transcript(tmp_path / "t.jsonl", RESEARCH_PROMPT), "/elsewhere"))
    assert _rows(vault / ".scout-logs" / "session-tokens.jsonl")[0]["scout_mode"] == "research"
    assert not (vault / ".scout-logs" / "usage-tracker.jsonl").exists()


def test_an_interactive_session_in_a_vault_stays_manual(tmp_path: Path) -> None:
    vault = _vault(tmp_path / "vault")
    session_tokens.run(stdin=_payload(_transcript(tmp_path / "t.jsonl", "what is on my plate today?"), vault))
    assert _rows(vault / ".scout-logs" / "session-tokens.jsonl")[0]["scout_mode"] == "manual"
    assert not (vault / ".scout-logs" / "usage-tracker.jsonl").exists()


# -- session-tool-log ---------------------------------------------------------


def test_tool_calls_of_a_prompt_named_run_are_logged_in_its_vault(tmp_path: Path) -> None:
    vault = _vault(tmp_path / "vault")
    event = session_tool_log.run(stdin=_payload(_transcript(tmp_path / "t.jsonl", RESEARCH_PROMPT), vault))
    assert event is not None and event.payload["mode"] == "research"
    [log] = list((vault / ".scout-logs").glob("connector-calls-*.jsonl"))
    assert [r["mode"] for r in _rows(log)] == ["research"]


def test_an_interactive_session_logs_no_tool_calls(tmp_path: Path) -> None:
    vault = _vault(tmp_path / "vault")
    assert session_tool_log.run(stdin=_payload(_transcript(tmp_path / "t.jsonl", "hi"), vault)) is None
    assert session_tool_log.run(stdin=_payload(_transcript(tmp_path / "u.jsonl", RESEARCH_PROMPT), tmp_path)) is None
    assert not list((vault / ".scout-logs").glob("connector-calls-*.jsonl"))
