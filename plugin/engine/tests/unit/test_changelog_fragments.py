"""Changelog fragments (scout.scripts.changelog): parsing, assembly, and the released-section guard."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scout.scripts import changelog, release_notes, versioning

REAL_REPO = Path(__file__).resolve().parents[4]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --- parsing ---


def test_read_fragments_sorts_by_name_and_maps_sections(tmp_path):
    d = tmp_path / "changelog.d"
    _write(d, "README.md", "# not a fragment\n")
    _write(d, ".DS_Store", "junk")
    _write(d, "zz-last.fixed.md", "- z\n")
    _write(d, "aa-first.upgrade-notes.md", "\n- a\n  - nested\n\n")
    got = changelog.read_fragments(d)
    assert [(f.path.name, f.section, f.text) for f in got] == [
        ("aa-first.upgrade-notes.md", "Upgrade notes", "- a\n  - nested"),
        ("zz-last.fixed.md", "Fixed", "- z"),
    ]


def test_a_missing_directory_has_no_fragments(tmp_path):
    assert changelog.read_fragments(tmp_path / "nope") == []


@pytest.mark.parametrize(
    ("name", "text", "problem"),
    [
        ("x.fixes.md", "- a\n", "unknown section 'fixes'"),
        ("x.md", "- a\n", "not a fragment name"),
        ("My_Branch.fixed.md", "- a\n", "not a fragment name"),
        ("x.fixed.txt", "- a\n", "not a fragment name"),
        ("x.fixed.md", " \n\n", "empty"),
        ("x.fixed.md", "Fixed a thing\n", "must start with a list item"),
        ("x.fixed.md", "- a\n### Fixed\n- b\n", ":2: no headings in a fragment"),
    ],
)
def test_a_malformed_fragment_is_refused(tmp_path, name, text, problem):
    _write(tmp_path, name, text)
    with pytest.raises(changelog.FragmentError, match=problem):
        changelog.read_fragments(tmp_path)


def test_every_problem_is_reported_at_once(tmp_path):
    _write(tmp_path, "a.fixes.md", "- a\n")
    _write(tmp_path, "b.fixed.md", "")
    (tmp_path / "c.fixed.md").mkdir()
    with pytest.raises(changelog.FragmentError) as e:
        changelog.read_fragments(tmp_path)
    assert len(str(e.value).splitlines()) == 3


# --- assembly ---

_FRAGS = [
    changelog.Fragment(Path("a.added.md"), "Added", "- frag added"),
    changelog.Fragment(Path("b.fixed.md"), "Fixed", "- frag fixed\n  - detail"),
    changelog.Fragment(Path("c.upgrade-notes.md"), "Upgrade notes", "- upgrade first"),
]


def test_unreleased_entries_come_first_verbatim_then_fragments_by_heading():
    unreleased = (
        "\n\n### Fixed\n- old fix\n\n- old fix after a blank\n\n"
        "### Documentation\n- a doc note\n\n### Added\n- old add\n\n"
    )
    assert changelog.render_section(unreleased, _FRAGS) == (
        "### Upgrade notes\n- upgrade first\n\n"
        "### Added\n- old add\n- frag added\n\n"
        "### Fixed\n- old fix\n\n- old fix after a blank\n- frag fixed\n  - detail\n\n"
        "### Documentation\n- a doc note"
    )


def test_a_heading_unreleased_repeats_is_merged_and_unknown_headings_kept():
    unreleased = "Some preamble.\n\n### Changed\n- one\n\n### Notes\n- odd\n\n### Changed\n- two\n"
    assert changelog.render_section(unreleased, []) == (
        "Some preamble.\n\n### Changed\n- one\n- two\n\n### Notes\n- odd"
    )


def test_assemble_without_an_unreleased_heading_goes_above_the_first_release():
    text = "# Changelog\n\nProse.\n\n## [1.0.0] - 2026-01-01\n\n- old\n"
    out = changelog.assemble(text, _FRAGS[:1], version="1.1.0", date="2026-02-02")
    assert out == (
        "# Changelog\n\nProse.\n\n## [1.1.0] - 2026-02-02\n\n### Added\n- frag added\n\n"
        "## [1.0.0] - 2026-01-01\n\n- old\n"
    )


def test_assemble_with_nothing_writes_an_empty_section():
    out = changelog.assemble("# C\n\n## [Unreleased]\n", [], version="1.1.0", date="2026-02-02")
    assert out == "# C\n\n## [Unreleased]\n\n## [1.1.0] - 2026-02-02\n"


def test_promoting_the_real_changelogs_without_fragments_renders_unreleased_unchanged(tmp_path):
    """The release notes a release without fragments gets are exactly today's: [Unreleased], verbatim."""
    for rel in versioning._CHANGELOGS:
        _write(tmp_path, rel, (REAL_REPO / rel).read_text(encoding="utf-8"))
    old = {rel: (tmp_path / rel).read_text(encoding="utf-8") for rel in versioning._CHANGELOGS}
    changelog.assemble_changelogs(tmp_path, version="9.9.0", date="2026-11-18")
    for rel in versioning._CHANGELOGS:
        new = (tmp_path / rel).read_text(encoding="utf-8")
        assert release_notes.extract_section(new, "9.9.0") == release_notes.extract_section(old[rel], "Unreleased")
        assert changelog.versioned_sections(new).keys() - {"9.9.0"} == changelog.versioned_sections(old[rel]).keys()


def _two_changelogs(root: Path) -> None:
    for rel in changelog.CHANGELOGS:
        _write(root, rel, "# C\n\n## [Unreleased]\n\n### Added\n- left over\n\n## [1.0.0] - 2026-01-01\n\n- old\n")


def test_assemble_changelogs_writes_both_and_deletes_their_fragments(tmp_path):
    _two_changelogs(tmp_path)
    p = _write(tmp_path, "plugin/changelog.d/x.fixed.md", "- plugin fix\n")
    a = _write(tmp_path, "apps/macos/changelog.d/y.added.md", "- app add\n")
    _write(tmp_path, "apps/macos/changelog.d/README.md", "keep me\n")
    assert sorted(changelog.assemble_changelogs(tmp_path, version="1.1.0", date="2026-02-02")) == sorted([p, a])
    assert not p.exists() and not a.exists() and (tmp_path / "apps/macos/changelog.d/README.md").exists()
    plugin = (tmp_path / "plugin/CHANGELOG.md").read_text(encoding="utf-8")
    app = (tmp_path / "apps/macos/CHANGELOG.md").read_text(encoding="utf-8")
    assert release_notes.extract_section(plugin, "1.1.0") == "### Added\n- left over\n\n### Fixed\n- plugin fix"
    assert release_notes.extract_section(app, "1.1.0") == "### Added\n- left over\n- app add"


def test_one_bad_fragment_changes_neither_changelog(tmp_path):
    _two_changelogs(tmp_path)
    before = {rel: (tmp_path / rel).read_text(encoding="utf-8") for rel in changelog.CHANGELOGS}
    good = _write(tmp_path, "plugin/changelog.d/x.fixed.md", "- fine\n")
    _write(tmp_path, "apps/macos/changelog.d/y.nope.md", "- bad\n")
    with pytest.raises(changelog.FragmentError, match="unknown section 'nope'"):
        changelog.assemble_changelogs(tmp_path, version="1.1.0", date="2026-02-02")
    assert {rel: (tmp_path / rel).read_text(encoding="utf-8") for rel in changelog.CHANGELOGS} == before
    assert good.exists()


def test_assemble_changelogs_refuses_a_version_already_there(tmp_path):
    _two_changelogs(tmp_path)
    with pytest.raises(ValueError, match=r"already has a \[1.0.0\] section"):
        changelog.assemble_changelogs(tmp_path, version="1.0.0", date="2026-02-02")


def test_versioning_promote_reports_a_bad_fragment_without_a_traceback(tmp_path, capsys):
    _two_changelogs(tmp_path)
    _write(tmp_path, "plugin/changelog.d/x.fixd.md", "- typo\n")
    assert versioning.main(["--repo-root", str(tmp_path), "promote", "1.1.0", "2026-02-02"]) == 1
    assert "unknown section 'fixd'" in capsys.readouterr().err


# --- check: fragments parse, released sections stay put ---


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _two_changelogs(repo)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def _commit(repo: Path, rel: str, text: str, msg: str = "change") -> None:
    _write(repo, rel, text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)


def test_check_passes_fragments_unreleased_additions_and_a_new_release(tmp_path):
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    _commit(repo, "plugin/changelog.d/x.fixed.md", "- fix\n")
    log = repo / "apps/macos/CHANGELOG.md"
    _commit(repo, "apps/macos/CHANGELOG.md", log.read_text().replace("- left over\n", "- left over\n- in flight\n"))
    changelog.assemble_changelogs(repo, version="1.1.0", date="2026-02-02")  # what a release PR adds
    assert changelog.check(repo, base) == []
    assert changelog.main(["--repo-root", str(repo), "check", "--base", base]) == 0


def test_check_refuses_an_entry_filed_under_a_released_section(tmp_path):
    """The silent-merge trap: a PR's line landed under a release main already has."""
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    log = repo / "plugin/CHANGELOG.md"
    _commit(repo, "plugin/CHANGELOG.md", log.read_text().replace("- old\n", "- old\n- my entry\n"))
    problems = changelog.check(repo, base)
    assert "plugin/CHANGELOG.md: the released [1.0.0] section changed" in problems
    assert any("move it to a fragment" in p for p in problems)
    assert changelog.main(["--repo-root", str(repo), "check", "--base", base]) == 1


def test_check_refuses_a_dropped_released_section(tmp_path):
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    log = repo / "apps/macos/CHANGELOG.md"
    _commit(repo, "apps/macos/CHANGELOG.md", log.read_text().split("## [1.0.0]")[0])
    assert "apps/macos/CHANGELOG.md: the released [1.0.0] section is gone" in changelog.check(repo, base)


def _two_releases(repo: Path) -> str:
    """Add a [1.1.0] above [1.0.0] in the plugin changelog and commit it as the base; returns its sha."""
    log = repo / "plugin/CHANGELOG.md"
    _commit(
        repo,
        "plugin/CHANGELOG.md",
        log.read_text().replace("## [1.0.0]", "## [1.1.0] - 2026-02-02\n\n- new\n\n## [1.0.0]"),
    )
    return _git(repo, "rev-parse", "HEAD")


def test_a_trailer_naming_the_version_allows_a_fix_to_that_section_only(tmp_path):
    repo = _repo(tmp_path)
    base = _two_releases(repo)
    log = repo / "plugin/CHANGELOG.md"
    msg = f"docs: fix a typo in 1.0.0\n\nThe entry named the wrong file.\n\n{changelog.TRAILER} 1.0.0 wrong file\n"
    _commit(repo, "plugin/CHANGELOG.md", log.read_text().replace("- old\n", "- older\n"), msg=msg)
    assert changelog.trailer_versions(repo, base) == {"1.0.0"}
    assert changelog.check(repo, base) == []
    _commit(repo, "plugin/CHANGELOG.md", log.read_text().replace("- new\n", "- newer\n"))  # not covered by it
    assert "plugin/CHANGELOG.md: the released [1.1.0] section changed" in changelog.check(repo, base)


def test_a_line_that_only_looks_like_a_trailer_allows_nothing(tmp_path):
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    log = repo / "plugin/CHANGELOG.md"
    for msg in (
        # Mid-message, wrapped prose: not in the closing trailer block, so git doesn't parse it as one.
        f"docs: edit\n\nA sentence wraps so the next line starts\n{changelog.TRAILER} 1.0.0 by accident.\n\nMore.\n",
        f"docs: edit\n\n{changelog.TRAILER} because I said so\n",  # a real trailer, but it names no version
    ):
        _commit(repo, "plugin/CHANGELOG.md", log.read_text() + "x\n", msg=msg)
        assert changelog.trailer_versions(repo, base) == set(), msg
        assert "plugin/CHANGELOG.md: the released [1.0.0] section changed" in changelog.check(repo, base), msg


def test_a_pr_into_its_release_branch_may_edit_that_section_only(tmp_path):
    """Fixes on release/vX.Y.Z (#674, dc2a3349) add to [X.Y.Z], which prepare wrote and finalize hasn't shipped."""
    repo = _repo(tmp_path)
    base = _two_releases(repo)
    log = repo / "plugin/CHANGELOG.md"
    _commit(repo, "plugin/CHANGELOG.md", log.read_text().replace("- new\n", "- new\n- a release-branch fix\n"))
    assert changelog.check(repo, base, "release/v1.1.0") == []
    assert changelog.check(repo, base, "refs/heads/release/v1.1.0") == []  # merge_group's base_ref
    assert "plugin/CHANGELOG.md: the released [1.1.0] section changed" in changelog.check(repo, base, "main")
    _commit(repo, "plugin/CHANGELOG.md", log.read_text().replace("- old\n", "- older\n"))
    assert (
        changelog.check(repo, base, "release/v1.1.0")[0] == "plugin/CHANGELOG.md: the released [1.0.0] section changed"
    )


def test_a_fragment_on_a_release_branch_is_refused(tmp_path):
    """It would survive rc and finalize, ship in X.Y.Z, and be listed under the next release."""
    repo = _repo(tmp_path)
    base = _two_releases(repo)
    _commit(repo, "plugin/changelog.d/late.fixed.md", "- too late\n")
    problems = changelog.check(repo, base, "release/v1.1.0")
    assert len(problems) == 1 and "late.fixed.md: a fragment on release/v1.1.0 is never assembled" in problems[0]
    assert changelog.check(repo, base, "main") == []


def test_changing_a_fragment_main_has_is_refused_and_deleting_one_is_not(tmp_path):
    """Two branches whose names end the same, or a reused branch name, would overwrite another entry."""
    repo = _repo(tmp_path)
    _commit(repo, "plugin/changelog.d/fix-x.fixed.md", "- theirs\n")
    _commit(repo, "plugin/changelog.d/README.md", "how to\n")
    base = _git(repo, "rev-parse", "HEAD")
    _commit(repo, "plugin/changelog.d/README.md", "how to, better\n")  # the README is docs, not an entry
    assert changelog.check(repo, base) == []
    _commit(repo, "plugin/changelog.d/fix-x.fixed.md", "- mine\n")
    problems = changelog.check(repo, base)
    assert len(problems) == 1 and problems[0].startswith("plugin/changelog.d/fix-x.fixed.md: changes a fragment")
    _git(repo, "rm", "-q", "plugin/changelog.d/fix-x.fixed.md")  # a revert, or the release PR, deletes it
    _git(repo, "commit", "-q", "-m", "revert")
    assert changelog.check(repo, base) == []


def test_the_release_pr_may_delete_fragments_main_has(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, "apps/macos/changelog.d/y.added.md", "- app add\n")
    base = _git(repo, "rev-parse", "HEAD")
    changelog.assemble_changelogs(repo, version="1.1.0", date="2026-02-02")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "release: v1.1.0")
    assert changelog.check(repo, base, "main") == []


def test_check_reports_bad_fragments_and_a_base_it_cannot_read(tmp_path):
    repo = _repo(tmp_path)
    _write(repo, "apps/macos/changelog.d/x.oops.md", "- a\n")
    assert any("unknown section 'oops'" in p for p in changelog.check(repo))
    no_base = changelog.check(repo, "0" * 40)  # a new branch's push: no base, so only the fragments
    assert len(no_base) == 1 and "unknown section 'oops'" in no_base[0]
    assert "is not a commit in this clone" in changelog.check(repo, "f" * 40)[-1]


def test_the_committed_fragments_parse():
    """What contract checks on every PR: this checkout's own fragments."""
    for frag_dir in changelog.CHANGELOGS.values():
        changelog.read_fragments(REAL_REPO / frag_dir)
        assert (REAL_REPO / frag_dir / "README.md").is_file()  # keeps the directory once a release empties it
