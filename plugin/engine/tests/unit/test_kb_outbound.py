"""Private-content matchers: ``kb check-outbound`` / ``kb check-query`` (#453, spec §6.1, §8.1, §8.2, §11.2).

Over the privacy fixture vault (spec §11.1). Private notes there: ``sam``
(``name: sam``, alias ``Sammy``), ``task-renew-permit``, ``billing`` (a plain
note, slug only: an ordinary word, so a link or its path names it, #576),
``draft`` and ``broken``. A draft or a query naming one of
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
    "common-word-slug-link": "See [[billing]] for the terms.",
    "common-word-slug-path": "Filed under topics/billing/billing.md today.",
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
    two = "See [[sam]] and [[billing]] %%privacy:allow sam%%"
    text = "\n".join([allowed, other, two])
    assert outbound.find_mentions(text, terms, allow_markers=True) == [2, 3]
    # sends ignore markers: every line is a hit
    assert outbound.find_mentions(text, terms, allow_markers=False) == [1, 2, 3]


@pytest.mark.parametrize(
    "line",
    [
        "Ask _Sammy_ now",  # Slack italics
        "Ask __Sammy__ now",
        "see sammy_notes",
        "| a | [[sam\\|Sam]] |",  # a wikilink in a table, pipe escaped
        "task renew permit is due",  # separators normalised
        "task_renew_permit is due",
        "Task-Renew_Permit is due",
        "q=Sammy%20contract",  # URL-encoded
        "q=task%2Drenew%2Dpermit",
        "q=contract+Sammy",
    ],
)
def test_matcher_bypasses_closed(vault: Path, line: str) -> None:
    terms = outbound.private_terms(vault, load_policy(vault))
    assert outbound.find_mentions(f"{CLEAN}\n{line}", terms, allow_markers=False) == [2]


def test_term_split_by_line_break_reports_every_line(vault: Path) -> None:
    terms = outbound.private_terms(vault, load_policy(vault))
    assert outbound.find_mentions(f"{CLEAN}\nplease renew the task\npermit soon", terms, allow_markers=False) == []
    assert outbound.find_mentions(f"{CLEAN}\nfiled under task-renew-\npermit soon", terms, allow_markers=False) == [
        2,
        3,
    ]
    # with markers, a split term is allowed only when every line it spans carries one
    one = "task %%privacy:allow task-renew-permit%%\nrenew permit"
    both = "task %%privacy:allow task-renew-permit%%\nrenew permit %%privacy:allow task-renew-permit%%"
    assert outbound.find_mentions(one, terms, allow_markers=True) == [1, 2]
    assert outbound.find_mentions(both, terms, allow_markers=True) == []


def test_multi_word_unicode_terms(tmp_path: Path) -> None:
    vault = build_privacy_vault(tmp_path)
    outbound.clear_cache()
    (vault / "knowledge-base" / "people" / "jose-lee.md").write_text(
        "---\nname: José Lee\ntype: person\nvisibility: private\naliases: [Straße Kid, Mary Jane]\n---\n",
        encoding="utf-8",
    )
    terms = outbound.private_terms(vault, load_policy(vault))
    hits = [
        "Call José\nLee tomorrow",  # 1-2: split by a line break
        "STRASSE KID is here",  # 3: full case folding
        "Mary-Jane said hi",  # 4: separators
        "q=Jos%C3%A9+Lee",  # 5: URL-decoded
        "Josélee and Mary Janet don't count",  # 6
    ]
    assert outbound.find_mentions("\n".join(hits), terms, allow_markers=False) == [1, 2, 3, 4, 5]


def test_accents_are_optional_on_both_sides() -> None:
    """A term matches with or without its accents, however the text spells them (precomposed or NFD)."""
    nfd = "Rene\u0301 Lee and Project Cafe\u0301\n"

    def hits(name: str, text: str) -> list[int]:
        terms = outbound.terms_of([outbound.note_from_text(f"people/{name}.md", f"---\nname: {name}\n---\n")])
        return outbound.find_mentions(text, terms, allow_markers=False)

    assert hits("Rene Lee", nfd) == [1]  # an unaccented term, accented text
    assert hits("Project Cafe", nfd) == [1]
    assert hits("Ren\u00e9 Lee", "Call Rene Lee\n") == [1]  # an accented term, unaccented text
    assert hits("Ren\u00e9 Lee", "x\nREN\u00c9 LEE\n") == [2]
    assert hits("Rene Lee", "Renee Lee and Ren Lee\n") == []  # still whole words
    # Hangul vowel jamo aren't accents: a Korean name, precomposed or as jamo, still matches only itself
    assert hits(
        "\ud55c\uad6d\uc5b4 \uc0ac\ub78c", "\u1112\u1161\u11ab\u1100\u116e\u11a8\u110b\u1165 \uc0ac\ub78c\n"
    ) == [1]
    assert hits("\ud55c\uad6d\uc5b4 \uc0ac\ub78c", "\u1112\u1100\u110b \uc0ac\ub78c\n") == []


def test_accents_are_ignored_only_for_longer_terms_and_only_on_latin_greek_cyrillic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def hits(name: str, text: str) -> list[int]:
        terms = outbound.terms_of([outbound.note_from_text(f"people/{name}.md", f"---\nname: {name}\n---\n")])
        return outbound.find_mentions(text, terms, allow_markers=False)

    assert hits("Mila", "Mil\u00e1 Jano, d\u00edky\n") == []  # one word under 5 characters: exact only
    assert hits("Mila", "Mila said\n") == [1]
    assert hits("Milan", "Mil\u00e1n\n") == [1]  # 5+ characters
    assert hits("\u039d\u03af\u03ba\u03bf\u03c2 Lee", "\u039d\u03b9\u03ba\u03bf\u03c2 Lee\n") == [1]  # Greek
    assert hits("\u0419\u043e\u0440\u0434\u0430\u043d", "\u0418\u043e\u0440\u0434\u0430\u043d\n") == [1]  # Cyrillic
    # a Japanese voicing mark changes the word: \u30ab (ka) is not \u30ac (ga)
    assert hits("\u30ab\u30b9\u30bf\u30e0\u8a08\u753b", "\u30ac\u30b9\u30bf\u30e0\u8a08\u753b\n") == []
    assert hits("\u30ab\u30b9\u30bf\u30e0\u8a08\u753b", "\u30ab\u30b9\u30bf\u30e0\u8a08\u753b\n") == [1]
    # Devanagari vowel signs stay
    assert hits("\u0915\u093f\u0924\u093e\u092c\u0918\u0930", "\u0915\u0924\u093e\u092c\u0918\u0930\n") == []
    monkeypatch.setattr(outbound, "UNACCENT_MIN_CHARS", outbound.MIN_TERM_CHARS)
    assert hits("Mila", "Mil\u00e1 Jano\n") == [1]  # one setting widens it to every term


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


# ----- a private note named with an ordinary word (#576) -------------------------------


def _private(vault: Path, rel: str, frontmatter: str = "") -> None:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{frontmatter}visibility: private\n---\n\nBody.\n", encoding="utf-8")


def test_a_common_word_slug_is_not_a_term(vault: Path) -> None:
    """``billing``, ``draft`` and ``broken`` are private slugs here, and ordinary words."""
    terms = outbound.private_terms(vault, load_policy(vault))
    text = "The billing terms changed.\nKeep the draft short.\nThe build is broken."
    assert outbound.find_mentions(text, terms, allow_markers=False) == []
    assert outbound.check_outbound(vault, text, "slack") == []


@pytest.mark.parametrize(
    "line",
    [
        "See [[billing]].",
        "See [[topics/billing/billing|the terms]].",
        "Filed under knowledge-base/topics/billing/billing.md",
        "see `billing/billing`",
        "Status: Billing CANARY-PRIV-3B5L",  # its two-word title
    ],
)
def test_a_common_word_slug_still_matches_as_a_link_its_path_or_title(vault: Path, line: str) -> None:
    terms = outbound.private_terms(vault, load_policy(vault))
    assert outbound.find_mentions(f"{CLEAN}\n{line}", terms, allow_markers=False) == [2]


def test_a_distinctive_one_word_slug_still_matches(tmp_path: Path) -> None:
    own = build_privacy_vault(tmp_path)
    outbound.clear_cache()
    _private(own, "knowledge-base/projects/zephyrine/zephyrine.md")
    _private(own, "knowledge-base/topics/pricing/pricing.md", "name: pricing\ntype: topic\ntitle: Pricing\n")
    terms = outbound.private_terms(own, load_policy(own))
    text = "The pricing page moved.\nThe Zephyrine launch moved.\nq=zephyrine"
    assert outbound.find_mentions(text, terms, allow_markers=False) == [2, 3]


@pytest.mark.parametrize("ext", ["csv", "json", "pdf", "xlsx"])
def test_a_private_file_that_isnt_a_note_follows_the_same_rule(ext: str) -> None:
    """A non-markdown private file has only its stem: ``documents.csv`` must not make "documents" a term."""
    terms = outbound.terms_of(
        [
            outbound.note_from_text(f"private/x/data/documents.{ext}", None),
            outbound.note_from_text(f"private/x/data/zephyrine.{ext}", None),
        ]
    )
    lines = [
        "The documents are in the shared folder.",
        f"See private/x/data/documents.{ext}",
        f"See data/documents.{ext}",
        f"See [[documents.{ext}]]",
        "Zephyrine numbers are in.",
    ]
    assert outbound.find_mentions("\n".join(lines), terms, allow_markers=False) == [2, 3, 4, 5]


@pytest.mark.parametrize(
    ("rel", "frontmatter"),
    [
        ("knowledge-base/people/grace.md", "name: grace\ntype: person\n"),
        ("knowledge-base/people/grace.md", ""),  # no `type:`: the people folder says it
        ("knowledge-base/contacts/grace.md", "name: Grace\ntype: Person\n"),
    ],
)
def test_a_person_keeps_a_name_that_is_an_ordinary_word(tmp_path: Path, rel: str, frontmatter: str) -> None:
    own = build_privacy_vault(tmp_path)
    outbound.clear_cache()
    _private(own, rel, frontmatter)
    terms = outbound.private_terms(own, load_policy(own))
    assert outbound.find_mentions(f"{CLEAN}\nAsk Grace about it.", terms, allow_markers=False) == [2]


@pytest.mark.parametrize("kind", ["organization", "pet", "Company", "client", "org"])
def test_an_organization_or_pet_keeps_a_name_that_is_an_ordinary_word(tmp_path: Path, kind: str) -> None:
    own = build_privacy_vault(tmp_path)
    outbound.clear_cache()
    _private(own, "knowledge-base/ontology/entities/vision.md", f"name: Vision\ntype: {kind}\n")
    terms = outbound.private_terms(own, load_policy(own))
    assert outbound.find_mentions(f"{CLEAN}\nWe met Vision today.", terms, allow_markers=False) == [2]


def test_a_project_named_with_an_ordinary_word_still_drops_it(tmp_path: Path) -> None:
    own = build_privacy_vault(tmp_path)
    outbound.clear_cache()
    _private(own, "knowledge-base/projects/vision/vision.md", "name: vision\ntype: project\n")
    terms = outbound.private_terms(own, load_policy(own))
    assert outbound.find_mentions(
        "Our vision is clear.\nSee projects/vision/vision.md", terms, allow_markers=False
    ) == [2]


def test_a_vault_root_file_named_with_an_ordinary_word_matches_by_its_file_name() -> None:
    """At the vault root there's no second path segment: the file name with its extension is the term."""
    terms = outbound.terms_of(
        [
            outbound.note_from_text("billing.md", "---\nvisibility: private\n---\n"),
            outbound.note_from_text("documents.csv", None),
        ]
    )
    lines = [
        "The billing terms changed.",
        "See billing.md for the terms.",
        "The documents are in.",
        "documents.csv attached",
    ]
    assert outbound.find_mentions("\n".join(lines), terms, allow_markers=False) == [2, 4]


def test_an_alias_is_a_term_even_when_it_is_an_ordinary_word(tmp_path: Path) -> None:
    own = build_privacy_vault(tmp_path)
    outbound.clear_cache()
    _private(own, "knowledge-base/topics/house-move/house-move.md", "aliases: [Budget]\n")
    terms = outbound.private_terms(own, load_policy(own))
    assert outbound.find_mentions(f"{CLEAN}\nThe budget is due.", terms, allow_markers=False) == [2]


# The file names a mature vault files its private notes under: one word each.
MATURE_SLUGS = (
    "plan notes documents readme index todo ideas journal inbox meetings agenda overview summary "
    "roadmap backlog goals decisions research reading status updates review retro report budget "
    "finance pricing billing invoices taxes payroll hiring interviews onboarding travel health "
    "contracts vendors customers strategy marketing sales launch release migration "
    "metrics analytics security privacy legal admin operations process checklist runbook guide "
    "family home house garden recipes books gifts shopping wishlist archive misc draft scratch"
).split()


def test_a_mature_vault_of_one_word_slugs_lets_ordinary_text_through(tmp_path: Path) -> None:
    own = build_privacy_vault(tmp_path)
    outbound.clear_cache()
    for i, slug in enumerate(MATURE_SLUGS):
        folder = ("topics", "projects", "personal", "areas")[i % 4]
        _private(own, f"knowledge-base/{folder}/{slug}/{slug}.md", f"name: {slug}\ntitle: {slug.title()}\n")
    _private(own, "knowledge-base/people/priyanka.md", "name: priyanka\ntype: person\n")
    _private(own, "knowledge-base/projects/zephyrine/zephyrine.md", "name: zephyrine\ntype: project\n")
    terms = outbound.private_terms(own, load_policy(own))
    assert {n.slug for n in terms.notes} >= set(MATURE_SLUGS)
    ordinary = (
        "Status update: the plan for the release is in the notes; see the roadmap and the backlog.\n"
        "Budget review and pricing decisions moved to the agenda for the next meeting.\n"
        "Hiring, onboarding and security reviews are on track. Reading list in the inbox.\n"
        "README and index documents need a refresh before launch; the draft checklist is in misc."
    )
    assert outbound.find_mentions(ordinary, terms, allow_markers=False) == []
    assert outbound.find_mentions(f"{ordinary}\nAsk Priyanka.\nZephyrine slipped.", terms, allow_markers=False) == [
        5,
        6,
    ]
    assert outbound.find_mentions("See [[roadmap]].\nSee topics/plan/plan.md", terms, allow_markers=False) == [1, 2]


# ----- check_outbound ------------------------------------------------------------------


@pytest.mark.parametrize("surface", SHARED + QUERY_SURFACES)
@pytest.mark.parametrize("kind", ["slug", "alias", "wikilink", "common-word-slug-path"])
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


def test_linked_slugs_ignores_links_without_a_target(vault: Path) -> None:
    terms = outbound.private_terms(vault, load_policy(vault))
    assert outbound.linked_slugs("[[#Open questions]] and [[ ]]", terms) == set()
    assert outbound.linked_slugs("[[#heading]] then [[Sammy]]", terms) == {"sam"}
