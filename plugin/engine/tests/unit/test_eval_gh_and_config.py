"""The fake gh on PATH and the stub MCP config (spec 2026-10-12 §4.1, §5.4)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

from tests.eval.harness.mcp_config import PERSONA_SERVERS, write_mcp_config
from tests.eval.harness.stubs.gh import GH_BIN_DIR

ENGINE = Path(__file__).resolve().parents[2]


def _gh(tmp_path: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": f"{GH_BIN_DIR}:/usr/bin:/bin",
        "SCOUT_EVAL_PYTHON": sys.executable,
        "SCOUT_EVAL_ENGINE": str(ENGINE),
        "SCOUT_EVAL_SCRIPT": str(tmp_path / "connectors.yaml"),
        "SCOUT_EVAL_CALLS": str(tmp_path / "calls.jsonl"),
        "SCOUT_EVAL_TODAY": "2026-10-14",
        "HOME": str(tmp_path),
    }
    return subprocess.run(["gh", *argv], env=env, capture_output=True, text=True, cwd=tmp_path)


def _script(tmp_path: Path) -> None:
    (tmp_path / "prs.json").write_text(json.dumps([{"number": 12, "updatedAt": "{{d-1}}"}]))
    (tmp_path / "connectors.yaml").write_text(
        "gh:\n"
        "  '*':\n"
        "    - match: {argv: '^pr list .*--author @me'}\n"
        "      respond: {stdout: prs.json}\n"
        "    - match: {argv: '^auth status'}\n"
        "      respond: {stdout: 'Logged in to github.com account alex-example'}\n"
        "    - match: {argv: '^api /repos/example-org/widgets/pulls/12/reviews'}\n"
        "      error: unavailable\n"
    )


def test_gh_on_path_answers_from_the_script_and_renders_dates(tmp_path: Path) -> None:
    _script(tmp_path)
    r = _gh(tmp_path, "pr", "list", "--state", "open", "--author", "@me", "--json", "number,updatedAt")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == [{"number": 12, "updatedAt": "2026-10-13"}]


def test_gh_inline_text_response(tmp_path: Path) -> None:
    _script(tmp_path)
    r = _gh(tmp_path, "auth", "status")
    assert (r.returncode, r.stdout.strip()) == (0, "Logged in to github.com account alex-example")


def test_gh_scripted_error_and_unstubbed_both_exit_nonzero_and_are_logged(tmp_path: Path) -> None:
    _script(tmp_path)
    err = _gh(tmp_path, "api", "/repos/example-org/widgets/pulls/12/reviews")
    miss = _gh(tmp_path, "repo", "delete", "example-org/widgets")
    assert err.returncode == 1 and "503" in err.stderr
    assert miss.returncode == 1 and "not available in this eval fixture" in miss.stderr
    rows = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert [(r["persona"], r["kind"]) for r in rows] == [("gh", "error"), ("gh", "unstubbed")]
    assert rows[1]["args"] == {"argv": "repo delete example-org/widgets"}


def test_mcp_config_has_one_stdio_server_per_persona(tmp_path: Path) -> None:
    out = tmp_path / "mcp.json"
    allowed = write_mcp_config(
        out,
        personas=["slack", "linear"],
        script=tmp_path / "connectors.yaml",
        calls=tmp_path / "calls.jsonl",
        today=date(2026, 10, 14),
        python=sys.executable,
    )
    cfg = json.loads(out.read_text())["mcpServers"]
    assert allowed == {"claude_ai_Slack", "claude_ai_Linear"} == set(cfg)
    slack = cfg["claude_ai_Slack"]
    assert slack["command"] == sys.executable
    assert slack["args"][:2] == ["-m", "tests.eval.harness.stubs.mcp_server"]
    assert slack["cwd"] == str(ENGINE)
    assert all(os.path.isabs(a) for a in slack["args"] if a.endswith((".yaml", ".jsonl")))
    assert PERSONA_SERVERS["calendar"] == "claude_ai_Google_Calendar"
