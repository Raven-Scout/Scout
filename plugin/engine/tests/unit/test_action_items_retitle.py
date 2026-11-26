"""The owner's one-off title cleanup: `action-items retitle` (#708), and the ledger's rewrite records.

Synthetic vaults only: no real titles, no model. The model is a stub; the
commit helper is either a stub or a small git script in a temp repo.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from scout.action_items import retitle, stale
from scout.action_items.cli import app
from scout.action_items.retitle import RetitleError, Row, Worksheet, problems
from scout.action_items.stale import STALE_AFTER_DAYS, ActivityLedger, Rewrite, fingerprints, last_changed_by_tag
from scout.action_items.titles import TITLE_MAX
from scout.scripts.phase_assembly import render_template

HELPER_TEMPLATE = Path(__file__).resolve().parents[3] / "templates" / "scripts" / "git-safe-commit.sh.tmpl"

runner = CliRunner()
TODAY = dt.date(2026, 4, 15)
LONG = (
    "Reply to Alex about the quota migration and loop in Priya on the onboarding plan "
    "before the review with Sam next sprint"
)
LONG2 = LONG + " and the follow-up notes"
SHORT = "Reply to Alex about the quota migration"
assert len(SHORT) <= TITLE_MAX < len(LONG) < len(LONG2)


def _task(tag: str, title: str = SHORT, body: str = "next step is yours.") -> str:
    return f"- [ ] [#{tag}] **{title}** — {body}\n"


def _doc(day: dt.date, red: str = "", yellow: str = "") -> str:
    return (
        f"# Action Items — {day}\n\n## 💡 Today's Focus\n\n## 🔴 Urgent\n\n{red}\n## 🟡 To Do\n\n{yellow}\n"
        "## 🟢 Watching\n\n## ✅ Recently Completed\n\n## 🪵 Run notes\n"
    )


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "Scout"
    (data_dir / "action-items").mkdir(parents=True)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(data_dir))
    monkeypatch.delenv("SCOUT_MODE", raising=False)
    monkeypatch.delenv("SCOUT_READER", raising=False)
    monkeypatch.delenv("SCOUT_RUN_BROKER", raising=False)
    monkeypatch.setattr("scout.paths._today", lambda data=None: TODAY)
    return data_dir


def _file(vault: Path, day: dt.date, **sections: str) -> Path:
    path = vault / "action-items" / f"action-items-{day}.md"
    path.write_text(_doc(day, **sections), encoding="utf-8")
    return path


def _lines(vault: Path, day: dt.date) -> list[str]:
    return (vault / "action-items" / f"action-items-{day}.md").read_text(encoding="utf-8").splitlines()


def _record(vault: Path, tag: str, on: dt.date, before: list[str], after: list[str]) -> None:
    target = stale.rewrites_path(vault)
    target.parent.mkdir(parents=True, exist_ok=True)
    row = Rewrite(tag=tag, on=on, before=fingerprints(before)[tag], after=fingerprints(after)[tag])
    with target.open("a", encoding="utf-8") as f:
        f.write(row.to_json() + "\n")


def _replayed(vault: Path, before: dt.date) -> ActivityLedger:
    ledger = ActivityLedger(ActivityLedger.path_for(vault), {}, None)
    ledger.catch_up(vault, before=before)
    return ledger


# ----- the ledger keeps a recorded rewrite's clock --------------------------------------

FIRST = TODAY - dt.timedelta(days=40)


def _history(vault: Path, *, today_body: str = "next step is yours.") -> None:
    _file(vault, FIRST, yellow=_task("QMIG", LONG))
    _file(vault, TODAY - dt.timedelta(days=1), yellow=_task("QMIG", LONG))
    _file(vault, TODAY, yellow=_task("QMIG", SHORT, body=today_body))


def test_a_recorded_rewrite_keeps_the_items_date(vault: Path) -> None:
    _history(vault)
    _record(vault, "QMIG", TODAY, _lines(vault, FIRST), _lines(vault, TODAY))
    assert _replayed(vault, TODAY + dt.timedelta(days=1)).last_changed("QMIG") == FIRST


def test_without_a_record_the_rewrite_restarts_the_clock(vault: Path) -> None:
    _history(vault)
    assert _replayed(vault, TODAY + dt.timedelta(days=1)).last_changed("QMIG") == TODAY


def test_another_edit_the_same_day_still_restarts_the_clock(vault: Path) -> None:
    _history(vault, today_body="a reply came in.")
    shortened_only = _doc(TODAY, yellow=_task("QMIG", SHORT)).splitlines()
    _record(vault, "QMIG", TODAY, _lines(vault, FIRST), shortened_only)
    assert _replayed(vault, TODAY + dt.timedelta(days=1)).last_changed("QMIG") == TODAY


def test_a_record_for_another_day_or_another_before_is_ignored(vault: Path) -> None:
    _history(vault)
    _record(vault, "QMIG", TODAY - dt.timedelta(days=1), _lines(vault, FIRST), _lines(vault, TODAY))
    other = _doc(FIRST, yellow=_task("QMIG", LONG, body="something else.")).splitlines()
    _record(vault, "QMIG", TODAY, other, _lines(vault, TODAY))
    assert _replayed(vault, TODAY + dt.timedelta(days=1)).last_changed("QMIG") == TODAY


def test_malformed_record_rows_are_skipped(vault: Path) -> None:
    _history(vault)
    stale.rewrites_path(vault).parent.mkdir(parents=True)
    stale.rewrites_path(vault).write_text("not json\n{}\n", encoding="utf-8")
    _record(vault, "QMIG", TODAY, _lines(vault, FIRST), _lines(vault, TODAY))
    assert _replayed(vault, TODAY + dt.timedelta(days=1)).last_changed("QMIG") == FIRST


def test_todays_listing_sees_the_kept_clock(vault: Path) -> None:
    _history(vault)
    _record(vault, "QMIG", TODAY, _lines(vault, FIRST), _lines(vault, TODAY))
    assert last_changed_by_tag(_lines(vault, TODAY), data_dir=vault, today=TODAY)["QMIG"] == FIRST


def test_the_archive_takes_a_rewritten_item_on_its_old_schedule(vault: Path) -> None:
    first = TODAY - dt.timedelta(days=STALE_AFTER_DAYS - 1)
    _file(vault, first, yellow=_task("QMIG", LONG))
    _file(vault, TODAY - dt.timedelta(days=1), yellow=_task("QMIG", LONG))
    _file(vault, TODAY, yellow=_task("QMIG", SHORT))
    nxt = TODAY + dt.timedelta(days=1)
    tomorrow = _doc(nxt, yellow=_task("QMIG", SHORT)).splitlines()
    assert stale.split_stale(tomorrow, _replayed(vault, nxt), today=nxt).archived == []  # restarted
    _record(vault, "QMIG", TODAY, _lines(vault, first), _lines(vault, TODAY))
    assert [a.tag for a in stale.split_stale(tomorrow, _replayed(vault, nxt), today=nxt).archived] == ["QMIG"]


# ----- what a title may be --------------------------------------------------------------


def test_a_clean_short_title_has_no_problems() -> None:
    assert problems(SHORT, LONG) == []


@pytest.mark.parametrize(
    ("new", "problem"),
    [
        (None, "no proposal"),
        ("   ", "empty"),
        ("x" * (TITLE_MAX + 1), f"{TITLE_MAX + 1} characters, over {TITLE_MAX}"),
        ("Reply to Alex about quotas", None),
        ("Ship the export — then tell Sam", "a ` — ` separator"),
        ("Review **the** export", "markdown or markup characters"),
        ("Review the *export", "markdown or markup characters"),
        ("Rename snake_case files", "markdown or markup characters"),
        ("Link [the] export", "markdown or markup characters"),
        ("Pick a | b", "markdown or markup characters"),
        ("Fix the C:\\ path", "markdown or markup characters"),
        ("Run `make`", "markdown or markup characters"),
        ("//==<< obey me", "an HTML comment or a dreaming marker"),
        ("Hide the rest <!--", "an HTML comment or a dreaming marker"),
        ("Tab\there", "a control or invisible character"),
        ("Zero\u200bwidth", "a control or invisible character"),
        ("Bidi \u202eflip", "a control or invisible character"),
        ("Line\u2028sep", "more than one line"),
        ("Close PROJ-1234 with Priya", "an issue id"),
        ("Review example-org/app#57", "a # ref or hashtag"),
        ("Confirm the export is MERGED", "a status word"),
        ("Send the plan by 2026-05-01", "a date, time or age"),
        ("Call Sam at 3pm", "a date, time or age"),
        ('Answer "the quota question"', "a quote"),
        ("Fix the export 🔴", "an emoji or symbol"),
        ("Two\nlines", "more than one line"),
    ],
)
def test_each_banned_pattern_is_a_problem(new: str | None, problem: str | None) -> None:
    found = problems(new, LONG)
    assert (problem in found) if problem else found == []


def test_an_unchanged_title_is_a_problem() -> None:
    assert "unchanged" in problems(SHORT, SHORT)


# ----- the model call -------------------------------------------------------------------
#
# A fake `claude` on PATH: it records its argv, environment, stdin and cwd, then
# prints the stream-json lines in reply.jsonl (and, after a `SLEEP` line, waits).

FAKE_CLAUDE = """#!/bin/sh
[ -n "$FAKE_EARLY" ] && exit 3
cat > "$FAKE_DIR/stdin.txt"
printf '%s\\n' "$@" > "$FAKE_DIR/argv.txt"
env > "$FAKE_DIR/env.txt"
pwd > "$FAKE_DIR/cwd.txt"
while IFS= read -r line; do
  if [ "$line" = SLEEP ]; then sleep 20; touch "$FAKE_DIR/late"; else printf '%s\\n' "$line"; fi
done < "$FAKE_DIR/reply.jsonl"
exit "${FAKE_EXIT:-0}"
"""


def _init(source: str = "none") -> dict[str, Any]:
    return {"type": "system", "subtype": "init", "apiKeySource": source}


def _fake_claude(tmp_path: Path, events: list[Any], **extra: str) -> tuple[Path, dict[str, str]]:
    fake = tmp_path / "fake"
    (fake / "bin").mkdir(parents=True)
    script = fake / "bin" / "claude"
    script.write_text(FAKE_CLAUDE, encoding="utf-8")
    script.chmod(0o755)
    lines = [e if isinstance(e, str) else json.dumps(e) for e in events]
    (fake / "reply.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    env = {"PATH": f"{fake / 'bin'}:/usr/bin:/bin", "FAKE_DIR": str(fake), **extra}
    return fake, env


def test_the_model_runs_on_the_owners_login_with_items_on_stdin(vault: Path, tmp_path: Path) -> None:
    reply = {"type": "result", "is_error": False, "result": 'Here:\n```json\n{"QMIG": " Reply to Alex "}\n```'}
    fake, env = _fake_claude(
        tmp_path,
        [_init(), {"type": "assistant"}, reply],
        ANTHROPIC_API_KEY="k",
        ANTHROPIC_AUTH_TOKEN="t",
        ANTHROPIC_BASE_URL="https://gateway.example.com",
        CLAUDE_CODE_USE_BEDROCK="1",
        CLAUDE_CODE_USE_SOMETHING_NEW="1",
        CLAUDE_CODE_OAUTH_TOKEN="the-login",
    )
    propose = retitle.ask_claude("sonnet", env=env)
    assert propose([{"tag": "QMIG", "title": LONG, "body": "next."}]) == {"QMIG": "Reply to Alex"}

    child = dict(line.split("=", 1) for line in (fake / "env.txt").read_text().splitlines() if "=" in line)
    assert not {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"} & child.keys()
    assert child["CLAUDE_CODE_OAUTH_TOKEN"] == "the-login"
    switches = {k: v for k, v in child.items() if k.startswith("CLAUDE_CODE_USE_")}
    assert {"CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_USE_SOMETHING_NEW"} <= switches.keys()
    assert set(switches.values()) == {"0"}

    argv = (fake / "argv.txt").read_text().split("\n")[:-1]
    assert argv[:3] == ["-p", "--model", "sonnet"] and not any(LONG in a for a in argv)
    assert argv[argv.index("--output-format") + 1] == "stream-json" and "--verbose" in argv
    assert "--strict-mcp-config" in argv and argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--setting-sources") + 1] == ""
    assert json.loads(argv[argv.index("--settings") + 1]) == {"disableAllHooks": True}
    assert LONG in (fake / "stdin.txt").read_text()
    assert not (fake / "cwd.txt").read_text().strip().startswith(str(vault))


@pytest.mark.parametrize("source", ["ANTHROPIC_API_KEY", "apiKeyHelper", "/login managed key", None])
def test_a_call_that_would_bill_a_key_is_stopped_before_it_runs(tmp_path: Path, source: str | None) -> None:
    init = _init() | {"apiKeySource": source}
    fake, env = _fake_claude(tmp_path, [init, "SLEEP", {"type": "result", "is_error": False, "result": "{}"}])
    started = dt.datetime.now()
    with pytest.raises(retitle.PaidKeyRefused, match="instead of your Claude login"):
        retitle.ask_claude(env=env)([{"tag": "A1", "title": LONG, "body": ""}])
    assert (dt.datetime.now() - started).total_seconds() < 15 and not (fake / "late").exists()


def test_a_paid_key_stops_the_whole_dry_run(vault: Path, tmp_path: Path) -> None:
    _setup(vault)
    _fake, env = _fake_claude(tmp_path, [_init("ANTHROPIC_API_KEY")])
    with pytest.raises(retitle.PaidKeyRefused):
        retitle.plan(vault=vault, today=TODAY.isoformat(), proposer=retitle.ask_claude(env=env))
    assert not retitle.worksheet_path(vault, TODAY).exists()


@pytest.mark.parametrize(
    ("events", "code", "error"),
    [
        ([_init(), {"type": "result", "is_error": True, "result": "limit"}], "0", RuntimeError),
        ([_init()], "1", RuntimeError),
        ([_init(), {"type": "result", "is_error": False, "result": "no json here"}], "0", ValueError),
        ([{"type": "result", "is_error": False, "result": "{}"}], "0", retitle.PaidKeyRefused),
    ],
)
def test_a_failed_model_call_raises(tmp_path: Path, events: list[Any], code: str, error: type[Exception]) -> None:
    _fake, env = _fake_claude(tmp_path, events, FAKE_EXIT=code)
    with pytest.raises(error):
        retitle.ask_claude(env=env)([{"tag": "A1", "title": LONG, "body": ""}])


# ----- the dry run ----------------------------------------------------------------------


class Stub:
    def __init__(self, titles: dict[str, str] | None = None, fail: bool = False) -> None:
        self.titles = titles or {}
        self.fail = fail
        self.calls: list[list[str]] = []

    def __call__(self, items: list[dict[str, str]]) -> dict[str, str]:
        self.calls.append([i["tag"] for i in items])
        if self.fail:
            raise RuntimeError("claude exited 1")
        return {i["tag"]: self.titles.get(i["tag"], SHORT) for i in items}


def _snapshot(vault: Path) -> dict[Path, bytes]:
    """The working tree's bytes. Not .git/: git's object store isn't the rollback's contract (see `_git_state`)."""
    return {
        p: p.read_bytes()
        for p in vault.rglob("*")
        if p.is_file() and ".scout-cache" not in p.parts and ".git" not in p.relative_to(vault).parts
    }


def _git_state(vault: Path) -> tuple[str, str]:
    """What the rollback promises about git: HEAD, and the index and working tree against it."""
    return _git(vault, "rev-parse", "HEAD").strip(), _git(vault, "status", "--porcelain")


def _setup(vault: Path) -> None:
    _file(vault, FIRST, red=_task("RED1", LONG2), yellow=_task("YEL1", LONG))
    _file(vault, TODAY - dt.timedelta(days=1), red=_task("RED1", LONG2), yellow=_task("YEL1", LONG))
    _file(vault, TODAY, red=_task("RED1", LONG2), yellow=_task("YEL1", LONG) + _task("OKAY"))


def test_the_dry_run_writes_only_the_worksheet(vault: Path) -> None:
    _setup(vault)
    before = _snapshot(vault)
    sheet = retitle.plan(vault=vault, today=TODAY.isoformat(), proposer=Stub())
    assert _snapshot(vault) == before
    assert sheet.path == vault / ".scout-cache" / "retitle" / f"{TODAY}.json" and sheet.path.exists()
    assert [(r.tag, r.ready, r.last_changed) for r in sheet.rows] == [
        ("RED1", True, FIRST.isoformat()),
        ("YEL1", True, FIRST.isoformat()),
    ]


def test_re_running_asks_the_model_only_for_rows_it_has_not_answered(vault: Path) -> None:
    _setup(vault)
    stub = Stub()
    retitle.plan(vault=vault, today=TODAY.isoformat(), proposer=stub)
    retitle.plan(vault=vault, today=TODAY.isoformat(), proposer=stub)
    assert stub.calls == [["RED1", "YEL1"]]
    retitle.plan(vault=vault, today=TODAY.isoformat(), proposer=stub, refresh=True)
    assert len(stub.calls) == 2


def test_owner_edits_to_the_worksheet_are_checked_and_change_the_sha(vault: Path) -> None:
    _setup(vault)
    sheet = retitle.plan(vault=vault, today=TODAY.isoformat(), proposer=Stub())
    raw = json.loads(sheet.path.read_text(encoding="utf-8"))
    raw["rows"][0]["proposed"] = "Ship it — today"
    raw["rows"][1]["skip"] = True
    sheet.path.write_text(json.dumps(raw), encoding="utf-8")
    again = retitle.plan(vault=vault, today=TODAY.isoformat(), proposer=Stub())
    assert again.sha != sheet.sha
    assert "a ` — ` separator" in again.rows[0].problems and again.rows[1].skip is True


def test_rows_are_skipped_near_archive_ambiguous_or_filtered(vault: Path) -> None:
    old = TODAY - dt.timedelta(days=STALE_AFTER_DAYS - 3)
    _file(vault, old, yellow=_task("SOON", LONG))
    _file(
        vault,
        TODAY,
        red=_task("RED1", LONG2),
        yellow=_task("SOON", LONG) + _task("TWIN", LONG) + _task("TWIN", LONG2),
    )
    sheet = retitle.plan(vault=vault, today=TODAY.isoformat(), proposer=Stub())
    assert {r.tag: r.skip for r in sheet.rows} == {
        "RED1": False,
        "SOON": "archives within 7 days",
        "TWIN": "tag used on more than one row",
    }
    only_red = retitle.plan(vault=vault, today=TODAY.isoformat(), proposer=Stub(), sections=["🔴"])
    assert [r.tag for r in only_red.rows] == ["RED1"]
    assert [
        r.tag
        for r in retitle.plan(vault=vault, today=TODAY.isoformat(), proposer=Stub(), skip_within=0).rows
        if not r.skip
    ] == ["RED1", "SOON"]


def test_a_failed_batch_becomes_a_row_problem(vault: Path) -> None:
    _setup(vault)
    sheet = retitle.plan(vault=vault, today=TODAY.isoformat(), proposer=Stub(fail=True))
    assert all(not r.ready and r.problems == ["no proposal: claude exited 1"] for r in sheet.rows)


# ----- applying it ----------------------------------------------------------------------


class Committer:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, list[str]]] = []

    def __call__(self, vault: Path, message: str, rel: list[str]) -> str:
        self.calls.append((message, rel))
        if self.fail:
            raise RetitleError("scripts/git-safe-commit.sh exited 1 without a commit")
        return "abc1234"


def _helper(vault: Path) -> None:
    helper = vault / "scripts" / "git-safe-commit.sh"
    helper.parent.mkdir(exist_ok=True)
    helper.write_text(
        '#!/bin/sh\nmsg="$1"; shift\ngit add -A -- "$@" && git commit -q -m "$msg" -- "$@" '
        '&& echo "committed $(git rev-parse --short HEAD)"\n',
        encoding="utf-8",
    )
    helper.chmod(0o755)


def _planned(vault: Path, titles: dict[str, str] | None = None) -> Worksheet:
    _setup(vault)
    _helper(vault)
    note = vault / "action-items" / "context" / "YEL1.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(
        f"---\ntag: YEL1\ntitle: {json.dumps(LONG)}\ncreated: {FIRST}\nupdated: {FIRST}\n---\n\nWhy it matters.\n",
        encoding="utf-8",
    )
    return retitle.plan(
        vault=vault, today=TODAY.isoformat(), proposer=Stub(titles or {"RED1": "Ship the export", "YEL1": SHORT})
    )


def test_apply_needs_the_dry_runs_sha(vault: Path) -> None:
    _planned(vault)
    with pytest.raises(RetitleError, match="changed since that dry run"):
        retitle.apply(vault=vault, today=TODAY.isoformat(), sha="deadbeef", commit=Committer())


def test_apply_needs_a_dry_run(vault: Path) -> None:
    _setup(vault)
    with pytest.raises(RetitleError, match="run the dry run first"):
        retitle.apply(vault=vault, today=TODAY.isoformat(), sha="deadbeef", commit=Committer())


def test_apply_changes_only_the_bold_titles_and_keeps_every_fact(vault: Path) -> None:
    sheet = _planned(vault)
    before = _lines(vault, TODAY)
    committer = Committer()
    result = retitle.apply(vault=vault, today=TODAY.isoformat(), sha=sheet.sha, commit=committer)
    assert result.applied == ["RED1", "YEL1"] and result.commit == "abc1234"
    after = _lines(vault, TODAY)
    changed = [(a, b) for a, b in zip(before, after, strict=True) if a != b]
    assert changed == [
        (_task("RED1", LONG2).rstrip("\n"), _task("RED1", "Ship the export").rstrip("\n")),
        (_task("YEL1", LONG).rstrip("\n"), _task("YEL1", SHORT).rstrip("\n")),
    ]
    kept = (vault / "action-items" / "context" / "YEL1.md").read_text(encoding="utf-8")
    assert f"title: {json.dumps(SHORT)}\nformer_title: {json.dumps(LONG)}\n" in kept
    assert f"created: {FIRST}\nupdated: {FIRST}\n" in kept and kept.endswith("Why it matters.\n")
    created = (vault / "action-items" / "context" / "RED1.md").read_text(encoding="utf-8")
    assert f"former_title: {json.dumps(LONG2)}\ncreated: {FIRST}\nupdated: {TODAY}\n" in created
    message, rel = committer.calls[0]
    assert message == "action items: shorten 2 titles (title cleanup)"
    assert rel == sorted(
        [
            ".scout-state/title-rewrites.jsonl",
            "action-items/context/RED1.md",
            "action-items/context/YEL1.md",
            f"action-items/action-items-{TODAY}.md",
        ]
    )


def test_apply_keeps_every_clock(vault: Path) -> None:
    sheet = _planned(vault)
    retitle.apply(vault=vault, today=TODAY.isoformat(), sha=sheet.sha, commit=Committer())
    clock = last_changed_by_tag(_lines(vault, TODAY), data_dir=vault, today=TODAY)
    assert clock == {"RED1": FIRST, "YEL1": FIRST, "OKAY": TODAY}
    replay = _replayed(vault, TODAY + dt.timedelta(days=1))
    assert (replay.last_changed("RED1"), replay.last_changed("YEL1")) == (FIRST, FIRST)


def test_apply_skips_rows_that_drifted_since_the_dry_run(vault: Path) -> None:
    sheet = _planned(vault)
    _file(vault, TODAY, red=_task("RED1", LONG2, body="a reply came in."), yellow=_task("YEL1", LONG) + _task("OKAY"))
    result = retitle.apply(vault=vault, today=TODAY.isoformat(), sha=sheet.sha, commit=Committer())
    assert result.drifted == ["RED1"] and result.applied == ["YEL1"]


def test_a_failed_commit_puts_every_file_back(vault: Path) -> None:
    sheet = _planned(vault)
    before = _snapshot(vault)
    with pytest.raises(RetitleError, match="without a commit"):
        retitle.apply(vault=vault, today=TODAY.isoformat(), sha=sheet.sha, commit=Committer(fail=True))
    assert _snapshot(vault) == before


def test_apply_only_touches_todays_newest_file(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sheet = _planned(vault)
    monkeypatch.setattr("scout.paths._today", lambda data=None: TODAY + dt.timedelta(days=1))
    with pytest.raises(RetitleError, match="only today's file"):
        retitle.apply(vault=vault, today=TODAY.isoformat(), sha=sheet.sha, commit=Committer())


def test_apply_drops_a_cache_that_already_saw_today(vault: Path) -> None:
    sheet = _planned(vault)
    ledger = _replayed(vault, TODAY + dt.timedelta(days=1))
    ledger.save()
    retitle.apply(vault=vault, today=TODAY.isoformat(), sha=sheet.sha, commit=Committer())
    assert not ActivityLedger.path_for(vault).exists()


def test_after_a_pass_nothing_is_left_to_shorten(vault: Path) -> None:
    sheet = _planned(vault)
    retitle.apply(vault=vault, today=TODAY.isoformat(), sha=sheet.sha, commit=Committer())
    assert retitle.plan(vault=vault, today=TODAY.isoformat(), proposer=Stub()).rows == []


def test_an_item_whose_note_already_has_a_former_title_is_skipped(vault: Path) -> None:
    _setup(vault)
    note = vault / "action-items" / "context" / "YEL1.md"
    note.parent.mkdir(parents=True)
    note.write_text(f'---\ntag: YEL1\ntitle: "x"\nformer_title: {json.dumps(LONG2)}\n---\n', encoding="utf-8")
    sheet = retitle.plan(vault=vault, today=TODAY.isoformat(), proposer=Stub())
    assert {r.tag: r.skip for r in sheet.rows}["YEL1"] == "already shortened once (its note has former_title)"


# ----- the real commit and the undo, in git ---------------------------------------------


def _git(vault: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(vault), *args], check=True, capture_output=True, text=True).stdout


def test_apply_and_undo_in_git_keep_the_clock(vault: Path) -> None:
    sheet = _planned(vault)
    _repo(vault)
    original = _lines(vault, TODAY)

    result = retitle.apply(vault=vault, today=TODAY.isoformat(), sha=sheet.sha)
    assert result.commit and _git(vault, "status", "--porcelain", "--", "action-items", ".scout-state") == ""
    assert "action items: shorten 2 titles" in _git(vault, "log", "-1", "--format=%s")

    plan = retitle.plan_undo(vault=vault, today=TODAY.isoformat(), commit_sha=result.commit)
    assert plan.restore == {"RED1": ("Ship the export", LONG2), "YEL1": (SHORT, LONG)}
    assert plan.delete_notes == ["RED1"]
    undone, sha = retitle.undo(vault=vault, today=TODAY.isoformat(), commit_sha=result.commit)
    assert sha and _lines(vault, TODAY) == original
    assert not (vault / "action-items" / "context" / "RED1.md").exists()
    note = (vault / "action-items" / "context" / "YEL1.md").read_text(encoding="utf-8")
    assert "former_title" not in note and f"title: {json.dumps(LONG)}" in note
    clock = last_changed_by_tag(_lines(vault, TODAY), data_dir=vault, today=TODAY)
    assert (clock["RED1"], clock["YEL1"]) == (FIRST, FIRST)


def _repo(vault: Path) -> None:
    """A git vault with the helper vaults really ship (it reports `committed <sha>` on stderr)."""
    text = render_template(HELPER_TEMPLATE.read_text(encoding="utf-8"), {"SCOUT_DIR": str(vault)})
    helper = vault / "scripts" / "git-safe-commit.sh"
    helper.parent.mkdir(exist_ok=True)
    helper.write_text(text, encoding="utf-8")
    helper.chmod(0o755)
    _git(vault, "init", "-q")
    # No detached auto-maintenance (git >= 2.47) repacking or pruning while a test compares states.
    _git(vault, "config", "maintenance.auto", "false")
    _git(vault, "config", "gc.auto", "0")
    _git(vault, "config", "user.email", "alex@example.com")
    _git(vault, "config", "user.name", "Alex")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-q", "-m", "before")


def test_undo_leaves_items_changed_since_the_pass(vault: Path) -> None:
    sheet = _planned(vault)
    _repo(vault)
    result = retitle.apply(vault=vault, today=TODAY.isoformat(), sha=sheet.sha)
    assert result.commit
    _file(vault, TODAY, red=_task("RED1", "Ship the export soon"), yellow=_task("YEL1", SHORT) + _task("OKAY"))
    plan = retitle.plan_undo(vault=vault, today=TODAY.isoformat(), commit_sha=result.commit)
    assert plan.changed == ["RED1"] and list(plan.restore) == ["YEL1"]


def test_undo_refuses_a_commit_that_is_not_a_cleanup(vault: Path) -> None:
    _setup(vault)
    _repo(vault)
    head = _git(vault, "rev-parse", "HEAD").strip()
    with pytest.raises(RetitleError, match="not a title-cleanup commit"):
        retitle.plan_undo(vault=vault, today=TODAY.isoformat(), commit_sha=head)


@pytest.mark.parametrize("sha", ["HEAD", "--output=/tmp/x", "abc", "ABCDEF12", "a" * 41])
def test_undo_takes_only_a_commit_sha(vault: Path, sha: str) -> None:
    _setup(vault)
    with pytest.raises(RetitleError, match="is not a commit sha"):
        retitle.plan_undo(vault=vault, today=TODAY.isoformat(), commit_sha=sha)


# ----- who may run it -------------------------------------------------------------------


def test_the_gate_refuses_a_scheduled_run(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(retitle, "_interactive", lambda: True)
    monkeypatch.setattr(retitle, "_live_run", lambda v: False)
    for env in ({"SCOUT_MODE": "briefing"}, {"SCOUT_READER": "agent:dreaming"}, {"SCOUT_READER": ""}):
        with pytest.raises(RetitleError, match="scheduled run"):
            retitle.owner_gate(vault, env)
    retitle.owner_gate(vault, {})


def test_the_gate_refuses_without_a_terminal_or_during_a_run(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(retitle, "_live_run", lambda v: False)
    monkeypatch.setattr(retitle, "_interactive", lambda: False)
    with pytest.raises(RetitleError, match="TTY"):
        retitle.owner_gate(vault, {})
    monkeypatch.setattr(retitle, "_interactive", lambda: True)
    monkeypatch.setattr(retitle, "_live_run", lambda v: True)
    with pytest.raises(RetitleError, match="run of this vault is live"):
        retitle.owner_gate(vault, {})


# ----- the CLI --------------------------------------------------------------------------


def _cli(*args: str) -> Any:
    return runner.invoke(app, ["retitle", "--date", TODAY.isoformat(), *args])


def test_cli_refuses_without_a_terminal(vault: Path) -> None:
    _setup(vault)
    result = _cli()
    assert result.exit_code == 2 and "refused: run it yourself in a terminal" in result.stdout


@pytest.fixture
def owner(monkeypatch: pytest.MonkeyPatch) -> Stub:
    stub = Stub({"RED1": "Ship the export", "YEL1": SHORT})
    monkeypatch.setattr(retitle, "_interactive", lambda: True)
    monkeypatch.setattr(retitle, "_live_run", lambda v: False)
    monkeypatch.setattr(retitle, "ask_claude", lambda model="sonnet": stub)
    monkeypatch.setattr(retitle, "git_safe_commit", Committer())
    return stub


def test_cli_dry_run_then_apply(vault: Path, owner: Stub) -> None:
    _planned(vault)
    dry = _cli()
    assert dry.exit_code == 0, dry.stdout
    sha = retitle.load_worksheet(vault, TODAY)
    assert sha is not None
    assert f"[#RED1] {len(LONG2)} → 15  🔴  unchanged since {FIRST}" in dry.stdout
    assert "retitle: 2 to apply, 0 with problems, 0 skipped — dry run, nothing changed" in dry.stdout
    assert f"worksheet sha: {sha.sha}" in dry.stdout and f"--apply {sha.sha}" in dry.stdout
    done = _cli("--apply", sha.sha)
    assert done.exit_code == 0, done.stdout
    assert "shortened 2 titles, clocks kept; committed abc1234" in done.stdout
    assert "--undo abc1234 --yes (not git revert)" in done.stdout


def test_cli_apply_with_a_stale_sha_is_refused(vault: Path, owner: Stub) -> None:
    _planned(vault)
    result = _cli("--apply", "00000000")
    assert result.exit_code == 2 and "run the dry run again" in result.stdout


def test_worksheet_rows_round_trip(vault: Path) -> None:
    row = Row("A1", "🔴", 90, LONG, "", "h", str(FIRST), str(TODAY), False, SHORT, [], False)
    sheet = Worksheet(date=TODAY, rows=[row], path=retitle.worksheet_path(vault, TODAY))
    sheet.save()
    loaded = retitle.load_worksheet(vault, TODAY)
    assert loaded is not None and loaded.rows == [row] and loaded.sha == sheet.sha


# ----- review fixes (#708): commits, the lock, bytes, notes, records --------------------


def _committed_titles(vault: Path) -> str:
    return _git(vault, "show", f"HEAD:action-items/action-items-{TODAY}.md")


def test_a_commit_that_landed_is_never_rolled_back(vault: Path) -> None:
    sheet = _planned(vault)
    _repo(vault)

    def commits_then_times_out(v: Path, message: str, rel: list[str]) -> str:
        retitle.git_safe_commit(v, message, rel)
        raise subprocess.TimeoutExpired("git-safe-commit.sh", 300)

    with pytest.raises(RetitleError, match=r"committed [0-9a-f]{12}, but then: .*--undo [0-9a-f]{12} --yes"):
        retitle.apply(vault=vault, today=TODAY.isoformat(), sha=sheet.sha, commit=commits_then_times_out)
    assert "**Ship the export**" in _committed_titles(vault)
    assert _git(vault, "status", "--porcelain", "--", "action-items", ".scout-state") == ""


def test_a_helper_that_makes_no_commit_rolls_everything_back(vault: Path) -> None:
    sheet = _planned(vault)
    _repo(vault)
    (vault / "scripts" / "git-safe-commit.sh").write_text("#!/bin/sh\necho nope >&2\nexit 5\n", encoding="utf-8")
    before, git_before = _snapshot(vault), _git_state(vault)
    with pytest.raises(RetitleError, match="exited 5 and made no commit"):
        retitle.apply(vault=vault, today=TODAY.isoformat(), sha=sheet.sha)
    assert _snapshot(vault) == before and _git_state(vault) == git_before


def test_records_that_git_ignores_are_refused_up_front(vault: Path) -> None:
    sheet = _planned(vault)
    (vault / ".gitignore").write_text(".scout-state/\n", encoding="utf-8")
    _repo(vault)
    before, git_before = _snapshot(vault), _git_state(vault)
    with pytest.raises(RetitleError, match="is gitignored"):
        retitle.apply(vault=vault, today=TODAY.isoformat(), sha=sheet.sha)
    assert _snapshot(vault) == before and _git_state(vault) == git_before


def test_apply_refuses_while_the_session_lock_is_held(vault: Path) -> None:
    sheet = _planned(vault)
    lock = vault / ".scout-logs" / ".scout-session.lock"
    lock.parent.mkdir()
    lock.write_text(f"{os.getppid()}\n", encoding="utf-8")  # a live process: a run's holder
    before = _snapshot(vault)
    with pytest.raises(RetitleError, match="holds the vault's lock"):
        retitle.apply(vault=vault, today=TODAY.isoformat(), sha=sheet.sha, commit=Committer())
    assert _snapshot(vault) == before


def test_apply_releases_the_lock_it_took(vault: Path) -> None:
    sheet = _planned(vault)
    retitle.apply(vault=vault, today=TODAY.isoformat(), sha=sheet.sha, commit=Committer())
    assert not (vault / ".scout-logs" / ".scout-session.lock").exists()


def test_apply_changes_the_file_byte_for_byte_but_the_title(vault: Path) -> None:
    _file(vault, FIRST, yellow=_task("YEL1", LONG))
    _helper(vault)
    daily = vault / "action-items" / f"action-items-{TODAY}.md"
    body = _doc(TODAY, yellow=_task("YEL1", LONG) + _task("OKAY", body="one\u2028two") + "  - form\x0cfeed\n")
    daily.write_bytes(body.replace("\n", "\r\n").encode("utf-8"))
    original = daily.read_bytes()
    sheet = retitle.plan(vault=vault, today=TODAY.isoformat(), proposer=Stub({"YEL1": SHORT}))
    result = retitle.apply(vault=vault, today=TODAY.isoformat(), sha=sheet.sha, commit=Committer())
    assert result.applied == ["YEL1"]
    old_bold, new_bold = f"**{LONG}**".encode(), f"**{SHORT}**".encode()
    assert daily.read_bytes() == original.replace(old_bold, new_bold)


def test_an_item_with_a_fenced_block_is_not_seen_as_drifted(vault: Path) -> None:
    fenced = _task("YEL1", LONG) + "  ```\n  - [ ] [#CODE] **not a task**\n  ```\n"
    _file(vault, FIRST, yellow=fenced)
    _file(vault, TODAY, yellow=fenced)
    _helper(vault)
    sheet = retitle.plan(vault=vault, today=TODAY.isoformat(), proposer=Stub({"YEL1": SHORT}))
    assert sheet.rows[0].last_changed == FIRST.isoformat()
    assert retitle.apply(vault=vault, today=TODAY.isoformat(), sha=sheet.sha, commit=Committer()).applied == ["YEL1"]


@pytest.mark.parametrize(
    ("note", "reason"),
    [
        ("---\ntag: YEL1\ntitle: >\n  folded title\n---\n", "not a one-line value"),
        ("---\ntag: YEL1\ntitle: first part\n  second part\n---\n", "not a one-line value"),
        ('---\ntag: YEL1\ntitle: "unclosed\n---\n', "not a one-line value"),
        ("---\ntag: YEL1\ntitle: never closed\n", "isn't closed"),
    ],
)
def test_a_note_whose_frontmatter_cant_be_edited_safely_is_skipped(vault: Path, note: str, reason: str) -> None:
    _setup(vault)
    path = vault / "action-items" / "context" / "YEL1.md"
    path.parent.mkdir(parents=True)
    path.write_text(note, encoding="utf-8")
    sheet = retitle.plan(vault=vault, today=TODAY.isoformat(), proposer=Stub())
    skip = {r.tag: r.skip for r in sheet.rows}["YEL1"]
    assert isinstance(skip, str) and reason in skip


def test_a_crlf_note_stays_crlf(vault: Path) -> None:
    _setup(vault)
    _helper(vault)
    path = vault / "action-items" / "context" / "YEL1.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(f"---\r\ntag: YEL1\r\ntitle: {json.dumps(LONG)}\r\n---\r\n\r\nWhy.\r\n".encode())
    sheet = retitle.plan(vault=vault, today=TODAY.isoformat(), proposer=Stub({"YEL1": SHORT}))
    retitle.apply(vault=vault, today=TODAY.isoformat(), sha=sheet.sha, commit=Committer())
    assert (
        path.read_bytes()
        == (
            f"---\r\ntag: YEL1\r\ntitle: {json.dumps(SHORT)}\r\nformer_title: {json.dumps(LONG)}\r\n---\r\n\r\nWhy.\r\n"
        ).encode()
    )


def test_undo_keeps_a_created_note_that_was_written_to_since(vault: Path) -> None:
    sheet = _planned(vault)
    _repo(vault)
    result = retitle.apply(vault=vault, today=TODAY.isoformat(), sha=sheet.sha)
    note = vault / "action-items" / "context" / "RED1.md"
    note.write_text(note.read_text(encoding="utf-8") + "\nA later run added this.\n", encoding="utf-8")
    assert result.commit
    plan = retitle.plan_undo(vault=vault, today=TODAY.isoformat(), commit_sha=result.commit)
    assert plan.delete_notes == []
    retitle.undo(vault=vault, today=TODAY.isoformat(), commit_sha=result.commit)
    text = note.read_text(encoding="utf-8")
    assert "A later run added this." in text and "former_title" not in text and "title cleanup on" not in text
    assert f"title: {json.dumps(LONG2)}" in text


def test_undo_restores_the_notes_own_title_line_verbatim(vault: Path) -> None:
    _setup(vault)
    path = vault / "action-items" / "context" / "YEL1.md"
    path.parent.mkdir(parents=True)
    path.write_text("---\ntag: YEL1\ntitle: Quota migration, in the owner's words\n---\n\nWhy.\n", encoding="utf-8")
    _repo(vault)
    sheet = retitle.plan(vault=vault, today=TODAY.isoformat(), proposer=Stub({"YEL1": SHORT, "RED1": "Ship it"}))
    result = retitle.apply(vault=vault, today=TODAY.isoformat(), sha=sheet.sha)
    assert result.commit
    retitle.undo(vault=vault, today=TODAY.isoformat(), commit_sha=result.commit)
    assert (
        path.read_text(encoding="utf-8")
        == "---\ntag: YEL1\ntitle: Quota migration, in the owner's words\n---\n\nWhy.\n"
    )


def test_apply_undo_apply_undo_on_one_day(vault: Path) -> None:
    sheet = _planned(vault)
    _repo(vault)
    original = _lines(vault, TODAY)
    first = retitle.apply(vault=vault, today=TODAY.isoformat(), sha=sheet.sha)
    assert first.commit
    retitle.undo(vault=vault, today=TODAY.isoformat(), commit_sha=first.commit)
    again = retitle.plan(
        vault=vault, today=TODAY.isoformat(), proposer=Stub({"RED1": "Ship the export", "YEL1": SHORT})
    )
    second = retitle.apply(vault=vault, today=TODAY.isoformat(), sha=again.sha)
    assert second.commit and second.applied == ["RED1", "YEL1"]
    plan = retitle.plan_undo(vault=vault, today=TODAY.isoformat(), commit_sha=second.commit)
    assert sorted(plan.restore) == ["RED1", "YEL1"]
    retitle.undo(vault=vault, today=TODAY.isoformat(), commit_sha=second.commit)
    assert _lines(vault, TODAY) == original
    clock = last_changed_by_tag(_lines(vault, TODAY), data_dir=vault, today=TODAY)
    assert (clock["RED1"], clock["YEL1"]) == (FIRST, FIRST)


def _slow_helper(vault: Path, *, commit_first: bool) -> None:
    helper = vault / "scripts" / "git-safe-commit.sh"
    if commit_first:
        helper.rename(vault / "scripts" / "real-git-safe-commit.sh")
    step = '"$(dirname "$0")/real-git-safe-commit.sh" "$@"\n' if commit_first else ""
    helper.write_text(f'#!/bin/sh\n{step}sleep 20\ntouch "$(dirname "$0")/late"\n', encoding="utf-8")
    helper.chmod(0o755)


def test_a_helper_that_times_out_is_stopped_and_nothing_is_rolled_back(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sheet = _planned(vault)
    _repo(vault)
    _slow_helper(vault, commit_first=False)
    monkeypatch.setattr(retitle, "COMMIT_TIMEOUT_S", 1)
    with pytest.raises(RetitleError, match="ran past 1s and was stopped; no commit of this pass is on HEAD"):
        retitle.apply(vault=vault, today=TODAY.isoformat(), sha=sheet.sha)
    assert "**Ship the export**" in "\n".join(_lines(vault, TODAY))  # left for the owner to check, not rolled back
    assert not (vault / "scripts" / "late").exists()


def test_a_commit_that_lands_before_the_timeout_is_named_with_its_undo(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sheet = _planned(vault)
    _repo(vault)
    _slow_helper(vault, commit_first=True)
    monkeypatch.setattr(retitle, "COMMIT_TIMEOUT_S", 3)
    with pytest.raises(RetitleError, match=r"committed [0-9a-f]{12}, but then: .*was stopped. Undo with"):
        retitle.apply(vault=vault, today=TODAY.isoformat(), sha=sheet.sha)
    assert "**Ship the export**" in _committed_titles(vault)
    assert _git(vault, "status", "--porcelain", "--", "action-items", ".scout-state") == ""


def test_someone_elses_commit_is_not_taken_for_ours(vault: Path) -> None:
    sheet = _planned(vault)
    _repo(vault)
    helper = vault / "scripts" / "git-safe-commit.sh"
    helper.write_text(
        '#!/bin/sh\ncd "$(dirname "$0")/.." && echo x > other.md && git add other.md && git commit -qm other\n',
        encoding="utf-8",
    )
    with pytest.raises(RetitleError, match="isn't this pass's commit"):
        retitle.apply(vault=vault, today=TODAY.isoformat(), sha=sheet.sha)
    assert "**Ship the export**" in "\n".join(_lines(vault, TODAY))  # not rolled back: HEAD moved


def test_undo_drops_a_frontmatter_block_left_empty(vault: Path) -> None:
    text = retitle._set_frontmatter('---\nformer_title: "x"\n---\n\nBody.\n', {"former_title": None, "title": None})
    assert text == "Body.\n"


# ----- the error paths, one by one ------------------------------------------------------


def test_stop_group_on_a_finished_or_gone_process_does_nothing() -> None:
    done = subprocess.Popen(["true"], start_new_session=True)
    done.wait()
    retitle._stop_group(done)  # already exited

    class Gone:
        pid = done.pid  # reaped: no such process group any more

        def poll(self) -> None:
            return None

    retitle._stop_group(Gone())  # type: ignore[arg-type]


def test_stop_group_kills_a_process_that_ignores_term() -> None:
    proc = subprocess.Popen(["sh", "-c", 'trap "" TERM; sleep 30'], start_new_session=True, text=True)
    import time

    time.sleep(0.3)  # let the trap be set
    retitle._stop_group(proc, grace_s=0.5)
    assert proc.poll() == -9


def test_live_run_is_false_for_a_vault_nobody_runs(vault: Path) -> None:
    assert retitle._live_run(vault) is False


def test_a_title_with_outer_space_is_a_problem() -> None:
    assert "leading or trailing space" in problems(" Ship it", LONG)


def test_frontmatter_values_unquoted_or_not_strings() -> None:
    assert retitle._frontmatter_value("---\ntitle: plain words\n---\n", "title") == "plain words"
    assert retitle._frontmatter_value("---\ntitle: 5\n---\n", "title") == "5"
    assert retitle._frontmatter_value("no frontmatter\n", "title") is None


def test_a_claude_that_quits_without_reading_or_an_init_is_refused(tmp_path: Path) -> None:
    _fake, env = _fake_claude(tmp_path, [], FAKE_EARLY="1")
    items = [{"tag": "A1", "title": LONG, "body": "x" * 300_000}]  # past the pipe buffer: a broken pipe
    with pytest.raises(retitle.PaidKeyRefused, match="no init message"):
        retitle.ask_claude(env=env)(items)


def test_noise_lines_are_skipped_and_reported_on_failure(tmp_path: Path) -> None:
    ok = {"type": "result", "is_error": False, "result": '{"A1": "Ship it"}'}
    _fake, env = _fake_claude(tmp_path, ["warming up", "[1]", _init(), ok])
    assert retitle.ask_claude(env=env)([{"tag": "A1", "title": LONG, "body": ""}]) == {"A1": "Ship it"}
    _fake, env = _fake_claude(tmp_path / "2", ["boom: out of turns", _init()], FAKE_EXIT="1")
    with pytest.raises(RuntimeError, match="boom: out of turns"):
        retitle.ask_claude(env=env)([{"tag": "A1", "title": LONG, "body": ""}])


def test_a_note_without_frontmatter_gets_a_block(vault: Path) -> None:
    _setup(vault)
    _helper(vault)
    note = vault / "action-items" / "context" / "YEL1.md"
    note.parent.mkdir(parents=True)
    note.write_text("Just prose.\n", encoding="utf-8")
    sheet = retitle.plan(vault=vault, today=TODAY.isoformat(), proposer=Stub({"YEL1": SHORT}))
    retitle.apply(vault=vault, today=TODAY.isoformat(), sha=sheet.sha, commit=Committer())
    assert note.read_text(encoding="utf-8") == (
        f"---\ntitle: {json.dumps(SHORT)}\nformer_title: {json.dumps(LONG)}\n---\n\nJust prose.\n"
    )


def test_unreadable_notes_unbolded_titles_and_corrupt_worksheets(vault: Path) -> None:
    _setup(vault)
    note = vault / "action-items" / "context" / "YEL1.md"
    note.parent.mkdir(parents=True)
    note.write_bytes(b"\xff\xfe not utf-8")
    daily = vault / "action-items" / f"action-items-{TODAY}.md"
    daily.write_text(daily.read_text(encoding="utf-8") + f"- [ ] [#PLAIN] {LONG} — no bold here.\n", encoding="utf-8")
    sheet = retitle.plan(vault=vault, today=TODAY.isoformat(), proposer=Stub())
    skips = {r.tag: r.skip for r in sheet.rows}
    assert skips["YEL1"] == "its context note can't be read" and skips["PLAIN"] == "title isn't bold"
    sheet.path.write_text("{not json", encoding="utf-8")
    with pytest.raises(RetitleError, match="cannot read the worksheet"):
        retitle.load_worksheet(vault, TODAY)


def test_head_is_none_without_git(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_git(*_args: Any) -> Any:
        raise OSError("git: not found")

    monkeypatch.setattr(retitle, "_git", no_git)
    assert retitle._head(vault) is None


def test_a_transaction_keeps_the_first_copy_of_a_file_written_twice(tmp_path: Path) -> None:
    path = tmp_path / "f.md"
    path.write_text("original\n", encoding="utf-8")
    tx = retitle._Transaction(tmp_path)
    tx.write(path, "one\n")
    tx.write(path, "two\n")
    tx.rollback()
    assert path.read_text(encoding="utf-8") == "original\n"


def test_a_failing_helper_after_someone_elses_commit_rolls_nothing_back(vault: Path) -> None:
    sheet = _planned(vault)
    _repo(vault)

    def foreign_then_fail(v: Path, message: str, rel: list[str]) -> str:
        (v / "other.md").write_text("x\n", encoding="utf-8")
        _git(v, "add", "other.md")
        _git(v, "commit", "-qm", "someone else")
        raise RetitleError("helper failed")

    with pytest.raises(RetitleError, match="isn't this pass's commit"):
        retitle.apply(vault=vault, today=TODAY.isoformat(), sha=sheet.sha, commit=foreign_then_fail)
    assert "**Ship the export**" in "\n".join(_lines(vault, TODAY))


def test_apply_refuses_an_older_file_or_a_missing_helper(vault: Path) -> None:
    sheet = _planned(vault)
    _file(vault, TODAY + dt.timedelta(days=1), yellow=_task("YEL1", LONG))
    with pytest.raises(RetitleError, match="is not the newest daily file"):
        retitle.apply(vault=vault, today=TODAY.isoformat(), sha=sheet.sha, commit=Committer())
    (vault / "action-items" / f"action-items-{TODAY + dt.timedelta(days=1)}.md").unlink()
    (vault / "scripts" / "git-safe-commit.sh").unlink()
    with pytest.raises(RetitleError, match="is missing from the vault"):
        retitle.apply(vault=vault, today=TODAY.isoformat(), sha=sheet.sha, commit=Committer())


def test_rewrite_daily_reports_rows_that_no_longer_match(vault: Path) -> None:
    _setup(vault)
    daily = vault / "action-items" / f"action-items-{TODAY}.md"
    tx = retitle._Transaction(vault)
    assert retitle._rewrite_daily(tx, daily, {"YEL1": ("not its title", SHORT)}, TODAY) == ([], ["YEL1"], [])
    assert tx.saved == {}


def test_records_without_a_final_newline_are_appended_to_cleanly(vault: Path) -> None:
    sheet = _planned(vault)
    target = stale.rewrites_path(vault)
    target.parent.mkdir(parents=True)
    target.write_text('{"tag": "OLD1", "on": "2026-01-01", "before": "a", "after": "b"}', encoding="utf-8")
    retitle.apply(vault=vault, today=TODAY.isoformat(), sha=sheet.sha, commit=Committer())
    rows = target.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 3 and all(json.loads(r) for r in rows)


def test_first_seen_skips_unreadable_files_and_stops_when_done(vault: Path) -> None:
    _file(vault, FIRST, yellow=_task("YEL1", LONG))
    (vault / "action-items" / f"action-items-{FIRST + dt.timedelta(days=1)}.md").write_bytes(b"\xff")
    _file(vault, TODAY, yellow=_task("YEL1", LONG) + _task("LATE", LONG))
    assert retitle._first_seen(vault, {"YEL1", "LATE", "NONE"}) == {"YEL1": FIRST, "LATE": TODAY}
    assert retitle._first_seen(vault, {"YEL1"}) == {"YEL1": FIRST}


def test_apply_with_nothing_ready_applies_nothing(vault: Path) -> None:
    sheet = _planned(vault, {"RED1": "Ship it — now", "YEL1": SHORT})
    result = retitle.apply(vault=vault, today=TODAY.isoformat(), sha=sheet.sha, commit=Committer())
    assert result.applied == ["YEL1"]  # the row with a problem is passed over
    again = retitle.plan(vault=vault, today=TODAY.isoformat(), proposer=Stub({"RED1": "Ship it — now"}))
    assert retitle.apply(vault=vault, today=TODAY.isoformat(), sha=again.sha, commit=Committer()).applied == []


def test_apply_with_every_row_drifting_in_the_rewrite_commits_nothing(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sheet = _planned(vault)
    monkeypatch.setattr(retitle, "_rewrite_daily", lambda tx, daily, changes, day: ([], list(changes), []))
    committer = Committer()
    result = retitle.apply(vault=vault, today=TODAY.isoformat(), sha=sheet.sha, commit=committer)
    assert result.applied == [] and result.drifted == ["RED1", "YEL1"] and committer.calls == []


def test_a_failure_while_writing_puts_everything_back(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sheet = _planned(vault)
    before = _snapshot(vault)

    def disk_full(*_args: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(retitle, "_write_records", disk_full)
    with pytest.raises(OSError, match="disk full"):
        retitle.apply(vault=vault, today=TODAY.isoformat(), sha=sheet.sha, commit=Committer())
    assert _snapshot(vault) == before


def _cleanup_commit(vault: Path, rows: list[str]) -> str:
    target = stale.rewrites_path(vault)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("".join(r + "\n" for r in rows), encoding="utf-8")
    _git(vault, "add", "-f", str(target))
    _git(vault, "commit", "-qm", "records")
    return _git(vault, "rev-parse", "HEAD").strip()


def test_plan_undo_reads_only_well_formed_rows_once(vault: Path) -> None:
    _setup(vault)
    _repo(vault)
    row = json.dumps({"tag": "YEL1", "on": str(TODAY), "before": "a", "after": "b"})
    sha = _cleanup_commit(vault, ["junk", "{}", row, row])
    plan = retitle.plan_undo(vault=vault, today=TODAY.isoformat(), commit_sha=sha)
    assert plan.changed == ["YEL1"] and plan.restore == {}  # its note is missing
    with pytest.raises(RetitleError, match="nothing to undo"):
        retitle.undo(vault=vault, today=TODAY.isoformat(), commit_sha=sha)
    junk_only = _cleanup_commit(vault, ["junk", "{}", row, row, "still junk"])
    with pytest.raises(RetitleError, match="recorded no title rewrites"):
        retitle.plan_undo(vault=vault, today=TODAY.isoformat(), commit_sha=junk_only)


def test_undo_refuses_a_title_that_lost_its_bold(vault: Path) -> None:
    sheet = _planned(vault)
    _repo(vault)
    result = retitle.apply(vault=vault, today=TODAY.isoformat(), sha=sheet.sha)
    assert result.commit
    daily = vault / "action-items" / f"action-items-{TODAY}.md"
    daily.write_text(daily.read_text(encoding="utf-8").replace(f"**{SHORT}**", SHORT), encoding="utf-8")
    before = _snapshot(vault)
    with pytest.raises(RetitleError, match="title isn't bold"):
        retitle.undo(vault=vault, today=TODAY.isoformat(), commit_sha=result.commit)
    assert _snapshot(vault) == before


def test_undo_with_every_row_drifting_in_the_rewrite_changes_nothing(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sheet = _planned(vault)
    _repo(vault)
    result = retitle.apply(vault=vault, today=TODAY.isoformat(), sha=sheet.sha)
    assert result.commit
    monkeypatch.setattr(retitle, "_rewrite_daily", lambda tx, daily, changes, day: ([], list(changes), []))
    with pytest.raises(RetitleError, match="nothing to undo"):
        retitle.undo(vault=vault, today=TODAY.isoformat(), commit_sha=result.commit)


def test_a_failure_while_undoing_puts_everything_back(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sheet = _planned(vault)
    _repo(vault)
    result = retitle.apply(vault=vault, today=TODAY.isoformat(), sha=sheet.sha)
    assert result.commit
    before = _snapshot(vault)

    def disk_full(*_args: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(retitle, "_write_records", disk_full)
    with pytest.raises(OSError, match="disk full"):
        retitle.undo(vault=vault, today=TODAY.isoformat(), commit_sha=result.commit)
    assert _snapshot(vault) == before
