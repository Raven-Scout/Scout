"""Budget test for `scoutctl session index` (spec §4.12): cold < 5 s, warm < 1 s, no gh."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scout.sessions.index import BuildOptions, build_index
from scout.sessions.settings import AgentSessionsSettings
from tests.unit.sessions_helpers import claude_home, support_dir, write_desktop_record, write_transcript

RECORDS = 200
TRANSCRIPTS = 450
TURNS_PER_TRANSCRIPT = 40


def _rows(n: int) -> list[dict]:
    rows: list[dict] = [
        {
            "type": "user",
            "timestamp": "2026-09-08T10:00:00.000Z",
            "message": {"role": "user", "content": [{"type": "text", "text": "do the thing"}]},
        }
    ]
    for i in range(n):
        rows.append(
            {
                "type": "assistant",
                "timestamp": "2026-09-08T10:00:01.000Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": f"t{i}",
                            "name": "Read",
                            "input": {"file_path": f"/Users/alex/code/repo-{i % 7}/src/f{i}.py"},
                        }
                    ],
                },
            }
        )
        rows.append(
            {
                "type": "user",
                "timestamp": "2026-09-08T10:00:02.000Z",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": f"t{i}", "content": "x" * 400}],
                },
            }
        )
    rows.append(
        {
            "type": "assistant",
            "timestamp": "2026-09-08T10:00:03.000Z",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "done"}]},
        }
    )
    return rows


@pytest.mark.perf
@pytest.mark.slow
def test_index_build_stays_within_budget(fake_data_dir: Path) -> None:
    s, h = support_dir(), claude_home()
    rows = _rows(TURNS_PER_TRANSCRIPT)
    for i in range(TRANSCRIPTS):
        uuid = f"{i:08d}-0000-0000-0000-000000000000"
        write_transcript(h, f"-Users-alex-code-repo-{i % 7}", uuid, rows, mtime_ago_hours=1 + (i % 24))
        if i < RECORDS:
            write_desktop_record(
                s,
                f"local_{i:04d}",
                cliSessionId=uuid,
                cwd=f"/Users/alex/code/repo-{i % 7}",
                originCwd=f"/Users/alex/code/repo-{i % 7}",
            )
    opts = BuildOptions(
        data_dir=fake_data_dir,
        settings=AgentSessionsSettings(),
        claude_home=h,
        support_dir=s,
        now=datetime.now(tz=UTC),
        use_gh=False,
        toplevel=lambda p: None,
        pid_alive=lambda pid: False,
    )

    t0 = time.perf_counter()
    cold = build_index(opts)
    cold_s = time.perf_counter() - t0
    assert len(cold.sessions) == TRANSCRIPTS  # 200 desktop + 250 cli-only

    # Persist the caches the way run() does, then rebuild with nothing changed.
    from scout.sessions import github
    from scout.sessions.transcript import TRANSCRIPT_CACHE_FILENAME, load_transcript_cache

    assert (fake_data_dir / ".scout-cache" / TRANSCRIPT_CACHE_FILENAME).exists()
    assert len(load_transcript_cache(fake_data_dir / ".scout-cache" / TRANSCRIPT_CACHE_FILENAME)) == TRANSCRIPTS
    assert (fake_data_dir / ".scout-cache" / github.PR_CACHE_FILENAME).exists()

    t1 = time.perf_counter()
    warm = build_index(opts)
    warm_s = time.perf_counter() - t1
    assert len(warm.sessions) == TRANSCRIPTS

    print(f"cold={cold_s:.2f}s warm={warm_s:.2f}s")
    assert cold_s < 5.0, f"cold build took {cold_s:.2f}s"
    assert warm_s < 1.0, f"warm build took {warm_s:.2f}s"
