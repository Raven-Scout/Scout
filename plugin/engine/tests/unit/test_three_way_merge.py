"""Unit tests for engine/scout/scripts/three_way_merge.py."""

from __future__ import annotations

import pytest

from scout.scripts.three_way_merge import MergeResult, three_way_merge


def test_clean_merge_no_conflict():
    base = "alpha\nbeta\ngamma\n"
    ours = "alpha\nbeta\ngamma\ndelta\n"  # plugin added a line at end
    theirs = "alpha\nBETA\ngamma\n"  # vault edited middle line
    result = three_way_merge(base=base, ours=ours, theirs=theirs)
    assert isinstance(result, MergeResult)
    assert result.conflicts is False
    # Both sides' changes should appear.
    assert "BETA" in result.content
    assert "delta" in result.content


def test_conflicting_change_returns_markers():
    base = "alpha\nbeta\ngamma\n"
    ours = "alpha\nBETA-OURS\ngamma\n"  # plugin changed line 2
    theirs = "alpha\nBETA-THEIRS\ngamma\n"  # vault changed line 2 differently
    result = three_way_merge(base=base, ours=ours, theirs=theirs)
    assert result.conflicts is True
    assert "<<<<<<<" in result.content
    assert "=======" in result.content
    assert ">>>>>>>" in result.content
    assert "BETA-OURS" in result.content
    assert "BETA-THEIRS" in result.content
    assert "|||||||" in result.content  # diff3-style base block


def test_identical_inputs_no_change():
    text = "alpha\nbeta\n"
    result = three_way_merge(base=text, ours=text, theirs=text)
    assert result.conflicts is False
    assert result.content == text


def test_empty_inputs():
    result = three_way_merge(base="", ours="", theirs="")
    assert result.conflicts is False
    assert result.content == ""


def test_one_side_deletes_content():
    """Vault keeps content; plugin (ours) removes it. No conflict expected."""
    base = "alpha\nbeta\ngamma\n"
    ours = "alpha\ngamma\n"  # plugin removed beta
    theirs = "alpha\nbeta\ngamma\n"  # vault unchanged
    result = three_way_merge(base=base, ours=ours, theirs=theirs)
    assert result.conflicts is False
    assert "beta" not in result.content


def test_merge_raises_on_git_timeout(monkeypatch):
    """A hung `git merge-file` must not block bootstrap forever (#47)."""
    import subprocess as _subprocess

    def fake_run(argv, **kwargs):
        assert kwargs.get("timeout") == 30
        raise _subprocess.TimeoutExpired(cmd=argv, timeout=30)

    monkeypatch.setattr("scout.scripts.three_way_merge.subprocess.run", fake_run)
    with pytest.raises(RuntimeError, match="timed out"):
        three_way_merge(base="b\n", ours="a\n", theirs="c\n")


def test_conflict_markers_carry_the_given_labels():
    """Labels name the sides in a draft a person resolves by hand; without them
    git prints the temp-file paths."""
    result = three_way_merge(
        base="x\n", ours="plugin side\n", theirs="vault side\n", labels=("plugin", "base", "vault")
    )
    assert result.conflicts is True
    lines = result.content.splitlines()
    assert "<<<<<<< plugin" in lines
    assert "||||||| base" in lines
    assert ">>>>>>> vault" in lines


def test_final_newline_only_difference_does_not_conflict():
    """base/ours lack a trailing "\\n"; theirs has one (e.g. an editor added it
    on save). ours also appends a line, theirs edits a mid-file line — real,
    non-overlapping changes that must merge cleanly despite the newline-only
    mismatch between the three sides."""
    base = "alpha\nbeta\ngamma"  # no trailing newline
    ours = "alpha\nbeta\ngamma\ndelta\n"  # appended a line; now ends with \n
    theirs = "alpha\nBETA\ngamma\n"  # mid-file edit; ends with \n though base didn't
    result = three_way_merge(base=base, ours=ours, theirs=theirs)
    assert result.conflicts is False
    assert "BETA" in result.content
    assert "delta" in result.content


def test_final_newline_only_difference_does_not_conflict_reverse_direction():
    """Same as above with the newline on the opposite side: base/theirs end
    without a trailing "\\n" this time, ours does."""
    base = "alpha\nbeta\ngamma\n"
    ours = "alpha\nbeta\ngamma\ndelta\n"  # appended a line; keeps the trailing \n
    theirs = "alpha\nBETA\ngamma"  # mid-file edit; drops the trailing \n
    result = three_way_merge(base=base, ours=ours, theirs=theirs)
    assert result.conflicts is False
    assert "BETA" in result.content
    assert "delta" in result.content


def test_a_missing_git_is_reported_as_merge_unavailable(monkeypatch):
    from scout.scripts.three_way_merge import MergeUnavailable

    def no_git(argv, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "git")

    monkeypatch.setattr("scout.scripts.three_way_merge.subprocess.run", no_git)
    with pytest.raises(MergeUnavailable, match="git"):
        three_way_merge(base="b\n", ours="a\n", theirs="c\n")
