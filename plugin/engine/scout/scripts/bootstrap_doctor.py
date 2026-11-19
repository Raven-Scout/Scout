"""Read-only health check for the bootstrap pipeline.

Used as pipeline stage 8 (post-install/upgrade smoke) and as a standalone
diagnostic via `scoutctl bootstrap doctor`. Never mutates the vault.
"""

from __future__ import annotations

import json
import os
import platform
import plistlib
import re
import shlex
import subprocess
import xml.parsers.expat
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path

import yaml

from scout.config import config_load_problem
from scout.health import CLI_AUTH_FAILURE_ERE, RunHealth  # noqa: F401  (re-export; test_claude_with_retry pins it)
from scout.scripts.brain_merge import BRAIN_KINDS, PROPOSED_DIR, has_conflict_markers, pending_brain_sidecars
from scout.scripts.brain_merge import sidecar_name as sidecar_name_for
from scout.yaml_safe import load_yaml, yaml_problem


class Severity(Enum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


@dataclass(frozen=True)
class WarningGroup:
    """Several findings of one kind, reported as ONE doctor warning (UI audit R1, #505).

    ``summary`` is that warning's text, verbatim as it appears in ``DoctorReport.warnings``;
    ``items`` names each file it stands for, so a client can show the list behind a
    disclosure instead of one warning per file.
    """

    kind: str  # e.g. "privacy.script-raw-kb-read"
    summary: str
    items: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {"kind": self.kind, "summary": self.summary, "count": len(self.items), "items": list(self.items)}


@dataclass(frozen=True)
class DoctorReport:
    severity: Severity
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Worth knowing, not a health problem: never changes the severity.
    notes: list[str] = field(default_factory=list)
    # Run health (is Scout actually running?) is reported beside install health and
    # never changes `severity` or `exit_code` (D2): an expired login must not fail
    # an upgrade's post-upgrade check. None when the vault is missing or the
    # evaluator crashed.
    run_health: RunHealth | None = None
    # Warnings that stand for many files of one kind; each summary is also in `warnings`.
    warning_groups: list[WarningGroup] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        return {Severity.GREEN: 0, Severity.YELLOW: 1, Severity.RED: 2}[self.severity]


# Plugin-owned files an engine before .scout-state/last-rendered/ 3-way merged
# into a blocking sidecar (bootstrap._CAT_MERGE_FILES, which this module can't
# import). Upgrades no longer write one, but one left behind still blocks.
_LEGACY_SIDECAR_FILES = ("knowledge-base/ontology/parser.py",)

_REQUIRED_CAT1_FILES = (
    "scripts/heartbeat.sh",
    "knowledge-base/ontology/parser.py",
    "action-items/render.py",
    "hooks/kb-pre-filter.sh",
)

# macOS TCC-protected directories — launchd-spawned processes cannot read files
# inside these without Full Disk Access granted to the executable.
_MACOS_TCC_PROTECTED_DIRS = ("Documents", "Desktop", "Downloads")


def _check_macos_plist_scoutctl_bin(*, home: Path, vault: Path | None = None) -> tuple[list[str], list[str]]:
    """Inspect the installed schedule-tick plist's scoutctl path.

    Reports as error if the file doesn't exist or isn't executable. Reports
    as error if the resolved path falls under a macOS TCC-protected directory
    (launchd cannot read those without Full Disk Access). Returns empty if
    the plist isn't installed — that case is already covered by the
    launchctl-list check upstream.
    """
    errors: list[str] = []
    warnings: list[str] = []
    plist_path = home / "Library" / "LaunchAgents" / "com.scout.schedule-tick.plist"
    if not plist_path.exists():
        return errors, warnings
    try:
        with plist_path.open("rb") as f:
            data = plistlib.load(f)
    except (plistlib.InvalidFileException, xml.parsers.expat.ExpatError, ValueError, OSError) as e:
        # launchd's parser is laxer than plistlib, so the job may still run. A
        # common cause is a plist from before #221 with a `--` path in its XML
        # comment (#249); reinstalling writes one without it.
        # install-plist takes its vault from SCOUT_DATA_DIR (else ~/Scout), so the
        # fix names the vault being checked; the app runs the doctor with it set.
        env = f"SCOUT_DATA_DIR={shlex.quote(str(vault))} " if vault is not None else ""
        warnings.append(
            f"could not parse {plist_path.name}: {e}. "
            f"Fix: {env}scoutctl schedule install-plist --force (rewrites it from the current template)"
        )
        return errors, warnings
    args = data.get("ProgramArguments") or []
    if not args:
        warnings.append(f"{plist_path.name}: ProgramArguments empty")
        return errors, warnings
    scoutctl_bin = Path(args[0])
    fix_hint = (
        "Fix: scoutctl schedule install-plist --force (re-derives the canonical path from the loaded plugin's venv)"
    )
    if not scoutctl_bin.exists():
        errors.append(f"plist references non-existent scoutctl: {scoutctl_bin}. {fix_hint}")
        return errors, warnings
    if not os.access(scoutctl_bin, os.X_OK):
        errors.append(f"plist references non-executable scoutctl: {scoutctl_bin}. {fix_hint}")
        return errors, warnings
    # TCC dir check: macOS sandbox blocks launchd-spawned processes from reading
    # files in protected dirs. We compare RESOLVED paths so a symlinked plugin
    # that lands in ~/Documents is still caught.
    resolved = scoutctl_bin.resolve()
    home_resolved = home.resolve()
    for protected in _MACOS_TCC_PROTECTED_DIRS:
        protected_path = home_resolved / protected
        try:
            resolved.relative_to(protected_path)
        except ValueError:
            continue
        errors.append(
            f"scoutctl resolves into ~/{protected} ({resolved}). macOS TCC blocks "
            f"launchd from reading this directory without Full Disk Access. Move the "
            f"plugin out of ~/{protected}, then re-install: "
            f"scoutctl schedule install-plist --force"
        )
        break
    return errors, warnings


def _check_linux_cron_scoutctl_bin() -> tuple[list[str], list[str]]:
    """Inspect the Linux scout-managed cron block for the scoutctl path."""
    errors: list[str] = []
    warnings: list[str] = []
    try:
        proc = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=False, timeout=5)
    except (subprocess.SubprocessError, FileNotFoundError):
        return errors, warnings
    if proc.returncode != 0:
        return errors, warnings
    in_block = False
    scoutctl_bin: Path | None = None
    for line in proc.stdout.splitlines():
        stripped = line.strip()
        if stripped == "# >>> scout-managed >>>":
            in_block = True
            continue
        if stripped == "# <<< scout-managed <<<":
            break
        if not in_block:
            continue
        if "scoutctl schedule tick" not in line:
            continue
        # Standard cron line: 5 time fields + command. Token at index 5 is the
        # executable path (assumes no whitespace in the path — same assumption
        # baked into install_cron's template). Defensive sanity check: the
        # token must look like a scoutctl path. If the user hand-edited the
        # managed block to use `@daily` shorthand (1 cron token instead of 5)
        # or otherwise broke the layout, parts[5] would point at the wrong
        # token — better to bail than to falsely accuse a different binary.
        parts = line.split()
        if len(parts) >= 6 and parts[5].endswith("/scoutctl"):
            scoutctl_bin = Path(parts[5])
        break
    if scoutctl_bin is None:
        return errors, warnings
    fix_hint = "Fix: scoutctl schedule install-cron (re-derives the canonical path from the loaded plugin's venv)"
    if not scoutctl_bin.exists():
        errors.append(f"crontab references non-existent scoutctl: {scoutctl_bin}. {fix_hint}")
    elif not os.access(scoutctl_bin, os.X_OK):
        errors.append(f"crontab references non-executable scoutctl: {scoutctl_bin}. {fix_hint}")
    return errors, warnings


def _check_launchd_jobs() -> tuple[list[str], list[str]]:
    """macOS: both scout LaunchAgents are loaded."""
    try:
        proc = subprocess.run(["launchctl", "list"], capture_output=True, text=True, check=False, timeout=5)
    except (subprocess.SubprocessError, OSError):
        # OSError: not found, or refused to start (PermissionError inside a scheduled run's write sandbox, which
        # denies executing launchctl, spec 2026-11-15 §3).
        return [], ["launchctl unavailable — skipped job registration check"]
    if proc.returncode != 0:
        # Not "no jobs": `launchctl list` itself was refused (inside a scheduled run's write sandbox, which denies
        # launchd job creation, spec 2026-11-15) or failed. Saying the jobs are missing would be a false alarm.
        return [], [f"launchctl list failed (exit {proc.returncode}) — skipped job registration check"]
    errors = [
        f"launchd: {label} not registered"
        for label in ("com.scout.schedule-tick", "com.scout.heartbeat")
        if label not in proc.stdout
    ]
    return errors, []


def _check_cron_jobs(*, home: Path, vault: Path) -> tuple[list[str], list[str]]:
    """Linux: the scout-managed crontab block runs the tick and the heartbeat for this vault.

    The block is the one `scoutctl schedule install-cron` writes
    (defaults/cron-managed-block.tmpl). Lines outside its markers don't count.
    A block without a SCOUT_DATA_DIR line (written before #271) runs ~/Scout.
    """
    try:
        proc = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=False, timeout=5)
    except (subprocess.SubprocessError, FileNotFoundError):
        return [], ["crontab unavailable — skipped job registration check"]
    # A non-zero `crontab -l` is "no crontab for <user>": nothing is scheduled.
    block: list[str] = []
    in_block = False
    for line in proc.stdout.splitlines() if proc.returncode == 0 else []:
        stripped = line.strip()
        if stripped == "# >>> scout-managed >>>":
            in_block = True
        elif stripped == "# <<< scout-managed <<<":
            break
        elif in_block:
            block.append(stripped)
    fix = f"Fix: SCOUT_DATA_DIR={shlex.quote(str(vault))} scoutctl schedule install-cron"
    errors: list[str] = []
    jobs = [line for line in block if line and not line.startswith("#")]
    if not any(" schedule tick" in line for line in jobs):
        errors.append(f"cron: scout schedule tick not installed. {fix}")
    if not any("heartbeat.sh" in line for line in jobs):
        errors.append(f"cron: scout heartbeat not installed. {fix}")
    if errors:
        return errors, []
    data_dirs = [line.split("=", 1)[1] for line in block if line.startswith("SCOUT_DATA_DIR=")]
    scheduled = Path(data_dirs[-1]) if data_dirs else home / "Scout"
    if scheduled.resolve() != vault.resolve():
        errors.append(f"cron: the scout jobs run the vault at {scheduled}, not {vault}. {fix}")
    return errors, []


def _check_scheduled_jobs(*, home: Path, vault: Path) -> tuple[list[str], list[str]]:
    """Dispatch to the platform's scheduler: launchd on macOS, cron on Linux, none elsewhere."""
    system = platform.system()
    if system == "Darwin":
        return _check_launchd_jobs()
    if system == "Linux":
        return _check_cron_jobs(home=home, vault=vault)
    return [], []


def _check_scheduler_bin_path(*, home: Path, vault: Path | None = None) -> tuple[list[str], list[str]]:
    """Dispatch to the platform-specific scoutctl-bin-path check."""
    system = platform.system()
    if system == "Darwin":
        return _check_macos_plist_scoutctl_bin(home=home, vault=vault)
    if system == "Linux":
        return _check_linux_cron_scoutctl_bin()
    return [], []


def _check_scoutctl_shim(*, home: Path) -> tuple[list[str], list[str]]:
    """Warn (never error) if the managed scoutctl shim is dangling.

    The SKILL.md-driven session relies on the ~/.local/bin shim to resolve
    bare `scoutctl` (#99). The realistic post-install failure is a shim left
    pointing at a plugin venv that a later update removed. We flag only that
    dangling case — deterministically, from the shim's own contents — rather
    than guessing reachability from the ambient PATH (which differs between
    the doctor process and the session, and would make the result
    environment-dependent). A missing shim isn't flagged: install and upgrade
    always (re)write it, so absence resolves itself on the next run.
    """
    from scout.scripts.install_scoutctl_shim import SHIM_MARKER

    warnings: list[str] = []
    shim = home / ".local" / "bin" / "scoutctl"
    if not shim.is_file():
        return [], []
    try:
        body = shim.read_text(encoding="utf-8")
    except OSError:
        return [], []
    if SHIM_MARKER in body:
        m = re.search(r'exec "([^"]+)"', body)
        if m and not Path(m.group(1)).exists():
            warnings.append(
                f"scoutctl shim at {shim} points at a missing target ({m.group(1)}) — "
                f"re-run `scoutctl bootstrap upgrade`."
            )
    return [], warnings


def _same_path(a: Path, b: Path) -> bool:
    try:
        return a.expanduser().resolve() == b.expanduser().resolve()
    except (OSError, RuntimeError):
        return a == b


def _shim_target(home: Path) -> Path | None:
    """What our ``~/.local/bin/scoutctl`` shim execs, or None.

    Only the shim bootstrap writes (it carries SHIM_MARKER) counts: a symlink or
    a foreign file there is the user's, bootstrap never rewrites it, so a
    warning about it could not be cleared by re-running bootstrap.
    """
    from scout.scripts.install_scoutctl_shim import SHIM_MARKER

    shim = home / ".local" / "bin" / "scoutctl"
    if shim.is_symlink() or not shim.is_file():
        return None
    try:
        body = shim.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    m = re.search(r'exec "([^"]+)"', body) if SHIM_MARKER in body else None
    return Path(m.group(1)) if m else None


def _check_engine_pointer(*, home: Path, vault: Path | None = None) -> tuple[list[str], list[str]]:
    """Warn (never error) when the engine pointer disagrees with reality.

    The pointer (~/.local/state/scout/engine.json) is how Scout.app and the
    docs find the engine, whose venv may live outside the plugin tree (#244).
    Bootstrap rewrites it together with the schedule-tick plist and the
    ``~/.local/bin/scoutctl`` shim (a ``--no-jobs`` run writes none of them),
    so any disagreement means they came from different engines or vaults —
    exactly the drift the pointer exists to make visible (#271). A missing
    pointer is not flagged (pre-pointer engines; the next bootstrap writes it).
    """
    from scout.scripts.engine_pointer import read_pointer

    pointer = read_pointer(home=home)
    if pointer is None:
        return [], []
    warnings: list[str] = []
    fix = "re-run `scoutctl bootstrap upgrade` so both track one engine"
    if not Path(pointer.scoutctl).exists():
        warnings.append(
            f"engine pointer names a missing scoutctl ({pointer.scoutctl}) — re-run `scoutctl bootstrap upgrade`."
        )
    shim_target = _shim_target(home)
    if shim_target is not None and not _same_path(shim_target, Path(pointer.scoutctl)):
        warnings.append(
            f"~/.local/bin/scoutctl runs {shim_target} but the engine pointer names {pointer.scoutctl} — {fix}."
        )
    pointer_vault = Path(pointer.vault) if pointer.vault else None
    if pointer_vault is not None and vault is not None and not _same_path(pointer_vault, vault):
        warnings.append(
            f"engine pointer is for {pointer_vault}, not the vault checked here ({vault}) — Scout.app follows "
            f"the pointer; run `scoutctl bootstrap upgrade` against the vault you use."
        )
    plist_path = home / "Library" / "LaunchAgents" / "com.scout.schedule-tick.plist"
    if plist_path.exists():
        try:
            with plist_path.open("rb") as f:
                data = plistlib.load(f)
        except (plistlib.InvalidFileException, xml.parsers.expat.ExpatError, ValueError, OSError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        args = data.get("ProgramArguments") or []
        if args and args[0] != pointer.scoutctl:
            warnings.append(
                f"engine pointer ({pointer.scoutctl}) and {plist_path.name} ({args[0]}) name different "
                f"scoutctl binaries — {fix}."
            )
        env = data.get("EnvironmentVariables")
        data_dir = env.get("SCOUT_DATA_DIR") if isinstance(env, dict) else None
        if pointer_vault is not None and isinstance(data_dir, str) and not _same_path(Path(data_dir), pointer_vault):
            warnings.append(
                f"{plist_path.name} runs the vault {data_dir} (SCOUT_DATA_DIR) but the engine pointer is for "
                f"{pointer_vault} — {fix}."
            )
    return [], warnings


_CLAUDE_BIN_LINE = re.compile(r'^CLAUDE_BIN="([^"]*)"\s*$', re.MULTILINE)


def _check_runner_claude_bin(*, vault: Path) -> tuple[list[str], list[str]]:
    """The rendered run-scout.sh must point CLAUDE_BIN at an executable claude.

    A dead path (e.g. the old /usr/local/bin/claude default on a machine using the
    native ~/.local/bin installer) makes every scheduled run fail before Claude
    starts, and nothing else surfaces it.
    """
    runner = vault / "run-scout.sh"
    if not runner.exists():
        return [], []  # missing runner is reported by the cat-1 file check
    match = _CLAUDE_BIN_LINE.search(runner.read_text(encoding="utf-8", errors="replace"))
    if match is None:
        return [], []
    claude_bin = match.group(1)
    if not claude_bin or not os.access(claude_bin, os.X_OK):
        return [
            f"run-scout.sh: CLAUDE_BIN={claude_bin or '(empty)'} is not an executable claude CLI — "
            "every scheduled run will fail. Fix: in scout-config.yaml set connectors.inputs.claude_bin "
            'to the output of `command -v claude` (or to "" to auto-detect), then run /scout-update.'
        ], []
    return [], []


def _scheduled_runtimes(*, vault: Path) -> tuple[dict[str, list[str]], list[str], list[str]]:
    """The agent runtimes the schedule uses, each with its slot keys, plus warnings and notes (spec 2026-11-13 §8.9).

    A slot runs on its own ``agent_runtime:``, else ``agent_runtimes.default``, else ``claude-code``
    (``scout.agent_runtimes.resolve``). ``SCOUT_AGENT_RUNTIME`` in this shell is left out: scheduled runs
    don't inherit it. Claude Code comes first. When the schedule or the config can't be resolved, the answer
    is Claude Code alone, so the doctor checks what it checked before the loop. A ConfigError (an unknown
    ``agent_runtime:`` or ``agent_runtimes.default``, or a schedule the engine rejects) is a warning: the
    same load refuses the run. The schedule's YAML check only parses the file, so it doesn't catch an unknown
    runtime. Any other failure is a note.
    """
    from scout.agent_runtimes import registry
    from scout.agent_runtimes.resolve import default_runtime, slot_runtimes
    from scout.errors import ConfigError

    try:
        fallback = default_runtime(vault).runtime
        used: dict[str, list[str]] = {}
        for key, runtime in sorted(slot_runtimes(vault).items()):
            used.setdefault(runtime or fallback, []).append(key)
    except ConfigError as e:
        return (
            {registry.BUILTIN_DEFAULT: []},
            [
                f"scheduled runs can't resolve their agent runtime ({e}); a run that loads this refuses to start. "
                "Fix the schedule or scout-config.yaml, then re-check with `scoutctl agent-runtime show`. "
                "Until then the doctor checked Claude Code only"
            ],
            [],
        )
    except Exception as e:  # noqa: BLE001 - a doctor check never breaks the doctor
        return (
            {registry.BUILTIN_DEFAULT: []},
            [],
            [f"could not read which agent runtimes the schedule uses ({e}); checked Claude Code only"],
        )
    if not used:
        return {fallback: []}, [], []
    order = sorted(used, key=lambda rid: (rid != registry.BUILTIN_DEFAULT, rid))
    return {rid: used[rid] for rid in order}, [], []


def _slot_list(slots: list[str]) -> str:
    return ", ".join(repr(s) for s in slots) if slots else "(none)"


def _check_runtime_bin(*, runtime: str, slots: list[str]) -> tuple[list[str], list[str]]:
    """A non-Claude runtime's CLI must be installed where scheduled runs look: (errors, warnings).

    Claude Code's check is ``_check_runner_claude_bin``, which reads the runner's ``CLAUDE_BIN``.
    Without the CLI, ``scoutctl agent-runtime check`` refuses each run (``version_unverified``).
    """
    from scout.agent_runtimes import registry

    adapter = registry.get(runtime)
    name = adapter.display_name
    fix = f"`scoutctl agent-runtime check --slot {slots[0]}`" if slots else "`scoutctl agent-runtime show`"
    try:
        found = adapter.locate(None)
        version = adapter.version(found) if found is not None else None
    except Exception as e:  # noqa: BLE001 - a doctor check never breaks the doctor
        return [], [f"could not look for the {name} CLI ({runtime}): {e}"]
    if found is None:
        return [
            f"{name} ({runtime}) isn't installed, or isn't on the PATH scheduled runs see — the runs of slot(s) "
            f"{_slot_list(slots)} on it are skipped. Fix: install it, then re-check with {fix}."
        ], []
    if version is None:
        return [], [
            f"{name} ({runtime}) at {found}: can't read its version, so the runs of slot(s) {_slot_list(slots)} "
            f"on it are skipped. Re-check with {fix}."
        ]
    return [], []


def _check_runtime_auth(*, runtime: str, slots: list[str]) -> tuple[list[str], list[str]]:
    """How a non-Claude runtime signs in for scheduled runs: (warnings, notes).

    Claude Code's is ``_check_claude_auth``.
    """
    from scout.agent_runtimes import registry

    adapter = registry.get(runtime)
    name = adapter.display_name
    binary = adapter.locate(None)
    if binary is None:
        return [], []  # _check_runtime_bin reported it
    auth = adapter.auth_status(binary)
    if auth.signed_in is False:
        return [
            f"{name} isn't signed in for scheduled runs ({auth.mode}: {auth.detail}); the runs of slot(s) "
            f"{_slot_list(slots)} on it fail until it is. Sign in to {name}, then start one of them to check."
        ], []
    if auth.signed_in is None:
        return [], [f"{name} signs in by {auth.mode} ({auth.detail}); only a run can tell whether that still works"]
    return [], [f"{name} signs in by {auth.mode} ({auth.detail})"]


def _check_run_sandbox(*, vault: Path, home: Path | None = None) -> tuple[list[str], list[str]]:
    """Whether the next scheduled run would run in the write sandbox (spec 2026-11-15 §8): (warnings, notes).

    Builds and self-tests the profile (one short sandbox-exec), never starts claude. Inside a sandboxed run
    the self-test can't nest, so the runner's own status is reported instead. The switch is judged as the
    scheduler sees it: ``SCOUT_RUN_SANDBOX`` in this shell is left out (scheduled runs don't inherit it), and
    whether the last run had it off, and from where, comes from that run's run-outcome row.
    """
    import os

    from scout import health
    from scout.sandbox import launch

    if os.environ.get("SCOUT_SANDBOX_STATUS") == "enforced":
        return [], ["run sandbox: enforced (this session runs inside it)"]
    warnings: list[str] = []
    notes: list[str] = []
    shell_switch = os.environ.get(launch.ENV_SWITCH, "").strip()
    env = {k: v for k, v in os.environ.items() if k != launch.ENV_SWITCH}
    if home is not None:
        env["HOME"] = str(home)
    status = "unknown"
    if shell_switch.lower() in launch.OFF_VALUES:
        # This shell (a test, the eval harness) runs without the sandbox; building and self-testing the profile here
        # says nothing about the scheduler's runs, so only the owner's switch is reported.
        on, source = launch.switch(env)
        status = "on" if on else "off"
        notes.append(
            f"run sandbox: not checked here ({launch.ENV_SWITCH}={shell_switch} in this shell, which scheduled runs "
            f"don't inherit); the owner's switch says {status} ({source})"
        )
    else:
        try:
            result = launch.prepare(vault, env)
        except Exception as e:  # noqa: BLE001 - a bug here must not break the doctor
            return [f"run sandbox: couldn't check ({e}); see `scoutctl sandbox status`"], []
        status = result.status
        line = f"run sandbox: {result.status} ({result.reason})"
        if result.status == "failed":
            warnings.append(f"{line}. Scheduled runs are skipped until `scoutctl sandbox status` says enforced.")
        elif result.status == "conflict":
            warnings.append(f"{line}. Scheduled runs go ahead under Claude Code's sandbox only.")
        elif result.status == "off":
            notes.append(f"{line}; turn it back on with `scoutctl sandbox on`")
        else:
            notes.append(line)
        if shell_switch:
            notes.append(f"this shell has {launch.ENV_SWITCH}={shell_switch}, which scheduled runs don't inherit")
    try:
        rows = health.read_outcomes(vault)
    except Exception:  # noqa: BLE001 - a bug here must not break the doctor
        rows = []
    last = next((r for r in reversed(rows) if r.outcome not in health.SKIP_OUTCOMES and r.sandbox), None)
    if last is not None and last.sandbox == "off" and last.sandbox_source == "env":
        warnings.append(
            f"the last scheduled run ran without the write sandbox: {launch.ENV_SWITCH}=off was in the environment "
            "the scheduler started it with. Remove it from the LaunchAgent, crontab or shell that starts the tick "
            "(it is meant for tests and the eval harness)."
        )
    elif last is not None and last.sandbox == "off" and last.sandbox_source == "switch" and status != "off":
        notes.append("the last scheduled run ran with the write sandbox turned off (`scoutctl sandbox off`)")
    notes.extend(_run_broker_notes(vault))
    return warnings, notes


def _run_broker_notes(vault: Path) -> list[str]:
    """One line on the last run's commit and Telegram broker (Phase 1d), and the queue it left, if any."""
    try:
        from scout.sandbox import broker

        status = broker.read_status(vault)
        waiting, failed = broker.pending(vault), len(broker.failed_entries(vault))
    except Exception:  # noqa: BLE001 - a bug here must not break the doctor
        return []
    out: list[str] = []
    if status is not None:
        out.append(f"run brokers: {status['status']} on the last run ({status.get('reason') or 'no reason given'})")
    if waiting or failed:
        out.append(
            f"run broker queue: {waiting} waiting for the next drain, {failed} failed "
            "(.scout-state/broker-queue/failed/; `scoutctl broker drain` retries the waiting ones)"
        )
    return out


def _check_gate_probe(*, vault: Path) -> tuple[list[str], list[str]]:
    """The last gate probe the runners ran (spec 2026-11-13 §3.5): (warnings, notes). Reads the cache only.

    A failed probe means scheduled runs are being skipped because claude doesn't load
    Scout's privacy hooks. No cache (no run since the probe shipped) says nothing.
    """
    from scout.agent_runtimes.gate_probe import read_cache

    cached = read_cache(vault)
    if cached is None:
        return [], []
    version = cached.claude_version or "an unknown version"
    if not cached.ok:
        return [
            f"gate probe failed ({cached.checked_at}, claude {version}): {cached.reason}. Scheduled runs are "
            "skipped until it passes. Run `scoutctl gates probe` to re-check; update Scout, or pin Claude Code "
            "to the last version that passed."
        ], []
    return [], [f"gate probe passed for claude {version} at {cached.checked_at}: Scout's privacy hooks load."]


RUN_TIMEOUT_WINDOW_DAYS = 30
RUN_TIMEOUT_MIN_RUNS = 5


def _check_run_timeouts(*, vault: Path, now: datetime | None = None) -> tuple[list[str], list[str]]:
    """Slots whose runs usually take longer than their run timeout, in awake time (spec 2026-11-13 §8.6): a warning.

    Reads the run-outcome rows' ``awake_s`` (runs from Phase 1 PR D on) over the last 30 days, per slot, with at
    least 5 runs. A slot whose runtime has a timeout and whose 90th-percentile awake length is above it gets a
    warning. While Claude Code's timeout is off (Jordan, 2026-11-16: it comes on at 45 minutes, 90 for research,
    once runs fit), a note says how its slots compare with those values. Silent with no awake lengths yet.
    """
    import json as _json
    import math

    from scout.agent_runtimes import limits as _limits
    from scout.agent_runtimes import registry
    from scout.agent_runtimes.resolve import default_runtime
    from scout.schedule import load_default_schedule, load_schedule

    ledger = vault / ".scout-logs" / "run-outcomes.jsonl"
    if not ledger.exists():
        return [], []
    since = (now or datetime.now(UTC)).timestamp() - RUN_TIMEOUT_WINDOW_DAYS * 86400
    lengths: dict[str, list[int]] = {}
    timeouts: dict[str, int] = {}
    unmeasured = 0  # rows from runners that record awake_s, without one (no python3 where the runner ran)
    try:
        for line in ledger.read_text(encoding="utf-8").splitlines():
            try:
                row = _json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict) or row.get("outcome", "ok") not in ("ok", "failed"):
                continue
            awake_s, finished = row.get("awake_s"), row.get("finished_at")
            if not isinstance(finished, int | float) or finished < since:
                continue
            if row.get("failure_class") == "timeout":
                timeouts[str(row.get("slot"))] = timeouts.get(str(row.get("slot")), 0) + 1
            if not isinstance(awake_s, int):
                if "agent_runtime" in row:
                    unmeasured += 1
                continue
            lengths.setdefault(str(row.get("slot")), []).append(awake_s)
        schedule_path = vault / ".scout-state" / "schedule.yaml"
        schedule = load_schedule(schedule_path) if schedule_path.exists() else load_default_schedule()
        fallback = default_runtime(vault).runtime
    except Exception as e:  # noqa: BLE001 - a doctor check never breaks the doctor
        return [], [f"could not compare run lengths with run timeouts: {e}"]

    warnings: list[str] = []
    readiness: list[str] = []
    # A run cut off at its timeout ran at least that long, so the 90th percentile under-counts such slots:
    # any timed-out run is worth a warning of its own (review of #653).
    for key, n in sorted(timeouts.items()):
        warnings.append(
            f"slot {key!r}: {n} run(s) in {RUN_TIMEOUT_WINDOW_DAYS} days were stopped at their run timeout "
            "(failure_class timeout). Raise it if the work needs longer: scoutctl agent-runtime set-limit "
            "<runtime> --run-timeout-minutes <minutes>"
        )
    for key, slot in sorted(schedule.items()):
        runs = lengths.get(key, [])
        if len(runs) < RUN_TIMEOUT_MIN_RUNS:
            continue
        ordered = sorted(runs)
        p90 = ordered[max(1, math.ceil(len(ordered) * 0.9)) - 1]
        runtime = slot.agent_runtime or fallback
        try:
            adapter = registry.get(runtime)
        except KeyError:
            continue
        lim = _limits.limits_for(vault, adapter, key, slot.type.value)
        if lim.run_timeout_s is not None:
            if p90 > lim.run_timeout_s:
                warnings.append(
                    f"slot {key!r}: its runs usually take {p90 // 60} min awake (90th percentile of {len(runs)} runs "
                    f"in {RUN_TIMEOUT_WINDOW_DAYS} days), above its {lim.run_timeout_s // 60}-min run timeout on "
                    f"{runtime}, so some runs are stopped. Raise it: scoutctl agent-runtime set-limit {runtime} "
                    "--run-timeout-minutes <minutes>"
                )
        else:
            planned = _limits.default_timeout_minutes(slot.type.value)
            verdict = "fits" if p90 <= planned * 60 else "would be stopped"
            readiness.append(f"{key} {p90 // 60} min p90 vs {planned} ({verdict})")
    notes = []
    if unmeasured and not lengths:
        notes.append(
            f"{unmeasured} recent run(s) recorded no awake length, so run lengths can't be compared with run "
            "timeouts: the runners measure it with python3, which wasn't found where they ran"
        )
    if readiness:
        notes.append(
            "run timeout off; awake run lengths vs the planned timeout (45 min, 90 for research): "
            + "; ".join(readiness)
        )
    return warnings, notes


def _check_run_limits(*, vault: Path, now: datetime | None = None) -> list[str]:
    """Run-limit inputs a run could have tampered with (spec 2026-11-13 §8.6), as warnings. Needs no run history.

    - scout-config.yaml ``budget.per_runtime`` values the limits ignore: ones that would loosen a limit, turn one
      on, go under a floor, or aren't whole numbers. Each scheduled slot is checked with its own kind, so a value
      looser than a briefing's 45 minutes but under research's 90 is named for the slots it is ignored on.
    - usage-tracker rows dated in the future, which the daily token ceiling skips (review of #654).
    """
    from scout.agent_runtimes import limits as _limits
    from scout.agent_runtimes import registry
    from scout.agent_runtimes.resolve import default_runtime
    from scout.schedule import load_default_schedule, load_schedule

    warnings: list[str] = []
    try:
        schedule_path = vault / ".scout-state" / "schedule.yaml"
        schedule = load_schedule(schedule_path) if schedule_path.exists() else load_default_schedule()
        fallback = default_runtime(vault).runtime
        # runtime -> ignored value -> the slots it was ignored on
        ignored: dict[str, dict[str, list[str]]] = {}
        for key, slot in sorted(schedule.items()):
            runtime = slot.agent_runtime or fallback
            try:
                adapter = registry.get(runtime)
            except KeyError:
                continue
            for value in _limits.limits_for(vault, adapter, key, slot.type.value).ignored:
                ignored.setdefault(runtime, {}).setdefault(value, []).append(key)
        for runtime, values in sorted(ignored.items()):
            parts = [f"{value} (slots: {', '.join(slots)})" for value, slots in values.items()]
            warnings.append(
                f"scout-config.yaml budget.per_runtime.{runtime}: ignored {'; '.join(parts)}. Only the owner can "
                f"loosen a run limit or turn one on: scoutctl agent-runtime set-limit {runtime} …"
            )
    except Exception as e:  # noqa: BLE001 - a doctor check never breaks the doctor
        warnings.append(f"could not check scout-config.yaml run limits: {e}")
    if n := _limits.future_rows(vault, now=now):
        warnings.append(
            f".scout-logs/usage-tracker.jsonl has {n} row(s) dated in the future. The daily token ceiling skips "
            "them; a run, or a wrong clock, wrote them. Remove them if nothing explains them."
        )
    return warnings


def _check_claude_auth(*, home: Path, vault: Path, now: datetime | None = None) -> tuple[list[str], list[str]]:
    """How scheduled runs sign in to Claude (#267), as (warnings, notes).

    The mode is a note. A token readable by others, one about to lapse (it lasts a
    year and the CLI can't say when it ends), or one this vault's runner predates
    and so never exports, is a warning.
    """
    from scout import claude_auth
    from scout.scripts.engine_pointer import read_pointer

    st = claude_auth.status(claude_auth.default_store(home=home), now=now)
    ptr = read_pointer(home=home)
    # The setup token is offered to non-app engines only (run-health spec Q6).
    notes = [st.describe(offer_setup=ptr is None or ptr.managed_by != "scout-app")]
    last = claude_auth.last_sign_in(vault)
    if last is not None:
        notes.append(last.describe())
    warnings = list(st.problems)
    if st.mode != "token":
        return warnings, notes
    expiry = st.expiry_text()
    if st.expiring and expiry:
        warnings.append(
            f"the long-lived Claude token scheduled runs use {expiry}; renew it: {claude_auth.SETUP_COMMAND}"
        )
    wrapper = vault / "scripts" / "claude-with-retry.sh"
    try:
        exports = "claude-oauth-token" in wrapper.read_text(encoding="utf-8", errors="replace")
    except OSError:
        exports = True  # a missing runner is the cat-1 check's to report
    if not exports:
        warnings.append(
            "a long-lived Claude token is stored, but this vault's scripts/claude-with-retry.sh predates it "
            "and never passes it to Claude; run /scout-update (or scoutctl bootstrap upgrade)"
        )
    return warnings, notes


def _has_markers(path: Path) -> bool:
    """True if ``path`` holds a conflict-marker line. Missing or unreadable
    files don't (other checks report those)."""
    try:
        return has_conflict_markers(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return False


# One grouped warning per privacy-drift rule (#505): what the files do, and the fix, once.
_PRIVACY_GROUP_TEXT = {
    "runner-no-reader": (
        "vault runners don't export SCOUT_READER=autonomous or SCOUT_MODE, so their sessions' reads "
        "aren't marked as unattended",
        'add `export SCOUT_MODE="<run-name>"` and `export SCOUT_READER=autonomous` before each launches claude',
    ),
    "script-raw-kb-read": (
        "vault scripts read the knowledge base directly, not through `scoutctl kb`, so what they produce "
        "carries no privacy labels",
        "make them read through `scoutctl kb files` (and `scoutctl kb read`), and send an item from a private file "
        "only to an owner-only channel, never Slack (#453)",
    ),
}


def _privacy_drift_warnings(vault: Path) -> tuple[list[str], list[WarningGroup]]:
    """Privacy drift as warnings: a rule one file breaks keeps its own message; a rule several
    files break is one warning with a count and the first two names (#505), plus a group listing
    them all."""
    from scout.scripts import vault_drift

    by_rule: dict[str, list[vault_drift.PrivacyDrift]] = {}
    for finding in vault_drift.privacy_drift(vault):
        by_rule.setdefault(finding.rule, []).append(finding)
    warnings: list[str] = []
    groups: list[WarningGroup] = []
    for rule, findings in by_rule.items():
        if len(findings) == 1 or rule not in _PRIVACY_GROUP_TEXT:
            warnings.extend(f"privacy: {f.message}" for f in findings)
            continue
        what, fix = _PRIVACY_GROUP_TEXT[rule]
        paths = [f.path for f in findings]
        named = ", ".join(paths[:2]) + (f" and {len(paths) - 2} more" if len(paths) > 2 else "")
        summary = f"privacy: {len(paths)} {what}: {named}. To fix, {fix}; `scoutctl bootstrap drift` lists them all."
        warnings.append(summary)
        groups.append(WarningGroup(kind=f"privacy.{rule}", summary=summary, items=tuple(paths)))
    return warnings, groups


def _check_vault_drift(*, vault: Path) -> tuple[list[str], list[str], list[WarningGroup]]:
    """Vault edits to plugin-owned files, as (warnings, notes, groups), plus privacy drift.

    A conflict is a warning: the vault runs an older version of a plugin-owned
    file until it is resolved. An edit upgrades keep, and a copy the first
    baseline parked, are notes: deliberate state, so they must not hold the
    doctor yellow forever.
    """
    from scout.scripts import vault_drift

    warnings: list[str] = []
    notes: list[str] = []
    edited: list[str] = []
    parked: list[str] = []
    for entry in vault_drift.scan(vault):
        if entry.status == "conflict":
            warnings.append(
                f"vault edit to {entry.path} conflicts with a plugin update — your version is still "
                f"running and the update is parked at {entry.parked[0]}. Merge by hand, then "
                f"`scoutctl bootstrap drift --resolve {entry.path}` (or copy the parked file over yours "
                f"to take the plugin's version)"
            )
        elif entry.status == "edited":
            edited.append(entry.path)
        elif entry.status == "replaced":
            parked += entry.parked
    if edited:
        # A vault-developed file (parser.py) is grown in the vault on purpose:
        # its edits are vault content, not a fix to send upstream.
        upstreamable = [p for p in edited if p not in vault_drift.VAULT_DEVELOPED_FILES]
        hint = " — `scoutctl bootstrap drift --patch` turns them into a plugin PR" if upstreamable else ""
        notes.append(
            f"{len(edited)} plugin-owned file(s) carry vault edits that upgrades keep: {', '.join(edited)}{hint}"
        )
    if parked:
        notes.append(
            f"an upgrade replaced {len(parked)} vault file(s) and parked the previous cop(ies): "
            f"{', '.join(parked)} — compare with `scoutctl bootstrap drift --diff`, dismiss "
            f"with `scoutctl bootstrap drift --resolve <file>`"
        )
    # Vault-local runners and scripts that read around the privacy labels (#453), grouped by rule (#505).
    privacy_warnings, groups = _privacy_drift_warnings(vault)
    warnings.extend(privacy_warnings)
    return warnings, notes, groups


def _check_custom_connectors(*, vault: Path) -> list[str]:
    """One warning per problem in connectors.custom.yaml, or in a custom connector's input
    stored in scout-config.yaml (#344); that connector is skipped until fixed. And one
    when the tool gate blocks every MCP tool because of the file (#564)."""
    from scout.custom_connectors import CUSTOM_FILE, load, stored_input_issues, tool_gate_problem

    custom = load(vault)
    warnings = [f"{CUSTOM_FILE}: {i.path}: {i.message} (skipped until fixed)" for i in custom.issues]
    if problem := tool_gate_problem(vault):
        warnings.append(
            f"{problem}, so Scout can't tell whether a connector there restricts its tools "
            "and may block every MCP tool in this vault's sessions until it is fixed"
        )
    try:
        cfg = load_yaml((vault / "scout-config.yaml").read_text(encoding="utf-8"))
        inputs = cfg["connectors"]["inputs"]
    except (OSError, UnicodeDecodeError, yaml.YAMLError, KeyError, TypeError):
        inputs = None  # a missing or malformed scout-config.yaml is not this check's to report
    if isinstance(inputs, dict):
        issues = stored_input_issues(custom.connectors, inputs)
        warnings += [f"scout-config.yaml: {i.path}: {i.message} (skipped until fixed)" for i in issues]
    return warnings


def _check_heartbeat_config(*, vault: Path) -> list[str]:
    """One warning per bad value or unknown key in the heartbeat's config (#231).

    The heartbeat keeps the default for each one and logs it every tick; the
    doctor surfaces it so it is not only in heartbeat.log.
    """
    from scout.scripts.heartbeat import load_config_with_issues

    return load_config_with_issues(vault / "scout-config.yaml")[1]


PRIVACY_WINDOW = timedelta(days=7)


UNCHECKED_EVENT = "privacy.check_unavailable"


def privacy_audit_counts(vault: Path, *, now: datetime) -> tuple[int, int]:
    """``(blocked or denied, unchecked commits)`` in ``.scout-logs/kb-reads.jsonl`` over the last 7 days.

    #453, spec §7.4 and §8.3. A row is *blocked or denied* when it is
    ``blocked`` or denied at least one path, and an *unchecked commit* when its
    ``event`` is ``privacy.check_unavailable`` (the owner committed while the
    privacy check couldn't run). Unreadable or malformed rows are skipped; a
    timestamp without a zone is taken as UTC.
    """
    log = vault / ".scout-logs" / "kb-reads.jsonl"
    cutoff = now - PRIVACY_WINDOW
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=UTC)
    blocked = unchecked = 0
    try:
        with log.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:  # streamed: the log only grows
                # Cheap pre-filter on the sorted-key rows: nothing to count → skip unparsed.
                if '"blocked": true' not in line and '"paths_denied": 0,' in line and UNCHECKED_EVENT not in line:
                    continue
                try:
                    row = json.loads(line)
                    ts = datetime.fromisoformat(row["ts"])
                    hit = row.get("blocked") is True or int(row.get("paths_denied") or 0) > 0
                    gap = row.get("event") == UNCHECKED_EVENT
                except (ValueError, KeyError, TypeError, AttributeError):
                    continue
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=UTC)
                if ts < cutoff:
                    continue
                blocked += hit
                unchecked += gap
    except OSError:
        return 0, 0
    return blocked, unchecked


def privacy_blocked_count(vault: Path, *, now: datetime) -> int:
    """Every row :func:`privacy_audit_counts` counts, of both kinds."""
    return sum(privacy_audit_counts(vault, now=now))


def _check_kb_lint_config(*, vault: Path) -> list[str]:
    """A lint config mistake falls back to report, which enforces nothing and fails
    nothing: this is where the owner hears about it, since runs commit unattended."""
    from scout.kb.lint import config_problems
    from scout.kb.lint_config import load_lint_config

    try:
        return [f"kb-lint: {problem}" for problem in config_problems(vault, load_lint_config(vault))]
    except Exception as e:  # noqa: BLE001 - the doctor reports it; the hook fails open on the same error
        return [f"kb-lint: the kb_lint config can't be read ({type(e).__name__}), so no size or line check blocks"]


def _check_privacy_audit(*, vault: Path, now: datetime | None) -> list[str]:
    blocked, unchecked = privacy_audit_counts(vault, now=now or datetime.now(UTC))
    out: list[str] = []
    if blocked:
        out.append(
            f"privacy: {blocked} blocked outbound check(s) or denied read(s) in the last 7 days "
            "(.scout-logs/kb-reads.jsonl) — a run reached for a shared surface with private content; "
            "the content was held back, but check what the runs were trying to send"
        )
    if unchecked:
        out.append(
            f"privacy: {unchecked} commit(s) in the last 7 days were made while the privacy check could not run "
            "(.scout-logs/kb-reads.jsonl) — they were NOT checked for private links in shared files; fix what "
            "this doctor reports about scoutctl, then review drafts/ and other shared files"
        )
    return out


def _check_repo_sync_host_cache(*, vault: Path, now: datetime | None) -> list[str]:
    """A linked vault whose cached Agnes Local-Only list is missing or stale (#596).

    The commit hook checks a run's moves against that cache, offline; without a
    current one it can only warn. Each linked run refreshes it when it takes the
    lease, so a stale one means runs aren't reaching Agnes. ``repo sync`` still
    refuses such a move against the list it fetches.
    """
    from scout.repo_sync import RepoSyncError, host_cache_problem, load_host_cache, load_state

    try:
        state = load_state(vault)
    except RepoSyncError:
        return []  # repo sync refuses this state, so it uploads nothing
    if state is None:
        return []
    problem = host_cache_problem(load_host_cache(vault, agnes_url=state.agnes_url), now=now)
    if problem is None:
        return []
    return [
        f"repo sync: {problem}, so the commit hook can't block a scheduled run's move out of a path only Agnes "
        "keeps local-only (it warns instead). Scheduled runs refresh it when they take the Agnes lease, so check "
        "their logs for a lease or Agnes error; scoutctl repo sync refreshes it too"
    ]


def _age(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 60:
        return f"{max(minutes, 0)} min"
    if minutes < 48 * 60:
        return f"{minutes // 60} h"
    return f"{minutes // (24 * 60)} days"


def _check_delegate_mirrors(*, vault: Path, now: datetime | None) -> tuple[list[str], list[str]]:
    """Each delegate mirror's age, as (warnings, notes) (#550 V2c). Nothing for a vault that links none.

    Warns on a failed last sync, a push that didn't finish, and a mirror behind
    HEAD that no sync has tried for an hour (the schedule tick tries every 15 min).
    """
    if not (vault / ".scout-state" / "delegate-mirrors.json").exists():
        return [], []
    from scout.delegate_mirror import load_links
    from scout.repo_sync import RepoSyncError, git

    try:
        links = load_links(vault)
    except RepoSyncError as e:
        return [f"delegate mirrors: {e.message}"], []
    now = now or datetime.now(UTC)
    head = git(vault, "rev-parse", "--verify", "-q", "HEAD", check=False).stdout.strip() or None

    def when(stamp: str | None) -> datetime | None:
        try:
            return datetime.fromisoformat(stamp.replace("Z", "+00:00")) if stamp else None
        except ValueError:
            return None

    warnings: list[str] = []
    notes: list[str] = []
    for key, link in sorted(links.items()):
        synced = when(link.last_sync)
        notes.append(
            f"delegate mirror {key}: last synced {_age(now - synced)} ago"
            if synced
            else f"delegate mirror {key}: never synced"
        )
        if link.last_error is not None:
            warnings.append(
                f"delegate mirror {key}: the last sync failed ({link.last_error.get('code', 'unknown')}); "
                f"see why with: scoutctl delegate sync {key}"
            )
        elif link.pending_mirror is not None:
            warnings.append(f"delegate mirror {key}: a push didn't finish; the next sync checks the remote")
        else:
            tried = when(link.last_attempt) or synced
            if head is not None and link.last_vault != head and (tried is None or now - tried > timedelta(hours=1)):
                warnings.append(
                    f"delegate mirror {key}: behind HEAD, and no sync has tried for over an hour; is the schedule "
                    "tick running? (scoutctl delegate sync --all syncs now)"
                )
    return warnings, notes


def run_doctor(
    *, vault: Path, check_jobs: bool = True, home: Path | None = None, now: datetime | None = None
) -> DoctorReport:
    """Run all doctor checks against ``vault``. Pure read.

    ``now`` pins the run-health clock (tests and the contract fixture); default is the real time.
    """
    errors: list[str] = []
    warnings: list[str] = []
    notes: list[str] = []
    home = home or Path.home()

    if not vault.is_dir():
        if vault.exists():
            errors.append(f"vault path is not a directory: {vault}")
        else:
            errors.append(f"vault directory missing: {vault}")
        return DoctorReport(severity=Severity.RED, errors=errors)

    # schedule.yaml must exist and parse.
    schedule_path = vault / ".scout-state" / "schedule.yaml"
    if not schedule_path.exists():
        errors.append(f"missing schedule.yaml at {schedule_path}")
    else:
        schedule_text = schedule_path.read_text(encoding="utf-8")
        try:
            load_yaml(schedule_text)
        except yaml.YAMLError as e:
            errors.append(f"schedule.yaml invalid: {yaml_problem(e, schedule_text)}")

    # scout-config.yaml must record version stamps.
    config_path = vault / "scout-config.yaml"
    if not config_path.exists():
        errors.append(f"missing scout-config.yaml at {config_path}")
    elif problem := config_load_problem(vault):
        errors.append(f"scout-config.yaml invalid: {problem}")
    else:
        cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        plugin = cfg.get("plugin") or {}
        if not plugin.get("version_at_last_setup"):
            errors.append("scout-config.yaml: plugin.version_at_last_setup missing")
        if not plugin.get("version_at_last_update"):
            errors.append("scout-config.yaml: plugin.version_at_last_update missing")

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

    # Pending brain-file sidecars (yellow): upgrades skip that file, so it is
    # not receiving plugin changes until the sidecar is resolved.
    pending = pending_brain_sidecars(vault)
    for sidecar_name in pending:
        live_name = sidecar_name.removesuffix(".proposed-merge")
        if _has_markers(vault / sidecar_name):
            # A merge with conflicts: it already holds the vault's version.
            how = f"(e.g. `mv {sidecar_name} {live_name}` and remove any conflict markers)"
        else:
            # The plugin's whole version, proposed because live wasn't the
            # plugin's: moving it into place drops the vault's brain.
            how = (
                f"(review `diff {live_name} {sidecar_name}` and copy in what you want; "
                f"`mv` would replace the vault's {live_name} with the plugin's version)"
            )
        warnings.append(
            f"{sidecar_name} pending — upgrades leave {live_name} as is until it is resolved: "
            f"make {live_name} the version you want {how}, then `scoutctl bootstrap resolve {live_name}`"
        )
    # A live brain file with conflict markers: an unfinished merge is running,
    # and upgrades hold the file until the markers are gone.
    for kind in BRAIN_KINDS:
        live_name = f"{kind}.md"
        if _has_markers(vault / live_name):
            warnings.append(
                f"{live_name} has conflict markers (<<<<<<< / >>>>>>>) from an unfinished merge — "
                f"upgrades leave {live_name} as is until they are removed"
            )
    # A sidecar removed without `bootstrap resolve`: the proposal behind it is
    # still on record, so the next upgrade merges or proposes from the old base.
    for kind in BRAIN_KINDS:
        live_name = f"{kind}.md"
        proposed = snapshot_dir / PROPOSED_DIR / live_name
        live = vault / live_name
        if sidecar_name_for(kind) in pending or not proposed.exists() or not live.exists():
            continue
        if proposed.read_bytes() != live.read_bytes():
            warnings.append(
                f"{sidecar_name_for(kind)} was removed without `scoutctl bootstrap resolve {live_name}` — "
                f"if {live_name} is your resolution, run it; otherwise the next upgrade proposes the change again"
            )
    # A parser.py sidecar an older engine left (yellow) still blocks the next upgrade.
    for rel in _LEGACY_SIDECAR_FILES:
        sidecar = vault / f"{rel}.proposed-merge"
        if sidecar.exists():
            warnings.append(
                f"unresolved merge conflict in {rel}.proposed-merge — resolve and "
                f"`mv {rel}.proposed-merge {rel}` before re-running /scout-update"
            )

    # Hand-edit backups (yellow but informational). Upgrades no longer write
    # them — vault edits are kept or parked under .scout-state/drift/ — but a
    # vault can still hold ones an older engine left.
    for bak in vault.glob("run-*.sh.bak.*"):
        warnings.append(f"runner backup present: {bak.name} (hand-edit detected on prior update)")

    warnings.extend(_check_custom_connectors(vault=vault))
    warnings.extend(_check_heartbeat_config(vault=vault))
    warnings.extend(_check_privacy_audit(vault=vault, now=now))
    warnings.extend(_check_kb_lint_config(vault=vault))
    warnings.extend(_check_repo_sync_host_cache(vault=vault, now=now))
    delegate_warnings, delegate_notes = _check_delegate_mirrors(vault=vault, now=now)
    warnings.extend(delegate_warnings)
    notes.extend(delegate_notes)
    timeout_warnings, timeout_notes = _check_run_timeouts(vault=vault, now=now)
    warnings.extend(timeout_warnings)
    notes.extend(timeout_notes)
    warnings.extend(_check_run_limits(vault=vault, now=now))

    groups: list[WarningGroup] = []
    try:
        drift_warnings, drift_notes, groups = _check_vault_drift(vault=vault)
    except OSError as e:
        drift_warnings, drift_notes = [f"could not check vault drift: {e}"], []
    warnings.extend(drift_warnings)
    notes.extend(drift_notes)

    # Live scheduled jobs: launchd on macOS, the scout-managed crontab block on
    # Linux (#449; launchctl on Linux only ever warned, so every Linux install was yellow).
    if check_jobs:
        job_errors, job_warnings = _check_scheduled_jobs(home=home, vault=vault)
        errors.extend(job_errors)
        warnings.extend(job_warnings)

    # Scheduler scoutctl path — verify the path baked into the installed plist
    # (macOS) or cron block (Linux) actually points to an executable scoutctl
    # and isn't trapped in a TCC-protected directory.
    if check_jobs:
        bin_errors, bin_warnings = _check_scheduler_bin_path(home=home, vault=vault)
        errors.extend(bin_errors)
        warnings.extend(bin_warnings)
        # Interactive/session scoutctl reachability (separate from the plist).
        _, shim_warnings = _check_scoutctl_shim(home=home)
        warnings.extend(shim_warnings)
        # Engine pointer vs. what's actually installed (plist, on-disk scoutctl).
        _, pointer_warnings = _check_engine_pointer(home=home, vault=vault)
        warnings.extend(pointer_warnings)
        # The agent CLIs the runners launch, one per runtime the schedule uses (spec 2026-11-13 §8.9).
        # Gated with the other host-runtime checks: they inspect this machine's binaries, not the vault's content.
        runtimes, runtime_warnings, runtime_notes = _scheduled_runtimes(vault=vault)
        warnings.extend(runtime_warnings)
        notes.extend(runtime_notes)
        for runtime_id, slots in runtimes.items():
            if runtime_id == "claude-code":
                bin_errors, bin_warnings = _check_runner_claude_bin(vault=vault)
            else:
                bin_errors, bin_warnings = _check_runtime_bin(runtime=runtime_id, slots=slots)
            errors.extend(bin_errors)
            warnings.extend(bin_warnings)
        # Whether that claude loads Scout's privacy hooks, from the runners' last gate probe.
        probe_warnings, probe_notes = _check_gate_probe(vault=vault)
        warnings.extend(probe_warnings)
        notes.extend(probe_notes)
        # Whether those runs are kept from writing what decides which gates run (OS write sandbox, Phase 1c).
        sandbox_warnings, sandbox_notes = _check_run_sandbox(vault=vault, home=home)
        warnings.extend(sandbox_warnings)
        notes.extend(sandbox_notes)
        # How those runs sign in, per runtime. Claude Code: the interactive login or a stored long-lived token.
        for runtime_id, slots in runtimes.items():
            if runtime_id == "claude-code":
                try:
                    auth_warnings, auth_notes = _check_claude_auth(home=home, vault=vault, now=now)
                except Exception as e:  # noqa: BLE001 - a bug here must not break the doctor
                    auth_warnings, auth_notes = [], [f"could not check how scheduled runs sign in to Claude: {e}"]
            else:
                try:
                    auth_warnings, auth_notes = _check_runtime_auth(runtime=runtime_id, slots=slots)
                except Exception as e:  # noqa: BLE001 - a bug here must not break the doctor
                    auth_warnings, auth_notes = [], [f"could not check how scheduled runs sign in to {runtime_id}: {e}"]
            warnings.extend(auth_warnings)
            notes.extend(auth_notes)

    # Run health sits beside the install verdict, never inside it (D2).
    from scout import health

    try:
        run_health: RunHealth | None = health.evaluate(vault, now=now, home=home)
    except Exception as e:  # noqa: BLE001 - a health-evaluator bug must not break the doctor
        run_health = None
        notes.append(f"run health could not be evaluated: {e}")

    if errors:
        return DoctorReport(
            severity=Severity.RED,
            errors=errors,
            warnings=warnings,
            notes=notes,
            run_health=run_health,
            warning_groups=groups,
        )
    if warnings:
        return DoctorReport(
            severity=Severity.YELLOW, warnings=warnings, notes=notes, run_health=run_health, warning_groups=groups
        )
    return DoctorReport(severity=Severity.GREEN, notes=notes, run_health=run_health)
