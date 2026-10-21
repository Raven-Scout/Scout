"""#453 Task 6: scheduled runs read as ``autonomous``; caches and vault scripts carry labels.

Spec §6.2 (choosing the reader) and the §6.3 rows for ``pre_session_data``,
``kb_pre_filter``, ``vault-freshness.py``, ``recurring-task-status.py``,
``heartbeat.py`` and the vault-local enrichment generator; §11.2 "Reader
resolution" and "Enrichment generator stand-in". Fixture: the anonymised
privacy vault (``privacy_vault_helpers``).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from scout.hooks import kb_pre_filter
from scout.kb.privacy import load_policy, surface_class
from scout.kb.read import read_file, visible_files
from scout.kb.reader import resolve_reader
from scout.schedule import OnMissPolicy, Slot, SlotType
from scout.scripts import heartbeat as hb
from scout.scripts import pre_session_data as psd
from scout.scripts import schedule_tick, vault_drift
from tests.unit.privacy_vault_helpers import CANARIES, build_privacy_vault

TEMPLATES = Path(__file__).resolve().parents[3] / "templates"
FRESHNESS = TEMPLATES / "scripts" / "vault-freshness.py"
RECURRING = TEMPLATES / "scripts" / "recurring-task-status.py"

PERMIT = "knowledge-base/personal/task-renew-permit.md"
GIFT = "knowledge-base/personal/task-buy-gift.md"
BILLING = "knowledge-base/topics/billing/billing.md"
ROLLOUT = "knowledge-base/topics/billing/rollout.md"
AGENT = "agent:person:priya@example.com"


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    return build_privacy_vault(tmp_path)


def _as(monkeypatch: pytest.MonkeyPatch, vault: Path, *, reader: str | None = None, mode: str | None = None) -> None:
    monkeypatch.setenv("SCOUT_DATA_DIR", str(vault))
    if reader is not None:
        monkeypatch.setenv("SCOUT_READER", reader)
    if mode is not None:
        monkeypatch.setenv("SCOUT_MODE", mode)


# ----- launchers ------------------------------------------------------------------


def test_heartbeat_launches_its_runner_as_the_autonomous_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    class FakePopen:
        pid = 1

        def __init__(self, argv: list[str], **kwargs: Any) -> None:
            seen.update(kwargs)

    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    hb.launch_runner(tmp_path / "run-dreaming.sh", vault=tmp_path, log_path=tmp_path / "hb.log")
    assert seen["env"]["SCOUT_READER"] == "autonomous"
    assert resolve_reader(None, seen["env"]) == "autonomous"


def test_schedule_tick_spawns_its_runner_as_the_autonomous_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    class FakePopen:
        pid = 2

        def __init__(self, argv: list[str], **kwargs: Any) -> None:
            seen.update(kwargs)

    monkeypatch.setattr(schedule_tick.subprocess, "Popen", FakePopen)
    slot = Slot(
        key="morning",
        type=SlotType.BRIEFING,
        runner="run-scout.sh",
        fires_at_local="08:00",
        weekdays=("Mon",),
        missed_window_hours=4,
        on_miss=OnMissPolicy.SKIP,
        cooldown_minutes=60,
    )
    schedule_tick._spawn_runner(tmp_path, "morning", slot)
    assert seen["env"]["SCOUT_READER"] == "autonomous"


# ----- session-context.json --------------------------------------------------------


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(psd, "get_pr_authored", lambda: [])
    monkeypatch.setattr(psd, "get_pr_review_requested", lambda: [])


def _dated(vault: Path) -> None:
    """Two dated notes: one private by the personal/ path default, one normal."""
    (vault / "knowledge-base/personal/notes-dated.md").write_text(
        "Last updated: 2026-10-01\n\nPersonal notes CANARY-PRIV-9D1T.\n", encoding="utf-8"
    )
    (vault / "knowledge-base/topics/dated.md").write_text("Last updated: 2026-10-02\n\nA topic.\n", encoding="utf-8")


def _context(vault: Path) -> dict[str, Any]:
    out = psd.run("briefing", data_dir=vault)
    return json.loads(out.read_text(encoding="utf-8"))


def test_session_context_labels_personal_tasks_for_the_autonomous_reader(
    vault: Path, monkeypatch: pytest.MonkeyPatch, no_network: None
) -> None:
    _as(monkeypatch, vault, mode="briefing")
    _dated(vault)
    ctx = _context(vault)

    tasks = {t["path"]: t for t in ctx["personal_tasks"]}
    assert tasks[PERMIT]["visibility"] == "private"
    assert tasks[GIFT]["visibility"] == "normal"
    assert tasks[PERMIT]["name"] == "task-renew-permit"

    dates = ctx["kb_file_dates"]
    assert dates["knowledge-base/personal/notes-dated.md"] == {"last_updated": "2026-10-01", "visibility": "private"}
    assert dates["knowledge-base/topics/dated.md"] == {"last_updated": "2026-10-02", "visibility": "normal"}


def test_session_context_needs_no_parser_on_path(
    vault: Path, monkeypatch: pytest.MonkeyPatch, no_network: None
) -> None:
    """Personal tasks come from the engine, not the vault parser.py shim (no PATH lookup)."""
    _as(monkeypatch, vault, reader="autonomous")
    monkeypatch.setenv("PATH", "/nonexistent")
    tasks = {t["path"] for t in _context(vault)["personal_tasks"]}
    assert {PERMIT, GIFT} <= tasks


def test_session_context_is_empty_for_an_agent_reader(
    vault: Path, monkeypatch: pytest.MonkeyPatch, no_network: None
) -> None:
    _as(monkeypatch, vault, reader=AGENT)
    _dated(vault)
    ctx = _context(vault)
    assert ctx["personal_tasks"] == []
    assert ctx["kb_file_dates"] == {}
    assert ctx["git_recent"] == ""
    text = json.dumps(ctx)
    assert not any(c in text for c in CANARIES.values())


# ----- kb-filter.md -------------------------------------------------------------------


def _kb_filter(vault: Path) -> str:
    assert kb_pre_filter.run("briefing") is not None
    return (vault / ".scout-cache" / "kb-filter.md").read_text(encoding="utf-8")


def test_kb_filter_marks_private_files_for_the_autonomous_reader(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _as(monkeypatch, vault, mode="briefing")
    _dated(vault)
    text = _kb_filter(vault)
    lines = text.splitlines()
    assert f"- [private] {BILLING}" in lines
    assert f"- {ROLLOUT}" in lines
    assert any(ln.startswith("- **knowledge-base/topics/dated.md** — ") for ln in lines)  # stale, normal
    # Every listed private file carries the mark; no normal one does.
    policy = load_policy(vault)
    for ln in lines:
        m = re.match(r"^- (\[private\] )?\**(knowledge-base/\S+?\.md)", ln)
        if m:
            from scout.kb.privacy import label_file

            assert bool(m.group(1)) == (label_file(vault, m.group(2), policy).visibility == "private"), ln


def test_kb_filter_lists_nothing_for_an_agent_reader(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _as(monkeypatch, vault, reader=AGENT)
    text = _kb_filter(vault)
    assert not [ln for ln in text.splitlines() if ln.startswith("- ")]
    assert "Stale: 0 | No date: 0 | Fresh: 0" in text


def test_kb_filter_in_an_interactive_session_reads_as_owner_and_labels(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _as(monkeypatch, vault)  # no SCOUT_MODE, no SCOUT_READER: the owner
    assert f"- [private] {BILLING}" in _kb_filter(vault).splitlines()


# ----- heartbeat research queue ------------------------------------------------------


def _queue_item(vault: Path, name: str, *, visibility: str | None = None, status: str = "open") -> None:
    d = vault / "knowledge-base" / "research-queue"
    d.mkdir(parents=True, exist_ok=True)
    vis = f"visibility: {visibility}\n" if visibility else ""
    (d / f"{name}.md").write_text(f"---\nstatus: {status}\n{vis}---\n\n# {name}\n", encoding="utf-8")


def test_research_queue_ignores_a_private_open_item(vault: Path) -> None:
    _queue_item(vault, "q-private", visibility="private")
    _queue_item(vault, "q-done", status="done")
    assert hb.research_queue_has_open(vault) is False
    _queue_item(vault, "q-normal")
    assert hb.research_queue_has_open(vault) is True


def test_legacy_research_queue_file_that_is_private_counts_as_empty(vault: Path) -> None:
    (vault / "knowledge-base" / "research-queue.md").write_text(
        "---\nvisibility: private\n---\n\n- [ ] look into something\n", encoding="utf-8"
    )
    assert hb.research_queue_has_open(vault) is False


# ----- drift: vault-local runners and KB readers -------------------------------------


def _drift_vault(tmp_path: Path) -> Path:
    v = tmp_path / "v"
    (v / "scripts").mkdir(parents=True)
    (v / "run-extra.sh").write_text("#!/bin/bash\n# CANARY-PRIV-8X8X\nclaude -p go\n", encoding="utf-8")
    (v / "run-ok.sh").write_text('#!/bin/bash\nexport SCOUT_MODE="trading"\nclaude -p go\n', encoding="utf-8")
    (v / "run-reader.sh").write_text("#!/bin/bash\nexport SCOUT_READER=autonomous\n", encoding="utf-8")
    (v / "scripts" / "enrich.py").write_text(
        "from pathlib import Path\n# CANARY-PRIV-8X8X\n"
        "for p in Path('knowledge-base/people').rglob('*.md'):\n    print(p.read_text())\n",
        encoding="utf-8",
    )
    (v / "scripts" / "enrich-ok.py").write_text(
        "import subprocess\n# knowledge-base people via the gate\n"
        "out = subprocess.run(['scoutctl', 'kb', 'files', '--glob', 'knowledge-base/people/**', '--json'])\n"
        "open('x')\n",
        encoding="utf-8",
    )
    (v / "scripts" / "unrelated.sh").write_text("#!/bin/bash\nfind . -name '*.log'\n", encoding="utf-8")
    # An engine-rendered script (it has a last-render record) is the engine's to keep compliant.
    managed = "scripts/kb-shard-log.py"
    (v / managed).write_text("import os\nos.walk('knowledge-base')\n", encoding="utf-8")
    snap = vault_drift.snapshot_path(v, managed)
    snap.parent.mkdir(parents=True)
    snap.write_text("import os\nos.walk('knowledge-base')\n", encoding="utf-8")
    return v


def test_drift_reports_a_runner_without_the_reader_exports(tmp_path: Path) -> None:
    findings = vault_drift.privacy_drift(_drift_vault(tmp_path))
    runners = [f for f in findings if f.rule == "runner-no-reader"]
    assert [f.path for f in runners] == ["run-extra.sh"]
    msg = runners[0].message
    assert "export SCOUT_READER=autonomous" in msg and "export SCOUT_MODE=" in msg
    assert "CANARY" not in msg and "claude -p" not in msg


def test_drift_reports_a_vault_script_reading_the_kb_without_scoutctl_kb(tmp_path: Path) -> None:
    findings = vault_drift.privacy_drift(_drift_vault(tmp_path))
    scripts = [f for f in findings if f.rule == "script-raw-kb-read"]
    assert [f.path for f in scripts] == ["scripts/enrich.py"]
    msg = scripts[0].message
    assert "scoutctl kb files" in msg and "visibility" in msg
    assert "CANARY" not in msg and "rglob" not in msg


def test_doctor_warns_about_privacy_drift(tmp_path: Path) -> None:
    from scout.scripts.bootstrap_doctor import _check_vault_drift

    warnings, _notes = _check_vault_drift(vault=_drift_vault(tmp_path))
    assert any("run-extra.sh" in w and "SCOUT_READER" in w for w in warnings)
    assert any("scripts/enrich.py" in w for w in warnings)


# ----- vault scripts: vault-freshness.py, recurring-task-status.py --------------------


def _script(script: Path, vault: Path, *args: str, python: list[str] | None = None, **env: str) -> str:
    clean = {k: v for k, v in os.environ.items() if not k.startswith("SCOUT_")}
    clean.update({"SCOUT_DATA_DIR": str(vault), "SCOUT_LOCALTIME": "/nonexistent", **env})
    proc = subprocess.run(
        [*(python or [sys.executable]), str(script), *args],
        capture_output=True,
        text=True,
        env=clean,
        cwd=vault,
        check=True,
    )
    return proc.stdout


def _freshness_rows(out: str) -> dict[str, dict[str, Any]]:
    return {r["file"]: r for r in json.loads(out)["files"]}


def test_vault_freshness_labels_rows_in_process(vault: Path) -> None:
    rows = _freshness_rows(_script(FRESHNESS, vault, "--all", "--json", SCOUT_MODE="briefing"))
    assert rows[PERMIT]["visibility"] == "private"
    assert rows[GIFT]["visibility"] == "normal"
    table = _script(FRESHNESS, vault, "--all", SCOUT_MODE="briefing")
    assert f"| [private] {PERMIT} |" in table
    assert f"| {GIFT} |" in table


def test_vault_freshness_labels_rows_through_scoutctl(vault: Path) -> None:
    """A vault python that can't import the engine asks `scoutctl kb files`."""
    scoutctl = Path(sys.executable).parent / "scoutctl"
    out = _script(
        FRESHNESS,
        vault,
        "--all",
        "--json",
        python=[sys.executable, "-S"],
        SCOUT_MODE="briefing",
        SCOUTCTL_BIN=str(scoutctl),
        PATH="/usr/bin:/bin",
    )
    rows = _freshness_rows(out)
    assert rows[PERMIT]["visibility"] == "private"
    assert rows[GIFT]["visibility"] == "normal"


def test_vault_freshness_fails_closed_without_the_engine(vault: Path) -> None:
    out = _script(FRESHNESS, vault, "--all", python=[sys.executable, "-S"], PATH="/usr/bin:/bin")
    ranked = [ln for ln in out.splitlines() if ln.startswith("| knowledge-base") or ln.startswith("| [private]")]
    assert ranked, out
    assert all(ln.startswith("| [private] ") for ln in ranked), ranked
    rows = _freshness_rows(
        _script(FRESHNESS, vault, "--all", "--json", python=[sys.executable, "-S"], PATH="/usr/bin:/bin")
    )
    assert {r["visibility"] for r in rows.values()} == {"unknown"}


def test_vault_freshness_lists_nothing_for_an_agent_reader(vault: Path) -> None:
    out = _script(FRESHNESS, vault, "--all", "--json", SCOUT_READER=AGENT)
    assert json.loads(out)["files"] == []


def _recurring(vault: Path) -> Path:
    d = vault / "knowledge-base" / "recurring-tasks"
    d.mkdir(parents=True, exist_ok=True)
    for slug, vis in (("pay-rent", "visibility: private\n"), ("team-sync", "")):
        (d / f"{slug}.md").write_text(
            f"---\ntype: recurring_task\nname: {slug}\ncadence: daily\n{vis}---\n\n# {slug}\n", encoding="utf-8"
        )
    return d


def test_recurring_task_status_labels_rows(vault: Path) -> None:
    d = _recurring(vault)
    rows = {r["name"]: r for r in json.loads(_script(RECURRING, vault, "--dir", str(d), "--json", SCOUT_MODE="x"))}
    assert rows["pay-rent"]["visibility"] == "private"
    assert rows["team-sync"]["visibility"] == "normal"
    table = _script(RECURRING, vault, "--dir", str(d), SCOUT_MODE="x")
    assert "| [private] pay-rent |" in table
    assert "| team-sync |" in table


def test_recurring_task_status_lists_nothing_for_an_agent_reader(vault: Path) -> None:
    d = _recurring(vault)
    assert json.loads(_script(RECURRING, vault, "--dir", str(d), "--json", SCOUT_READER=AGENT)) == []


# ----- the enrichment-question generator (a vault-local script; test stand-in) ---------


def _enrichment_questions(vault: Path, reader: str) -> list[dict[str, str]]:
    """What a compliant vault enrichment generator does (spec §6.3): list people
    notes through the read gate, take each note's open questions, and tag each
    question with its source's visibility."""
    questions = []
    for rel, label in visible_files(vault, reader, ["knowledge-base/people/**"]):
        _label, data = read_file(vault, reader, rel)
        text = data.decode("utf-8")
        _, _, section = text.partition("## Open questions")
        for line in section.splitlines():
            if line.startswith("- "):
                questions.append({"question": line[2:], "source": rel, "visibility": label.visibility})
    return questions


def _route(policy: Any, question: dict[str, str]) -> list[str]:
    """Channels a question may go to: a private one only to owner-only surfaces."""
    candidates = ["telegram_private_chat", "vault", "slack"]
    if question["visibility"] == "normal":
        return candidates
    return [c for c in candidates if surface_class(policy, c) == "owner_only"]


def test_enrichment_question_from_a_private_note_is_routed_owner_only(vault: Path) -> None:
    policy = load_policy(vault)
    questions = _enrichment_questions(vault, "autonomous")
    sam = [q for q in questions if q["source"] == "knowledge-base/people/sam.md"]
    assert sam and all(q["visibility"] == "private" for q in sam)
    for q in sam:
        channels = _route(policy, q)
        assert channels
        assert all(surface_class(policy, c) == "owner_only" for c in channels)
        assert "slack" not in channels
    assert _enrichment_questions(vault, AGENT) == []


def test_the_stand_in_reads_only_through_the_gate() -> None:
    """The stand-in itself would pass the drift check: no raw KB reads."""
    import inspect

    src = inspect.getsource(_enrichment_questions)
    assert "rglob" not in src and "open(" not in src and "os.walk" not in src
