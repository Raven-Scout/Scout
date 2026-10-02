"""Write support for the vault's ``auto_update:`` block.

Serves ``scoutctl config set-auto-update``, which the auto-update opt-in steps
of /scout-setup and /scout-update call. Those steps used to run an inline pyyaml
round-trip — ``safe_load``, set two keys, ``safe_dump`` — and that deleted every
comment in ``scout-config.yaml``: the note ``scoutctl budget set`` writes above
``budget:``, and anything the user wrote by hand.

The writer edits lines, like :mod:`scout.scripts.budget_config`, because the
file is not single-purpose: it holds bootstrap state (version stamps,
connectors, schedule) and user overrides, written by several producers. Only
the lines of the ``auto_update:`` block change; every other byte is kept. Each
edit is checked by loading the result back. If that check fails, the block
alone is re-dumped (its own comments are lost, nothing else is). If that fails
too, nothing is written.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

from scout import paths

# The channels a vault may subscribe to. Only `stable` ships today.
CHANNELS = ("stable",)
DEFAULT_CHANNEL = "stable"

_KEY = "auto_update"
_HEADER_RE = re.compile(r"auto_update[ \t]*:(?P<rest>[ \t].*)?$")
_SEQUENCE_ITEM_RE = re.compile(r"-(\s|$)")
_APPENDED_BLOCK_COMMENT = (
    "# Whether Scout keeps itself up to date — toggle via /scout-update or\n"
    "# `scoutctl config set-auto-update`. Safe to edit by hand.\n"
)


class AutoUpdateWriteError(Exception):
    """A `config set-auto-update` could not be applied. Carries a user-facing message."""


def _validate_channel(channel: str | None) -> None:
    if channel is not None and channel not in CHANNELS:
        raise AutoUpdateWriteError(f"unknown channel {channel!r} — expected one of: {', '.join(CHANNELS)}")


def _scalar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _loads_as(text: str, expected: dict[str, Any]) -> bool:
    try:
        return bool(yaml.safe_load(text) == expected)
    except yaml.YAMLError:
        return False


def _newline(line: str) -> str:
    return line[len(line.rstrip("\r\n")) :]


def _is_filler(line: str) -> bool:
    """A blank or comment-only line."""
    stripped = line.strip()
    return not stripped or stripped.startswith("#")


def _entry_starts(lines: list[str]) -> list[int]:
    """Indices of zero-indent lines that open a top-level entry.

    A ``---`` marker counts too: it ends whatever came before it. A zero-indent
    ``- item`` does not — it is a sequence item under the previous key.
    """
    return [
        i
        for i, line in enumerate(lines)
        if line.strip() and line[0] not in " \t#" and not _SEQUENCE_ITEM_RE.match(line)
    ]


def _edit_children(
    lines: list[str], start: int, content_end: int, values: dict[str, Any], changed: list[str]
) -> list[str]:
    """Rewrite or insert the ``changed`` child lines of a block-style mapping.

    A rewritten line keeps its indent and any inline comment. Missing keys go in
    after the block's last content line, at the indent its children already use.
    """
    out = list(lines)
    indent = "  "
    for line in out[start + 1 : content_end]:
        if not _is_filler(line):
            indent = line[: len(line) - len(line.lstrip(" \t"))]
            break
    child_re = re.compile(
        rf"^{re.escape(indent)}(?P<key>[A-Za-z_][A-Za-z0-9_]*)[ \t]*:"
        r"(?P<sep>[ \t]*)(?P<value>.*?)(?P<comment>[ \t]+#.*)?$"
    )

    found: set[str] = set()
    for i in range(start + 1, content_end):
        body = out[i].rstrip("\r\n")
        match = child_re.match(body)
        if match is None or match["key"] not in changed:
            continue
        key = match["key"]
        found.add(key)
        head = body[: match.start("sep")]
        out[i] = f"{head} {_scalar(values[key])}{match['comment'] or ''}{_newline(out[i])}"

    missing = [key for key in changed if key not in found]
    if missing:
        last = content_end - 1
        if not _newline(out[last]):
            out[last] += "\n"
        out[content_end:content_end] = [f"{indent}{key}: {_scalar(values[key])}\n" for key in missing]
    return out


def _append_block(text: str, values: dict[str, Any]) -> str:
    body = "".join(f"  {key}: {_scalar(value)}\n" for key, value in values.items())
    block = f"{_APPENDED_BLOCK_COMMENT}{_KEY}:\n{body}"
    if not text.strip():
        return text + block
    prefix = text if text.endswith("\n") else text + "\n"
    separator = "" if prefix.endswith("\n\n") else "\n"
    return f"{prefix}{separator}{block}"


def apply_auto_update(text: str, *, enabled: bool | None = None, channel: str | None = None) -> str:
    """Return ``text`` with the top-level ``auto_update:`` block updated.

    ``enabled`` / ``channel`` of None leave that key as it is, and fill it with
    the default (``false`` / ``stable``) when the block lacks it, so the block
    always states both. The block is appended when absent. Text that already
    holds these values comes back unchanged, so a re-run writes nothing.

    Pure: no filesystem, so the byte-preservation guarantee is testable
    directly. Raises :class:`AutoUpdateWriteError` for an unknown channel and for
    a file that cannot be edited safely (unparseable, not a mapping, the block
    duplicated, or an edit that would not load back as intended).
    """
    _validate_channel(channel)
    try:
        original = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise AutoUpdateWriteError(f"cannot parse scout-config.yaml, so it was not changed: {e}") from e
    if original is None:
        original = {}
    if not isinstance(original, dict):
        raise AutoUpdateWriteError(f"scout-config.yaml is not a mapping (got {type(original).__name__})")

    current = original.get(_KEY)
    values = dict(current) if isinstance(current, dict) else {}
    if enabled is not None:
        values["enabled"] = enabled
    else:
        values.setdefault("enabled", False)
    if channel is not None:
        values["channel"] = channel
    else:
        values.setdefault("channel", DEFAULT_CHANNEL)
    if values == current:
        return text
    expected = {**original, _KEY: values}

    lines = text.splitlines(keepends=True)
    starts = _entry_starts(lines)
    headers = [(i, m) for i in starts if (m := _HEADER_RE.match(lines[i].rstrip("\r\n")))]
    if len(headers) > 1:
        raise AutoUpdateWriteError("scout-config.yaml defines auto_update more than once — merge them by hand")

    if not headers:
        if _KEY in original:
            # Present, but not as a plain `auto_update:` line we can find.
            raise AutoUpdateWriteError("cannot locate the auto_update block in scout-config.yaml — edit it by hand")
        result = _append_block(text, {"enabled": values["enabled"], "channel": values["channel"]})
        if _loads_as(result, expected):
            return result
        raise AutoUpdateWriteError("appending auto_update would change other settings — edit it by hand")

    start, header = headers[0]
    end = next((i for i in starts if i > start), len(lines))
    content_end = end
    while content_end > start + 1 and _is_filler(lines[content_end - 1]):
        content_end -= 1

    inline = (header["rest"] or "").strip()
    if not inline or inline.startswith("#"):
        if isinstance(current, dict):
            changed = [key for key in ("enabled", "channel") if key not in current or current[key] != values[key]]
        else:
            changed = ["enabled", "channel"]
        result = "".join(_edit_children(lines, start, content_end, values, changed))
        if _loads_as(result, expected):
            return result

    # Flow style, an inline scalar, or a line edit that did not load back:
    # re-dump this block alone.
    dumped = yaml.safe_dump({_KEY: values}, sort_keys=False, default_flow_style=False)
    result = "".join(lines[:start]) + dumped + "".join(lines[content_end:])
    if _loads_as(result, expected):
        return result
    raise AutoUpdateWriteError("cannot update auto_update without disturbing the rest of scout-config.yaml")


def _mtime_ns(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def _payload(config_path: Path, text: str, *, changed: bool) -> dict[str, Any]:
    block = (yaml.safe_load(text) or {}).get(_KEY) or {}
    return {
        "config_path": str(config_path),
        "enabled": block.get("enabled"),
        "channel": block.get("channel"),
        "changed": changed,
    }


def write_auto_update(
    *,
    enabled: bool | None = None,
    channel: str | None = None,
    data_dir: Path | None = None,
    max_attempts: int = 4,
) -> dict[str, Any]:
    """Apply the change to the vault config; return what the block now says.

    The payload is ``{config_path, enabled, channel, changed}``. Re-reads and
    re-applies when the file changes between our read and our replace — the
    same guard as ``budget_config.write_budget`` — because scout-config.yaml
    has several producers and a blind write can drop a concurrent one.

    Refuses a vault with no scout-config.yaml rather than creating one: that
    file is how ``bootstrap upgrade`` recognises an installed vault.
    """
    _validate_channel(channel)
    target = paths.require_data_dir(data_dir)
    config_path = paths.config_path(target)

    for _ in range(max_attempts):
        try:
            text = config_path.read_text(encoding="utf-8")
        except FileNotFoundError as e:
            raise AutoUpdateWriteError(f"no vault config at {config_path} — run /scout-setup first") from e
        except OSError as e:
            raise AutoUpdateWriteError(f"cannot read {config_path}: {e}") from e
        mtime_at_read = _mtime_ns(config_path)

        updated = apply_auto_update(text, enabled=enabled, channel=channel)
        if updated == text:
            return _payload(config_path, text, changed=False)

        # A concurrent writer landed between our read and now → loop and
        # reapply onto their content rather than overwriting it.
        if _mtime_ns(config_path) != mtime_at_read:
            continue

        tmp = config_path.with_name(f"{config_path.name}.{os.getpid()}.tmp")
        try:
            tmp.write_text(updated, encoding="utf-8")
            os.replace(tmp, config_path)
        except OSError as e:
            tmp.unlink(missing_ok=True)
            raise AutoUpdateWriteError(f"cannot write {config_path}: {e}") from e
        return _payload(config_path, updated, changed=True)

    raise AutoUpdateWriteError(f"{config_path} kept changing under us — another writer is active; nothing was written")


__all__ = [
    "CHANNELS",
    "DEFAULT_CHANNEL",
    "AutoUpdateWriteError",
    "apply_auto_update",
    "write_auto_update",
]
