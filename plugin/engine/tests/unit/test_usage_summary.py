"""`scoutctl budget usage --json`: Scout's own scheduled-run usage, for the app's usage card.

``tests/fixtures/contract/usage-summary.json`` is the canonical copy of the JSON
shape; the app keeps a byte copy at ``apps/macos/ScoutTests/Fixtures/usage-summary.json``
(``contract.yml`` compares them). Set ``SCOUT_REGEN_CONTRACT=1`` to rewrite the
canonical file from the deterministic vault below, then ``cp`` it over the app copy.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout.scripts import usage_summary as us

# Read at import: tests/conftest.py scrubs SCOUT_* from the environment before each test runs.
REGEN = os.environ.get("SCOUT_REGEN_CONTRACT") == "1"
CANONICAL = Path(__file__).resolve().parents[1] / "fixtures" / "contract" / "usage-summary.json"

# Wednesday 2026-04-29, 15:00 in New York.
NOW = datetime(2026, 4, 29, 19, 0, tzinfo=UTC)

SID_MORNING = "11111111-1111-4111-8111-111111111111"
SID_RETRY_1 = "22222222-2222-4222-8222-222222222221"
SID_RETRY_2 = "22222222-2222-4222-8222-222222222222"
SID_MONDAY = "33333333-3333-4333-8333-333333333333"
SID_INTERACTIVE = "44444444-4444-4444-8444-444444444444"
SID_ELSEWHERE = "55555555-5555-4555-8555-555555555555"


def _usage(inp: int, out: int, cache_read: int = 0, cache_write: int = 0) -> dict:
    return {
        "input_tokens": inp,
        "output_tokens": out,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_write,
    }


def _transcript(path: Path, messages: list[tuple[str, dict, int]]) -> None:
    """Write a transcript where each message's usage repeats on *n* content-block rows, as Claude Code does."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"type": "custom-title", "customTitle": "scout-run"})]
    for msg_id, usage, blocks in messages:
        for i in range(blocks):
            lines.append(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "id": msg_id,
                            "model": "claude-sonnet-5-5",
                            "content": [{"type": "text", "text": f"block {i}"}],
                            "usage": usage,
                        },
                    }
                )
            )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _row(ts: datetime, **fields: object) -> str:
    row = {"ts": ts.strftime("%Y-%m-%dT%H:%M:%SZ"), "type": "morning-briefing", "budget_cap": 8.0, "exit_code": 0}
    row.update(fields)
    return json.dumps(row)


def _project_dir(home: Path, cwd: Path) -> Path:
    return home / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(cwd))


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.fixture
def vault(fake_data_dir: Path, home: Path) -> Path:
    """A vault with three finished runs this week, an interactive session and another project's session."""
    (fake_data_dir / "scout-config.yaml").write_text("user:\n  timezone: America/New_York\n", encoding="utf-8")
    proj = _project_dir(home, fake_data_dir)

    # Today 08:00 ET: one run, two messages, each repeated over 3 content blocks.
    _transcript(
        proj / f"{SID_MORNING}.jsonl",
        [("msg_a", _usage(100, 50, 10_000, 2_000), 3), ("msg_b", _usage(20, 30, 12_000, 0), 3)],
    )
    # Today 13:00 ET: a run that needed a retry; the first attempt was priced from usage.
    _transcript(proj / f"{SID_RETRY_1}.jsonl", [("msg_c", _usage(5, 5, 1_000, 0), 2)])
    _transcript(proj / f"{SID_RETRY_2}.jsonl", [("msg_d", _usage(7, 3, 2_000, 500), 4)])
    # A subagent of the retried run.
    _transcript(proj / SID_RETRY_2 / "subagents" / "agent-1.jsonl", [("msg_e", _usage(1, 1, 100, 0), 2)])
    # Monday: a run in the week, not today.
    _transcript(proj / f"{SID_MONDAY}.jsonl", [("msg_f", _usage(1_000, 1_000, 0, 0), 2)])
    # The owner's interactive session in the vault: no tracker row names it.
    _transcript(proj / f"{SID_INTERACTIVE}.jsonl", [("msg_g", _usage(9_999_999, 9_999_999, 9_999_999, 0), 5)])
    # A session in another project on the Mac, named nowhere.
    _transcript(
        _project_dir(home, home / "code" / "other") / f"{SID_ELSEWHERE}.jsonl",
        [("msg_h", _usage(8_888_888, 8_888_888, 0, 0), 5)],
    )

    rows = [
        # Last Saturday (outside this week), a priced run.
        _row(NOW - timedelta(days=4), source="runner", budget_spent=9.0, cost_source="transcript", sessions=[]),
        # Monday 12:00 ET: a run from before runner rows listed sessions, $0 and unpriced.
        _row(datetime(2026, 4, 27, 16, 0, tzinfo=UTC), source="runner", budget_spent=0.0),
        # Monday 18:00 ET: Claude's own figure.
        _row(
            datetime(2026, 4, 27, 22, 0, tzinfo=UTC),
            source="runner",
            budget_spent=1.5,
            cost_source="transcript",
            measured_attempts=1,
            attempts=1,
            sessions=[SID_MONDAY],
        ),
        # Today 08:00 ET: the session's own $0 row, then the runner's measured row.
        _row(datetime(2026, 4, 29, 12, 0, tzinfo=UTC), source="session", budget_spent=0.0),
        _row(
            datetime(2026, 4, 29, 12, 5, tzinfo=UTC),
            source="runner",
            budget_spent=2.25,
            cost_source="transcript",
            measured_attempts=1,
            attempts=1,
            sessions=[SID_MORNING],
            run_started="2026-04-29T11:30:00Z",
        ),
        # Today 13:00 ET: the failed attempt's row, then the runner's row that claims it.
        _row(
            datetime(2026, 4, 29, 17, 0, tzinfo=UTC),
            source="attempt",
            budget_spent=0.4,
            cost_source="usage",
            session_id=SID_RETRY_1,
            run_started="2026-04-29T16:30:00Z",
        ),
        _row(
            datetime(2026, 4, 29, 17, 30, tzinfo=UTC),
            source="runner",
            budget_spent=1.1,
            cost_source="transcript",
            measured_attempts=1,
            estimated_attempts=1,
            attempts=2,
            sessions=[SID_RETRY_1, SID_RETRY_2],
            run_started="2026-04-29T16:30:00Z",
        ),
        # Schedule ticks and malformed lines are not runs.
        json.dumps({"ts": "2026-04-29T18:00:00Z", "source": "schedule.tick", "type": "tick"}),
        "not json",
    ]
    (fake_data_dir / ".scout-logs" / "usage-tracker.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return fake_data_dir


def _render(vault: Path, home: Path) -> str:
    return us.dumps(us.summarize(vault, now=NOW, home=home))


def test_usage_summary_matches_the_shared_fixture(vault: Path, home: Path) -> None:
    text = _render(vault, home)
    if REGEN:
        CANONICAL.write_text(text, encoding="utf-8")
    assert CANONICAL.read_text(encoding="utf-8") == text, (
        "regenerate with SCOUT_REGEN_CONTRACT=1, then cp it to apps/macos/ScoutTests/Fixtures/usage-summary.json"
    )


def test_the_fixture_exercises_the_contract_shape(vault: Path, home: Path) -> None:
    doc = json.loads(_render(vault, home))
    assert set(doc) == {"schema_version", "scope", "timezone", "generated_at", "today", "week"}
    assert doc["schema_version"] == us.SCHEMA_VERSION == 1
    assert doc["scope"] == "scheduled-runs"
    assert doc["timezone"] == "America/New_York"
    assert doc["generated_at"] == "2026-04-29T19:00:00Z"
    for key in ("today", "week"):
        assert set(doc[key]) == {
            "start",
            "end",
            "runs",
            "runs_with_tokens",
            "sessions",
            "tokens",
            "cost_usd",
            "cost_kind",
            "priced_runs",
        }
        assert set(doc[key]["tokens"]) == {"input", "output", "cache_read", "cache_creation"}
    assert doc["today"]["start"] == "2026-04-29T00:00:00-04:00"
    assert doc["week"]["start"] == "2026-04-27T00:00:00-04:00"


def test_repeated_message_rows_count_once(vault: Path, home: Path) -> None:
    """Each message's usage repeats on every content-block row; summing the rows would triple today's tokens."""
    today = us.summarize(vault, now=NOW, home=home)["today"]
    assert today["tokens"] == {
        "input": 100 + 20 + 5 + 7 + 1,
        "output": 50 + 30 + 5 + 3 + 1,
        "cache_read": 10_000 + 12_000 + 1_000 + 2_000 + 100,
        "cache_creation": 2_000 + 500,
    }


def test_only_scouts_scheduled_runs_count(vault: Path, home: Path) -> None:
    """The interactive vault session and another project's session are on disk but named by no runner row."""
    summary = us.summarize(vault, now=NOW, home=home)
    for period in ("today", "week"):
        tokens = summary[period]["tokens"]
        assert tokens["input"] < 9_999_999
        assert tokens["output"] < 8_888_888
    assert summary["today"]["sessions"] == 3  # the morning run, and both attempts of the retried run
    assert summary["week"]["sessions"] == 4


def test_runs_and_cost_come_from_the_tracker(vault: Path, home: Path) -> None:
    summary = us.summarize(vault, now=NOW, home=home)
    today, week = summary["today"], summary["week"]
    assert (today["runs"], today["runs_with_tokens"], today["priced_runs"]) == (2, 2, 2)
    # The runner rows stand in for the session's $0 row and the attempt row they claim.
    assert today["cost_usd"] == pytest.approx(2.25 + 1.1)
    assert today["cost_kind"] == "priced"  # one run had an estimated attempt
    assert (week["runs"], week["runs_with_tokens"], week["priced_runs"]) == (4, 3, 3)
    assert week["cost_usd"] == pytest.approx(2.25 + 1.1 + 1.5)
    assert week["cost_kind"] == "partial"  # Monday's legacy $0 row is not a measurement


@pytest.mark.parametrize(
    ("row", "kind"),
    [
        ({"cost_source": "transcript", "measured_attempts": 1}, "reported"),
        ({"cost_source": "transcript", "measured_attempts": 1, "estimated_attempts": 1}, "priced"),
        ({}, "unpriced"),
        ({"cost_kind": "unpriced", "cost_source": "transcript"}, "unpriced"),
        ({"cost_kind": "priced"}, "priced"),
        ({"cost_kind": "made-up", "cost_source": "transcript"}, "reported"),
    ],
)
def test_a_runner_rows_cost_kind(row: dict, kind: str) -> None:
    assert us.row_cost_kind(row) == kind


@pytest.mark.parametrize(
    ("kinds", "period"),
    [
        ([], "none"),
        (["reported", "reported"], "reported"),
        (["reported", "priced"], "priced"),
        (["unpriced"], "unpriced"),
        (["reported", "unpriced"], "partial"),
    ],
)
def test_a_periods_cost_kind(kinds: list[str], period: str) -> None:
    assert us._period_kind(kinds) == period


def test_an_empty_vault_reports_no_runs_and_no_cost(fake_data_dir: Path, home: Path) -> None:
    today = us.summarize(fake_data_dir, now=NOW, home=home)["today"]
    assert today["runs"] == 0
    assert today["tokens"] == {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0}
    assert (today["cost_usd"], today["cost_kind"]) == (0, "none")


def test_an_unsafe_session_id_is_never_opened(fake_data_dir: Path, home: Path) -> None:
    proj = _project_dir(home, fake_data_dir)
    _transcript(proj.parent / "escape.jsonl", [("msg_x", _usage(5, 5), 1)])
    (fake_data_dir / ".scout-logs" / "usage-tracker.jsonl").write_text(
        _row(NOW, source="runner", budget_spent=0.0, sessions=["../escape", "", 7]) + "\n", encoding="utf-8"
    )
    today = us.summarize(fake_data_dir, now=NOW, home=home)["today"]
    assert (today["runs"], today["runs_with_tokens"], today["sessions"]) == (1, 0, 0)


def test_the_cli_prints_the_json_summary(vault: Path, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.cli import app

    monkeypatch.setenv("HOME", str(home))
    result = CliRunner().invoke(app, ["budget", "usage", "--json"])
    assert result.exit_code == 0, result.output
    doc = json.loads(result.output)
    assert doc["scope"] == "scheduled-runs"
    assert set(doc["today"]["tokens"]) == {"input", "output", "cache_read", "cache_creation"}

    text = CliRunner().invoke(app, ["budget", "usage"])
    assert text.exit_code == 0, text.output
    assert "Scout's scheduled runs" in text.output
