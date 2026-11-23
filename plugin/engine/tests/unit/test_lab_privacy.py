"""The lab's privacy rules (review of #761, items 2-4).

- The lab reads ``visibility`` with the privacy layer's own parser: ``Private``, ``secret`` or any value but
  ``normal`` is private, and so is frontmatter that doesn't parse; an absent key is ``normal``.
- An experiment's folder (scripts, outputs, run logs) is private by path; the note beside it keeps its own label.
- ``lab show --json`` and ``lab list`` leave out, for a reader who isn't full, every experiment whose note that
  reader may not see.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout import cli
from scout.errors import LabError
from scout.kb import privacy
from scout.kb.read import visibility_map
from scout.lab import experiments, ops, paths, runner, summary, writer
from scout.lab.lint import index_findings, lab_findings
from scout.lab.parser import parse_experiment, parse_front_page
from scout.sandbox import broker

FIXTURES = Path(__file__).parent.parent / "fixtures" / "lab"
SLUG = "summary-drift"
LAB = "knowledge-base/lab"
FRONT = f"{LAB}/{SLUG}/{SLUG}.md"
TIMELINE = f"{LAB}/{SLUG}/{SLUG}-timeline.md"
E1 = f"{LAB}/{SLUG}/experiments/{SLUG}-E1-baseline.md"
E2 = f"{LAB}/{SLUG}/experiments/{SLUG}-E2-section-hash.md"
E2_DATA = "sampled pages 2026-10-01..2026-11-20"  # only E2's note says this (its frontmatter `data:`)
NOW = dt.datetime(2026, 11, 21, 13, 30, tzinfo=dt.UTC)
PRIVATE_SPELLINGS = ["Private", "PRIVATE", " private ", "secret", "hidden", "''", "null", "[private]", "1"]
cli_runner = CliRunner()


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    shutil.copytree(FIXTURES / SLUG, paths.thread_dir(vault, SLUG))
    return vault


def _set_visibility(vault: Path, rel: str, value: str) -> None:
    path = vault / rel
    text = path.read_text(encoding="utf-8")
    assert "visibility: normal" in text, rel
    path.write_text(text.replace("visibility: normal", f"visibility: {value}", 1), encoding="utf-8")


def _front_with(value: str) -> str:
    return (
        (FIXTURES / SLUG / f"{SLUG}.md")
        .read_text(encoding="utf-8")
        .replace("visibility: normal", f"visibility: {value}")
    )


# --- 1. visibility is read with the privacy layer's parser -----------------------------------------------------


@pytest.mark.parametrize("value", PRIVATE_SPELLINGS)
def test_any_visibility_but_normal_is_private_on_a_front_page_and_an_experiment(value: str) -> None:
    assert parse_front_page(SLUG, _front_with(value)).visibility == "private"
    note = (FIXTURES / SLUG / "experiments" / f"{SLUG}-E2-section-hash.md").read_text(encoding="utf-8")
    assert parse_experiment(SLUG, note.replace("visibility: normal", f"visibility: {value}")).visibility == "private"


def test_normal_absent_and_broken_visibility() -> None:
    assert parse_front_page(SLUG, _front_with("normal")).visibility == "normal"
    assert parse_front_page(SLUG, _front_with("Normal")).visibility == "normal"  # the privacy parser's own reading
    absent = _front_with("normal").replace("visibility: normal\n", "")
    assert parse_front_page(SLUG, absent).visibility == "normal"  # as the privacy layer: absent is normal
    assert parse_front_page(SLUG, _front_with("[unclosed")).visibility == "private"  # unparseable: fail closed
    assert parse_front_page(SLUG, "# no frontmatter at all\n").visibility == "normal"
    e1 = (FIXTURES / SLUG / "experiments" / f"{SLUG}-E1-baseline.md").read_text(encoding="utf-8")
    assert "visibility" not in e1 and parse_experiment(SLUG, e1).visibility == "normal"


def test_the_lab_parser_agrees_with_the_labels() -> None:
    for value in [*PRIVATE_SPELLINGS, "normal", "Normal"]:
        text = _front_with(value)
        label = privacy.classify(FRONT, text, privacy.load_policy(None))
        assert parse_front_page(SLUG, text).visibility == label.visibility, value


def test_private_spelled_otherwise_leaves_the_thread_out_of_lab_md(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _set_visibility(vault, FRONT, "Private")
    ops.write_index(vault)
    index = paths.index_path(vault).read_text(encoding="utf-8")
    assert f"[[{SLUG}]]" not in index and "Keeping cached summaries" not in index
    assert "_1 private thread(s) not listed._" in index
    # and lint agrees: the thread counts as private, so lab.md must not list it and must count it
    assert index_findings(_Source(vault), index) == []
    listed = index.replace("## Active\n", f"## Active\n- [[{SLUG}]] — hi\n")
    assert any("links private thread" in f.message for f in index_findings(_Source(vault), listed))


class _Source:
    """The working tree, as lab lint reads it."""

    def __init__(self, vault: Path) -> None:
        self.vault = vault

    def files(self) -> list[str]:
        return sorted(p.relative_to(self.vault).as_posix() for p in self.vault.rglob("*") if p.is_file())

    def read(self, rel: str) -> str | None:
        try:
            return (self.vault / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None


def test_lint_demands_private_on_the_markdown_of_a_thread_spelled_private(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _set_visibility(vault, FRONT, "Private")
    for rel in (TIMELINE, E1, E2):
        found = lab_findings(vault, rel, (vault / rel).read_text(encoding="utf-8"))
        assert any(f.blocking and "must say 'visibility: private'" in f.message for f in found), rel
    # A file that says private in another spelling is private to the labels, so it passes.
    e2 = (vault / E2).read_text(encoding="utf-8").replace("visibility: normal", "visibility: PRIVATE")
    assert lab_findings(vault, E2, e2) == []
    [f] = lab_findings(vault, f"{LAB}/{SLUG}/experiments/{SLUG}-E2-section-hash/run.py", "print(1)\n")
    assert f.blocking and "non-markdown" in f.message


def test_plan_refuses_a_thread_spelled_private(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _set_visibility(vault, FRONT, "Private")
    body = "## Hypothesis\nh\n\n## Method\nm\n\n## Data\nd\n"
    with pytest.raises(LabError, match="private thread"):
        experiments.plan(vault, SLUG, "x", "T", [], body, now=NOW)


@pytest.mark.parametrize("value", ["Private", "secret"])
def test_the_broker_counts_other_spellings_as_private(tmp_path: Path, value: str) -> None:
    vault = _vault(tmp_path)
    front, note = vault / FRONT, vault / E2
    normal_front, normal_note = front.read_text("utf-8"), note.read_text("utf-8")
    assert broker._private_at(SLUG, front, note, normal_front, normal_note) is None
    flipped = normal_front.replace("visibility: normal", f"visibility: {value}")
    assert broker._private_at(SLUG, front, note, flipped, normal_note) is not None  # the front page at HEAD
    note.write_text(normal_note.replace("visibility: normal", f"visibility: {value}"), "utf-8")
    assert broker._private_at(SLUG, front, note, normal_front, normal_note) is not None  # the worktree note


def test_the_runner_counts_other_spellings_as_private(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _set_visibility(vault, E2, "Private")
    note = vault / E2
    note.write_text(note.read_text("utf-8").replace("status: planned", "status: running"), "utf-8")
    assert runner._running_note(vault, SLUG, "E2") == (note, True)


def test_the_writer_writes_and_reads_visibility_as_the_privacy_layer_does() -> None:
    page = writer.new_front_page(SLUG, "T", "Q?", [], [], "2026-11-21", "Private")
    assert "\nvisibility: private\n" in page and parse_front_page(SLUG, page).visibility == "private"
    assert "\nvisibility: private\n" in writer.new_front_page(SLUG, "T", "Q?", [], [], "2026-11-21", "secret")
    assert writer.new_timeline(SLUG, "T", "PRIVATE").startswith("---\nvisibility: private\n---\n")
    assert writer.new_timeline(SLUG, "T", "normal") == "# T — timeline\n"


# --- 2. an experiment's folder is private by path --------------------------------------------------------------


def test_the_shipped_policy_labels_experiment_folder_files_private() -> None:
    policy = privacy.load_policy(None)
    folder = f"{LAB}/x/experiments/x-E1-a"
    for rel in (f"{folder}/out.csv", f"{folder}/run-20261121-0111.log", f"{folder}/deep/nested/run.py"):
        label = privacy.classify(rel, None, policy)
        assert (label.visibility, label.visibility_source) == ("private", "forced"), rel
    # A markdown output with no visibility of its own is private too.
    assert privacy.classify(f"{folder}/notes.md", "# Notes\n", policy).visibility == "private"
    # The note beside the folder keeps its own label.
    note = privacy.classify(f"{LAB}/x/experiments/x-E1-a.md", "---\nvisibility: normal\n---\n", policy)
    assert (note.visibility, note.visibility_source) == ("normal", "frontmatter")
    assert privacy.classify(f"{LAB}/x/experiments/x-E1-a.md", "---\nid: E1\n---\n", policy).visibility == "normal"
    assert privacy.classify(f"{LAB}/x/x.md", "---\nvisibility: normal\n---\n", policy).visibility == "normal"


NORMAL_FM = "---\nvisibility: normal\n---\nCANARY-COPIED-TRANSCRIPT\n"


@pytest.mark.parametrize("name", ["out.md", "out.csv", "out.txt", "run-1.log", "deep/out.md"])
def test_an_experiment_output_cant_label_itself_normal(name: str) -> None:
    """A sandboxed script writes these files: frontmatter saying normal doesn't make one normal (forced)."""
    rel = f"{LAB}/x/experiments/x-E1-a/{name}"
    label = privacy.classify(rel, NORMAL_FM, privacy.load_policy(None))
    assert (label.visibility, label.visibility_source) == ("private", "forced")
    assert label.federation_scope == "none"


@pytest.mark.parametrize(
    "overlay",
    [
        '  visibility_defaults:\n    - {glob: "knowledge-base/lab/**", visibility: normal}\n',
        "  visibility_forced_private: []\n",
        '  visibility_forced_private:\n    - "nothing/**"\n',
    ],
)
def test_an_owner_overlay_cant_narrow_the_forced_paths(tmp_path: Path, overlay: str) -> None:
    (tmp_path / "scout-config.yaml").write_text(f"schema_version: 1\nprivacy:\n{overlay}", encoding="utf-8")
    policy = privacy.load_policy(tmp_path)
    assert policy.visibility_forced_private == privacy.load_policy(None).visibility_forced_private
    for text in (None, NORMAL_FM):
        label = privacy.classify(f"{LAB}/x/experiments/x-E1-a/out.md", text, policy)
        assert (label.visibility, label.visibility_source) == ("private", "forced")
    note = privacy.classify(f"{LAB}/x/experiments/x-E1-a.md", "---\nvisibility: normal\n---\n", policy)
    assert note.visibility == "normal"


def test_label_file_on_disk(tmp_path: Path) -> None:
    folder = tmp_path / LAB / "x" / "experiments" / "x-E1-a"
    folder.mkdir(parents=True)
    (folder / "out.csv").write_text("a,b\n", "utf-8")
    (folder.parent / "x-E1-a.md").write_text("---\nvisibility: normal\n---\n# E1\n", "utf-8")
    (folder / "out.md").write_text(NORMAL_FM, "utf-8")
    policy = privacy.load_policy(tmp_path)
    assert privacy.label_file(tmp_path, f"{LAB}/x/experiments/x-E1-a/out.csv", policy).visibility == "private"
    forced = privacy.label_file(tmp_path, f"{LAB}/x/experiments/x-E1-a/out.md", policy)
    assert (forced.visibility, forced.visibility_source) == ("private", "forced")
    assert privacy.label_file(tmp_path, f"{LAB}/x/experiments/x-E1-a.md", policy).visibility == "normal"


DELEGATE = "grok-personal"


def _with_delegate(vault: Path) -> Path:
    (vault / "scout-config.yaml").write_text(
        f"schema_version: 1\nprivacy:\n  delegates:\n    {DELEGATE}: {{}}\n", encoding="utf-8"
    )
    return vault


def _experiment_files(vault: Path) -> list[str]:
    folder = vault / LAB / SLUG / "experiments" / f"{SLUG}-E2-section-hash"
    folder.mkdir()
    for name in ("run.py", "out.csv", "run-20261121-1330.log", "results.md"):
        (folder / name).write_text("CANARY-EXPERIMENT-OUTPUT\n", "utf-8")
    for name in ("claims-normal.md", "claims-normal.txt"):  # a script's output that labels itself normal
        (folder / name).write_text(NORMAL_FM, "utf-8")
    return sorted(p.relative_to(vault).as_posix() for p in folder.iterdir())


@pytest.mark.parametrize("reader", [f"delegate:{DELEGATE}", "elsewhere"])
def test_a_delegate_or_elsewhere_reader_sees_the_note_but_not_its_folder(tmp_path: Path, reader: str) -> None:
    vault = _with_delegate(_vault(tmp_path))
    inside = _experiment_files(vault)
    seen = visibility_map(vault, reader, ["knowledge-base/**"])
    assert seen.get(E2) == "normal" and seen.get(FRONT) == "normal"
    assert [rel for rel in inside if rel in seen] == []
    owner = visibility_map(vault, "owner", ["knowledge-base/**"])
    assert {owner[rel] for rel in inside} == {"private"}  # the owner still sees them, labeled private


# --- 3. lab show / lab list filter experiments by their note's label --------------------------------------------


def _private_e2(tmp_path: Path) -> Path:
    vault = _with_delegate(_vault(tmp_path))
    _set_visibility(vault, E2, "private")
    return vault


@pytest.mark.parametrize("reader", [f"delegate:{DELEGATE}", "elsewhere"])
def test_show_leaves_out_an_experiment_whose_note_the_reader_cant_see(tmp_path: Path, reader: str) -> None:
    vault = _private_e2(tmp_path)
    labels = visibility_map(vault, reader, ["knowledge-base/**"])
    assert FRONT in labels and E2 not in labels
    doc = summary.lab_thread(vault, SLUG, labels=labels, full=False)
    assert [e["eid"] for e in doc["experiments"]] == ["E1"]
    assert doc["experiments"][0]["frontmatter"]["id"] == "E1"
    assert doc["private_experiments"] == 1
    text = json.dumps(doc)
    assert E2_DATA not in text and "Section-hash staleness check" not in text  # E2's frontmatter and line title
    # The owner comment on Q3 still counts (it is the front page's); none on E2's own line is counted.
    assert doc["unanswered_comments"] == 1


def test_show_hides_an_experiment_whose_note_is_missing_from_the_labels(tmp_path: Path) -> None:
    """A note the reader may not see for any reason (not only its own `visibility`) is left out: fail closed."""
    vault = _vault(tmp_path)
    doc = summary.lab_thread(vault, SLUG, labels={FRONT: "normal", E1: "normal"}, full=False)
    assert [e["eid"] for e in doc["experiments"]] == ["E1"] and doc["private_experiments"] == 1
    doc = summary.lab_thread(vault, SLUG, labels={FRONT: "normal", E1: "normal", E2: "private"}, full=False)
    assert [e["eid"] for e in doc["experiments"]] == ["E1"]


def test_a_full_reader_still_gets_every_experiment(tmp_path: Path) -> None:
    vault = _private_e2(tmp_path)
    doc = summary.lab_thread(vault, SLUG, labels=visibility_map(vault, "owner", ["knowledge-base/**"]), full=True)
    assert [e["eid"] for e in doc["experiments"]] == ["E2", "E1"] and doc["private_experiments"] == 0
    e2 = next(e for e in doc["experiments"] if e["eid"] == "E2")
    assert e2["frontmatter"]["visibility"] == "private" and e2["frontmatter"]["questions"] == ["Q3"]
    assert E2_DATA in json.dumps(doc)


def test_list_leaves_the_hidden_experiment_out_of_its_id_lists(tmp_path: Path) -> None:
    vault = _private_e2(tmp_path)
    labels = visibility_map(vault, "elsewhere", ["knowledge-base/**"])
    [row] = summary.lab_list(vault, labels, full=False, now=NOW)["threads"]
    assert row["experiments"] == {"planned": [], "running": [], "due": [], "held": []}
    [row] = summary.lab_list(vault, None, full=True, now=NOW)["threads"]
    assert row["experiments"]["due"] == ["E2"] and row["experiments"]["held"] == ["E2"]


@pytest.fixture
def cli_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    vault = _private_e2(tmp_path)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(vault))
    monkeypatch.delenv("SCOUT_READER", raising=False)
    monkeypatch.delenv("SCOUT_MODE", raising=False)
    return vault


def test_show_json_through_the_cli(cli_vault: Path) -> None:
    owner = cli_runner.invoke(cli.app, ["lab", "show", SLUG, "--json"])
    assert owner.exit_code == 0, owner.output
    assert E2_DATA in owner.stdout and json.loads(owner.stdout)["private_experiments"] == 0
    for reader in ("elsewhere", f"delegate:{DELEGATE}"):
        shown = cli_runner.invoke(cli.app, ["lab", "show", SLUG, "--json", "--reader", reader])
        assert shown.exit_code == 0, shown.output
        doc = json.loads(shown.stdout)
        assert [e["eid"] for e in doc["experiments"]] == ["E1"] and doc["private_experiments"] == 1, reader
        assert E2_DATA not in shown.stdout, reader
