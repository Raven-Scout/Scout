"""``privacy.sync_only``: the fail-closed upload allowlist for ``scoutctl repo``.

Same harness as ``test_repo_sync``: a fake host over a local bare repository and
the anonymised privacy fixture vault, so git runs for real. The allowlist here is
``knowledge-base/projects/**``; everything else in the vault stays home. Leak
checks grep the mirror's whole history (trees, blobs and messages), because a name
in a commit message or the policy document is as much on the server as a file.
"""

# ruff: noqa: F811  (every test takes the vault and host fixtures imported below)

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from scout import repo_sync as rs
from scout.cli import app
from scout.kb import share
from scout.kb.privacy import CLASSIFIER_VERSION, classify, load_policy, policy_from_config_text, policy_from_overlay
from scout.scripts import privacy_migrate

from .privacy_vault_helpers import git
from .test_repo_sync import FakeHost, _bare_git, _commit, _commit_on_mirror, _link, mirror_history_paths, mirror_policy
from .test_repo_sync import host as host  # noqa: F401  (fixture)
from .test_repo_sync import vault as vault  # noqa: F401  (fixture)

ALLOWED = "knowledge-base/projects/**"
WIDGET = "knowledge-base/projects/widget/widget.md"
HOME = "knowledge-base/topics/misc/plain.md"
AGNES_NOTE = "knowledge-base/topics/from-agnes.md"


def _rules(*entries: str, host_entries: tuple[str, ...] = ()) -> rs.LocalOnlyRules:
    return rs.LocalOnlyRules.build([policy_from_overlay({"sync_only": list(entries)})], host_entries)


def _write_privacy(vault: Path, **keys: Any) -> None:
    """Set keys in the config's ``privacy:`` block (a value of None removes the key), uncommitted."""
    config = vault / "scout-config.yaml"
    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    for key, value in keys.items():
        if value is None:
            data["privacy"].pop(key, None)
        else:
            data["privacy"][key] = value
    config.write_text(yaml.safe_dump(data), encoding="utf-8")


def _set_privacy(vault: Path, subject: str = "config edit", **keys: Any) -> None:
    _write_privacy(vault, **keys)
    git(vault, "add", "-A")
    git(vault, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", subject)


def _linked(vault: Path, host: FakeHost, *entries: str, **kw: Any) -> rs.Result:
    _set_privacy(vault, sync_only=list(entries or (ALLOWED,)))
    return _link(vault, host, **kw)


def _mirror_text(host: FakeHost) -> str:
    """Every path, blob and commit message reachable from the mirror's main."""
    return _bare_git(host.bare, "log", "--all", "--format=%B", "-p", "--full-diff", "--root")


def _tip_paths(host: FakeHost) -> set[str]:
    return set(_bare_git(host.bare, "ls-tree", "-r", "--name-only", "main").splitlines())


# ----- the rule ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rel", "refused"),
    [
        ("knowledge-base/projects/widget/widget.md", False),
        ("knowledge-base/topics/misc/plain.md", True),
        ("inbox.md", True),
        ("scout-config.yaml", True),
        # Allowed by the host's plumbing list, kept home by the allowlist.
        (".scout-state/schedule.yaml", True),
        (".scout-state/id-map.json", True),
        # The one exception: every mirror commit must carry it.
        (rs.POLICY_PATH, False),
        # The allowlist never overrides a Local-Only or secret rule.
        ("knowledge-base/projects/widget/.env", True),
        ("knowledge-base/projects/widget/key.pem", True),
        # Matched as written: a spelling the list doesn't name stays home.
        ("Knowledge-Base/Projects/widget/widget.md", True),
    ],
)
def test_refused_outside_the_allowlist(rel: str, refused: bool) -> None:
    assert _rules(ALLOWED).refused(rel) is refused


def test_a_catch_all_allowlist_still_keeps_plumbing_and_secrets_home() -> None:
    rules = _rules("**")
    assert rules.refused(".claude/settings.json")
    assert rules.refused(".env")
    assert rules.refused(".scout-logs/run.log")
    assert not rules.refused("inbox.md")


@pytest.mark.parametrize("entry", ["notes", "notes/", "./notes", "notes/**"])
def test_a_folder_can_be_named_as_a_plain_path_or_a_glob(entry: str) -> None:
    rules = _rules(entry)
    assert not rules.refused("notes/a/b.md")
    assert rules.refused("notes-old/b.md")


def test_a_local_only_entry_inside_the_allowlist_wins() -> None:
    policy = policy_from_overlay({"sync_only": ["notes/**"], "local_only_add": ["notes/closed/**"]})
    rules = rs.LocalOnlyRules.build([policy], ())
    assert rules.refused("notes/closed/x.md")
    assert not rules.refused("notes/open/x.md")


def test_every_list_must_allow_a_path() -> None:
    wide = policy_from_overlay({"sync_only": ["a/**", "b/**"]})
    narrow = policy_from_overlay({"sync_only": ["a/**"]})
    rules = rs.LocalOnlyRules.build([wide], ()).with_policy(narrow)
    assert not rules.refused("a/x.md")
    assert rules.refused("b/x.md")


def test_a_policy_without_the_key_does_not_widen_the_rules() -> None:
    rules = _rules(ALLOWED).with_policy(policy_from_overlay({}))
    assert rules.refused(HOME)


def test_no_key_means_no_allowlist() -> None:
    rules = rs.LocalOnlyRules.build([policy_from_overlay({})], ())
    assert not rules.sync_only
    assert not rules.refused(HOME)


# ----- the key -----------------------------------------------------------------------------


def test_the_key_is_carried_in_the_effective_policy_only_when_set() -> None:
    assert policy_from_overlay({}).sync_only is None
    assert "sync_only" not in policy_from_overlay({}).to_json()
    policy = policy_from_overlay({"sync_only": [" ./notes/** ", "a.md"]})
    assert policy.sync_only == ("notes/**", "a.md")
    assert policy.to_json()["sync_only"] == ["notes/**", "a.md"]
    assert policy_from_overlay({"sync_only": []}).sync_only == ()


@pytest.mark.parametrize("value", [None, "notes/**", 5, {"a": 1}, ["notes/**", 5], [""], ["/etc/**"], ["a/../b"]])
def test_a_bad_value_uploads_nothing_and_warns(value: object) -> None:
    policy = policy_from_overlay({"sync_only": value})
    assert policy.sync_only == ()
    assert "privacy-sync-only-invalid" in policy.warnings
    assert rs.LocalOnlyRules.build([policy], ()).refused(WIDGET)


def test_the_key_still_narrows_while_the_file_does_not_load() -> None:
    policy = policy_from_config_text('bad: 2026-13-45\nprivacy:\n  sync_only: ["notes/**"]\n')
    assert "privacy-overlay-unreadable" in policy.warnings
    assert policy.sync_only == ("notes/**",)


def test_labels_do_not_depend_on_the_key() -> None:
    text = "---\nvisibility: normal\n---\nBody.\n"
    plain, scoped = policy_from_overlay({}), policy_from_overlay({"sync_only": ["notes/**"]})
    assert classify(HOME, text, plain) == classify(HOME, text, scoped)
    assert CLASSIFIER_VERSION == 4  # sync_only never bumped it; 4 is #550's NoteFacts.shared_source


# ----- link: what goes up ------------------------------------------------------------------


def test_link_mirrors_only_the_allowlist(vault: Path, host: FakeHost) -> None:
    result = _linked(vault, host)

    assert mirror_history_paths(host.bare) == {
        rs.POLICY_PATH,
        "knowledge-base/projects/widget/notes.md",
        WIDGET,
    }
    assert result.stripped > 0
    state = rs.load_state(vault)
    assert state is not None and state.sync_only == [ALLOWED]


def test_link_sends_no_name_outside_the_allowlist(vault: Path, host: FakeHost) -> None:
    _set_privacy(
        vault,
        sync_only=[ALLOWED],
        local_only_add=["trading/**", "closed-client/**", "knowledge-base/projects/widget/closed/**"],
        visibility_defaults=[
            {"glob": "other-client/**", "visibility": "private"},
            {"glob": "knowledge-base/projects/widget/notes.md", "visibility": "private"},
        ],
        shared_files_add=["named-draft/**"],
        federation_ceilings=[{"glob": "ceiling-client/**", "ceiling": "none"}],
        federation_defaults=[{"glob": "default-client/**", "scope": ["person:priya@example.com"]}],
    )
    _link(vault, host)

    text = _mirror_text(host)
    for name in ("trading", "closed-client", "other-client", "named-draft", "ceiling-client", "default-client", "Raw/"):
        assert name not in text, name
    assert host.local_only == ("knowledge-base/projects/widget/closed/",)

    doc = mirror_policy(host.bare)
    assert doc["host_local_only_paths"] == ["knowledge-base/projects/widget/closed/"]
    assert doc["policy"]["local_only"]["default"] == ["knowledge-base/projects/widget/closed/**"]
    assert doc["policy"]["sync_only"] == [ALLOWED]
    assert {lb["path"] for lb in doc["labels"]} == _tip_paths(host) - {rs.POLICY_PATH}


def test_agnes_rebuilds_the_same_labels_from_the_cut_down_policy(vault: Path, host: FakeHost) -> None:
    """The indexer classifies each file from the document, so cutting it down must not change a label."""
    _set_privacy(
        vault,
        sync_only=[ALLOWED],
        visibility_defaults=[
            {"glob": "**/notes.md", "visibility": "private"},
            {"glob": "other-client/**", "visibility": "private"},
        ],
    )
    _link(vault, host)

    doc = mirror_policy(host.bare)
    assert {"glob": "**/notes.md", "visibility": "private"} in doc["policy"]["visibility_defaults"]
    rebuilt = policy_from_overlay(
        {"visibility_defaults": [e for e in doc["policy"]["visibility_defaults"] if e["glob"] == "**/notes.md"]}
    )
    by_path = {lb["path"]: lb for lb in doc["labels"]}
    for rel, label in by_path.items():
        blob = _bare_git(host.bare, "show", f"main:{rel}")
        assert classify(rel, blob, rebuilt).visibility == label["visibility"]
    assert by_path["knowledge-base/projects/widget/notes.md"]["visibility"] == "private"


def test_an_ordinary_vault_keeps_its_whole_policy_document(vault: Path, host: FakeHost) -> None:
    _set_privacy(vault, federation_defaults=[{"glob": "trading/**", "scope": "none"}])
    _link(vault, host)
    doc = mirror_policy(host.bare)
    assert doc["policy"] == load_policy(vault).to_json()
    assert "sync_only" not in doc["policy"]
    assert "trading/" in doc["host_local_only_paths"]


def test_link_refuses_a_bad_allowlist(vault: Path, host: FakeHost) -> None:
    _set_privacy(vault, sync_only="knowledge-base/projects/**")
    with pytest.raises(rs.RepoSyncError) as e:
        _link(vault, host)
    assert e.value.code == "sync_only_invalid"
    assert not host.exists


def test_link_on_top_of_old_history_says_that_history_stays(vault: Path, host: FakeHost) -> None:
    _link(vault, host)
    rs.unlink(vault, None)
    _set_privacy(vault, sync_only=[ALLOWED])
    result = _link(vault, host, replace_remote=True)
    assert any("stays there" in n for n in result.notes)


# ----- sync: messages ----------------------------------------------------------------------


def test_a_mirror_commit_never_copies_a_laptop_subject(vault: Path, host: FakeHost) -> None:
    _linked(vault, host)
    _commit(vault, WIDGET, "edit\n", "SUBJECT-NAMING-A-CLIENT")

    result = rs.sync(vault, host)

    assert len(result.pushed) == 1
    assert "SUBJECT-NAMING-A-CLIENT" not in _mirror_text(host)
    assert _bare_git(host.bare, "log", "-1", "--format=%s", "main").strip() == rs.GENERIC_MESSAGE.strip()


def test_a_squash_never_lists_laptop_subjects(vault: Path, host: FakeHost, tmp_path: Path) -> None:
    _linked(vault, host)
    _commit(vault, WIDGET, "edit\n", "SUBJECT-NAMING-A-CLIENT")
    _commit_on_mirror(tmp_path, host, {"knowledge-base/projects/widget/notes.md": "from agnes\n"}, "agnes edit")

    rs.sync(vault, host)

    assert "SUBJECT-NAMING-A-CLIENT" not in _mirror_text(host)


def test_the_link_snapshot_message_does_not_name_a_laptop_commit(vault: Path, host: FakeHost) -> None:
    _linked(vault, host)
    head = git(vault, "rev-parse", "HEAD").strip()
    assert head[:12] not in _mirror_text(host)


def test_a_commit_outside_the_allowlist_pushes_nothing(vault: Path, host: FakeHost) -> None:
    _linked(vault, host)
    before = _bare_git(host.bare, "rev-parse", "main")
    _commit(vault, HOME, "edit\n", "outside")

    result = rs.sync(vault, host)

    assert result.pushed == []
    assert _bare_git(host.bare, "rev-parse", "main") == before


def test_new_paths_outside_the_allowlist_stay_home(vault: Path, host: FakeHost) -> None:
    _linked(vault, host)
    _commit(vault, "brand-new-folder/note.md", "new\n", "new folder")
    _commit(vault, "knowledge-base/projects/widget/new.md", "in\n", "new note")

    rs.sync(vault, host)

    assert "brand-new-folder" not in _mirror_text(host)
    assert "knowledge-base/projects/widget/new.md" in _tip_paths(host)


# ----- fail closed -------------------------------------------------------------------------


def test_removing_the_key_does_not_widen_the_mirror(vault: Path, host: FakeHost) -> None:
    _linked(vault, host)
    _set_privacy(vault, "drop the allowlist", sync_only=None)
    _commit(vault, HOME, "edit\n", "outside")
    _commit(vault, WIDGET, "edit\n", "inside")

    result = rs.sync(vault, host)

    assert mirror_history_paths(host.bare) == {rs.POLICY_PATH, WIDGET, "knowledge-base/projects/widget/notes.md"}
    assert any("sync-only --accept" in n for n in result.notes)
    assert mirror_policy(host.bare)["policy"]["sync_only"] == [ALLOWED]


def test_editing_the_list_narrows_at_once_and_widens_only_when_accepted(vault: Path, host: FakeHost) -> None:
    _linked(vault, host, "knowledge-base/projects/widget/widget.md", "knowledge-base/projects/widget/notes.md")
    _set_privacy(vault, sync_only=["knowledge-base/projects/widget/widget.md"])
    result = rs.sync(vault, host)
    assert _tip_paths(host) == {rs.POLICY_PATH, WIDGET}
    assert any("sync-only --accept" in n for n in result.notes)

    _set_privacy(vault, sync_only=["knowledge-base/projects/widget/widget.md", "inbox.md"])
    rs.sync(vault, host)
    assert "inbox.md" not in _tip_paths(host)

    rs.accept_sync_only(vault)
    rs.sync(vault, host)
    assert "inbox.md" in _tip_paths(host)


def test_a_later_key_arms_itself_and_says_what_stays_on_the_server(vault: Path, host: FakeHost) -> None:
    _link(vault, host)
    _set_privacy(vault, sync_only=[ALLOWED])

    result = rs.sync(vault, host)

    state = rs.load_state(vault)
    assert state is not None and state.sync_only == [ALLOWED]
    assert _tip_paths(host) == {rs.POLICY_PATH, WIDGET, "knowledge-base/projects/widget/notes.md"}
    assert any("stays there" in n for n in result.notes)


def test_a_key_in_any_unsynced_commit_arms_the_allowlist(vault: Path, host: FakeHost) -> None:
    _link(vault, host)
    _set_privacy(vault, "on", sync_only=[ALLOWED])
    _set_privacy(vault, "off", sync_only=None)

    rs.sync(vault, host)

    state = rs.load_state(vault)
    assert state is not None and state.sync_only == [ALLOWED]
    assert _tip_paths(host) == {rs.POLICY_PATH, WIDGET, "knowledge-base/projects/widget/notes.md"}


def test_a_pin_armed_from_an_unsynced_commit_filters_the_host_list_in_the_same_sync(
    vault: Path, host: FakeHost
) -> None:
    _link(vault, host)
    _set_privacy(vault, "on", sync_only=[ALLOWED], local_only_add=["trading/**", "closed-client/**"])
    _set_privacy(vault, "off", sync_only=None)

    rs.sync(vault, host)

    assert not any("closed-client" in e for put in host.puts for e in put)
    assert "closed-client" not in _mirror_text(host)


def test_lifting_is_an_owner_step_at_the_keyboard(vault: Path, host: FakeHost) -> None:
    _linked(vault, host)
    _set_privacy(vault, sync_only=None)
    rs.sync(vault, host)

    with pytest.raises(rs.RepoSyncError) as e:
        rs.accept_sync_only(vault, env={"SCOUT_MODE": "morning"})
    assert e.value.code == "headless_refused"
    state = rs.load_state(vault)
    assert state is not None and state.sync_only == [ALLOWED]

    result = rs.accept_sync_only(vault)

    assert any("lifted" in n for n in result.notes)
    rs.sync(vault, host)
    assert HOME in _tip_paths(host)


def test_lifting_waits_for_unsynced_commits_that_still_hold_the_key(vault: Path, host: FakeHost) -> None:
    _linked(vault, host)
    _set_privacy(vault, "narrow", sync_only=[WIDGET])
    _set_privacy(vault, "off", sync_only=None)
    with pytest.raises(rs.RepoSyncError) as e:
        rs.accept_sync_only(vault)
    assert e.value.code == "sync_only_pending"


def test_accept_refuses_a_bad_list_and_keeps_the_pin(vault: Path, host: FakeHost) -> None:
    _linked(vault, host)
    _set_privacy(vault, sync_only=5)
    with pytest.raises(rs.RepoSyncError) as e:
        rs.accept_sync_only(vault)
    assert e.value.code == "sync_only_invalid"
    state = rs.load_state(vault)
    assert state is not None and state.sync_only == [ALLOWED]


def test_accept_needs_a_link(vault: Path) -> None:
    with pytest.raises(rs.RepoSyncError) as e:
        rs.accept_sync_only(vault)
    assert e.value.code == "not_linked"


def test_sync_refuses_a_bad_list(vault: Path, host: FakeHost) -> None:
    _linked(vault, host)
    _set_privacy(vault, sync_only=5)
    with pytest.raises(rs.RepoSyncError) as e:
        rs.sync(vault, host)
    assert e.value.code == "sync_only_invalid"


def test_a_bad_list_in_an_old_commit_does_not_empty_the_mirror(vault: Path, host: FakeHost) -> None:
    _linked(vault, host)
    _set_privacy(vault, "typo", sync_only=5)
    _set_privacy(vault, "fix", sync_only=[ALLOWED])

    result = rs.sync(vault, host)

    assert result.pushed == []
    assert WIDGET in _tip_paths(host)


def test_a_state_without_the_pin_stays_readable_by_older_engines(vault: Path, host: FakeHost) -> None:
    _link(vault, host)
    assert "sync_only" not in json.loads(rs.state_path(vault).read_text())


def test_a_pinned_state_is_refused_by_an_engine_that_cannot_enforce_it(vault: Path, host: FakeHost) -> None:
    """An older engine builds LinkState from the file's keys, so a key it doesn't know makes it refuse."""
    _linked(vault, host)
    data = json.loads(rs.state_path(vault).read_text())
    assert data["sync_only"] == [ALLOWED]
    data["a_key_this_engine_does_not_know"] = True
    rs.state_path(vault).write_text(json.dumps(data))
    with pytest.raises(rs.RepoSyncError) as e:
        rs.load_state(vault)
    assert e.value.code == "state_unreadable"


# ----- replay ------------------------------------------------------------------------------


def test_a_commit_made_in_agnes_outside_the_allowlist_warns_and_is_not_lost_silently(
    vault: Path, host: FakeHost, tmp_path: Path
) -> None:
    _linked(vault, host)
    sha = _commit_on_mirror(tmp_path, host, {AGNES_NOTE: "CONTENT-OF-THE-NOTE\n"}, "note in agnes")

    result = rs.sync(vault, host)

    assert not (vault / AGNES_NOTE).exists()
    assert result.replay_skipped == [{"path": AGNES_NOTE, "mirror_commit": sha}]
    warning = next(n for n in result.notes if AGNES_NOTE in n)
    assert sha[:12] in warning
    assert "CONTENT-OF-THE-NOTE" not in json.dumps(result.to_json())
    # This same sync takes it off the mirror's tip; its history still has it.
    assert AGNES_NOTE not in _tip_paths(host)
    assert _bare_git(host.bare, "show", f"{sha}:{AGNES_NOTE}") == "CONTENT-OF-THE-NOTE\n"


def test_a_commit_made_in_agnes_inside_the_allowlist_replays_without_a_warning(
    vault: Path, host: FakeHost, tmp_path: Path
) -> None:
    _linked(vault, host)
    rel = "knowledge-base/projects/widget/agnes.md"
    _commit_on_mirror(tmp_path, host, {rel: "in\n"}, "note in agnes")

    result = rs.sync(vault, host)

    assert (vault / rel).exists()
    assert result.replay_skipped == []
    assert rel in _tip_paths(host)


def test_a_removal_outside_the_allowlist_loses_nothing_and_does_not_warn(
    vault: Path, host: FakeHost, tmp_path: Path
) -> None:
    _link(vault, host)
    _commit_on_mirror(tmp_path, host, {HOME: None}, "remove in agnes")
    _write_privacy(vault, sync_only=[ALLOWED])

    result = rs.sync(vault, host)

    assert result.replay_skipped == []
    assert (vault / HOME).exists()


def test_an_ordinary_vault_does_not_warn_about_local_only_replays(vault: Path, host: FakeHost, tmp_path: Path) -> None:
    _link(vault, host)
    _commit_on_mirror(tmp_path, host, {"Raw/scan.txt": None}, "Remove Local-Only Paths from the repository")
    host.local_only = (*host.local_only, "Raw/")
    assert rs.sync(vault, host).replay_skipped == []


# ----- the trading gate --------------------------------------------------------------------


def test_the_gate_asks_nothing_about_paths_the_allowlist_keeps_home(vault: Path, host: FakeHost) -> None:
    _commit(vault, "TRADING.md", "x\n", "root trading file")
    _commit(vault, "brokerage/statement.md", "x\n", "trading folder")
    privacy_migrate.accept(vault)  # the owner went through /scout-update again
    _linked(vault, host)  # no --confirm-sync needed
    assert "TRADING.md" not in mirror_history_paths(host.bare)


def test_the_gate_still_asks_about_a_trading_path_the_allowlist_uploads(vault: Path, host: FakeHost) -> None:
    _commit(vault, "TRADING.md", "x\n", "root trading file")
    with pytest.raises(rs.RepoSyncError) as e:
        _linked(vault, host, ALLOWED, "TRADING.md")
    assert e.value.code == "trading_paths_unconfirmed"
    assert "TRADING.md" in e.value.message


def test_the_gate_still_asks_about_a_trading_folder_the_allowlist_reaches_into(vault: Path, host: FakeHost) -> None:
    """Probing ``brokerage/x`` says "outside", but ``brokerage/reports/`` would upload."""
    _commit(vault, "brokerage/reports/q3.md", "x\n", "trading folder")
    privacy_migrate.accept(vault)
    with pytest.raises(rs.RepoSyncError) as e:
        _linked(vault, host, "brokerage/reports/**")
    assert e.value.code == "trading_paths_unconfirmed"
    assert "brokerage/**" in e.value.message


# ----- kb share ----------------------------------------------------------------------------


def test_kb_share_refuses_a_note_outside_the_allowlist(vault: Path) -> None:
    _set_privacy(vault, sync_only=[ALLOWED])
    with pytest.raises(share.ShareRefused, match="sync_only"):
        share.plan_share(vault, HOME, ["priya@example.com"], directory=None)


def test_kb_share_refuses_a_folder_outside_the_allowlist(vault: Path) -> None:
    _set_privacy(vault, sync_only=[ALLOWED])
    with pytest.raises(share.ShareRefused, match="sync_only"):
        share.plan_share(vault, "knowledge-base/topics/misc", ["priya@example.com"], directory=None)


GADGET_A = "knowledge-base/projects/gadget/a.md"
GADGET_B = "knowledge-base/projects/gadget/b.md"


def _gadget(vault: Path) -> None:
    _commit(vault, GADGET_A, "A note.\n", "gadget a")
    _commit(vault, GADGET_B, "B note.\n", "gadget b")


def test_kb_share_leaves_out_the_part_of_a_folder_outside_the_allowlist(vault: Path) -> None:
    _gadget(vault)
    _set_privacy(vault, sync_only=[GADGET_A])
    plan = share.plan_share(vault, "knowledge-base/projects/gadget", ["priya@example.com"], directory=None)
    assert [p for p, _ in plan.newly] == [GADGET_A]
    assert (GADGET_B, "outside-sync-only") in plan.excluded
    assert any("privacy.sync_only" in line for line in plan.lines())


def test_kb_share_follows_the_pin_when_the_key_is_gone(vault: Path, host: FakeHost) -> None:
    _linked(vault, host)
    _set_privacy(vault, sync_only=None)
    with pytest.raises(share.ShareRefused, match="sync_only"):
        share.plan_share(vault, HOME, ["priya@example.com"], directory=None)


def test_kb_share_is_unchanged_without_the_key(vault: Path) -> None:
    plan = share.plan_share(vault, HOME, ["priya@example.com"], directory=None)
    assert [p for p, _ in plan.newly] == [HOME]


def test_kb_shares_does_not_show_a_note_outside_the_allowlist_as_shared(vault: Path) -> None:
    _gadget(vault)
    _set_privacy(
        vault,
        federation={"enabled": True},
        federation_defaults=[{"glob": "knowledge-base/projects/gadget/**", "scope": ["person:priya@example.com"]}],
        sync_only=[GADGET_A],
    )
    _, rows = share.list_shares(vault)
    row = next(r for r in rows if r.glob == "knowledge-base/projects/gadget/**")
    assert row.shared == [GADGET_A]
    assert row.excluded["outside-sync-only"] == [GADGET_B]


# ----- preview -----------------------------------------------------------------------------


def _objects(vault: Path) -> str:
    return git(vault, "count-objects", "-v")


def test_preview_lists_exactly_what_link_pushes(vault: Path, host: FakeHost) -> None:
    _set_privacy(vault, sync_only=[ALLOWED])
    before = _objects(vault)

    preview = rs.preview(vault)

    assert _objects(vault) == before
    assert not host.exists
    assert preview.sync_only == [ALLOWED]
    assert preview.by_folder == {".scout-state": 1, "knowledge-base/projects": 2}
    assert preview.total == 3
    assert preview.kept > 0

    _link(vault, host)
    assert set(preview.paths) == mirror_history_paths(host.bare)
    assert preview.host_local_only_paths == list(host.local_only)


def test_preview_without_an_allowlist_says_so(vault: Path) -> None:
    preview = rs.preview(vault)
    assert preview.sync_only is None
    assert "trading/positions.md" not in preview.paths
    assert HOME in preview.paths
    assert any("no privacy.sync_only" in line for line in preview.lines())


def test_preview_counts_private_notes_and_warns_about_the_config(vault: Path) -> None:
    _set_privacy(vault, sync_only=["knowledge-base/personal/**", "scout-config.yaml"])
    preview = rs.preview(vault)
    assert preview.private == 1  # task-renew-permit.md; task-buy-gift.md says visibility: normal
    text = "\n".join(preview.lines())
    assert "1 private" in text
    assert "scout-config.yaml" in text


def test_preview_works_on_a_linked_vault_and_uses_the_pin(vault: Path, host: FakeHost) -> None:
    _linked(vault, host)
    _set_privacy(vault, sync_only=None)
    preview = rs.preview(vault)
    assert preview.sync_only == [ALLOWED]
    assert preview.config_sync_only is None
    assert HOME not in preview.paths


def test_preview_reports_the_trading_questions_link_would_ask(vault: Path) -> None:
    _commit(vault, "TRADING.md", "x\n", "root trading file")
    assert rs.preview(vault).trading_unconfirmed == ["TRADING.md"]


def test_preview_refuses_a_bad_allowlist(vault: Path) -> None:
    _set_privacy(vault, sync_only=5)
    with pytest.raises(rs.RepoSyncError) as e:
        rs.preview(vault)
    assert e.value.code == "sync_only_invalid"


def test_preview_with_a_folder_depth(vault: Path) -> None:
    _set_privacy(vault, sync_only=[ALLOWED, "inbox.md"])
    assert rs.preview(vault, depth=1).by_folder == {".": 1, ".scout-state": 1, "knowledge-base": 2}


# ----- cli ---------------------------------------------------------------------------------


def test_cli_preview(vault: Path) -> None:
    _set_privacy(vault, sync_only=[ALLOWED])
    runner = CliRunner()

    out = runner.invoke(app, ["repo", "preview"])
    assert out.exit_code == 0, out.output
    assert "knowledge-base/projects: 2" in out.output
    assert WIDGET not in out.output

    out = runner.invoke(app, ["repo", "preview", "--paths"])
    assert WIDGET in out.output

    out = runner.invoke(app, ["repo", "preview", "--json"])
    data = json.loads(out.output)
    assert data["ok"] is True and data["total"] == 3 and "paths" not in data

    out = runner.invoke(app, ["repo", "preview", "--json", "--paths"])
    assert WIDGET in json.loads(out.output)["paths"]


def test_cli_preview_refusal_exits_2(vault: Path) -> None:
    _set_privacy(vault, sync_only=5)
    out = CliRunner().invoke(app, ["repo", "preview", "--json"])
    assert out.exit_code == 2
    assert json.loads(out.output)["code"] == "sync_only_invalid"


def test_cli_sync_only_shows_and_accepts(vault: Path, host: FakeHost) -> None:
    _linked(vault, host)
    _set_privacy(vault, sync_only=None)
    runner = CliRunner()

    out = runner.invoke(app, ["repo", "sync-only"])
    assert out.exit_code == 0, out.output
    assert ALLOWED in out.output and "no privacy.sync_only" in out.output
    state = rs.load_state(vault)
    assert state is not None and state.sync_only == [ALLOWED]

    git(vault, "update-ref", "refs/heads/unused", "HEAD")
    rs.sync(vault, host)
    out = runner.invoke(app, ["repo", "sync-only", "--accept", "--json"])
    assert out.exit_code == 0, out.output
    assert json.loads(out.output)["ok"] is True
    state = rs.load_state(vault)
    assert state is not None and state.sync_only is None


def test_cli_sync_only_needs_a_link(vault: Path) -> None:
    out = CliRunner().invoke(app, ["repo", "sync-only"])
    assert out.exit_code == 2


# ----- review of #574: path spellings, the pin, symlinks, the push guard -----------------------


def _add(vault: Path, rel: str, text: str, subject: str = "add") -> None:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    git(vault, "add", "-f", "--", rel)
    git(vault, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", subject)


LOOKALIKES = [
    "knowledge-base/projects\u200b",  # zero-width space
    "knowledge-base/projects.",  # trailing dot
    "knowledge-base/projects ",  # trailing space
    "knowledge-base/\uff50rojects",  # fullwidth p
]


@pytest.mark.parametrize("folder", LOOKALIKES)
def test_a_folder_that_only_folds_to_an_allowed_one_stays_home(vault: Path, host: FakeHost, folder: str) -> None:
    assert _rules(ALLOWED).refused(f"{folder}/leak.md")
    _set_privacy(vault, sync_only=[ALLOWED])
    _add(vault, f"{folder}/leak.md", "LOOKALIKE-CANARY\n")
    _link(vault, host)
    assert "LOOKALIKE-CANARY" not in _mirror_text(host)


def test_an_entry_matches_its_own_unicode_spelling() -> None:
    decomposed = "cafe\u0301/**"  # NFD; the path below is NFC
    assert not _rules(decomposed).refused("caf\u00e9/a.md")


@pytest.mark.parametrize(
    "rel",
    [".scout-state/Privacy-Policy.json", ".scout-state/privacy-policy.json.", ".scout-state/privacy\u200b-policy.json"],
)
def test_only_the_generated_policy_file_may_carry_its_name(vault: Path, host: FakeHost, rel: str) -> None:
    assert _rules(ALLOWED).refused(rel)
    assert rs.LocalOnlyRules.build([policy_from_overlay({})], ()).refused(rel)
    _set_privacy(vault, sync_only=[ALLOWED])
    _add(vault, rel, "POLICY-LOOKALIKE-CANARY\n")
    _link(vault, host)
    assert "POLICY-LOOKALIKE-CANARY" not in _mirror_text(host)


def test_dropping_the_pin_from_the_state_file_does_not_lift_it(vault: Path, host: FakeHost) -> None:
    _linked(vault, host)
    data = json.loads(rs.state_path(vault).read_text())
    del data["sync_only"]
    rs.state_path(vault).write_text(json.dumps(data))
    _write_privacy(vault, sync_only=None)  # uncommitted, so no hook sees it

    rs.sync(vault, host)

    assert HOME not in _tip_paths(host)
    state = rs.load_state(vault)
    assert state is not None and state.sync_only == [ALLOWED]


def test_a_rewritten_history_whose_head_commits_the_key_still_arms(vault: Path, host: FakeHost) -> None:
    _link(vault, host)
    git(vault, "reset", "-q", "--hard", "HEAD~1")
    _set_privacy(vault, sync_only=[ALLOWED])
    _write_privacy(vault, sync_only=None)

    rs.sync(vault, host)

    state = rs.load_state(vault)
    assert state is not None and state.sync_only == [ALLOWED]
    assert HOME not in _tip_paths(host)


def test_link_pins_heads_list_when_the_working_tree_dropped_it(vault: Path, host: FakeHost) -> None:
    _set_privacy(vault, sync_only=[ALLOWED])
    _write_privacy(vault, sync_only=None)
    assert HOME not in rs.preview(vault).paths

    _link(vault, host)

    assert HOME not in mirror_history_paths(host.bare)
    state = rs.load_state(vault)
    assert state is not None and state.sync_only == [ALLOWED]


def test_lifting_waits_while_head_still_commits_the_key(vault: Path, host: FakeHost) -> None:
    _linked(vault, host)
    _write_privacy(vault, sync_only=None)
    with pytest.raises(rs.RepoSyncError) as e:
        rs.accept_sync_only(vault)
    assert e.value.code == "sync_only_pending"


def test_a_symlink_naming_a_folder_outside_the_list_stays_home(vault: Path, host: FakeHost) -> None:
    _set_privacy(vault, sync_only=[ALLOWED])
    (vault / "outside-client").mkdir()
    (vault / "outside-client" / "plan.md").write_text("x\n")
    (vault / "knowledge-base/projects/widget/plan.md").symlink_to("../../../outside-client/plan.md")
    (vault / "knowledge-base/projects/widget/up.md").symlink_to("/etc/hosts")
    (vault / "knowledge-base/projects/widget/same.md").symlink_to("widget.md")
    git(vault, "add", "-A")
    git(vault, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", "links")

    _link(vault, host)

    assert "outside-client" not in _mirror_text(host)
    assert "/etc/hosts" not in _mirror_text(host)
    assert "knowledge-base/projects/widget/same.md" in _tip_paths(host)


def _hand_built_mirror_commit(vault: Path, files: dict[str, str]) -> str:
    lines = []
    for rel, text in {**files, rs.POLICY_PATH: "{}\n"}.items():
        sha = rs.git(vault, "hash-object", "-w", "--stdin", input=text).stdout.strip()
        lines.append(f"100644 {sha}\t{rel}")
    tree = rs._write_tree(vault, lines)
    return rs.git(vault, "commit-tree", "--no-gpg-sign", tree, "-m", "hand-built").stdout.strip()


@pytest.mark.parametrize("rel", [HOME, "knowledge-base/projects./leak.md", ".scout-state/Privacy-Policy.json"])
def test_the_push_guard_refuses_what_the_allowlist_keeps_home(vault: Path, rel: str) -> None:
    rules = _rules(ALLOWED)
    rs.check_mirror_commits(vault, rules, [_hand_built_mirror_commit(vault, {WIDGET: "in\n"})])
    with pytest.raises(rs.RepoSyncError) as e:
        rs.check_mirror_commits(vault, rules, [_hand_built_mirror_commit(vault, {WIDGET: "in\n", rel: "out\n"})])
    assert e.value.code == "local_only_path_in_push"


def test_the_push_guard_refuses_a_symlink_out_of_the_allowlist(vault: Path) -> None:
    sha = rs.git(vault, "hash-object", "-w", "--stdin", input="../../../outside-client/plan.md").stdout.strip()
    doc = rs.git(vault, "hash-object", "-w", "--stdin", input="{}\n").stdout.strip()
    tree = rs._write_tree(
        vault, [f"120000 {sha}\tknowledge-base/projects/widget/plan.md", f"100644 {doc}\t{rs.POLICY_PATH}"]
    )
    rev = rs.git(vault, "commit-tree", "--no-gpg-sign", tree, "-m", "hand-built").stdout.strip()
    with pytest.raises(rs.RepoSyncError):
        rs.check_mirror_commits(vault, _rules(ALLOWED), [rev])


def test_dropping_the_pin_and_committing_the_removal_does_not_lift_it(vault: Path, host: FakeHost) -> None:
    """The last synced commit still had the key, and no --accept ran."""
    _linked(vault, host)
    data = json.loads(rs.state_path(vault).read_text())
    del data["sync_only"]
    rs.state_path(vault).write_text(json.dumps(data))
    _set_privacy(vault, "drop", sync_only=None)  # --no-verify: past the hook's privacy-widening check

    rs.sync(vault, host)

    assert HOME not in _tip_paths(host)
