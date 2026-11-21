"""`scoutctl inbox review … --json` is a contract with the macOS app's inbox review sheet (#550 V4b3).

``tests/fixtures/contract/inbox-review.json`` is the canonical copy; the app keeps a byte copy at
``apps/macos/ScoutTests/Fixtures/inbox-review.json`` (``contract.yml`` compares them). Set
``SCOUT_REGEN_CONTRACT=1`` to rewrite the canonical file from the deterministic vault below, then ``cp`` it over
the app copy and update the pins in ``apps/macos/ScoutTests/Engine/InboxReviewTests.swift``.

One document holds each output the app reads, under a key per command:

- ``review``: ``inbox review --json``. An observation with a no-break space (shown as ``\\u{00A0}``) and a
  look-alike word, a held comment, and an op over the day's limit.
- ``accept``: ``inbox review accept <ULID> --confirm <ULID> --json``.
- ``accepted``: ``inbox review --accepted --json``: one accepted observation kept private, one promoted.
- ``promote_preview``: ``inbox review promote <ULID> --preview --json``, with the delegates who would see it.
- ``promote``: ``inbox review promote <ULID> --to <them> --previewed <sha> --confirm <ULID> --json``.
- ``unpromote_preview``: ``inbox review unpromote <ULID> --preview --json``.
- ``refusal``: a refused command's ``--json`` (promoting a promoted note again).

The values come from the library calls the CLI makes, with a fixed clock; the CLI's own output is checked
against the same keys below. All text is stand-in text. A new field or state goes into the builder, never the
JSON.
"""

from __future__ import annotations

import datetime as dt
import functools
import json
import os
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from scout import delegate_apply as da
from scout import delegate_inbox as di
from scout import delegate_proposals as dp
from scout import delegate_review as dr
from scout.cli import app
from scout.repo_sync import RepoSyncError

from .privacy_vault_helpers import git

# Read at import: tests/conftest.py scrubs SCOUT_* from the environment before each test runs.
REGEN = os.environ.get("SCOUT_REGEN_CONTRACT") == "1"
CANONICAL = Path(__file__).resolve().parents[1] / "fixtures" / "contract" / "inbox-review.json"

NOW = dt.datetime(2026, 11, 19, 12, 0, tzinfo=dt.UTC)
STAMP = "2026-11-19T12:00:00Z"
D = "grok-personal"
DATE = "2026-11-19"

CONFIG = """user:
  timezone: America/New_York
privacy:
  delegates:
    grok-personal: {}
    duet:
      include_private: true
    research-bot: {}
"""


def _ulid(minutes_before: int, tail: str) -> str:
    """A ULID made ``minutes_before`` NOW, with a fixed random part."""
    ms = int((NOW - dt.timedelta(minutes=minutes_before)).timestamp() * 1000)
    head = ""
    for _ in range(10):
        head = dp._CROCKFORD[ms % 32] + head
        ms //= 32
    assert len(tail) == 16
    return head + tail


OBSERVATION = _ulid(50, "00000000000000AB")
COMMENT = _ulid(40, "00000000000000CD")
LIMITED = _ulid(30, "00000000000000EF")
KEPT = _ulid(20, "00000000000000GH")
PROMOTED = _ulid(10, "00000000000000JK")

PROPOSALS: list[tuple[str, str, dict[str, Any]]] = [
    (
        OBSERVATION,
        "observation",
        {
            "text": "The supplier said the new parts ship in two weeks.\nThe next\u00a0payment is due on the 1st.",
            "about": "Parts: supplier",
            "source": "https://example.com/supplier-update",
        },
    ),
    (COMMENT, "action_item_op", {"op": "add-comment", "tag": "AB12", "date": DATE, "args": {"comment": "Done?"}}),
    (LIMITED, "action_item_op", {"op": "mark-done", "tag": "CD34", "date": DATE, "args": {}}),
    (KEPT, "observation", {"text": "The office moves to the third floor in March."}),
    (PROMOTED, "observation", {"text": "The team meeting moved to Thursdays."}),
]
# A look-alike word: a Cyrillic a (U+0430) in "payment", put into the first observation below.
LOOK_ALIKE = "p\u0430yment"


def _commit(vault: Path, message: str, rels: list[str]) -> str:
    git(vault, "add", "--", *rels)
    git(vault, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", message, "--", *rels)
    return git(vault, "rev-parse", "HEAD").strip()


def build_vault(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The fixture's vault (a temp HOME: the signing key goes there, not the real one), with fixed git dates."""
    vault = root / "Scout"
    home = root / "home"
    vault.mkdir()
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SCOUT_DATA_DIR", str(vault))
    monkeypatch.delenv("SCOUT_MODE", raising=False)
    monkeypatch.delenv("SCOUT_READER", raising=False)
    for name in ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE"):
        monkeypatch.setenv(name, STAMP)
    monkeypatch.setattr(da, "_default_commit", _commit)
    (vault / "scout-config.yaml").write_text(CONFIG, encoding="utf-8")
    git(vault, "init", "-q", "-b", "main")
    git(vault, "add", "-A")
    git(vault, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", "config")
    state = di.InboxState(
        inboxes={
            D: di.InboxLink(
                id=D,
                remote="https://github.com/example-user/grok-inbox.git",
                linked_at="2026-11-10T09:00:00Z",
                bound_to="github:1:2",
                last_pull="2026-11-19T11:45:00Z",
            )
        }
    )
    folder = vault.joinpath(*di.INBOX_DIR, D)
    folder.mkdir(parents=True)
    for ulid, kind, body in PROPOSALS:
        if ulid == OBSERVATION:
            body = {**body, "text": body["text"].replace("payment", LOOK_ALIKE)}
        draft = dp.Proposal(id=ulid, delegate=D, made_at="2026-11-19T11:00:00Z", kind=kind, body=body)
        proposal = dp.parse(draft.path, draft.to_bytes(), D)
        data = proposal.to_bytes()
        state.taken.setdefault(D, {})[ulid] = {"mac": di.proposal_mac(D, ulid, data), "at": "2026-11-19T11:45:00Z"}
        (folder / f"{ulid}.json").write_bytes(data)
    state.applied[D] = {
        COMMENT: {"op": "add-comment", "tag": "AB12", "date": DATE, "outcome": "held", "at": "2026-11-19T11:46:00Z"},
        LIMITED: {"op": "mark-done", "tag": "CD34", "date": DATE, "outcome": "limited", "at": "2026-11-19T11:46:00Z"},
    }
    di.save_state(vault, state)
    return vault


def _ok(obj: Any) -> dict[str, Any]:
    return {"ok": True, **obj.to_json()}


def document(root: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    vault = build_vault(root, monkeypatch)
    doc: dict[str, Any] = {"accept": _ok(dr.accept(vault, KEPT, confirm=KEPT, now=NOW))}
    dr.accept(vault, PROMOTED, confirm=PROMOTED, now=NOW)
    doc["review"] = [i.to_json() for i in dr.review(vault, now=NOW)]  # what still waits
    preview = dr.preview_promote(vault, PROMOTED, now=NOW)
    doc["promote_preview"] = _ok(preview)
    doc["promote"] = _ok(
        dr.promote(vault, PROMOTED, confirm=PROMOTED, previewed=preview.sha256, to=preview.audience, now=NOW)
    )
    doc["accepted"] = [a.to_json() for a in dr.accepted(vault)]
    doc["unpromote_preview"] = _ok(dr.preview_unpromote(vault, PROMOTED))
    with pytest.raises(RepoSyncError) as e:
        dr.preview_promote(vault, PROMOTED, now=NOW)
    doc["refusal"] = {"ok": False, "code": e.value.code, "message": e.value.message}
    return doc


def render(root: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    return json.dumps(document(root, monkeypatch), indent=2, sort_keys=True) + "\n"


def test_inbox_review_matches_the_shared_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    text = render(tmp_path, monkeypatch)
    if REGEN:
        CANONICAL.write_text(text, encoding="utf-8")
    assert CANONICAL.read_text(encoding="utf-8") == text, (
        "regenerate with SCOUT_REGEN_CONTRACT=1, then cp it to apps/macos/ScoutTests/Fixtures/inbox-review.json"
    )


@pytest.fixture
def doc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    return json.loads(render(tmp_path, monkeypatch))


def test_the_fixture_exercises_every_shape(doc: dict[str, Any]) -> None:
    review = {i["ulid"]: i for i in doc["review"]}
    assert list(review) == [OBSERVATION, COMMENT, LIMITED]
    obs, comment, limited = review[OBSERVATION], review[COMMENT], review[LIMITED]
    assert obs["kind"] == "observation" and obs["status"] == "waiting" and obs["delegate"] == D
    assert "next\\u{00A0}" in obs["text"] and obs["look_alikes"] == [LOOK_ALIKE]
    assert obs["about"] == "Parts: supplier" and obs["source"] == "https://example.com/supplier-update"
    assert comment["kind"] == "comment" and comment["status"] == "held" and comment["tag"] == "AB12"
    assert limited["kind"] == "op" and limited["status"] == "limited" and limited["op"] == "mark-done"
    assert doc["accept"]["outcome"] == "accepted" and doc["accept"]["note"] == dr.note_rel(D, KEPT)
    assert dr.EDITED in doc["accept"]["notes"]
    assert doc["promote_preview"]["audience"] == ["grok-personal", "duet", "research-bot"]
    assert doc["promote_preview"]["sha256"] and doc["promote_preview"]["status"] == "accepted"
    assert doc["promote"]["outcome"] == "promoted"
    kept, promoted = doc["accepted"]
    assert kept["ulid"] == KEPT and not kept["promoted"] and kept["promoted_to"] is None
    assert promoted["promoted"] and promoted["promoted_to"] == doc["promote_preview"]["audience"]
    assert doc["unpromote_preview"]["audience"] == doc["promote_preview"]["audience"]
    assert doc["unpromote_preview"]["status"] == "promoted"
    assert doc["refusal"] == {"ok": False, "code": "already_promoted", "message": doc["refusal"]["message"]}


def test_no_secret_or_path_reaches_the_fixture(doc: dict[str, Any], tmp_path: Path) -> None:
    text = json.dumps(doc)
    for leaked in (str(tmp_path), "scout-secrets", '"mac"', "bound_to", "grok-inbox"):
        assert leaked not in text


def test_the_cli_prints_the_same_shapes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The fixture comes from the library; the CLI the app runs prints the same keys around it."""
    build_vault(tmp_path, monkeypatch)
    # The CLI reads the real clock, and the vault's op over the limit is listed for a week after its fixed
    # time, so these calls run at NOW like the fixture's.
    for name in ("review", "accept", "preview_promote", "promote", "unpromote"):
        monkeypatch.setattr(dr, name, functools.partial(getattr(dr, name), now=NOW))
    expected = json.loads(CANONICAL.read_text(encoding="utf-8"))
    runner = CliRunner()

    def run(*args: str) -> Any:
        result = runner.invoke(app, ["inbox", "review", *args, "--json"])
        assert result.exit_code in (0, 2), result.output
        return json.loads(result.output)

    assert set(run("accept", KEPT, "--confirm", KEPT)) == set(expected["accept"])
    run("accept", PROMOTED, "--confirm", PROMOTED)
    assert [set(i) for i in run()] == [set(i) for i in expected["review"]]
    preview = run("promote", PROMOTED, "--preview")
    assert set(preview) == set(expected["promote_preview"]) and preview["audience"] == ["grok-personal", "duet",
                                                                                       "research-bot"]  # fmt: skip
    to = ",".join(preview["audience"])
    stale = run("promote", PROMOTED, "--to", to, "--previewed", "0" * 64, "--confirm", PROMOTED)
    assert stale["ok"] is False and stale["code"] == "note_changed"  # the app's hash is what binds it
    done = run("promote", PROMOTED, "--to", to, "--previewed", preview["sha256"], "--confirm", PROMOTED)
    assert set(done) == set(expected["promote"])
    assert [set(a) for a in run("--accepted")] == [set(a) for a in expected["accepted"]]
    back = run("unpromote", PROMOTED, "--preview")
    assert set(back) == set(expected["unpromote_preview"])
    assert set(run("promote", PROMOTED, "--preview")) == set(expected["refusal"])
    stale = run("unpromote", PROMOTED, "--previewed", "0" * 64)
    assert stale["code"] == "note_changed"
    assert run("unpromote", PROMOTED, "--previewed", back["sha256"])["outcome"] == "unpromoted"
