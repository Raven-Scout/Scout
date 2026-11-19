"""The ``elsewhere`` reader: the owner reading the KB from outside the vault (#721 review, item 3).

It sees what a listed delegate without ``include_private`` sees, with no policy needed: no private,
local-only or shared-in note and no phone inbox. Nothing defaults to it; ``--reader elsewhere`` narrows to it
from ``owner`` or ``autonomous``, and nothing can widen it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner, Result

from scout.cli import app
from scout.kb.ontology import clear_frontmatter_cache
from scout.kb.privacy import Label, reads_any, visible_to
from scout.kb.reader import ELSEWHERE_READER, resolve_reader
from tests.unit.privacy_vault_helpers import CANARIES, build_privacy_vault

SAM = "knowledge-base/people/sam.md"
PERMIT = "knowledge-base/personal/task-renew-permit.md"
GIFT = "knowledge-base/personal/task-buy-gift.md"


@pytest.fixture(scope="module")
def vault(tmp_path_factory: pytest.TempPathFactory) -> Path:
    clear_frontmatter_cache()
    return build_privacy_vault(tmp_path_factory.mktemp("kb-elsewhere"))


def kb(vault: Path, *args: str, env: dict[str, str] | None = None) -> Result:
    return CliRunner().invoke(app, ["kb", *args, "--repo", str(vault)], env=env or {})


def _label(path: str = "knowledge-base/topics/x.md", visibility: str = "normal", **kw: bool) -> Label:
    return Label(path=path, visibility=visibility, visibility_source="frontmatter", **kw)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("label", "seen"),
    [
        (_label(), True),
        (_label(visibility="private"), False),
        (_label(local_only=True), False),
        (_label(shared_source=True), False),
        (_label(path=".scout-inbox/2026-01-01.md"), False),
    ],
    ids=["normal", "private", "local-only", "shared-in", "phone-inbox"],
)
def test_elsewhere_sees_only_plain_notes(label: Label, seen: bool) -> None:
    assert visible_to(label, ELSEWHERE_READER) is seen
    assert visible_to(label, "owner")
    assert reads_any(ELSEWHERE_READER)


def test_elsewhere_narrows_owner_and_autonomous_and_widens_nothing() -> None:
    assert resolve_reader("elsewhere", {}) == ELSEWHERE_READER
    assert resolve_reader("elsewhere", {"SCOUT_MODE": "briefing"}) == ELSEWHERE_READER
    assert resolve_reader(None, {"SCOUT_READER": "elsewhere"}) == ELSEWHERE_READER
    for flag, env in (
        ("owner", {"SCOUT_READER": "elsewhere"}),
        ("autonomous", {"SCOUT_READER": "elsewhere"}),
        ("elsewhere", {"SCOUT_READER": "agent:person:priya@example.com"}),
        ("elsewhere", {"SCOUT_READER": "delegate:phone"}),
    ):
        with pytest.raises(typer.BadParameter):
            resolve_reader(flag, env)
    assert resolve_reader("agent:person:priya@example.com", {"SCOUT_READER": "elsewhere"}).startswith("agent:")


def test_kb_reads_as_elsewhere_drop_private_notes(vault: Path) -> None:
    files = {r["path"] for r in json.loads(kb(vault, "files", "--json", "--reader", "elsewhere").stdout)}
    assert GIFT in files and "inbox.md" in files
    assert not files & {SAM, PERMIT, "trading/positions.md"}

    hits = kb(vault, "search", CANARIES[SAM], "--fixed", "--reader", "elsewhere")
    assert hits.exit_code in (0, 1) and SAM not in hits.stdout and CANARIES[SAM] not in hits.stdout

    denied = kb(vault, "read", SAM, "--json", "--reader", "elsewhere")
    assert denied.exit_code == 3 and CANARIES[SAM] not in denied.stdout
    assert kb(vault, "read", GIFT, "--reader", "elsewhere").exit_code == 0

    assert kb(vault, "resolve", "sam", "--reader", "elsewhere").exit_code == 1
    assert kb(vault, "resolve", "sam").stdout.splitlines() == [f"[private] {SAM}"]  # the owner still sees it


def test_kb_as_elsewhere_in_a_run_is_allowed_but_an_agent_cannot_widen_to_it(vault: Path) -> None:
    assert kb(vault, "files", "--reader", "elsewhere", env={"SCOUT_MODE": "briefing"}).exit_code == 0
    refused = kb(vault, "files", "--reader", "elsewhere", env={"SCOUT_READER": "agent:person:priya@example.com"})
    assert refused.exit_code == 2
