"""The progress watcher's rule and line (``scout.run_progress``; review of #701).

A hung ``claude -p`` behind a live runner shell must not look like progress: the
lease keepalive and the run brokers live as long as the runner, so the watcher
leaves them (and itself) out of the session it samples. These tests drive
``watch`` with a fake process table and fake clocks; the runner, reaper and
health paths are ``tests/integration/test_run_progress_runner.py``.

``tests/fixtures/contract/run-progress.alive`` is the canonical line the macOS app
parses (``apps/macos/ScoutTests/Fixtures/run-progress.alive``, byte-compared by
``contract.yml``). ``SCOUT_REGEN_CONTRACT=1`` rewrites it from :data:`CONTRACT`;
then ``cp`` it over the app copy and update the pins in
``apps/macos/ScoutTests/Services/RunProgressTests.swift``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout import cli, health
from scout import run_progress as rp

REGEN = os.environ.get("SCOUT_REGEN_CONTRACT") == "1"
CANONICAL = Path(__file__).resolve().parents[1] / "fixtures" / "contract" / "run-progress.alive"
# A path with a space: the log is the line's last field and may hold any.
CONTRACT = rp.Heartbeat(
    runner_pid=4242,
    progress_at=1_763_550_000,
    stalled_s=1500,
    fresh_for_s=210,
    log="/Users/alex/My Vault/.scout-logs/scout-2026-11-19_14-00.log",
)

RUNNER = 100


def test_the_line_matches_the_shared_fixture() -> None:
    text = CONTRACT.render()
    if REGEN:
        CANONICAL.write_text(text, encoding="utf-8")
    assert CANONICAL.read_text(encoding="utf-8") == text, (
        "regenerate with SCOUT_REGEN_CONTRACT=1, then cp it to apps/macos/ScoutTests/Fixtures/run-progress.alive"
    )
    assert rp.parse(text) == CONTRACT


@pytest.mark.parametrize(
    "text",
    [
        "",
        "2 4242 1 0 210 /log\n",  # another version
        "1 4242 1 0 210\n",  # no log
        "1 0 1 0 210 /log\n",  # no runner
        "1 4242 1 0 0 /log\n",  # never fresh
        "1 4242 x 0 210 /log\n",
        "1 4242 1 -5 210 /log\n",
        "1 4242 1 0 210 /log\nsecond line\n",
    ],
)
def test_anything_but_the_line_is_unreadable(text: str) -> None:
    assert rp.parse(text) is None


def test_a_line_is_trusted_only_while_its_watcher_samples(tmp_path: Path) -> None:
    path = tmp_path / ".scout-session.lock.alive"
    rp.write(path, CONTRACT)
    written = path.stat().st_mtime
    assert rp.read_fresh(path, now=written + CONTRACT.fresh_for_s) == CONTRACT
    assert rp.read_fresh(path, now=written + CONTRACT.fresh_for_s + 1) is None, "a dead watcher's line is no answer"
    assert rp.read_fresh(tmp_path / "missing") is None
    assert [p.name for p in tmp_path.iterdir()] == [path.name], "the write leaves no tmp file"


def test_fresh_for_spans_three_samples_and_slack() -> None:
    assert rp.fresh_for(60) == 210
    assert rp.fresh_for(1) == 33


# --- the session -----------------------------------------------------------------

PS = """\
  100     1   100 Ss    0:01.00 /bin/bash /v/run-scout.sh
  101   100   100 S     0:05.00 /usr/bin/python3 /e/scoutctl repo lease keepalive --run-id x --parent-pid 100
  102   100   100 S     0:00.50 /usr/bin/python3 /e/scoutctl progress watch --vault /v --runner-pid 100 --log /v/l
  103   102   100 R     0:00.01 ps -A -ww -o pid=
  104   100   100 S     0:00.10 /bin/bash /v/scripts/claude-with-retry.sh /v/l claude -p x
  105   104   100 S     0:20.00 /usr/bin/python3 /e/scoutctl broker serve --vault /v --dir /d --parent 104
  106   104   100 S     1:02.30 claude --name scout-x -p go
  107   106   100 S     0:00.20 git status
  108   106   100 Z     0:00.00 (sh)
  200     1   200 Ss    9:00.00 claude --name someone-else
"""


def test_the_session_leaves_out_scouts_helpers_and_what_they_start() -> None:
    cpu, pids = rp.session_sample(rp.parse_ps(PS), RUNNER, self_pid=102)
    assert pids == {100, 104, 106, 107}, "keepalive, broker, the watcher and its ps are not the agent; zombies neither"
    assert cpu == 100 + 10 + 6230 + 20


def test_the_session_of_a_runner_that_leads_no_group_is_its_tree() -> None:
    leader = rp.Proc(RUNNER, 1, 99, "S", 100, "/bin/bash run-scout.sh")  # in group 99, which it doesn't lead
    procs = [leader if p.pid == RUNNER else p for p in rp.parse_ps(PS)]
    procs.append(rp.Proc(300, 1, 99, "S", 5000, "zsh"))  # the operator's shell, in the runner's group
    _, pids = rp.session_sample(procs, RUNNER, self_pid=102)
    assert 300 not in pids and pids == {100, 104, 106, 107}


@pytest.mark.parametrize(
    ("text", "cs"), [("0:01.00", 100), ("1:02.30", 6230), ("01:00:00", 360000), ("2-00:00:01", 17280100)]
)
def test_cpu_time_reads_both_ps_formats(text: str, cs: int) -> None:
    assert rp.cpu_centiseconds(text) == cs


# --- the watcher ----------------------------------------------------------------


class Host:
    """A process table, an awake clock and a wall clock that move only when the watcher sleeps."""

    def __init__(self, vault: Path, tables: list[str], *, runner_lives: int) -> None:
        self.vault = vault
        self.tables = tables
        self.samples = 0
        self.awake = 1000
        self.wall = 1_763_550_000.0
        self.runner_lives = runner_lives  # samples before the runner exits
        self.lines: list[rp.Heartbeat] = []
        lock = vault / ".scout-logs" / ".scout-session.lock"
        lock.parent.mkdir(parents=True)
        lock.write_text(f"{RUNNER}\n", encoding="utf-8")

    def ps(self) -> str:
        table = self.tables[min(self.samples, len(self.tables) - 1)]
        self.samples += 1
        return table

    def sleep(self, secs: float) -> None:
        beat = rp.parse(rp.heartbeat_path(self.vault).read_text(encoding="utf-8"))
        assert beat is not None
        self.lines.append(beat)
        self.awake += int(secs)
        self.wall += secs

    def run(self, **kw: object) -> str:
        return rp.watch(
            self.vault,
            runner_pid=RUNNER,
            log="/v/scout.log",
            sample_s=60,
            cpu_threshold_cs=1000,
            self_pid=102,
            ps=self.ps,
            alive=lambda _pid: self.samples <= self.runner_lives,
            awake_now=lambda: ("awake", self.awake),
            wall_now=lambda: self.wall,
            sleep=self.sleep,
            **kw,  # type: ignore[arg-type]
        )


def _table(claude_cpu: str, *, keepalive_cpu: str = "0:05.00", broker_cpu: str = "0:20.00", extra: str = "") -> str:
    return (
        PS.replace("1:02.30 claude", f"{claude_cpu} claude")
        .replace("0:05.00 /usr/bin/python3 /e/scoutctl repo", f"{keepalive_cpu} /usr/bin/python3 /e/scoutctl repo")
        .replace("0:20.00 /usr/bin/python3 /e/scoutctl broker", f"{broker_cpu} /usr/bin/python3 /e/scoutctl broker")
        + extra
    )


def test_a_hung_claude_behind_a_live_shell_stalls_however_busy_the_helpers_are(tmp_path: Path) -> None:
    """The finding from #701's review: the keepalive renews (and logs) and the broker polls, claude does nothing."""
    tables = [_table("1:02.30", keepalive_cpu=f"0:{5 + i:02d}.00", broker_cpu=f"{i}:20.00") for i in range(8)]
    host = Host(tmp_path, tables, runner_lives=6)
    assert host.run() == "gone"
    assert [b.stalled_s for b in host.lines] == [0, 60, 120, 180, 240, 300, 360]
    assert {b.progress_at for b in host.lines} == {1_763_550_000}
    assert not rp.heartbeat_path(tmp_path).exists(), "the runner is gone, so is its line"


def test_a_slow_but_alive_run_keeps_resetting_its_stall(tmp_path: Path) -> None:
    """A model thinking for minutes between tool calls: little CPU, but each tool call changes the process list."""
    tool = "  109   106   100 S     0:00.01 /bin/zsh -c rg TODO\n"
    tables = [_table("1:02.30"), _table("1:02.40"), _table("1:02.50", extra=tool), _table("1:02.60"), _table("1:02.70")]
    host = Host(tmp_path, tables, runner_lives=4)
    host.run()
    stalls = [b.stalled_s for b in host.lines]
    assert stalls == [0, 60, 0, 0, 60], "the tool call started, then ended: two changes of the process list"


def test_cpu_alone_counts_as_progress(tmp_path: Path) -> None:
    tables = [_table("1:02.30"), _table("1:08.30"), _table("1:12.40"), _table("1:12.40")]
    host = Host(tmp_path, tables, runner_lives=3)
    host.run()
    assert [b.stalled_s for b in host.lines] == [0, 60, 0, 60], "6 s is under the 10 s threshold; 10.1 s since is over"


def test_sleep_is_not_stall(tmp_path: Path) -> None:
    host = Host(tmp_path, [_table("1:02.30")], runner_lives=3)

    def sleep_through_the_night(secs: float) -> None:
        Host.sleep(host, secs)
        host.wall += 8 * 3600  # the wall clock runs on while the host sleeps; the awake clock doesn't

    host.sleep = sleep_through_the_night  # type: ignore[method-assign]
    host.run()
    assert [b.stalled_s for b in host.lines] == [0, 60, 120, 180]


def test_a_watcher_whose_runner_lost_the_lock_leaves_the_new_holders_line(tmp_path: Path) -> None:
    host = Host(tmp_path, [_table("1:02.30")], runner_lives=99)
    lock = tmp_path / ".scout-logs" / ".scout-session.lock"
    path = rp.heartbeat_path(tmp_path)

    def reaped_and_replaced(secs: float) -> None:
        Host.sleep(host, secs)
        lock.write_text("555\n", encoding="utf-8")
        rp.write(path, rp.Heartbeat(555, 1, 0, 210, "/v/new.log"))

    host.sleep = reaped_and_replaced  # type: ignore[method-assign]
    assert host.run() == "not_holder"
    beat = rp.parse(path.read_text(encoding="utf-8"))
    assert beat is not None and beat.runner_pid == 555


def test_sigterm_ends_the_watcher_and_its_line(tmp_path: Path) -> None:
    host = Host(tmp_path, [_table("1:02.30")], runner_lives=99)

    def terminated(_secs: float) -> None:
        raise SystemExit(0)  # what the CLI's SIGTERM handler raises mid-sleep

    host.sleep = terminated  # type: ignore[method-assign]
    with pytest.raises(SystemExit):
        host.run()
    assert not rp.heartbeat_path(tmp_path).exists()


def test_the_real_ps_table_parses() -> None:
    procs = rp.parse_ps(rp.run_ps())
    me = [p for p in procs if p.pid == os.getpid()]
    assert me and me[0].cpu_cs >= 0 and "python" in me[0].command.lower()


def test_pid_alive() -> None:
    assert rp._pid_alive(os.getpid())
    assert not rp._pid_alive(0)
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    assert not rp._pid_alive(gone.pid)


def test_env_secs_takes_only_positive_whole_numbers() -> None:
    assert rp.env_secs("X", 60, {"X": "5"}) == 5
    assert [rp.env_secs("X", 60, {"X": v}) for v in ("", "0", "-1", "1.5", "five")] == [60] * 5


def test_the_cli_watches_with_the_reapers_knobs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: dict[str, object] = {}
    monkeypatch.setattr(rp, "watch", lambda vault, **kw: seen.update(vault=vault, **kw) or "gone")
    monkeypatch.setenv("SCOUT_PROGRESS_SAMPLE_SECS", "7")
    monkeypatch.setenv("SCOUT_LOCK_PROGRESS_CPU_SECS", "3")
    result = CliRunner().invoke(
        cli.app, ["progress", "watch", "--vault", str(tmp_path), "--runner-pid", "42", "--log", "/v/x.log"]
    )
    assert result.exit_code == 0, result.output
    assert seen == {"vault": tmp_path, "runner_pid": 42, "log": "/v/x.log", "sample_s": 7, "cpu_threshold_cs": 300}


# --- run health reads the same line on the same ceiling ---------------------------


def _holding(vault: Path, pid: int, stalled_s: int) -> None:
    logs = vault / ".scout-logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / ".scout-session.lock").write_text(f"{pid}\n", encoding="utf-8")
    rp.write(rp.heartbeat_path(vault), rp.Heartbeat(pid, 1_763_550_000, stalled_s, 210, "/v/scout.log"))


def test_health_reports_a_holder_past_the_reapers_ceiling(tmp_path: Path) -> None:
    _holding(tmp_path, os.getpid(), 7300)
    (finding,) = health.progress_findings(tmp_path, now=datetime.now(UTC), env={})
    assert (finding.id, finding.severity, finding.log) == ("session.no_progress", "yellow", "/v/scout.log")
    assert finding.since == datetime.fromtimestamp(1_763_550_000, UTC)
    assert "121 min" in finding.summary
    assert health.progress_findings(tmp_path, now=datetime.now(UTC), env={"SCOUT_LOCK_MAX_AGE_SECS": "7300"}) == []


@pytest.mark.parametrize("case", ["no_line", "stale", "other_holder", "dead_holder", "unreadable_lock"])
def test_health_says_nothing_without_a_fresh_line_for_the_live_holder(tmp_path: Path, case: str) -> None:
    _holding(tmp_path, os.getpid(), 9000)
    now = datetime.now(UTC)
    lock = tmp_path / ".scout-logs" / ".scout-session.lock"
    if case == "no_line":
        rp.heartbeat_path(tmp_path).unlink()
    elif case == "stale":
        now = datetime.fromtimestamp(now.timestamp() + 211, UTC)
    elif case == "other_holder":
        rp.write(rp.heartbeat_path(tmp_path), rp.Heartbeat(os.getppid(), 1, 9000, 210, "/v/scout.log"))
    elif case == "dead_holder":
        gone = subprocess.Popen([sys.executable, "-c", "pass"])
        gone.wait()
        _holding(tmp_path, gone.pid, 9000)
    else:
        lock.write_bytes(b"\xff\xfe")
    assert health.progress_findings(tmp_path, now=now, env={}) == []
