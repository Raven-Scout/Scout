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

import io
import json
from pathlib import Path
from typing import Any

import pytest
import requests
import typer
import yaml
from typer.testing import CliRunner

from scout import cli
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


# ----- the Agnes lookups, on the wire (requests faked) ----------------------------------


class _Resp:
    def __init__(self, status: int = 200, body: bytes = b"[]") -> None:
        self.status_code = status
        self.content = body

    def json(self) -> Any:
        return json.loads(self.content)


def _answering(monkeypatch: pytest.MonkeyPatch, answer: Any) -> None:
    def fake_get(*_a: Any, **_k: Any) -> Any:
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(requests, "get", fake_get)


def test_a_lookup_sends_the_pat_and_compares_emails_case_insensitively(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_get(url: str, **kwargs: Any) -> _Resp:
        seen.update(url=url, **kwargs)
        return _Resp(200, b'{"data": [{"email": " Priya@Example.com "}, "junk"]}')

    monkeypatch.setattr(requests, "get", fake_get)
    assert share.Directory("https://agnes.example.com/", "tok").colleague("priya@example.com") == "known"
    assert seen["url"] == "https://agnes.example.com/api/v1/colleagues"
    assert seen["params"] == {"q": "priya@example.com"}
    assert seen["headers"]["Authorization"] == "Bearer tok"
    assert seen["timeout"] == 5.0


@pytest.mark.parametrize(
    "answer",
    [
        requests.ConnectionError("down"),
        requests.Timeout("slow"),
        _Resp(200, b"not json"),
        _Resp(204, b""),
        _Resp(404, b'{"detail": "Not Found"}'),
        _Resp(401, b'{"detail": "no"}'),
        _Resp(500, b"oops"),
    ],
)
def test_every_lookup_failure_is_soft(monkeypatch: pytest.MonkeyPatch, answer: Any) -> None:
    _answering(monkeypatch, answer)
    d = share.Directory("https://agnes.example.com", "tok")
    assert d.colleague("priya@example.com") == "unchecked"
    assert d.share_targets() is None


def test_a_200_without_a_data_list_is_unchecked_not_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    _answering(monkeypatch, _Resp(200, b'{"rows": []}'))
    assert share.Directory("https://agnes.example.com", "tok").colleague("priya@example.com") == "unchecked"


def test_share_targets_skip_junk_and_flag_everyone(monkeypatch: pytest.MonkeyPatch) -> None:
    body = json.dumps(
        [
            "junk",
            {"name": "No id"},
            {"id": "g1"},
            {"id": "g2", "name": "Team", "is_everyone": False},
            {"id": "everyone"},
        ]
    )
    _answering(monkeypatch, _Resp(200, body.encode()))
    assert share.Directory("https://agnes.example.com", "tok").share_targets() == [
        share.ShareTarget("g1", "g1"),
        share.ShareTarget("g2", "Team"),
        share.ShareTarget("everyone", "everyone", True),
    ]


def _link(vault: Path, url: str = "https://agnes.example.com") -> None:
    from scout.repo_sync import LinkState, save_state

    save_state(vault, LinkState(agnes_url=url, clone_url="", linked_at="", last_laptop="", last_mirror=""))


def test_a_linked_vault_has_a_directory(vault: Path) -> None:
    from scout.repo_sync import save_token

    _link(vault)
    save_token(vault, "tok")
    d = share.directory_for(vault)
    assert d is not None and d.base_url == "https://agnes.example.com"


def test_a_link_with_no_token_or_url_or_a_torn_state_has_no_directory(vault: Path) -> None:
    from scout.repo_sync import save_token, state_path

    _link(vault)
    assert share.directory_for(vault) is None  # no token
    save_token(vault, "tok")
    _link(vault, url="")
    assert share.directory_for(vault) is None  # no url
    state_path(vault).write_text("{torn", encoding="utf-8")
    assert share.directory_for(vault) is None


# ----- arguments -------------------------------------------------------------------------


def test_with_takes_repeats_commas_and_spare_positionals(vault: Path) -> None:
    res = kb(vault, "share", WIDGET, "--with", "priya@example.com,sam@example.com", "--dry-run", "--json")
    assert json.loads(res.stdout)["principals"] == [PRIYA, SAM]
    res = kb(vault, "share", WIDGET, "--with", "priya@example.com", "sam@example.com", "--dry-run", "--json")
    assert json.loads(res.stdout)["principals"] == [PRIYA, SAM]
    res = kb(
        vault, "share", WIDGET, "--with", "priya@example.com", "--with", "PRIYA@example.com", "--dry-run", "--json"
    )
    assert json.loads(res.stdout)["principals"] == [PRIYA]
    res = kb(vault, "share", WIDGET, "--with", "person:priya@example.com", "--dry-run", "--json")
    assert json.loads(res.stdout)["principals"] == [PRIYA]


def test_share_needs_someone_to_share_with(vault: Path) -> None:
    res = kb(vault, "share", WIDGET)
    assert res.exit_code == 2 and "at least one" in res.output


@pytest.mark.parametrize("command", ["share", "unshare"])
def test_a_target_outside_the_vault_or_not_a_note_is_a_usage_error(vault: Path, tmp_path: Path, command: str) -> None:
    (vault / WIDGET / "data.txt").write_text("x\n", encoding="utf-8")
    for target in (str(tmp_path / "elsewhere"), f"{WIDGET}/data.txt", f"{WIDGET}/missing.md"):
        res = kb(vault, command, target, "--with", "priya@example.com")
        assert res.exit_code == 2, target
        assert "error:" in res.output


def test_a_refusal_in_json_says_nothing_was_written(vault: Path) -> None:
    res = kb(vault, "share", EMAIL, "--with", "priya@example.com", "--yes", "--json")
    assert res.exit_code == 1
    data = json.loads(res.stdout)
    assert data["written"] is False and "refused" in data["refused"]


def test_json_without_yes_previews_and_writes_nothing(vault: Path) -> None:
    res = kb(vault, "share", WIDGET, "--with", "priya@example.com", "--json")
    assert res.exit_code == 1
    assert json.loads(res.stdout)["written"] is False
    assert "federation_defaults" not in overlay(vault)


def test_json_with_yes_says_it_was_written(vault: Path) -> None:
    res = kb(vault, "share", WIDGET, "--with", "priya@example.com", "--yes", "--json")
    assert res.exit_code == 0 and json.loads(res.stdout)["written"] is True


def test_a_terminal_is_asked_to_confirm(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    argv = ["kb", "share", WIDGET, "--with", "priya@example.com", "--repo", str(vault)]
    res = runner.invoke(app, argv, input="n\n")
    assert res.exit_code == 1 and "Nothing was written." in res.output
    assert "federation_defaults" not in overlay(vault)
    res = runner.invoke(app, argv, input="y\n")
    assert res.exit_code == 0 and "Wrote scout-config.yaml" in res.output
    assert overlay(vault)["federation_defaults"] == [{"glob": f"{WIDGET}/**", "scope": [PRIYA]}]


@pytest.mark.parametrize("bad", ["priya", "group:"])
def test_unshare_with_a_bad_value_is_a_usage_error(vault: Path, bad: str) -> None:
    assert kb(vault, "unshare", WIDGET, "--with", bad).exit_code == 2


# ----- group resolution and the picker -----------------------------------------------------


GROUPS = [
    {"id": "a1b2c3", "name": "Widget team", "is_everyone": False},
    {"id": "d4e5f6", "name": "Twins", "is_everyone": False},
    {"id": "f7a8b9", "name": "twins", "is_everyone": False},
    {"id": "everyone", "name": "Everyone", "is_everyone": True},
]


def test_people_and_groups_mix_and_one_group_named_twice_is_one_principal() -> None:
    principals, names, warnings = share.resolve_groups(
        [PRIYA, "group:a1b2c3", "group:WIDGET TEAM"], FakeDirectory(groups=GROUPS)
    )
    assert principals == [PRIYA, "group:a1b2c3"]
    assert names == {"group:a1b2c3": "Widget team"} and warnings == []


def test_an_ambiguous_group_name_needs_its_id() -> None:
    with pytest.raises(share.ShareUsage, match="give its id"):
        share.resolve_groups(["group:twins"], FakeDirectory(groups=GROUPS))
    assert share.resolve_groups(["group:d4e5f6"], FakeDirectory(groups=GROUPS))[0] == ["group:d4e5f6"]


def test_offline_everyone_is_refused_and_a_name_needs_an_id() -> None:
    with pytest.raises(share.ShareRefused, match="Everyone"):
        share.resolve_groups(["group:Everyone"], None)
    with pytest.raises(share.ShareUsage, match="give the group's id"):
        share.resolve_groups(["group:Widget team"], None)


def test_an_agnes_that_cant_answer_is_treated_as_offline() -> None:
    principals, names, warnings = share.resolve_groups(["group:team-1"], FakeDirectory(groups=None))
    assert principals == ["group:team-1"] and names == {}
    assert "couldn't check it with Agnes" in warnings[0]


def test_two_unchecked_emails_read_in_the_plural() -> None:
    assert "these emails are" in share.check_people([PRIYA, SAM], None)[0]
    assert "these emails are" in share.check_people([PRIYA, SAM], FakeDirectory(colleagues=None))[0]
    assert share.check_people(["group:team-1"], None) == []


def test_pick_group_needs_a_link_and_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    with pytest.raises(typer.Exit) as e:
        cli._pick_groups(None)
    assert e.value.exit_code == 1
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    with pytest.raises(typer.Exit):
        cli._pick_groups(FakeDirectory(groups=GROUPS))


def test_interactive_is_whether_stdin_is_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert cli._interactive() is False


@pytest.mark.parametrize("groups", [None, [], [GROUPS[3]]])
def test_pick_group_with_nothing_to_pick_is_refused(
    monkeypatch: pytest.MonkeyPatch, groups: list[dict[str, Any]] | None
) -> None:
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    with pytest.raises(typer.Exit) as e:
        cli._pick_groups(FakeDirectory(groups=groups))
    assert e.value.exit_code == 1


def test_pick_group_lists_targets_without_everyone_and_takes_numbers(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr(typer, "prompt", lambda *_a, **_k: "1, 3")
    assert cli._pick_groups(FakeDirectory(groups=GROUPS)) == ["group:a1b2c3", "group:f7a8b9"]
    out = capsys.readouterr().out
    assert "1. Widget team  (group:a1b2c3)" in out and "Everyone" not in out


@pytest.mark.parametrize("answer", ["x", "0", "9"])
def test_pick_group_refuses_a_number_that_isnt_listed(monkeypatch: pytest.MonkeyPatch, answer: str) -> None:
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr(typer, "prompt", lambda *_a, **_k: answer)
    with pytest.raises(typer.Exit) as e:
        cli._pick_groups(FakeDirectory(groups=GROUPS))
    assert e.value.exit_code == 2


def test_the_share_command_takes_the_picked_groups(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_pick_groups", lambda _d: ["group:team-1"])
    res = kb(vault, "share", WIDGET, "--pick-group", "--dry-run", "--json")
    assert res.exit_code == 0, res.output
    assert json.loads(res.stdout)["principals"] == ["group:team-1"]


# ----- the overlay file -----------------------------------------------------------------------


def _privacy(vault: Path, block: str) -> None:
    """Append to the fixture's ``privacy:`` block, which ends the file."""
    with (vault / "scout-config.yaml").open("a", encoding="utf-8") as fh:
        fh.write(block)


@pytest.mark.parametrize(
    "body",
    [
        b"privacy: [unclosed\n",
        b"- just\n- a list\n",
        b"\xff\xfeprivacy: {}\n",
        b"privacy: just-a-string\n",
        b"privacy:\n  federation_defaults: {glob: x}\n",
        b"privacy:\n  federation_defaults: [not-a-mapping]\n",
    ],
    ids=["bad-yaml", "a-list", "not-utf8", "privacy-string", "defaults-mapping", "defaults-entry-string"],
)
@pytest.mark.parametrize("command", ["share", "unshare"])
def test_a_config_these_commands_cant_edit_is_refused(vault: Path, command: str, body: bytes) -> None:
    (vault / "scout-config.yaml").write_bytes(body)
    res = kb(vault, command, WIDGET, "--with", "priya@example.com", "--yes" if command == "share" else "--dry-run")
    assert res.exit_code == 1, res.output
    assert "refused" in res.output
    assert (vault / "scout-config.yaml").read_bytes() == body


def test_an_empty_privacy_block_is_fine(vault: Path) -> None:
    (vault / "scout-config.yaml").write_text("privacy:\n", encoding="utf-8")
    assert kb(vault, "share", WIDGET, "--with", "priya@example.com", "--yes").exit_code == 0
    assert overlay(vault)["federation"] == {"enabled": True}


def test_an_empty_config_is_fine(vault: Path) -> None:
    (vault / "scout-config.yaml").write_text("# nothing yet\n", encoding="utf-8")
    assert kb(vault, "share", WIDGET, "--with", "priya@example.com", "--yes").exit_code == 0
    assert overlay(vault)["federation"] == {"enabled": True}


def test_a_share_writes_a_config_that_wasnt_there(vault: Path) -> None:
    (vault / "scout-config.yaml").unlink()
    assert kb(vault, "share", WIDGET, "--with", "priya@example.com", "--yes").exit_code == 0
    assert overlay(vault)["federation_defaults"] == [{"glob": f"{WIDGET}/**", "scope": [PRIYA]}]


def test_a_write_that_wouldnt_load_back_is_refused(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    before = (vault / "scout-config.yaml").read_text(encoding="utf-8")
    plan = share.plan_share(vault, WIDGET, ["priya@example.com"], directory=None)
    monkeypatch.setattr("scout.scripts.bootstrap._dump_keeping_comments", lambda *_a: "other: value\n")
    with pytest.raises(RuntimeError, match="could not write"):
        share.apply(vault, plan)
    assert (vault / "scout-config.yaml").read_text(encoding="utf-8") == before


def test_other_federation_keys_survive_a_share(vault: Path) -> None:
    _privacy(vault, "  federation:\n    enabled: false\n    note: mine\n")
    assert kb(vault, "share", WIDGET, "--with", "priya@example.com", "--yes").exit_code == 0
    assert overlay(vault)["federation"] == {"enabled": True, "note": "mine"}


def test_a_non_mapping_federation_key_is_replaced_by_the_switch(vault: Path) -> None:
    _privacy(vault, "  federation: true\n")
    assert kb(vault, "share", WIDGET, "--with", "priya@example.com", "--yes").exit_code == 0
    assert overlay(vault)["federation"] == {"enabled": True}


def test_an_entry_with_no_glob_is_left_alone(vault: Path) -> None:
    junk = {"scope": [SAM]}
    _privacy(vault, f"  federation_defaults:\n    - {json.dumps(junk)}\n")
    assert kb(vault, "share", WIDGET, "--with", "priya@example.com", "--yes").exit_code == 0
    assert overlay(vault)["federation_defaults"] == [junk, {"glob": f"{WIDGET}/**", "scope": [PRIYA]}]
    assert kb(vault, "unshare", WIDGET).exit_code == 0
    assert overlay(vault)["federation_defaults"] == [junk]


# ----- what a share leaves out, and why --------------------------------------------------------


def test_an_empty_folder_uses_the_ceiling_a_new_note_there_would_have(vault: Path) -> None:
    (vault / "knowledge-base/projects/empty").mkdir(parents=True)
    (vault / "knowledge-base/people/empty").mkdir(parents=True)
    res = kb(vault, "share", "knowledge-base/projects/empty", "--with", "priya@example.com", "--yes")
    assert res.exit_code == 0 and "Newly shared: 0 note(s)" in res.output
    assert overlay(vault)["federation_defaults"] == [{"glob": "knowledge-base/projects/empty/**", "scope": [PRIYA]}]
    assert kb(vault, "share", "knowledge-base/people/empty", "--with", "priya@example.com", "--yes").exit_code == 1


def test_a_plumbing_note_cant_be_shared(vault: Path) -> None:
    _note(vault, ".claude/note.md", "name: Note")
    res = kb(vault, "share", ".claude/note.md", "--with", "priya@example.com", "--yes")
    assert res.exit_code == 1 and "can't be shared" in res.output


def test_a_local_only_note_is_refused_and_excluded_from_its_folder(vault: Path) -> None:
    keys = f"{WIDGET}/keys/notes.md"
    _note(vault, keys, "name: Keys")
    _privacy(vault, f'    - "{WIDGET}/keys/**"\n')  # continues the fixture's local_only_add list
    res = kb(vault, "share", keys, "--with", "priya@example.com", "--yes")
    assert res.exit_code == 1 and "local-only" in res.output
    res = kb(vault, "share", WIDGET, "--with", "priya@example.com", "--dry-run")
    assert f"{keys}  local-only (never leaves the laptop)" in res.output


def test_a_note_over_a_narrowed_ceiling_is_excluded_for_the_ceiling(vault: Path) -> None:
    _privacy(vault, f'  federation_ceilings:\n    - {{glob: "{WIDGET}/design/**", ceiling: none}}\n')
    res = kb(vault, "share", WIDGET, "--with", "priya@example.com", "--dry-run")
    assert res.exit_code == 0
    assert f"{DESIGN}  its folder can't be shared this widely" in res.output


def test_a_folder_narrowed_to_people_is_shared_note_by_note(vault: Path) -> None:
    _privacy(vault, f'  federation_ceilings:\n    - {{glob: "{WIDGET}/design/**", ceiling: people}}\n')
    res = kb(vault, "share", f"{WIDGET}/design", "--with", "priya@example.com", "--yes")
    assert res.exit_code == 1 and "note by note" in res.output
    assert kb(vault, "share", DESIGN, "--with", "priya@example.com", "--yes").exit_code == 0
    assert label(vault, DESIGN)["federation_scope"] == [PRIYA]


def test_a_note_an_earlier_entry_decides_says_so(vault: Path) -> None:
    _privacy(vault, '  federation_defaults:\n    - {glob: "knowledge-base/projects/*/plan.md", scope: none}\n')
    res = kb(vault, "share", WIDGET, "--with", "priya@example.com", "--dry-run")
    assert f"{PLAN}  an earlier federation_defaults entry decides" in res.output


def test_the_preview_prints_private_links_and_the_switch(vault: Path) -> None:
    _note(vault, PLAN, "name: Widget plan\ntype: project", "Ask [[sam]] first.\n")
    out = kb(vault, "share", WIDGET, "--with", "priya@example.com", "--dry-run").output
    assert "1 note(s) about to be shared link or name a private note" in out
    assert f"{PLAN}  line(s) 5" in out
    assert "This turns sharing on for this vault" in out


def test_the_preview_counts_notes_already_shared(vault: Path) -> None:
    kb(vault, "share", WIDGET, "--with", "priya@example.com", "--yes")
    out = kb(vault, "share", WIDGET, "--with", "priya@example.com", "--dry-run").output
    assert "Already shared with them: 2 note(s)" in out
    assert "This turns sharing on" not in out


def test_a_note_that_vanishes_during_the_preview_is_skipped(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = share._labels

    def racing(v: Path, policy: Any, *, persist: bool) -> dict[str, Any]:
        labels = dict(real(v, policy, persist=persist))
        if not persist:
            labels.pop(PLAN, None)
        return labels

    kb(vault, "share", WIDGET, "--with", "priya@example.com", "--yes")
    monkeypatch.setattr(share, "_labels", racing)
    assert PLAN not in [p for p, _ in share.plan_unshare(vault, WIDGET, []).stopped]
    kb(vault, "unshare", WIDGET)
    assert PLAN not in [p for p, _ in share.plan_share(vault, WIDGET, ["priya@example.com"], directory=None).newly]


def test_a_note_unreadable_for_the_link_check_is_skipped(vault: Path) -> None:
    assert share._private_links(vault, load_policy(vault), [f"{WIDGET}/missing.md"]) == []


# ----- org entries (stored; they admit nobody) ----------------------------------------------------


_ORG_WIDGET = f'  federation: {{enabled: true}}\n  federation_defaults:\n    - {{glob: "{WIDGET}/**", scope: org}}\n'


def test_a_person_added_to_an_org_entry_stays_unshared_and_the_preview_says_so(vault: Path) -> None:
    _privacy(vault, _ORG_WIDGET)
    data = json.loads(kb(vault, "share", WIDGET, "--with", "priya@example.com", "--dry-run", "--json").stdout)
    assert data["newly_shared"] == []
    assert {"path": PLAN, "reason": "earlier-entry"} in data["excluded"]


def test_a_subfolder_of_an_org_entry_gets_only_the_named_people(vault: Path) -> None:
    _privacy(vault, _ORG_WIDGET)
    assert kb(vault, "share", f"{WIDGET}/design", "--with", "priya@example.com", "--yes").exit_code == 0
    assert label(vault, DESIGN)["federation_scope"] == [PRIYA]


def test_unsharing_a_person_leaves_an_org_entry_and_unsharing_org_removes_it(vault: Path) -> None:
    _privacy(vault, _ORG_WIDGET)
    res = kb(vault, "unshare", WIDGET, "--with", "priya@example.com")
    assert res.exit_code == 0 and "Nothing to change" in res.output
    assert label(vault, PLAN)["federation_scope"] == "org"
    res = kb(vault, "unshare", WIDGET, "--with", "org", "--json")
    assert json.loads(res.stdout)["stopped"][0]["principals"] == ["org"]
    assert overlay(vault)["federation_defaults"] == []


# ----- kb unshare edges ---------------------------------------------------------------------------


def test_unshare_dry_run_writes_nothing(vault: Path) -> None:
    kb(vault, "share", WIDGET, "--with", "priya@example.com", "--yes")
    data = json.loads(kb(vault, "unshare", WIDGET, "--dry-run", "--json").stdout)
    assert data["written"] is False and data["changes_overlay"] is True
    assert label(vault, PLAN)["federation_scope"] == [PRIYA]


def test_unsharing_someone_a_broader_entry_doesnt_name_changes_nothing(vault: Path) -> None:
    kb(vault, "share", WIDGET, "--with", "priya@example.com", "--yes")
    res = kb(vault, "unshare", f"{WIDGET}/design", "--with", "sam@example.com")
    assert res.exit_code == 0 and "Nothing to change" in res.output
    assert overlay(vault)["federation_defaults"] == [{"glob": f"{WIDGET}/**", "scope": [PRIYA]}]


def test_a_note_sharing_itself_with_someone_else_isnt_listed(vault: Path) -> None:
    kb(vault, "share", WIDGET, "--with", "priya@example.com", "--yes")
    data = json.loads(kb(vault, "unshare", WIDGET, "--with", "priya@example.com", "--json").stdout)
    assert data["still_shared_by_frontmatter"] == []
    assert label(vault, SOLO)["federation_scope"] == [SAM]


def test_unshare_turns_a_group_name_into_its_id(vault: Path) -> None:
    groups = [{"id": "a1b2c3", "name": "Widget team", "is_everyone": False}]
    assert kb(vault, "share", WIDGET, "--with", "group:a1b2c3", "--with", "priya@example.com", "--yes").exit_code == 0
    plan = share.plan_unshare(
        vault, WIDGET, ["group:widget team", "sam@example.com"], directory=FakeDirectory(groups=groups)
    )
    assert plan.principals == ["group:a1b2c3", SAM]
    share.apply(vault, plan)
    assert overlay(vault)["federation_defaults"] == [{"glob": f"{WIDGET}/**", "scope": [PRIYA]}]


def test_unshare_offline_keeps_a_group_name_as_given(vault: Path) -> None:
    plan = share.plan_unshare(vault, WIDGET, ["group:widget team"], directory=FakeDirectory(groups=None))
    assert plan.principals == ["group:widget team"]


def test_an_order_unshare_cant_narrow_is_refused(vault: Path) -> None:
    _privacy(
        vault,
        "  federation: {enabled: true}\n  federation_defaults:\n"
        f'    - {{glob: "knowledge-base/projects/*/plan.md", scope: ["{PRIYA}"]}}\n',
    )
    before = (vault / "scout-config.yaml").read_text(encoding="utf-8")
    res = kb(vault, "unshare", WIDGET, "--with", "priya@example.com")
    assert res.exit_code == 1 and "couldn't narrow" in res.output
    assert (vault / "scout-config.yaml").read_text(encoding="utf-8") == before


@pytest.mark.parametrize(
    ("outer", "inner", "want"),
    [
        ("a/**", "a/b/**", True),
        ("a/**", "a/**", True),
        ("a/**", "a/b.md", True),
        ("a/**", "ab/**", False),
        ("a/b.md", "a/b.md", False),
        ("a/*/c/**", "a/b/c/**", False),
        ("a/**", "a/*/c.md", False),
    ],
)
def test_covers_glob(outer: str, inner: str, want: bool) -> None:
    assert share._covers_glob(outer, inner) is want


def test_an_exclusion_stays_while_a_later_entry_would_share_the_notes(vault: Path) -> None:
    kb(vault, "share", WIDGET, "--with", "priya@example.com", "--yes")
    kb(vault, "share", f"{WIDGET}/design", "--with", "sam@example.com", "--yes")
    assert kb(vault, "unshare", f"{WIDGET}/design").exit_code == 0
    assert overlay(vault)["federation_defaults"] == [
        {"glob": f"{WIDGET}/design/**", "scope": "none"},
        {"glob": f"{WIDGET}/**", "scope": [PRIYA]},
    ]
    assert label(vault, DESIGN)["federation_scope"] == "none"


# ----- kb shares edges ------------------------------------------------------------------------------


def test_nothing_shared_says_so(vault: Path) -> None:
    out = kb(vault, "shares").output
    assert "Sharing is off" in out and "Nothing is shared." in out


def test_with_sharing_off_a_default_lists_its_notes_as_off_and_private_stays_private(vault: Path) -> None:
    _privacy(vault, f'  federation_defaults:\n    - {{glob: "{WIDGET}/**", scope: ["{PRIYA}"]}}\n')
    data = json.loads(kb(vault, "shares", "--json").stdout)
    assert data["enabled"] is False
    (row,) = data["shares"]
    assert row["notes"] == 0
    assert row["excluded"]["private"] == [SECRET]
    assert PLAN in row["excluded"]["sharing-off"]
    assert "excluded, sharing is off" in kb(vault, "shares").output


def test_a_self_sharing_note_outside_any_share_has_its_own_row(vault: Path) -> None:
    _privacy(vault, "  federation: {enabled: true}\n")
    data = json.loads(kb(vault, "shares", "--json").stdout)
    assert [(r["glob"], r["source"], r["principals"]) for r in data["shares"]] == [(SOLO, "frontmatter", [SAM])]
    assert "its own frontmatter" in kb(vault, "shares").output


def test_a_none_entry_and_a_repeated_glob_get_no_row_of_their_own(vault: Path) -> None:
    _privacy(
        vault,
        "  federation: {enabled: true}\n  federation_defaults:\n"
        f'    - {{glob: "{WIDGET}/design/**", scope: none}}\n'
        f'    - {{glob: "{WIDGET}/**", scope: ["{PRIYA}"]}}\n'
        f'    - {{glob: "{WIDGET}/**", scope: ["{SAM}"]}}\n',
    )
    data = json.loads(kb(vault, "shares", "--json").stdout)
    rows = [r for r in data["shares"] if r["source"] == "default"]
    assert [(r["glob"], r["principals"]) for r in rows] == [(f"{WIDGET}/**", [PRIYA])]
    assert DESIGN not in rows[0]["shared"]


def test_kb_shares_shows_a_groups_current_name(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    groups = [{"id": "a1b2c3", "name": "Widget team", "is_everyone": False}]
    assert kb(vault, "share", WIDGET, "--with", "group:a1b2c3", "--yes").exit_code == 0
    monkeypatch.setattr(share, "directory_for", lambda _v: FakeDirectory(groups=groups))
    assert "with Widget team (group:a1b2c3)" in kb(vault, "shares").output
    assert json.loads(kb(vault, "shares", "--json").stdout)["group_names"] == {"group:a1b2c3": "Widget team"}


def test_group_names_ask_agnes_only_when_a_row_names_a_group() -> None:
    person = share.ShareRow(glob="x/**", source="default", principals=[PRIYA])
    group = share.ShareRow(glob="y/**", source="default", principals=["group:a1b2c3"])
    assert share.group_names(None, [group]) == {}
    assert share.group_names(FakeDirectory(groups=None), [person]) == {}
    assert share.group_names(FakeDirectory(groups=None), [group]) == {}
