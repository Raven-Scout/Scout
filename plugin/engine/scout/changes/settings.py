"""The changes map's owner settings: the auto-rollback switch, the review window and the calibration date.

They live in the owner store (``settings.json`` beside the owner engagement ledger), never in ``scout-config.yaml``:
a run can write the config and could switch rollback on for itself, and the sandbox denies runs any write to the
owner store. The reader never raises; anything missing or malformed reads as its default."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from scout.changes.store import atomic_write_json, owner_dir

AUTO_MODES = ("off", "targeted", "one-at-a-time")
SETTINGS_NAME = "settings.json"
_SCHEMA = 1
REVIEW_DAYS_MIN = 7
REVIEW_DAYS_MAX = 60


@dataclass(frozen=True)
class Settings:
    auto_rollback: str = "off"
    review_days: int = 14
    # None while calibrating (spec §7a): verdicts are shown, labelled, and nothing acts on them.
    calibrated_on: date | None = None


def _clamp(days: int) -> int:
    return max(REVIEW_DAYS_MIN, min(REVIEW_DAYS_MAX, days))


def _from(data: Any) -> Settings:
    default = Settings()
    if not isinstance(data, dict) or data.get("schema") != _SCHEMA:
        return default
    mode = data.get("auto_rollback")
    days = data.get("review_days")
    raw_date = data.get("calibrated_on")
    calibrated: date | None = None
    if isinstance(raw_date, str):
        try:
            calibrated = date.fromisoformat(raw_date)
        except ValueError:
            calibrated = None
    return Settings(
        auto_rollback=mode if isinstance(mode, str) and mode in AUTO_MODES else default.auto_rollback,
        review_days=_clamp(days) if isinstance(days, int) and not isinstance(days, bool) else default.review_days,
        calibrated_on=calibrated,
    )


def read_settings(env: Mapping[str, str]) -> Settings:
    """``owner_dir(env)/settings.json`` (``{"schema": 1, ...}``). Never raises: each bad field reads as its default."""
    try:
        text = (owner_dir(env) / SETTINGS_NAME).read_text(encoding="utf-8")
        return _from(json.loads(text))
    except Exception:  # noqa: BLE001 - unreadable, not JSON, nested too deep, no home: the defaults
        return Settings()


def write_settings(settings: Settings, env: Mapping[str, str]) -> None:
    """Atomic 0600 write to the owner store. Raises ValueError for an unknown mode, OSError when it didn't save.

    The caller checks the owner (``store.is_owner_caller``); this only writes."""
    if settings.auto_rollback not in AUTO_MODES:
        raise ValueError(f"auto_rollback: {settings.auto_rollback!r} not in {AUTO_MODES}")
    data = {
        "schema": _SCHEMA,
        "auto_rollback": settings.auto_rollback,
        "review_days": _clamp(settings.review_days),
        "calibrated_on": settings.calibrated_on.isoformat() if settings.calibrated_on else None,
    }
    if not atomic_write_json(owner_dir(env) / SETTINGS_NAME, data):
        raise OSError("settings.json: could not save")
