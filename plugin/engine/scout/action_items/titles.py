"""*Clean Title, Prose Body, Refs Block*, held to a length: `action-items check-titles`.

The rule (``phases/core/action-items.md``) asks for a task title the owner can
scan: one line of at most :data:`TITLE_MAX` characters, with the detail in the
body after `` — `` and the evidence in the task's context note. Prose alone
didn't hold it. On a mature vault (UI audit, 2026-11) the median title was
about 90 characters and the longest over 260, so the app's lists showed
paragraphs of bold serif. ``Today's Focus`` drifted the same way, to whole
paragraphs per bullet.

What is checked, in today's file:

- every open top-level task whose title is **new or changed** since the newest
  earlier daily file: it must be at most :data:`TITLE_MAX` characters;
- the :data:`LEGACY_PER_RUN` longest **carried** titles over the limit in
  🔴 Urgent, so the list people read first converges a few at a time. Other
  carried titles are left alone: rewording an item with no news restarts its
  60-day stale clock (*Stale Items Auto-Archive*), so they get shortened when a
  run next touches them for a real reason;
- ``## 💡 Today's Focus``: at most :data:`FOCUS_MAX_BULLETS` bullets, each at
  most :data:`FOCUS_MAX_CHARS` characters.

The title is the first ``**bold**`` segment after the ``[#TAG]``; a line with
no bold uses the text before its `` — `` separator.
"""

from __future__ import annotations

import datetime as _dt
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from scout import paths
from scout.action_items.past_due import latest_daily
from scout.action_items.prefetched import daily_lines, unfenced
from scout.action_items.stale import task_blocks

TITLE_MAX = 80
LEGACY_PER_RUN = 5
FOCUS_MAX_BULLETS = 5
FOCUS_MAX_CHARS = 300

_TASK_HEAD_RE = re.compile(r"^- \[[ xX]\]\s+\[#[A-Za-z0-9]{2,8}\]\s*")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_SEPARATOR_RE = re.compile(r"\s[—–]\s")
_RED = "🔴"
_FOCUS = "💡"


def title_of(line: str) -> str:
    """The title of a task line: its first bold segment, else the text before the separator."""
    head = _TASK_HEAD_RE.match(line)
    rest = line[head.end() :] if head else line
    bold = _BOLD_RE.search(rest)
    if bold:
        return bold.group(1).strip()
    return _SEPARATOR_RE.split(rest, maxsplit=1)[0].strip()


@dataclass(frozen=True)
class TitleFinding:
    label: str  # "[#TAG]" or "Today's Focus"
    problem: str
    tag: str | None = None
    chars: int | None = None
    carried: bool = False


@dataclass(frozen=True)
class TitleCheck:
    checked: int
    findings: list[TitleFinding] = field(default_factory=list)
    # Carried titles over the limit that this run is not asked to shorten.
    long_carried: int = 0

    @property
    def passed(self) -> bool:
        return not self.findings


def _sections(lines: Sequence[str]) -> list[str]:
    """The section emoji (first token of the `## ` heading) each line sits under."""
    out: list[str] = []
    section = ""
    for line in lines:
        if line.startswith("## "):
            heading = line[3:].strip()
            section = heading.split(" ", 1)[0] if heading else ""
        out.append(section)
    return out


def _titles(lines: Sequence[str]) -> dict[str, tuple[str, str]]:
    """Tag → (section, title) for each open top-level task; the first row with a tag wins."""
    sections = _sections(lines)
    out: dict[str, tuple[str, str]] = {}
    for block in task_blocks(list(lines)):
        out.setdefault(block.tag, (sections[block.start], title_of(lines[block.start])))
    return out


def _focus_bullets(lines: Sequence[str]) -> list[str]:
    sections = _sections(lines)
    return [
        line.strip()
        for line, section in zip(lines, sections, strict=True)
        if section == _FOCUS and re.match(r"^[-*]\s+\S", line)
    ]


def judge(lines: Sequence[str], *, earlier: Sequence[str] = ()) -> TitleCheck:
    """Check today's ``lines`` against the title and focus limits.

    ``earlier`` is the newest earlier daily file: a title it already had under
    the same tag is *carried*, anything else is new or changed.
    """
    before = {tag: title for tag, (_section, title) in _titles(earlier).items()}
    findings: list[TitleFinding] = []
    legacy_red: list[TitleFinding] = []
    long_carried = 0
    current = _titles(lines)
    for tag, (section, title) in current.items():
        n = len(title)
        if n <= TITLE_MAX:
            continue
        if before.get(tag) != title:
            findings.append(TitleFinding(f"[#{tag}]", f"title is {n} characters, over {TITLE_MAX}", tag=tag, chars=n))
        elif section == _RED:
            legacy_red.append(
                TitleFinding(
                    f"[#{tag}]",
                    f"carried 🔴 title is {n} characters, over {TITLE_MAX}",
                    tag=tag,
                    chars=n,
                    carried=True,
                )
            )
        else:
            long_carried += 1
    legacy_red.sort(key=lambda f: -(f.chars or 0))
    findings += legacy_red[:LEGACY_PER_RUN]
    long_carried += len(legacy_red[LEGACY_PER_RUN:])

    bullets = _focus_bullets(lines)
    if len(bullets) > FOCUS_MAX_BULLETS:
        findings.append(TitleFinding("Today's Focus", f"has {len(bullets)} bullets, over {FOCUS_MAX_BULLETS}"))
    for i, bullet in enumerate(bullets, start=1):
        if len(bullet) > FOCUS_MAX_CHARS:
            findings.append(
                TitleFinding(
                    "Today's Focus",
                    f"bullet {i} is {len(bullet)} characters, over {FOCUS_MAX_CHARS}",
                    chars=len(bullet),
                )
            )
    return TitleCheck(checked=len(current), findings=findings, long_carried=long_carried)


def _earlier_lines(data_dir: Path, day: _dt.date) -> list[str]:
    found = latest_daily(data_dir, day - _dt.timedelta(days=1))
    if found is None:
        return []
    try:
        return unfenced(found[1].read_text(encoding="utf-8").splitlines())
    except (OSError, UnicodeDecodeError):
        return []


def check_titles(*, data_dir: Path | None = None, today: str) -> TitleCheck:
    """Check today's file. Raises ``PrefetchCheckError`` when it doesn't exist yet."""
    lines = daily_lines(data_dir, today)
    day = _dt.date.fromisoformat(today)
    return judge(lines, earlier=_earlier_lines(paths.data_dir(data_dir), day))
