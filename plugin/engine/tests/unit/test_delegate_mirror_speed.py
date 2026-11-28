"""The faster delegate sync builds the same mirror as before.

Three changes made the sync faster: one combined matcher in :mod:`scout.kb.outbound` (``_hits``), a memo key
worked out from what the scan reads rather than from every hidden file's bytes (:func:`scan_fingerprint`), and
text files scanned across cores (:func:`delegate_mirror._scan_texts`). None may change what a mirror holds:

- the matcher finds what the one before it found, on every file of a generated vault and on text written to
  trip it (accents, case, wikilinks, links, URL encoding, a name inside a longer word);
- a build with the matcher before it, scanning in this process, and a build with the new one scanning across
  processes give the same tree, the same redactions and the same files left out, on the fixture vault and on a
  generated one of several hundred files;
- an edit to a hidden note's body that changes none of its names reuses every memoised scan, and an alias or
  title change scans again.

``_reference_hits`` is the matcher as it was before (its ``_hits``, kept here as the oracle).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scout import delegate_mirror as dm
from scout import repo_sync as rs
from scout.kb import outbound
from scout.kb.outbound import PrivateTerms, clear_cache, find_mentions
from scout.scripts import privacy_migrate

from .delegate_big_vault import build_big_vault
from .privacy_vault_helpers import git, git_env

DELEGATES = ("grok-personal", "duet")
PROVENANCE = "knowledge-base/topics/from-priya.md"


def _reference_hits(text: str, terms: PrivateTerms) -> list[tuple[str, int, int]]:
    """``outbound._hits`` before the combined matcher: every note's pattern searched on its own."""
    (folded, origin), bare = outbound._folds(text)
    starts = [0] + [i + 1 for i, ch in enumerate(text) if ch == "\n"]
    found: list[tuple[str, int, int]] = []
    targets = [
        (outbound._line(starts, origin, m.start()), outbound._link_target(m.group(1)))
        for m in outbound._WIKILINK.finditer(folded)
    ]
    for n, target in targets:
        if target:
            found += [(note.path, n, n) for note in terms.notes if note.linked_by(target)]
    for form, where, pre, attr, prefix_attr in (
        (folded, origin, terms.any_term, "pattern", "prefixes"),
        (bare[0], bare[1], terms.any_bare, "bare_pattern", "bare_prefixes"),
    ):
        if pre is None or not pre.search(form):
            continue
        seen: dict[str, list[int]] = {}
        for note in terms.notes:
            pattern = getattr(note, attr)
            if pattern is not None:
                found += [
                    (
                        note.path,
                        outbound._line(starts, where, m.start()),
                        outbound._line(starts, where, m.end() - 1),
                    )
                    for m in outbound._matches(pattern, getattr(note, prefix_attr), form, seen)
                ]
    if terms.excerpts is not None:
        found += terms.excerpts.hits(text)
    return found


# ----- vaults -------------------------------------------------------------------------------------------------


def _vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, people: int, topics: int, daily: int, normal: int
) -> Path:
    """The fixture vault with both delegates, a shared-source note, and ``people`` … ``normal`` generated notes."""
    clear_cache()
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    for key, value in git_env().items():
        if key.startswith("GIT_"):
            monkeypatch.setenv(key, value)
    monkeypatch.delenv("SCOUT_MODE", raising=False)
    monkeypatch.delenv("SCOUT_READER", raising=False)
    v = build_big_vault(tmp_path / "v", people=people, topics=topics, daily=daily, normal=normal)
    (v / PROVENANCE).write_text(
        "---\ntitle: From Priya\nvisibility: normal\n"
        "source: shared:priya@example.com:knowledge-base/topics/plan.md\n---\n\n"
        "Notes from Priya's shared plan. CANARY-SHARED-9Q2W\n",
        encoding="utf-8",
    )
    _commit(v, PROVENANCE, None, "shared note")
    privacy_migrate.accept(v)
    _use(v, monkeypatch)
    return v


def _use(v: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCOUT_DATA_DIR", str(v))
    monkeypatch.setenv(rs.TEST_VAULT_ENV, str(v))


def _commit(vault: Path, rel: str, text: str | None, subject: str) -> None:
    if text is not None:
        (vault / rel).write_text(text, encoding="utf-8")
    git(vault, "add", "-A")
    git(vault, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", subject)


def _tricky_note(scan: dm.Scanner) -> str:
    """A normal note that names hidden notes every way the matcher has a rule for, and almost names them."""
    lines = ["---", "title: Tricky", "visibility: normal", "---", ""]
    forms = [t for n in scan.terms.notes for t in n.terms][:40]
    for i, form in enumerate(forms):
        lines += [
            f"Plain {form} here.",
            f"Upper {form.upper()} and lower {form.lower()}.",
            f"Inside a word: x{form}y and {form}s.",
            f"Accented {form.replace('a', 'á').replace('e', 'é')} too.",
            f"Encoded https://example.org/{form.replace(' ', '%20')} link.",
            f"Split across{form[: len(form) // 2]}\n{form[len(form) // 2 :]} lines.",
            f"Wikilink [[{form}|shown]] and [[{form}#Part]].",
            f"Line {i} names nothing at all.",
        ]
    for n in scan.terms.notes[:20]:
        if n.link_path:
            lines += [f"See [[{n.link_path}]] and [a link]({n.link_path}.md).", f"Path {n.link_path} itself."]
    return "\n".join(lines) + "\n"


def _with_tricky_note(v: Path, delegate: str = "grok-personal") -> None:
    blobs = dm.BlobReader(v)
    try:
        seen = dm.visibility(v, dm._head(v), delegate, blobs)
        scan = dm.hidden_terms(v, seen.entries, sorted(seen.hidden), blobs)
    finally:
        blobs.close()
    rel = "knowledge-base/topics/tricky.md"
    (v / rel).write_text(_tricky_note(scan), encoding="utf-8")
    _commit(v, rel, None, "tricky note")
    privacy_migrate.accept(v)


# ----- the matcher ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("delegate", DELEGATES)
def test_the_combined_matcher_finds_what_the_one_before_it_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, delegate: str
) -> None:
    v = _vault(tmp_path, monkeypatch, people=40, topics=30, daily=10, normal=150)
    _with_tricky_note(v, delegate)
    blobs = dm.BlobReader(v)
    try:
        seen = dm.visibility(v, dm._head(v), delegate, blobs)
        scan = dm.hidden_terms(v, seen.entries, sorted(seen.hidden), blobs)
        texts = [blobs.read(e.sha).decode("utf-8", "replace") for e, _ in seen.visible]
    finally:
        blobs.close()
    texts += [e.path for e, _ in seen.visible]
    assert scan.terms.notes, "the vault hides notes, so there is something to find"
    found = 0
    for text in texts:
        for allow in (False, True):
            new = outbound._hits(text, scan.terms)
            assert new == _reference_hits(text, scan.terms), text[:200]
            ours = find_mentions(text, scan.terms, allow_markers=allow)
            with monkeypatch.context() as m:
                m.setattr(outbound, "_hits", _reference_hits)
                assert find_mentions(text, scan.terms, allow_markers=allow) == ours, text[:200]
            found += bool(ours)
    # duet sees private notes, so it is hidden only from the few local-only ones.
    assert found >= (2 if delegate == "duet" else 20), (
        "enough of the texts name a hidden note for the comparison to mean something"
    )


# ----- whole builds -----------------------------------------------------------------------------------------


def _plan(v: Path, delegate: str) -> dict[str, Any]:
    """Everything :func:`plan` decided, in the order it decided it."""
    blobs = dm.BlobReader(v)
    try:
        p = dm.plan(v, dm._head(v), delegate, blobs)[0]
    finally:
        blobs.close()
    return {
        "files": [(f.path, f.mode, f.data, f.sha) for f in p.files],
        "labels": [repr(label) for label in p.labels],
        "redacted": list(p.redacted.items()),
        "left_out": p.left_out,
        "left_out_files": list(p.left_out_files.items()),
        "hidden_paths": sorted(p.hidden_paths),
        "withheld": p.withheld,
        "agent_files": p.agent_files,
    }


def _tree(bare: Path) -> list[str]:
    """``ls-tree -r main`` without the manifest, which carries the vault's own mark."""
    out = subprocess.run(["git", "-C", str(bare), "ls-tree", "-r", "main"], check=True, capture_output=True, text=True)
    return [line for line in out.stdout.splitlines() if not line.endswith("\t" + dm.MANIFEST_PATH)]


def _link(v: Path, delegate: str, tmp_path: Path, name: str) -> tuple[dict[str, Any], list[str]]:
    bare = tmp_path / f"{name}.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    result = dm.link(v, delegate, str(bare), account_unshared=True)
    kept = {k: v for k, v in result.to_json().items() if k not in ("pushed", "notes")}
    return kept, _tree(bare)


def _old_way(monkeypatch: pytest.MonkeyPatch) -> None:
    """Build as before: the reference matcher, every file scanned in this process."""
    monkeypatch.setattr(outbound, "_hits", _reference_hits)
    monkeypatch.setenv(dm.SCAN_WORKERS_ENV, "1")


def _new_way(monkeypatch: pytest.MonkeyPatch) -> None:
    """Build the new way, across two processes whatever the vault's size."""
    monkeypatch.setattr(dm, "PARALLEL_MIN_FILES", 1)
    monkeypatch.setenv(dm.SCAN_WORKERS_ENV, "2")


def _spy_pool(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """How many files each :func:`_scan_texts` call handed to other processes."""
    calls: list[int] = []
    real = dm._scan_texts

    def spy(scan: dm.Scanner, files: list[tuple[str, str]], **kw: Any) -> Any:
        got = real(scan, files, **kw)
        if got is not None:
            calls.append(len(files))
        return got

    monkeypatch.setattr(dm, "_scan_texts", spy)
    return calls


@pytest.mark.parametrize(
    "sizes",
    [
        pytest.param({"people": 0, "topics": 0, "daily": 0, "normal": 0}, id="fixture-vault"),
        pytest.param({"people": 40, "topics": 30, "daily": 20, "normal": 300}, id="generated-vault"),
    ],
)
def test_a_build_across_processes_is_the_build_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sizes: dict[str, int]
) -> None:
    old = _vault(tmp_path / "old", monkeypatch, **sizes)
    _with_tricky_note(old)
    new = tmp_path / "new" / "v"
    shutil.copytree(old, new, symlinks=True)
    git(new, "update-index", "-q", "--refresh")
    plans: dict[str, dict[str, Any]] = {}
    for delegate in DELEGATES:
        with monkeypatch.context() as m:
            _use(old, m)
            _old_way(m)
            clear_cache()
            old_plan = _plan(old, delegate)
            old_link = _link(old, delegate, tmp_path, f"old-{delegate}")
        with monkeypatch.context() as m:
            _use(new, m)
            _new_way(m)
            pool = _spy_pool(m)
            clear_cache()
            new_plan = _plan(new, delegate)
            new_link = _link(new, delegate, tmp_path, f"new-{delegate}")
        assert pool, "the new build scanned in other processes"
        assert new_plan == old_plan
        assert new_link == old_link
        plans[delegate] = old_plan
    grok = plans["grok-personal"]
    assert grok["redacted"], "lines were removed, so redaction was compared"
    assert grok["hidden_paths"], "files were withheld, so that was compared"


def test_a_pool_that_cannot_start_scans_here_with_the_same_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    v = _vault(tmp_path, monkeypatch, people=20, topics=20, daily=5, normal=60)
    monkeypatch.setenv(dm.SCAN_WORKERS_ENV, "1")
    before = _plan(v, "grok-personal")

    def broken(*_a: object, **_k: object) -> None:
        raise OSError("no processes here")

    import concurrent.futures

    monkeypatch.setattr(concurrent.futures, "ProcessPoolExecutor", broken)
    monkeypatch.setattr(dm, "PARALLEL_MIN_FILES", 1)
    monkeypatch.setenv(dm.SCAN_WORKERS_ENV, "4")
    clear_cache()
    assert _plan(v, "grok-personal") == before


@pytest.mark.parametrize(("raw", "want"), [("0", 1), ("1", 1), ("3", 3), (" 2 ", 2), ("lots", None), ("", None)])
def test_scan_workers_reads_the_setting_or_counts_cores(
    monkeypatch: pytest.MonkeyPatch, raw: str, want: int | None
) -> None:
    monkeypatch.setenv(dm.SCAN_WORKERS_ENV, raw)
    monkeypatch.setattr(dm.os, "cpu_count", lambda: 64)
    assert dm.scan_workers() == (dm.MAX_SCAN_WORKERS if want is None else want)


# ----- the memo key -----------------------------------------------------------------------------------------


def _fingerprint(v: Path, delegate: str = "grok-personal") -> str:
    blobs = dm.BlobReader(v)
    try:
        seen = dm.visibility(v, dm._head(v), delegate, blobs)
        scan = dm.hidden_terms(v, seen.entries, sorted(seen.hidden), blobs)
        return dm.scan_fingerprint(v, seen.policy, scan)
    finally:
        blobs.close()


def _count_scans(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The paths :func:`_mirror_file` scans from now on."""
    scanned: list[str] = []
    real = dm._mirror_file

    def counting(e: dm.TreeEntry, *a: Any, **k: Any) -> Any:
        scanned.append(e.path)
        return real(e, *a, **k)

    monkeypatch.setattr(dm, "_mirror_file", counting)
    return scanned


def _hidden(v: Path, folder: str) -> Path:
    return sorted((v / folder).glob("*.md"))[0]


@pytest.fixture
def linked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    v = _vault(tmp_path, monkeypatch, people=20, topics=20, daily=10, normal=80)
    bare = tmp_path / "m.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    dm.link(v, "grok-personal", str(bare), account_unshared=True)
    return v


def test_editing_a_hidden_notes_body_reuses_every_scan(linked: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    v = linked
    before = _fingerprint(v)
    edited: list[str] = []
    for folder in ("daily", "knowledge-base/people", "knowledge-base/topics/private"):
        note = _hidden(v, folder)
        edited.append(str(note.relative_to(v)))
        note.write_text(note.read_text(encoding="utf-8") + "\nOne more line, naming nobody new.\n", encoding="utf-8")
    git(v, "add", "--", *edited)  # only the notes: a sync may have changed .gitignore, a visible file
    git(v, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", "edit hidden bodies")
    assert _fingerprint(v) == before
    scanned = _count_scans(monkeypatch)
    result = dm.sync(v, "grok-personal")
    assert result.action == "sync" and scanned == []


@pytest.mark.parametrize(
    ("folder", "old", "new"),
    [
        ("knowledge-base/people", "aliases: [", "aliases: [Zorblax, "),
        ("knowledge-base/topics/private", "title: ", "title: Renamed "),
    ],
    ids=["alias", "title"],
)
def test_changing_a_hidden_notes_name_scans_again(
    linked: Path, monkeypatch: pytest.MonkeyPatch, folder: str, old: str, new: str
) -> None:
    v = linked
    before = _fingerprint(v)
    note = _hidden(v, folder)
    text = note.read_text(encoding="utf-8")
    assert old in text
    note.write_text(text.replace(old, new, 1), encoding="utf-8")
    _commit(v, "x", None, "rename a hidden note")
    assert _fingerprint(v) != before
    scanned = _count_scans(monkeypatch)
    dm.sync(v, "grok-personal")
    assert len(scanned) > 50, "every visible file was scanned again"


def test_a_new_alias_a_visible_note_already_names_is_redacted(linked: Path) -> None:
    v = linked
    visible = "knowledge-base/topics/t0/lunch-plans.md"
    (v / visible).parent.mkdir(parents=True, exist_ok=True)
    _commit(
        v, visible, "---\ntitle: Plans\nvisibility: normal\n---\n\nLunch with Zorblax on Friday.\nNothing else.\n", "x"
    )
    first = dm.sync(v, "grok-personal").redacted_lines
    note = _hidden(v, "knowledge-base/people")
    note.write_text(note.read_text(encoding="utf-8").replace("aliases: [", "aliases: [Zorblax, ", 1), encoding="utf-8")
    _commit(v, "x", None, "alias")
    result = dm.sync(v, "grok-personal")
    assert result.redacted_lines == first + 1


def test_check_tree_across_processes_still_refuses_a_line_that_names_a_hidden_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    v = _vault(tmp_path, monkeypatch, people=20, topics=20, daily=5, normal=80)
    _new_way(monkeypatch)
    pool = _spy_pool(monkeypatch)
    head = dm._head(v)
    blobs = dm.BlobReader(v)
    try:
        the_plan = dm.plan(v, head, "grok-personal", blobs)[0]
    finally:
        blobs.close()
    honest = git(v, "commit-tree", dm.write_tree(v, the_plan), "-m", "x").strip()
    dm.check_tree(v, honest, "grok-personal", head)  # the control: the honest build passes
    redacted = sorted(the_plan.redacted)[0]
    raw = (v / redacted).read_bytes()
    the_plan.files = [dm.MirrorFile(f.path, f.mode, raw, "") if f.path == redacted else f for f in the_plan.files]
    forged = git(v, "commit-tree", dm.write_tree(v, the_plan), "-m", "x").strip()
    with pytest.raises(rs.RepoSyncError) as e:
        dm.check_tree(v, forged, "grok-personal", head)
    assert e.value.code == "delegate_mirror_unsafe"
    assert len(pool) >= 3, "the build and both checks scanned in other processes"
