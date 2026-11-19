"""The three runner templates scan the vault's git log for changes after the session, before the outcome record,
from the last scan's watermark (no fixed window)."""

from __future__ import annotations

from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parents[3] / "templates"


@pytest.mark.parametrize("tmpl", ["run-scout.sh.tmpl", "run-dreaming.sh.tmpl", "run-research.sh.tmpl"])
def test_runner_scans_after_session_before_outcome(tmpl):
    text = (TEMPLATES / tmpl).read_text()
    scan_at = text.index('"$SCOUTCTL_BIN" changes scan >>')
    # The success path records through "$OUTCOME" (= scripts/run-outcome.sh); the one literal
    # `run-outcome.sh" record` earlier in the file is an early-exit skip path, before the session.
    assert scan_at < text.rindex('"$OUTCOME" record')
    assert scan_at > text.index("agent-runtime exec")
    line = text[scan_at : text.index("\n", scan_at)]
    assert '>> "$LOG_FILE" 2>&1 || true' in line
    # No fixed window: a plain scan reads from the last scan's watermark, so a long gap between runs loses nothing.
    assert "--since" not in line
