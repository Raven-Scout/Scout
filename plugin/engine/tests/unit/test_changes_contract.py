"""``scoutctl changes --json`` and ``scoutctl engagement --json`` are a contract with the macOS app.

``tests/fixtures/contract/changes.json`` and ``engagement.json`` are the canonical copies. Set
``SCOUT_REGEN_CONTRACT=1`` to rewrite them from the deterministic vault below; never edit them by hand."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from scout.changes.governance import item_hash
from scout.changes.models import CHANGE_KINDS, Change, Signal, Why
from scout.changes.report import changes_report, daily_counts, engagement_report, pending_dm_count
from scout.changes.store import (
    CHANGES_NAME,
    ENGAGEMENT_NAME,
    append_change,
    append_signal,
    owner_dir,
    read_changes,
    read_signals,
    vault_key,
)

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
PRAGUE = ZoneInfo("Europe/Prague")
# Read at import: tests/conftest.py scrubs SCOUT_* from the environment before each test runs.
REGEN = os.environ.get("SCOUT_REGEN_CONTRACT") == "1"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "contract"
CANONICAL_CHANGES = FIXTURES / "changes.json"
CANONICAL_ENGAGEMENT = FIXTURES / "engagement.json"
CANONICAL_DIGEST = FIXTURES / "changes-digest.json"


def _ts(days_ago: float) -> datetime:
    return NOW - timedelta(days=days_ago)


# The wishlist-done change: its 14-day review window (10-27..11-09) closed before NOW, so its test is done.
DONE_TS = _ts(20)


def _changes() -> list[Change]:
    """One coherent row per kind, in ``sorted(CHANGE_KINDS)`` order. Extend by adding to ``specs``: a row is what a
    scan of a real commit would write (a brain edit's ``why`` is its target; a rollback names the change it undoes)."""
    kinds = sorted(CHANGE_KINDS)
    ids = {kind: f"chg-{i:012x}" for i, kind in enumerate(kinds)}
    specs: dict[str, dict] = {
        "brain-edit": {
            "why": Why("pattern", "pattern-288", "A mistake that kept recurring", inferred=False),
            "files": ("SKILL.md",),
            "targets": ("pattern-288",),
            "author_lane": "dreaming",
        },
        "driver": {
            "why": Why("unknown", "", inferred=True),
            "files": ("scripts/check-clock.py",),
            "author_lane": "dreaming",
        },
        "kb-structure": {"files": ("knowledge-base/topics/a.md",), "backfilled": True},
        "phase": {"files": ("DREAMING.md",), "governance": True, "author_lane": "dreaming"},
        "rollback": {
            "why": Why("verdict", f"verdict:{ids['brain-edit']}", "The test found it hurt", inferred=False),
            "files": ("SKILL.md",),
            "reverts": ids["brain-edit"],
            "author_lane": "dreaming",
            "ts": _ts(0.5),  # after the brain-edit it reverts (_ts(1))
        },
        "upgrade": {"files": ("scripts/upgrade.sh",), "author_lane": "update"},
        # Old enough that its review is done: a recurrence of its target in the review window makes it hurt.
        "wishlist-done": {
            "files": ("docs/wishlist/red-carry.md",),
            "author_lane": "work",
            "ts": DONE_TS,
            "targets": ("pattern-249",),
        },
    }
    out = []
    for i, kind in enumerate(kinds):
        base: dict = {
            "id": ids[kind],
            "ts": _ts(1 + i),
            "kind": kind,
            "summary": f"A {kind} change",
            "why": Why("pattern", "pattern-249", "Why it happened", inferred=False),
            "commit": f"{i:040x}",
            "files": ("SKILL.md",),
            "targets": ("briefing",),
        }
        out.append(Change(**{**base, **specs[kind]}))
    return out


def _build(root: Path) -> tuple[Path, Path, dict]:
    vault, home = root / "Scout", root / "home"
    vault.mkdir()
    home.mkdir()
    env = {"HOME": str(home)}
    for change in _changes():
        append_change(vault, change)
    # Local days in Prague: the 23:30Z reliance row falls on the 11th, not the 10th.
    # (kind, surface, ts, session, ref). Extend by appending a row. Three DMs, as of NOW (11-16 12:00Z):
    # answered (reply 2 h after the send), unanswered (sent 76 h ago, so noise on the 13th) and pending (28 h old).
    rows: list[tuple[str, str, datetime, str | None, str | None]] = [
        ("session-reliance", "kb", datetime(2026, 11, 10, 9, 0, tzinfo=UTC), "a" * 16, None),
        ("session-reliance", "skill", datetime(2026, 11, 10, 10, 0, tzinfo=UTC), "a" * 16, None),
        ("session-reliance", "skill", datetime(2026, 11, 10, 23, 30, tzinfo=UTC), "b" * 16, None),
        ("dm-sent", "slack-dm", datetime(2026, 11, 11, 8, 0, tzinfo=UTC), None, "1762848000.000100"),
        (
            "dm-reply",
            "slack-dm",
            datetime(2026, 11, 11, 10, 0, tzinfo=UTC),
            None,
            "1762848000.000100/1762855200.000200",
        ),
        ("app-edit", "app", datetime(2026, 11, 12, 9, 0, tzinfo=UTC), None, None),
        ("inline-comment", "action-items", datetime(2026, 11, 12, 10, 0, tzinfo=UTC), None, None),
        ("approval", "kb", datetime(2026, 11, 13, 9, 0, tzinfo=UTC), None, None),
        ("dm-sent", "slack-dm", datetime(2026, 11, 13, 8, 0, tzinfo=UTC), None, "1763020800.000300"),
        ("dm-reply", "slack-dm", datetime(2026, 11, 14, 9, 0, tzinfo=UTC), None, None),
        ("dm-reaction", "slack-dm", datetime(2026, 11, 14, 9, 5, tzinfo=UTC), None, None),
        ("dm-sent", "slack-dm", datetime(2026, 11, 15, 8, 0, tzinfo=UTC), None, "1763193600.000400"),
        ("red-carried", "action-items", datetime(2026, 11, 15, 9, 0, tzinfo=UTC), None, None),
        # Before the engagement window (since NOW - 14 d), inside the wishlist-done change's review window: its
        # target recurred twice (hurt), and the owner marked the verdict wrong.
        ("recurrence", "mistake-audit", datetime(2026, 11, 1, 9, 0, tzinfo=UTC), None, "pattern-249@2026-11-01"),
        ("recurrence", "mistake-audit", datetime(2026, 11, 2, 9, 0, tzinfo=UTC), None, "pattern-249@2026-11-02"),
        ("verdict-wrong", "app", datetime(2026, 11, 2, 10, 0, tzinfo=UTC), None, "chg-000000000006"),
    ]
    for kind, surface, ts, session, ref in rows:
        append_signal(Signal(ts=ts, kind=kind, surface=surface, session=session, ref=ref), vault=vault, env=env)
    # One governance change waiting for the owner's OK (one file that doesn't fit is counted, not listed).
    pending = vault / "governance-pending"
    pending.mkdir()
    (pending / "skip-carried-red.md").write_text(
        "---\nsummary: Let the briefing drop a red item nobody answered for six days\n"
        "why: The same red item was carried for six days with no reply.\ntargets:\n  - briefing\n---\n\n"
        "```diff\n--- a/SKILL.md\n+++ b/SKILL.md\n@@ -1 +1 @@\n-old\n+new\n```\n",
        encoding="utf-8",
    )
    (pending / "not-a-proposal.md").write_text("no frontmatter\n", encoding="utf-8")
    # A harness-fallback item (`governance: false`: applying it is an ordinary change) that the owner approved; the
    # approval names the hash of the item they saw (scout.changes.governance.item_hash).
    fallback_diff = "--- a/DREAMING.md\n+++ b/DREAMING.md\n@@ -1 +1 @@\n-old\n+new\n"
    (pending / "tighten-the-wrap.md").write_text(
        "---\nsummary: Keep the dreaming wrap to five lines\nwhy: pattern-288\ngovernance: false\n---\n\n"
        f"```diff\n{fallback_diff}```\n",
        encoding="utf-8",
    )
    append_signal(
        Signal(
            ts=datetime(2026, 11, 15, 9, 30, tzinfo=UTC),
            kind="approval",
            surface="app",
            ref=f"approve:gov-tighten-the-wrap@{_FALLBACK_HASH(fallback_diff)}",
        ),
        vault=vault,
        env=env,
    )
    # One torn line in each file.
    for path in (vault / ".scout-logs" / CHANGES_NAME, vault / ".scout-logs" / ENGAGEMENT_NAME):
        with path.open("a", encoding="utf-8") as f:
            f.write('{"kind": "brain-ed\n')
    with (owner_dir(env) / ENGAGEMENT_NAME).open("a", encoding="utf-8") as f:
        f.write("not json\n")
    return vault, home, env


def _FALLBACK_HASH(diff: str) -> str:  # noqa: N802 - reads as the constant it stands for
    return item_hash(
        diff, governance=False, targets=(), summary="Keep the dreaming wrap to five lines", why="pattern-288"
    )


def _text(doc: dict) -> str:
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


def _check_or_regen(path: Path, text: str) -> None:
    if REGEN:
        path.write_text(text, encoding="utf-8")
    assert path.read_text(encoding="utf-8") == text, "regenerate with SCOUT_REGEN_CONTRACT=1"


def test_changes_contract_matches_canonical(tmp_path):
    vault, _, env = _build(tmp_path)
    doc = changes_report(vault, now=NOW, since=NOW - timedelta(days=30), env=env, tz=PRAGUE)
    assert doc["skipped_rows"] == 1 and set(doc["counts"]) == CHANGE_KINDS
    assert doc["schema_version"] == 1 and doc["truncated"] is False and len(doc["changes"]) == 7
    stamps = [c["ts"] for c in doc["changes"]]
    assert stamps == sorted(stamps, reverse=True)
    assert any(c["why"]["inferred"] for c in doc["changes"]) and any(c["backfilled"] for c in doc["changes"])
    # Every change carries its test: one testing, one done with a verdict, the untested lanes and kinds not tested.
    assert doc["calibrating"] is True and doc["auto_rollback"] == "off"
    tests = {c["kind"]: c["test"] for c in doc["changes"]}
    assert tests["driver"]["state"] == "testing" and tests["driver"]["after"] is None
    done = tests["wishlist-done"]
    assert done["state"] == "done" and done["verdict"] == "hurt" and done["reasons"] == ["recurrence-up"]
    # Marked wrong by the owner: still suspect on the map, but no auto-rollback mode would act on it.
    assert done["suspect"] is True and done["marked_wrong"] is True
    assert done["would_have_done"] == {"targeted": False, "one_at_a_time": False}
    assert {tests[k]["state"] for k in ("kb-structure", "rollback", "upgrade")} == {"not-tested"}
    rolled = tests["brain-edit"]
    assert rolled["verdict"] == "rolled back" and rolled["rolled_back_by"] == "chg-000000000004"
    assert doc["needs_ok"] == [
        {
            "id": "gov-skip-carried-red",
            "summary": "Let the briefing drop a red item nobody answered for six days",
            "why": "The same red item was carried for six days with no reply.",
            "targets": ["briefing"],
            "path": "governance-pending/skip-carried-red.md",
            "state": "awaiting-ok",
            "diff_hash": doc["needs_ok"][0]["diff_hash"],
            "governance": True,
        },
        {
            "id": "gov-tighten-the-wrap",
            "summary": "Keep the dreaming wrap to five lines",
            "why": "pattern-288",
            "targets": [],
            "path": "governance-pending/tighten-the-wrap.md",
            "state": "approved",
            "diff_hash": _FALLBACK_HASH("--- a/DREAMING.md\n+++ b/DREAMING.md\n@@ -1 +1 @@\n-old\n+new\n"),
            "governance": False,
        },
    ]
    assert len(doc["needs_ok"][0]["diff_hash"]) == 64
    assert doc["needs_ok_skipped"] == 1
    # The rollback (untested) and the 16-days-older wishlist-done change are not in the phase change's overlap.
    assert tests["phase"]["overlap"] == ["chg-000000000000", "chg-000000000001"]
    _check_or_regen(CANONICAL_CHANGES, _text(doc))


def test_changes_report_reads_settings_from_the_owner_store(tmp_path):
    from datetime import date

    from scout.changes.settings import Settings, write_settings

    vault, _, env = _build(tmp_path)
    write_settings(Settings(auto_rollback="targeted", review_days=21, calibrated_on=date(2026, 11, 1)), env)
    # A settings file in the run-writable logs is never read.
    (vault / ".scout-logs" / "settings.json").write_text(json.dumps({"schema": 1, "auto_rollback": "one-at-a-time"}))
    doc = changes_report(vault, now=NOW, since=NOW - timedelta(days=30), env=env, tz=PRAGUE)
    assert doc["auto_rollback"] == "targeted" and doc["calibrating"] is False
    tests = {c["kind"]: c["test"] for c in doc["changes"]}
    # 21 review days: the wishlist-done change (10-27) is reviewed on 11-17, after NOW.
    assert tests["wishlist-done"]["state"] == "testing" and tests["wishlist-done"]["review_on"] == "2026-11-17"
    assert tests["driver"]["calibrating"] is False


def test_engagement_contract_has_every_day(tmp_path):
    vault, _, env = _build(tmp_path)
    doc = engagement_report(vault, env, now=NOW, since=NOW - timedelta(days=14), tz=PRAGUE)
    assert len(doc["days"]) == 15
    empty = {"feedback": {}, "noise": {}, "reliance": {}}
    assert doc["days"][3] == {"date": "2026-11-05", "feedback": 0, "reliance": 0, "noise": 0, "by_surface": empty}
    assert doc["skipped_rows"] == 2
    # approval and an answered or pending dm-sent are not engagement; a session counts once per surface per local day.
    # The unanswered DM (sent on the 13th) is noise; the one sent on the 15th is still pending.
    assert doc["totals"] == {"feedback": 5, "reliance": 3, "noise": 2}
    assert doc["pending"] == {"dm_sent": 1}
    by_date = {d["date"]: d for d in doc["days"]}
    assert by_date["2026-11-11"]["reliance"] == 1  # 23:30Z is already the 11th in Prague
    # by_surface is split per field: an ignored red (noise) never reads as engagement on its surface.
    assert by_date["2026-11-15"]["by_surface"] == {**empty, "noise": {"action-items": 1}}
    assert by_date["2026-11-12"]["by_surface"] == {**empty, "feedback": {"action-items": 1, "app": 1}}
    assert by_date["2026-11-13"]["by_surface"] == {**empty, "noise": {"slack-dm": 1}}  # the unanswered DM's day
    assert by_date["2026-11-11"]["by_surface"]["feedback"] == {"slack-dm": 1}
    assert by_date["2026-11-10"]["by_surface"] == {**empty, "reliance": {"kb": 1, "skill": 1}}
    for d in doc["days"]:
        for field in ("feedback", "reliance", "noise"):
            assert sum(d["by_surface"][field].values()) == d[field]
    _check_or_regen(CANONICAL_ENGAGEMENT, _text(doc))


def test_digest_contract_matches_canonical(tmp_path):
    from scout.changes.digest import build_digest

    vault, _, env = _build(tmp_path)
    folder = vault / "docs" / "wishlist"
    folder.mkdir(parents=True)
    (folder / "gmail-empty-check.md").write_text(
        "---\ntitle: Say so when a Gmail search finds nothing\nstatus: open\npriority: high\ndate: 2026-11-10\n---\n",
        encoding="utf-8",
    )
    (folder / "shipped-one.md").write_text("---\ntitle: Shipped\nstatus: done\npriority: high\n---\n", encoding="utf-8")
    doc = build_digest(vault, env, now=NOW, tz=PRAGUE)
    assert doc["schema_version"] == 1 and doc["week_of"] == "2026-11-16" and len(doc["days"]) == 7
    assert doc["needs_ok"] == 1 and doc["up_next"] == [
        {"slug": "gmail-empty-check", "title": "Say so when a Gmail search finds nothing"}
    ]
    assert str(vault) not in json.dumps(doc) and "/Users/" not in json.dumps(doc)
    _check_or_regen(CANONICAL_DIGEST, _text(doc))


def test_reports_carry_no_absolute_paths(tmp_path):
    vault, home, env = _build(tmp_path)
    docs = (
        changes_report(vault, now=NOW, since=NOW - timedelta(days=30), env=env, tz=PRAGUE),
        engagement_report(vault, env, now=NOW, since=NOW - timedelta(days=14), tz=PRAGUE),
    )
    for doc in docs:
        text = json.dumps(doc)
        assert str(vault) not in text and str(home) not in text and "/Users/" not in text


def test_limit_truncates_but_counts_everything(tmp_path):
    vault, _, env = _build(tmp_path)
    doc = changes_report(vault, now=NOW, since=NOW - timedelta(days=30), limit=3, env=env, tz=PRAGUE)
    assert doc["truncated"] is True and len(doc["changes"]) == 3
    assert sum(doc["counts"].values()) == 7


def test_concurrent_scans_duplicate_rows_show_and_count_once(tmp_path):
    """Two overlapping scans can each append the same commit's row; the report keeps the first, once."""
    vault = tmp_path / "Scout"
    vault.mkdir()
    env = {"HOME": str(tmp_path / "home")}
    first = Change(
        id="chg-abcdef012345",
        ts=_ts(1),
        kind="brain-edit",
        summary="first row",
        why=Why(type="unknown", ref="", inferred=True),
        commit="abcdef012345" + "0" * 28,
        files=("SKILL.md",),
        targets=(),
        governance=False,
        author_lane="dreaming",
    )
    other = replace(first, id="chg-000000000001", kind="driver", summary="another")
    append_change(vault, first)
    append_change(vault, other)
    append_change(vault, replace(first, summary="second row"))
    assert len(read_changes(vault)[0]) == 3
    doc = changes_report(vault, now=NOW, since=NOW - timedelta(days=30), env=env, tz=PRAGUE)
    assert [c["id"] for c in doc["changes"]].count("chg-abcdef012345") == 1
    assert next(c for c in doc["changes"] if c["id"] == "chg-abcdef012345")["summary"] == "first row"
    assert len(doc["changes"]) == 2 and sum(doc["counts"].values()) == 2
    assert doc["counts"]["brain-edit"] == 1 and doc["counts"]["driver"] == 1
    assert doc["truncated"] is False


def test_duplicate_reliance_rows_count_once(tmp_path):
    vault, home = tmp_path / "Scout", tmp_path / "home"
    vault.mkdir()
    env = {"HOME": str(home)}
    when = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)
    for _ in range(2):
        append_signal(Signal(ts=when, kind="session-reliance", surface="kb", session="c" * 16), vault=vault, env=env)
    doc = engagement_report(vault, env, now=NOW, since=NOW - timedelta(days=14), tz=PRAGUE)
    assert doc["totals"]["reliance"] == 1


def test_zoneless_timestamps_are_utc_and_sort_with_aware_rows(tmp_path):
    vault, home = tmp_path / "Scout", tmp_path / "home"
    (vault / ".scout-logs").mkdir(parents=True)
    env = {"HOME": str(home)}
    naive = {"kind": "red-carried", "surface": "action-items", "ts": "2026-11-12T09:00:00"}
    aware = {"kind": "red-carried", "surface": "action-items", "ts": "2026-11-12T08:00:00+00:00"}
    (vault / ".scout-logs" / ENGAGEMENT_NAME).write_text(json.dumps(naive) + "\n" + json.dumps(aware) + "\n")
    signals, skipped = read_signals(vault, env)
    assert skipped == 0 and [s.ts.hour for s in signals] == [8, 9]
    assert all(s.ts.tzinfo is not None for s in signals)
    doc = engagement_report(vault, env, now=NOW, since=NOW - timedelta(days=14), tz=PRAGUE)
    assert doc["totals"]["noise"] == 2


def test_unhashable_kinds_are_skipped_not_raised(tmp_path):
    vault, home = tmp_path / "Scout", tmp_path / "home"
    (vault / ".scout-logs").mkdir(parents=True)
    env = {"HOME": str(home)}
    bad = json.dumps({"kind": ["x"], "surface": "kb", "ts": "2026-11-12T09:00:00Z"}) + "\n"
    (vault / ".scout-logs" / ENGAGEMENT_NAME).write_text(bad)
    (vault / ".scout-logs" / CHANGES_NAME).write_text(json.dumps({"kind": ["x"]}) + "\n")
    owner = owner_dir(env)
    owner.mkdir(parents=True)
    keyed = {"kind": ["x"], "surface": "kb", "ts": "2026-11-12T09:00:00Z", "vault_key": vault_key(vault)}
    (owner / ENGAGEMENT_NAME).write_text(json.dumps(keyed) + "\n")
    assert read_signals(vault, env) == ([], 2)
    assert read_changes(vault) == ([], 1)


def test_cli_emits_both_documents_and_bare_prints_help(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from scout.cli import app

    vault, home, _ = _build(tmp_path)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(vault))
    monkeypatch.setenv("HOME", str(home))
    runner = CliRunner()
    out = runner.invoke(app, ["changes", "--json", "--since", "3650d", "--limit", "2"])
    assert out.exit_code == 0, out.output
    doc = json.loads(out.stdout)
    assert doc["schema_version"] == 1 and len(doc["changes"]) == 2
    assert doc["auto_rollback"] == "off" and doc["calibrating"] is True
    assert all(c["test"]["state"] in ("testing", "done", "not-tested") for c in doc["changes"])
    out = runner.invoke(app, ["engagement", "--json", "--since", "3650d"])
    assert out.exit_code == 0, out.output
    assert json.loads(out.stdout)["schema_version"] == 1
    assert "Usage" in runner.invoke(app, ["changes"]).output
    assert runner.invoke(app, ["changes", "--json", "--since", "soon"]).exit_code == 2


def test_signals_outside_the_window_and_sessionless_reliance(tmp_path):
    vault, home = tmp_path / "Scout", tmp_path / "home"
    vault.mkdir()
    env = {"HOME": str(home)}
    inside = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)
    before = NOW - timedelta(days=30)
    after = NOW + timedelta(days=1)
    # No session id to dedupe on: every row counts. Out-of-window rows count for nothing.
    for when in (inside, inside, before, after):
        append_signal(Signal(ts=when, kind="session-reliance", surface="kb"), vault=vault, env=env)
    doc = engagement_report(vault, env, now=NOW, since=NOW - timedelta(days=14), tz=PRAGUE)
    assert doc["totals"]["reliance"] == 2


def _dm_day(signals: list[Signal], *, now: datetime = NOW) -> dict:
    return daily_counts(signals, since=now - timedelta(days=14), now=now, tz=PRAGUE)


def _sent(hours_ago: float, ref: str = "100.1") -> Signal:
    return Signal(ts=NOW - timedelta(hours=hours_ago), kind="dm-sent", surface="slack-dm", ref=ref)


def _answer(sent: Signal, hours_after: float, tail: str = "200.2", kind: str = "dm-reply") -> Signal:
    return Signal(ts=sent.ts + timedelta(hours=hours_after), kind=kind, surface="slack-dm", ref=f"{sent.ref}/{tail}")


def test_an_unanswered_dm_is_noise_after_48h():
    sent = _sent(50)
    days = _dm_day([sent])
    day = days[sent.ts.astimezone(PRAGUE).date().isoformat()]
    assert day["noise"] == 1 and day["by_surface"]["noise"] == {"slack-dm": 1}
    assert sum(d["noise"] for d in days.values()) == 1


def test_a_reply_at_49h_leaves_it_noise():
    sent = _sent(60)
    reply = _answer(sent, 49)
    days = _dm_day([sent, reply])
    assert days[sent.ts.astimezone(PRAGUE).date().isoformat()]["noise"] == 1
    assert days[reply.ts.astimezone(PRAGUE).date().isoformat()]["feedback"] == 1


def test_a_dm_exactly_48h_old_is_noise_and_a_reply_at_exactly_48h_answers_it():
    """T3: both edges of the 48 h window are inclusive: noise from 48 h on, and a reply at +48 h still counts."""
    sent = _sent(48)
    assert sum(d["noise"] for d in _dm_day([sent]).values()) == 1
    assert pending_dm_count([sent], since=NOW - timedelta(days=14), now=NOW) == 0
    just_under = _sent(48 - 1 / 3600)
    assert sum(d["noise"] for d in _dm_day([just_under]).values()) == 0
    assert pending_dm_count([just_under], since=NOW - timedelta(days=14), now=NOW) == 1
    older = _sent(60)
    assert sum(d["noise"] for d in _dm_day([older, _answer(older, 48)]).values()) == 0
    assert sum(d["noise"] for d in _dm_day([older, _answer(older, 48 + 1 / 3600)]).values()) == 1


def test_a_reaction_within_48h_answers_it():
    sent = _sent(60)
    days = _dm_day([sent, _answer(sent, 2, "r:+1", "dm-reaction")])
    assert sum(d["noise"] for d in days.values()) == 0


def test_a_young_dm_is_pending_not_noise(tmp_path):
    sent = _sent(30)
    assert sum(d["noise"] for d in _dm_day([sent]).values()) == 0
    vault, home = tmp_path / "Scout", tmp_path / "home"
    vault.mkdir()
    env = {"HOME": str(home)}
    append_signal(sent, vault=vault, env=env)
    doc = engagement_report(vault, env, now=NOW, since=NOW - timedelta(days=14), tz=PRAGUE)
    assert doc["pending"] == {"dm_sent": 1} and doc["totals"]["noise"] == 0


def test_a_reply_to_another_dm_does_not_answer_it():
    sent = _sent(50, "100.1")
    other = _answer(_sent(50, "999.9"), 1)
    days = _dm_day([sent, other])
    assert sum(d["noise"] for d in days.values()) == 1


def test_a_dm_without_a_ref_or_on_another_surface_is_ignored():
    bare = Signal(ts=_ts(3), kind="dm-sent", surface="slack-dm")
    telegram = Signal(ts=_ts(3), kind="dm-sent", surface="telegram", ref="100.1")
    assert sum(d["noise"] for d in _dm_day([bare, telegram]).values()) == 0


def test_a_duplicated_dm_sent_row_counts_once():
    sent = _sent(50)
    assert sum(d["noise"] for d in _dm_day([sent, sent]).values()) == 1


def test_verdict_wrong_counts_as_feedback():
    row = Signal(ts=_ts(1), kind="verdict-wrong", surface="app", ref="chg-0123456789ab")
    days = _dm_day([row])
    assert sum(d["feedback"] for d in days.values()) == 1
