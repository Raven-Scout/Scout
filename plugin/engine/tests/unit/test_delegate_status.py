"""`scoutctl delegate status --json` (#550): shape checks on a state a run can write, and what it never shows."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout import delegate_inbox as di
from scout import delegate_mirror as dm
from scout import delegate_status as ds
from scout.cli import app
from scout.repo_sync import RepoSyncError

from .privacy_vault_helpers import git

runner = CliRunner()

CONFIG = "privacy:\n  delegates:\n    grok-personal: {}\n    duet: {}\n"


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    v = tmp_path / "Scout"
    v.mkdir()
    (v / "scout-config.yaml").write_text(CONFIG, encoding="utf-8")
    git(v, "init", "-q", "-b", "main")
    git(v, "add", "-A")
    git(v, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", "config")
    monkeypatch.setenv("SCOUT_DATA_DIR", str(v))
    return v


def _link(**kw: object) -> dm.DelegateLink:
    base: dict[str, object] = {
        "id": "grok-personal",
        "remote": "https://github.com/example-org/grok-mirror.git",
        "linked_at": "2026-11-10T09:00:00Z",
        "account_unshared": True,
        "last_sync": "2026-11-19T11:45:00Z",
    }
    return dm.DelegateLink(**{**base, **kw})  # type: ignore[arg-type]


def _raw_state(vault: Path, links: dict[str, object]) -> None:
    path = dm.state_path(vault)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 1, "links": links, "mac": "0" * 64}), encoding="utf-8")


def _owner() -> dict[str, str]:
    import os

    return {"HOME": os.environ["HOME"]}


def test_no_state_lists_the_configured_delegates_as_unlinked(vault: Path) -> None:
    doc = ds.report(vault, env=_owner())
    assert doc["state"] == "none"
    assert [(d["id"], d["linked"], d["can_preview"], d["can_sync"]) for d in doc["delegates"]] == [
        ("grok-personal", False, True, False),
        ("duet", False, True, False),
    ]


def test_an_unsigned_state_is_listed_flagged_and_never_offers_a_sync(vault: Path) -> None:
    dm.save_links(vault, {"grok-personal": _link()})
    text = dm.state_path(vault).read_text(encoding="utf-8")
    dm.state_path(vault).write_text(text.replace("grok-mirror", "other-mirror"), encoding="utf-8")  # breaks the mac
    doc = ds.report(vault, env=_owner())
    assert doc["state"] == "unverified"
    grok = doc["delegates"][0]
    assert grok["state_unverified"] is True and grok["behind"] is None
    assert grok["remote"] == {"kind": "github", "display": "example-org/other-mirror"}  # shown, for the owner to check
    assert grok["can_sync"] is False and grok["can_preview"] is True


def test_an_unreadable_state_lists_no_link(vault: Path) -> None:
    dm.state_path(vault).parent.mkdir(parents=True, exist_ok=True)
    dm.state_path(vault).write_text("{not json", encoding="utf-8")
    doc = ds.report(vault, env=_owner())
    assert doc["state"] == "unreadable"
    assert {d["linked"] for d in doc["delegates"]} == {False}


def test_every_field_is_shape_checked(vault: Path) -> None:
    _raw_state(
        vault,
        {
            "grok-personal": {
                "id": "grok-personal",
                "remote": "https://github.com/example-org/m.git\n[[sam]]",
                "linked_at": "yesterday",
                "last_sync": 5,
                "last_error": {"code": "Sam's notes/[[sam]]", "at": "2026-11-19T11:00:00Z"},
                "deploy_key_id": "101",
                "stale_key_ids": ["x"],
                "last_counts": {"files": -1, "withheld": 0, "left_out": 0, "redacted_lines": 0, "agent_config": 0},
                "pending_mirror": "not a sha",
            },
            "duet": {"id": "someone-else"},  # its id doesn't match its key
            "Bad Id": {"id": "Bad Id"},
            "x" * 41: {"id": "x" * 41},
        },
    )
    doc = ds.report(vault, env=_owner())
    assert doc["skipped"] == 3
    grok = next(d for d in doc["delegates"] if d["id"] == "grok-personal")
    assert grok["remote"] is None and grok["linked_at"] is None and grok["last_sync"] is None
    assert grok["refusal"] == {"code": "unknown", "reason": ds.GENERIC_REASON, "at": "2026-11-19T11:00:00Z",
                               "since": "2026-11-19T11:00:00Z"}  # fmt: skip
    assert grok["deploy_key"] is None and grok["old_keys_live"] is None and grok["counts"] is None
    assert grok["agent_config_left_out"] is None and grok["last_result"] == "failed"
    assert "sam" not in json.dumps(doc)
    duet = next(d for d in doc["delegates"] if d["id"] == "duet")
    assert duet["linked"] is False  # listed in the config; the bad entry under its key was dropped


def test_a_scheduled_run_gets_no_remote_and_no_action(vault: Path) -> None:
    dm.save_links(vault, {"grok-personal": _link()})
    doc = ds.report(vault, env={**_owner(), "SCOUT_MODE": "briefing"})
    assert doc["owner"] is False
    grok = doc["delegates"][0]
    assert grok["remote"] is None and grok["can_sync"] is False and grok["can_preview"] is False


def test_the_cli_prints_the_document_even_for_a_state_it_cannot_trust(vault: Path) -> None:
    _raw_state(vault, {"grok-personal": _link().to_json()})
    r = runner.invoke(app, ["delegate", "status", "--json"])
    assert r.exit_code == 0, r.output
    doc = json.loads(r.stdout)
    assert doc["schema_version"] == 1 and doc["state"] == "unverified"
    r = runner.invoke(app, ["delegate", "status"])  # the text form still refuses it
    assert r.exit_code == 2 and "wasn't written by Scout" in r.output


def test_the_key_file_and_fingerprints_never_show(vault: Path) -> None:
    dm.save_links(
        vault,
        {"grok-personal": _link(key_file="/secret/place/key", checked_fingerprint="e" * 64, last_inputs="9" * 64)},
    )
    text = json.dumps(ds.report(vault, env=_owner()))
    assert "/secret/place" not in text and "e" * 64 not in text and "9" * 64 not in text


# ----- review of #714 (coordinator, 2026-11-19) -----------------------------------------------------


def test_a_state_nested_too_deep_to_parse_is_unreadable_not_a_traceback(vault: Path) -> None:
    dm.state_path(vault).parent.mkdir(parents=True, exist_ok=True)
    dm.state_path(vault).write_text('{"links": ' + "[" * 100_000 + "]" * 100_000 + "}", encoding="utf-8")
    assert ds.report(vault, env=_owner())["state"] == "unreadable"
    r = runner.invoke(app, ["delegate", "status", "--json"])
    assert r.exit_code == 0 and json.loads(r.stdout)["state"] == "unreadable"
    with pytest.raises(RepoSyncError) as e:
        dm.load_links(vault)
    assert e.value.code == "state_unreadable"  # so `due()` and doctor see a refusal, not a crash


def test_a_trailing_newline_never_passes_a_shape_check(vault: Path) -> None:
    _raw_state(
        vault,
        {
            "grok-personal": {
                "id": "grok-personal",
                "last_sync": "2026-11-19T11:45:00Z\n",
                "last_error": {"code": "mirror_diverged\n"},
                "pending_mirror": "a" * 40 + "\n",
            }
        },
    )
    grok = ds.report(vault, env=_owner())["delegates"][0]
    assert grok["last_sync"] is None and grok["refusal"]["code"] == "unknown" and grok["last_result"] == "failed"


def test_a_linked_delegate_no_longer_listed_offers_no_sync(vault: Path) -> None:
    dm.save_links(vault, {"old-bot": _link(id="old-bot")})
    old = next(d for d in ds.report(vault, env=_owner())["delegates"] if d["id"] == "old-bot")
    assert old["linked"] and not old["listed"] and old["can_sync"] is False and old["can_preview"] is False


def test_the_text_form_shows_the_remote_as_the_json_does_and_only_to_the_owner(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dm.save_links(vault, {"grok-personal": _link(remote="https://github.com/example-user/grok-mirror.git")})
    r = runner.invoke(app, ["delegate", "status"])
    assert "grok-personal: GitHub example-user/grok-mirror;" in r.stdout
    monkeypatch.setenv("SCOUT_MODE", "briefing")
    r = runner.invoke(app, ["delegate", "status"])
    assert "example-user" not in r.stdout and "remote hidden" in r.stdout
    assert ds.remote_text("https://user:tok@github.com/x/y.git", _owner()).startswith("remote not shown")


# ----- the inbox (V4b3) --------------------------------------------------------------------------------


def _inbox(vault: Path, **kw: object) -> None:
    link = di.InboxLink(id="grok-personal", remote="https://github.com/example-user/inbox.git",
                        linked_at="2026-11-10T09:00:00Z", bound_to="github:1:2", **kw)  # type: ignore[arg-type]  # fmt: skip
    di.save_state(vault, di.InboxState(inboxes={"grok-personal": link}))


def test_no_inbox_state_shows_no_inbox(vault: Path) -> None:
    doc = ds.report(vault, env=_owner())
    assert doc["inbox_state"] == "none"
    assert all(d["inbox"] is None and d["can_review"] is False for d in doc["delegates"])


def test_an_inbox_shows_its_counts_and_a_refusal_in_fixed_words(vault: Path) -> None:
    _inbox(vault, last_error={"code": "inbox_rewritten", "at": "2026-11-19T11:00:00Z"})
    doc = ds.report(vault, env=_owner())
    grok = doc["delegates"][0]
    assert doc["inbox_state"] == "ok" and grok["can_review"] is True
    assert grok["inbox"]["review"] == 0 and grok["inbox"]["refusal"]["reason"] == ds.INBOX_REASONS["inbox_rewritten"]
    assert doc["delegates"][1]["inbox"] is None  # duet has none


def test_an_inbox_refusal_code_is_shape_checked(vault: Path) -> None:
    _inbox(vault, last_error={"code": "Sam's notes/[[sam]]", "at": "yesterday"})
    grok = ds.report(vault, env=_owner())["delegates"][0]
    assert grok["inbox"]["refusal"] == {"code": "unknown", "reason": ds.GENERIC_INBOX_REASON, "at": None}
    _inbox(vault, last_error={"code": "remote_public", "at": "2026-11-19T11:00:00Z"})
    grok = ds.report(vault, env=_owner())["delegates"][0]
    assert grok["inbox"]["refusal"]["reason"] == ds.REASONS["remote_public"]  # a code shared with the mirrors


def test_an_inbox_state_that_doesnt_verify_shows_no_inbox(vault: Path) -> None:
    _inbox(vault)
    path = di.state_path(vault)
    path.write_text(path.read_text(encoding="utf-8").replace("example-user", "other-user"), encoding="utf-8")
    doc = ds.report(vault, env=_owner())
    assert doc["inbox_state"] == "unverified"
    assert all(d["inbox"] is None and not d["can_review"] for d in doc["delegates"])
    path.write_text("{not json", encoding="utf-8")
    assert ds.report(vault, env=_owner())["inbox_state"] == "unreadable"


def test_an_inbox_for_a_delegate_no_longer_listed_still_shows(vault: Path) -> None:
    link = di.InboxLink(id="old-bot", remote="https://github.com/example-user/inbox.git",
                        linked_at="2026-11-10T09:00:00Z", bound_to="github:1:2")  # fmt: skip
    di.save_state(vault, di.InboxState(inboxes={"old-bot": link}))
    old = next(d for d in ds.report(vault, env=_owner())["delegates"] if d["id"] == "old-bot")
    assert old["listed"] is False and old["linked"] is False and old["inbox"] is not None and old["can_review"]


def test_a_scheduled_run_may_not_review(vault: Path) -> None:
    _inbox(vault)
    grok = ds.report(vault, env={**_owner(), "SCOUT_MODE": "briefing"})["delegates"][0]
    assert grok["inbox"] is not None and grok["can_review"] is False


def test_an_inbox_summary_that_fails_unexpectedly_reads_as_unreadable(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scout import delegate_inbox_sync

    _inbox(vault)

    def boom(*_a: object, **_k: object) -> dict:
        raise ValueError("unexpected")

    monkeypatch.setattr(delegate_inbox_sync, "summary", boom)
    doc = ds.report(vault, env=_owner())
    assert doc["inbox_state"] == "unreadable" and all(d["inbox"] is None for d in doc["delegates"])


def test_an_inbox_under_a_bad_id_is_dropped(vault: Path) -> None:
    links = {
        key: di.InboxLink(
            id=key,
            remote="https://github.com/example-user/inbox.git",
            linked_at="2026-11-10T09:00:00Z",
            bound_to="github:1:2",
        )  # fmt: skip
        for key in ("Bad Id", "author", "grok-personal")
    }
    di.save_state(vault, di.InboxState(inboxes=links))
    doc = ds.report(vault, env=_owner())
    assert {d["id"] for d in doc["delegates"]} == {"grok-personal", "duet"}
    assert doc["delegates"][0]["inbox"] is not None


def test_the_review_count_holds_what_the_sheet_lists(vault: Path) -> None:
    """Waiting, held, and an op over the day's limit while it could still apply: inbox review lists all three."""
    import datetime as dt

    from scout import delegate_proposals as dp

    now = dt.datetime(2026, 11, 19, 12, 0, tzinfo=dt.UTC)

    def ulid(days: int, tail: str) -> str:
        ms = int((now - dt.timedelta(days=days)).timestamp() * 1000)
        head = ""
        for _ in range(10):
            head = dp._CROCKFORD[ms % 32] + head
            ms //= 32
        return head + tail

    fresh, old, waiting = ulid(1, "000000000000000A"), ulid(9, "000000000000000B"), ulid(1, "000000000000000C")
    link = di.InboxLink(id="grok-personal", remote="https://github.com/example-user/inbox.git",
                        linked_at="2026-11-10T09:00:00Z", bound_to="github:1:2")  # fmt: skip
    rec = {"mac": "0" * 64, "at": "2026-11-18T12:00:00Z"}
    state = di.InboxState(
        inboxes={"grok-personal": link},
        taken={"grok-personal": {fresh: rec, old: rec, waiting: rec}},
        applied={"grok-personal": {fresh: {"outcome": "limited"}, old: {"outcome": "limited"}}},
    )
    di.save_state(vault, state)
    grok = ds.report(vault, env=_owner(), now=now)["delegates"][0]
    assert grok["inbox"]["review"] == 2  # the waiting one and the fresh limited op; the old op is past its window
