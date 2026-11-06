"""claude-with-retry.sh records a failed attempt's spend and gates the retry on it (#561).

Renders the wrapper and drives it with a fake ``claude`` that writes a
transcript per attempt the way Claude Code does (a ``custom-title`` row with the
run's ``--name``, then ``cost-state`` at exit) and records the argv it got. A
stub ``scoutctl`` runs this checkout's engine for ``scoutctl budget …``. After a
failed attempt the wrapper calls ``scoutctl budget record-attempt``: the attempt's
row lands before the retry starts, the retry's ``--max-budget-usd`` is what the
run has left, and no retry follows once that or the window's budget is spent.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]  # …/plugin
TEMPLATE = REPO_ROOT / "templates" / "scripts" / "claude-with-retry.sh.tmpl"
NAME = "scout-research-20261102-1300"
CAP = "25"

SCOUTCTL_STUB = """#!/usr/bin/env python3
import os, sys
if sys.argv[1:2] == ["budget"]:
    os.execv({python!r}, [{python!r}, "-m", "scout.cli", *sys.argv[1:]])
sys.exit(0)
"""

# Attempt n: write transcript a<n> priced at the n-th cost, snapshot the tracker as
# it was when the attempt started, then print the n-th output and exit with the n-th code.
FAKE_CLAUDE = """#!/bin/bash
n=$(cat "{counter}" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "{counter}"
printf '%s\\n' "$@" > "{work}/argv.$n"
cp "{tracker}" "{work}/tracker-at-start.$n" 2>/dev/null || : > "{work}/tracker-at-start.$n"
costs=({costs}); outputs=({outputs}); codes=({codes})
mkdir -p "{transcripts}"
{{
  echo '{{"type":"custom-title","customTitle":"{name}","sessionId":"a'$n'"}}'
  echo '{{"type":"cost-state","sessionId":"a'$n'","totalCostUSD":'"${{costs[$((n - 1))]}}"'}}'
}} > "{transcripts}/a$n.jsonl"
echo "${{outputs[$((n - 1))]}}"
exit "${{codes[$((n - 1))]}}"
"""

TRANSIENT = "API Error: Connection closed mid-response. The response above may be incomplete."
PERMANENT = "Error: something else went wrong"


@pytest.fixture(autouse=True)
def _silence_desktop_notifier(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    quiet = tmp_path_factory.mktemp("quiet-notifier")
    for name in ("osascript", "notify-send"):
        p = quiet / name
        p.write_text("#!/bin/bash\ncat > /dev/null\nexit 0\n", encoding="utf-8")
        p.chmod(0o755)
    monkeypatch.setenv("PATH", f"{quiet}{os.pathsep}{os.environ['PATH']}")


def _exe(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


class Rig:
    def __init__(self, tmp_path: Path, *, costs: list[float], outputs: list[str], codes: list[int]) -> None:
        self.vault = tmp_path / "Scout"
        (self.vault / ".scout-logs").mkdir(parents=True)
        # A window budget far above these runs, so only the run's own cap stops a retry.
        (self.vault / "scout-config.yaml").write_text("budget:\n  daily_usd: 1000\n", encoding="utf-8")
        self.work = tmp_path / "work"
        self.work.mkdir()
        self.config = tmp_path / "claude-config"
        self.tracker = self.vault / ".scout-logs" / "usage-tracker.jsonl"
        self.log = self.vault / ".scout-logs" / "run.log"
        text = TEMPLATE.read_text(encoding="utf-8").replace("{{INSTANCE_NAME}}", "Scout")
        self.wrapper = _exe(self.vault / "scripts" / "claude-with-retry.sh", text)
        self.scoutctl = _exe(tmp_path / "bin" / "scoutctl", SCOUTCTL_STUB.format(python=sys.executable))
        transcripts = self.config / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(self.vault))
        self.claude = _exe(
            tmp_path / "fake-claude.sh",
            FAKE_CLAUDE.format(
                counter=self.work / "count",
                work=self.work,
                tracker=self.tracker,
                transcripts=transcripts,
                name=NAME,
                costs=" ".join(str(c) for c in costs),
                outputs=" ".join(json.dumps(o) for o in outputs),
                codes=" ".join(str(c) for c in codes),
            ),
        )

    def run(self, *, scoutctl: Path | None | str = "default") -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if not k.startswith(("SCOUT", "CLAUDE"))}
        env.update(
            SCOUT_RETRY_BACKOFF_S="0",
            SCOUT_RETRY_MAX="2",
            SCOUT_NO_CAFFEINATE="1",
            SCOUT_DATA_DIR=str(self.vault),
            CLAUDE_CONFIG_DIR=str(self.config),
            SCOUT_RUN_TYPE="research",
            SCOUT_RUN_STARTED=str(int(time.time()) - 5),
        )
        if scoutctl == "default":
            env["SCOUTCTL_BIN"] = str(self.scoutctl)
        elif scoutctl is not None:
            env["SCOUTCTL_BIN"] = str(scoutctl)
        args = ["--model", "opus", "--max-budget-usd", CAP, "--name", NAME, "-p", "go"]
        return subprocess.run(
            [str(self.wrapper), str(self.log), str(self.claude), *args],
            env=env,
            cwd=self.vault,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def attempts(self) -> int:
        return int((self.work / "count").read_text().strip())

    def cap_of(self, attempt: int) -> str:
        argv = (self.work / f"argv.{attempt}").read_text(encoding="utf-8").splitlines()
        return argv[argv.index("--max-budget-usd") + 1]

    def rows(self, path: Path | None = None) -> list[dict]:
        text = (path or self.tracker).read_text(encoding="utf-8") if (path or self.tracker).exists() else ""
        return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_a_failed_attempt_is_recorded_and_the_retry_gets_what_is_left(tmp_path: Path) -> None:
    rig = Rig(tmp_path, costs=[8.4, 5.0], outputs=[TRANSIENT, "done"], codes=[1, 0])

    result = rig.run()

    assert result.returncode == 0, rig.log.read_text()
    assert rig.attempts() == 2
    [row] = rig.rows(rig.work / "tracker-at-start.2")
    assert row["source"] == "attempt" and row["session_id"] == "a1" and row["budget_spent"] == 8.4
    assert row["type"] == "research" and row["exit_code"] == 1
    assert rig.cap_of(1) == CAP
    assert rig.cap_of(2) == "16.60"
    assert "16.60" in rig.log.read_text()


def test_a_failed_attempt_with_no_retry_is_recorded(tmp_path: Path) -> None:
    rig = Rig(tmp_path, costs=[6.25], outputs=[PERMANENT], codes=[1])

    result = rig.run()

    assert result.returncode == 1
    assert rig.attempts() == 1
    [row] = rig.rows()
    assert row["source"] == "attempt" and row["session_id"] == "a1" and row["budget_spent"] == 6.25


def test_no_retry_once_the_attempts_spent_the_runs_cap(tmp_path: Path) -> None:
    rig = Rig(tmp_path, costs=[25.0, 1.0], outputs=[TRANSIENT, "done"], codes=[1, 0])

    result = rig.run()

    assert result.returncode == 1
    assert rig.attempts() == 1
    log = rig.log.read_text()
    assert "no retry" in log and "Transient API error detected" not in log


def test_without_scoutctl_the_retry_runs_as_before(tmp_path: Path) -> None:
    rig = Rig(tmp_path, costs=[8.4, 5.0], outputs=[TRANSIENT, "done"], codes=[1, 0])

    result = rig.run(scoutctl=None)

    assert result.returncode == 0, rig.log.read_text()
    assert rig.attempts() == 2 and rig.cap_of(2) == CAP
    assert rig.rows() == []


def test_an_engine_without_record_attempt_still_retries_with_the_full_cap(tmp_path: Path) -> None:
    rig = Rig(tmp_path, costs=[8.4, 5.0], outputs=[TRANSIENT, "done"], codes=[1, 0])
    old = _exe(tmp_path / "bin" / "old-scoutctl", '#!/bin/bash\necho "No such command" >&2\nexit 2\n')

    result = rig.run(scoutctl=old)

    assert result.returncode == 0, rig.log.read_text()
    assert rig.attempts() == 2 and rig.cap_of(2) == CAP
