"""The owner's answer to "may the changes map read activity outside the vault (session transcripts, app edits)?".

It lives in the owner store (``owner_dir/consent.json``), never in ``scout-config.yaml``: a scheduled run can write
that file, so a run must not be able to grant itself consent. Only an owner session may write the answer."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from scout.changes.store import is_owner_session, owner_dir

SCHEMA = 1
CONSENT_NAME = "consent.json"


def _path(env: Mapping[str, str]) -> Path:
    return owner_dir(env) / CONSENT_NAME


def out_of_vault_answer(env: Mapping[str, str]) -> bool | None:
    """True or False once answered; None when never asked (or the record doesn't parse)."""
    try:
        data = json.loads(_path(env).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != SCHEMA or not isinstance(data.get("out_of_vault"), bool):
        return None
    return data["out_of_vault"]


def set_out_of_vault(answer: bool, env: Mapping[str, str]) -> None:
    if not is_owner_session(env):
        raise PermissionError("the consent answer is the owner's; a scheduled run may not write it")
    path = _path(env)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = {"schema": SCHEMA, "out_of_vault": answer, "answered_at": datetime.now(UTC).isoformat()}
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
