"""The governance-pending folder reader and the owner's decision rules."""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scout.changes.governance import (
    PENDING_DIR,
    _parse_text,
    approved_application,
    decision,
    decision_ref,
    diff_paths,
    filed_by,
    read_pending,
    spent_at,
    undo_requested,
)
from scout.changes.models import Signal

GOOD = """---
summary: Let the briefing skip a carried red item
why: The same red item was carried for six days with no reply.
targets:
  - briefing
---

Some prose about it.

```diff
--- a/SKILL.md
+++ b/SKILL.md
@@ -1 +1 @@
-old
+new
```
"""


def _write(vault: Path, name: str, text: str) -> None:
    folder = vault / PENDING_DIR
    folder.mkdir(exist_ok=True)
    (folder / name).write_text(text, encoding="utf-8")


def test_read_pending_parses_a_good_file_and_counts_a_bad_one(tmp_path):
    _write(tmp_path, "skip-red.md", GOOD)
    _write(tmp_path, "broken.md", "no frontmatter at all\n")
    items, bad = read_pending(tmp_path)
    assert bad == 1
    [item] = items
    assert item.id == "gov-skip-red" and item.slug == "skip-red"
    assert item.summary == "Let the briefing skip a carried red item"
    assert item.targets == ("briefing",) and item.path == "governance-pending/skip-red.md"
    assert "+new" in item.diff


def test_applied_folder_is_not_pending(tmp_path):
    _write(tmp_path, "skip-red.md", GOOD)
    applied = tmp_path / PENDING_DIR / "applied"
    applied.mkdir()
    (applied / "old.md").write_text(GOOD)
    items, bad = read_pending(tmp_path)
    assert [i.id for i in items] == ["gov-skip-red"] and bad == 0


def test_missing_folder_is_no_files(tmp_path):
    assert read_pending(tmp_path) == ([], 0)


@pytest.mark.parametrize(
    "text",
    [
        GOOD.replace("summary: Let the briefing skip a carried red item", "summary: " + "x" * 121),
        GOOD.replace("why: The same", "why2: The same"),
        GOOD.replace("```diff", "```text"),
        GOOD + "\n```diff\n--- a/X\n+++ b/X\n```\n",  # two diff blocks
        GOOD.replace("--- a/SKILL.md\n+++ b/SKILL.md", "--- a/../outside.md\n+++ b/../outside.md"),
        GOOD.replace("--- a/SKILL.md\n+++ b/SKILL.md", "--- /etc/passwd\n+++ /etc/passwd"),
        GOOD.replace("targets:\n  - briefing", "targets: briefing"),
        GOOD.replace("--- a/SKILL.md\n+++ b/SKILL.md", "--- a/ok dir/../../etc/x\n+++ b/SKILL.md"),
        GOOD.replace("--- a/SKILL.md\n+++ b/SKILL.md", '--- "a/b c/../x"\n+++ b/SKILL.md'),
        GOOD.replace("--- a/SKILL.md\n+++ b/SKILL.md", "diff --git a/x y b/x y\n--- a/SKILL.md\n+++ b/SKILL.md"),
        GOOD.replace(
            "--- a/SKILL.md\n+++ b/SKILL.md", "copy from SKILL.md\ncopy to ../x\n--- a/SKILL.md\n+++ b/SKILL.md"
        ),
        GOOD.replace("--- a/SKILL.md\n+++ b/SKILL.md", "--- ../x\n+++ SKILL.md"),
        GOOD.replace("--- a/SKILL.md\n+++ b/SKILL.md", "diff --git a/../x b/SKILL.md\n--- a/SKILL.md\n+++ b/SKILL.md"),
        GOOD.replace(
            "--- a/SKILL.md\n+++ b/SKILL.md\n@@ -1 +1 @@\n-old\n+new\n", "@@ -1 +1 @@\n-old\n+new\n"
        ),  # no headers
        "---\n: : :\n---\n```diff\n```\n",
    ],
)
def test_files_that_do_not_fit_are_counted(tmp_path, text):
    _write(tmp_path, "x.md", text)
    assert read_pending(tmp_path) == ([], 1)


def test_a_removed_line_that_reads_like_a_header_is_content(tmp_path):
    body = "@@ -1,2 +1,2 @@\n-- foo\n-old\n+new\n+++ bar\n"
    _write(tmp_path, "x.md", GOOD.replace("@@ -1 +1 @@\n-old\n+new\n", body))
    assert [i.id for i in read_pending(tmp_path)[0]] == ["gov-x"]
    _write(
        tmp_path,
        "y.md",
        GOOD.replace("--- a/SKILL.md\n+++ b/SKILL.md", "--- a/SKILL.md\t2026-01-01 10:00\n+++ b/SKILL.md"),
    )
    assert [i.id for i in read_pending(tmp_path)[0]] == ["gov-x", "gov-y"]


def test_the_context_is_the_prose_around_the_diff_and_is_cut(tmp_path):
    """I3: the file's own explanation, shown beside the diff; it is not part of what the owner's decision binds."""
    from scout.changes.governance import CONTEXT_MAX

    _write(tmp_path, "a.md", GOOD)
    [item] = read_pending(tmp_path)[0]
    assert item.context == "Some prose about it."
    _write(tmp_path, "a.md", GOOD.replace("Some prose about it.", "y" * (CONTEXT_MAX + 50)))
    [long] = read_pending(tmp_path)[0]
    assert len(long.context) == CONTEXT_MAX and long.context.endswith("…") and long.hash == item.hash
    _write(tmp_path, "a.md", GOOD.replace("Some prose about it.\n", ""))
    assert read_pending(tmp_path)[0][0].context == ""


def test_bad_slug_is_counted(tmp_path):
    _write(tmp_path, "Bad Slug.md", GOOD)
    assert read_pending(tmp_path) == ([], 1)


def test_targets_are_optional(tmp_path):
    _write(tmp_path, "t.md", GOOD.replace("targets:\n  - briefing\n", ""))
    [item] = read_pending(tmp_path)[0]
    assert item.targets == ()


def _sig(ref: str, minutes: int, kind: str = "approval") -> Signal:
    return Signal(
        ts=datetime(2026, 11, 16, 9, 0, tzinfo=UTC) + timedelta(minutes=minutes), kind=kind, surface="scoutctl", ref=ref
    )


H = "0123456789abcdef"


def test_newest_decision_wins():
    sigs = [_sig(f"approve:gov-a@{H}", 0), _sig(f"reject:gov-a@{H}", 5)]
    assert decision(sigs, "gov-a", H) == "rejected"
    assert decision([*sigs, _sig(f"approve:gov-a@{H}", 9)], "gov-a", H) == "approved"
    assert decision(sigs, "gov-b", H) == "awaiting-ok"
    assert decision([_sig(f"approve:gov-a@{H}", 0, kind="dm-reply")], "gov-a", H) == "awaiting-ok"
    # Rows in any order: the newest stands, an older one read after it doesn't.
    assert decision([_sig(f"reject:gov-a@{H}", 5), _sig(f"approve:gov-a@{H}", 0)], "gov-a", H) == "rejected"


def test_a_spent_decision_counts_for_nothing():
    """#747 review: a decision made at or before the file was last acted on is used up."""
    sigs = [_sig(f"approve:gov-a@{H}", 0)]
    spent = datetime(2026, 11, 16, 9, 1, tzinfo=UTC)
    assert decision(sigs, "gov-a", H, spent=spent) == "awaiting-ok"
    assert decision([*sigs, _sig(f"approve:gov-a@{H}", 2)], "gov-a", H, spent=spent) == "approved"


def test_an_unbound_decision_in_the_same_second_as_its_use_is_spent():
    """Git keeps whole seconds: an approval at 09:00:00.7 applied by a commit at 09:00:00 is used up."""
    row = Signal(
        ts=datetime(2026, 11, 16, 9, 0, 0, 700_000, tzinfo=UTC),
        kind="approval",
        surface="app",
        ref=f"approve:gov-a@{H}",
    )
    assert decision([row], "gov-a", H, spent=datetime(2026, 11, 16, 9, 0, 0, tzinfo=UTC)) == "awaiting-ok"
    assert decision([row], "gov-a", H, spent=datetime(2026, 11, 16, 8, 59, 59, tzinfo=UTC)) == "approved"


def test_a_bound_decision_counts_only_for_its_filing_whatever_the_clocks_say():
    """#747 review: a decision names the commit that filed the file; a later filing (a new commit) waits for a new
    one, however the commit dates were set."""
    first, second = "a" * 40, "b" * 40
    assert decision_ref("approve", "gov-a", H, first) == f"approve:gov-a@{H}@{first}"
    rows = [_sig(decision_ref("approve", "gov-a", H, first), 0)]
    assert decision(rows, "gov-a", H, filed=first) == "approved"
    assert decision(rows, "gov-a", H, filed=second) == "awaiting-ok"
    assert decision(rows, "gov-a", H) == "awaiting-ok"  # not committed as it is now: no filing to match
    rows.append(_sig(decision_ref("reject", "gov-a", H, second), 1))
    assert decision(rows, "gov-a", H, filed=second) == "rejected"
    # A bound row ignores `spent`: commit order already said which filing it was for.
    spent = datetime(2026, 11, 17, tzinfo=UTC)
    assert decision(rows[:1], "gov-a", H, filed=first, spent=spent) == "approved"


def test_a_decision_counts_only_for_the_diff_it_was_made_on():
    """I3: a row for another hash (the diff changed after the decision) or with none (the old ref form) is nothing."""
    assert decision([_sig("approve:gov-a", 0)], "gov-a", H) == "awaiting-ok"
    assert decision([_sig("approve:gov-a@ffffffffffffffff", 0)], "gov-a", H) == "awaiting-ok"
    newer_diff = [_sig(f"approve:gov-a@{H}", 0), _sig("reject:gov-a@ffffffffffffffff", 5)]
    assert decision(newer_diff, "gov-a", H) == "approved"
    assert decision_ref("approve", "gov-a", H) == f"approve:gov-a@{H}"


def test_the_hash_is_the_diff_blocks_and_changes_with_it(tmp_path):
    _write(tmp_path, "skip-red.md", GOOD)
    [item] = read_pending(tmp_path)[0]
    assert len(item.hash) == 64
    _write(tmp_path, "skip-red.md", GOOD.replace("+new", "+newer"))
    assert read_pending(tmp_path)[0][0].hash != item.hash
    # Prose outside the diff block doesn't change what was approved.
    _write(tmp_path, "skip-red.md", GOOD.replace("Some prose about it.", "Other prose."))
    assert read_pending(tmp_path)[0][0].hash == item.hash


def test_governance_flag_defaults_true_and_must_be_a_bool(tmp_path):
    _write(tmp_path, "a.md", GOOD)
    _write(tmp_path, "b.md", GOOD.replace("targets:", "governance: false\ntargets:"))
    _write(tmp_path, "c.md", GOOD.replace("targets:", "governance: maybe\ntargets:"))
    items, bad = read_pending(tmp_path)
    assert [(i.id, i.governance) for i in items] == [("gov-a", True), ("gov-b", False)] and bad == 1


def test_diff_paths_reads_every_header_side():
    """m1: both sides of a rename come from the headers (git apply --numstat prints only the new one)."""
    rename = (
        "diff --git a/knowledge-base/a.md b/knowledge-base/b.md\nsimilarity index 100%\n"
        "rename from knowledge-base/a.md\nrename to knowledge-base/b.md\n"
    )
    assert diff_paths(rename) == ["knowledge-base/a.md", "knowledge-base/b.md"]  # a pure rename
    both = rename + "--- a/knowledge-base/a.md\n+++ b/knowledge-base/b.md\n@@ -1 +1 @@\n-x\n+y\n"
    assert diff_paths(both) == ["knowledge-base/a.md", "knowledge-base/b.md"]
    new_file = "--- /dev/null\n+++ b/scripts/new.py\n@@ -0,0 +1 @@\n+print()\n"
    assert diff_paths(new_file) == ["scripts/new.py"]
    # git apply strips one folder (-p1): an unprefixed ---/+++ name would patch another path, so it is refused.
    assert diff_paths("--- scripts/x.py\n+++ scripts/x.py\n@@ -1 +1 @@\n-a\n+b\n") is None


@pytest.mark.parametrize(
    "body",
    [
        "@@ -1,2 +1 @@\n-old\n+new\n",  # the old side ends one line early
        "@@ -1 +1,2 @@\n-old\n+new\n",  # the new side ends one line early
        "@@ -1 +1 @@\n-old\n-older\n+new\n",  # one removed line too many
        "@@ -1 +1 @@\n-old\n?what\n",  # a line no hunk can hold
        "@@ junk @@\n-old\n+new\n",  # an @@ line that isn't a hunk header
    ],
)
def test_hunk_counts_that_do_not_add_up_are_unreadable(tmp_path, body):
    _write(tmp_path, "x.md", GOOD.replace("@@ -1 +1 @@\n-old\n+new\n", body))
    assert read_pending(tmp_path) == ([], 1)


def test_no_newline_markers_frontmatter_shapes_and_empty_targets(tmp_path):
    marker = "\\ No newline at end of file\n"
    no_newline = GOOD.replace("-old\n+new\n", f"-old\n{marker}+new\n{marker}")
    _write(tmp_path, "a.md", no_newline)
    _write(tmp_path, "b.md", GOOD.replace("targets:\n  - briefing\n", "targets:\n"))
    _write(tmp_path, "c.md", "---\n- a list\n- not a mapping\n---\n" + GOOD.split("---\n", 2)[2])
    items, bad = read_pending(tmp_path)
    assert [(i.id, i.targets) for i in items] == [("gov-a", ("briefing",)), ("gov-b", ())] and bad == 1


def test_undo_requested():
    assert undo_requested([_sig("undo:chg-0123456789ab", 0)], "chg-0123456789ab")
    assert not undo_requested([_sig("approve:chg-0123456789ab", 0)], "chg-0123456789ab")


# --- approved_application: is a pending→applied move one the owner approved? -----------------------------------------

AT = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)


def _git(repo: Path, *args: str, at: datetime | None = None) -> str:
    env = dict(os.environ)
    if at is not None:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = at.isoformat()
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True, env=env)
    return out.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "vault"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.name", "Scout Test")
    _git(r, "config", "user.email", "scout@example.invalid")
    _git(r, "config", "commit.gpgsign", "false")
    (r / "SKILL.md").write_text("old\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "seed", at=AT - timedelta(days=9))
    return r


def _file(repo: Path, slug: str, text: str, at: datetime) -> str:
    """Commit ``governance-pending/<slug>.md``; the filing commit's sha."""
    _write(repo, f"{slug}.md", text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"dreaming [22:00]: file {slug}", at=at)
    return _git(repo, "rev-parse", "HEAD")


def _apply_diff(repo: Path, slug: str) -> None:
    """Patch the working tree with the pending file's diff, as ``scoutctl changes apply`` does (a file that doesn't
    parse has no diff to apply), and stage what it patched."""
    try:
        item = _parse_text((repo / PENDING_DIR / f"{slug}.md").read_text(encoding="utf-8"), slug)
    except Exception:  # noqa: BLE001 - malformed YAML: nothing to apply
        item = None
    if item is None:
        return
    subprocess.run(["git", "-C", str(repo), "apply"], input=item.diff, text=True, check=True, capture_output=True)
    _git(repo, "add", "-A", "--", *item.paths)


def _move(repo: Path, *slugs: str, at: datetime, to: str = "applied", patch: bool = True) -> str:
    """Commit the move of each pending file into ``to`` (``applied/`` by default) with its diff applied (unless
    ``patch`` is false); the commit's sha."""
    (repo / PENDING_DIR / to).mkdir(exist_ok=True)
    for slug in slugs:
        if patch:
            _apply_diff(repo, slug)
        _git(repo, "mv", f"{PENDING_DIR}/{slug}.md", f"{PENDING_DIR}/{to}/{slug}.md")
    _git(repo, "commit", "-q", "-m", "dreaming [22:40]: apply", at=at)
    return _git(repo, "rev-parse", "HEAD")


def _hash_of(text: str) -> str:
    item = _parse_text(text, "x")
    assert item is not None
    return item.hash


def _row(verb: str, slug: str, digest: str, ts: datetime, filed: str | None = None) -> Signal:
    return Signal(ts=ts, kind="approval", surface="app", ref=decision_ref(verb, f"gov-{slug}", digest, filed))


def test_a_bound_approval_makes_the_move_approved_only_for_its_filing(repo):
    filing = _file(repo, "a", GOOD, AT - timedelta(hours=2))
    sha = _move(repo, "a", at=AT)
    h = _hash_of(GOOD)
    assert approved_application(repo, [], sha) is False
    # Bound rows count whatever their time says: commit order already ties them to the filing.
    assert approved_application(repo, [_row("approve", "a", h, AT + timedelta(days=1), filing)], sha) is True
    assert approved_application(repo, [_row("approve", "a", h, AT, "f" * 40)], sha) is False
    assert approved_application(repo, [_row("approve", "b", h, AT, filing)], sha) is False
    assert approved_application(repo, [_row("approve", "a", "0" * 64, AT, filing)], sha) is False


def test_an_unbound_approval_counts_only_up_to_the_apply_commit(repo):
    _file(repo, "a", GOOD, AT - timedelta(hours=2))
    sha = _move(repo, "a", at=AT)
    h = _hash_of(GOOD)
    assert approved_application(repo, [_row("approve", "a", h, AT - timedelta(hours=1))], sha) is True
    # The same second as the commit (git keeps no finer time) is before it; a later one isn't.
    assert approved_application(repo, [_row("approve", "a", h, AT + timedelta(microseconds=700_000))], sha) is True
    assert approved_application(repo, [_row("approve", "a", h, AT + timedelta(seconds=1))], sha) is False
    # A newer rejection before the apply wins; one after it is not counted.
    rows = [_row("approve", "a", h, AT - timedelta(hours=1)), _row("reject", "a", h, AT - timedelta(minutes=1))]
    assert approved_application(repo, rows, sha) is False
    rows = [_row("approve", "a", h, AT - timedelta(hours=1)), _row("reject", "a", h, AT + timedelta(minutes=1))]
    assert approved_application(repo, rows, sha) is True


def test_an_approval_used_by_an_earlier_application_is_spent(repo):
    """Applied, reverted by hand, filed again and moved again by a run: the first unbound approval is used up."""
    _file(repo, "a", GOOD, AT - timedelta(hours=5))
    first = _move(repo, "a", at=AT - timedelta(hours=4))
    _git(repo, "rm", "-q", f"{PENDING_DIR}/applied/a.md")
    (repo / "SKILL.md").write_text("old\n")
    _git(repo, "add", "SKILL.md")
    _git(repo, "commit", "-q", "-m", "scout: revert by hand", at=AT - timedelta(hours=3))
    _file(repo, "a", GOOD, AT - timedelta(hours=2))
    second = _move(repo, "a", at=AT)
    rows = [_row("approve", "a", _hash_of(GOOD), AT - timedelta(hours=4, minutes=30))]
    assert approved_application(repo, rows, first) is True
    assert approved_application(repo, rows, second) is False


def test_a_merge_that_brings_the_pending_file_back_needs_a_new_approval(repo):
    """Review fix: a merge that restores the pending file from a side branch is TREESAME to that branch for the path,
    so git's default history simplification follows the side branch, finds the original filing and misses the
    earlier application. Read along first parents (as name_status diffs), the merge is the new filing and the earlier
    move spends the old approval, for a bound and an unbound one alike."""
    filing = _file(repo, "a", GOOD, AT - timedelta(hours=5))
    _git(repo, "branch", "side")
    first = _move(repo, "a", at=AT - timedelta(hours=4))
    _git(repo, "checkout", "-q", "side")
    (repo / "README.md").write_text("side\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "side work", at=AT - timedelta(hours=3, minutes=30))
    _git(repo, "checkout", "-q", "-")
    _git(repo, "merge", "-q", "--no-ff", "--no-commit", "side", at=AT - timedelta(hours=3))
    _git(repo, "checkout", "side", "--", f"{PENDING_DIR}/a.md")
    _git(repo, "rm", "-q", f"{PENDING_DIR}/applied/a.md")
    _git(repo, "checkout", "side", "--", "SKILL.md")  # the merge takes the side branch's unpatched brain back too
    _git(repo, "commit", "-q", "-m", "merge side", at=AT - timedelta(hours=3))
    second = _move(repo, "a", at=AT)
    h = _hash_of(GOOD)
    for rows in (
        [_row("approve", "a", h, AT - timedelta(hours=4, minutes=30), filing)],
        [_row("approve", "a", h, AT - timedelta(hours=4, minutes=30))],
    ):
        assert approved_application(repo, rows, first) is True
        assert approved_application(repo, rows, second) is False
    # HEAD's lookups read the same way: the pending file at HEAD~1 (the merge) was filed by the merge itself.
    _git(repo, "reset", "-q", "--hard", "HEAD~1")
    [item] = read_pending(repo)[0]
    assert filed_by(repo, item) == _git(repo, "rev-parse", "HEAD")
    assert spent_at(repo, item) == AT - timedelta(hours=4)


_OTHER = GOOD.replace(
    "--- a/SKILL.md\n+++ b/SKILL.md\n@@ -1 +1 @@\n-old\n+new\n",
    "--- /dev/null\n+++ b/DREAMING.md\n@@ -0,0 +1 @@\n+other\n",
)


def test_every_move_in_the_commit_needs_its_own_approval(repo):
    other = _OTHER
    fa = _file(repo, "a", GOOD, AT - timedelta(hours=3))
    fb = _file(repo, "b", other, AT - timedelta(hours=2))
    sha = _move(repo, "a", "b", at=AT)
    a = _row("approve", "a", _hash_of(GOOD), AT, fa)
    assert approved_application(repo, [a], sha) is False
    assert approved_application(repo, [a, _row("approve", "b", _hash_of(other), AT, fb)], sha) is True


_FALLBACK = GOOD.replace("targets:", "governance: false\ntargets:")


@pytest.mark.parametrize(
    "text",
    [
        "---\n: [\n---\n",  # malformed YAML
        "no frontmatter\n",
        _FALLBACK,  # a fallback item is deleted on apply, never moved: moved, it is no governance change
    ],
)
def test_an_applied_file_that_is_not_a_governance_item_is_never_approved(repo, text):
    filing = _file(repo, "a", text, AT - timedelta(hours=2))
    sha = _move(repo, "a", at=AT)
    assert approved_application(repo, [_row("approve", "a", _hash_of(_FALLBACK), AT, filing)], sha) is False


def test_a_commit_that_moves_nothing_into_applied_is_not_an_approved_application(repo, monkeypatch):
    from scout.changes import scan

    assert approved_application(repo, [], "not-a-sha") is False
    assert approved_application(repo, [], _git(repo, "rev-parse", "HEAD")) is False  # the seed: nothing moved
    _file(repo, "a", GOOD, AT - timedelta(hours=1))
    assert approved_application(repo, [], _move(repo, "a", at=AT, to="elsewhere", patch=False)) is False
    # git can't say when the apply commit was made: not approved.
    filing = _file(repo, "b", GOOD, AT)
    sha = _move(repo, "b", at=AT + timedelta(hours=1))
    rows = [_row("approve", "b", _hash_of(GOOD), AT, filing)]
    assert approved_application(repo, rows, sha) is True
    real = scan._git

    def no_time(vault, *args):
        return "not a date\n" if "--format=%cI" in args and "-1" in args else real(vault, *args)

    monkeypatch.setattr(scan, "_git", no_time)
    assert approved_application(repo, rows, sha) is False
    monkeypatch.setattr(scan, "_git", lambda vault, *args: None if "-1" in args else real(vault, *args))
    assert approved_application(repo, rows, sha) is False


# --- I1: the apply commit holds the approved diff and nothing else ----------------------------------------------------


def _approved_rows(slug: str, text: str, filing: str) -> list[Signal]:
    return [_row("approve", slug, _hash_of(text), AT, filing)]


def test_the_exact_approved_diff_is_an_approved_application(repo):
    filing = _file(repo, "a", GOOD, AT - timedelta(hours=1))
    sha = _move(repo, "a", at=AT)
    assert (repo / "SKILL.md").read_text() == "new\n"
    assert approved_application(repo, _approved_rows("a", GOOD, filing), sha) is True


def test_an_extra_file_in_the_apply_commit_is_not_approved(repo):
    """A whole-file commit sweeps in an earlier, unapproved brain edit: the move must not make it immune."""
    filing = _file(repo, "a", GOOD, AT - timedelta(hours=1))
    (repo / "DREAMING.md").write_text("an untested edit\n")
    _git(repo, "add", "DREAMING.md")
    sha = _move(repo, "a", at=AT)
    assert approved_application(repo, _approved_rows("a", GOOD, filing), sha) is False


def test_an_extra_hunk_in_an_approved_file_is_not_approved(repo):
    (repo / "SKILL.md").write_text("old\n" + "keep\n" * 8 + "tail\n")
    _git(repo, "add", "SKILL.md")
    _git(repo, "commit", "-q", "-m", "seed more", at=AT - timedelta(days=2))
    text = GOOD.replace("@@ -1 +1 @@\n-old\n+new\n", "@@ -1,2 +1,2 @@\n-old\n+new\n keep\n")  # with context
    filing = _file(repo, "a", text, AT - timedelta(hours=1))
    _apply_diff(repo, "a")
    (repo / "SKILL.md").write_text((repo / "SKILL.md").read_text().replace("tail\n", "tail, edited by a run\n"))
    _git(repo, "add", "SKILL.md")
    sha = _move(repo, "a", at=AT, patch=False)
    assert approved_application(repo, _approved_rows("a", text, filing), sha) is False
    # The same diff, alone, is approved.
    _git(repo, "reset", "-q", "--hard", "HEAD~1")
    sha = _move(repo, "a", at=AT)
    assert approved_application(repo, _approved_rows("a", text, filing), sha) is True


def test_a_move_without_its_diff_or_with_another_is_not_approved(repo):
    filing = _file(repo, "a", GOOD, AT - timedelta(hours=1))
    unpatched = _move(repo, "a", at=AT, patch=False)
    assert approved_application(repo, _approved_rows("a", GOOD, filing), unpatched) is False
    _git(repo, "reset", "-q", "--hard", "HEAD~1")
    (repo / "SKILL.md").write_text("something else\n")
    _git(repo, "add", "SKILL.md")
    other = _move(repo, "a", at=AT, patch=False)
    assert approved_application(repo, _approved_rows("a", GOOD, filing), other) is False


def test_two_items_in_one_commit_each_bring_their_own_paths(repo):
    fa = _file(repo, "a", GOOD, AT - timedelta(hours=3))
    fb = _file(repo, "b", _OTHER, AT - timedelta(hours=2))
    sha = _move(repo, "a", "b", at=AT)
    rows = [*_approved_rows("a", GOOD, fa), *_approved_rows("b", _OTHER, fb)]
    assert approved_application(repo, rows, sha) is True


def test_an_apply_commit_git_cannot_replay_is_not_approved(repo, monkeypatch):
    """Fail closed: the parent's files, the commit's files or the replayed patch unreadable."""
    from scout.changes import scan

    filing = _file(repo, "a", GOOD, AT - timedelta(hours=1))
    sha = _move(repo, "a", at=AT)
    rows = _approved_rows("a", GOOD, filing)
    real = scan._git_raw

    for needle in ("ls-tree", "cat-file", "apply"):

        def broken(vault, args, stdin=None, *, outside_repo=False, needle=needle):
            return None if needle in args else real(vault, args, stdin, outside_repo=outside_repo)

        monkeypatch.setattr(scan, "_git_raw", broken)
        assert approved_application(repo, rows, sha) is False, needle
    monkeypatch.setattr(scan, "_git_raw", real)
    assert approved_application(repo, rows, sha) is True


def test_a_symlink_in_an_approved_path_is_not_approved(repo):
    link = GOOD.replace(
        "--- a/SKILL.md\n+++ b/SKILL.md\n@@ -1 +1 @@\n-old\n+new\n",
        "diff --git a/scripts/run.sh b/scripts/run.sh\nnew file mode 120000\n--- /dev/null\n+++ b/scripts/run.sh\n"
        "@@ -0,0 +1 @@\n+elsewhere\n\\ No newline at end of file\n",
    )
    filing = _file(repo, "a", link, AT - timedelta(hours=1))
    sha = _move(repo, "a", at=AT)
    assert (repo / "scripts" / "run.sh").is_symlink()
    assert approved_application(repo, _approved_rows("a", link, filing), sha) is False
    # A plain file with the link's text in its place: the replayed diff makes a link, which is never a match.
    _git(repo, "reset", "-q", "--hard", "HEAD~1")
    (repo / "scripts").mkdir(exist_ok=True)
    (repo / "scripts" / "run.sh").write_text("elsewhere")
    _git(repo, "add", "scripts/run.sh")
    sha = _move(repo, "a", at=AT, patch=False)
    assert approved_application(repo, _approved_rows("a", link, filing), sha) is False


def test_a_diff_that_deletes_a_file_is_approved_when_the_commit_deletes_it(repo):
    (repo / "scripts").mkdir()
    (repo / "scripts" / "old.sh").write_text("old\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed script", at=AT - timedelta(days=2))
    gone = GOOD.replace(
        "--- a/SKILL.md\n+++ b/SKILL.md\n@@ -1 +1 @@\n-old\n+new\n",
        "diff --git a/scripts/old.sh b/scripts/old.sh\ndeleted file mode 100644\n--- a/scripts/old.sh\n+++ /dev/null\n"
        "@@ -1 +0,0 @@\n-old\n",
    )
    filing = _file(repo, "a", gone, AT - timedelta(hours=1))
    sha = _move(repo, "a", at=AT)
    assert not (repo / "scripts" / "old.sh").exists()
    assert approved_application(repo, _approved_rows("a", gone, filing), sha) is True


def test_an_executable_bit_the_diff_never_set_is_not_approved(repo):
    (repo / "scripts").mkdir()
    (repo / "scripts" / "x.sh").write_text("old\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed script", at=AT - timedelta(days=2))
    text = GOOD.replace("a/SKILL.md", "a/scripts/x.sh").replace("b/SKILL.md", "b/scripts/x.sh")
    filing = _file(repo, "a", text, AT - timedelta(hours=1))
    _apply_diff(repo, "a")
    os.chmod(repo / "scripts" / "x.sh", 0o755)
    _git(repo, "add", "scripts/x.sh")
    sha = _move(repo, "a", at=AT, patch=False)
    assert approved_application(repo, _approved_rows("a", text, filing), sha) is False
    # A diff that does set the bit is approved.
    _git(repo, "reset", "-q", "--hard", "HEAD~1")
    _git(repo, "rm", "-q", f"{PENDING_DIR}/a.md")
    _git(repo, "commit", "-q", "-m", "drop a", at=AT - timedelta(minutes=40))
    mode = text.replace(
        "--- a/scripts/x.sh",
        "diff --git a/scripts/x.sh b/scripts/x.sh\nold mode 100644\nnew mode 100755\n--- a/scripts/x.sh",
    )
    filing = _file(repo, "b", mode, AT - timedelta(minutes=30))
    sha = _move(repo, "b", at=AT)
    assert os.access(repo / "scripts" / "x.sh", os.X_OK)
    assert approved_application(repo, _approved_rows("b", mode, filing), sha) is True
