#!/usr/bin/env python3
"""vault-freshness.py — rank knowledge-base files by how stale git says they are.

``hooks/kb-pre-filter.sh`` ranks each KB file by the "Last verified" / "Last
updated" date the file *claims*. A claim can drift: a file can carry a fresh
verified date for facts nobody re-checked. A git commit date cannot drift that
way, since it is when the content last actually changed. This script ranks
files by their last commit against the same freshness budgets, so a session
starts with both views, and flags files whose claimed date disagrees with git.

Freshness budgets are the kb-pre-filter tiers (engine/scout/hooks/kb_pre_filter.py;
a test keeps the two in step):
  - a few named surfaces by basename (e.g. linear-issues.md at 6h)
  - otherwise by ``priority:`` in the file head: 🔴 72h / 🟡 168h / 🟢 336h
  - otherwise 168h
Entity files (knowledge-base/people/, personal/, ontology/entities/) hold facts
that do not rot in a week, and get 720h. A file is ``stale`` past its budget and
``very-stale`` past twice its budget.

Not ranked: archives (an ``archive``/``archived`` directory, or ``-archive`` in the
file name) and dated records (a path component carrying an ISO date, such as an
audit snapshot or an event's folder). Both are point-in-time by design.

Usage:
  python3 scripts/vault-freshness.py                 # ranked table, stale files only
  python3 scripts/vault-freshness.py --all           # every scanned file
  python3 scripts/vault-freshness.py --limit 20      # top-N most overdue
  python3 scripts/vault-freshness.py --json          # machine-readable
  python3 scripts/vault-freshness.py --divergence    # only the claimed-date vs git audit
  python3 scripts/vault-freshness.py --out .scout-cache/vault-freshness.md   # also write a cache view

The vault is $SCOUT_DATA_DIR, or else the directory this script is installed in
(<vault>/scripts/). Read-only: it never writes KB files, only stdout and --out.
Standard library only, so it runs under any python3.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

# Budgets in hours. These mirror engine/scout/hooks/kb_pre_filter.py
# (FRESHNESS_OVERRIDES, PRIORITY_FRESHNESS, DEFAULT_FRESHNESS_HOURS).
BASENAME_HOURS = {
    "linear-issues.md": 6,
    "knowledge-base.md": 6,
    "people.md": 168,
    "channels.md": 336,
    "ai-costs.md": 168,
    "ai-landscape.md": 168,
}
PRIORITY_HOURS = {
    "🔴": 72,
    "🟡": 168,
    "🟢": 336,
}
DEFAULT_HOURS = 168
HEAD_SCAN_LINES = 25

# Entity files: people, personal reference facts, ontology entities.
ENTITY_DIRS = ("people", "personal", "ontology/entities")
ENTITY_HOURS = 720

ARCHIVE_DIRS = {"archive", "archived"}
ISO_DATE_RE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
PRIORITY_RE = re.compile(r"priority:\s*(.*)", re.IGNORECASE)

# Claimed-date divergence: compare the date a file claims on its "Last verified"
# / "Last updated" line with its last commit.
#   claim-lags-git     committed well after the claimed date: an edit that did
#                      not re-verify the file
#   claim-ahead-of-git claims a verification newer than any commit touching it;
#                      writing the claim is itself a commit, so this should not
#                      happen, and flags a claim that was never re-checked
CLAIM_LABEL_RE = re.compile(r"(?:last\s+verified|last\s+updated|prior\s+verified)\b", re.IGNORECASE)
MONTHS = {
    m: i
    for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)
}
# "Jun 14, 2026" / "June 14 2026" / "Jun 14" (no year: the commit's year)
MONTH_DATE_RE = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:,?\s+(20\d{2}))?\b")
CLAIM_LAGS_DAYS = 14
CLAIM_AHEAD_DAYS = 3
CLAIM_SCAN_LINES = 40

STATUSES = ("ok", "stale", "very-stale", "untracked")


def vault_dir() -> Path:
    env = os.environ.get("SCOUT_DATA_DIR")
    return Path(env) if env else Path(__file__).resolve().parent.parent


def _head(path: Path, n: int) -> list[str]:
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            return [line.rstrip("\n") for _, line in zip(range(n), fh)]
    except OSError:
        return []


def freshness_hours(path: Path, vault: Path) -> int:
    """The file's freshness budget in hours: basename, then entity dir, then priority."""
    if path.name in BASENAME_HOURS:
        return BASENAME_HOURS[path.name]
    rel = path.relative_to(vault / "knowledge-base").as_posix()
    if any(rel.startswith(d + "/") for d in ENTITY_DIRS):
        return ENTITY_HOURS
    for line in _head(path, HEAD_SCAN_LINES):
        m = PRIORITY_RE.search(line)
        if m:
            value = m.group(1).replace('"', "")
            for emoji, hours in PRIORITY_HOURS.items():
                if emoji in value:
                    return hours
            return DEFAULT_HOURS
    return DEFAULT_HOURS


def is_ranked(rel_parts: tuple[str, ...]) -> bool:
    """False for archives and dated records, which are old by design."""
    for part in rel_parts:
        if part.lower() in ARCHIVE_DIRS or ISO_DATE_RE.search(part):
            return False
    return "-archive" not in rel_parts[-1].lower()


def last_commits(vault: Path) -> dict[str, tuple[datetime, str]]:
    """{vault-relative path: (committer time, short hash)} of each KB file's newest commit.

    One ``git log`` walk for the whole knowledge base; the first time a path
    appears is its newest commit. Empty when the vault is not a git repository.
    """
    try:
        out = subprocess.run(
            [
                "git",
                "-c",
                "core.quotepath=off",
                "-C",
                str(vault),
                "log",
                "--relative",
                "--format=%x00%ct %h",
                "--name-only",
                "--",
                "knowledge-base",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    commits: dict[str, tuple[datetime, str]] = {}
    current: tuple[datetime, str] | None = None
    for line in out.splitlines():
        if line.startswith("\x00"):
            epoch, _, short = line[1:].partition(" ")
            try:
                current = (datetime.fromtimestamp(int(epoch), timezone.utc), short)
            except ValueError:
                current = None
        elif line and current is not None:
            commits.setdefault(line, current)
    return commits


def parse_claimed_date(path: Path, fallback_year: int) -> date | None:
    """The newest date claimed on a 'Last verified' / 'Last updated' line in the file head.

    Only the first date after the label counts: these lines often mention other
    dates in their prose (a deadline, an event), which are not the claim.
    """
    found: list[date] = []
    for line in _head(path, CLAIM_SCAN_LINES):
        label = CLAIM_LABEL_RE.search(line)
        if not label:
            continue
        rest = line[label.end() :]
        candidates: list[tuple[int, date]] = []
        iso = ISO_DATE_RE.search(rest)
        if iso:
            try:
                candidates.append((iso.start(), date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))))
            except ValueError:
                pass
        for mon in MONTH_DATE_RE.finditer(rest):
            month = MONTHS.get(mon.group(1)[:3].lower())
            if not month:
                continue
            try:
                candidates.append((mon.start(), date(int(mon.group(3) or fallback_year), month, int(mon.group(2)))))
            except ValueError:
                continue
            break
        if candidates:
            found.append(min(candidates)[1])
    return max(found) if found else None


def classify_divergence(commit_day: date, claimed: date | None) -> tuple[str, int]:
    """(label, days) where days = commit day - claimed day (positive: git is newer)."""
    if claimed is None:
        return "no-claim", 0
    delta = (commit_day - claimed).days
    if delta >= CLAIM_LAGS_DAYS:
        return "claim-lags-git", delta
    if -delta >= CLAIM_AHEAD_DAYS:
        return "claim-ahead-of-git", delta
    return "aligned", delta


def scan(vault: Path, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    kb = vault / "knowledge-base"
    commits = last_commits(vault)
    rows: list[dict] = []
    for path in sorted(kb.rglob("*.md")) if kb.is_dir() else []:
        if not is_ranked(path.relative_to(kb).parts):
            continue
        rel = path.relative_to(vault).as_posix()
        budget = freshness_hours(path, vault)
        commit = commits.get(rel)
        if commit is None:
            rows.append(
                {
                    "file": rel,
                    "last_commit": None,
                    "hash": None,
                    "age_hours": None,
                    "budget_hours": budget,
                    "overdue_hours": None,
                    "status": "untracked",
                    "claimed_date": None,
                    "divergence": "no-claim",
                    "claim_delta_days": 0,
                }
            )
            continue
        when, short = commit
        age_h = (now - when).total_seconds() / 3600
        if age_h <= budget:
            status = "ok"
        elif age_h <= 2 * budget:
            status = "stale"
        else:
            status = "very-stale"
        commit_day = when.date()
        claimed = parse_claimed_date(path, commit_day.year)
        divergence, delta = classify_divergence(commit_day, claimed)
        rows.append(
            {
                "file": rel,
                "last_commit": commit_day.isoformat(),
                "hash": short,
                "age_hours": round(age_h),
                "budget_hours": budget,
                "overdue_hours": round(age_h - budget),
                "status": status,
                "claimed_date": claimed.isoformat() if claimed else None,
                "divergence": divergence,
                "claim_delta_days": delta,
            }
        )
    # Most overdue first; untracked files have no age to rank on and go last.
    rows.sort(key=lambda r: (r["overdue_hours"] is None, -(r["overdue_hours"] or 0)))
    return rows


BADGE = {"ok": "✅ ok", "stale": "🟡 stale", "very-stale": "🔴 very-stale", "untracked": "⚪ untracked"}
DIVERGENCE_LABEL = {
    "claim-lags-git": "edited without re-verifying? (git newer than claim)",
    "claim-ahead-of-git": "claim newer than any commit",
}


def render_table(rows: list[dict]) -> str:
    lines = [
        "| File | Last commit | Age | Budget | Overdue | Status |",
        "|------|-------------|-----|--------|---------|--------|",
    ]
    for r in rows:
        if r["age_hours"] is None:
            lines.append(f"| {r['file']} | — | — | {r['budget_hours']}h | — | {BADGE[r['status']]} |")
            continue
        over_d = r["overdue_hours"] / 24
        over = f"+{over_d:.1f}d" if r["overdue_hours"] > 0 else f"{over_d:.1f}d"
        lines.append(
            f"| {r['file']} | {r['last_commit']} (`{r['hash']}`) | {r['age_hours'] / 24:.1f}d "
            f"| {r['budget_hours'] // 24 or 1}d | {over} | {BADGE[r['status']]} |"
        )
    return "\n".join(lines)


def render_divergence_table(rows: list[dict]) -> str:
    lines = [
        "| File | Claimed verified | Last commit | Δ (git−claim) | Flag |",
        "|------|------------------|-------------|---------------|------|",
    ]
    for r in rows:
        d = r["claim_delta_days"]
        lines.append(
            f"| {r['file']} | {r['claimed_date']} | {r['last_commit']} (`{r['hash']}`) "
            f"| {'+' if d > 0 else ''}{d}d | {DIVERGENCE_LABEL[r['divergence']]} |"
        )
    return "\n".join(lines)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="git-derived knowledge-base freshness ranking")
    ap.add_argument(
        "--all", action="store_true", help="show every scanned file (default: stale, very-stale, untracked)"
    )
    ap.add_argument("--limit", type=int, default=0, help="show only the N most overdue (0 = no limit)")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of markdown")
    ap.add_argument("--out", metavar="PATH", help="also write the markdown view here (relative to the vault)")
    ap.add_argument("--divergence", action="store_true", help="show only the claimed-date vs git divergence audit")
    args = ap.parse_args(argv)

    vault = vault_dir()
    rows = scan(vault)
    shown = rows if args.all else [r for r in rows if r["status"] != "ok"]
    if args.limit:
        shown = shown[: args.limit]
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in STATUSES}
    diverged = sorted(
        (r for r in rows if r["divergence"] in DIVERGENCE_LABEL), key=lambda r: -abs(r["claim_delta_days"])
    )

    if args.json:
        payload: dict = {"counts": counts, "files": diverged if args.divergence else shown}
        if not args.divergence:
            payload["divergence"] = diverged
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    if args.divergence:
        body = (
            f"# Vault Freshness — claimed-date divergence ({stamp})\n\n"
            f"{len(diverged)} of {_plural(len(rows), 'file')} claim a verification date out of step "
            "with their last git commit.\n\n"
            + (
                render_divergence_table(diverged)
                if diverged
                else "_No divergence: every claimed date aligns with git._"
            )
            + "\n"
        )
    else:
        body = (
            f"# Vault Freshness — last git commit per KB file ({stamp})\n\n"
            f"Scanned {_plural(len(rows), 'KB file')} · ✅ {counts['ok']} ok · 🟡 {counts['stale']} stale · "
            f"🔴 {counts['very-stale']} very-stale · ⚪ {counts['untracked']} untracked. "
            "Ranked by hours past the freshness budget, by git committer date.\n\n" + render_table(shown) + "\n"
        )
        if diverged:
            body += (
                f"\n## Claimed-date divergence ({_plural(len(diverged), 'file')})\n\n"
                "These claim a verification date out of step with their last commit; "
                "`vault-freshness.py --divergence` lists them all.\n\n" + render_divergence_table(diverged[:10]) + "\n"
            )
    print(body)
    if args.out:
        out_path = vault / args.out
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(body, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
