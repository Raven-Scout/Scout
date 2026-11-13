"""The doctor reports the cached gate probe (spec 2026-11-13 §3.5, plan Task 1a.4). It never runs claude."""

from __future__ import annotations

import json
from pathlib import Path

from scout.scripts.bootstrap_doctor import _check_gate_probe


def _cache(vault: Path, **over: object) -> None:
    data = {
        "ok": True,
        "reason": "kb-read-gate denied the probe's shell read of a KB note",
        "claude_version": "2.1.318 (Claude Code)",
        "hooks_sha256": "abc",
        "flags": ["--permission-mode", "auto"],
        "env_key": "CLAUDE_CODE_SIMPLE=",
        "checked_at": "2026-11-13T21:29:05+00:00",
        **over,
    }
    (vault / ".scout-state").mkdir(parents=True, exist_ok=True)
    (vault / ".scout-state" / "gate-probe.json").write_text(json.dumps(data), encoding="utf-8")


def test_doctor_warns_when_cached_probe_failed(tmp_path: Path) -> None:
    _cache(tmp_path, ok=False, reason="Scout's gates are not loaded: the probe's shell read ran")
    warnings, notes = _check_gate_probe(vault=tmp_path)
    (warning,) = warnings
    assert "gates are not loaded" in warning
    assert "scoutctl gates probe" in warning
    assert notes == []


def test_doctor_notes_probe_version_when_ok(tmp_path: Path) -> None:
    _cache(tmp_path)
    warnings, notes = _check_gate_probe(vault=tmp_path)
    assert warnings == []
    (note,) = notes
    assert "2.1.318" in note and "2026-11-13" in note


def test_doctor_is_silent_without_a_probe_result(tmp_path: Path) -> None:
    assert _check_gate_probe(vault=tmp_path) == ([], [])


def test_doctor_is_silent_on_an_unreadable_cache(tmp_path: Path) -> None:
    (tmp_path / ".scout-state").mkdir()
    (tmp_path / ".scout-state" / "gate-probe.json").write_text("{nope", encoding="utf-8")
    assert _check_gate_probe(vault=tmp_path) == ([], [])
