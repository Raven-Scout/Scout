"""Shared Areas, the owner's side: ``scoutctl kb share | unshare | shares`` (Agnes #6090 §4, slice S1a).

The §9 milestone 1 "done when" for S1a, over a made-up vault (owner Alex,
colleagues Priya and Sam, project ``widget``):

- sharing a project folder with a person previews its notes and the copy warning;
- ``kb shares`` lists them;
- a private note under the folder shows as excluded;
- a share above the ceiling is refused;
- a meeting note shares with a person and refuses ``org``;
- an email under ``sources/`` refuses any share.

No test reaches the network: ``requests`` is blocked in this module, and the
Agnes lookups are a fake :class:`Directory`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import requests
import yaml
from typer.testing import CliRunner

from scout.cli import app
from scout.kb import share
from scout.kb.privacy import label_file, load_policy

runner = CliRunner()

WIDGET = "knowledge-base/projects/widget"
PLAN = f"{WIDGET}/plan.md"
SECRET = f"{WIDGET}/secret.md"
SOLO = f"{WIDGET}/solo.md"
DESIGN = f"{WIDGET}/design/notes.md"
MEETING = "knowledge-base/sources/2026-10/2026-10-01-weekly-sync.md"
THREAD = "knowledge-base/sources/2026-10/2026-10-02-launch-thread.md"
EMAIL = "knowledge-base/sources/2026-10/2026-10-03-vendor-email.md"
SAM_NOTE = "knowledge-base/people/sam.md"
PRIYA = "person:priya@example.com"
SAM = "person:sam@example.com"

CONFIG = """\
user:
  name: Alex  # the owner

# Privacy overlay
privacy:
  local_only_add:
    - "trading/**"
"""


def _note(vault: Path, rel: str, frontmatter: str, body: str = "Body text.\n") -> None:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{frontmatter}\n---\n{body}", encoding="utf-8")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("a test reached the network")

    monkeypatch.setattr(requests, "get", refuse)
    monkeypatch.setattr(requests, "request", refuse)


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("SCOUT_MODE", raising=False)
    monkeypatch.delenv("SCOUT_READER", raising=False)
    v = tmp_path / "vault"
    v.mkdir()
    (v / "scout-config.yaml").write_text(CONFIG, encoding="utf-8")
    _note(v, PLAN, "name: Widget plan\ntype: project")
    _note(v, SECRET, "visibility: private", "Salary notes.\n")
    _note(v, SOLO, f"federation_scope: [{SAM}]")
    _note(v, DESIGN, "name: Design notes")
    _note(v, MEETING, "kind: meeting\nparticipants: ['[[priya]]']")
    _note(v, THREAD, "kind: thread")
    _note(v, EMAIL, "kind: email")
    _note(v, SAM_NOTE, "name: Sam\ntype: person\nvisibility: private\naliases: [Sammy]")
    return v


def kb(vault: Path, *args: str, env: dict[str, str] | None = None) -> Any:
    return runner.invoke(app, ["kb", *args, "--repo", str(vault)], env=env)


def overlay(vault: Path) -> dict[str, Any]:
    return yaml.safe_load((vault / "scout-config.yaml").read_text(encoding="utf-8"))["privacy"]


def label(vault: Path, rel: str) -> dict[str, Any]:
    return label_file(vault, rel, load_policy(vault)).to_json()


class FakeDirectory(share.Directory):
    """Agnes lookups without a network: ``colleagues`` None means the endpoint is missing (pre-#5753)."""

    def __init__(self, colleagues: list[str] | None = None, groups: list[dict[str, Any]] | None = None) -> None:
        super().__init__("https://agnes.example.com", "token")
        self.colleagues = colleagues
        self.groups = groups

    def _get(self, path: str, params: Any = None) -> tuple[int, Any] | None:
        if path == "/api/v1/colleagues":
            if self.colleagues is None:
                return 404, {"detail": "Not Found"}
            q = dict(params or {})["q"]
            return 200, {"data": [{"name": "X", "email": e, "can_be_asked": False} for e in self.colleagues if e == q]}
        if path == "/api/sharing/groups":
            return (200, self.groups) if self.groups is not None else None
        raise AssertionError(path)


# ----- done when (§9, milestone 1, S1a) ----------------------------------------------


def test_sharing_a_project_folder_previews_its_notes_and_the_copy_warning(vault: Path) -> None:
    before = (vault / "scout-config.yaml").read_text(encoding="utf-8")
    res = kb(vault, "share", WIDGET, "--with", "priya@example.com", "--dry-run")
    assert res.exit_code == 0, res.output
    assert f"Share {WIDGET}/** with priya@example.com" in res.output
    assert f"{PLAN}  +priya@example.com" in res.output
    assert f"{DESIGN}  +priya@example.com" in res.output
    assert share.COPY_WARNING in res.output
    assert "checked only when someone reads" in res.output  # not linked: the read-time warning
    assert (vault / "scout-config.yaml").read_text(encoding="utf-8") == before  # a preview writes nothing


def test_a_share_is_written_only_when_confirmed(vault: Path) -> None:
    res = kb(vault, "share", WIDGET, "--with", "priya@example.com")
    assert res.exit_code == 1
    assert "nothing was written" in res.output.lower()
    assert "federation_defaults" not in overlay(vault)

    res = kb(vault, "share", WIDGET, "--with", "priya@example.com", "--yes")
    assert res.exit_code == 0, res.output
    priv = overlay(vault)
    assert priv["federation"] == {"enabled": True}
    assert priv["federation_defaults"] == [{"glob": f"{WIDGET}/**", "scope": [PRIYA]}]
    assert priv["local_only_add"] == ["trading/**"]
    text = (vault / "scout-config.yaml").read_text(encoding="utf-8")
    assert "name: Alex  # the owner" in text  # other blocks kept byte for byte
    assert label(vault, PLAN)["federation_scope"] == [PRIYA]
    assert label(vault, PLAN)["federation_source"] == "default"


def test_kb_shares_lists_them_and_a_private_note_is_excluded(vault: Path) -> None:
    assert kb(vault, "share", WIDGET, "--with", "priya@example.com", "--yes").exit_code == 0
    res = kb(vault, "shares", "--json")
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert data["enabled"] is True
    rows = {row["glob"]: row for row in data["shares"]}
    folder = rows[f"{WIDGET}/**"]
    assert folder["principals"] == [PRIYA]
    assert folder["shared"] == [DESIGN, PLAN]
    assert folder["notes"] == 2
    assert folder["excluded"] == {"private": [SECRET], "frontmatter": [SOLO]}
    assert rows[SOLO]["source"] == "frontmatter" and rows[SOLO]["principals"] == [SAM]

    text = kb(vault, "shares").output
    assert f"{WIDGET}/**  with priya@example.com  (2 note(s))" in text
    assert "excluded, private (private always wins): 1" in text
    assert SECRET in text


def test_the_preview_lists_a_private_note_under_the_folder_as_excluded(vault: Path) -> None:
    res = kb(vault, "share", WIDGET, "--with", "priya@example.com", "--dry-run", "--json")
    data = json.loads(res.output)
    assert {"path": SECRET, "reason": "private"} in data["excluded"]
    assert {"path": SOLO, "reason": "frontmatter"} in data["excluded"]
    assert SECRET not in [n["path"] for n in data["newly_shared"]]
    assert data["written"] is False and data["copy_warning"] == share.COPY_WARNING


@pytest.mark.parametrize(
    ("target", "who", "message"),
    [
        ("knowledge-base/people", "priya@example.com", "can't be shared"),
        (SAM_NOTE, "priya@example.com", "can't be shared"),
        (MEETING, "group:team-1", "named people only"),
        (THREAD, "org", "named people only"),
    ],
)
def test_a_share_above_the_ceiling_is_refused(vault: Path, target: str, who: str, message: str) -> None:
    res = kb(vault, "share", target, "--with", who, "--yes")
    assert res.exit_code == 1
    assert "refused" in res.output and message in res.output
    assert "federation_defaults" not in overlay(vault)


def test_a_meeting_note_shares_with_a_person_and_refuses_org(vault: Path) -> None:
    refused = kb(vault, "share", MEETING, "--with", "org", "--yes")
    assert refused.exit_code == 1
    assert "above the ceiling" in refused.output

    res = kb(vault, "share", MEETING, "--with", "priya@example.com", "--yes")
    assert res.exit_code == 0, res.output
    assert f"{MEETING}  +priya@example.com" in res.output
    got = label(vault, MEETING)
    assert (got["federation_scope"], got["ceiling"]) == ([PRIYA], "people")
    assert overlay(vault)["federation_defaults"] == [{"glob": MEETING, "scope": [PRIYA]}]


@pytest.mark.parametrize("who", ["priya@example.com", "group:team-1", "org"])
def test_an_email_under_sources_refuses_any_share(vault: Path, who: str) -> None:
    res = kb(vault, "share", EMAIL, "--with", who, "--yes")
    assert res.exit_code == 1
    assert "refused" in res.output
    assert label(vault, EMAIL)["federation_scope"] == "none"


# ----- more refusals ------------------------------------------------------------------


def test_org_is_refused_even_where_the_ceiling_allows_it(vault: Path) -> None:
    res = kb(vault, "share", WIDGET, "--with", "org", "--yes")
    assert res.exit_code == 1
    assert "org admits nobody" in res.output


@pytest.mark.parametrize("target", ["knowledge-base/sources", "knowledge-base/sources/2026-10", "knowledge-base"])
def test_a_folder_holding_meetings_is_shared_note_by_note(vault: Path, target: str) -> None:
    res = kb(vault, "share", target, "--with", "priya@example.com", "--yes")
    assert res.exit_code == 1
    assert "note by note" in res.output


@pytest.mark.parametrize(
    ("target", "message"), [(SECRET, "private always wins"), (SOLO, "sets its own federation_scope")]
)
def test_a_note_that_cant_take_a_default_is_refused(vault: Path, target: str, message: str) -> None:
    res = kb(vault, "share", target, "--with", "priya@example.com", "--yes")
    assert res.exit_code == 1 and message in res.output


def test_a_scheduled_run_may_not_change_sharing(vault: Path) -> None:
    for args in (["share", WIDGET, "--with", "priya@example.com", "--yes"], ["unshare", WIDGET]):
        res = kb(vault, *args, env={"SCOUT_MODE": "dreaming"})
        assert res.exit_code == 1
        assert "only the owner" in res.output
    assert "federation_defaults" not in overlay(vault)


@pytest.mark.parametrize("bad", ["priya", "person:nope", "group:"])
def test_a_bad_with_value_is_a_usage_error(vault: Path, bad: str) -> None:
    assert kb(vault, "share", WIDGET, "--with", bad, "--dry-run").exit_code == 2


def test_another_persons_agent_gets_no_share_list(vault: Path) -> None:
    res = kb(vault, "shares", "--reader", "agent:person:priya@example.com", "--json")
    assert res.exit_code == 3
    assert json.loads(res.output) == {"denied": True}


# ----- people and groups through Agnes (fail soft) ------------------------------------


def test_before_the_colleagues_endpoint_an_email_is_accepted_with_a_warning(vault: Path) -> None:
    plan = share.plan_share(vault, WIDGET, ["priya@example.com"], directory=FakeDirectory(colleagues=None))
    assert any("checked only when someone reads" in w for w in plan.warnings)


def test_once_agnes_looks_people_up_an_unknown_email_is_refused(vault: Path) -> None:
    d = FakeDirectory(colleagues=["priya@example.com"])
    plan = share.plan_share(vault, WIDGET, ["Priya@Example.com"], directory=d)
    assert plan.warnings == [] and plan.principals == [PRIYA]
    with pytest.raises(share.ShareRefused, match="isn't a user"):
        share.plan_share(vault, WIDGET, ["nobody@example.com"], directory=d)


def test_an_unreachable_agnes_fails_soft() -> None:
    d = share.Directory("https://agnes.example.com", "token")
    d._get = lambda *_a, **_k: None  # type: ignore[method-assign]  # a timeout
    assert d.colleague("priya@example.com") == "unchecked"
    assert d.share_targets() is None


def test_a_group_is_written_by_its_id_and_everyone_is_refused(vault: Path) -> None:
    groups = [
        {"id": "a1b2c3", "name": "Widget team", "is_everyone": False},
        {"id": "everyone", "name": "Everyone", "is_everyone": True},
    ]
    plan = share.plan_share(vault, WIDGET, ["group:widget team"], directory=FakeDirectory(groups=groups))
    assert plan.principals == ["group:a1b2c3"]
    assert plan.names == {"group:a1b2c3": "Widget team"}
    assert "Widget team (group:a1b2c3)" in "\n".join(plan.lines())
    with pytest.raises(share.ShareRefused, match="Everyone"):
        share.plan_share(vault, WIDGET, ["group:Everyone"], directory=FakeDirectory(groups=groups))
    with pytest.raises(share.ShareRefused, match="not one of your share targets"):
        share.plan_share(vault, WIDGET, ["group:other"], directory=FakeDirectory(groups=groups))


def test_a_group_share_preview_names_the_group_and_the_owners_membership(vault: Path) -> None:
    groups = [{"id": "a1b2c3", "name": "Widget team", "is_everyone": False}]
    plan = share.plan_share(vault, WIDGET, ["group:a1b2c3"], directory=FakeDirectory(groups=groups))
    text = "\n".join(plan.lines())
    assert f"group a1b2c3: Widget team; {share.GROUP_MEMBERS_NOTE}" in text
    assert share.GROUP_OWNER_NOTE in text
    data = plan.to_json()
    assert data["groups"] == [
        {"id": "a1b2c3", "name": "Widget team", "members": None, "members_note": share.GROUP_MEMBERS_NOTE}
    ]
    assert data["group_owner_note"] == share.GROUP_OWNER_NOTE

    offline = share.plan_share(vault, WIDGET, ["group:team-1"], directory=None)
    assert "group team-1: (name not checked with Agnes)" in "\n".join(offline.lines())
    people_only = share.plan_share(vault, WIDGET, ["priya@example.com"], directory=None)
    assert share.GROUP_OWNER_NOTE not in "\n".join(people_only.lines())
    assert people_only.to_json()["groups"] == [] and people_only.to_json()["group_owner_note"] is None


def test_offline_a_group_id_is_kept_with_a_warning(vault: Path) -> None:
    plan = share.plan_share(vault, WIDGET, ["group:team-1"], directory=None)
    assert plan.principals == ["group:team-1"]
    assert any("couldn't check it with Agnes" in w for w in plan.warnings)


def test_the_vault_link_is_the_directory(vault: Path) -> None:
    assert share.directory_for(vault) is None  # not linked: no lookups at all


# ----- folder entries, ordering, and the lint warning ---------------------------------


def test_a_folder_share_reaches_narrower_entries_inside_it(vault: Path) -> None:
    assert kb(vault, "share", f"{WIDGET}/design", "--with", "sam@example.com", "--yes").exit_code == 0
    assert kb(vault, "share", WIDGET, "--with", "priya@example.com", "--yes").exit_code == 0
    assert overlay(vault)["federation_defaults"] == [
        {"glob": f"{WIDGET}/design/**", "scope": [SAM, PRIYA]},
        {"glob": f"{WIDGET}/**", "scope": [PRIYA]},
    ]
    assert label(vault, DESIGN)["federation_scope"] == [SAM, PRIYA]


def test_a_narrower_share_goes_before_a_broader_entry(vault: Path) -> None:
    assert kb(vault, "share", WIDGET, "--with", "priya@example.com", "--yes").exit_code == 0
    assert kb(vault, "share", f"{WIDGET}/design", "--with", "sam@example.com", "--yes").exit_code == 0
    globs = [e["glob"] for e in overlay(vault)["federation_defaults"]]
    assert globs == [f"{WIDGET}/design/**", f"{WIDGET}/**"]
    assert label(vault, DESIGN)["federation_scope"] == [PRIYA, SAM]


def test_sharing_one_note_of_a_shared_folder_keeps_the_folders_people(vault: Path) -> None:
    assert kb(vault, "share", WIDGET, "--with", "priya@example.com", "--yes").exit_code == 0
    assert kb(vault, "share", PLAN, "--with", "sam@example.com", "--yes").exit_code == 0
    assert overlay(vault)["federation_defaults"][0] == {"glob": PLAN, "scope": [PRIYA, SAM]}
    assert label(vault, PLAN)["federation_scope"] == [PRIYA, SAM]


def test_sharing_again_changes_nothing(vault: Path) -> None:
    assert kb(vault, "share", WIDGET, "--with", "priya@example.com", "--yes").exit_code == 0
    res = kb(vault, "share", WIDGET, "--with", "priya@example.com", "--yes")
    assert res.exit_code == 0 and "Nothing to change" in res.output


def test_the_preview_flags_a_note_that_links_a_private_note(vault: Path) -> None:
    _note(vault, PLAN, "name: Widget plan\ntype: project", "Ask [[sam]] first.\n")
    data = json.loads(kb(vault, "share", WIDGET, "--with", "priya@example.com", "--dry-run", "--json").output)
    assert data["private_links"] == [{"path": PLAN, "lines": [5]}]


# ----- kb unshare (vault only in S1a) ---------------------------------------------------


def test_unshare_with_one_person_keeps_the_others(vault: Path) -> None:
    kb(vault, "share", WIDGET, "--with", "priya@example.com", "--with", "sam@example.com", "--yes")
    res = kb(vault, "unshare", WIDGET, "--with", "sam@example.com")
    assert res.exit_code == 0, res.output
    assert overlay(vault)["federation_defaults"] == [{"glob": f"{WIDGET}/**", "scope": [PRIYA]}]
    assert "next `scoutctl repo sync`" in res.output
    assert f"{PLAN}  -sam@example.com" in res.output
    assert f"{SOLO}" in res.output  # it shares itself with Sam in its own frontmatter: listed, not edited


def test_unshare_everyone_removes_the_entry(vault: Path) -> None:
    kb(vault, "share", WIDGET, "--with", "priya@example.com", "--yes")
    data = json.loads(kb(vault, "unshare", WIDGET, "--json").output)
    assert data["written"] is True and data["pending_until_sync"] is True
    assert overlay(vault)["federation_defaults"] == []
    assert label(vault, PLAN)["federation_scope"] == "none"


def test_unsharing_a_subfolder_of_a_shared_folder_writes_an_exclusion(vault: Path) -> None:
    kb(vault, "share", WIDGET, "--with", "priya@example.com", "--yes")
    assert kb(vault, "unshare", f"{WIDGET}/design", "--with", "priya@example.com").exit_code == 0
    assert overlay(vault)["federation_defaults"] == [
        {"glob": f"{WIDGET}/design/**", "scope": "none"},
        {"glob": f"{WIDGET}/**", "scope": [PRIYA]},
    ]
    assert label(vault, DESIGN)["federation_scope"] == "none"
    assert label(vault, PLAN)["federation_scope"] == [PRIYA]


def test_unsharing_what_isnt_shared_changes_nothing(vault: Path) -> None:
    res = kb(vault, "unshare", WIDGET, "--with", "priya@example.com")
    assert res.exit_code == 0 and "Nothing to change" in res.output


# ----- the label and the synced policy ---------------------------------------------------


def test_kb_classify_carries_the_ceiling_and_the_source(vault: Path) -> None:
    kb(vault, "share", WIDGET, "--with", "priya@example.com", "--yes")
    res = kb(vault, "classify", PLAN, MEETING, EMAIL, "--json")
    got = {
        row["path"]: (row["ceiling"], row["federation_source"], row["federation_scope"])
        for row in json.loads(res.output)
    }
    assert got == {
        PLAN: ("org", "default", [PRIYA]),
        MEETING: ("people", "default", "none"),
        EMAIL: ("none", "default", "none"),
    }


def test_the_synced_policy_document_carries_the_stage_2_keys(vault: Path) -> None:
    from scout.repo_sync import policy_document

    kb(vault, "share", WIDGET, "--with", "priya@example.com", "--yes")
    doc = json.loads(policy_document(load_policy(vault), [], host_entries=[]))
    pol = doc["policy"]
    assert pol["federation"] == {"enabled": True}
    assert pol["federation_defaults"] == [{"glob": f"{WIDGET}/**", "scope": [PRIYA]}]
    assert {"glob": "knowledge-base/sources/**", "kinds": ["meeting", "thread"], "ceiling": "people"} in pol[
        "federation_ceilings"
    ]
    assert pol["federation_ceilings_overlay"] == []


def test_kb_policy_text_shows_the_federation_keys(vault: Path) -> None:
    kb(vault, "share", WIDGET, "--with", "priya@example.com", "--yes")
    out = kb(vault, "policy").output
    assert "federation.enabled:   true" in out
    assert "knowledge-base/sources/** (kind: meeting, thread) -> people" in out
    assert f"{WIDGET}/** -> {PRIYA}" in out
