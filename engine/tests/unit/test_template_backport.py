"""Unit tests for scout.scripts.template_backport — turning a vault's edit to a
managed file into a patch against the plugin's template (``scoutctl bootstrap
drift --patch``)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from scout.scripts.phase_assembly import render_template
from scout.scripts.template_backport import backport_patch

VARS = {
    "SCOUT_DIR": "/Users/alex/Scout",
    "SCOUTCTL_BIN": "/opt/scout-plugin/.venv/bin/scoutctl",
    "INSTANCE_NAME": "TestScout",
    "USER_NAME": "Alex",
    "TIMEZONE": "America/New_York",
}

TEMPLATE = (
    "#!/bin/bash\n"
    "# {{INSTANCE_NAME}} heartbeat\n"
    'SCOUT_DIR="{{SCOUT_DIR}}"\n'
    "set -euo pipefail\n"
    'exec "{{SCOUTCTL_BIN}}" heartbeat run\n'
)
PLUGIN_REL = "templates/scripts/heartbeat.sh.tmpl"


def _live_with_fix() -> str:
    """The render, plus a vault fix that mentions the vault path and changes a line."""
    render = render_template(TEMPLATE, VARS)
    return render.replace(
        "set -euo pipefail\n",
        "set -euo pipefail\nmkdir -p /Users/alex/Scout/.scout-cache\n",
    ).replace("heartbeat run\n", "heartbeat run --quiet\n")


def test_the_patched_template_renders_back_to_the_vault_file() -> None:
    live = _live_with_fix()
    p = backport_patch(
        vault_rel="scripts/heartbeat.sh", plugin_rel=PLUGIN_REL, template=TEMPLATE, rendered=True, live=live, vars_=VARS
    )

    assert p.template_after is not None
    assert render_template(p.template_after, VARS) == live
    # Vault-specific values go back to their template variables.
    assert "mkdir -p {{SCOUT_DIR}}/.scout-cache" in p.template_after
    assert 'exec "{{SCOUTCTL_BIN}}" heartbeat run --quiet' in p.template_after
    assert p.warnings == []


def test_the_patch_applies_to_the_template_with_git_apply(tmp_path: Path) -> None:
    repo = tmp_path / "plugin"
    (repo / "templates" / "scripts").mkdir(parents=True)
    (repo / PLUGIN_REL).write_text(TEMPLATE, encoding="utf-8")
    p = backport_patch(
        vault_rel="scripts/heartbeat.sh",
        plugin_rel=PLUGIN_REL,
        template=TEMPLATE,
        rendered=True,
        live=_live_with_fix(),
        vars_=VARS,
    )
    (tmp_path / "fix.patch").write_text(p.patch, encoding="utf-8")

    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "apply", str(tmp_path / "fix.patch")], check=True)

    assert (repo / PLUGIN_REL).read_text(encoding="utf-8") == p.template_after


def test_an_added_line_with_an_instance_specific_value_is_flagged() -> None:
    live = render_template(TEMPLATE, VARS) + 'echo "ping Alex"\n'
    p = backport_patch(
        vault_rel="scripts/heartbeat.sh", plugin_rel=PLUGIN_REL, template=TEMPLATE, rendered=True, live=live, vars_=VARS
    )

    assert any("USER_NAME" in w for w in p.warnings)


def test_a_verbatim_file_is_patched_as_is_and_vault_paths_are_flagged() -> None:
    source = "def main():\n    return 1\n"
    live = "def main():\n    root = '/Users/alex/Scout'\n    return 1\n"
    p = backport_patch(
        vault_rel="scripts/recurring-task-status.py",
        plugin_rel="templates/scripts/recurring-task-status.py",
        template=source,
        rendered=False,
        live=live,
        vars_=VARS,
    )

    assert p.template_after == live  # never re-templatized: the file isn't rendered
    assert any("SCOUT_DIR" in w for w in p.warnings)


def test_an_unedited_file_has_no_patch() -> None:
    p = backport_patch(
        vault_rel="scripts/heartbeat.sh",
        plugin_rel=PLUGIN_REL,
        template=TEMPLATE,
        rendered=True,
        live=render_template(TEMPLATE, VARS),
        vars_=VARS,
    )
    assert p.patch == ""


def test_a_missing_final_newline_still_makes_a_valid_patch(tmp_path: Path) -> None:
    live = render_template(TEMPLATE, VARS).rstrip("\n") + "\necho done"
    p = backport_patch(
        vault_rel="scripts/heartbeat.sh", plugin_rel=PLUGIN_REL, template=TEMPLATE, rendered=True, live=live, vars_=VARS
    )
    repo = tmp_path / "plugin"
    (repo / "templates" / "scripts").mkdir(parents=True)
    (repo / PLUGIN_REL).write_text(TEMPLATE, encoding="utf-8")
    (tmp_path / "fix.patch").write_text(p.patch, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "apply", str(tmp_path / "fix.patch")], check=True)
    assert render_template((repo / PLUGIN_REL).read_text(encoding="utf-8"), VARS) == live
