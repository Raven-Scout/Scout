"""Shared Areas S1b end to end: ``scoutctl kb unshare | share | shares`` in a subprocess, against a fake A8b Agnes.

The fake (``tests/unit/fake_agnes_shares.py``) listens on 127.0.0.1 in this
process; ``scoutctl`` runs as ``python -m scout.cli`` with a temp HOME, so the
link token it reads is the made-up one written here, never a real secret.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from tests.unit.fake_agnes_shares import FakeAgnes, fake_jwt, serve
from tests.unit.test_kb_share import CONFIG, PLAN, PRIYA, SAM, WIDGET, _note

GLOB = f"{WIDGET}/**"


@pytest.fixture
def agnes() -> Iterator[FakeAgnes]:
    with serve(FakeAgnes()) as fake:
        yield fake


@pytest.fixture
def home(tmp_path: Path) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.fixture
def vault(tmp_path: Path, home: Path, agnes: FakeAgnes, monkeypatch: pytest.MonkeyPatch) -> Path:
    v = tmp_path / "vault"
    v.mkdir()
    sharing = (
        "  federation: {enabled: true}\n  federation_defaults:\n"
        f'    - {{glob: "{GLOB}", scope: ["{PRIYA}", "{SAM}"]}}\n'
    )
    (v / "scout-config.yaml").write_text(CONFIG + sharing, encoding="utf-8")
    _note(v, PLAN, "name: Widget plan\ntype: project")
    monkeypatch.setenv("HOME", str(home))  # save_token / save_state write under the temp HOME
    from scout.repo_sync import LinkState, save_state, save_token

    save_state(v, LinkState(agnes_url=agnes.url, clone_url="", linked_at="", last_laptop="", last_mirror=""))
    save_token(v, fake_jwt("pat"))
    agnes.share(GLOB, "priya@example.com", "sam@example.com")
    return v


def scoutctl(home: Path, vault: Path, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("SCOUT_")}
    env.update(HOME=str(home), **extra)
    return subprocess.run(
        [sys.executable, "-P", "-m", "scout.cli", "kb", *args, "--repo", str(vault)],
        env=env,
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=120,
    )


def defaults(vault: Path) -> list[dict[str, object]]:
    return yaml.safe_load((vault / "scout-config.yaml").read_text(encoding="utf-8"))["privacy"]["federation_defaults"]


def test_unshare_stops_at_once_then_share_clears_it(home: Path, vault: Path, agnes: FakeAgnes) -> None:
    res = scoutctl(home, vault, "unshare", WIDGET, "--with", "priya@example.com", "--json")
    assert res.returncode == 0, res.stderr
    data = json.loads(res.stdout)
    assert data["pending_until_sync"] is False
    assert [c["body"] for c in agnes.calls("POST", "/shares/stop")] == [
        {"glob": GLOB, "person": PRIYA, "write_back": False}
    ]
    assert defaults(vault) == [{"glob": GLOB, "scope": [SAM]}]

    listed = json.loads(scoutctl(home, vault, "shares", "--json").stdout)
    assert [s["principal"] for s in listed["shares"][0]["stops"]] == [PRIYA]

    res = scoutctl(home, vault, "share", WIDGET, "--with", "priya@example.com", "--yes")
    assert res.returncode == 0, res.stderr
    assert len(agnes.calls("POST", "/clear-stop")) == 1 and agnes.open_stops() == []
    assert defaults(vault) == [{"glob": GLOB, "scope": [SAM, PRIYA]}]


@pytest.mark.parametrize(
    "extra",
    [{"SCOUT_MODE": "briefing"}, {"SCOUT_READER": "autonomous"}, {"SCOUT_READER": "delegate:phone"}],
    ids=["scheduled", "engine-run", "delegate"],
)
def test_a_run_cant_clear_a_stop(home: Path, vault: Path, agnes: FakeAgnes, extra: dict[str, str]) -> None:
    agnes.add_stop(GLOB, PRIYA)
    res = scoutctl(home, vault, "share", WIDGET, "--with", "priya@example.com", "--yes", **extra)
    assert res.returncode != 0
    assert "refused" in res.stderr or "isn't available to delegate:phone" in res.stderr
    assert agnes.calls("POST") == [] and len(agnes.open_stops()) == 1


def test_an_old_agnes_falls_back_to_the_next_sync(home: Path, vault: Path, agnes: FakeAgnes) -> None:
    agnes.mode = "old"
    res = scoutctl(home, vault, "unshare", WIDGET)
    assert res.returncode == 0, res.stderr
    assert "no instant stop yet (A8b)" in res.stdout
    assert defaults(vault) == []
    assert agnes.calls("POST") == []
