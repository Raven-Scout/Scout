"""Budget tests for the session index on a realistic fixture (1b spec §5).

CI asserts the work each build does, not how long it takes:
- a cold build decodes every desktop record and fully parses every transcript;
- an unchanged rebuild does neither;
- the app's steady state (5 records rewritten, 5 transcripts grown) touches exactly those.

The wall-clock ceilings are generous and only catch a gross slowdown on a slow runner. The
real budgets (cold < 5 s, warm < 1 s) are checked on a large real machine.

The fixture is about half a gigabyte, shaped like the author's machine:
- records padded with an unused many-object field, the way MCP configuration pads real ones;
- transcripts in Claude Code's row mix;
- project folders that are repositories, some with linked worktrees.

It is deleted when the test ends, so pytest's retained tmp dirs don't keep it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from scout.sessions.index import BuildOptions, build_index
from scout.sessions.model import Index
from scout.sessions.settings import AgentSessionsSettings
from scout.sessions.stats import BuildStats

RECORDS = 260  # desktop records (the author's machine: 257)
CLI_ONLY = 40  # transcripts with no desktop record
REPOS = 30
WORKTREES = 5  # linked worktrees, one in each of the first five repositories
RECORD_BYTES = 650_000  # real records are ~600 KB, almost all MCP configuration the index never reads
TURNS = 110  # a tool call, its 2–20 KB result and an attachment per turn: ≈ 330 rows, ≈ 1.2 MB
STEADY = 5
COLD_CEILING_S = 60.0
WARM_CEILING_S = 10.0


def _compact(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"))


def _cc_encode(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def _bump(path: Path) -> None:
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


def _padding() -> str:
    """An unused field shaped like a real record's MCP configuration: thousands of small objects."""
    server = {
        "name": "server",
        "type": "http",
        "url": "https://mcp.example.com/v1",
        "tools": [{"name": f"tool_{n}", "enabled": True} for n in range(20)],
    }
    return _compact([server] * (RECORD_BYTES // (len(_compact(server)) + 1)))


def _transcript(cwd: str) -> bytes:
    def at(n: int) -> str:
        return f"2026-09-08T{10 + n // 3600:02d}:{n // 60 % 60:02d}:{n % 60:02d}.000Z"

    rows: list[dict[str, Any]] = [
        {"type": "user", "timestamp": at(0), "message": {"role": "user", "content": "Tidy the parser"}}
    ]
    for i in range(TURNS):
        path = f"{cwd}/src/module_{i % 40:02d}.py"
        rows.append(
            {
                "type": "assistant",
                "timestamp": at(3 * i + 1),
                "message": {
                    "model": "claude-opus-5",
                    "id": f"msg_{i:03d}",
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": f"toolu_{i:03d}", "name": "Read", "input": {"file_path": path}}
                    ],
                },
            }
        )
        rows.append(
            {
                "type": "user",
                "timestamp": at(3 * i + 2),
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "tool_use_id": f"toolu_{i:03d}",
                            "type": "tool_result",
                            "content": "    return parse(line)  # source\n" * (60 + (i % 12) * 50),
                        }
                    ],
                },
                "toolUseResult": {"type": "text", "file": {"filePath": path, "numLines": 200}},
            }
        )
        rows.append(
            {
                "type": "attachment",
                "timestamp": at(3 * i + 3),
                "attachment": {"type": "hook_success", "hookName": "PostToolUse", "content": ""},
            }
        )
    rows.append(
        {
            "type": "assistant",
            "timestamp": at(3 * TURNS + 1),
            "message": {"role": "assistant", "content": [{"type": "text", "text": "Done. Blank lines are skipped."}]},
        }
    )
    return ("\n".join(_compact(r) for r in rows) + "\n").encode()


@dataclass
class World:
    opts: BuildOptions
    repos: list[Path]
    records: list[Path]
    transcripts: list[Path]


def _build_world(root: Path, data_dir: Path) -> World:
    support, home, code = root / "support", root / "claude", root / "code"
    store = support / "claude-code-sessions" / "org-0000" / "user-0000"
    store.mkdir(parents=True)
    repos: list[Path] = []
    for k in range(REPOS):
        repo = code / f"repo-{k:02d}"
        (repo / ".git").mkdir(parents=True)
        repos.append(repo)
    cwds = [str(r) for r in repos]
    for k in range(WORKTREES):
        gitdir = repos[k] / ".git" / "worktrees" / f"w{k}"
        gitdir.mkdir(parents=True)
        (gitdir / "commondir").write_text("../..\n", encoding="utf-8")
        wt = repos[k] / ".claude" / "worktrees" / f"w{k}"
        wt.mkdir(parents=True)
        (wt / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
        cwds.append(str(wt))
    pad = _padding()
    blobs = {cwd: _transcript(cwd) for cwd in cwds}
    now = datetime.now(tz=UTC).replace(microsecond=0)
    records: list[Path] = []
    transcripts: list[Path] = []
    for i in range(RECORDS + CLI_ONLY):
        cwd = cwds[i % len(cwds)]
        uuid = f"{i:08d}-0000-0000-0000-000000000000"
        t = home / "projects" / _cc_encode(cwd) / f"{uuid}.jsonl"
        t.parent.mkdir(parents=True, exist_ok=True)
        t.write_bytes(blobs[cwd])
        at = (now - timedelta(hours=1 + i % 24)).timestamp()
        os.utime(t, (at, at))
        transcripts.append(t)
        if i < RECORDS:
            fields = {
                "sessionId": f"local_{i:04d}",
                "cliSessionId": uuid,
                "cwd": cwd,
                "originCwd": cwd,
                "createdAt": int(at * 1000) - 3_600_000,
                "lastActivityAt": int(at * 1000),
                "model": "claude-opus-5",
                "effort": "high",
                "isArchived": False,
                "title": f"Session {i}",
                "titleSource": "auto",
                "completedTurns": TURNS,
            }
            r = store / f"local_{i:04d}.json"
            r.write_text(_compact(fields)[:-1] + ',"remoteMcpServersConfig":' + pad + "}", encoding="utf-8")
            records.append(r)
    opts = BuildOptions(
        data_dir=data_dir,
        settings=AgentSessionsSettings(),
        claude_home=home,
        support_dir=support,
        now=now,
        use_gh=False,
        pid_alive=lambda pid: False,
    )
    return World(opts=opts, repos=repos, records=records, transcripts=transcripts)


@pytest.fixture
def world_root(tmp_path: Path) -> Iterator[Path]:
    root = tmp_path / "world"
    root.mkdir()
    yield root
    shutil.rmtree(root, ignore_errors=True)


def _timed(opts: BuildOptions) -> tuple[Index, BuildStats, float]:
    stats = BuildStats()
    t0 = time.perf_counter()
    index = build_index(opts, stats=stats)
    return index, stats, time.perf_counter() - t0


@pytest.mark.perf
@pytest.mark.slow
def test_index_builds_do_only_the_work_that_changed(
    fake_data_dir: Path, world_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    t0 = time.perf_counter()
    world = _build_world(world_root, fake_data_dir)
    print(f"fixture generated in {time.perf_counter() - t0:.1f}s")

    def no_subprocess(*args: Any, **kwargs: Any) -> None:
        raise AssertionError(f"an index build started a subprocess: {args[:1]}")

    monkeypatch.setattr(subprocess, "Popen", no_subprocess)  # project roots come from repo_root, never git

    # Cold: empty caches.
    cold, stats, secs = _timed(world.opts)
    print(f"cold {secs:.2f}s {stats}")
    assert len(cold.sessions) == RECORDS + CLI_ONLY and cold.source_errors == []
    assert (stats.desktop_decoded, stats.desktop_served_last_good) == (RECORDS, 0)
    assert (stats.transcripts_full_parsed, stats.transcripts_tail_parsed) == (RECORDS + CLI_ONLY, 0)
    assert sorted(stats.caches_written) == ["desktop", "transcripts"]
    assert {s.project_key for s in cold.sessions} == {str(r.resolve()) for r in world.repos}  # worktrees → main repo
    assert secs < COLD_CEILING_S

    # Unchanged: nothing decoded, parsed, read or written.
    again, stats, secs = _timed(world.opts)
    print(f"unchanged {secs:.2f}s {stats}")
    assert stats == BuildStats()
    assert again.to_dict() == cold.to_dict()
    assert secs < WARM_CEILING_S

    # Steady state: the desktop app rewrote 5 records and 5 sessions appended a turn.
    for r in world.records[:STEADY]:
        text = r.read_text(encoding="utf-8").replace('"title":"Session', '"title":"Renamed session', 1)
        r.write_text(text, encoding="utf-8")
        _bump(r)
    turn = {
        "type": "assistant",
        "timestamp": "2026-09-08T12:00:00.000Z",
        "message": {"role": "assistant", "content": [{"type": "text", "text": "Anything else?"}]},
    }
    appended = (_compact(turn) + "\n").encode()
    for t in world.transcripts[:STEADY]:
        with t.open("ab") as f:
            f.write(appended)
        _bump(t)
    steady, stats, secs = _timed(world.opts)
    print(f"steady {secs:.2f}s {stats}")
    assert (stats.desktop_decoded, stats.desktop_served_last_good) == (STEADY, 0)
    assert (stats.transcripts_full_parsed, stats.transcripts_tail_parsed) == (0, STEADY)
    assert stats.transcript_bytes_read == STEADY * len(appended)
    assert sorted(stats.caches_written) == ["desktop", "transcripts"]
    assert sum(1 for s in steady.sessions if (s.title or "").startswith("Renamed session")) == STEADY
    assert secs < WARM_CEILING_S

    # Identical output: a from-scratch build over the same sources agrees with the incremental one.
    fresh_dir = world_root / "fresh-vault"
    fresh_dir.mkdir()
    assert build_index(replace(world.opts, data_dir=fresh_dir)).to_dict() == steady.to_dict()
