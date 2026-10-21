"""``scoutctl kb delta``: the vault's own commits since a point, each changed path labelled (#453, spec §6.1, §11.2).

The fixture vault's history (``build_privacy_vault``): the seed commit, then
``private only``, ``private and normal`` and ``normal only``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from scout.cli import app
from scout.kb.delta import Commit, delta
from scout.kb.ontology import clear_frontmatter_cache
from scout.kb.read import UsageError
from tests.unit.privacy_vault_helpers import COMMITS, SEED_SUBJECT, build_privacy_vault, git

runner = CliRunner()
AGENT = "agent:person:priya@example.com"
SAM = "knowledge-base/people/sam.md"
PERMIT = "knowledge-base/personal/task-renew-permit.md"
BILLING = "knowledge-base/topics/billing/billing.md"
ROLLOUT = "knowledge-base/topics/billing/rollout.md"
PRIYA = "knowledge-base/people/priya.md"


@pytest.fixture(scope="module")
def shared_vault(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_privacy_vault(tmp_path_factory.mktemp("kb-delta"))


@pytest.fixture
def vault(shared_vault: Path) -> Path:
    clear_frontmatter_cache()
    return shared_vault


def kb(vault: Path, *args: str, reader: str | None = None) -> Result:
    env = {"SCOUT_READER": reader} if reader else {}
    return runner.invoke(app, ["kb", *args, "--repo", str(vault)], env=env)


def _vis(commit: Commit) -> dict[str, str]:
    return {path: label.visibility for path, label in commit.paths}


def _commit(vault: Path, subject: str) -> None:
    git(vault, "add", "-A")
    git(vault, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", subject)


@pytest.mark.parametrize("reader", ["owner", "autonomous"])
def test_delta_labels_every_path_and_keeps_private_only_commit(vault: Path, reader: str) -> None:
    commits = delta(vault, reader, "HEAD~3", with_diff=False)
    assert [c.subject for c in commits] == [s for s, _ in reversed(COMMITS)]  # newest first
    normal, mixed, private = commits
    assert _vis(private) == {PERMIT: "private", SAM: "private"}
    assert _vis(mixed) == {BILLING: "private", ROLLOUT: "normal"}
    assert _vis(normal) == {PRIYA: "normal"}
    assert private.paths[0][1].path == private.paths[0][0]
    assert all(len(c.sha) == 40 and c.author == "Alex" and c.diff is None for c in commits)


def test_delta_with_diff(vault: Path) -> None:
    commits = delta(vault, "autonomous", "HEAD~3", with_diff=True)
    private = commits[-1]
    assert private.diff is not None
    assert "+Update 1." in private.diff and SAM in private.diff


def test_delta_duration_includes_seed(vault: Path) -> None:
    commits = delta(vault, "owner", "2d", with_diff=False)
    assert [c.subject for c in commits][-1] == SEED_SUBJECT
    assert len(commits) == len(COMMITS) + 1
    for spec in ("8h", "1w"):
        assert len(delta(vault, "owner", spec, with_diff=False)) == len(COMMITS) + 1


def test_delta_agent_gets_nothing(vault: Path) -> None:
    assert delta(vault, AGENT, "HEAD~3", with_diff=True) == []


@pytest.mark.parametrize("since", ["no-such-rev", "--all", "-1", ""])
def test_delta_bad_since(vault: Path, since: str) -> None:
    with pytest.raises(UsageError):
        delta(vault, "owner", since, with_diff=False)


def test_delta_deleted_renamed_and_binary(tmp_path: Path) -> None:
    vault = build_privacy_vault(tmp_path)
    (vault / SAM).unlink()
    _commit(vault, "delete sam")
    moved = "knowledge-base/topics/misc/permit.md"
    git(vault, "mv", PERMIT, moved)
    _commit(vault, "move permit")
    (vault / "Raw" / "photo.png").write_bytes(b"\x89PNG\x00\x01\x02\xff\xfe")
    (vault / "Raw" / "latin1.md").write_bytes(b"caf\xe9 \xff\n")
    _commit(vault, "binary")

    binary, move, delete = delta(vault, "owner", "HEAD~3", with_diff=True)
    assert _vis(delete) == {SAM: "private"}  # labelled from its last committed content
    assert delete.diff is not None and "CANARY-PRIV-2S4M" in delete.diff
    # a rename lists both sides; the source keeps its private path default
    assert _vis(move) == {PERMIT: "private", moved: "normal"}
    assert _vis(binary) == {"Raw/photo.png": "normal", "Raw/latin1.md": "normal"}
    assert binary.diff is not None and "Binary" in binary.diff


def test_delta_file_now_private_is_private(tmp_path: Path) -> None:
    """A path whose current label is private stays private even if it was normal at the commit."""
    vault = build_privacy_vault(tmp_path)
    clear_frontmatter_cache()
    text = (vault / PRIYA).read_text(encoding="utf-8").replace("type: person\n", "type: person\nvisibility: private\n")
    (vault / PRIYA).write_text(text, encoding="utf-8")  # uncommitted
    (normal,) = delta(vault, "owner", "HEAD~1", with_diff=False)
    assert _vis(normal) == {PRIYA: "private"}


# ----- CLI -----------------------------------------------------------------------------


@pytest.mark.parametrize("reader", ["owner", "autonomous"])
def test_cli_delta_json_and_text(vault: Path, reader: str) -> None:
    result = kb(vault, "delta", "--since", "HEAD~3", "--json", reader=reader)
    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert [r["subject"] for r in rows] == [s for s, _ in reversed(COMMITS)]
    private = rows[-1]
    assert {p["path"]: p["label"]["visibility"] for p in private["paths"]} == {PERMIT: "private", SAM: "private"}
    assert private["diff"] is None
    assert set(private) == {"sha", "subject", "author", "paths", "diff"}

    text = kb(vault, "delta", "--since", "HEAD~3", reader=reader).stdout.splitlines()
    assert f"  [private] {SAM}" in text and f"  {PRIYA}" in text
    assert any(line.endswith(" private only") for line in text)

    with_diff = json.loads(kb(vault, "delta", "--since", "HEAD~3", "--diff", "--json", reader=reader).stdout)
    assert "+Update 1." in with_diff[-1]["diff"]


def test_cli_delta_agent(vault: Path) -> None:
    result = kb(vault, "delta", "--since", "HEAD~3", "--diff", reader=AGENT)
    assert (result.exit_code, result.stdout) == (0, "")
    as_json = kb(vault, "delta", "--since", "HEAD~3", "--json", reader=AGENT)
    assert json.loads(as_json.stdout) == []
    assert "CANARY" not in as_json.output


def test_cli_delta_bad_since(vault: Path) -> None:
    result = kb(vault, "delta", "--since", "no-such-rev")
    assert result.exit_code == 2
