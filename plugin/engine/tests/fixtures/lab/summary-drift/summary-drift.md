---
type: lab-thread
title: Keeping cached summaries honest
status: active
created: 2026-11-20
approved: 2026-11-20
last_run: 2026-11-21T01:11
visibility: normal
next_q: 8
next_e: 3
---
# Keeping cached summaries honest

## Question
What is the cheapest signal that a stored summary no longer matches the page it summarises?

## Current understanding
Changed length is a weak guide to staleness; see [[summary-drift-notes]]. Hashing only the sections a summary cites looks cheap and has not been tried on real pages. Regenerating on every save is the safe default but costs more than it needs to; see [[regeneration-cost-notes]].

## Open questions
- **Q3** Does a hash of the cited sections flag stale summaries better than a changed-length rule? · open · raised 2026-11-20
  - alex: try it on a real sample of pages, not only synthetic ones
    - scout (2026-11-21 01:11): Planned [[summary-drift-E2-section-hash|E2]] for the next run.
  - alex: and report how many false alarms it raises
- **Q5** Which page types change most often between summary refreshes? · open · raised 2026-11-21

## Answered questions
- **Q1** Is changed length alone a usable staleness signal? · answered · 2026-11-20 — no: it missed rewrites that kept a page the same size; see [[summary-drift-notes]].
- **Q2** Should every summary carry a version stamp? · dropped · 2026-11-21 — owner: not relevant

## Experiments
- [[summary-drift-E2-section-hash|E2]] Section-hash staleness check on sampled pages · planned · runs after 2026-11-21 13:00
- [[summary-drift-E1-baseline|E1]] Length rule on rewritten pages · done · 2026-11-20 — length rule missed 7 rewrites

## Evidence
- [[summary-drift-notes]] — why changed length is a weak staleness signal
- [[regeneration-cost-notes]] — what one refresh costs per page

## Comments
- alex: keep this thread on detection, not on how summaries get rewritten
  - scout (2026-11-21 01:11): Understood, new questions here will be about detection.
