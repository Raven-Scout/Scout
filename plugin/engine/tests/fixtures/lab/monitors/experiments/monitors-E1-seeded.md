---
type: lab-experiment
id: E1
thread: monitors
questions: [Q1]
status: done
planned: 2026-11-20T09:00
not_before: 2026-11-20T10:00
cost_cap_usd: 1.00
data: [sample transcripts 2026-10-01..2026-11-19]
---
## Hypothesis
A single judge catches most seeded faults but misses subtle ones.

## Method
Seed 40 faults into sample transcripts and score the judge's flags.

## Data
Sample transcripts, 2026-10-01 to 2026-11-19.

## Result
Recall 0.81 on 40 seeded faults. Not shown: behaviour against adaptive attackers.

## What it means
A plain judge is a usable baseline for Q1, but says nothing about adaptation.
