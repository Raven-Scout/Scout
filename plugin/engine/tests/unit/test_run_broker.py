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
from dataclasses import dataclass
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
        (["scripts"], "protected"),  # a folder holding protected files
        (["notes", "scripts/"], "protected"),
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


def _lab_file(vault: Path, rel: str, size: int) -> None:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        f.truncate(size)  # sparse: no disk used


def test_commit_refuses_a_large_lab_file(vault: Path) -> None:
    rel = "knowledge-base/lab/t/experiments/t-E1-x/out.bin"
    _lab_file(vault, rel, 11 * 1024 * 1024)
    with pytest.raises(broker.Refused) as err:
        broker.validate_commit(_commit([rel]), vault, _env())
    assert err.value.exit_code == broker.EXIT_REFUSED
    assert rel in err.value.reason and "checkpoints/" in err.value.reason


def test_commit_allows_a_lab_file_at_the_limit(vault: Path) -> None:
    rel = "knowledge-base/lab/t/experiments/t-E1-x/out.bin"
    _lab_file(vault, rel, broker.LAB_COMMIT_MAX_FILE_BYTES)
    assert broker.validate_commit(_commit([rel]), vault, _env())[1] == [rel]


def test_commit_allows_a_large_file_elsewhere(vault: Path) -> None:
    """The 10 MiB rule is the lab's only: a large file outside knowledge-base/lab/ passes."""
    _lab_file(vault, "notes/big.bin", 11 * 1024 * 1024)
    _lab_file(vault, "knowledge-base/projects/big.bin", 11 * 1024 * 1024)
    msg_paths = broker.validate_commit(_commit(["notes/big.bin", "knowledge-base/projects/big.bin"]), vault, _env())[1]
    assert msg_paths == ["notes/big.bin", "knowledge-base/projects/big.bin"]


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
    assert set(sender.calls[0]) == {"tier", "parse_mode", "body", "allow_duplicate", "dry_run", "reply_to", "covers"}
    assert sender.calls[0]["reply_to"] is None and sender.calls[0]["covers"] == []


def _inbound(vault: Path, *ids: int) -> None:
    from scout.telegram_inbound.ledger import InboundMessage, Ledger

    ledger = Ledger(vault)
    with ledger.locked():
        for i in ids:
            ledger.add(InboundMessage(message_id=i, date=0, text="q", reply_to_text=None, forwarded=False, kind="text"))


def test_broker_passes_reply_to_and_covers_through(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _inbound(vault, 5, 6, 7)
    sender = Sender()
    live = broker.Broker(vault, _env(), sender=sender, run_id="scout-broker.reply")
    assert live.handle(_tg(reply_to=7, covers=[5, 6])).exit == 0
    assert sender.calls[0]["reply_to"] == 7 and sender.calls[0]["covers"] == [5, 6]
    # The default sender is cli_send in this process: it records the reply in this broker's vault, whatever the cwd.
    seen: dict[str, Any] = {}

    def fake_cli_send(**kw: Any) -> int:
        seen.update(kw)
        return 0

    from scout.scripts import notify_telegram

    monkeypatch.setattr(notify_telegram, "cli_send", fake_cli_send)
    monkeypatch.chdir("/")
    assert broker.Broker(vault, _env(), run_id="scout-broker.reply").handle(_tg("b", reply_to=7, covers=[5])).exit == 0
    assert seen["reply_to"] == 7 and seen["covers"] == [5] and seen["vault"] == vault


@pytest.mark.parametrize(
    "extra",
    [
        {"covers": "5,6"},
        {"covers": ["5"]},
        {"covers": [True]},
        {"covers": [0]},
        {"covers": [-3]},
        {"covers": [1.5]},
        {"covers": list(range(1, 102))},
        {"reply_to": "7"},
        {"reply_to": 0},
        {"reply_to": True},
        {"reply_to": 7.0},
        {"reply_to": None, "covers": [5]},  # a reply threads to a message
    ],
)
def test_broker_refuses_non_int_covers(vault: Path, extra: dict[str, Any]) -> None:
    sender = Sender()
    reply = broker.Broker(vault, _env(), sender=sender).handle(_tg(**{"reply_to": 7, **extra}))
    assert reply.exit == broker.EXIT_REFUSED and reply.kind == broker.REFUSED
    assert sender.calls == []


def test_queued_reply_with_covers_is_validated_on_drain(vault: Path) -> None:
    _inbound(vault, 5, 7)
    broker.enqueue(vault, "telegram", _tg("forged", reply_to=7, covers=["all"]), now=NOW)
    broker.enqueue(vault, "telegram", _tg("answer", reply_to=7, covers=[5, 7]), now=NOW)
    sender = Sender()
    result = broker.drain(vault, broker=broker.Broker(vault, _env(), sender=sender, clock=lambda: NOW))
    assert result.failed == 1 and result.done == 1
    assert [(c["body"], c["reply_to"], c["covers"]) for c in sender.calls] == [("answer", 7, [5, 7])]


@dataclass
class _Event:
    id: str = "01EVENT"


def _real_sender(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """The default sender (cli_send in-process, with its ledger check), with only the network send faked."""
    from scout.scripts import notify_telegram

    sent: list[dict[str, Any]] = []

    def fake_send(**kw: Any) -> _Event:
        sent.append(kw)
        return _Event()

    monkeypatch.setattr(notify_telegram, "send", fake_send)
    return sent


def test_a_reply_to_a_message_not_yet_received_is_refused_for_good(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run can't queue a reply to a message id it predicts and have it "answer" that message once it
    arrives: the first drain fails the entry instead of keeping it as transient."""
    sent = _real_sender(monkeypatch)
    _inbound(vault, 5)
    broker.enqueue(vault, "telegram", _tg("pre-answer", reply_to=9, covers=[5]), now=NOW)
    first = broker.drain(vault, broker=broker.Broker(vault, _env(), clock=lambda: NOW))
    assert (first.failed, first.kept, first.done) == (1, 0, 0)
    _inbound(vault, 9)  # the predicted message arrives
    later = broker.drain(vault, broker=broker.Broker(vault, _env(), clock=lambda: NOW + timedelta(minutes=5)))
    assert (later.failed, later.kept, later.done) == (0, 0, 0)
    assert sent == []
    from scout.telegram_inbound.ledger import Ledger

    assert Ledger(vault).unanswered_ids() == {5, 9}


def test_a_reply_drained_twice_is_already_sent_not_refused(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A drain stopped between the send and the unlink leaves the entry: the next drain finds its request id in
    the send log (done), before the ledger check that would now refuse it as already answered."""
    sent = _real_sender(monkeypatch)
    _inbound(vault, 5)
    req = _tg("answer", reply_to=5, rid="01REPLYRID")
    broker.enqueue(vault, "telegram", req, now=NOW)
    assert broker.drain(vault, broker=broker.Broker(vault, _env(), clock=lambda: NOW)).done == 1
    broker.enqueue(vault, "telegram", req, now=NOW)
    again = broker.drain(vault, broker=broker.Broker(vault, _env(), clock=lambda: NOW))
    assert (again.done, again.failed) == (1, 0)
    assert len(sent) == 1


def test_a_live_reply_to_an_unknown_message_is_a_refusal(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _real_sender(monkeypatch)
    _inbound(vault, 5)
    live = broker.Broker(vault, _env(), run_id="scout-broker.reply", clock=lambda: NOW)
    reply = live.handle(_tg("x", reply_to=9))
    assert reply.exit == 7 and reply.kind == broker.REFUSED
    assert "unknown or already answered message ids: 9" in reply.stderr
    assert sent == []
    assert live.handle(_tg("y", reply_to=5)).exit == 0 and len(sent) == 1


def test_the_per_run_limit_holds(vault: Path) -> None:
    sender = Sender()
    b = broker.Broker(
        vault, _env(), limits=broker.Limits(max_per_run=2), sender=sender, clock=lambda: NOW, run_id="scout-broker.p"
    )
    codes = [b.handle(_tg(f"m{i}")).exit for i in range(3)]
    assert codes == [0, 0, broker.EXIT_LIMIT] and len(sender.calls) == 2


def test_the_hourly_limit_holds_across_runs(vault: Path) -> None:
    sender = Sender()
    limits = broker.Limits(max_per_run=10, max_per_hour=3)
    first = broker.Broker(vault, _env(), limits=limits, sender=sender, clock=lambda: NOW, run_id="scout-broker.a")
    assert [first.handle(_tg(f"a{i}")).exit for i in range(2)] == [0, 0]
    second = broker.Broker(
        vault, _env(), limits=limits, sender=sender, clock=lambda: NOW + timedelta(minutes=5), run_id="scout-broker.b"
    )
    assert second.handle(_tg("b0")).exit == 0
    full = second.handle(_tg("b1"))
    assert full.exit == broker.EXIT_LIMIT and full.kind == broker.HOUR_LIMIT
    later = broker.Broker(
        vault, _env(), limits=limits, sender=sender, clock=lambda: NOW + timedelta(minutes=61), run_id="scout-broker.c"
    )
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
    assert hook_paths.protected_write(target, None, env) == hook_paths.OWNER_STATE


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


# -- the queue stays inside the vault (review of #648) ------------------------------------------------------------


@pytest.mark.parametrize("folder", ["commit", "telegram", "failed", "broker-queue"])
def test_the_drain_touches_nothing_outside_the_vaults_queue(vault: Path, tmp_path: Path, folder: str) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.json").write_text('{"message": "x", "paths": ["notes/a.md"]}')
    before = sorted(p.name for p in outside.iterdir())
    queue = vault / broker.QUEUE_DIR
    queue.mkdir(parents=True)
    broker.enqueue(vault, "commit", _commit(["notes/a.md"], message="real"), now=NOW)
    if folder == "broker-queue":
        shutil_move = queue.rename(vault / ".scout-state" / "moved")
        del shutil_move
        queue.symlink_to(outside)
    else:
        target = queue / folder
        if target.exists():
            for p in target.iterdir():
                p.unlink()
            target.rmdir()
        target.symlink_to(outside)
    rec = Recorder(vault, {})
    result = broker.drain(vault, broker=rec)
    assert result.unsafe and rec.seen == []
    assert sorted(p.name for p in outside.iterdir()) == before
    assert (outside / "keep.json").read_text().startswith('{"message"')
    assert broker.queue_problem(vault) is not None
    assert broker.pending(vault) == 0


def test_a_queue_entry_that_isnt_a_regular_file_never_stalls_the_drain(vault: Path) -> None:
    import threading

    broker.enqueue(vault, "commit", _commit(["notes/a.md"], message="after"), now=NOW)
    folder = broker.queue_dir(vault, "commit")
    os.mkfifo(folder / "00000000000000000000000000.json")
    (folder / "00000000000000000000000001.json").symlink_to(folder / "elsewhere.json")
    rec = Recorder(vault, {})
    results: list[broker.DrainResult] = []
    t = threading.Thread(target=lambda: results.append(broker.drain(vault, broker=rec)), daemon=True)
    t.start()
    t.join(15)
    assert not t.is_alive() and results
    assert results[0].failed == 2 and results[0].done == 1
    assert rec.seen == [("commit", "after")]


def test_the_drain_stops_starting_entries_at_its_deadline(vault: Path) -> None:
    for i in range(3):
        broker.enqueue(vault, "commit", _commit(["notes/a.md"], message=f"c{i}"), now=NOW)
    result = broker.drain(vault, broker=Recorder(vault, {}), deadline_s=0)
    assert result.done == 0 and result.kept == 3 and broker.pending(vault) == 3


def test_the_locks_are_write_only_to_the_owner(vault: Path) -> None:
    broker.enqueue(vault, "commit", _commit(["notes/a.md"]), now=NOW)
    broker.drain(vault, broker=Recorder(vault, {}))
    broker.Broker(vault, _env(), sender=Sender()).handle(_tg("x"))
    for name in (broker.DRAIN_LOCK_NAME, broker.TELEGRAM_LOCK_NAME):
        path = broker.owner_dir(os.environ) / name
        assert stat.S_IMODE(path.stat().st_mode) == 0o200
        with pytest.raises(PermissionError):
            path.open("rb")


# -- requests that would crash a parser ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        b"[" * 200_000,
        b'{"a":' * 100_000,
        b"1" * 100_000,
        b"\xff\xfe not utf-8",
        b'{"v": 1, "verb": "commit", "message": NaN}',
        b"",
    ],
)
def test_a_malformed_request_is_refused_and_the_broker_keeps_answering(vault: Path, payload: bytes) -> None:
    import socket
    import threading

    b = broker.Broker(vault, _env())
    for data, want in ((payload + b"\n", broker.EXIT_REFUSED), (b'{"v": 1, "verb": "ping"}\n', 0)):
        server, client = socket.socketpair()
        with server, client:
            answering = threading.Thread(target=broker._answer, args=(server, b), daemon=True)
            answering.start()  # it reads while the request is written: a socketpair's buffer is small
            client.sendall(data)
            client.shutdown(socket.SHUT_WR)
            answering.join(30)
            assert not answering.is_alive()
            reply = json.loads(client.recv(1 << 20))
        assert reply["exit"] == want, payload[:20]


# -- queued Telegram messages keep the run's limits and are sent once ---------------------------------------------


def test_a_queued_message_that_breaks_the_rules_fails_at_once(vault: Path) -> None:
    broker.enqueue(vault, "telegram", _tg(tier="loud"), now=NOW)
    result = broker.drain(vault, broker=broker.Broker(vault, _env(), sender=Sender(), clock=lambda: NOW))
    assert result.failed == 1 and broker.pending(vault) == 0


def test_a_full_hour_keeps_queued_messages_without_using_their_tries(vault: Path) -> None:
    limits = broker.Limits(max_per_run=4, max_per_hour=1)
    busy = broker.Broker(vault, _env(), limits=limits, sender=Sender(), clock=lambda: NOW, run_id="scout-broker.x")
    assert busy.handle(_tg("first")).exit == 0
    broker.register_run(os.environ, "scout-broker.y", now=NOW)  # its broker started, then died
    broker.enqueue(vault, "telegram", {**_tg("later"), "run": "scout-broker.y"}, now=NOW)
    sender = Sender()
    result = broker.drain(vault, broker=broker.Broker(vault, _env(), limits=limits, sender=sender, clock=lambda: NOW))
    assert result.kept == 1 and sender.calls == []
    (entry,) = broker._queued(vault, "telegram")
    assert entry["attempts"] == 0
    an_hour_on = broker.Broker(vault, _env(), limits=limits, sender=sender, clock=lambda: NOW + timedelta(minutes=61))
    assert broker.drain(vault, broker=an_hour_on).done == 1 and len(sender.calls) == 1


def test_a_run_sends_at_most_its_allowance_live_and_queued_together(
    vault: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = "scout-broker.r1"
    broker.register_run(os.environ, run)  # what `broker serve` does as it starts
    live = broker.Broker(vault, _env(), sender=Sender(), run_id=run)
    assert [live.handle(_tg(f"live{i}")).exit for i in range(3)] == [0, 0, 0]
    gone = str(tmp_path / run / broker.SOCKET_NAME)  # the broker died: the client queues
    env = {**os.environ, "SCOUT_DATA_DIR": str(vault), broker.ENV_SOCKET: gone}
    fields = {"tier": "info", "parse_mode": "none", "allow_duplicate": False, "dry_run": False}
    assert broker.client_telegram({**fields, "body": "queued0"}, env=env) == 0
    assert broker.client_telegram({**fields, "body": "queued1"}, env=env) == broker.EXIT_LIMIT
    assert "sent or queued its 4" in capsys.readouterr().err
    sender = Sender()
    result = broker.drain(vault, broker=broker.Broker(vault, _env(), sender=sender))
    assert result.done == 1 and [c["body"] for c in sender.calls] == ["queued0"]


def test_queued_messages_get_no_allowance_from_a_run_id_the_owner_side_never_recorded(vault: Path) -> None:
    for i in range(8):
        broker.enqueue(vault, "telegram", {**_tg(f"forged{i}"), "run": f"scout-broker.forged{i}"}, now=NOW)
    sender = Sender()
    result = broker.drain(vault, broker=broker.Broker(vault, _env(), sender=sender, clock=lambda: NOW))
    assert len(sender.calls) == broker.UNREGISTERED_PER_HOUR  # one shared slot an hour, whatever the ids say
    assert result.done == 1 and result.failed == 7 and broker.pending(vault) == 0


def test_a_closed_run_keeps_its_allowance_only_for_what_it_left_queued(vault: Path) -> None:
    run = "scout-broker.c1"
    broker.register_run(os.environ, run, now=NOW)
    broker.enqueue(vault, "telegram", {**_tg("left over"), "run": run, "rid": "01LEFTOVER"}, now=NOW)
    broker.drain(vault, broker=Recorder(vault, {"left over": 2}), run=run)  # the wrapper's drain: Telegram is down
    broker.enqueue(vault, "telegram", {**_tg("written after"), "run": run, "rid": "01AFTER"}, now=NOW)
    broker.enqueue(vault, "telegram", {**_tg("also after"), "run": run, "rid": "01AFTER2"}, now=NOW)
    sender = Sender()
    result = broker.drain(vault, broker=broker.Broker(vault, _env(), sender=sender, clock=lambda: NOW))
    bodies = sorted(c["body"] for c in sender.calls)
    assert "left over" in bodies  # credited to its run
    assert len(bodies) == 2 and result.failed == 1  # the two written after share one unvouched slot


def test_a_run_whose_broker_is_gone_is_closed_and_keeps_only_what_it_queued(vault: Path, tmp_path: Path) -> None:
    import subprocess as sp

    dead = sp.Popen(["/usr/bin/true"])
    dead.wait()  # a broker whose wrapper was killed: its process is gone
    gone, alive = "scout-broker.gone", "scout-broker.alive"
    broker.register_run(os.environ, gone, pid=dead.pid, folder=tmp_path / gone)
    (tmp_path / alive).mkdir()
    broker.register_run(os.environ, alive, pid=os.getpid(), folder=tmp_path / alive)
    broker.enqueue(vault, "telegram", {**_tg("queued before"), "run": gone, "rid": "01BEFORE"}, now=NOW)
    assert broker.close_stale_runs(os.environ, vault) == [gone]
    broker.enqueue(vault, "telegram", {**_tg("borrowed a"), "run": gone, "rid": "01BORROW1"}, now=NOW)
    broker.enqueue(vault, "telegram", {**_tg("borrowed b"), "run": gone, "rid": "01BORROW2"}, now=NOW)
    sender = Sender()
    result = broker.drain(vault, broker=broker.Broker(vault, _env(), sender=sender, clock=lambda: NOW))
    assert "queued before" in [c["body"] for c in sender.calls]
    assert len(sender.calls) == 2 and result.failed == 1  # the later two share one unvouched slot
    assert broker._read_record(os.environ, alive)["open"] is True  # a live run stays open


def test_a_message_the_broker_already_sent_is_not_sent_again_from_the_queue(vault: Path) -> None:
    sender = Sender()
    live = broker.Broker(vault, _env(), sender=sender, run_id="scout-broker.r2")
    req = {**_tg("once"), "rid": "01REQUEST", "run": "scout-broker.r2"}
    assert live.handle(req).exit == 0  # sent, then the reply was lost: the client queued it too
    broker.enqueue(vault, "telegram", req, now=datetime.now(UTC))
    result = broker.drain(vault, broker=broker.Broker(vault, _env(), sender=sender))
    assert result.done == 1 and len(sender.calls) == 1


# -- leftovers ------------------------------------------------------------------------------------------------------


def test_old_broker_folders_are_swept_and_nothing_else(tmp_path: Path) -> None:
    old, recent, keep = tmp_path / "scout-broker.old", tmp_path / "scout-broker.new", tmp_path / "scout-broker.mine"
    for d in (old, recent, keep):
        d.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    (other / "x").write_text("x")
    (old / "inside").symlink_to(other)
    (tmp_path / "scout-broker.link").symlink_to(other)
    day_ago = datetime.now(UTC).timestamp() - 2 * 86_400
    for d in (old, keep):
        os.utime(d, (day_ago, day_ago))
    assert broker.sweep_stale(tmp_path, keep=keep.name) == 1
    assert not old.exists() and recent.exists() and keep.exists()
    assert (other / "x").exists() and (tmp_path / "scout-broker.link").is_symlink()


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


def test_a_run_reads_the_telegram_inbound_ledger_but_never_writes_it(vault: Path, tmp_path: Path) -> None:
    """`scoutctl telegram pending` runs inside the run's sandbox: the ledger is write-protected, not read-protected."""
    env = _env()
    rules = sbx.build_rules(vault, env) + sbx.broker_rules(vault, tmp_path / "broker", env)
    inbound = vault / ".scout-state" / "telegram-inbound"
    for name in ("messages.json", "replies.jsonl", "offset.json", ".lock"):
        assert sbx.decide(rules, str(inbound / name), "read") == "allow", name
        assert sbx.decide(rules, str(inbound / name)) == "deny", name


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
        ("u8", "Bash", {"command": "echo x > notes/e.md\ngit add notes/e.md\nscripts/git-safe-commit.sh m notes/f.md"}),
        ("u9", "Bash", {"command": "git rm -q --cached notes/g.md"}),
        ("u10", "Bash", {"command": "git restore --source=HEAD --staged --worktree notes/h.md"}),
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
    assert b["helper_calls_kept"] == 2 and b["helper_commits_with_sha"] == 1
    assert b["sessions_reusing_a_sha_mid_session"] == 1 and b["telegram_calls_kept"] == 1
    kinds = {k: v["calls"] for k, v in b["vault_git_writes"].items()}
    assert kinds == {
        "add": 2,  # one on a later line of a multi-line command
        "commit": 1,
        "checkout --": 1,
        "restore --staged": 1,
        "restore --worktree": 1,  # --staged --worktree puts the file back: git show
        "rm --cached": 1,
        "config (write)": 1,
    }
    assert "gitignore" in b["vault_git_writes"]["rm --cached"]["instead"]
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
    telegram = broker.queue_dir(vault, "telegram")
    if telegram.is_dir():
        telegram.rmdir()  # the drain made it, empty
    telegram.symlink_to(vault / "notes")
    unsafe = [f for f in health.broker_findings([row], vault=vault) if f.id == "broker.queue_unsafe"]
    assert unsafe and unsafe[0].severity == "red"
