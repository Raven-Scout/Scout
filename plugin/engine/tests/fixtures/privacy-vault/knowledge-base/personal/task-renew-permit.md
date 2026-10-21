---
name: task-renew-permit
type: task
status: open
domain: personal
title: "Renew parking permit CANARY-PRIV-1R7K"
deadline: 2000-06-30
surface_rule:
  default: do_not_surface
  windows:
    - {from: 2000-01-01, to: 2000-12-31, surface: "🟡", task: "Renew parking permit CANARY-PRIV-1R7K"}
---

# Renew parking permit CANARY-PRIV-1R7K

Renew the residential parking permit before it lapses (CANARY-PRIV-1R7K).
No `visibility:` key: this note is private by the `knowledge-base/personal/**`
path default. The dates are rewritten by `build_privacy_vault` so the
`surface_rule` window covers the day the test runs.
