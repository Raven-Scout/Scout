"""Unit tests for scout.scripts.cc_session_cache.

The module is now a back-compat shim (Agent Sessions plan 1, closes #74/#75
by moving the real work into `scout.sessions`): it re-exports the two
transcript extractors for anything still importing them from here, and its
`main()` delegates to `scout.sessions.index.run(render=True)`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scout.scripts.cc_session_cache import (
    extract_files_touched,
    extract_first_message,
)

# ----- helpers ------------------------------------------------------------


def _write_jsonl(path: Path, lines: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")


# ----- first message extraction ------------------------------------------


def test_extract_first_message_handles_content_list(tmp_path: Path) -> None:
    jsonl = tmp_path / "s.jsonl"
    _write_jsonl(
        jsonl,
        [
            {"type": "assistant", "message": {"content": "irrelevant"}},
            {
                "type": "user",
                "message": {"content": [{"type": "text", "text": "build me a thing"}]},
            },
        ],
    )
    assert extract_first_message(jsonl) == "build me a thing"


def test_extract_first_message_handles_string_content(tmp_path: Path) -> None:
    jsonl = tmp_path / "s.jsonl"
    _write_jsonl(jsonl, [{"type": "user", "content": "hi there"}])
    assert extract_first_message(jsonl) == "hi there"


def test_extract_first_message_falls_back_when_no_match(tmp_path: Path) -> None:
    jsonl = tmp_path / "s.jsonl"
    _write_jsonl(jsonl, [{"type": "assistant", "message": {"content": "no users here"}}])
    assert "could not extract" in extract_first_message(jsonl)


def test_extract_first_message_handles_malformed_lines(tmp_path: Path) -> None:
    jsonl = tmp_path / "s.jsonl"
    jsonl.write_text(
        "not json\n" + json.dumps({"type": "user", "message": {"content": "found it"}}) + "\n",
        encoding="utf-8",
    )
    assert extract_first_message(jsonl) == "found it"


# ----- files-touched extraction ------------------------------------------


def test_extract_files_touched_filters_noise_and_collapses_home(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    jsonl = tmp_path / "s.jsonl"
    contents = (
        '{"file_path":"/Users/me/.claude/projects/x/tool-results/y"}\n'
        '{"file_path":"/Users/me/.claude/plugins/cache/abc"}\n'
        '{"file_path":"/Users/me/repo/src/main.py"}\n'
        f'{{"file_path":"{home}/work/project/notes.md"}}\n'
        '{"file_path":"/private/tmp/claude-temp"}\n'
        '{"file_path":"/Users/me/node_modules/some/lib.js"}\n'
    )
    jsonl.write_text(contents, encoding="utf-8")
    touched = extract_files_touched(jsonl, home=home)
    # Two legitimate paths survive; the rest are noise-filtered.
    assert "/Users/me/repo/src/main.py" in touched
    assert "~/work/project/notes.md" in touched
    assert len(touched) == 2


def test_extract_files_touched_caps_at_ten(tmp_path: Path) -> None:
    jsonl = tmp_path / "s.jsonl"
    jsonl.write_text(
        "\n".join(f'{{"file_path":"/p/file-{i:02d}.md"}}' for i in range(20)) + "\n",
        encoding="utf-8",
    )
    assert len(extract_files_touched(jsonl)) == 10


# ----- main() delegates to the index --------------------------------------


def test_main_delegates_to_the_index_and_never_raises(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import scout.sessions.github as gh
    from scout.scripts.cc_session_cache import main

    monkeypatch.setattr(gh, "gh_available", lambda: False)
    assert main(hours=6, tz_name="UTC") == 0
    assert (fake_data_dir / ".scout-cache" / "cc-sessions.md").exists()
