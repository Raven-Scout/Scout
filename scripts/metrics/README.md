# Repo metrics archive

GitHub shows clone and view traffic for the last 14 days only. The
`traffic-archive` workflow saves it every day, so the history survives.

The data lives on the orphan **`metrics`** branch, under `metrics/`. It never
touches `main`, and its commits carry `[skip ci]`.

| File | Rows | Source |
|---|---|---|
| `clones.csv` | `date, count, uniques` | Traffic API, per UTC day |
| `views.csv` | `date, count, uniques` | Traffic API, per UTC day |
| `ci-runs.csv` | `date, runs` | Actions runs created that day |
| `referrers.csv` | `snapshot_date, referrer, count, uniques` | Rolling 14-day totals, one snapshot per day |
| `paths.csv` | `snapshot_date, path, count, uniques` | Rolling 14-day totals, one snapshot per day |
| `release-downloads.csv` | `snapshot_date, tag, asset, download_count` | Lifetime download counters, one snapshot per day |

## Reading clones honestly

Every CI job clones the repo, and a run has about three jobs. On a busy day CI
is most of `clones.csv`. Read it next to `ci-runs.csv` and trust the days with
zero runs: what's left there is people's installs and updates, plus the
maintainers' own machines. Unique cloners can't be added up across days,
because the same machine counts once per day.

The newest day in each window is partial. The next run overwrites it.

## Setup

The workflow's own `GITHUB_TOKEN` can't read traffic. Add a repo secret
**`TRAFFIC_TOKEN`**: a fine-grained personal access token, resource owner
`Raven-Scout`, repository `Raven-Scout/Scout` only, permission
**Administration: read**. Without it the workflow still archives CI runs and
release downloads, and it warns that traffic was skipped.

Run it by hand from the Actions tab (`traffic-archive` → Run workflow). To see what it would write without committing anything, run it locally:

```bash
METRICS_DRY_RUN=1 GITHUB_REPOSITORY=Raven-Scout/Scout GITHUB_TOKEN="$(gh auth token)" python3 scripts/metrics/archive_traffic.py
```
