"""What the layout move does to Scout's own state (spec 2026-11-26 §4.8, §4.5 step 10): brain snapshots and
their provenance, merge bases of unedited managed files, label parity finishing forward, binary files."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scout import layout_migrate
from scout.kb import privacy
from scout.scripts import brain_merge, vault_drift
from tests.unit.layout_v1_vault import build_v1_vault

SPACES = "spaces:\n  - key: work\n    default: true\n  - key: personal\n    visibility: private\n"
PLUGIN_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))


def _commit(vault: Path) -> None:
    subprocess.run(["git", "-C", str(vault), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(vault), "-c", "commit.gpgsign=false", "commit", "-qm", "x"], check=True)


def test_brain_snapshots_are_rewritten_and_their_provenance_follows_only_where_it_matched(tmp_path: Path) -> None:
    vault = build_v1_vault(tmp_path / "v", spaces=SPACES, dirty=False)
    snap = vault / ".scout-state" / "last-assembled"
    (snap / "proposed").mkdir(parents=True)
    text = "# SKILL\n\nRun `scripts/scoutctl kb lint`.\n"
    (snap / "SKILL.md").write_text(text)
    (snap / "DREAMING.md").write_text("# DREAMING\n\nscripts/heartbeat.sh\n")
    (snap / "proposed" / "SKILL.md").write_text(text)
    records = {
        "SKILL.md": brain_merge.Provenance.assembled(text),
        "DREAMING.md": brain_merge.Provenance.assembled("something else"),  # doesn't match its snapshot
    }
    (snap / brain_merge.PROVENANCE_FILE).write_text(brain_merge.dumps_provenance(records))
    assert layout_migrate.migrate_layout(vault).state == "applied"
    new = (snap / "SKILL.md").read_text()
    assert "scout/scripts/scoutctl" in new and "scout/scripts/scoutctl" in (snap / "proposed" / "SKILL.md").read_text()
    after = brain_merge.load_provenance(snap)
    assert after["SKILL.md"] == brain_merge.Provenance.assembled(new)  # re-recorded: it matched before
    assert after["DREAMING.md"] == records["DREAMING.md"]  # left alone: it didn't


def test_an_unedited_managed_file_gets_a_base_with_the_same_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = build_v1_vault(tmp_path / "v", spaces=SPACES, dirty=False)
    hook = (vault / "hooks/kb-pre-filter.sh").read_text()
    monkeypatch.setattr(
        vault_drift,
        "load_render_history",
        lambda: {"hooks/kb-pre-filter.sh": [vault_drift.signature(hook, rendered=False)]},
    )
    plan = layout_migrate.plan(vault)
    layout_migrate.apply(vault, plan, plugin_root=PLUGIN_ROOT)
    base = vault_drift.snapshot_path(vault, "scout/hooks/kb-pre-filter.sh")
    live = vault / "scout/hooks/kb-pre-filter.sh"
    assert base.read_text() == live.read_text()  # equal before, equal after
    assert "$SCOUT_DIR/scout/scripts/scoutctl" in live.read_text()


def test_parity_still_holds_if_a_note_slips_past_the_preconditions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spaces = SPACES + "  - key: svj\n"  # normal: the svj-* notes would leave personal/'s private default
    vault = build_v1_vault(tmp_path / "v", spaces=spaces, dirty=False)
    monkeypatch.setattr(layout_migrate, "parity_problem", lambda vault, plan: None)
    out = layout_migrate.migrate_layout(vault)
    assert out.state == "applied"
    policy = privacy.load_policy(vault)
    assert privacy.label_file(vault, "knowledge-base/svj/svj-board.md", policy).visibility == "private"
    assert "visibility: private" in (vault / "knowledge-base/svj/svj-board.md").read_text()
    assert "Notes made private to keep their label" in (vault / out.report).read_text()  # type: ignore[operator]


def test_a_note_that_becomes_more_private_is_reported(tmp_path: Path) -> None:
    overlay = "privacy:\n  visibility_defaults:\n    - glob: knowledge-base/work/topics/**\n      visibility: private\n"
    vault = build_v1_vault(tmp_path / "v", spaces=SPACES + overlay, dirty=False)
    out = layout_migrate.migrate_layout(vault)
    report = (vault / out.report).read_text()  # type: ignore[operator]
    section = report.split("## Notes that became more private", 1)[1].split("##", 1)[0]
    assert "knowledge-base/work/topics/cooking.md" in section


def test_binary_and_non_utf8_files_are_moved_but_never_rewritten(tmp_path: Path) -> None:
    vault = build_v1_vault(tmp_path / "v", spaces=SPACES, dirty=False)
    (vault / "scripts/blob.bin").write_bytes(b"scripts/x\0\x01\x02")
    (vault / "scripts/latin1.sh").write_bytes("# caf\xe9 scripts/x\n".encode("latin-1"))
    _commit(vault)
    assert layout_migrate.migrate_layout(vault).state == "applied"
    assert (vault / "scout/scripts/blob.bin").read_bytes() == b"scripts/x\0\x01\x02"
    assert (vault / "scout/scripts/latin1.sh").read_bytes() == "# caf\xe9 scripts/x\n".encode("latin-1")


def test_a_config_line_still_naming_an_old_folder_is_listed_as_left() -> None:
    from scout.layout_rewrite import Ref, unplaced_globs

    refs = [Ref("knowledge-base/projects", "knowledge-base/{space}/projects", True, True)]
    text = (
        'a:\n  # knowledge-base/projects/x\n  - "knowledge-base/projects/**"\n'
        "  - knowledge-base/projects-old\n  b: x/knowledge-base/projects\n"
    )
    # comments, a longer folder name and a path inside another one are not the old folder
    assert unplaced_globs(text, refs) == ['- "knowledge-base/projects/**"']
