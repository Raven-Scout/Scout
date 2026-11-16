"""Which model providers the owner lets a run send vault content to (spec 2026-11-13 §8.4, Q4).

Anthropic is always consented: it is today's behaviour, and the default. Any other
provider needs the owner's explicit step, ``scoutctl privacy consent-provider <id>``,
recorded like the P5 consent (#510): who (the owner, at a keyboard, never a run), when,
and for which vault.

**Where consent lives.** A scheduled run can write ``scout-config.yaml``, so a list
there can't be what grants consent: a run could consent for the next one. The grant
is a record in ``~/.local/state/scout/provider-consent.json``, which the run write gate
and the OS sandbox protect like ``run-sandbox.json`` (``kb/hook_paths.py``). The
overlay key ``privacy.providers`` in ``scout-config.yaml`` can only *narrow* it: when
present, a provider must be in both. So a run that edits the overlay can revoke,
never grant.

**Local-only paths** never go to a non-Anthropic run, consent or not (§8.4). That
exclusion is enforced by the read gate, which learns it in Phase 2 with the first
such runtime; until then ``check`` refuses every non-Anthropic run on the vault.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scout.kb.hook_paths import PROVIDER_CONSENT_NAME

ALWAYS = "anthropic"
SCHEMA = 1


def record_path(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    return Path(env.get("HOME") or Path.home()) / ".local" / "state" / "scout" / PROVIDER_CONSENT_NAME


def _vault_key(vault: Path) -> str:
    try:
        return str(vault.resolve())
    except OSError:
        return str(vault.absolute())


def _read(path: Path) -> dict[str, Any]:
    """The record, or an empty one when missing. A record that doesn't parse is treated as empty (no consent)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema": SCHEMA, "vaults": {}}
    if not isinstance(data, dict) or data.get("schema") != SCHEMA or not isinstance(data.get("vaults"), dict):
        return {"schema": SCHEMA, "vaults": {}}
    return data


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _normalise(provider: str) -> str:
    p = provider.strip().lower()
    if not p or not all(c.isalnum() or c in "-_." for c in p):
        raise ValueError(f"provider {provider!r} is not a provider id (e.g. openai, google, xai)")
    return p


@dataclass(frozen=True)
class ProviderConsent:
    provider: str
    consented_at: str | None  # ISO-8601 UTC; None for anthropic, which needs none


def recorded(vault: Path, *, env: Mapping[str, str] | None = None) -> dict[str, str]:
    """provider -> consented_at, from the protected record, for *vault*."""
    entry = _read(record_path(env)).get("vaults", {}).get(_vault_key(vault), {})
    providers = entry.get("providers") if isinstance(entry, dict) else None
    if not isinstance(providers, dict):
        return {}
    out: dict[str, str] = {}
    for name, info in providers.items():
        if isinstance(name, str) and isinstance(info, dict) and isinstance(info.get("consented_at"), str):
            out[name] = info["consented_at"]
    return out


def overlay_providers(vault: Path) -> tuple[str, ...] | None:
    """``privacy.providers`` from scout-config.yaml, or None when absent. Raises ValueError on a wrong shape."""
    from scout.config import load_config

    privacy = load_config(vault).get("privacy")
    if not isinstance(privacy, dict) or privacy.get("providers") is None:
        return None
    value = privacy["providers"]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"scout-config.yaml: privacy.providers must be a list of provider ids, got {value!r}")
    return tuple(v.strip().lower() for v in value)


def consented(vault: Path, *, env: Mapping[str, str] | None = None) -> tuple[ProviderConsent, ...]:
    """The providers a run on *vault* may send content to: anthropic, plus each recorded one the overlay allows."""
    out = [ProviderConsent(ALWAYS, None)]
    overlay = overlay_providers(vault)
    for name, at in sorted(recorded(vault, env=env).items()):
        if name == ALWAYS:
            continue
        if overlay is not None and name not in overlay:
            continue
        out.append(ProviderConsent(name, at))
    return tuple(out)


def is_consented(vault: Path, provider: str, *, env: Mapping[str, str] | None = None) -> bool:
    if provider == ALWAYS:
        return True
    return any(c.provider == provider for c in consented(vault, env=env))


def consent(vault: Path, provider: str, *, env: Mapping[str, str] | None = None, now: datetime | None = None) -> str:
    """Record the owner's consent for *provider* on *vault*; return its timestamp. Idempotent (keeps the first)."""
    p = _normalise(provider)
    if p == ALWAYS:
        return ""
    path = record_path(env)
    data = _read(path)
    vaults = data.setdefault("vaults", {})
    entry = vaults.setdefault(_vault_key(vault), {})
    providers = entry.setdefault("providers", {})
    if p in providers and isinstance(providers[p], dict) and isinstance(providers[p].get("consented_at"), str):
        return str(providers[p]["consented_at"])
    at = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    providers[p] = {"consented_at": at}
    _write(path, data)
    return at


def revoke(vault: Path, provider: str, *, env: Mapping[str, str] | None = None) -> bool:
    """Remove *provider*'s consent for *vault*. True when there was one. Anthropic can't be revoked here."""
    p = _normalise(provider)
    if p == ALWAYS:
        raise ValueError("anthropic is the provider every Claude Code run uses; it can't be revoked here")
    path = record_path(env)
    data = _read(path)
    providers = data.get("vaults", {}).get(_vault_key(vault), {}).get("providers", {})
    if p not in providers:
        return False
    del providers[p]
    _write(path, data)
    return True


__all__ = [
    "ALWAYS",
    "ProviderConsent",
    "consent",
    "consented",
    "is_consented",
    "overlay_providers",
    "record_path",
    "recorded",
    "revoke",
]
