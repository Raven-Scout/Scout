---
name: Widgets launch
type: project
status: active
priority: "🔴"
slack_channels:
  - id: C0123456789
    name: "#widgets-launch"
---
# Widgets launch
**Last verified:** {{d-2}}
**Parent:** [[projects]]
**Status:** active — beta to general availability by {{d+12}}

## Summary

Ships the configurable widget catalog to every workspace. [[people/priya|Priya]] owns the launch plan; [[people/alex|Alex]] owns the rollout code; [[people/sam|Sam]] is adding the rate limits the catalog needs.

## Current state

- The beta is live for internal workspaces.
- Rate limiting lands through `example-org/widgets#12` (review requested from Alex).
- The launch checklist lives in the tracker as PROJ-1201.

## Open questions

- Does the catalog need a per-workspace quota? Asked by Priya on {{wd-1}}, not yet answered.

## Sources

- Tracker: PROJ-1201, PROJ-1207
- Repo: `example-org/widgets`
- Channel: https://acme-co.slack.com/archives/C0123456789/p1700000000000000
