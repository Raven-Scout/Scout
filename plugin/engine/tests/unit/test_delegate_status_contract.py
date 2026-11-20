"""`scoutctl delegate status --json` is a contract with the macOS app's Settings ▸ Agent copies card (#550).

``tests/fixtures/contract/delegate-status.json`` is the canonical copy; the app keeps a byte copy at
``apps/macos/ScoutTests/Fixtures/delegate-status.json`` (``contract.yml`` compares them). Set
``SCOUT_REGEN_CONTRACT=1`` to rewrite the canonical file from the deterministic vault below, then ``cp`` it over
the app copy and update the pins in ``apps/macos/ScoutTests/Engine/DelegateStatusTests.swift``.

The vault lists three delegates: ``grok-personal`` synced cleanly to a GitHub mirror under the owner's own user
(the engine refuses an organisation's), with two files left out as agent config and an old deploy key still live;
``duet``'s last sync to a local mirror (which has no deploy key) was refused; and ``research-bot`` is listed but
not linked yet. Their inboxes (V4b3): ``grok-personal``'s has two proposals waiting for review (one taken from
an observation, one held comment) and one file no pull took; ``duet``'s last pull was refused; ``research-bot``
has none. A new field or state goes into the builder, never the JSON.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scout import delegate_inbox as di
from scout import delegate_mirror as dm
from scout import delegate_status

from .privacy_vault_helpers import git

# Read at import: tests/conftest.py scrubs SCOUT_* from the environment before each test runs.
REGEN = os.environ.get("SCOUT_REGEN_CONTRACT") == "1"
CANONICAL = Path(__file__).resolve().parents[1] / "fixtures" / "contract" / "delegate-status.json"

NOW = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)

CONFIG = """user:
  timezone: America/New_York
privacy:
  delegates:
    grok-personal: {}
    duet:
      include_private: true
    research-bot: {}
"""


def build_vault(root: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict[str, str]]:
    """The fixture's vault and the owner's environment (a temp HOME: the signing key goes there, not the real one)."""
    vault = root / "Scout"
    home = root / "home"
    vault.mkdir()
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    (vault / "scout-config.yaml").write_text(CONFIG, encoding="utf-8")
    git(vault, "init", "-q", "-b", "main")
    git(vault, "add", "-A")
    git(vault, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", "config")
    head = git(vault, "rev-parse", "HEAD").strip()
    inputs = dm.sync_inputs(vault)
    links = {
        "grok-personal": dm.DelegateLink(
            id="grok-personal",
            remote="https://github.com/example-user/grok-mirror.git",
            linked_at="2026-11-10T09:00:00Z",
            account_unshared=True,
            last_mirror="a" * 40,
            last_vault=head,
            last_sync="2026-11-19T11:45:00Z",
            last_attempt="2026-11-19T11:45:00Z",
            deploy_key_id=102,
            stale_key_ids=[101],
            key_file=str(home / ".scout-secrets" / "delegate-mirrors" / "0123456789abcdef-grok-personal"),
            checked_fingerprint="f" * 64,
            last_inputs=inputs,
            last_counts={"files": 412, "withheld": 37, "left_out": 3, "redacted_lines": 9, "agent_config": 2},
        ),
        "duet": dm.DelegateLink(
            id="duet",
            remote=str(home / "mirrors" / "duet.git"),
            linked_at="2026-11-12T15:30:00Z",
            account_unshared=True,
            last_mirror="b" * 40,
            last_vault="c" * 40,
            last_sync="2026-11-18T22:00:00Z",
            last_attempt="2026-11-19T11:00:00Z",
            pending_mirror="d" * 40,
            last_error={
                "code": "mirror_diverged",
                "at": "2026-11-19T11:00:00Z",
                "first_at": "2026-11-19T08:00:00Z",
                "head": head,
                "inputs": inputs,
            },
            last_inputs="0" * 64,
            last_counts={"files": 530, "withheld": 12, "left_out": 0, "redacted_lines": 0, "agent_config": 0},
        ),
    }
    dm.save_links(vault, links)
    waiting, held, planted = "01JD5Z0000000000000000ABCD", "01JD5Z0000000000000000ABCE", "01JD5Z0000000000000000ABCF"
    inbox = di.InboxState(
        inboxes={
            "grok-personal": di.InboxLink(
                id="grok-personal",
                remote="https://github.com/example-user/grok-inbox.git",
                linked_at="2026-11-10T09:05:00Z",
                bound_to="github:1:2",
                last_pull="2026-11-19T11:45:00Z",
            ),
            "duet": di.InboxLink(
                id="duet",
                remote=str(home / "inboxes" / "duet.git"),
                linked_at="2026-11-12T15:35:00Z",
                bound_to="local:1:2",
                last_pull="2026-11-18T22:00:00Z",
                last_error={"code": "inbox_rewritten", "at": "2026-11-19T11:30:00Z"},
            ),
        },
        taken={
            "grok-personal": {
                waiting: {"mac": "0" * 64, "at": "2026-11-19T09:00:00Z"},
                held: {"mac": "0" * 64, "at": "2026-11-19T11:45:00Z"},
            }
        },  # fmt: skip
        applied={
            "grok-personal": {
                held: {
                    "op": "add-comment",
                    "tag": "AB12",
                    "date": "2026-11-19",
                    "outcome": "held",
                    "at": "2026-11-19T11:46:00Z",
                }
            }
        },  # fmt: skip
    )
    di.save_state(vault, inbox)
    folder = vault.joinpath(*di.INBOX_DIR, "grok-personal")
    folder.mkdir(parents=True)
    (folder / f"{planted}.json").write_text("{}", encoding="utf-8")  # no pull took it: something else put it there
    return vault, {"HOME": str(home)}


def render(root: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    vault, env = build_vault(root, monkeypatch)
    return json.dumps(delegate_status.report(vault, env=env, now=NOW), indent=2, sort_keys=True) + "\n"


def test_delegate_status_matches_the_shared_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    text = render(tmp_path, monkeypatch)
    if REGEN:
        CANONICAL.write_text(text, encoding="utf-8")
    assert CANONICAL.read_text(encoding="utf-8") == text, (
        "regenerate with SCOUT_REGEN_CONTRACT=1, then cp it to apps/macos/ScoutTests/Fixtures/delegate-status.json"
    )


@pytest.fixture
def doc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    return json.loads(render(tmp_path, monkeypatch))


def test_the_fixture_exercises_every_state(doc: dict) -> None:
    assert doc["schema_version"] == delegate_status.SCHEMA_VERSION == 1
    assert doc["state"] == "ok" and doc["owner"] is True and doc["skipped"] == 0
    by_id = {d["id"]: d for d in doc["delegates"]}
    assert [d["id"] for d in doc["delegates"]] == ["grok-personal", "duet", "research-bot"]  # the config's order
    grok, duet, bot = by_id["grok-personal"], by_id["duet"], by_id["research-bot"]
    assert grok["last_result"] == "ok" and grok["behind"] is False and grok["refusal"] is None
    assert grok["remote"] == {"kind": "github", "display": "example-user/grok-mirror"}
    assert grok["agent_config_left_out"] == 2 and grok["counts"]["files"] == 412
    assert grok["can_sync"] and grok["can_preview"]
    assert grok["deploy_key"] is True and grok["old_keys_live"] == 1
    assert duet["last_result"] == "failed" and duet["behind"] is True
    assert duet["refusal"] == {
        "code": "mirror_diverged",
        "reason": delegate_status.REASONS["mirror_diverged"],
        "at": "2026-11-19T11:00:00Z",
        "since": "2026-11-19T08:00:00Z",
    }
    assert duet["remote"] == {"kind": "local", "display": "~/mirrors/duet.git"}
    assert duet["deploy_key"] is False and duet["old_keys_live"] == 0
    assert bot["linked"] is False and bot["listed"] is True and bot["can_preview"] and not bot["can_sync"]
    assert doc["inbox_state"] == "ok"
    assert grok["inbox"] == {
        "linked_at": "2026-11-10T09:05:00Z",
        "last_pull": "2026-11-19T11:45:00Z",
        "refusal": None,
        "review": 2,
        "oldest_review": "2026-11-19T09:00:00Z",
        "waiting": 2,
        "untaken": 1,
    }
    assert grok["can_review"] and duet["can_review"] and not bot["can_review"]
    assert duet["inbox"]["refusal"] == {
        "code": "inbox_rewritten",
        "reason": delegate_status.INBOX_REASONS["inbox_rewritten"],
        "at": "2026-11-19T11:30:00Z",
    }
    assert duet["inbox"]["review"] == 0 and bot["inbox"] is None
    for d in doc["delegates"]:
        assert d["state_unverified"] is False
        assert set(d) == set(doc["delegates"][0])


def test_no_secret_path_or_commit_reaches_the_fixture(doc: dict, tmp_path: Path) -> None:
    text = json.dumps(doc)
    assert str(tmp_path) not in text
    for leaked in ("scout-secrets", "a" * 40, "b" * 40, "d" * 40, "f" * 64, "0" * 64, "key_file", "inputs",
                   "grok-inbox", "inboxes", "bound_to", "ABCD"):  # fmt: skip
        assert leaked not in text
