"""Unit tests for topics.md renames and dismissed.md — the topic board's own files."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout.action_items.cli import app
from scout.action_items.dismissed import dismiss, dismissed_keys, dismissed_path
from scout.action_items.topics import rename_topic, topics_path
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
    "- Renamed: Old name\n"
    "- Summary: Retention question\n"
)


@pytest.fixture
def topics(fake_data_dir: Path) -> Path:
    path = topics_path(fake_data_dir)
    path.write_text(_TOPICS, encoding="utf-8")
    return path


def test_rename_adds_the_line_after_the_topic_fields(topics: Path, fake_data_dir: Path) -> None:
    event = rename_topic("strm", "  Reattach  ", data_dir=fake_data_dir)
    assert topics.read_text(encoding="utf-8") == _TOPICS.replace(
        "- Summary: API, UI and flag land in order\n",
        "- Summary: API, UI and flag land in order\n- Renamed: Reattach\n",
    )
    assert event.kind == "action_item.topic_renamed"
    assert event.payload == {"tag": "STRM", "name": "Reattach"}


def test_rename_replaces_an_earlier_rename(topics: Path, fake_data_dir: Path) -> None:
    rename_topic("QBLK", "Tracing", data_dir=fake_data_dir)
    assert topics.read_text(encoding="utf-8").endswith(
        "## [#QBLK] Tracing job\n- Summary: Retention question\n- Renamed: Tracing\n"
    )


def test_empty_name_clears_the_rename(topics: Path, fake_data_dir: Path) -> None:
    event = rename_topic("QBLK", " ", data_dir=fake_data_dir)
    assert "Renamed" not in topics.read_text(encoding="utf-8")
    assert event.payload["name"] is None


def test_rename_keeps_crlf(fake_data_dir: Path) -> None:
    path = topics_path(fake_data_dir)
    path.write_bytes(b"## [#STRM] Stream reattach\r\n- Summary: x\r\n")
    rename_topic("STRM", "Reattach", data_dir=fake_data_dir)
    assert path.read_bytes() == b"## [#STRM] Stream reattach\r\n- Summary: x\r\n- Renamed: Reattach\r\n"


@pytest.mark.parametrize(
    ("tag", "name", "message"),
    [("NOPE", "x", "no topic [#NOPE]"), ("STRM", "two\nlines", "one line")],
)
def test_rename_rejects_bad_input(topics: Path, fake_data_dir: Path, tag: str, name: str, message: str) -> None:
    with pytest.raises(ActionItemError, match=message.replace("[", r"\[")):
        rename_topic(tag, name, data_dir=fake_data_dir)
    assert topics.read_text(encoding="utf-8") == _TOPICS


def test_rename_without_a_topics_file(fake_data_dir: Path) -> None:
    with pytest.raises(ActionItemError, match="does not exist"):
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
    assert "- Renamed: Reattach" in topics.read_text(encoding="utf-8")
    assert dismissed_keys(fake_data_dir) == ["Delete the stale branch"]


def test_manifest_advertises_topic_board_writes() -> None:
    from scout.manifest import build_manifest

    assert build_manifest().features["topic_board_v1"] is True
