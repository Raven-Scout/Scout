#!/usr/bin/env python3
"""Rough usage counts for Scout, from numbers GitHub already publishes.

Nothing here adds tracking: it reads the public download totals GitHub keeps
for every release file and turns them into estimates. Per release:

  Scout-X.Y.Z.dmg     Sparkle's feed points each update at this versioned DMG,
                      while the website and README link Scout.dmg. So its total
                      is roughly how many running apps updated to X.Y.Z: the
                      best single count of active apps. An app that is closed,
                      or whose user skips or postpones the update, isn't in it;
                      a person downloading this exact file by hand is.
                      Releases before Scout.dmg existed (v0.18.16 and older,
                      #699) served this file to new installs too, so their
                      totals mix the two; they are flagged and left out of the
                      active-apps figure.
  Scout.dmg           the download link on the website and README: new installs
                      (and reinstalls) while this release was Latest.
  appcast.xml         every running app fetches the update feed from
                      /releases/latest/download/ about once an hour
                      (SUScheduledCheckInterval in apps/macos/Scout-Info.plist), so
                      this counts update checks made while the release was
                      Latest. Checks pause while an app holds a found update
                      that isn't installed yet, so it's an activity signal, not
                      a head count.
  scout-release.json  install.sh downloads it once per run (#455): installer runs.

Your own Macs count, and so does anyone fetching these files by hand (a
release check with curl, a browser). GitHub updates the totals with some delay,
so the newest release reads low for its first hours.

Usage:
  scripts/usage-counts.py                 # last 7 days, then every release, newest first
  scripts/usage-counts.py --days 30       # summary over the last 30 days
  scripts/usage-counts.py --json          # machine-readable
  scripts/usage-counts.py --from-file f   # a saved `gh api repos/.../releases` array instead of GitHub
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

REPO = "Raven-Scout/Scout"
VERSIONED_DMG = re.compile(r"^Scout-\d+\.\d+\.\d+\.dmg$")


def parse_time(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def fetch_releases(from_file: str | None) -> list[dict]:
    if from_file:
        with open(from_file, encoding="utf-8") as f:
            return json.load(f)
    out = subprocess.run(
        ["gh", "api", "--paginate", "--slurp", f"repos/{REPO}/releases?per_page=100"],
        check=True, capture_output=True, text=True,
    ).stdout
    return [r for page in json.loads(out) for r in page]


def asset_count(release: dict, match) -> int:
    return sum(a.get("download_count", 0) for a in release.get("assets", []) if match(a.get("name", "")))


def build_rows(releases: list[dict], now: datetime) -> list[dict]:
    """One row per published, non-prerelease release, oldest first: the span it was Latest and its counts."""
    stable = sorted(
        (r for r in releases if not r.get("draft") and not r.get("prerelease") and r.get("published_at")),
        key=lambda r: parse_time(r["published_at"]),
    )
    rows = []
    for i, r in enumerate(stable):
        start = parse_time(r["published_at"])
        current = i + 1 == len(stable)
        end = now if current else parse_time(stable[i + 1]["published_at"])
        hours = (end - start).total_seconds() / 3600
        checks = asset_count(r, lambda n: n == "appcast.xml")
        has_install_dmg = any(a.get("name") == "Scout.dmg" for a in r.get("assets", []))
        rows.append({
            "tag": r["tag_name"],
            "latest_from": start.isoformat(),
            "latest_until": None if current else end.isoformat(),
            "hours_latest": round(hours, 1),
            "apps_updated": asset_count(r, VERSIONED_DMG.match),
            "includes_installs": not has_install_dmg,
            "new_installs": asset_count(r, lambda n: n == "Scout.dmg") if has_install_dmg else None,
            "update_checks": checks,
            "checks_per_day": round(checks / (hours / 24), 1) if hours >= 12 else None,
            "installer_runs": asset_count(r, lambda n: n == "scout-release.json"),
        })
    return rows


def summarize(rows: list[dict], now: datetime, days: int) -> dict:
    """Totals over the releases published in the last `days` days.

    Whole releases only: GitHub's totals can't be split by date. The current
    release is left out of the apps figures, since its updates are still arriving,
    and so are releases whose versioned DMG also served new installs.
    """
    cutoff = now - timedelta(days=days)
    picked = [r for r in rows if parse_time(r["latest_from"]) >= cutoff]
    done = [r for r in picked if r["latest_until"] is not None and not r["includes_installs"]]
    updated = [r["apps_updated"] for r in done]
    return {
        "days": days,
        "releases": [r["tag"] for r in picked],
        "apps_updated_avg": round(sum(updated) / len(updated), 1) if updated else None,
        "apps_updated_max": max(updated) if updated else None,
        "new_installs": sum(r["new_installs"] or 0 for r in picked),
        "installer_runs": sum(r["installer_runs"] for r in picked),
        "update_checks": sum(r["update_checks"] for r in picked),
    }


def fmt(v) -> str:
    return "–" if v is None else str(v)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Rough Scout usage counts from GitHub's public release download totals.")
    p.add_argument("--days", type=int, default=7, help="summary span in days (default 7)")
    p.add_argument("--json", action="store_true", help="print JSON instead of a table")
    p.add_argument("--from-file", help="saved `gh api repos/.../releases` JSON array")
    p.add_argument("--now", help=argparse.SUPPRESS)  # tests: fix the clock (ISO 8601)
    a = p.parse_args(argv)
    if a.days <= 0:
        p.error("--days must be positive")

    now = parse_time(a.now) if a.now else datetime.now(timezone.utc)
    rows = build_rows(fetch_releases(a.from_file), now)
    s = summarize(rows, now, a.days)

    if a.json:
        json.dump({"summary": s, "releases": rows[::-1]}, sys.stdout, indent=2)
        print()
        return 0

    print(f"Last {s['days']} days ({len(s['releases'])} releases):")
    print(f"  active apps       ≈ {fmt(s['apps_updated_avg'])} updated to each release "
          f"(most: {fmt(s['apps_updated_max'])}; current release not counted yet)")
    print(f"  new app installs  {s['new_installs']}")
    print(f"  installer runs    {s['installer_runs']}")
    print(f"  update checks     {s['update_checks']}")
    print()
    head = (f"{'release':<10} {'Latest for':>10} {'apps updated':>13} {'new installs':>13} "
            f"{'checks':>7} {'/day':>6} {'installer':>10}")
    print(head)
    print("-" * len(head))
    for r in rows[::-1]:
        updated = f"{r['apps_updated']}*" if r["includes_installs"] else str(r["apps_updated"])
        print(f"{r['tag']:<10} {r['hours_latest']:>9}h {updated:>13} {fmt(r['new_installs']):>13} "
              f"{r['update_checks']:>7} {fmt(r['checks_per_day']):>6} {r['installer_runs']:>10}")
    if any(r["includes_installs"] for r in rows):
        print("\n* Before Scout.dmg (#699), this DMG was also the website's download: updates and new installs mixed.")
    print("Your own Macs count too. GitHub's totals lag, so the newest release reads low at first.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
