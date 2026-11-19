"""CLI tests for `scoutctl changes scan` and `scoutctl engagement record|consent`."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout.cli import app

_runner = CliRunner()


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.delenv("SCOUT_MODE", raising=False)
    monkeypatch.delenv("SCOUT_READER", raising=False)
    return h


@pytest.fixture
def vault(tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    v = tmp_path / "Scout"
    (v / ".scout-logs").mkdir(parents=True)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(v))
    return v


@pytest.fixture
def cli():
    return lambda args: _runner.invoke(app, args)


@pytest.fixture
def git_vault(vault: Path) -> Path:
    def git(*a: str) -> None:
        subprocess.run(["git", "-C", str(vault), *a], check=True, capture_output=True, env=dict(os.environ))

    git("init", "-q")
    git("config", "user.name", "Scout Test")
    git("config", "user.email", "scout@example.invalid")
    git("config", "commit.gpgsign", "false")
    (vault / "SKILL.md").write_text("x\n")
    git("add", "SKILL.md")
    git("commit", "-q", "-m", "dreaming [22:12]: SKILL.md self-edit — x")
    return vault


def test_record_refuses_owner_kind_in_a_run(cli, vault, home, monkeypatch):
    monkeypatch.setenv("SCOUT_MODE", "dreaming")
    r = cli(["engagement", "record", "session-reliance", "--surface", "kb"])
    assert r.exit_code == 2 and not any(home.rglob("engagement.jsonl"))


def test_record_owner_kind_in_an_owner_session(cli, vault, home):
    r = cli(["engagement", "record", "approval", "--surface", "action-items", "--ref", "approve:chg-0123456789ab"])
    assert r.exit_code == 0
    assert len(list(home.rglob("engagement.jsonl"))) == 1
    assert not (vault / ".scout-logs/engagement.jsonl").exists()


def test_record_run_kind_in_a_run(cli, vault, monkeypatch):
    monkeypatch.setenv("SCOUT_MODE", "dreaming")
    r = cli(
        [
            "engagement",
            "record",
            "dm-reply",
            "--surface",
            "slack-dm",
            "--ref",
            "1790330702.929009",
            "--sentiment",
            "positive",
        ]
    )
    assert r.exit_code == 0 and (vault / ".scout-logs/engagement.jsonl").read_text().count("\n") == 1


def test_record_rejects_unknown_surface(cli, vault):
    assert cli(["engagement", "record", "dm-reply", "--surface", "carrier-pigeon"]).exit_code == 2


def test_record_rejects_unknown_kind_sentiment_and_ts(cli, vault):
    assert cli(["engagement", "record", "nope", "--surface", "kb"]).exit_code == 2
    assert cli(["engagement", "record", "dm-reply", "--surface", "kb", "--sentiment", "ecstatic"]).exit_code == 2
    assert cli(["engagement", "record", "dm-reply", "--surface", "kb", "--ts", "yesterday-ish"]).exit_code == 2


def test_consent_owner_only(cli, home, vault, monkeypatch):
    assert cli(["engagement", "consent", "--show"]).output.strip() == "not asked"
    assert cli(["engagement", "consent", "--out-of-vault", "yes"]).exit_code == 0
    monkeypatch.setenv("SCOUT_MODE", "briefing")
    assert cli(["engagement", "consent", "--out-of-vault", "no"]).exit_code == 2
    monkeypatch.delenv("SCOUT_MODE")
    assert cli(["engagement", "consent", "--show"]).output.strip() == "yes"
    [path] = home.rglob("consent.json")
    assert path.stat().st_mode & 0o777 == 0o600


def test_consent_rejects_bad_answer(cli, vault):
    assert cli(["engagement", "consent", "--out-of-vault", "maybe"]).exit_code == 2


def test_changes_scan_cli(cli, git_vault):
    from scout.changes.store import read_scan_mark

    assert cli(["changes", "scan"]).output.strip() == "1 new change(s)"
    head = subprocess.run(
        ["git", "-C", str(git_vault), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    assert read_scan_mark(git_vault) == head  # a plain scan moves the watermark
    assert cli(["changes", "scan"]).output.strip() == "0 new change(s)"


def test_changes_scan_cli_explicit_window_leaves_the_mark(cli, git_vault):
    from scout.changes.store import read_scan_mark

    assert cli(["changes", "scan", "--since", "2d"]).output.strip() == "1 new change(s)"
    assert read_scan_mark(git_vault) is None
    assert cli(["changes", "scan", "--backfill"]).output.strip() == "0 new change(s)"
    assert read_scan_mark(git_vault) is None


def test_changes_scan_bad_since(cli, git_vault):
    assert cli(["changes", "scan", "--since", "soonish"]).exit_code == 2


def _git(repo: Path, *a: str) -> None:
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, env=dict(os.environ))


def test_owner_rows_from_a_vault_worktree_key_the_installed_vault(cli, tmp_path, home, monkeypatch):
    """No SCOUT_DATA_DIR and a cwd in a worktree (its own scout-config.yaml): the engine pointer's vault, not cwd."""
    from scout.changes.store import owner_dir, vault_key
    from scout.scripts.engine_pointer import EnginePointer, write_pointer

    vault = tmp_path / "Scout"
    vault.mkdir()
    _git(vault, "init", "-q")
    _git(vault, "config", "user.name", "Scout Test")
    _git(vault, "config", "user.email", "scout@example.invalid")
    _git(vault, "config", "commit.gpgsign", "false")
    (vault / "scout-config.yaml").write_text("schedule: {}\n")
    _git(vault, "add", "scout-config.yaml")
    _git(vault, "commit", "-q", "-m", "init")
    wt = vault / ".claude" / "worktrees" / "wt"
    _git(vault, "worktree", "add", "-q", "--detach", str(wt))
    write_pointer(
        EnginePointer("0.0.0", "/e", "/e/python", "/e/scoutctl", str(vault), "dev", "2026-11-17T00:00:00Z"),
        home=home,
    )
    monkeypatch.delenv("SCOUT_DATA_DIR", raising=False)
    monkeypatch.chdir(wt)
    assert cli(["engagement", "record", "inline-comment", "--surface", "action-items"]).exit_code == 0
    [row] = [json.loads(x) for x in (owner_dir({"HOME": str(home)}) / "engagement.jsonl").read_text().splitlines()]
    assert row["vault_key"] == vault_key(vault)
    monkeypatch.chdir(tmp_path)  # the report also follows the pointer, wherever it is run from
    out = cli(["engagement", "--json", "--since", "1d"])
    assert out.exit_code == 0, out.output
    assert json.loads(out.stdout)["totals"]["feedback"] == 1


def _a_live_run(monkeypatch: pytest.MonkeyPatch, *, runs: bool) -> None:
    from scout.kb import live_run

    source = live_run.CommitSource(runs, "dreaming", "ancestry" if runs else "host")
    monkeypatch.setattr(live_run, "commit_source_now", lambda vault: source)


def test_owner_kinds_refused_when_the_process_table_shows_a_run(cli, vault, home, monkeypatch):
    """SCOUT_MODE unset is not enough: a run that hides its env is still a run (the kb-lint process check)."""
    _a_live_run(monkeypatch, runs=True)
    assert cli(["engagement", "record", "session-reliance", "--surface", "kb"]).exit_code == 2
    assert cli(["engagement", "consent", "--out-of-vault", "yes"]).exit_code == 2
    assert not any(home.rglob("engagement.jsonl")) and not any(home.rglob("consent.json"))
    # A run kind is still the run's to record.
    assert cli(["engagement", "record", "dm-reply", "--surface", "slack-dm", "--ref", "1.2/3.4"]).exit_code == 0


def test_owner_host_during_a_run_may_record(cli, vault, home, monkeypatch):
    _a_live_run(monkeypatch, runs=False)
    assert (
        cli(["engagement", "record", "approval", "--surface", "app", "--ref", "approve:chg-0123456789ab"]).exit_code
        == 0
    )
    assert cli(["engagement", "consent", "--out-of-vault", "no"]).exit_code == 0


def test_ref_is_an_id_not_a_path(cli, vault, home):
    import typer.main

    record = typer.main.get_command(app).commands["engagement"].commands["record"]  # type: ignore[attr-defined]
    [ref] = [p for p in record.params if p.name == "ref"]
    assert "path)" not in ref.help and "commit sha" in ref.help
    for path in ("/Users/someone/notes.md", "~/notes.md"):
        assert cli(["engagement", "record", "app-edit", "--surface", "app", "--ref", path]).exit_code == 2
    assert not any(home.rglob("engagement.jsonl"))
    assert cli(["engagement", "record", "app-edit", "--surface", "app", "--ref", "1790330702.929009"]).exit_code == 0


def test_bare_groups_print_help(cli, vault):
    assert "scan" in cli(["changes"]).output
    assert "record" in cli(["engagement"]).output


def test_changes_report_needs_an_existing_vault(cli, tmp_path, home, monkeypatch):
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path / "no-such-vault"))
    r = cli(["changes", "--json", "--since", "7d"])
    assert r.exit_code == 2
    assert "vault directory missing" in r.output


def test_record_needs_an_existing_vault(cli, tmp_path, home, monkeypatch):
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path / "no-such-vault"))
    r = cli(["engagement", "record", "dm-reply", "--surface", "slack-dm", "--ref", "1.2/3.4"])
    assert r.exit_code == 2
    assert "vault directory missing" in r.output


_DM = ["engagement", "record", "dm-reply", "--surface", "slack-dm"]


def test_record_twice_prints_already_recorded(cli, vault):
    args = [*_DM, "--ref", "1791624082.460129/1791625000.000100", "--ts", "1791625000.000100"]
    first = cli(args)
    second = cli(args)
    assert first.exit_code == 0 and second.exit_code == 0
    assert "already recorded" not in first.output and second.output.strip() == "already recorded"
    rows = [json.loads(x) for x in (vault / ".scout-logs/engagement.jsonl").read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["ts"].startswith("2026-10-10T") and rows[0]["ts"].endswith("+00:00")


def test_record_ts_accepts_slack_ts_and_iso_only(cli, vault):
    assert cli([*_DM, "--ref", "a/b", "--ts", "1791625000"]).exit_code == 0
    assert cli([*_DM, "--ref", "a/c", "--ts", "2026-10-10T09:00:00+00:00"]).exit_code == 0
    assert cli([*_DM, "--ref", "a/d", "--ts", "12345"]).exit_code == 2


def test_record_refuses_a_run_kind_without_ref(cli, vault, home):
    assert cli(["engagement", "record", "dm-sent", "--surface", "slack-dm"]).exit_code == 2
    assert cli(["engagement", "record", "approval", "--surface", "app"]).exit_code == 2
    assert not (vault / ".scout-logs/engagement.jsonl").exists() and not any(home.rglob("engagement.jsonl"))


def test_record_refuses_change_on_other_kinds(cli, vault):
    ok = cli([*_DM, "--ref", "1.2/3.4", "--change", "chg-0123456789ab"])
    assert ok.exit_code == 0
    row = json.loads((vault / ".scout-logs/engagement.jsonl").read_text())
    assert row["change"] == "chg-0123456789ab"
    for kind in ("dm-sent", "dm-reaction", "recurrence"):
        r = cli(["engagement", "record", kind, "--surface", "slack-dm", "--ref", "5.6", "--change", "chg-0123456789ab"])
        assert r.exit_code == 2, kind
    assert cli([*_DM, "--ref", "1.2/9.9", "--change", "chg-xyz"]).exit_code == 2


def test_verdict_wrong_is_owner_only(cli, vault, home, monkeypatch):
    args = ["engagement", "record", "verdict-wrong", "--surface", "app", "--ref", "chg-0123456789ab"]
    monkeypatch.setenv("SCOUT_MODE", "dreaming")
    assert cli(args).exit_code == 2
    assert not any(home.rglob("engagement.jsonl")) and not (vault / ".scout-logs/engagement.jsonl").exists()
    monkeypatch.delenv("SCOUT_MODE")
    assert cli(args).exit_code == 0
    assert len(list(home.rglob("engagement.jsonl"))) == 1


def test_record_with_no_home_exits_2(cli, vault, home, monkeypatch):
    def no_home():
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.delenv("SCOUT_DATA_DIR")
    monkeypatch.delenv("HOME")
    monkeypatch.setattr(Path, "home", staticmethod(no_home))
    r = cli([*_DM, "--ref", "1.2/3.4"])
    assert r.exit_code == 2 and not isinstance(r.exception, RuntimeError)
    assert "no home directory" in r.output
    assert cli(["engagement", "consent", "--out-of-vault", "yes"]).exit_code == 2
    assert cli(["engagement", "--json"]).exit_code == 2


def _cwd_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A vault found from the cwd (a worktree, say) that isn't the installed one, with SCOUT_DATA_DIR unset."""
    wt = tmp_path / "worktree-vault"
    wt.mkdir()
    (wt / "scout-config.yaml").write_text("")
    monkeypatch.delenv("SCOUT_DATA_DIR", raising=False)
    monkeypatch.chdir(wt)
    return wt


def test_changes_json_keys_owner_rows_to_the_installed_vault(cli, tmp_path, home, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from scout.changes.models import Change, Signal, Why
    from scout.changes.store import append_change, append_signal

    installed = home / "Scout"  # no SCOUT_DATA_DIR and no engine pointer: the installed vault is ~/Scout
    installed.mkdir()
    wt = _cwd_vault(tmp_path, monkeypatch)
    now = datetime.now(UTC)
    env = {"HOME": str(home)}
    ids = ("chg-0123456789ab", "chg-0123456789ac")
    for cid in ids:
        change = Change(
            id=cid,
            ts=now - timedelta(days=20),
            kind="brain-edit",
            summary="a change",
            why=Why("unknown", "", inferred=True),
            commit=cid[4:] + "0" * 28,
            files=("SKILL.md",),
            author_lane="dreaming",
        )
        append_change(wt, change)  # git and .scout-logs/ are read from the cwd's vault
    # The owner marked the first verdict wrong, keyed to the installed vault as owner rows are. A row keyed to the
    # cwd's vault, for the second, isn't the installed vault's and never counts.
    wrong = Signal(ts=now - timedelta(days=1), kind="verdict-wrong", surface="app", ref=ids[0])
    append_signal(wrong, vault=installed, env=env)
    append_signal(Signal(ts=wrong.ts, kind="verdict-wrong", surface="app", ref=ids[1]), vault=wt, env=env)
    r = cli(["changes", "--json"])
    assert r.exit_code == 0, r.output
    marked = {c["id"]: c["test"]["marked_wrong"] for c in json.loads(r.stdout)["changes"]}
    assert marked == {ids[0]: True, ids[1]: False}


def test_changes_json_with_no_home_exits_2(cli, tmp_path, home, monkeypatch):
    def no_home():
        raise RuntimeError("Could not determine home directory.")

    _cwd_vault(tmp_path, monkeypatch)
    monkeypatch.delenv("HOME")
    monkeypatch.setattr(Path, "home", staticmethod(no_home))
    r = cli(["changes", "--json"])
    assert r.exit_code == 2 and not isinstance(r.exception, RuntimeError)
    assert "no home directory" in r.output


# --- owner decisions: approve / reject / undo / wrong / auto-rollback / calibrated ---------------------------------

_PENDING = """---
summary: Let the briefing skip a carried red item
why: The same red item was carried for six days with no reply.
---

```diff
--- a/SKILL.md
+++ b/SKILL.md
@@ -1 +1 @@
-old
+new
```
"""


def _pending(vault: Path, slug: str = "skip-red") -> str:
    folder = vault / "governance-pending"
    folder.mkdir(exist_ok=True)
    (folder / f"{slug}.md").write_text(_PENDING, encoding="utf-8")
    return f"gov-{slug}"


def _owner_rows(home: Path) -> list[dict]:
    return [json.loads(x) for p in home.rglob("engagement.jsonl") for x in p.read_text().splitlines()]


def _needs_ok(cli) -> dict[str, str]:
    r = cli(["changes", "--json", "--since", "1d"])
    assert r.exit_code == 0, r.output
    return {n["id"]: n["state"] for n in json.loads(r.stdout)["needs_ok"]}


def test_approve_reject_undo_wrong_are_owner_only(cli, git_vault, home, monkeypatch):
    gov = _pending(git_vault)
    assert cli(["changes", "scan"]).exit_code == 0
    change = json.loads(cli(["changes", "--json"]).stdout)["changes"][0]["id"]
    monkeypatch.setenv("SCOUT_MODE", "dreaming")
    for args in (
        ["approve", gov],
        ["reject", gov],
        ["undo", change],
        ["wrong", change],
        ["auto-rollback", "targeted"],
        ["calibrated"],
        ["calibrated", "--reset"],
    ):
        assert cli(["changes", *args]).exit_code == 2, args
    assert not any(home.rglob("engagement.jsonl")) and not any(home.rglob("settings.json"))
    assert not (git_vault / ".scout-logs/engagement.jsonl").exists()


def test_a_live_run_with_its_env_hidden_is_refused_too(cli, vault, home, monkeypatch):
    gov = _pending(vault)
    _a_live_run(monkeypatch, runs=True)
    assert cli(["changes", "approve", gov]).exit_code == 2
    assert cli(["changes", "auto-rollback", "targeted"]).exit_code == 2
    assert not any(home.rglob("engagement.jsonl")) and not any(home.rglob("settings.json"))


def test_approve_unknown_id_exits_2(cli, vault, home):
    _pending(vault)
    for verb in ("approve", "reject"):
        r = cli(["changes", verb, "gov-nope"])
        assert r.exit_code == 2 and "no pending governance change" in r.output
    assert not any(home.rglob("engagement.jsonl"))


def test_approve_then_changes_json_shows_the_state(cli, vault, home):
    gov = _pending(vault)
    assert _needs_ok(cli) == {gov: "awaiting-ok"}
    assert cli(["changes", "approve", gov]).exit_code == 0
    assert _needs_ok(cli) == {gov: "approved"}
    [row] = _owner_rows(home)
    assert (row["kind"], row["ref"], row["surface"]) == ("approval", f"approve:{gov}", "scoutctl")
    assert cli(["changes", "reject", gov, "--surface", "app"]).exit_code == 0
    assert _needs_ok(cli) == {gov: "rejected"}


def test_approve_reject_approve_ends_approved(cli, vault, home):
    gov = _pending(vault)
    for verb in ("approve", "reject", "approve"):
        assert cli(["changes", verb, gov]).exit_code == 0
    assert _needs_ok(cli) == {gov: "approved"}
    assert [r["ref"] for r in _owner_rows(home)] == [f"approve:{gov}", f"reject:{gov}", f"approve:{gov}"]


def test_repeating_the_standing_decision_is_a_no_op(cli, vault, home):
    gov = _pending(vault)
    assert cli(["changes", "approve", gov]).exit_code == 0
    again = cli(["changes", "approve", gov])
    assert again.exit_code == 0 and again.output.strip() == "already approved"
    assert len(_owner_rows(home)) == 1
    assert cli(["changes", "reject", gov]).exit_code == 0
    assert cli(["changes", "reject", gov]).output.strip() == "already rejected"


def test_bad_surface_exits_2(cli, vault, home):
    gov = _pending(vault)
    assert cli(["changes", "approve", gov, "--surface", "kb"]).exit_code == 2
    assert not any(home.rglob("engagement.jsonl"))


def _commit_file(repo: Path, path: str, message: str) -> None:
    (repo / path).write_text(message + "\n")
    _git(repo, "add", path)
    _git(repo, "commit", "-q", "-m", message)


def test_undo_records_once_for_a_tested_change(cli, git_vault, home):
    assert cli(["changes", "scan"]).exit_code == 0
    change = json.loads(cli(["changes", "--json"]).stdout)["changes"][0]
    assert change["test"]["state"] == "testing"
    first = cli(["changes", "undo", change["id"]])
    assert first.exit_code == 0 and first.output.strip() == ""
    assert cli(["changes", "undo", change["id"]]).output.strip() == "already recorded"
    [row] = _owner_rows(home)
    assert (row["kind"], row["ref"]) == ("approval", f"undo:{change['id']}")


def test_undo_refuses_interactive_and_governance_changes(cli, git_vault, home):
    _commit_file(git_vault, "DREAMING.md", "interactive: tweak DREAMING.md")
    _commit_file(
        git_vault,
        "RESEARCH.md",
        "dreaming [22:12]: RESEARCH.md self-edit — y\n\nScout-Change-Governance: yes",
    )
    assert cli(["changes", "scan", "--since", "2d"]).exit_code == 0
    changes = json.loads(cli(["changes", "--json"]).stdout)["changes"]
    governance = next(c for c in changes if c["governance"])
    r = cli(["changes", "undo", governance["id"]])
    assert r.exit_code == 2 and "governance" in r.output
    untested = [c for c in changes if c["test"]["state"] == "not-tested"]
    assert untested, "the interactive change is listed and not under test"
    r = cli(["changes", "undo", untested[0]["id"]])
    assert r.exit_code == 2 and "isn't under test" in r.output
    assert cli(["changes", "undo", "chg-ffffffffffff"]).exit_code == 2
    assert cli(["changes", "undo", "nonsense"]).exit_code == 2
    assert not any(home.rglob("engagement.jsonl"))


def test_wrong_records_verdict_wrong_once_and_shows_in_the_report(cli, git_vault, home):
    assert cli(["changes", "scan"]).exit_code == 0
    cid = json.loads(cli(["changes", "--json"]).stdout)["changes"][0]["id"]
    assert cli(["changes", "wrong", cid, "--surface", "app"]).exit_code == 0
    assert cli(["changes", "wrong", cid]).output.strip() == "already recorded"
    [row] = _owner_rows(home)
    assert (row["kind"], row["ref"], row["surface"]) == ("verdict-wrong", cid, "app")
    assert json.loads(cli(["changes", "--json"]).stdout)["changes"][0]["test"]["marked_wrong"] is True
    assert cli(["changes", "wrong", "not-an-id"]).exit_code == 2


def test_auto_rollback_writes_settings(cli, vault, home):
    from scout.changes.settings import read_settings

    assert cli(["changes", "auto-rollback", "targeted"]).exit_code == 0
    assert read_settings({"HOME": str(home)}).auto_rollback == "targeted"
    assert json.loads(cli(["changes", "--json"]).stdout)["auto_rollback"] == "targeted"
    assert cli(["changes", "auto-rollback", "sometimes"]).exit_code == 2
    assert read_settings({"HOME": str(home)}).auto_rollback == "targeted"
    assert not (vault / ".scout-logs/settings.json").exists()


def test_calibrated_sets_today_in_the_host_zone_and_resets(cli, vault, home):
    from datetime import datetime

    from scout.changes.settings import read_settings
    from scout.config import resolve_timezone

    assert cli(["changes", "auto-rollback", "one-at-a-time"]).exit_code == 0
    assert cli(["changes", "calibrated"]).exit_code == 0
    settings = read_settings({"HOME": str(home)})
    assert settings.calibrated_on == datetime.now(resolve_timezone(vault)).date()
    assert settings.auto_rollback == "one-at-a-time"  # the other settings are kept
    assert json.loads(cli(["changes", "--json"]).stdout)["calibrating"] is False
    assert cli(["changes", "calibrated", "--reset"]).exit_code == 0
    assert read_settings({"HOME": str(home)}).calibrated_on is None
