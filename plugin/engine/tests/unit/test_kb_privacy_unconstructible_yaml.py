"""A YAML value PyYAML can't construct fails closed, like malformed YAML.

``yaml.safe_load`` scans ``date: 2026-13-45`` fine, then raises a bare
``ValueError`` from ``datetime.date``. Other values raise ``KeyError``,
``IndexError``, ``AttributeError``, ``OverflowError`` or ``RecursionError``. None
is a ``yaml.YAMLError``, so each one crashed ``scoutctl kb`` instead of labelling
the note private.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from scout import repo_sync as rs
from scout.cli import app
from scout.errors import KBSchemaError
from scout.kb import outbound, privacy
from scout.kb.lint import run_hook
from scout.kb.lint_privacy import _config_data, _Unparseable, policy_from_config_text, privacy_staged
from scout.kb.ontology import KnowledgeGraph, clear_frontmatter_cache
from scout.kb.privacy import classify, load_policy, note_facts
from scout.kb.share import ShareRefused, load_overlay
from scout.scripts import privacy_migrate

from .privacy_vault_helpers import build_privacy_vault, git

# One value per exception PyYAML lets out, plus the near neighbours of the reported date.
UNCONSTRUCTIBLE = [
    pytest.param("date: 2026-13-45", id="value-error-impossible-date"),
    pytest.param("date: 2026-02-30", id="value-error-day-out-of-range"),
    pytest.param("when: 2026-01-01 25:00:00", id="value-error-impossible-time"),
    pytest.param("when: 2026-01-01 10:00:00 +25:00", id="value-error-offset-out-of-range"),
    pytest.param("n: 0x_", id="value-error-empty-hex"),
    pytest.param("n: " + "9" * 5000, id="value-error-int-digit-limit"),
    pytest.param("n: !!int foo", id="value-error-tagged-int"),
    pytest.param("n: !!float foo", id="value-error-tagged-float"),
    pytest.param('n: !!int ""', id="index-error-empty-int"),
    pytest.param('n: !!float ""', id="index-error-empty-float"),
    pytest.param("n: !!bool foo", id="key-error-tagged-bool"),
    pytest.param("n: !!timestamp foo", id="attribute-error-tagged-timestamp"),
    pytest.param('n: "\\U99999999"', id="overflow-error-unicode-escape"),
    pytest.param("n: " + "[" * 5000 + "]" * 5000, id="recursion-error-deep-nesting"),
]

RUN = {"SCOUT_MODE": "morning-briefing"}
NOTE = "knowledge-base/topics/misc/odd-value.md"
FORCED = ("private", "forced", ("frontmatter-unparseable",))


def _fm(body: str) -> str:
    return f"---\n{body}\n---\n# Note\n\nBody text.\n"


def _kb(vault: Path, *args: str) -> Any:
    return CliRunner().invoke(app, ["kb", *args, "--repo", str(vault)])


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("SCOUT_READER", raising=False)
    monkeypatch.delenv("SCOUT_MODE", raising=False)
    clear_frontmatter_cache()
    outbound.clear_cache()
    return build_privacy_vault(tmp_path)


def _write(vault: Path, rel: str, text: str) -> None:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ----- the shared parse --------------------------------------------------------


@pytest.mark.parametrize("body", UNCONSTRUCTIBLE)
def test_load_yaml_only_ever_raises_a_yaml_error(body: str) -> None:
    with pytest.raises(yaml.YAMLError):
        privacy.load_yaml(body)
    with pytest.raises(yaml.YAMLError):
        privacy.load_frontmatter_yaml(body)


def test_load_yaml_returns_what_safe_load_returns() -> None:
    text = "name: Alex\nwhen: 2026-01-31\nn: 0x1f\nitems: [1, 2.5, true, null]\n"
    assert privacy.load_yaml(text) == yaml.safe_load(text)
    assert privacy.load_frontmatter_yaml(text) == yaml.safe_load(text)


def test_load_yaml_keeps_a_yaml_error_as_it_is() -> None:
    with pytest.raises(yaml.MarkedYAMLError):  # ontology reports its line and column
        privacy.load_yaml("name: [unclosed\n")


def test_load_yaml_reads_a_stream(tmp_path: Path) -> None:
    (tmp_path / "x.yaml").write_text("a: 1\n", encoding="utf-8")
    with (tmp_path / "x.yaml").open(encoding="utf-8") as fh:
        assert privacy.load_yaml(fh) == {"a": 1}


# ----- the classifier ----------------------------------------------------------


@pytest.mark.parametrize("body", UNCONSTRUCTIBLE)
def test_classify_forces_it_private(body: str) -> None:
    label = classify("knowledge-base/topics/misc/plain.md", _fm(body), load_policy(None))
    assert (label.visibility, label.visibility_source, label.warnings) == FORCED


def test_the_label_matches_malformed_yaml_exactly() -> None:
    policy = load_policy(None)
    rel = "knowledge-base/topics/misc/plain.md"
    assert classify(rel, _fm("date: 2026-13-45"), policy) == classify(rel, _fm("name: [unclosed"), policy)


def test_nothing_else_in_the_block_can_widen_it() -> None:
    body = "visibility: normal\nfederation_scope: org\ndate: 2026-13-45"
    label = classify("knowledge-base/topics/misc/plain.md", _fm(body), load_policy(None))
    assert (label.visibility, label.visibility_source, label.warnings) == FORCED
    assert label.federation_scope_declared == "none" and label.shared_file is False


def test_note_facts_says_unparseable() -> None:
    assert note_facts(_fm("date: 2026-13-45")).state == "unparseable"


def test_a_good_date_is_still_fine() -> None:
    label = classify("knowledge-base/topics/misc/plain.md", _fm("date: 2026-12-31"), load_policy(None))
    assert (label.visibility, label.warnings) == ("normal", ())


# ----- the other frontmatter readers -------------------------------------------


@pytest.mark.parametrize("body", UNCONSTRUCTIBLE)
def test_outbound_terms_and_migration_skip_it(body: str) -> None:
    assert outbound._names_from_text(_fm(body)) == []
    assert privacy_migrate._frontmatter(_fm(body)) is None


def test_the_graph_reports_it_as_a_parse_error_without_its_text(vault: Path) -> None:
    _write(vault, NOTE, _fm("name: Alex\ntype: person\nn: !!int CANARY-7Q2X"))
    kb_root = vault / "knowledge-base"
    schema = str(kb_root / "ontology" / "schema.yaml")
    graph = KnowledgeGraph(schema_path=schema, kb_root=str(kb_root), vault=vault).load()
    (error,) = [e for e in graph.parse_errors if e["path"] == NOTE]
    assert (error["_label"]["visibility"], error["_label"]["visibility_source"]) == ("private", "forced")
    assert "CANARY" not in error["error"]  # the original exception's message quotes the note


def test_a_schema_with_such_a_value_is_a_schema_error(tmp_path: Path) -> None:
    schema = tmp_path / "schema.yaml"
    schema.write_text("when: 2026-13-45\n", encoding="utf-8")
    with pytest.raises(KBSchemaError, match="malformed"):
        KnowledgeGraph(schema_path=str(schema), kb_root=str(tmp_path), vault=tmp_path)


# ----- scout-config.yaml -------------------------------------------------------


@pytest.mark.parametrize("body", UNCONSTRUCTIBLE)
def test_load_policy_drops_the_overlay(tmp_path: Path, body: str) -> None:
    (tmp_path / "scout-config.yaml").write_text(body + "\n", encoding="utf-8")
    assert load_policy(tmp_path).warnings == ("privacy-overlay-unreadable",)


@pytest.mark.parametrize("body", UNCONSTRUCTIBLE)
def test_lint_reads_the_config_as_unparseable(body: str) -> None:
    assert policy_from_config_text(body).warnings == ("privacy-overlay-unreadable",)
    assert isinstance(_config_data(body), _Unparseable)


def test_kb_share_refuses_such_a_config(tmp_path: Path) -> None:
    (tmp_path / "scout-config.yaml").write_text("when: 2026-13-45\n", encoding="utf-8")
    with pytest.raises(ShareRefused):
        load_overlay(tmp_path)


# ----- repo_sync ---------------------------------------------------------------


def test_repo_sync_labels_the_blob_and_reads_the_config_at_a_commit(vault: Path) -> None:
    _write(vault, NOTE, _fm("date: 2026-13-45"))
    _write(vault, "scout-config.yaml", "when: 2026-13-45\n")
    git(vault, "add", "--", NOTE, "scout-config.yaml")
    git(vault, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", "odd values")
    blobs = rs.BlobReader(vault)
    try:
        policy = rs.policy_at(vault, "HEAD", blobs)
        assert policy.warnings == ("privacy-overlay-unreadable",)
        entry = rs._blob_at(vault, "HEAD", NOTE)
        assert entry is not None
        label = rs.label_blob(NOTE, entry, policy, blobs)
    finally:
        blobs.close()
    assert (label.visibility, label.visibility_source, label.warnings) == FORCED


# ----- the commands ------------------------------------------------------------


@pytest.mark.parametrize("body", UNCONSTRUCTIBLE)
def test_kb_classify_labels_it_private(vault: Path, body: str) -> None:
    _write(vault, NOTE, _fm(body))
    result = _kb(vault, "classify", NOTE, "--json")
    assert result.exit_code == 0, result.output
    (row,) = json.loads(result.stdout)
    assert (row["visibility"], row["visibility_source"], tuple(row["warnings"])) == FORCED


def test_kb_files_read_search_and_stats_keep_working(vault: Path) -> None:
    _write(vault, NOTE, _fm("date: 2026-13-45"))
    files = _kb(vault, "files", "--json")
    assert files.exit_code == 0, files.output
    assert NOTE in files.stdout
    read = _kb(vault, "read", NOTE, "--json")
    assert read.exit_code == 0, read.output
    assert "Body text." in read.stdout
    search = _kb(vault, "search", "Body text", "--json")
    assert search.exit_code == 0, search.output
    assert NOTE in search.stdout
    stats = _kb(vault, "stats")
    assert stats.exit_code == 0, stats.output


def test_the_commands_keep_working_with_such_a_value_in_the_config(vault: Path) -> None:
    _write(vault, "scout-config.yaml", "when: 2026-13-45\n")
    for args in (("files", "--json"), ("policy", "--json"), ("classify", "knowledge-base/people/priya.md", "--json")):
        result = _kb(vault, *args)
        assert result.exit_code == 0, result.output


# ----- the commit hook ---------------------------------------------------------


def test_the_privacy_check_still_runs_on_a_commit_holding_it(vault: Path) -> None:
    _write(vault, NOTE, _fm("date: 2026-13-45"))
    git(vault, "add", "--", NOTE)
    assert [(f.path, f.check, f.blocking) for f in privacy_staged(vault, env={})] == [
        (NOTE, "frontmatter-unparseable", False)
    ]


def test_the_hook_does_not_skip_the_privacy_check(vault: Path) -> None:
    _write(vault, NOTE, _fm("date: 2026-13-45"))
    git(vault, "add", "--", NOTE)
    out = io.StringIO()
    assert run_hook(vault, env={}, out=out) == 0
    assert "[frontmatter-unparseable]" in out.getvalue() and "could not run" not in out.getvalue()


def test_a_run_commit_with_such_a_value_in_the_config_is_refused(vault: Path) -> None:
    from scout.kb.lint_privacy import PolicyUnreadable

    _write(vault, "scout-config.yaml", "when: 2026-13-45\n")
    git(vault, "add", "--", "scout-config.yaml")
    with pytest.raises(PolicyUnreadable):
        privacy_staged(vault, env=RUN)
