"""add / remove / validate / list for custom connectors — the logic behind `scoutctl connectors custom`.

Kept out of cli.py so the contract the desktop app and the wizards call (one JSON
object + stable exit codes) is unit-testable without Typer. See
docs/superpowers/specs/2026-10-02-custom-connectors-design.md §3.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from scout import custom_connectors as cc
from scout.scripts.bootstrap import (
    BootstrapConfig,
    CustomApplyResult,
    _template_vars,
    apply_custom_change,
    config_from_vault,
    write_connector_config,
)
from scout.scripts.bootstrap_lock import LockBusyError, acquire_lock_with_wait, release_lock
from scout.scripts.connector_detect import run_bash_probe
from scout.scripts.custom_assembly import render_custom_sections

_EXIT_CODES = {
    "applied": 0,
    "unchanged": 0,
    "valid": 0,
    "dry-run": 0,
    "error": 1,
    "invalid": 2,
    "probe-failed": 2,
    "deferred": 3,
    "conflict": 3,
    "busy": 4,
}

ProbeRunner = Callable[[str], int]  # command -> exit code


def _message(result: CustomApplyResult) -> str:
    if result.status == "applied":
        return "Live: the next scheduled run reads it."
    if result.status == "unchanged":
        return "Already up to date."
    if result.status == "conflict":
        files = ", ".join(result.sidecars)
        return (
            f"Saved, but {files} needs your review: merge it into the live file, then run "
            f"`scoutctl bootstrap resolve {result.sidecars[0].removesuffix('.proposed-merge')}`."
        )
    if result.waiting:
        files = ", ".join(result.waiting)
        return (
            f"Saved. {files} has a pending review (a .proposed-merge sidecar or unresolved conflict markers); "
            "this change lands after you resolve it and run /scout-update."
        )
    # `before` no longer re-assembles to the snapshot: the plugin changed, or
    # connectors.custom.yaml was hand-edited, since the last install/upgrade.
    return (
        "Saved. SKILL.md was last assembled from a different plugin version or connector file, "
        "so this takes effect at the next `scoutctl bootstrap upgrade` (or /scout-update)."
    )


@dataclass
class Outcome:
    status: str
    key: str = ""
    issues: list[cc.Issue] = field(default_factory=list)
    message: str = ""
    updated: list[str] = field(default_factory=list)
    sidecars: list[str] = field(default_factory=list)
    waiting: list[str] = field(default_factory=list)
    sections: list[dict[str, str]] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        return _EXIT_CODES[self.status]

    def to_json(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "key": self.key,
            "message": self.message,
            "issues": [{"path": i.path, "message": i.message} for i in self.issues],
            "updated": self.updated,
            "sidecars": self.sidecars,
            "waiting": self.waiting,
            "sections": self.sections,
        }


def _split(raw_def: Any) -> tuple[str, dict[str, Any], list[cc.Issue]]:
    if not isinstance(raw_def, dict):
        return "", {}, [cc.Issue("definition", "must be a mapping with a `key` field")]
    body = dict(raw_def)
    key = body.pop("key", None)
    if not isinstance(key, str) or not key:
        return "", body, [cc.Issue("key", "required: the connector key, e.g. outlook")]
    return key, body, []


def _unexpected(e: Exception) -> str:
    return f"unexpected error: {type(e).__name__}: {e}"


def _key_of(raw_def: Any) -> str:
    key = raw_def.get("key") if isinstance(raw_def, dict) else None
    return key if isinstance(key, str) else ""


# Each public entry point below ends in a catch-all: the contract (spec §3) is one
# JSON object and exit 1 for any failure that isn't invalid/deferred/conflict, so
# an unexpected exception must never escape as a traceback (exit 70). The lock is
# released by _under_lock's `finally` before the exception reaches the catch-all.


def validate(raw_def: Any, *, plugin_root: Path) -> Outcome:
    try:
        key, body, issues = _split(raw_def)
        if not issues:
            _, issues = cc.parse_connector(
                key, body, reserved=cc.reserved_keys(plugin_root), presets=cc.load_presets(plugin_root)
            )
        return Outcome("invalid" if issues else "valid", key, issues)
    except Exception as e:  # noqa: BLE001 — see the contract note above
        return Outcome("error", _key_of(raw_def), message=_unexpected(e))


def _no_vault(vault: Path) -> Outcome | None:
    if (vault / "scout-config.yaml").exists():
        return None
    return Outcome("error", message=f"no Scout vault at {vault} — install Scout first (the Scout app, or install.sh)")


_CONFIG_READ_ERRORS = (yaml.YAMLError, UnicodeDecodeError, OSError, TypeError, ValueError)


def _try_config_from_vault(
    vault: Path, *, plugin_root: Path, plugin_version: str
) -> tuple[BootstrapConfig | None, str | None]:
    """``config_from_vault``, returning ``(None, message)`` instead of raising.

    Shared by ``_read_config`` (add/remove: a read failure is a hard "error"
    Outcome) and ``list_custom`` (a read failure degrades to an empty enabled
    set plus an issue — the listing still shows what connectors.custom.yaml
    holds). Covers a missing/unreadable file, unparseable YAML, and YAML that
    parses but isn't shaped as a mapping (config_from_vault's ValueError).
    """
    try:
        return config_from_vault(vault, plugin_root=plugin_root, plugin_version=plugin_version), None
    except _CONFIG_READ_ERRORS as e:
        return None, f"scout-config.yaml could not be read: {e}"


def _read_config(vault: Path, key: str, *, plugin_root: Path, plugin_version: str) -> BootstrapConfig | Outcome:
    """``config_from_vault``, with a malformed scout-config.yaml surfaced as an Outcome."""
    cfg, error = _try_config_from_vault(vault, plugin_root=plugin_root, plugin_version=plugin_version)
    if error is not None:
        return Outcome("error", key, message=error)
    assert cfg is not None
    return cfg


def _under_lock(vault: Path, key: str, change: Callable[[], Outcome], *, wait: bool = True) -> Outcome:
    """Run ``change`` holding the session lock.

    ``change`` must do every read of vault state (scout-config.yaml,
    connectors.custom.yaml, the snapshot) itself: a read made before the lock
    is stale by the time a concurrent add/remove has committed, and writing
    whole-file replacements from it would erase that other change.

    ``wait=False`` tries to acquire the lock exactly once (``timeout_s=0``)
    instead of polling for the default window; either way a held lock comes
    back as a ``busy`` Outcome, never ``error``.
    """
    lock = vault / ".scout-logs" / ".scout-session.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        if wait:
            acquire_lock_with_wait(lock)
        else:
            acquire_lock_with_wait(lock, timeout_s=0)
    except LockBusyError:
        return Outcome("busy", key, message="A Scout session is running; try again when it finishes.")
    try:
        return change()
    finally:
        release_lock(lock)


def _commit(
    key: str,
    before: BootstrapConfig,
    after: BootstrapConfig,
    custom_before: dict[str, cc.CustomConnector],
    custom_after: dict[str, cc.CustomConnector],
    raw_after: dict[str, Any],
) -> Outcome:
    """Apply first, then write config, then the definition. The caller holds the lock."""
    vault = after.vault
    result = apply_custom_change(before, after, custom_before=custom_before, custom_after=custom_after)
    # connectors.custom.yaml goes last: if the config write fails, the definition
    # file still says what it said before, so re-running the same add/remove finds
    # the entry where it expects it and converges. The other order could leave a
    # removed entry gone from the file while it stays enabled with its inputs in
    # scout-config.yaml — and a retried `remove` then answers "no such connector".
    write_connector_config(vault, enabled=after.enabled_connectors, inputs=after.connector_inputs)
    cc.write(vault, raw_after)
    return Outcome(
        result.status,
        key,
        message=_message(result),
        updated=result.updated,
        sidecars=result.sidecars,
        waiting=result.waiting,
    )


def _inputs_after(
    before: BootstrapConfig, connector: cc.CustomConnector, inputs: dict[str, str]
) -> tuple[dict[str, str], list[cc.Issue]]:
    """The connector inputs with ``inputs`` namespaced in, plus an issue per declared input still empty."""
    key = connector.key
    new_inputs = dict(before.connector_inputs)
    for name, value in inputs.items():
        new_inputs[f"{key}__{name}"] = value
    needs_path = f"connectors.{key}.needs_user_input"
    issues = [
        cc.Issue(needs_path, f"no value for {name!r}; pass --input {name}=<value>")
        for name in connector.needs_user_input
        if not new_inputs.get(f"{key}__{name}")
    ]
    return new_inputs, issues


def add(
    vault: Path,
    raw_def: Any,
    *,
    plugin_root: Path,
    plugin_version: str,
    inputs: dict[str, str],
    dry_run: bool = False,
    unverified: bool = False,
    probe_runner: ProbeRunner = run_bash_probe,
    wait: bool = True,
) -> Outcome:
    try:
        return _add(
            vault,
            raw_def,
            plugin_root=plugin_root,
            plugin_version=plugin_version,
            inputs=inputs,
            dry_run=dry_run,
            unverified=unverified,
            probe_runner=probe_runner,
            wait=wait,
        )
    except Exception as e:  # noqa: BLE001 — see the contract note above validate()
        return Outcome("error", _key_of(raw_def), message=_unexpected(e))


def _add(
    vault: Path,
    raw_def: Any,
    *,
    plugin_root: Path,
    plugin_version: str,
    inputs: dict[str, str],
    dry_run: bool,
    unverified: bool,
    probe_runner: ProbeRunner,
    wait: bool = True,
) -> Outcome:
    if (missing := _no_vault(vault)) is not None:
        return missing
    # Outside the lock: everything that depends only on the definition and the flags.
    key, body, issues = _split(raw_def)
    if issues:
        return Outcome("invalid", key, issues)
    connector, issues = cc.parse_connector(
        key, body, reserved=cc.reserved_keys(plugin_root), presets=cc.load_presets(plugin_root)
    )
    if connector is not None:
        needs_path = f"connectors.{key}.needs_user_input"
        for name in sorted(set(inputs) - set(connector.needs_user_input)):
            issues.append(cc.Issue(needs_path, f"{name!r} is not an input this connector declares"))
        for name in sorted(n for n, value in inputs.items() if cc.CREDENTIAL_RE.search(value)):
            msg = (
                f"the value for {name!r} looks like a credential; inputs are written into SKILL.md "
                "and must not be secrets — sign the tool in through its own MCP connector or CLI instead"
            )
            issues.append(cc.Issue(needs_path, msg))
    if issues or connector is None:
        return Outcome("invalid", key, issues)

    if dry_run:  # reads only, writes nothing, never takes the lock
        before = _read_config(vault, key, plugin_root=plugin_root, plugin_version=plugin_version)
        if isinstance(before, Outcome):
            return before
        new_inputs, issues = _inputs_after(before, connector, inputs)
        if issues:
            return Outcome("invalid", key, issues)
        dry_run_vars = _template_vars(
            dataclasses.replace(
                before, enabled_connectors=before.enabled_connectors | {key}, connector_inputs=new_inputs
            )
        )
        sections = [
            {"target": f"{kind}.md", "activity": s.activity, "body": s.rendered_body}
            for kind in ("SKILL", "RESEARCH")
            for s in render_custom_sections(plugin_root, kind, {key: connector}, {key}, dry_run_vars, new_inputs)
        ]
        return Outcome("dry-run", key, sections=sections)

    if connector.probe.kind == "bash" and not unverified:
        rc = probe_runner(connector.probe.value)
        if rc != 0:
            message = f"`{connector.probe.value}` exited {rc}"
            return Outcome("probe-failed", key, [cc.Issue(f"connectors.{key}.probe", message)])

    def change() -> Outcome:
        assert connector is not None
        before = _read_config(vault, key, plugin_root=plugin_root, plugin_version=plugin_version)
        if isinstance(before, Outcome):
            return before
        current = cc.load(vault, plugin_root=plugin_root)
        new_inputs, missing_inputs = _inputs_after(before, connector, inputs)
        if missing_inputs:  # needs the stored values, so it is checked under the lock
            return Outcome("invalid", key, missing_inputs)
        after = dataclasses.replace(
            before, enabled_connectors=before.enabled_connectors | {key}, connector_inputs=new_inputs
        )
        return _commit(
            key,
            before,
            after,
            current.connectors,
            {**current.connectors, key: connector},
            {**current.raw, key: body},
        )

    return _under_lock(vault, key, change, wait=wait)


def remove(vault: Path, key: str, *, plugin_root: Path, plugin_version: str, wait: bool = True) -> Outcome:
    try:
        return _remove(vault, key, plugin_root=plugin_root, plugin_version=plugin_version, wait=wait)
    except Exception as e:  # noqa: BLE001 — see the contract note above validate()
        return Outcome("error", key, message=_unexpected(e))


def _remove(vault: Path, key: str, *, plugin_root: Path, plugin_version: str, wait: bool = True) -> Outcome:
    if (missing := _no_vault(vault)) is not None:
        return missing
    unknown = Outcome("invalid", key, [cc.Issue(f"connectors.{key}", "no such custom connector")])
    # Cheap early answer without waiting for the lock; re-checked under it below.
    if key not in cc.load(vault, plugin_root=plugin_root).raw:
        return unknown

    def change() -> Outcome:
        current = cc.load(vault, plugin_root=plugin_root)
        if key not in current.raw:
            return unknown
        before = _read_config(vault, key, plugin_root=plugin_root, plugin_version=plugin_version)
        if isinstance(before, Outcome):
            return before
        after = dataclasses.replace(
            before,
            enabled_connectors=before.enabled_connectors - {key},
            connector_inputs={k: v for k, v in before.connector_inputs.items() if not k.startswith(f"{key}__")},
        )
        return _commit(
            key,
            before,
            after,
            current.connectors,
            {k: c for k, c in current.connectors.items() if k != key},
            {k: v for k, v in current.raw.items() if k != key},
        )

    return _under_lock(vault, key, change, wait=wait)


def list_custom(vault: Path, *, plugin_root: Path) -> dict[str, Any]:
    try:
        return _list_custom(vault, plugin_root=plugin_root)
    except Exception as e:  # noqa: BLE001 — see the contract note above validate()
        return {"connectors": [], "issues": [{"path": "", "message": _unexpected(e)}]}


def _issues_for_key(issues: list[cc.Issue], key: str) -> list[dict[str, str]]:
    """This key's own issues: path equal to ``connectors.<key>`` or nested under it."""
    base = f"connectors.{key}"
    return [{"path": i.path, "message": i.message} for i in issues if i.path == base or i.path.startswith(f"{base}.")]


def _list_custom(vault: Path, *, plugin_root: Path) -> dict[str, Any]:
    current = cc.load(vault, plugin_root=plugin_root)
    issues = list(current.issues)
    enabled: set[str] = set()
    if (vault / "scout-config.yaml").exists():
        cfg, error = _try_config_from_vault(vault, plugin_root=plugin_root, plugin_version="")
        if error is not None:
            issues.append(cc.Issue("scout-config.yaml", error))
        else:
            assert cfg is not None
            enabled = cfg.enabled_connectors
    valid_rows = [
        {
            "key": c.key,
            "display_name": c.display_name,
            "enabled": c.key in enabled,
            "server": c.server,
            "health_key": c.health_key,
            "preset": c.preset,
            "activities": [a for a in cc.ACTIVITIES if a in c.activities],
            "valid": True,
            "definition": current.raw[c.key],
        }
        for c in current.connectors.values()
    ]
    invalid_rows = [
        {
            "key": key,
            "valid": False,
            "enabled": key in enabled,
            "display_name": raw_def.get("display_name") if isinstance(raw_def, dict) else None,
            "definition": raw_def,
            "issues": _issues_for_key(current.issues, key),
        }
        for key, raw_def in current.raw.items()
        if key not in current.connectors
    ]
    for row in invalid_rows:
        if not isinstance(row["display_name"], str):
            row["display_name"] = None
    rows = sorted([*valid_rows, *invalid_rows], key=lambda r: r["key"])
    return {"connectors": rows, "issues": [{"path": i.path, "message": i.message} for i in issues]}


def presets_json(plugin_root: Path) -> dict[str, Any]:
    return {"presets": cc.load_presets(plugin_root)}
