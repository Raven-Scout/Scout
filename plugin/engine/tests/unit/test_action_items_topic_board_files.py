"""Unit tests for topic-names.md and dismissed.md — the topic board's own files."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout.action_items.cli import app
from scout.action_items.dismissed import dismiss, dismissed_keys, dismissed_path
from scout.action_items.topic_names import parse_topic_names, rename_topic, topic_names_path
from scout.action_items.topics import topics_path
from scout.errors import ActionItemError

runner = CliRunner()

_TOPICS = (
    "# Topics\n"
    "\n"
    "## [#STRM] Stream reattach\n"
    "- Session: https://claude.ai/code/session_0123\n"
    "- Summary: API, UI and flag land in order\n"
    "\n"
    "## [#QBLK] Tracing job\n"
    "- Summary: Retention question\n"
)


@pytest.fixture
def topics(fake_data_dir: Path) -> Path:
    path = topics_path(fake_data_dir)
    path.write_text(_TOPICS, encoding="utf-8")
    return path


def test_rename_writes_topic_names_and_leaves_topics_alone(topics: Path, fake_data_dir: Path) -> None:
    event = rename_topic("strm", "  Reattach  ", data_dir=fake_data_dir)
    assert topics.read_text(encoding="utf-8") == _TOPICS
    assert topic_names_path(fake_data_dir).read_text(encoding="utf-8") == (
        "# Topic names\n\nYour own names for topics, set in the app. Scout runs never write this file.\n\n"
        "- [#STRM] Reattach\n"
    )
    assert event.kind == "action_item.topic_renamed"
    assert event.payload == {"tag": "STRM", "name": "Reattach"}


def test_rename_replaces_an_earlier_name(topics: Path, fake_data_dir: Path) -> None:
    rename_topic("QBLK", "Tracing", data_dir=fake_data_dir)
    rename_topic("STRM", "Reattach", data_dir=fake_data_dir)
    rename_topic("QBLK", "Tracing job v2", data_dir=fake_data_dir)
    text = topic_names_path(fake_data_dir).read_text(encoding="utf-8")
    assert text.endswith("- [#STRM] Reattach\n- [#QBLK] Tracing job v2\n")
    assert parse_topic_names(text) == {"STRM": "Reattach", "QBLK": "Tracing job v2"}


def test_empty_name_clears_the_name(topics: Path, fake_data_dir: Path) -> None:
    rename_topic("QBLK", "Tracing", data_dir=fake_data_dir)
    event = rename_topic("QBLK", " ", data_dir=fake_data_dir)
    assert "QBLK" not in topic_names_path(fake_data_dir).read_text(encoding="utf-8")
    assert event.payload["name"] is None


def test_rename_keeps_crlf(topics: Path, fake_data_dir: Path) -> None:
    path = topic_names_path(fake_data_dir)
    path.write_bytes(b"# Topic names\r\n- [#QBLK] Old\r\n")
    rename_topic("STRM", "Reattach", data_dir=fake_data_dir)
    assert path.read_bytes() == b"# Topic names\r\n- [#QBLK] Old\r\n- [#STRM] Reattach\r\n"


@pytest.mark.parametrize(
    ("tag", "name", "message"),
    [("NOPE", "x", "no topic [#NOPE]"), ("STRM", "two\nlines", "one line")],
)
def test_rename_rejects_bad_input(topics: Path, fake_data_dir: Path, tag: str, name: str, message: str) -> None:
    with pytest.raises(ActionItemError, match=message.replace("[", r"\[")):
        rename_topic(tag, name, data_dir=fake_data_dir)
    assert not topic_names_path(fake_data_dir).exists()


def test_rename_without_a_topics_file(fake_data_dir: Path) -> None:
    with pytest.raises(ActionItemError, match="no topic"):
        rename_topic("STRM", "x", data_dir=fake_data_dir)


def test_dismiss_creates_the_file_and_is_idempotent(fake_data_dir: Path) -> None:
    url = "https://acme-co.slack.com/archives/C0123456789/p1700000000000000"
    assert dismiss(url, data_dir=fake_data_dir).payload == {"key": url, "added": True}
    assert dismiss(f" {url} ", data_dir=fake_data_dir).payload == {"key": url, "added": False}
    dismiss("Delete the stale branch", data_dir=fake_data_dir)
    assert dismissed_keys(fake_data_dir) == [url, "Delete the stale branch"]
    assert dismissed_path(fake_data_dir).read_text(encoding="utf-8").startswith("# Dismissed\n")


def test_dismissed_keys_without_a_file(fake_data_dir: Path) -> None:
    assert dismissed_keys(fake_data_dir) == []


@pytest.mark.parametrize("key", ["", "  ", "a\nb"])
def test_dismiss_rejects_bad_keys(fake_data_dir: Path, key: str) -> None:
    with pytest.raises(ActionItemError, match="one non-empty line"):
        dismiss(key, data_dir=fake_data_dir)


def test_cli_rename_topic_and_dismiss(topics: Path, fake_data_dir: Path) -> None:
    renamed = runner.invoke(app, ["rename-topic", "#STRM", "--name", "Reattach", "--data-dir", str(fake_data_dir)])
    dismissed = runner.invoke(app, ["dismiss", "--key", "Delete the stale branch"])
    assert renamed.exit_code == 0, renamed.output
    assert dismissed.exit_code == 0, dismissed.output
    assert json.loads(renamed.stdout)["payload"] == {"tag": "STRM", "name": "Reattach"}
    assert json.loads(dismissed.stdout)["payload"] == {"key": "Delete the stale branch", "added": True}
    assert parse_topic_names(topic_names_path(fake_data_dir).read_text(encoding="utf-8")) == {"STRM": "Reattach"}
    assert dismissed_keys(fake_data_dir) == ["Delete the stale branch"]


def test_manifest_advertises_topic_board_writes() -> None:
    from scout.manifest import build_manifest

    assert build_manifest().features["topic_board_v1"] is True
