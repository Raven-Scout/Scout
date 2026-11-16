"""`scoutctl action-items check-red-polls`.

The runner pre-fetches every 🔴 project's `slack_channels:` into
`.scout-cache/session-context.json`. Each channel must appear, by `#name` or
id, on a `🔴 channels polled: …` line in today's file. A 🔴 project with no
channels is reported, not failed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from scout.action_items.cli import app
from scout.action_items.prefetched import PrefetchCheckError
from scout.action_items.red_polls import RedChannel, check_red_polls

runner = CliRunner()
TODAY = "2026-04-15"
WIDGETS = {
    "project": "Widgets launch",
    "path": "knowledge-base/projects/widgets/widgets.md",
    "visibility": "normal",
    "channels": [{"id": "C0123456789", "name": "#widgets-launch"}, {"id": "C0987654321", "name": ""}],
}
BILLING = {
    "project": "Billing cleanup",
    "path": "knowledge-base/projects/b/b.md",
    "visibility": "normal",
    "channels": [],
}

HEAD = f"# Action Items — {TODAY}\n\n## 🔴 Urgent\n\n- [ ] [#QUOTA] **Answer Priya** — reply\n\n"


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "Scout"
    (data_dir / "action-items").mkdir(parents=True)
    (data_dir / ".scout-cache").mkdir(parents=True)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(data_dir))
    return data_dir


def _context(vault: Path, projects: list[dict] | None, **extra: Any) -> Path:
    path = vault / ".scout-cache" / "session-context.json"
    data: dict[str, Any] = {"generated_at": f"{TODAY}T07:00:00", "session_type": "morning-briefing", **extra}
    if projects is not None:
        data["red_project_channels"] = projects
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _daily(vault: Path, notes: str) -> Path:
    path = vault / "action-items" / f"action-items-{TODAY}.md"
    path.write_text(
        HEAD
        + "## 🪵 Run notes & connector availability\n\n- **2026-04-15 07:40 UTC** (morning-briefing) — ok\n"
        + notes,
        encoding="utf-8",
    )
    return path


def test_every_channel_listed_by_name_or_id_passes(vault: Path) -> None:
    _context(vault, [WIDGETS])
    _daily(vault, "  - 🔴 channels polled: #widgets-launch, C0987654321\n")
    result = check_red_polls(today=TODAY)
    assert result.unpolled == () and len(result.channels) == 2


def test_the_id_alone_covers_a_named_channel(vault: Path) -> None:
    _context(vault, [WIDGETS])
    _daily(vault, "- 🔴 channels polled: C0123456789, C0987654321 (unavailable: rate limited)\n")
    assert check_red_polls(today=TODAY).unpolled == ()


def test_a_channel_not_listed_is_named(vault: Path) -> None:
    _context(vault, [WIDGETS])
    _daily(vault, "- 🔴 channels polled: #widgets-launch\n")
    assert check_red_polls(today=TODAY).unpolled == (RedChannel("Widgets launch", "C0987654321", ""),)


def test_naming_the_channel_outside_a_polled_line_does_not_count(vault: Path) -> None:
    _context(vault, [WIDGETS])
    _daily(vault, "- read #widgets-launch and C0987654321 via search\n")
    assert len(check_red_polls(today=TODAY).unpolled) == 2


def test_a_near_miss_name_does_not_count(vault: Path) -> None:
    _context(vault, [WIDGETS])
    _daily(vault, "- 🔴 channels polled: #widgets-launch-old, C09876543210\n")
    assert len(check_red_polls(today=TODAY).unpolled) == 2


def test_a_polled_line_in_a_code_fence_does_not_count(vault: Path) -> None:
    _context(vault, [WIDGETS])
    _daily(vault, "```\n🔴 channels polled: #widgets-launch, C0987654321\n```\n")
    assert len(check_red_polls(today=TODAY).unpolled) == 2


def test_a_red_project_without_channels_is_reported_not_failed(vault: Path) -> None:
    _context(vault, [BILLING])
    result = check_red_polls(today=TODAY)  # no daily file needed: nothing to poll
    assert result.channels == () and result.unpolled == ()
    assert result.unconfigured == ("Billing cleanup",)


def test_a_context_from_an_older_engine_has_nothing_to_check(vault: Path) -> None:
    _context(vault, None)
    assert check_red_polls(today=TODAY).channels == ()


@pytest.mark.parametrize(
    ("projects", "reason"),
    [("nope", "not a list"), (["nope"], "entry 1 is not an object")],
)
def test_it_refuses_a_malformed_list(vault: Path, projects: Any, reason: str) -> None:
    _context(vault, projects)
    with pytest.raises(PrefetchCheckError, match=reason):
        check_red_polls(today=TODAY)


def test_it_refuses_without_todays_file(vault: Path) -> None:
    _context(vault, [WIDGETS])
    with pytest.raises(PrefetchCheckError, match="no action-items file"):
        check_red_polls(today=TODAY)


def test_labels() -> None:
    assert RedChannel("p", "C0123456789", "#a").label == "#a (C0123456789)"
    assert RedChannel("p", "C0123456789", "").label == "C0123456789"
    assert RedChannel("p", "", "#a").label == "#a"


# ----- CLI -----------------------------------------------------------------------------


def _cli(*args: str) -> Any:
    return runner.invoke(app, ["check-red-polls", "--date", TODAY, *args])


def test_cli_exit_0_when_all_are_listed(vault: Path) -> None:
    _context(vault, [WIDGETS, BILLING])
    _daily(vault, "  - 🔴 channels polled: #widgets-launch, C0987654321\n")
    result = _cli()
    assert result.exit_code == 0
    assert "all 2 🔴 project channels listed as polled" in result.stdout
    assert "🔴 project Billing cleanup has no slack_channels:" in result.stdout


def test_cli_exit_0_when_nothing_declares_a_channel(vault: Path) -> None:
    _context(vault, [])
    result = _cli()
    assert result.exit_code == 0 and "no 🔴 project declares a Slack channel" in result.stdout


def test_cli_exit_1_names_each_unpolled_channel(vault: Path) -> None:
    _context(vault, [WIDGETS])
    _daily(vault, "")
    result = _cli()
    assert result.exit_code == 1
    assert "2 of 2 🔴 project channels are not listed as polled" in result.stdout
    assert "#widgets-launch (C0123456789) — Widgets launch" in result.stdout
    assert "`🔴 channels polled: #a, #b`" in result.stdout


def test_cli_exit_2_when_it_cannot_check(vault: Path) -> None:
    result = _cli()
    assert result.exit_code == 2 and "check-red-polls: cannot check" in result.stdout


def test_cli_json(vault: Path) -> None:
    _context(vault, [WIDGETS, BILLING])
    _daily(vault, "- 🔴 channels polled: #widgets-launch\n")
    result = _cli("--json")
    assert result.exit_code == 1
    assert json.loads(result.stdout) == {
        "checked": 2,
        "unpolled": [{"project": "Widgets launch", "id": "C0987654321", "name": ""}],
        "unconfigured": ["Billing cleanup"],
        "has_entry": True,
    }


# ----- only this run's Run notes entry counts ---------------------------------------

MORNING = "- **2026-04-15 07:40 UTC** (morning-briefing) — ok"


def _two_runs(vault: Path, afternoon: str) -> None:
    path = vault / "action-items" / f"action-items-{TODAY}.md"
    path.write_text(
        HEAD
        + "## 🪵 Run notes & connector availability\n\n"
        + afternoon
        + f"{MORNING}\n  - 🔴 channels polled: #widgets-launch, C0987654321\n",
        encoding="utf-8",
    )


def test_an_earlier_runs_polled_line_does_not_count(vault: Path) -> None:
    _context(vault, [WIDGETS], run_notes_before=[MORNING])
    _two_runs(vault, "- **2026-04-15 13:05 UTC** (midday-consolidation) — ok\n")
    result = check_red_polls(today=TODAY)
    assert len(result.unpolled) == 2 and result.has_entry


def test_this_runs_entry_counts(vault: Path) -> None:
    _context(vault, [WIDGETS], run_notes_before=[MORNING])
    _two_runs(
        vault,
        "- **2026-04-15 13:05 UTC** (midday-consolidation) — ok\n  - 🔴 channels polled: C0123456789, C0987654321\n",
    )
    assert check_red_polls(today=TODAY).unpolled == ()


def test_no_entry_from_this_run_yet_is_said(vault: Path) -> None:
    _context(vault, [WIDGETS], run_notes_before=[MORNING])
    _two_runs(vault, "")
    result = check_red_polls(today=TODAY)
    assert not result.has_entry and len(result.unpolled) == 2
    cli = _cli()
    assert cli.exit_code == 1 and "no entry from this run yet" in cli.stdout


def test_cli_json_error(vault: Path) -> None:
    result = _cli("--json")
    assert result.exit_code == 2 and "not found" in json.loads(result.stdout)["error"]


def test_cli_rejects_a_bad_date(vault: Path) -> None:
    result = runner.invoke(app, ["check-red-polls", "--date", "tomorrow"])
    assert result.exit_code == 2 and "invalid date" in result.stdout
