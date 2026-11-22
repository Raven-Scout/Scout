"""`scoutctl health resolve-prompt <kind> <key> --json` is a contract with the macOS app's "Resolve with Claude".

``tests/fixtures/contract/resolve-prompt.json`` is the canonical copy; the app keeps a byte copy at
``apps/macos/ScoutTests/Fixtures/resolve-prompt.json`` (``contract.yml`` compares them). Set
``SCOUT_REGEN_CONTRACT=1`` to rewrite the canonical file from the deterministic vault below, then ``cp`` it over
the app copy and update the pins in ``apps/macos/ScoutTests/Engine/ResolvePromptContractTests.swift``.

The file holds one document per kind, as ``{"finding": …, "connector": …, "run": …}``: the integrity-restore
finding of a run whose check put a protected file back on a branch, a critical connector alert, and a failed run.
The vault path is written as ``/Users/alex/Scout`` and the engine's own path as ``/Users/alex/.scout/engine``.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scout import health, resolve_prompt

from .test_resolve_prompt import ALERT, NOW, _row, _write_alerts, build_vault

# Read at import: tests/conftest.py scrubs SCOUT_* from the environment before each test runs.
REGEN = os.environ.get("SCOUT_REGEN_CONTRACT") == "1"
CANONICAL = Path(__file__).resolve().parents[1] / "fixtures" / "contract" / "resolve-prompt.json"
FAILED_LOG = "scout-2026-11-19_18-30.log"
SUBJECTS = {"finding": "run.integrity_restored", "connector": "mcp:example_tracker", "run": FAILED_LOG}


def _render(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(health, "vault_birth", lambda _v: NOW - timedelta(days=60))
    monkeypatch.setattr(health, "tick_job_installed", lambda home=None: True)
    monkeypatch.setattr(health, "recent_commits", lambda *a, **k: [])
    rows = [
        _row(datetime(2026, 11, 19, 17, 3, tzinfo=UTC), 6, integrity="restored"),
        _row(
            datetime(2026, 11, 19, 18, 30, tzinfo=UTC),
            2,
            outcome="failed",
            exit_code=1,
            failure_class="network",
            commits_in_window=0,
            newest_commit=None,
        ),
    ]
    vault = build_vault(tmp_path, rows=rows)
    with (vault / ".scout-logs" / FAILED_LOG).open("a", encoding="utf-8") as f:
        f.write("API Error: Connection error. (ECONNRESET)\n")
    _write_alerts(vault, [ALERT])
    doc = {
        kind: resolve_prompt.build(vault, kind, key, now=NOW, home=tmp_path / "home").to_dict()
        for kind, key in SUBJECTS.items()
    }
    text = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    engine = str(Path(resolve_prompt.scout.__file__).resolve().parents[1])
    return (
        text.replace(engine, "/Users/alex/.scout/engine")
        .replace(str(vault), "/Users/alex/Scout")
        .replace(str(tmp_path), "/Users/alex")
    )


def test_resolve_prompt_matches_the_shared_fixture(tmp_path, monkeypatch):
    text = _render(tmp_path, monkeypatch)
    if REGEN:
        CANONICAL.write_text(text, encoding="utf-8")
    assert CANONICAL.read_text(encoding="utf-8") == text, (
        "regenerate with SCOUT_REGEN_CONTRACT=1, then cp it to apps/macos/ScoutTests/Fixtures/resolve-prompt.json"
    )


def test_the_fixture_has_every_kind_and_key(tmp_path, monkeypatch):
    doc = json.loads(_render(tmp_path, monkeypatch))
    assert set(doc) == set(resolve_prompt.KINDS)
    for kind, key in SUBJECTS.items():
        d = doc[kind]
        assert set(d) == {"schema_version", "kind", "key", "current", "title", "cwd", "prompt"}
        assert (d["schema_version"], d["kind"], d["key"], d["current"]) == (1, kind, key, True)
        assert d["cwd"] == "/Users/alex/Scout"
        assert resolve_prompt.GUARDRAILS in d["prompt"]
