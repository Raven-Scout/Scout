"""The run brokers' checks, limits, queue and profile rules (Phase 1d, spec 2026-11-16 §9.1).

Each test states a property the brokers keep: an allowed commit request passes, a request outside the allowed
paths is refused before git runs, the owner's limits hold whatever the request says, a queued request lands
once and in order, and the tightened rules refuse git-dir writes and secret reads while ordinary work goes on.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from scout.kb import hook_paths
from scout.sandbox import broker
from scout.sandbox import profile as sbx

NOW = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    v = (tmp_path / "vault").resolve()
    for d in ("notes", "scripts", ".scout-state", ".scout-logs"):
        (v / d).mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(v)], check=True)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(v))
    hook_paths._ROOTS_CACHE.clear()
    return v


def _env() -> dict[str, str]:
    return dict(os.environ)


def _commit(paths: list[str], message: str = "notes: x", **extra: Any) -> dict[str, Any]:
    return {"v": 1, "verb": "commit", "message": message, "paths": paths, **extra}


# -- commit requests -------------------------------------------------------------------------------------------------


def test_ordinary_vault_paths_pass_and_come_back_relative(vault: Path) -> None:
    msg, paths, override = broker.validate_commit(
        _commit(["notes/a.md", str(vault / "notes" / "b.md"), "knowledge-base/projects/x.md"]), vault, _env()
    )
    assert msg == "notes: x"
    assert paths == ["notes/a.md", "notes/b.md", "knowledge-base/projects/x.md"]
    assert override is None


def test_the_lint_override_is_carried_and_nothing_else_from_the_request(vault: Path) -> None:
    req = _commit(["notes/a.md"], override="kept after two lint fixes", env={"GIT_DIR": "/x"}, argv=["--no-verify"])
    _, _, override = broker.validate_commit(req, vault, _env())
    assert override == "kept after two lint fixes"
    env = broker.commit_env(vault, {"PATH": "/usr/bin", "HOME": "/h", "GIT_DIR": "/x", "SCOUT_MODE": "scout"}, override)
    assert env["SCOUT_LINT_OVERRIDE"] == override
    assert env["GIT_LITERAL_PATHSPECS"] == "1" and env["SCOUT_DATA_DIR"] == str(vault)
    assert not [k for k in env if k.startswith("GIT_") and k != "GIT_LITERAL_PATHSPECS"]
    assert env["SCOUT_MODE"] == "scout"


def test_broker_commits_always_get_the_scheduled_run_checks(vault: Path) -> None:
    env = broker.commit_env(vault, {"PATH": "/usr/bin", "HOME": "/h"}, None)
    assert env["SCOUT_MODE"] and env["SCOUT_READER"] == "autonomous"


@pytest.mark.parametrize(
    ("paths", "reason"),
    [
        (["../outside.md"], "outside the vault"),
        (["/etc/hosts"], "outside the vault"),
        ([".git/config"], "git's own folder"),
        (["notes/../.git/HEAD"], "git's own folder"),
        (["scripts/claude-with-retry.sh"], "protected"),
        ([".claude/settings.local.json"], "protected"),
        (["--all"], "blanket"),
        (["."], "blanket"),
        (["-n"], "can't start with"),
        ([":(glob)**"], "can't start with"),
        (["notes/a\nb.md"], "control character"),
        ([], "usage"),
    ],
)
def test_a_path_outside_what_a_run_may_commit_is_refused(vault: Path, paths: list[str], reason: str) -> None:
    with pytest.raises(broker.Refused) as err:
        broker.validate_commit(_commit(paths), vault, _env())
    assert err.value.exit_code == broker.EXIT_REFUSED
    assert reason in err.value.reason


def test_a_link_out_of_the_vault_is_refused(vault: Path, tmp_path: Path) -> None:
    (tmp_path / "elsewhere.md").write_text("x\n")
    (vault / "notes" / "link.md").symlink_to(tmp_path / "elsewhere.md")
    with pytest.raises(broker.Refused, match="outside the vault"):
        broker.validate_commit(_commit(["notes/link.md"]), vault, _env())


@pytest.mark.parametrize(
    "req",
    [
        _commit(["notes/a.md"], message=""),
        _commit(["notes/a.md"], message="x" * (broker.MAX_MESSAGE_CHARS + 1)),
        _commit([f"notes/{i}.md" for i in range(broker.MAX_PATHS + 1)]),
        _commit(["notes/a.md"], override="line one\nline two"),
    ],
)
def test_requests_over_the_size_rules_are_refused(vault: Path, req: dict[str, Any]) -> None:
    with pytest.raises(broker.Refused):
        broker.validate_commit(req, vault, _env())


def test_a_refused_commit_never_starts_git(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[object] = []
    monkeypatch.setattr(broker.subprocess, "Popen", lambda *a, **k: started.append(a))
    reply = broker.Broker(vault, _env()).handle(_commit(["../x.md"]))
    assert reply.exit == broker.EXIT_REFUSED and "outside the vault" in reply.stderr
    assert started == []


def test_unknown_requests_are_answered_not_obeyed(vault: Path) -> None:
    b = broker.Broker(vault, _env())
    assert b.handle({"v": 1, "verb": "ping"}).stdout == "pong"
    assert b.handle({"v": 2, "verb": "commit"}).exit == broker.EXIT_REFUSED
    assert b.handle({"v": 1, "verb": "push"}).exit == broker.EXIT_REFUSED
    assert b.handle(["not", "a", "request"]).exit == broker.EXIT_REFUSED


# -- Telegram --------------------------------------------------------------------------------------------------------


class Sender:
    def __init__(self) -> None:
        self.calls: list[Mapping[str, Any]] = []

    def __call__(self, fields: Mapping[str, Any]) -> broker.Reply:
        self.calls.append(dict(fields))
        return broker.Reply(0, "id=01TEST\n", "{}")


def _tg(body: str = "wrap", **extra: Any) -> dict[str, Any]:
    return {"v": 1, "verb": "telegram", "tier": "info", "parse_mode": "html", "body": body, **extra}


def test_the_send_carries_only_the_message_never_a_recipient(vault: Path) -> None:
    sender = Sender()
    reply = broker.Broker(vault, _env(), sender=sender).handle(_tg(chat_id="12345", token="t", url="https://x"))
    assert reply.exit == 0
    assert set(sender.calls[0]) == {"tier", "parse_mode", "body", "allow_duplicate", "dry_run"}


def test_the_per_run_limit_holds(vault: Path) -> None:
    sender = Sender()
    b = broker.Broker(vault, _env(), limits=broker.Limits(max_per_run=2), sender=sender, clock=lambda: NOW)
    codes = [b.handle(_tg(f"m{i}")).exit for i in range(3)]
    assert codes == [0, 0, broker.EXIT_LIMIT] and len(sender.calls) == 2


def test_the_hourly_limit_holds_across_runs(vault: Path) -> None:
    sender = Sender()
    limits = broker.Limits(max_per_run=10, max_per_hour=3)
    first = broker.Broker(vault, _env(), limits=limits, sender=sender, clock=lambda: NOW)
    assert [first.handle(_tg(f"a{i}")).exit for i in range(2)] == [0, 0]
    second = broker.Broker(vault, _env(), limits=limits, sender=sender, clock=lambda: NOW + timedelta(minutes=5))
    assert [second.handle(_tg(f"b{i}")).exit for i in range(2)] == [0, broker.EXIT_LIMIT]
    later = broker.Broker(vault, _env(), limits=limits, sender=sender, clock=lambda: NOW + timedelta(minutes=61))
    assert later.handle(_tg("c")).exit == 0
    assert len(sender.calls) == 4


def test_the_size_limit_holds_and_dry_runs_cost_nothing(vault: Path) -> None:
    sender = Sender()
    b = broker.Broker(vault, _env(), limits=broker.Limits(max_per_run=1, max_chars=10), sender=sender)
    assert b.handle(_tg("x" * 11)).exit == broker.EXIT_LIMIT
    assert [b.handle(_tg("hi", dry_run=True)).exit for _ in range(3)] == [0, 0, 0]
    assert b.handle(_tg("hi")).exit == 0
    assert len(sender.calls) == 4


def test_limits_come_from_the_owner_file_or_the_defaults(tmp_path: Path) -> None:
    env = {"HOME": str(tmp_path)}
    assert broker.load_limits(env) == broker.Limits()
    path = broker.owner_dir(env) / broker.CONFIG_NAME
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"telegram": {"max_per_run": 2, "max_per_hour": "lots", "max_chars": -1}}))
    assert broker.load_limits(env) == broker.Limits(max_per_run=2)
    path.write_text("not json")
    assert broker.load_limits(env) == broker.Limits()


def test_the_owner_folder_is_protected_from_runs(tmp_path: Path) -> None:
    env = {**os.environ, "HOME": str(tmp_path)}
    hook_paths._ROOTS_CACHE.clear()
    target = broker.owner_dir(env) / broker.CONFIG_NAME
    assert hook_paths.protected_write(target, None, env) == hook_paths.ENGINE


# -- the queue -------------------------------------------------------------------------------------------------------


def test_a_queued_entry_is_written_whole_and_private(vault: Path) -> None:
    qid = broker.enqueue(vault, "commit", _commit(["notes/a.md"]), now=NOW)
    path = broker.queue_dir(vault, "commit") / f"{qid}.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    entry = json.loads(path.read_text())
    assert entry["paths"] == ["notes/a.md"] and entry["attempts"] == 0 and "verb" not in entry
    assert [p.name for p in path.parent.iterdir()] == [path.name]  # no temp file left behind


class Recorder(broker.Broker):
    def __init__(self, vault: Path, codes: dict[str, int]) -> None:
        super().__init__(vault, _env(), clock=lambda: NOW)
        self.codes = codes
        self.seen: list[tuple[str, Any]] = []

    def handle(self, req: Any) -> broker.Reply:
        key = req.get("message") or req.get("body")
        self.seen.append((req["verb"], key))
        return broker.Reply(self.codes.get(key, 0))


def test_the_drain_applies_entries_in_order_and_removes_them(vault: Path) -> None:
    for i in range(3):
        broker.enqueue(vault, "commit", _commit(["notes/a.md"], message=f"c{i}"), now=NOW)
    broker.enqueue(vault, "telegram", _tg("t0"), now=NOW)
    rec = Recorder(vault, {})
    result = broker.drain(vault, broker=rec)
    assert rec.seen == [("commit", "c0"), ("commit", "c1"), ("commit", "c2"), ("telegram", "t0")]
    assert result.done == 4 and broker.pending(vault) == 0


def test_the_queue_folder_decides_the_verb(vault: Path) -> None:
    broker.enqueue(vault, "commit", {**_commit(["notes/a.md"]), "verb": "telegram"}, now=NOW)
    rec = Recorder(vault, {})
    broker.drain(vault, broker=rec)
    assert rec.seen == [("commit", "notes: x")]


def test_a_transient_failure_stays_queued_and_counts_its_attempts(vault: Path) -> None:
    qid = broker.enqueue(vault, "commit", _commit(["notes/a.md"], message="busy"), now=NOW)
    result = broker.drain(vault, broker=Recorder(vault, {"busy": 3}))
    assert result.kept == 1
    entry = json.loads((broker.queue_dir(vault, "commit") / f"{qid}.json").read_text())
    assert entry["attempts"] == 1 and entry["last_exit"] == 3


def test_refused_expired_and_exhausted_entries_move_to_failed(vault: Path) -> None:
    broker.enqueue(vault, "commit", _commit(["notes/a.md"], message="refused"), now=NOW)
    broker.enqueue(vault, "commit", _commit(["notes/a.md"], message="old"), now=NOW - timedelta(hours=25))
    qid = broker.enqueue(vault, "telegram", _tg("tired"), now=NOW)
    path = broker.queue_dir(vault, "telegram") / f"{qid}.json"
    path.write_text(json.dumps({**json.loads(path.read_text()), "attempts": broker.QUEUE_MAX_ATTEMPTS}))
    (broker.queue_dir(vault, "commit") / "junk.json").write_text("{not json")
    rec = Recorder(vault, {"refused": broker.EXIT_REFUSED})
    result = broker.drain(vault, broker=rec)
    assert result.failed == 4 and broker.pending(vault) == 0
    assert len(broker.failed_entries(vault)) == 4
    assert rec.seen == [("commit", "refused")]  # the expired, exhausted and malformed ones never ran
    events = "".join(p.read_text() for p in (vault / ".scout-logs").glob("schedule-events-*.jsonl"))
    assert events.count("broker.queue_failed") == 4


def test_one_drain_at_a_time(vault: Path) -> None:
    broker.enqueue(vault, "commit", _commit(["notes/a.md"]), now=NOW)
    with broker._locked(broker.owner_dir(os.environ) / broker.DRAIN_LOCK_NAME):
        result = broker.drain(vault, broker=Recorder(vault, {}))
    assert result.busy and broker.pending(vault) == 1


def test_a_client_with_no_broker_queues_a_commit_and_says_there_is_no_sha(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = broker.client_commit("notes: a", ["notes/a.md"], env={"SCOUT_DATA_DIR": str(vault)})
    assert code == broker.EXIT_QUEUED
    assert "no SHA yet" in capsys.readouterr().err
    assert broker.pending(vault) == 1


def test_a_client_with_no_broker_queues_a_telegram_message_as_delivered_later(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fields = {"tier": "info", "parse_mode": "none", "body": "wrap", "allow_duplicate": False, "dry_run": False}
    assert broker.client_telegram(fields, env={"SCOUT_DATA_DIR": str(vault)}) == 0
    err = capsys.readouterr().err
    assert err.startswith("id=") and "after this session" in err
    assert broker.pending(vault) == 1


# -- the profile rules -----------------------------------------------------------------------------------------------


def test_the_tightened_rules_refuse_git_dir_writes_and_secret_reads_and_keep_ordinary_work(
    vault: Path, tmp_path: Path
) -> None:
    env = _env()
    (Path(env["HOME"]) / ".scout-secrets").mkdir(exist_ok=True)
    broker_dir = tmp_path / "broker"
    base = sbx.build_rules(vault, env)
    rules = base + sbx.broker_rules(vault, broker_dir, env)
    git = vault / ".git"
    for rel in ("objects/ab/cd", "refs/heads/main", "index", "index.lock", "HEAD", "logs/HEAD", "packed-refs"):
        assert sbx.decide(base, str(git / rel)) == "allow", rel  # 1c's carve-outs …
        assert sbx.decide(rules, str(git / rel)) == "deny", rel  # … are gone while the broker commits
    secrets = Path(env["HOME"]) / ".scout-secrets"
    for target in (secrets / "telegram-bot-token", secrets / "telegram-chat-id", secrets):
        assert sbx.decide(rules, str(target), "read") == "deny"
    assert sbx.decide(rules, str(broker_dir / "b.sock"), "unlink") == "deny"
    for ok in (vault / "notes" / "a.md", vault / ".scout-state" / "broker-queue" / "commit" / "x.json"):
        assert sbx.decide(rules, str(ok)) == "allow"
        assert sbx.decide(rules, str(ok), "read") == "allow"


def test_the_rules_render_after_the_base_profile(vault: Path, tmp_path: Path) -> None:
    env = _env()
    text = sbx.with_broker_rules("(version 1)\n(allow default)\n", sbx.broker_rules(vault, tmp_path / "b", env))
    assert text.startswith("(version 1)\n(allow default)\n;; Phase 1d")
    assert f'(subpath "{vault / ".git"}")' in text


def test_the_hook_layer_reads_the_telegram_files_only_without_a_broker(tmp_path: Path) -> None:
    env = {"HOME": str(tmp_path)}
    token = tmp_path / ".scout-secrets" / "telegram-bot-token"
    assert hook_paths.secret_read(token, env) is None
    assert hook_paths.secret_read(token, {**env, "SCOUT_RUN_BROKER": "/tmp/b.sock"}) == hook_paths.SECRETS
    other = tmp_path / ".scout-secrets" / "linear-api-key"
    assert hook_paths.secret_read(other, env) == hook_paths.SECRETS


def test_an_old_helper_keeps_the_run_on_the_1c_profile(vault: Path, tmp_path: Path) -> None:
    (vault / "scripts" / "git-safe-commit.sh").write_text('#!/bin/bash\ngit commit -- "$@"\n')
    with pytest.raises(broker.BrokerError, match="predates"):
        broker.build_profile(vault, tmp_path, "(version 1)\n(allow default)\n")


# -- the replay ------------------------------------------------------------------------------------------------------


def _row(kind: str, block: dict[str, Any]) -> str:
    return json.dumps({"type": kind, "message": {"content": [block]}})


def test_the_replay_names_a_replacement_for_every_in_vault_git_write(vault: Path, tmp_path: Path) -> None:
    from scout.sandbox import replay

    project = tmp_path / "project"
    project.mkdir()
    calls = [
        ("u1", "Bash", {"command": 'scripts/git-safe-commit.sh "notes: a" notes/a.md'}),
        ("u2", "Write", {"file_path": str(vault / "notes" / "log.md"), "content": "commit abc1234 run"}),
        ("u3", "Bash", {"command": "git add notes/b.md && git commit -qm b"}),
        ("u4", "Bash", {"command": "git checkout -- notes/c.md; git restore --staged notes/d.md; git log -1"}),
        ("u5", "Bash", {"command": 'git config user.email "alex@example.com"'}),
        ("u6", "Bash", {"command": "scoutctl notify telegram --body-file - <<'X'\nwrap\nX"}),
        ("u7", "Bash", {"command": "git -C /elsewhere/repo commit -qm other"}),
    ]
    rows = [json.dumps({"entrypoint": "sdk-cli", "type": "system"})]
    for cid, name, inp in calls:
        rows.append(_row("assistant", {"type": "tool_use", "id": cid, "name": name, "input": inp}))
        rows.append(_row("progress", {"type": "tool_use", "id": cid, "name": name, "input": inp}))  # not counted
        if cid == "u1":
            result = "git-safe-commit: committed abc1234 (1 path(s))"
            rows.append(_row("user", {"type": "tool_result", "tool_use_id": cid, "content": result}))
    (project / "s.jsonl").write_text("\n".join(rows) + "\n")
    report = replay.replay(project, vault, limit=5, brokers=True)
    b = report["brokers"]
    assert b["helper_calls_kept"] == 1 and b["helper_commits_with_sha"] == 1
    assert b["sessions_reusing_a_sha_mid_session"] == 1 and b["telegram_calls_kept"] == 1
    kinds = {k: v["calls"] for k, v in b["vault_git_writes"].items()}
    assert kinds == {"add": 1, "commit": 1, "checkout --": 1, "restore --staged": 1, "config (write)": 1}
    assert b["vault_git_writes_without_a_replacement"] == 0
    assert b["newly_denied_writes_outside_the_git_dir"] == 0
    assert "with the run brokers" in replay.format_report(report)


# -- health ------------------------------------------------------------------------------------------------------


def test_status_and_failed_entries_reach_health(vault: Path) -> None:
    from scout import health

    started = datetime.now(UTC) - timedelta(minutes=5)
    row = health.OutcomeRow(
        slot="morning", mode="morning", started_at=started, finished_at=started + timedelta(minutes=4),
        exit_code=0, failure_class="", outcome="ok", log=None, commits_in_window=None, newest_commit=None,
        sandbox="enforced",
    )  # fmt: skip
    assert health.broker_findings([row], vault=vault) == []
    broker.write_status(vault, "unavailable", "the broker didn't answer within 10s")
    broker.enqueue(vault, "commit", _commit(["notes/a.md"]), now=NOW - timedelta(hours=30))
    broker.drain(vault, broker=Recorder(vault, {}))
    ids = [f.id for f in health.broker_findings([row], vault=vault)]
    assert ids == ["runs.broker_unavailable", "broker.queue_failed"]
    broker.write_status(vault, "on", "commits and Telegram go through the run's broker")
    assert [f.id for f in health.broker_findings([row], vault=vault)] == ["broker.queue_failed"]
