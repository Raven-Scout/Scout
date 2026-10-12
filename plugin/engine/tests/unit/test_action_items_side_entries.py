"""Inbox and Loose ends keys, dismissed.md and topic-names.md against the shared
fixture the app also tests (`topic-board-files.json`), plus the materialize filter."""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

import pytest

from scout.action_items.dismissed import parse_dismissed
from scout.action_items.materialize import materialize
from scout.action_items.side_entries import drop_dismissed, is_side_section, side_entry_key
from scout.action_items.topic_names import parse_topic_names

FIXTURE = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "contract" / "topic-board-files.json").read_text(
        encoding="utf-8"
    )
)
SLACK = "https://acme-co.slack.com/archives/C0123456789/p1700000000000000"


@pytest.mark.parametrize("case", FIXTURE["side_entries"], ids=lambda c: c["bullet"][:40])
def test_side_entry_key_matches_the_fixture(case: dict[str, str]) -> None:
    assert side_entry_key(case["bullet"]) == case["key"]


def test_dismissed_md_matches_the_fixture() -> None:
    case = FIXTURE["dismissed_md"]
    assert parse_dismissed(case["text"].splitlines()) == case["keys"]


def test_topic_names_md_matches_the_fixture() -> None:
    case = FIXTURE["topic_names_md"]
    assert parse_topic_names(case["text"]) == case["names"]


@pytest.mark.parametrize("case", FIXTURE["side_sections"], ids=lambda c: c["heading"])
def test_is_side_section_matches_the_fixture(case: dict[str, object]) -> None:
    assert is_side_section(str(case["heading"])) is case["side"]


def test_a_level_three_heading_is_not_a_side_section() -> None:
    assert is_side_section("### 📥 Inbox") is False


def test_drop_dismissed_takes_the_lines_under_an_entry_too() -> None:
    lines = [
        "## 🧹 Loose ends",
        "- Close the stale draft",
        "  - it was superseded",
        "",
        "- Keep this one",
        "  - and its note",
    ]
    kept = drop_dismissed(lines, {"Close the stale draft"})
    assert kept == ["## 🧹 Loose ends", "", "- Keep this one", "  - and its note"]


def test_drop_dismissed_keeps_tasks_and_other_sections() -> None:
    lines = [
        "## 📥 Inbox",
        f"- Slack · Oct 10 — [Priya asked]({SLACK})",
        "- Close the stale draft",
        f"- [ ] [#AAAA] **A task that links {SLACK}**",
        "## 🟢 Watching",
        "- Close the stale draft",
        "## 🧹 Loose ends",
        "# Notes",
        "- Close the stale draft",
    ]
    assert drop_dismissed(lines, {SLACK, "Close the stale draft"}) == [
        "## 📥 Inbox",
        f"- [ ] [#AAAA] **A task that links {SLACK}**",
        "## 🟢 Watching",
        "- Close the stale draft",
        "## 🧹 Loose ends",
        "# Notes",
        "- Close the stale draft",
    ]
    assert drop_dismissed(lines, set()) is lines


def test_materialize_drops_dismissed_entries(tmp_path: Path) -> None:
    items = tmp_path / "action-items"
    items.mkdir()
    (items / "dismissed.md").write_text(f"# Dismissed\n\n- {SLACK}\n", encoding="utf-8")
    (items / "action-items-2026-07-05.md").write_text(
        "# Action Items — Sunday, Jul 5, 2026\n"
        "\n"
        "## 🔴 Urgent\n"
        "- [ ] [#AAAA] **call the bank**\n"
        "\n"
        "## 📥 Inbox\n"
        f"- Slack · Oct 10 — [Priya asked]({SLACK})\n"
        "- GitHub · Oct 9 — Review requested https://github.com/example-org/web/pull/7\n",
        encoding="utf-8",
    )

    created = materialize(data_dir=tmp_path, date=_dt.date(2026, 7, 6))

    assert created is not None
    text = created.read_text(encoding="utf-8")
    assert SLACK not in text
    assert "- GitHub · Oct 9 — Review requested https://github.com/example-org/web/pull/7" in text
    assert "- [ ] [#AAAA] **call the bank**" in text


def test_materialize_carries_verbatim_when_dismissed_is_unreadable(tmp_path: Path) -> None:
    items = tmp_path / "action-items"
    items.mkdir()
    (items / "dismissed.md").mkdir()
    body = f"## 📥 Inbox\n- Slack · Oct 10 — [Priya asked]({SLACK})\n"
    (items / "action-items-2026-07-05.md").write_text("# Action Items\n" + body, encoding="utf-8")

    created = materialize(data_dir=tmp_path, date=_dt.date(2026, 7, 6))

    assert created is not None
    assert SLACK in created.read_text(encoding="utf-8")
