"""The headless-update ledger — ``.scout-logs/update-outcomes.jsonl`` (run-health spec §4.1).

One row per ``scoutctl self-update run`` attempt that got past the scheduled
gate. Append-only: ``self_update_run`` writes it and ``health.update_findings``
reads it. Readers skip lines they can't parse, so a truncated append never
breaks an evaluation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

LEDGER = ".scout-logs/update-outcomes.jsonl"
RESULTS = (
    "up_to_date",
    "not_eligible",
    "waiting_release",
    "deferred",
    "held",
    "applied",
    "applied_with_sidecars",
    "failed",
)
STEPS = ("check", "fetch", "venv", "vault", "verify")


@dataclass(frozen=True)
class UpdateOutcome:
    at: datetime
    from_version: str
    target: str | None
    managed_by: str
    result: str
    step: str
    doctor: str | None = None
    sidecars: tuple[str, ...] = ()
    detail: str = ""
    log: str | None = None
    # When the target's GitHub release was published; None when unknown or unpublished.
    release_published_at: datetime | None = None

    def to_json(self) -> dict:
        return {
            "at": int(self.at.timestamp()),
            "from": self.from_version,
            "target": self.target,
            "managed_by": self.managed_by,
            "result": self.result,
            "step": self.step,
            "doctor": self.doctor,
            "sidecars": list(self.sidecars),
            "detail": self.detail,
            "log": self.log,
            "release_published_at": int(self.release_published_at.timestamp())
            if self.release_published_at is not None
            else None,
        }


def append(vault: Path, row: UpdateOutcome) -> None:
    path = vault / LEDGER
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row.to_json(), sort_keys=True) + "\n")


def _epoch(value: object) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return datetime.fromtimestamp(value, UTC)


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _parse(obj: object) -> UpdateOutcome | None:
    if not isinstance(obj, dict):
        return None
    at = _epoch(obj.get("at"))
    result, step = obj.get("result"), obj.get("step")
    from_version, managed_by = obj.get("from"), obj.get("managed_by")
    if at is None or result not in RESULTS or step not in STEPS:
        return None
    if not isinstance(from_version, str) or not isinstance(managed_by, str):
        return None
    sidecars = obj.get("sidecars")
    return UpdateOutcome(
        at=at,
        from_version=from_version,
        target=_str_or_none(obj.get("target")),
        managed_by=managed_by,
        result=result,
        step=step,
        doctor=_str_or_none(obj.get("doctor")),
        sidecars=tuple(s for s in sidecars if isinstance(s, str)) if isinstance(sidecars, list) else (),
        detail=_str_or_none(obj.get("detail")) or "",
        log=_str_or_none(obj.get("log")),
        release_published_at=_epoch(obj.get("release_published_at")),
    )


def read(vault: Path) -> list[UpdateOutcome]:
    """Every parseable row, oldest first. A missing or unreadable ledger reads as empty."""
    try:
        text = (vault / LEDGER).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    rows: list[UpdateOutcome] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = _parse(json.loads(line))
        except json.JSONDecodeError:
            continue
        if row is not None:
            rows.append(row)
    return rows
