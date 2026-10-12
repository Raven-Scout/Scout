"""Scripted connector responses (spec 2026-10-12 §5.4).

A scenario's ``connectors.yaml`` maps persona → tool → an ordered rule list.
The first rule that matches answers; ``"*"`` holds a persona's fallback rules,
tried after the tool's own.

A rule has:

- ``match``: field → regex, searched in the stringified argument (JSON for
  non-strings); every field must match;
- one outcome: ``respond`` (an object, or a path relative to the script
  holding JSON, date-rendered), ``error`` (``auth_expired``, ``unavailable``
  or a literal message), or ``capture`` (record the call under a name and
  answer ``ok``);
- optional ``times: N``: the rule stops matching after N hits.

Hit counts come from the caller, because the gh stub is a new process per
call and the MCP stub must agree with it: both count rows in the calls log.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from ..dates import render_dates

RULE_KEYS = {"match", "respond", "error", "capture", "times"}
ERRORS = {
    "auth_expired": "Authentication required: the connection's OAuth token has expired. Reconnect it in Settings.",
    "unavailable": "Service unavailable: the server did not respond (503).",
}


class ScriptError(Exception):
    pass


@dataclass(frozen=True)
class Reply:
    kind: str  # "ok" | "error" | "unstubbed"
    payload: Any
    rule: str | None
    capture: str | None = None


@dataclass(frozen=True)
class _Rule:
    rule_id: str
    match: dict[str, re.Pattern[str]]
    respond: Any
    error: str | None
    capture: str | None
    times: int | None


def _as_text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, sort_keys=True)


class Script:
    def __init__(self, rules: dict[str, dict[str, list[_Rule]]]) -> None:
        self._rules = rules

    @classmethod
    def load(cls, path: Path, *, today: date) -> Script:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        rules: dict[str, dict[str, list[_Rule]]] = {}
        for persona, tools in raw.items():
            for tool, entries in (tools or {}).items():
                for i, entry in enumerate(entries or []):
                    rule_id = f"{persona}:{tool}:{i}"
                    rules.setdefault(persona, {}).setdefault(tool, []).append(
                        cls._rule(rule_id, entry or {}, path.parent, today)
                    )
        return cls(rules)

    @staticmethod
    def _rule(rule_id: str, entry: dict[str, Any], base: Path, today: date) -> _Rule:
        # Every scenario, every gh call and every persona loads the script, so a typo must
        # fail here, loudly: `macth:` would otherwise match every call, `time: 1` never stop.
        unknown = set(entry) - RULE_KEYS
        if unknown:
            raise ScriptError(f"rule {rule_id}: unknown key(s) {', '.join(sorted(unknown))}")
        times = entry.get("times")
        if times is not None and (not isinstance(times, int) or isinstance(times, bool) or times < 1):
            raise ScriptError(f"rule {rule_id}: times must be a positive integer, got {times!r}")
        # An error stands alone; otherwise respond, capture, or a capture that also responds.
        has_error = "error" in entry
        if has_error == ("respond" in entry or "capture" in entry):
            raise ScriptError(f"rule {rule_id} needs an error, or a respond and/or capture (not both kinds)")
        respond = entry.get("respond")
        if isinstance(respond, str):
            file = base / respond
            if not file.is_file():
                raise ScriptError(f"rule {rule_id}: response file not found: {respond}")
            try:
                respond = json.loads(render_dates(file.read_text(encoding="utf-8"), today))
            except json.JSONDecodeError as exc:
                raise ScriptError(f"rule {rule_id}: response file {respond} is not JSON: {exc}") from exc
        error = entry.get("error")
        return _Rule(
            rule_id=rule_id,
            match={k: re.compile(str(v)) for k, v in (entry.get("match") or {}).items()},
            respond={"ok": True} if respond is None and error is None else respond,
            error=None if error is None else ERRORS.get(error, str(error)),
            capture=entry.get("capture"),
            times=entry.get("times"),
        )

    def reply(self, persona: str, tool: str, args: dict[str, Any], *, prior_hits: Callable[[str], int]) -> Reply:
        tools = self._rules.get(persona, {})
        for rule in [*tools.get(tool, []), *tools.get("*", [])]:
            if rule.times is not None and prior_hits(rule.rule_id) >= rule.times:
                continue
            if not all(field in args and rx.search(_as_text(args[field])) for field, rx in rule.match.items()):
                continue
            if rule.error is not None:
                return Reply("error", rule.error, rule.rule_id)
            return Reply("ok", rule.respond, rule.rule_id, rule.capture)
        return Reply("unstubbed", f"{persona}.{tool} is not available in this eval fixture", None)
