"""Write support for the vault's ``models:`` block.

Serves ``scoutctl model set-default``, which Scout.app's Settings calls: the app
never edits ``scout-config.yaml`` itself. Like
:mod:`scout.scripts.auto_update_config`, it edits lines rather than round-tripping
the file through pyyaml, which would delete every comment in it. Only the
``models:`` block changes, each edit is checked by loading the result back, and
nothing is written when that check fails.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

from scout import paths
from scout.errors import ConfigError
from scout.models import validate_model
from scout.scripts.auto_update_config import _edit_children, _entry_starts, _is_filler, _loads_as, _mtime_ns

_KEY = "models"
_HEADER_RE = re.compile(r"models[ \t]*:(?P<rest>[ \t].*)?$")
_APPENDED_BLOCK_COMMENT = (
    "# The Claude model scheduled runs use when their slot names none (a slot's\n"
    "# own `model:` in .scout-state/schedule.yaml wins). opus, sonnet, haiku or a\n"
    "# full claude-… model ID. Set with `scoutctl model set-default` or in Scout.app.\n"
)


class ModelsWriteError(Exception):
    """A `model set-default` could not be applied. Carries a user-facing message."""


def _validated(model: str) -> str:
    try:
        return validate_model(model, where="models.default")
    except ConfigError as e:
        raise ModelsWriteError(str(e)) from e


def apply_models_default(text: str, model: str) -> str:
    """Return ``text`` with ``models.default`` set to ``model``; every other byte kept.

    Appends a commented ``models:`` block when there is none. Text that already
    says ``model`` comes back unchanged, so a re-run writes nothing. Pure: no
    filesystem. Raises :class:`ModelsWriteError` for a bad model and for a file
    that cannot be edited safely.
    """
    value = _validated(model)
    try:
        original = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ModelsWriteError(f"cannot parse scout-config.yaml, so it was not changed: {e}") from e
    if original is None:
        original = {}
    if not isinstance(original, dict):
        raise ModelsWriteError(f"scout-config.yaml is not a mapping (got {type(original).__name__})")

    current = original.get(_KEY)
    if current is not None and not isinstance(current, dict):
        raise ModelsWriteError(f"scout-config.yaml: models is {current!r}, not a mapping — fix it by hand")
    values: dict[str, Any] = dict(current or {})
    if values.get("default") == value:
        return text
    values["default"] = value
    expected = {**original, _KEY: values}

    lines = text.splitlines(keepends=True)
    starts = _entry_starts(lines)
    headers = [(i, m) for i in starts if (m := _HEADER_RE.match(lines[i].rstrip("\r\n")))]
    if len(headers) > 1:
        raise ModelsWriteError("scout-config.yaml defines models more than once — merge them by hand")

    if not headers:
        if _KEY in original:
            raise ModelsWriteError("cannot locate the models block in scout-config.yaml — edit it by hand")
        block = f"{_APPENDED_BLOCK_COMMENT}{_KEY}:\n  default: {value}\n"
        if not text.strip():
            result = text + block
        else:
            prefix = text if text.endswith("\n") else text + "\n"
            result = prefix + ("" if prefix.endswith("\n\n") else "\n") + block
        if _loads_as(result, expected):
            return result
        raise ModelsWriteError("appending models would change other settings — edit it by hand")

    start, header = headers[0]
    end = next((i for i in starts if i > start), len(lines))
    content_end = end
    while content_end > start + 1 and _is_filler(lines[content_end - 1]):
        content_end -= 1

    inline = (header["rest"] or "").strip()
    if not inline or inline.startswith("#"):
        held = set(current or {})
        edited = _edit_children(lines, start, content_end, values, ["default"], held)
        if edited is not None and _loads_as(result := "".join(edited), expected):
            return result

    dumped = yaml.safe_dump({_KEY: values}, sort_keys=False, default_flow_style=False)
    result = "".join(lines[:start]) + dumped + "".join(lines[content_end:])
    if _loads_as(result, expected):
        return result
    raise ModelsWriteError("cannot update models without disturbing the rest of scout-config.yaml")


def write_models_default(model: str, *, data_dir: Path | None = None, max_attempts: int = 4) -> dict[str, Any]:
    """Apply the change to the vault config; return ``{config_path, default, changed}``.

    Re-applies when another writer changes the file between our read and our
    replace, as ``write_auto_update`` does. Refuses a vault with no
    scout-config.yaml rather than creating one.
    """
    value = _validated(model)
    target = paths.require_data_dir(data_dir)
    config_path = paths.config_path(target)

    for _ in range(max_attempts):
        try:
            text = config_path.read_text(encoding="utf-8")
        except FileNotFoundError as e:
            raise ModelsWriteError(f"no vault config at {config_path} — run /scout-setup first") from e
        except (OSError, UnicodeDecodeError) as e:
            raise ModelsWriteError(f"cannot read {config_path}: {e}") from e
        mtime_at_read = _mtime_ns(config_path)

        updated = apply_models_default(text, value)
        if updated == text:
            return {"config_path": str(config_path), "default": value, "changed": False}
        if _mtime_ns(config_path) != mtime_at_read:
            continue

        tmp = config_path.with_name(f"{config_path.name}.{os.getpid()}.tmp")
        try:
            tmp.write_text(updated, encoding="utf-8")
            os.replace(tmp, config_path)
        except OSError as e:
            tmp.unlink(missing_ok=True)
            raise ModelsWriteError(f"cannot write {config_path}: {e}") from e
        return {"config_path": str(config_path), "default": value, "changed": True}

    raise ModelsWriteError(f"{config_path} kept changing under us — another writer is active; nothing was written")


__all__ = ["ModelsWriteError", "apply_models_default", "write_models_default"]
