"""Unit tests for scout.scripts.pre_session_data.

Closes #74 (the pre-session-data slice) and #76 (kb_pre_filter double-open
fix gets the mtime-cached extraction it needed).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scout.scripts.pre_session_data import (
    KB_DATES_CACHE_FILENAME,
    SessionContext,
    extract_last_updated,
    gather,
    gather_kb_file_dates,
    write_context,
)

# ----- last-updated extraction --------------------------------------------


def test_extract_last_updated_with_colon(tmp_path: Path) -> None:
    md = tmp_path / "doc.md"
    md.write_text("# Title\n**Last updated:** May 27, 2026 2:30 PM ET\nbody\n")
    assert extract_last_updated(md) == "May 27, 2026 2:30 PM ET"


def test_extract_last_updated_case_insensitive(tmp_path: Path) -> None:
    md = tmp_path / "doc.md"
    md.write_text("# Title\nLast VERIFIED: 2026-05-25\n")
    assert extract_last_updated(md) == "2026-05-25"


def test_extract_last_updated_returns_empty_when_absent(tmp_path: Path) -> None:
    md = tmp_path / "doc.md"
    md.write_text("# Title\nno date line here\n")
    assert extract_last_updated(md) == ""


def test_extract_last_updated_only_scans_first_five_lines(tmp_path: Path) -> None:
    md = tmp_path / "doc.md"
    md.write_text("line1\nline2\nline3\nline4\nline5\nLast updated: should be ignored\n")
    assert extract_last_updated(md) == ""


# ----- KB walking + cache --------------------------------------------------


def test_gather_kb_file_dates_walks_and_caches(tmp_path: Path) -> None:
    scout_dir = tmp_path / "vault"
    kb = scout_dir / "knowledge-base"
    kb.mkdir(parents=True)
    (kb / "foo.md").write_text("# foo\nLast updated: May 1, 2026\n")
    (kb / "bar.md").write_text("# bar\nNo date here\n")

    cache_path = tmp_path / ".scout-cache" / KB_DATES_CACHE_FILENAME
    out = gather_kb_file_dates(kb, scout_dir=scout_dir, cache_path=cache_path)

    assert "knowledge-base/foo.md" in out
    assert out["knowledge-base/foo.md"] == "May 1, 2026"
    # bar.md has no date — it's still cached (negative entry) but absent from
    # the returned dict to match bash's "only emit non-empty" behavior.
    assert "knowledge-base/bar.md" not in out
    assert cache_path.exists()
    cached = json.loads(cache_path.read_text())
    assert "knowledge-base/bar.md" in cached  # negative cache entry stored


def test_gather_kb_file_dates_reuses_cache_when_mtime_unchanged(tmp_path: Path) -> None:
    scout_dir = tmp_path / "vault"
    kb = scout_dir / "knowledge-base"
    kb.mkdir(parents=True)
    md = kb / "foo.md"
    md.write_text("# foo\nLast updated: May 1, 2026\n")

    cache_path = tmp_path / ".scout-cache" / KB_DATES_CACHE_FILENAME
    gather_kb_file_dates(kb, scout_dir=scout_dir, cache_path=cache_path)

    # Corrupt the file but preserve mtime — cache should still serve original.
    original_mtime_ns = md.stat().st_mtime_ns
    md.write_text("# foo\nLast updated: TOTALLY DIFFERENT\n")
    os.utime(md, ns=(original_mtime_ns, original_mtime_ns))

    out = gather_kb_file_dates(kb, scout_dir=scout_dir, cache_path=cache_path)
    assert out["knowledge-base/foo.md"] == "May 1, 2026"


def test_gather_kb_file_dates_re_extracts_on_mtime_bump(tmp_path: Path) -> None:
    scout_dir = tmp_path / "vault"
    kb = scout_dir / "knowledge-base"
    kb.mkdir(parents=True)
    md = kb / "foo.md"
    md.write_text("# foo\nLast updated: May 1, 2026\n")
    cache_path = tmp_path / ".scout-cache" / KB_DATES_CACHE_FILENAME
    gather_kb_file_dates(kb, scout_dir=scout_dir, cache_path=cache_path)

    md.write_text("# foo\nLast updated: May 28, 2026\n")
    out = gather_kb_file_dates(kb, scout_dir=scout_dir, cache_path=cache_path)
    assert out["knowledge-base/foo.md"] == "May 28, 2026"


def test_gather_kb_file_dates_excludes_ontology_and_archive(tmp_path: Path) -> None:
    scout_dir = tmp_path / "vault"
    kb = scout_dir / "knowledge-base"
    (kb / "ontology").mkdir(parents=True)
    (kb / "archive").mkdir(parents=True)
    (kb / "ontology" / "schema.md").write_text("Last updated: May 1, 2026\n")
    (kb / "archive" / "old.md").write_text("Last updated: May 1, 2026\n")
    (kb / "current.md").write_text("Last updated: May 1, 2026\n")
    cache_path = tmp_path / ".scout-cache" / KB_DATES_CACHE_FILENAME
    out = gather_kb_file_dates(kb, scout_dir=scout_dir, cache_path=cache_path)
    assert set(out) == {"knowledge-base/current.md"}


def test_gather_kb_file_dates_missing_root(tmp_path: Path) -> None:
    out = gather_kb_file_dates(
        tmp_path / "nope",
        scout_dir=tmp_path,
        cache_path=tmp_path / "cache.json",
    )
    assert out == {}


def test_gather_kb_file_dates_opens_each_file_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression guard: bash version effectively opened each file 4-5 times
    (head + grep + head + sed + sed). The cached-Python path opens at most once,
    and zero times when cache hits."""
    scout_dir = tmp_path / "vault"
    kb = scout_dir / "knowledge-base"
    kb.mkdir(parents=True)
    md = kb / "foo.md"
    md.write_text("# foo\nLast updated: May 1, 2026\n")
    cache_path = tmp_path / ".scout-cache" / KB_DATES_CACHE_FILENAME

    opens = {"count": 0}
    original_open = Path.open

    def counting_open(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if self == md:
            opens["count"] += 1
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counting_open)
    gather_kb_file_dates(kb, scout_dir=scout_dir, cache_path=cache_path)
    assert opens["count"] == 1


# ----- gh / git helpers --------------------------------------------------


def test_gather_returns_empty_when_no_git_no_gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.scripts import pre_session_data as psd

    scout_dir = tmp_path / "vault"
    (scout_dir / "knowledge-base").mkdir(parents=True)

    monkeypatch.setenv("SCOUT_DATA_DIR", str(scout_dir))
    monkeypatch.setattr(psd, "get_pr_authored", lambda: [])
    monkeypatch.setattr(psd, "get_pr_review_requested", lambda: [])

    ctx = gather("briefing", scout_dir=scout_dir)
    assert ctx.session_type == "briefing"
    assert ctx.git_recent == []
    assert ctx.pr_authored == []
    assert ctx.pr_review_requested == []
    assert ctx.personal_tasks == []


def test_gather_calls_gh_helpers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.scripts import pre_session_data as psd

    scout_dir = tmp_path / "vault"
    (scout_dir / "knowledge-base").mkdir(parents=True)
    monkeypatch.setattr(psd, "get_pr_authored", lambda: [{"number": 42, "title": "x"}])
    monkeypatch.setattr(psd, "get_pr_review_requested", lambda: [{"number": 43, "title": "y"}])

    ctx = gather("research", scout_dir=scout_dir)
    assert ctx.pr_authored == [{"number": 42, "title": "x"}]
    assert ctx.pr_review_requested == [{"number": 43, "title": "y"}]


def test_review_requests_fetch_url_and_a_longer_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """`action-items check-review-requests` matches on the URL and sees only what is fetched (#404)."""
    from scout.scripts import pre_session_data as psd

    seen: list[list[str]] = []
    monkeypatch.setattr(psd, "_gh_json", lambda args, timeout=10: seen.append(args) or [])
    assert psd.get_pr_review_requested() == []
    args = seen[0]
    assert args[:4] == ["search", "prs", "--review-requested", "@me"]
    assert "url" in args[args.index("--json") + 1].split(",")
    assert args[args.index("--limit") + 1] == str(psd.REVIEW_REQUEST_LIMIT)
    assert psd.REVIEW_REQUEST_LIMIT > psd.PR_LIST_LIMIT


# ----- write_context ----------------------------------------------------


def test_write_context_round_trip(tmp_path: Path) -> None:
    ctx = SessionContext(
        generated_at="2026-05-28T12:00:00",
        session_type="briefing",
        git_recent=[{"sha": "abc123", "subject": "s", "visibility": "normal", "paths": [], "line": "abc123 s"}],
        kb_file_dates={"knowledge-base/foo.md": {"last_updated": "May 1", "visibility": "normal"}},
        pr_authored=[{"number": 1}],
        pr_review_requested=[{"number": 2}],
        personal_tasks=[{"name": "t", "path": "knowledge-base/t.md", "visibility": "normal"}],
    )
    out = tmp_path / "session-context.json"
    write_context(ctx, out)
    parsed = json.loads(out.read_text())
    assert parsed["session_type"] == "briefing"
    assert parsed["pr_authored"] == [{"number": 1}]
    assert parsed["kb_file_dates"]["knowledge-base/foo.md"] == {"last_updated": "May 1", "visibility": "normal"}


# ----- Linear own activity and 🔴 project channels --------------------------


def _project(scout_dir: Path, name: str, front: str) -> None:
    path = scout_dir / "knowledge-base" / "projects" / name / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\ntype: project\n{front}---\n\n# {name}\n", encoding="utf-8")


def test_gather_carries_the_linear_fetch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.linear_activity import FETCHED, OwnActivity
    from scout.scripts import pre_session_data as psd

    scout_dir = tmp_path / "vault"
    (scout_dir / "knowledge-base").mkdir(parents=True)
    rows = [{"identifier": "PROJ-1", "title": "t", "url": "u", "state": "Todo", "actions": ["created"], "last_at": "x"}]
    seen: list[Path] = []

    def fetch(d: Path, now: object) -> OwnActivity:
        seen.append(d)
        return OwnActivity(FETCHED, "2026-04-14T07:00:00Z", rows)

    monkeypatch.setattr(psd, "get_linear_own_activity", fetch)
    ctx = gather("morning-briefing", scout_dir=scout_dir)
    assert seen == [scout_dir]
    assert ctx.linear_own_activity == rows
    assert ctx.linear_own_activity_since == "2026-04-14T07:00:00Z"
    assert ctx.linear_own_activity_status == "fetched"


def test_gather_says_why_nothing_was_fetched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.linear_activity import FAILED, OwnActivity
    from scout.scripts import pre_session_data as psd

    scout_dir = tmp_path / "vault"
    (scout_dir / "knowledge-base").mkdir(parents=True)
    failed = OwnActivity(FAILED, "2026-04-14T07:00:00Z", None, "HTTP 401")
    monkeypatch.setattr(psd, "get_linear_own_activity", lambda d, now: failed)
    ctx = gather("morning-briefing", scout_dir=scout_dir)
    assert ctx.linear_own_activity is None
    assert ctx.linear_own_activity_status == "failed: HTTP 401"


def test_gather_without_a_key_records_no_key(tmp_path: Path) -> None:
    """The hermetic HOME has no ~/.scout-secrets: the real fetch reports no_key and calls nothing."""
    scout_dir = tmp_path / "vault"
    (scout_dir / "knowledge-base").mkdir(parents=True)
    ctx = gather("morning-briefing", scout_dir=scout_dir)
    assert ctx.linear_own_activity is None
    assert ctx.linear_own_activity_status == "no_key"
    assert ctx.linear_own_activity_since


def test_a_reader_that_sees_nothing_gets_no_linear_fetch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.scripts import pre_session_data as psd

    scout_dir = tmp_path / "vault"
    _project(scout_dir, "widgets", 'status: active\npriority: "🔴"\nslack_channels:\n  - id: C0123456789\n')
    monkeypatch.setenv("SCOUT_READER", "agent:person:priya@example.com")
    monkeypatch.setattr(psd, "get_linear_own_activity", lambda *a: pytest.fail("agent readers get no Linear fetch"))
    ctx = gather("morning-briefing", scout_dir=scout_dir)
    assert ctx.linear_own_activity is None and ctx.linear_own_activity_status == "skipped"
    assert ctx.red_project_channels == []


def test_gather_carries_a_truncated_fetch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.linear_activity import FETCHED, OwnActivity
    from scout.scripts import pre_session_data as psd

    scout_dir = tmp_path / "vault"
    (scout_dir / "knowledge-base").mkdir(parents=True)
    cut = OwnActivity(FETCHED, "2026-04-14T07:00:00Z", [], truncated=True)
    monkeypatch.setattr(psd, "get_linear_own_activity", lambda d, now: cut)
    assert gather("morning-briefing", scout_dir=scout_dir).linear_own_activity_truncated is True


@pytest.mark.parametrize("session_type", ["dreaming", "research"])
def test_dreaming_and_research_get_no_linear_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session_type: str
) -> None:
    from scout.scripts import pre_session_data as psd

    scout_dir = tmp_path / "vault"
    (scout_dir / "knowledge-base").mkdir(parents=True)
    monkeypatch.setattr(psd, "get_linear_own_activity", lambda *a: pytest.fail("no Linear fetch for this session"))
    ctx = gather(session_type, scout_dir=scout_dir)
    assert ctx.linear_own_activity is None
    assert ctx.linear_own_activity_status == f"skipped: {session_type} session"


def test_gather_loads_the_knowledge_graph_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import scout.kb.read

    scout_dir = tmp_path / "vault"
    _project(scout_dir, "widgets", 'status: active\npriority: "🔴"\nslack_channels:\n  - id: C0123456789\n')
    real, loads = scout.kb.read.load_graph, []

    def counting(*args: object, **kwargs: object) -> object:
        loads.append(args)
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(scout.kb.read, "load_graph", counting)
    ctx = gather("morning-briefing", scout_dir=scout_dir)
    assert len(loads) == 1
    assert [r["project"] for r in ctx.red_project_channels] == ["widgets"]


DAILY = """# Action Items — 2026-04-15

## 📋 Scout Digest — 2026-04-15 (07:40)

### Your Linear Activity

- PROJ-1: you created it

```
## 🪵 Run notes & connector availability
- fenced, not an entry
```

## 🪵 Run notes & connector availability

- **2026-04-15 07:40 UTC** (morning-briefing) — ok
  - 🔴 channels polled: #widgets-launch
"""


def test_gather_records_todays_file_as_it_stands(tmp_path: Path) -> None:
    import datetime as dt

    scout_dir = tmp_path / "vault"
    (scout_dir / "action-items").mkdir(parents=True)
    (scout_dir / "action-items" / "action-items-2026-04-15.md").write_text(DAILY, encoding="utf-8")
    ctx = gather("midday-consolidation", scout_dir=scout_dir, now=dt.datetime(2026, 4, 15, 13, 0, tzinfo=dt.UTC))
    assert ctx.run_notes_before == ["- **2026-04-15 07:40 UTC** (morning-briefing) — ok"]
    assert [line for line in ctx.linear_report_before if line] == ["- PROJ-1: you created it"]


def test_without_todays_file_nothing_stood_before(tmp_path: Path) -> None:
    from datetime import date as dt_date

    from scout.scripts.pre_session_data import get_daily_before

    assert get_daily_before(tmp_path, dt_date(2026, 4, 15)) == ([], [])


def test_a_reader_that_sees_nothing_gets_no_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import datetime as dt

    scout_dir = tmp_path / "vault"
    (scout_dir / "action-items").mkdir(parents=True)
    (scout_dir / "action-items" / "action-items-2026-04-15.md").write_text(DAILY, encoding="utf-8")
    monkeypatch.setenv("SCOUT_READER", "agent:person:priya@example.com")
    ctx = gather("midday-consolidation", scout_dir=scout_dir, now=dt.datetime(2026, 4, 15, 13, 0, tzinfo=dt.UTC))
    assert ctx.run_notes_before == [] and ctx.linear_report_before == []


def test_red_project_channels_lists_every_red_project_and_its_channels(tmp_path: Path) -> None:
    from scout.scripts.pre_session_data import get_red_project_channels

    scout_dir = tmp_path / "vault"
    _project(
        scout_dir,
        "widgets",
        'status: active\npriority: "🔴"\nslack_channels:\n  - id: C0123456789\n    name: "#widgets-launch"\n'
        '  - "#widgets-eng (C0987654321)"\n',
    )
    _project(scout_dir, "gateway", 'status: "🔴 blocked on capacity"\npriority: high\nslack_channels: [C0111111111]\n')
    _project(scout_dir, "billing", 'status: active\npriority: "🔴"\n')
    _project(scout_dir, "docs", 'status: active\npriority: "🟡"\nslack_channels:\n  - id: C0222222222\n')

    rows = {r["project"]: r for r in get_red_project_channels(scout_dir, "owner")}

    assert set(rows) == {"widgets", "gateway", "billing"}  # 🔴 by priority or by status; 🟡 is out
    assert rows["widgets"]["channels"] == [
        {"id": "C0123456789", "name": "#widgets-launch"},
        {"id": "C0987654321", "name": "#widgets-eng"},
    ]
    assert rows["gateway"]["channels"] == [{"id": "C0111111111", "name": ""}]
    assert rows["billing"]["channels"] == []  # 🔴 with no slack_channels: the check reports it
    assert rows["widgets"]["path"] == "knowledge-base/projects/widgets/widgets.md"
    assert rows["widgets"]["visibility"] == "normal"


def test_red_project_channels_is_empty_without_a_knowledge_base(tmp_path: Path) -> None:
    from scout.scripts.pre_session_data import get_red_project_channels

    assert get_red_project_channels(tmp_path, "owner") == []


def test_red_project_channels_is_empty_when_the_kb_cannot_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import scout.kb.read
    from scout.scripts.pre_session_data import get_red_project_channels

    _project(tmp_path, "widgets", 'status: active\npriority: "🔴"\n')

    def broken(*_: object) -> object:
        raise RuntimeError("bad graph")

    monkeypatch.setattr(scout.kb.read, "load_graph", broken)
    assert get_red_project_channels(tmp_path, "owner") == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"id": "C0123456789", "name": "widgets-launch"}, {"id": "C0123456789", "name": "#widgets-launch"}),
        ("#widgets-launch", {"id": "", "name": "#widgets-launch"}),
        ("C0123456789", {"id": "C0123456789", "name": ""}),
        ("no channel here", None),
        (None, None),
    ],
)
def test_a_slack_channels_entry_may_be_a_mapping_or_a_string(raw: object, expected: dict | None) -> None:
    from scout.scripts.pre_session_data import _channel

    assert _channel(raw) == expected


def test_write_context_carries_the_new_fields(tmp_path: Path) -> None:
    ctx = SessionContext(
        generated_at="2026-05-28T12:00:00",
        session_type="briefing",
        linear_own_activity=[{"identifier": "PROJ-1"}],
        linear_own_activity_since="2026-05-27T12:00:00Z",
        linear_own_activity_status="fetched",
        red_project_channels=[{"project": "widgets", "channels": [{"id": "C0123456789", "name": ""}]}],
    )
    out = tmp_path / "session-context.json"
    write_context(ctx, out)
    parsed = json.loads(out.read_text())
    assert parsed["linear_own_activity"] == [{"identifier": "PROJ-1"}]
    assert parsed["linear_own_activity_status"] == "fetched"
    assert parsed["red_project_channels"][0]["project"] == "widgets"
