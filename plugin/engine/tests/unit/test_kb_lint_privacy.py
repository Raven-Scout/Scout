"""Privacy kb-lint checks and the widening guard (#453, spec §8.2, §8.3, §11.2 Lint).

Over the privacy fixture vault (spec §11.1). Shared files there:
``drafts/reply-to-priya.md`` (the ``drafts/**`` default) and the notes that
declare a ``federation_scope`` (``widget.md``, ``notes.md``). Private notes:
``sam`` (alias ``Sammy``), ``task-renew-permit``, ``billing``, ``draft``,
``broken``. ``priya.md`` and the daily file are owner-only: they may link
private notes freely.

The blocking checks (``private-link``, ``private-mention``) hold for every
commit, the owner's included, in ``report`` mode and with
``SCOUT_LINT_OVERRIDE`` set. The widening guard only checks a scheduled run's
commit (``SCOUT_MODE`` set). No output names a private note or term.
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout.cli import app
from scout.kb import outbound
from scout.kb.lint import run_hook
from scout.kb.lint_privacy import ALLOW_MARKER_RE, privacy_report, privacy_staged
from scout.kb.ontology import clear_frontmatter_cache
from tests.unit.privacy_vault_helpers import CANARIES, action_items_rel, build_privacy_vault, git

DRAFT = "drafts/reply-to-priya.md"
WIDGET = "knowledge-base/projects/widget/widget.md"
PRIYA = "knowledge-base/people/priya.md"
SAM = "knowledge-base/people/sam.md"
PERMIT = "knowledge-base/personal/task-renew-permit.md"
GIFT = "knowledge-base/personal/task-buy-gift.md"
PLAIN = "knowledge-base/topics/misc/plain.md"
RUN = {"SCOUT_MODE": "morning-briefing"}
# Words that name a private note in the fixture, which no output may contain.
PRIVATE_WORDS = ("sammy", "[[sam", "task-renew-permit", "billing", "canary", "parking permit")


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    clear_frontmatter_cache()
    outbound.clear_cache()
    return build_privacy_vault(tmp_path)


def _read(vault: Path, rel: str) -> str:
    return (vault / rel).read_text(encoding="utf-8")


def stage(vault: Path, rel: str, text: str) -> None:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    git(vault, "add", "--", rel)


def append(vault: Path, rel: str, line: str) -> int:
    """Append one line to ``rel`` and stage it; returns its 1-based line number."""
    text = _read(vault, rel)
    if not text.endswith("\n"):
        text += "\n"
    text += line + "\n"
    stage(vault, rel, text)
    return text.count("\n")


def commit(vault: Path, subject: str = "owner edit") -> None:
    git(vault, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", subject)


def hook(vault: Path, env: dict[str, str] | None = None) -> tuple[int, str]:
    buf = io.StringIO()
    rc = run_hook(vault, env=env or {}, out=buf)
    return rc, buf.getvalue()


def checks(vault: Path, env: dict[str, str] | None = None) -> list[tuple[str, str, bool, int | None]]:
    findings = privacy_staged(vault, env=env or {})
    return sorted((f.path, f.check, f.blocking, f.line) for f in findings)


def assert_no_private_words(text: str) -> None:
    low = text.lower()
    for word in PRIVATE_WORDS:
        assert word not in low, word
    for canary in CANARIES.values():
        assert canary.lower() not in low


# ----- private-link / private-mention ------------------------------------------


@pytest.mark.parametrize("rel", [DRAFT, WIDGET])
def test_a_private_link_in_a_shared_file_blocks_an_interactive_commit(vault: Path, rel: str) -> None:
    n = append(vault, rel, "See [[sam]] for the details.")
    assert checks(vault) == [(rel, "private-link", True, n)]
    rc, out = hook(vault)
    assert rc == 1
    assert f"{rel}:{n}" in out and "[private-link]" in out
    assert_no_private_words(out)


def test_it_blocks_in_report_mode_and_ignores_the_override(vault: Path) -> None:
    with (vault / "scout-config.yaml").open("a", encoding="utf-8") as fh:
        fh.write("kb_lint:\n  mode: report\n")
    git(vault, "add", "scout-config.yaml")
    commit(vault, "report mode")
    append(vault, DRAFT, "See [[sam]] for the details.")
    rc, out = hook(vault, {"SCOUT_LINT_OVERRIDE": "need it now"})
    assert rc == 1
    assert "override accepted" not in out
    assert not (vault / ".scout-logs" / "lint-overrides.log").exists()


@pytest.mark.parametrize(
    "line",
    [
        "See [[Sammy]] for the details.",  # alias (Review Focus 4)
        "See [[sam|our contractor]] for the details.",  # display text
        "See [[people/sam]] for the details.",  # path
        "See [[sam.md#Open questions]].",  # .md and heading
    ],
)
def test_a_link_by_alias_display_name_or_path_is_a_private_link(vault: Path, line: str) -> None:
    n = append(vault, DRAFT, line)
    assert checks(vault) == [(DRAFT, "private-link", True, n)]


@pytest.mark.parametrize(
    "line", ["Ask Sammy about the contract.", "Remember task-renew-permit before Friday.", "The billing terms changed."]
)
def test_naming_a_private_note_in_a_shared_file_is_a_private_mention(vault: Path, line: str) -> None:
    n = append(vault, DRAFT, line)
    assert checks(vault) == [(DRAFT, "private-mention", True, n)]
    rc, out = hook(vault)
    assert rc == 1
    assert_no_private_words(out)


def test_the_same_line_marker_lets_exactly_that_link_through(vault: Path) -> None:
    append(vault, DRAFT, "See [[sam]] for the details. %%privacy:allow sam%%")
    assert checks(vault) == []
    assert hook(vault)[0] == 0


def test_a_marker_for_another_target_allows_nothing(vault: Path) -> None:
    n = append(vault, DRAFT, "See [[sam]] for the details. %%privacy:allow priya%%")
    assert checks(vault) == [(DRAFT, "private-link", True, n)]


def test_a_marker_on_another_line_allows_nothing(vault: Path) -> None:
    append(vault, DRAFT, "%%privacy:allow sam%%")
    n = append(vault, DRAFT, "See [[sam]] for the details.")
    assert checks(vault) == [(DRAFT, "private-link", True, n)]


def test_a_marker_allows_one_target_not_a_second_one_on_the_line(vault: Path) -> None:
    n = append(vault, DRAFT, "See [[sam]] and the billing terms. %%privacy:allow sam%%")
    assert checks(vault) == [(DRAFT, "private-mention", True, n)]


@pytest.mark.parametrize("rel", [PRIYA, "daily"])
def test_owner_only_files_may_link_private_notes(vault: Path, rel: str) -> None:
    rel = action_items_rel() if rel == "daily" else rel
    append(vault, rel, "- [ ] Follow up with [[sam]] and Sammy")
    assert checks(vault) == []
    assert hook(vault)[0] == 0


def test_an_old_link_in_an_edited_shared_file_does_not_block(vault: Path) -> None:
    append(vault, DRAFT, "See [[sam]] for the details.")
    commit(vault)
    append(vault, DRAFT, "Thanks again!")
    assert checks(vault) == []
    assert hook(vault)[0] == 0


def test_a_new_shared_file_checks_every_line(vault: Path) -> None:
    stage(vault, "drafts/note-to-alex.md", "# Note\n\nSee [[sam]].\n")
    assert checks(vault) == [("drafts/note-to-alex.md", "private-link", True, 3)]


def test_a_note_that_declares_federation_scope_in_the_staged_text_is_shared(vault: Path) -> None:
    stage(vault, PLAIN, "---\nfederation_scope: org\n---\n# Plain note\n\nSee [[sam]].\n")
    assert checks(vault) == [(PLAIN, "private-link", True, 6)]


def test_marking_a_note_private_does_not_block_the_commit_that_does_it(vault: Path) -> None:
    stage(vault, PRIYA, _read(vault, PRIYA).replace("type: person\n", "type: person\nvisibility: private\n", 1))
    assert checks(vault) == []
    assert hook(vault)[0] == 0


def test_shared_files_add_in_the_overlay_widens_the_lint_scope(vault: Path) -> None:
    with (vault / "scout-config.yaml").open("a", encoding="utf-8") as fh:
        fh.write('  shared_files_add:\n    - "outbox/**"\n')
    git(vault, "add", "scout-config.yaml")
    commit(vault, "outbox is shared")
    stage(vault, "outbox/slack.txt", "Ask Sammy.\n")
    assert checks(vault) == [("outbox/slack.txt", "private-mention", True, 1)]


# ----- the warn / error rules ---------------------------------------------------


def test_bad_visibility_and_federation_values_warn(vault: Path) -> None:
    stage(vault, PLAIN, "---\nvisibility: secret\n---\n# Plain note\n")
    stage(vault, PRIYA, _read(vault, PRIYA).replace("type: person\n", "type: person\nfederation_scope: everyone\n", 1))
    assert checks(vault) == [(PRIYA, "visibility-value", False, None), (PLAIN, "visibility-value", False, None)]
    assert hook(vault)[0] == 0


def test_private_with_federation_scope_is_a_contradiction(vault: Path) -> None:
    stage(vault, PRIYA, _read(vault, PRIYA).replace("type: person\n", "type: person\nvisibility: private\n", 1))
    stage(
        vault,
        WIDGET,
        _read(vault, WIDGET).replace("federation_scope: org\n", "federation_scope: org\nvisibility: private\n"),
    )
    assert checks(vault) == [(WIDGET, "privacy-contradiction", False, None)]


def test_unparseable_frontmatter_warns_in_the_hook(vault: Path) -> None:
    stage(vault, PLAIN, "---\ntitle: [oops\n---\n# Plain note\n")
    assert checks(vault) == [(PLAIN, "frontmatter-unparseable", False, None)]
    rc, out = hook(vault)
    assert rc == 0 and "[frontmatter-unparseable]" in out


def test_a_stage_2_overlay_key_warns(vault: Path) -> None:
    with (vault / "scout-config.yaml").open("a", encoding="utf-8") as fh:
        fh.write("  federation:\n    enabled: true\n")
    git(vault, "add", "scout-config.yaml")
    assert checks(vault) == [("scout-config.yaml", "federation-reserved", False, None)]


# ----- the commit, not the working tree ----------------------------------------------


def test_an_unstaged_overlay_can_not_make_a_staged_mention_safe(vault: Path) -> None:
    n = append(vault, DRAFT, "Remember task-renew-permit before Friday.")
    with (vault / "scout-config.yaml").open("a", encoding="utf-8") as fh:  # unstaged
        fh.write('  visibility_defaults:\n    - {glob: "knowledge-base/personal/**", visibility: normal}\n')
    assert checks(vault) == [(DRAFT, "private-mention", True, n)]


def test_an_unstaged_edit_making_sam_normal_does_not_lift_the_block(vault: Path) -> None:
    n = append(vault, DRAFT, "See [[sam]] for the details.")
    (vault / SAM).write_text(_read(vault, SAM).replace("visibility: private", "visibility: normal"))  # unstaged
    assert checks(vault) == [(DRAFT, "private-link", True, n)]


def test_a_staged_note_made_normal_still_counts_as_private(vault: Path) -> None:
    n = append(vault, DRAFT, "See [[sam]] for the details.")
    stage(vault, SAM, _read(vault, SAM).replace("visibility: private", "visibility: normal"))
    assert (DRAFT, "private-link", True, n) in checks(vault)


def test_a_note_private_only_in_the_index_counts(vault: Path) -> None:
    stage(vault, PRIYA, _read(vault, PRIYA).replace("type: person\n", "type: person\nvisibility: private\n", 1))
    (vault / PRIYA).write_text(_read(vault, PRIYA).replace("visibility: private\n", ""))  # disk: normal again
    n = append(vault, DRAFT, "See [[priya]] about it.")
    assert checks(vault) == [(DRAFT, "private-link", True, n)]


# ----- a file that becomes shared --------------------------------------------------


def test_a_note_renamed_into_drafts_has_every_line_checked(vault: Path) -> None:
    git(vault, "mv", PRIYA, "drafts/x.md")
    found = checks(vault)
    assert found and all(f[:3] == ("drafts/x.md", "private-link", True) for f in found)


def test_a_note_given_a_federation_scope_has_every_line_checked(vault: Path) -> None:
    stage(vault, PRIYA, _read(vault, PRIYA).replace("type: person\n", "type: person\nfederation_scope: org\n", 1))
    found = checks(vault)
    assert found and all(f[:3] == (PRIYA, "private-link", True) for f in found)


def test_a_federation_scope_note_outside_the_kb_is_shared(vault: Path) -> None:
    """Ruling R23: a staged .md declaring federation_scope is checked wherever it lives."""
    stage(vault, "notes/outside.md", "---\nfederation_scope: org\n---\nSee [[sam]].\n")
    assert checks(vault) == [("notes/outside.md", "private-link", True, 4)]
    assert checks(vault, RUN) == [
        ("notes/outside.md", "privacy-widening", True, None),
        ("notes/outside.md", "private-link", True, 4),
    ]


# ----- git can't hide a line ----------------------------------------------------------


def test_a_nul_byte_does_not_hide_a_line(vault: Path) -> None:
    path = vault / DRAFT
    path.write_bytes(path.read_bytes() + b"bin\x00ary\nSee [[sam]].\n")
    git(vault, "add", "--", DRAFT)
    found = checks(vault)
    assert [f[1] for f in found] == ["private-link"]


@pytest.mark.parametrize("attr", ["drafts/** -diff", "drafts/** binary", "*.md -text"])
def test_gitattributes_do_not_hide_a_line(vault: Path, attr: str) -> None:
    stage(vault, ".gitattributes", attr + "\n")
    n = append(vault, DRAFT, "See [[sam]].")
    assert checks(vault) == [(DRAFT, "private-link", True, n)]


# ----- the widening guard (scheduled runs) ----------------------------------------


def test_a_run_may_not_make_a_private_note_normal(vault: Path) -> None:
    stage(vault, SAM, _read(vault, SAM).replace("visibility: private", "visibility: normal"))
    assert checks(vault, RUN) == [(SAM, "privacy-widening", True, None)]
    rc, out = hook(vault, {**RUN, "SCOUT_LINT_OVERRIDE": "x"})
    assert rc == 1 and "[privacy-widening]" in out
    # The owner may.
    assert checks(vault) == []


def test_a_run_may_not_drop_the_visibility_line_of_a_private_personal_note(vault: Path) -> None:
    stage(
        vault, PERMIT, _read(vault, PERMIT).replace("domain: personal\n", "domain: personal\nvisibility: private\n", 1)
    )
    commit(vault)
    stage(vault, PERMIT, _read(vault, PERMIT).replace("visibility: private\n", "", 1))
    assert checks(vault, RUN) == [(PERMIT, "privacy-widening", True, None)]
    assert checks(vault) == []


def test_a_run_may_not_move_a_private_note_out_of_its_private_folder(vault: Path) -> None:
    git(vault, "mv", PERMIT, "knowledge-base/topics/misc/task-renew-permit.md")
    assert checks(vault, RUN) == [("knowledge-base/topics/misc/task-renew-permit.md", "privacy-widening", True, None)]


def test_a_run_may_not_set_or_widen_federation_scope(vault: Path) -> None:
    stage(vault, PLAIN, "---\nfederation_scope: org\n---\n" + _read(vault, PLAIN))
    stage(
        vault,
        "knowledge-base/projects/widget/notes.md",
        _read(vault, "knowledge-base/projects/widget/notes.md").replace(
            "[person:priya@example.com]", "[person:priya@example.com, person:alex@example.com]"
        ),
    )
    assert checks(vault, RUN) == [
        ("knowledge-base/projects/widget/notes.md", "privacy-widening", True, None),
        (PLAIN, "privacy-widening", True, None),
    ]


def test_a_run_may_narrow(vault: Path) -> None:
    stage(
        vault,
        WIDGET,
        _read(vault, WIDGET).replace("federation_scope: org", "federation_scope: [person:priya@example.com]"),
    )
    stage(vault, GIFT, _read(vault, GIFT).replace("visibility: normal\n", "", 1))  # personal/ default: private
    stage(vault, "knowledge-base/topics/billing/rollout.md", "---\ntitle: Billing rollout\nvisibility: private\n---\n")
    assert checks(vault, RUN) == []


def test_a_run_may_not_add_a_marker(vault: Path) -> None:
    n = append(vault, PRIYA, "Works with Alex. %%privacy:allow priya%%")
    assert checks(vault, RUN) == [(PRIYA, "privacy-widening", True, n)]
    assert checks(vault) == []


def test_a_run_may_reedit_a_line_that_keeps_the_owners_marker(vault: Path) -> None:
    append(vault, DRAFT, "See [[sam]] soon. %%privacy:allow sam%%")
    commit(vault)
    stage(vault, DRAFT, _read(vault, DRAFT).replace("See [[sam]] soon.", "See [[sam]] today."))
    assert checks(vault, RUN) == []


def test_a_run_may_not_move_a_marker_to_a_new_line(vault: Path) -> None:
    append(vault, PRIYA, "Old line. %%privacy:allow priya%%")
    commit(vault)
    n = append(vault, PRIYA, "New line. %%privacy:allow priya%%")
    assert checks(vault, RUN) == [(PRIYA, "privacy-widening", True, n)]


def test_a_run_commit_with_an_unparseable_config_is_refused(vault: Path) -> None:
    from scout.kb.lint_privacy import PolicyUnreadable

    stage(vault, "scout-config.yaml", "privacy: [unclosed\n")
    with pytest.raises(PolicyUnreadable):
        privacy_staged(vault, env=RUN)
    rc, out = hook(vault, RUN)
    assert rc == 1 and "[privacy-check-failed]" in out
    assert ("scout-config.yaml", "privacy-overlay-unreadable", False, None) in checks(vault)


def test_a_run_may_not_touch_the_privacy_block(vault: Path) -> None:
    text = _read(vault, "scout-config.yaml")
    stage(vault, "scout-config.yaml", text.replace('"trading/**"', '"trading/**"\n    - "taxes/**"'))
    assert checks(vault, RUN) == [("scout-config.yaml", "privacy-widening", True, None)]
    stage(vault, "scout-config.yaml", text.replace("name: Alex", "name: Alex B"))
    assert checks(vault, RUN) == []


def test_a_run_adding_a_private_link_to_a_draft_is_blocked_too(vault: Path) -> None:
    n = append(vault, DRAFT, "See [[sam]].")
    assert checks(vault, RUN) == [(DRAFT, "private-link", True, n)]


# ----- kb lint --report -----------------------------------------------------------


def test_report_lists_markers_and_old_unmarked_hits_in_shared_files(vault: Path) -> None:
    marked = append(vault, DRAFT, "See [[sam]]. %%privacy:allow sam%%")
    unmarked = append(vault, DRAFT, "Ask Sammy too.")
    append(vault, PRIYA, "Linked from [[sam]] freely.")  # owner-only: not listed
    commit(vault)
    rep = privacy_report(vault)
    assert rep.markers == [(DRAFT, marked, "sam")]
    assert rep.unmarked == [(DRAFT, unmarked)]

    result = CliRunner().invoke(app, ["kb", "lint", "--report", "--repo", str(vault)])
    assert result.exit_code == 0, result.output
    assert f"{DRAFT}:{marked}" in result.output and "allow sam" in result.output
    assert f"{DRAFT}:{unmarked}" in result.output
    unmarked_lines = [ln for ln in result.output.splitlines() if f":{unmarked}" in ln]
    assert_no_private_words("\n".join(unmarked_lines))


def test_report_lists_unparseable_frontmatter_as_an_error(vault: Path) -> None:
    result = CliRunner().invoke(app, ["kb", "lint", "--report", "--repo", str(vault)])
    assert result.exit_code == 0, result.output
    line = next(ln for ln in result.output.splitlines() if "knowledge-base/topics/misc/broken.md" in ln)
    assert "error" in line and "frontmatter-unparseable" in line


def test_allow_marker_regex_is_the_matchers_own() -> None:
    """One definition (scout.kb.outbound): case-insensitive, spaces or tabs around the parts."""
    assert ALLOW_MARKER_RE == outbound.ALLOW_MARKER_RE
    marker = re.compile(ALLOW_MARKER_RE, re.IGNORECASE)
    assert marker.fullmatch("%%privacy:allow task-renew-permit%%")
    assert marker.search("x %% privacy:allow\tsam.notes_2 %% y").group(1) == "sam.notes_2"  # type: ignore[union-attr]
    assert marker.search("%%PRIVACY:ALLOW Sam%%").group(1) == "Sam"  # type: ignore[union-attr]
    assert not marker.search("%%privacy:allow%%")


def test_hook_findings_are_logged_without_content(vault: Path) -> None:
    append(vault, DRAFT, "Ask Sammy about it.")
    hook(vault)
    rows = [json.loads(ln) for ln in (vault / ".scout-logs" / "kb-lint.jsonl").read_text().splitlines()]
    assert [r["check"] for r in rows] == ["private-mention"]
    assert_no_private_words(json.dumps(rows))
