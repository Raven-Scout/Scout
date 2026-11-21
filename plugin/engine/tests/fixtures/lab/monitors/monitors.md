---
type: lab-thread
title: Monitors under adaptive attack
status: active
created: 2026-11-20
approved: 2026-11-20
last_run: 2026-11-21T01:11
visibility: normal
next_q: 8
next_e: 3
---
# Monitors under adaptive attack

## Question
Do behaviour monitors that score well on fixed test sets still catch an agent that adapts to them?

## Current understanding
Catch rates fall sharply once attackers refine against the monitor; see [[sandbagging-and-monitor-evasion]]. Cascades look cheap on static sets but have no adaptive test yet.

## Open questions
- **Q3** Do probe→judge cascades keep their recall against adaptive attacks? · open · raised 2026-11-20
  - alex: test this on our own traces, don't just read
    - scout (2026-11-21 01:11): Planned [[monitors-E2-cascade|E2]] for the next run.
  - alex: also compare against the plain judge baseline
- **Q5** Does the cascade threshold transfer between trace sources? · open · raised 2026-11-21

## Answered questions
- **Q1** How far do catch rates fall under refined attacks? · answered · 2026-11-20 — 95% to 32–60% across three papers; see [[sandbagging-and-monitor-evasion]].
- **Q2** Do monitors need retraining per model family? · dropped · 2026-11-21 — owner: not relevant

## Experiments
- [[monitors-E2-cascade|E2]] Cascade recall on seeded faults in own transcripts · planned · runs after 2026-11-21 13:00
- [[monitors-E1-seeded|E1]] Seeded faults against a single judge · done · 2026-11-20 — baseline recall 0.81

## Evidence
- [[sandbagging-and-monitor-evasion]] — how much catch rates drop under adaptive attackers
- [[probe-monitor-false-positives-and-cascades]] — cascade cost and false-positive behaviour

## Comments
- alex: keep this thread focused on adaptive attacks
  - scout (2026-11-21 01:11): Understood, scoping new questions to adaptive attacks.
