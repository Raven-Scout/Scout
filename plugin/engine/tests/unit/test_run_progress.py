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
import stat
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
        path = rp.heartbeat_path(self.vault)
        beat = rp.parse(path.read_text(encoding="utf-8")) if path.exists() else None
        self.lines.append(beat)  # type: ignore[arg-type]  # None: the watcher withdrew its line
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


def _ps_time(cs: int) -> str:
    return f"{cs // 6000}:{cs % 6000 // 100:02d}.{cs % 100:02d}"


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
    # 6 s is under the 10 s threshold; 10.1 s within the window is over, and stays over while it is in the window.
    assert [b.stalled_s for b in host.lines] == [0, 60, 0, 0]


def test_a_trickle_of_cpu_never_adds_up_to_progress(tmp_path: Path) -> None:
    """The review of #730: a hung ``claude -p`` still burns a little CPU. Measured on a ``claude -p`` whose API
    accepted the connection and never answered: 0.17 s in 220 s after start-up (~0.08%), so ~0.5 s per
    10-minute window. Summed since the last progress, as the first version did, any trickle reaches the 10 s
    threshold eventually and resets the stall; within a sliding window it never does."""
    trickle = [_table(_ps_time(6230 + 50 * i)) for i in range(40)]  # +0.5 s of CPU a sample
    host = Host(tmp_path, trickle, runner_lives=30)
    host.run()
    assert [b.stalled_s for b in host.lines] == [60 * i for i in range(31)], "0.5 s a sample is 5 s a window: never 10"
    assert host.lines[-1].stalled_s == 1800


def test_a_failed_sample_is_neither_progress_nor_stall(tmp_path: Path) -> None:
    """``ps`` timing out, failing, or returning a table without the runner says nothing either way."""
    outcomes: list[object] = [
        _table("1:02.30"),
        subprocess.TimeoutExpired("ps", 30),
        OSError("fork failed"),
        "",
        _table("1:02.30").replace("  100     1   100 Ss", "  999     1   999 Ss"),  # no runner row
        _table("1:02.30"),
    ]
    host = Host(tmp_path, [], runner_lives=5)

    def ps() -> str:
        out = outcomes[min(host.samples, len(outcomes) - 1)]
        host.samples += 1
        if isinstance(out, BaseException):
            raise out
        return str(out)

    host.ps = ps  # type: ignore[method-assign]
    host.run()
    stalls = [b.stalled_s if b else None for b in host.lines]
    # Three failures in a row and the line is withdrawn (a fresh line must not vouch for an unmeasured run);
    # it comes back with the next good sample, and only that last gap had two good samples to measure.
    assert stalls == [0, 0, 0, None, None, 60]


def test_a_long_ps_outage_leaves_no_line_to_trust(tmp_path: Path) -> None:
    host = Host(tmp_path, [_table("1:02.30")], runner_lives=8)
    good = host.ps

    def ps() -> str:
        if host.samples >= 1:
            host.samples += 1
            raise subprocess.TimeoutExpired("ps", 30)
        return good()

    host.ps = ps  # type: ignore[method-assign]
    host.run()
    assert [b is not None for b in host.lines] == [True, True, True] + [False] * 6


def test_a_clock_that_went_back_adds_no_stall(tmp_path: Path) -> None:
    host = Host(tmp_path, [_table("1:02.30")], runner_lives=3)

    def rebooted(secs: float) -> None:
        Host.sleep(host, secs)
        if len(host.lines) == 2:
            host.awake = 5  # a reboot: the awake clock starts again near zero

    host.sleep = rebooted  # type: ignore[method-assign]
    host.run()
    assert [b.stalled_s for b in host.lines] == [0, 60, 60, 120]


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


# --- which processes are Scout's helpers ------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "/usr/bin/python3 /e/scoutctl repo lease keepalive --run-id x --parent-pid 1",
        "/Users/alex/Library/Application Support/Scout/engine/.venv/bin/python -P -m scout.cli broker serve --dir /d",
        "/opt/py/bin/python3 -P -m scout progress watch --vault /v --runner-pid 1 --log /v/l",
        "/bin/bash /Users/alex/My Vault/bin/scoutctl repo lease keepalive --run-id x",
        "scoutctl broker serve",
    ],
)
def test_the_engines_helpers_are_helpers(command: str) -> None:
    assert rp.is_helper(command)


@pytest.mark.parametrize(
    "command",
    [
        # claude's and the retry wrapper's argv carry the prompt: a prompt naming a helper is not one.
        "claude --permission-mode auto --model sonnet -p Check whether scoutctl broker serve is up",
        "/bin/bash /v/scripts/claude-with-retry.sh /v/l /e/scoutctl agent-runtime exec -p run progress watch now",
        "caffeinate -i sandbox-exec -p (version 1) /e/scoutctl agent-runtime exec -p broker serve",
        "/usr/bin/python3 /e/notscoutctl repo lease keepalive",
        "/usr/bin/python3 /e/scoutctl repo lease release --run-id x",
        "/usr/bin/python3 /e/scoutctl progress watchful",
    ],
)
def test_a_command_that_only_mentions_a_helper_is_not_one(command: str) -> None:
    assert not rp.is_helper(command)


def test_a_prompt_naming_a_helper_does_not_hide_claudes_tree() -> None:
    table = PS.replace("claude --name scout-x -p go", "claude --name scout-x -p restart the broker serve loop")
    _, pids = rp.session_sample(rp.parse_ps(table), RUNNER, self_pid=102)
    assert {106, 107} <= pids


# --- reading and writing the line safely --------------------------------------


def test_freshness_is_capped_and_never_in_the_future(tmp_path: Path) -> None:
    """A run can write .scout-logs: a forged ``fresh for`` or a future mtime must not keep a line fresh."""
    path = tmp_path / ".scout-session.lock.alive"
    rp.write(path, rp.Heartbeat(4242, 1, 0, 999_999_999_999, "/v/l"))
    written = path.stat().st_mtime
    assert rp.MAX_FRESH_SECS == 630
    assert rp.read_fresh(path, now=written + 630) is not None
    assert rp.read_fresh(path, now=written + 631) is None
    assert rp.read_fresh(path, now=written - 30) is not None
    assert rp.read_fresh(path, now=written - 31) is None, "an mtime in the future is forged or skewed"


def test_a_link_or_fifo_at_the_line_is_never_followed_or_waited_on(tmp_path: Path) -> None:
    path = tmp_path / ".scout-session.lock.alive"
    target = tmp_path / "elsewhere"
    target.write_text(CONTRACT.render(), encoding="utf-8")
    path.symlink_to(target)
    assert rp.read_fresh(path, now=target.stat().st_mtime) is None
    path.unlink()
    os.mkfifo(path)
    assert rp.read_fresh(path) is None  # returns at once: never opened for a blocking read
    rp.remove_if_ours(path, 4242)
    assert path.exists(), "a FIFO is no line of ours"


def test_a_file_too_big_or_not_utf8_is_unreadable(tmp_path: Path) -> None:
    path = tmp_path / ".scout-session.lock.alive"
    path.write_bytes(b"\xff\xfe" + b"x" * 10)
    assert rp.read_fresh(path) is None
    path.write_text(CONTRACT.render()[:-1] + "x" * rp.MAX_LINE_BYTES + "\n", encoding="utf-8")
    assert rp.read_fresh(path) is None, "a line longer than MAX_LINE_BYTES is refused, not torn"


def test_a_link_planted_where_the_watcher_writes_is_never_written_through(tmp_path: Path) -> None:
    """The first version wrote ``<line>.<pid>``, a name a run could guess from ``ps`` and plant as a link to a
    runner script or a secret. ``safe_write`` temp names are random and opened O_EXCL | O_NOFOLLOW, and the
    rename replaces a link at the line itself rather than writing through it."""
    path = tmp_path / ".scout-session.lock.alive"
    secret = tmp_path / "secret"
    secret.write_text("keep me\n", encoding="utf-8")
    (tmp_path / f"{path.name}.{os.getpid()}").symlink_to(secret)
    path.symlink_to(secret)
    rp.write(path, CONTRACT)
    assert secret.read_text(encoding="utf-8") == "keep me\n"
    assert not path.is_symlink() and rp.parse(path.read_text(encoding="utf-8")) == CONTRACT
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_a_write_that_cannot_land_is_skipped(tmp_path: Path) -> None:
    rp.write(tmp_path / "missing-dir" / ".scout-session.lock.alive", CONTRACT)  # no directory: nothing, no raise
    assert not (tmp_path / "missing-dir").exists()
    (tmp_path / "is-a-dir").mkdir()
    (tmp_path / "is-a-dir" / "x").write_text("", encoding="utf-8")
    rp.write(tmp_path / "is-a-dir", CONTRACT)  # the rename fails: the tmp file goes
    assert sorted(p.name for p in tmp_path.iterdir()) == ["is-a-dir"]


@pytest.mark.parametrize(("text", "pid"), [("4242\n", 4242), ("", None), ("abc\n", None), ("٣\n", None)])
def test_the_lock_pid_reads_only_ascii_digits(tmp_path: Path, text: str, pid: int | None) -> None:
    lock = tmp_path / ".scout-session.lock"
    lock.write_text(text, encoding="utf-8")
    assert rp.lock_pid(lock) == pid
    assert rp.lock_pid(tmp_path / "missing") is None


def test_unparseable_cpu_times_are_skipped() -> None:
    assert rp.cpu_centiseconds("1:xx.00") is None
    assert rp.parse_ps("  100     1   100 Ss    1:xx.00 bash\nnot a row\n") == []


def test_the_real_ps_table_parses() -> None:
    procs = rp.parse_ps(rp.run_ps())
    me = [p for p in procs if p.pid == os.getpid()]
    assert me and me[0].cpu_cs >= 0 and "python" in me[0].command.lower()


def test_pid_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    assert rp._pid_alive(os.getpid())
    assert not rp._pid_alive(0)
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    assert not rp._pid_alive(gone.pid)

    def not_ours(_pid: int, _sig: int) -> None:
        raise PermissionError

    monkeypatch.setattr(rp.os, "kill", not_ours)
    assert rp._pid_alive(1), "a process of another user is alive"


def test_env_secs_takes_whole_numbers_and_zero_only_where_the_shell_does() -> None:
    assert rp.env_secs("X", 60, {"X": "5"}) == 5
    assert [rp.env_secs("X", 60, {"X": v}) for v in ("", "0", "-1", "1.5", "five", "٣")] == [60] * 6
    assert rp.env_secs("X", 60, {"X": "0"}, allow_zero=True) == 0


def test_the_shell_reader_uses_the_same_limits() -> None:
    """scout-lock.sh reads the line in its own python3 snippet; its numbers must be these."""
    lock_lib = (Path(__file__).resolve().parents[3] / "templates" / "scripts" / "scout-lock.sh.tmpl").read_text(
        encoding="utf-8"
    )
    assert f"min(int(parts[4]), {rp.MAX_FRESH_SECS})" in lock_lib
    assert f"-{rp.FRESH_SLACK_SECS} <= age" in lock_lib
    assert f"os.read(fd, {rp.MAX_LINE_BYTES + 1})" in lock_lib and f"len(data) > {rp.MAX_LINE_BYTES}" in lock_lib
    assert "os.O_NOFOLLOW | os.O_NONBLOCK" in lock_lib


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
