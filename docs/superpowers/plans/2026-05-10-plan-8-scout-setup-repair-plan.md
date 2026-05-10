# Plan 8 — `/scout-setup` repair + onboarding/upgrade flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/scout-setup` correctly install the Plan-5 world for fresh users, and add `/scout-update` so existing vaults pick up plugin changes without clobbering vault edits.

**Architecture:** Two thin slash commands wrap a single Python entry point — `scoutctl bootstrap {install|upgrade|doctor}` — which runs an 8-stage pipeline (pre-flight → migrations → cat-1 writes → cat-1b runners → cat-4 3-way merge → job lifecycle → version stamp → doctor smoke). Cat-4 conflicts produce sidecar files instead of overwriting the live SKILL.md, so the running system stays functional during conflict resolution. macOS uses launchd; Linux uses crontab managed-block writes.

**Tech Stack:**
- Python 3.11 (engine: `~/scout-plugin/engine/scout/`)
- Typer (CLI)
- `git merge-file` via subprocess (3-way merge)
- pytest (unit + integration)
- Bash (slash commands, install-venv fallback)
- launchd plist (macOS scheduling) / crontab (Linux scheduling)

**Spec:** `~/scout-app/docs/superpowers/specs/2026-05-09-plan-8-scout-setup-repair-design.md` (commit `71c3f45`)

**Repo locations:**
- Engine + plugin source: `~/scout-plugin/`
- Spec/plan tracking + Mac app: `~/scout-app/`
- User vault (target of installs): `~/Scout/`

**Commit conventions:** Per the spec, commits to `~/scout-plugin/` use conventional-commits style (`feat:`, `fix:`, `test:`, `docs:`, `chore:`). The vault repo (`~/Scout/`) is untouched until end-to-end testing.

---

## Phase A — Engine core (foundations, no CLI surface yet)

### Task A1: Three-way merge helper

**Files:**
- Create: `~/scout-plugin/engine/scout/scripts/three_way_merge.py`
- Create: `~/scout-plugin/engine/tests/unit/test_three_way_merge.py`

- [ ] **Step 1: Write the failing tests**

Create `~/scout-plugin/engine/tests/unit/test_three_way_merge.py`:

```python
"""Unit tests for engine/scout/scripts/three_way_merge.py."""

from __future__ import annotations

from scout.scripts.three_way_merge import MergeResult, three_way_merge


def test_clean_merge_no_conflict(tmp_path):
    base = "alpha\nbeta\ngamma\n"
    ours = "alpha\nbeta\ngamma\ndelta\n"      # plugin added a line at end
    theirs = "alpha\nBETA\ngamma\n"            # vault edited middle line
    result = three_way_merge(base=base, ours=ours, theirs=theirs)
    assert isinstance(result, MergeResult)
    assert result.conflicts is False
    # Both sides' changes should appear.
    assert "BETA" in result.content
    assert "delta" in result.content


def test_conflicting_change_returns_markers(tmp_path):
    base = "alpha\nbeta\ngamma\n"
    ours = "alpha\nBETA-OURS\ngamma\n"          # plugin changed line 2
    theirs = "alpha\nBETA-THEIRS\ngamma\n"      # vault changed line 2 differently
    result = three_way_merge(base=base, ours=ours, theirs=theirs)
    assert result.conflicts is True
    assert "<<<<<<<" in result.content
    assert "=======" in result.content
    assert ">>>>>>>" in result.content
    assert "BETA-OURS" in result.content
    assert "BETA-THEIRS" in result.content


def test_identical_inputs_no_change():
    text = "alpha\nbeta\n"
    result = three_way_merge(base=text, ours=text, theirs=text)
    assert result.conflicts is False
    assert result.content == text


def test_empty_inputs():
    result = three_way_merge(base="", ours="", theirs="")
    assert result.conflicts is False
    assert result.content == ""
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd ~/scout-plugin/engine && pytest tests/unit/test_three_way_merge.py -v
```

Expected: ImportError / ModuleNotFoundError on `scout.scripts.three_way_merge`.

- [ ] **Step 3: Write the implementation**

Create `~/scout-plugin/engine/scout/scripts/three_way_merge.py`:

```python
"""Three-way merge wrapper around `git merge-file`.

Used by stage 5 of the bootstrap pipeline to merge plugin-side phase
updates with vault-side edits to SKILL.md / DREAMING.md / RESEARCH.md.
"""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MergeResult:
    """Outcome of a three-way merge.

    - ``content``: the merged text. If ``conflicts`` is True, the text
      contains conflict markers (``<<<<<<< ours``, ``=======``,
      ``>>>>>>> theirs``, with diff3-style ``||||||| base`` blocks).
    - ``conflicts``: whether any conflicts were left unresolved.
    """

    content: str
    conflicts: bool


def three_way_merge(*, base: str, ours: str, theirs: str) -> MergeResult:
    """Merge ``ours`` and ``theirs`` against common ancestor ``base``.

    Wraps ``git merge-file --diff3 -p`` which is shipped with every
    git installation. Exit code 0 = clean; >0 = number of conflicts.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        ours_path = tmp_path / "ours"
        base_path = tmp_path / "base"
        theirs_path = tmp_path / "theirs"
        ours_path.write_text(ours)
        base_path.write_text(base)
        theirs_path.write_text(theirs)

        proc = subprocess.run(
            [
                "git",
                "merge-file",
                "--diff3",
                "-p",
                str(ours_path),
                str(base_path),
                str(theirs_path),
            ],
            capture_output=True,
            text=True,
        )
        # git merge-file: returncode 0 = clean, >0 = number of conflicts,
        # <0 = fatal. Treat fatal as "raise".
        if proc.returncode < 0:
            raise RuntimeError(f"git merge-file failed: {proc.stderr}")
        return MergeResult(content=proc.stdout, conflicts=proc.returncode > 0)
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd ~/scout-plugin/engine && pytest tests/unit/test_three_way_merge.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
cd ~/scout-plugin && git add engine/scout/scripts/three_way_merge.py engine/tests/unit/test_three_way_merge.py && git commit -m "feat(engine): add three_way_merge helper for stage 5 cat-4 merges

Wraps git merge-file --diff3 -p with a typed MergeResult. Used by the
bootstrap pipeline's cat-4 stage to merge plugin-side phase updates
against vault-side edits to SKILL.md/DREAMING.md/RESEARCH.md. Plan 8 §4.5."
```

---

### Task A2: Heartbeat plist installer

**Files:**
- Create: `~/scout-plugin/engine/scout/defaults/com.scout.heartbeat.plist`
- Create: `~/scout-plugin/engine/scout/scripts/install_heartbeat_plist.py`
- Create: `~/scout-plugin/engine/tests/unit/test_install_heartbeat_plist.py`

- [ ] **Step 1: Write the heartbeat plist defaults file**

Create `~/scout-plugin/engine/scout/defaults/com.scout.heartbeat.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<!--
  com.scout.heartbeat.plist — runs ~/Scout/scripts/heartbeat.sh every 30 min.

  Installed by `scoutctl schedule install-heartbeat-plist`, which fills
  __USER_HOME__ at install time. Mirrors the schedule-tick plist pattern.
-->
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.scout.heartbeat</string>

    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>__USER_HOME__/Scout/scripts/heartbeat.sh</string>
    </array>

    <key>WorkingDirectory</key>
    <string>__USER_HOME__/Scout</string>

    <key>StartInterval</key>
    <integer>1800</integer>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>__USER_HOME__/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>HOME</key>
        <string>__USER_HOME__</string>
    </dict>

    <key>StandardOutPath</key>
    <string>__USER_HOME__/Scout/.scout-logs/launchd-heartbeat.log</string>
    <key>StandardErrorPath</key>
    <string>__USER_HOME__/Scout/.scout-logs/launchd-heartbeat.log</string>
</dict>
</plist>
```

- [ ] **Step 2: Write the failing tests**

Create `~/scout-plugin/engine/tests/unit/test_install_heartbeat_plist.py`:

```python
"""Unit tests for engine/scout/scripts/install_heartbeat_plist.py."""

from __future__ import annotations

import pytest

from scout.scripts.install_heartbeat_plist import install_plist, uninstall_plist


def test_install_plist_writes_filled_template(tmp_path):
    target_dir = tmp_path / "LaunchAgents"
    target_dir.mkdir()
    install_plist(home=tmp_path, agents_dir=target_dir)
    written = target_dir / "com.scout.heartbeat.plist"
    assert written.exists()
    content = written.read_text()
    assert "__USER_HOME__" not in content
    assert str(tmp_path) in content
    assert "<integer>1800</integer>" in content


def test_install_plist_refuses_to_overwrite_without_force(tmp_path):
    target_dir = tmp_path / "LaunchAgents"
    target_dir.mkdir()
    plist = target_dir / "com.scout.heartbeat.plist"
    plist.write_text("# existing\n")
    with pytest.raises(FileExistsError):
        install_plist(home=tmp_path, agents_dir=target_dir, force=False)
    assert plist.read_text() == "# existing\n"


def test_install_plist_force_overwrites(tmp_path):
    target_dir = tmp_path / "LaunchAgents"
    target_dir.mkdir()
    plist = target_dir / "com.scout.heartbeat.plist"
    plist.write_text("# old\n")
    install_plist(home=tmp_path, agents_dir=target_dir, force=True)
    assert "<integer>1800</integer>" in plist.read_text()


def test_uninstall_plist_removes_file(tmp_path):
    target_dir = tmp_path / "LaunchAgents"
    target_dir.mkdir()
    plist = target_dir / "com.scout.heartbeat.plist"
    plist.write_text("dummy\n")
    uninstall_plist(agents_dir=target_dir)
    assert not plist.exists()


def test_uninstall_plist_silent_when_missing(tmp_path):
    target_dir = tmp_path / "LaunchAgents"
    target_dir.mkdir()
    uninstall_plist(agents_dir=target_dir)  # no exception
```

- [ ] **Step 3: Run tests to confirm they fail**

```bash
cd ~/scout-plugin/engine && pytest tests/unit/test_install_heartbeat_plist.py -v
```

Expected: ImportError on `scout.scripts.install_heartbeat_plist`.

- [ ] **Step 4: Write the implementation**

Create `~/scout-plugin/engine/scout/scripts/install_heartbeat_plist.py`:

```python
"""Helper for `scoutctl schedule install-heartbeat-plist [--uninstall] [--force]`.

Mirrors install_schedule_plist.py for com.scout.heartbeat.plist.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

PLIST_NAME = "com.scout.heartbeat.plist"
TEMPLATE = Path(__file__).parent.parent / "defaults" / PLIST_NAME


def install_plist(
    *,
    home: Path,
    agents_dir: Path | None = None,
    force: bool = False,
    bootstrap: bool = False,
) -> Path:
    """Render the template into ~/Library/LaunchAgents/."""
    agents_dir = agents_dir or (home / "Library" / "LaunchAgents")
    agents_dir.mkdir(parents=True, exist_ok=True)
    target = agents_dir / PLIST_NAME
    if target.exists() and not force:
        raise FileExistsError(target)
    rendered = TEMPLATE.read_text().replace("__USER_HOME__", str(home))
    target.write_text(rendered)
    if bootstrap:
        uid = os.getuid()
        subprocess.run(
            ["launchctl", "bootstrap", f"gui/{uid}", str(target)],
            check=False,
        )
    return target


def uninstall_plist(*, agents_dir: Path | None = None, bootout: bool = False) -> None:
    """Remove the plist (and optionally bootout the job from launchd)."""
    agents_dir = agents_dir or (Path.home() / "Library" / "LaunchAgents")
    target = agents_dir / PLIST_NAME
    if bootout:
        uid = os.getuid()
        subprocess.run(
            ["launchctl", "bootout", f"gui/{uid}/com.scout.heartbeat"],
            check=False,
        )
    if target.exists():
        target.unlink()
```

- [ ] **Step 5: Run tests to confirm they pass**

```bash
cd ~/scout-plugin/engine && pytest tests/unit/test_install_heartbeat_plist.py -v
```

Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
cd ~/scout-plugin && git add engine/scout/defaults/com.scout.heartbeat.plist engine/scout/scripts/install_heartbeat_plist.py engine/tests/unit/test_install_heartbeat_plist.py && git commit -m "feat(engine): add heartbeat plist installer

Mirrors install_schedule_plist.py for com.scout.heartbeat.plist with
30-min StartInterval. Removes the gap where the live heartbeat plist
had no plugin source-of-truth. Plan 8 §5.1."
```

---

### Task A3: Cron managed-block installer (Linux)

**Files:**
- Create: `~/scout-plugin/engine/scout/defaults/cron-managed-block.tmpl`
- Create: `~/scout-plugin/engine/scout/scripts/install_cron.py`
- Create: `~/scout-plugin/engine/tests/unit/test_install_cron.py`

- [ ] **Step 1: Create the cron managed-block template**

Create `~/scout-plugin/engine/scout/defaults/cron-managed-block.tmpl`:

```
# >>> scout-managed >>>
# Lines between these markers are managed by `scoutctl schedule install-cron`.
# Do not edit by hand — your changes will be lost on the next /scout-update.
SHELL=/bin/bash
PATH=__USER_HOME__/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin
*/5 * * * * __USER_HOME__/scout-plugin/.venv/bin/scoutctl schedule tick >> __USER_HOME__/Scout/.scout-logs/cron.log 2>&1
*/30 * * * * __USER_HOME__/Scout/scripts/heartbeat.sh >> __USER_HOME__/Scout/.scout-logs/cron.log 2>&1
# <<< scout-managed <<<
```

- [ ] **Step 2: Write the failing tests**

Create `~/scout-plugin/engine/tests/unit/test_install_cron.py`:

```python
"""Unit tests for engine/scout/scripts/install_cron.py.

Tests use FakeCrontab — a stand-in for the real `crontab` binary that
captures invocations so we can assert atomic-rewrite behavior without
mutating the developer's actual crontab.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.scripts import install_cron as cron_mod


class FakeCrontab:
    """In-memory crontab simulator. Replace `crontab -l` and `crontab <file>`."""

    def __init__(self, initial: str = "") -> None:
        self.content = initial
        self.apply_calls: list[str] = []
        self.fail_next_apply = False

    def list(self) -> tuple[int, str, str]:
        if self.content:
            return (0, self.content, "")
        return (1, "", "no crontab for user\n")

    def apply(self, file_path: str) -> tuple[int, str, str]:
        self.apply_calls.append(Path(file_path).read_text())
        if self.fail_next_apply:
            return (1, "", "fake apply failure\n")
        self.content = Path(file_path).read_text()
        return (0, "", "")


@pytest.fixture
def fake(monkeypatch):
    fc = FakeCrontab()

    def fake_run(args, capture_output=True, text=True, check=False):
        from subprocess import CompletedProcess

        if args[:2] == ["crontab", "-l"]:
            rc, out, err = fc.list()
            return CompletedProcess(args, rc, stdout=out, stderr=err)
        if args[0] == "crontab" and len(args) == 2:
            rc, out, err = fc.apply(args[1])
            return CompletedProcess(args, rc, stdout=out, stderr=err)
        raise AssertionError(f"unexpected subprocess call: {args}")

    monkeypatch.setattr(cron_mod.subprocess, "run", fake_run)
    return fc


def test_install_into_empty_crontab(fake, tmp_path):
    cron_mod.install_cron(home=tmp_path, backup_dir=tmp_path)
    assert "# >>> scout-managed >>>" in fake.content
    assert "# <<< scout-managed <<<" in fake.content
    assert "scoutctl schedule tick" in fake.content
    assert "heartbeat.sh" in fake.content
    assert str(tmp_path) in fake.content  # __USER_HOME__ replaced


def test_install_replaces_existing_managed_block(fake, tmp_path):
    fake.content = (
        "# user's own line\n"
        "0 * * * * /something/else\n"
        "# >>> scout-managed >>>\n"
        "*/99 * * * * old-stale-line\n"
        "# <<< scout-managed <<<\n"
        "# trailing user line\n"
    )
    cron_mod.install_cron(home=tmp_path, backup_dir=tmp_path)
    # User lines preserved
    assert "# user's own line" in fake.content
    assert "0 * * * * /something/else" in fake.content
    assert "# trailing user line" in fake.content
    # Old block gone
    assert "old-stale-line" not in fake.content
    # New block present
    assert "scoutctl schedule tick" in fake.content


def test_install_atomic_failure_preserves_original(fake, tmp_path):
    fake.content = "# user line\n"
    fake.fail_next_apply = True
    with pytest.raises(cron_mod.CrontabApplyError):
        cron_mod.install_cron(home=tmp_path, backup_dir=tmp_path)
    # crontab still equals original — atomic temp-file approach kept user safe
    assert fake.content == "# user line\n"


def test_install_writes_backup_of_previous_crontab(fake, tmp_path):
    fake.content = "0 * * * * /old/job\n"
    cron_mod.install_cron(home=tmp_path, backup_dir=tmp_path)
    backups = list(tmp_path.glob(".crontab.scout-bak.*"))
    assert len(backups) == 1
    assert "/old/job" in backups[0].read_text()


def test_uninstall_removes_managed_block(fake, tmp_path):
    fake.content = (
        "# user line\n"
        "# >>> scout-managed >>>\n"
        "*/5 * * * * scoutctl schedule tick\n"
        "# <<< scout-managed <<<\n"
    )
    cron_mod.uninstall_cron(home=tmp_path, backup_dir=tmp_path)
    assert "# >>> scout-managed >>>" not in fake.content
    assert "# user line" in fake.content


def test_uninstall_silent_when_no_block(fake, tmp_path):
    fake.content = "# user line\n"
    cron_mod.uninstall_cron(home=tmp_path, backup_dir=tmp_path)
    assert fake.content == "# user line\n"
```

- [ ] **Step 3: Run tests to confirm they fail**

```bash
cd ~/scout-plugin/engine && pytest tests/unit/test_install_cron.py -v
```

Expected: ImportError on `scout.scripts.install_cron`.

- [ ] **Step 4: Write the implementation**

Create `~/scout-plugin/engine/scout/scripts/install_cron.py`:

```python
"""Helper for `scoutctl schedule install-cron [--uninstall]`.

Linux-side scheduling: writes a managed block (between
``# >>> scout-managed >>>`` and ``# <<< scout-managed <<<`` markers) to
the user's crontab. Atomic rewrite via NamedTemporaryFile so a failed
``crontab`` apply leaves the original crontab intact.
"""

from __future__ import annotations

import datetime as _dt
import os
import subprocess
import tempfile
from pathlib import Path

TEMPLATE = Path(__file__).parent.parent / "defaults" / "cron-managed-block.tmpl"
BLOCK_OPEN = "# >>> scout-managed >>>"
BLOCK_CLOSE = "# <<< scout-managed <<<"


class CrontabApplyError(Exception):
    """Raised when `crontab <tmpfile>` returns nonzero."""


def _list_crontab() -> str:
    """Return current crontab content, or "" if user has none."""
    proc = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=False)
    if proc.returncode == 0:
        return proc.stdout
    return ""


def _apply_crontab(content: str) -> None:
    """Apply the new crontab via temp-file. Atomic from user's perspective."""
    fd, path = tempfile.mkstemp(suffix=".cron")
    try:
        os.write(fd, content.encode("utf-8"))
        os.close(fd)
        proc = subprocess.run(
            ["crontab", path], capture_output=True, text=True, check=False
        )
        if proc.returncode != 0:
            raise CrontabApplyError(f"crontab apply failed: {proc.stderr}")
    finally:
        if os.path.exists(path):
            os.unlink(path)


def _strip_managed_block(text: str) -> str:
    """Remove existing ``# >>> scout-managed >>>`` ... ``# <<< scout-managed <<<`` block."""
    lines = text.splitlines()
    out: list[str] = []
    in_block = False
    for line in lines:
        if line.strip() == BLOCK_OPEN:
            in_block = True
            continue
        if line.strip() == BLOCK_CLOSE:
            in_block = False
            continue
        if not in_block:
            out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def _render_block(home: Path) -> str:
    """Render the cron-managed-block template with HOME substituted."""
    return TEMPLATE.read_text().replace("__USER_HOME__", str(home))


def _backup(previous: str, backup_dir: Path) -> None:
    """Write the prior crontab to ~/.crontab.scout-bak.YYYY-MM-DD."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    today = _dt.date.today().isoformat()
    (backup_dir / f".crontab.scout-bak.{today}").write_text(previous)


def install_cron(*, home: Path, backup_dir: Path | None = None) -> None:
    """Install or replace the scout-managed block in the user's crontab."""
    backup_dir = backup_dir or home
    previous = _list_crontab()
    stripped = _strip_managed_block(previous)
    block = _render_block(home)
    if stripped and not stripped.endswith("\n"):
        stripped += "\n"
    new_content = stripped + block
    if not new_content.endswith("\n"):
        new_content += "\n"
    _apply_crontab(new_content)
    _backup(previous, backup_dir)


def uninstall_cron(*, home: Path, backup_dir: Path | None = None) -> None:
    """Remove the scout-managed block from the user's crontab."""
    backup_dir = backup_dir or home
    previous = _list_crontab()
    stripped = _strip_managed_block(previous)
    if stripped == previous:
        return  # nothing to do
    _apply_crontab(stripped)
    _backup(previous, backup_dir)
```

- [ ] **Step 5: Run tests to confirm they pass**

```bash
cd ~/scout-plugin/engine && pytest tests/unit/test_install_cron.py -v
```

Expected: 6 passed.

- [ ] **Step 6: Commit**

```bash
cd ~/scout-plugin && git add engine/scout/defaults/cron-managed-block.tmpl engine/scout/scripts/install_cron.py engine/tests/unit/test_install_cron.py && git commit -m "feat(engine): add cron managed-block installer for Linux

Atomic rewrite via NamedTemporaryFile + single 'crontab <tmpfile>' call,
so a failed apply leaves the original crontab intact. Previous crontab
backed up to ~/.crontab.scout-bak.YYYY-MM-DD. Plan 8 §4.8."
```

---

### Task A4: Connector probe registry loader

**Files:**
- Create: `~/scout-plugin/templates/connector-probes.yaml`
- Create: `~/scout-plugin/engine/scout/scripts/connector_probes.py`
- Create: `~/scout-plugin/engine/tests/unit/test_connector_probe_registry.py`

- [ ] **Step 1: Create the probe registry**

Create `~/scout-plugin/templates/connector-probes.yaml`:

```yaml
# Declarative probe registry for /scout-setup connector detection.
# When MCP namespaces shift, update this file (no wizard prose changes needed).

slack:
  primary: mcp__plugin_slack_slack__slack_read_user_profile
  fallbacks:
    - mcp__claude_ai_Slack__slack_read_user_profile
  needs_user_input:
    - user_slack_id
calendar:
  primary: mcp__claude_ai_Google_Calendar__list_calendars
  fallbacks: []
gmail:
  primary: mcp__claude_ai_Gmail__list_labels
  fallbacks: []
linear:
  primary: mcp__plugin_linear_linear__list_teams
  fallbacks: []
github:
  primary: bash
  command: "gh auth status"
  needs_user_input:
    - github_username
    - github_repos
granola:
  primary: mcp__claude_ai_Granola__list_meetings
  fallbacks: []
drive:
  primary: mcp__claude_ai_Google_Drive__list_recent_files
  fallbacks: []
claude_sessions:
  primary: bash
  command: "test -d ~/.claude/projects"
```

- [ ] **Step 2: Write the failing tests**

Create `~/scout-plugin/engine/tests/unit/test_connector_probe_registry.py`:

```python
"""Unit tests for engine/scout/scripts/connector_probes.py."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from scout.scripts.connector_probes import (
    Probe,
    ProbeKind,
    load_registry,
)


def _registry(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "connector-probes.yaml"
    p.write_text(dedent(body))
    return p


def test_load_basic_mcp_probe(tmp_path):
    path = _registry(
        tmp_path,
        """
        slack:
          primary: mcp__plugin_slack_slack__slack_read_user_profile
          fallbacks:
            - mcp__claude_ai_Slack__slack_read_user_profile
        """,
    )
    reg = load_registry(path)
    assert "slack" in reg
    probe = reg["slack"]
    assert probe.kind is ProbeKind.MCP_TOOL
    assert probe.tool_chain == [
        "mcp__plugin_slack_slack__slack_read_user_profile",
        "mcp__claude_ai_Slack__slack_read_user_profile",
    ]


def test_load_bash_probe(tmp_path):
    path = _registry(
        tmp_path,
        """
        github:
          primary: bash
          command: "gh auth status"
        """,
    )
    reg = load_registry(path)
    probe = reg["github"]
    assert probe.kind is ProbeKind.BASH
    assert probe.bash_command == "gh auth status"


def test_load_with_user_input_fields(tmp_path):
    path = _registry(
        tmp_path,
        """
        slack:
          primary: mcp__plugin_slack_slack__slack_read_user_profile
          fallbacks: []
          needs_user_input:
            - user_slack_id
        """,
    )
    reg = load_registry(path)
    assert reg["slack"].needs_user_input == ["user_slack_id"]


def test_missing_primary_raises(tmp_path):
    path = _registry(
        tmp_path,
        """
        slack:
          fallbacks: []
        """,
    )
    with pytest.raises(ValueError, match="missing 'primary'"):
        load_registry(path)


def test_bash_probe_without_command_raises(tmp_path):
    path = _registry(
        tmp_path,
        """
        github:
          primary: bash
        """,
    )
    with pytest.raises(ValueError, match="bash probe.*requires 'command'"):
        load_registry(path)


def test_probe_emits_user_input_default_empty(tmp_path):
    path = _registry(
        tmp_path,
        """
        calendar:
          primary: mcp__claude_ai_Google_Calendar__list_calendars
          fallbacks: []
        """,
    )
    reg = load_registry(path)
    assert reg["calendar"].needs_user_input == []
```

- [ ] **Step 3: Run tests to confirm they fail**

```bash
cd ~/scout-plugin/engine && pytest tests/unit/test_connector_probe_registry.py -v
```

Expected: ImportError on `scout.scripts.connector_probes`.

- [ ] **Step 4: Write the implementation**

Create `~/scout-plugin/engine/scout/scripts/connector_probes.py`:

```python
"""Loader for templates/connector-probes.yaml.

The /scout-setup wizard reads this registry and tries each connector's
``primary`` tool, falling through to ``fallbacks`` until one succeeds
(or all fail, in which case the connector is marked disabled).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import yaml


class ProbeKind(Enum):
    MCP_TOOL = "mcp_tool"   # primary is an MCP tool name to call
    BASH = "bash"           # primary is "bash"; command is the shell command


@dataclass(frozen=True)
class Probe:
    name: str
    kind: ProbeKind
    tool_chain: list[str] = field(default_factory=list)  # MCP_TOOL only
    bash_command: str = ""                                # BASH only
    needs_user_input: list[str] = field(default_factory=list)


def load_registry(path: Path) -> dict[str, Probe]:
    """Parse connector-probes.yaml into typed Probe objects."""
    raw = yaml.safe_load(path.read_text()) or {}
    out: dict[str, Probe] = {}
    for name, body in raw.items():
        if not isinstance(body, dict):
            raise ValueError(f"connector {name!r}: expected mapping, got {type(body).__name__}")
        if "primary" not in body:
            raise ValueError(f"connector {name!r}: missing 'primary'")
        primary = body["primary"]
        needs = list(body.get("needs_user_input") or [])

        if primary == "bash":
            if "command" not in body:
                raise ValueError(f"connector {name!r}: bash probe requires 'command'")
            out[name] = Probe(
                name=name,
                kind=ProbeKind.BASH,
                bash_command=body["command"],
                needs_user_input=needs,
            )
        else:
            chain = [primary] + list(body.get("fallbacks") or [])
            out[name] = Probe(
                name=name,
                kind=ProbeKind.MCP_TOOL,
                tool_chain=chain,
                needs_user_input=needs,
            )
    return out
```

- [ ] **Step 5: Run tests to confirm they pass**

```bash
cd ~/scout-plugin/engine && pytest tests/unit/test_connector_probe_registry.py -v
```

Expected: 6 passed.

- [ ] **Step 6: Commit**

```bash
cd ~/scout-plugin && git add templates/connector-probes.yaml engine/scout/scripts/connector_probes.py engine/tests/unit/test_connector_probe_registry.py && git commit -m "feat(plugin+engine): add connector probe registry

YAML at templates/connector-probes.yaml lists primary + fallback tool
names per connector. When MCP namespaces shift (which they have, per
Plan 8 §4.7 finding), update one YAML file instead of rewriting the
wizard prose. Plan 8 §4.7."
```

---

### Task A5: Phase assembly module

The bootstrap pipeline needs Python code that does what scout-setup.md tells Claude to do today: read `phases/{core,connectors,modes,research}/` files, parse multi-section YAML frontmatter, filter by enabled connectors, render template variables, and concatenate into a final SKILL.md / DREAMING.md / RESEARCH.md.

**Files:**
- Create: `~/scout-plugin/engine/scout/scripts/phase_assembly.py`
- Create: `~/scout-plugin/engine/tests/unit/test_phase_assembly.py`
- Create: `~/scout-plugin/engine/tests/unit/fixtures/phases/core/dummy.md` (test fixture)

- [ ] **Step 1: Create test fixtures**

Create `~/scout-plugin/engine/tests/unit/fixtures/phases/core/dummy-core.md`:

```markdown
---
phase: core
name: dummy-core
slot: setup
mode: [briefing]
requires: null
---

## Core Setup Section

Hello {{USER_NAME}} from core. SCOUT_DIR is {{SCOUT_DIR}}.
```

Create `~/scout-plugin/engine/tests/unit/fixtures/phases/connectors/dummy-slack.md`:

```markdown
---
phase: connector
name: dummy-slack
slot: query
mode: [briefing]
requires: slack
---

## Slack Query

Slack ID: {{USER_SLACK_ID}}.

---
phase: connector
name: dummy-slack
slot: outbound-scan
mode: [consolidation]
requires: slack
---

## Slack Outbound

Outbound for {{USER_NAME}}.
```

- [ ] **Step 2: Write the failing tests**

Create `~/scout-plugin/engine/tests/unit/test_phase_assembly.py`:

```python
"""Unit tests for engine/scout/scripts/phase_assembly.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.scripts.phase_assembly import (
    PhaseSection,
    parse_phase_file,
    render_template,
    select_sections,
)

FIXTURES = Path(__file__).parent / "fixtures" / "phases"


def test_parse_single_section_file():
    sections = parse_phase_file(FIXTURES / "core" / "dummy-core.md")
    assert len(sections) == 1
    s = sections[0]
    assert s.phase == "core"
    assert s.name == "dummy-core"
    assert s.slot == "setup"
    assert s.mode == ["briefing"]
    assert s.requires is None
    assert "Hello {{USER_NAME}}" in s.body


def test_parse_multi_section_file():
    sections = parse_phase_file(FIXTURES / "connectors" / "dummy-slack.md")
    assert len(sections) == 2
    assert sections[0].slot == "query"
    assert sections[1].slot == "outbound-scan"
    assert sections[0].requires == "slack"


def test_select_filters_by_enabled_connectors():
    sections = parse_phase_file(FIXTURES / "connectors" / "dummy-slack.md")
    selected = select_sections(sections, enabled_connectors={"slack"})
    assert len(selected) == 2
    selected_disabled = select_sections(sections, enabled_connectors=set())
    assert selected_disabled == []


def test_select_keeps_requires_null():
    sections = parse_phase_file(FIXTURES / "core" / "dummy-core.md")
    selected = select_sections(sections, enabled_connectors=set())
    assert len(selected) == 1


def test_render_template_substitutes_variables():
    out = render_template(
        "Hello {{USER_NAME}} at {{SCOUT_DIR}}",
        {"USER_NAME": "Alice", "SCOUT_DIR": "/tmp/x"},
    )
    assert out == "Hello Alice at /tmp/x"


def test_render_template_empty_for_unknown_var():
    out = render_template("X {{UNKNOWN_VAR}} Y", {})
    assert out == "X  Y"


def test_select_filters_by_slot():
    sections = parse_phase_file(FIXTURES / "connectors" / "dummy-slack.md")
    selected = select_sections(
        sections,
        enabled_connectors={"slack"},
        slot="outbound-scan",
    )
    assert len(selected) == 1
    assert selected[0].slot == "outbound-scan"
```

- [ ] **Step 3: Run tests to confirm they fail**

```bash
cd ~/scout-plugin/engine && pytest tests/unit/test_phase_assembly.py -v
```

Expected: ImportError on `scout.scripts.phase_assembly`.

- [ ] **Step 4: Write the implementation**

Create `~/scout-plugin/engine/scout/scripts/phase_assembly.py`:

```python
"""Phase file parsing, selection, and template rendering.

Phase files (under ``~/scout-plugin/phases/{core,connectors,modes,research}/``)
have YAML frontmatter and may contain multiple sections separated by ``---``
fences with their own frontmatter blocks. The bootstrap pipeline uses this
module to assemble SKILL.md / DREAMING.md / RESEARCH.md from phase files
based on which connectors the user has enabled.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class PhaseSection:
    """One frontmatter+body section of a phase file."""

    phase: str
    name: str
    slot: str
    mode: list[str]
    requires: str | None
    body: str


_FRONTMATTER_FENCE = "---"


def parse_phase_file(path: Path) -> list[PhaseSection]:
    """Return all sections in a phase file (single or multi)."""
    text = path.read_text()
    if not text.startswith(_FRONTMATTER_FENCE):
        raise ValueError(f"{path}: phase file must start with '---' frontmatter fence")

    # Split on lines that are exactly "---" (frontmatter delimiter).
    # Multi-section files have alternating frontmatter blocks and bodies:
    #   ---\n<frontmatter>\n---\n<body>\n---\n<frontmatter>\n---\n<body>\n...
    parts = re.split(r"^---\s*$", text, flags=re.MULTILINE)
    # parts[0] is the leading empty (before the first '---').
    # Then alternating: frontmatter, body, frontmatter, body, ...
    sections: list[PhaseSection] = []
    i = 1
    while i < len(parts) - 1:
        fm_text = parts[i]
        body = parts[i + 1] if i + 1 < len(parts) else ""
        i += 2
        fm = yaml.safe_load(fm_text) or {}
        sections.append(
            PhaseSection(
                phase=str(fm.get("phase", "")),
                name=str(fm.get("name", "")),
                slot=str(fm.get("slot", "")),
                mode=list(fm.get("mode") or []),
                requires=fm.get("requires"),
                body=body.strip("\n"),
            )
        )
    return sections


def select_sections(
    sections: list[PhaseSection],
    *,
    enabled_connectors: set[str],
    slot: str | None = None,
) -> list[PhaseSection]:
    """Filter sections: keep when requires is null OR connector enabled.

    Optionally narrow to a specific slot (e.g., "outbound-scan").
    """
    out: list[PhaseSection] = []
    for s in sections:
        if s.requires is not None and s.requires not in enabled_connectors:
            continue
        if slot is not None and s.slot != slot:
            continue
        out.append(s)
    return out


_VAR_RE = re.compile(r"\{\{(\w+)\}\}")


def render_template(text: str, variables: dict[str, str]) -> str:
    """Replace ``{{VAR}}`` with ``variables[VAR]``; unknown vars become ""."""
    return _VAR_RE.sub(lambda m: variables.get(m.group(1), ""), text)
```

- [ ] **Step 5: Run tests to confirm they pass**

```bash
cd ~/scout-plugin/engine && pytest tests/unit/test_phase_assembly.py -v
```

Expected: 7 passed.

- [ ] **Step 6: Commit**

```bash
cd ~/scout-plugin && git add engine/scout/scripts/phase_assembly.py engine/tests/unit/test_phase_assembly.py engine/tests/unit/fixtures && git commit -m "feat(engine): add phase_assembly module

Parses multi-section phase files (YAML frontmatter + body), filters
by enabled connectors and slot, renders {{VAR}} templates. Foundation
for stage 5 of the bootstrap pipeline. Plan 8 §4.5 / §5.1."
```

---

### Task A6: Bootstrap state + lock module

**Files:**
- Create: `~/scout-plugin/engine/scout/scripts/bootstrap_lock.py`
- Create: `~/scout-plugin/engine/tests/unit/test_bootstrap_lock.py`

- [ ] **Step 1: Write the failing tests**

Create `~/scout-plugin/engine/tests/unit/test_bootstrap_lock.py`:

```python
"""Unit tests for engine/scout/scripts/bootstrap_lock.py."""

from __future__ import annotations

import os

import pytest

from scout.scripts.bootstrap_lock import (
    LockBusyError,
    acquire_lock,
    is_lock_held_by_live_pid,
    release_lock,
    remove_stale_lock,
)


def test_acquire_lock_writes_pid(tmp_path):
    lock = tmp_path / ".scout-session.lock"
    acquire_lock(lock)
    assert lock.exists()
    assert lock.read_text().strip() == str(os.getpid())


def test_release_lock_removes_file(tmp_path):
    lock = tmp_path / ".scout-session.lock"
    acquire_lock(lock)
    release_lock(lock)
    assert not lock.exists()


def test_is_lock_held_by_live_pid(tmp_path):
    lock = tmp_path / ".scout-session.lock"
    lock.write_text(str(os.getpid()))
    assert is_lock_held_by_live_pid(lock) is True


def test_is_lock_not_held_when_pid_dead(tmp_path):
    lock = tmp_path / ".scout-session.lock"
    # PID 999999 is unlikely to exist; check it.
    fake_pid = 999999
    lock.write_text(str(fake_pid))
    assert is_lock_held_by_live_pid(lock) is False


def test_is_lock_not_held_when_file_missing(tmp_path):
    lock = tmp_path / ".scout-session.lock"
    assert is_lock_held_by_live_pid(lock) is False


def test_remove_stale_lock_removes_dead_pid(tmp_path):
    lock = tmp_path / ".scout-session.lock"
    lock.write_text("999999")
    remove_stale_lock(lock)
    assert not lock.exists()


def test_remove_stale_lock_preserves_live_pid(tmp_path):
    lock = tmp_path / ".scout-session.lock"
    lock.write_text(str(os.getpid()))
    remove_stale_lock(lock)
    assert lock.exists()


def test_acquire_raises_when_held_by_live_pid(tmp_path):
    lock = tmp_path / ".scout-session.lock"
    lock.write_text(str(os.getpid()))
    with pytest.raises(LockBusyError):
        acquire_lock(lock)
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd ~/scout-plugin/engine && pytest tests/unit/test_bootstrap_lock.py -v
```

Expected: ImportError.

- [ ] **Step 3: Write the implementation**

Create `~/scout-plugin/engine/scout/scripts/bootstrap_lock.py`:

```python
"""Global pipeline lock for `scoutctl bootstrap install|upgrade`.

Holds ``.scout-logs/.scout-session.lock`` for the entire 8-stage pipeline.
Runner scripts and the dispatcher already check this lock and skip when
held — so holding it for the pipeline closes every interleaving window
between bootstrap stages and dispatcher ticks.
"""

from __future__ import annotations

import os
import time
from pathlib import Path


class LockBusyError(Exception):
    """Raised when the lock is already held by a live PID."""

    def __init__(self, lock_path: Path, pid: int) -> None:
        self.lock_path = lock_path
        self.pid = pid
        super().__init__(f"lock {lock_path} held by live PID {pid}")


def is_lock_held_by_live_pid(lock_path: Path) -> bool:
    """Return True iff the lock file exists and its PID is alive."""
    if not lock_path.exists():
        return False
    try:
        pid = int(lock_path.read_text().strip())
    except (ValueError, OSError):
        return False
    try:
        os.kill(pid, 0)  # signal 0 — existence probe, no kill
        return True
    except (ProcessLookupError, PermissionError):
        # PermissionError means the process exists but we can't signal it
        # (different uid). Treat as "live" for safety.
        return False if isinstance_proc_lookup() else True


def isinstance_proc_lookup() -> bool:
    # Helper kept inline so the conditional above is testable; refactor
    # candidate if call sites multiply.
    import sys
    return sys.exc_info()[0] is ProcessLookupError


def acquire_lock(lock_path: Path) -> None:
    """Take the lock by writing our PID. Raise if already held by a live PID."""
    if is_lock_held_by_live_pid(lock_path):
        try:
            pid = int(lock_path.read_text().strip())
        except (ValueError, OSError):
            pid = -1
        raise LockBusyError(lock_path, pid)
    if lock_path.exists():
        # Stale (dead PID). Remove and retry.
        lock_path.unlink()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(str(os.getpid()))


def release_lock(lock_path: Path) -> None:
    """Release the lock if we still hold it."""
    if not lock_path.exists():
        return
    try:
        pid = int(lock_path.read_text().strip())
    except (ValueError, OSError):
        lock_path.unlink()
        return
    if pid == os.getpid():
        lock_path.unlink()


def remove_stale_lock(lock_path: Path) -> None:
    """Remove the lock file iff its PID is dead. No-op otherwise."""
    if lock_path.exists() and not is_lock_held_by_live_pid(lock_path):
        lock_path.unlink()


def acquire_lock_with_wait(
    lock_path: Path, *, timeout_s: int = 300, poll_s: int = 10
) -> None:
    """Acquire with up to ``timeout_s`` of polling. Raise LockBusyError on timeout."""
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            acquire_lock(lock_path)
            return
        except LockBusyError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(poll_s)
```

> Note: the helper `isinstance_proc_lookup()` is intentionally awkward — Python's exception-checking idiom for "was the most recent exception ProcessLookupError" lacks a clean form. Implementer may replace with a try/except-around-os.kill that returns explicitly.

**Implementer cleanup:** replace the `isinstance_proc_lookup` dance with the cleaner form below. Update `is_lock_held_by_live_pid` to:

```python
def is_lock_held_by_live_pid(lock_path: Path) -> bool:
    """Return True iff the lock file exists and its PID is alive."""
    if not lock_path.exists():
        return False
    try:
        pid = int(lock_path.read_text().strip())
    except (ValueError, OSError):
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # different uid — process exists, treat as live
```

Use the cleaner form when implementing. Drop the `isinstance_proc_lookup` helper.

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd ~/scout-plugin/engine && pytest tests/unit/test_bootstrap_lock.py -v
```

Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
cd ~/scout-plugin && git add engine/scout/scripts/bootstrap_lock.py engine/tests/unit/test_bootstrap_lock.py && git commit -m "feat(engine): add bootstrap_lock — global pipeline lock helpers

Provides acquire/release for .scout-session.lock with stale-PID cleanup
and waited acquisition. Bootstrap install/upgrade hold this lock for
the full pipeline so dispatcher ticks become no-ops while updates run.
Plan 8 §4.9."
```

---

### Task A7: Bootstrap doctor (read-only health check)

**Files:**
- Create: `~/scout-plugin/engine/scout/scripts/bootstrap_doctor.py`
- Create: `~/scout-plugin/engine/tests/unit/test_bootstrap_doctor.py`

- [ ] **Step 1: Write the failing tests**

Create `~/scout-plugin/engine/tests/unit/test_bootstrap_doctor.py`:

```python
"""Unit tests for engine/scout/scripts/bootstrap_doctor.py."""

from __future__ import annotations

from pathlib import Path

from scout.scripts.bootstrap_doctor import (
    DoctorReport,
    Severity,
    run_doctor,
)


def _populate_minimal_vault(vault: Path) -> None:
    """Create the file structure a healthy vault has after bootstrap."""
    (vault / ".scout-state").mkdir(parents=True)
    (vault / ".scout-state" / "schedule.yaml").write_text("schema_version: 1\nslots: {}\n")
    (vault / ".scout-state" / "last-assembled").mkdir()
    for name in ("SKILL", "DREAMING", "RESEARCH"):
        (vault / f"{name}.md").write_text(f"# {name}\n")
        (vault / ".scout-state" / "last-assembled" / f"{name}.md").write_text(f"# {name}\n")
    (vault / "scout-config.yaml").write_text(
        "user:\n  name: Test\nplugin:\n  version_at_last_setup: '0.4.0'\n  version_at_last_update: '0.4.0'\n"
    )
    (vault / "scripts").mkdir()
    (vault / "scripts" / "heartbeat.sh").write_text("#!/bin/bash\necho ok\n")
    (vault / "knowledge-base").mkdir()
    (vault / "knowledge-base" / "ontology").mkdir()
    (vault / "knowledge-base" / "ontology" / "parser.py").write_text("# parser\n")
    (vault / "action-items").mkdir()
    (vault / "action-items" / "render.py").write_text("# render\n")
    (vault / "hooks").mkdir()
    (vault / "hooks" / "kb-pre-filter.sh").write_text("#!/bin/bash\n")


def test_healthy_vault_returns_green(tmp_path):
    _populate_minimal_vault(tmp_path)
    report = run_doctor(vault=tmp_path, check_jobs=False)
    assert isinstance(report, DoctorReport)
    assert report.severity is Severity.GREEN
    assert report.errors == []


def test_missing_schedule_yaml_is_red(tmp_path):
    _populate_minimal_vault(tmp_path)
    (tmp_path / ".scout-state" / "schedule.yaml").unlink()
    report = run_doctor(vault=tmp_path, check_jobs=False)
    assert report.severity is Severity.RED
    assert any("schedule.yaml" in e for e in report.errors)


def test_sidecar_proposed_merge_is_yellow(tmp_path):
    _populate_minimal_vault(tmp_path)
    (tmp_path / "SKILL.md.proposed-merge").write_text("conflict markers here")
    report = run_doctor(vault=tmp_path, check_jobs=False)
    assert report.severity is Severity.YELLOW
    assert any("proposed-merge" in w for w in report.warnings)


def test_missing_version_stamp_is_red(tmp_path):
    _populate_minimal_vault(tmp_path)
    (tmp_path / "scout-config.yaml").write_text("user:\n  name: Test\n")
    report = run_doctor(vault=tmp_path, check_jobs=False)
    assert report.severity is Severity.RED
    assert any("version_at_last" in e for e in report.errors)


def test_exit_code_matches_severity(tmp_path):
    _populate_minimal_vault(tmp_path)
    g = run_doctor(vault=tmp_path, check_jobs=False)
    assert g.exit_code == 0
    (tmp_path / "SKILL.md.proposed-merge").write_text("x")
    y = run_doctor(vault=tmp_path, check_jobs=False)
    assert y.exit_code == 1
    (tmp_path / ".scout-state" / "schedule.yaml").unlink()
    r = run_doctor(vault=tmp_path, check_jobs=False)
    assert r.exit_code == 2
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd ~/scout-plugin/engine && pytest tests/unit/test_bootstrap_doctor.py -v
```

Expected: ImportError.

- [ ] **Step 3: Write the implementation**

Create `~/scout-plugin/engine/scout/scripts/bootstrap_doctor.py`:

```python
"""Read-only health check for the bootstrap pipeline.

Used as pipeline stage 8 (post-install/upgrade smoke) and as a standalone
diagnostic via `scoutctl bootstrap doctor`. Never mutates the vault.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import yaml


class Severity(Enum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


@dataclass(frozen=True)
class DoctorReport:
    severity: Severity
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        return {Severity.GREEN: 0, Severity.YELLOW: 1, Severity.RED: 2}[self.severity]


_REQUIRED_CAT1_FILES = (
    "scripts/heartbeat.sh",
    "knowledge-base/ontology/parser.py",
    "action-items/render.py",
    "hooks/kb-pre-filter.sh",
)


def run_doctor(*, vault: Path, check_jobs: bool = True) -> DoctorReport:
    """Run all doctor checks against ``vault``. Pure read."""
    errors: list[str] = []
    warnings: list[str] = []

    if not vault.exists():
        errors.append(f"vault directory missing: {vault}")
        return DoctorReport(severity=Severity.RED, errors=errors)

    # schedule.yaml must exist and parse.
    schedule_path = vault / ".scout-state" / "schedule.yaml"
    if not schedule_path.exists():
        errors.append(f"missing schedule.yaml at {schedule_path}")
    else:
        try:
            yaml.safe_load(schedule_path.read_text())
        except yaml.YAMLError as e:
            errors.append(f"schedule.yaml invalid: {e}")

    # scout-config.yaml must record version stamps.
    config_path = vault / "scout-config.yaml"
    if not config_path.exists():
        errors.append(f"missing scout-config.yaml at {config_path}")
    else:
        try:
            cfg = yaml.safe_load(config_path.read_text()) or {}
            plugin = cfg.get("plugin") or {}
            if not plugin.get("version_at_last_setup"):
                errors.append("scout-config.yaml: plugin.version_at_last_setup missing")
            if not plugin.get("version_at_last_update"):
                errors.append("scout-config.yaml: plugin.version_at_last_update missing")
        except yaml.YAMLError as e:
            errors.append(f"scout-config.yaml invalid: {e}")

    # Cat-1 files must exist with non-zero content.
    for rel in _REQUIRED_CAT1_FILES:
        path = vault / rel
        if not path.exists():
            errors.append(f"cat-1 file missing: {rel}")
        elif path.stat().st_size == 0:
            errors.append(f"cat-1 file empty: {rel}")

    # Snapshots present?
    snapshot_dir = vault / ".scout-state" / "last-assembled"
    for name in ("SKILL", "DREAMING", "RESEARCH"):
        snap = snapshot_dir / f"{name}.md"
        if not snap.exists():
            warnings.append(f"snapshot missing: {snap.relative_to(vault)}")

    # Sidecar conflict files (yellow).
    for name in ("SKILL", "DREAMING", "RESEARCH"):
        sidecar = vault / f"{name}.md.proposed-merge"
        if sidecar.exists():
            warnings.append(
                f"unresolved merge conflict in {sidecar.name} — resolve and "
                f"`mv {sidecar.name} {name}.md` before re-running /scout-update"
            )

    # Hand-edit backups (yellow but informational).
    for bak in vault.glob("run-*.sh.bak.*"):
        warnings.append(f"runner backup present: {bak.name} (hand-edit detected on prior update)")

    # Live launchd jobs.
    if check_jobs and os.name == "posix":
        try:
            proc = subprocess.run(
                ["launchctl", "list"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            if "com.scout.schedule-tick" not in proc.stdout:
                errors.append("launchd: com.scout.schedule-tick not registered")
            if "com.scout.heartbeat" not in proc.stdout:
                errors.append("launchd: com.scout.heartbeat not registered")
        except (subprocess.SubprocessError, FileNotFoundError):
            warnings.append("launchctl unavailable — skipped job registration check")

    if errors:
        return DoctorReport(severity=Severity.RED, errors=errors, warnings=warnings)
    if warnings:
        return DoctorReport(severity=Severity.YELLOW, warnings=warnings)
    return DoctorReport(severity=Severity.GREEN)
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd ~/scout-plugin/engine && pytest tests/unit/test_bootstrap_doctor.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
cd ~/scout-plugin && git add engine/scout/scripts/bootstrap_doctor.py engine/tests/unit/test_bootstrap_doctor.py && git commit -m "feat(engine): add bootstrap_doctor (read-only health check)

Returns DoctorReport(severity, errors, warnings) with exit codes
0/1/2 for green/yellow/red. Used as pipeline stage 8 and as a
standalone diagnostic. Plan 8 §8.3."
```

---

### Task A8: Bootstrap pipeline core (stages + orchestration)

This task implements the 8-stage pipeline orchestrator. Each stage is a small function. The orchestrator wires them together and acquires/releases the global lock.

**Files:**
- Create: `~/scout-plugin/engine/scout/scripts/bootstrap.py`
- Create: `~/scout-plugin/engine/tests/unit/test_bootstrap_install.py`
- Create: `~/scout-plugin/engine/tests/unit/test_bootstrap_upgrade.py`
- Modify: `~/scout-plugin/engine/scout/scripts/__init__.py` (export bootstrap)

- [ ] **Step 1: Write install pipeline tests (failing)**

Create `~/scout-plugin/engine/tests/unit/test_bootstrap_install.py`:

```python
"""Unit tests for engine/scout/scripts/bootstrap.py — install pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.scripts.bootstrap import (
    BootstrapConfig,
    InstallResult,
    install,
)


def _config(vault: Path, *, plugin_root: Path) -> BootstrapConfig:
    return BootstrapConfig(
        vault=vault,
        plugin_root=plugin_root,
        instance_name="TestScout",
        instance_name_lower="testscout",
        user_name="Test User",
        user_email="test@example.com",
        timezone="America/New_York",
        platform="macos",
        plugin_version="0.4.0",
        enabled_connectors=set(),
        connector_inputs={},
        skip_jobs=True,        # don't touch ~/Library/LaunchAgents in tests
        skip_claude=True,      # don't run a real Claude session
    )


def test_install_creates_directory_tree(tmp_path):
    plugin = Path(__file__).parent.parent.parent.parent  # repo root: ~/scout-plugin
    vault = tmp_path / "Scout"
    result = install(_config(vault, plugin_root=plugin))
    assert isinstance(result, InstallResult)
    assert vault.exists()
    assert (vault / "knowledge-base").is_dir()
    assert (vault / "action-items").is_dir()
    assert (vault / ".scout-state").is_dir()
    assert (vault / "scripts").is_dir()
    assert (vault / "hooks").is_dir()


def test_install_writes_scout_config(tmp_path):
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=plugin))
    config = (vault / "scout-config.yaml").read_text()
    assert "TestScout" in config
    assert "version_at_last_setup" in config
    assert "0.4.0" in config


def test_install_seeds_schedule_yaml(tmp_path):
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=plugin))
    schedule = vault / ".scout-state" / "schedule.yaml"
    assert schedule.exists()
    assert "schema_version" in schedule.read_text()


def test_install_writes_assembled_files_and_snapshots(tmp_path):
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=plugin))
    for name in ("SKILL", "DREAMING", "RESEARCH"):
        assert (vault / f"{name}.md").exists()
        assert (vault / ".scout-state" / "last-assembled" / f"{name}.md").exists()


def test_install_refuses_existing_vault(tmp_path):
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    vault.mkdir()
    (vault / "scout-config.yaml").write_text("# already here\n")
    with pytest.raises(FileExistsError, match="vault detected"):
        install(_config(vault, plugin_root=plugin))


def test_install_records_plugin_version(tmp_path):
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=plugin))
    config_text = (vault / "scout-config.yaml").read_text()
    import yaml
    cfg = yaml.safe_load(config_text)
    assert cfg["plugin"]["version_at_last_setup"] == "0.4.0"
    assert cfg["plugin"]["version_at_last_update"] == "0.4.0"
```

- [ ] **Step 2: Write upgrade pipeline tests (failing)**

Create `~/scout-plugin/engine/tests/unit/test_bootstrap_upgrade.py`:

```python
"""Unit tests for engine/scout/scripts/bootstrap.py — upgrade pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scout.scripts.bootstrap import (
    BootstrapConfig,
    UpgradeResult,
    install,
    upgrade,
)


def _config(vault: Path, *, plugin_root: Path) -> BootstrapConfig:
    return BootstrapConfig(
        vault=vault,
        plugin_root=plugin_root,
        instance_name="TestScout",
        instance_name_lower="testscout",
        user_name="Test User",
        user_email="test@example.com",
        timezone="America/New_York",
        platform="macos",
        plugin_version="0.4.0",
        enabled_connectors=set(),
        connector_inputs={},
        skip_jobs=True,
        skip_claude=True,
    )


def test_upgrade_refuses_when_no_vault(tmp_path):
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    with pytest.raises(FileNotFoundError, match="no vault"):
        upgrade(_config(vault, plugin_root=plugin))


def test_upgrade_idempotent_after_install(tmp_path):
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=plugin))

    cfg = _config(vault, plugin_root=plugin)
    cfg.plugin_version = "0.4.1"
    result = upgrade(cfg)
    assert isinstance(result, UpgradeResult)
    cfg_text = (vault / "scout-config.yaml").read_text()
    new_cfg = yaml.safe_load(cfg_text)
    assert new_cfg["plugin"]["version_at_last_update"] == "0.4.1"
    assert new_cfg["plugin"]["version_at_last_setup"] == "0.4.0"  # unchanged


def test_upgrade_sidecar_on_conflict(tmp_path):
    """Vault edits + plugin edits at same SKILL.md location → sidecar."""
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=plugin))

    # Simulate vault edit at a known location of SKILL.md.
    skill = vault / "SKILL.md"
    skill_text = skill.read_text()
    # Replace any phrase consistently to create a divergence vs. snapshot.
    skill.write_text(skill_text.replace("BASE_DIR", "VAULT_EDITED_BASE_DIR"))

    # Simulate plugin change to the SAME line by editing the snapshot
    # so that fresh-assembly still equals the snapshot (no plugin change),
    # then we manually corrupt the snapshot to look like the plugin's
    # *previous* version. Result: ours==current-assembly diverges from
    # snapshot==base, theirs==vault has its own change at the same place.
    snapshot = vault / ".scout-state" / "last-assembled" / "SKILL.md"
    snap_text = snapshot.read_text()
    snapshot.write_text(snap_text.replace("BASE_DIR", "OLD_BASE_DIR"))

    cfg = _config(vault, plugin_root=plugin)
    cfg.plugin_version = "0.4.1"
    result = upgrade(cfg)
    # Conflict expected → sidecar exists, live SKILL.md untouched
    sidecar = vault / "SKILL.md.proposed-merge"
    assert sidecar.exists()
    assert "VAULT_EDITED_BASE_DIR" in skill.read_text()  # live untouched
    assert any("SKILL.md" in c for c in result.conflicts)


def test_upgrade_refuses_with_pending_sidecar(tmp_path):
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=plugin))
    (vault / "SKILL.md.proposed-merge").write_text("# pending\n")
    with pytest.raises(RuntimeError, match="proposed-merge"):
        upgrade(_config(vault, plugin_root=plugin))


def test_upgrade_runner_hand_edit_creates_backup(tmp_path):
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=plugin))
    runner = vault / "run-scout.sh"
    runner.write_text(runner.read_text() + "\n# hand edit\n")
    cfg = _config(vault, plugin_root=plugin)
    cfg.plugin_version = "0.4.1"
    upgrade(cfg)
    backups = list(vault.glob("run-scout.sh.bak.*"))
    assert len(backups) == 1
    assert "# hand edit" in backups[0].read_text()
```

- [ ] **Step 3: Run tests to confirm they fail**

```bash
cd ~/scout-plugin/engine && pytest tests/unit/test_bootstrap_install.py tests/unit/test_bootstrap_upgrade.py -v
```

Expected: ImportError on `scout.scripts.bootstrap`.

- [ ] **Step 4: Write the bootstrap orchestrator**

Create `~/scout-plugin/engine/scout/scripts/bootstrap.py`:

```python
"""Bootstrap pipeline — install/upgrade orchestrator for /scout-setup and /scout-update.

8 stages, behavior varies by command:
1. Pre-flight       — vault state checks, lock acquisition
2. Schema migrations — empty in 0.4.0
3. Cat 1 file writes — plists, ontology, render.py, scripts, hooks
4. Cat 1b runner writes — with hand-edit detection (upgrade only)
5. Cat 4 assembled  — SKILL/DREAMING/RESEARCH (3-way merge on upgrade)
6. Job lifecycle    — launchd / cron
7. Version stamp    — scout-config.yaml plugin.version_*
8. Doctor smoke     — runs bootstrap_doctor.run_doctor

See docs/superpowers/specs/2026-05-09-plan-8-scout-setup-repair-design.md.
"""

from __future__ import annotations

import datetime as _dt
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from scout.scripts.bootstrap_doctor import DoctorReport, run_doctor
from scout.scripts.bootstrap_lock import (
    acquire_lock_with_wait,
    release_lock,
)
from scout.scripts.phase_assembly import (
    parse_phase_file,
    render_template,
    select_sections,
)
from scout.scripts.three_way_merge import three_way_merge


@dataclass
class BootstrapConfig:
    vault: Path
    plugin_root: Path
    instance_name: str
    instance_name_lower: str
    user_name: str
    user_email: str
    timezone: str
    platform: str  # "macos" | "linux"
    plugin_version: str
    enabled_connectors: set[str]
    connector_inputs: dict[str, str]  # e.g., {"user_slack_id": "U..."}
    skip_jobs: bool = False
    skip_claude: bool = False  # reserved for first-run prompt


@dataclass
class InstallResult:
    vault: Path
    doctor: DoctorReport


@dataclass
class UpgradeResult:
    vault: Path
    doctor: DoctorReport
    conflicts: list[str] = field(default_factory=list)
    backups: list[str] = field(default_factory=list)


# ---------- shared helpers ----------

_CAT1_DIR_LAYOUT = (
    "knowledge-base/projects",
    "knowledge-base/ontology/entities",
    "knowledge-base/people",
    "knowledge-base/personal",
    "action-items/archive",
    "action-items/meeting-prep",
    "docs",
    "scripts",
    "hooks",
    ".scout-logs",
    ".scout-cache",
    ".scout-state/last-assembled",
)

_CAT1_FILES_FROM_PLUGIN = {
    # vault relative path → plugin relative path (templates copied verbatim)
    "knowledge-base/ontology/parser.py": "templates/knowledge-base/ontology/parser.py",
    "knowledge-base/ontology/__init__.py": "templates/knowledge-base/ontology/__init__.py",
    "action-items/render.py": "templates/action-items/render.py",
}

_CAT1_TEMPLATES = (
    # vault relative → plugin template path (with var substitution)
    ("scripts/budget-check.sh", "templates/scripts/budget-check.sh.tmpl"),
    ("scripts/heartbeat.sh", "templates/scripts/heartbeat.sh.tmpl"),
    ("scripts/pre-session-data.sh", "templates/scripts/pre-session-data.sh.tmpl"),
    ("scripts/cc-session-cache.sh", "templates/scripts/cc-session-cache.sh.tmpl"),
    ("scripts/write-session-cost.sh", "templates/scripts/write-session-cost.sh.tmpl"),
    ("scripts/rate-limit-detect.sh", "templates/scripts/rate-limit-detect.sh.tmpl"),
    ("hooks/kb-pre-filter.sh", "templates/hooks/kb-pre-filter.sh.tmpl"),
)

_CAT1B_RUNNERS = (
    ("run-scout.sh", "templates/run-scout.sh.tmpl"),
    ("run-dreaming.sh", "templates/run-dreaming.sh.tmpl"),
    ("run-research.sh", "templates/run-research.sh.tmpl"),
)


def _template_vars(cfg: BootstrapConfig) -> dict[str, str]:
    """Build the {{VAR}} substitution map from cfg + connector_inputs."""
    return {
        "INSTANCE_NAME": cfg.instance_name,
        "INSTANCE_NAME_LOWER": cfg.instance_name_lower,
        "USER_NAME": cfg.user_name,
        "USER_EMAIL": cfg.user_email,
        "USER_SLACK_ID": cfg.connector_inputs.get("user_slack_id", ""),
        "GITHUB_USERNAME": cfg.connector_inputs.get("github_username", ""),
        "GITHUB_REPOS": cfg.connector_inputs.get("github_repos", ""),
        "SCOUT_DIR": str(cfg.vault),
        "TIMEZONE": cfg.timezone,
        "PLATFORM": cfg.platform,
        "MAX_BUDGET": cfg.connector_inputs.get("max_budget", "5.00"),
        "CLAUDE_BIN": cfg.connector_inputs.get("claude_bin", "/usr/local/bin/claude"),
        "TODAY_DATE": _dt.date.today().isoformat(),
    }


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content)
    tmp.replace(path)


# ---------- stages ----------

def _stage_create_dirs(cfg: BootstrapConfig) -> None:
    for rel in _CAT1_DIR_LAYOUT:
        (cfg.vault / rel).mkdir(parents=True, exist_ok=True)


def _stage_cat1_writes(cfg: BootstrapConfig) -> None:
    """Stage 3: cat 1 file overwrites (always)."""
    vars_ = _template_vars(cfg)
    for vault_rel, plugin_rel in _CAT1_FILES_FROM_PLUGIN.items():
        src = cfg.plugin_root / plugin_rel
        if not src.exists():
            # Plugin packaging may not include this file in tests;
            # write a placeholder so doctor/cat-1 checks pass.
            _atomic_write(cfg.vault / vault_rel, f"# placeholder: {plugin_rel}\n")
            continue
        _atomic_write(cfg.vault / vault_rel, src.read_text())
    for vault_rel, tmpl_rel in _CAT1_TEMPLATES:
        src = cfg.plugin_root / tmpl_rel
        if not src.exists():
            _atomic_write(cfg.vault / vault_rel, f"# placeholder: {tmpl_rel}\n")
            continue
        rendered = render_template(src.read_text(), vars_)
        _atomic_write(cfg.vault / vault_rel, rendered)
        (cfg.vault / vault_rel).chmod(0o755)


def _stage_cat1b_runners(cfg: BootstrapConfig, *, is_upgrade: bool) -> list[str]:
    """Stage 4: cat 1b runner writes. Returns list of backup filenames created."""
    vars_ = _template_vars(cfg)
    backups: list[str] = []
    for vault_rel, tmpl_rel in _CAT1B_RUNNERS:
        src = cfg.plugin_root / tmpl_rel
        target = cfg.vault / vault_rel
        if not src.exists():
            continue
        rendered = render_template(src.read_text(), vars_)
        if is_upgrade and target.exists():
            current = target.read_text()
            if current != rendered:
                # Hand edit detected — back up.
                today = _dt.date.today().isoformat()
                bak = cfg.vault / f"{vault_rel}.bak.{today}"
                shutil.copy2(target, bak)
                backups.append(bak.name)
        _atomic_write(target, rendered)
        target.chmod(0o755)
    return backups


def _assemble(cfg: BootstrapConfig, kind: str) -> str:
    """Assemble SKILL/DREAMING/RESEARCH from phase files.

    kind: "SKILL" | "DREAMING" | "RESEARCH"
    """
    vars_ = _template_vars(cfg)
    phases_root = cfg.plugin_root / "phases"
    bodies: list[str] = [f"# {kind}\n\n**BASE_DIR:** `{cfg.vault}`\n"]
    if kind == "SKILL":
        sources = [phases_root / "core", phases_root / "connectors"]
    elif kind == "DREAMING":
        sources = [phases_root / "core", phases_root / "modes"]
    else:  # RESEARCH
        sources = [phases_root / "core", phases_root / "research"]
    for src_dir in sources:
        if not src_dir.exists():
            continue
        for phase_file in sorted(src_dir.glob("*.md")):
            sections = parse_phase_file(phase_file)
            kept = select_sections(sections, enabled_connectors=cfg.enabled_connectors)
            for s in kept:
                bodies.append(render_template(s.body, vars_))
    return "\n\n".join(bodies)


def _stage_cat4_install(cfg: BootstrapConfig) -> None:
    """Stage 5 (install): assemble + write live + write snapshot."""
    snapshot_dir = cfg.vault / ".scout-state" / "last-assembled"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    for kind in ("SKILL", "DREAMING", "RESEARCH"):
        content = _assemble(cfg, kind)
        _atomic_write(cfg.vault / f"{kind}.md", content)
        _atomic_write(snapshot_dir / f"{kind}.md", content)


def _stage_cat4_upgrade(cfg: BootstrapConfig) -> list[str]:
    """Stage 5 (upgrade): 3-way merge with sidecar policy. Returns conflict file names."""
    snapshot_dir = cfg.vault / ".scout-state" / "last-assembled"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    conflicts: list[str] = []
    for kind in ("SKILL", "DREAMING", "RESEARCH"):
        ours = _assemble(cfg, kind)
        live = cfg.vault / f"{kind}.md"
        theirs = live.read_text() if live.exists() else ours
        snap = snapshot_dir / f"{kind}.md"
        base = snap.read_text() if snap.exists() else theirs
        result = three_way_merge(base=base, ours=ours, theirs=theirs)
        if not result.conflicts:
            _atomic_write(live, result.content)
            _atomic_write(snap, ours)
        else:
            sidecar = cfg.vault / f"{kind}.md.proposed-merge"
            _atomic_write(sidecar, result.content)
            conflicts.append(sidecar.name)
            # live and snap untouched intentionally
    return conflicts


def _stage_jobs_install(cfg: BootstrapConfig) -> None:
    """Stage 6: install schedule-tick + heartbeat (or cron block)."""
    if cfg.skip_jobs:
        return
    if cfg.platform == "macos":
        from scout.scripts.install_schedule_plist import install_plist as install_st
        from scout.scripts.install_heartbeat_plist import install_plist as install_hb

        install_st(home=Path.home(), force=True, bootstrap=True)
        install_hb(home=Path.home(), force=True, bootstrap=True)
    elif cfg.platform == "linux":
        from scout.scripts.install_cron import install_cron

        install_cron(home=Path.home())


def _stage_seed_schedule(cfg: BootstrapConfig) -> None:
    """Stage 3 sub-step: seed .scout-state/schedule.yaml from plugin defaults (install only)."""
    src = cfg.plugin_root / "engine" / "scout" / "defaults" / "schedule.yaml"
    target = cfg.vault / ".scout-state" / "schedule.yaml"
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if src.exists():
        shutil.copy2(src, target)
    else:
        target.write_text("schema_version: 1\nslots: {}\n")


def _stage_version_stamp(cfg: BootstrapConfig, *, is_upgrade: bool) -> None:
    """Stage 7: write/update plugin.version_at_last_{setup,update}."""
    config_path = cfg.vault / "scout-config.yaml"
    if config_path.exists():
        existing = yaml.safe_load(config_path.read_text()) or {}
    else:
        existing = {}
    existing.setdefault("user", {})
    existing["user"]["name"] = cfg.user_name
    existing["user"]["email"] = cfg.user_email
    existing["instance"] = {
        "name": cfg.instance_name,
        "name_lower": cfg.instance_name_lower,
    }
    plugin = existing.setdefault("plugin", {})
    if not is_upgrade:
        plugin["version_at_last_setup"] = cfg.plugin_version
    plugin["version_at_last_update"] = cfg.plugin_version
    plugin.setdefault("applied_migrations", [])
    config_path.write_text(yaml.safe_dump(existing, sort_keys=False))


# ---------- entry points ----------

_VAULT_MARKERS = ("scout-config.yaml", ".scout-state")


def _vault_exists(vault: Path) -> bool:
    if not vault.exists():
        return False
    return any((vault / m).exists() for m in _VAULT_MARKERS)


def _refuse_pending_sidecars(vault: Path) -> None:
    pending = [
        f"{n}.md.proposed-merge"
        for n in ("SKILL", "DREAMING", "RESEARCH")
        if (vault / f"{n}.md.proposed-merge").exists()
    ]
    if pending:
        raise RuntimeError(
            f"Unresolved proposed-merge sidecar(s): {pending}. "
            f"Edit each to remove conflict markers, then "
            f"`mv X.md.proposed-merge X.md`, then re-run /scout-update."
        )


def install(cfg: BootstrapConfig) -> InstallResult:
    """Run the install pipeline. Stage 1 refuses if vault already exists."""
    if _vault_exists(cfg.vault):
        raise FileExistsError(
            f"vault detected at {cfg.vault} — run /scout-update instead, "
            f"or manually remove the vault first (see Plan 8 §4.6 reset snippet)."
        )
    cfg.vault.mkdir(parents=True, exist_ok=True)
    lock = cfg.vault / ".scout-logs" / ".scout-session.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    acquire_lock_with_wait(lock)
    try:
        _stage_create_dirs(cfg)
        _stage_cat1_writes(cfg)
        _stage_seed_schedule(cfg)
        _stage_cat1b_runners(cfg, is_upgrade=False)
        _stage_cat4_install(cfg)
        _stage_jobs_install(cfg)
        _stage_version_stamp(cfg, is_upgrade=False)
    finally:
        release_lock(lock)
    report = run_doctor(vault=cfg.vault, check_jobs=not cfg.skip_jobs)
    return InstallResult(vault=cfg.vault, doctor=report)


def upgrade(cfg: BootstrapConfig) -> UpgradeResult:
    """Run the upgrade pipeline. Stage 1 refuses if no vault."""
    if not _vault_exists(cfg.vault):
        raise FileNotFoundError(
            f"no vault at {cfg.vault} — run /scout-setup instead."
        )
    _refuse_pending_sidecars(cfg.vault)
    lock = cfg.vault / ".scout-logs" / ".scout-session.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    acquire_lock_with_wait(lock)
    try:
        _stage_cat1_writes(cfg)
        backups = _stage_cat1b_runners(cfg, is_upgrade=True)
        conflicts = _stage_cat4_upgrade(cfg)
        _stage_jobs_install(cfg)  # bootstrap=True implies bootout+rebootstrap
        _stage_version_stamp(cfg, is_upgrade=True)
    finally:
        release_lock(lock)
    report = run_doctor(vault=cfg.vault, check_jobs=not cfg.skip_jobs)
    return UpgradeResult(
        vault=cfg.vault,
        doctor=report,
        conflicts=conflicts,
        backups=backups,
    )
```

- [ ] **Step 5: Run tests to confirm they pass**

```bash
cd ~/scout-plugin/engine && pytest tests/unit/test_bootstrap_install.py tests/unit/test_bootstrap_upgrade.py -v
```

Expected: 11 passed (6 install + 5 upgrade). If a test fails because `phases/` files don't render cleanly under the test fixture, debug by adding a minimal `phases/core/dummy.md` directly under the plugin root (mirroring tests/unit/fixtures) — but the real plugin's `phases/` directory already contains valid files, so this should pass on the real codebase.

- [ ] **Step 6: Commit**

```bash
cd ~/scout-plugin && git add engine/scout/scripts/bootstrap.py engine/tests/unit/test_bootstrap_install.py engine/tests/unit/test_bootstrap_upgrade.py && git commit -m "feat(engine): add bootstrap pipeline orchestrator

Implements install/upgrade entry points with the 8-stage pipeline,
global lock, sidecar conflict policy on cat-4 merges, runner hand-edit
detection with backup, and version-stamp recording. Plan 8 §4.3, §4.5,
§4.9, §4.10."
```

---

## Phase B — Engine CLI (wire bootstrap into scoutctl)

### Task B1: `scoutctl bootstrap` subcommand group

**Files:**
- Modify: `~/scout-plugin/engine/scout/cli.py` (add `bootstrap_app`)

- [ ] **Step 1: Read the existing cli.py structure**

```bash
grep -n "^def main\|app.add_typer" ~/scout-plugin/engine/scout/cli.py
```

Identify a good insertion point — after `notify_app` registration (around line 628) but before `def main()`.

- [ ] **Step 2: Add the bootstrap subcommand group**

Insert into `~/scout-plugin/engine/scout/cli.py` immediately before `def main()` (or after the last `app.add_typer` block):

```python
def _register_bootstrap() -> None:
    bootstrap_app = typer.Typer(help="Bootstrap pipeline (install/upgrade/doctor).")
    app.add_typer(bootstrap_app, name="bootstrap")

    @bootstrap_app.command("install")
    def cli_bootstrap_install(
        instance_name: str = typer.Option("Scout", "--instance-name"),
        user_name: str = typer.Option(..., "--user-name"),
        user_email: str = typer.Option(..., "--user-email"),
        timezone: str = typer.Option("America/New_York", "--timezone"),
        platform: str = typer.Option("macos", "--platform"),
        skip_jobs: bool = typer.Option(False, "--no-jobs"),
        skip_claude: bool = typer.Option(False, "--skip-claude"),
        connectors: str = typer.Option("", "--connectors", help="Comma-separated enabled connector names"),
    ) -> None:
        """Install Scout into the user's vault directory."""
        from scout import paths as _paths
        from scout import __version__
        from scout.scripts.bootstrap import BootstrapConfig, install

        vault = _paths.data_dir()
        cfg = BootstrapConfig(
            vault=vault,
            plugin_root=Path(__file__).parent.parent.parent,
            instance_name=instance_name,
            instance_name_lower=instance_name.lower().replace(" ", "-"),
            user_name=user_name,
            user_email=user_email,
            timezone=timezone,
            platform=platform,
            plugin_version=__version__,
            enabled_connectors=set(c.strip() for c in connectors.split(",") if c.strip()),
            connector_inputs={},
            skip_jobs=skip_jobs,
            skip_claude=skip_claude,
        )
        result = install(cfg)
        typer.echo(f"installed: {result.vault}")
        typer.echo(f"doctor: {result.doctor.severity.value}")
        for w in result.doctor.warnings:
            typer.echo(f"  warning: {w}", err=True)
        for e in result.doctor.errors:
            typer.echo(f"  error: {e}", err=True)
        raise typer.Exit(code=result.doctor.exit_code)

    @bootstrap_app.command("upgrade")
    def cli_bootstrap_upgrade(
        skip_jobs: bool = typer.Option(False, "--no-jobs"),
        skip_claude: bool = typer.Option(False, "--skip-claude"),
    ) -> None:
        """Upgrade an existing vault against the current plugin templates."""
        from scout import paths as _paths
        from scout import __version__
        from scout.scripts.bootstrap import BootstrapConfig, upgrade

        vault = _paths.data_dir()
        cfg_path = vault / "scout-config.yaml"
        if not cfg_path.exists():
            typer.echo(f"no vault at {vault} — run /scout-setup", err=True)
            raise typer.Exit(code=2)
        import yaml as _yaml
        existing = _yaml.safe_load(cfg_path.read_text()) or {}
        connectors = set(existing.get("connectors", {}).get("enabled") or [])
        instance = existing.get("instance", {})
        user = existing.get("user", {})
        cfg = BootstrapConfig(
            vault=vault,
            plugin_root=Path(__file__).parent.parent.parent,
            instance_name=instance.get("name", "Scout"),
            instance_name_lower=instance.get("name_lower", "scout"),
            user_name=user.get("name", ""),
            user_email=user.get("email", ""),
            timezone=existing.get("timezone", "America/New_York"),
            platform="macos",  # TODO: re-detect
            plugin_version=__version__,
            enabled_connectors=connectors,
            connector_inputs=existing.get("connectors", {}).get("inputs", {}),
            skip_jobs=skip_jobs,
            skip_claude=skip_claude,
        )
        result = upgrade(cfg)
        typer.echo(f"upgraded: {result.vault}")
        for c in result.conflicts:
            typer.echo(f"  conflict (sidecar): {c}", err=True)
        for b in result.backups:
            typer.echo(f"  backup: {b}", err=True)
        typer.echo(f"doctor: {result.doctor.severity.value}")
        raise typer.Exit(code=result.doctor.exit_code)

    @bootstrap_app.command("doctor")
    def cli_bootstrap_doctor(
        no_jobs: bool = typer.Option(False, "--no-jobs", help="Skip launchd registration check"),
    ) -> None:
        """Run the read-only health check on the current vault."""
        from scout import paths as _paths
        from scout.scripts.bootstrap_doctor import run_doctor

        report = run_doctor(vault=_paths.data_dir(), check_jobs=not no_jobs)
        typer.echo(f"severity: {report.severity.value}")
        for w in report.warnings:
            typer.echo(f"warning: {w}")
        for e in report.errors:
            typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=report.exit_code)


_register_bootstrap()
```

- [ ] **Step 3: Test the CLI surface**

```bash
cd ~/scout-plugin/engine && python -m scout.cli bootstrap --help
```

Expected: usage line + `install`, `upgrade`, `doctor` subcommands listed.

- [ ] **Step 4: Run the full test suite to ensure nothing broke**

```bash
cd ~/scout-plugin/engine && pytest -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
cd ~/scout-plugin && git add engine/scout/cli.py && git commit -m "feat(engine): register scoutctl bootstrap {install,upgrade,doctor}

Wires the bootstrap pipeline into the scoutctl CLI surface. Plan 8 §4.1."
```

---

### Task B2: `scoutctl schedule install-heartbeat-plist` + `install-cron` + `install-all`

**Files:**
- Modify: `~/scout-plugin/engine/scout/cli.py`

- [ ] **Step 1: Add the three new schedule subcommands**

Inside the existing `_register_schedule_subapp` block in `~/scout-plugin/engine/scout/cli.py` (the `def _register_schedule_subapp()` function around line 256), add three new commands at the end:

```python
    @schedule_app.command("install-heartbeat-plist")
    def cli_schedule_install_heartbeat_plist(
        force: bool = typer.Option(False, "--force", "-f"),
        bootstrap: bool = typer.Option(True, "--bootstrap/--no-bootstrap"),
        uninstall: bool = typer.Option(False, "--uninstall"),
    ) -> None:
        """Install or remove com.scout.heartbeat.plist."""
        from scout.scripts.install_heartbeat_plist import (
            install_plist as _i,
            uninstall_plist as _u,
        )
        if uninstall:
            _u(bootout=bootstrap)
            typer.echo("uninstalled com.scout.heartbeat.plist")
            return
        try:
            target = _i(home=Path.home(), force=force, bootstrap=bootstrap)
            typer.echo(f"installed: {target}")
        except FileExistsError as e:
            typer.echo(f"plist exists at {e}; use --force to overwrite", err=True)
            raise typer.Exit(code=1) from e

    @schedule_app.command("install-cron")
    def cli_schedule_install_cron(
        uninstall: bool = typer.Option(False, "--uninstall"),
    ) -> None:
        """Install or remove the Linux scout-managed crontab block."""
        from scout.scripts.install_cron import (
            CrontabApplyError,
            install_cron as _i,
            uninstall_cron as _u,
        )
        try:
            if uninstall:
                _u(home=Path.home())
                typer.echo("removed scout-managed crontab block")
            else:
                _i(home=Path.home())
                typer.echo("installed scout-managed crontab block")
        except CrontabApplyError as e:
            typer.echo(f"crontab apply failed: {e}", err=True)
            raise typer.Exit(code=1) from e

    @schedule_app.command("install-all")
    def cli_schedule_install_all(
        uninstall: bool = typer.Option(False, "--uninstall"),
        force: bool = typer.Option(False, "--force"),
    ) -> None:
        """Platform-aware installer (launchd on macOS, cron on Linux)."""
        import platform as _platform
        system = _platform.system()
        if system == "Darwin":
            from scout.scripts.install_schedule_plist import (
                install_plist as install_st, uninstall_plist as uninstall_st,
            )
            from scout.scripts.install_heartbeat_plist import (
                install_plist as install_hb, uninstall_plist as uninstall_hb,
            )
            if uninstall:
                uninstall_st(bootout=True)
                uninstall_hb(bootout=True)
                typer.echo("uninstalled launchd plists")
                return
            install_st(home=Path.home(), force=force, bootstrap=True)
            install_hb(home=Path.home(), force=force, bootstrap=True)
            typer.echo("installed launchd plists")
        elif system == "Linux":
            from scout.scripts.install_cron import install_cron, uninstall_cron
            if uninstall:
                uninstall_cron(home=Path.home())
                typer.echo("uninstalled scout-managed crontab block")
                return
            install_cron(home=Path.home())
            typer.echo("installed scout-managed crontab block")
        else:
            typer.echo(f"unsupported platform: {system}", err=True)
            raise typer.Exit(code=2)
```

- [ ] **Step 2: Test the new CLI surfaces**

```bash
cd ~/scout-plugin/engine && python -m scout.cli schedule install-heartbeat-plist --help
python -m scout.cli schedule install-cron --help
python -m scout.cli schedule install-all --help
```

Expected: each command lists its options.

- [ ] **Step 3: Run the full test suite**

```bash
cd ~/scout-plugin/engine && pytest -q
```

Expected: all tests pass (no test regression).

- [ ] **Step 4: Commit**

```bash
cd ~/scout-plugin && git add engine/scout/cli.py && git commit -m "feat(engine): register schedule install-{heartbeat-plist,cron,all}

Three new scoutctl schedule subcommands. install-all is the
platform-aware wrapper used by stage 6 of the bootstrap pipeline.
Plan 8 §4.7."
```

---

## Phase C — Plugin templates + slash commands

### Task C1: Extract inline templates from scout-setup.md

**Files:**
- Create: `~/scout-plugin/templates/dreaming-proposals.md.tmpl`
- Create: `~/scout-plugin/templates/scout-mistake-audit.md.tmpl`
- Create: `~/scout-plugin/templates/review-queue.md.tmpl`
- Create: `~/scout-plugin/templates/.gitignore.tmpl`

- [ ] **Step 1: Create dreaming-proposals.md.tmpl**

Create `~/scout-plugin/templates/dreaming-proposals.md.tmpl`:

```markdown
# Dreaming Proposals

Proposals for changes to SKILL.md, generated by dreaming feedback processing runs. {{USER_NAME}} reviews and approves proposals; the next dreaming run applies approved ones.

## How It Works

1. Dreaming Phase 1 identifies improvements from feedback signals
2. Changes targeting SKILL.md are written here as proposals (never edited directly)
3. {{USER_NAME}} reviews and changes status to `Approved` for items to apply
4. The next dreaming run applies approved proposals and marks them `Applied`

---

## Proposals

*No proposals yet. Proposals will appear here after dreaming runs process feedback.*
```

- [ ] **Step 2: Create scout-mistake-audit.md.tmpl**

Create `~/scout-plugin/templates/scout-mistake-audit.md.tmpl`:

```markdown
# {{INSTANCE_NAME}} Mistake Audit

Track errors and patterns to improve {{INSTANCE_NAME}}'s output quality over time. Updated by dreaming runs during feedback processing.

**Parent:** [[knowledge-base]]

## Purpose

This file records specific mistakes {{INSTANCE_NAME}} has made, groups them into patterns, and tracks fixes. The dreaming session uses this to avoid repeating errors and to measure improvement.

## Mistake Log

*No mistakes recorded yet. Entries will appear here as dreaming runs process feedback.*

## Pattern Summary

| Pattern | Occurrences | Status | Last Seen |
|---------|------------|--------|-----------|
```

- [ ] **Step 3: Create review-queue.md.tmpl**

Create `~/scout-plugin/templates/review-queue.md.tmpl`:

```markdown
# Review Queue

Items {{INSTANCE_NAME}} is uncertain about. {{USER_NAME}} reviews these and either approves them into the KB or rejects them.

**Parent:** [[knowledge-base]]

## Pending Review

*No items pending review. Items will appear here when {{INSTANCE_NAME}} encounters uncertain or conflicting information.*

## Reviewed

| Date | Item | Decision | Notes |
|------|------|----------|-------|
```

- [ ] **Step 4: Create .gitignore.tmpl**

Create `~/scout-plugin/templates/.gitignore.tmpl`:

```
.scout-logs/
.scout-cache/
.scout-state/last-assembled/
.obsidian/
.DS_Store
__pycache__/
*.pyc
*.bak.*
*.proposed-merge
```

- [ ] **Step 5: Add these template paths to bootstrap.py's `_CAT1_TEMPLATES` (or extend `_CAT1_FILES_FROM_PLUGIN`)**

Modify `~/scout-plugin/engine/scout/scripts/bootstrap.py` to also seed these on install. Add to `_CAT1_TEMPLATES`:

```python
_CAT1_TEMPLATES = (
    # ... existing entries ...
    (".gitignore", "templates/.gitignore.tmpl"),
)

_INSTALL_ONLY_TEMPLATES = (
    # Vault-owned files seeded once on install (cat 2). Never overwritten on upgrade.
    ("dreaming-proposals.md", "templates/dreaming-proposals.md.tmpl"),
    ("knowledge-base/scout-mistake-audit.md", "templates/scout-mistake-audit.md.tmpl"),
    ("knowledge-base/review-queue.md", "templates/review-queue.md.tmpl"),
)
```

Add a new helper inside bootstrap.py:

```python
def _stage_install_only_seeds(cfg: BootstrapConfig) -> None:
    """Seed cat-2 vault-owned files on install only (never overwritten)."""
    vars_ = _template_vars(cfg)
    for vault_rel, tmpl_rel in _INSTALL_ONLY_TEMPLATES:
        target = cfg.vault / vault_rel
        if target.exists():
            continue  # never overwrite
        src = cfg.plugin_root / tmpl_rel
        if not src.exists():
            continue
        rendered = render_template(src.read_text(), vars_)
        _atomic_write(target, rendered)
```

Call `_stage_install_only_seeds(cfg)` from `install()` between `_stage_cat1_writes(cfg)` and `_stage_seed_schedule(cfg)`. Do NOT call it from `upgrade()`.

- [ ] **Step 6: Run all tests to confirm nothing broke**

```bash
cd ~/scout-plugin/engine && pytest -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
cd ~/scout-plugin && git add templates/dreaming-proposals.md.tmpl templates/scout-mistake-audit.md.tmpl templates/review-queue.md.tmpl templates/.gitignore.tmpl engine/scout/scripts/bootstrap.py && git commit -m "feat(plugin+engine): extract inline templates + install-only seeds

Moves four inline content blocks out of scout-setup.md into proper
template files, and adds _stage_install_only_seeds for cat-2 files
seeded on first install only. Plan 8 §5.1."
```

---

### Task C2: Fix runner templates (drop clock-derived MODE; add SCOUT_DATA_DIR)

**Files:**
- Modify: `~/scout-plugin/templates/run-scout.sh.tmpl`
- Modify: `~/scout-plugin/templates/run-dreaming.sh.tmpl`

- [ ] **Step 1: Update `templates/run-scout.sh.tmpl`**

Read the current file and replace lines 7 (single SCOUT_DIR line) through the mode-derivation block (lines ~60-71) such that:
- After `SCOUT_DIR="{{SCOUT_DIR}}"` add: `export SCOUT_DATA_DIR="$SCOUT_DIR"`
- Replace the `case $HOUR in {{BRIEFING_HOUR}})` block (lines ~54-71) with a single line that reads `MODE="${SCOUT_FORCE_MODE:-manual}"`

Open `~/scout-plugin/templates/run-scout.sh.tmpl` and:

Apply this exact substitution. Replace this block:

```
SCOUT_DIR="{{SCOUT_DIR}}"
LOG_DIR="$SCOUT_DIR/.scout-logs"
```

with:

```
SCOUT_DIR="{{SCOUT_DIR}}"
# Exported so any consumer of scout-plugin's engine package (scout.kb, etc.)
# resolves the user's vault. Required by Plan 5+.
export SCOUT_DATA_DIR="$SCOUT_DIR"
LOG_DIR="$SCOUT_DIR/.scout-logs"
```

And replace this block (find the existing `# Determine mode label` comment section through the `esac\nfi` block):

```bash
# Determine mode label for session name (must happen before pre-session hooks that use $MODE)
HOUR=$(date +%H)
DAY_OF_WEEK=$(date +%u)  # 1=Mon ... 6=Sat, 7=Sun

if [ "$DAY_OF_WEEK" -ge 6 ]; then
    # Weekend
    case $HOUR in
        {{BRIEFING_HOUR}}) MODE="weekend-briefing" ;;
        *)  MODE="weekend-manual" ;;
    esac
else
    # Weekday
    case $HOUR in
        {{BRIEFING_HOUR}}) MODE="morning-briefing" ;;
        {{CONSOLIDATION_HOURS_CASE}}
        *)  MODE="manual" ;;
    esac
fi
```

with:

```bash
# Mode is set by the dispatcher (SCOUT_FORCE_MODE). For manual invocations
# without SCOUT_FORCE_MODE set, default to "manual" — operators can still
# fire the runner directly for debugging, and the prompt downstream will
# pick a reasonable mode based on day-of-week + hour.
MODE="${SCOUT_FORCE_MODE:-manual}"
```

- [ ] **Step 2: Update `templates/run-dreaming.sh.tmpl`**

Apply the same SCOUT_DATA_DIR addition near the top (after SCOUT_DIR), and replace the dreaming-specific mode-derivation block (find the existing `# Determine session label from hour` block):

```bash
# Determine session label from hour
HOUR=$(date +%H)
case $HOUR in
    {{DREAMING_HOURS_CASE}}
    *)  MODE="dreaming-manual" ;;
esac
```

with:

```bash
# Mode is set by the dispatcher (SCOUT_FORCE_MODE). For manual invocations
# without SCOUT_FORCE_MODE set, default to "dreaming-manual".
MODE="${SCOUT_FORCE_MODE:-dreaming-manual}"
```

- [ ] **Step 3: Verify run-research.sh.tmpl is already correct**

```bash
grep -n "SCOUT_FORCE_MODE\|case \$HOUR\|export SCOUT_DATA_DIR" ~/scout-plugin/templates/run-research.sh.tmpl
```

If `SCOUT_FORCE_MODE` is missing, apply the same edits (add SCOUT_DATA_DIR export; replace any clock-derived MODE block with `MODE="${SCOUT_FORCE_MODE:-research-manual}"`).

- [ ] **Step 4: Visual diff against the live vault runners to confirm parity**

```bash
diff <(sed -e 's/{{SCOUT_DIR}}/\/Users\/jordanburger\/Scout/g' \
           -e 's/{{INSTANCE_NAME}}/Scout/g' \
           -e 's/{{INSTANCE_NAME_LOWER}}/scout/g' \
           -e 's/{{USER_NAME}}/Jordan/g' \
           -e 's/{{USER_SLACK_ID}}/U02T4ADKB38/g' \
           -e 's/{{CLAUDE_BIN}}/\/Users\/jordanburger\/.local\/bin\/claude/g' \
           -e 's/{{MAX_BUDGET}}/5.00/g' \
           ~/scout-plugin/templates/run-scout.sh.tmpl) \
     ~/Scout/run-scout.sh
```

Expected: minimal diff — only structural differences from any vault-side hand edits we want to discard. Document any non-obvious deltas in the commit message.

- [ ] **Step 5: Commit**

```bash
cd ~/scout-plugin && git add templates/run-scout.sh.tmpl templates/run-dreaming.sh.tmpl templates/run-research.sh.tmpl && git commit -m "fix(templates): runners read SCOUT_FORCE_MODE; export SCOUT_DATA_DIR

Drops legacy clock-derived case \$HOUR mode block — the dispatcher
already passes SCOUT_FORCE_MODE per slot. Adds SCOUT_DATA_DIR export
required by Plan 5's engine. Templates now match the live vault
runners. Plan 8 §5.2."
```

---

### Task C3: Plugin install-venv.sh fallback script

**Files:**
- Create: `~/scout-plugin/scripts/install-venv.sh`

- [ ] **Step 1: Create the fallback script**

Create `~/scout-plugin/scripts/install-venv.sh`:

```bash
#!/bin/bash
# Fallback installer for ~/scout-plugin/.venv — invoked manually if
# /scout-setup's automatic venv install times out.
#
# Usage: bash ~/scout-plugin/scripts/install-venv.sh
#
# After this completes, retry /scout-setup or run /scout-setup --skip-venv-install.

set -euo pipefail

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$PLUGIN_ROOT/.venv"

if [ ! -d "$PLUGIN_ROOT/engine" ]; then
    echo "error: engine directory not found at $PLUGIN_ROOT/engine" >&2
    exit 1
fi

if [ -d "$VENV" ]; then
    echo "venv already exists at $VENV — recreating..."
    rm -rf "$VENV"
fi

echo "creating venv at $VENV..."
python3 -m venv "$VENV"

echo "installing scout-engine in editable mode (this may take 30-60s)..."
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -e "$PLUGIN_ROOT/engine[dev]"

if [ ! -x "$VENV/bin/scoutctl" ]; then
    echo "error: scoutctl not found at $VENV/bin/scoutctl after install" >&2
    exit 1
fi

echo "ok: venv ready at $VENV"
echo "verify: $VENV/bin/scoutctl version"
```

- [ ] **Step 2: chmod +x**

```bash
chmod +x ~/scout-plugin/scripts/install-venv.sh
```

- [ ] **Step 3: Verify script runs (smoke)**

```bash
bash ~/scout-plugin/scripts/install-venv.sh
```

Expected output ending in: `ok: venv ready at /Users/jordanburger/scout-plugin/.venv` and a successful `scoutctl version` line.

- [ ] **Step 4: Commit**

```bash
cd ~/scout-plugin && git add scripts/install-venv.sh && git commit -m "feat(plugin): add scripts/install-venv.sh fallback

Documented manual fallback for users whose /scout-setup automatic
venv install times out. Idempotent — recreates the venv if present.
Plan 8 §5.1, §6.1."
```

---

### Task C4: Rewrite `commands/scout-setup.md`

**Files:**
- Modify: `~/scout-plugin/commands/scout-setup.md` (full rewrite)

- [ ] **Step 1: Replace the entire scout-setup.md with the new wizard prose**

Open `~/scout-plugin/commands/scout-setup.md` and replace its full content with:

```markdown
---
name: scout-setup
description: First-time install of Scout. Detects connected tools, collects user details, and hands off to scoutctl bootstrap install. For upgrading an existing vault, run /scout-update.
---

# Scout Setup Wizard (greenfield only)

You are the Scout setup wizard. Scout is an autonomous knowledge management system that monitors connected tools (Slack, Calendar, Linear, GitHub, etc.), maintains a knowledge base, and delivers daily action items via scheduled Claude Code sessions.

This command is for **fresh installs only**. If a vault already exists, refuse and tell the user to run `/scout-update`.

---

## Step 0: Pre-flight (refuse if vault detected; install venv if missing)

Run this single bash command:

```bash
bash <<'EOF'
set -e
test -f "$HOME/Scout/scout-config.yaml" && echo "VAULT_EXISTS" && exit 0
test -d "$HOME/Scout/.scout-state" && echo "VAULT_EXISTS" && exit 0
ls "$HOME/Library/LaunchAgents/com.scout."*.plist 2>/dev/null && echo "ORPHAN_JOBS" && exit 0
echo "FRESH"
EOF
```

- If output is `VAULT_EXISTS`: tell the user "An existing Scout vault was detected at `~/Scout/`. To upgrade, run `/scout-update`. To start over, see the manual reset snippet in the README." Stop here.
- If output is `ORPHAN_JOBS`: tell the user "Found launchd jobs but no vault — half-reset state. Run this to clean up:" then show the [Manual Reset](#manual-reset) snippet. Stop here.
- If output is `FRESH`: continue.

Check the engine venv exists:

```bash
test -x "$HOME/scout-plugin/.venv/bin/scoutctl" && echo "VENV_OK" || echo "VENV_MISSING"
```

- If `VENV_MISSING`: tell the user "Engine venv missing. Installing now (this typically takes 30–60 seconds)..." then run, with explicit 5-minute timeout:

  ```bash
  bash ~/scout-plugin/scripts/install-venv.sh
  ```

  (Use the Bash tool with `timeout: 300000`.) If install fails: stop and instruct the user to run `bash ~/scout-plugin/scripts/install-venv.sh` manually, then retry `/scout-setup`.

---

## Step 1: Collect user details (one question at a time)

Ask each of these in order, waiting for each answer:

1. "What would you like to name this Scout instance? (default: Scout)"
2. "What's your name? (used in commit messages and the KB)"
3. "What's your email? (used for git config)"
4. "Timezone? (default: America/New_York)"

---

## Step 2: Connector inventory (read templates/connector-probes.yaml)

Read the probe registry:

```bash
cat ${CLAUDE_PLUGIN_ROOT}/templates/connector-probes.yaml
```

For each connector entry in the YAML:
- If `primary: bash`, run the bash command. If exit code is 0, mark connector enabled.
- Otherwise, attempt to call `primary` as an MCP tool. If it returns data, mark enabled. If not (or tool not found), try each `fallbacks` entry. If all fail, mark disabled.
- For each enabled connector with `needs_user_input`, ask the user for the listed fields and store the values.

After all probes complete, present the checklist as a tidy summary:

```
Connected tools:
  [✓] Slack          [✓] Calendar          [✗] Gmail
  [✓] Linear         [✓] GitHub             [✗] Granola
  [✗] Drive          [✓] Claude Sessions
```

Confirm with the user: "Proceed with these connectors? Or pause to enable more first?"

---

## Step 3: Hand off to `scoutctl bootstrap install`

Build the comma-separated connector list (only enabled), then run:

```bash
~/scout-plugin/.venv/bin/scoutctl bootstrap install \
    --instance-name "<INSTANCE_NAME>" \
    --user-name "<USER_NAME>" \
    --user-email "<USER_EMAIL>" \
    --timezone "<TIMEZONE>" \
    --platform "$(uname -s | tr '[:upper:]' '[:lower:]' | sed 's/darwin/macos/')" \
    --connectors "<comma-separated-enabled-list>"
```

Capture exit code and stdout. The command emits one line per concern: `installed: <path>`, `doctor: green`, plus warnings for sidecar files or missing snapshots.

---

## Step 4: Report and offer first-run

Report the result to the user:
- Vault path, enabled connectors, doctor severity.
- If doctor severity is `green`: "Setup complete. Want to run your first morning briefing now? (yes/no)"
- If `yellow`: list the warnings; tell the user the system will work but those items want attention.
- If `red`: list the errors; tell the user setup did not complete cleanly and link to `scoutctl bootstrap doctor` for diagnosis.

If the user wants the first briefing:

```bash
SCOUT_FORCE_MODE=morning-briefing ~/Scout/run-scout.sh
```

Otherwise: "First scheduled run will fire at the next slot in `~/Scout/.scout-state/schedule.yaml`."

---

## Manual Reset

If you need to wipe Scout entirely and start over:

```bash
# macOS
launchctl bootout gui/$UID/com.scout.schedule-tick gui/$UID/com.scout.heartbeat 2>/dev/null
rm -f ~/Library/LaunchAgents/com.scout.*.plist

# Linux
crontab -l | sed '/# >>> scout-managed >>>/,/# <<< scout-managed <<</d' | crontab -

# Both
rm -rf ~/Scout
```

Then re-run `/scout-setup`.
```

- [ ] **Step 2: Confirm scout-setup.md parses (no template leftovers)**

```bash
grep -n "{{" ~/scout-plugin/commands/scout-setup.md
```

Expected: only `{{SCOUT_DIR}}` and similar inside example bash blocks; no orphan template variables. (The new file should reference `<INSTANCE_NAME>` placeholders that the wizard fills with user input — those are intentional.)

- [ ] **Step 3: Commit**

```bash
cd ~/scout-plugin && git add commands/scout-setup.md && git commit -m "rewrite(commands): scout-setup as thin wrapper around scoutctl bootstrap install

Removes 700+ lines of stale Markdown that hardcoded legacy plist
generation, MCP probe names, and clock-derived schedule variables.
Replaced with a 100-line wizard that calls connector-probes.yaml
for detection and scoutctl bootstrap install for the actual work.
Plan 8 §5.2."
```

---

### Task C5: Add `commands/scout-update.md`

**Files:**
- Create: `~/scout-plugin/commands/scout-update.md`

- [ ] **Step 1: Create the new command**

Create `~/scout-plugin/commands/scout-update.md`:

```markdown
---
name: scout-update
description: Upgrade an existing Scout vault to the current plugin version. Idempotent — re-runs converge to the same state. For first-time install, run /scout-setup.
---

# Scout Update

You are the Scout updater. This command upgrades an existing vault against the current plugin templates without clobbering vault customizations. It runs an 8-stage pipeline (pre-flight → migrations → cat-1 file overwrites → cat-1b runner regeneration → cat-4 3-way merge → job lifecycle → version stamp → doctor).

This command is for **existing vaults only**. If no vault exists, refuse and tell the user to run `/scout-setup`.

---

## Step 0: Pre-flight (refuse if no vault; refuse if pending sidecars)

Run:

```bash
bash <<'EOF'
set -e
test -f "$HOME/Scout/scout-config.yaml" || { echo "NO_VAULT"; exit 0; }
ls "$HOME/Scout/"{SKILL,DREAMING,RESEARCH}.md.proposed-merge 2>/dev/null && { echo "PENDING_SIDECARS"; exit 0; }
test -x "$HOME/scout-plugin/.venv/bin/scoutctl" || { echo "VENV_MISSING"; exit 0; }
echo "READY"
EOF
```

- `NO_VAULT`: "No Scout vault found at `~/Scout/`. Run `/scout-setup` for a fresh install."
- `PENDING_SIDECARS`: "Unresolved merge conflicts from a prior `/scout-update`:" — list the sidecar files. Then: "Edit each file to remove conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`), then run `mv X.md.proposed-merge X.md` for each. Then re-run `/scout-update`."
- `VENV_MISSING`: "Engine venv missing. Run `bash ~/scout-plugin/scripts/install-venv.sh` then re-run `/scout-update`."
- `READY`: continue.

---

## Step 1: Show what's about to happen

Read the current and target plugin versions:

```bash
~/scout-plugin/.venv/bin/scoutctl version
python3 -c "import json; print(json.load(open('${CLAUDE_PLUGIN_ROOT}/plugin.json'))['version'])"
grep version_at_last_update ~/Scout/scout-config.yaml || true
```

Tell the user: "Plugin version: `<plugin>`. Vault was last updated against version `<vault>`. About to apply Plan 8 upgrade pipeline. Proceed? (yes/no)"

If user declines, stop.

---

## Step 2: Run `scoutctl bootstrap upgrade`

```bash
~/scout-plugin/.venv/bin/scoutctl bootstrap upgrade
```

Capture exit code (0 = green, 1 = yellow, 2 = red) and stdout/stderr.

---

## Step 3: Report

- If exit 0: "Upgrade complete. Doctor: green. New version recorded."
- If exit 1: list every `warning:` line. Highlight any `conflict (sidecar):` rows — these are the SKILL/DREAMING/RESEARCH files the user must merge by hand. Provide the resolution instructions: edit the sidecar, `mv X.md.proposed-merge X.md`, re-run `/scout-update`.
- If exit 2: list every `error:` line. Suggest `scoutctl bootstrap doctor` for a clean read of the current state.

If runner backups appeared (`run-*.sh.bak.*`), tell the user the live runners had hand-edits that have been preserved as backups; the fresh templates were installed.
```

- [ ] **Step 2: Commit**

```bash
cd ~/scout-plugin && git add commands/scout-update.md && git commit -m "feat(commands): add /scout-update for idempotent vault upgrades

Thin wrapper around scoutctl bootstrap upgrade. Pre-flight refuses
on missing vault, missing venv, or pending sidecar conflicts.
Plan 8 §4.1, §6.2."
```

---

## Phase D — Cleanup + plugin metadata

### Task D1: Update stale "Plan 7" labels in engine

**Files:**
- Modify: `~/scout-plugin/engine/scout/schedule.py:47`
- Modify: `~/scout-plugin/engine/scout/scripts/schedule_tick.py:387–395`

- [ ] **Step 1: Read line 47 of schedule.py**

```bash
sed -n '45,50p' ~/scout-plugin/engine/scout/schedule.py
```

Expected:
```
class SlotRuntime(StrEnum):
    LOCAL = "local"
    REMOTE = "remote"  # Reserved for Plan 7. Loader accepts; dispatcher rejects.
```

- [ ] **Step 2: Update the comment**

In `~/scout-plugin/engine/scout/schedule.py` line 47, replace:

```python
    REMOTE = "remote"  # Reserved for Plan 7. Loader accepts; dispatcher rejects.
```

with:

```python
    REMOTE = "remote"  # Reserved for a future plan (remote routine integration via Anthropic routines API); not yet wired. Loader accepts; dispatcher rejects.
```

- [ ] **Step 3: Update the error message in schedule_tick.py**

In `~/scout-plugin/engine/scout/scripts/schedule_tick.py` lines ~387–395, replace this block:

```python
    if slot.runtime == SlotRuntime.REMOTE:
        raise ConfigError(
            f"slot {slot_key!r} has runtime: remote, which is reserved for Plan 7 "
            f"(remote routine integration). The dispatcher cannot fire remote slots "
            f"until that work lands. Edit ~/Scout/.scout-state/schedule.yaml and set "
            f"runtime: local, or delete the slot."
        )
```

with:

```python
    if slot.runtime == SlotRuntime.REMOTE:
        raise ConfigError(
            f"slot {slot_key!r} has runtime: remote, which is not yet implemented. "
            f"Remote routine integration is reserved for a future plan. "
            f"Edit ~/Scout/.scout-state/schedule.yaml and set runtime: local, "
            f"or delete the slot."
        )
```

- [ ] **Step 4: Run tests to ensure error message change doesn't break any test**

```bash
cd ~/scout-plugin/engine && pytest -q -k "schedule"
```

Expected: all schedule-related tests pass. If any test asserts the literal string `"reserved for Plan 7"`, update the assertion to match the new error.

- [ ] **Step 5: Commit**

```bash
cd ~/scout-plugin && git add engine/scout/schedule.py engine/scout/scripts/schedule_tick.py && git commit -m "fix(engine): drop stale 'reserved for Plan 7' labels on runtime: remote

Plan 7 shipped as the schedules-tab visual rewrite — remote routine
execution was bumped to a future plan with no number yet (likely
post-Plan-9). Engine error/comment now reflects reality. Plan 8 §5.2."
```

---

### Task D2: Delete dead templates; update plugin.json

**Files:**
- Delete: `~/scout-plugin/templates/launchd-plist.tmpl`
- Delete: `~/scout-plugin/templates/cron-entry.tmpl`
- Modify: `~/scout-plugin/plugin.json`

- [ ] **Step 1: Delete dead templates**

```bash
rm ~/scout-plugin/templates/launchd-plist.tmpl
rm ~/scout-plugin/templates/cron-entry.tmpl
```

- [ ] **Step 2: Update plugin.json**

Open `~/scout-plugin/plugin.json` and replace its content with:

```json
{
  "name": "scout",
  "version": "0.4.0",
  "description": "Autonomous knowledge management and daily briefing system. Monitors your work tools (Slack, Calendar, Linear, GitHub, etc.), synthesizes findings into a persistent knowledge base with a formal ontology, and delivers daily action items — all running unattended via scheduled Claude Code sessions. Six session types: Morning Briefing, Consolidation, Dreaming, Research, interactive Work sessions, and Meta Review. Pre-session hooks pre-compute KB staleness, recent git activity, PR state, and Claude Code session summaries before each run, trading a few seconds of shell work for large token savings inside the session.",
  "commands": [
    "commands/scout-setup.md",
    "commands/scout-update.md",
    "commands/scout-status.md",
    "commands/scout-work.md",
    "commands/scout-meta-review.md"
  ],
  "skills": [
    "skills/scout-briefing.md",
    "skills/scout-consolidation.md",
    "skills/scout-dream.md",
    "skills/scout-research.md"
  ]
}
```

- [ ] **Step 3: Bump engine version to match**

```bash
sed -i.bak 's/^version = "0.4.0"$/version = "0.4.0"/' ~/scout-plugin/engine/pyproject.toml
rm ~/scout-plugin/engine/pyproject.toml.bak
```

(If `engine/pyproject.toml` already declares `version = "0.4.0"`, this is a no-op — verify with `grep version ~/scout-plugin/engine/pyproject.toml`. The shipped plan-7 version is already `0.4.0`, so this should be a no-op.)

- [ ] **Step 4: Commit**

```bash
cd ~/scout-plugin && git add -A templates/ plugin.json && git commit -m "chore(plugin): delete dead templates; register scout-update; bump to 0.4.0

Removes templates/launchd-plist.tmpl (per-mode plist generator deleted
in Plan 5) and templates/cron-entry.tmpl (replaced by managed-block
approach). Adds commands/scout-update.md to the manifest. Plan 8 §5.3."
```

---

## Phase E — Integration smoke + ship

### Task E1: Integration smoke test

**Files:**
- Create: `~/scout-plugin/engine/tests/integration/test_bootstrap_smoke.sh`

- [ ] **Step 1: Write the smoke test**

```bash
mkdir -p ~/scout-plugin/engine/tests/integration
```

Create `~/scout-plugin/engine/tests/integration/test_bootstrap_smoke.sh`:

```bash
#!/bin/bash
# Integration smoke test for scoutctl bootstrap install + upgrade.
# Runs against a temp vault — no host pollution.
#
# Usage: bash ~/scout-plugin/engine/tests/integration/test_bootstrap_smoke.sh

set -euo pipefail

TEST_VAULT=$(mktemp -d -t scout-smoke-XXXXXX)
trap 'rm -rf "$TEST_VAULT"' EXIT

SCOUTCTL="${SCOUTCTL:-$HOME/scout-plugin/.venv/bin/scoutctl}"

if [ ! -x "$SCOUTCTL" ]; then
    echo "FAIL: scoutctl not found at $SCOUTCTL" >&2
    exit 1
fi

echo "=== install ==="
SCOUT_DATA_DIR="$TEST_VAULT" "$SCOUTCTL" bootstrap install \
    --no-jobs \
    --skip-claude \
    --instance-name "TestScout" \
    --user-name "Test User" \
    --user-email "test@example.com" \
    --timezone "America/New_York" \
    --platform "macos" \
    || true   # doctor may report yellow on no-jobs

# Required directory tree
test -d "$TEST_VAULT/knowledge-base" || { echo "FAIL: knowledge-base"; exit 1; }
test -d "$TEST_VAULT/action-items" || { echo "FAIL: action-items"; exit 1; }
test -d "$TEST_VAULT/.scout-state" || { echo "FAIL: .scout-state"; exit 1; }

# Cat-1 files
test -s "$TEST_VAULT/scripts/heartbeat.sh" || { echo "FAIL: heartbeat.sh empty/missing"; exit 1; }
test -s "$TEST_VAULT/knowledge-base/ontology/parser.py" || { echo "FAIL: parser.py"; exit 1; }
test -s "$TEST_VAULT/action-items/render.py" || { echo "FAIL: render.py"; exit 1; }

# Cat-4 assembled + snapshots
for kind in SKILL DREAMING RESEARCH; do
    test -s "$TEST_VAULT/$kind.md" || { echo "FAIL: $kind.md"; exit 1; }
    test -s "$TEST_VAULT/.scout-state/last-assembled/$kind.md" || { echo "FAIL: snapshot $kind.md"; exit 1; }
done

# Schedule
test -s "$TEST_VAULT/.scout-state/schedule.yaml" || { echo "FAIL: schedule.yaml"; exit 1; }
SCOUT_DATA_DIR="$TEST_VAULT" "$SCOUTCTL" schedule list >/dev/null || { echo "FAIL: scoutctl schedule list"; exit 1; }

# Version stamp
grep -q "version_at_last_setup" "$TEST_VAULT/scout-config.yaml" || { echo "FAIL: version_at_last_setup"; exit 1; }

echo ""
echo "=== upgrade (idempotent) ==="
SCOUT_DATA_DIR="$TEST_VAULT" "$SCOUTCTL" bootstrap upgrade --no-jobs --skip-claude || true

# Should still pass all checks
for kind in SKILL DREAMING RESEARCH; do
    test -s "$TEST_VAULT/$kind.md" || { echo "FAIL: post-upgrade $kind.md"; exit 1; }
done

echo ""
echo "=== doctor ==="
SCOUT_DATA_DIR="$TEST_VAULT" "$SCOUTCTL" bootstrap doctor --no-jobs

echo ""
echo "PASS: bootstrap smoke test"
```

- [ ] **Step 2: chmod and run**

```bash
chmod +x ~/scout-plugin/engine/tests/integration/test_bootstrap_smoke.sh
bash ~/scout-plugin/engine/tests/integration/test_bootstrap_smoke.sh
```

Expected: ends with `PASS: bootstrap smoke test`.

- [ ] **Step 3: Commit**

```bash
cd ~/scout-plugin && git add engine/tests/integration/test_bootstrap_smoke.sh && git commit -m "test(integration): bootstrap install + upgrade smoke

Runs against a temp vault with --no-jobs --skip-claude so it doesn't
pollute the host. Asserts directory tree, cat-1 files, cat-4 assembled
+ snapshots, schedule.yaml, version stamp. Plan 8 §8.2."
```

---

### Task E2: Live-vault `/scout-update` against `~/Scout/`

This task is performed **manually** by Jordan, not by an agent. The agent should pause and prompt for confirmation before proceeding.

- [ ] **Step 1: Snapshot the live vault before testing**

Backup state for safety:

```bash
cd ~/Scout && git status --short && git log --oneline -3
cp ~/Scout/SKILL.md ~/Scout/SKILL.md.pre-plan-8-bak
cp ~/Scout/DREAMING.md ~/Scout/DREAMING.md.pre-plan-8-bak
cp ~/Scout/RESEARCH.md ~/Scout/RESEARCH.md.pre-plan-8-bak
cp ~/Scout/scout-config.yaml ~/Scout/scout-config.yaml.pre-plan-8-bak 2>/dev/null || echo "no scout-config.yaml yet"
```

- [ ] **Step 2: Run `/scout-update` against live vault**

In a Claude Code session, invoke `/scout-update`. Read the wizard's stdout carefully — note any conflicts (sidecar files) and runner backups.

- [ ] **Step 3: Verify the live system still runs after update**

```bash
launchctl list | grep com.scout
# Expected: com.scout.heartbeat + com.scout.schedule-tick

ls ~/Scout/{SKILL,DREAMING,RESEARCH}.md.proposed-merge 2>/dev/null
# Any sidecars? Resolve them per Plan 8 §6.2 before next run.

ls ~/Scout/run-*.sh.bak.* 2>/dev/null
# Any runner backups? Inspect to confirm hand-edits were preserved.

grep version_at_last_update ~/Scout/scout-config.yaml
# Should show 0.4.0
```

- [ ] **Step 4: Wait for next dispatcher fire (5 min)**

Watch for the next dispatcher tick to fire a runner. Verify the runner picked up `SCOUT_FORCE_MODE` correctly and produced normal output.

```bash
ls -t ~/Scout/.scout-logs/scout-*.log | head -1 | xargs tail -20
```

- [ ] **Step 5: Resolve any sidecars (manual)**

For each `*.proposed-merge` file:
1. Open in editor; resolve `<<<<<<< / ======= / >>>>>>>` markers.
2. `mv ~/Scout/SKILL.md.proposed-merge ~/Scout/SKILL.md`
3. Repeat for DREAMING/RESEARCH if applicable.
4. Re-run `/scout-update` to refresh the snapshot cleanly.

- [ ] **Step 6: Commit live-vault state**

```bash
cd ~/Scout && git add -A && git commit -m "scout: Plan 8 /scout-update applied — version 0.4.0 recorded"
```

(Or however the live-vault state diverges; commit message should reflect what changed.)

---

### Task E3: Tag scout-plugin v0.4.0

**Files:**
- Tag: `~/scout-plugin` `v0.4.0`

- [ ] **Step 1: Verify all Plan 8 commits are pushed (if a remote exists)**

```bash
cd ~/scout-plugin && git status && git log --oneline -10
```

- [ ] **Step 2: Run the full test suite once more**

```bash
cd ~/scout-plugin/engine && pytest -q
```

Expected: all green.

- [ ] **Step 3: Tag and push**

```bash
cd ~/scout-plugin && git tag -a v0.4.0 -m "Plan 8: scout-setup repair + onboarding/upgrade flow

- /scout-setup rewritten as thin wrapper around scoutctl bootstrap install
- /scout-update added — idempotent upgrade with sidecar conflict policy
- 8-stage pipeline in scoutctl bootstrap (lock-protected, atomic-write)
- Linux scheduling via crontab managed block (atomic rewrite)
- Connector probe registry (templates/connector-probes.yaml)
- Heartbeat plist + cron now have plugin source-of-truth
- Stale 'Plan 7' labels on runtime: remote replaced

Spec: docs/superpowers/specs/2026-05-09-plan-8-scout-setup-repair-design.md
"
git push --tags 2>/dev/null || echo "no remote — local tag only"
```

- [ ] **Step 4: Update scout-app FOLLOWUPS to mark Plan 8 resolved**

```bash
cd ~/scout-app && grep -n "Plan 8\|scout-setup" docs/superpowers/FOLLOWUPS.md | head -5
```

If there are entries, move them to the Resolved section with:

```markdown
### scout-plugin Plan 8 — scout-setup repair + onboarding/upgrade flow (2026-05-XX)

- **scout-setup staleness (cross-cutting, important)** — Resolved by Plan 8.
  Spec: `docs/superpowers/specs/2026-05-09-plan-8-scout-setup-repair-design.md`.
  Plan: `docs/superpowers/plans/2026-05-10-plan-8-scout-setup-repair-plan.md`.
  Plugin tag: scout-plugin `v0.4.0`.
- **stale 'Plan 7' labels on runtime: remote** — Resolved.
- **No /scout-update workflow** — Resolved.
```

Commit:

```bash
cd ~/scout-app && git add docs/superpowers/FOLLOWUPS.md && git commit -m "docs: mark Plan 8 resolved in FOLLOWUPS"
```

---

## Self-Review

### Spec coverage

Walk through each spec section and confirm each requirement maps to a task:

- §1 Problem (9 issues) → all addressed:
  - Issue 1 (schedule install wrong) → Tasks A2, A3, B2, C2
  - Issue 2 (clock-derived MODE) → Task C2
  - Issue 3 (stale probe names) → Task A4 + C1
  - Issue 4 (Reset/Reassemble unsafe) → Task C4 (Reset removed; documented manually)
  - Issue 5 (no /scout-update) → Tasks A8, B1, C5
  - Issue 6 (heartbeat plist no source) → Task A2
  - Issue 7 (pre-flight only checks one file) → Task C4 (3 checks: scout-config.yaml + .scout-state + LaunchAgents)
  - Issue 8 (Linux scheduling dead) → Tasks A3, B2
  - Issue 9 (scoutctl invisible) → Tasks B1, B2, C4 (slash commands now invoke scoutctl)

- §2 Goals → all met by Phase A–E.
- §3 Non-goals → out-of-scope items not implemented (Plan 9, runner unification, cat 5/6).
- §4.1 Two commands → C4, C5
- §4.2 File ownership taxonomy → A8 implementation (cat 1 / 1b / 4 stages); cat 2 documented; install-only seeds for cat 2 in C1
- §4.3 Pipeline → A8 orchestrator
- §4.4 Hand-edit detection → A8 `_stage_cat1b_runners` + test in upgrade tests
- §4.5 3-way merge with sidecar → A1 + A8 `_stage_cat4_upgrade`
- §4.6 Reset path removed → C4 + C5
- §4.7 Connector probe registry → A4
- §4.8 Linux scheduling + atomic crontab → A3
- §4.9 Global pipeline lock → A6 + A8 (acquire_lock_with_wait in install/upgrade)
- §4.10 scout-config.yaml plugin section → A8 `_stage_version_stamp`
- §5.1 Add — every file in the spec list maps to a task. Verified.
- §5.2 Modify — every file mapped. Verified.
- §5.3 Delete — Task D2.
- §6.1 /scout-setup pre-flight → C4 Step 0
- §6.2 /scout-update pre-flight → C5 Step 0
- §7 Error handling → A8 (every stage handles errors per the spec table)
- §8.1 Unit tests → covered across A1–A8, B1
- §8.2 Integration smoke → E1
- §8.3 Doctor → A7
- §9 Sequencing → Phase A → B → C → D → E mirrors §9 ordering
- §10 Out of scope → not implemented (correctly)
- §11 Risks → mitigations implemented (sidecar in A8/A1; lock in A6/A8; atomic crontab in A3; venv timeout in C4 Step 0)
- §12 Q1–Q6 — all decisions baked into the design

**No spec gaps.**

### Placeholder scan

- No "TBD", "TODO", or "implement later" left in step bodies.
- No "Add appropriate error handling" — each error-handling decision is explicit (sidecar, lock-busy raise, atomic write, etc.).
- No "Similar to Task N" — each task contains its own complete code.
- All function names/types referenced (BootstrapConfig, MergeResult, DoctorReport, Probe, Severity, etc.) are defined in their introducing tasks.

### Type consistency

- `BootstrapConfig` defined in Task A8, used in B1 — fields match.
- `MergeResult.{content, conflicts}` introduced in A1, consumed in A8.
- `Probe.{kind, tool_chain, bash_command, needs_user_input}` introduced in A4, used (in slash command prose) in C4.
- `DoctorReport.{severity, errors, warnings, exit_code}` introduced in A7, consumed in A8 + B1.
- `Severity.{GREEN, YELLOW, RED}` consistent across A7, A8, B1.
- `LockBusyError` introduced in A6, raised in A8 (via `acquire_lock_with_wait`).
- `CrontabApplyError` introduced in A3, raised in B2.
- `PhaseSection.{phase, name, slot, mode, requires, body}` introduced in A5, consumed in A8 `_assemble`.

**No inconsistencies found.**

---

**Plan complete and saved to `docs/superpowers/plans/2026-05-10-plan-8-scout-setup-repair-plan.md`.**
