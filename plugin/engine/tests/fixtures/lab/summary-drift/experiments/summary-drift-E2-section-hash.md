---
type: lab-experiment
id: E2
thread: summary-drift
questions: [Q3]
status: planned
planned: 2026-11-21T01:11
not_before: 2026-11-21T13:00
cost_cap_usd: 1.00
visibility: normal
data: [sampled pages 2026-10-01..2026-11-20]
---
## Hypothesis
Hashing only the sections a summary cites flags stale summaries with few false alarms.

## Method
Hash each cited section when its summary is written, recompute later, and compare the flags with the hand verdicts from E1.

## Data
The rewritten-pages export from E1, plus untouched pages as controls.

## Result

## What it means
