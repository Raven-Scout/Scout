---
phase: core
name: run-health
slot: run-health
mode: [briefing, consolidation, dreaming, research]
requires: null
---

## Run Health

Silently record whether Scout itself is healthy. **At the start of the run**, before any other work, run:

```bash
cd {{SCOUT_DIR}}
mkdir -p .scout-cache
scoutctl health --json > .scout-cache/run-health.json 2>/dev/null || true
```

Do not mention this step in your output. A non-zero exit or an empty file just means there is nothing to report.

**At wrap**, read `.scout-cache/run-health.json` (skip this if the file is missing, empty, or not valid JSON). It was written before this run recorded its own outcome, so it describes Scout **before this run**, and this run is newer evidence:

- Never repeat `auth.expired`: this session running proves the Claude login works.
- Do not repeat a `runs.failing` finding, nor a `lane.dark:<type>` or `lane.silent:<type>` finding whose `<type>` is this run's own session type (briefing, consolidation, dreaming or research): this run is the evidence either way.
- Every other `red` finding **leads** the wrap notification, one line each: `Before this run: <summary> — <fix>`.
- Then, when it holds `yellow` findings, add one line: `⚠️ N run-health warnings before this run — first: <summary>`.
- Ignore `note` findings. When nothing is left to say, say nothing about run health.

Speak about a run's output only in measured terms: "N commits in this run's window (newest `abc1234`)", or "no commits in this run's window". Say nothing when the commit count is unknown. Never write "by this run": a commit in the window may have come from a concurrent session.
