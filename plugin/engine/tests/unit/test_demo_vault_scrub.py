"""The demo vault overlay (docs/demo/vault) names no real person, org, host or tracker.

It feeds public screenshots, so it is held to the eval fixture's allowlist
(spec 2026-10-12 §5.3): a word that isn't a shared stand-in or plain English fails here.
"""

from __future__ import annotations

from pathlib import Path

from tests.eval.harness.scrub import EVAL_ROOT, load_allowlist, scan

DEMO_VAULT = Path(__file__).resolve().parents[4] / "docs" / "demo" / "vault"


def test_demo_overlay_exists() -> None:
    assert any(DEMO_VAULT.rglob("*.md")), f"no demo overlay at {DEMO_VAULT}"


def test_demo_overlay_scans_clean() -> None:
    findings = scan(DEMO_VAULT, load_allowlist(EVAL_ROOT / "allowlist.txt"))
    assert findings == [], "\n".join(f"{f.path}:{f.line}: {f.kind} {f.token!r}" for f in findings)
