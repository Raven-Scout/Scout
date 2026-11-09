"""`scoutctl kb lint` — the pre-commit ratchet that keeps the vault from
growing mega-files.

Blocking (where the path's mode is ``block``; ``kb_lint.mode`` or a
``kb_lint.path_modes`` rule, see ``scout.kb.lint_config``): an over-budget file
that grew, a new file over budget, an added run-diary heading outside log
paths, an added line over the line limit. Warnings: a topic-note paragraph with
no citation, a wikilink that resolves nowhere.

Only ADDED lines are line-checked, and a line that only re-adds a removed one
with a cosmetic edit (checkbox tick, short-id prefix, strike-through) is not
"added". A new daily action-items file is compared against the newest earlier
daily file, not against nothing.

Existing debt must be able to drain, by hand or through the dreaming shrink
pass, so an added line that replaces a line already breaking the same rule
doesn't block either (:class:`_LineDebt`): an in-place edit that doesn't make
the line longer, or a verbatim move into another file of the commit. An in-place
edit is counted, not compared: any line no longer than one the file removes
passes, since it can't raise the file's count of long lines or its longest. Each
removed line excuses one added line, so splitting one long line into two long
ones still blocks, as do growth, a copy, a reworded move and a new line.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, TextIO

from scout import paths
from scout.action_items.context_notes import context_note_rel
from scout.ids import leading_prefix_pattern
from scout.kb.git_io import (
    changed_lines,
    head_files,
    head_size,
    head_text,
    head_text_lossy,
    main_root,
    staged_line_changes,
    staged_paths,
    staged_size,
    staged_text,
    staged_text_lossy,
    tracked_files,
)
from scout.kb.lint_config import PRIVACY_BLOCKING, LintConfig, load_lint_config
from scout.kb.lint_rules import has_citation, is_diary_heading, is_heading, normalize_line, wikilink_targets

if TYPE_CHECKING:
    from scout.kb.structure import PageRules

OVERRIDE_ENV = "SCOUT_LINT_OVERRIDE"


@dataclass(frozen=True)
class Finding:
    path: str
    check: str
    blocking: bool
    message: str
    line: int | None = None


@dataclass
class LintResult:
    findings: list[Finding] = field(default_factory=list)

    @property
    def blocking(self) -> list[Finding]:
        return [f for f in self.findings if f.blocking]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if not f.blocking]


def _kb(n: int) -> str:
    return f"{n / 1024:.1f} KB"


# A front page holds current state only (docs/kb-page-standard.md); its detail has a home to go to first.
FRONT_PAGE_FIX = (
    "move the detail into a note in the project's knowledge/ folder first and link it from the page "
    "(write protocol: split before you exceed), then make the edit"
)
GROWTH_FIX = "move content into topic/source notes (or the log's monthly shard) instead of adding here"


def _check_size(repo: Path, rel: str, head_rel: str | None, cfg: LintConfig, front: bool = False) -> list[Finding]:
    budget = cfg.budget_for(rel)
    if budget is None:
        return []
    new = staged_size(repo, rel)
    if new <= budget:
        return []
    old = head_size(repo, head_rel) if head_rel else None
    if old is None:
        fix = FRONT_PAGE_FIX if front else "split it into smaller linked notes"
        return [Finding(rel, "size", True, f"new file is {_kb(new)}, over its {_kb(budget)} budget — {fix}")]
    if new > old:
        fix = FRONT_PAGE_FIX if front else GROWTH_FIX
        return [
            Finding(
                rel,
                "size",
                True,
                f"{_kb(new)} is over its {_kb(budget)} budget and grew by {_kb(new - old)} — {fix}",
            )
        ]
    return []


@dataclass
class _Debt:
    """A line the commit removes that already broke a line rule where it was."""

    source: str  # the path it was removed from
    rel: str  # the file it counts as an in-place edit of: ``source``, or a rename's new path
    norm: str
    chars: int
    movable: bool = True
    spent: bool = False


class _LineDebt:
    """What the commit removes that may excuse what it adds, one line for one.

    A removed line counts only if it broke the rule in its own file, so a move
    can't launder a line that was fine at its source (a diary heading from a
    log file, a long line from an unlinted file). Built on first use: a commit
    with no over-limit line and no diary heading never pays for the diff."""

    def __init__(self, repo: Path, cfg: LintConfig) -> None:
        self.repo = repo
        self.cfg = cfg
        self._debts: dict[str, list[_Debt]] | None = None

    def _broken(self, source: str, line: str) -> list[str]:
        if not self.cfg.in_scope(source):
            return []
        broken = ["mega-line"] if len(line) > self.cfg.line_limit(source) else []
        if not self.cfg.is_log(source) and is_diary_heading(line):
            broken.append("diary")
        return broken

    def _load(self) -> dict[str, list[_Debt]]:
        if self._debts is None:
            self._debts = {"mega-line": [], "diary": []}
            for change in staged_line_changes(self.repo):
                if change.old_rel is None:
                    continue
                kept = {normalize_line(t) for t in change.added}
                here = change.new_rel or change.old_rel
                for line in change.removed:
                    norm = normalize_line(line)
                    if norm not in kept:
                        for check in self._broken(change.old_rel, line):
                            self._debts[check].append(_Debt(change.old_rel, here, norm, len(line)))
        return self._debts

    def add_baseline(self, rel: str, baseline_text: str, staged: str) -> None:
        """A new daily file's "removed" lines: its baseline's lines it no longer
        carries. They stay in the earlier file, so they excuse edits here, not moves."""
        kept = {normalize_line(t) for t in staged.split("\n")}
        debts = self._load()
        for line in baseline_text.split("\n"):
            norm = normalize_line(line)
            if norm not in kept:
                for check in self._broken(rel, line):
                    debts[check].append(_Debt(rel, rel, norm, len(line), movable=False))

    def _may_move(self, debt: _Debt, rel: str, check: str) -> bool:
        """Debt doesn't cross from a report path into a block path, by a move or by a
        rename: that would add it where it's enforced. (A rename that leaves the
        line alone adds nothing, so it never gets here.)"""
        return self.cfg.mode_for(rel, check) != "block" or self.cfg.mode_for(debt.source, check) == "block"

    def _find(self, rel: str, check: str, text: str, debts: list[_Debt]) -> _Debt | None:
        # In place: the longest debt left that's no shorter. A diary heading has no length to grow.
        needed = len(text) if check == "mega-line" else 0
        own = [d for d in debts if not d.spent and d.rel == rel and d.chars >= needed and self._may_move(d, rel, check)]
        if own:
            return max(own, key=lambda d: d.chars)
        norm = normalize_line(text)
        for d in debts:
            if not d.spent and d.movable and d.rel != rel and d.norm == norm and self._may_move(d, rel, check):
                return d
        return None

    def excused(self, rel: str, check: str, lines: list[tuple[int, str]]) -> set[int]:
        """The line numbers of ``lines`` (added to ``rel``, breaking ``check``) that only replace debt."""
        if not lines:
            return set()
        debts = self._load()[check]
        paid: set[int] = set()
        # Longest first, so each debt goes to the longest line it can excuse.
        for n, text in sorted(lines, key=lambda line: -len(line[1])):
            debt = self._find(rel, check, text, debts)
            if debt is not None:
                debt.spent = True
                paid.add(n)
        return paid


def _check_lines(
    repo: Path,
    rel: str,
    added: list[tuple[int, str]],
    cfg: LintConfig,
    debts: _LineDebt,
    baseline_text: str | None = None,
) -> list[Finding]:
    limit = cfg.line_limit(rel)
    diary_scope = not cfg.is_log(rel)
    over = [(n, text) for n, text in added if len(text) > limit]
    diary = [(n, text) for n, text in added if diary_scope and is_diary_heading(text)]
    if baseline_text is not None and (over or diary):
        debts.add_baseline(rel, baseline_text, staged_text(repo, rel))
    excused_over = debts.excused(rel, "mega-line", over)
    excused_diary = debts.excused(rel, "diary", diary)
    out: list[Finding] = []
    for n, text in added:
        if len(text) > limit and n not in excused_over:
            out.append(
                Finding(
                    rel,
                    "mega-line",
                    True,
                    f"line {n} is {len(text)} chars (limit {limit}) — put the detail in a linked note",
                    n,
                )
            )
        if diary_scope and is_diary_heading(text) and n not in excused_diary:
            out.append(
                Finding(
                    rel,
                    "diary",
                    True,
                    f"line {n} adds a run-diary heading {text[:100]!r} — edit the current state in place; "
                    "the commit message carries what this run did",
                    n,
                )
            )
    return out


def _paragraphs(text: str) -> list[tuple[int, int, str]]:
    """(first_line, last_line, text) for prose paragraphs, skipping frontmatter,
    fenced code, headings and tables. Line numbers are 1-based."""
    lines = text.splitlines()
    i = 0
    if lines and lines[0].strip() == "---":
        i = 1
        while i < len(lines) and lines[i].strip() != "---":
            i += 1
        i += 1
    out: list[tuple[int, int, str]] = []
    buf: list[str] = []
    start = 0
    in_fence = False

    def flush(end: int) -> None:
        if buf:
            out.append((start, end, "\n".join(buf)))
            buf.clear()

    for idx in range(i, len(lines)):
        ln = lines[idx]
        n = idx + 1
        if ln.lstrip().startswith(("```", "~~~")):
            flush(n - 1)
            in_fence = not in_fence
            continue
        if in_fence or not ln.strip() or is_heading(ln) or ln.lstrip().startswith("|"):
            flush(n - 1)
            continue
        if not buf:
            start = n
        buf.append(ln)
    flush(len(lines))
    return out


def _check_citations(rel: str, text: str, added_nums: set[int]) -> list[Finding]:
    out: list[Finding] = []
    for first, last, para in _paragraphs(text):
        if any(first <= n <= last for n in added_nums) and not has_citation(para):
            out.append(
                Finding(
                    rel,
                    "citation",
                    False,
                    f"paragraph at line {first} has no [[source]] or URL citation",
                    first,
                )
            )
    return out


_FM_ALIASES_RE = re.compile(r"^aliases:[ \t]*(.*?)\s*$")
_FM_LIST_ITEM_RE = re.compile(r"^\s*-\s+(.*?)\s*$")
_FM_INLINE_ITEM_RE = re.compile(r"""\s*(?:"([^"]*)"|'([^']*)'|([^,]*))\s*(?:,|$)""")
_FM_NON_NAMES = {"", "null", "~", "|", ">", "[]"}


def _fm_scalar(raw: str) -> str:
    """One YAML scalar: quotes stripped, or an unquoted value's ` #` comment."""
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1].strip()
    return raw.split(" #", 1)[0].strip()


def _frontmatter_aliases(path: Path) -> list[str]:
    """The `aliases:` a file's YAML frontmatter declares.

    Obsidian resolves a wikilink to a filename or to one of the target's
    `aliases`, and to nothing else in frontmatter — `name:` and `title:` are
    not link targets there, so indexing them would silence links that are
    broken for the reader. All three forms Obsidian reads or writes are
    handled: a scalar, an inline list, and a block list (what its Properties
    editor saves).

    Deliberately a line scanner, not a YAML parse: lint runs over every tracked
    file on every commit, frontmatter is the first few lines, and a malformed
    document must not raise.
    """
    names: list[str] = []
    try:
        with path.open(encoding="utf-8-sig", errors="replace") as fh:
            if fh.readline().strip() != "---":
                return names
            in_block = False
            for _ in range(60):
                line = fh.readline()
                if not line or line.strip() == "---":
                    break
                if in_block:
                    item = _FM_LIST_ITEM_RE.match(line)
                    if item:
                        names.append(_fm_scalar(item.group(1)))
                        continue
                    in_block = False
                m = _FM_ALIASES_RE.match(line)
                if not m:
                    continue
                val = m.group(1)
                if not val or val.startswith("#"):  # block list follows
                    in_block = True
                elif val.startswith("[") and val.endswith("]"):  # inline list
                    for dq, sq, bare in _FM_INLINE_ITEM_RE.findall(val[1:-1]):
                        names.append(dq or sq or bare.strip())
                else:
                    names.append(_fm_scalar(val))
    except OSError:
        return names
    return [n for n in names if n.lower() not in _FM_NON_NAMES]


def _link_targets(repo: Path) -> set[str]:
    known: set[str] = set()
    for p in tracked_files(repo):
        pp = PurePosixPath(p)
        for form in (p, str(pp.with_suffix("")), pp.name, pp.stem):
            known.add(form.lower())
        if p.startswith("knowledge-base/"):
            kbrel = PurePosixPath(p[len("knowledge-base/") :])
            known.add(str(kbrel).lower())
            known.add(str(kbrel.with_suffix("")).lower())
        if p.endswith(".md"):
            for nm in _frontmatter_aliases(repo / p):
                known.add(nm.lower())
    return known


def _check_links(rel: str, added: list[tuple[int, str]], known: set[str]) -> list[Finding]:
    out: list[Finding] = []
    for n, text in added:
        for target in wikilink_targets(text):
            t = target.lower()
            if t not in known and PurePosixPath(t).name not in known:
                out.append(Finding(rel, "dangling-link", False, f"line {n}: [[{target}]] resolves to no file", n))
    return out


_DAILY = re.compile(r"^action-items/action-items-(\d{4}-\d{2}-\d{2})\.md$")


def _daily_baseline(repo: Path, rel: str) -> str | None:
    """For a daily action-items file, the newest EARLIER daily file at HEAD.

    Today's file is new at HEAD, so without a baseline every carried-forward
    line would count as added and the size check would treat it as a
    brand-new file — re-checking existing debt every day."""
    m = _DAILY.match(rel)
    if not m:
        return None
    earlier = [p for p in head_files(repo, "action-items") if (d := _DAILY.match(p)) and d.group(1) < m.group(1)]
    return max(earlier) if earlier else None


def _new_lines(repo: Path, rel: str, head_rel: str | None, baseline_text: str | None) -> list[tuple[int, str]]:
    """The added lines that are really new, for the line checks.

    With a baseline (a new daily file), a line is new when its normalized form
    is not among the baseline's lines. Otherwise it comes from the staged diff,
    minus lines that only re-add a removed line with a cosmetic change
    (checkbox tick, ``[#XXXX]`` prefix, strike-through) — see
    ``normalize_line``."""
    if baseline_text is not None:
        seen = {normalize_line(ln) for ln in baseline_text.split("\n")}
        lines = staged_text(repo, rel).split("\n")
        return [(n, t) for n, t in enumerate(lines, 1) if t.strip() and normalize_line(t) not in seen]
    added, removed = changed_lines(repo, rel, head_rel)
    gone = {normalize_line(ln) for ln in removed}
    return [(n, t) for n, t in added if normalize_line(t) not in gone]


_OPEN_TASK = re.compile(r"^\s*- \[ \] (.*)$")


def _check_context_notes(repo: Path, rel: str, added: list[tuple[int, str]], staged: set[str]) -> list[Finding]:
    """Warn (never block) when a newly added open item in a daily file has no
    `action-items/context/<TAG>.md` note in the working tree or staged set."""
    if not _DAILY.match(rel):
        return []
    out: list[Finding] = []
    for n, text in added:
        m = _OPEN_TASK.match(text)
        tm = leading_prefix_pattern().match(m.group(1)) if m else None
        if tm is None:
            continue
        note = context_note_rel(tm.group(1))
        if note in staged or (repo / note).exists():
            continue
        out.append(Finding(rel, "missing-context-note", False, f"line {n}: [#{tm.group(1)}] has no {note}", n))
    return out


@dataclass
class StagedFile:
    """What a staged-diff check sees for one in-scope file."""

    repo: Path
    cfg: LintConfig
    rel: str
    # The HEAD path the size check compares against (a daily file's baseline when new).
    size_base: str | None
    # The really-new lines (``_new_lines``), 1-based.
    added: list[tuple[int, str]]
    staged_rels: set[str]
    # Every wikilink target in the vault, built once per lint on first use.
    known_targets: Callable[[], set[str]]
    # The lines the commit removes, shared across its files (one line excuses one).
    debts: _LineDebt
    # The page checks' settings, loaded once per lint on first use; None when unusable.
    page_rules: Callable[[], PageRules | None]
    # A new daily file's earlier daily file; its lines count as this file's old version.
    baseline_text: str | None = None


StagedCheck = Callable[[StagedFile], list[Finding]]


def _size(f: StagedFile) -> list[Finding]:
    return _check_size(f.repo, f.rel, f.size_base, f.cfg, front=_is_front(f))


def _lines(f: StagedFile) -> list[Finding]:
    return _check_lines(f.repo, f.rel, f.added, f.cfg, f.debts, f.baseline_text)


def _context_notes(f: StagedFile) -> list[Finding]:
    return _check_context_notes(f.repo, f.rel, f.added, f.staged_rels)


def _citations(f: StagedFile) -> list[Finding]:
    if not f.cfg.is_topic(f.rel):
        return []
    return _check_citations(f.rel, staged_text(f.repo, f.rel), {n for n, _ in f.added})


def _links(f: StagedFile) -> list[Finding]:
    return _check_links(f.rel, f.added, f.known_targets())


# The page standard's per-file checks. Like the rest of this ratchet they name only
# what the commit makes worse, so a page already in debt can still be edited;
# `scoutctl kb structure` reports the debt itself.

# A rewrite can add hundreds of narrated lines or long entries, and a run reads every finding.
PAGE_NARRATION_SHOWN = 10


def _page_kind(rules: PageRules, rel: str) -> str | None:
    from scout.errors import KBError

    try:
        return rules.kind(rel)
    except KBError:  # a malformed schema: `kb validate` names it, and the page checks step aside
        return None


def _page_private(f: StagedFile) -> bool:
    """Whether the staged page is private, so its findings carry only paths, line numbers and counts."""
    from scout.kb.privacy import classify, load_policy

    return classify(f.rel, staged_text_lossy(f.repo, f.rel), load_policy(f.repo)).visibility == "private"


def _page_rules(f: StagedFile) -> PageRules | None:
    # Every project path has at least <dir>/<name>/<file>, so a shallower file loads nothing.
    return f.page_rules() if f.rel.count("/") >= 2 else None


def _is_front(f: StagedFile) -> bool:
    rules = _page_rules(f)
    return rules is not None and _page_kind(rules, f.rel) == "front"


def _page_loose(f: StagedFile) -> list[Finding]:
    rules = _page_rules(f)
    if rules is None or _page_kind(rules, f.rel) != "loose":
        return []
    parent = PurePosixPath(f.rel).parent
    if f.size_base is not None and _page_kind(rules, f.size_base) == "loose":
        old_parent = PurePosixPath(f.size_base).parent
        if old_parent == parent or not (f.repo / old_parent / f"{old_parent.name}.md").is_file():
            return []  # an edit, a rename in the folder, or the whole project renamed: no new debt
    front = str(parent / f"{parent.name}.md")
    if front not in f.staged_rels and not (f.repo / front).is_file():
        return []  # no front page, so `kb structure` doesn't count this folder as a project
    return [
        Finding(
            f.rel,
            "page-loose",
            True,
            "new file beside the project's front page — file it under one of "
            + ", ".join(f"{d}/" for d in sorted(rules.cfg.allowed_dirs)),
        )
    ]


def _page_sections(f: StagedFile) -> list[Finding]:
    from scout.kb.structure import missing_sections, split_page

    rules = _page_rules(f)
    if rules is None or _page_kind(rules, f.rel) != "front":
        return []
    required = rules.cfg.required_sections
    missing = missing_sections(split_page(staged_text_lossy(f.repo, f.rel))[1], required)
    # A note renamed into the front-page slot is a new front page: its old text isn't a baseline.
    was_front = f.size_base is not None and _page_kind(rules, f.size_base) == "front"
    old = head_text_lossy(f.repo, f.size_base) if f.size_base and was_front else None
    if old is not None:
        already = set(missing_sections(split_page(old)[1], required))
        missing = [s for s in missing if s not in already]
    if not missing:
        return []
    verb = "new front page lacks" if old is None else "commit removes"
    what = f"{len(missing)} required section(s)" if _page_private(f) else "required section(s) " + ", ".join(missing)
    return [Finding(f.rel, "page-sections", True, f"{verb} {what} — see docs/kb-page-standard.md")]


def _page_narration(f: StagedFile) -> list[Finding]:
    from scout.kb.frontmatter import frontmatter_block
    from scout.kb.structure import narration_found

    rules = _page_rules(f)
    if rules is None or _page_kind(rules, f.rel) != "front":
        return []
    markers = rules.cfg.narration_markers
    hits = [(n, labels) for n, t in f.added if (labels := narration_found(t, markers))]
    if not hits:
        return []
    lines = staged_text_lossy(f.repo, f.rel).split("\n")
    block = frontmatter_block(lines)
    body_starts = 0 if block is None else len(block) + 2  # frontmatter is where provenance belongs
    # Each marker the commit removes from this page excuses one added line with it, so
    # fixing a typo on a narrated line is not new debt. A line needs all its markers covered.
    spare = Counter(
        label for line in changed_lines(f.repo, f.rel, f.size_base)[1] for label in narration_found(line, markers)
    )
    new = []
    for n, labels in hits:
        if n <= body_starts:
            continue
        if all(spare[label] > 0 for label in labels):
            spare.subtract(labels)
        else:
            new.append((n, labels))
    private = bool(new) and _page_private(f)
    out = []
    for n, labels in new[:PAGE_NARRATION_SHOWN]:
        what = "run narration" if private else "run narration (" + ", ".join(labels) + ")"
        out.append(Finding(f.rel, "page-narration", True, f"line {n} adds {what} — keep it in the commit message", n))
    if len(new) > PAGE_NARRATION_SHOWN:
        out.append(Finding(f.rel, "page-narration", True, f"and {len(new) - PAGE_NARRATION_SHOWN} more narrated lines"))
    return out


def _page_bullets(f: StagedFile) -> list[Finding]:
    """A long entry the commit adds to "Where it stands": how a rebuilt page turns back into paragraphs.

    Like ``mega-line``, an entry the commit removes that was already too long excuses one
    added entry no longer than it, so shortening or fixing a long bullet is not new debt."""
    from scout.kb.structure import section_entries

    rules = _page_rules(f)
    if rules is None or _page_kind(rules, f.rel) != "front":
        return []
    limit, sections = rules.cfg.max_bullet_chars, rules.cfg.bullet_sections
    added = {n for n, _ in f.added}
    entries = section_entries(staged_text_lossy(f.repo, f.rel), sections)
    long = [(first, len(t)) for first, last, t in entries if len(t) > limit and any(first <= n <= last for n in added)]
    if not long:
        return []
    was_front = f.size_base is not None and _page_kind(rules, f.size_base) == "front"
    old = head_text_lossy(f.repo, f.size_base) if f.size_base and was_front else None
    kept = {t for _, _, t in entries}
    spare = sorted(
        (len(t) for _, _, t in section_entries(old or "", sections) if len(t) > limit and t not in kept), reverse=True
    )
    new = []
    # Longest first, each taking the longest debt left: that excuses as many as any order could.
    for first, chars in sorted(long, key=lambda e: -e[1]):
        if spare and spare[0] >= chars:
            spare.pop(0)
        else:
            new.append((first, chars))
    new.sort()
    where = "" if not new or _page_private(f) else " in " + " / ".join(f'"{s}"' for s in sections)
    out = [
        Finding(
            f.rel,
            "page-bullet",
            True,
            f"line {n} adds a {chars}-char entry{where} (limit {limit}) — move the detail into a note in "
            "the project's knowledge/ folder first and link it, keeping one line of current state here "
            "(write protocol: split before you exceed)",
            n,
        )
        for n, chars in new[:PAGE_NARRATION_SHOWN]
    ]
    if len(new) > PAGE_NARRATION_SHOWN:
        out.append(Finding(f.rel, "page-bullet", True, f"and {len(new) - PAGE_NARRATION_SHOWN} more long entries"))
    return out


# The checks `lint_staged` runs on each in-scope staged file, in output order.
# A new check is one function here, and its id goes in `lint_config.KNOWN_CHECKS`.
# It reports `Finding(blocking=True)` for what should refuse a commit, and the
# path's mode (`LintConfig.mode_for`, which a `path_modes` rule can scope to
# named checks) decides whether it does.
STAGED_CHECKS: tuple[StagedCheck, ...] = (
    _size,
    _lines,
    _context_notes,
    _citations,
    _links,
    _page_loose,
    _page_sections,
    _page_narration,
    _page_bullets,
)


def lint_staged(repo: Path, cfg: LintConfig) -> LintResult:
    result = LintResult()
    known: set[str] | None = None
    debts = _LineDebt(repo, cfg)

    def known_targets() -> set[str]:
        nonlocal known
        if known is None:
            known = _link_targets(repo)
        return known

    rules: PageRules | None = None
    rules_loaded = False

    def page_rules() -> PageRules | None:
        """A bad `kb_structure` costs the page checks, not the size and line lint: it warns once."""
        nonlocal rules, rules_loaded
        if not rules_loaded:
            from scout.errors import KBError
            from scout.kb.structure import load_page_rules

            rules_loaded = True
            try:
                rules = load_page_rules(repo, cfg.exclude_globs)
            except KBError as e:
                result.findings.append(Finding("scout-config.yaml", "page-config", False, f"{e}; page checks skipped"))
        return rules

    staged_list = staged_paths(repo)
    staged_rels = {sp.rel for sp in staged_list}
    # Block paths claim debt first, or a copy on a report path could spend what a
    # move on a block path needs. Stable, so one mode everywhere keeps git's order.
    claim_order = sorted(
        staged_list,
        key=lambda sp: "block" not in (cfg.mode_for(sp.rel, "mega-line"), cfg.mode_for(sp.rel, "diary")),
    )
    for sp in claim_order:
        if not cfg.in_scope(sp.rel):
            continue
        size_base = sp.head_rel
        baseline_text: str | None = None
        if sp.head_rel is None:
            baseline = _daily_baseline(repo, sp.rel)
            if baseline is not None:
                size_base = baseline
                baseline_text = head_text(repo, baseline)
        added = _new_lines(repo, sp.rel, sp.head_rel, baseline_text)
        staged = StagedFile(
            repo, cfg, sp.rel, size_base, added, staged_rels, known_targets, debts, page_rules, baseline_text
        )
        for check in STAGED_CHECKS:
            result.findings += check(staged)
    return result


def over_budget(repo: Path, cfg: LintConfig) -> list[tuple[str, int, int]]:
    rows: list[tuple[str, int, int]] = []
    for rel in tracked_files(repo):
        if not cfg.in_scope(rel) or cfg.report_excluded(rel):
            continue
        budget = cfg.budget_for(rel)
        path = repo / rel
        if budget is None or not path.is_file():
            continue
        size = path.stat().st_size
        if size > budget:
            rows.append((rel, size, budget))
    rows.sort(key=lambda r: r[1] - r[2], reverse=True)
    return rows


def config_problems(repo: Path, cfg: LintConfig) -> list[str]:
    """What's wrong with the lint config, for ``kb lint --report`` and the doctor.

    The commit hook prints only ``cfg.warnings``: this also lists the tracked
    files, which a commit shouldn't pay for."""
    problems = list(cfg.warnings)
    if cfg.path_modes:
        try:
            problems += cfg.rules_matching_nothing(tracked_files(repo))
        except (OSError, subprocess.SubprocessError):
            pass  # not a git checkout, so there's no file list to check the rules against
    return problems


def _hard(f: Finding) -> bool:
    return f.blocking and f.check in PRIVACY_BLOCKING


def enforced(f: Finding, cfg: LintConfig | None) -> bool:
    """Whether ``f`` refuses the commit: a privacy block always; any other
    blocking finding only where its path's mode is ``block``. Without a config
    (loading it crashed) the size/diary lint fails open; a scout-config.yaml that
    doesn't parse is not that case: it loads as every check blocking."""
    if _hard(f):
        return True
    return f.blocking and cfg is not None and cfg.mode_for(f.path, f.check) == "block"


def format_findings(result: LintResult, cfg: LintConfig | None) -> str:
    mode = cfg.mode_label if cfg is not None else "?"
    # Global `block` with no rules still has report findings: REPORT_UNTIL_NAMED's.
    per_path = cfg is not None and (
        bool(cfg.path_modes) or (cfg.mode == "block" and not all(enforced(f, cfg) for f in result.blocking))
    )
    count = f"{len(result.blocking)} blocking"
    if per_path:
        count += f" ({sum(enforced(f, cfg) for f in result.blocking)} enforced)"
    lines = [f"kb-lint: {count}, {len(result.warnings)} warning(s) (mode: {mode})"]
    for f in result.findings:
        mark = "✗" if f.blocking else "!"
        where = f"{f.path}:{f.line}" if f.line else f.path
        tail = " (report)" if per_path and f.blocking and not enforced(f, cfg) else ""
        lines.append(f"  {mark} {where} [{f.check}]{tail} {f.message}")
    soft = [f for f in result.blocking if not _hard(f)]
    blocked = [f for f in soft if enforced(f, cfg)]
    if any(_hard(f) for f in result.blocking):
        lines.append(
            "Commit blocked by the privacy checks: they block in every lint mode, and "
            f"{OVERRIDE_ENV} does not apply to them."
        )
    if blocked:
        lines.append(
            "Commit blocked. Fix the files above — or, only after two failed attempts, re-run with "
            f'{OVERRIDE_ENV}="<reason>" (logged; the next dreaming run must clean it up).'
        )
        if len(blocked) < len(soft):
            lines.append("(findings marked (report) are in report-mode paths — logged, not blocking)")
    elif soft:
        lines.append("(report mode — not blocking; findings logged to .scout-logs/kb-lint.jsonl)")
    return "\n".join(lines)


def _append_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


PRIVACY_UNAVAILABLE = "kb-lint: privacy check could not run"


def is_scheduled(env: Mapping[str, str]) -> bool:
    """Whether a commit counts as a scheduled run's: :func:`scout.kb.reader.is_scheduled` (ruling R27)."""
    from scout.kb.reader import is_scheduled as _is_scheduled

    return _is_scheduled(env)


def _with_live_run(repo: Path, env: Mapping[str, str], out: TextIO) -> Mapping[str, str]:
    """``env``, or a scheduled run's when the commit comes from a live run whatever ``env`` says (#595).

    A run can unset or blank ``SCOUT_MODE`` for one ``git commit``, but not the
    processes it runs in (:mod:`scout.kb.live_run`): a commit whose ancestry
    passes through the run, or one detached from it while it is live, is
    checked as the run's, and the event ``kb_lint.scheduled_by_process`` leaves
    a trace. The owner's commit from their own terminal or app during a run
    keeps the owner's rules, with a one-line notice. A run that died leaving
    its lock counts until that lock is cleared, so its line says how: it can only
    reach an owner, or a run that has already killed its own parents, and the
    event is written first.
    """
    if is_scheduled(env):
        return env
    from scout.kb.live_run import commit_source_now

    root = main_root(repo)
    src = commit_source_now(root)
    if src is None:
        return env
    if not src.runs:
        if src.dead_holder:
            _say(
                out,
                f"kb-lint: a scheduled run ({src.mode}) died and left its session lock; this commit comes from your "
                "own session, so your rules apply. The next run clears it, or delete .scout-logs/.scout-session.lock.",
            )
        else:
            _say(
                out,
                "kb-lint: a scheduled run is in progress; this commit comes from your own session, "
                "so your rules apply.",
            )
        return env
    if src.dead_holder:
        _say(
            out,
            f"kb-lint: a scheduled run ({src.mode}) died and left its session lock, so this commit gets the "
            "scheduled-run checks. If it is your own, the next run clears it, or delete "
            ".scout-logs/.scout-session.lock and commit again.",
        )
    else:
        _say(out, f"kb-lint: this commit comes from a scheduled run ({src.mode}), so the scheduled-run checks apply.")
    try:
        from scout.events import append_event

        append_event(
            paths.logs_dir(root),
            kind="kb_lint.scheduled_by_process",
            source="hook:kb-lint",
            payload={"mode": src.mode, "by": src.by},
        )
    except Exception:  # noqa: BLE001, S110 - the verdict stands without the event
        pass
    return {**env, "SCOUT_MODE": src.mode, "SCOUT_READER": "autonomous"}


def _privacy_failed(repo: Path, env: Mapping[str, str], err: Exception) -> Finding:
    """A scheduled run's refusal when the privacy checker errored (spec §8.3).

    Names the error class only: an exception's text can quote file content (a
    YAML error does). Also writes the run-health event ``privacy.check_failed``;
    failing to write it doesn't lift the refusal.
    """
    name = type(err).__name__
    try:
        from scout.events import append_event

        append_event(
            paths.logs_dir(main_root(repo)),
            kind="privacy.check_failed",
            source="hook:kb-lint",
            payload={"error": name, "mode": env.get("SCOUT_MODE") or env.get("SCOUT_READER")},
        )
    except Exception:  # noqa: BLE001, S110 - the refusal stands without the event
        pass
    return Finding(
        "(privacy)",
        "privacy-check-failed",
        True,
        f"the privacy check failed ({name}), so the commit is refused (run-health event privacy.check_failed)",
    )


def privacy_unavailable_warning(reason: str) -> str:
    return (
        f"kb-lint: WARNING: the privacy check could not run ({reason}); this commit was NOT checked "
        "for private links in shared files. Run scoutctl bootstrap doctor."
    )


def _privacy_unavailable(repo: Path, out: TextIO, reason: str) -> None:
    """The owner's interactive commit goes on unchecked: a loud warning and an audit row (Jordan, 2026-10-22)."""
    _say(out, PRIVACY_UNAVAILABLE)
    _say(out, privacy_unavailable_warning(reason))
    try:
        from scout.kb.read_log import log_event

        log_event(
            main_root(repo),
            reader="owner",
            command="kb lint --staged",
            surface=None,
            args=None,
            returned=0,
            denied=0,
            private_paths=0,
            blocked=False,
            event="privacy.check_unavailable",
        )
    except Exception:  # noqa: BLE001, S110 - the audit row never fails the commit
        pass


def _privacy_pass(repo: Path, env: Mapping[str, str], out: TextIO) -> list[Finding]:
    """``scout.kb.lint_privacy`` over the staged changes.

    If it errors, a scheduled run's commit gets a blocking finding (fail
    closed); the owner's interactive commit gets a warning and an audit row,
    and no finding (ruling, Jordan 2026-10-22).
    """
    try:
        from scout.kb.lint_privacy import privacy_staged

        return privacy_staged(repo, env=env)
    except Exception as e:  # noqa: BLE001 - spec §8.3
        if is_scheduled(env):
            _say(out, PRIVACY_UNAVAILABLE)
            return [_privacy_failed(repo, env, e)]
        _privacy_unavailable(repo, out, type(e).__name__)
        return []


def run_hook(
    repo: Path,
    *,
    env: Mapping[str, str] | None = None,
    now: datetime | None = None,
    out: TextIO | None = None,
) -> int:
    env = os.environ if env is None else env
    out = out or sys.stderr
    env = _with_live_run(repo, env, out)
    # The privacy pass runs first and on its own: its verdict stands whatever
    # happens below. If it can't run, a scheduled run's commit is refused and
    # the owner's goes on with a warning and an audit row (spec §8.3; Jordan,
    # 2026-10-22). The size/diary lint and all the bookkeeping fail OPEN, as
    # the hook always has: a crash there is reported and never blocks.
    privacy = _privacy_pass(repo, env, out)
    hard = any(_hard(f) for f in privacy)
    cfg: LintConfig | None = None
    result = LintResult()
    try:
        cfg = load_lint_config(repo)
        for warning in cfg.warnings:
            _say(out, f"kb-lint: config: {warning}")
        result = lint_staged(repo, cfg)
    except Exception as e:  # noqa: BLE001 - fail open (spec §8.3)
        cfg, result = None, LintResult()
        _say(out, f"kb-lint: size/diary check failed ({type(e).__name__}) — not blocking")
    result.findings[:0] = privacy
    if not result.findings:
        return 0
    ts = (now or datetime.now(UTC)).isoformat()
    run_mode = env.get("SCOUT_MODE", "interactive")
    logs: Path | None = None
    try:
        logs = paths.logs_dir(main_root(repo))
        # lint_mode is the finding's own path mode; enforced, whether it refused the commit.
        _append_jsonl(
            logs / "kb-lint.jsonl",
            [
                {
                    "ts": ts,
                    "mode": run_mode,
                    "lint_mode": cfg.mode_for(f.path, f.check) if cfg else None,
                    "enforced": enforced(f, cfg),
                    **asdict(f),
                }
                for f in result.findings
            ],
        )
    except Exception as e:  # noqa: BLE001 - bookkeeping never decides a verdict
        _say(out, f"kb-lint: could not write .scout-logs/kb-lint.jsonl ({type(e).__name__})")
    try:
        print(format_findings(result, cfg), file=out)
    except Exception as e:  # noqa: BLE001
        _say(out, f"kb-lint: could not format the findings ({type(e).__name__})")
        if hard:
            _say(out, _privacy_summary(result.findings))
    if hard:
        return 1
    try:
        blocked = [f for f in result.blocking if enforced(f, cfg)]
    except Exception as e:  # noqa: BLE001 - fail open (spec §8.3); the privacy verdict is already in
        _say(out, f"kb-lint: size/diary check failed ({type(e).__name__}) — not blocking")
        return 0
    if not blocked:
        return 0
    reason = env.get(OVERRIDE_ENV, "").strip()
    if reason:
        files = sorted({f.path for f in blocked})
        try:
            if logs is None:
                raise OSError("no logs directory")
            _append_jsonl(logs / "lint-overrides.log", [{"ts": ts, "mode": run_mode, "files": files, "reason": reason}])
        except Exception as e:  # noqa: BLE001
            _say(out, f"kb-lint: could not log the override ({type(e).__name__})")
        _say(out, f"kb-lint: override accepted ({reason}) — logged to .scout-logs/lint-overrides.log")
        return 0
    return 1


def _say(out: TextIO, line: str) -> None:
    """Print one line; a broken stream never changes the hook's verdict."""
    try:
        print(line, file=out)
    except Exception:  # noqa: BLE001, S110
        pass


def _privacy_summary(findings: list[Finding]) -> str:
    """``kb-lint: privacy: <rule ids> at <file>:<lines>; …`` for the hard findings (no content)."""
    by_file: dict[str, tuple[set[str], set[int]]] = {}
    for f in findings:
        if _hard(f):
            rules, lines = by_file.setdefault(f.path, (set(), set()))
            rules.add(f.check)
            if f.line:
                lines.add(f.line)
    parts = []
    for path, (rules, lines) in sorted(by_file.items()):
        where = f"{path}:{','.join(str(n) for n in sorted(lines))}" if lines else path
        parts.append(f"{','.join(sorted(rules))} at {where}")
    return "kb-lint: privacy: " + "; ".join(parts)


def override_count(logs_dir: Path, *, days: int, now: datetime) -> int:
    log = logs_dir / "lint-overrides.log"
    if not log.exists():
        return 0
    cutoff = now - timedelta(days=days)
    n = 0
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            ts = datetime.fromisoformat(json.loads(line)["ts"])
        except (ValueError, KeyError, TypeError):
            continue
        if ts >= cutoff:
            n += 1
    return n
