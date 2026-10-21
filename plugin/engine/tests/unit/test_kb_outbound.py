"""Private-content matchers: ``kb check-outbound`` / ``kb check-query`` (#453, spec §6.1, §8.1, §8.2, §11.2).

Over the privacy fixture vault (spec §11.1). Private notes there: ``sam``
(``name: sam``, alias ``Sammy``), ``task-renew-permit``, ``billing`` (a plain
note, slug only), ``draft`` and ``broken``. A draft or a query naming one of
them fails for every shared surface and passes for owner-only ones; the
output names line numbers only, never the matched term.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from scout.cli import app
from scout.kb import outbound
from scout.kb.ontology import clear_frontmatter_cache
from scout.kb.privacy import load_policy
from tests.unit.privacy_vault_helpers import CANARIES, build_privacy_vault

runner = CliRunner()
AGENT = "agent:person:priya@example.com"
SHARED = ["slack", "connector_write", "public_repo", "telegram_group_chat"]
QUERY_SURFACES = ["connector_query", "web_query"]
OWNER_ONLY = ["vault", "telegram_private_chat"]
CLEAN = "Review the PROJ-1234 rollout with Priya."

# One hit per kind of private term: slug, name:, alias, wikilink.
HITS = {
    "slug": "Remember task-renew-permit before Friday.",
    "name": "Ask Sam about the contract.",  # 3 chars: must NOT hit (see below)
    "alias": "Ask Sammy about the contract.",
    "wikilink": "See [[sam]] for details.",
    "wikilink-display": "See [[sam|our contractor]] for details.",
    "wikilink-path": "See [[people/sam#Open questions]].",
    "wikilink-alias": "See [[Sammy]].",
    "plain-slug": "The billing terms changed.",
    "multi-word-name-case": "TASK-RENEW-PERMIT is due.",
}


@pytest.fixture(scope="module")
def shared_vault(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_privacy_vault(tmp_path_factory.mktemp("kb-outbound"))


@pytest.fixture
def vault(shared_vault: Path) -> Path:
    clear_frontmatter_cache()
    outbound.clear_cache()
    return shared_vault


def kb(vault: Path, *args: str, reader: str | None = None) -> Result:
    env = {"SCOUT_READER": reader} if reader else {}
    return runner.invoke(app, ["kb", *args, "--repo", str(vault)], env=env)


def _log_rows(vault: Path) -> list[dict[str, object]]:
    path = vault / ".scout-logs" / "kb-reads.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _no_leak(text: str) -> None:
    lowered = text.lower()
    for term in ("sam", "sammy", "task-renew-permit", "billing", "renew", "canary"):
        assert term not in lowered, f"{term!r} leaked: {text!r}"
    for canary in CANARIES.values():
        assert canary not in text


# ----- the matcher ---------------------------------------------------------------------


@pytest.mark.parametrize("kind", [k for k in HITS if k != "name"])
def test_find_mentions_hits(vault: Path, kind: str) -> None:
    terms = outbound.private_terms(vault, load_policy(vault))
    text = f"{CLEAN}\n{HITS[kind]}\n{CLEAN}"
    assert outbound.find_mentions(text, terms, allow_markers=False) == [2]


def test_three_char_name_and_substrings_dont_match(vault: Path) -> None:
    terms = outbound.private_terms(vault, load_policy(vault))
    text = "\n".join(
        [
            HITS["name"],  # `sam` is 3 chars
            "Sammyson and samsung are not Sammy.x? no: Sammyson",
            "rebilling and billings are different words",
            "my-task-renew-permits",
        ]
    )
    # line 2 does contain "Sammy." (whole word before the dot): that one must hit
    assert outbound.find_mentions(text, terms, allow_markers=False) == [2]
    assert outbound.find_mentions("Sammyson\nrebilling\nbillings", terms, allow_markers=False) == []


def test_name_and_aliases_of_entities_are_terms(vault: Path) -> None:
    terms = outbound.private_terms(vault, load_policy(vault))
    by_slug = {n.slug: n for n in terms.notes}
    assert {"sam", "task-renew-permit", "billing"} <= set(by_slug)
    assert "sammy" in {t.lower() for t in by_slug["sam"].terms}
    assert "sam" not in {t.lower() for t in by_slug["sam"].terms}  # under 4 chars
    assert "priya" not in by_slug  # normal notes aren't private terms
    assert "task-buy-gift" not in by_slug


def test_marker_allows_only_its_own_target_and_only_for_lint(vault: Path) -> None:
    terms = outbound.private_terms(vault, load_policy(vault))
    allowed = "See [[sam]] %%privacy:allow sam%%"
    other = "See [[sam]] %%privacy:allow billing%%"
    two = "See [[sam]] and the billing terms %%privacy:allow sam%%"
    text = "\n".join([allowed, other, two])
    assert outbound.find_mentions(text, terms, allow_markers=True) == [2, 3]
    # sends ignore markers: every line is a hit
    assert outbound.find_mentions(text, terms, allow_markers=False) == [1, 2, 3]


def test_terms_follow_vault_edits(vault: Path, tmp_path: Path) -> None:
    own = build_privacy_vault(tmp_path)
    outbound.clear_cache()
    policy = load_policy(own)
    assert outbound.find_mentions("Ask Priyanka.", outbound.private_terms(own, policy), allow_markers=False) == []
    (own / "knowledge-base" / "people" / "priyanka.md").write_text(
        "---\nname: priyanka\ntype: person\nvisibility: private\naliases: [Pri Pri Two]\n---\n", encoding="utf-8"
    )
    terms = outbound.private_terms(own, policy)
    # a multi-word term matches across any run of whitespace, at word boundaries only
    assert outbound.find_mentions(
        "Ask Priyanka.\nAsk pri  pri two\nAsk pri pri twofold", terms, allow_markers=False
    ) == [
        1,
        2,
    ]
    assert outbound.find_mentions("Ask (pri pri two)!", terms, allow_markers=False) == [1]


# ----- check_outbound ------------------------------------------------------------------


@pytest.mark.parametrize("surface", SHARED + QUERY_SURFACES)
@pytest.mark.parametrize("kind", ["slug", "alias", "wikilink", "plain-slug"])
def test_check_outbound_blocks_shared(vault: Path, surface: str, kind: str) -> None:
    assert outbound.check_outbound(vault, f"{CLEAN}\n\n{HITS[kind]}", surface) == [3]


@pytest.mark.parametrize("surface", OWNER_ONLY)
def test_check_outbound_passes_owner_only(vault: Path, surface: str) -> None:
    assert outbound.check_outbound(vault, "\n".join(HITS.values()), surface) == []


def test_unknown_surface_is_shared(vault: Path) -> None:
    assert outbound.check_outbound(vault, HITS["alias"], "carrier_pigeon") == [1]


def test_check_outbound_ignores_a_marker(vault: Path) -> None:
    assert outbound.check_outbound(vault, "See [[sam]] %%privacy:allow sam%%", "slack") == [1]


def test_check_outbound_canary_title_hits(vault: Path) -> None:
    # A private note's title is private content too (spec §4.2).
    assert outbound.check_outbound(vault, "Status: Renew parking permit CANARY-PRIV-1R7K", "slack") == [1]


# ----- CLI -----------------------------------------------------------------------------


@pytest.mark.parametrize("reader", ["owner", "autonomous"])
@pytest.mark.parametrize("surface", SHARED)
def test_cli_check_outbound_blocks_with_lines_only(vault: Path, tmp_path: Path, reader: str, surface: str) -> None:
    draft = tmp_path / "draft.md"
    draft.write_text(f"{CLEAN}\n{HITS['alias']}\n{CLEAN}\n{HITS['wikilink']}\n", encoding="utf-8")
    result = kb(vault, "check-outbound", str(draft), "--surface", surface, reader=reader)
    assert result.exit_code == 1, result.output
    assert result.stdout.strip() == "blocked: lines 2, 4"
    _no_leak(result.output)
    as_json = kb(vault, "check-outbound", str(draft), "--surface", surface, "--json", reader=reader)
    assert as_json.exit_code == 1
    assert json.loads(as_json.stdout) == {"ok": False, "lines": [2, 4]}


@pytest.mark.parametrize("surface", OWNER_ONLY)
def test_cli_check_outbound_passes_owner_only(vault: Path, tmp_path: Path, surface: str) -> None:
    draft = tmp_path / "draft.md"
    draft.write_text("\n".join(HITS.values()), encoding="utf-8")
    result = kb(vault, "check-outbound", str(draft), "--surface", surface)
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "ok"
    as_json = kb(vault, "check-outbound", str(draft), "--surface", surface, "--json")
    assert json.loads(as_json.stdout) == {"ok": True, "lines": []}


def test_cli_check_outbound_clean_passes(vault: Path, tmp_path: Path) -> None:
    draft = tmp_path / "draft.md"
    draft.write_text(CLEAN, encoding="utf-8")
    result = kb(vault, "check-outbound", str(draft), "--surface", "slack")
    assert (result.exit_code, result.stdout.strip()) == (0, "ok")


@pytest.mark.parametrize("surface", QUERY_SURFACES)
def test_cli_check_query_alias_only_fails(vault: Path, surface: str) -> None:
    """Review Focus 3: a query with only a private note's alias (no slug) is blocked."""
    result = kb(vault, "check-query", "contract renewal Sammy", "--surface", surface, reader="autonomous")
    assert result.exit_code == 1, result.output
    assert result.stdout.strip() == "blocked"
    _no_leak(result.output)
    as_json = kb(vault, "check-query", "contract renewal Sammy", "--surface", surface, "--json")
    assert (as_json.exit_code, json.loads(as_json.stdout)) == (1, {"ok": False})


@pytest.mark.parametrize("surface", QUERY_SURFACES)
def test_cli_check_query_clean_passes(vault: Path, surface: str) -> None:
    result = kb(vault, "check-query", "widget rollout PROJ-1234", "--surface", surface)
    assert (result.exit_code, result.stdout.strip()) == (0, "ok")
    as_json = kb(vault, "check-query", "widget rollout PROJ-1234", "--surface", surface, "--json")
    assert json.loads(as_json.stdout) == {"ok": True}


@pytest.mark.parametrize("args", [["check-query", "Sammy", "--surface", "web_query"], ["check-outbound"]])
def test_cli_agent_reader_refused(vault: Path, tmp_path: Path, args: list[str]) -> None:
    if args == ["check-outbound"]:
        draft = tmp_path / "draft.md"
        draft.write_text(HITS["alias"], encoding="utf-8")
        args = ["check-outbound", str(draft), "--surface", "slack"]
    result = kb(vault, *args, reader=AGENT)
    assert result.exit_code == 2, result.output
    assert "ok" != result.stdout.strip() and "blocked" not in result.output
    _no_leak(result.output)


def test_cli_missing_draft_is_usage_error(vault: Path, tmp_path: Path) -> None:
    result = kb(vault, "check-outbound", str(tmp_path / "nope.md"), "--surface", "slack")
    assert result.exit_code == 2
    assert result.stdout == ""


def test_blocked_check_writes_log_event(tmp_path: Path) -> None:
    vault = build_privacy_vault(tmp_path)
    outbound.clear_cache()
    assert _log_rows(vault) == []
    ok = kb(vault, "check-query", "widget rollout", "--surface", "web_query")  # owner, passes: not logged
    assert ok.exit_code == 0 and _log_rows(vault) == []
    blocked = kb(vault, "check-query", "Sammy contract", "--surface", "web_query")
    assert blocked.exit_code == 1
    rows = _log_rows(vault)
    assert len(rows) == 1
    row = rows[0]
    assert row["blocked"] is True
    assert row["surface"] == "web_query"
    assert row["command"] == "check-query"
    assert row["reader"] == "owner"
    raw = (vault / ".scout-logs" / "kb-reads.jsonl").read_text(encoding="utf-8")
    assert "Sammy" not in raw and "contract" not in raw
