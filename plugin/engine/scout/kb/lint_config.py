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
Privacy findings block whatever the mode (:data:`PRIVACY_BLOCKING`).

A ``scout-config.yaml`` that doesn't load at all is the exception: nothing says
which paths its ``kb_lint`` blocked, so every check a mode decides blocks
until the file loads again (:attr:`LintConfig.config_problem`, #567).

A bad mode value means ``report``, never ``block``, so a config mistake can
silently turn enforcement off. :attr:`LintConfig.warnings` names each mistake:
the hook prints them on every commit, and ``kb lint --report`` and the doctor
show them too. A rule whose fields have the wrong type is dropped, because
guessing could widen it (``checks: 5`` reading as "every check"). A dropped rule
or an over-complex glob (:data:`MAX_GLOB_STARS`) in a rule's ``glob`` or
``exclude`` sends the path on to the next rule, which may block: that fails
closed, and the warning says what happened.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from scout.config import config_load_problem, load_config, user_value

_MODES = frozenset({"report", "block"})
_SCOPE_PREFIXES = ("knowledge-base/", "action-items/")

# Checks that report until a `path_modes` rule names them in `checks:`. A vault's
# `mode: block` or a rule with no `checks:` was written before they existed, so
# neither makes them block: every page starts out in debt to the page standard.
REPORT_UNTIL_NAMED = frozenset({"page-loose", "page-sections", "page-narration"})
# Every check id `kb lint` reports. A new check registers its id here, and
# test_every_finding_uses_a_known_check_id fails until it does.
# The blocking checks a mode decides; only these are worth naming in `checks:`.
MODE_CHECKS = frozenset({"size", "mega-line", "diary"}) | REPORT_UNTIL_NAMED
# Block in every mode and ignore SCOUT_LINT_OVERRIDE (#453, spec §8.2/§8.3; scout.kb.lint_privacy).
PRIVACY_BLOCKING = frozenset({"private-link", "private-mention", "privacy-widening", "privacy-check-failed"})
# Warnings: never block, whatever the mode.
WARNING_CHECKS = frozenset(
    {
        "citation",
        "dangling-link",
        "missing-context-note",
        "visibility-value",
        "privacy-contradiction",
        "federation-ceiling",
        "frontmatter-unparseable",
        "privacy-overlay-unreadable",
        "page-config",
    }
)
KNOWN_CHECKS = MODE_CHECKS | PRIVACY_BLOCKING | WARNING_CHECKS

# More `*` than this and a kb-lint glob matches nothing (`_lint_match`). It dates
# from when `glob_match` could backtrack; it can't now, and the cap goes in a
# follow-up to #565. `**/x/**/y/**` is the most a lint glob needs. Privacy and sync
# rules never go through this cap: see `_lint_match`.
MAX_GLOB_STARS = 6


@dataclass(frozen=True)
class _CompiledGlob:
    """A path glob as a set of positions to track at once, one bit per position."""

    prefix: str  # literal text before the first wildcard, checked without tracking anything
    start: int
    accept: int
    steps: dict[str, tuple[int, int]]  # character -> (positions it advances, positions it stays in)
    other_step: tuple[int, int]  # the same, for any character not in ``steps``
    free_moves: int  # positions that also count as later positions, consuming nothing
    move_ends: dict[int, int]  # bit index of such a position -> the last position that reaches


@lru_cache(maxsize=256)
def _compile_glob(glob: str) -> _CompiledGlob:
    """Read a path glob: ``*`` stays within one segment, ``**`` crosses segments.
    (fnmatch's ``*`` crosses ``/``, which would make ``projects/*/*.md`` match
    arbitrarily deep files.)

    The rest is exact and pinned by test_glob_match_differential: ``*`` and ``?``
    match anything but ``/``, newlines included, while ``**`` matches anything but a
    newline; ``**/`` is optional as a whole and otherwise ends on a ``/``; every other
    character, ``[`` included, is itself.
    """
    prefix: list[str] = []
    literals: dict[str, int] = {}
    any_but_slash = in_segment = in_line = after_slash = 0
    moves: dict[int, tuple[int, ...]] = {}
    at = 0  # the position the next token starts from
    i = 0
    while i < len(glob):
        here = 1 << at
        if glob.startswith("**/", i):
            # `at` skips the whole token; `at + 1` is inside it and leaves only on a "/".
            moves[at] = (at + 1, at + 2)
            in_line |= here << 1
            after_slash |= here << 1
            at += 2
            i += 3
        elif glob.startswith("**", i):
            moves[at] = (at + 1,)
            in_line |= here
            at += 1
            i += 2
        elif glob[i] == "*":
            moves[at] = (at + 1,)
            in_segment |= here
            at += 1
            i += 1
        elif glob[i] == "?":
            any_but_slash |= here
            at += 1
            i += 1
        elif at == 0:
            prefix.append(glob[i])
            i += 1
        else:
            literals[glob[i]] = literals.get(glob[i], 0) | here
            at += 1
            i += 1

    def step(char: str) -> tuple[int, int]:
        advance = literals.get(char, 0)
        advance |= after_slash if char == "/" else any_but_slash
        return advance, (0 if char == "/" else in_segment) | (0 if char == "\n" else in_line)

    move_ends: dict[int, int] = {}
    for position in sorted(moves, reverse=True):
        # Moves only go forward and to adjacent positions, so what one reaches is a contiguous run.
        last = moves[position][-1]
        move_ends[position] = move_ends.get(last, last)
    return _CompiledGlob(
        prefix="".join(prefix),
        start=(1 << (move_ends.get(0, 0) + 1)) - 1,
        accept=1 << at,
        steps={char: step(char) for char in (*literals, "/", "\n")},
        other_step=step(""),
        free_moves=sum(1 << position for position in moves),
        move_ends=move_ends,
    )


def _fullmatch(compiled: _CompiledGlob, text: str) -> bool:
    """Whether the glob matches all of ``text``. One pass, whatever the glob: no choice is
    ever retried, because every position still possible is carried forward together."""
    if not text.startswith(compiled.prefix):
        return False
    states = compiled.start
    for char in text[len(compiled.prefix) :]:
        advance, stay = compiled.steps.get(char, compiled.other_step)
        states = ((states & advance) << 1) | (states & stay)
        moving = states & compiled.free_moves
        while moving:
            lowest = moving & -moving
            reached = (1 << (compiled.move_ends[lowest.bit_length() - 1] + 1)) - lowest
            states |= reached
            moving &= ~reached
        if not states:
            return False
    return bool(states & compiled.accept)


def glob_match(glob: str, rel: str) -> bool:
    compiled = _compile_glob(glob)
    # A trailing newline is optional in ``rel``: the regex this replaced ended in ``$``.
    return _fullmatch(compiled, rel) or (rel.endswith("\n") and _fullmatch(compiled, rel[:-1]))


def _any(globs: tuple[str, ...], rel: str) -> bool:
    return any(glob_match(g, rel) for g in globs)


def _lint_match(glob: str, rel: str) -> bool:
    """``glob_match`` for kb-lint's own lists, where an over-complex glob matches nothing.

    Only for these: a lint glob that drops out warns and fails no safety rule.
    A privacy or sync rule that dropped out would quietly stop protecting a
    path, so ``scout.kb.privacy``, ``lint_privacy``, ``repo_sync`` and
    ``kb.read`` call ``glob_match`` directly."""
    return glob.count("*") <= MAX_GLOB_STARS and glob_match(glob, rel)


def lint_any(globs: tuple[str, ...], rel: str) -> bool:
    """Whether any glob matches, with the over-complex ones dropped; see ``_lint_match``. For anything in the hook."""
    return any(_lint_match(g, rel) for g in globs)


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
    # Config mistakes, for the person: what was wrong and what happens instead.
    warnings: tuple[str, ...] = ()
    # Why scout-config.yaml doesn't load (``config_load_problem``); while set, every check blocks.
    config_problem: str | None = None

    def mode_for(self, rel: str, check: str) -> str:
        """The mode a finding of ``check`` on ``rel`` is enforced in: the first
        matching ``path_modes`` rule's, else the global ``mode``. A
        :data:`REPORT_UNTIL_NAMED` check matches only a rule that names it, and
        otherwise reports. ``block`` for every check while the config doesn't load."""
        if self.config_problem is not None:
            return "block"
        named_only = check in REPORT_UNTIL_NAMED
        for r in self.path_modes:
            if (r.checks or named_only) and check not in r.checks:
                continue
            if not _lint_match(r.glob, rel) or lint_any(r.exclude, rel):
                continue
            if r.exclude_logs and self.is_log(rel):
                continue
            return r.mode
        return "report" if named_only else self.mode

    @property
    def mode_label(self) -> str:
        """The mode for display: ``report``/``block``, plus ``per path`` when rules exist."""
        if self.config_problem is not None:
            return f"block, {CONFIG_FILE} unusable"
        return f"{self.mode}, per path" if self.path_modes else self.mode

    def in_scope(self, rel: str) -> bool:
        return rel.endswith(".md") and rel.startswith(_SCOPE_PREFIXES) and not lint_any(self.exclude_globs, rel)

    def budget_for(self, rel: str) -> int | None:
        for b in self.budgets:
            if _lint_match(b.glob, rel):
                return b.bytes
        return None

    def report_excluded(self, rel: str) -> bool:
        return lint_any(self.report_exclude_globs, rel)

    def is_log(self, rel: str) -> bool:
        return lint_any(self.log_globs, rel)

    def is_topic(self, rel: str) -> bool:
        return lint_any(self.topic_globs, rel)

    def line_limit(self, rel: str) -> int:
        return self.max_line_chars_strict if lint_any(self.strict_line_globs, rel) else self.max_line_chars

    def rules_matching_nothing(self, tracked: list[str]) -> list[str]:
        """A warning per ``path_modes`` rule whose glob matches no file kb-lint checks.

        The loader can't see this one: ``Knowledge-Base/**`` is a fine glob
        that enforces nothing, and it reads as a flip that was applied."""
        linted = [rel for rel in tracked if self.in_scope(rel)]
        return [
            f"path_modes rule for {r.glob!r} matches no file kb-lint checks, so it does nothing"
            for r in self.path_modes
            if not any(_lint_match(r.glob, rel) for rel in linted)
        ]


Warn = Callable[[str], None]


def _bytes(value: Any) -> int | None:
    return None if value is None else int(value)


def _globs(raw: dict[str, Any], key: str) -> tuple[str, ...]:
    return tuple(str(g) for g in (raw.get(key) or []))


def _mode(value: Any, where: str, warn: Warn) -> str:
    """A mode value; anything but exactly ``report``/``block`` is ``report`` (never block by typo)."""
    if isinstance(value, str) and value in _MODES:
        return value
    warn(f"{where} has mode {value!r}, not report or block, so it reports and blocks nothing")
    return "report"


def _str_tuple(value: Any) -> tuple[str, ...] | None:
    """A string or a list of strings as a tuple; None for any other shape."""
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return tuple(value)
    return None


_RULE_KEYS = frozenset({"glob", "mode", "exclude", "exclude_logs", "checks"})


def _rule_checks(value: Any, where: str, warn: Warn) -> frozenset[str] | None:
    """A rule's ``checks`` (empty when absent, which means every check); None when
    the rule must go: empty or malformed would otherwise read as every check."""
    if value is None:
        return frozenset()
    checks = _str_tuple(value)
    if not checks:
        warn(f"{where} has checks {value!r}, not a list of check ids, so the rule is dropped")
        return None
    for check in checks:
        if check not in KNOWN_CHECKS:
            warn(f"{where} names unknown check {check!r}, which it can never match; known: {sorted(MODE_CHECKS)}")
        elif check not in MODE_CHECKS:
            warn(f"{where} names {check!r}, which no mode changes: privacy always blocks, warnings never do")
    return frozenset(checks)


def _path_mode(d: Any, where: str, warn: Warn) -> PathMode | None:
    """One ``path_modes`` rule, or None (with a warning) when it can't be read as written."""
    if not isinstance(d, dict):
        warn(f"{where} is {d!r}, not a rule, so it is dropped")
        return None
    unknown = sorted(str(k) for k in d if k not in _RULE_KEYS)
    if unknown:
        warn(f"{where} has unknown key(s) {unknown}, ignored; known: {sorted(_RULE_KEYS)}")
    glob = d.get("glob")
    if not isinstance(glob, str) or not glob:
        warn(f"{where} has glob {glob!r}, not a path glob, so the rule is dropped")
        return None
    where = f"{where} ({glob})"
    if glob.startswith(("/", "./")):
        warn(f"{where} starts with {glob.split('/')[0]}/, but paths are vault-relative, so it can never match")
    raw_exclude = d.get("exclude")
    exclude = () if raw_exclude is None else _str_tuple(raw_exclude)
    if exclude is None:
        warn(f"{where} has exclude {raw_exclude!r}, not a list of globs, so the rule is dropped")
        return None
    exclude_logs = d.get("exclude_logs", False)
    if not isinstance(exclude_logs, bool):
        warn(f"{where} has exclude_logs {exclude_logs!r}, not true or false, so the rule is dropped")
        return None
    checks = _rule_checks(d.get("checks"), where, warn)
    if checks is None:
        return None
    return PathMode(glob, _mode(d.get("mode"), where, warn), exclude, exclude_logs, checks)


def _path_modes(raw: Any, warn: Warn) -> tuple[PathMode, ...]:
    """``kb_lint.path_modes`` in order, minus the rules that can't be read as written."""
    if raw is None:
        return ()
    if not isinstance(raw, list):
        warn(f"path_modes is a {type(raw).__name__}, not a list of rules, so every rule is ignored")
        return ()
    rules = (_path_mode(d, f"path_modes rule {i}", warn) for i, d in enumerate(raw, 1))
    return tuple(r for r in rules if r is not None)


def _complex_globs(cfg: LintConfig, warn: Warn) -> None:
    rule_globs = (g for r in cfg.path_modes for g in (r.glob, *r.exclude))
    lists = (cfg.log_globs, cfg.strict_line_globs, cfg.exclude_globs, cfg.topic_globs, cfg.report_exclude_globs)
    for glob in dict.fromkeys((*rule_globs, *(b.glob for b in cfg.budgets), *(g for gs in lists for g in gs))):
        if glob.count("*") > MAX_GLOB_STARS:
            warn(f"glob {glob!r} has more than {MAX_GLOB_STARS} '*', so it matches nothing")


_KB_LINT_KEYS = frozenset(
    {
        "mode",
        "path_modes",
        "max_line_chars",
        "max_line_chars_strict",
        "budgets",
        "log_globs",
        "strict_line_globs",
        "exclude_globs",
        "topic_globs",
        "report_exclude_globs",
    }
)


def _section_mistakes(cfg: dict[str, Any], data_dir: Path | None, warn: Warn) -> None:
    """A misspelt or misshapen ``kb_lint``: it reads as applied and enforces nothing.

    The shape comes from the vault's own file, because the merge has already
    swapped a non-mapping ``kb_lint`` for the defaults by the time ``cfg`` exists."""
    written = user_value("kb_lint", data_dir)
    if written is not None and not isinstance(written, dict):
        warn(f"kb_lint is a {type(written).__name__}, not a mapping, so it is ignored and the defaults apply")
    for key in cfg:
        if key != "kb_lint" and re.sub(r"[-_\s]", "", str(key)).lower() == "kblint":
            warn(f"top-level key {key!r} isn't kb_lint, so it is ignored")
    raw = cfg.get("kb_lint")
    unknown = sorted(str(k) for k in raw if k not in _KB_LINT_KEYS) if isinstance(raw, dict) else []
    if unknown:
        warn(f"kb_lint has unknown key(s) {unknown}, ignored; known: {sorted(_KB_LINT_KEYS)}")


def _fail_closed(cfg: LintConfig, problem: str) -> LintConfig:
    """``cfg`` (the packaged defaults) with every check blocking, and one line saying why."""
    warning = (
        f"{CONFIG_FILE} could not be used ({problem}), so its kb_lint and kb_budgets settings are not "
        "applied and every size, line, diary and page check blocks until it is fixed"
    )
    return dataclasses.replace(cfg, mode="block", config_problem=problem, warnings=(warning, *cfg.warnings))


def load_lint_config(data_dir: Path | None = None) -> LintConfig:
    problem = config_load_problem(data_dir)
    cfg = load_config(data_dir)
    raw = cfg.get("kb_lint") or {}
    if not isinstance(raw, dict):
        raw = {}
    user_budgets = cfg.get("kb_budgets") or {}
    budgets = [Budget(str(g), _bytes(b)) for g, b in user_budgets.items()] if isinstance(user_budgets, dict) else []
    budgets += [Budget(str(d["glob"]), _bytes(d.get("bytes"))) for d in raw.get("budgets") or []]
    warnings: list[str] = []
    _section_mistakes(cfg, data_dir, warnings.append)
    lint_cfg = LintConfig(
        mode=_mode(raw.get("mode", "report"), "kb_lint", warnings.append),
        path_modes=_path_modes(raw.get("path_modes"), warnings.append),
        max_line_chars=int(raw.get("max_line_chars", 1500)),
        max_line_chars_strict=int(raw.get("max_line_chars_strict", 500)),
        budgets=tuple(budgets),
        log_globs=_globs(raw, "log_globs"),
        strict_line_globs=_globs(raw, "strict_line_globs"),
        exclude_globs=_globs(raw, "exclude_globs"),
        topic_globs=_globs(raw, "topic_globs"),
        report_exclude_globs=_globs(raw, "report_exclude_globs"),
    )
    _complex_globs(lint_cfg, warnings.append)
    lint_cfg = dataclasses.replace(lint_cfg, warnings=tuple(warnings))
    return lint_cfg if problem is None else _fail_closed(lint_cfg, problem)
