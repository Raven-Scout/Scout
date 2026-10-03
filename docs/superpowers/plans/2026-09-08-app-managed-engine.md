# App-Managed Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A new user installs Scout.app and nothing else; the app installs, registers, bootstraps and later upgrades the Scout engine it was built against.

**Architecture:** Three parts, each shippable alone. **Part A** (scout-plugin) makes the engine locatable and drivable headlessly: `resolve_scoutctl_bin` follows the running interpreter, every bootstrap writes an engine pointer at `~/.local/state/scout/engine.json`, `scoutctl bootstrap auto --json` dispatches install/upgrade/migrate from filesystem state, `scoutctl connectors detect --json` answers the connector probes without an LLM, `install-venv.sh` builds with uv into any directory, and the plists carry `SCOUT_DATA_DIR`. **Part B** (Scout.app) replaces the guessed `scoutctl` path with `EngineLocator` over the pointer and legacy layouts, surfaces engine health in Settings and gates the tabs on it. **Part C** (Scout.app) bundles the pinned plugin tree as a build product and adds `EngineInstaller` / `EngineUpgrader` / the onboarding flow that drive Part A's commands.

**Tech Stack:** Python 3.11+ · Typer · pytest (engine) · Swift 6 / SwiftUI / swift-testing · Xcode 26 · bash · uv 0.12.1 · Claude Code CLI 2.1.259 (`claude plugin`, `claude auth status --json`, `claude mcp list`)

**Spec:** [`docs/superpowers/specs/2026-09-08-app-managed-engine-design.md`](../specs/2026-09-08-app-managed-engine-design.md) — every task below cites the section it implements.

## Global Constraints

Every task's requirements implicitly include this section. Values are copied from the spec.

- **Canonical layout (§4.1):** `~/.local/share/scout/engine/<version>/` (plugin tree), `~/.local/share/scout/engine/current` (symlink), `~/.local/share/scout/venv/<version>/` (venv, **outside** the tree), `~/.local/state/scout/engine.json` (pointer), `~/.local/state/scout/install.log`, `~/.local/bin/uv`, `~/.local/bin/scoutctl` (existing shim).
- **Pointer schema (§4.2):** `schema_version: 1`, keys `version, engine_root, python, scoutctl, vault, managed_by, written_at`; `managed_by ∈ {scout-app, install.sh, claude-code, dev, unknown}`. Written by the engine, never by the app.
- **Engine venv resolution (E1):** `Path(sys.executable).absolute().parent / "scoutctl"` — never `.resolve()` (a venv's `bin/python` is a symlink to the base interpreter).
- **Bootstrap JSON contract (E3):** `BootstrapResult` keys `schema_version, action, reason, dry_run, vault, plugin_version, error, doctor{severity,errors,warnings}, conflicts, backups, snapshots_recorded, pointer`. `action ∈ {install, upgrade, migrate-legacy, refused}`. Exit code = doctor's 0/1/2; refused → 2; `--dry-run` → 0.
- **Detection statuses (E4):** `connected | needs_auth | unavailable | unknown`. Unmappable is `unknown`, never `unavailable`.
- **Prerequisites (§4.3):** Claude Code *installed* is the only hard gate. Sign-in is surfaced, not blocking. Never invoke `/usr/bin/git` to test for git (CLT prompt); use `xcode-select -p`. Never run Anthropic's installer inside the app — open Terminal with `curl -fsSL https://claude.ai/install.sh | bash`.
- **uv (§4.3):** pinned `0.12.1`; assets `uv-{aarch64,x86_64}-apple-darwin.tar.gz` from `https://github.com/astral-sh/uv/releases/download/<version>/`; SHA-256 verified against `engine-release.json`; installed to `~/.local/bin/uv`; existing uv used as-is.
- **Engine payload (§6):** `Scout/Resources/engine-release.json` pins `repo, version, tag, commit`; the tarball `scout-engine-<version>.tar.gz` is a **build product** (never committed) produced by `scripts/bundle-engine.sh` via `git archive`; Release builds fail without it, Debug builds warn.
- **Adoption (§10):** `.external` engines are never modified. `managed_by` other than `scout-app` ⇒ external.
- **Fixtures must be anonymized** (repo `CLAUDE.md`): home `/Users/alex`, org `example-org`, no real tokens or emails beyond `alex@example.com`.
- **Repo idioms:** every shell-out through `ProcessRunner`; published state mutated on `@MainActor`; new `.swift` files under `Scout/` / `ScoutTests/` auto-compile (synchronized groups); test files need explicit `import Foundation` / `import Combine` (MemberImportVisibility); engine tests use `tmp_path` and the autouse hermetic `HOME` from `engine/tests/conftest.py`.
- **Commit style:** conventional prefixes (`feat(engine):`, `fix(app):`, `docs:`); end commit messages with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## Where the work happens

- **Part A** runs in a scout-plugin worktree, never in `~/scout-plugin` itself (its `engine/.venv` is the live engine the launchd plists point at):

  ```bash
  git -C ~/scout-plugin fetch origin
  git -C ~/scout-plugin worktree add ~/scout-plugin-wt-engine -b feat/app-managed-engine origin/main
  cd ~/scout-plugin-wt-engine && uv venv .venv --python 3.12 && uv pip install --python .venv/bin/python -e "engine[dev]"
  ```

  Run engine tests as `cd ~/scout-plugin-wt-engine/engine && ../.venv/bin/pytest <path> -q`. Lint before each commit: `../.venv/bin/ruff check scout tests && ../.venv/bin/ruff format --check scout tests && ../.venv/bin/mypy scout`.
- **Parts B and C** run in this repo on branch `feat/app-managed-engine` (cut from `main` after PR #104 merges). Run app tests as `xcodebuild test -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/<SuiteName> CODE_SIGNING_ALLOWED=NO 2>&1 | tail -30` — a real `@Suite` name, never a directory (a directory selector runs zero tests and reports green).

## File structure

**Part A — scout-plugin**

| Path | Responsibility |
| --- | --- |
| `engine/scout/scripts/install_schedule_plist.py` | `resolve_scoutctl_bin()` from `sys.executable`; `install_plist(vault=)` renders `__SCOUT_DIR__` |
| `engine/scout/scripts/install_heartbeat_plist.py` | `install_plist(vault=)` renders `__SCOUT_DIR__` |
| `engine/scout/defaults/com.scout.{schedule-tick,heartbeat}.plist` | `__SCOUT_DIR__` placeholder + `SCOUT_DATA_DIR` env |
| `engine/scout/scripts/engine_pointer.py` (new) | `EnginePointer`, `current_pointer`, `write_pointer`, `read_pointer`, `pointer_path` |
| `engine/scout/scripts/bootstrap.py` | `BootstrapConfig.managed_by`, `_stage_write_engine_pointer`, `pointer` on results, `_stage_jobs_install(vault=)` |
| `engine/scout/scripts/bootstrap_auto.py` (new) | `AutoAction`, `Plan`, `detect`, `result_dict`, `run` |
| `engine/scout/scripts/bootstrap_doctor.py` | `_check_engine_pointer` |
| `engine/scout/scripts/connector_detect.py` (new) | `DetectStatus`, `Detection`, `parse_mcp_list`, `server_slug`, `tool_server_slug`, `detect`, `run_claude_mcp_list`, `run_bash_probe`, `to_json_dict` |
| `engine/scout/cli.py` | `bootstrap auto`, `--json` on bootstrap subcommands, `--managed-by`, `connectors detect` |
| `engine/bin/scoutctl` | pointer as a venv candidate |
| `scripts/install-venv.sh` | `SCOUT_VENV_DIR`, `SCOUT_UV`, `SCOUT_VENV_EXTRAS`, `SCOUT_PYTHON_VERSION`, uv path |
| `commands/scout-setup.md`, `commands/scout-update.md`, `README.md`, `CHANGELOG.md` | `--managed-by claude-code`; docs |
| `engine/tests/unit/test_{engine_pointer,scoutctl_launcher,bootstrap_auto,connector_detect,install_venv_script}.py` (new), `test_cli_bootstrap_subapp.py` (new), existing `test_install_*_plist.py`, `test_bootstrap_doctor.py`, `test_cli_connectors_subapp.py` | tests |
| `engine/tests/fixtures/claude-mcp-list.txt` (new) | anonymized `claude mcp list` output |

**Part B — Scout.app (adopt)**

| Path | Responsibility |
| --- | --- |
| `Scout/Engine/EngineLayout.swift` | every canonical path, derived from an injectable `home` |
| `Scout/Engine/EnginePointer.swift` | `Codable` mirror of `engine.json` |
| `Scout/Engine/ClaudePluginsRegistry.swift` | parse `installed_plugins.json` (v2) and `known_marketplaces.json` |
| `Scout/Engine/EngineLocator.swift` | `EngineInstall`, `ExternalSource`, `EngineState`, `EngineLocator.locate()` |
| `Scout/Engine/DoctorReport.swift` | doctor JSON + legacy text parser |
| `Scout/Engine/EngineHealthService.swift` | `@MainActor ObservableObject`: state, doctor, `needsAttention`, `refresh()` |
| `Scout/Engine/EnvironmentInjectingRunner.swift` | `ProcessRunner` decorator adding `SCOUT_DATA_DIR` to every call |
| `Scout/Engine/EngineSettingsModel.swift`, `Scout/Shell/EngineSettingsSection.swift` | Settings ▸ Engine |
| `Scout/Onboarding/EngineUnavailableView.swift` | Phase-1 gate placeholder (Part C replaces its body) |
| `Scout/Shell/AppState.swift`, `MainWindowView.swift`, `SidebarView.swift`, `SettingsView.swift`, `Scout/Services/ScheduleService.swift`, `Scout/ActionItems/ActionItemsEnvironmentCheck.swift` | wiring, gate, badge, error copy |
| `ScoutTests/Engine/*Tests.swift`, `ScoutTests/Engine/ScriptedRunner.swift`, `ScoutTests/Fixtures/engine/*`, `ScoutTests/Fixtures/claude-plugins/*` | tests + anonymized fixtures |

**Part C — Scout.app (install)**

| Path | Responsibility |
| --- | --- |
| `Scout/Resources/engine-release.json` | the pin (engine + uv) |
| `scripts/bundle-engine.sh`, `scripts/tests/bundle-engine.test.sh`, `Scout.xcodeproj/project.pbxproj` | tarball build phase |
| `Scout/Engine/EngineRelease.swift` | decode the pin; locate the bundled tarball |
| `Scout/Engine/ClaudeCodeCLI.swift` | argv builders + `auth status` / `--version` decoders |
| `Scout/Engine/PrerequisiteChecker.swift` | claude / auth / git / uv probes |
| `Scout/Engine/UvInstaller.swift` | pinned download + SHA-256 + install |
| `Scout/Engine/EngineInstaller.swift` | the six steps, `BootstrapInput`, `BootstrapResult` |
| `Scout/Engine/EngineVersion.swift`, `Scout/Engine/EngineUpgrader.swift` | compare + upgrade + GC |
| `Scout/Utilities/TerminalHandoff.swift` | open Terminal running a command |
| `Scout/Onboarding/OnboardingViewModel.swift`, `OnboardingView.swift`, `ConnectorDetection.swift` | the flow |
| `scripts/release.sh`, `.github/workflows/ci.yml`, `README.md`, `docs/ROADMAP.md` | guards, CI step, docs |

---

# Part A — scout-plugin (engine changes E1–E6)

Land as one PR on scout-plugin, released as **v0.10.0** (Task A8). Each task is one commit.

### Task A1: `resolve_scoutctl_bin()` follows the running interpreter (E1)

**Files:**
- Modify: `engine/scout/scripts/install_schedule_plist.py:19-39`
- Modify: `engine/scout/scripts/bootstrap.py:166` (`SCOUTCTL_BIN` template var)
- Modify: `engine/scout/defaults/com.scout.schedule-tick.plist:6-9` (comment only)
- Test: `engine/tests/unit/test_install_schedule_plist.py:38-45`

**Interfaces:**
- Produces: `resolve_scoutctl_bin() -> Path` = `Path(sys.executable).absolute().parent / "scoutctl"`. Consumed by A2 (`current_pointer`), the plists, the shim.

- [ ] **Step 1: Replace the resolver test**

Replace `test_resolve_scoutctl_bin_points_at_running_engine_venv` with:

```python
def test_resolve_scoutctl_bin_is_the_running_interpreters_sibling():
    """The scoutctl that matches the running engine is the console script
    beside the interpreter executing this test — whatever venv that is, and
    wherever it lives relative to the plugin tree (spec E1)."""
    import sys

    assert resolve_scoutctl_bin() == Path(sys.executable).absolute().parent / "scoutctl"


def test_resolve_scoutctl_bin_does_not_follow_symlinks(monkeypatch, tmp_path):
    """A venv's bin/python is a symlink to the base interpreter; resolving it
    would name a scoutctl that does not exist."""
    import sys

    real = tmp_path / "base" / "bin" / "python3"
    real.parent.mkdir(parents=True)
    real.write_text("")
    venv_py = tmp_path / "venv" / "bin" / "python"
    venv_py.parent.mkdir(parents=True)
    venv_py.symlink_to(real)
    monkeypatch.setattr(sys, "executable", str(venv_py))

    assert resolve_scoutctl_bin() == venv_py.parent / "scoutctl"
```

- [ ] **Step 2: Run to verify failure**

Run: `../.venv/bin/pytest tests/unit/test_install_schedule_plist.py -q`
Expected: 2 FAIL — the old resolver returns `<plugin_root>/.venv/bin/scoutctl`.

- [ ] **Step 3: Implement**

In `install_schedule_plist.py` add `import sys` and replace `resolve_scoutctl_bin`:

```python
def resolve_scoutctl_bin() -> Path:
    """Return the scoutctl console script beside the running interpreter.

    The interpreter executing the engine *is* the venv the engine is installed
    into, so its ``bin/`` sibling ``scoutctl`` is by construction the one that
    matches the loaded plugin — in every layout: a venv inside the checkout
    (``<root>/.venv``), a venv outside it (the app-managed layout,
    ``~/.local/share/scout/venv/<v>``), or a marketplace clone.

    Do NOT ``resolve()`` the path: in a venv ``sys.executable`` is
    ``<venv>/bin/python``, a symlink to the base interpreter; resolving it
    would point at ``/opt/homebrew/…/bin/scoutctl``, which does not exist.
    """
    return Path(sys.executable).absolute().parent / "scoutctl"
```

In `bootstrap.py` add `from scout.scripts.install_schedule_plist import resolve_scoutctl_bin` to the imports and change the template var to `"SCOUTCTL_BIN": str(resolve_scoutctl_bin()),`. In the plist template comment replace the sentence starting `__SCOUTCTL_BIN__ defaults to` with `__SCOUTCTL_BIN__ is the scoutctl beside the interpreter that ran the install (see resolve_scoutctl_bin).`

- [ ] **Step 4: Run the suite**

Run: `../.venv/bin/pytest tests/unit/test_install_schedule_plist.py tests/unit/test_bootstrap_install.py tests/unit/test_install_scoutctl_shim.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add engine/scout/scripts/install_schedule_plist.py engine/scout/scripts/bootstrap.py engine/scout/defaults/com.scout.schedule-tick.plist engine/tests/unit/test_install_schedule_plist.py
git commit -m "fix(engine): resolve scoutctl from the running interpreter, not <root>/.venv (E1)"
```

### Task A2: Engine pointer module + bootstrap stage + `--managed-by` (E2a)

**Files:**
- Create: `engine/scout/scripts/engine_pointer.py`
- Modify: `engine/scout/scripts/bootstrap.py` (`BootstrapConfig`, results, `install`/`upgrade`/`migrate_legacy`)
- Modify: `engine/scout/cli.py:1192-1378` (`--managed-by` on install / upgrade / migrate-legacy)
- Modify: `commands/scout-setup.md:128`, `commands/scout-update.md:133` (pass `--managed-by claude-code`)
- Test: `engine/tests/unit/test_engine_pointer.py` (new), `engine/tests/unit/test_bootstrap_install.py` (add)

**Interfaces:**
- Produces:
  ```python
  POINTER_SCHEMA_VERSION = 1
  MANAGED_BY_VALUES = ("scout-app", "install.sh", "claude-code", "dev", "unknown")
  def state_dir(home: Path) -> Path            # home/.local/state/scout
  def pointer_path(home: Path) -> Path         # state_dir/engine.json
  @dataclass(frozen=True) class EnginePointer: version, engine_root, python, scoutctl, vault, managed_by, written_at: str; schema_version: int = 1
  def current_pointer(*, vault: Path, managed_by: str) -> EnginePointer
  def write_pointer(pointer: EnginePointer, *, home: Path) -> Path
  def read_pointer(*, home: Path) -> EnginePointer | None
  ```
  `BootstrapConfig.managed_by: str = "unknown"`; `InstallResult/UpgradeResult/MigrateLegacyResult.pointer: Path | None = None`.

- [ ] **Step 1: Write the failing tests**

`engine/tests/unit/test_engine_pointer.py`:

```python
"""Unit tests for engine/scout/scripts/engine_pointer.py (spec §4.2)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import scout
from scout import __version__
from scout.scripts.engine_pointer import (
    POINTER_SCHEMA_VERSION,
    EnginePointer,
    current_pointer,
    pointer_path,
    read_pointer,
    write_pointer,
)


def test_pointer_path_is_xdg_state(tmp_path):
    assert pointer_path(tmp_path) == tmp_path / ".local" / "state" / "scout" / "engine.json"


def test_current_pointer_describes_the_running_engine(tmp_path):
    p = current_pointer(vault=tmp_path / "Scout", managed_by="scout-app")
    assert p.version == __version__
    assert p.engine_root == str(Path(scout.__file__).parent.parent.parent)
    assert p.python == str(Path(sys.executable).absolute())
    assert p.scoutctl == str(Path(sys.executable).absolute().parent / "scoutctl")
    assert p.vault == str(tmp_path / "Scout")
    assert p.managed_by == "scout-app"
    assert p.written_at.endswith("Z")
    assert p.schema_version == POINTER_SCHEMA_VERSION


def test_write_then_read_round_trips(tmp_path):
    p = current_pointer(vault=tmp_path / "Scout", managed_by="install.sh")
    written = write_pointer(p, home=tmp_path)
    assert written == pointer_path(tmp_path)
    raw = json.loads(written.read_text())
    assert raw["schema_version"] == 1
    assert raw["managed_by"] == "install.sh"
    assert read_pointer(home=tmp_path) == p


def test_read_pointer_returns_none_when_missing_or_malformed(tmp_path):
    assert read_pointer(home=tmp_path) is None
    pointer_path(tmp_path).parent.mkdir(parents=True)
    pointer_path(tmp_path).write_text("{not json")
    assert read_pointer(home=tmp_path) is None
    pointer_path(tmp_path).write_text(json.dumps({"schema_version": 99, "version": "x"}))
    assert read_pointer(home=tmp_path) is None


def test_write_is_atomic_no_tmp_left_behind(tmp_path):
    write_pointer(current_pointer(vault=tmp_path / "Scout", managed_by="dev"), home=tmp_path)
    leftovers = [p for p in pointer_path(tmp_path).parent.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []
```

Append to `engine/tests/unit/test_bootstrap_install.py`:

```python
def test_install_writes_engine_pointer_for_this_vault(tmp_path):
    """Every bootstrap records where the engine lives (spec §4.2). HOME is the
    hermetic per-test home from conftest, so Path.home() is safe to read."""
    from scout.scripts.engine_pointer import read_pointer

    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    cfg = _config(vault, plugin_root=plugin)
    cfg.managed_by = "scout-app"
    result = install(cfg)
    pointer = read_pointer(home=Path.home())
    assert pointer is not None
    assert pointer.vault == str(vault)
    assert pointer.managed_by == "scout-app"
    assert result.pointer == Path.home() / ".local" / "state" / "scout" / "engine.json"


def test_install_pointer_defaults_to_unknown_manager(tmp_path):
    from scout.scripts.engine_pointer import read_pointer

    plugin = Path(__file__).parent.parent.parent.parent
    install(_config(tmp_path / "Scout", plugin_root=plugin))
    assert read_pointer(home=Path.home()).managed_by == "unknown"
```

- [ ] **Step 2: Run to verify failure**

Run: `../.venv/bin/pytest tests/unit/test_engine_pointer.py tests/unit/test_bootstrap_install.py -q`
Expected: FAIL — `ModuleNotFoundError: scout.scripts.engine_pointer`.

- [ ] **Step 3: Implement the module**

`engine/scout/scripts/engine_pointer.py`:

```python
"""The engine pointer — ``~/.local/state/scout/engine.json`` (spec §4.2).

One file that answers "where is the engine?" for every consumer: Scout.app
(``EngineLocator``), the ``engine/bin/scoutctl`` launcher (venv candidate),
the doctor (consistency check) and ``install.sh``. Written by every bootstrap
entrypoint in the same stage as the ``~/.local/bin/scoutctl`` shim. Readers
treat a missing or malformed file as "no pointer" and fall back to discovery.
"""

from __future__ import annotations

import datetime as _dt
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

POINTER_SCHEMA_VERSION = 1
MANAGED_BY_VALUES = ("scout-app", "install.sh", "claude-code", "dev", "unknown")
_FIELDS = ("version", "engine_root", "python", "scoutctl", "vault", "managed_by", "written_at")


def state_dir(home: Path) -> Path:
    return home / ".local" / "state" / "scout"


def pointer_path(home: Path) -> Path:
    return state_dir(home) / "engine.json"


@dataclass(frozen=True)
class EnginePointer:
    version: str
    engine_root: str
    python: str
    scoutctl: str
    vault: str
    managed_by: str
    written_at: str
    schema_version: int = POINTER_SCHEMA_VERSION

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True) + "\n"


def current_pointer(*, vault: Path, managed_by: str) -> EnginePointer:
    """Describe the engine executing this call.

    Root comes from the imported package (editable installs point at the
    source tree); interpreter and scoutctl from ``sys.executable`` (E1).
    """
    import scout
    from scout import __version__
    from scout.scripts.install_schedule_plist import resolve_scoutctl_bin

    root = Path(scout.__file__).parent.parent.parent
    now = _dt.datetime.now(_dt.UTC).replace(microsecond=0)
    return EnginePointer(
        version=__version__,
        engine_root=str(root),
        python=str(Path(sys.executable).absolute()),
        scoutctl=str(resolve_scoutctl_bin()),
        vault=str(vault),
        managed_by=managed_by,
        written_at=now.isoformat().replace("+00:00", "Z"),
    )


def write_pointer(pointer: EnginePointer, *, home: Path) -> Path:
    """Atomically write the pointer; returns its path."""
    target = pointer_path(home)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(pointer.to_json(), encoding="utf-8")
    tmp.replace(target)
    return target


def read_pointer(*, home: Path) -> EnginePointer | None:
    """Return the pointer, or None when absent, unreadable, malformed, or of
    an unknown schema version. Never raises."""
    try:
        raw = json.loads(pointer_path(home).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict) or raw.get("schema_version") != POINTER_SCHEMA_VERSION:
        return None
    try:
        return EnginePointer(**{k: str(raw[k]) for k in _FIELDS})
    except (KeyError, TypeError):
        return None
```

- [ ] **Step 4: Wire the bootstrap stage**

In `bootstrap.py`: add `managed_by: str = "unknown"` as the last field of `BootstrapConfig`; add `pointer: Path | None = None` to `InstallResult`, `UpgradeResult`, `MigrateLegacyResult` (after `doctor`); add the stage:

```python
def _stage_write_engine_pointer(cfg: BootstrapConfig) -> Path:
    """Record where THIS engine lives (~/.local/state/scout/engine.json, §4.2).

    Not gated by skip_jobs: the pointer is state about the engine that just
    ran, true in every mode, and tests run under a hermetic HOME.
    """
    from scout.scripts.engine_pointer import current_pointer, write_pointer

    return write_pointer(current_pointer(vault=cfg.vault, managed_by=cfg.managed_by), home=Path.home())
```

Call it in all three entrypoints immediately after `_stage_version_stamp(...)` inside the `try:` block, capturing `pointer = _stage_write_engine_pointer(cfg)`, and pass `pointer=pointer` into the returned result.

- [ ] **Step 5: Add `--managed-by` to the CLI and the runbooks**

In `cli.py`, add to `cli_bootstrap_install`, `cli_bootstrap_upgrade` and `cli_bootstrap_migrate_legacy`:

```python
        managed_by: str = typer.Option(
            "unknown",
            "--managed-by",
            help="Who owns this engine install: scout-app | install.sh | claude-code | dev | unknown (recorded in the engine pointer).",
        ),
```

and pass `managed_by=managed_by` into each `BootstrapConfig(...)`. In `commands/scout-setup.md` add `--managed-by claude-code \` as the first flag of the `bootstrap install` block; in `commands/scout-update.md` change the Step 2 command to `"$SCOUTCTL" bootstrap upgrade --managed-by claude-code`.

- [ ] **Step 6: Run the suite and lint**

Run: `../.venv/bin/pytest tests/unit/test_engine_pointer.py tests/unit/test_bootstrap_install.py tests/unit/test_bootstrap_upgrade.py tests/unit/test_bootstrap_migrate_legacy.py -q && ../.venv/bin/ruff check scout tests && ../.venv/bin/mypy scout`
Expected: PASS, clean.

- [ ] **Step 7: Commit**

```bash
git add engine/scout/scripts/engine_pointer.py engine/scout/scripts/bootstrap.py engine/scout/cli.py commands/scout-setup.md commands/scout-update.md engine/tests/unit/test_engine_pointer.py engine/tests/unit/test_bootstrap_install.py
git commit -m "feat(engine): write an engine pointer on every bootstrap; --managed-by (E2)"
```

### Task A3: Launcher honors the pointer; doctor checks pointer ↔ plist (E2b)

**Files:**
- Modify: `engine/bin/scoutctl:38-55`
- Modify: `engine/scout/scripts/bootstrap_doctor.py` (new `_check_engine_pointer`, wired in `run_doctor`)
- Test: `engine/tests/unit/test_scoutctl_launcher.py` (new), `engine/tests/unit/test_bootstrap_doctor.py` (add)

**Interfaces:**
- Consumes: `write_pointer`, `EnginePointer`, `read_pointer` from A2.
- Produces: launcher candidate order `PLUGIN_ROOT/.venv` → `ENGINE_DIR/.venv` → **pointer `python`** → marketplaces cross-jump → `python3`.

- [ ] **Step 1: Write the failing launcher tests**

`engine/tests/unit/test_scoutctl_launcher.py`:

```python
"""Behavioral tests for engine/bin/scoutctl (the bash launcher).

Runs the real launcher against a throwaway plugin tree with fake interpreters
that echo their argv, so we can assert WHICH python it exec'd."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from scout.scripts.engine_pointer import EnginePointer, write_pointer

LAUNCHER = Path(__file__).resolve().parents[3] / "engine" / "bin" / "scoutctl"


def _fake_python(venv: Path, tag: str) -> Path:
    py = venv / "bin" / "python"
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text(f'#!/bin/sh\necho "{tag} $*"\n', encoding="utf-8")
    py.chmod(0o755)
    return py


def _plugin_tree(tmp_path: Path) -> Path:
    root = tmp_path / "plugin"
    (root / "engine" / "bin").mkdir(parents=True)
    dst = root / "engine" / "bin" / "scoutctl"
    shutil.copy(LAUNCHER, dst)
    dst.chmod(0o755)
    return root


def _pointer(home: Path, python: Path) -> None:
    write_pointer(
        EnginePointer(
            version="0.0.0",
            engine_root="/nonexistent",
            python=str(python),
            scoutctl=str(python.parent / "scoutctl"),
            vault=str(home / "Scout"),
            managed_by="scout-app",
            written_at="2026-01-01T00:00:00Z",
        ),
        home=home,
    )


def _run(root: Path, home: Path, extra_path: str = "") -> str:
    env = {"HOME": str(home), "PATH": f"{extra_path}:/usr/bin:/bin".lstrip(":")}
    out = subprocess.run(
        [str(root / "engine" / "bin" / "scoutctl"), "version"],
        env=env, capture_output=True, text=True, check=True, timeout=10,
    )
    return out.stdout.strip()


def test_uses_pointer_python_when_tree_has_no_venv(tmp_path):
    home = tmp_path / "home"
    py = _fake_python(tmp_path / "outside-venv", "POINTER_PY")
    _pointer(home, py)
    assert _run(_plugin_tree(tmp_path), home) == "POINTER_PY -m scout.cli version"


def test_prefers_in_tree_venv_over_pointer(tmp_path):
    """Edit-and-go: a dev checkout with its own venv keeps using it."""
    home = tmp_path / "home"
    _pointer(home, _fake_python(tmp_path / "outside-venv", "POINTER_PY"))
    root = _plugin_tree(tmp_path)
    _fake_python(root / ".venv", "TREE_PY")
    assert _run(root, home) == "TREE_PY -m scout.cli version"


def test_malformed_pointer_falls_through_to_system_python3(tmp_path):
    home = tmp_path / "home"
    (home / ".local" / "state" / "scout").mkdir(parents=True)
    (home / ".local" / "state" / "scout" / "engine.json").write_text("{not json", encoding="utf-8")
    sysbin = tmp_path / "sysbin"
    sysbin.mkdir()
    py3 = sysbin / "python3"
    py3.write_text('#!/bin/sh\necho "SYSTEM_PY $*"\n', encoding="utf-8")
    py3.chmod(0o755)
    assert _run(_plugin_tree(tmp_path), home, extra_path=str(sysbin)) == "SYSTEM_PY -m scout.cli version"


def test_pointer_python_that_no_longer_exists_is_skipped(tmp_path):
    home = tmp_path / "home"
    _pointer(home, tmp_path / "gone" / "bin" / "python")
    sysbin = tmp_path / "sysbin"
    sysbin.mkdir()
    py3 = sysbin / "python3"
    py3.write_text('#!/bin/sh\necho "SYSTEM_PY $*"\n', encoding="utf-8")
    py3.chmod(0o755)
    assert _run(_plugin_tree(tmp_path), home, extra_path=str(sysbin)) == "SYSTEM_PY -m scout.cli version"
```

- [ ] **Step 2: Run to verify failure**

Run: `../.venv/bin/pytest tests/unit/test_scoutctl_launcher.py -q`
Expected: `test_uses_pointer_python_when_tree_has_no_venv` FAILS (falls through to `python3`, which the bare PATH cannot find → `CalledProcessError`); the other three pass already.

- [ ] **Step 3: Add the pointer candidate to the launcher**

In `engine/bin/scoutctl`, immediately after the `candidates=( … )` array (line 41) and before the `if [[ "$PLUGIN_ROOT" == */plugins/cache/* ]]` block, insert:

```bash
# The engine pointer (~/.local/state/scout/engine.json, written by every
# `scoutctl bootstrap …`) names the venv that matches the installed engine.
# Consulted AFTER the in-tree venv so a dev checkout keeps edit-and-go, and
# BEFORE the marketplaces/ cross-jump so Claude Code's cache copy of an
# app-managed engine (whose venv lives outside the tree) resolves without
# guessing. Parsed with sed on purpose: this launcher has to work with only
# /bin/sh tools — finding Python is the whole point.
POINTER="${XDG_STATE_HOME:-$HOME/.local/state}/scout/engine.json"
if [ -r "$POINTER" ]; then
    POINTER_PY="$(sed -n 's/^[[:space:]]*"python"[[:space:]]*:[[:space:]]*"\([^"]*\)".*$/\1/p' "$POINTER" | head -n 1)"
    if [ -n "$POINTER_PY" ]; then
        candidates+=("$POINTER_PY")
    fi
fi
```

Also extend the header comment's probe order list with `2b. engine pointer  — ~/.local/state/scout/engine.json "python"`.

- [ ] **Step 4: Run launcher tests + shellcheck**

Run: `../.venv/bin/pytest tests/unit/test_scoutctl_launcher.py -q && shellcheck ../engine/bin/scoutctl` (install shellcheck with `brew install shellcheck` if absent — CI runs it at full severity).
Expected: 4 PASS; shellcheck clean.

- [ ] **Step 5: Write the failing doctor tests**

Append to `engine/tests/unit/test_bootstrap_doctor.py` (it already has a `_vault(tmp_path)`-style helper producing a green vault; reuse whatever the file names it — read the file first and match):

```python
def _write_plist(home: Path, scoutctl: str) -> None:
    import plistlib

    agents = home / "Library" / "LaunchAgents"
    agents.mkdir(parents=True, exist_ok=True)
    with (agents / "com.scout.schedule-tick.plist").open("wb") as f:
        plistlib.dump({"Label": "com.scout.schedule-tick", "ProgramArguments": [scoutctl, "schedule", "tick"]}, f)


def _pointer(home: Path, scoutctl: Path) -> None:
    from scout.scripts.engine_pointer import EnginePointer, write_pointer

    write_pointer(
        EnginePointer(version="0.0.0", engine_root=str(scoutctl.parents[2]), python=str(scoutctl.parent / "python"),
                      scoutctl=str(scoutctl), vault=str(home / "Scout"), managed_by="scout-app",
                      written_at="2026-01-01T00:00:00Z"),
        home=home,
    )


def test_doctor_silent_when_pointer_and_plist_agree(tmp_path):
    from scout.scripts.bootstrap_doctor import _check_engine_pointer

    home = tmp_path / "home"
    scoutctl = home / ".local" / "share" / "scout" / "venv" / "0.0.0" / "bin" / "scoutctl"
    scoutctl.parent.mkdir(parents=True)
    scoutctl.write_text("#!/bin/sh\n")
    _pointer(home, scoutctl)
    _write_plist(home, str(scoutctl))
    assert _check_engine_pointer(home=home) == ([], [])


def test_doctor_warns_when_pointer_and_plist_disagree(tmp_path):
    from scout.scripts.bootstrap_doctor import _check_engine_pointer

    home = tmp_path / "home"
    scoutctl = home / "venv" / "bin" / "scoutctl"
    scoutctl.parent.mkdir(parents=True)
    scoutctl.write_text("#!/bin/sh\n")
    _pointer(home, scoutctl)
    _write_plist(home, "/somewhere/else/scoutctl")
    errors, warnings = _check_engine_pointer(home=home)
    assert errors == []
    assert len(warnings) == 1 and "different scoutctl" in warnings[0]


def test_doctor_warns_when_pointer_scoutctl_is_missing(tmp_path):
    from scout.scripts.bootstrap_doctor import _check_engine_pointer

    home = tmp_path / "home"
    _pointer(home, home / "gone" / "bin" / "scoutctl")
    _, warnings = _check_engine_pointer(home=home)
    assert len(warnings) == 1 and "missing scoutctl" in warnings[0]


def test_doctor_silent_without_pointer(tmp_path):
    from scout.scripts.bootstrap_doctor import _check_engine_pointer

    assert _check_engine_pointer(home=tmp_path / "home") == ([], [])
```

- [ ] **Step 6: Implement the doctor check**

In `bootstrap_doctor.py` add after `_check_scoutctl_shim`:

```python
def _check_engine_pointer(*, home: Path) -> tuple[list[str], list[str]]:
    """Warn (never error) when the engine pointer disagrees with reality.

    Both the pointer and the schedule-tick plist are rewritten by every
    bootstrap run, so disagreement means they were produced by different
    engines — exactly the drift the pointer exists to make visible. A missing
    pointer is not flagged (pre-pointer engines; the next bootstrap writes it).
    """
    from scout.scripts.engine_pointer import read_pointer

    pointer = read_pointer(home=home)
    if pointer is None:
        return [], []
    warnings: list[str] = []
    if not Path(pointer.scoutctl).exists():
        warnings.append(
            f"engine pointer names a missing scoutctl ({pointer.scoutctl}) — re-run `scoutctl bootstrap upgrade`."
        )
    plist_path = home / "Library" / "LaunchAgents" / "com.scout.schedule-tick.plist"
    if plist_path.exists():
        try:
            with plist_path.open("rb") as f:
                args = plistlib.load(f).get("ProgramArguments") or []
        except (plistlib.InvalidFileException, OSError):
            args = []
        if args and args[0] != pointer.scoutctl:
            warnings.append(
                f"engine pointer ({pointer.scoutctl}) and {plist_path.name} ({args[0]}) name different "
                f"scoutctl binaries — re-run `scoutctl bootstrap upgrade` so both track one engine."
            )
    return [], warnings
```

Wire it in `run_doctor` inside the existing `if check_jobs:` block, after the shim check: `_, pointer_warnings = _check_engine_pointer(home=home); warnings.extend(pointer_warnings)`.

- [ ] **Step 7: Run and commit**

Run: `../.venv/bin/pytest tests/unit/test_bootstrap_doctor.py tests/unit/test_scoutctl_launcher.py -q && ../.venv/bin/ruff check scout tests && ../.venv/bin/mypy scout`
Expected: PASS.

```bash
git add engine/bin/scoutctl engine/scout/scripts/bootstrap_doctor.py engine/tests/unit/test_scoutctl_launcher.py engine/tests/unit/test_bootstrap_doctor.py
git commit -m "feat(engine): launcher and doctor read the engine pointer (E2)"
```

### Task A4: `scoutctl bootstrap auto` and `--json` on every bootstrap subcommand (E3)

**Files:**
- Create: `engine/scout/scripts/bootstrap_auto.py`
- Modify: `engine/scout/cli.py:1188-1381` (`_register_bootstrap`)
- Test: `engine/tests/unit/test_bootstrap_auto.py` (new), `engine/tests/unit/test_cli_bootstrap_subapp.py` (new)

**Interfaces:**
- Consumes: `install`, `upgrade`, `migrate_legacy`, `_is_legacy_vault`, `_vault_exists`, `_CAT_MERGE_FILES` (bootstrap.py); `DoctorReport`.
- Produces:
  ```python
  class AutoAction(Enum): INSTALL="install"; UPGRADE="upgrade"; MIGRATE_LEGACY="migrate-legacy"; REFUSED="refused"
  @dataclass(frozen=True) class Plan: action: AutoAction; reason: str
  def pending_sidecars(vault: Path) -> list[str]
  def detect(vault: Path) -> Plan
  def doctor_dict(report: DoctorReport | None) -> dict | None
  def result_dict(*, action, vault, plugin_version, result, error=None, dry_run=False, reason="") -> dict
  def run(cfg: BootstrapConfig, *, dry_run: bool = False) -> tuple[dict, int]
  ```
  CLI: `scoutctl bootstrap auto [--user-name --user-email --instance-name --timezone --platform auto|macos|linux --connectors --user-slack-id --github-username --github-repos --claude-bin auto|PATH --max-budget --no-jobs --skip-claude --managed-by --interactive/--no-interactive --yes --dry-run --json]`; `--json` on `install`, `upgrade`, `migrate-legacy`, `doctor`. This argv is the contract Part C's `EngineInstaller.bootstrapAutoArguments` builds.

- [ ] **Step 1: Write the failing module tests**

`engine/tests/unit/test_bootstrap_auto.py`:

```python
"""Unit tests for engine/scout/scripts/bootstrap_auto.py (scout-plugin#26, spec E3)."""

from __future__ import annotations

from pathlib import Path

from scout.scripts.bootstrap import BootstrapConfig
from scout.scripts.bootstrap_auto import AutoAction, detect, result_dict, run

PLUGIN = Path(__file__).resolve().parents[3]


def _cfg(vault: Path) -> BootstrapConfig:
    return BootstrapConfig(
        vault=vault, plugin_root=PLUGIN, instance_name="TestScout", instance_name_lower="testscout",
        user_name="Alex", user_email="alex@example.com", timezone="America/New_York", platform="macos",
        plugin_version="0.10.0", enabled_connectors=set(), connector_inputs={}, skip_jobs=True, skip_claude=True,
        managed_by="scout-app",
    )


def test_detect_missing_dir_is_install(tmp_path):
    assert detect(tmp_path / "Scout").action is AutoAction.INSTALL


def test_detect_empty_dir_is_install(tmp_path):
    (tmp_path / "Scout").mkdir()
    assert detect(tmp_path / "Scout").action is AutoAction.INSTALL


def test_detect_legacy_vault_is_migrate(tmp_path):
    (tmp_path / "Scout" / ".scout-state").mkdir(parents=True)
    assert detect(tmp_path / "Scout").action is AutoAction.MIGRATE_LEGACY


def test_detect_configured_vault_is_upgrade(tmp_path):
    (tmp_path / "Scout").mkdir()
    (tmp_path / "Scout" / "scout-config.yaml").write_text("instance_name: x\n")
    assert detect(tmp_path / "Scout").action is AutoAction.UPGRADE


def test_detect_pending_sidecar_is_refused(tmp_path):
    (tmp_path / "Scout").mkdir()
    (tmp_path / "Scout" / "scout-config.yaml").write_text("instance_name: x\n")
    (tmp_path / "Scout" / "SKILL.md.proposed-merge").write_text("<<<<<<<\n")
    plan = detect(tmp_path / "Scout")
    assert plan.action is AutoAction.REFUSED and "sidecar" in plan.reason


def test_detect_nonempty_non_vault_is_refused(tmp_path):
    (tmp_path / "Scout").mkdir()
    (tmp_path / "Scout" / "notes.txt").write_text("hi")
    plan = detect(tmp_path / "Scout")
    assert plan.action is AutoAction.REFUSED and "not a Scout vault" in plan.reason


def test_run_installs_then_upgrades(tmp_path):
    vault = tmp_path / "Scout"
    first, code = run(_cfg(vault))
    assert first["action"] == "install" and code in (0, 1)
    assert first["doctor"]["severity"] in ("green", "yellow")
    assert first["pointer"] is not None
    assert (vault / "scout-config.yaml").exists()
    second, _ = run(_cfg(vault))
    assert second["action"] == "upgrade"
    assert second["conflicts"] == []


def test_run_dry_run_mutates_nothing(tmp_path):
    vault = tmp_path / "Scout"
    d, code = run(_cfg(vault), dry_run=True)
    assert code == 0 and d["dry_run"] is True and d["action"] == "install" and d["doctor"] is None
    assert not vault.exists()


def test_run_refused_returns_exit_2(tmp_path):
    vault = tmp_path / "Scout"
    vault.mkdir()
    (vault / "notes.txt").write_text("hi")
    d, code = run(_cfg(vault))
    assert code == 2 and d["action"] == "refused" and d["error"]


def test_result_dict_has_the_contract_keys(tmp_path):
    d = result_dict(action=AutoAction.INSTALL, vault=tmp_path, plugin_version="0.10.0", result=None)
    assert set(d) == {
        "schema_version", "action", "reason", "dry_run", "vault", "plugin_version", "error",
        "doctor", "conflicts", "backups", "snapshots_recorded", "pointer",
    }
```

- [ ] **Step 2: Run to verify failure**

Run: `../.venv/bin/pytest tests/unit/test_bootstrap_auto.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement the module**

`engine/scout/scripts/bootstrap_auto.py`:

```python
"""``scoutctl bootstrap auto`` — detect vault state, dispatch, report (spec E3, #26).

The state → action table is the one in docs/specs/scoutctl-bootstrap-auto.md.
This module is the machine-readable face of the bootstrap pipeline: Scout.app
decodes ``result_dict`` and never parses the human text.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from scout.scripts.bootstrap import (
    _CAT_MERGE_FILES,
    BootstrapConfig,
    InstallResult,
    MigrateLegacyResult,
    UpgradeResult,
    _is_legacy_vault,
    _vault_exists,
    install,
    migrate_legacy,
    upgrade,
)
from scout.scripts.bootstrap_doctor import DoctorReport

RESULT_SCHEMA_VERSION = 1


class AutoAction(Enum):
    INSTALL = "install"
    UPGRADE = "upgrade"
    MIGRATE_LEGACY = "migrate-legacy"
    REFUSED = "refused"


@dataclass(frozen=True)
class Plan:
    action: AutoAction
    reason: str


def pending_sidecars(vault: Path) -> list[str]:
    names = [f"{n}.md.proposed-merge" for n in ("SKILL", "DREAMING", "RESEARCH")]
    names += [f"{rel}.proposed-merge" for rel in _CAT_MERGE_FILES]
    return [n for n in names if (vault / n).exists()]


def detect(vault: Path) -> Plan:
    if not vault.exists() or (vault.is_dir() and not any(vault.iterdir())):
        return Plan(AutoAction.INSTALL, "no vault: directory missing or empty")
    if not vault.is_dir():
        return Plan(AutoAction.REFUSED, f"{vault} exists and is not a directory")
    sidecars = pending_sidecars(vault)
    if sidecars:
        return Plan(
            AutoAction.REFUSED,
            f"unresolved proposed-merge sidecar(s): {sidecars} — edit each, `mv X.proposed-merge X`, then re-run",
        )
    if _is_legacy_vault(vault):
        return Plan(AutoAction.MIGRATE_LEGACY, ".scout-state/ present without scout-config.yaml (pre-Plan-8 vault)")
    if _vault_exists(vault):
        return Plan(AutoAction.UPGRADE, "scout-config.yaml present")
    return Plan(
        AutoAction.REFUSED,
        f"{vault} is non-empty but is not a Scout vault (no scout-config.yaml or .scout-state/) — pick an empty folder",
    )


def doctor_dict(report: DoctorReport | None) -> dict[str, Any] | None:
    if report is None:
        return None
    return {"severity": report.severity.value, "errors": list(report.errors), "warnings": list(report.warnings)}


def result_dict(
    *,
    action: AutoAction,
    vault: Path,
    plugin_version: str,
    result: InstallResult | UpgradeResult | MigrateLegacyResult | None,
    error: str | None = None,
    dry_run: bool = False,
    reason: str = "",
) -> dict[str, Any]:
    pointer = getattr(result, "pointer", None)
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "action": action.value,
        "reason": reason,
        "dry_run": dry_run,
        "vault": str(vault),
        "plugin_version": plugin_version,
        "error": error,
        "doctor": doctor_dict(getattr(result, "doctor", None)),
        "conflicts": list(getattr(result, "conflicts", None) or []),
        "backups": list(getattr(result, "backups", None) or []),
        "snapshots_recorded": list(getattr(result, "snapshots_recorded", None) or []),
        "pointer": str(pointer) if pointer else None,
    }


def run(cfg: BootstrapConfig, *, dry_run: bool = False) -> tuple[dict[str, Any], int]:
    """Detect, dispatch, and return ``(result_dict, exit_code)``.

    Exit codes: the doctor's 0/1/2 after a run; 2 when refused; 0 for dry-run.
    """
    plan = detect(cfg.vault)
    common = {"vault": cfg.vault, "plugin_version": cfg.plugin_version, "reason": plan.reason}
    if dry_run:
        return result_dict(action=plan.action, result=None, dry_run=True, **common), 0
    if plan.action is AutoAction.REFUSED:
        return result_dict(action=plan.action, result=None, error=plan.reason, **common), 2
    try:
        if plan.action is AutoAction.INSTALL:
            res: InstallResult | UpgradeResult | MigrateLegacyResult = install(cfg)
        elif plan.action is AutoAction.MIGRATE_LEGACY:
            res = migrate_legacy(cfg)
        else:
            res = upgrade(cfg)
    except (FileExistsError, FileNotFoundError, RuntimeError) as e:
        return result_dict(action=AutoAction.REFUSED, result=None, error=str(e), **common), 2
    return result_dict(action=plan.action, result=res, **common), res.doctor.exit_code
```

- [ ] **Step 4: Run module tests**

Run: `../.venv/bin/pytest tests/unit/test_bootstrap_auto.py -q`
Expected: PASS.

- [ ] **Step 5: Write the failing CLI tests**

`engine/tests/unit/test_cli_bootstrap_subapp.py`:

```python
"""CLI tests for `scoutctl bootstrap auto` and the --json flags (spec E3)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from scout.cli import app

runner = CliRunner()
IDENTITY = ["--user-name", "Alex", "--user-email", "alex@example.com"]
HEADLESS = ["--no-jobs", "--skip-claude", "--no-interactive", "--yes", "--json", "--platform", "macos",
            "--claude-bin", "/usr/local/bin/claude", "--managed-by", "scout-app"]


def _vault(tmp_path: Path, monkeypatch) -> Path:
    v = tmp_path / "Scout"
    monkeypatch.setenv("SCOUT_DATA_DIR", str(v))
    return v


def test_auto_installs_fresh_vault_and_emits_json(tmp_path, monkeypatch):
    vault = _vault(tmp_path, monkeypatch)
    result = runner.invoke(app, ["bootstrap", "auto", *HEADLESS, *IDENTITY])
    assert result.exit_code in (0, 1), result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["action"] == "install"
    assert payload["vault"] == str(vault)
    assert payload["doctor"]["severity"] in ("green", "yellow")
    assert (vault / "scout-config.yaml").exists()


def test_auto_second_run_upgrades(tmp_path, monkeypatch):
    _vault(tmp_path, monkeypatch)
    runner.invoke(app, ["bootstrap", "auto", *HEADLESS, *IDENTITY])
    result = runner.invoke(app, ["bootstrap", "auto", *HEADLESS])
    assert json.loads(result.stdout)["action"] == "upgrade", result.stdout + result.stderr


def test_auto_dry_run_reports_plan_without_touching_disk(tmp_path, monkeypatch):
    vault = _vault(tmp_path, monkeypatch)
    result = runner.invoke(app, ["bootstrap", "auto", *HEADLESS, *IDENTITY, "--dry-run"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True and payload["action"] == "install"
    assert not vault.exists()


def test_auto_non_interactive_without_identity_exits_2(tmp_path, monkeypatch):
    _vault(tmp_path, monkeypatch)
    result = runner.invoke(app, ["bootstrap", "auto", *HEADLESS])
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["action"] == "refused" and "--user-name" in payload["error"]


def test_auto_refuses_non_vault_directory(tmp_path, monkeypatch):
    vault = _vault(tmp_path, monkeypatch)
    vault.mkdir()
    (vault / "notes.txt").write_text("hi")
    result = runner.invoke(app, ["bootstrap", "auto", *HEADLESS, *IDENTITY])
    assert result.exit_code == 2
    assert json.loads(result.stdout)["action"] == "refused"


def test_doctor_json_shape(tmp_path, monkeypatch):
    _vault(tmp_path, monkeypatch)
    runner.invoke(app, ["bootstrap", "auto", *HEADLESS, *IDENTITY])
    result = runner.invoke(app, ["bootstrap", "doctor", "--no-jobs", "--json"])
    payload = json.loads(result.stdout)
    assert set(payload) == {"severity", "errors", "warnings"}


def test_install_json_matches_auto_contract(tmp_path, monkeypatch):
    _vault(tmp_path, monkeypatch)
    result = runner.invoke(app, ["bootstrap", "install", "--no-jobs", "--skip-claude", "--json", *IDENTITY])
    payload = json.loads(result.stdout)
    assert payload["action"] == "install" and "doctor" in payload and "pointer" in payload
```

- [ ] **Step 6: Run to verify failure**

Run: `../.venv/bin/pytest tests/unit/test_cli_bootstrap_subapp.py -q`
Expected: FAIL — `No such command 'auto'` / `No such option: --json`.

- [ ] **Step 7: Implement the CLI**

In `cli.py` `_register_bootstrap`, add two helpers at the top of the function body and the new command; add `json_out: bool = typer.Option(False, "--json", help="Emit the BootstrapResult JSON (consumed by Scout.app).")` to `install`, `upgrade`, `migrate-legacy`, and `--json` to `doctor`.

```python
    def _emit(payload: dict, *, json_out: bool) -> None:
        """One printer for every bootstrap subcommand: JSON on stdout, or the
        human lines. Warnings/errors go to stderr in text mode so a caller that
        captures stdout still gets a clean report."""
        import json as _json

        if json_out:
            typer.echo(_json.dumps(payload, indent=2, sort_keys=True))
            return
        if payload.get("error"):
            typer.echo(f"{payload['action']}: {payload['error']}", err=True)
            return
        typer.echo(f"{payload['action']}: {payload['vault']}")
        for c in payload.get("conflicts", []):
            typer.echo(f"  conflict (sidecar): {c}", err=True)
        for b in payload.get("backups", []):
            typer.echo(f"  backup: {b}", err=True)
        doctor = payload.get("doctor")
        if doctor:
            typer.echo(f"doctor: {doctor['severity']}")
            for w in doctor["warnings"]:
                typer.echo(f"  warning: {w}", err=True)
            for e in doctor["errors"]:
                typer.echo(f"  error: {e}", err=True)

    def _config_from_existing_vault(vault: Path, *, skip_jobs: bool, skip_claude: bool, managed_by: str):
        """BootstrapConfig for an existing vault, read back from scout-config.yaml
        (extracted from the upgrade command so `auto` shares it)."""
        import yaml as _yaml

        from scout import __version__
        from scout.scripts.bootstrap import BootstrapConfig

        cfg_path = vault / "scout-config.yaml"
        try:
            existing = _yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        except (_yaml.YAMLError, UnicodeDecodeError) as e:
            raise ConfigError(f"scout-config.yaml is malformed: {e}") from e
        instance = existing.get("instance", {})
        user = existing.get("user", {})
        return BootstrapConfig(
            vault=vault,
            plugin_root=Path(__file__).parent.parent.parent,
            instance_name=instance.get("name", "Scout"),
            instance_name_lower=instance.get("name_lower", "scout"),
            user_name=user.get("name", ""),
            user_email=user.get("email", ""),
            timezone=existing.get("timezone", "America/New_York"),
            platform=existing.get("platform", "macos"),
            plugin_version=__version__,
            enabled_connectors=set(existing.get("connectors", {}).get("enabled") or []),
            connector_inputs=existing.get("connectors", {}).get("inputs", {}),
            skip_jobs=skip_jobs,
            skip_claude=skip_claude,
            managed_by=managed_by,
        )
```

Refactor `cli_bootstrap_upgrade` to call `_config_from_existing_vault` (behavior unchanged) and to end with `_emit(result_dict(action=AutoAction.UPGRADE, vault=vault, plugin_version=__version__, result=result), json_out=json_out)`; do the same for `install` (`AutoAction.INSTALL`) and `migrate-legacy` (`AutoAction.MIGRATE_LEGACY`), importing `AutoAction, result_dict` from `scout.scripts.bootstrap_auto` inside each function. `doctor --json` prints `json.dumps(doctor_dict(report))`. Then the new command:

```python
    @bootstrap_app.command("auto")
    def cli_bootstrap_auto(
        user_name: str = typer.Option("", "--user-name", help="Required for install / migrate-legacy."),
        user_email: str = typer.Option("", "--user-email", help="Required for install / migrate-legacy."),
        instance_name: str = typer.Option("Scout", "--instance-name"),
        timezone: str = typer.Option("America/New_York", "--timezone"),
        platform_: str = typer.Option("auto", "--platform", help="macos | linux | auto (from uname)"),
        connectors: str = typer.Option("", "--connectors", help="Comma-separated enabled connector names"),
        user_slack_id: str = typer.Option("", "--user-slack-id"),
        github_username: str = typer.Option("", "--github-username"),
        github_repos: str = typer.Option("", "--github-repos"),
        claude_bin: str = typer.Option("auto", "--claude-bin", help="Absolute path, or auto (`command -v claude`)"),
        max_budget: str = typer.Option("5.00", "--max-budget"),
        skip_jobs: bool = typer.Option(False, "--no-jobs"),
        skip_claude: bool = typer.Option(False, "--skip-claude"),
        managed_by: str = typer.Option("unknown", "--managed-by"),
        interactive: bool | None = typer.Option(
            None, "--interactive/--no-interactive",
            help="Prompt for missing identity fields. Default: interactive when stdin is a TTY.",
        ),
        yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
        dry_run: bool = typer.Option(False, "--dry-run", help="Print the detected action and exit 0."),
        json_out: bool = typer.Option(False, "--json", help="Emit the BootstrapResult JSON (consumed by Scout.app)."),
    ) -> None:
        """Detect the vault's state and run install, upgrade or migrate-legacy accordingly (#26)."""
        import platform as _platform
        import shutil

        from scout import __version__
        from scout import paths as _paths
        from scout.scripts.bootstrap import BootstrapConfig
        from scout.scripts.bootstrap_auto import AutoAction, detect, result_dict, run

        vault = _paths.data_dir()
        if interactive is None:
            interactive = sys.stdin.isatty()
        if platform_ == "auto":
            platform_ = {"Darwin": "macos", "Linux": "linux"}.get(_platform.system(), "")
            if not platform_:
                _emit(result_dict(action=AutoAction.REFUSED, vault=vault, plugin_version=__version__, result=None,
                                  error=f"unsupported platform {_platform.system()!r}; pass --platform"), json_out=json_out)
                raise typer.Exit(code=2)
        if claude_bin == "auto":
            claude_bin = shutil.which("claude") or "/usr/local/bin/claude"

        plan = detect(vault)
        needs_identity = plan.action in (AutoAction.INSTALL, AutoAction.MIGRATE_LEGACY)
        if needs_identity and not dry_run:
            if interactive:
                user_name = user_name or typer.prompt("Your name")
                user_email = user_email or typer.prompt("Your email")
            missing = [f for f, v in (("--user-name", user_name), ("--user-email", user_email)) if not v]
            if missing:
                _emit(result_dict(action=AutoAction.REFUSED, vault=vault, plugin_version=__version__, result=None,
                                  error=f"{plan.action.value} needs {' and '.join(missing)} (or run interactively)",
                                  reason=plan.reason), json_out=json_out)
                raise typer.Exit(code=2)

        if plan.action is AutoAction.UPGRADE and (vault / "scout-config.yaml").exists():
            if user_name or user_email:
                typer.echo("note: identity flags are ignored on upgrade (read from scout-config.yaml)", err=True)
            cfg = _config_from_existing_vault(vault, skip_jobs=skip_jobs, skip_claude=skip_claude, managed_by=managed_by)
        else:
            cfg = BootstrapConfig(
                vault=vault,
                plugin_root=Path(__file__).parent.parent.parent,
                instance_name=instance_name,
                instance_name_lower=instance_name.lower().replace(" ", "-"),
                user_name=user_name,
                user_email=user_email,
                timezone=timezone,
                platform=platform_,
                plugin_version=__version__,
                enabled_connectors=set(c.strip() for c in connectors.split(",") if c.strip()),
                connector_inputs={
                    "user_slack_id": user_slack_id,
                    "github_username": github_username,
                    "github_repos": github_repos,
                    "claude_bin": claude_bin,
                    "max_budget": max_budget,
                },
                skip_jobs=skip_jobs,
                skip_claude=skip_claude,
                managed_by=managed_by,
            )

        if not dry_run and not yes and interactive and plan.action is not AutoAction.REFUSED:
            if not typer.confirm(f"About to run bootstrap {plan.action.value} on {vault} ({plan.reason}). Proceed?"):
                raise typer.Exit(code=1)

        payload, code = run(cfg, dry_run=dry_run)
        _emit(payload, json_out=json_out)
        raise typer.Exit(code=code)
```

- [ ] **Step 8: Run everything touched, lint, commit**

Run: `../.venv/bin/pytest tests/unit/test_cli_bootstrap_subapp.py tests/unit/test_bootstrap_auto.py tests/unit/test_cli.py -q && ../.venv/bin/ruff check scout tests && ../.venv/bin/ruff format --check scout tests && ../.venv/bin/mypy scout`
Expected: PASS, clean.

```bash
git add engine/scout/scripts/bootstrap_auto.py engine/scout/cli.py engine/tests/unit/test_bootstrap_auto.py engine/tests/unit/test_cli_bootstrap_subapp.py
git commit -m "feat(engine): scoutctl bootstrap auto + --json on every bootstrap subcommand (E3, closes #26)"
```

### Task A5: `scoutctl connectors detect --json` (E4)

**Files:**
- Create: `engine/scout/scripts/connector_detect.py`, `engine/tests/fixtures/claude-mcp-list.txt`
- Modify: `engine/scout/cli.py:218-302` (`_register_connectors`)
- Test: `engine/tests/unit/test_connector_detect.py` (new), `engine/tests/unit/test_cli_connectors_subapp.py` (add)

**Interfaces:**
- Consumes: `Probe`, `ProbeKind`, `resolve_registry` (connector_probes.py).
- Produces:
  ```python
  class DetectStatus(Enum): CONNECTED="connected"; NEEDS_AUTH="needs_auth"; UNAVAILABLE="unavailable"; UNKNOWN="unknown"
  @dataclass(frozen=True) class Detection: connector: str; status: DetectStatus; needs_user_input: list[str]; evidence: str
  def parse_mcp_list(text: str) -> dict[str, tuple[DetectStatus, str]]      # display name → (status, raw line)
  def server_slug(display_name: str) -> str                                  # "claude.ai Google Calendar" → "claude_ai_Google_Calendar"
  def tool_server_slug(tool: str) -> str | None                              # "mcp__claude_ai_Gmail__list_labels" → "claude_ai_Gmail"
  def detect(registry, *, mcp_list_output: str | None, run_bash: Callable[[str], int]) -> dict[str, Detection]
  def run_claude_mcp_list(claude_bin: str, *, timeout: float = 60.0) -> str | None
  def run_bash_probe(command: str, *, timeout: float = 15.0) -> int
  def to_json_dict(dets: dict[str, Detection]) -> dict[str, dict]
  ```
  CLI: `scoutctl connectors detect --json [--claude-bin PATH] [--timeout SECONDS]` → `{"<connector>": {"status", "needs_user_input", "evidence"}}`. Part C's `ConnectorDetection` decodes this.

- [ ] **Step 1: Write the fixture** (`engine/tests/fixtures/claude-mcp-list.txt`, anonymized)

```text
Checking MCP server health…

[mcp-sdk] SEP-2352: stored OAuth credential has no 'issuer' stamp (pre-upgrade storage). SEP-2352 isolation is inactive for this read.
claude.ai Gmail: https://mcp.example.invalid/gmail - ✔ Connected
claude.ai Google Calendar: https://mcp.example.invalid/calendar - ✔ Connected
claude.ai Slack: https://mcp.example.invalid/slack - ! Needs authentication
claude.ai Granola: https://mcp.example.invalid/granola - ✔ Connected
Some Internal Tool: https://mcp.example.invalid/tool - ✘ Failed to connect — HTTP 503: Error POSTing to endpoint
```

- [ ] **Step 2: Write the failing tests**

`engine/tests/unit/test_connector_detect.py`:

```python
"""Unit tests for engine/scout/scripts/connector_detect.py (spec E4)."""

from __future__ import annotations

from pathlib import Path

from scout.scripts.connector_detect import (
    DetectStatus,
    detect,
    parse_mcp_list,
    server_slug,
    to_json_dict,
    tool_server_slug,
)
from scout.scripts.connector_probes import Probe, ProbeKind

FIXTURE = (Path(__file__).resolve().parents[1] / "fixtures" / "claude-mcp-list.txt").read_text(encoding="utf-8")


def test_parse_mcp_list_reads_one_status_per_server():
    servers = parse_mcp_list(FIXTURE)
    assert servers["claude.ai Gmail"][0] is DetectStatus.CONNECTED
    assert servers["claude.ai Slack"][0] is DetectStatus.NEEDS_AUTH
    assert servers["Some Internal Tool"][0] is DetectStatus.UNAVAILABLE
    assert "Checking MCP server health…" not in servers
    assert len(servers) == 5


def test_server_slug_matches_claude_codes_tool_namespace():
    assert server_slug("claude.ai Google Calendar") == "claude_ai_Google_Calendar"
    assert server_slug("claude.ai Gmail") == "claude_ai_Gmail"
    assert server_slug("fathom") == "fathom"


def test_tool_server_slug_extracts_the_middle_segment():
    assert tool_server_slug("mcp__claude_ai_Gmail__list_labels") == "claude_ai_Gmail"
    assert tool_server_slug("mcp__plugin_slack_slack__slack_read_user_profile") == "plugin_slack_slack"
    assert tool_server_slug("bash") is None


def _mcp(name: str, chain: list[str], needs: list[str] | None = None) -> Probe:
    return Probe(name=name, kind=ProbeKind.MCP_TOOL, tool_chain=chain, needs_user_input=needs or [])


def test_detect_maps_mcp_probes_to_server_status():
    reg = {
        "email": _mcp("email", ["mcp__claude_ai_Gmail__list_labels"]),
        "slack": _mcp("slack", ["mcp__plugin_slack_slack__slack_read_user_profile", "mcp__claude_ai_Slack__slack_read_user_profile"], ["user_slack_id"]),
        "fathom": _mcp("fathom", ["mcp__fathom__list_meetings"]),
    }
    dets = detect(reg, mcp_list_output=FIXTURE, run_bash=lambda cmd: 1)
    assert dets["email"].status is DetectStatus.CONNECTED
    assert dets["slack"].status is DetectStatus.NEEDS_AUTH  # fallback tool matched the claude.ai server
    assert dets["slack"].needs_user_input == ["user_slack_id"]
    assert dets["fathom"].status is DetectStatus.UNKNOWN  # no such server listed


def test_detect_runs_bash_probes_directly():
    reg = {"github": Probe(name="github", kind=ProbeKind.BASH, bash_command="gh auth status", needs_user_input=["github_username"])}
    calls: list[str] = []

    def fake_bash(cmd: str) -> int:
        calls.append(cmd)
        return 0

    dets = detect(reg, mcp_list_output=None, run_bash=fake_bash)
    assert calls == ["gh auth status"]
    assert dets["github"].status is DetectStatus.CONNECTED
    assert dets["github"].evidence == "`gh auth status` exit 0"


def test_detect_is_unknown_not_unavailable_when_mcp_list_failed():
    reg = {"email": _mcp("email", ["mcp__claude_ai_Gmail__list_labels"])}
    dets = detect(reg, mcp_list_output=None, run_bash=lambda cmd: 1)
    assert dets["email"].status is DetectStatus.UNKNOWN
    assert "unavailable" in dets["email"].evidence


def test_to_json_dict_shape():
    reg = {"email": _mcp("email", ["mcp__claude_ai_Gmail__list_labels"])}
    payload = to_json_dict(detect(reg, mcp_list_output=FIXTURE, run_bash=lambda cmd: 1))
    assert payload == {"email": {"status": "connected", "needs_user_input": [], "evidence": "claude.ai Gmail: https://mcp.example.invalid/gmail - ✔ Connected"}}
```

Append to `engine/tests/unit/test_cli_connectors_subapp.py`:

```python
def test_detect_json_is_unknown_for_mcp_probes_when_claude_is_missing():
    """A claude binary that does not exist must degrade to `unknown`, never crash."""
    result = runner.invoke(app, ["connectors", "detect", "--json", "--claude-bin", "/nonexistent/claude"])
    assert result.exit_code == 0, result.stdout + result.stderr
    data = json.loads(result.stdout)
    assert data["slack"]["status"] == "unknown"
    assert data["claude_sessions"]["status"] in ("connected", "unavailable")  # bash probe ran
    assert set(data["github"]) == {"status", "needs_user_input", "evidence"}
```

- [ ] **Step 3: Run to verify failure**

Run: `../.venv/bin/pytest tests/unit/test_connector_detect.py tests/unit/test_cli_connectors_subapp.py -q`
Expected: FAIL — `ModuleNotFoundError` / `No such command 'detect'`.

- [ ] **Step 4: Implement the module**

`engine/scout/scripts/connector_detect.py`:

```python
"""Headless connector detection for Scout.app onboarding (spec E4).

Replaces the LLM-mediated probe loop in /scout-setup: ``bash`` probes run
directly; ``mcp_tool`` probes are answered by ``claude mcp list``, whose one
line per server carries a status glyph. Detection is a hint the user confirms
in the UI — anything we cannot map is ``unknown``, never ``unavailable``.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from scout.scripts.connector_probes import Probe, ProbeKind


class DetectStatus(Enum):
    CONNECTED = "connected"
    NEEDS_AUTH = "needs_auth"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Detection:
    connector: str
    status: DetectStatus
    needs_user_input: list[str]
    evidence: str


# One `claude mcp list` server line: "<name>: <url or command> - <glyph> <text>".
# Banner lines and "[mcp-sdk] …" warnings do not match and are ignored.
_LINE = re.compile(r"^(?P<name>[^:]+?):\s.*?\s-\s(?P<glyph>[✔✓!✘✗])\s*(?P<text>.*)$")
_GLYPH = {
    "✔": DetectStatus.CONNECTED,
    "✓": DetectStatus.CONNECTED,
    "!": DetectStatus.NEEDS_AUTH,
    "✘": DetectStatus.UNAVAILABLE,
    "✗": DetectStatus.UNAVAILABLE,
}


def parse_mcp_list(text: str) -> dict[str, tuple[DetectStatus, str]]:
    """Server display name → (status, the raw line it came from)."""
    out: dict[str, tuple[DetectStatus, str]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        m = _LINE.match(line)
        if m:
            out[m["name"].strip()] = (_GLYPH[m["glyph"]], line)
    return out


def server_slug(display_name: str) -> str:
    """Claude Code's tool-name segment for a server: runs of non-alphanumerics → '_'.

    Observed: tool ``mcp__claude_ai_Google_Calendar__list_calendars`` belongs to
    the server listed as ``claude.ai Google Calendar``.
    """
    return re.sub(r"[^A-Za-z0-9]+", "_", display_name).strip("_")


def tool_server_slug(tool: str) -> str | None:
    """``mcp__<server>__<tool>`` → ``<server>``; None for anything else."""
    if not tool.startswith("mcp__"):
        return None
    slug, sep, _tool = tool[len("mcp__") :].rpartition("__")
    return slug if sep else None


def _match_server(tool: str, servers: dict[str, tuple[DetectStatus, str]]) -> tuple[DetectStatus, str] | None:
    want = tool_server_slug(tool)
    if not want:
        return None
    by_slug = {server_slug(name): status for name, status in servers.items()}
    if want in by_slug:
        return by_slug[want]
    lowered = {k.lower(): v for k, v in by_slug.items()}
    return lowered.get(want.lower())


def detect(
    registry: dict[str, Probe],
    *,
    mcp_list_output: str | None,
    run_bash: Callable[[str], int],
) -> dict[str, Detection]:
    servers = parse_mcp_list(mcp_list_output) if mcp_list_output is not None else None
    out: dict[str, Detection] = {}
    for name in sorted(registry):
        probe = registry[name]
        needs = list(probe.needs_user_input)
        if probe.kind is ProbeKind.BASH:
            rc = run_bash(probe.bash_command)
            status = DetectStatus.CONNECTED if rc == 0 else DetectStatus.UNAVAILABLE
            out[name] = Detection(name, status, needs, f"`{probe.bash_command}` exit {rc}")
            continue
        if servers is None:
            out[name] = Detection(name, DetectStatus.UNKNOWN, needs, "`claude mcp list` unavailable")
            continue
        best: tuple[DetectStatus, str] | None = None
        for tool in probe.tool_chain:
            hit = _match_server(tool, servers)
            if hit is None:
                continue
            if best is None or hit[0] is DetectStatus.CONNECTED:
                best = hit
            if hit[0] is DetectStatus.CONNECTED:
                break
        if best is None:
            out[name] = Detection(name, DetectStatus.UNKNOWN, needs, "no matching MCP server in `claude mcp list`")
        else:
            out[name] = Detection(name, best[0], needs, best[1])
    return out


def run_claude_mcp_list(claude_bin: str, *, timeout: float = 60.0) -> str | None:
    """stdout of `claude mcp list`, or None when the CLI is missing, fails, or hangs."""
    try:
        proc = subprocess.run([claude_bin, "mcp", "list"], capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def run_bash_probe(command: str, *, timeout: float = 15.0) -> int:
    try:
        return subprocess.run(command, shell=True, capture_output=True, timeout=timeout, check=False).returncode
    except (OSError, subprocess.SubprocessError):
        return 1


def to_json_dict(dets: dict[str, Detection]) -> dict[str, dict[str, Any]]:
    return {
        name: {"status": d.status.value, "needs_user_input": d.needs_user_input, "evidence": d.evidence}
        for name, d in dets.items()
    }
```

- [ ] **Step 5: Add the CLI command**

In `_register_connectors`, after `probe-registry`:

```python
    @connectors_app.command("detect")
    def cli_connectors_detect(
        json_out: bool = typer.Option(False, "--json", help="Emit detections as JSON (consumed by Scout.app onboarding)."),
        claude_bin: str = typer.Option("claude", "--claude-bin", help="Claude Code binary used for `claude mcp list`."),
        timeout: float = typer.Option(60.0, "--timeout", help="Seconds to wait for `claude mcp list`."),
    ) -> None:
        """Detect which connectors are reachable right now, without an LLM (spec E4)."""
        import json as _json

        from scout.scripts.connector_detect import detect, run_bash_probe, run_claude_mcp_list, to_json_dict
        from scout.scripts.connector_probes import resolve_registry

        reg = resolve_registry()
        dets = detect(reg, mcp_list_output=run_claude_mcp_list(claude_bin, timeout=timeout), run_bash=run_bash_probe)
        if json_out:
            typer.echo(_json.dumps(to_json_dict(dets), indent=2, sort_keys=True))
        else:
            for name, d in dets.items():
                typer.echo(f"{name}\t{d.status.value}\t{d.evidence}")
```

- [ ] **Step 6: Run, lint, commit**

Run: `../.venv/bin/pytest tests/unit/test_connector_detect.py tests/unit/test_cli_connectors_subapp.py -q && ../.venv/bin/ruff check scout tests && ../.venv/bin/mypy scout`
Expected: PASS.

```bash
git add engine/scout/scripts/connector_detect.py engine/scout/cli.py engine/tests/fixtures/claude-mcp-list.txt engine/tests/unit/test_connector_detect.py engine/tests/unit/test_cli_connectors_subapp.py
git commit -m "feat(engine): scoutctl connectors detect --json — headless probes via claude mcp list (E4)"
```

### Task A6: `install-venv.sh` — uv, `SCOUT_VENV_DIR`, extras (E5)

**Files:**
- Modify: `scripts/install-venv.sh`
- Test: `engine/tests/unit/test_install_venv_script.py` (new)

**Interfaces:**
- Produces: env contract `SCOUT_VENV_DIR` (default `<plugin-root>/.venv`), `SCOUT_UV` (explicit uv path), `SCOUT_VENV_EXTRAS` (default `dev`), `SCOUT_PYTHON_VERSION` (default `3.12`, uv path only). Part C's `EngineInstaller.venvEnvironment` sets `SCOUT_VENV_DIR`, `SCOUT_UV`, `SCOUT_VENV_EXTRAS=full`.

- [ ] **Step 1: Write the failing test**

`engine/tests/unit/test_install_venv_script.py`:

```python
"""Behavioral test for scripts/install-venv.sh with a fake uv (spec E5)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from textwrap import dedent

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "install-venv.sh"
PLUGIN_ROOT = SCRIPT.parents[1]


def _fake_uv(tmp_path: Path) -> Path:
    """Records every invocation and fakes the two things the script needs:
    `uv venv` creates bin/python, `uv pip install` creates bin/scoutctl."""
    uv = tmp_path / "fakebin" / "uv"
    uv.parent.mkdir()
    uv.write_text(
        dedent(
            """\
            #!/bin/bash
            echo "uv $*" >> "$FAKE_UV_LOG"
            case "$1" in
              venv)
                target="${@: -1}"
                mkdir -p "$target/bin" && printf '#!/bin/sh\\n' > "$target/bin/python" && chmod +x "$target/bin/python" ;;
              pip)
                py=""; while [ $# -gt 0 ]; do [ "$1" = "--python" ] && py="$2"; shift; done
                printf '#!/bin/sh\\n' > "$(dirname "$py")/scoutctl" && chmod +x "$(dirname "$py")/scoutctl" ;;
            esac
            """
        ),
        encoding="utf-8",
    )
    uv.chmod(0o755)
    return uv


def test_uses_uv_into_scout_venv_dir_with_requested_extras(tmp_path):
    uv = _fake_uv(tmp_path)
    venv = tmp_path / "share" / "scout" / "venv" / "0.10.0"
    log = tmp_path / "uv.log"
    proc = subprocess.run(
        ["bash", str(SCRIPT)],
        env={
            "HOME": str(tmp_path / "home"), "PATH": "/usr/bin:/bin",
            "SCOUT_UV": str(uv), "SCOUT_VENV_DIR": str(venv), "SCOUT_VENV_EXTRAS": "full", "FAKE_UV_LOG": str(log),
        },
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    lines = log.read_text().splitlines()
    assert lines[0] == f"uv venv --python 3.12 {venv}"
    assert lines[1] == f"uv pip install --python {venv}/bin/python --quiet -e {PLUGIN_ROOT}/engine[full]"
    assert (venv / "bin" / "scoutctl").exists()
    assert f"ok: venv ready at {venv}" in proc.stdout


def test_defaults_to_plugin_root_venv_and_dev_extras(tmp_path, monkeypatch):
    """Default location is unchanged so /scout-update keeps working; run the
    script from a COPY of the tree so the developer's real .venv is untouched."""
    import shutil

    root = tmp_path / "plugin"
    (root / "engine").mkdir(parents=True)
    (root / "scripts").mkdir()
    shutil.copy(SCRIPT, root / "scripts" / "install-venv.sh")
    uv = _fake_uv(tmp_path)
    log = tmp_path / "uv.log"
    proc = subprocess.run(
        ["bash", str(root / "scripts" / "install-venv.sh")],
        env={"HOME": str(tmp_path / "home"), "PATH": "/usr/bin:/bin", "SCOUT_UV": str(uv), "FAKE_UV_LOG": str(log)},
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"uv venv --python 3.12 {root}/.venv" in log.read_text()
    assert f"-e {root}/engine[dev]" in log.read_text()
```

- [ ] **Step 2: Run to verify failure**

Run: `../.venv/bin/pytest tests/unit/test_install_venv_script.py -q`
Expected: FAIL — the current script ignores `SCOUT_UV`/`SCOUT_VENV_DIR` and tries `python3.13 -m venv` into `<root>/.venv`.

- [ ] **Step 3: Rewrite the script**

Replace `scripts/install-venv.sh` with:

```bash
#!/bin/bash
# Build the scout-engine venv.
#
# Location:  $SCOUT_VENV_DIR (default: <plugin-root>/.venv). Scout.app passes
#            ~/.local/share/scout/venv/<version> so the venv lives OUTSIDE the
#            plugin tree Claude Code copies into its cache (spec §4.1).
# Builder:   uv when available ($SCOUT_UV, `uv` on PATH, or ~/.local/bin/uv).
#            uv downloads a managed CPython, so this works on Macs whose only
#            python3 is Apple's 3.9. Falls back to `python3.1x -m venv` + pip.
# Extras:    $SCOUT_VENV_EXTRAS (default: dev). Scout.app passes `full`.
# Python:    $SCOUT_PYTHON_VERSION (default: 3.12) — uv path only.
#
# Usage: [SCOUT_VENV_DIR=…] bash <plugin-root>/scripts/install-venv.sh
# The plugin root is derived from this script's own location, so it works
# whether the script lives under ~/.claude/plugins/…, ~/.local/share/scout/
# engine/<v>/, or a hand-cloned ~/scout-plugin.

set -euo pipefail

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${SCOUT_VENV_DIR:-$PLUGIN_ROOT/.venv}"
EXTRAS="${SCOUT_VENV_EXTRAS:-dev}"
PY_VERSION="${SCOUT_PYTHON_VERSION:-3.12}"

if [ ! -d "$PLUGIN_ROOT/engine" ]; then
    echo "error: engine directory not found at $PLUGIN_ROOT/engine" >&2
    exit 1
fi

UV="${SCOUT_UV:-}"
if [ -z "$UV" ] && command -v uv >/dev/null 2>&1; then UV="$(command -v uv)"; fi
if [ -z "$UV" ] && [ -x "${HOME:-/nonexistent}/.local/bin/uv" ]; then UV="$HOME/.local/bin/uv"; fi

if [ -d "$VENV" ]; then
    echo "venv already exists at $VENV — recreating..."
    rm -rf "$VENV"
fi
mkdir -p "$(dirname "$VENV")"

if [ -n "$UV" ]; then
    echo "using uv ($UV), python $PY_VERSION"
    "$UV" venv --python "$PY_VERSION" "$VENV"
    echo "installing scout-engine[$EXTRAS] in editable mode..."
    "$UV" pip install --python "$VENV/bin/python" --quiet -e "$PLUGIN_ROOT/engine[$EXTRAS]"
else
    # No uv: pick a Python >= 3.11 (engine[requires-python]). Apple's bundled
    # /usr/bin/python3 is 3.9 on every macOS we support, so try explicit minors.
    PYTHON=""
    for candidate in python3.13 python3.12 python3.11; do
        if command -v "$candidate" >/dev/null 2>&1; then PYTHON="$candidate"; break; fi
    done
    if [ -z "$PYTHON" ] && command -v python3 >/dev/null 2>&1; then
        if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
            PYTHON="python3"
        fi
    fi
    if [ -z "$PYTHON" ]; then
        cat >&2 <<EOF
error: neither uv nor a Python >= 3.11 was found on PATH.
Install uv (https://docs.astral.sh/uv) — it downloads Python for you — or one of:
  macOS:   brew install python@3.13
  Debian:  sudo apt install python3.13 python3.13-venv
then re-run: bash $PLUGIN_ROOT/scripts/install-venv.sh
EOF
        exit 1
    fi
    echo "using $PYTHON ($("$PYTHON" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])'))"
    "$PYTHON" -m venv "$VENV"
    echo "installing scout-engine[$EXTRAS] in editable mode (this may take 30-60s)..."
    "$VENV/bin/pip" install --quiet --upgrade pip
    "$VENV/bin/pip" install --quiet -e "$PLUGIN_ROOT/engine[$EXTRAS]"
fi

if [ ! -x "$VENV/bin/scoutctl" ]; then
    echo "error: scoutctl not found at $VENV/bin/scoutctl after install" >&2
    exit 1
fi

echo "ok: venv ready at $VENV"
echo "verify: $VENV/bin/scoutctl version"
```

- [ ] **Step 4: Run test + shellcheck, commit**

Run: `../.venv/bin/pytest tests/unit/test_install_venv_script.py -q && shellcheck -S error ../scripts/install-venv.sh`
Expected: PASS, clean.

```bash
git add scripts/install-venv.sh engine/tests/unit/test_install_venv_script.py
git commit -m "feat(engine): install-venv.sh builds with uv into SCOUT_VENV_DIR (E5)"
```

### Task A7: Plists carry `SCOUT_DATA_DIR` (E6)

**Files:**
- Modify: `engine/scout/defaults/com.scout.schedule-tick.plist`, `engine/scout/defaults/com.scout.heartbeat.plist`
- Modify: `engine/scout/scripts/install_schedule_plist.py:42-64`, `engine/scout/scripts/install_heartbeat_plist.py:19-38`
- Modify: `engine/scout/scripts/bootstrap.py::_stage_jobs_install`, `engine/scout/cli.py` (`schedule install-plist`, `install-heartbeat-plist`, `install-all` — every `install_plist(...)` call gains `vault=_paths.data_dir()`)
- Test: `engine/tests/unit/test_install_schedule_plist.py`, `engine/tests/unit/test_install_heartbeat_plist.py`

**Interfaces:**
- Produces: `install_plist(*, home, agents_dir=None, force=False, bootstrap=False, vault: Path | None = None)` in both modules; `vault` defaults to `home / "Scout"`. Templates use `__SCOUT_DIR__` for every vault path and set `EnvironmentVariables.SCOUT_DATA_DIR`.

- [ ] **Step 1: Write the failing tests** (append to both plist test files)

```python
def test_install_plist_renders_vault_into_env_and_paths(tmp_path):
    target_dir = tmp_path / "LaunchAgents"
    target_dir.mkdir()
    vault = tmp_path / "Vaults" / "Work"
    install_plist(home=tmp_path, agents_dir=target_dir, vault=vault)
    content = (target_dir / PLIST_NAME).read_text()
    assert "__SCOUT_DIR__" not in content
    assert f"<key>SCOUT_DATA_DIR</key>\n        <string>{vault}</string>" in content
    assert f"{vault}/.scout-logs/" in content
    assert f"{tmp_path}/Scout" not in content


def test_install_plist_vault_defaults_to_home_scout(tmp_path):
    target_dir = tmp_path / "LaunchAgents"
    target_dir.mkdir()
    install_plist(home=tmp_path, agents_dir=target_dir)
    content = (target_dir / PLIST_NAME).read_text()
    assert f"<string>{tmp_path}/Scout</string>" in content
```

(Import `PLIST_NAME` from the module under test in each file.)

- [ ] **Step 2: Run to verify failure**

Run: `../.venv/bin/pytest tests/unit/test_install_schedule_plist.py tests/unit/test_install_heartbeat_plist.py -q`
Expected: FAIL — `TypeError: unexpected keyword 'vault'`.

- [ ] **Step 3: Update the templates**

In `com.scout.schedule-tick.plist`: replace both `__USER_HOME__/Scout/.scout-logs/…` strings with `__SCOUT_DIR__/.scout-logs/…`, and inside `EnvironmentVariables` add after the `HOME` pair:

```xml
        <key>SCOUT_DATA_DIR</key>
        <string>__SCOUT_DIR__</string>
```

In `com.scout.heartbeat.plist`: `ProgramArguments[1]` → `__SCOUT_DIR__/scripts/heartbeat.sh`, `WorkingDirectory` → `__SCOUT_DIR__`, both log paths → `__SCOUT_DIR__/.scout-logs/launchd-heartbeat.log`, and the same `SCOUT_DATA_DIR` pair. Update each header comment: "fills `__USER_HOME__` and `__SCOUT_DIR__` at install time".

- [ ] **Step 4: Update both installers**

In both `install_plist` signatures add `vault: Path | None = None`; first line of the body `vault = vault or (home / "Scout")`; add `.replace("__SCOUT_DIR__", escape(str(vault), quote=True))` to the render chain (both). In `bootstrap.py::_stage_jobs_install` pass `vault=cfg.vault` to `install_st(...)` and `install_hb(...)`. In `cli.py` every `install_plist(`/`install_heartbeat` call in `_register_schedule` gains `vault=_paths.data_dir()` (import `paths as _paths` locally where missing).

- [ ] **Step 5: Run, lint, commit**

Run: `../.venv/bin/pytest tests/unit/test_install_schedule_plist.py tests/unit/test_install_heartbeat_plist.py tests/unit/test_bootstrap_install.py tests/unit/test_cli_schedule_subapp.py -q && ../.venv/bin/ruff check scout tests && ../.venv/bin/mypy scout`
Expected: PASS.

```bash
git add engine/scout/defaults/com.scout.schedule-tick.plist engine/scout/defaults/com.scout.heartbeat.plist engine/scout/scripts/install_schedule_plist.py engine/scout/scripts/install_heartbeat_plist.py engine/scout/scripts/bootstrap.py engine/scout/cli.py engine/tests/unit/test_install_schedule_plist.py engine/tests/unit/test_install_heartbeat_plist.py
git commit -m "feat(engine): launchd plists carry SCOUT_DATA_DIR for non-default vault roots (E6)"
```

### Task A8: Docs, changelog, PR, release v0.10.0

**Files:**
- Modify: `CHANGELOG.md` (`## [Unreleased]`), `README.md` (Install section), `commands/scout-setup.md` (Step 0 note)
- Owner of the release steps: maintainer (branch protection requires the PR flow).

- [ ] **Step 1: Changelog** — under `## [Unreleased]` add:

```markdown
### Added
- `scoutctl bootstrap auto` — detects vault state and dispatches install / upgrade / migrate-legacy; `--json`, `--dry-run`, `--no-interactive`, `--yes` (#26). `--json` also on `install`, `upgrade`, `migrate-legacy`, `doctor`.
- Engine pointer `~/.local/state/scout/engine.json`, written by every bootstrap; `--managed-by`; the `scoutctl` launcher and the doctor read it.
- `scoutctl connectors detect --json` — headless connector detection via `claude mcp list` (Scout.app onboarding).
- `scripts/install-venv.sh`: builds with `uv` when present; `SCOUT_VENV_DIR`, `SCOUT_VENV_EXTRAS`, `SCOUT_PYTHON_VERSION`, `SCOUT_UV`.
### Changed
- `resolve_scoutctl_bin()` names the scoutctl beside the running interpreter instead of `<plugin-root>/.venv/bin/scoutctl`.
- launchd plists set `SCOUT_DATA_DIR` and render every vault path from the bootstrapped vault.
```

- [ ] **Step 2: README** — in *Install*, above the `curl` block add: "**macOS:** install [Scout.app](https://github.com/Raven-Scout/Scout/releases) — it installs this plugin, its engine and your vault for you (from Scout.app 0.13). The one-liner below is the terminal / Linux path."

- [ ] **Step 3: `commands/scout-setup.md`** — after Step 0's venv check add one line: "If `~/.local/state/scout/engine.json` exists with `managed_by: scout-app`, tell the user Scout.app manages this engine and to run setup from the app; stop."

- [ ] **Step 4: Full suite + PR**

Run: `../.venv/bin/pytest -q -m "not slow"` then `git push -u origin feat/app-managed-engine` and open the PR on `Raven-Scout/scout-plugin` titled `feat(engine): app-managed engine support — pointer, bootstrap auto, connectors detect, uv venv, SCOUT_DATA_DIR (E1–E6)` linking Scout#104.

- [ ] **Step 5: Release (maintainer)** — after merge: `scripts/release.sh minor` (prepare PR → merge) then `scripts/release.sh --finalize v0.10.0`. Record the tag's commit: `git ls-remote https://github.com/Raven-Scout/scout-plugin.git 'refs/tags/v0.10.0^{}'` — Part C's Task C1 pins it.

---

# Part B — Scout.app adopts the engine (spec §4.4 `EngineLocator`, `EngineHealthService`; §5 Settings ▸ Engine; phase 1 of §10)

Branch `feat/app-managed-engine` in this repo. Works against **any** engine version (pointer-aware, legacy-discovering); ships before Part C so every existing install stops seeing blanks.

### Task B1: `EngineLayout`, `EnginePointer`, `ClaudePluginsRegistry`

**Files:**
- Create: `Scout/Engine/EngineLayout.swift`, `Scout/Engine/EnginePointer.swift`, `Scout/Engine/ClaudePluginsRegistry.swift`
- Create fixtures: `ScoutTests/Fixtures/engine/engine.json`, `ScoutTests/Fixtures/claude-plugins/installed_plugins.json`, `ScoutTests/Fixtures/claude-plugins/known_marketplaces.json`
- Test: `ScoutTests/Engine/EngineLayoutTests.swift`, `ScoutTests/Engine/EnginePointerTests.swift`, `ScoutTests/Engine/ClaudePluginsRegistryTests.swift`

**Interfaces (produced):**

```swift
struct EngineLayout: Equatable, Sendable {
    let home: URL
    var shareDir, engineDir, venvDir, currentEngineLink, stateDir, pointerURL, installLogURL, localBin, shimURL, uvURL, claudePluginsDir, devCheckout: URL
    func engineRoot(version: String) -> URL; func venv(version: String) -> URL; func scoutctl(version: String) -> URL
    static let live: EngineLayout
}
struct EnginePointer: Codable, Equatable, Sendable { schemaVersion: Int; version, engineRoot, python, scoutctl, vault, managedBy, writtenAt: String
    static let supportedSchemaVersion = 1
    static func decode(_ data: Data) throws -> EnginePointer     // throws on wrong schema
    static func load(from url: URL) -> EnginePointer?            // nil on missing / malformed / wrong schema
}
struct InstalledPlugin: Equatable, Sendable { let id: String; let version: String; let installPath: String }
enum MarketplaceSource: Equatable, Sendable { case directory(path: String), github(repo: String), git(url: String), other(String) }
struct KnownMarketplace: Equatable, Sendable { let name: String; let source: MarketplaceSource; let installLocation: String? }
enum ClaudePluginsRegistry {
    static let scoutPluginID = "scout@scout-plugin"; static let scoutMarketplaceName = "scout-plugin"
    static func installedPlugins(from data: Data) throws -> [InstalledPlugin]
    static func knownMarketplaces(from data: Data) throws -> [KnownMarketplace]
    static func scoutPlugin(pluginsDir: URL) -> InstalledPlugin?
    static func scoutMarketplace(pluginsDir: URL) -> KnownMarketplace?
}
```

- [ ] **Step 1: Write the fixtures**

`ScoutTests/Fixtures/engine/engine.json`:

```json
{
  "engine_root": "/Users/alex/.local/share/scout/engine/0.10.0",
  "managed_by": "scout-app",
  "python": "/Users/alex/.local/share/scout/venv/0.10.0/bin/python",
  "schema_version": 1,
  "scoutctl": "/Users/alex/.local/share/scout/venv/0.10.0/bin/scoutctl",
  "vault": "/Users/alex/Scout",
  "version": "0.10.0",
  "written_at": "2026-09-08T14:02:11Z"
}
```

`ScoutTests/Fixtures/claude-plugins/installed_plugins.json`:

```json
{
  "version": 2,
  "plugins": {
    "scout@scout-plugin": [
      { "scope": "user", "installPath": "/Users/alex/.claude/plugins/cache/scout-plugin/scout/0.9.0", "version": "0.9.0",
        "installedAt": "2026-05-09T11:18:42.951Z", "lastUpdated": "2026-07-13T19:43:57.613Z", "gitCommitSha": "0000000000000000000000000000000000000000" }
    ],
    "other@example-marketplace": [
      { "scope": "user", "installPath": "/Users/alex/.claude/plugins/cache/example-marketplace/other/1.0.0", "version": "1.0.0" }
    ]
  }
}
```

`ScoutTests/Fixtures/claude-plugins/known_marketplaces.json`:

```json
{
  "scout-plugin": { "source": { "source": "github", "repo": "example-org/scout-plugin" },
                    "installLocation": "/Users/alex/.claude/plugins/marketplaces/scout-plugin", "lastUpdated": "2026-07-13T19:43:40.507Z" },
  "example-marketplace": { "source": { "source": "directory", "path": "/Users/alex/example-marketplace" },
                           "installLocation": "/Users/alex/example-marketplace", "lastUpdated": "2026-07-13T19:43:40.507Z" }
}
```

- [ ] **Step 2: Write the failing tests**

`ScoutTests/Engine/EngineLayoutTests.swift`:

```swift
import Testing
import Foundation
@testable import Scout

@Suite("EngineLayout")
struct EngineLayoutTests {
    let layout = EngineLayout(home: URL(fileURLWithPath: "/Users/alex"))

    @Test func canonicalPathsMatchTheSpec() {
        #expect(layout.engineDir.path == "/Users/alex/.local/share/scout/engine")
        #expect(layout.currentEngineLink.path == "/Users/alex/.local/share/scout/engine/current")
        #expect(layout.venvDir.path == "/Users/alex/.local/share/scout/venv")
        #expect(layout.pointerURL.path == "/Users/alex/.local/state/scout/engine.json")
        #expect(layout.installLogURL.path == "/Users/alex/.local/state/scout/install.log")
        #expect(layout.shimURL.path == "/Users/alex/.local/bin/scoutctl")
        #expect(layout.uvURL.path == "/Users/alex/.local/bin/uv")
        #expect(layout.claudePluginsDir.path == "/Users/alex/.claude/plugins")
        #expect(layout.devCheckout.path == "/Users/alex/scout-plugin")
    }

    @Test func versionedPaths() {
        #expect(layout.engineRoot(version: "0.10.0").path == "/Users/alex/.local/share/scout/engine/0.10.0")
        #expect(layout.venv(version: "0.10.0").path == "/Users/alex/.local/share/scout/venv/0.10.0")
        #expect(layout.scoutctl(version: "0.10.0").path == "/Users/alex/.local/share/scout/venv/0.10.0/bin/scoutctl")
    }
}
```

`ScoutTests/Engine/EnginePointerTests.swift`:

```swift
import Testing
import Foundation
@testable import Scout

@Suite("EnginePointer")
struct EnginePointerTests {
    static let fixtures = Bundle(for: FixtureAnchor.self).resourceURL!.appendingPathComponent("Fixtures/engine")

    @Test func decodesTheEngineJsonWrittenByScoutctl() throws {
        let data = try Data(contentsOf: Self.fixtures.appendingPathComponent("engine.json"))
        let p = try EnginePointer.decode(data)
        #expect(p.schemaVersion == 1)
        #expect(p.version == "0.10.0")
        #expect(p.engineRoot == "/Users/alex/.local/share/scout/engine/0.10.0")
        #expect(p.scoutctl == "/Users/alex/.local/share/scout/venv/0.10.0/bin/scoutctl")
        #expect(p.vault == "/Users/alex/Scout")
        #expect(p.managedBy == "scout-app")
    }

    @Test func rejectsUnknownSchemaVersion() {
        let data = #"{"schema_version": 2, "version": "x", "engine_root": "", "python": "", "scoutctl": "", "vault": "", "managed_by": "", "written_at": ""}"#.data(using: .utf8)!
        #expect(throws: (any Error).self) { try EnginePointer.decode(data) }
    }

    @Test func loadReturnsNilForMissingOrMalformed() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        #expect(EnginePointer.load(from: dir.appendingPathComponent("missing.json")) == nil)
        let bad = dir.appendingPathComponent("bad.json")
        try "{not json".write(to: bad, atomically: true, encoding: .utf8)
        #expect(EnginePointer.load(from: bad) == nil)
    }
}
```

`ScoutTests/Engine/ClaudePluginsRegistryTests.swift`:

```swift
import Testing
import Foundation
@testable import Scout

@Suite("ClaudePluginsRegistry")
struct ClaudePluginsRegistryTests {
    static let dir = Bundle(for: FixtureAnchor.self).resourceURL!.appendingPathComponent("Fixtures/claude-plugins")

    @Test func parsesInstalledPluginsV2() throws {
        let plugins = try ClaudePluginsRegistry.installedPlugins(from: Data(contentsOf: Self.dir.appendingPathComponent("installed_plugins.json")))
        let scout = plugins.first { $0.id == "scout@scout-plugin" }
        #expect(scout?.version == "0.9.0")
        #expect(scout?.installPath == "/Users/alex/.claude/plugins/cache/scout-plugin/scout/0.9.0")
        #expect(plugins.count == 2)
    }

    @Test func parsesKnownMarketplaceSources() throws {
        let markets = try ClaudePluginsRegistry.knownMarketplaces(from: Data(contentsOf: Self.dir.appendingPathComponent("known_marketplaces.json")))
        #expect(markets.first { $0.name == "scout-plugin" }?.source == .github(repo: "example-org/scout-plugin"))
        #expect(markets.first { $0.name == "example-marketplace" }?.source == .directory(path: "/Users/alex/example-marketplace"))
    }

    @Test func scoutLookupsReadFromAPluginsDir() {
        #expect(ClaudePluginsRegistry.scoutPlugin(pluginsDir: Self.dir)?.version == "0.9.0")
        #expect(ClaudePluginsRegistry.scoutMarketplace(pluginsDir: Self.dir)?.installLocation == "/Users/alex/.claude/plugins/marketplaces/scout-plugin")
        #expect(ClaudePluginsRegistry.scoutPlugin(pluginsDir: URL(fileURLWithPath: "/nonexistent")) == nil)
    }

    @Test func malformedJsonThrows() {
        #expect(throws: (any Error).self) { try ClaudePluginsRegistry.installedPlugins(from: "nope".data(using: .utf8)!) }
    }
}
```

- [ ] **Step 3: Run to verify failure**

Run: `xcodebuild test -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/EngineLayoutTests CODE_SIGNING_ALLOWED=NO 2>&1 | tail -20`
Expected: build error — `EngineLayout` undefined.

- [ ] **Step 4: Implement**

`Scout/Engine/EngineLayout.swift`:

```swift
import Foundation

/// Where an app-managed engine lives (spec §4.1). Every path derives from
/// `home` so tests point it at a temp directory. Mirrors scout-plugin's
/// `engine_pointer.py` and the `~/.local/{share,state,bin}` conventions Claude
/// Code itself uses (`~/.local/bin/claude`, `~/.local/share/claude/versions/`).
struct EngineLayout: Equatable, Sendable {
    let home: URL

    var shareDir: URL { home.appending(path: ".local/share/scout") }
    var engineDir: URL { shareDir.appending(path: "engine") }
    var venvDir: URL { shareDir.appending(path: "venv") }
    var currentEngineLink: URL { engineDir.appending(path: "current") }
    var stateDir: URL { home.appending(path: ".local/state/scout") }
    var pointerURL: URL { stateDir.appending(path: "engine.json") }
    var installLogURL: URL { stateDir.appending(path: "install.log") }
    var localBin: URL { home.appending(path: ".local/bin") }
    /// The shim `scoutctl bootstrap` writes; also the placeholder executable
    /// AppState hands services when no engine is found (ENOENT → clear error).
    var shimURL: URL { localBin.appending(path: "scoutctl") }
    var uvURL: URL { localBin.appending(path: "uv") }
    var claudePluginsDir: URL { home.appending(path: ".claude/plugins") }
    /// The maintainer's dev checkout; adopted read-only (spec §10).
    var devCheckout: URL { home.appending(path: "scout-plugin") }

    func engineRoot(version: String) -> URL { engineDir.appending(path: version) }
    func venv(version: String) -> URL { venvDir.appending(path: version) }
    func scoutctl(version: String) -> URL { venv(version: version).appending(path: "bin/scoutctl") }

    static let live = EngineLayout(home: FileManager.default.homeDirectoryForCurrentUser)
}
```

`Scout/Engine/EnginePointer.swift`:

```swift
import Foundation

/// Mirror of `~/.local/state/scout/engine.json` (spec §4.2), written by every
/// `scoutctl bootstrap …`. The app only reads it.
struct EnginePointer: Codable, Equatable, Sendable {
    let schemaVersion: Int
    let version: String
    let engineRoot: String
    let python: String
    let scoutctl: String
    let vault: String
    let managedBy: String
    let writtenAt: String

    static let supportedSchemaVersion = 1

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version", version, engineRoot = "engine_root", python, scoutctl, vault
        case managedBy = "managed_by", writtenAt = "written_at"
    }

    struct UnsupportedSchema: Error, Equatable { let found: Int }

    static func decode(_ data: Data) throws -> EnginePointer {
        let p = try JSONDecoder().decode(EnginePointer.self, from: data)
        guard p.schemaVersion == supportedSchemaVersion else { throw UnsupportedSchema(found: p.schemaVersion) }
        return p
    }

    /// nil on missing, unreadable, malformed, or unknown schema — callers fall
    /// back to discovery (spec §4.2 "authoritative but not exclusive").
    static func load(from url: URL) -> EnginePointer? {
        guard let data = try? Data(contentsOf: url) else { return nil }
        return try? decode(data)
    }
}
```

`Scout/Engine/ClaudePluginsRegistry.swift`:

```swift
import Foundation

/// Read-only view of Claude Code's plugin bookkeeping under `~/.claude/plugins/`.
/// Shapes verified against Claude Code 2.1.259 (`installed_plugins.json`
/// schema version 2; `known_marketplaces.json` keyed by marketplace name).
/// #74's `PluginManifests` parses the same files for update checks — whichever
/// lands second should dedupe onto one type.
struct InstalledPlugin: Equatable, Sendable {
    let id: String
    let version: String
    let installPath: String
}

enum MarketplaceSource: Equatable, Sendable {
    case directory(path: String)
    case github(repo: String)
    case git(url: String)
    case other(String)
}

struct KnownMarketplace: Equatable, Sendable {
    let name: String
    let source: MarketplaceSource
    let installLocation: String?
}

enum ClaudePluginsRegistry {
    static let scoutPluginID = "scout@scout-plugin"
    static let scoutMarketplaceName = "scout-plugin"

    private struct InstalledFile: Decodable {
        struct Entry: Decodable { let version: String; let installPath: String }
        let plugins: [String: [Entry]]
    }

    private struct MarketplaceEntry: Decodable {
        struct Source: Decodable { let source: String; let path: String?; let repo: String?; let url: String? }
        let source: Source
        let installLocation: String?
    }

    static func installedPlugins(from data: Data) throws -> [InstalledPlugin] {
        let file = try JSONDecoder().decode(InstalledFile.self, from: data)
        return file.plugins.flatMap { id, entries in
            entries.map { InstalledPlugin(id: id, version: $0.version, installPath: $0.installPath) }
        }.sorted { $0.id < $1.id }
    }

    static func knownMarketplaces(from data: Data) throws -> [KnownMarketplace] {
        let file = try JSONDecoder().decode([String: MarketplaceEntry].self, from: data)
        return file.map { name, entry in
            let source: MarketplaceSource
            switch (entry.source.source, entry.source.path, entry.source.repo, entry.source.url) {
            case ("directory", let path?, _, _): source = .directory(path: path)
            case ("github", _, let repo?, _):    source = .github(repo: repo)
            case ("git", _, _, let url?):        source = .git(url: url)
            default:                             source = .other(entry.source.source)
            }
            return KnownMarketplace(name: name, source: source, installLocation: entry.installLocation)
        }.sorted { $0.name < $1.name }
    }

    static func scoutPlugin(pluginsDir: URL) -> InstalledPlugin? {
        guard let data = try? Data(contentsOf: pluginsDir.appending(path: "installed_plugins.json")),
              let plugins = try? installedPlugins(from: data) else { return nil }
        return plugins.first { $0.id == scoutPluginID }
    }

    static func scoutMarketplace(pluginsDir: URL) -> KnownMarketplace? {
        guard let data = try? Data(contentsOf: pluginsDir.appending(path: "known_marketplaces.json")),
              let markets = try? knownMarketplaces(from: data) else { return nil }
        return markets.first { $0.name == scoutMarketplaceName }
    }
}
```

- [ ] **Step 5: Run the three suites, commit**

Run: `xcodebuild test -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/EngineLayoutTests -only-testing:ScoutTests/EnginePointerTests -only-testing:ScoutTests/ClaudePluginsRegistryTests CODE_SIGNING_ALLOWED=NO 2>&1 | tail -20`
Expected: all PASS.

```bash
git add Scout/Engine/EngineLayout.swift Scout/Engine/EnginePointer.swift Scout/Engine/ClaudePluginsRegistry.swift ScoutTests/Engine ScoutTests/Fixtures/engine ScoutTests/Fixtures/claude-plugins
git commit -m "feat(engine): EngineLayout, EnginePointer and Claude plugin registry parsers"
```

### Task B2: `EngineLocator` and `EngineState`

**Files:**
- Create: `Scout/Engine/EngineLocator.swift`
- Test: `ScoutTests/Engine/EngineLocatorTests.swift`

**Interfaces:**
- Consumes: `EngineLayout`, `EnginePointer.load`, `ClaudePluginsRegistry`.
- Produces:
  ```swift
  struct EngineInstall: Equatable, Sendable { let root: URL; let scoutctl: URL; let python: URL?; let version: String?; let vault: URL? }
  enum ExternalSource: Equatable, Sendable { case devCheckout, installSh, claudeCode, marketplaceCache, shim, unknown(String) }
  enum EngineState: Equatable, Sendable {
      case notInstalled
      case managed(EngineInstall, vaultBootstrapped: Bool)
      case external(EngineInstall, ExternalSource)
      case broken(EngineInstall?, reason: String)
      var install: EngineInstall?; var scoutctl: URL?; var isManaged: Bool; var gatesTabs: Bool   // notInstalled / broken / managed-without-vault
  }
  struct EngineLocator: Sendable {
      let layout: EngineLayout; let fileManager: FileManager
      func locate() -> EngineState
      func pointer() -> EnginePointer?
      static func version(atRoot root: URL) -> String?          // <root>/.claude-plugin/plugin.json → version
      static func parseShimTarget(_ text: String) -> String?      // requires "# scout-plugin scoutctl shim"; exec "<path>" "$@"
      static func externalSource(managedBy: String) -> ExternalSource
  }
  ```

- [ ] **Step 1: Write the failing tests**

`ScoutTests/Engine/EngineLocatorTests.swift`:

```swift
import Testing
import Foundation
@testable import Scout

/// Each test builds a throwaway `home` and asserts the matrix row from spec §10.
@Suite("EngineLocator")
struct EngineLocatorTests {
    let fm = FileManager.default

    func makeHome() throws -> EngineLayout {
        let home = fm.temporaryDirectory.appendingPathComponent("engine-locator-\(UUID().uuidString)")
        try fm.createDirectory(at: home, withIntermediateDirectories: true)
        return EngineLayout(home: home)
    }

    func executable(_ url: URL) throws {
        try fm.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try "#!/bin/sh\n".write(to: url, atomically: true, encoding: .utf8)
        try fm.setAttributes([.posixPermissions: 0o755], ofItemAtPath: url.path)
    }

    func pluginTree(_ root: URL, version: String) throws {
        try fm.createDirectory(at: root.appending(path: ".claude-plugin"), withIntermediateDirectories: true)
        try #"{"name": "scout", "version": "\#(version)"}"#.write(to: root.appending(path: ".claude-plugin/plugin.json"), atomically: true, encoding: .utf8)
    }

    func pointer(_ layout: EngineLayout, version: String, managedBy: String, vault: String? = nil) throws {
        let root = layout.engineRoot(version: version)
        let json = """
        {"schema_version": 1, "version": "\(version)", "engine_root": "\(root.path)",
         "python": "\(layout.venv(version: version).path)/bin/python", "scoutctl": "\(layout.scoutctl(version: version).path)",
         "vault": "\(vault ?? layout.home.appending(path: "Scout").path)", "managed_by": "\(managedBy)", "written_at": "2026-09-08T00:00:00Z"}
        """
        try fm.createDirectory(at: layout.stateDir, withIntermediateDirectories: true)
        try json.write(to: layout.pointerURL, atomically: true, encoding: .utf8)
    }

    @Test func nothingOnDiskIsNotInstalled() throws {
        let layout = try makeHome()
        #expect(EngineLocator(layout: layout).locate() == .notInstalled)
    }

    @Test func appManagedPointerIsManaged() throws {
        let layout = try makeHome()
        try pluginTree(layout.engineRoot(version: "0.10.0"), version: "0.10.0")
        try executable(layout.scoutctl(version: "0.10.0"))
        try pointer(layout, version: "0.10.0", managedBy: "scout-app")
        guard case .managed(let install, let bootstrapped) = EngineLocator(layout: layout).locate() else {
            Issue.record("expected .managed"); return
        }
        #expect(bootstrapped)
        #expect(install.version == "0.10.0")
        #expect(install.scoutctl == layout.scoutctl(version: "0.10.0"))
        #expect(install.vault == layout.home.appending(path: "Scout"))
    }

    @Test func pointerFromOtherManagerIsExternal() throws {
        let layout = try makeHome()
        try pluginTree(layout.engineRoot(version: "0.10.0"), version: "0.10.0")
        try executable(layout.scoutctl(version: "0.10.0"))
        try pointer(layout, version: "0.10.0", managedBy: "install.sh")
        #expect(EngineLocator(layout: layout).locate().externalSource == .installSh)
    }

    @Test func pointerWithMissingScoutctlIsBroken() throws {
        let layout = try makeHome()
        try pointer(layout, version: "0.10.0", managedBy: "scout-app")
        guard case .broken(_, let reason) = EngineLocator(layout: layout).locate() else { Issue.record("expected .broken"); return }
        #expect(reason.contains("scoutctl"))
    }

    @Test func conventionalLayoutWithoutPointerIsManagedButNotBootstrapped() throws {
        let layout = try makeHome()
        try pluginTree(layout.engineRoot(version: "0.10.0"), version: "0.10.0")
        try fm.createSymbolicLink(at: layout.currentEngineLink, withDestinationURL: layout.engineRoot(version: "0.10.0"))
        try executable(layout.scoutctl(version: "0.10.0"))
        guard case .managed(let install, let bootstrapped) = EngineLocator(layout: layout).locate() else { Issue.record("expected .managed"); return }
        #expect(!bootstrapped)
        #expect(install.version == "0.10.0")
    }

    @Test func shimPointingAtALiveVenvIsExternal() throws {
        let layout = try makeHome()
        let real = layout.home.appending(path: "somewhere/.venv/bin/scoutctl")
        try executable(real)
        try pluginTree(layout.home.appending(path: "somewhere"), version: "0.9.0")
        try fm.createDirectory(at: layout.localBin, withIntermediateDirectories: true)
        try "#!/bin/sh\n# scout-plugin scoutctl shim — regenerated by `scoutctl bootstrap install/upgrade`.\nexec \"\(real.path)\" \"$@\"\n"
            .write(to: layout.shimURL, atomically: true, encoding: .utf8)
        let state = EngineLocator(layout: layout).locate()
        #expect(state.externalSource == .shim)
        #expect(state.install?.version == "0.9.0")
    }

    @Test func marketplaceCacheInstallIsExternal() throws {
        let layout = try makeHome()
        let cache = layout.claudePluginsDir.appending(path: "cache/scout-plugin/scout/0.9.0")
        try pluginTree(cache, version: "0.9.0")
        try executable(cache.appending(path: ".venv/bin/scoutctl"))
        try fm.createDirectory(at: layout.claudePluginsDir, withIntermediateDirectories: true)
        try #"{"version": 2, "plugins": {"scout@scout-plugin": [{"version": "0.9.0", "installPath": "\#(cache.path)"}]}}"#
            .write(to: layout.claudePluginsDir.appending(path: "installed_plugins.json"), atomically: true, encoding: .utf8)
        #expect(EngineLocator(layout: layout).locate().externalSource == .marketplaceCache)
    }

    @Test func devCheckoutIsExternal() throws {
        let layout = try makeHome()
        try pluginTree(layout.devCheckout, version: "0.9.0")
        try executable(layout.devCheckout.appending(path: ".venv/bin/scoutctl"))
        #expect(EngineLocator(layout: layout).locate().externalSource == .devCheckout)
    }

    @Test func parseShimTargetRequiresTheMarker() {
        #expect(EngineLocator.parseShimTarget("#!/bin/sh\n# scout-plugin scoutctl shim\nexec \"/x/bin/scoutctl\" \"$@\"\n") == "/x/bin/scoutctl")
        #expect(EngineLocator.parseShimTarget("#!/bin/sh\nexec \"/x/bin/scoutctl\" \"$@\"\n") == nil)
    }
}

extension EngineState {
    var externalSource: ExternalSource? {
        if case .external(_, let s) = self { return s }
        return nil
    }
}
```

- [ ] **Step 2: Run to verify failure**

Run: `… -only-testing:ScoutTests/EngineLocatorTests …`
Expected: build error — `EngineLocator` undefined.

- [ ] **Step 3: Implement**

`Scout/Engine/EngineLocator.swift`:

```swift
import Foundation

/// A concrete engine on disk.
struct EngineInstall: Equatable, Sendable {
    let root: URL
    let scoutctl: URL
    let python: URL?
    let version: String?
    let vault: URL?
}

/// Who owns an engine the app did not install (spec §4.4 / §10).
enum ExternalSource: Equatable, Sendable {
    case devCheckout, installSh, claudeCode, marketplaceCache, shim
    case unknown(String)
}

enum EngineState: Equatable, Sendable {
    case notInstalled
    case managed(EngineInstall, vaultBootstrapped: Bool)
    case external(EngineInstall, ExternalSource)
    case broken(EngineInstall?, reason: String)

    var install: EngineInstall? {
        switch self {
        case .managed(let i, _), .external(let i, _): return i
        case .broken(let i, _): return i
        case .notInstalled: return nil
        }
    }
    var scoutctl: URL? { install?.scoutctl }
    var isManaged: Bool { if case .managed = self { return true }; return false }
    /// True when the tabs have nothing trustworthy to show (spec §5).
    var gatesTabs: Bool {
        switch self {
        case .notInstalled, .broken: return true
        case .managed(_, let bootstrapped): return !bootstrapped
        case .external: return false
        }
    }
}

/// Pure filesystem discovery, in the precedence order of spec §4.4's table.
struct EngineLocator: Sendable {
    let layout: EngineLayout
    var fileManager: FileManager = .default

    static let shimMarker = "# scout-plugin scoutctl shim"

    func pointer() -> EnginePointer? { EnginePointer.load(from: layout.pointerURL) }

    func locate() -> EngineState {
        if let p = pointer() {
            let scoutctl = URL(fileURLWithPath: p.scoutctl)
            let install = EngineInstall(root: URL(fileURLWithPath: p.engineRoot), scoutctl: scoutctl,
                                        python: URL(fileURLWithPath: p.python), version: p.version,
                                        vault: URL(fileURLWithPath: p.vault))
            guard fileManager.isExecutableFile(atPath: scoutctl.path) else {
                return .broken(install, reason: "engine pointer names a missing scoutctl: \(p.scoutctl)")
            }
            return p.managedBy == "scout-app"
                ? .managed(install, vaultBootstrapped: true)
                : .external(install, Self.externalSource(managedBy: p.managedBy))
        }
        if let conventional = conventionalLayout() { return .managed(conventional, vaultBootstrapped: false) }
        if let shim = shimTarget() { return .external(shim, .shim) }
        if let cache = marketplaceCacheInstall() { return .external(cache, .marketplaceCache) }
        if let dev = devCheckout() { return .external(dev, .devCheckout) }
        return .notInstalled
    }

    // MARK: discovery helpers

    /// `engine/current` → versioned root; venv beside it. The installer stopped
    /// before `bootstrap` (which writes the pointer), or a user deleted state.
    private func conventionalLayout() -> EngineInstall? {
        guard let dest = try? fileManager.destinationOfSymbolicLink(atPath: layout.currentEngineLink.path) else { return nil }
        let root = URL(fileURLWithPath: dest, relativeTo: layout.engineDir).standardizedFileURL
        let version = root.lastPathComponent
        let scoutctl = layout.scoutctl(version: version)
        guard fileManager.isExecutableFile(atPath: scoutctl.path) else { return nil }
        return EngineInstall(root: root, scoutctl: scoutctl, python: layout.venv(version: version).appending(path: "bin/python"),
                             version: Self.version(atRoot: root) ?? version, vault: nil)
    }

    private func shimTarget() -> EngineInstall? {
        guard let text = try? String(contentsOf: layout.shimURL, encoding: .utf8),
              let target = Self.parseShimTarget(text),
              fileManager.isExecutableFile(atPath: target) else { return nil }
        let scoutctl = URL(fileURLWithPath: target)
        // <root>/.venv/bin/scoutctl → root is three levels up.
        let root = scoutctl.deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        return EngineInstall(root: root, scoutctl: scoutctl, python: scoutctl.deletingLastPathComponent().appending(path: "python"),
                             version: Self.version(atRoot: root), vault: nil)
    }

    private func marketplaceCacheInstall() -> EngineInstall? {
        var roots: [URL] = []
        if let plugin = ClaudePluginsRegistry.scoutPlugin(pluginsDir: layout.claudePluginsDir) {
            roots.append(URL(fileURLWithPath: plugin.installPath))
        }
        if let loc = ClaudePluginsRegistry.scoutMarketplace(pluginsDir: layout.claudePluginsDir)?.installLocation {
            roots.append(URL(fileURLWithPath: loc))
        }
        return roots.lazy.compactMap { root in installIfVenv(at: root) }.first
    }

    private func devCheckout() -> EngineInstall? { installIfVenv(at: layout.devCheckout) }

    /// The pre-pointer convention: a venv at `<root>/.venv` (or `<root>/engine/.venv`).
    private func installIfVenv(at root: URL) -> EngineInstall? {
        for venv in [root.appending(path: ".venv"), root.appending(path: "engine/.venv")] {
            let scoutctl = venv.appending(path: "bin/scoutctl")
            if fileManager.isExecutableFile(atPath: scoutctl.path) {
                return EngineInstall(root: root, scoutctl: scoutctl, python: venv.appending(path: "bin/python"),
                                     version: Self.version(atRoot: root), vault: nil)
            }
        }
        return nil
    }

    // MARK: pure helpers

    static func version(atRoot root: URL) -> String? {
        struct Manifest: Decodable { let version: String }
        guard let data = try? Data(contentsOf: root.appending(path: ".claude-plugin/plugin.json")) else { return nil }
        return try? JSONDecoder().decode(Manifest.self, from: data).version
    }

    static func parseShimTarget(_ text: String) -> String? {
        guard text.contains(shimMarker),
              let range = text.range(of: #"exec "([^"]+)""#, options: .regularExpression) else { return nil }
        let match = text[range]
        guard let open = match.firstIndex(of: "\""), let close = match.lastIndex(of: "\""), open < close else { return nil }
        return String(match[match.index(after: open)..<close])
    }

    static func externalSource(managedBy: String) -> ExternalSource {
        switch managedBy {
        case "dev": return .devCheckout
        case "install.sh": return .installSh
        case "claude-code": return .claudeCode
        default: return .unknown(managedBy)
        }
    }
}
```

- [ ] **Step 4: Run, commit**

Run: `… -only-testing:ScoutTests/EngineLocatorTests …`
Expected: 9 PASS.

```bash
git add Scout/Engine/EngineLocator.swift ScoutTests/Engine/EngineLocatorTests.swift
git commit -m "feat(engine): EngineLocator — pointer first, then conventional layout, shim, marketplace cache, dev checkout"
```

### Task B3: `DoctorReport`, `ScriptedRunner`, `EngineHealthService`

**Files:**
- Create: `Scout/Engine/DoctorReport.swift`, `Scout/Engine/EngineHealthService.swift`
- Create: `ScoutTests/Engine/ScriptedRunner.swift` (shared test double)
- Test: `ScoutTests/Engine/DoctorReportTests.swift`, `ScoutTests/Engine/EngineHealthServiceTests.swift`

**Interfaces:**
- Produces:
  ```swift
  struct DoctorReport: Equatable, Sendable, Decodable { enum Severity: String, Decodable { case green, yellow, red }; let severity: Severity; let errors: [String]; let warnings: [String]
      static func parse(stdout: Data) -> DoctorReport?   // JSON (E3) or legacy "severity: …" text }
  @MainActor final class EngineHealthService: ObservableObject {
      @Published private(set) var state: EngineState; @Published private(set) var doctor: DoctorReport?; @Published private(set) var lastError: String?; @Published private(set) var lastChecked: Date?
      var needsAttention: Bool
      init(locator: EngineLocator, runner: any ProcessRunner, environment: [String: String] = [:])
      func refresh() async }
  // test double
  final class ScriptedRunner: ProcessRunner, @unchecked Sendable { init(); func on(_ predicate: @escaping (URL, [String]) -> Bool, _ respond: @escaping (URL, [String], [String: String]) throws -> ProcessResult); var calls: [(executable: URL, arguments: [String], environment: [String: String])] }
  ```

- [ ] **Step 1: Write the shared test double**

`ScoutTests/Engine/ScriptedRunner.swift`:

```swift
import Foundation
@testable import Scout

/// A `ProcessRunner` that answers by rules and records every call. Rules are
/// tried in order; the first predicate match answers. Unmatched calls throw
/// ENOENT, which is what a missing executable produces in production.
final class ScriptedRunner: ProcessRunner, @unchecked Sendable {
    typealias Responder = (URL, [String], [String: String]) throws -> ProcessResult
    private var rules: [((URL, [String]) -> Bool, Responder)] = []
    private let lock = NSLock()
    private(set) var calls: [(executable: URL, arguments: [String], environment: [String: String])] = []

    func on(_ predicate: @escaping (URL, [String]) -> Bool, _ respond: @escaping Responder) {
        lock.withLock { rules.append((predicate, respond)) }
    }

    /// Convenience: match on the executable's last path component + a leading argument prefix.
    func on(tool: String, prefix: [String] = [], stdout: String = "", stderr: String = "", exit: Int32 = 0) {
        on({ url, args in url.lastPathComponent == tool && Array(args.prefix(prefix.count)) == prefix }) { _, _, _ in
            ProcessResult(exitCode: exit, stdout: Data(stdout.utf8), stderr: Data(stderr.utf8))
        }
    }

    func run(executable: URL, arguments: [String], environment: [String: String], workingDirectory: URL?) async throws -> ProcessResult {
        let rule = lock.withLock { () -> Responder? in
            calls.append((executable, arguments, environment))
            return rules.first { $0.0(executable, arguments) }?.1
        }
        guard let rule else { throw NSError(domain: NSPOSIXErrorDomain, code: 2, userInfo: [NSLocalizedDescriptionKey: "ENOENT \(executable.path)"]) }
        return try rule(executable, arguments, environment)
    }

    func calls(to tool: String) -> [[String]] { calls.filter { $0.executable.lastPathComponent == tool }.map(\.arguments) }
}
```

- [ ] **Step 2: Write the failing tests**

`ScoutTests/Engine/DoctorReportTests.swift`:

```swift
import Testing
import Foundation
@testable import Scout

@Suite("DoctorReport")
struct DoctorReportTests {
    @Test func parsesJsonFromDoctorDashJson() {
        let r = DoctorReport.parse(stdout: Data(#"{"severity": "yellow", "errors": [], "warnings": ["snapshot missing: x"]}"#.utf8))
        #expect(r == DoctorReport(severity: .yellow, errors: [], warnings: ["snapshot missing: x"]))
    }

    @Test func parsesLegacyTextFromOlderEngines() {
        let text = "severity: red\nwarning: runner backup present: run-scout.sh.bak.1\nerror: launchd: com.scout.schedule-tick not registered\n"
        let r = DoctorReport.parse(stdout: Data(text.utf8))
        #expect(r?.severity == .red)
        #expect(r?.errors == ["launchd: com.scout.schedule-tick not registered"])
        #expect(r?.warnings == ["runner backup present: run-scout.sh.bak.1"])
    }

    @Test func garbageIsNil() {
        #expect(DoctorReport.parse(stdout: Data("Traceback…".utf8)) == nil)
    }
}
```

`ScoutTests/Engine/EngineHealthServiceTests.swift`:

```swift
import Testing
import Foundation
@testable import Scout

@Suite("EngineHealthService")
@MainActor
struct EngineHealthServiceTests {
    func managedHome() throws -> EngineLayout {
        let fm = FileManager.default
        let layout = EngineLayout(home: fm.temporaryDirectory.appendingPathComponent("health-\(UUID().uuidString)"))
        let scoutctl = layout.scoutctl(version: "0.10.0")
        try fm.createDirectory(at: scoutctl.deletingLastPathComponent(), withIntermediateDirectories: true)
        try "#!/bin/sh\n".write(to: scoutctl, atomically: true, encoding: .utf8)
        try fm.setAttributes([.posixPermissions: 0o755], ofItemAtPath: scoutctl.path)
        try fm.createDirectory(at: layout.stateDir, withIntermediateDirectories: true)
        try """
        {"schema_version": 1, "version": "0.10.0", "engine_root": "\(layout.engineRoot(version: "0.10.0").path)", "python": "x",
         "scoutctl": "\(scoutctl.path)", "vault": "\(layout.home.path)/Scout", "managed_by": "scout-app", "written_at": "2026-09-08T00:00:00Z"}
        """.write(to: layout.pointerURL, atomically: true, encoding: .utf8)
        return layout
    }

    @Test func refreshLocatesAndRunsDoctorWithVaultEnv() async throws {
        let layout = try managedHome()
        let runner = ScriptedRunner()
        runner.on(tool: "scoutctl", prefix: ["bootstrap", "doctor"], stdout: #"{"severity": "green", "errors": [], "warnings": []}"#)
        let svc = EngineHealthService(locator: EngineLocator(layout: layout), runner: runner, environment: ["SCOUT_DATA_DIR": "/v"])
        await svc.refresh()
        #expect(svc.state.isManaged)
        #expect(svc.doctor?.severity == .green)
        #expect(!svc.needsAttention)
        #expect(runner.calls.first?.arguments == ["bootstrap", "doctor", "--json"])
        #expect(runner.calls.first?.environment["SCOUT_DATA_DIR"] == "/v")
    }

    @Test func redDoctorNeedsAttention() async throws {
        let layout = try managedHome()
        let runner = ScriptedRunner()
        runner.on(tool: "scoutctl", prefix: ["bootstrap", "doctor"], stdout: "severity: red\nerror: vault directory missing: /x\n", exit: 2)
        let svc = EngineHealthService(locator: EngineLocator(layout: layout), runner: runner)
        await svc.refresh()
        #expect(svc.doctor?.severity == .red)
        #expect(svc.needsAttention)
    }

    @Test func notInstalledSkipsDoctorAndNeedsAttention() async throws {
        let layout = EngineLayout(home: FileManager.default.temporaryDirectory.appendingPathComponent("empty-\(UUID().uuidString)"))
        let runner = ScriptedRunner()
        let svc = EngineHealthService(locator: EngineLocator(layout: layout), runner: runner)
        await svc.refresh()
        #expect(svc.state == .notInstalled)
        #expect(runner.calls.isEmpty)
        #expect(svc.needsAttention)
    }
}
```

- [ ] **Step 3: Run to verify failure** — build error (`DoctorReport`, `EngineHealthService` undefined).

- [ ] **Step 4: Implement**

`Scout/Engine/DoctorReport.swift`:

```swift
import Foundation

/// `scoutctl bootstrap doctor` result. Engines ≥ 0.10.0 emit JSON (`--json`);
/// older adopted engines print `severity: …` / `warning: …` / `error: …`
/// lines, which the app still understands so adoption works before upgrade.
struct DoctorReport: Equatable, Sendable, Decodable {
    enum Severity: String, Decodable, Sendable { case green, yellow, red }
    let severity: Severity
    let errors: [String]
    let warnings: [String]

    static func parse(stdout: Data) -> DoctorReport? {
        if let json = try? JSONDecoder().decode(DoctorReport.self, from: stdout) { return json }
        guard let text = String(data: stdout, encoding: .utf8) else { return nil }
        var severity: Severity?
        var errors: [String] = [], warnings: [String] = []
        for raw in text.split(separator: "\n") {
            let line = raw.trimmingCharacters(in: .whitespaces)
            if line.hasPrefix("severity: ") { severity = Severity(rawValue: String(line.dropFirst("severity: ".count))) }
            else if line.hasPrefix("warning: ") { warnings.append(String(line.dropFirst("warning: ".count))) }
            else if line.hasPrefix("error: ") { errors.append(String(line.dropFirst("error: ".count))) }
        }
        guard let severity else { return nil }
        return DoctorReport(severity: severity, errors: errors, warnings: warnings)
    }
}
```

`Scout/Engine/EngineHealthService.swift`:

```swift
import Foundation
import Combine

/// One observable answer to "is the engine there and healthy?" (spec §4.4).
/// Drives the window gate, Settings ▸ Engine and the sidebar badge.
@MainActor
final class EngineHealthService: ObservableObject {
    @Published private(set) var state: EngineState = .notInstalled
    @Published private(set) var doctor: DoctorReport?
    @Published private(set) var lastError: String?
    @Published private(set) var lastChecked: Date?

    private let locator: EngineLocator
    private let runner: any ProcessRunner
    private let environment: [String: String]
    private var timer: Timer?

    init(locator: EngineLocator, runner: any ProcessRunner, environment: [String: String] = [:]) {
        self.locator = locator
        self.runner = runner
        self.environment = environment
    }

    var needsAttention: Bool {
        if state.gatesTabs { return true }
        if case .broken = state { return true }
        return doctor?.severity == .red
    }

    /// Locate off the main actor, run the doctor, publish on the main actor.
    func refresh() async {
        let locator = self.locator
        let located = await Task.detached { locator.locate() }.value
        state = located
        lastChecked = Date()
        guard let scoutctl = located.scoutctl, !located.gatesTabs || located.isManaged else {
            doctor = nil
            return
        }
        do {
            let result = try await runner.run(executable: scoutctl, arguments: ["bootstrap", "doctor", "--json"],
                                              environment: environment, workingDirectory: nil)
            doctor = DoctorReport.parse(stdout: result.stdout)
            lastError = doctor == nil ? "doctor output not understood: \(ScheduleService.previewBytes(result.stderr.isEmpty ? result.stdout : result.stderr, max: 200))" : nil
        } catch {
            doctor = nil
            lastError = "could not run scoutctl: \(String(describing: error).prefix(160))"
        }
    }

    /// Re-check every 10 minutes (spec §4.4).
    func startPeriodicRefresh(interval: TimeInterval = 600) {
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { [weak self] _ in
            Task { await self?.refresh() }
        }
    }
}
```

- [ ] **Step 5: Run both suites, commit**

Run: `… -only-testing:ScoutTests/DoctorReportTests -only-testing:ScoutTests/EngineHealthServiceTests …`
Expected: PASS.

```bash
git add Scout/Engine/DoctorReport.swift Scout/Engine/EngineHealthService.swift ScoutTests/Engine/ScriptedRunner.swift ScoutTests/Engine/DoctorReportTests.swift ScoutTests/Engine/EngineHealthServiceTests.swift
git commit -m "feat(engine): DoctorReport parser and EngineHealthService"
```

### Task B4: `EnvironmentInjectingRunner`, configurable vault, `AppState` wiring

**Files:**
- Create: `Scout/Engine/EnvironmentInjectingRunner.swift`
- Modify: `Scout/Shell/AppState.swift:64-83, 205-209, 237-264, 373-399`
- Test: `ScoutTests/Engine/EnvironmentInjectingRunnerTests.swift`, `ScoutTests/Shell/AppStateVaultResolutionTests.swift`

**Interfaces:**
- Produces:
  ```swift
  struct EnvironmentInjectingRunner: ProcessRunner { let base: any ProcessRunner; let extra: [String: String] }   // call-site env wins on collision
  extension AppState { static func resolveScoutDirectory(defaults: UserDefaults, pointer: EnginePointer?, home: URL) -> URL }
  // AppState gains: let engineLayout: EngineLayout; let engineHealth: EngineHealthService
  // AppState.resolveScoutctlPath() is DELETED; ScoutctlInvocation stays (argsPrefix now always []).
  ```
  UserDefaults key: `scoutDataDir` (string path, `~` allowed; blank = unset).

- [ ] **Step 1: Write the failing tests**

`ScoutTests/Engine/EnvironmentInjectingRunnerTests.swift`:

```swift
import Testing
import Foundation
@testable import Scout

@Suite("EnvironmentInjectingRunner")
struct EnvironmentInjectingRunnerTests {
    @Test func injectsExtraAndLetsCallSiteWin() async throws {
        let inner = ScriptedRunner()
        inner.on({ _, _ in true }) { _, _, _ in ProcessResult(exitCode: 0, stdout: Data(), stderr: Data()) }
        let runner = EnvironmentInjectingRunner(base: inner, extra: ["SCOUT_DATA_DIR": "/vault", "A": "base"])
        _ = try await runner.run(executable: URL(fileURLWithPath: "/bin/true"), arguments: [], environment: ["A": "call"], workingDirectory: nil)
        #expect(inner.calls[0].environment == ["SCOUT_DATA_DIR": "/vault", "A": "call"])
    }
}
```

`ScoutTests/Shell/AppStateVaultResolutionTests.swift`:

```swift
import Testing
import Foundation
@testable import Scout

@Suite("AppState.resolveScoutDirectory")
struct AppStateVaultResolutionTests {
    let home = URL(fileURLWithPath: "/Users/alex")
    func defaults(_ value: String?) -> UserDefaults {
        let d = UserDefaults(suiteName: "AppStateVaultResolutionTests-\(UUID().uuidString)")!
        if let value { d.set(value, forKey: "scoutDataDir") }
        return d
    }
    let pointer = EnginePointer(schemaVersion: 1, version: "0.10.0", engineRoot: "/e", python: "/p", scoutctl: "/s",
                                vault: "/Users/alex/Vaults/Work", managedBy: "scout-app", writtenAt: "")

    @Test func userDefaultWinsAndExpandsTilde() {
        #expect(AppState.resolveScoutDirectory(defaults: defaults("~/Custom"), pointer: pointer, home: home).path == "/Users/alex/Custom")
    }
    @Test func pointerVaultIsSecond() {
        #expect(AppState.resolveScoutDirectory(defaults: defaults(nil), pointer: pointer, home: home).path == "/Users/alex/Vaults/Work")
    }
    @Test func blankDefaultFallsThrough() {
        #expect(AppState.resolveScoutDirectory(defaults: defaults("  "), pointer: nil, home: home).path == "/Users/alex/Scout")
    }
}
```

- [ ] **Step 2: Run to verify failure** — build error.

- [ ] **Step 3: Implement the runner**

`Scout/Engine/EnvironmentInjectingRunner.swift`:

```swift
import Foundation

/// Adds a fixed environment to every process the app spawns — today that is
/// `SCOUT_DATA_DIR`, so a non-default vault root reaches every `scoutctl`
/// call without touching each call site (spec §4.4). Call-site values win.
struct EnvironmentInjectingRunner: ProcessRunner {
    let base: any ProcessRunner
    let extra: [String: String]

    func run(executable: URL, arguments: [String], environment: [String: String], workingDirectory: URL?) async throws -> ProcessResult {
        try await base.run(executable: executable, arguments: arguments,
                           environment: extra.merging(environment) { _, callSite in callSite },
                           workingDirectory: workingDirectory)
    }
}
```

- [ ] **Step 4: Rewire `AppState.init`**

Replace lines 64–83 (`let scoutDir = …` through `let scoutctlResolved = AppState.resolveScoutctlPath()`) with:

```swift
        let layout = EngineLayout.live
        let locator = EngineLocator(layout: layout)
        let engineState = locator.locate()
        let scoutDir = Self.resolveScoutDirectory(defaults: .standard, pointer: locator.pointer(), home: layout.home)
        let actionItemsDir = scoutDir.appendingPathComponent("action-items")
        let watcher = FileWatcher()
        // Every scoutctl call must see the vault the app is looking at (the
        // engine defaults to ~/Scout otherwise) — inject it once, here.
        let runner = EnvironmentInjectingRunner(base: SystemProcessRunner(), extra: ["SCOUT_DATA_DIR": scoutDir.path])

        // The engine is found by EngineLocator (pointer → conventional layout
        // → shim → marketplace cache → dev checkout). When nothing is found we
        // hand services the shim path: ENOENT there is the honest failure,
        // and EngineHealthService gates the UI on the same fact. No more
        // `/usr/bin/env scoutctl` and PATH luck.
        let scoutctlResolved = ScoutctlInvocation(executable: engineState.scoutctl ?? layout.shimURL, argsPrefix: [])
        let engineHealth = EngineHealthService(locator: locator, runner: runner, environment: ["SCOUT_DATA_DIR": scoutDir.path])
```

Add stored properties `let engineLayout: EngineLayout` and `let engineHealth: EngineHealthService`, assign them beside `self.scoutDirectory = scoutDir`, and in the launch `Task` add `await engineHealth.refresh(); await MainActor.run { engineHealth.startPeriodicRefresh() }` before the environment check. Delete `resolveScoutctlPath()` and its doc comment (lines 373–399); keep `struct ScoutctlInvocation`. Add:

```swift
    /// Vault root precedence (spec §4.4): the `scoutDataDir` default (tilde
    /// expanded) → the engine pointer's `vault` → `~/Scout`.
    static func resolveScoutDirectory(defaults: UserDefaults, pointer: EnginePointer?, home: URL) -> URL {
        if let raw = defaults.string(forKey: "scoutDataDir")?.trimmingCharacters(in: .whitespacesAndNewlines), !raw.isEmpty {
            return URL(fileURLWithPath: (raw as NSString).expandingTildeInPath)
        }
        if let pointer { return URL(fileURLWithPath: pointer.vault) }
        return home.appendingPathComponent("Scout")
    }
```

Update the stale comment in `Scout/ControlCenter/UpcomingStripView.swift:126` and `ScoutTests/ActionItems/ActionItemsIntegrationTests.swift:66` to say `EngineLocator` instead of `AppState.resolveScoutctlPath`.

- [ ] **Step 5: Run the new suites and the whole target once, commit**

Run: `… -only-testing:ScoutTests/EnvironmentInjectingRunnerTests -only-testing:ScoutTests/AppStateVaultResolutionTests …` then the full `xcodebuild test … -only-testing:ScoutTests …`.
Expected: PASS; nothing else regresses (`AppStateFireNowTests` still passes — `fireNowArguments` is untouched).

```bash
git add Scout/Engine/EnvironmentInjectingRunner.swift Scout/Shell/AppState.swift Scout/ControlCenter/UpcomingStripView.swift ScoutTests/ActionItems/ActionItemsIntegrationTests.swift ScoutTests/Engine/EnvironmentInjectingRunnerTests.swift ScoutTests/Shell/AppStateVaultResolutionTests.swift
git commit -m "feat(app): locate the engine via EngineLocator; configurable vault root; SCOUT_DATA_DIR on every scoutctl call"
```

### Task B5: Settings ▸ Engine

**Files:**
- Create: `Scout/Engine/EngineSettingsModel.swift`, `Scout/Shell/EngineSettingsSection.swift`
- Modify: `Scout/Shell/SettingsView.swift:56-66, 185-194, 253-255`
- Test: `ScoutTests/Engine/EngineSettingsModelTests.swift`

**Interfaces:**
- Produces:
  ```swift
  struct EngineSettingsModel: Equatable {
      init(state: EngineState, doctor: DoctorReport?, bundledVersion: String?)
      var sourceLabel: String        // "App-managed" | "Dev checkout (~/scout-plugin)" | "Claude Code marketplace" | "install.sh" | "Not installed" | "Broken"
      var installedVersionLabel: String; var bundledVersionLabel: String?   // "0.10.0" / "—"
      var rootPath: String?; var healthLabel: String; var healthIsOK: Bool; var messages: [String]
      var canUpdate: Bool            // managed && bundled > installed   (false until Part C provides bundledVersion)
      var canRepair: Bool            // managed || broken || notInstalled  (button enabled in Part C)
      var showsHandOff: Bool         // external && behind bundled
  }
  ```
  `SettingsView` gets `@EnvironmentObject var appState: AppState` (already injected in `ScoutApp`) and renders `EngineSettingsSection(health: appState.engineHealth, bundledVersion: nil)`; the "Scout directory" row becomes an editable `SettingsField` on `@AppStorage("scoutDataDir")`.

- [ ] **Step 1: Write the failing tests**

`ScoutTests/Engine/EngineSettingsModelTests.swift`:

```swift
import Testing
import Foundation
@testable import Scout

@Suite("EngineSettingsModel")
struct EngineSettingsModelTests {
    let install = EngineInstall(root: URL(fileURLWithPath: "/Users/alex/.local/share/scout/engine/0.10.0"),
                                scoutctl: URL(fileURLWithPath: "/s"), python: nil, version: "0.10.0", vault: nil)

    @Test func managedAndGreen() {
        let m = EngineSettingsModel(state: .managed(install, vaultBootstrapped: true),
                                    doctor: DoctorReport(severity: .green, errors: [], warnings: []), bundledVersion: "0.10.0")
        #expect(m.sourceLabel == "App-managed")
        #expect(m.installedVersionLabel == "0.10.0")
        #expect(m.healthLabel == "Healthy")
        #expect(m.healthIsOK)
        #expect(!m.canUpdate)
    }

    @Test func managedBehindBundledCanUpdate() {
        let m = EngineSettingsModel(state: .managed(install, vaultBootstrapped: true), doctor: nil, bundledVersion: "0.11.0")
        #expect(m.canUpdate)
        #expect(m.bundledVersionLabel == "0.11.0")
    }

    @Test func externalBehindShowsHandOff() {
        let m = EngineSettingsModel(state: .external(install, .devCheckout), doctor: nil, bundledVersion: "0.11.0")
        #expect(m.sourceLabel == "Dev checkout (~/scout-plugin)")
        #expect(m.showsHandOff)
        #expect(!m.canUpdate)
    }

    @Test func notInstalledAndRedDoctorMessages() {
        let m1 = EngineSettingsModel(state: .notInstalled, doctor: nil, bundledVersion: nil)
        #expect(m1.sourceLabel == "Not installed" && m1.installedVersionLabel == "—" && !m1.healthIsOK && m1.canRepair)
        let m2 = EngineSettingsModel(state: .managed(install, vaultBootstrapped: true),
                                     doctor: DoctorReport(severity: .red, errors: ["launchd: com.scout.heartbeat not registered"], warnings: ["w"]), bundledVersion: nil)
        #expect(m2.healthLabel == "Needs attention")
        #expect(m2.messages == ["launchd: com.scout.heartbeat not registered", "w"])
    }
}
```

- [ ] **Step 2: Run to verify failure** — build error.

- [ ] **Step 3: Implement the model**

`Scout/Engine/EngineSettingsModel.swift`:

```swift
import Foundation

/// Pure presentation model for Settings ▸ Engine (spec §5). Views stay thin;
/// this is what the tests pin.
struct EngineSettingsModel: Equatable {
    let state: EngineState
    let doctor: DoctorReport?
    let bundledVersion: String?

    init(state: EngineState, doctor: DoctorReport?, bundledVersion: String?) {
        self.state = state; self.doctor = doctor; self.bundledVersion = bundledVersion
    }

    var sourceLabel: String {
        switch state {
        case .notInstalled: return "Not installed"
        case .broken: return "Broken"
        case .managed: return "App-managed"
        case .external(_, let source):
            switch source {
            case .devCheckout: return "Dev checkout (~/scout-plugin)"
            case .marketplaceCache, .claudeCode: return "Claude Code marketplace"
            case .installSh: return "install.sh"
            case .shim: return "Existing install (via ~/.local/bin/scoutctl)"
            case .unknown(let who): return "External (\(who))"
            }
        }
    }

    var installedVersionLabel: String { state.install?.version ?? "—" }
    var bundledVersionLabel: String? { bundledVersion }
    var rootPath: String? { state.install?.root.path }

    var healthIsOK: Bool {
        guard !state.gatesTabs, case .some(let d) = doctor else { return false }
        return d.severity != .red
    }
    var healthLabel: String {
        if case .notInstalled = state { return "Not installed" }
        if case .broken = state { return "Broken" }
        guard let doctor else { return "Unknown" }
        switch doctor.severity {
        case .green: return "Healthy"
        case .yellow: return "Healthy, with warnings"
        case .red: return "Needs attention"
        }
    }
    var messages: [String] {
        if case .broken(_, let reason) = state { return [reason] }
        guard let doctor else { return [] }
        return doctor.errors + doctor.warnings
    }

    private var isBehindBundled: Bool {
        guard let bundled = bundledVersion, let installed = state.install?.version,
              let b = EngineVersion(bundled), let i = EngineVersion(installed) else { return false }
        return i < b
    }
    var canUpdate: Bool { state.isManaged && isBehindBundled }
    var canRepair: Bool { if case .external = state { return false }; return true }
    var showsHandOff: Bool { if case .external = state { return isBehindBundled }; return false }
}
```

`EngineVersion` is introduced by Part C Task C6; for Part B add the minimal type now in `Scout/Engine/EngineVersion.swift` (C6 extends its tests, not its API):

```swift
import Foundation

/// `major.minor.patch[-pre]`; pre-release sorts before the release. #74's
/// `SemVer` covers the same ground — dedupe onto one type when both exist.
struct EngineVersion: Equatable, Comparable, Sendable, CustomStringConvertible {
    let major: Int, minor: Int, patch: Int
    let preRelease: String?

    init?(_ text: String) {
        let trimmed = text.hasPrefix("v") ? String(text.dropFirst()) : text
        let core = trimmed.split(separator: "-", maxSplits: 1, omittingEmptySubsequences: false)
        let parts = core[0].split(separator: ".").map { Int($0) }
        guard parts.count == 3, let a = parts[0], let b = parts[1], let c = parts[2] else { return nil }
        major = a; minor = b; patch = c
        preRelease = core.count == 2 && !core[1].isEmpty ? String(core[1]) : nil
    }

    static func < (l: EngineVersion, r: EngineVersion) -> Bool {
        if (l.major, l.minor, l.patch) != (r.major, r.minor, r.patch) { return (l.major, l.minor, l.patch) < (r.major, r.minor, r.patch) }
        switch (l.preRelease, r.preRelease) {
        case (nil, nil): return false
        case (.some, nil): return true
        case (nil, .some): return false
        case (.some(let a), .some(let b)): return a < b
        }
    }
    var description: String { "\(major).\(minor).\(patch)" + (preRelease.map { "-\($0)" } ?? "") }
}
```

- [ ] **Step 4: Implement the section view and wire Settings**

`Scout/Shell/EngineSettingsSection.swift` (uses the private `SettingsCard`/`SettingsRow`/`SettingsField` in `SettingsView.swift` — make those three `fileprivate` → internal by deleting `private`):

```swift
import SwiftUI

/// Settings ▸ Engine (spec §5). Buttons that Part C implements are wired
/// through optional closures so Part B ships with them hidden.
struct EngineSettingsSection: View {
    @ObservedObject var health: EngineHealthService
    var bundledVersion: String?
    var onUpdate: (() -> Void)? = nil
    var onRepair: (() -> Void)? = nil
    @AppStorage("scoutDataDir") private var scoutDataDir: String = ""

    private var model: EngineSettingsModel { EngineSettingsModel(state: health.state, doctor: health.doctor, bundledVersion: bundledVersion) }

    var body: some View {
        SettingsCard {
            SettingsRow(title: "Engine", help: model.sourceLabel) {
                Text(versionText).font(DS.mono(12, weight: .medium)).foregroundStyle(DS.Ink.p1)
            }
            if let root = model.rootPath {
                SettingsRow(title: "Engine location", help: "The scout-plugin tree the app and launchd jobs run.") {
                    Text(root).font(DS.mono(11)).foregroundStyle(DS.Ink.p3).lineLimit(1).truncationMode(.middle)
                }
            }
            SettingsField(label: "Scout vault", help: "Folder Scout reads and writes. Blank = `~/Scout`, or the vault the engine was set up for. Points the app at a vault; never moves data. Takes effect after restarting Scout.") {
                SettingsInput(text: $scoutDataDir, placeholder: health.state.install?.vault?.path ?? "~/Scout")
            }
            SettingsRow(title: "Health", help: model.messages.first ?? "Last checked \(health.lastChecked.map { $0.formatted(date: .omitted, time: .shortened) } ?? "never")") {
                HStack(spacing: 10) {
                    Text(model.healthLabel)
                        .font(DS.sans(12, weight: .medium))
                        .foregroundStyle(model.healthIsOK ? DS.Status.ok : DS.Status.warn)
                    Button("Check now") { Task { await health.refresh() } }
                        .buttonStyle(.plainHit)
                        .font(DS.sans(12))
                }
            }
            if model.messages.count > 1 {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(model.messages.dropFirst(), id: \.self) { Text($0).font(DS.mono(11)).foregroundStyle(DS.Ink.p3) }
                }.padding(.vertical, 10)
            }
            if model.canUpdate, let onUpdate {
                SettingsRow(title: "Update engine", help: "Install engine \(bundledVersion ?? "") that ships with this app, then upgrade the vault.") {
                    Button("Update") { onUpdate() }.buttonStyle(.plainHit)
                }
            }
            if model.canRepair, let onRepair {
                SettingsRow(title: "Repair", help: "Re-run the installer steps that failed or went missing.") {
                    Button("Repair…") { onRepair() }.buttonStyle(.plainHit)
                }
            }
            if model.showsHandOff {
                SettingsRow(title: "Update available", help: "This engine is managed outside the app. Run `/scout-update` in Claude Code.") {
                    Button("Copy /scout-update") { NSPasteboard.general.clearContents(); NSPasteboard.general.setString("/scout-update", forType: .string) }
                        .buttonStyle(.plainHit)
                }
            }
        }
    }

    private var versionText: String {
        guard let bundled = model.bundledVersionLabel, bundled != model.installedVersionLabel else { return model.installedVersionLabel }
        return "\(model.installedVersionLabel) → \(bundled)"
    }
}
```

In `SettingsView`: add `@EnvironmentObject var appState: AppState`; delete the "Scout directory" `SettingsRow` (lines 56–66) and `scoutDirPath`; insert `section(label: "Engine") { EngineSettingsSection(health: appState.engineHealth) }` directly after the General section; in About delete the `Plugin` and `Daemon` rows.

- [ ] **Step 5: Run, build the app, commit**

Run: `… -only-testing:ScoutTests/EngineSettingsModelTests …` then `xcodebuild -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS' build CODE_SIGNING_ALLOWED=NO 2>&1 | tail -5`.
Expected: PASS; BUILD SUCCEEDED. Launch the Debug app once and open Settings: the Engine section shows this machine's dev checkout as `Dev checkout (~/scout-plugin)` with its version and a health line.

```bash
git add Scout/Engine/EngineSettingsModel.swift Scout/Engine/EngineVersion.swift Scout/Shell/EngineSettingsSection.swift Scout/Shell/SettingsView.swift ScoutTests/Engine/EngineSettingsModelTests.swift
git commit -m "feat(app): Settings ▸ Engine — source, version, vault path, health"
```

### Task B6: Route empty states to the engine; sidebar badge

**Files:**
- Create: `Scout/Onboarding/EngineUnavailableView.swift`
- Modify: `Scout/Shell/MainWindowView.swift:33-68`, `Scout/Shell/SidebarView.swift:6-28, 56-70`, `Scout/Services/ScheduleService.swift:109-111`, `Scout/ActionItems/ActionItemsEnvironmentCheck.swift:49-52`
- Test: `ScoutTests/Services/ScheduleServiceTests.swift` (adjust the ENOENT expectation if one exists), `ScoutTests/ActionItems/ActionItemsEnvironmentCheckTests.swift:55-68`

**Interfaces:**
- Produces: `EngineUnavailableView(state: EngineState, openSettings: () -> Void)`; `SidebarView(selection:, proposalsBadge:, wishlistBadge:, researchBadge:, settingsAttention: Bool)`. Copy: `"Scout engine not found — open Settings ▸ Engine to install or repair it."`

- [ ] **Step 1: Update the two error strings and their tests**

`ScheduleService.formatRunnerError` ENOENT branch → `return "Scout engine not found — open Settings ▸ Engine to install or repair it."`. `ActionItemsEnvironmentCheck` ENOENT message → the same string. In `ActionItemsEnvironmentCheckTests.failsWhenScoutctlNotFound` assert `result.message?.contains("Settings ▸ Engine") == true`; grep `ScheduleServiceTests` for `scout-plugin is installed` and update the same way.

- [ ] **Step 2: The gate view**

`Scout/Onboarding/EngineUnavailableView.swift` (Part C replaces the body with `OnboardingView`; keep the file):

```swift
import SwiftUI

/// Shown in the detail pane whenever the engine cannot back the tabs
/// (spec §5). Part C swaps this for the full onboarding flow.
struct EngineUnavailableView: View {
    let state: EngineState
    let openSettings: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Scout needs its engine").font(DS.serif(24, weight: .medium)).foregroundStyle(DS.Ink.p1)
            Text(explanation).font(DS.sans(13)).foregroundStyle(DS.Ink.p2).fixedSize(horizontal: false, vertical: true)
            Button("Open Settings ▸ Engine") { openSettings() }.buttonStyle(.plainHit).font(DS.sans(13, weight: .medium))
        }
        .frame(maxWidth: 520, alignment: .leading)
        .padding(40)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }

    private var explanation: String {
        switch state {
        case .notInstalled: return "No Scout engine was found on this Mac. Install it from Settings ▸ Engine to start collecting briefings."
        case .broken(_, let reason): return "The engine that was here is broken: \(reason)"
        case .managed(_, false): return "The engine is installed but your vault has not been set up yet."
        case .managed, .external: return "The engine is present."
        }
    }
}
```

- [ ] **Step 3: Gate and badge**

`MainWindowView.body`: change `detail` to

```swift
        } detail: {
            Group {
                if appState.engineHealth.state.gatesTabs && selection != .settings {
                    EngineUnavailableView(state: appState.engineHealth.state) { selection = .settings }
                } else {
                    detail
                }
            }
            .background(PaperBackdrop())
        }
```

and add `@ObservedObject` forwarding by declaring `@EnvironmentObject var appState` (already present). `SidebarView`: add `var settingsAttention: Bool = false`; in `row(_:)` add a parameter `attention: Bool = false` that draws `Circle().fill(DS.Status.warn).frame(width: 6, height: 6)` after the label when true; pass `attention: settingsAttention` on the Settings row; `MainWindowView` passes `settingsAttention: appState.engineHealth.needsAttention`. Because `engineHealth` is a nested `ObservableObject`, forward its changes in `AppState.init` exactly like `wishlistDoc.objectWillChange` (`engineHealth.objectWillChange.receive(on: DispatchQueue.main).sink { [weak self] _ in self?.objectWillChange.send() }.store(in: &cancellables)`).

- [ ] **Step 4: Run the full test target, build, commit**

Run: full `xcodebuild test … -only-testing:ScoutTests …`.
Expected: PASS. Launch the Debug app: with the dev checkout present the tabs render as before; temporarily rename `~/scout-plugin/.venv` (and restore it) to see the gate and the dot.

```bash
git add Scout/Onboarding/EngineUnavailableView.swift Scout/Shell/MainWindowView.swift Scout/Shell/SidebarView.swift Scout/Shell/AppState.swift Scout/Services/ScheduleService.swift Scout/ActionItems/ActionItemsEnvironmentCheck.swift ScoutTests
git commit -m "feat(app): gate tabs on engine health; point missing-engine errors at Settings ▸ Engine"
```

Part B ends here: open the PR `feat(app): adopt the engine — EngineLocator, health, Settings ▸ Engine (Part B of #104)` and ship it as an app release before starting Part C.

---

# Part C — Scout.app installs and upgrades the engine (spec §4.3–§4.4 installer, §5 onboarding, §6 build, phase 2 of §10)

Prerequisite: scout-plugin **v0.10.0** released (Task A8) and Part B merged. Branch `feat/app-managed-engine-install`.

### Task C1: `engine-release.json` and `EngineRelease`

**Files:**
- Create: `Scout/Resources/engine-release.json`, `Scout/Engine/EngineRelease.swift`
- Test: `ScoutTests/Engine/EngineReleaseTests.swift`, fixture `ScoutTests/Fixtures/engine/engine-release.json`

**Interfaces:**
- Produces:
  ```swift
  struct EngineRelease: Codable, Equatable, Sendable {
      struct Engine: Codable, Equatable, Sendable { let repo, version, tag, commit: String }
      struct Uv: Codable, Equatable, Sendable { let version: String; let sha256: [String: String] }   // keyed by "aarch64-apple-darwin" / "x86_64-apple-darwin"
      let schemaVersion: Int; let engine: Engine; let uv: Uv
      var tarballName: String                          // "scout-engine-<version>.tar.gz"
      static func load(bundle: Bundle = .main) throws -> EngineRelease
      func bundledTarballURL(bundle: Bundle = .main) -> URL?   // nil when the build phase could not bundle (Debug)
  }
  ```

- [ ] **Step 1: Fill the pin**

Obtain the values (after A8's release):

```bash
git ls-remote https://github.com/Raven-Scout/scout-plugin.git 'refs/tags/v0.10.0^{}' 'refs/tags/v0.10.0'   # use the ^{} (peeled) sha if present
curl -fsSL https://github.com/astral-sh/uv/releases/download/0.12.1/uv-aarch64-apple-darwin.tar.gz.sha256
curl -fsSL https://github.com/astral-sh/uv/releases/download/0.12.1/uv-x86_64-apple-darwin.tar.gz.sha256
```

Write `Scout/Resources/engine-release.json` (the two `sha256` values are the first whitespace-separated token of each `.sha256` file; `commit` is the 40-hex sha from `ls-remote`):

```json
{
  "schema_version": 1,
  "engine": {
    "repo": "Raven-Scout/scout-plugin",
    "version": "0.10.0",
    "tag": "v0.10.0",
    "commit": "<40-hex sha of refs/tags/v0.10.0^{}>"
  },
  "uv": {
    "version": "0.12.1",
    "sha256": {
      "aarch64-apple-darwin": "<64-hex from uv-aarch64-apple-darwin.tar.gz.sha256>",
      "x86_64-apple-darwin": "<64-hex from uv-x86_64-apple-darwin.tar.gz.sha256>"
    }
  }
}
```

Copy the same file to `ScoutTests/Fixtures/engine/engine-release.json` but with `"commit": "0123456789abcdef0123456789abcdef01234567"` and the two sha256 values replaced by 64 `0`s — the fixture tests decoding, not the real pin.

- [ ] **Step 2: Write the failing tests**

`ScoutTests/Engine/EngineReleaseTests.swift`:

```swift
import Testing
import Foundation
@testable import Scout

@Suite("EngineRelease")
struct EngineReleaseTests {
    static let fixtures = Bundle(for: FixtureAnchor.self).resourceURL!.appendingPathComponent("Fixtures/engine")

    @Test func decodesThePin() throws {
        let data = try Data(contentsOf: Self.fixtures.appendingPathComponent("engine-release.json"))
        let r = try JSONDecoder().decode(EngineRelease.self, from: data)
        #expect(r.schemaVersion == 1)
        #expect(r.engine.repo == "Raven-Scout/scout-plugin")
        #expect(r.engine.tag == "v\(r.engine.version)")
        #expect(r.engine.commit.count == 40)
        #expect(r.uv.version == "0.12.1")
        #expect(r.uv.sha256["aarch64-apple-darwin"]?.count == 64)
        #expect(r.tarballName == "scout-engine-\(r.engine.version).tar.gz")
    }

    /// The real pin in the app bundle must be internally consistent, and when
    /// the build phase bundled a tarball its manifest must match the pin. In CI
    /// the tarball is required; a Debug build without network may lack it.
    @Test func bundledPinIsSelfConsistent() throws {
        let release = try EngineRelease.load(bundle: .main)
        #expect(release.engine.tag == "v\(release.engine.version)")
        #expect(release.engine.commit.range(of: "^[0-9a-f]{40}$", options: .regularExpression) != nil)
        guard let tarball = release.bundledTarballURL(bundle: .main) else {
            #expect(ProcessInfo.processInfo.environment["CI"] != "true", "CI builds must bundle the engine tarball")
            return
        }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/tar")
        p.arguments = ["-xzOf", tarball.path, ".claude-plugin/plugin.json"]
        let pipe = Pipe(); p.standardOutput = pipe
        try p.run(); p.waitUntilExit()
        struct Manifest: Decodable { let version: String }
        let manifest = try JSONDecoder().decode(Manifest.self, from: pipe.fileHandleForReading.readDataToEndOfFile())
        #expect(manifest.version == release.engine.version)
    }
}
```

- [ ] **Step 3: Run to verify failure** — build error.

- [ ] **Step 4: Implement**

`Scout/Engine/EngineRelease.swift`:

```swift
import Foundation

/// The engine this build of the app ships (spec §6). Decoded from
/// `Resources/engine-release.json`; the tarball named by `tarballName` is a
/// build product placed beside it by `scripts/bundle-engine.sh`.
struct EngineRelease: Codable, Equatable, Sendable {
    struct Engine: Codable, Equatable, Sendable { let repo: String; let version: String; let tag: String; let commit: String }
    struct Uv: Codable, Equatable, Sendable { let version: String; let sha256: [String: String] }

    let schemaVersion: Int
    let engine: Engine
    let uv: Uv

    enum CodingKeys: String, CodingKey { case schemaVersion = "schema_version", engine, uv }

    var tarballName: String { "scout-engine-\(engine.version).tar.gz" }

    struct MissingResource: Error { let name: String }

    static func load(bundle: Bundle = .main) throws -> EngineRelease {
        guard let url = bundle.url(forResource: "engine-release", withExtension: "json") else { throw MissingResource(name: "engine-release.json") }
        return try JSONDecoder().decode(EngineRelease.self, from: Data(contentsOf: url))
    }

    /// nil when this build carries no engine (Debug without a reachable source).
    func bundledTarballURL(bundle: Bundle = .main) -> URL? {
        bundle.url(forResource: "scout-engine-\(engine.version)", withExtension: "tar.gz")
    }
}
```

- [ ] **Step 5: Run, commit**

Run: `… -only-testing:ScoutTests/EngineReleaseTests …` — `decodesThePin` PASS; `bundledPinIsSelfConsistent` PASS (no tarball yet, not CI).

```bash
git add Scout/Resources/engine-release.json Scout/Engine/EngineRelease.swift ScoutTests/Engine/EngineReleaseTests.swift ScoutTests/Fixtures/engine/engine-release.json
git commit -m "feat(app): pin the bundled engine release (scout-plugin v0.10.0, uv 0.12.1)"
```

### Task C2: `scripts/bundle-engine.sh`, Xcode build phase, bash test, CI step

**Files:**
- Create: `scripts/bundle-engine.sh`, `scripts/tests/bundle-engine.test.sh`
- Modify: `Scout.xcodeproj/project.pbxproj` (new `PBXShellScriptBuildPhase`; `ENABLE_USER_SCRIPT_SANDBOXING = NO` on both project-level configurations, lines 266 and 329 — the phase needs network for the clone fallback and writes into the product)
- Modify: `.github/workflows/ci.yml` (run the bash test before `xcodebuild`)

**Interfaces:**
- Produces: env contract `SCOUT_ENGINE_PIN` (path; default `Scout/Resources/engine-release.json`), `SCOUT_ENGINE_SOURCE` (checkout to archive from), `SCOUT_ENGINE_OUT` (output dir when not an Xcode phase), `SCOUT_BUNDLE_STRICT=1` (fail instead of warn); output `<out>/scout-engine-<version>.tar.gz` made with `git archive` (tracked files only — no `.git`, `.venv`, caches).

- [ ] **Step 1: Write the failing bash test**

`scripts/tests/bundle-engine.test.sh`:

```bash
#!/usr/bin/env bash
# Tests for scripts/bundle-engine.sh against a throwaway git repo.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$HERE/../bundle-engine.sh"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
FAILS=0
assert() { if ! eval "$1"; then echo "FAIL: $2"; FAILS=$((FAILS + 1)); else echo "ok: $2"; fi; }

# A fake scout-plugin with one tagged commit.
SRC="$TMP/src"; mkdir -p "$SRC/.claude-plugin" "$SRC/engine"
git -C "$SRC" init -q
printf '{"name": "scout", "version": "9.9.9"}\n' > "$SRC/.claude-plugin/plugin.json"
echo "print('hi')" > "$SRC/engine/x.py"
mkdir -p "$SRC/.venv/bin" && echo junk > "$SRC/.venv/bin/python"   # untracked: must NOT be archived
git -C "$SRC" add .claude-plugin engine && git -C "$SRC" -c user.name=t -c user.email=t@example.com commit -qm init
git -C "$SRC" tag v9.9.9
COMMIT="$(git -C "$SRC" rev-parse HEAD)"

PIN="$TMP/pin.json"
printf '{"schema_version":1,"engine":{"repo":"example-org/scout-plugin","version":"9.9.9","tag":"v9.9.9","commit":"%s"},"uv":{"version":"0","sha256":{}}}\n' "$COMMIT" > "$PIN"

# 1. archives the pinned commit from SCOUT_ENGINE_SOURCE
OUT="$TMP/out1"
SCOUT_ENGINE_PIN="$PIN" SCOUT_ENGINE_SOURCE="$SRC" SCOUT_ENGINE_OUT="$OUT" bash "$SCRIPT"
assert '[ -f "$OUT/scout-engine-9.9.9.tar.gz" ]' "tarball produced"
assert 'tar -tzf "$OUT/scout-engine-9.9.9.tar.gz" | grep -q "^engine/x.py$"' "tracked file archived"
assert '! tar -tzf "$OUT/scout-engine-9.9.9.tar.gz" | grep -q ".venv"' "untracked .venv excluded"

# 2. refuses when the manifest version disagrees with the pin
sed 's/"version":"9.9.9"/"version":"1.0.0"/' "$PIN" > "$TMP/pin-bad.json"
set +e; SCOUT_ENGINE_PIN="$TMP/pin-bad.json" SCOUT_ENGINE_SOURCE="$SRC" SCOUT_ENGINE_OUT="$TMP/out2" bash "$SCRIPT" 2>/dev/null; RC=$?; set -e
assert '[ "$RC" -ne 0 ]' "version mismatch fails"
assert '[ ! -f "$TMP/out2/scout-engine-1.0.0.tar.gz" ]' "no tarball on mismatch"

# 3. strict mode fails when no source is reachable; lenient mode exits 0 without a tarball
sed "s/$COMMIT/ffffffffffffffffffffffffffffffffffffffff/" "$PIN" > "$TMP/pin-missing.json"
set +e; SCOUT_ENGINE_PIN="$TMP/pin-missing.json" SCOUT_ENGINE_SOURCE="$SRC" SCOUT_ENGINE_OUT="$TMP/out3" SCOUT_BUNDLE_STRICT=1 bash "$SCRIPT" 2>/dev/null; RC=$?; set -e
assert '[ "$RC" -ne 0 ]' "strict: unknown commit fails"
set +e; SCOUT_ENGINE_PIN="$TMP/pin-missing.json" SCOUT_ENGINE_SOURCE="$SRC" SCOUT_ENGINE_OUT="$TMP/out4" SCOUT_BUNDLE_STRICT=0 SCOUT_ENGINE_NO_NETWORK=1 bash "$SCRIPT" 2>/dev/null; RC=$?; set -e
assert '[ "$RC" -eq 0 ] && [ ! -d "$TMP/out4" ]' "lenient: warns and bundles nothing"

[ "$FAILS" -eq 0 ] || exit 1
```

- [ ] **Step 2: Run to verify failure** — `bash scripts/tests/bundle-engine.test.sh` → script missing.

- [ ] **Step 3: Write the script**

`scripts/bundle-engine.sh`:

```bash
#!/usr/bin/env bash
# Materialize the engine payload Scout.app ships (spec §6): a tarball of the
# scout-plugin tree at the commit pinned in Scout/Resources/engine-release.json.
#
# Source, in order:
#   1. $SCOUT_ENGINE_SOURCE            a checkout that has the pinned commit
#   2. ../scout-plugin (sibling)       if it has the pinned commit
#   3. shallow clone of the pinned tag (network; skipped if SCOUT_ENGINE_NO_NETWORK=1)
# Output: <out>/scout-engine-<version>.tar.gz — the built product's Resources
# when run as an Xcode phase, else $SCOUT_ENGINE_OUT (default build/engine/).
#
# `git archive` ships tracked files only: never .git, .venv, or caches. The
# manifest inside the archive must match the pin or the tarball is discarded.
#
# Exit: 0 with a tarball. Without a reachable source: Release/strict → 1;
# Debug → 0 with a warning and no tarball (EngineRelease reports "not bundled").
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PIN="${SCOUT_ENGINE_PIN:-$REPO_ROOT/Scout/Resources/engine-release.json}"

read_pin() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["engine"][sys.argv[2]])' "$PIN" "$1"; }
VERSION="$(read_pin version)"; TAG="$(read_pin tag)"; COMMIT="$(read_pin commit)"; REPO="$(read_pin repo)"

STRICT="${SCOUT_BUNDLE_STRICT:-0}"
[[ "${CONFIGURATION:-}" == "Release" ]] && STRICT=1

if [[ -n "${BUILT_PRODUCTS_DIR:-}" && -n "${UNLOCALIZED_RESOURCES_FOLDER_PATH:-}" ]]; then
  OUT_DIR="$BUILT_PRODUCTS_DIR/$UNLOCALIZED_RESOURCES_FOLDER_PATH"
else
  OUT_DIR="${SCOUT_ENGINE_OUT:-$REPO_ROOT/build/engine}"
fi
OUT="$OUT_DIR/scout-engine-$VERSION.tar.gz"

fail_or_warn() {
  if [[ "$STRICT" == 1 ]]; then echo "error: $* (engine must be bundled in Release builds)" >&2; exit 1; fi
  echo "warning: $* — engine not bundled in this Debug build" >&2; exit 0
}
has_commit() { git -C "$1" cat-file -e "$COMMIT^{commit}" 2>/dev/null; }

SRC=""
if [[ -n "${SCOUT_ENGINE_SOURCE:-}" ]] && has_commit "$SCOUT_ENGINE_SOURCE"; then
  SRC="$SCOUT_ENGINE_SOURCE"
elif [[ -d "$REPO_ROOT/../scout-plugin/.git" ]] && has_commit "$REPO_ROOT/../scout-plugin"; then
  SRC="$REPO_ROOT/../scout-plugin"
elif [[ "${SCOUT_ENGINE_NO_NETWORK:-0}" != 1 ]]; then
  TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
  if git clone --quiet --depth 1 --branch "$TAG" "https://github.com/$REPO.git" "$TMP/src" 2>/dev/null && has_commit "$TMP/src"; then
    SRC="$TMP/src"
  fi
fi
[[ -n "$SRC" ]] || fail_or_warn "no source with commit $COMMIT ($REPO@$TAG) reachable"

mkdir -p "$OUT_DIR"
git -C "$SRC" archive --format=tar.gz -o "$OUT" "$COMMIT"
GOT="$(tar -xzOf "$OUT" .claude-plugin/plugin.json | python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])')"
if [[ "$GOT" != "$VERSION" ]]; then
  rm -f "$OUT"
  echo "error: bundled plugin.json version $GOT != pinned $VERSION" >&2
  exit 1
fi
echo "→ bundled engine $VERSION ($COMMIT) → $OUT"
```

`chmod +x scripts/bundle-engine.sh scripts/tests/bundle-engine.test.sh`.

- [ ] **Step 4: Run the bash test** — `bash scripts/tests/bundle-engine.test.sh` → 7 `ok:` lines, exit 0.

- [ ] **Step 5: Add the Xcode phase**

In `project.pbxproj`: (a) change both `ENABLE_USER_SCRIPT_SANDBOXING = YES;` (lines 266, 329) to `NO`; (b) in the Scout target's `buildPhases` (line 84–88) append `BEEE45B02F9599AB0078191D /* Bundle Engine */,` after the Resources phase; (c) add a new section before `/* Begin PBXSourcesBuildPhase section */`:

```
/* Begin PBXShellScriptBuildPhase section */
		BEEE45B02F9599AB0078191D /* Bundle Engine */ = {
			isa = PBXShellScriptBuildPhase;
			alwaysOutOfDate = 1;
			buildActionMask = 2147483647;
			files = (
			);
			inputFileListPaths = (
			);
			inputPaths = (
				"$(SRCROOT)/Scout/Resources/engine-release.json",
				"$(SRCROOT)/scripts/bundle-engine.sh",
			);
			name = "Bundle Engine";
			outputFileListPaths = (
			);
			outputPaths = (
			);
			runOnlyForDeploymentPostprocessing = 0;
			shellPath = /bin/sh;
			shellScript = "\"$SRCROOT/scripts/bundle-engine.sh\"\n";
			showEnvVarsInLog = 0;
		};
/* End PBXShellScriptBuildPhase section */
```

Build Debug: `xcodebuild -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS' build CODE_SIGNING_ALLOWED=NO 2>&1 | grep -E "bundled engine|warning: no source|BUILD"` — on this machine the sibling `../scout-plugin` supplies the commit; the Resources folder of the built app now contains `scout-engine-0.10.0.tar.gz`.

- [ ] **Step 6: CI step**

In `.github/workflows/ci.yml` before "Run ScoutTests" add:

```yaml
      - name: Bundle-engine script tests
        run: bash scripts/tests/bundle-engine.test.sh
```

(The `xcodebuild test` step already runs the phase; the runner has git and network, so the clone fallback bundles the tarball and `EngineReleaseTests.bundledPinIsSelfConsistent` verifies it under `CI=true`.)

- [ ] **Step 7: Run `EngineReleaseTests` (now with a tarball), commit**

```bash
git add scripts/bundle-engine.sh scripts/tests/bundle-engine.test.sh Scout.xcodeproj/project.pbxproj .github/workflows/ci.yml
git commit -m "build(app): bundle the pinned scout-plugin tree into Resources via git archive"
```

### Task C3: `ClaudeCodeCLI` and `PrerequisiteChecker`

**Files:**
- Create: `Scout/Engine/ClaudeCodeCLI.swift`, `Scout/Engine/PrerequisiteChecker.swift`
- Test: `ScoutTests/Engine/ClaudeCodeCLITests.swift`, `ScoutTests/Engine/PrerequisiteCheckerTests.swift`, fixture `ScoutTests/Fixtures/claude-plugins/auth-status.json`

**Interfaces:**
- Produces:
  ```swift
  enum ClaudeCodeCLI {
      static func marketplaceAdd(path: URL) -> [String]      // ["plugin","marketplace","add", path]
      static let marketplaceUpdate: [String]                 // ["plugin","marketplace","update","scout-plugin"]
      static let pluginInstall: [String]                     // ["plugin","install","scout@scout-plugin"]
      static let pluginUpdate: [String]                      // ["plugin","update","scout@scout-plugin"]
      static let authStatus: [String]                        // ["auth","status","--json"]
      static let version: [String]                           // ["--version"]
      struct AuthStatus: Decodable, Equatable { let loggedIn: Bool; let authMethod: String?; let subscriptionType: String? }
      static func parseAuthStatus(_ data: Data) -> AuthStatus?
      static func parseVersion(_ data: Data) -> String?      // "2.1.259 (Claude Code)" → "2.1.259"
      static let installCommand = "curl -fsSL https://claude.ai/install.sh | bash"
      static func loginCommand(claude: URL) -> String        // "\"<path>\" auth login"
  }
  enum ClaudeStatus: Equatable, Sendable { case missing; case installed(path: URL, version: String?) }
  enum AuthState: Equatable, Sendable { case unknown, signedOut, signedIn }
  enum ToolState: Equatable, Sendable { case missing; case present(URL) }
  struct Prerequisites: Equatable, Sendable { let claude: ClaudeStatus; let auth: AuthState; let git: ToolState; let uv: ToolState; var canInstallEngine: Bool }
  struct PrerequisiteChecker: Sendable {
      init(runner: any ProcessRunner, layout: EngineLayout, claudePathOverride: String = "", fileManager: FileManager = .default, resolveClaude: @Sendable (String) -> String? = ClaudeLauncher.resolveClaudePath)
      func check() async -> Prerequisites
  }
  ```

- [ ] **Step 1: Fixture** — `ScoutTests/Fixtures/claude-plugins/auth-status.json`:

```json
{ "loggedIn": true, "authMethod": "claude.ai", "apiProvider": "firstParty", "email": "alex@example.com", "orgName": "Example Org", "subscriptionType": "team" }
```

- [ ] **Step 2: Write the failing tests**

`ScoutTests/Engine/ClaudeCodeCLITests.swift`:

```swift
import Testing
import Foundation
@testable import Scout

@Suite("ClaudeCodeCLI")
struct ClaudeCodeCLITests {
    @Test func argvBuilders() {
        #expect(ClaudeCodeCLI.marketplaceAdd(path: URL(fileURLWithPath: "/Users/alex/.local/share/scout/engine/current")) == ["plugin", "marketplace", "add", "/Users/alex/.local/share/scout/engine/current"])
        #expect(ClaudeCodeCLI.pluginInstall == ["plugin", "install", "scout@scout-plugin"])
        #expect(ClaudeCodeCLI.pluginUpdate == ["plugin", "update", "scout@scout-plugin"])
        #expect(ClaudeCodeCLI.marketplaceUpdate == ["plugin", "marketplace", "update", "scout-plugin"])
        #expect(ClaudeCodeCLI.authStatus == ["auth", "status", "--json"])
    }

    @Test func parsesAuthStatus() throws {
        let url = Bundle(for: FixtureAnchor.self).resourceURL!.appendingPathComponent("Fixtures/claude-plugins/auth-status.json")
        let s = ClaudeCodeCLI.parseAuthStatus(try Data(contentsOf: url))
        #expect(s == ClaudeCodeCLI.AuthStatus(loggedIn: true, authMethod: "claude.ai", subscriptionType: "team"))
        #expect(ClaudeCodeCLI.parseAuthStatus(Data("nope".utf8)) == nil)
    }

    @Test func parsesVersionLine() {
        #expect(ClaudeCodeCLI.parseVersion(Data("2.1.259 (Claude Code)\n".utf8)) == "2.1.259")
        #expect(ClaudeCodeCLI.parseVersion(Data("".utf8)) == nil)
    }

    @Test func handOffCommands() {
        #expect(ClaudeCodeCLI.installCommand == "curl -fsSL https://claude.ai/install.sh | bash")
        #expect(ClaudeCodeCLI.loginCommand(claude: URL(fileURLWithPath: "/Users/alex/.local/bin/claude")) == "\"/Users/alex/.local/bin/claude\" auth login")
    }
}
```

`ScoutTests/Engine/PrerequisiteCheckerTests.swift`:

```swift
import Testing
import Foundation
@testable import Scout

@Suite("PrerequisiteChecker")
struct PrerequisiteCheckerTests {
    func layout() throws -> EngineLayout {
        let l = EngineLayout(home: FileManager.default.temporaryDirectory.appendingPathComponent("prereq-\(UUID().uuidString)"))
        try FileManager.default.createDirectory(at: l.localBin, withIntermediateDirectories: true)
        return l
    }

    @Test func allPresentAndSignedIn() async throws {
        let l = try layout()
        try "#!/bin/sh\n".write(to: l.uvURL, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: l.uvURL.path)
        let runner = ScriptedRunner()
        runner.on(tool: "claude", prefix: ["--version"], stdout: "2.1.259 (Claude Code)\n")
        runner.on(tool: "claude", prefix: ["auth", "status"], stdout: #"{"loggedIn": true}"#)
        runner.on(tool: "xcode-select", prefix: ["-p"], stdout: "/Library/Developer/CommandLineTools\n")
        let checker = PrerequisiteChecker(runner: runner, layout: l, resolveClaude: { _ in "/Users/alex/.local/bin/claude" })
        let p = await checker.check()
        #expect(p.claude == .installed(path: URL(fileURLWithPath: "/Users/alex/.local/bin/claude"), version: "2.1.259"))
        #expect(p.auth == .signedIn)
        #expect(p.git == .present(URL(fileURLWithPath: "/usr/bin/git")))
        #expect(p.uv == .present(l.uvURL))
        #expect(p.canInstallEngine)
    }

    @Test func missingClaudeBlocksInstallAndLeavesAuthUnknown() async throws {
        let l = try layout()
        let runner = ScriptedRunner()
        runner.on(tool: "xcode-select", prefix: ["-p"], stdout: "", stderr: "xcode-select: error: unable to get active developer directory", exit: 2)
        let p = await PrerequisiteChecker(runner: runner, layout: l, resolveClaude: { _ in nil }).check()
        #expect(p.claude == .missing)
        #expect(p.auth == .unknown)
        #expect(p.git == .missing)
        #expect(p.uv == .missing)
        #expect(!p.canInstallEngine)
        #expect(runner.calls(to: "claude").isEmpty)
    }

    @Test func signedOutWhenAuthSaysSo() async throws {
        let l = try layout()
        let runner = ScriptedRunner()
        runner.on(tool: "claude", prefix: ["--version"], stdout: "2.1.259 (Claude Code)\n")
        runner.on(tool: "claude", prefix: ["auth", "status"], stdout: #"{"loggedIn": false}"#, exit: 1)
        runner.on(tool: "xcode-select", prefix: ["-p"], stdout: "/Applications/Xcode.app/Contents/Developer\n")
        let p = await PrerequisiteChecker(runner: runner, layout: l, resolveClaude: { _ in "/opt/homebrew/bin/claude" }).check()
        #expect(p.auth == .signedOut)
        #expect(p.canInstallEngine)
    }
}
```

- [ ] **Step 3: Run to verify failure** — build error.

- [ ] **Step 4: Implement**

`Scout/Engine/ClaudeCodeCLI.swift`:

```swift
import Foundation

/// The handful of Claude Code CLI invocations the installer drives, as tested
/// argv builders and decoders. Verified against Claude Code 2.1.259 (spec §12).
enum ClaudeCodeCLI {
    static func marketplaceAdd(path: URL) -> [String] { ["plugin", "marketplace", "add", path.path] }
    static let marketplaceUpdate = ["plugin", "marketplace", "update", ClaudePluginsRegistry.scoutMarketplaceName]
    static let pluginInstall = ["plugin", "install", ClaudePluginsRegistry.scoutPluginID]
    static let pluginUpdate = ["plugin", "update", ClaudePluginsRegistry.scoutPluginID]
    static let authStatus = ["auth", "status", "--json"]
    static let version = ["--version"]

    /// Anthropic's documented native installer. Shown to the user and run in
    /// their own terminal — never inside the app (spec §4.3).
    static let installCommand = "curl -fsSL https://claude.ai/install.sh | bash"
    static func loginCommand(claude: URL) -> String { "\"\(claude.path)\" auth login" }

    struct AuthStatus: Decodable, Equatable {
        let loggedIn: Bool
        let authMethod: String?
        let subscriptionType: String?
    }

    static func parseAuthStatus(_ data: Data) -> AuthStatus? { try? JSONDecoder().decode(AuthStatus.self, from: data) }

    static func parseVersion(_ data: Data) -> String? {
        guard let text = String(data: data, encoding: .utf8) else { return nil }
        let token = text.split(whereSeparator: \.isWhitespace).first.map(String.init)
        return token?.isEmpty == false ? token : nil
    }
}
```

`Scout/Engine/PrerequisiteChecker.swift`:

```swift
import Foundation

enum ClaudeStatus: Equatable, Sendable { case missing; case installed(path: URL, version: String?) }
enum AuthState: Equatable, Sendable { case unknown, signedOut, signedIn }
enum ToolState: Equatable, Sendable { case missing; case present(URL) }

struct Prerequisites: Equatable, Sendable {
    let claude: ClaudeStatus
    let auth: AuthState
    let git: ToolState
    let uv: ToolState
    /// Claude Code *installed* is the only hard gate (spec §4.3).
    var canInstallEngine: Bool { if case .installed = claude { return true }; return false }
}

/// Probes the four things the engine needs from the machine (spec §4.3).
/// Never invokes `/usr/bin/git` (a missing CLT would pop Apple's dialog).
struct PrerequisiteChecker: Sendable {
    let runner: any ProcessRunner
    let layout: EngineLayout
    var claudePathOverride: String = ""
    var fileManager: FileManager = .default
    var resolveClaude: @Sendable (String) -> String? = { ClaudeLauncher.resolveClaudePath(override: $0) }

    private static let gitCandidates = ["/opt/homebrew/bin/git", "/usr/local/bin/git", "/opt/local/bin/git"]
    private static let uvCandidates = ["/opt/homebrew/bin/uv", "/usr/local/bin/uv"]

    func check() async -> Prerequisites {
        let override = claudePathOverride
        let resolve = resolveClaude
        let claudePath = await Task.detached { resolve(override) }.value   // may consult the login shell
        var claude: ClaudeStatus = .missing
        var auth: AuthState = .unknown
        if let claudePath {
            let url = URL(fileURLWithPath: claudePath)
            let version = (try? await runner.run(executable: url, arguments: ClaudeCodeCLI.version, environment: [:], workingDirectory: nil))
                .flatMap { ClaudeCodeCLI.parseVersion($0.stdout) }
            claude = .installed(path: url, version: version)
            if let status = try? await runner.run(executable: url, arguments: ClaudeCodeCLI.authStatus, environment: [:], workingDirectory: nil),
               let parsed = ClaudeCodeCLI.parseAuthStatus(status.stdout) {
                auth = parsed.loggedIn ? .signedIn : .signedOut
            }
        }
        return Prerequisites(claude: claude, auth: auth, git: await gitState(), uv: uvState())
    }

    private func gitState() async -> ToolState {
        if let r = try? await runner.run(executable: URL(fileURLWithPath: "/usr/bin/xcode-select"), arguments: ["-p"], environment: [:], workingDirectory: nil),
           r.exitCode == 0 {
            return .present(URL(fileURLWithPath: "/usr/bin/git"))
        }
        if let path = Self.gitCandidates.first(where: { fileManager.isExecutableFile(atPath: $0) }) { return .present(URL(fileURLWithPath: path)) }
        return .missing
    }

    private func uvState() -> ToolState {
        if fileManager.isExecutableFile(atPath: layout.uvURL.path) { return .present(layout.uvURL) }
        if let path = Self.uvCandidates.first(where: { fileManager.isExecutableFile(atPath: $0) }) { return .present(URL(fileURLWithPath: path)) }
        return .missing
    }
}
```

- [ ] **Step 5: Run, commit**

Run: `… -only-testing:ScoutTests/ClaudeCodeCLITests -only-testing:ScoutTests/PrerequisiteCheckerTests …` → PASS.

```bash
git add Scout/Engine/ClaudeCodeCLI.swift Scout/Engine/PrerequisiteChecker.swift ScoutTests/Engine/ClaudeCodeCLITests.swift ScoutTests/Engine/PrerequisiteCheckerTests.swift ScoutTests/Fixtures/claude-plugins/auth-status.json
git commit -m "feat(app): Claude Code CLI argv/decoders and prerequisite checks (claude, auth, git, uv)"
```

### Task C4: `UvInstaller`

**Files:**
- Create: `Scout/Engine/UvInstaller.swift`
- Test: `ScoutTests/Engine/UvInstallerTests.swift`

**Interfaces:**
- Produces:
  ```swift
  protocol FileDownloader: Sendable { func download(_ url: URL) async throws -> URL }   // local temp file
  struct URLSessionDownloader: FileDownloader
  enum UvArch: String, Sendable { case arm64 = "aarch64-apple-darwin", x86_64 = "x86_64-apple-darwin"; static var current: UvArch }
  enum UvInstallerError: Error, Equatable { case unsupportedArch(String), missingChecksum(String), checksumMismatch(expected: String, actual: String), binaryNotInArchive }
  struct UvInstaller: Sendable {
      init(release: EngineRelease.Uv, layout: EngineLayout, downloader: any FileDownloader, runner: any ProcessRunner, arch: UvArch = .current, fileManager: FileManager = .default)
      static func assetURL(version: String, arch: UvArch) -> URL   // https://github.com/astral-sh/uv/releases/download/<v>/uv-<arch>.tar.gz
      func existing() -> URL?                                      // layout.uvURL, /opt/homebrew/bin/uv, /usr/local/bin/uv
      func ensure(log: @Sendable (String) -> Void) async throws -> URL
  }
  ```

- [ ] **Step 1: Write the failing tests**

`ScoutTests/Engine/UvInstallerTests.swift`:

```swift
import Testing
import Foundation
import CryptoKit
@testable import Scout

@Suite("UvInstaller")
struct UvInstallerTests {
    struct StubDownloader: FileDownloader {
        let file: URL
        func download(_ url: URL) async throws -> URL { file }
    }

    func layout() throws -> EngineLayout {
        let l = EngineLayout(home: FileManager.default.temporaryDirectory.appendingPathComponent("uv-\(UUID().uuidString)"))
        try FileManager.default.createDirectory(at: l.localBin, withIntermediateDirectories: true)
        return l
    }

    /// Build `uv-<arch>.tar.gz` containing `uv-<arch>/uv` the way astral ships it.
    func fakeTarball(arch: UvArch, in dir: URL) throws -> (URL, String) {
        let stage = dir.appendingPathComponent("stage/uv-\(arch.rawValue)")
        try FileManager.default.createDirectory(at: stage, withIntermediateDirectories: true)
        try "#!/bin/sh\necho uv 0.12.1\n".write(to: stage.appendingPathComponent("uv"), atomically: true, encoding: .utf8)
        let tar = dir.appendingPathComponent("uv-\(arch.rawValue).tar.gz")
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/tar")
        p.arguments = ["-czf", tar.path, "-C", dir.appendingPathComponent("stage").path, "uv-\(arch.rawValue)"]
        try p.run(); p.waitUntilExit()
        let sha = SHA256.hash(data: try Data(contentsOf: tar)).map { String(format: "%02x", $0) }.joined()
        return (tar, sha)
    }

    @Test func assetURLFollowsAstralNaming() {
        #expect(UvInstaller.assetURL(version: "0.12.1", arch: .arm64).absoluteString == "https://github.com/astral-sh/uv/releases/download/0.12.1/uv-aarch64-apple-darwin.tar.gz")
    }

    @Test func existingUvShortCircuits() async throws {
        let l = try layout()
        try "#!/bin/sh\n".write(to: l.uvURL, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: l.uvURL.path)
        let installer = UvInstaller(release: .init(version: "0.12.1", sha256: [:]), layout: l, downloader: StubDownloader(file: URL(fileURLWithPath: "/nonexistent")), runner: SystemProcessRunner(), arch: .arm64)
        #expect(try await installer.ensure(log: { _ in }) == l.uvURL)
    }

    @Test func downloadsVerifiesAndInstalls() async throws {
        let l = try layout()
        let (tar, sha) = try fakeTarball(arch: .arm64, in: l.home)
        var logs: [String] = []
        let installer = UvInstaller(release: .init(version: "0.12.1", sha256: ["aarch64-apple-darwin": sha]), layout: l,
                                    downloader: StubDownloader(file: tar), runner: SystemProcessRunner(), arch: .arm64)
        let uv = try await installer.ensure(log: { logs.append($0) })
        #expect(uv == l.uvURL)
        #expect(FileManager.default.isExecutableFile(atPath: uv.path))
        #expect(logs.contains { $0.contains("sha256 ok") })
    }

    @Test func checksumMismatchInstallsNothing() async throws {
        let l = try layout()
        let (tar, _) = try fakeTarball(arch: .arm64, in: l.home)
        let installer = UvInstaller(release: .init(version: "0.12.1", sha256: ["aarch64-apple-darwin": String(repeating: "0", count: 64)]), layout: l,
                                    downloader: StubDownloader(file: tar), runner: SystemProcessRunner(), arch: .arm64)
        await #expect(throws: UvInstallerError.self) { try await installer.ensure(log: { _ in }) }
        #expect(!FileManager.default.fileExists(atPath: l.uvURL.path))
    }

    @Test func missingChecksumForArchIsAnError() async throws {
        let l = try layout()
        let installer = UvInstaller(release: .init(version: "0.12.1", sha256: [:]), layout: l, downloader: StubDownloader(file: URL(fileURLWithPath: "/x")), runner: SystemProcessRunner(), arch: .x86_64)
        await #expect(throws: UvInstallerError.missingChecksum("x86_64-apple-darwin")) { try await installer.ensure(log: { _ in }) }
    }
}
```

- [ ] **Step 2: Run to verify failure** — build error.

- [ ] **Step 3: Implement**

`Scout/Engine/UvInstaller.swift`:

```swift
import Foundation
import CryptoKit

protocol FileDownloader: Sendable {
    /// Download to a temporary file and return its URL.
    func download(_ url: URL) async throws -> URL
}

struct URLSessionDownloader: FileDownloader {
    func download(_ url: URL) async throws -> URL {
        let (tmp, response) = try await URLSession.shared.download(from: url)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            throw URLError(.badServerResponse)
        }
        return tmp
    }
}

enum UvArch: String, Sendable {
    case arm64 = "aarch64-apple-darwin"
    case x86_64 = "x86_64-apple-darwin"

    static var current: UvArch {
        #if arch(arm64)
        return .arm64
        #else
        return .x86_64
        #endif
    }
}

enum UvInstallerError: Error, Equatable {
    case missingChecksum(String)
    case checksumMismatch(expected: String, actual: String)
    case binaryNotInArchive
    case extractFailed(String)
}

/// Obtains `uv` (spec §4.3): reuse one that exists, else download the pinned
/// release for this architecture, verify SHA-256 against the pin baked into
/// the app, and install it to `~/.local/bin/uv`.
struct UvInstaller: Sendable {
    let release: EngineRelease.Uv
    let layout: EngineLayout
    let downloader: any FileDownloader
    let runner: any ProcessRunner
    var arch: UvArch = .current
    var fileManager: FileManager = .default

    private static let systemCandidates = ["/opt/homebrew/bin/uv", "/usr/local/bin/uv"]

    static func assetURL(version: String, arch: UvArch) -> URL {
        URL(string: "https://github.com/astral-sh/uv/releases/download/\(version)/uv-\(arch.rawValue).tar.gz")!
    }

    func existing() -> URL? {
        if fileManager.isExecutableFile(atPath: layout.uvURL.path) { return layout.uvURL }
        return Self.systemCandidates.first { fileManager.isExecutableFile(atPath: $0) }.map { URL(fileURLWithPath: $0) }
    }

    func ensure(log: @Sendable (String) -> Void) async throws -> URL {
        if let uv = existing() { log("uv present at \(uv.path)"); return uv }
        guard let expected = release.sha256[arch.rawValue]?.lowercased() else { throw UvInstallerError.missingChecksum(arch.rawValue) }
        let url = Self.assetURL(version: release.version, arch: arch)
        log("downloading \(url.lastPathComponent)")
        let file = try await downloader.download(url)
        let actual = SHA256.hash(data: try Data(contentsOf: file)).map { String(format: "%02x", $0) }.joined()
        guard actual == expected else { throw UvInstallerError.checksumMismatch(expected: expected, actual: actual) }
        log("sha256 ok")
        let stage = fileManager.temporaryDirectory.appendingPathComponent("uv-extract-\(UUID().uuidString)")
        try fileManager.createDirectory(at: stage, withIntermediateDirectories: true)
        defer { try? fileManager.removeItem(at: stage) }
        let tar = try await runner.run(executable: URL(fileURLWithPath: "/usr/bin/tar"), arguments: ["-xzf", file.path, "-C", stage.path], environment: [:], workingDirectory: nil)
        guard tar.exitCode == 0 else { throw UvInstallerError.extractFailed(String(data: tar.stderr, encoding: .utf8) ?? "") }
        let binary = stage.appendingPathComponent("uv-\(arch.rawValue)/uv")
        guard fileManager.fileExists(atPath: binary.path) else { throw UvInstallerError.binaryNotInArchive }
        try fileManager.createDirectory(at: layout.localBin, withIntermediateDirectories: true)
        if fileManager.fileExists(atPath: layout.uvURL.path) { try fileManager.removeItem(at: layout.uvURL) }
        try fileManager.moveItem(at: binary, to: layout.uvURL)
        try fileManager.setAttributes([.posixPermissions: 0o755], ofItemAtPath: layout.uvURL.path)
        log("installed uv \(release.version) → \(layout.uvURL.path)")
        return layout.uvURL
    }
}
```

- [ ] **Step 4: Run, commit**

Run: `… -only-testing:ScoutTests/UvInstallerTests …` → PASS.

```bash
git add Scout/Engine/UvInstaller.swift ScoutTests/Engine/UvInstallerTests.swift
git commit -m "feat(app): UvInstaller — pinned, checksum-verified uv into ~/.local/bin"
```

### Task C5: `EngineInstaller` — the six steps

**Files:**
- Create: `Scout/Engine/EngineInstaller.swift`, `Scout/Engine/BootstrapResult.swift`
- Test: `ScoutTests/Engine/EngineInstallerTests.swift`, `ScoutTests/Engine/BootstrapResultTests.swift`

**Interfaces:**
- Consumes: `EngineLayout`, `EngineRelease`, `UvInstaller`, `ClaudeCodeCLI`, `ClaudePluginsRegistry`, `DoctorReport`, `ProcessRunner`.
- Produces:
  ```swift
  enum InstallStep: String, CaseIterable, Sendable { case ensureUv, unpackEngine, buildVenv, registerWithClaudeCode, bootstrapVault, verify }
  enum StepStatus: Equatable, Sendable { case pending, running, done, skipped(String), failed(String) }
  struct InstallProgress: Equatable, Sendable { let step: InstallStep; let status: StepStatus; let log: String }
  struct BootstrapInput: Equatable, Sendable { var vault: URL; var instanceName = "Scout"; var userName: String; var userEmail: String; var timezone: String; var connectors: Set<String> = []; var userSlackID = ""; var githubUsername = ""; var githubRepos = ""; var maxBudget = "5.00" }
  struct BootstrapResult: Decodable, Equatable, Sendable { let action: String; let reason: String?; let vault: String; let pluginVersion: String?; let error: String?; let doctor: DoctorReport?; let conflicts: [String]; let backups: [String]; let pointer: String?
      static func parse(_ data: Data) -> BootstrapResult? }
  enum InstallMode: Equatable, Sendable { case install(BootstrapInput), upgrade(vault: URL) }
  actor EngineInstaller {
      init(layout: EngineLayout, release: EngineRelease, tarballURL: URL?, runner: any ProcessRunner, uv: UvInstaller, claude: URL, fileManager: FileManager = .default, progress: @Sendable (InstallProgress) -> Void)
      func run(steps: [InstallStep], mode: InstallMode) async -> Bool          // true when every requested step is done/skipped
      static func bootstrapAutoArguments(mode: InstallMode, claude: URL, managedBy: String = "scout-app") -> [String]
      static func venvEnvironment(layout: EngineLayout, version: String, uv: URL) -> [String: String]
  }
  ```

- [ ] **Step 1: Write the failing tests**

`ScoutTests/Engine/BootstrapResultTests.swift`:

```swift
import Testing
import Foundation
@testable import Scout

@Suite("BootstrapResult")
struct BootstrapResultTests {
    @Test func decodesTheEngineContract() {
        let json = """
        {"schema_version": 1, "action": "install", "reason": "no vault: directory missing or empty", "dry_run": false,
         "vault": "/Users/alex/Scout", "plugin_version": "0.10.0", "error": null,
         "doctor": {"severity": "yellow", "errors": [], "warnings": ["snapshot missing: x"]},
         "conflicts": [], "backups": [], "snapshots_recorded": [], "pointer": "/Users/alex/.local/state/scout/engine.json"}
        """
        let r = BootstrapResult.parse(Data(json.utf8))
        #expect(r?.action == "install")
        #expect(r?.pluginVersion == "0.10.0")
        #expect(r?.doctor?.severity == .yellow)
        #expect(r?.pointer == "/Users/alex/.local/state/scout/engine.json")
    }

    @Test func refusedCarriesError() {
        let r = BootstrapResult.parse(Data(#"{"schema_version":1,"action":"refused","reason":"","dry_run":false,"vault":"/v","plugin_version":"0.10.0","error":"install needs --user-name","doctor":null,"conflicts":[],"backups":[],"snapshots_recorded":[],"pointer":null}"#.utf8))
        #expect(r?.action == "refused" && r?.error == "install needs --user-name" && r?.doctor == nil)
    }
}
```

`ScoutTests/Engine/EngineInstallerTests.swift`:

```swift
import Testing
import Foundation
@testable import Scout

@Suite("EngineInstaller")
struct EngineInstallerTests {
    let fm = FileManager.default

    struct Fixture {
        let layout: EngineLayout
        let release: EngineRelease
        let tarball: URL
        let runner: ScriptedRunner
        let claude = URL(fileURLWithPath: "/Users/alex/.local/bin/claude")
        var progress: [InstallProgress] = []
    }

    /// A tiny plugin tree tarred like `git archive` (no top-level prefix), plus
    /// a fake install-venv.sh that honors SCOUT_VENV_DIR by creating scoutctl.
    func fixture(version: String = "0.10.0") throws -> Fixture {
        let home = fm.temporaryDirectory.appendingPathComponent("installer-\(UUID().uuidString)")
        let layout = EngineLayout(home: home)
        let tree = home.appendingPathComponent("tree")
        try fm.createDirectory(at: tree.appendingPathComponent(".claude-plugin"), withIntermediateDirectories: true)
        try fm.createDirectory(at: tree.appendingPathComponent("scripts"), withIntermediateDirectories: true)
        try #"{"name": "scout", "version": "\#(version)"}"#.write(to: tree.appendingPathComponent(".claude-plugin/plugin.json"), atomically: true, encoding: .utf8)
        try "#!/bin/bash\nmkdir -p \"$SCOUT_VENV_DIR/bin\"; printf '#!/bin/sh\\necho \(version)\\n' > \"$SCOUT_VENV_DIR/bin/scoutctl\"; chmod +x \"$SCOUT_VENV_DIR/bin/scoutctl\"\n"
            .write(to: tree.appendingPathComponent("scripts/install-venv.sh"), atomically: true, encoding: .utf8)
        let tarball = home.appendingPathComponent("scout-engine-\(version).tar.gz")
        let p = Process(); p.executableURL = URL(fileURLWithPath: "/usr/bin/tar")
        p.arguments = ["-czf", tarball.path, "-C", tree.path, "."]
        try p.run(); p.waitUntilExit()
        let release = EngineRelease(schemaVersion: 1, engine: .init(repo: "example-org/scout-plugin", version: version, tag: "v\(version)", commit: String(repeating: "a", count: 40)),
                                    uv: .init(version: "0.12.1", sha256: [:]))
        // uv already present so ensureUv short-circuits without a network.
        try fm.createDirectory(at: layout.localBin, withIntermediateDirectories: true)
        try "#!/bin/sh\n".write(to: layout.uvURL, atomically: true, encoding: .utf8)
        try fm.setAttributes([.posixPermissions: 0o755], ofItemAtPath: layout.uvURL.path)
        let runner = ScriptedRunner()
        // Real tar for extraction; everything else is scripted.
        runner.on({ url, _ in url.path == "/usr/bin/tar" }) { url, args, _ in
            try await_(SystemProcessRunner().run(executable: url, arguments: args, environment: [:], workingDirectory: nil))
        }
        // `bash install-venv.sh` really runs the fake script so the venv appears.
        runner.on({ url, args in url.path == "/bin/bash" && args.first?.hasSuffix("install-venv.sh") == true }) { url, args, env in
            try await_(SystemProcessRunner().run(executable: url, arguments: args, environment: env, workingDirectory: nil))
        }
        runner.on({ url, args in url.lastPathComponent == "scoutctl" && args == ["version"] }, { _, _, _ in ProcessResult(exitCode: 0, stdout: Data("\(version)\n".utf8), stderr: Data()) })
        return Fixture(layout: layout, release: release, tarball: tarball, runner: runner)
    }

    func installer(_ f: Fixture, sink: @escaping @Sendable (InstallProgress) -> Void) -> EngineInstaller {
        EngineInstaller(layout: f.layout, release: f.release, tarballURL: f.tarball, runner: f.runner,
                        uv: UvInstaller(release: f.release.uv, layout: f.layout, downloader: URLSessionDownloader(), runner: f.runner),
                        claude: f.claude, progress: sink)
    }

    @Test func unpackBuildRegisterProducesTheCanonicalLayout() async throws {
        let f = try fixture()
        f.runner.on(tool: "claude", prefix: ["plugin", "marketplace", "add"])
        f.runner.on(tool: "claude", prefix: ["plugin", "install"])
        let ok = await installer(f) { _ in }.run(steps: [.ensureUv, .unpackEngine, .buildVenv, .registerWithClaudeCode], mode: .upgrade(vault: f.layout.home.appendingPathComponent("Scout")))
        #expect(ok)
        #expect(fm.fileExists(atPath: f.layout.engineRoot(version: "0.10.0").appendingPathComponent(".claude-plugin/plugin.json").path))
        #expect(try fm.destinationOfSymbolicLink(atPath: f.layout.currentEngineLink.path).hasSuffix("0.10.0"))
        #expect(fm.isExecutableFile(atPath: f.layout.scoutctl(version: "0.10.0").path))
        #expect(!fm.fileExists(atPath: f.layout.engineRoot(version: "0.10.0").path + ".partial"))
        #expect(f.runner.calls(to: "claude") == [ClaudeCodeCLI.marketplaceAdd(path: f.layout.currentEngineLink), ClaudeCodeCLI.pluginInstall])
        let venvCall = f.runner.calls.first { $0.arguments.first?.hasSuffix("install-venv.sh") == true }
        #expect(venvCall?.environment["SCOUT_VENV_DIR"] == f.layout.venv(version: "0.10.0").path)
        #expect(venvCall?.environment["SCOUT_VENV_EXTRAS"] == "full")
        #expect(venvCall?.environment["SCOUT_UV"] == f.layout.uvURL.path)
    }

    @Test func registerUpdatesWhenAlreadyInstalledAndStopsOnForeignMarketplace() async throws {
        let f = try fixture()
        try fm.createDirectory(at: f.layout.claudePluginsDir, withIntermediateDirectories: true)
        try #"{"scout-plugin": {"source": {"source": "github", "repo": "example-org/scout-plugin"}}}"#.write(to: f.layout.claudePluginsDir.appendingPathComponent("known_marketplaces.json"), atomically: true, encoding: .utf8)
        var seen: [InstallProgress] = []
        let ok = await installer(f) { seen.append($0) }.run(steps: [.registerWithClaudeCode], mode: .upgrade(vault: f.layout.home))
        #expect(!ok)
        guard case .failed(let why)? = seen.last?.status else { Issue.record("expected failure"); return }
        #expect(why.contains("scout-plugin") && why.contains("github"))
        #expect(f.runner.calls(to: "claude").isEmpty)
    }

    @Test func bootstrapVaultPassesTheContractArgvAndDecodesTheResult() async throws {
        let f = try fixture()
        let vault = f.layout.home.appendingPathComponent("Scout")
        f.runner.on(tool: "scoutctl", prefix: ["bootstrap", "auto"], stdout: #"{"schema_version":1,"action":"install","reason":"","dry_run":false,"vault":"\#(vault.path)","plugin_version":"0.10.0","error":null,"doctor":{"severity":"green","errors":[],"warnings":[]},"conflicts":[],"backups":[],"snapshots_recorded":[],"pointer":"p"}"#)
        // Pretend unpack+venv already happened.
        _ = await installer(f) { _ in }.run(steps: [.unpackEngine, .buildVenv], mode: .upgrade(vault: vault))
        let input = BootstrapInput(vault: vault, userName: "Alex", userEmail: "alex@example.com", timezone: "Europe/Prague", connectors: ["github", "slack"], userSlackID: "U0123", githubUsername: "alex", githubRepos: "example-org/a,example-org/b", maxBudget: "8.00")
        let ok = await installer(f) { _ in }.run(steps: [.bootstrapVault], mode: .install(input))
        #expect(ok)
        let call = f.runner.calls(to: "scoutctl").last!
        #expect(call == EngineInstaller.bootstrapAutoArguments(mode: .install(input), claude: f.claude))
        #expect(call.contains("--no-interactive") && call.contains("--yes") && call.contains("--json") && call.contains("--managed-by") && call.contains("scout-app"))
        #expect(call[call.firstIndex(of: "--connectors")! + 1] == "github,slack")
        #expect(f.runner.calls.last?.environment["SCOUT_DATA_DIR"] == vault.path)
    }

    @Test func redDoctorFailsTheBootstrapStep() async throws {
        let f = try fixture()
        let vault = f.layout.home.appendingPathComponent("Scout")
        _ = await installer(f) { _ in }.run(steps: [.unpackEngine, .buildVenv], mode: .upgrade(vault: vault))
        f.runner.on(tool: "scoutctl", prefix: ["bootstrap", "auto"], stdout: #"{"schema_version":1,"action":"upgrade","reason":"","dry_run":false,"vault":"/v","plugin_version":"0.10.0","error":null,"doctor":{"severity":"red","errors":["launchd: com.scout.heartbeat not registered"],"warnings":[]},"conflicts":[],"backups":[],"snapshots_recorded":[],"pointer":null}"#, exit: 2)
        var seen: [InstallProgress] = []
        let ok = await installer(f) { seen.append($0) }.run(steps: [.bootstrapVault], mode: .upgrade(vault: vault))
        #expect(!ok)
        guard case .failed(let why)? = seen.last?.status else { Issue.record("expected failure"); return }
        #expect(why.contains("heartbeat"))
    }

    @Test func manifestMismatchLeavesNoEngineBehind() async throws {
        var f = try fixture(version: "0.10.0")
        f = Fixture(layout: f.layout, release: EngineRelease(schemaVersion: 1, engine: .init(repo: "x", version: "0.11.0", tag: "v0.11.0", commit: String(repeating: "b", count: 40)), uv: f.release.uv), tarball: f.tarball, runner: f.runner)
        let ok = await installer(f) { _ in }.run(steps: [.unpackEngine], mode: .upgrade(vault: f.layout.home))
        #expect(!ok)
        #expect(!fm.fileExists(atPath: f.layout.engineRoot(version: "0.11.0").path))
        #expect(!fm.fileExists(atPath: f.layout.currentEngineLink.path))
    }
}

/// Bridge for calling an async runner inside ScriptedRunner's sync responder.
func await_<T: Sendable>(_ op: @autoclosure @escaping @Sendable () async throws -> T) throws -> T {
    let box = Box<Result<T, any Error>>()
    let sem = DispatchSemaphore(value: 0)
    Task.detached { box.value = await Result { try await op() }; sem.signal() }
    sem.wait()
    return try box.value!.get()
}
final class Box<T>: @unchecked Sendable { var value: T? }
extension Result where Failure == any Error {
    init(_ body: () async throws -> Success) async { do { self = .success(try await body()) } catch { self = .failure(error) } }
}
```

- [ ] **Step 2: Run to verify failure** — build error.

- [ ] **Step 3: Implement `BootstrapResult`**

`Scout/Engine/BootstrapResult.swift`:

```swift
import Foundation

/// `scoutctl bootstrap auto --json` (engine ≥ 0.10.0, spec E3). Unknown keys
/// are ignored so the engine can grow the contract additively.
struct BootstrapResult: Decodable, Equatable, Sendable {
    let action: String
    let reason: String?
    let vault: String
    let pluginVersion: String?
    let error: String?
    let doctor: DoctorReport?
    let conflicts: [String]
    let backups: [String]
    let pointer: String?

    enum CodingKeys: String, CodingKey {
        case action, reason, vault, pluginVersion = "plugin_version", error, doctor, conflicts, backups, pointer
    }

    static func parse(_ data: Data) -> BootstrapResult? { try? JSONDecoder().decode(BootstrapResult.self, from: data) }
}
```

- [ ] **Step 4: Implement the installer**

`Scout/Engine/EngineInstaller.swift`:

```swift
import Foundation

enum InstallStep: String, CaseIterable, Sendable {
    case ensureUv, unpackEngine, buildVenv, registerWithClaudeCode, bootstrapVault, verify

    var title: String {
        switch self {
        case .ensureUv: return "Get uv (Python)"
        case .unpackEngine: return "Unpack engine"
        case .buildVenv: return "Build Python environment"
        case .registerWithClaudeCode: return "Register with Claude Code"
        case .bootstrapVault: return "Set up vault and schedule"
        case .verify: return "Verify"
        }
    }
}

enum StepStatus: Equatable, Sendable { case pending, running, done, skipped(String), failed(String) }

struct InstallProgress: Equatable, Sendable {
    let step: InstallStep
    let status: StepStatus
    let log: String
}

/// Everything `scoutctl bootstrap auto` needs for a fresh install (spec §5 steps 4–5).
struct BootstrapInput: Equatable, Sendable {
    var vault: URL
    var instanceName: String = "Scout"
    var userName: String
    var userEmail: String
    var timezone: String
    var connectors: Set<String> = []
    var userSlackID: String = ""
    var githubUsername: String = ""
    var githubRepos: String = ""
    var maxBudget: String = "5.00"
}

enum InstallMode: Equatable, Sendable {
    case install(BootstrapInput)
    case upgrade(vault: URL)

    var vault: URL {
        switch self {
        case .install(let i): return i.vault
        case .upgrade(let v): return v
        }
    }
}

/// The six idempotent steps of spec §4.4, each independently re-runnable.
/// Nothing half-done ever looks whole: partial unpacks carry `.partial`,
/// `current` is repointed only after the manifest check, and the pointer is
/// written by the engine itself once its venv has run.
actor EngineInstaller {
    private let layout: EngineLayout
    private let release: EngineRelease
    private let tarballURL: URL?
    private let runner: any ProcessRunner
    private let uv: UvInstaller
    private let claude: URL
    private let fileManager: FileManager
    private let progress: @Sendable (InstallProgress) -> Void
    private var uvPath: URL?

    init(layout: EngineLayout, release: EngineRelease, tarballURL: URL?, runner: any ProcessRunner, uv: UvInstaller,
         claude: URL, fileManager: FileManager = .default, progress: @escaping @Sendable (InstallProgress) -> Void) {
        self.layout = layout; self.release = release; self.tarballURL = tarballURL; self.runner = runner
        self.uv = uv; self.claude = claude; self.fileManager = fileManager; self.progress = progress
    }

    private var version: String { release.engine.version }
    private var engineRoot: URL { layout.engineRoot(version: version) }
    private var scoutctl: URL { layout.scoutctl(version: version) }

    func run(steps: [InstallStep], mode: InstallMode) async -> Bool {
        for step in steps {
            report(step, .running, "")
            do {
                let note = try await perform(step, mode: mode)
                report(step, .done, note)
            } catch let skip as Skipped {
                report(step, .skipped(skip.reason), skip.reason)
            } catch {
                report(step, .failed(String(describing: error)), String(describing: error))
                return false
            }
        }
        return true
    }

    // MARK: steps

    private struct Skipped: Error { let reason: String }
    private struct Failure: Error, CustomStringConvertible { let description: String }

    private func perform(_ step: InstallStep, mode: InstallMode) async throws -> String {
        switch step {
        case .ensureUv:
            let path = try await uv.ensure { [progress] line in progress(InstallProgress(step: .ensureUv, status: .running, log: line)) }
            uvPath = path
            return path.path
        case .unpackEngine:
            return try await unpackEngine()
        case .buildVenv:
            return try await buildVenv()
        case .registerWithClaudeCode:
            return try await registerWithClaudeCode()
        case .bootstrapVault:
            return try await bootstrapVault(mode: mode)
        case .verify:
            let result = try await runner.run(executable: scoutctl, arguments: ["bootstrap", "doctor", "--json"],
                                              environment: ["SCOUT_DATA_DIR": mode.vault.path], workingDirectory: nil)
            guard let report = DoctorReport.parse(stdout: result.stdout) else { throw Failure(description: "doctor output not understood") }
            if report.severity == .red { throw Failure(description: report.errors.joined(separator: "; ")) }
            return "doctor: \(report.severity.rawValue)" + (report.warnings.isEmpty ? "" : " — " + report.warnings.joined(separator: "; "))
        }
    }

    private func unpackEngine() async throws -> String {
        if EngineLocator.version(atRoot: engineRoot) == version {
            try repointCurrent()
            throw Skipped(reason: "engine \(version) already unpacked")
        }
        guard let tarballURL else { throw Failure(description: "this build carries no engine tarball (Debug build without a source)") }
        let partial = URL(fileURLWithPath: engineRoot.path + ".partial")
        try? fileManager.removeItem(at: partial)
        try fileManager.createDirectory(at: partial, withIntermediateDirectories: true)
        let tar = try await runner.run(executable: URL(fileURLWithPath: "/usr/bin/tar"), arguments: ["-xzf", tarballURL.path, "-C", partial.path],
                                       environment: [:], workingDirectory: nil)
        guard tar.exitCode == 0 else { throw Failure(description: "tar failed: \(String(data: tar.stderr, encoding: .utf8) ?? "")") }
        guard EngineLocator.version(atRoot: partial) == version else {
            try? fileManager.removeItem(at: partial)
            throw Failure(description: "bundled engine manifest does not match pinned version \(version)")
        }
        try? fileManager.removeItem(at: engineRoot)
        try fileManager.moveItem(at: partial, to: engineRoot)
        try repointCurrent()
        return engineRoot.path
    }

    /// Atomic symlink swap: create `current.tmp`, then rename over `current`.
    private func repointCurrent() throws {
        try fileManager.createDirectory(at: layout.engineDir, withIntermediateDirectories: true)
        let tmp = layout.engineDir.appendingPathComponent("current.tmp")
        try? fileManager.removeItem(at: tmp)
        try fileManager.createSymbolicLink(at: tmp, withDestinationURL: URL(fileURLWithPath: version, relativeTo: layout.engineDir))
        if (try? fileManager.destinationOfSymbolicLink(atPath: layout.currentEngineLink.path)) != nil || fileManager.fileExists(atPath: layout.currentEngineLink.path) {
            _ = try fileManager.replaceItemAt(layout.currentEngineLink, withItemAt: tmp)
        } else {
            try fileManager.moveItem(at: tmp, to: layout.currentEngineLink)
        }
    }

    static func venvEnvironment(layout: EngineLayout, version: String, uv: URL) -> [String: String] {
        ["SCOUT_VENV_DIR": layout.venv(version: version).path, "SCOUT_UV": uv.path, "SCOUT_VENV_EXTRAS": "full",
         "HOME": layout.home.path, "PATH": "\(layout.localBin.path):/usr/bin:/bin"]
    }

    private func buildVenv() async throws -> String {
        let uvURL = uvPath ?? uv.existing() ?? layout.uvURL
        if fileManager.isExecutableFile(atPath: scoutctl.path),
           let v = try? await runner.run(executable: scoutctl, arguments: ["version"], environment: [:], workingDirectory: nil),
           String(data: v.stdout, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) == version {
            throw Skipped(reason: "venv for \(version) already built")
        }
        let script = engineRoot.appendingPathComponent("scripts/install-venv.sh")
        let build = try await runner.run(executable: URL(fileURLWithPath: "/bin/bash"), arguments: [script.path],
                                         environment: Self.venvEnvironment(layout: layout, version: version, uv: uvURL), workingDirectory: engineRoot)
        guard build.exitCode == 0 else { throw Failure(description: "install-venv.sh failed: \(ScheduleService.previewBytes(build.stderr.isEmpty ? build.stdout : build.stderr, max: 400))") }
        let check = try await runner.run(executable: scoutctl, arguments: ["version"], environment: [:], workingDirectory: nil)
        let got = String(data: check.stdout, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines)
        guard got == version else { throw Failure(description: "scoutctl reports \(got ?? "nothing"), expected \(version)") }
        return scoutctl.path
    }

    private func registerWithClaudeCode() async throws -> String {
        let marketplace = ClaudePluginsRegistry.scoutMarketplace(pluginsDir: layout.claudePluginsDir)
        var notes: [String] = []
        switch marketplace?.source {
        case nil:
            let add = try await runner.run(executable: claude, arguments: ClaudeCodeCLI.marketplaceAdd(path: layout.currentEngineLink), environment: [:], workingDirectory: nil)
            guard add.exitCode == 0 else { throw Failure(description: "claude plugin marketplace add failed: \(ScheduleService.previewBytes(add.stderr, max: 300))") }
            notes.append("marketplace added")
        case .directory(let path) where URL(fileURLWithPath: path).standardizedFileURL == layout.currentEngineLink.standardizedFileURL:
            notes.append("marketplace already points at the managed engine")
        case .some(let other):
            throw Failure(description: "Claude Code already has a 'scout-plugin' marketplace from another source (\(other)). Adopting it instead of replacing it — see Settings ▸ Engine → Migrate.")
        }
        let installed = ClaudePluginsRegistry.scoutPlugin(pluginsDir: layout.claudePluginsDir) != nil
        let args = installed ? ClaudeCodeCLI.pluginUpdate : ClaudeCodeCLI.pluginInstall
        let result = try await runner.run(executable: claude, arguments: args, environment: [:], workingDirectory: nil)
        guard result.exitCode == 0 else { throw Failure(description: "claude \(args.joined(separator: " ")) failed: \(ScheduleService.previewBytes(result.stderr, max: 300))") }
        notes.append(installed ? "plugin updated (restart Claude Code to load it)" : "plugin installed (restart Claude Code to load it)")
        return notes.joined(separator: "; ")
    }

    static func bootstrapAutoArguments(mode: InstallMode, claude: URL, managedBy: String = "scout-app") -> [String] {
        var args = ["bootstrap", "auto", "--no-interactive", "--yes", "--json", "--managed-by", managedBy, "--platform", "macos", "--claude-bin", claude.path]
        if case .install(let i) = mode {
            args += ["--instance-name", i.instanceName, "--user-name", i.userName, "--user-email", i.userEmail, "--timezone", i.timezone,
                     "--connectors", i.connectors.sorted().joined(separator: ","), "--user-slack-id", i.userSlackID,
                     "--github-username", i.githubUsername, "--github-repos", i.githubRepos, "--max-budget", i.maxBudget]
        }
        return args
    }

    private func bootstrapVault(mode: InstallMode) async throws -> String {
        let result = try await runner.run(executable: scoutctl, arguments: Self.bootstrapAutoArguments(mode: mode, claude: claude),
                                          environment: ["SCOUT_DATA_DIR": mode.vault.path], workingDirectory: nil)
        guard let decoded = BootstrapResult.parse(result.stdout) else {
            throw Failure(description: "bootstrap output not understood (exit \(result.exitCode)): \(ScheduleService.previewBytes(result.stderr.isEmpty ? result.stdout : result.stderr, max: 400))")
        }
        if decoded.action == "refused" { throw Failure(description: decoded.error ?? "bootstrap refused") }
        if let doctor = decoded.doctor, doctor.severity == .red { throw Failure(description: doctor.errors.joined(separator: "; ")) }
        var note = "\(decoded.action) → \(decoded.vault)"
        if let doctor = decoded.doctor { note += " (doctor: \(doctor.severity.rawValue))" }
        if !decoded.conflicts.isEmpty { note += "; conflicts to resolve: \(decoded.conflicts.joined(separator: ", "))" }
        return note
    }

    // MARK: reporting

    private func report(_ step: InstallStep, _ status: StepStatus, _ log: String) {
        progress(InstallProgress(step: step, status: status, log: log))
        appendLog("\(ISO8601DateFormatter().string(from: Date())) \(step.rawValue) \(status) \(log)\n")
    }

    private func appendLog(_ line: String) {
        try? fileManager.createDirectory(at: layout.stateDir, withIntermediateDirectories: true)
        if let handle = try? FileHandle(forWritingTo: layout.installLogURL) {
            defer { try? handle.close() }
            _ = try? handle.seekToEnd()
            try? handle.write(contentsOf: Data(line.utf8))
        } else {
            try? Data(line.utf8).write(to: layout.installLogURL)
        }
    }
}
```

- [ ] **Step 5: Run both suites, commit**

Run: `… -only-testing:ScoutTests/BootstrapResultTests -only-testing:ScoutTests/EngineInstallerTests …` → PASS.

```bash
git add Scout/Engine/EngineInstaller.swift Scout/Engine/BootstrapResult.swift ScoutTests/Engine/EngineInstallerTests.swift ScoutTests/Engine/BootstrapResultTests.swift
git commit -m "feat(app): EngineInstaller — unpack, venv, register with Claude Code, bootstrap auto, verify"
```

### Task C6: `EngineUpgrader` (+ `EngineVersion` tests)

**Files:**
- Create: `Scout/Engine/EngineUpgrader.swift`
- Test: `ScoutTests/Engine/EngineVersionTests.swift`, `ScoutTests/Engine/EngineUpgraderTests.swift`

**Interfaces:**
- Produces:
  ```swift
  struct EngineUpgrader: Sendable {
      init(layout: EngineLayout, release: EngineRelease, fileManager: FileManager = .default)
      func needsUpgrade(state: EngineState) -> Bool                               // managed && installed < bundled
      static let upgradeSteps: [InstallStep]                                      // [.ensureUv, .unpackEngine, .buildVenv, .registerWithClaudeCode, .bootstrapVault, .verify]
      func garbageCollect(keeping current: String) throws -> [String]            // removes engine/<v> + venv/<v> except current and the newest previous
  }
  ```

- [ ] **Step 1: Write the failing tests**

`ScoutTests/Engine/EngineVersionTests.swift`:

```swift
import Testing
@testable import Scout

@Suite("EngineVersion")
struct EngineVersionTests {
    @Test func ordering() {
        #expect(EngineVersion("0.9.0")! < EngineVersion("0.10.0")!)
        #expect(EngineVersion("0.10.0")! < EngineVersion("1.0.0")!)
        #expect(EngineVersion("1.0.0-rc.1")! < EngineVersion("1.0.0")!)
        #expect(EngineVersion("v0.10.0") == EngineVersion("0.10.0"))
        #expect(EngineVersion("nope") == nil)
        #expect(EngineVersion("1.2") == nil)
    }
}
```

`ScoutTests/Engine/EngineUpgraderTests.swift`:

```swift
import Testing
import Foundation
@testable import Scout

@Suite("EngineUpgrader")
struct EngineUpgraderTests {
    let fm = FileManager.default
    func layout() throws -> EngineLayout {
        let l = EngineLayout(home: fm.temporaryDirectory.appendingPathComponent("upgrader-\(UUID().uuidString)"))
        try fm.createDirectory(at: l.engineDir, withIntermediateDirectories: true)
        try fm.createDirectory(at: l.venvDir, withIntermediateDirectories: true)
        return l
    }
    func release(_ v: String) -> EngineRelease { .init(schemaVersion: 1, engine: .init(repo: "x", version: v, tag: "v\(v)", commit: ""), uv: .init(version: "0", sha256: [:])) }
    func install(_ v: String, l: EngineLayout) -> EngineInstall { .init(root: l.engineRoot(version: v), scoutctl: l.scoutctl(version: v), python: nil, version: v, vault: nil) }

    @Test func needsUpgradeOnlyForManagedAndBehind() throws {
        let l = try layout()
        let up = EngineUpgrader(layout: l, release: release("0.11.0"))
        #expect(up.needsUpgrade(state: .managed(install("0.10.0", l: l), vaultBootstrapped: true)))
        #expect(!up.needsUpgrade(state: .managed(install("0.11.0", l: l), vaultBootstrapped: true)))
        #expect(!up.needsUpgrade(state: .external(install("0.9.0", l: l), .devCheckout)))
        #expect(!up.needsUpgrade(state: .notInstalled))
    }

    @Test func garbageCollectKeepsCurrentAndOnePrevious() throws {
        let l = try layout()
        for v in ["0.9.0", "0.10.0", "0.11.0"] {
            try fm.createDirectory(at: l.engineRoot(version: v), withIntermediateDirectories: true)
            try fm.createDirectory(at: l.venv(version: v), withIntermediateDirectories: true)
        }
        try fm.createSymbolicLink(at: l.currentEngineLink, withDestinationURL: URL(fileURLWithPath: "0.11.0", relativeTo: l.engineDir))
        let removed = try EngineUpgrader(layout: l, release: release("0.11.0")).garbageCollect(keeping: "0.11.0")
        #expect(removed == ["0.9.0"])
        #expect(fm.fileExists(atPath: l.engineRoot(version: "0.10.0").path))
        #expect(!fm.fileExists(atPath: l.venv(version: "0.9.0").path))
        #expect(fm.fileExists(atPath: l.currentEngineLink.path))
    }
}
```

- [ ] **Step 2: Run to verify failure** — build error.

- [ ] **Step 3: Implement**

`Scout/Engine/EngineUpgrader.swift`:

```swift
import Foundation

/// Decides whether the bundled engine is newer than the managed one and
/// tidies old versions afterwards (spec §4.4). The steps themselves are
/// `EngineInstaller`'s; `bootstrap auto` dispatches to `upgrade`, which
/// re-renders plists and shim to the new venv and rewrites the pointer.
struct EngineUpgrader: Sendable {
    let layout: EngineLayout
    let release: EngineRelease
    var fileManager: FileManager = .default

    static let upgradeSteps: [InstallStep] = [.ensureUv, .unpackEngine, .buildVenv, .registerWithClaudeCode, .bootstrapVault, .verify]

    func needsUpgrade(state: EngineState) -> Bool {
        guard case .managed(let install, _) = state,
              let installed = install.version.flatMap(EngineVersion.init),
              let bundled = EngineVersion(release.engine.version) else { return false }
        return installed < bundled
    }

    /// Remove every `engine/<v>` and `venv/<v>` except `current` and the
    /// newest other version (spec §11 Q5: keep one previous). Returns removed versions.
    func garbageCollect(keeping current: String) throws -> [String] {
        let versions = (try? fileManager.contentsOfDirectory(atPath: layout.engineDir.path))?
            .filter { $0 != "current" && $0 != "current.tmp" && !$0.hasSuffix(".partial") && EngineVersion($0) != nil }
            .sorted { EngineVersion($0)! < EngineVersion($1)! } ?? []
        let previous = versions.filter { $0 != current }.last
        var removed: [String] = []
        for v in versions where v != current && v != previous {
            try? fileManager.removeItem(at: layout.engineRoot(version: v))
            try? fileManager.removeItem(at: layout.venv(version: v))
            removed.append(v)
        }
        return removed
    }
}
```

- [ ] **Step 4: Run, commit**

```bash
git add Scout/Engine/EngineUpgrader.swift ScoutTests/Engine/EngineVersionTests.swift ScoutTests/Engine/EngineUpgraderTests.swift
git commit -m "feat(app): EngineUpgrader — bundled-vs-installed decision and version GC"
```

### Task C7: Onboarding — `TerminalHandoff`, `ConnectorDetection`, `OnboardingViewModel`, `OnboardingView`

**Files:**
- Create: `Scout/Utilities/TerminalHandoff.swift`, `Scout/Engine/ConnectorDetection.swift`, `Scout/Onboarding/OnboardingViewModel.swift`, `Scout/Onboarding/OnboardingView.swift`
- Modify: `Scout/Utilities/ClaudeLauncher.swift:540` (`runAppleScript` from `private` to internal)
- Test: `ScoutTests/Engine/ConnectorDetectionTests.swift`, `ScoutTests/Utilities/TerminalHandoffTests.swift`, `ScoutTests/Onboarding/OnboardingViewModelTests.swift`

**Interfaces:**
- Produces:
  ```swift
  enum TerminalHandoff { static func makeScript(command: String) -> String; static func run(_ command: String) throws }
  struct ConnectorDetection: Decodable, Equatable, Sendable { enum Status: String, Decodable { case connected, needsAuth = "needs_auth", unavailable, unknown }; let status: Status; let needsUserInput: [String]; let evidence: String
      static func parse(_ data: Data) -> [String: ConnectorDetection]? }
  @MainActor final class OnboardingViewModel: ObservableObject {
      enum Step: Int, CaseIterable { case welcome, prerequisites, engine, identity, connectors, vault, ready }
      @Published var step: Step; @Published var vaultPath: String; @Published var prerequisites: Prerequisites?; @Published var progress: [InstallStep: InstallProgress]
      @Published var identity: BootstrapInput; @Published var detections: [String: ConnectorDetection]; @Published var enabledConnectors: Set<String>; @Published var lastError: String?; @Published var busy: Bool
      init(engineState: EngineState, layout: EngineLayout, release: EngineRelease?, runner: any ProcessRunner, prerequisites: PrerequisiteChecker, makeInstaller: @escaping (@Sendable (InstallProgress) -> Void) -> EngineInstaller?, handoff: @escaping (String) throws -> Void = TerminalHandoff.run, onFinished: @escaping () -> Void)
      var canContinue: Bool; func start() async; func continueTapped() async; func back()
      func installClaudeCode(); func signIn(); func installCommandLineTools() async; func recheckPrerequisites() async
      func installEngine() async; func detectConnectors() async; func createVault() async; func runFirstBriefing() async
      static func initialStep(engineState: EngineState, prerequisites: Prerequisites) -> Step
      static func prefilledIdentity(gitName: String?, gitEmail: String?, vault: URL) -> BootstrapInput
  }
  struct OnboardingView: View { @ObservedObject var model: OnboardingViewModel }
  ```

- [ ] **Step 1: Write the failing tests**

`ScoutTests/Utilities/TerminalHandoffTests.swift`:

```swift
import Testing
@testable import Scout

@Suite("TerminalHandoff")
struct TerminalHandoffTests {
    @Test func scriptRunsTheCommandVisiblyInTerminal() {
        let s = TerminalHandoff.makeScript(command: "curl -fsSL https://claude.ai/install.sh | bash")
        #expect(s.contains("tell application \"Terminal\""))
        #expect(s.contains("activate"))
        #expect(s.contains("do script \"curl -fsSL https://claude.ai/install.sh | bash\""))
    }

    @Test func escapesQuotesForAppleScript() {
        let s = TerminalHandoff.makeScript(command: "\"/Users/alex/.local/bin/claude\" auth login")
        #expect(s.contains(#"do script "\"/Users/alex/.local/bin/claude\" auth login""#))
    }
}
```

`ScoutTests/Engine/ConnectorDetectionTests.swift`:

```swift
import Testing
import Foundation
@testable import Scout

@Suite("ConnectorDetection")
struct ConnectorDetectionTests {
    @Test func decodesScoutctlConnectorsDetect() {
        let json = #"{"email": {"status": "connected", "needs_user_input": [], "evidence": "claude.ai Gmail: … - ✔ Connected"}, "slack": {"status": "needs_auth", "needs_user_input": ["user_slack_id"], "evidence": "…"}, "fathom": {"status": "unknown", "needs_user_input": [], "evidence": "no matching MCP server"}}"#
        let d = ConnectorDetection.parse(Data(json.utf8))
        #expect(d?["email"]?.status == .connected)
        #expect(d?["slack"]?.status == .needsAuth && d?["slack"]?.needsUserInput == ["user_slack_id"])
        #expect(d?["fathom"]?.status == .unknown)
        #expect(ConnectorDetection.parse(Data("x".utf8)) == nil)
    }
}
```

`ScoutTests/Onboarding/OnboardingViewModelTests.swift`:

```swift
import Testing
import Foundation
@testable import Scout

@Suite("OnboardingViewModel")
@MainActor
struct OnboardingViewModelTests {
    let layout = EngineLayout(home: URL(fileURLWithPath: "/Users/alex"))
    let install = EngineInstall(root: URL(fileURLWithPath: "/e"), scoutctl: URL(fileURLWithPath: "/s"), python: nil, version: "0.10.0", vault: URL(fileURLWithPath: "/Users/alex/Scout"))
    let ready = Prerequisites(claude: .installed(path: URL(fileURLWithPath: "/c"), version: "2.1.259"), auth: .signedIn, git: .present(URL(fileURLWithPath: "/usr/bin/git")), uv: .missing)

    @Test func initialStepFollowsTheEngineState() {
        #expect(OnboardingViewModel.initialStep(engineState: .notInstalled, prerequisites: ready) == .welcome)
        #expect(OnboardingViewModel.initialStep(engineState: .notInstalled, prerequisites: Prerequisites(claude: .missing, auth: .unknown, git: .missing, uv: .missing)) == .welcome)
        #expect(OnboardingViewModel.initialStep(engineState: .managed(install, vaultBootstrapped: false), prerequisites: ready) == .identity)
        #expect(OnboardingViewModel.initialStep(engineState: .managed(install, vaultBootstrapped: true), prerequisites: ready) == .ready)
        #expect(OnboardingViewModel.initialStep(engineState: .broken(nil, reason: "x"), prerequisites: ready) == .welcome)
    }

    @Test func prerequisitesGateOnlyOnClaudeInstalled() {
        let model = OnboardingViewModel(engineState: .notInstalled, layout: layout, release: nil, runner: ScriptedRunner(),
                                        prerequisites: PrerequisiteChecker(runner: ScriptedRunner(), layout: layout), makeInstaller: { _ in nil }, handoff: { _ in }, onFinished: {})
        model.step = .prerequisites
        model.prerequisites = Prerequisites(claude: .missing, auth: .unknown, git: .present(URL(fileURLWithPath: "/usr/bin/git")), uv: .missing)
        #expect(!model.canContinue)
        model.prerequisites = Prerequisites(claude: .installed(path: URL(fileURLWithPath: "/c"), version: nil), auth: .signedOut, git: .missing, uv: .missing)
        #expect(model.canContinue)   // sign-in and git are surfaced, not blocking
    }

    @Test func identityStepRequiresNameAndEmail() {
        let model = OnboardingViewModel(engineState: .notInstalled, layout: layout, release: nil, runner: ScriptedRunner(),
                                        prerequisites: PrerequisiteChecker(runner: ScriptedRunner(), layout: layout), makeInstaller: { _ in nil }, handoff: { _ in }, onFinished: {})
        model.step = .identity
        model.identity = OnboardingViewModel.prefilledIdentity(gitName: nil, gitEmail: nil, vault: URL(fileURLWithPath: "/Users/alex/Scout"))
        #expect(!model.canContinue)
        model.identity.userName = "Alex"; model.identity.userEmail = "alex@example.com"
        #expect(model.canContinue)
    }

    @Test func prefillUsesGitConfigAndSystemTimezone() {
        let i = OnboardingViewModel.prefilledIdentity(gitName: "Alex", gitEmail: "alex@example.com", vault: URL(fileURLWithPath: "/Users/alex/Scout"))
        #expect(i.userName == "Alex" && i.userEmail == "alex@example.com" && i.timezone == TimeZone.current.identifier && i.instanceName == "Scout")
    }

    @Test func handoffsRunTheDocumentedCommands() {
        var ran: [String] = []
        let model = OnboardingViewModel(engineState: .notInstalled, layout: layout, release: nil, runner: ScriptedRunner(),
                                        prerequisites: PrerequisiteChecker(runner: ScriptedRunner(), layout: layout), makeInstaller: { _ in nil }, handoff: { ran.append($0) }, onFinished: {})
        model.prerequisites = ready
        model.installClaudeCode()
        model.signIn()
        #expect(ran == [ClaudeCodeCLI.installCommand, ClaudeCodeCLI.loginCommand(claude: URL(fileURLWithPath: "/c"))])
    }

    @Test func detectConnectorsEnablesConnectedOnes() async {
        let runner = ScriptedRunner()
        runner.on(tool: "scoutctl", prefix: ["connectors", "detect"], stdout: #"{"email": {"status": "connected", "needs_user_input": [], "evidence": ""}, "slack": {"status": "needs_auth", "needs_user_input": ["user_slack_id"], "evidence": ""}, "github": {"status": "connected", "needs_user_input": ["github_username"], "evidence": ""}}"#)
        let model = OnboardingViewModel(engineState: .managed(install, vaultBootstrapped: false), layout: layout, release: nil, runner: runner,
                                        prerequisites: PrerequisiteChecker(runner: runner, layout: layout), makeInstaller: { _ in nil }, handoff: { _ in }, onFinished: {})
        model.prerequisites = ready
        await model.detectConnectors()
        #expect(model.enabledConnectors == ["email", "github"])
        #expect(model.detections["slack"]?.status == .needsAuth)
        #expect(runner.calls(to: "scoutctl").first == ["connectors", "detect", "--json", "--claude-bin", "/c"])
    }
}
```

- [ ] **Step 2: Run to verify failure** — build error.

- [ ] **Step 3: Implement `TerminalHandoff` and `ConnectorDetection`**

`Scout/Utilities/TerminalHandoff.swift`:

```swift
import Foundation

/// Opens Terminal.app running one command, visibly, in front of the user
/// (spec §4.3): the app never runs Anthropic's installer or a login flow
/// itself. Reuses ClaudeLauncher's AppleScript escaping and runner.
enum TerminalHandoff {
    static func makeScript(command: String) -> String {
        """
        tell application "Terminal"
          activate
          do script "\(ClaudeLauncher.appleScriptEscape(command))"
        end tell
        """
    }

    @MainActor
    static func run(_ command: String) throws {
        try ClaudeLauncher.runAppleScript(makeScript(command: command))
    }
}
```

(In `ClaudeLauncher.swift` change `private static func runAppleScript` to `static func runAppleScript`.)

`Scout/Engine/ConnectorDetection.swift`:

```swift
import Foundation

/// One entry of `scoutctl connectors detect --json` (engine ≥ 0.10.0, spec E4).
struct ConnectorDetection: Decodable, Equatable, Sendable {
    enum Status: String, Decodable, Sendable { case connected, needsAuth = "needs_auth", unavailable, unknown }
    let status: Status
    let needsUserInput: [String]
    let evidence: String

    enum CodingKeys: String, CodingKey { case status, needsUserInput = "needs_user_input", evidence }

    static func parse(_ data: Data) -> [String: ConnectorDetection]? {
        try? JSONDecoder().decode([String: ConnectorDetection].self, from: data)
    }

    /// Human labels for the connectors the shipped registry knows; unknown keys show as-is.
    static let displayNames: [String: String] = [
        "slack": "Slack", "calendar": "Google Calendar", "email": "Gmail", "linear": "Linear", "github": "GitHub",
        "granola": "Granola", "fathom": "Fathom", "drive": "Google Drive", "claude_sessions": "Claude Code sessions",
    ]
}
```

- [ ] **Step 4: Implement the view model**

`Scout/Onboarding/OnboardingViewModel.swift`:

```swift
import Foundation
import Combine

/// State machine behind OnboardingView (spec §5). All work runs off the main
/// actor; every mutation lands here on @MainActor.
@MainActor
final class OnboardingViewModel: ObservableObject {
    enum Step: Int, CaseIterable { case welcome, prerequisites, engine, identity, connectors, vault, ready }

    @Published var step: Step
    @Published var vaultPath: String
    @Published var prerequisites: Prerequisites?
    @Published var progress: [InstallStep: InstallProgress] = [:]
    @Published var identity: BootstrapInput
    @Published var detections: [String: ConnectorDetection] = [:]
    @Published var enabledConnectors: Set<String> = []
    @Published var lastError: String?
    @Published var busy = false
    @Published var doctor: DoctorReport?

    let engineState: EngineState
    let layout: EngineLayout
    let release: EngineRelease?
    private let runner: any ProcessRunner
    private let checker: PrerequisiteChecker
    private let makeInstaller: (@Sendable (InstallProgress) -> Void) -> EngineInstaller?
    private let handoff: (String) throws -> Void
    private let onFinished: () -> Void

    init(engineState: EngineState, layout: EngineLayout, release: EngineRelease?, runner: any ProcessRunner,
         prerequisites: PrerequisiteChecker,
         makeInstaller: @escaping (@Sendable (InstallProgress) -> Void) -> EngineInstaller?,
         handoff: @escaping (String) throws -> Void = { try TerminalHandoff.run($0) },
         onFinished: @escaping () -> Void) {
        self.engineState = engineState; self.layout = layout; self.release = release; self.runner = runner
        self.checker = prerequisites; self.makeInstaller = makeInstaller; self.handoff = handoff; self.onFinished = onFinished
        let vault = engineState.install?.vault ?? layout.home.appending(path: "Scout")
        self.vaultPath = vault.path
        self.identity = Self.prefilledIdentity(gitName: nil, gitEmail: nil, vault: vault)
        self.step = .welcome
    }

    // MARK: derived

    var vaultURL: URL { URL(fileURLWithPath: (vaultPath as NSString).expandingTildeInPath) }
    var claudePath: URL? { if case .installed(let p, _)? = prerequisites?.claude { return p }; return nil }
    var engineScoutctl: URL? { release.map { layout.scoutctl(version: $0.engine.version) } ?? engineState.scoutctl }

    var canContinue: Bool {
        switch step {
        case .welcome: return !vaultPath.trimmingCharacters(in: .whitespaces).isEmpty
        case .prerequisites: return prerequisites?.canInstallEngine == true
        case .engine: return [.unpackEngine, .buildVenv, .registerWithClaudeCode].allSatisfy { isDone($0) }
        case .identity: return !identity.userName.trimmingCharacters(in: .whitespaces).isEmpty && identity.userEmail.contains("@")
        case .connectors: return true
        case .vault: return isDone(.bootstrapVault)
        case .ready: return true
        }
    }

    private func isDone(_ s: InstallStep) -> Bool {
        switch progress[s]?.status { case .done?, .skipped?: return true; default: return false }
    }

    static func initialStep(engineState: EngineState, prerequisites: Prerequisites) -> Step {
        switch engineState {
        case .managed(_, vaultBootstrapped: true), .external: return .ready
        case .managed(_, vaultBootstrapped: false): return prerequisites.canInstallEngine ? .identity : .prerequisites
        case .notInstalled, .broken: return .welcome
        }
    }

    static func prefilledIdentity(gitName: String?, gitEmail: String?, vault: URL) -> BootstrapInput {
        BootstrapInput(vault: vault, userName: gitName ?? "", userEmail: gitEmail ?? "", timezone: TimeZone.current.identifier)
    }

    // MARK: flow

    func start() async {
        prerequisites = await checker.check()
        let (name, email) = await gitIdentity()
        identity = Self.prefilledIdentity(gitName: name, gitEmail: email, vault: vaultURL)
        step = Self.initialStep(engineState: engineState, prerequisites: prerequisites!)
    }

    func continueTapped() async {
        guard canContinue, let next = Step(rawValue: step.rawValue + 1) else { return }
        identity.vault = vaultURL
        step = next
        switch next {
        case .prerequisites: await recheckPrerequisites()
        case .engine: await installEngine()
        case .connectors: await detectConnectors()
        case .vault: await createVault()
        case .ready: await refreshDoctor()
        default: break
        }
    }

    func back() { if let prev = Step(rawValue: step.rawValue - 1) { step = prev } }
    func finish() { onFinished() }

    // MARK: prerequisites

    func recheckPrerequisites() async { prerequisites = await checker.check() }

    func installClaudeCode() { runHandoff(ClaudeCodeCLI.installCommand) }

    func signIn() {
        guard let claudePath else { return }
        runHandoff(ClaudeCodeCLI.loginCommand(claude: claudePath))
    }

    func installCommandLineTools() async {
        _ = try? await runner.run(executable: URL(fileURLWithPath: "/usr/bin/xcode-select"), arguments: ["--install"], environment: [:], workingDirectory: nil)
    }

    private func runHandoff(_ command: String) {
        do { try handoff(command) } catch { lastError = "Could not open Terminal: \(error.localizedDescription)" }
    }

    // MARK: engine

    func installEngine() async {
        await runInstaller(steps: [.ensureUv, .unpackEngine, .buildVenv, .registerWithClaudeCode], mode: .upgrade(vault: vaultURL))
    }

    func createVault() async {
        identity.vault = vaultURL
        identity.connectors = enabledConnectors
        await runInstaller(steps: [.bootstrapVault, .verify], mode: .install(identity))
    }

    private func runInstaller(steps: [InstallStep], mode: InstallMode) async {
        busy = true; lastError = nil
        defer { busy = false }
        guard let installer = makeInstaller({ [weak self] p in Task { @MainActor in self?.progress[p.step] = p } }) else {
            lastError = "This build of Scout carries no engine (Debug build without a bundled tarball)."; return
        }
        let ok = await installer.run(steps: steps, mode: mode)
        if !ok, let failed = progress.values.first(where: { if case .failed = $0.status { return true }; return false }) {
            lastError = failed.log
        }
    }

    // MARK: connectors

    func detectConnectors() async {
        guard let scoutctl = engineScoutctl, let claudePath else { return }
        busy = true; defer { busy = false }
        guard let result = try? await runner.run(executable: scoutctl, arguments: ["connectors", "detect", "--json", "--claude-bin", claudePath.path],
                                                 environment: ["SCOUT_DATA_DIR": vaultURL.path], workingDirectory: nil),
              let parsed = ConnectorDetection.parse(result.stdout) else {
            lastError = "Could not detect connectors — you can still pick them by hand."; return
        }
        detections = parsed
        enabledConnectors = Set(parsed.filter { $0.value.status == .connected }.map(\.key))
    }

    // MARK: ready

    func refreshDoctor() async {
        guard let scoutctl = engineScoutctl else { return }
        if let r = try? await runner.run(executable: scoutctl, arguments: ["bootstrap", "doctor", "--json"], environment: ["SCOUT_DATA_DIR": vaultURL.path], workingDirectory: nil) {
            doctor = DoctorReport.parse(stdout: r.stdout)
        }
    }

    func runFirstBriefing() async {
        guard let scoutctl = engineScoutctl else { return }
        struct Slot: Decodable { let key: String; let type: String? }
        guard let list = try? await runner.run(executable: scoutctl, arguments: ["schedule", "list", "--json"], environment: ["SCOUT_DATA_DIR": vaultURL.path], workingDirectory: nil),
              let slots = try? JSONDecoder().decode([Slot].self, from: list.stdout),
              let briefing = slots.first(where: { $0.type == "briefing" }) ?? slots.first else { lastError = "No briefing slot found in the schedule."; return }
        _ = try? await runner.run(executable: scoutctl, arguments: ["schedule", "fire-now", briefing.key], environment: ["SCOUT_DATA_DIR": vaultURL.path], workingDirectory: vaultURL)
    }

    private func gitIdentity() async -> (String?, String?) {
        func read(_ key: String) async -> String? {
            guard let r = try? await runner.run(executable: URL(fileURLWithPath: "/usr/bin/git"), arguments: ["config", "--global", key], environment: [:], workingDirectory: nil),
                  r.exitCode == 0 else { return nil }
            let s = String(data: r.stdout, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines)
            return s?.isEmpty == false ? s : nil
        }
        guard prerequisites?.git != .missing else { return (nil, nil) }   // never trigger the CLT dialog
        return (await read("user.name"), await read("user.email"))
    }
}
```

(Confirm the `schedule list --json` element shape against `ScheduleService`'s decoder before relying on `type`; if slots carry `mode` instead of `type`, decode that field.)

- [ ] **Step 5: Implement the view**

`Scout/Onboarding/OnboardingView.swift` — one file, thin, DS tokens throughout:

```swift
import SwiftUI

struct OnboardingView: View {
    @ObservedObject var model: OnboardingViewModel

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            stepIndicator.padding(.bottom, 20)
            ScrollView { content.frame(maxWidth: 640, alignment: .leading) }
            footer
        }
        .padding(32)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .task { await model.start() }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Set up Scout").font(DS.serif(24, weight: .medium)).foregroundStyle(DS.Ink.p1)
            Text(subtitle).font(DS.sans(12.5)).foregroundStyle(DS.Ink.p3)
        }.padding(.bottom, 14)
    }

    private var subtitle: String {
        switch model.step {
        case .welcome: return "Scout.app installs and manages everything it needs — except Claude Code itself."
        case .prerequisites: return "Two things must already be on this Mac."
        case .engine: return "Installing the Scout engine."
        case .identity: return "Used in commit messages and the knowledge base."
        case .connectors: return "Detected from Claude Code. Toggle anything the detection got wrong."
        case .vault: return "Creating your vault and scheduling the sessions."
        case .ready: return "Scout is set up."
        }
    }

    private var stepIndicator: some View {
        HStack(spacing: 6) {
            ForEach(OnboardingViewModel.Step.allCases, id: \.rawValue) { s in
                Capsule().fill(s.rawValue <= model.step.rawValue ? DS.Accent.fill : DS.Paper.sunk).frame(height: 4)
            }
        }
    }

    @ViewBuilder private var content: some View {
        switch model.step {
        case .welcome: welcome
        case .prerequisites: prerequisites
        case .engine: progressList([.ensureUv, .unpackEngine, .buildVenv, .registerWithClaudeCode])
        case .identity: identity
        case .connectors: connectors
        case .vault: progressList([.bootstrapVault, .verify])
        case .ready: ready
        }
    }

    private var welcome: some View {
        VStack(alignment: .leading, spacing: 12) {
            bullet("The Scout engine (a Claude Code plugin) goes to `~/.local/share/scout`.")
            bullet("A private Python environment is built with uv — nothing system-wide changes.")
            bullet("Claude Code learns the `/scout-*` commands; scheduled sessions run via launchd.")
            bullet("Your knowledge base and action items live in the vault folder below.")
            field("Vault folder", text: $model.vaultPath, placeholder: "~/Scout")
        }
    }

    private var prerequisites: some View {
        VStack(alignment: .leading, spacing: 10) {
            let p = model.prerequisites
            prereqRow("Claude Code", ok: p?.canInstallEngine == true,
                      detail: { if case .installed(_, let v)? = p?.claude { return "Installed" + (v.map { " (\($0))" } ?? "") } else { return "Not found. Install it in Terminal, then re-check." } }(),
                      action: p?.canInstallEngine == true ? nil : ("Install in Terminal…", { model.installClaudeCode() }))
            prereqRow("Signed in to Claude", ok: p?.auth == .signedIn,
                      detail: p?.auth == .signedIn ? "Signed in" : "Scheduled runs need a signed-in Claude Code. You can finish setup first.",
                      action: p?.auth == .signedIn || p?.canInstallEngine != true ? nil : ("Sign in…", { model.signIn() }))
            prereqRow("Command Line Tools (git)", ok: p?.git != .missing,
                      detail: p?.git != .missing ? "Present" : "Your vault is a git repository. Apple will prompt to install the tools.",
                      action: p?.git != .missing ? nil : ("Install…", { Task { await model.installCommandLineTools() } }))
            Button("Re-check") { Task { await model.recheckPrerequisites() } }.buttonStyle(.plainHit).font(DS.sans(12, weight: .medium))
        }
        .task { while model.step == .prerequisites, model.prerequisites?.canInstallEngine != true || model.prerequisites?.auth != .signedIn {
            try? await Task.sleep(for: .seconds(3)); await model.recheckPrerequisites() } }
    }

    private var identity: some View {
        VStack(alignment: .leading, spacing: 12) {
            field("Instance name", text: $model.identity.instanceName, placeholder: "Scout")
            field("Your name", text: $model.identity.userName, placeholder: "Alex")
            field("Email", text: $model.identity.userEmail, placeholder: "alex@example.com")
            field("Timezone", text: $model.identity.timezone, placeholder: TimeZone.current.identifier)
        }
    }

    private var connectors: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(model.detections.keys.sorted(), id: \.self) { key in
                let d = model.detections[key]!
                Toggle(isOn: Binding(get: { model.enabledConnectors.contains(key) }, set: { on in if on { model.enabledConnectors.insert(key) } else { model.enabledConnectors.remove(key) } })) {
                    HStack {
                        Text(ConnectorDetection.displayNames[key] ?? key).font(DS.sans(13, weight: .medium)).foregroundStyle(DS.Ink.p1)
                        Text(statusLabel(d.status)).font(DS.sans(11.5)).foregroundStyle(d.status == .connected ? DS.Status.ok : DS.Ink.p3)
                    }
                }
            }
            if model.enabledConnectors.contains("slack") { field("Slack user ID", text: $model.identity.userSlackID, placeholder: "U0123456789") }
            if model.enabledConnectors.contains("github") {
                field("GitHub username", text: $model.identity.githubUsername, placeholder: "alex")
                field("GitHub repos to watch (comma-separated)", text: $model.identity.githubRepos, placeholder: "example-org/app,example-org/api")
            }
            field("Per-session budget (USD)", text: $model.identity.maxBudget, placeholder: "5.00")
            Text("Connectors are authorized in Claude Code (`claude mcp list`). Re-detect after connecting more.").font(DS.sans(11.5)).foregroundStyle(DS.Ink.p3)
            Button("Re-detect") { Task { await model.detectConnectors() } }.buttonStyle(.plainHit).font(DS.sans(12, weight: .medium))
        }
    }

    private var ready: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let d = model.doctor {
                Text("Health: \(d.severity.rawValue)").font(DS.sans(13, weight: .medium)).foregroundStyle(d.severity == .red ? DS.Status.warn : DS.Status.ok)
                ForEach(d.errors + d.warnings, id: \.self) { Text($0).font(DS.mono(11)).foregroundStyle(DS.Ink.p3) }
            }
            Button("Run your first briefing now") { Task { await model.runFirstBriefing() } }.buttonStyle(.plainHit).font(DS.sans(13, weight: .medium))
            if model.prerequisites?.auth != .signedIn { Text("Sign in to Claude Code before the first scheduled run.").font(DS.sans(11.5)).foregroundStyle(DS.Status.warn) }
        }
    }

    private func progressList(_ steps: [InstallStep]) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(steps, id: \.rawValue) { s in
                let p = model.progress[s]
                HStack(alignment: .top, spacing: 10) {
                    Text(glyph(p?.status)).font(DS.mono(13)).frame(width: 16)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(s.title).font(DS.sans(13, weight: .medium)).foregroundStyle(DS.Ink.p1)
                        if let log = p?.log, !log.isEmpty { Text(log).font(DS.mono(11)).foregroundStyle(DS.Ink.p3).lineLimit(3) }
                    }
                }
            }
            if let err = model.lastError {
                Text(err).font(DS.sans(12)).foregroundStyle(DS.Status.warn)
                Button("Retry") { Task { model.step == .engine ? await model.installEngine() : await model.createVault() } }.buttonStyle(.plainHit)
            }
        }
    }

    private var footer: some View {
        HStack {
            if model.step != .welcome && model.step != .ready { Button("Back") { model.back() }.buttonStyle(.plainHit) }
            Spacer()
            if model.step == .ready {
                Button("Open Scout") { model.finish() }.buttonStyle(.plainHit).font(DS.sans(13, weight: .medium))
            } else {
                Button(model.busy ? "Working…" : "Continue") { Task { await model.continueTapped() } }
                    .buttonStyle(.plainHit).font(DS.sans(13, weight: .medium)).disabled(!model.canContinue || model.busy)
            }
        }.padding(.top, 16)
    }

    // MARK: atoms

    private func bullet(_ text: String) -> some View { Text("• " + text).font(DS.sans(13)).foregroundStyle(DS.Ink.p2) }
    private func field(_ label: String, text: Binding<String>, placeholder: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label.uppercased()).font(DS.sans(11, weight: .medium)).tracking(0.66).foregroundStyle(DS.Ink.p4)
            TextField(placeholder, text: text).textFieldStyle(.plain).font(DS.sans(13, weight: .medium)).padding(.horizontal, 10).frame(height: 30)
                .background(RoundedRectangle(cornerRadius: 6).fill(DS.Paper.sunk).overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(DS.Rule.soft, lineWidth: 0.5)))
        }
    }
    private func prereqRow(_ title: String, ok: Bool, detail: String, action: (String, () -> Void)?) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Text(ok ? "✓" : "○").font(DS.mono(13)).foregroundStyle(ok ? DS.Status.ok : DS.Ink.p3).frame(width: 16)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(DS.sans(13, weight: .medium)).foregroundStyle(DS.Ink.p1)
                Text(detail).font(DS.sans(11.5)).foregroundStyle(DS.Ink.p3)
            }
            Spacer()
            if let (label, act) = action { Button(label, action: act).buttonStyle(.plainHit).font(DS.sans(12, weight: .medium)) }
        }
    }
    private func glyph(_ s: StepStatus?) -> String {
        switch s { case .done?: return "✓"; case .skipped?: return "–"; case .running?: return "…"; case .failed?: return "✗"; default: return "○" }
    }
    private func statusLabel(_ s: ConnectorDetection.Status) -> String {
        switch s { case .connected: return "connected"; case .needsAuth: return "needs authentication in Claude Code"; case .unavailable: return "not found"; case .unknown: return "could not detect" }
    }
}
```

- [ ] **Step 6: Run the three suites + build, commit**

Run: `… -only-testing:ScoutTests/TerminalHandoffTests -only-testing:ScoutTests/ConnectorDetectionTests -only-testing:ScoutTests/OnboardingViewModelTests …` then `xcodebuild … build`.
Expected: PASS; BUILD SUCCEEDED.

```bash
git add Scout/Utilities/TerminalHandoff.swift Scout/Utilities/ClaudeLauncher.swift Scout/Engine/ConnectorDetection.swift Scout/Onboarding/OnboardingViewModel.swift Scout/Onboarding/OnboardingView.swift ScoutTests/Utilities/TerminalHandoffTests.swift ScoutTests/Engine/ConnectorDetectionTests.swift ScoutTests/Onboarding/OnboardingViewModelTests.swift
git commit -m "feat(app): onboarding flow — prerequisites, engine install, identity, connectors, vault, ready"
```

### Task C8: Wire it up — gate, launch-time upgrade, Settings buttons

**Files:**
- Modify: `Scout/Shell/AppState.swift` (engine services: `engineRelease`, `makeInstaller`, `engineUpgrader`, `runEngineUpgradeIfNeeded()`), `Scout/Shell/MainWindowView.swift` (gate → `OnboardingView`), `Scout/Shell/SettingsView.swift` + `Scout/Shell/EngineSettingsSection.swift` (Update / Repair wired), `Scout/Onboarding/EngineUnavailableView.swift` (delete; superseded)
- Create: `Scout/Onboarding/EngineUpgradeSheet.swift`
- Test: `ScoutTests/Shell/AppStateEngineWiringTests.swift`

**Interfaces:**
- Produces on `AppState`:
  ```swift
  let engineRelease: EngineRelease?                       // nil only if the resource is missing (never in a real build)
  @Published var engineUpgradeProgress: [InstallStep: InstallProgress]?   // non-nil while an upgrade sheet is showing
  func makeInstaller(progress: @escaping @Sendable (InstallProgress) -> Void) -> EngineInstaller?   // nil when no tarball or no claude
  func makeOnboardingModel(onFinished: @escaping () -> Void) -> OnboardingViewModel
  func runEngineUpgradeIfNeeded() async                   // spec §4.4 EngineUpgrader; automatic (spec §11 Q1 — recommended)
  static func shouldAutoUpgrade(state: EngineState, release: EngineRelease?) -> Bool
  ```

- [ ] **Step 1: Write the failing test**

`ScoutTests/Shell/AppStateEngineWiringTests.swift`:

```swift
import Testing
import Foundation
@testable import Scout

@Suite("AppState engine wiring")
struct AppStateEngineWiringTests {
    let install = EngineInstall(root: URL(fileURLWithPath: "/e"), scoutctl: URL(fileURLWithPath: "/s"), python: nil, version: "0.10.0", vault: nil)
    func release(_ v: String) -> EngineRelease { .init(schemaVersion: 1, engine: .init(repo: "x", version: v, tag: "v\(v)", commit: ""), uv: .init(version: "0", sha256: [:])) }

    @Test func autoUpgradeOnlyWhenManagedAndBehind() {
        #expect(AppState.shouldAutoUpgrade(state: .managed(install, vaultBootstrapped: true), release: release("0.11.0")))
        #expect(!AppState.shouldAutoUpgrade(state: .managed(install, vaultBootstrapped: true), release: release("0.10.0")))
        #expect(!AppState.shouldAutoUpgrade(state: .managed(install, vaultBootstrapped: false), release: release("0.11.0")))   // finish onboarding first
        #expect(!AppState.shouldAutoUpgrade(state: .external(install, .devCheckout), release: release("0.11.0")))
        #expect(!AppState.shouldAutoUpgrade(state: .managed(install, vaultBootstrapped: true), release: nil))
    }
}
```

- [ ] **Step 2: Implement in `AppState`**

Add properties and methods:

```swift
    let engineRelease: EngineRelease? = try? EngineRelease.load()
    @Published var engineUpgradeProgress: [InstallStep: InstallProgress]? = nil

    static func shouldAutoUpgrade(state: EngineState, release: EngineRelease?) -> Bool {
        guard let release, case .managed(_, vaultBootstrapped: true) = state else { return false }
        return EngineUpgrader(layout: .live, release: release).needsUpgrade(state: state)
    }

    func makeInstaller(progress: @escaping @Sendable (InstallProgress) -> Void) -> EngineInstaller? {
        guard let engineRelease,
              let claude = ClaudeLauncher.resolveClaudePath(override: UserDefaults.standard.string(forKey: "claudeCLIPath") ?? "") else { return nil }
        return EngineInstaller(layout: engineLayout, release: engineRelease, tarballURL: engineRelease.bundledTarballURL(), runner: runner,
                               uv: UvInstaller(release: engineRelease.uv, layout: engineLayout, downloader: URLSessionDownloader(), runner: runner),
                               claude: URL(fileURLWithPath: claude), progress: progress)
    }

    func makeOnboardingModel(onFinished: @escaping () -> Void) -> OnboardingViewModel {
        OnboardingViewModel(engineState: engineHealth.state, layout: engineLayout, release: engineRelease, runner: runner,
                            prerequisites: PrerequisiteChecker(runner: runner, layout: engineLayout, claudePathOverride: UserDefaults.standard.string(forKey: "claudeCLIPath") ?? ""),
                            makeInstaller: { [weak self] sink in self?.makeInstaller(progress: sink) },
                            onFinished: { [weak self] in Task { await self?.engineHealth.refresh() }; onFinished() })
    }

    /// Spec §4.4: a newer bundled engine is applied on launch for app-managed
    /// installs. The sheet shows progress; the health service re-reads after.
    func runEngineUpgradeIfNeeded() async {
        guard Self.shouldAutoUpgrade(state: engineHealth.state, release: engineRelease), let engineRelease else { return }
        engineUpgradeProgress = [:]
        guard let installer = makeInstaller(progress: { [weak self] p in Task { @MainActor in self?.engineUpgradeProgress?[p.step] = p } }) else {
            engineUpgradeProgress = nil; return
        }
        let vault = scoutDirectory
        let ok = await installer.run(steps: EngineUpgrader.upgradeSteps, mode: .upgrade(vault: vault))
        if ok { _ = try? EngineUpgrader(layout: engineLayout, release: engineRelease).garbageCollect(keeping: engineRelease.engine.version) }
        await engineHealth.refresh()
        if ok { engineUpgradeProgress = nil }   // on failure the sheet stays with the log + Retry
    }
```

Call `await runEngineUpgradeIfNeeded()` in the launch `Task` right after `await engineHealth.refresh()`. `makeInstaller` needs `runner` as `any ProcessRunner` — the stored `runner` property already exists (`self.runner = runner`).

- [ ] **Step 3: Views**

`Scout/Onboarding/EngineUpgradeSheet.swift`:

```swift
import SwiftUI

/// Modal progress while the bundled engine replaces the installed one.
struct EngineUpgradeSheet: View {
    let progress: [InstallStep: InstallProgress]
    let targetVersion: String
    let retry: () -> Void
    let dismiss: () -> Void

    private var failed: InstallProgress? { progress.values.first { if case .failed = $0.status { return true }; return false } }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Updating the Scout engine to \(targetVersion)").font(DS.serif(20, weight: .medium)).foregroundStyle(DS.Ink.p1)
            ForEach(EngineUpgrader.upgradeSteps, id: \.rawValue) { s in
                HStack(spacing: 10) {
                    Text({ switch progress[s]?.status { case .done?: return "✓"; case .skipped?: return "–"; case .running?: return "…"; case .failed?: return "✗"; default: return "○" } }()).font(DS.mono(13)).frame(width: 16)
                    Text(s.title).font(DS.sans(13)).foregroundStyle(DS.Ink.p1)
                }
            }
            if let failed {
                Text(failed.log).font(DS.mono(11)).foregroundStyle(DS.Status.warn).lineLimit(6)
                HStack { Button("Retry", action: retry); Button("Later", action: dismiss) }.buttonStyle(.plainHit)
            }
        }
        .padding(28).frame(width: 460)
    }
}
```

`MainWindowView`: replace the Phase-1 gate with

```swift
            Group {
                if appState.engineHealth.state.gatesTabs && selection != .settings {
                    OnboardingView(model: appState.makeOnboardingModel(onFinished: { selection = .controlCenter }))
                } else {
                    detail
                }
            }
            .background(PaperBackdrop())
            .sheet(isPresented: Binding(get: { appState.engineUpgradeProgress != nil }, set: { if !$0 { appState.engineUpgradeProgress = nil } })) {
                EngineUpgradeSheet(progress: appState.engineUpgradeProgress ?? [:], targetVersion: appState.engineRelease?.engine.version ?? "",
                                   retry: { Task { await appState.runEngineUpgradeIfNeeded() } },
                                   dismiss: { appState.engineUpgradeProgress = nil })
            }
```

Hold the onboarding model in `@State private var onboarding: OnboardingViewModel?` created once per gate appearance rather than rebuilt on every render (`.onAppear`/`.onChange(of: gatesTabs)`); the snippet above shows the wiring, the state holder is the implementer's job. Delete `EngineUnavailableView.swift`.

`SettingsView`: pass `bundledVersion: appState.engineRelease?.engine.version`, `onUpdate: { Task { await appState.runEngineUpgradeIfNeeded() } }`, and `onRepair: { repairing = true }` where a `.sheet(isPresented: $repairing)` shows `OnboardingView(model: appState.makeOnboardingModel(onFinished: { repairing = false }))`.

- [ ] **Step 4: Run the full target, build, smoke, commit**

Run: full `xcodebuild test … -only-testing:ScoutTests …` → PASS. Smoke on this machine (dev checkout adopted → no gate, no upgrade). Then simulate a fresh machine without touching the real one: `HOME=$(mktemp -d) open …/Debug/Scout.app` is not enough because `~/.claude` moves too — use the clean-account rehearsal in Task C10 instead.

```bash
git add Scout/Shell/AppState.swift Scout/Shell/MainWindowView.swift Scout/Shell/SettingsView.swift Scout/Shell/EngineSettingsSection.swift Scout/Onboarding/EngineUpgradeSheet.swift ScoutTests/Shell/AppStateEngineWiringTests.swift
git rm Scout/Onboarding/EngineUnavailableView.swift
git commit -m "feat(app): onboarding gates the window; bundled engine upgrades on launch; Settings Update/Repair"
```

### Task C9: `release.sh` guards, CI, README, roadmap

**Files:**
- Modify: `scripts/release.sh` (before the build: pin check; after the build: bundle check; notes line), `README.md` (Install section), `docs/ROADMAP.md:46-51`, `docs/README.md` (index rows)
- Test: `scripts/tests/release-lib.test.sh` if #74's `release-lib.sh` has landed (add the pin-check function there); otherwise inline in `release.sh`.

- [ ] **Step 1: Pin verification before the build** — insert after the `TAG="v$VERSION"` line:

```bash
# ─────────────────────────────────────────────────────────────────────────────
# Engine pin (spec §6): the tag must exist on scout-plugin and resolve to the
# pinned commit; Release builds refuse to ship without the bundled tarball.
# ─────────────────────────────────────────────────────────────────────────────
PIN="$REPO_ROOT/Scout/Resources/engine-release.json"
ENGINE_REPO="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["engine"]["repo"])' "$PIN")"
ENGINE_TAG="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["engine"]["tag"])' "$PIN")"
ENGINE_COMMIT="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["engine"]["commit"])' "$PIN")"
ENGINE_VERSION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["engine"]["version"])' "$PIN")"
REMOTE_COMMIT="$(git ls-remote "https://github.com/$ENGINE_REPO.git" "refs/tags/$ENGINE_TAG^{}" "refs/tags/$ENGINE_TAG" | awk 'NR==1{print $1}')"
if [[ -z "$REMOTE_COMMIT" ]]; then
  echo "✗ engine pin: tag $ENGINE_TAG not found on $ENGINE_REPO" >&2; exit 1
fi
if [[ "$REMOTE_COMMIT" != "$ENGINE_COMMIT" ]]; then
  echo "✗ engine pin: $ENGINE_TAG is $REMOTE_COMMIT on $ENGINE_REPO but engine-release.json pins $ENGINE_COMMIT" >&2; exit 1
fi
export SCOUT_BUNDLE_STRICT=1
echo "→ Engine pin OK: scout-plugin $ENGINE_TAG ($ENGINE_COMMIT)"
```

(`REPO_ROOT` is defined a few lines below `TAG=` today — move its definition above this block.)

- [ ] **Step 2: Bundle verification after the build** — after `APP=…` existence check:

```bash
BUNDLED="$APP/Contents/Resources/scout-engine-$ENGINE_VERSION.tar.gz"
[[ -f "$BUNDLED" ]] || { echo "✗ built app carries no engine tarball at $BUNDLED" >&2; exit 1; }
GOT="$(tar -xzOf "$BUNDLED" .claude-plugin/plugin.json | python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])')"
[[ "$GOT" == "$ENGINE_VERSION" ]] || { echo "✗ bundled engine is $GOT, pin says $ENGINE_VERSION" >&2; exit 1; }
```

- [ ] **Step 3: Release notes** — in the notes heredoc, after the `## What's changed` block, add `echo "**Engine:** scout-plugin \`$ENGINE_TAG\` (\`${ENGINE_COMMIT:0:7}\`), installed and managed by the app."`.

- [ ] **Step 4: README** — replace the *Install (prebuilt DMG)* section body with:

```markdown
1. Download the latest `Scout-*.dmg` from [Releases](https://github.com/Raven-Scout/Scout/releases), drag **Scout.app** to Applications, and open it.
2. If Claude Code is not installed, Scout offers to open Terminal with Anthropic's installer, then asks you to sign in.
3. Scout installs the Scout engine (the `scout-plugin` Claude Code plugin and a private Python environment under `~/.local/share/scout`), registers it with Claude Code, asks for your name, email and connectors, creates your vault (default `~/Scout`), and schedules the sessions.

That is the whole install. Updating the app updates the engine. Already have Scout set up via `install.sh` or a dev checkout? The app adopts it as-is and shows where it lives in Settings ▸ Engine.
```

and delete the sentence "The app expects a Scout instance at `~/Scout/` … run `/scout-setup` first".

- [ ] **Step 5: Roadmap + docs index** — in `docs/ROADMAP.md` Phase 5 row for #51 append "**Spec + plan:** `docs/superpowers/specs/2026-09-08-app-managed-engine-design.md` / `docs/superpowers/plans/2026-09-08-app-managed-engine.md`." In `docs/README.md` add a table row `| App-managed engine (Scout.app installs and owns the engine) | [superpowers/specs/2026-09-08-app-managed-engine-design.md](./superpowers/specs/2026-09-08-app-managed-engine-design.md) | [superpowers/plans/2026-09-08-app-managed-engine.md](./superpowers/plans/2026-09-08-app-managed-engine.md) |`.

- [ ] **Step 6: Dry-run the release script, commit**

Run: `SKIP_NOTARIZE=1 SKIP_RELEASE=1 scripts/release.sh 0.13.0-dryrun 2>&1 | tail -20` — expect "Engine pin OK" and a DMG in `build/release/`. Delete `build/`.

```bash
git add scripts/release.sh README.md docs/ROADMAP.md docs/README.md
git commit -m "build(app): release.sh verifies the engine pin and the bundled tarball; app-first install docs"
```

### Task C10: Clean-account rehearsal (owner: Jordan, or delegated with a fresh macOS user)

No code. This is the acceptance test for Part C, on a **new macOS user account** on this Mac (System Settings ▸ Users & Groups ▸ Add User) so `~` is empty and Claude Code is absent.

- [ ] **Step 1: Fresh account, nothing installed.** Copy the Release DMG built by `SKIP_RELEASE=1 scripts/release.sh` into the account, open Scout.app. Expect: onboarding at *Welcome*; *Prerequisites* shows Claude Code missing, Continue disabled.
- [ ] **Step 2: Install Claude Code from the button.** Expect: Terminal opens showing `curl -fsSL https://claude.ai/install.sh | bash`; after it finishes, the row flips to "Installed (2.x.y)" within 3 s without clicking Re-check; Continue enables while *Signed in* is still ○.
- [ ] **Step 3: Engine install.** Expect: uv downloaded (`~/.local/bin/uv`), `~/.local/share/scout/engine/0.10.0`, `engine/current`, `venv/0.10.0/bin/scoutctl version` prints `0.10.0`; `claude plugin marketplace list` shows `scout-plugin  Source: Directory (…/engine/current)`; `claude plugin list --json` lists `scout@scout-plugin`.
- [ ] **Step 4: Identity + connectors.** Expect: name/email blank (no git config), timezone = system; connectors all "could not detect" or "needs authentication" (nothing authorized yet) — toggle `github` off, leave defaults.
- [ ] **Step 5: Vault.** Expect: `~/Scout/scout-config.yaml`, `~/.local/state/scout/engine.json` with `managed_by: scout-app`, `launchctl list | grep com.scout` shows `schedule-tick` and `heartbeat`, doctor green or yellow (auth warning acceptable).
- [ ] **Step 6: Sign in, first briefing.** `claude auth login` via the button; *Ready* → "Run your first briefing now" → a `.scout-logs/scout-*.log` appears and the Control Center shows the run.
- [ ] **Step 7: Upgrade path.** Bump `engine-release.json` to a `v0.10.1` (or a test tag) and rebuild; launch → the upgrade sheet runs; `engine/current` → new version; `venv/<old>` kept; pointer updated; doctor green.
- [ ] **Step 8: Adoption path (your main account).** Launch the same build in your normal account: no onboarding; Settings ▸ Engine says *Dev checkout (~/scout-plugin)*; nothing under `~/.local/share/scout` was created.

Record findings as issues; anything that blocks Step 1–6 blocks the release.

---

## Self-review against the spec

**Coverage.** §4.1 layout → B1 `EngineLayout`, C5 `unpackEngine`/`repointCurrent`, A6 `SCOUT_VENV_DIR`. §4.2 pointer → A2 (write), A3 (launcher + doctor), B1/B2 (read), A4 (`pointer` in JSON). §4.3 prerequisites → C3, C4, C7 (`installClaudeCode`, `signIn`, `installCommandLineTools`). §4.4 `EngineRelease` → C1; `EngineLocator` → B2; `PrerequisiteChecker` → C3; `EngineInstaller` six steps → C5; `EngineUpgrader` → C6 + C8 launch hook + GC; `EngineHealthService` → B3 (+10-min timer); `ClaudeCodeCLI` → C3; `AppState` changes (locator, vault precedence, `SCOUT_DATA_DIR` injection, editable vault) → B4, B5. §5 onboarding steps 1–7 → C7; Settings ▸ Engine → B5, C8; gate + error-copy routing + sidebar dot → B6, C8. §6 `bundle-engine.sh`, build phase, release guards, CI → C2, C9. §7 E1–E6 → A1–A7. §9 relationships: #74 plugin row consumes `EngineHealthService` and `engineRelease` (C8 exposes both; the row edit itself belongs to whichever PR lands second — noted in B1/B5 dedupe comments). §10 adoption matrix → B2 tests, one per row; migration button is phase 3 (out of this plan, as the spec says). §11 Q1 implemented as "automatic" (C8 `runEngineUpgradeIfNeeded`), Q5 as "keep one previous" (C6 GC) — both flagged for review in the spec.

**Gaps deliberately left:** phase-3 items (legacy migration button, `install.sh` convergence, #74 plugin-row wiring); Linux cron `SCOUT_DATA_DIR` (E6 is plists-only, macOS scope); scout-plugin#229 (separate PR, called out in A8).

**Placeholder scan.** The only `<…>` values are the three pin values in C1 (commit, two sha256s) — each with the exact command that produces it after A8's release. C7's `schedule list --json` decoder carries an explicit "confirm field name" instruction rather than a guess.

**Type consistency.** `EngineLayout`, `EnginePointer`, `EngineInstall`, `ExternalSource`, `EngineState.gatesTabs/isManaged/scoutctl/install`, `EngineLocator.version(atRoot:)`, `DoctorReport.parse(stdout:)`, `EngineHealthService.refresh()/needsAttention/state/doctor`, `EngineRelease.engine.version/bundledTarballURL`, `UvInstaller.ensure(log:)/existing()`, `InstallStep`, `StepStatus`, `InstallProgress`, `BootstrapInput`, `InstallMode`, `EngineInstaller.run(steps:mode:)/bootstrapAutoArguments/venvEnvironment`, `EngineUpgrader.upgradeSteps/needsUpgrade/garbageCollect`, `EngineVersion`, `ClaudeCodeCLI.*`, `Prerequisites.canInstallEngine`, `ConnectorDetection.parse`, `TerminalHandoff.run/makeScript`, `ScriptedRunner.on(tool:prefix:…)/calls(to:)` are used with the same names and signatures in every task that references them. Engine side: `resolve_scoutctl_bin`, `EnginePointer`/`current_pointer`/`write_pointer`/`read_pointer`/`pointer_path`, `BootstrapConfig.managed_by`, `result.pointer`, `AutoAction`/`detect`/`result_dict`/`run`, `DetectStatus`/`Detection`/`detect`/`parse_mcp_list`, `install_plist(vault=)` — consistent across A1–A8 and referenced identically from C5's argv builder and C7's decoders.

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-08-app-managed-engine.md`, pushed to PR #104 with the spec. Per the review-first flow nothing is implemented yet. Two execution options once the spec and plan are approved:

1. **Subagent-driven (recommended)** — one fresh subagent per task with review between tasks (`superpowers:subagent-driven-development`); Part A in the scout-plugin worktree first, then Part B, then Part C after v0.10.0 is tagged.
2. **Inline** — execute in-session with checkpoints (`superpowers:executing-plans`), same ordering.

Tasks A8 (plugin release) and C10 (clean-account rehearsal) need Jordan's hands or explicit delegation.
