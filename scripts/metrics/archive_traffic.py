#!/usr/bin/env python3
"""Archive this repo's GitHub traffic and download counts before GitHub drops them.

GitHub keeps clone and view traffic for 14 days only, so a trend older than two weeks
is gone unless someone saves it. This script reads the current window and upserts it
into CSV files on an orphan `metrics` branch, in one commit per run.

What it records (one CSV each, under metrics/):
  clones.csv             date, count, uniques            (traffic API, per UTC day)
  views.csv              date, count, uniques            (traffic API, per UTC day)
  ci-runs.csv            date, runs                      (Actions runs created that day)
  referrers.csv          snapshot_date, referrer, count, uniques   (rolling 14-day totals)
  paths.csv              snapshot_date, path, count, uniques       (rolling 14-day totals)
  release-downloads.csv  snapshot_date, tag, asset, download_count (lifetime counters)

ci-runs.csv exists because every CI job clones the repo: on busy days CI is most of
clones.csv. Read the two together, and trust the days with zero runs.

Tokens:
  TRAFFIC_TOKEN  a fine-grained token with "Administration: read" on this repo. The
                 workflow GITHUB_TOKEN cannot read traffic. Without it, the traffic
                 tables are skipped with a warning and everything else still runs.
  GITHUB_TOKEN   contents: write (to commit to the metrics branch), actions: read.

METRICS_DRY_RUN=1 reads everything and prints what it would write, without committing.

Stdlib only, so the workflow needs no install step.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import Any

API = "https://api.github.com"
DIR = "metrics"

DATED = {
    "clones.csv": ["date", "count", "uniques"],
    "views.csv": ["date", "count", "uniques"],
    "ci-runs.csv": ["date", "runs"],
}
SNAPSHOTS = {
    "referrers.csv": ["snapshot_date", "referrer", "count", "uniques"],
    "paths.csv": ["snapshot_date", "path", "count", "uniques"],
    "release-downloads.csv": ["snapshot_date", "tag", "asset", "download_count"],
}


# --- pure merge logic (unit-tested) -------------------------------------------------


def parse_csv(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text))) if text.strip() else []


def render_csv(rows: list[dict[str, str]], fields: list[str]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({f: row.get(f, "") for f in fields})
    return out.getvalue()


def upsert_dated(existing: list[dict[str, str]], fresh: list[dict[str, str]]) -> list[dict[str, str]]:
    """Fresh rows replace stored rows for the same date; older dates are kept.

    The newest day in a traffic window is partial, so the next run must overwrite it.
    """
    by_date = {r["date"]: r for r in existing}
    by_date.update({r["date"]: r for r in fresh})
    return [by_date[d] for d in sorted(by_date)]


def replace_snapshot(
    existing: list[dict[str, str]], fresh: list[dict[str, str]], snapshot_date: str
) -> list[dict[str, str]]:
    """Drop any rows already stored for snapshot_date, then add fresh. Re-running a day is idempotent."""
    kept = [r for r in existing if r["snapshot_date"] != snapshot_date]
    return sorted(kept + fresh, key=lambda r: r["snapshot_date"])


def traffic_rows(payload: dict, key: str) -> list[dict[str, str]]:
    return [
        {"date": item["timestamp"][:10], "count": str(item["count"]), "uniques": str(item["uniques"])}
        for item in payload.get(key, [])
    ]


def window_dates(rows: list[dict[str, str]], today: str) -> list[str]:
    """Every UTC date from the oldest traffic row through today, so CI counts cover the same days.

    With no traffic rows (no TRAFFIC_TOKEN), it covers the same 14 days GitHub would have.
    """
    end = datetime.strptime(today, "%Y-%m-%d").date()
    if rows:
        day = datetime.strptime(min(r["date"] for r in rows), "%Y-%m-%d").date()
    else:
        day = end - timedelta(days=13)
    out = []
    while day <= end:
        out.append(day.isoformat())
        day += timedelta(days=1)
    return out


# --- GitHub API ---------------------------------------------------------------------


class Forbidden(Exception):
    pass


def api(method: str, path: str, token: str, body: dict | None = None) -> Any:
    req = urllib.request.Request(
        API + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "scout-traffic-archive",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as err:
        if err.code in (401, 403):
            raise Forbidden(f"{method} {path}: HTTP {err.code}") from err
        if err.code == 404:
            return None
        raise


def read_branch_files(repo: str, branch: str, token: str) -> tuple[str | None, dict[str, str]]:
    """The branch head SHA (None if the branch doesn't exist yet) and the CSVs on it."""
    ref = api("GET", f"/repos/{repo}/git/ref/heads/{branch}", token)
    if not ref:
        return None, {}
    head = ref["object"]["sha"]
    files = {}
    for name in [*DATED, *SNAPSHOTS]:
        blob = api("GET", f"/repos/{repo}/contents/{DIR}/{name}?ref={head}", token)
        if blob:
            files[name] = base64.b64decode(blob["content"]).decode()
    return head, files


def commit_files(repo: str, branch: str, token: str, head: str | None, files: dict[str, str], message: str) -> str:
    """One commit with every file. Creates the branch as an orphan on the first run."""
    tree = [
        {"path": f"{DIR}/{name}", "mode": "100644", "type": "blob", "content": text} for name, text in files.items()
    ]
    body: dict = {"tree": tree}
    parents: list[str] = []
    if head:
        body["base_tree"] = api("GET", f"/repos/{repo}/git/commits/{head}", token)["tree"]["sha"]
        parents = [head]
    new_tree = api("POST", f"/repos/{repo}/git/trees", token, body)["sha"]
    commit = api(
        "POST", f"/repos/{repo}/git/commits", token, {"message": message, "tree": new_tree, "parents": parents}
    )["sha"]
    if head:
        api("PATCH", f"/repos/{repo}/git/refs/heads/{branch}", token, {"sha": commit})
    else:
        api("POST", f"/repos/{repo}/git/refs", token, {"ref": f"refs/heads/{branch}", "sha": commit})
    return commit


def warn(msg: str) -> None:
    print(f"::warning::{msg}", file=sys.stderr)


def main() -> int:
    repo = os.environ["GITHUB_REPOSITORY"]
    branch = os.environ.get("METRICS_BRANCH", "metrics")
    write_token = os.environ["GITHUB_TOKEN"]
    traffic_token = os.environ.get("TRAFFIC_TOKEN") or write_token
    today = datetime.now(UTC).date().isoformat()

    head, stored = read_branch_files(repo, branch, write_token)
    fresh_dated: dict[str, list[dict[str, str]]] = {}
    fresh_snap: dict[str, list[dict[str, str]]] = {}

    try:
        fresh_dated["clones.csv"] = traffic_rows(
            api("GET", f"/repos/{repo}/traffic/clones?per=day", traffic_token), "clones"
        )
        fresh_dated["views.csv"] = traffic_rows(
            api("GET", f"/repos/{repo}/traffic/views?per=day", traffic_token), "views"
        )
        fresh_snap["referrers.csv"] = [
            {"snapshot_date": today, "referrer": r["referrer"], "count": str(r["count"]), "uniques": str(r["uniques"])}
            for r in api("GET", f"/repos/{repo}/traffic/popular/referrers", traffic_token)
        ]
        fresh_snap["paths.csv"] = [
            {"snapshot_date": today, "path": p["path"], "count": str(p["count"]), "uniques": str(p["uniques"])}
            for p in api("GET", f"/repos/{repo}/traffic/popular/paths", traffic_token)
        ]
    except Forbidden as err:
        warn(f"traffic not archived ({err}). Add a TRAFFIC_TOKEN secret with Administration: read on {repo}.")

    dates = window_dates(fresh_dated.get("clones.csv", []), today)
    fresh_dated["ci-runs.csv"] = [
        {
            "date": d,
            "runs": str(api("GET", f"/repos/{repo}/actions/runs?created={d}&per_page=1", write_token)["total_count"]),
        }
        for d in dates
    ]

    releases: list[dict] = []
    page = 1
    while True:
        batch = api("GET", f"/repos/{repo}/releases?per_page=100&page={page}", write_token)
        if not batch:
            break
        releases += batch
        page += 1
    fresh_snap["release-downloads.csv"] = [
        {"snapshot_date": today, "tag": rel["tag_name"], "asset": a["name"], "download_count": str(a["download_count"])}
        for rel in releases
        for a in rel.get("assets", [])
    ]

    out: dict[str, str] = {}
    for name, rows in fresh_dated.items():
        out[name] = render_csv(upsert_dated(parse_csv(stored.get(name, "")), rows), DATED[name])
    for name, rows in fresh_snap.items():
        out[name] = render_csv(replace_snapshot(parse_csv(stored.get(name, "")), rows, today), SNAPSHOTS[name])
    changed = {name: text for name, text in out.items() if stored.get(name) != text}
    if not changed:
        print("nothing changed")
        return 0
    if os.environ.get("METRICS_DRY_RUN"):
        for name in sorted(changed):
            rows = changed[name].count("\n") - 1
            print(f"would write {DIR}/{name} ({rows} rows) to {branch}")
        return 0
    sha = commit_files(repo, branch, write_token, head, changed, f"metrics: archive {today} [skip ci]")
    print(f"archived {', '.join(sorted(changed))} → {branch}@{sha[:7]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
