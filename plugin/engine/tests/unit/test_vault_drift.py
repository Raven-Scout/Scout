"""Unit tests for scout.scripts.vault_drift — how an upgrade treats a vault's
edits to the plugin-owned files it rewrites.

``decide`` is the decision table from the design
(docs/superpowers/specs/2026-09-30-upgrade-keeps-vault-edits-design.md) as a
pure function; ``reconcile`` applies it to a vault on disk; ``scan`` and
``resolve`` read and settle what an upgrade parked.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scout.scripts import vault_drift as vd
from scout.scripts.three_way_merge import MergeResult, MergeUnavailable

BASE = "#!/bin/bash\none\ntwo\nthree\nfour\nfive\n"
# The plugin changes line "two"; the vault changes line "five" — far apart.
NEW = BASE.replace("two\n", "two (plugin)\n")
EDITED = BASE.replace("five\n", "five (vault)\n")


# ---------------------------------------------------------------- decide ----


def test_a_missing_live_file_is_written() -> None:
    d = vd.decide(new=NEW, live=None, base=None)
    assert (d.outcome, d.live, d.snapshot) == ("written", NEW, NEW)


def test_a_live_file_equal_to_the_new_render_is_left_alone() -> None:
    d = vd.decide(new=NEW, live=NEW, base=BASE)
    assert (d.outcome, d.live, d.snapshot) == ("unchanged", None, NEW)


def test_an_unedited_file_takes_the_plugin_change() -> None:
    d = vd.decide(new=NEW, live=BASE, base=BASE)
    assert (d.outcome, d.live, d.snapshot) == ("updated", NEW, NEW)


def test_a_vault_edit_the_plugin_did_not_touch_is_kept() -> None:
    d = vd.decide(new=BASE, live=EDITED, base=BASE)
    assert (d.outcome, d.live, d.snapshot) == ("kept", None, BASE)


def test_a_vault_edit_and_a_plugin_change_in_different_places_merge() -> None:
    d = vd.decide(new=NEW, live=EDITED, base=BASE)
    assert d.outcome == "merged"
    assert d.live is not None
    assert "two (plugin)" in d.live and "five (vault)" in d.live
    assert d.snapshot == NEW


def test_a_merge_that_only_reproduces_the_plugin_change_is_a_plain_update() -> None:
    """The vault made the same fix the plugin then shipped (along with more):
    nothing of the vault's own is left, so there is nothing to report."""
    same_fix = BASE.replace("two\n", "two (plugin)\n")
    plugin_did_more = same_fix.replace("four\n", "four (plugin)\n")
    d = vd.decide(new=plugin_did_more, live=same_fix, base=BASE)
    assert (d.outcome, d.live) == ("updated", plugin_did_more)


def test_overlapping_changes_keep_the_vault_version_and_do_not_advance_the_base() -> None:
    clash = BASE.replace("two\n", "two (vault)\n")
    d = vd.decide(new=NEW, live=clash, base=BASE)
    assert d.outcome == "conflict"
    assert d.live is None  # the vault's working file stays in place
    assert d.snapshot is None  # the next upgrade retries the same merge
    assert d.merge_draft is not None and "<<<<<<<" in d.merge_draft


def test_a_merge_that_cannot_run_is_a_conflict_that_says_why() -> None:
    """Data-safe (the vault's version stays live), but the report must not send
    the user to hand-merge a file that only needs git installed."""

    def broken_merge(**_kw: str) -> MergeResult:
        raise MergeUnavailable("git is not installed")

    d = vd.decide(new=NEW, live=EDITED, base=BASE, merge=broken_merge)
    assert (d.outcome, d.live, d.snapshot, d.merge_draft) == ("conflict", None, None, None)
    assert d.reason is not None and "git is not installed" in d.reason


def test_a_bug_in_the_merge_is_not_mistaken_for_a_conflict() -> None:
    def buggy_merge(**_kw: str) -> MergeResult:
        raise RecursionError("maximum recursion depth exceeded")

    with pytest.raises(RecursionError):
        vd.decide(new=NEW, live=EDITED, base=BASE, merge=buggy_merge)


def test_a_conflict_report_carries_its_reason(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    vd.reconcile(vault, REL, BASE)
    (vault / REL).write_text(EDITED)

    def broken_merge(**_kw: str) -> MergeResult:
        raise MergeUnavailable("git is not installed")

    edit = vd.reconcile(vault, REL, NEW, merge=broken_merge)

    assert edit is not None and "git is not installed" in edit.describe()


def test_first_baseline_of_a_known_render_updates_silently() -> None:
    d = vd.decide(new=NEW, live=BASE, base=None, known_render=True)
    assert (d.outcome, d.live, d.snapshot) == ("updated", NEW, NEW)


def test_first_baseline_of_an_unknown_file_installs_the_plugin_version() -> None:
    d = vd.decide(new=NEW, live=EDITED, base=None)
    assert (d.outcome, d.live, d.snapshot) == ("replaced", NEW, NEW)


def test_first_baseline_of_an_unknown_vault_developed_file_keeps_the_vault_version() -> None:
    d = vd.decide(new=NEW, live=EDITED, base=None, vault_developed=True)
    assert (d.outcome, d.live, d.snapshot, d.merge_draft) == ("conflict", None, None, None)
    assert d.reason is not None and "no record" in d.reason


# ------------------------------------------------------------ signatures ----

TEMPLATE = 'SCOUT_DIR="{{SCOUT_DIR}}"\nexec "{{SCOUTCTL_BIN}}" tick  # {{INSTANCE_NAME}}\nliteral line\n'


def _render(scout_dir: str, bin_: str, name: str) -> str:
    return (
        TEMPLATE.replace("{{SCOUT_DIR}}", scout_dir)
        .replace("{{SCOUTCTL_BIN}}", bin_)
        .replace("{{INSTANCE_NAME}}", name)
    )


def test_a_render_matches_its_template_signature_whatever_the_variable_values() -> None:
    sig = vd.signature(TEMPLATE, rendered=True)
    assert vd.matches(_render("/Users/alex/Scout", "/opt/scout/.venv/bin/scoutctl", "Scout"), sig)
    # The plugin root moved (a marketplace version bump): still the same render.
    assert vd.matches(_render("/Users/alex/Scout", "/cache/0.9.0/.venv/bin/scoutctl", ""), sig)


def test_an_edited_render_does_not_match() -> None:
    sig = vd.signature(TEMPLATE, rendered=True)
    live = _render("/v", "/b", "S")
    assert not vd.matches(live.replace("literal line", "literal line (vault)"), sig)
    assert not vd.matches(live + "extra\n", sig)
    assert not vd.matches(live.replace(" tick", " tock"), sig)


def test_a_value_pinned_to_this_upgrade_must_match() -> None:
    """With no base, a var line is only "a render with other values" for the
    values that move on their own (the plugin root). A hand-edited value on a
    var line is an edit, and must not be overwritten silently."""
    sig = vd.signature(TEMPLATE, rendered=True)
    pinned = {"SCOUT_DIR": "/Users/alex/Scout", "INSTANCE_NAME": "Scout"}
    moved_root = _render("/Users/alex/Scout", "/cache/0.9.0/.venv/bin/scoutctl", "Scout")
    assert vd.matches(moved_root, sig, pinned=pinned)
    assert not vd.matches(_render("/Volumes/other/Scout", "/b", "Scout"), sig, pinned=pinned)
    assert not vd.matches(_render("/Users/alex/Scout", "/b", "Scout --debug"), sig, pinned=pinned)


def test_a_free_variable_must_have_one_value_throughout() -> None:
    sig = vd.signature("A={{X}}\nB={{X}}\n", rendered=True)
    assert vd.matches("A=1\nB=1\n", sig)
    assert not vd.matches("A=1\nB=2\n", sig)


def test_a_verbatim_file_signature_is_an_exact_hash() -> None:
    text = "x = '{{not a var}}'\n"
    sig = vd.signature(text, rendered=False)
    assert vd.matches(text, sig)
    assert not vd.matches("x = 'anything'\n", sig)


def test_signatures_round_trip_through_json() -> None:
    sig = vd.signature(TEMPLATE, rendered=True)
    assert vd.Signature.from_json(sig.to_json()) == sig


def test_the_shipped_render_history_is_well_formed() -> None:
    history = vd.load_render_history()
    assert history, "render-history.json is empty or missing"
    for rel, sigs in history.items():
        assert not rel.startswith("templates/"), f"keyed by plugin path, not vault path: {rel}"
        assert sigs and all(isinstance(s, vd.Signature) for s in sigs)


def test_an_unreadable_render_history_says_so(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Without the history every older release's file reads as "replaced" —
    a flood of false reports — so the cause must be visible."""
    missing = tmp_path / "render-history.json"

    assert vd.load_render_history(missing) == {}

    err = capsys.readouterr().err
    assert "render history" in err and str(missing) in err


def test_a_malformed_history_entry_drops_only_itself(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    good = vd.signature("A={{X}}\n", rendered=True).to_json()
    path = tmp_path / "render-history.json"
    path.write_text(json.dumps({"files": {"a.sh": [good, {"lines": 2}], "b.sh": [good]}}), encoding="utf-8")

    history = vd.load_render_history(path)

    assert [len(history["a.sh"]), len(history["b.sh"])] == [1, 1]
    assert "a.sh" in capsys.readouterr().err


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "render-history"
REPO = Path(__file__).resolve().parents[3]


def test_the_shipped_history_recognises_a_real_older_release_render() -> None:
    """heartbeat.sh as v0.11.0 shipped it, rendered for some vault, must be
    recognised: that is what lets a vault last upgraded by v0.11.0 take its
    first baseline without a false "replaced" report."""
    old = (FIXTURES / "heartbeat.sh.v0.11.0.tmpl").read_text(encoding="utf-8")
    live = old.replace("{{SCOUT_DIR}}", "/Users/alex/Scout").replace("{{TIMEZONE}}", "Europe/Prague")
    live = live.replace("{{SCOUTCTL_BIN}}", "/cache/scout/0.11.0/.venv/bin/scoutctl").replace(
        "{{INSTANCE_NAME}}", "Scout"
    )
    sigs = vd.load_render_history()["scripts/heartbeat.sh"]
    assert any(vd.matches(live, s) for s in sigs)
    assert not any(vd.matches(live + "# vault edit\n", s) for s in sigs)


def _has_release_tags() -> bool:
    # The generator reads the plugin's own pre-monorepo v* release tags. In a
    # full Raven-Scout/Scout clone (the former scout-plugin repo) those are its
    # bare v* tags; a clone of the old app repo (Raven-Scout/scout-app-legacy)
    # has bare v* tags of the same names (v0.11.0 included) that belong to the
    # macOS app — so require that the tag carries the plugin tree, not merely
    # that a tag of that name exists.
    import subprocess

    out = subprocess.run(
        ["git", "-C", str(REPO), "cat-file", "-e", "v0.11.0:engine/scout/__init__.py"],
        capture_output=True,
        text=True,
    )
    return out.returncode == 0


@pytest.mark.skipif(
    not _has_release_tags(), reason="needs the plugin's bare v* release tags (a full Raven-Scout/Scout clone)"
)
def test_the_shipped_history_is_what_the_generator_builds() -> None:
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "gen-render-history.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr


# -------------------------------------------------------------- reconcile ----


def _vault(tmp_path: Path) -> Path:
    v = tmp_path / "Scout"
    (v / ".scout-state").mkdir(parents=True)
    return v


REL = "scripts/heartbeat.sh"


def test_reconcile_records_a_snapshot_for_a_fresh_file(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    assert vd.reconcile(vault, REL, NEW) is None
    assert (vault / REL).read_text() == NEW
    assert vd.snapshot_path(vault, REL).read_text() == NEW


def test_reconcile_keeps_a_vault_edit_and_reports_it(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    vd.reconcile(vault, REL, BASE)
    (vault / REL).write_text(EDITED)

    edit = vd.reconcile(vault, REL, BASE)

    assert edit == vd.VaultEdit(REL, "kept")
    assert (vault / REL).read_text() == EDITED


def test_reconcile_parks_the_plugin_version_on_conflict(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    vd.reconcile(vault, REL, BASE)
    clash = BASE.replace("two\n", "two (vault)\n")
    (vault / REL).write_text(clash)

    edit = vd.reconcile(vault, REL, NEW)

    plugin = vault / ".scout-state" / "drift" / f"{REL}.plugin"
    draft = vault / ".scout-state" / "drift" / f"{REL}.merge"
    assert edit == vd.VaultEdit(REL, "conflict", (str(plugin.relative_to(vault)), str(draft.relative_to(vault))))
    assert (vault / REL).read_text() == clash
    assert plugin.read_text() == NEW
    assert "<<<<<<< plugin" in draft.read_text() and ">>>>>>> vault" in draft.read_text()
    assert vd.snapshot_path(vault, REL).read_text() == BASE


def test_reconcile_clears_a_conflict_once_it_is_gone(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    vd.reconcile(vault, REL, BASE)
    (vault / REL).write_text(BASE.replace("two\n", "two (vault)\n"))
    vd.reconcile(vault, REL, NEW)

    # The user takes the plugin's version.
    (vault / REL).write_text(NEW)
    assert vd.reconcile(vault, REL, NEW) is None

    assert not (vault / ".scout-state" / "drift" / f"{REL}.plugin").exists()
    assert not (vault / ".scout-state" / "drift" / f"{REL}.merge").exists()
    assert vd.snapshot_path(vault, REL).read_text() == NEW


def test_reconcile_parks_an_unknown_file_on_first_baseline(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    (vault / "scripts").mkdir()
    (vault / REL).write_text(EDITED)

    edit = vd.reconcile(vault, REL, NEW)

    parked = vault / ".scout-state" / "drift" / f"{REL}.vault"
    assert edit == vd.VaultEdit(REL, "replaced", (str(parked.relative_to(vault)),))
    assert parked.read_text() == EDITED
    assert (vault / REL).read_text() == NEW


def test_reconcile_never_overwrites_an_earlier_parked_copy(tmp_path: Path) -> None:
    """Two first baselines (say the snapshot dir was deleted in between) must
    not lose the first parked copy — the same promise the dated .bak names made (#62)."""
    vault = _vault(tmp_path)
    (vault / "scripts").mkdir()
    (vault / REL).write_text("first edit\n")
    vd.reconcile(vault, REL, NEW)
    vd.snapshot_path(vault, REL).unlink()
    (vault / REL).write_text("second edit\n")

    edit = vd.reconcile(vault, REL, NEW)

    drift = vault / ".scout-state" / "drift" / "scripts"
    assert (drift / "heartbeat.sh.vault").read_text() == "first edit\n"
    assert (drift / "heartbeat.sh.vault-1").read_text() == "second edit\n"
    assert edit is not None and edit.parked == (".scout-state/drift/scripts/heartbeat.sh.vault-1",)


def test_reconcile_updates_a_known_render_silently_on_first_baseline(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    (vault / "scripts").mkdir()
    old = _render("/v", "/old/bin/scoutctl", "Scout")
    (vault / REL).write_text(old)

    edit = vd.reconcile(vault, REL, NEW, signatures=[vd.signature(TEMPLATE, rendered=True)])

    assert edit is None
    assert (vault / REL).read_text() == NEW
    assert not (vault / ".scout-state" / "drift").exists()


def test_reconcile_carries_over_a_legacy_merge_snapshot(tmp_path: Path) -> None:
    """parser.py's old 3-way merge kept its base under last-assembled/; it is
    the exact base, so it moves to last-rendered/ instead of a first baseline."""
    vault = _vault(tmp_path)
    rel = "knowledge-base/ontology/parser.py"
    legacy = vault / ".scout-state" / "last-assembled" / rel
    legacy.parent.mkdir(parents=True)
    legacy.write_text(BASE)
    (vault / rel).parent.mkdir(parents=True)
    (vault / rel).write_text(EDITED)

    edit = vd.reconcile(vault, rel, NEW, vault_developed=True)

    assert edit is not None and edit.outcome == "merged"
    assert "five (vault)" in (vault / rel).read_text()
    assert vd.snapshot_path(vault, rel).read_text() == NEW
    assert not legacy.exists()


# ------------------------------------------------------------------- scan ----


def test_scan_of_an_unedited_vault_is_empty(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    vd.reconcile(vault, REL, NEW)
    assert vd.scan(vault) == []


def test_scan_reports_edits_conflicts_and_parked_copies(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    vd.reconcile(vault, "run-scout.sh", BASE)
    (vault / "run-scout.sh").write_text(EDITED)  # edited
    vd.reconcile(vault, REL, BASE)
    (vault / REL).write_text(BASE.replace("two\n", "two (vault)\n"))
    vd.reconcile(vault, REL, NEW)  # conflict
    (vault / "hooks").mkdir()
    (vault / "hooks/kb-pre-filter.sh").write_text("old\n")
    vd.reconcile(vault, "hooks/kb-pre-filter.sh", NEW)  # replaced

    by_status = {(e.status, e.path) for e in vd.scan(vault)}

    assert by_status == {
        ("edited", "run-scout.sh"),
        ("conflict", REL),
        ("replaced", "hooks/kb-pre-filter.sh"),
    }


def test_a_leftover_merge_draft_does_not_hide_an_edit(tmp_path: Path) -> None:
    """A .merge whose .plugin is gone (deleted by hand, or a crash between the
    two removals) is not a conflict, and must not mask the file's real state."""
    vault = _conflicted(tmp_path)
    (vault / ".scout-state" / "drift" / f"{REL}.plugin").unlink()

    statuses = {(e.path, e.status) for e in vd.scan(vault)}

    assert (REL, "edited") in statuses


def test_a_conflict_is_stale_only_when_a_newer_update_exists(tmp_path: Path) -> None:
    """After a conflict the base stays old on purpose; that alone must not
    tell the user to upgrade again."""
    vault = _conflicted(tmp_path)

    [same] = vd.report(vault, {REL: NEW})
    [newer] = vd.report(vault, {REL: NEW + "six (plugin)\n"})

    assert (same.status, same.stale) == ("conflict", False)
    assert newer.stale


# ---------------------------------------------------------------- resolve ----


def _conflicted(tmp_path: Path) -> Path:
    vault = _vault(tmp_path)
    vd.reconcile(vault, REL, BASE)
    (vault / REL).write_text(BASE.replace("two\n", "two (vault)\n"))
    vd.reconcile(vault, REL, NEW)
    return vault


def test_resolve_takes_the_parked_plugin_version_as_the_new_base(tmp_path: Path) -> None:
    vault = _conflicted(tmp_path)
    (vault / REL).write_text(NEW.replace("five\n", "five (vault)\n"))  # merged by hand

    vd.resolve(vault, REL)

    assert vd.snapshot_path(vault, REL).read_text() == NEW
    assert not (vault / ".scout-state" / "drift" / f"{REL}.plugin").exists()
    assert not (vault / ".scout-state" / "drift" / f"{REL}.merge").exists()
    # The next upgrade with the same plugin keeps the hand merge as the vault's edit.
    assert vd.reconcile(vault, REL, NEW) == vd.VaultEdit(REL, "kept")


def test_resolve_refuses_a_file_that_still_has_conflict_markers(tmp_path: Path) -> None:
    vault = _conflicted(tmp_path)
    draft = vault / ".scout-state" / "drift" / f"{REL}.merge"
    (vault / REL).write_text(draft.read_text())

    with pytest.raises(ValueError, match="conflict markers"):
        vd.resolve(vault, REL)
    assert (vault / ".scout-state" / "drift" / f"{REL}.plugin").exists()


def test_resolve_refuses_a_conflict_nobody_merged(tmp_path: Path) -> None:
    """Clearing a yellow doctor by reflex must not drop the plugin's update for
    good: --resolve checks the update's lines are in the file."""
    vault = _conflicted(tmp_path)

    with pytest.raises(ValueError, match="doesn't have the parked update"):
        vd.resolve(vault, REL)
    assert (vault / ".scout-state" / "drift" / f"{REL}.plugin").exists()


def _parked(tmp_path: Path, *, base: str | None, update: str, live: str) -> Path:
    """A vault with a conflict parked for REL: ``update`` waiting, ``live`` in place."""
    vault = _vault(tmp_path)
    if base is not None:
        snap = vd.snapshot_path(vault, REL)
        snap.parent.mkdir(parents=True, exist_ok=True)
        snap.write_text(base)
    (vault / REL).parent.mkdir(parents=True, exist_ok=True)
    (vault / REL).write_text(live)
    plugin = vault / ".scout-state" / "drift" / f"{REL}.plugin"
    plugin.parent.mkdir(parents=True, exist_ok=True)
    plugin.write_text(update)
    return vault


def test_resolve_refuses_while_a_line_the_update_deletes_is_still_there(tmp_path: Path) -> None:
    """An update that only removes a line is still an update: resolving without
    taking it would keep the removed line for good."""
    base = '#!/bin/bash\none\nrm -rf "$TMP"\ntwo\nthree\n'
    update = base.replace('rm -rf "$TMP"\n', "")
    unmerged = base.replace("two\n", "two (vault)\n")
    vault = _parked(tmp_path, base=base, update=update, live=unmerged)

    with pytest.raises(ValueError, match="doesn't have the parked update"):
        vd.resolve(vault, REL)

    (vault / REL).write_text(update.replace("two\n", "two (vault)\n"))  # merged by hand
    vd.resolve(vault, REL)
    assert vd.snapshot_path(vault, REL).read_text() == update


def test_resolve_wants_an_added_line_where_the_update_put_it_not_just_anywhere(tmp_path: Path) -> None:
    """A short added line (``fi``) that the file already has elsewhere is not
    proof the update was merged."""
    base = "#!/bin/bash\nif a; then\n  x\nfi\nif b; then\n  y\n"
    update = base + "fi\n"
    unmerged = base.replace("  x\n", "  x (vault)\n").replace("  y\n", "  y (vault)\n")
    vault = _parked(tmp_path, base=base, update=update, live=unmerged)

    with pytest.raises(ValueError, match="doesn't have the parked update"):
        vd.resolve(vault, REL)

    (vault / REL).write_text(unmerged + "fi\n")
    vd.resolve(vault, REL)


def test_resolve_matches_whole_lines_not_substrings(tmp_path: Path) -> None:
    base = "#!/bin/bash\none\ntwo\n"
    update = base + "done\n"
    unmerged = "#!/bin/bash\none (vault)\ntwo\necho undone\n"
    vault = _parked(tmp_path, base=base, update=update, live=unmerged)

    with pytest.raises(ValueError, match="doesn't have the parked update"):
        vd.resolve(vault, REL)


def test_resolve_ignores_trailing_whitespace_in_a_hand_merge(tmp_path: Path) -> None:
    base = "#!/bin/bash\none\ntwo\n"
    update = base + "three\n"
    merged = "#!/bin/bash\none (vault)\ntwo\nthree   \n"
    vault = _parked(tmp_path, base=base, update=update, live=merged)

    vd.resolve(vault, REL)

    assert vd.snapshot_path(vault, REL).read_text() == update


def test_resolve_with_no_recorded_base_needs_drop_update(tmp_path: Path) -> None:
    """With no base there is no telling whether the update was merged, so a
    plain --resolve must not record it as merged."""
    vault = _parked(tmp_path, base=None, update="plugin version with the fix\n", live="vault version\n")

    with pytest.raises(ValueError, match="--drop-update"):
        vd.resolve(vault, REL)
    assert (vault / ".scout-state" / "drift" / f"{REL}.plugin").exists()

    vd.resolve(vault, REL, drop_update=True)
    assert not (vault / ".scout-state" / "drift" / f"{REL}.plugin").exists()


def test_vault_developed_files_match_the_bootstrap_table() -> None:
    """The doctor and drift read the vault-developed set from here; bootstrap
    from _CAT_MERGE_FILES. They must name the same files."""
    from scout.scripts.bootstrap import _CAT_MERGE_FILES

    assert set(_CAT_MERGE_FILES) == vd.VAULT_DEVELOPED_FILES


def test_resolve_can_drop_the_update_on_purpose(tmp_path: Path) -> None:
    vault = _conflicted(tmp_path)
    mine = (vault / REL).read_text()

    vd.resolve(vault, REL, drop_update=True)

    assert vd.reconcile(vault, REL, NEW) == vd.VaultEdit(REL, "kept")
    assert (vault / REL).read_text() == mine


def test_resolve_refuses_when_the_file_is_missing(tmp_path: Path) -> None:
    vault = _conflicted(tmp_path)
    (vault / REL).unlink()

    with pytest.raises(ValueError, match="missing"):
        vd.resolve(vault, REL)


def test_resolve_dismisses_parked_vault_copies(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    (vault / "scripts").mkdir()
    (vault / REL).write_text(EDITED)
    vd.reconcile(vault, REL, NEW)

    vd.resolve(vault, REL)

    assert vd.scan(vault) == []


def test_resolve_with_nothing_parked_is_an_error(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    vd.reconcile(vault, REL, NEW)
    with pytest.raises(ValueError, match="nothing to resolve"):
        vd.resolve(vault, REL)


# ---------------------------------------------------------------- report ----


def test_report_counts_an_edit_against_the_plugins_render(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    vd.reconcile(vault, REL, BASE)
    (vault / REL).write_text(EDITED)

    [row] = vd.report(vault, {REL: BASE})

    assert (row.status, row.added, row.removed, row.stale) == ("edited", 1, 1, False)
    assert "+five (vault)" in row.diff.splitlines()


def test_report_flags_a_file_the_plugin_changed_since_the_last_upgrade(tmp_path: Path) -> None:
    """A patch made now would revert the plugin's change, so drift --patch skips it."""
    vault = _vault(tmp_path)
    vd.reconcile(vault, REL, BASE)
    (vault / REL).write_text(EDITED)

    [row] = vd.report(vault, {REL: NEW})

    assert row.stale
    assert "run `scoutctl bootstrap upgrade`" in row.describe()


@pytest.mark.parametrize("rel", ["../outside.sh", "/etc/hosts", "scripts/../../outside.sh"])
def test_resolve_rejects_a_path_outside_the_vault(tmp_path: Path, rel: str) -> None:
    """--resolve takes a path from the command line; it must not reach past the vault."""
    vault = _vault(tmp_path)
    (vault / ".scout-state" / "drift").mkdir(parents=True)
    (tmp_path / "outside.sh.plugin").write_text("x\n")  # what a ../ path would find
    with pytest.raises(ValueError, match="vault-relative"):
        vd.resolve(vault, rel)
    assert not (tmp_path / "outside.sh").exists()


def test_a_crash_after_parking_recovers_without_losing_or_duplicating_the_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = _vault(tmp_path)
    (vault / "scripts").mkdir()
    (vault / REL).write_text(EDITED)
    real_write = vd._write

    def crash_on_live(path: Path, content: str) -> None:
        if path == vault / REL:
            raise OSError("disk full")
        real_write(path, content)

    monkeypatch.setattr(vd, "_write", crash_on_live)
    with pytest.raises(OSError):
        vd.reconcile(vault, REL, NEW)
    monkeypatch.setattr(vd, "_write", real_write)

    edit = vd.reconcile(vault, REL, NEW)

    assert edit is not None and edit.parked == (f".scout-state/drift/{REL}.vault",)
    assert (vault / ".scout-state" / "drift" / f"{REL}.vault").read_text() == EDITED
    assert not (vault / ".scout-state" / "drift" / f"{REL}.vault-1").exists()
    assert (vault / REL).read_text() == NEW


def test_a_crash_while_parking_leaves_the_edited_file_in_place(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The copy is parked before the file is replaced: if parking fails, the
    vault's edit must still be live, never already overwritten."""
    vault = _vault(tmp_path)
    (vault / "scripts").mkdir()
    (vault / REL).write_text(EDITED)
    real_write = vd._write

    def crash_on_park(path: Path, content: str) -> None:
        if path.name.endswith(".vault"):
            raise OSError("disk full")
        real_write(path, content)

    monkeypatch.setattr(vd, "_write", crash_on_park)
    with pytest.raises(OSError):
        vd.reconcile(vault, REL, NEW)

    assert (vault / REL).read_text() == EDITED
