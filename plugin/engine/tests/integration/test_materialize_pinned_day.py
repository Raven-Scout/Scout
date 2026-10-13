"""templates/scripts/materialize-daily-file.sh carries the newest daily file into today's.

"Today" comes from ``scout.config.today``: the clock in the configured zone, or,
in an eval run, the day the harness pinned in ``SCOUT_EVAL_TODAY``. Before the pin
was honoured, an eval rendered for a past day carried its fixture into the real
date's file, and every check read a file that was never written. A real run
never sets the variable, so the script is the same and the clock decides.

The template is rendered into a temporary vault and run by the real shell with
this checkout's engine.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from scout.scripts.phase_assembly import render_template

TEMPLATE = Path(__file__).resolve().parents[3] / "templates" / "scripts" / "materialize-daily-file.sh.tmpl"


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "Scout"
    (v / "action-items").mkdir(parents=True)
    scoutctl = v / "bin" / "scoutctl"
    scoutctl.parent.mkdir()
    scoutctl.write_text(f'#!/bin/sh\nexec "{sys.executable}" -m scout.cli "$@"\n', encoding="utf-8")
    scoutctl.chmod(0o755)
    script = v / "scripts" / "materialize-daily-file.sh"
    script.parent.mkdir()
    script.write_text(
        render_template(TEMPLATE.read_text(encoding="utf-8"), {"SCOUT_DIR": str(v), "SCOUTCTL_BIN": str(scoutctl)}),
        encoding="utf-8",
    )
    script.chmod(0o755)
    return v


def _seed(vault: Path, day: date) -> None:
    (vault / "action-items" / f"action-items-{day.isoformat()}.md").write_text(
        f"# Action Items — {day.isoformat()}\n\n## 🔴 Do today\n\n- [ ] [#ALPHA] **Ship the runbook** — due\n",
        encoding="utf-8",
    )


def _run(vault: Path, **env: str) -> subprocess.CompletedProcess[str]:
    base = {k: v for k, v in os.environ.items() if not k.startswith("SCOUT_")}
    return subprocess.run(
        [str(vault / "scripts" / "materialize-daily-file.sh")],
        env={**base, "SCOUT_USER_TIMEZONE": "UTC", **env},
        capture_output=True,
        text=True,
        check=False,
    )


def _daily_files(vault: Path) -> set[str]:
    return {p.name for p in (vault / "action-items").glob("action-items-*.md")}


def test_an_eval_run_carries_into_the_pinned_day(vault: Path) -> None:
    _seed(vault, date(2026, 10, 11))

    result = _run(vault, SCOUT_EVAL_TODAY="2026-10-12")

    assert result.returncode == 0, result.stderr
    assert _daily_files(vault) == {"action-items-2026-10-11.md", "action-items-2026-10-12.md"}
    carried = (vault / "action-items" / "action-items-2026-10-12.md").read_text(encoding="utf-8")
    assert "[#ALPHA]" in carried


def _carried_into_the_clocks_day(vault: Path, **env: str) -> None:
    """Run the script; exactly one file is created, for the clock's day (either side of a midnight)."""
    before = datetime.now(UTC).date()
    _seed(vault, before - timedelta(days=2))
    seeded = _daily_files(vault)

    result = _run(vault, **env)

    after = datetime.now(UTC).date()
    assert result.returncode == 0, result.stderr
    (created,) = _daily_files(vault) - seeded
    assert created in {f"action-items-{d.isoformat()}.md" for d in (before, after)}


@pytest.mark.parametrize("pin", [None, ""], ids=["unset", "empty"])
def test_a_real_run_carries_into_the_clocks_day(vault: Path, pin: str | None) -> None:
    _carried_into_the_clocks_day(vault, **({} if pin is None else {"SCOUT_EVAL_TODAY": pin}))


def test_an_unreadable_pin_never_blocks_the_run(vault: Path) -> None:
    """The backstop never fails a run: a bad pin falls back to the clock here
    (scout.config.today itself refuses it, for every other caller)."""
    _carried_into_the_clocks_day(vault, SCOUT_EVAL_TODAY="Monday")
