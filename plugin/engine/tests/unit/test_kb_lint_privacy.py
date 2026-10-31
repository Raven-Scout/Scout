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


_SHARE_TOPICS = (
    "  federation:\n    enabled: true\n  federation_defaults:\n"
    '    - {glob: "knowledge-base/topics/misc/**", scope: ["person:priya@example.com"]}\n'
)


def test_stage_2_overlay_keys_no_longer_warn(vault: Path) -> None:
    with (vault / "scout-config.yaml").open("a", encoding="utf-8") as fh:
        fh.write(_SHARE_TOPICS)
    git(vault, "add", "scout-config.yaml")
    assert checks(vault) == []


def test_a_note_shared_by_a_folder_default_is_a_shared_file(vault: Path) -> None:
    """Agnes #6090 §4: a note with an effective share blocks a private link like any shared file."""
    with (vault / "scout-config.yaml").open("a", encoding="utf-8") as fh:
        fh.write(_SHARE_TOPICS)
    git(vault, "add", "scout-config.yaml")
    commit(vault, "share topics/misc with Priya")
    n = append(vault, PLAIN, "See [[sam]] for the details.")
    assert checks(vault) == [(PLAIN, "private-link", True, n)]


def test_the_same_note_is_owner_only_while_sharing_is_off(vault: Path) -> None:
    with (vault / "scout-config.yaml").open("a", encoding="utf-8") as fh:
        fh.write(_SHARE_TOPICS.replace("enabled: true", "enabled: false"))
    git(vault, "add", "scout-config.yaml")
    commit(vault, "share topics/misc later")
    append(vault, PLAIN, "See [[sam]] for the details.")
    assert checks(vault) == []


def test_a_scope_wider_than_the_ceiling_warns(vault: Path) -> None:
    stage(vault, PRIYA, _read(vault, PRIYA).replace("type: person\n", "type: person\nfederation_scope: org\n", 1))
    assert (PRIYA, "federation-ceiling", False, None) in checks(vault)


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
    found = [f for f in checks(vault) if f[1] != "federation-ceiling"]  # a people note's ceiling is none
    assert found and all(f[:3] == (PRIYA, "private-link", True) for f in found)


def test_a_federation_scope_note_outside_the_kb_is_shared(vault: Path) -> None:
    """Ruling R23: a staged .md declaring federation_scope is checked wherever it lives."""
    stage(vault, "notes/outside.md", "---\nfederation_scope: org\n---\nSee [[sam]].\n")
    assert checks(vault) == [
        ("notes/outside.md", "federation-ceiling", False, None),  # outside every shareable folder
        ("notes/outside.md", "private-link", True, 4),
    ]
    assert checks(vault, RUN) == [
        ("notes/outside.md", "federation-ceiling", False, None),
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


def test_the_autonomous_reader_alone_makes_a_commit_a_run(vault: Path) -> None:
    """Ruling R27: the widening guard and the fail-closed path share one predicate."""
    from scout.kb.lint import is_scheduled

    env = {"SCOUT_READER": "autonomous"}
    assert is_scheduled(env)
    stage(vault, SAM, _read(vault, SAM).replace("visibility: private", "visibility: normal"))
    assert checks(vault, env) == [(SAM, "privacy-widening", True, None)]
    assert checks(vault, {"SCOUT_READER": "owner"}) == []


@pytest.mark.parametrize("reader", ["agent:person:priya@example.com", "agent:group:eng", "garbage", ""])
def test_an_agent_or_malformed_reader_is_checked_for_widening(vault: Path, reader: str) -> None:
    """``resolve_reader`` fails such a reader closed (#490), so the guard does too."""
    stage(vault, SAM, _read(vault, SAM).replace("visibility: private", "visibility: normal"))
    assert checks(vault, {"SCOUT_READER": reader}) == [(SAM, "privacy-widening", True, None)]
    assert checks(vault) == []


@pytest.mark.parametrize(
    ("env", "run"),
    [
        ({}, False),
        ({"SCOUT_MODE": ""}, False),
        ({"SCOUT_READER": ""}, True),
        ({"SCOUT_READER": "owner "}, True),
        ({"SCOUT_READER": "owner"}, False),
        ({"SCOUT_MODE": "dreaming"}, True),
        ({"SCOUT_MODE": "dreaming", "SCOUT_READER": "owner"}, True),
        ({"SCOUT_READER": "autonomous"}, True),
        ({"SCOUT_READER": " Autonomous "}, True),
        ({"SCOUT_READER": "agent:person:priya@example.com"}, True),
        ({"SCOUT_READER": "garbage"}, True),
    ],
)
def test_is_scheduled(env: dict[str, str], run: bool) -> None:
    from scout.kb.lint import is_scheduled

    assert is_scheduled(env) is run


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


# ----- added lines stay cheap on long files ---------------------------------------------


def test_a_one_line_edit_in_a_30k_line_file_is_fast() -> None:
    import time

    from scout.kb.lint_privacy import added_marker_lines
    from scout.kb.lint_rules import added_line_numbers

    old_lines = [f"line {i} of the session log" for i in range(30_000)]
    new_lines = list(old_lines)
    new_lines[15_000] = "line 15000 edited %%privacy:allow sam%%"
    old, new = "\n".join(old_lines), "\n".join(new_lines)
    start = time.perf_counter()
    assert added_line_numbers(old, new) == {15_001}
    assert added_marker_lines(old, new) == [15_001]
    assert time.perf_counter() - start < 2.0


@pytest.mark.parametrize(
    ("old", "new"),
    [("a\nb\nc", "a\nx\nc"), ("a\nb", "a\nb\nc"), ("a\nb", "z\na\nb"), ("a\na\na", "a\na"), ("", "x"), ("x", "")],
)
def test_changed_blocks_matches_difflib(old: str, new: str) -> None:
    import difflib

    from scout.kb.lint_rules import changed_blocks

    a, b = old.split("\n"), new.split("\n")
    want = {
        j
        for t, _i1, _i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes()
        if t in ("insert", "replace")
        for j in range(j1, j2)
    }
    got = {j for t, _i1, _i2, j1, j2 in changed_blocks(a, b) if t in ("insert", "replace") for j in range(j1, j2)}
    assert len(got) == len(want)


# ----- edges: policy sources, scope, renames, the report ------------------------------


@pytest.mark.parametrize(
    ("head", "staged", "widens"),
    [
        ("none", "org", True),
        ("none", ("person:priya@example.com",), True),
        ("org", ("person:priya@example.com",), False),
        (("person:priya@example.com",), ("person:priya@example.com", "group:ops"), True),
        (("person:priya@example.com", "group:ops"), ("group:ops",), False),
        ("org", "org", False),
        (("group:ops",), "none", False),
    ],
)
def test_widens(head: object, staged: object, widens: bool) -> None:
    from scout.kb.lint_privacy import _widens

    assert _widens(head, staged) is widens  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("text", "warnings"),
    [
        (None, ()),
        ("", ()),
        ("- just\n- a list\n", ("privacy-overlay-unreadable",)),
        ("privacy: [unclosed\n", ("privacy-overlay-unreadable",)),
        ("privacy:\n  federation:\n    enabled: true\n", ()),
        ("privacy:\n  federation: true\n", ("privacy-overlay-invalid",)),
    ],
)
def test_policy_from_config_text(text: str | None, warnings: tuple[str, ...]) -> None:
    from scout.kb.lint_privacy import policy_from_config_text

    assert policy_from_config_text(text).warnings == warnings


def test_a_config_removed_from_the_index_falls_back_to_heads_policy(vault: Path) -> None:
    """The commit deletes scout-config.yaml from the index: HEAD's policy (trading/** local-only) still applies."""
    from scout.kb.lint_privacy import _context

    git(vault, "rm", "-q", "--cached", "scout-config.yaml")
    ctx = _context(vault, {})
    assert "trading/**" in ctx.policy.local_only
    assert ctx.policy == ctx.head_policy


def test_a_run_may_not_delete_the_config_with_its_privacy_block(vault: Path) -> None:
    git(vault, "rm", "-q", "--cached", "scout-config.yaml")
    assert checks(vault, RUN) == [("scout-config.yaml", "privacy-widening", True, None)]
    assert checks(vault) == []


def test_a_run_adding_a_new_config_with_a_privacy_block_is_blocked(tmp_path: Path) -> None:
    from tests.conftest import KbRepo

    repo = KbRepo(tmp_path / "v")
    (repo.root / "knowledge-base").mkdir(parents=True)
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.email", "t@example.com")
    repo.git("config", "user.name", "T")
    repo.stage("README.md", "vault\n")
    repo.git("-c", "commit.gpgsign=false", "commit", "-q", "-m", "init")
    repo.stage("scout-config.yaml", 'privacy:\n  shared_files_add: ["outbox/**"]\n')
    assert [(f.path, f.check) for f in privacy_staged(repo.root, env=RUN)] == [
        ("scout-config.yaml", "privacy-widening")
    ]


def test_a_run_rewriting_an_unparseable_config_is_refused(vault: Path) -> None:
    stage(vault, "scout-config.yaml", "privacy: [one\n")
    commit(vault, "broken config")
    stage(vault, "scout-config.yaml", "privacy: [two\n")
    with pytest.raises(Exception, match="doesn't parse"):
        privacy_staged(vault, env=RUN)  # the commit policy itself is unreadable: refused (minor f)
    assert ("scout-config.yaml", "privacy-overlay-unreadable", False, None) in checks(vault)


def test_unparseable_config_texts_compare_by_text() -> None:
    from scout.kb.lint_privacy import _config_data, _privacy_block

    assert _config_data(None) is None
    assert _privacy_block(_config_data("a: [x\n")) == _privacy_block(_config_data("a: [x\n"))
    assert _privacy_block(_config_data("a: [x\n")) != _privacy_block(_config_data("a: [y\n"))
    assert _privacy_block(_config_data("- a list\n")) != _privacy_block(_config_data("privacy: {}\n"))
    assert _privacy_block(_config_data("user: {name: Alex}\n")) is None


def test_a_staged_note_outside_the_scope_without_federation_scope_is_ignored(vault: Path) -> None:
    append(vault, "inbox.md", "- Ask Sammy about [[sam]].")
    assert checks(vault) == []
    assert checks(vault, RUN) == []


def test_a_pure_rename_of_a_draft_adds_no_lines(vault: Path) -> None:
    append(vault, DRAFT, "See [[sam]]. %%privacy:allow sam%%")
    append(vault, DRAFT, "Ask Sammy.")  # old, unmarked: stays as it is
    commit(vault)
    git(vault, "mv", DRAFT, "drafts/reply-renamed.md")
    assert checks(vault) == []
    assert checks(vault, RUN) == []


def test_splitting_a_marked_line_in_two_counts_as_adding_a_marker(vault: Path) -> None:
    from scout.kb.lint_privacy import added_marker_lines

    old = "A [[sam]] %%privacy:allow sam%%\nend"
    new = "A [[sam]] %%privacy:allow sam%%\nB [[sam]] %%privacy:allow sam%%\nend"
    assert added_marker_lines(old, new) == [2]
    # Both lines rewritten: each keeps a target the replaced line had, but the count grew.
    rewritten = "A2 [[sam]] %%privacy:allow sam%%\nB [[sam]] %%privacy:allow sam%%\nend"
    assert added_marker_lines(old, rewritten) == [1]


def test_a_run_may_delete_lines(vault: Path) -> None:
    from scout.kb.lint_rules import added_line_numbers

    assert added_line_numbers("a\nb\nc", "a\nc") == set()
    stage(vault, PRIYA, _read(vault, PRIYA).replace("Engineer at example-org", "", 1))
    assert checks(vault, RUN) == []


def test_report_ignores_a_stage_2_overlay_and_skips_missing_and_unreadable_files(vault: Path) -> None:
    import os

    with (vault / "scout-config.yaml").open("a", encoding="utf-8") as fh:
        fh.write("  federation:\n    enabled: true\n")
    (vault / "drafts" / "gone.md").write_text("x\n")
    git(vault, "add", "drafts/gone.md")
    (vault / "drafts" / "gone.md").unlink()  # tracked, not on disk
    locked = vault / "drafts" / "locked.md"
    locked.write_text("Ask Sammy.\n")
    locked.chmod(0)
    try:
        rep = privacy_report(vault)
    finally:
        locked.chmod(0o644)
    assert all(rel != "scout-config.yaml" for rel, _check, _sev in rep.problems)  # Stage 2 keys don't warn
    assert all(rel != "drafts/gone.md" for rel, _n in rep.unmarked)
    if os.geteuid() != 0:  # root reads a mode-000 file anyway
        assert ("drafts/locked.md", "file-unreadable", "error") in rep.problems
        assert all(rel != "drafts/locked.md" for rel, _n in rep.unmarked)


def test_uncommitted_paths_before_the_first_commit(tmp_path: Path) -> None:
    from scout.kb.git_io import uncommitted_paths

    repo = tmp_path / "r"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "a.md").write_text("a\n")
    git(repo, "add", "a.md")
    (repo / "b.md").write_text("b\n")  # untracked: not a version git knows
    assert uncommitted_paths(repo) == {"a.md"}


def test_the_config_is_always_in_the_privacy_scope() -> None:
    from scout.kb.lint_config import in_privacy_scope

    assert in_privacy_scope("scout-config.yaml", ())
    assert in_privacy_scope("knowledge-base/x.md", ())
    assert not in_privacy_scope("knowledge-base/x.txt", ())
    assert in_privacy_scope("outbox/x.txt", ("outbox/**",))
