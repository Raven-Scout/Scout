---
name: Report builder
type: project
status: "Active"
priority: "🟡"
summary: "Self-serve report exports for workspace admins."
last_updated: {{today}}T07:10:00+00:00
as_of: {{d-1}}
last_verified: {{d-1}}
verified_against: ["gh example-org/api", "issue tracker project PROJ"]
---
# Report builder

The report builder lets a workspace admin export usage and billing reports without asking support.
It lives in the API service. [[people/alex|Alex]] owns the export code and decides what ships in
the first release.

**Status:** 🟡 exports work but are capped · **Owner:** [[people/alex|Alex]] · **User's role:** owner ·
**Links:** [repo](https://github.com/example-org/api) · [tracker](https://linear.app/example-org/project/report-builder)

## Where it stands
_As of {{d-1}}._
- CSV exports are built in memory and capped at 50,000 rows. Streaming them in chunks is in review
  in the export streaming PR ([example-org/api#57](https://github.com/example-org/api/pull/57)). See [[report-builder-exports]].
- The nightly PDF job is being retired in favour of on-demand exports
  ([PROJ-1240](https://linear.app/example-org/issue/PROJ-1240)), in progress with [[people/sam|Sam]].
- Pilot workspaces get the builder once streaming ships. See [[report-builder-decisions]].

**Update ({{today}} morning-consolidation):** the export streaming PR merged on {{d-1}}, PROJ-1240
moved to Done, and the weekly sync raised the row cap to 200,000 rows. Last verified {{today}}
against GitHub, Linear and the sync notes.

## What's next
| When | What | Who |
|---|---|---|
| {{d-1}} | Weekly sync: Priya decides the row cap | [[people/alex\|Alex]], [[people/priya\|Priya]], [[people/sam\|Sam]] |
| {{d+10}} | Pilot opens to the first workspaces | [[people/priya\|Priya]] |

## Key decisions
| Date | Decision | Why |
|---|---|---|
| {{d-8}} | CSV is the only export format in the first release | Admins asked for CSV first ([[report-builder-decisions]]) |
| {{d-15}} | Exports run on demand, not on a nightly schedule | Most nightly reports were never opened ([[report-builder-decisions]]) |

## Open questions
- **Should the row cap go up once exports stream?** Priya decides at the weekly sync. [[report-builder-exports]]

## People
| Person | Role on this project |
|---|---|
| [[people/alex\|Alex]] | Owner; writes the export code |
| [[people/priya\|Priya]] | Product; runs the pilot |
| [[people/sam\|Sam]] | Retires the nightly PDF job |

## Go deeper
- **Exports**: [[report-builder-exports]], how an export is built, its limits, and the streaming change.
- **Decisions**: [[report-builder-decisions]], every decision with its date, owner and source.

## History
The builder started as a support script that emailed nightly PDF reports. It became a self-serve
export once admins asked for raw data. The full record is in [[report-builder-decisions]].
