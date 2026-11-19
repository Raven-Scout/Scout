"""``scoutctl delegate status --json``: the delegate mirrors, for the app's Settings ▸ Agent copies card (#550).

Read-only: no lock, no network, no write, and no key, token or secret is read.
The document is versioned (:data:`SCHEMA_VERSION`); the canonical example is
``tests/fixtures/contract/delegate-status.json``, which the macOS app keeps a
byte copy of.

**Everything read is shape-checked.** ``.scout-state/delegate-mirrors.json``
lives in the vault, where a run, a replayed commit or a hand edit can write. Its
signature (:func:`scout.delegate_mirror._verified`) decides whether it is
trusted; when it doesn't match, the links are still listed so the owner can see
them, each flagged ``state_unverified``, and nothing in them is used to decide
anything. Either way each field is checked for its type and form before it is
shown, and a field that fails shows as ``null`` (an entry whose id fails is
dropped and counted in ``skipped``). What a sync was refused for is shown as its
code and a fixed sentence for that code, never the refusal's message, which can
name vault paths.

**What it never holds:** note content or names, vault paths, the key file's
path, fingerprints, commit ids, or anything from the config but the delegate
ids it lists. The mirror's remote is shown only to the owner at the keyboard
(:func:`scout.scripts.privacy_migrate.headless_reason`); a scheduled run gets
``null``, and a delegate reader can't run ``delegate`` commands at all.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
GENERATOR = "scoutctl delegate status"

_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_CODE = re.compile(r"^[a-z][a-z_]{0,47}$")
_SHA = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")
_REMOTE_MAX = 512
_COUNT_MAX = 10**9

# What each refusal a sync records means, in fixed words (the refusal's own message can name vault paths).
REASONS: dict[str, str] = {
    "busy": "Another delegate link, sync or unlink was running.",
    "config_names_hidden": "scout-config.yaml names a note this agent can't see, so it can't go into the mirror.",
    "config_not_mirrored": "scout-config.yaml wouldn't reach the mirror, so the agent would see nothing.",
    "delegate_mirror_unsafe": "The check before the push found something the agent mustn't see; nothing was pushed.",
    "delegate_not_listed": "The delegate isn't listed under privacy.delegates in scout-config.yaml.",
    "git_failed": "A git command failed.",
    "internal": "Scout hit an unexpected error.",
    "mirror_diverged": "The mirror repository moved since the last sync; nothing was pushed.",
    "mirror_unsettled": "Redaction didn't settle for the mirror.",
    "no_commits": "The vault has no commits yet.",
    "policy_missing": "The mirror's privacy policy file was missing.",
    "privacy_overlay_unreadable": "The privacy settings in scout-config.yaml can't be read.",
    "remote_fork": "The mirror repository is a fork.",
    "remote_in_vault": "The mirror's path is inside the vault.",
    "remote_org_owned": "The mirror repository belongs to an organisation, not your own GitHub user.",
    "remote_public": "The mirror repository isn't private.",
    "remote_rewritten": "git rewrites the mirror's address (url.*.insteadOf).",
    "remote_shared": "The mirror repository has a collaborator besides you.",
    "remote_taken": "The mirror repository holds another delegate's or another vault's mirror.",
    "remote_timeout": "The mirror repository didn't answer in time.",
    "remote_unbound": "The mirror repository doesn't hold this delegate's mirror.",
    "remote_unsupported": "The mirror's address isn't a private GitHub repository or a local path.",
    "remote_unverified": "GitHub couldn't be asked whether the mirror repository is private.",
    "shared_reads_unreadable": "The shared-reads ledger can't be read.",
    "state_unreadable": "The delegate mirror state can't be read.",
    "token_insecure": "The stored push token's file is readable by others.",
    "token_missing": "The stored push token is missing.",
    "trading_paths_not_local_only": "Paths that look like trading data aren't local-only yet.",
}
GENERIC_REASON = "The sync was refused; run it in a terminal to see why."


def _stamp(value: Any) -> str | None:
    return value if isinstance(value, str) and _STAMP.match(value) else None


def _count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= _COUNT_MAX:
        return None
    return value


def _counts(value: Any) -> dict[str, int] | None:
    from scout.delegate_mirror import COUNT_KEYS

    if not isinstance(value, dict):
        return None
    out = {k: _count(value.get(k)) for k in COUNT_KEYS}
    if any(v is None for v in out.values()):
        return None
    return {k: v for k, v in out.items() if v is not None}


def _remote(value: Any, home: str) -> dict[str, str | None] | None:
    """``{kind, display}`` for a remote string: a GitHub ``owner/name``, a local path (home as ``~``), or neither."""
    from scout.delegate_mirror import _local_remote, github_repo

    if not isinstance(value, str) or not value or len(value) > _REMOTE_MAX or not value.isprintable():
        return None
    repo = github_repo(value)
    if repo is not None:
        return {"kind": "github", "display": repo}
    local = _local_remote(value)
    if local is not None:
        text = str(local)
        if home and (text == home or text.startswith(home.rstrip("/") + "/")):
            text = "~" + text[len(home.rstrip("/")) :]
        return {"kind": "local", "display": text}
    return {"kind": "other", "display": None}


def _refusal(error: Any) -> dict[str, str | None] | None:
    if not isinstance(error, dict):
        return None
    code = error.get("code")
    code = code if isinstance(code, str) and _CODE.match(code) else "unknown"
    return {
        "code": code,
        "reason": REASONS.get(code, GENERIC_REASON),
        "at": _stamp(error.get("at")),
        "since": _stamp(error.get("first_at")) or _stamp(error.get("at")),
    }


def _entry(key: str, raw: Mapping[str, Any], *, verified: bool, owner: bool, home: str) -> dict[str, Any]:
    refusal = _refusal(raw.get("last_error"))
    last_sync = _stamp(raw.get("last_sync"))
    pending = isinstance(raw.get("pending_mirror"), str) and bool(_SHA.match(raw["pending_mirror"]))
    if refusal is not None:
        result = "failed"
    elif pending:
        result = "pending"
    elif last_sync is not None:
        result = "ok"
    else:
        result = "never"
    stale = raw.get("stale_key_ids")
    stale_n = len(stale) if isinstance(stale, list) and all(_count(k) is not None for k in stale) else None
    key_id = raw.get("deploy_key_id")
    counts = _counts(raw.get("last_counts"))
    return {
        "id": key,
        "linked": True,
        "listed": False,  # set by the caller from the config
        "state_unverified": not verified,
        "remote": _remote(raw.get("remote"), home) if owner else None,
        "linked_at": _stamp(raw.get("linked_at")),
        "last_sync": last_sync,
        "last_attempt": _stamp(raw.get("last_attempt")) or last_sync,
        "last_result": result,
        "refusal": refusal,
        "behind": None,  # set by the caller when the state is trusted
        "deploy_key": None if key_id is not None and _count(key_id) is None else key_id is not None,
        "old_keys_live": stale_n,
        "counts": counts,
        "agent_config_left_out": counts["agent_config"] if counts is not None else None,
        "can_sync": False,
        "can_preview": False,
    }


def _unlinked(key: str) -> dict[str, Any]:
    return {
        "id": key,
        "linked": False,
        "listed": True,
        "state_unverified": False,
        "remote": None,
        "linked_at": None,
        "last_sync": None,
        "last_attempt": None,
        "last_result": "never",
        "refusal": None,
        "behind": None,
        "deploy_key": None,
        "old_keys_live": None,
        "counts": None,
        "agent_config_left_out": None,
        "can_sync": False,
        "can_preview": False,
    }


def _listed(vault: Path) -> list[str]:
    """The delegate ids ``scout-config.yaml → privacy.delegates`` lists, in its order; [] when it can't be read."""
    from scout.kb.privacy import is_delegate_id, load_policy

    try:
        return [d.id for d in load_policy(vault).delegates if is_delegate_id(d.id)]
    except Exception:  # noqa: BLE001 - a config that can't be read lists nothing here; doctor reports it
        return []


def report(vault: Path, *, env: Mapping[str, str], now: datetime | None = None) -> dict[str, Any]:
    """The versioned status document. Never raises on what it reads: a bad state shows in ``state``."""
    from scout.delegate_mirror import STATE_REL, DelegateLink, _verified, needs_sync
    from scout.kb.privacy import is_delegate_id
    from scout.scripts.privacy_migrate import headless_reason

    owner = headless_reason(env) is None
    home = env.get("HOME", "")
    state = "none"
    skipped = 0
    entries: dict[str, dict[str, Any]] = {}
    verified_body: dict[str, Any] | None = None
    path = vault / STATE_REL
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        raw_links = data.get("links") if isinstance(data, dict) else None
        if not isinstance(raw_links, dict):
            state = "unreadable"
        else:
            verified_body = _verified("links", data)
            state = "ok" if verified_body is not None else "unverified"
            for key, raw in raw_links.items():
                well_formed = isinstance(key, str) and is_delegate_id(key) and isinstance(raw, dict)
                if not (well_formed and raw.get("id") == key):
                    skipped += 1
                    continue
                entries[key] = _entry(key, raw, verified=verified_body is not None, owner=owner, home=home)
    if verified_body is not None:
        try:
            links = {k: DelegateLink.from_json(v) for k, v in verified_body["links"].items() if k in entries}
            behind = set(needs_sync(vault, links, now=now))
            for key in links:
                entries[key]["behind"] = key in behind
        except Exception:  # noqa: BLE001 - "behind" is a hint; a link it can't judge shows null
            pass
    listed = _listed(vault)
    for key in listed:
        entries.setdefault(key, _unlinked(key))["listed"] = True
    for e in entries.values():
        e["can_preview"] = owner and e["listed"]
        e["can_sync"] = owner and state == "ok" and e["linked"]
    order = {k: i for i, k in enumerate(listed)}
    delegates = sorted(entries.values(), key=lambda e: (order.get(e["id"], len(order)), e["id"]))
    return {
        "schema_version": SCHEMA_VERSION,
        "generator": GENERATOR,
        "state": state,
        "owner": owner,
        "skipped": skipped,
        "delegates": delegates,
    }
