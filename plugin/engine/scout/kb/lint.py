"""`scoutctl kb lint` — the pre-commit ratchet that keeps the vault from
growing mega-files.

Blocking (in ``block`` mode): an over-budget file that grew, a new file over
budget, an added run-diary heading outside log paths, an added line over the
line limit. Warnings: a topic-note paragraph with no citation, a wikilink that
resolves nowhere. Only ADDED lines are line-checked, so existing debt never
blocks a commit; it drains through the dreaming shrink pass instead. A line
that only re-adds a removed one with a cosmetic edit (checkbox tick, short-id
prefix, strike-through) is not "added", and a new daily action-items file is
compared against the newest earlier daily file, not against nothing.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import TextIO

from scout import paths
from scout.action_items.context_notes import context_note_rel
from scout.ids import leading_prefix_pattern
from scout.kb.git_io import (
    changed_lines,
    head_files,
    head_size,
    head_text,
    main_root,
    staged_paths,
    staged_size,
    staged_text,
    tracked_files,
)
from scout.kb.lint_config import LintConfig, load_lint_config
from scout.kb.lint_rules import has_citation, is_diary_heading, is_heading, normalize_line, wikilink_targets

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


def _check_size(repo: Path, rel: str, head_rel: str | None, cfg: LintConfig) -> list[Finding]:
    budget = cfg.budget_for(rel)
    if budget is None:
        return []
    new = staged_size(repo, rel)
    if new <= budget:
        return []
    old = head_size(repo, head_rel) if head_rel else None
    if old is None:
        return [
            Finding(
                rel,
                "size",
                True,
                f"new file is {_kb(new)}, over its {_kb(budget)} budget — split it into smaller linked notes",
            )
        ]
    if new > old:
        return [
            Finding(
                rel,
                "size",
                True,
                f"{_kb(new)} is over its {_kb(budget)} budget and grew by {_kb(new - old)} — "
                "move content into topic/source notes (or the log's monthly shard) instead of adding here",
            )
        ]
    return []


def _check_lines(rel: str, added: list[tuple[int, str]], cfg: LintConfig) -> list[Finding]:
    limit = cfg.line_limit(rel)
    diary_scope = not cfg.is_log(rel)
    out: list[Finding] = []
    for n, text in added:
        if len(text) > limit:
            out.append(
                Finding(
                    rel,
                    "mega-line",
                    True,
                    f"line {n} is {len(text)} chars (limit {limit}) — put the detail in a linked note",
                    n,
                )
            )
        if diary_scope and is_diary_heading(text):
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


def lint_staged(repo: Path, cfg: LintConfig) -> LintResult:
    result = LintResult()
    known: set[str] | None = None
    staged_list = staged_paths(repo)
    staged_rels = {sp.rel for sp in staged_list}
    for sp in staged_list:
        if not cfg.in_scope(sp.rel):
            continue
        size_base = sp.head_rel
        baseline_text: str | None = None
        if sp.head_rel is None:
            baseline = _daily_baseline(repo, sp.rel)
            if baseline is not None:
                size_base = baseline
                baseline_text = head_text(repo, baseline)
        result.findings += _check_size(repo, sp.rel, size_base, cfg)
        added = _new_lines(repo, sp.rel, sp.head_rel, baseline_text)
        result.findings += _check_lines(sp.rel, added, cfg)
        result.findings += _check_context_notes(repo, sp.rel, added, staged_rels)
        if cfg.is_topic(sp.rel):
            result.findings += _check_citations(sp.rel, staged_text(repo, sp.rel), {n for n, _ in added})
        if known is None:
            known = _link_targets(repo)
        result.findings += _check_links(sp.rel, added, known)
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


# Privacy checks (#453, spec §8.2/§8.3; scout.kb.lint_privacy) that block in
# every mode and ignore SCOUT_LINT_OVERRIDE.
PRIVACY_BLOCKING = frozenset({"private-link", "private-mention", "privacy-widening", "privacy-check-failed"})


def _hard(f: Finding) -> bool:
    return f.blocking and f.check in PRIVACY_BLOCKING


def format_findings(result: LintResult, cfg: LintConfig | None) -> str:
    mode = cfg.mode if cfg is not None else "?"
    lines = [f"kb-lint: {len(result.blocking)} blocking, {len(result.warnings)} warning(s) (mode: {mode})"]
    for f in result.findings:
        mark = "✗" if f.blocking else "!"
        where = f"{f.path}:{f.line}" if f.line else f.path
        lines.append(f"  {mark} {where} [{f.check}] {f.message}")
    soft = [f for f in result.blocking if not _hard(f)]
    if any(_hard(f) for f in result.blocking):
        lines.append(
            "Commit blocked by the privacy checks: they block in every lint mode, and "
            f"{OVERRIDE_ENV} does not apply to them."
        )
    if soft and mode == "block":
        lines.append(
            "Commit blocked. Fix the files above — or, only after two failed attempts, re-run with "
            f'{OVERRIDE_ENV}="<reason>" (logged; the next dreaming run must clean it up).'
        )
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
        "for private links in shared files. Run scoutctl doctor."
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
        result = lint_staged(repo, cfg)
    except Exception as e:  # noqa: BLE001 - fail open (spec §8.3)
        cfg, result = None, LintResult()
        _say(out, f"kb-lint: size/diary check failed ({type(e).__name__}) — not blocking")
    result.findings[:0] = privacy
    if not result.findings:
        return 0
    lint_mode = cfg.mode if cfg else None
    ts = (now or datetime.now(UTC)).isoformat()
    run_mode = env.get("SCOUT_MODE", "interactive")
    logs: Path | None = None
    try:
        logs = paths.logs_dir(main_root(repo))
        _append_jsonl(
            logs / "kb-lint.jsonl",
            [{"ts": ts, "mode": run_mode, "lint_mode": lint_mode, **asdict(f)} for f in result.findings],
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
    if not result.blocking or cfg is None or cfg.mode == "report":
        return 0
    reason = env.get(OVERRIDE_ENV, "").strip()
    if reason:
        files = sorted({f.path for f in result.blocking})
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
