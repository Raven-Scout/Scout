---
name: Report builder
type: project
status: "Active"
priority: "🟡"
summary: "Self-serve report exports for workspace admins."
last_updated: {{today}}T07:10:00+00:00
as_of: {{today}}
last_verified: {{today}}
verified_against: ["gh example-org/api", "issue tracker project PROJ"]
---
# Report builder

The report builder lets a workspace admin export usage and billing reports without asking support.
It lives in the API service. [[people/alex|Alex]] owns the export code and decides what ships in
the first release.

**Status:** 🟢 exports stream; pilot next · **Owner:** [[people/alex|Alex]] · **User's role:** owner ·
**Links:** [repo](https://github.com/example-org/api) · [tracker](https://linear.app/example-org/project/report-builder)

## Where it stands
_As of {{today}}._
- CSV exports stream in chunks, so the cap rose to 200,000 rows. The export streaming PR
  ([example-org/api#57](https://github.com/example-org/api/pull/57)) merged on {{d-1}}. See [[report-builder-exports]].
- The nightly PDF job is retired ([PROJ-1240](https://linear.app/example-org/issue/PROJ-1240)); exports run on demand.
- The pilot opens to three workspaces on {{d+7}}. See [[report-builder-decisions]].
- **{{d-1}} update:** checked gh, Linear and the sync notes this morning: PR 57 merged, PROJ-1240 done.

## What's next
| When | What | Who |
|---|---|---|
| {{d+3}} | Pick the three pilot workspaces | [[people/priya\|Priya]] |
| {{d+7}} | Pilot opens to three workspaces | [[people/priya\|Priya]] |

## Key decisions
| Date | Decision | Why |
|---|---|---|
| {{d-1}} | The row cap goes from 50,000 to 200,000 rows | Streaming removed the memory limit ([[report-builder-decisions]]) |
| {{d-8}} | CSV is the only export format in the first release | Admins asked for CSV first ([[report-builder-decisions]]) |
| {{d-15}} | Exports run on demand, not on a nightly schedule | Most nightly reports were never opened ([[report-builder-decisions]]) |

## Open questions
- None right now.

## People
| Person | Role on this project |
|---|---|
| [[people/alex\|Alex]] | Owner; writes the export code |
| [[people/priya\|Priya]] | Product; runs the pilot |
| [[people/sam\|Sam]] | Retired the nightly PDF job |

## Go deeper
- **Exports**: [[report-builder-exports]], how an export is built, its limits, and the streaming change.
- **Decisions**: [[report-builder-decisions]], every decision with its date, owner and source.

## History
The builder started as a support script that emailed nightly PDF reports. It became a self-serve
export once admins asked for raw data. The full record is in [[report-builder-decisions]].
