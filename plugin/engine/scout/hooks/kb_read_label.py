"""PostToolUse hook — a Read of a KB note comes back with the note's privacy label.

Runs may Read KB notes (Edit needs a Read on any Claude Code version; Jordan,
2026-10-29, #536). What ``scoutctl kb read`` adds over a raw Read is the label,
so this hook adds it: after a Read of a file under ``knowledge-base/`` or
``action-items/context/``, it attaches ``additionalContext`` naming the file and
its label, computed exactly as ``scoutctl kb`` labels it
(:func:`scout.kb.privacy.label_file` under the vault's policy). For a private
note it says the content must not reach any shared surface.

  - **Who**: every session in a Scout vault, runs and the owner alike. It only
    labels; it never blocks or changes a call. In a run (a reader other than
    ``owner``) the read is also logged in ``.scout-logs/kb-reads.jsonl`` like
    any non-owner ``scoutctl kb`` read: counts and a hash, never the path.
  - **Fail closed**: a label that can't be computed is reported as private.
    hooks.json does the same when ``scoutctl`` can't start.
  - **Cheap**: hooks.json exits in the shell unless the payload names a KB path
    and the session is a run or sits in a vault.

``ontology/schema.yaml`` and ``parser.py`` are tooling, not notes: no label.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import IO, Any

from scout import paths
from scout.kb.raw_read import KB_DIRS, ONTOLOGY_TOOLING

GATE = "kb-label"
MARK = f"[scout:{GATE}]"
_SHARED = (
    "Its content (text, title, slug, name, aliases, or a link to it) must not reach any shared surface: no Slack "
    "message (a DM to the owner included), connector write, connector or web query, drafts/ file or back-port. "
    "It may go to vault files and the owner's private Telegram chat, with its title copied exactly."
)


def private_text(rel: str, why: str) -> str:
    return f"{MARK} {rel} is [private] ({why}). {_SHARED}"


def unlabelled_text(rel: str) -> str:
    return (
        f"{MARK} Scout could not label {rel}, so treat it as [private]. {_SHARED} "
        "`scripts/scoutctl kb read <path>` shows its label."
    )


def normal_text(rel: str) -> str:
    return f"{MARK} {rel} is normal (not private). Anything sent to a shared surface still goes through check-outbound."


def session_vault(payload: Mapping[str, Any]) -> Path | None:
    """SCOUT_DATA_DIR, else the vault containing the session's cwd (the payload's, else ours), else None."""
    env = os.environ.get("SCOUT_DATA_DIR")
    if env:
        vault = paths.resolve_path(env)
        return vault if (vault / paths.VAULT_MARKER).is_file() else None
    cwd = payload.get("cwd")
    return paths.vault_containing(cwd if isinstance(cwd, str) and cwd else os.getcwd())


def _kb_rel(vault: Path, raw: str, cwd: Path) -> str | None:
    """The file's vault path when it is a KB note (not tooling), else None. Raises when it can't tell."""
    from scout.kb.privacy import normalize_rel

    p = Path(raw).expanduser()
    target = p if p.is_absolute() else cwd / p
    try:
        rel = normalize_rel(vault, target)
    except ValueError:
        return None  # outside the vault
    if not any(rel == d or rel.startswith(d + "/") for d in KB_DIRS) or ONTOLOGY_TOOLING.search(rel):
        return None
    return rel


def label_context(payload: Mapping[str, Any], *, reader: str) -> str | None:
    """The additionalContext for one PostToolUse payload, or None when it isn't a Read of a KB note."""
    if payload.get("tool_name") != "Read":
        return None
    tool_input = payload.get("tool_input")
    raw = tool_input.get("file_path") if isinstance(tool_input, Mapping) else None
    if not isinstance(raw, str) or not raw:
        return None
    vault = session_vault(payload)
    if vault is None:
        return None
    cwd = payload.get("cwd")
    rel = _kb_rel(vault, raw, paths.resolve_path(cwd) if isinstance(cwd, str) and cwd else vault)
    if rel is None:
        return None
    try:
        from scout.kb.privacy import label_file, load_policy

        label = label_file(vault, rel, load_policy(vault))
        private = label.visibility == "private"
        text = private_text(rel, str(label.visibility_source)) if private else normal_text(rel)
    except Exception:  # noqa: BLE001 — fail closed: an unknown label is private
        private, text = True, unlabelled_text(rel)
    _log(vault, reader=reader, rel=rel, private=private)
    return text


def _log(vault: Path, *, reader: str, rel: str, private: bool) -> None:
    from scout.kb.read_log import log_event

    try:
        log_event(
            vault,
            reader=reader,
            command=f"hook {GATE}",
            surface=None,
            args=[rel],
            returned=1,
            denied=0,
            private_paths=int(private),
            blocked=False,
        )
    except Exception:  # noqa: BLE001, S110 - the label stands without its log row
        pass


def run(*, stdin: IO[str] | None = None, env: Mapping[str, str] | None = None) -> dict[str, Any] | None:
    """Read one PostToolUse payload; return the hook output, or None for nothing to add."""
    from scout.kb.reader import resolve_reader

    env = os.environ if env is None else env
    src = stdin if stdin is not None else sys.stdin
    try:
        payload = json.load(src)
    except Exception:  # noqa: BLE001
        return None  # nothing to label: the Read already happened
    if not isinstance(payload, dict):
        return None
    try:
        text = label_context(payload, reader=resolve_reader(None, env))
    except Exception:  # noqa: BLE001 — fail closed: a KB-looking read that can't be labelled is private
        text = unlabelled_text("the file you just read")
    if text is None:
        return None
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}}


def main() -> int:
    """CLI entry point: scoutctl hook kb-read-label."""
    out = run()
    if out is not None:
        print(json.dumps(out))
    return 0
