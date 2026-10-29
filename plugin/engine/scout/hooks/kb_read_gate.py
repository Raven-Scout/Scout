"""PreToolUse hook — scheduled runs read the knowledge base only through ``scoutctl kb``.

``scoutctl kb`` is the one place that applies a note's privacy label (#453): it
labels private notes, denies what a reader may not see and logs non-owner
reads. A shell ``cat``/``grep``/``ls``/``find`` into ``knowledge-base/`` skips
all of that, and the phase rule saying so lost in 30 of 34 privacy eval runs
(hard-rule audit 2026-10, row 2). This gate makes it mechanical:

  - **Bash**: a command :func:`raw_kb_read` flags is denied. ``scoutctl``
    segments (so ``scripts/scoutctl kb …``), the engine's scripts, ``git
    add``/``commit`` and quoted commit messages are never flagged.
  - **Grep / Glob**: a search inside ``knowledge-base/`` or
    ``action-items/context/``, or over the whole vault (no path, or the vault
    root, with no file filter or one that reaches the KB), is denied.
  - **Read** stays allowed (Edit needs it, on any Claude Code version): the
    ``kb-read-label`` PostToolUse hook (:mod:`scout.hooks.kb_read_label`)
    tells the session the note's privacy label instead. Only a reader that
    ``scoutctl kb`` shows nothing (an ``agent:*`` reader, #453 v1) is denied a
    Read of a KB note. ``knowledge-base/ontology/schema.yaml`` and
    ``parser.py`` are tooling, not notes, and are never gated.

Scope: only scheduled runs, by #517's rule (:func:`scout.kb.lint.is_scheduled`:
``SCOUT_MODE`` set, or a reader other than exactly ``owner``), and only in a
Scout vault (``SCOUT_DATA_DIR``, else the vault containing the session's cwd).
The owner's interactive sessions and sessions in other repos are never gated.
hooks.json checks the run and the KB words in the payload in the shell first,
so Python only starts for a run's call that may touch the KB (and every
Grep/Glob, which can search the vault with no path at all), and for a Read only
when the reader is neither ``owner`` nor ``autonomous``.

In a run this fails closed: a payload that can't be read, or a gate that
errors, denies the call. Outside a run it never denies.

The classifier (:mod:`scout.kb.raw_read`) is shared with the eval harness's ``privacy_hygiene`` check, so
what the gate denies and what the eval counts are one definition.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import IO, Any

from scout import paths
from scout.kb.raw_read import KB_DIRS, ONTOLOGY_TOOLING, filter_reaches_kb, raw_kb_read

GATE = "kb-read-gate"
MARK = f"[scout:{GATE}]"
_REPLACEMENT = (
    "Read the KB through `scripts/scoutctl kb` instead: `kb search <words>`, `kb files [dir]`, "
    "`kb read <path>`, `kb resolve <name>`, `kb name-lookup <name>`, `kb delta` (in place of git log/diff). "
    "`.scout-cache/session-context.json` already lists personal tasks and KB file dates."
)


# --- the gate -----------------------------------------------------------------------


def session_vault(payload: Mapping[str, Any]) -> Path | None:
    """SCOUT_DATA_DIR, else the vault containing the session's cwd (the payload's, else ours), else None."""
    env = os.environ.get("SCOUT_DATA_DIR")
    if env:
        return paths.resolve_path(env)
    cwd = payload.get("cwd")
    return paths.vault_containing(cwd if isinstance(cwd, str) and cwd else os.getcwd())


def _cwd(payload: Mapping[str, Any], vault: Path) -> Path:
    cwd = payload.get("cwd")
    return paths.resolve_path(cwd) if isinstance(cwd, str) and cwd else vault


def _rel(path: Path, vault: Path) -> str | None:
    """``path`` relative to the vault (posix), or None when it is outside."""
    try:
        return path.relative_to(vault).as_posix()
    except ValueError:
        return None


def _in_kb(path: Path, vault: Path) -> bool:
    rel = _rel(path, vault)
    return rel is not None and any(rel == d or rel.startswith(d + "/") for d in KB_DIRS)


def _covers_kb(path: Path, vault: Path) -> bool:
    """A search rooted here reaches the KB: it is the vault, a folder above it, or a KB parent."""
    rel = _rel(path, vault)
    if rel is None:
        return vault.is_relative_to(path)
    return rel == "." or any(d.startswith(rel + "/") for d in KB_DIRS)


def _target(raw: str, cwd: Path) -> Path:
    p = Path(raw).expanduser()
    return paths.resolve_path(p if p.is_absolute() else cwd / p)


def _deny_bash() -> str:
    return (
        f"{MARK} Scheduled runs don't read knowledge-base/ or action-items/context/ with shell commands "
        f"(cat, grep, ls, find, head, git show …): that skips the privacy labels. {_REPLACEMENT} "
        "To change a note, Read it and Edit it."
    )


def _deny_search(tool: str) -> str:
    return (
        f"{MARK} Scheduled runs don't {tool} inside knowledge-base/ or action-items/context/, or across the whole "
        f"vault: that skips the privacy labels. {_REPLACEMENT} To search other folders, give {tool} that folder "
        "as its path (or a file filter outside the KB)."
    )


def _deny_read() -> str:
    return f"{MARK} This session's reader may not read knowledge-base/ or action-items/context/ files. {_REPLACEMENT}"


def decide(payload: Mapping[str, Any], *, reader: str) -> str | None:
    """The deny reason for one run's tool call, or None to allow it. May raise: the caller fails closed."""
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if not isinstance(tool, str) or not isinstance(tool_input, dict):
        raise ValueError("not a tool-call payload")
    if tool not in ("Bash", "Read", "Grep", "Glob"):
        return None
    vault = session_vault(payload)
    if vault is None:
        return None  # not a Scout vault: nothing here is the KB
    cwd = _cwd(payload, vault)
    if tool == "Bash":
        command = tool_input.get("command")
        if not isinstance(command, str):
            raise ValueError("Bash call without a command")
        return _deny_bash() if raw_kb_read(command, in_kb=_in_kb(cwd, vault)) else None
    if tool in ("Grep", "Glob"):
        raw = tool_input.get("path")
        root = _target(raw, cwd) if isinstance(raw, str) and raw else cwd
        if _in_kb(root, vault) or (_covers_kb(root, vault) and filter_reaches_kb(tool, tool_input)):
            return _deny_search(tool)
        return None
    raw = tool_input.get("file_path")
    if not isinstance(raw, str) or not raw:
        raise ValueError("Read call without a file_path")
    target = _target(raw, cwd)
    rel = _rel(target, vault)
    if not _in_kb(target, vault) or (rel is not None and ONTOLOGY_TOOLING.search(rel)):
        return None
    from scout.kb.privacy import FULL_READERS

    return None if reader in FULL_READERS else _deny_read()


def _deny(reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def run(*, stdin: IO[str] | None = None, env: Mapping[str, str] | None = None) -> dict[str, Any] | None:
    """Read one PreToolUse payload; return the deny decision, or None to allow."""
    from scout.kb.lint import is_scheduled
    from scout.kb.reader import resolve_reader

    env = os.environ if env is None else env
    if not is_scheduled(env):
        return None  # the owner's own session: never gated
    src = stdin if stdin is not None else sys.stdin
    try:
        payload = json.load(src)
        if not isinstance(payload, dict):
            raise ValueError("payload is not an object")
        reason = decide(payload, reader=resolve_reader(None, env))
    except Exception as exc:  # noqa: BLE001 — a run fails closed
        reason = (
            f"{MARK} Scout's KB read gate could not check this call ({type(exc).__name__}), so it is blocked in "
            f"this scheduled run. {_REPLACEMENT}"
        )
    return None if reason is None else _deny(reason)


def main() -> int:
    """CLI entry point: scoutctl hook kb-read-gate."""
    decision = run()
    if decision is not None:
        print(json.dumps(decision))
    return 0
