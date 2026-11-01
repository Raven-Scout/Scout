"""`scoutctl action-items check-review-requests` (#404).

Every review request the runner pre-fetched into `.scout-cache/session-context.json`
must end the run as an action item (by `owner/repo#N` ref or PR URL), a dismissed
entry, or a run-notes skip line with a reason. The check lists the rest.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout.action_items.cli import app
from scout.action_items.review_requests import (
    ReviewCheckError,
    ReviewRequest,
    check_review_requests,
    load_review_requests,
    unaccounted,
)

runner = CliRunner()
TODAY = "2026-04-15"

PR_12 = {
    "number": 12,
    "title": "Rate limits for the widget catalog",
    "url": "https://github.com/example-org/widgets/pull/12",
    "repository": {"nameWithOwner": "example-org/widgets", "name": "widgets"},
}
PR_40 = {
    "number": 40,
    "title": "Drop the old auth header from the docs",
    "repository": {"nameWithOwner": "example-org/api", "name": "api"},
}

DAILY_HEAD = f"# Action Items — {TODAY}\n\n## 🔴 Urgent\n\n"


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "Scout"
    (data_dir / "action-items").mkdir(parents=True)
    (data_dir / ".scout-cache").mkdir(parents=True)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(data_dir))
    return data_dir


def _context(vault: Path, requests: list[dict], generated_at: str = f"{TODAY}T07:00:00") -> Path:
    path = vault / ".scout-cache" / "session-context.json"
    path.write_text(
        json.dumps({"generated_at": generated_at, "session_type": "x", "pr_review_requested": requests}),
        encoding="utf-8",
    )
    return path


def _daily(vault: Path, body: str) -> Path:
    path = vault / "action-items" / f"action-items-{TODAY}.md"
    path.write_text(DAILY_HEAD + body, encoding="utf-8")
    return path


def _req(repo: str = "example-org/api", number: int = 40) -> ReviewRequest:
    return ReviewRequest(repo=repo, number=number, title="t", url=f"https://github.com/{repo}/pull/{number}")


# ----- load_review_requests --------------------------------------------------


def test_load_reads_repo_and_number_and_derives_the_url(vault: Path) -> None:
    requests = load_review_requests(_context(vault, [PR_12, PR_40]), today=TODAY)
    assert [r.ref for r in requests] == ["example-org/widgets#12", "example-org/api#40"]
    assert requests[1].url == "https://github.com/example-org/api/pull/40"
    assert requests[1].title == "Drop the old auth header from the docs"


def test_load_falls_back_to_the_url_when_repository_is_missing(vault: Path) -> None:
    entry = {"number": 7, "url": "https://github.com/example-org/web/pull/7"}
    assert load_review_requests(_context(vault, [entry]), today=TODAY)[0].ref == "example-org/web#7"


def test_load_with_no_requests_is_empty(vault: Path) -> None:
    assert load_review_requests(_context(vault, []), today=TODAY) == []


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (None, "not found"),
        ("{not json", "not valid JSON"),
        (json.dumps([1]), "not a JSON object"),
        (json.dumps({"generated_at": f"{TODAY}T07:00:00", "pr_review_requested": {}}), "not a list"),
        (json.dumps({"pr_review_requested": []}), "no generated_at"),
        (json.dumps({"generated_at": "2026-04-14T07:00:00", "pr_review_requested": []}), "from 2026-04-14"),
        (json.dumps({"generated_at": f"{TODAY}T07:00:00", "pr_review_requested": [{"title": "x"}]}), "entry 1"),
        (json.dumps({"generated_at": f"{TODAY}T07:00:00", "pr_review_requested": ["x"]}), "entry 1"),
    ],
)
def test_load_refuses_what_it_cannot_check(vault: Path, content: str | None, reason: str) -> None:
    path = vault / ".scout-cache" / "session-context.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    with pytest.raises(ReviewCheckError, match=reason):
        load_review_requests(path, today=TODAY)


# ----- unaccounted ------------------------------------------------------------


def test_a_ref_in_a_refs_line_places_it() -> None:
    lines = ["## 🟢 Watching", "- [ ] [#APIREV] **Review the docs PR** — team queue", "  - Refs: example-org/api#40"]
    assert unaccounted([_req()], lines) == []


def test_the_pr_url_in_the_task_line_places_it() -> None:
    lines = ["## 🟡 To Do", "- [ ] [#APIREV] **Review the [docs PR](https://github.com/example-org/api/pull/40)**"]
    assert unaccounted([_req()], lines) == []


def test_matching_ignores_case_of_owner_and_repo() -> None:
    lines = ["## 🟢 Watching", "- [ ] [#APIREV] **Review it**", "  - Refs: Example-Org/API#40"]
    assert unaccounted([_req()], lines) == []


def test_a_done_item_accounts_for_it() -> None:
    lines = ["## ✅ Recently Completed", "- [x] [#APIREV] **Reviewed the docs PR** — example-org/api#40"]
    assert unaccounted([_req()], lines) == []


@pytest.mark.parametrize(
    "line",
    [
        "  - Refs: example-org/api#400",  # a longer number
        "  - Refs: example-org/api#4",  # a shorter number
        "  - Refs: other-org/example-org/api#40x",  # another repo's suffix
        "  - Refs: example-org/apis#40",  # another repo
        "  - https://github.com/example-org/api/pull/401",
    ],
)
def test_near_misses_do_not_place_it(line: str) -> None:
    lines = ["## 🟢 Watching", "- [ ] [#APIREV] **Something else**", line]
    assert unaccounted([_req()], lines) == [_req()]


@pytest.mark.parametrize(
    "heading",
    ["## 💡 Today's Focus", "## 🎯 Today's Focus", "## 🪵 Run notes & connector availability", "## 📥 Inbox"],
)
def test_mentions_outside_the_tiers_do_not_place_it(heading: str) -> None:
    lines = [heading, "- Review example-org/api#40 today"]
    assert unaccounted([_req()], lines) == [_req()]


def test_a_skip_line_with_a_reason_accounts_for_it() -> None:
    lines = [
        "## 🪵 Run notes & connector availability",
        "- **2026-04-15 07:00 UTC** (morning-consolidation) — 1 new.",
        "  - Skipped review request example-org/api#40 — merged before this run classified it",
    ]
    assert unaccounted([_req()], lines) == []


def test_a_skip_line_may_name_the_url() -> None:
    lines = ["- Skipped review request https://github.com/example-org/api/pull/40 — bot PR, auto-merges"]
    assert unaccounted([_req()], lines) == []


@pytest.mark.parametrize(
    "line",
    [
        "- Skipped review request example-org/api#40",  # no reason
        "- Skipped review request example-org/api#40 — ",  # empty reason
        "- Skipped review request example-org/api#40 — ...",  # no words
        "- Skipped review request example-org/api#41 — wrong PR",
    ],
)
def test_a_skip_without_a_reason_or_for_another_pr_does_not(line: str) -> None:
    assert unaccounted([_req()], [line]) == [_req()]


def test_a_skip_inside_a_code_fence_does_not_count() -> None:
    lines = ["```", "- Skipped review request example-org/api#40 — example only", "```"]
    assert unaccounted([_req()], lines) == [_req()]


def test_a_dismissed_url_accounts_for_it() -> None:
    assert unaccounted([_req()], [], dismissed=["https://github.com/example-org/api/pull/40/"]) == []


def test_only_the_missing_ones_are_listed_in_order() -> None:
    a, b, c = _req(number=1), _req(number=2), _req(number=3)
    lines = ["## 🟡 To Do", "- [ ] [#TWO] **Review two**", "  - Refs: example-org/api#2"]
    assert unaccounted([a, b, c], lines) == [a, c]


# ----- check_review_requests ---------------------------------------------------


def test_check_reads_the_vault(vault: Path) -> None:
    _context(vault, [PR_12, PR_40])
    _daily(vault, "- [ ] [#RV12] **Review the rate-limit PR**\n  - Refs: example-org/widgets#12\n")
    (vault / "action-items" / "dismissed.md").write_text("# Dismissed\n\n- https://example.com/x\n", encoding="utf-8")
    result = check_review_requests(data_dir=vault, today=TODAY)
    assert [r.ref for r in result.checked] == ["example-org/widgets#12", "example-org/api#40"]
    assert [r.ref for r in result.unaccounted] == ["example-org/api#40"]


def test_check_without_requests_needs_no_daily_file(vault: Path) -> None:
    _context(vault, [])
    assert check_review_requests(data_dir=vault, today=TODAY).unaccounted == ()


def test_check_without_todays_file_refuses(vault: Path) -> None:
    _context(vault, [PR_40])
    with pytest.raises(ReviewCheckError, match="no action-items file"):
        check_review_requests(data_dir=vault, today=TODAY)


# ----- CLI --------------------------------------------------------------------


def _cli(vault: Path, *extra: str):
    return runner.invoke(app, ["check-review-requests", "--data-dir", str(vault), "--date", TODAY, *extra])


def test_cli_exit_0_when_all_are_accounted_for(vault: Path) -> None:
    _context(vault, [PR_40])
    _daily(vault, "- [ ] [#APIREV] **Review the docs PR**\n  - Refs: example-org/api#40\n")
    result = _cli(vault)
    assert result.exit_code == 0, result.output
    assert "all 1 pre-fetched review request" in result.output


def test_cli_exit_0_when_none_were_fetched(vault: Path) -> None:
    _context(vault, [])
    result = _cli(vault)
    assert result.exit_code == 0
    assert "no review requests" in result.output


def test_cli_exit_1_lists_each_missing_request(vault: Path) -> None:
    _context(vault, [PR_12, PR_40])
    _daily(vault, "- [ ] [#RV12] **Review the rate-limit PR**\n  - Refs: example-org/widgets#12\n")
    result = _cli(vault)
    assert result.exit_code == 1
    assert "1 of 2" in result.output
    listed = "example-org/api#40 — Drop the old auth header from the docs — https://github.com/example-org/api/pull/40"
    assert listed in result.output
    assert "Skipped review request <ref> — <reason>" in result.output


def test_cli_exit_2_when_it_cannot_check(vault: Path) -> None:
    result = _cli(vault)
    assert result.exit_code == 2
    assert "cannot check" in result.output


def test_cli_json(vault: Path) -> None:
    _context(vault, [PR_40])
    _daily(vault, "")
    result = _cli(vault, "--json")
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["checked"] == 1
    assert payload["unaccounted"] == [
        {
            "ref": "example-org/api#40",
            "url": "https://github.com/example-org/api/pull/40",
            "title": "Drop the old auth header from the docs",
        }
    ]


def test_cli_json_error(vault: Path) -> None:
    result = _cli(vault, "--json")
    assert result.exit_code == 2
    assert "not found" in json.loads(result.stdout)["error"]


def test_cli_rejects_a_bad_date(vault: Path) -> None:
    result = runner.invoke(app, ["check-review-requests", "--data-dir", str(vault), "--date", "nope"])
    assert result.exit_code == 2
    assert "invalid date" in result.output


def test_cli_defaults_to_the_configured_today_and_context(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import datetime as dt

    monkeypatch.setattr("scout.config.today", lambda _data=None: dt.date.fromisoformat(TODAY))
    _context(vault, [PR_40])
    _daily(vault, "- [ ] [#APIREV] **Review the docs PR**\n  - Refs: example-org/api#40\n")
    result = runner.invoke(app, ["check-review-requests"])
    assert result.exit_code == 0, result.output


def test_cli_context_override(vault: Path, tmp_path: Path) -> None:
    other = tmp_path / "ctx.json"
    other.write_text(json.dumps({"generated_at": f"{TODAY}T01:00:00", "pr_review_requested": []}), encoding="utf-8")
    result = _cli(vault, "--context", str(other))
    assert result.exit_code == 0
