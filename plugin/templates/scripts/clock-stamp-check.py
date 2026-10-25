#!/usr/bin/env python3
"""clock-stamp-check — does a run's own clock match the machine's?

Pattern #276's named detection gap, written 2026-10-22 `dreaming`.

THE DEFECT
----------
On 2026-10-21 the `dreaming-evening` run labelled six commits `[18:5x]` through
`[19:5x]` while the machine's committer clock ran 18:41 -> 18:54. Thirteen real
minutes narrated as seventy. The same invented clock reached the wrap DM
("Wednesday Oct 21, 7:5x PM EDT", sent 18:55) and the `## 📋 Scout Digest`
heading in the daily file ("19:45 EDT", written 18:53).

It is not a timezone bug. A zone error is a FIXED offset; this one widened
monotonically (+9, +13, +23, +40, +48, +55). A widening error is the signature
of an estimate, and wall-clock time is the one quantity a model cannot estimate.
The rule was obeyed in form — right format, right zone label, correctly masked
`HH:Mx` — and skipped in substance: no `date` call behind it. Format compliance
read as rule compliance (Pattern #158, one level down).

Why it matters past the cosmetics: these stamps BECOME evidence. `[19:4x]` in a
commit subject is what a later run greps to date a claim; "re-read live at 7:5x"
is what a later run trusts instead of re-querying. A fabricated clock is a
fabricated freshness guarantee.

WHY AHEAD AND BEHIND ARE NOT THE SAME FINDING
---------------------------------------------
This is the whole precision argument, so it is stated plainly:

  AHEAD  — the label sits in the future relative to the commit that carries it.
           There is no honest way to produce this. You cannot read a clock that
           has not happened yet. Every minute ahead is a minute composed.
           => a finding, drives exit 3.

  BEHIND — the label is older than the commit. This is what an HONEST run looks
           like when `date` was read early and the commit landed later. Pattern
           #276's own "how to apply" names keeping the old stamp as one of the
           two correct options. Flagging it would punish the honest behaviour.
           => advisory only, never drives exit 3.

Three other date checks already read these surfaces and all reported clean on
the day of the defect, because each asks whether a label is internally
consistent: `check-weekday-claims.py` validates a weekday against its own date,
`daily-file-date-frame-check.py` asks whether a heading belongs to this file's
day (Oct 21 was the right day — only the hour was invented), and
`stale-clock-check.py` follows `[#TAG]` rows. This one is the first to compare a
written clock against an INDEPENDENT measurement of the same moment: git's
committer date, stamped by the OS, which no composing run can talk itself past.

THE ZONE TRAP, AND WHY THIS READS GIT'S OFFSET INSTEAD OF `scout-tz.sh`
------------------------------------------------------------------------
The first draft of this driver compared every label against the host's zone
*today* and reported 260 of 379 commits "ahead", most by 370-430 minutes. That
is not composition — it is six hours, which is exactly the gap between two
zones. During a travel window Scout honestly stamps labels in the local zone
honestly, and the detector was re-reading them in a zone the machine only
adopted later.

`scout-tz.sh` answers "where is this Mac NOW", which is the right question for
WRITING a stamp and the wrong one for AUDITING an old one. So each commit is
judged in **its own recorded UTC offset** — git's `%cI` carries the offset the
committing machine held at that moment, which is the only surviving record of
where Scout was standing. The host zone is kept for the report header only.

A detector that fires on two-thirds of a healthy corpus gets ignored, and then
the one real finding inside it is ignored too (Pattern #229).

ABSENCE IS NOT ZERO (Pattern #262)
-----------------------------------
A commit with no `[HH:Mx]` label is not evidence of good behaviour and not
evidence of bad. It is unlabelled. Those are counted and excluded, never folded
into the clean total. `briefing [2026-10-17]` carries a DATE, not a clock, and
is likewise out of scope.

EXIT CODES
----------
  0  clean — every labelled commit's clock is readable as measured
  3  findings — at least one label is ahead of the commit that carries it
  4  soft-skip — not a git repo / no labelled commits in the window

Usage:
  python3 scripts/clock-stamp-check.py [--days N] [--ahead-grace MIN] [--all]
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# `dreaming [19:4x]: ...`, `consolidation [17:0x]: ...`, `research [01:2x]: ...`,
# `work [13:15]: ...`. Deliberately NOT `briefing [2026-10-17]` — that bracket
# holds a date, and a date is not a clock claim.
SUBJECT_RE = re.compile(
    r"^(?P<kind>[a-z][a-z -]{0,24}?)\s*\[(?P<hh>\d{1,2}):(?P<mm>\d{2}|\d[xX])\]\s*:"
)

# `## 📋 Scout Digest — Tuesday Oct 21, 2026 (19:45 EDT)` — the one clock string
# in this family a human reads directly rather than greps.
DIGEST_RE = re.compile(r"^##\s*📋\s*Scout Digest\s*—\s*(?P<rest>.+)$")
DIGEST_TIME_RE = re.compile(
    r"\((?P<hh>\d{1,2}):(?P<mm>\d{2})\s*(?P<ampm>[AaPp]\.?[Mm]\.?)?\s*(?P<zone>[A-Z]{2,5})?\)"
)

AHEAD_GRACE_MIN = 5  # NTP skew + the seconds between reading `date` and committing
BEHIND_NOTE_MIN = 45  # honest-but-stale; advisory only, never a finding


def host_zone() -> str:
    """The Mac's own zone, the same resolution every other Scout surface uses."""
    try:
        out = subprocess.run(
            ["bash", str(REPO / "scripts" / "scout-tz.sh")],
            capture_output=True, text=True, timeout=10,
        )
        z = out.stdout.strip()
        if z:
            return z
    except Exception:
        pass
    return "America/New_York"


def _tz(name: str):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return dt.timezone.utc


def label_window(hh: int, mm_raw: str, day: dt.date, tz: dt.tzinfo) -> tuple[dt.datetime, dt.datetime]:
    """The span of real time a written label can honestly denote.

    A masked `4x` is a deliberate 10-minute bucket, not a typo — Scout masks the
    final digit so a stamp cannot be mistaken for a precision it does not have.
    Treating it as its own span is what keeps this detector off honest runs:
    `[18:4x]` written at 18:41 is INSIDE its window, drift zero.
    """
    if mm_raw[-1] in "xX":
        lo = int(mm_raw[0]) * 10
        hi = lo + 9
    else:
        lo = hi = int(mm_raw)
    start = dt.datetime(day.year, day.month, day.day, hh, lo, 0, tzinfo=tz)
    end = dt.datetime(day.year, day.month, day.day, hh, hi, 59, tzinfo=tz)
    return start, end


def drift_minutes(commit_dt: dt.datetime, hh: int, mm_raw: str) -> tuple[float, dt.datetime]:
    """Signed drift in minutes: positive = label AHEAD of the commit.

    `commit_dt` must carry the commit's OWN offset (git `%cI`), not a zone
    chosen today — see the zone-trap note in the module docstring. The label is
    read as local-to-that-commit, which is what the run that wrote it meant.

    The label carries no date, so it is anchored on the three candidate days
    around the commit and the nearest one wins. That is what makes a `23:5x`
    label on a 00:04 commit read as five minutes behind rather than
    twenty-three hours ahead — the midnight case this vault hits every night a
    run straddles the boundary.
    """
    tz = commit_dt.tzinfo
    best = None
    for delta in (-1, 0, 1):
        day = (commit_dt + dt.timedelta(days=delta)).date()
        start, end = label_window(hh, mm_raw, day, tz)
        if start <= commit_dt <= end:
            d = 0.0
        elif commit_dt < start:
            d = (start - commit_dt).total_seconds() / 60.0
        else:
            d = -((commit_dt - end).total_seconds() / 60.0)
        if best is None or abs(d) < abs(best[0]):
            best = (d, start)
    return best


def correct_subject(message: str, now: dt.datetime | None = None,
                    grace: int = AHEAD_GRACE_MIN) -> tuple[str, str | None, str | None]:
    """Rewrite a leading `<lane> [HH:MM]:` stamp that is ahead of the clock.

    This is the WRITE side of the same question the report asks on the READ
    side, and it deliberately shares `SUBJECT_RE` / `label_window` /
    `drift_minutes` with it. A fixer with its own copy of "what does a masked
    bucket mean" is a fixer that will one day disagree with the detector and
    leave a finding nobody can clear.

    Only an AHEAD stamp is rewritten. A stamp BEHIND its commit is what an
    honest run looks like when `date` was read early and the commit landed
    later — Pattern #276 names keeping that old stamp as one of the two correct
    options, so overwriting it would destroy information rather than add any.

    Returns `(message, old_label, new_label)`; both labels are None when
    nothing was touched.
    """
    head, sep, rest = message.partition("\n")
    m = SUBJECT_RE.match(head)
    if not m:
        return message, None, None
    hh, mm_raw = int(m.group("hh")), m.group("mm")
    # The regex admits `[99:99]`; a nonsense clock is left alone rather than
    # "corrected", because we cannot know what it was trying to say.
    if hh > 23 or (mm_raw[-1] not in "xX" and int(mm_raw) > 59):
        return message, None, None

    if now is None:
        now = dt.datetime.now(_tz(host_zone()))
    drift, _ = drift_minutes(now, hh, mm_raw)
    if drift <= grace:
        return message, None, None

    masked = mm_raw[-1] in "xX"
    new_label = f"{now.hour:02d}:" + (f"{now.minute // 10}x" if masked
                                      else f"{now.minute:02d}")
    old_label = f"{hh:02d}:{mm_raw}"
    head = head[:m.start("hh")] + new_label + head[m.end("mm"):]
    return head + sep + rest, old_label, new_label


def git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(REPO), *args],
        capture_output=True, text=True, timeout=120,
    ).stdout


def scan_commits(days: int):
    """Every labelled commit in the window, with its OS-stamped committer date."""
    raw = git(
        "log", f"--since={days} days ago", "--no-merges",
        "--pretty=format:%H%x00%cI%x00%s",
    )
    labelled, unlabelled = [], 0
    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = line.split("\0")
        if len(parts) != 3:
            continue
        sha, cdate, subject = parts
        m = SUBJECT_RE.match(subject)
        if not m:
            unlabelled += 1
            continue
        # Keep the commit in ITS OWN offset — see the zone-trap note above.
        commit_dt = dt.datetime.fromisoformat(cdate)
        hh, mm_raw = int(m.group("hh")), m.group("mm")
        if hh > 23:
            unlabelled += 1
            continue
        d, _ = drift_minutes(commit_dt, hh, mm_raw)
        labelled.append({
            "sha": sha[:8],
            "kind": m.group("kind").strip(),
            "label": f"{hh:02d}:{mm_raw}",
            "commit_local": commit_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "offset": commit_dt.strftime("%z"),
            "commit_dt": commit_dt,
            "drift": d,
            "subject": subject[:90],
        })
    return labelled, unlabelled


def scan_digest_headings(limit: int = 6):
    """The digest heading's clock vs the commit that introduced that heading.

    `git log -S<exact line>` finds the commit where the string's occurrence
    count changed — i.e. the write — rather than the last commit to touch the
    file, which on a daily file is almost never the same thing.
    """
    out = []
    ai = REPO / "action-items"
    if not ai.is_dir():
        return out
    files = sorted(ai.glob("action-items-20*.md"), reverse=True)[:limit]
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            hm = DIGEST_RE.match(line.strip())
            if not hm:
                continue
            tm = DIGEST_TIME_RE.search(hm.group("rest"))
            if not tm:
                continue
            hh, mm = int(tm.group("hh")), int(tm.group("mm"))
            ampm = (tm.group("ampm") or "").lower().replace(".", "")
            if ampm == "pm" and hh < 12:
                hh += 12
            elif ampm == "am" and hh == 12:
                hh = 0
            rel = str(f.relative_to(REPO))
            raw = git("log", "--format=%H%x00%cI", "-1", "-S", line.strip(), "--", rel)
            if not raw.strip():
                continue
            sha, cdate = raw.strip().split("\0")
            # Keep the commit in ITS OWN offset — see the zone-trap note above.
            commit_dt = dt.datetime.fromisoformat(cdate)
            d, _ = drift_minutes(commit_dt, hh, f"{mm:02d}")
            out.append({
                "file": rel,
                "sha": sha[:8],
                "heading": line.strip()[:88],
                "commit_local": commit_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "drift": d,
            })
            break  # one digest per daily file
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--ahead-grace", type=int, default=AHEAD_GRACE_MIN)
    ap.add_argument("--all", action="store_true",
                    help="list every labelled commit, not just the drifting ones")
    ap.add_argument("--fix-subject", metavar="MSG",
                    help="print MSG with an ahead-of-clock [HH:Mx] stamp corrected "
                         "to the machine's own time; exit 10 if it was changed. "
                         "Called by scripts/git-safe-commit.sh at commit time.")
    args = ap.parse_args()

    if args.fix_subject is not None:
        fixed, old, new = correct_subject(args.fix_subject, grace=args.ahead_grace)
        sys.stdout.write(fixed)
        if old is None:
            return 0
        print(f"clock-stamp: corrected [{old}] -> [{new}] — the label was ahead of "
              f"this machine's clock (Pattern #276)", file=sys.stderr)
        return 10

    if not (REPO / ".git").exists():
        print("⏭️  not a git repo — nothing to check")
        return 4

    zone = host_zone()
    tz = _tz(zone)
    commits, unlabelled = scan_commits(args.days)

    print("# Clock-stamp check — a run's written clock vs the machine's\n")
    if not commits:
        print(f"⏭️  no `[HH:Mx]`-labelled commits in the last {args.days} days "
              f"({unlabelled} unlabelled). Nothing to compare.")
        return 4

    ahead = [c for c in commits if c["drift"] > args.ahead_grace]
    behind = [c for c in commits if c["drift"] < -BEHIND_NOTE_MIN]
    digests = scan_digest_headings()
    digest_ahead = [d for d in digests if d["drift"] > args.ahead_grace]

    print(f"Scanned **{len(commits)} labelled commit(s)** over {args.days}d in `{zone}` "
          f"· {unlabelled} unlabelled (out of scope, not counted clean) "
          f"· {len(digests)} digest heading(s).\n")

    if args.all:
        print("| Commit | Label | Committer clock | Drift |")
        print("|---|---|---|---|")
        for c in sorted(commits, key=lambda x: x["commit_local"]):
            print(f"| `{c['sha']}` | `{c['kind']} [{c['label']}]` | "
                  f"{c['commit_local']} | {c['drift']:+.0f}m |")
        print()

    rc = 0
    if ahead or digest_ahead:
        rc = 3
        print(f"## 🔴 {len(ahead) + len(digest_ahead)} stamp(s) ahead of their own commit\n")
        print("A label in the future cannot have been read from a clock. "
              "Re-run `date` at compose time; never advance a stamp by reasoning.\n")
        if ahead:
            print("| Commit | Label written | Machine said | Ahead by |")
            print("|---|---|---|---|")
            for c in sorted(ahead, key=lambda x: -x["drift"]):
                print(f"| `{c['sha']}` | `{c['kind']} [{c['label']}]` | "
                      f"{c['commit_local']} | **+{c['drift']:.0f} min** |")
            print()
        for d in digest_ahead:
            print(f"- **Digest heading** in `{d['file']}` (`{d['sha']}`) is "
                  f"**+{d['drift']:.0f} min** ahead — written at {d['commit_local']}.")
            print(f"  `{d['heading']}`")
        print()
        runs = sorted({c["commit_local"][:10] + " " + c["kind"] for c in ahead})
        if runs:
            print(f"_Runs involved: {', '.join(runs)}._\n")

    if behind:
        print(f"## 🟡 {len(behind)} stamp(s) more than {BEHIND_NOTE_MIN} min behind "
              f"(advisory — this is what honest-but-stale looks like)\n")
        for c in sorted(behind, key=lambda x: x["drift"])[:8]:
            print(f"- `{c['sha']}` `{c['kind']} [{c['label']}]` committed "
                  f"{c['commit_local']} — {abs(c['drift']):.0f} min later. "
                  f"Re-reading `date` before the commit would tighten it.")
        print("\n_Not a finding: a stale stamp is read, only late. "
              "Only a stamp ahead of its commit is composed._\n")

    if rc == 0:
        print("✅ clean — every labelled commit's clock is consistent with the "
              "machine's committer date (within the grace window).")
    return rc


if __name__ == "__main__":
    sys.exit(main())
