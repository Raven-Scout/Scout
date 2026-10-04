"""A re-run of /scout-update or install.sh must switch the registered version.

On an existing install `claude plugin install` only unpacks the new cache and
keeps the old version registered, so the plugin-root resolver that runs next
returns the old plugin. Both entry points therefore follow the install with
`claude plugin update` (#234, stale-registry half): /scout-update once per scope
Scout is installed in, stopping with PLUGIN_UPDATE_FAILED when one fails, and
install.sh for its user-scope install, with a warning when it fails.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).parent.parent.parent.parent
_UPDATE_MD = _REPO / "commands" / "scout-update.md"
_INSTALL_SH = _REPO / "install.sh"
_SCOPE_SNIPPET_RE = re.compile(r"python3 -c '([^']*\.get\(\"scope\"\)[^']*)'")


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_scout_update_runs_the_update_after_the_install() -> None:
    text = _text(_UPDATE_MD)
    install = text.index("claude plugin install scout@scout-plugin")
    update = text.index('claude plugin update scout@scout-plugin --scope "$scope"')
    assert update > install


def test_scout_update_reports_a_failed_update_and_stops() -> None:
    text = _text(_UPDATE_MD)
    assert 'echo "PLUGIN_UPDATE_FAILED:$scope"' in text
    assert "If the output contains `PLUGIN_UPDATE_FAILED:<scope>`, **stop here**" in text


def test_install_sh_updates_user_scope_and_warns_on_failure() -> None:
    text = _text(_INSTALL_SH)
    install = text.index('claude plugin install "$PLUGIN_ID"')
    update = text.index('claude plugin update "$PLUGIN_ID" --scope user')
    assert update > install
    line = text[update : text.index("\n", update)]
    assert "|| true" not in line, "a failed update must not be silenced"
    assert "warning: could not switch the registered Scout plugin" in text


def _scopes(payload: object) -> str:
    match = _SCOPE_SNIPPET_RE.search(_text(_UPDATE_MD))
    assert match is not None, "the scope lookup in Step 0.5 is missing"
    done = subprocess.run(
        [sys.executable, "-c", match.group(1)], input=json.dumps(payload), capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


_SCOUT = "scout@scout-plugin"


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ([{"id": _SCOUT, "scope": "user"}], "user"),
        ([{"id": _SCOUT, "scope": "user"}, {"id": _SCOUT, "scope": "project"}], "project user"),
        ({"plugins": {"scout-plugin": [{"id": _SCOUT, "scope": "local"}]}}, "local"),
        ([{"id": _SCOUT}], "user"),
        ([{"id": "notes@example-market", "scope": "project"}], "user"),
    ],
)
def test_the_scope_lookup_covers_every_install(payload: object, expected: str) -> None:
    assert _scopes(payload) == expected
