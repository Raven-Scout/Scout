"""kb-lint configuration: size budgets, path scopes and blocking mode.

Defaults ship in scout/defaults/scout-config.yaml under ``kb_lint``. A vault
may add budgets with a top-level ``kb_budgets: {glob: bytes}`` map, which is
consulted before the defaults (first match wins), and may set
``kb_lint.mode`` to ``block`` once report-mode tuning is done.

``kb_lint.path_modes`` sets the mode per path instead: an ordered list of
``{glob, mode, exclude?, exclude_logs?, checks?}`` rules. The FIRST rule that
matches a finding decides its mode; a rule matches when ``glob`` matches the
path, no ``exclude`` glob does, the path is not a log file (``log_globs``)
when ``exclude_logs`` is true, and ``checks`` (if given) names the finding's
check. A path no rule matches gets the global ``mode``. So a narrow rule goes
before a broad one, and an excluded path falls through to the next rule.
Privacy findings block whatever the mode (``scout.kb.lint.PRIVACY_BLOCKING``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from scout.config import load_config

_MODES = frozenset({"report", "block"})
_SCOPE_PREFIXES = ("knowledge-base/", "action-items/")


@lru_cache(maxsize=256)
def _glob_re(glob: str) -> re.Pattern[str]:
    """Translate a path glob to a regex: ``*`` stays within one segment,
    ``**`` crosses segments. (fnmatch's ``*`` crosses ``/``, which would make
    ``projects/*/*.md`` match arbitrarily deep files.)"""
    out: list[str] = []
    i = 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        elif glob[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def glob_match(glob: str, rel: str) -> bool:
    return bool(_glob_re(glob).match(rel))


def _any(globs: tuple[str, ...], rel: str) -> bool:
    return any(glob_match(g, rel) for g in globs)


CONFIG_FILE = "scout-config.yaml"


def in_privacy_scope(rel: str, shared_files: tuple[str, ...]) -> bool:
    """Whether the privacy checks (#453, spec §8.2) look at ``rel``.

    Wider than :meth:`LintConfig.in_scope`: markdown under ``knowledge-base/``
    and ``action-items/`` (exclude globs don't apply: they are size-lint
    tuning), every file matching a ``shared_files`` glob whatever its suffix,
    and ``scout-config.yaml`` (the widening guard watches its ``privacy:``
    block).
    """
    if rel == CONFIG_FILE:
        return True
    if rel.endswith(".md") and rel.startswith(_SCOPE_PREFIXES):
        return True
    return _any(shared_files, rel)


@dataclass(frozen=True)
class Budget:
    glob: str
    bytes: int | None


@dataclass(frozen=True)
class PathMode:
    """One ``kb_lint.path_modes`` rule; see the module docstring for matching."""

    glob: str
    mode: str
    exclude: tuple[str, ...] = ()
    exclude_logs: bool = False
    # Check ids this rule covers (``size``, ``mega-line``, …); empty means every check.
    checks: frozenset[str] = frozenset()


@dataclass(frozen=True)
class LintConfig:
    mode: str
    max_line_chars: int
    max_line_chars_strict: int
    budgets: tuple[Budget, ...]
    log_globs: tuple[str, ...]
    strict_line_globs: tuple[str, ...]
    exclude_globs: tuple[str, ...]
    topic_globs: tuple[str, ...]
    # Dated records `--report` never ranks as shrink targets (past daily
    # action-items files; the legacy session log, migrated by one-time sharding).
    report_exclude_globs: tuple[str, ...] = ()
    path_modes: tuple[PathMode, ...] = ()

    def mode_for(self, rel: str, check: str | None = None) -> str:
        """The mode a finding of ``check`` on ``rel`` is enforced in: the first
        matching ``path_modes`` rule's, else the global ``mode``. ``check`` None
        skips rules that name checks."""
        for r in self.path_modes:
            if r.checks and (check is None or check not in r.checks):
                continue
            if not glob_match(r.glob, rel) or _any(r.exclude, rel):
                continue
            if r.exclude_logs and self.is_log(rel):
                continue
            return r.mode
        return self.mode

    @property
    def mode_label(self) -> str:
        """The mode for display: ``report``/``block``, plus ``per path`` when rules exist."""
        return f"{self.mode}, per path" if self.path_modes else self.mode

    def in_scope(self, rel: str) -> bool:
        return rel.endswith(".md") and rel.startswith(_SCOPE_PREFIXES) and not _any(self.exclude_globs, rel)

    def budget_for(self, rel: str) -> int | None:
        for b in self.budgets:
            if glob_match(b.glob, rel):
                return b.bytes
        return None

    def report_excluded(self, rel: str) -> bool:
        return _any(self.report_exclude_globs, rel)

    def is_log(self, rel: str) -> bool:
        return _any(self.log_globs, rel)

    def is_topic(self, rel: str) -> bool:
        return _any(self.topic_globs, rel)

    def line_limit(self, rel: str) -> int:
        return self.max_line_chars_strict if _any(self.strict_line_globs, rel) else self.max_line_chars


def _bytes(value: Any) -> int | None:
    return None if value is None else int(value)


def _globs(raw: dict[str, Any], key: str) -> tuple[str, ...]:
    return tuple(str(g) for g in (raw.get(key) or []))


def _mode(value: Any) -> str:
    """A mode value; anything but ``report``/``block`` is ``report`` (never block by typo)."""
    mode = str(value) if value is not None else "report"
    return mode if mode in _MODES else "report"


def _str_list(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    return tuple(str(v) for v in value) if isinstance(value, list) else ()


def _path_modes(raw: Any) -> tuple[PathMode, ...]:
    """``kb_lint.path_modes`` in order; an entry with no ``glob`` is skipped."""
    if not isinstance(raw, list):
        return ()
    out: list[PathMode] = []
    for d in raw:
        if not isinstance(d, dict) or not d.get("glob"):
            continue
        out.append(
            PathMode(
                glob=str(d["glob"]),
                mode=_mode(d.get("mode")),
                exclude=_str_list(d.get("exclude")),
                exclude_logs=bool(d.get("exclude_logs", False)),
                checks=frozenset(_str_list(d.get("checks"))),
            )
        )
    return tuple(out)


def load_lint_config(data_dir: Path | None = None) -> LintConfig:
    cfg = load_config(data_dir)
    raw = cfg.get("kb_lint") or {}
    if not isinstance(raw, dict):
        raw = {}
    user_budgets = cfg.get("kb_budgets") or {}
    budgets = [Budget(str(g), _bytes(b)) for g, b in user_budgets.items()] if isinstance(user_budgets, dict) else []
    budgets += [Budget(str(d["glob"]), _bytes(d.get("bytes"))) for d in raw.get("budgets") or []]
    return LintConfig(
        mode=_mode(raw.get("mode", "report")),
        path_modes=_path_modes(raw.get("path_modes")),
        max_line_chars=int(raw.get("max_line_chars", 1500)),
        max_line_chars_strict=int(raw.get("max_line_chars_strict", 500)),
        budgets=tuple(budgets),
        log_globs=_globs(raw, "log_globs"),
        strict_line_globs=_globs(raw, "strict_line_globs"),
        exclude_globs=_globs(raw, "exclude_globs"),
        topic_globs=_globs(raw, "topic_globs"),
        report_exclude_globs=_globs(raw, "report_exclude_globs"),
    )
