"""The ``antigravity`` adapter: Google's Antigravity CLI (``agy``), Tier L at best (Phase 2).

Spec ``docs/superpowers/specs/2026-11-13-runtime-agnostic-design.md`` §8, with the Phase 0
findings (``2026-11-16-runtime-agnostic-phase0-findings.md`` §1). This is PR 2A's skeleton:
where agy lives, its version, and what it adds to the run sandbox. It isn't registered
(``registry._FACTORIES``) until 2B gives it a launch line, so no run can choose it yet.

agy keeps its sessions, transcripts and log in ``~/.gemini/antigravity-cli`` and reads its
hooks from ``~/.gemini/config`` (findings §1.2, §1.6). ``~/.gemini`` is protected whole by
``hook_paths.AGENT_DIRS``, so the only thing agy adds to the sandbox is its data folder as a
carve-out. The profile accepts that carve-out only because ``profile.AGENT_DATA_DIRS`` lists it
for the ``antigravity`` id, and denies every hook and config name under it again (#806).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path

from scout.agent_runtimes.base import (
    Capability,
    Failure,
    Launch,
    ProcessMarkers,
    RunRequest,
    RuntimeAuth,
    SandboxPaths,
)

# Relative to $HOME. DATA_DIR_REL must stay exactly profile.AGENT_DATA_DIRS["antigravity"]; a test pins it.
DATA_DIR_REL = ".gemini/antigravity-cli"
CONFIG_DIR_REL = ".gemini/config"
# Where agy's installer puts it, and where its self-updater replaces it (findings §0.6).
DEFAULT_BINARY_REL = ".local/bin/agy"
VERSION_TIMEOUT_S = 15.0
_VERSION_RE = re.compile(r"\d+\.\d+\.\d+")

# What agy does whatever its hooks do (findings §1.1, §1.9, §0.1). No budget_cap: agy has no spend flag, so its
# runs get the timeout and the daily token ceiling by default (spec §8.6). No post_tool_context: Tier L at best
# (findings §1.3). The hook capabilities are the probe's to prove (2E), never declared.
DECLARED_CAPABILITIES: frozenset[Capability] = frozenset(
    {
        Capability.HEADLESS_EVENTS,
        Capability.USAGE_TOKENS,
        Capability.CUSTOM_ENDPOINT,
        Capability.SKILLS,
        Capability.CONTEXT_FILE,
    }
)

_NOT_YET = "the antigravity adapter can't launch a run until Phase 2B"


def _home(env: Mapping[str, str]) -> Path:
    return Path(env.get("HOME") or os.path.expanduser("~"))


class Antigravity:
    id = "antigravity"
    display_name = "Antigravity CLI"
    provider = "google"
    declared_capabilities = DECLARED_CAPABILITIES
    interactive_host = False  # Scout's skills don't run inside an agy session yet (2I)

    # -- where agy keeps things -------------------------------------------------

    def data_dir(self, env: Mapping[str, str]) -> Path:
        """Sessions, transcripts and the log (findings §1.6): the one folder a run lets agy write in ~/.gemini."""
        return _home(env) / DATA_DIR_REL

    def config_dir(self, env: Mapping[str, str]) -> Path:
        """Where agy reads its user hooks (``hooks.json``, findings §1.2). Never writable by a run."""
        return _home(env) / CONFIG_DIR_REL

    def sandbox_paths(self, env: Mapping[str, str]) -> SandboxPaths:
        """agy's data folder as a carve-out. ~/.gemini is already protected whole (``hook_paths.AGENT_DIRS``)."""
        return SandboxPaths(protected=(), writable=(self.data_dir(env),))

    # -- the binary ---------------------------------------------------------------

    def locate(self, hint: Path | None) -> Path | None:
        """``hint``, else ``agy`` on PATH, else ``~/.local/bin/agy``; ``None`` when none is executable."""
        candidates: list[Path] = []
        if hint is not None:
            candidates.append(Path(hint))
        if found := shutil.which("agy"):
            candidates.append(Path(found))
        candidates.append(_home(os.environ) / DEFAULT_BINARY_REL)
        for path in candidates:
            if path.is_file() and os.access(path, os.X_OK):
                return path
        return None

    def version(self, binary: Path) -> str | None:
        """The first ``X.Y.Z`` in ``agy --version``; ``None`` on any failure."""
        try:
            proc = subprocess.run(
                [str(binary), "--version"],
                capture_output=True,
                text=True,
                timeout=VERSION_TIMEOUT_S,
                check=False,
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if proc.returncode != 0:
            return None
        m = _VERSION_RE.search(proc.stdout)
        return m.group(0) if m else None

    def auth_status(self, binary: Path) -> RuntimeAuth:
        """Unknown without starting agy: its OAuth token is in the Keychain (findings §1.7). 2B reads ``/usage``."""
        return RuntimeAuth(signed_in=None, mode="login", detail="Google sign-in (not checked before Phase 2B)")

    def capabilities(self, vault: Path, binary: Path, version: str | None) -> frozenset[Capability]:
        """The declared set until the free probe (2E) can prove the hook capabilities for a version."""
        return DECLARED_CAPABILITIES

    # -- models (2B fixes these from `agy models`) --------------------------------

    def validate_model(self, value: object, *, where: str) -> str:
        raise NotImplementedError(_NOT_YET)

    def default_model(self) -> str:
        raise NotImplementedError(_NOT_YET)

    def known_models(self) -> tuple[str, ...]:
        return ()

    # -- a run (2B) ---------------------------------------------------------------

    def headless_argv(self, req: RunRequest) -> Launch:
        raise NotImplementedError(_NOT_YET)

    def classify_failure(self, log_tail: str, exit_code: int) -> Failure:
        raise NotImplementedError(_NOT_YET)

    def process_markers(self, req: RunRequest | None = None) -> ProcessMarkers:
        raise NotImplementedError(_NOT_YET)


__all__ = [
    "CONFIG_DIR_REL",
    "DATA_DIR_REL",
    "DECLARED_CAPABILITIES",
    "DEFAULT_BINARY_REL",
    "Antigravity",
]
