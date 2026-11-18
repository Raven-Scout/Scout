"""Shared Areas S1b: ``kb unshare`` stops a share in Agnes at once, ``kb shares`` shows the stops, and only
``kb share`` at the owner's keyboard clears one (keboola/agnes-the-ai-analyst#6090 §4, §5.6; A8b is #7450).

Agnes is :mod:`fake_agnes_shares` on 127.0.0.1; ``requests`` may reach nothing
else. The vault is the made-up one of ``test_kb_share`` (owner Alex, colleagues
Priya and Sam, project ``widget``), linked with a made-up JWT in a temp HOME.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import requests
import yaml
from typer.testing import CliRunner

from scout.cli import app
from scout.kb import share, share_stops
from scout.kb.share import Target

from .fake_agnes_shares import FakeAgnes, fake_jwt, serve
from .test_kb_share import CONFIG, DESIGN, PLAN, PRIYA, SAM, SECRET, WIDGET, _note

runner = CliRunner()
GLOB = f"{WIDGET}/**"
PAT = fake_jwt("pat")
GROUP = "group:a1b2c3"


@pytest.fixture
def agnes() -> Iterator[FakeAgnes]:
    with serve(FakeAgnes()) as fake:
        yield fake


@pytest.fixture(autouse=True)
def _only_the_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    real_request, real_get = requests.request, requests.get

    def guarded(method: str, url: str, **kw: Any) -> Any:
        assert url.startswith("http://127.0.0.1:"), f"a test reached {url}"
        return real_request(method, url, **kw)

    def guarded_get(url: str, **kw: Any) -> Any:  # the S1a people/group lookups
        assert url.startswith("http://127.0.0.1:"), f"a test reached {url}"
        return real_get(url, **kw)

    monkeypatch.setattr(requests, "request", guarded)
    monkeypatch.setattr(requests, "get", guarded_get)


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agnes: FakeAgnes) -> Path:
    for key in ("SCOUT_MODE", "SCOUT_READER"):
        monkeypatch.delenv(key, raising=False)
    v = tmp_path / "vault"
    v.mkdir()
    (v / "scout-config.yaml").write_text(CONFIG, encoding="utf-8")
    _note(v, PLAN, "name: Widget plan\ntype: project")
    _note(v, SECRET, "visibility: private", "Salary notes.\n")
    _note(v, DESIGN, "name: Design notes")
    link(v, agnes.url, PAT)
    return v


def link(vault: Path, url: str, token: str) -> None:
    from scout.repo_sync import LinkState, save_state, save_token

    save_state(vault, LinkState(agnes_url=url, clone_url="", linked_at="", last_laptop="", last_mirror=""))
    save_token(vault, token)


def kb(vault: Path, *args: str, env: dict[str, str] | None = None) -> Any:
    return runner.invoke(app, ["kb", *args, "--repo", str(vault)], env=env)


def defaults(vault: Path) -> list[dict[str, Any]]:
    return yaml.safe_load((vault / "scout-config.yaml").read_text(encoding="utf-8"))["privacy"]["federation_defaults"]


def shared_with(vault: Path, agnes: FakeAgnes, *people: str, groups: dict[str, list[str]] | None = None) -> None:
    """The vault shares widget with ``people`` (and ``groups``), and Agnes, synced, lists the same."""
    who = [f"--with={p}" for p in people] + [f"--with={g}" for g in groups or {}]
    agnes.mode, mode = "a8b", agnes.mode
    res = kb(vault, "share", WIDGET, *who, "--yes")
    assert res.exit_code == 0, res.output
    agnes.mode = mode
    agnes.share(GLOB, *people, groups={g[len("group:") :]: m for g, m in (groups or {}).items()})
    agnes.requests.clear()


# ----- kb unshare: the instant stop ------------------------------------------------------------


def test_unshare_stops_the_share_in_agnes_before_it_writes_the_vault(vault: Path, agnes: FakeAgnes) -> None:
    shared_with(vault, agnes, "priya@example.com")
    seen: list[list[dict[str, Any]]] = []
    agnes.on_stop = lambda body: seen.append(defaults(vault))

    res = kb(vault, "unshare", WIDGET)

    assert res.exit_code == 0, res.output
    (stop,) = agnes.calls("POST", "/shares/stop")
    assert stop["body"] == {"glob": GLOB, "write_back": False}  # a stop for everyone, never written back by Agnes
    assert stop["bearer"]
    assert seen == [[{"glob": GLOB, "scope": [PRIYA]}]]  # Agnes stopped it while the vault still shared it
    assert defaults(vault) == []
    assert share_stops.STOPPED_NOTE in res.output
    assert share_stops.VAULT_NOTE in res.output
    assert PAT not in res.output


def test_unshare_one_person_stops_it_for_them_only(vault: Path, agnes: FakeAgnes) -> None:
    shared_with(vault, agnes, "priya@example.com", "sam@example.com")
    data = json.loads(kb(vault, "unshare", WIDGET, "--with", "priya@example.com", "--json").stdout)
    assert [c["body"] for c in agnes.calls("POST", "/shares/stop")] == [
        {"glob": GLOB, "person": PRIYA, "write_back": False}
    ]
    assert data["pending_until_sync"] is False and data["agnes"]["status"] == "stopped"
    assert data["agnes"]["stops"][0]["principal"] == PRIYA
    assert defaults(vault) == [{"glob": GLOB, "scope": [SAM]}]


def test_a_stop_names_the_shares_glob_so_later_notes_stay_stopped(vault: Path, agnes: FakeAgnes) -> None:
    shared_with(vault, agnes, "priya@example.com")
    kb(vault, "unshare", WIDGET)
    # One stop on the glob, not one per note: Agnes applies it to whatever lands under the folder later.
    assert [c["body"]["glob"] for c in agnes.calls("POST", "/shares/stop")] == [GLOB]


def test_a_group_member_is_stopped_alone_and_the_vault_is_left_alone(vault: Path, agnes: FakeAgnes) -> None:
    shared_with(vault, agnes, groups={GROUP: ["sam@example.com", "priya@example.com"]})
    before = defaults(vault)
    res = kb(vault, "unshare", WIDGET, "--with", "sam@example.com")
    assert res.exit_code == 0, res.output
    assert [c["body"] for c in agnes.calls("POST", "/shares/stop")] == [
        {"glob": GLOB, "person": SAM, "write_back": False}
    ]
    assert f"Stopped in Agnes: {GLOB} for sam@example.com" in res.output
    assert "lives in Agnes only" in res.output
    assert defaults(vault) == before
    assert "Nothing to change" not in res.output


def test_a_subfolder_of_a_broader_share_waits_for_the_sync(vault: Path, agnes: FakeAgnes) -> None:
    shared_with(vault, agnes, "priya@example.com")
    res = kb(vault, "unshare", f"{WIDGET}/design")
    assert res.exit_code == 0, res.output
    assert agnes.calls("POST") == []  # Agnes stops only a whole share; never the broader one for this
    assert "Agnes stops only a whole share" in res.output
    assert share.UNSHARE_PENDING in res.output
    assert defaults(vault)[0]["glob"] == f"{WIDGET}/design/**"


def test_unshare_dry_run_asks_agnes_nothing_but_says_what_it_would_stop(vault: Path, agnes: FakeAgnes) -> None:
    shared_with(vault, agnes, "priya@example.com")
    res = kb(vault, "unshare", WIDGET, "--dry-run")
    assert res.exit_code == 0
    assert agnes.calls("POST") == []
    assert f"Would stop in Agnes at once: {GLOB} for everyone" in res.output
    assert defaults(vault) == [{"glob": GLOB, "scope": [PRIYA]}]


def test_an_existing_stop_isnt_made_again(vault: Path, agnes: FakeAgnes) -> None:
    shared_with(vault, agnes, "priya@example.com")
    agnes.add_stop(GLOB, None)
    kb(vault, "unshare", WIDGET)
    assert agnes.calls("POST") == []


def test_a_share_agnes_doesnt_list_yet_has_nothing_to_stop(vault: Path, agnes: FakeAgnes) -> None:
    assert kb(vault, "share", WIDGET, "--with", "priya@example.com", "--yes").exit_code == 0  # not synced
    agnes.requests.clear()
    res = kb(vault, "unshare", WIDGET)
    assert res.exit_code == 0
    assert agnes.calls("POST") == []
    assert "Agnes isn't serving that share" in res.output
    assert defaults(vault) == []


def test_unshare_says_to_pull_first_when_agnes_wrote_on_top(vault: Path, agnes: FakeAgnes) -> None:
    shared_with(vault, agnes, "priya@example.com")
    agnes.pending_server_commits = 1
    res = kb(vault, "unshare", WIDGET)
    assert "Agnes has 1 commit(s) on top of your last push" in res.output


def test_a_sync_only_vault_never_asks_for_a_write_back(vault: Path, agnes: FakeAgnes) -> None:
    text = (vault / "scout-config.yaml").read_text(encoding="utf-8")
    (vault / "scout-config.yaml").write_text(text + '  sync_only:\n    - "knowledge-base/"\n', encoding="utf-8")
    shared_with(vault, agnes, "priya@example.com", "sam@example.com")
    kb(vault, "unshare", WIDGET, "--with", "sam@example.com")
    kb(vault, "unshare", WIDGET)
    bodies = [c["body"] for c in agnes.calls("POST", "/shares/stop")]
    assert len(bodies) == 2 and all(b["write_back"] is False for b in bodies)


# ----- the fallback while A8b isn't there -------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "note"),
    [("old", share_stops.FALLBACK_NOTE), ("sharing_off", share_stops.SHARING_OFF_NOTE)],
)
def test_an_agnes_without_a8b_keeps_the_s1a_unshare(vault: Path, agnes: FakeAgnes, mode: str, note: str) -> None:
    shared_with(vault, agnes, "priya@example.com")
    agnes.mode = mode
    res = kb(vault, "unshare", WIDGET, "--json")
    assert res.exit_code == 0
    data = json.loads(res.stdout)
    assert data["written"] is True and data["pending_until_sync"] is True
    assert data["agnes"]["status"] == "unavailable" and note in data["agnes"]["notes"]
    assert data["agnes"]["failed"] is False  # an Agnes that can't stop isn't a failure: exit 0
    assert defaults(vault) == []
    assert agnes.calls("POST") == []
    res = kb(vault, "unshare", WIDGET, "--with", "sam@example.com")
    assert note in res.output


def test_an_old_agnes_still_shares_and_lists(vault: Path, agnes: FakeAgnes) -> None:
    agnes.mode = "old"
    res = kb(vault, "share", WIDGET, "--with", "priya@example.com", "--yes")
    assert res.exit_code == 0, res.output
    assert defaults(vault) == [{"glob": GLOB, "scope": [PRIYA]}]
    assert agnes.calls("POST") == []
    res = kb(vault, "shares")
    assert res.exit_code == 0 and f"Stops in Agnes: not shown. {share_stops.FALLBACK_NOTE}" in res.output
    assert json.loads(kb(vault, "shares", "--json").stdout)["agnes"]["available"] is False


def test_an_unreachable_agnes_keeps_the_vault_change(vault: Path, agnes: FakeAgnes) -> None:
    shared_with(vault, agnes, "priya@example.com")
    link(vault, "http://127.0.0.1:9", PAT)  # nothing listens on the discard port
    res = kb(vault, "unshare", WIDGET)
    assert res.exit_code == 4, res.output  # the vault is narrowed, but no stop was confirmed
    assert share_stops.RETRY_NOTE in res.output
    assert "Agnes couldn't be asked" in res.output and share.UNSHARE_PENDING in res.output
    assert defaults(vault) == []


def test_an_unlinked_vault_unshares_as_before(vault: Path, agnes: FakeAgnes) -> None:
    from scout.repo_sync import state_path

    shared_with(vault, agnes, "priya@example.com")
    state_path(vault).unlink()
    data = json.loads(kb(vault, "unshare", WIDGET, "--json").stdout)
    assert data["pending_until_sync"] is True and data["agnes"]["status"] == "unlinked"
    assert agnes.requests == []


# ----- kb shares: the stops ----------------------------------------------------------------------


def test_kb_shares_lists_open_unlisted_and_recently_cleared_stops(vault: Path, agnes: FakeAgnes) -> None:
    shared_with(vault, agnes, "priya@example.com", "sam@example.com")
    agnes.add_stop(GLOB, PRIYA)
    agnes.add_stop("knowledge-base/projects/gone/**", None)
    agnes.add_stop(GLOB, SAM, cleared=True)

    res = kb(vault, "shares")

    assert res.exit_code == 0, res.output
    assert "STOPPED in Agnes for priya@example.com since 2026-11-16 09:30 UTC" in res.output
    assert "knowledge-base/projects/gone/**  STOPPED for everyone since 2026-11-16 09:30 UTC" in res.output
    assert "Stops cleared in the last 30 days:" in res.output
    assert f"{GLOB}  for sam@example.com: stopped 2026-11-16 09:30 UTC, cleared 2026-11-17 10:00 UTC" in res.output
    data = json.loads(kb(vault, "shares", "--json").stdout)
    (row,) = data["shares"]
    assert [s["principal"] for s in row["stops"]] == [PRIYA]
    assert [s["glob"] for s in data["agnes"]["stops_not_listed"]] == ["knowledge-base/projects/gone/**"]
    assert data["agnes"]["cleared_stops"][0]["cleared_at"] == "2026-11-17T10:00:00Z"
    assert all(c["method"] == "GET" for c in agnes.requests)


def test_kb_shares_with_no_stops_says_so(vault: Path, agnes: FakeAgnes) -> None:
    shared_with(vault, agnes, "priya@example.com")
    assert "Stops in Agnes: none." in kb(vault, "shares").output


# ----- kb share: clearing a stop -----------------------------------------------------------------


def test_sharing_again_clears_the_stop_for_the_person_named(vault: Path, agnes: FakeAgnes) -> None:
    shared_with(vault, agnes, "priya@example.com", "sam@example.com")
    assert kb(vault, "unshare", WIDGET, "--with", "priya@example.com").exit_code == 0
    kb(vault, "unshare", WIDGET, "--with", "sam@example.com")
    priya_stop, sam_stop = agnes.open_stops()
    agnes.requests.clear()

    preview = kb(vault, "share", WIDGET, "--with", "priya@example.com", "--dry-run")
    assert f"Clears the stop in Agnes on {GLOB} for priya@example.com" in preview.output
    assert f"Still stopped in Agnes: {GLOB} for sam@example.com" in preview.output
    assert agnes.calls("POST") == []

    res = kb(vault, "share", WIDGET, "--with", "priya@example.com", "--yes")

    assert res.exit_code == 0, res.output
    (clear,) = agnes.calls("POST", "/clear-stop")
    assert clear["body"] == {"stop_id": priya_stop["stop_id"]} and clear["bearer"]
    assert [s["stop_id"] for s in agnes.open_stops()] == [sam_stop["stop_id"]]
    assert f"Cleared the stop in Agnes on {GLOB} for priya@example.com" in res.output
    assert PAT not in res.output


def test_clearing_a_stop_for_everyone_keeps_the_people_not_named_stopped(vault: Path, agnes: FakeAgnes) -> None:
    shared_with(vault, agnes, "priya@example.com", "sam@example.com")
    kb(vault, "unshare", WIDGET)  # Agnes still lists both until the next sync
    (everyone,) = agnes.open_stops()
    agnes.requests.clear()

    res = kb(vault, "share", WIDGET, "--with", "priya@example.com", "--yes")

    assert res.exit_code == 0, res.output
    posts = [(c["path"].rsplit("/", 1)[1], c["body"]) for c in agnes.calls("POST")]
    assert posts == [
        ("stop", {"glob": GLOB, "person": SAM, "write_back": False}),  # Sam first, so he is never let back in
        ("clear-stop", {"stop_id": everyone["stop_id"]}),
    ]
    assert [(s["glob"], s["principal"]) for s in agnes.open_stops()] == [(GLOB, SAM)]


def test_a_share_already_in_the_vault_still_clears_its_stop(vault: Path, agnes: FakeAgnes) -> None:
    shared_with(vault, agnes, "priya@example.com")
    agnes.add_stop(GLOB, PRIYA)  # stopped in Agnes only (the web page, write_back off)
    res = kb(vault, "share", WIDGET, "--with", "priya@example.com", "--yes")
    assert res.exit_code == 0, res.output
    assert len(agnes.calls("POST", "/clear-stop")) == 1
    assert "Nothing to change" not in res.output


def test_a_clear_is_a_widening_so_it_needs_the_confirm(vault: Path, agnes: FakeAgnes) -> None:
    shared_with(vault, agnes, "priya@example.com")
    agnes.add_stop(GLOB, PRIYA)
    res = kb(vault, "share", WIDGET, "--with", "priya@example.com")  # no tty, no --yes
    assert res.exit_code == 1
    assert agnes.calls("POST") == []


def test_a_stop_on_a_broader_share_isnt_cleared_by_a_subfolder_share(vault: Path, agnes: FakeAgnes) -> None:
    shared_with(vault, agnes, "priya@example.com")
    agnes.add_stop(GLOB, PRIYA)
    res = kb(vault, "share", f"{WIDGET}/design", "--with", "priya@example.com", "--yes")
    assert res.exit_code == 0, res.output
    assert agnes.calls("POST") == []
    assert "a stop on the broader share" in res.output


def test_agnes_refusing_a_clear_leaves_the_stop_in_force(vault: Path, agnes: FakeAgnes) -> None:
    shared_with(vault, agnes, "priya@example.com")
    agnes.add_stop(GLOB, PRIYA)
    agnes.clear_refuses = True
    res = kb(vault, "share", WIDGET, "--with", "priya@example.com", "--yes")
    assert res.exit_code == 0
    assert "Agnes refused to clear it" in res.output and "still in force" in res.output
    assert len(agnes.open_stops()) == 1


# ----- refusals: only the owner at the keyboard clears -------------------------------------------


@pytest.mark.parametrize(
    "env",
    [
        {"SCOUT_MODE": "briefing"},
        {"SCOUT_READER": "autonomous"},
        {"SCOUT_READER": "agent:person:sam@example.com"},
        {"SCOUT_READER": "delegate:phone"},
        {"SCOUT_READER": ""},
    ],
    ids=["scheduled", "engine-run", "agent", "delegate", "malformed-reader"],
)
def test_a_clear_is_refused_outside_the_owners_keyboard(vault: Path, agnes: FakeAgnes, env: dict[str, str]) -> None:
    client = share_stops.client_for(vault)
    assert client is not None
    assert share_stops.clear_refusal(vault, env, client) is not None
    plan = share_stops.ClearPlan(clears=[share_stops.Stop("stop0001", GLOB, None, "cli", "not_requested", "")])
    with pytest.raises(share_stops.ClearRefused, match="Only the owner, at the keyboard"):
        share_stops.apply_clears(vault, env, client, plan)
    assert agnes.requests == []  # refused before anything was sent


@pytest.mark.parametrize("env", [{"SCOUT_MODE": "dreaming"}, {"SCOUT_READER": "delegate:phone"}])
def test_kb_share_in_a_run_is_refused_and_sends_nothing(vault: Path, agnes: FakeAgnes, env: dict[str, str]) -> None:
    shared_with(vault, agnes, "priya@example.com")
    agnes.add_stop(GLOB, PRIYA)
    res = kb(vault, "share", WIDGET, "--with", "priya@example.com", "--yes", env=env)
    assert res.exit_code != 0 and "refused" in res.output
    assert agnes.requests == []
    assert len(agnes.open_stops()) == 1


def test_a_delegate_checkout_is_refused(vault: Path, agnes: FakeAgnes) -> None:
    (vault / ".git").mkdir()
    (vault / ".git" / "scout-delegate.env").write_text("SCOUT_READER=delegate:phone\n", encoding="utf-8")
    client = share_stops.client_for(vault)
    assert client is not None
    assert "delegate checkout" in (share_stops.clear_refusal(vault, {}, client) or "")


@pytest.mark.parametrize("token", [fake_jwt("session"), fake_jwt("agent"), fake_jwt(None), "opaque-token"])
def test_a_token_that_isnt_a_pat_is_refused_before_anything_is_written(
    vault: Path, agnes: FakeAgnes, token: str
) -> None:
    shared_with(vault, agnes, "priya@example.com")
    kb(vault, "unshare", WIDGET, "--with", "priya@example.com")
    link(vault, agnes.url, token)
    before = (vault / "scout-config.yaml").read_text(encoding="utf-8")
    agnes.requests.clear()

    res = kb(vault, "share", WIDGET, "--with", "priya@example.com", "--yes")

    assert res.exit_code == 2
    assert "isn't a personal access token" in res.output and "nothing was written" in res.output
    assert (vault / "scout-config.yaml").read_text(encoding="utf-8") == before
    assert agnes.calls("POST") == []
    assert token not in res.output


def test_the_owner_at_the_keyboard_may_clear(vault: Path) -> None:
    client = share_stops.client_for(vault)
    assert client is not None and share_stops.clear_refusal(vault, {}, client) is None


@pytest.mark.parametrize(
    ("token", "kind"),
    [
        (fake_jwt("pat"), "pat"),
        (fake_jwt("session"), "session"),
        (fake_jwt(None), None),
        ("a.b", None),
        ("x.!!!.y", None),
        (fake_jwt("PAT; rm -rf"), "unknown"),
    ],
)
def test_token_kind_reads_only_the_typ_claim(token: str, kind: str | None) -> None:
    assert share_stops.token_kind(token) == kind


# ----- kb share on a hand-written scope: org entry -------------------------------------------------


def test_sharing_a_folder_with_an_org_entry_is_refused(vault: Path) -> None:
    text = (vault / "scout-config.yaml").read_text(encoding="utf-8")
    org = f'  federation: {{enabled: true}}\n  federation_defaults:\n    - {{glob: "{GLOB}", scope: org}}\n'
    (vault / "scout-config.yaml").write_text(text + org, encoding="utf-8")
    res = kb(vault, "share", WIDGET, "--with", "priya@example.com", "--yes")
    assert res.exit_code == 1
    assert f"shares {GLOB} with org, which admits nobody" in res.output
    assert f"kb unshare {WIDGET} --with org" in res.output
    assert defaults(vault) == [{"glob": GLOB, "scope": "org"}]


# ----- the client's answers ----------------------------------------------------------------------


def test_the_client_tells_an_old_agnes_from_a_refusal(agnes: FakeAgnes) -> None:
    client = share_stops.StopsClient(agnes.url, PAT)
    with pytest.raises(share_stops.StopRefused) as refused:
        client.stop("knowledge-base/nowhere/**", None)
    assert refused.value.code == "share_not_found"
    with pytest.raises(share_stops.StopRefused) as refused:
        client.clear("stop9999")
    assert refused.value.code == "share_stop_not_found"
    agnes.mode = "old"
    with pytest.raises(share_stops.StopsUnavailable) as gone:
        client.shares()
    assert gone.value.reason == "old_agnes"


def test_unshare_stops_plans_from_the_listing() -> None:
    view = share_stops.SharesView.parse(
        {
            "shares": [
                {
                    "glob": GLOB,
                    "kind": "folder",
                    "principals": [
                        {"principal": PRIYA, "kind": "person"},
                        {"principal": GROUP, "kind": "group", "members": [{"email": "Sam@Example.com"}]},
                    ],
                    "stops": [],
                },
                {"glob": f"{WIDGET}/plan.md", "kind": "note", "principals": [{"principal": PRIYA}], "stops": []},
            ],
            "pending_server_commits": "junk",
        }
    )
    assert view.pending_server_commits == 0
    target = Target(rel=WIDGET, folder=True)
    wanted, notes = share_stops.unshare_stops(view, target, [PRIYA, SAM, "person:nobody@example.com"])
    assert [(r.glob, r.principal, r.via) for r in wanted] == [(GLOB, PRIYA, None), (GLOB, SAM, GROUP)]
    assert notes == []
    assert share_stops.unshare_stops(view, target, None)[0] == [share_stops.StopRequest(GLOB, None)]


def test_a_stop_agnes_refuses_leaves_the_unshare_pending_until_sync(vault: Path, agnes: FakeAgnes) -> None:
    shared_with(vault, agnes, "priya@example.com", "sam@example.com")
    del agnes.shares[GLOB][1]  # Agnes's synced copy no longer admits Sam, but the listing was read before
    view = share_stops.SharesView.parse(
        {"shares": [{"glob": GLOB, "principals": [{"principal": PRIYA}, {"principal": SAM}], "stops": []}]}
    )
    client = share_stops.client_for(vault)
    wanted, notes = share_stops.unshare_stops(view, Target(rel=WIDGET, folder=True), [PRIYA, SAM])
    outcome = share_stops.make_stops(client, view, None, wanted, notes, dry_run=False)
    assert outcome.status == "partial" and outcome.pending_until_sync
    assert [s.principal for s in outcome.made] == [PRIYA]
    assert any("Agnes didn't stop" in n and "share_not_found" in n for n in outcome.notes)


def test_kb_shares_flags_a_stop_on_someone_the_vault_shares_with_again(vault: Path, agnes: FakeAgnes) -> None:
    shared_with(vault, agnes, "priya@example.com", "sam@example.com")
    agnes.add_stop(GLOB, PRIYA)  # say Priya was put back in scout-config.yaml by hand, and synced
    agnes.add_stop("knowledge-base/projects/gone/**", None)
    out = kb(vault, "shares").output
    hint = f"run `scoutctl kb share {WIDGET} --with priya@example.com` to lift it"
    assert hint in out and out.count("still stopped although") == 1


# ----- review fixes: a stop that fails after the listing worked ---------------------------------


@pytest.mark.parametrize("fail", [500, 401, "timeout"])
def test_a_stop_that_fails_after_the_listing_exits_4_and_kb_shares_remembers(
    vault: Path, agnes: FakeAgnes, monkeypatch: pytest.MonkeyPatch, fail: int | str
) -> None:
    shared_with(vault, agnes, "priya@example.com", "sam@example.com")
    timing_out = [fail == "timeout"]
    if fail == "timeout":
        guarded = requests.request

        def slow(method: str, url: str, **kw: Any) -> Any:
            if timing_out[0] and url.endswith("/shares/stop"):
                raise requests.ReadTimeout("read timed out")
            return guarded(method, url, **kw)

        monkeypatch.setattr(requests, "request", slow)
    else:
        agnes.stop_fail = int(fail)

    res = kb(vault, "unshare", WIDGET, "--with", "sam@example.com")

    assert res.exit_code == 4, res.output
    assert share_stops.RETRY_NOTE in res.output
    assert defaults(vault) == [{"glob": GLOB, "scope": [PRIYA]}]  # the vault change is kept
    assert agnes.open_stops() == []
    shares = kb(vault, "shares")
    assert f"Agnes still serves {GLOB} to sam@example.com, which this vault no longer shares" in shares.output
    assert f"`scoutctl kb unshare {WIDGET} --with person:sam@example.com` to retry the stop at once" in shares.output
    data = json.loads(kb(vault, "shares", "--json").stdout)
    assert data["agnes"]["still_served"] == [{"glob": GLOB, "principals": [SAM]}]

    agnes.stop_fail = None
    timing_out[0] = False
    retry = kb(vault, "unshare", WIDGET, "--with", "sam@example.com")  # the vault is already narrowed
    assert retry.exit_code == 0, retry.output
    assert [(s["glob"], s["principal"]) for s in agnes.open_stops()] == [(GLOB, SAM)]
    assert "Agnes still serves" not in kb(vault, "shares").output


def test_a_partial_group_stop_exits_4(vault: Path, agnes: FakeAgnes) -> None:
    shared_with(vault, agnes, groups={GROUP: ["sam@example.com"]})
    agnes.stop_fail = 500
    res = kb(vault, "unshare", WIDGET, "--with", "sam@example.com", "--json")
    assert res.exit_code == 4
    assert json.loads(res.stdout)["agnes"]["failed"] is True


def test_an_old_agnes_is_not_a_failed_stop(vault: Path, agnes: FakeAgnes) -> None:
    shared_with(vault, agnes, "priya@example.com")
    agnes.mode = "old"
    assert kb(vault, "unshare", WIDGET).exit_code == 0


def test_kb_shares_names_what_an_unsynced_old_agnes_unshare_left_served(vault: Path, agnes: FakeAgnes) -> None:
    shared_with(vault, agnes, "priya@example.com")
    agnes.mode = "old"
    kb(vault, "unshare", WIDGET)
    agnes.mode = "a8b"
    out = kb(vault, "shares").output
    assert f"Agnes still serves {GLOB} to priya@example.com" in out
    assert f"`scoutctl kb unshare {WIDGET}` to retry" in out


def test_keeping_an_unnamed_person_stopped_fails_so_the_stop_for_everyone_stays(vault: Path, agnes: FakeAgnes) -> None:
    shared_with(vault, agnes, "priya@example.com", "sam@example.com")
    kb(vault, "unshare", WIDGET)
    (everyone,) = agnes.open_stops()
    agnes.stop_fail = 500  # the stop that would keep Sam out fails
    agnes.requests.clear()

    res = kb(vault, "share", WIDGET, "--with", "priya@example.com", "--yes")

    assert res.exit_code == 0, res.output
    assert agnes.calls("POST", "/clear-stop") == []  # never cleared: that would let Sam back in
    assert [s["stop_id"] for s in agnes.open_stops()] == [everyone["stop_id"]]
    assert "its stop for everyone stays" in res.output


def test_apply_clears_skips_only_the_globs_it_couldnt_narrow(vault: Path, agnes: FakeAgnes) -> None:
    agnes.share(GLOB, "priya@example.com", "sam@example.com")
    agnes.share(f"{WIDGET}/design/**", "priya@example.com")
    wide = agnes.add_stop(GLOB, None)
    design = agnes.add_stop(f"{WIDGET}/design/**", PRIYA)
    client = share_stops.client_for(vault)
    assert client is not None
    view = client.shares()
    plan = share_stops.share_clears(view, Target(rel=WIDGET, folder=True), [PRIYA])
    assert [r.principal for r in plan.keep_stopped] == [SAM]
    agnes.stop_fail = 500
    lines = share_stops.apply_clears(vault, {}, client, plan)
    cleared = [c["body"]["stop_id"] for c in agnes.calls("POST", "/clear-stop")]
    assert cleared == [design["stop_id"]] and wide["stop_id"] not in cleared
    assert any("its stop for everyone stays" in line for line in lines)


# ----- review fixes: the process check, an unreadable token, quoting ----------------------------


def test_a_clear_is_refused_inside_a_live_run_whatever_the_environment(
    vault: Path, agnes: FakeAgnes, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scout.kb import live_run

    client = share_stops.client_for(vault)
    assert client is not None
    seen: list[Path] = []

    def in_a_run(v: Path) -> live_run.CommitSource:
        seen.append(v)
        return live_run.CommitSource(True, "briefing", "ancestry")

    monkeypatch.setattr(live_run, "commit_source_now", in_a_run)
    refusal = share_stops.clear_refusal(vault, {}, client)  # an environment that says "owner"
    assert refusal is not None and "inside a scheduled run (briefing, by ancestry)" in refusal
    assert seen == [vault]
    monkeypatch.setattr(live_run, "commit_source_now", lambda v: live_run.CommitSource(False, "briefing", "host"))
    assert share_stops.clear_refusal(vault, {}, client) is None  # the owner's own terminal during a run
    shared_with(vault, agnes, "priya@example.com")
    agnes.add_stop(GLOB, PRIYA)
    monkeypatch.setattr(live_run, "commit_source_now", in_a_run)
    res = kb(vault, "share", WIDGET, "--with", "priya@example.com", "--yes")
    assert res.exit_code == 2 and "inside a scheduled run" in res.output
    assert agnes.calls("POST") == []


def test_an_unreadable_token_degrades_to_unlinked(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scout import repo_sync

    def sandboxed(_vault: Path) -> str:
        raise PermissionError("Operation not permitted")

    monkeypatch.setattr(repo_sync, "load_token", sandboxed)
    assert share_stops.client_for(vault) is None
    assert share.directory_for(vault) is None
    res = kb(vault, "shares", env={"SCOUT_READER": "autonomous"})
    assert res.exit_code == 0, res.output


def test_printed_commands_quote_paths_and_people() -> None:
    served = [("knowledge-base/projects/my widget/**", ["person:sam@example.com"])]
    (line,) = share_stops.still_served_lines(served, {"knowledge-base/projects/my widget/**": ["x"]})
    assert "`scoutctl kb unshare 'knowledge-base/projects/my widget' --with person:sam@example.com`" in line
    rows = [share.ShareRow(glob="knowledge-base/projects/my widget/**", source="default", principals=[PRIYA])]
    stop = share_stops.Stop("stop0001", "knowledge-base/projects/my widget/**", PRIYA, "cli", "not_requested", "")
    out = share.shares_lines(True, rows, {}, {rows[0].glob: [stop]})
    assert any("`scoutctl kb share 'knowledge-base/projects/my widget' --with priya@example.com`" in x for x in out)


# ----- review fixes (delta): a linked vault whose token can't be loaded is a failure, not "unlinked" -----


def _break_token(vault: Path, how: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from scout import repo_sync

    path = repo_sync.token_path(vault)
    if how == "missing":
        path.unlink()
    elif how == "insecure":
        path.chmod(0o644)
    else:

        def sandboxed(_vault: Path) -> str:
            raise PermissionError("Operation not permitted")

        monkeypatch.setattr(repo_sync, "load_token", sandboxed)


@pytest.mark.parametrize(
    ("how", "said"),
    [("missing", "is missing"), ("insecure", "has loose permissions"), ("unreadable", "can't be read")],
)
def test_a_linked_vault_with_a_bad_token_fails_the_unshare(
    vault: Path, agnes: FakeAgnes, monkeypatch: pytest.MonkeyPatch, how: str, said: str
) -> None:
    shared_with(vault, agnes, "priya@example.com")
    _break_token(vault, how, monkeypatch)

    res = kb(vault, "unshare", WIDGET, "--json")

    assert res.exit_code == 4, res.output
    data = json.loads(res.stdout)
    assert data["written"] is True and data["agnes"]["failed"] is True
    assert any(f"linked to Agnes, but the Agnes token {said}" in n for n in data["agnes"]["notes"])
    assert share_stops.UNLINKED_NOTE not in res.output
    assert agnes.requests == []
    assert defaults(vault) == []
    owner = kb(vault, "shares")
    assert owner.exit_code == 0 and f"the Agnes token {said}" in owner.output
    quiet = kb(vault, "shares", env={"SCOUT_READER": "autonomous"})
    assert quiet.exit_code == 0 and "Agnes token" not in quiet.output


def test_kb_share_warns_when_the_token_cant_be_loaded(vault: Path, agnes: FakeAgnes) -> None:
    from scout import repo_sync

    repo_sync.token_path(vault).unlink()
    res = kb(vault, "share", WIDGET, "--with", "priya@example.com", "--yes")
    assert res.exit_code == 0, res.output
    assert "the Agnes token is missing" in res.output and "stay in force" in res.output


def test_principals_compare_case_insensitively_for_people_only() -> None:
    assert share_stops.same_principal("person:Sam@Example.com", SAM)
    assert not share_stops.same_principal("group:ABC", "group:abc")
    assert share_stops.same_principal(None, None) and not share_stops.same_principal(None, SAM)
    view = share_stops.SharesView.parse(
        {"shares": [{"glob": GLOB, "principals": [{"principal": "person:Priya@Example.com"}], "stops": []}]}
    )
    assert share_stops.still_served(view, {GLOB: [PRIYA]}) == []
    assert share_stops.still_served(view, {}) == [(GLOB, ["person:Priya@Example.com"])]


def test_a_torn_link_state_reads_as_unlinked(vault: Path) -> None:
    from scout.repo_sync import state_path

    state_path(vault).write_text("{torn", encoding="utf-8")
    assert share_stops.connect(vault) == (None, None)
